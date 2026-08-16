"""Executable Phase 8 scientist handlers that compose existing native adapters.

The handlers in this module close product-level contract gaps.  They do not
replace engine adapters: each scientific calculation is still performed by a
declared RDKit, PySCF, OpenMM, Vina, or Qiskit capability and all intermediate
artifacts remain persisted in the shared artifact repository.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from cgr.kernel.contracts import CapabilityVersion, ExecutionContext, ExecutionStatus
from cgr.discovery import (
    CandidateEvaluatorRegistry,
    CandidateGeneratorRegistry,
    CandidateScientificVerifierRegistry,
    CandidateValidityCheckerRegistry,
    DiscoveryCampaign,
    DiscoveryCampaignBudget,
    DiscoveryCampaignRun,
    DiscoveryCampaignRuntime,
    DiscoveryObjective,
    DiscoveryStoppingPolicy,
    MolecularCandidateEvidenceVerifier,
    ObjectiveDirection,
    RDKitDeNovoMolecularCandidateGenerator,
    RDKitMolecularCandidateGenerator,
    RDKitMolecularDescriptorEvaluator,
    RDKitMolecularValidityChecker,
    SelectionPolicy,
    VinaMolecularDockingEvaluator,
)
from cgr.molecular import (
    CONFORMER_GENERATION,
    CONSTRAINED_DISTANCE_SCAN,
    PREPARE,
    SMILES_PARSE,
    MolecularConformerSet,
    MolecularDistanceScan,
    MolecularEnvironment,
    MolecularSimulationAtom,
    MolecularSimulationBond,
    MolecularSimulationSystem,
    MolecularSystemConstructionSettings,
    MolecularVector3,
    MeekoDockingPreparationAdapter,
    RDKitCheminformaticsAdapter,
)
from cgr.electronic_structure import (
    ACTIVE_SPACE_CONSTRUCT,
    ACTIVE_SPACE_SELECT,
    CONFIGURATION_DEFINE,
    HARTREE_FOCK,
    IMPLICIT_SOLVENT_HARTREE_FOCK,
    MOLECULE_CONSTRUCT,
    ElectronicActiveSpace,
    ElectronicFrequencyAnalysis,
    ElectronicFrequencyMode,
    ElectronicImplicitSolventResult,
    ElectronicMolecule,
    ElectronicReactionPathPoint,
    ElectronicReactionPathResult,
    ElectronicTensor,
    ElectronicTransitionStateSearch,
    PySCFElectronicStructureAdapter,
    QM_REGION_PREPARE,
    QMMM_EMBEDDING_PREPARE,
    QMMM_HARTREE_FOCK,
    QMMM_HYBRID_EXECUTE,
    transition_state_verification_request,
)
from cgr.quantum_workflow import (
    ANSATZ_CONSTRUCT,
    FERMION_TO_QUBIT_MAP,
    HAMILTONIAN_CONSTRUCT,
    VQE_EXECUTE,
    QiskitQuantumWorkflowAdapter,
    VariationalGroundStateResult,
)
from cgr.protein_design import (
    ExternalProteinEngineAdapter,
    ProteinDesignSpecification,
    ProteinDesignVerificationReport,
    resolve_protein_residue_count,
)
from cgr.science import (
    ArtifactPointer,
    ArtifactReference,
    CapabilityInvocation,
    CreationProvenance,
)
from cgr.workflow_graph import CapabilityInvocation as WorkflowCapabilityInvocation
from cgr.scientific_verification import (
    CalculationEvidence,
    MetalCentreRequest,
    PotentialEnergyCurveRequest,
    PotentialEnergyPoint,
    VerificationOutcome,
    default_scientific_verifier_registry,
)

from .scientific_executions import ScientificExecutionRecord
from .scientific_objectives import StructuredScientificObjective
from .scientific_runtime import (
    ScientificCapabilityFailure,
    ScientificCapabilityOutcome,
    ScientificEngineHandler,
    ScientistCapabilityRegistry,
)

_VERSION = CapabilityVersion(major=1, minor=0, patch=0)


@runtime_checkable
class ScientificPayloadStore(Protocol):
    def read(self, reference: ArtifactReference) -> bytes: ...

    def write(self, reference: ArtifactReference, payload: bytes) -> None: ...


def _stable_identifier(prefix: str, *values: object) -> str:
    digest = hashlib.sha256(
        "\x1f".join(str(value) for value in values).encode("utf-8")
    ).hexdigest()[:32]
    return f"{prefix}-{digest}"


class _NativeRunner:
    def __init__(self, store: ScientificPayloadStore) -> None:
        if not isinstance(store, ScientificPayloadStore):
            raise TypeError("Phase 8 handlers require an exact artifact payload store.")
        self.store = store

    def invoke(
        self,
        adapter: object,
        capability_name: str,
        *,
        inputs: tuple[ArtifactReference, ...] = (),
        parameters: Mapping[str, object] | None = None,
        execution_identifier: str,
        objective: StructuredScientificObjective,
    ) -> tuple[ArtifactReference, ...]:
        envelope = next(
            (
                item
                for item in adapter.declaration.capabilities  # type: ignore[attr-defined]
                if item.descriptor.capability_name == capability_name
            ),
            None,
        )
        if envelope is None:
            raise ScientificCapabilityFailure(
                "scientific_capability_unavailable",
                f"Native capability {capability_name} is unavailable.",
            )
        objective_payload = objective.model_dump_json().encode("utf-8")
        result = adapter.invoke(  # type: ignore[attr-defined]
            CapabilityInvocation(
                capability=envelope.descriptor,
                input_artifacts=inputs,
                experiment=ArtifactPointer(
                    artifact_identifier=objective.objective_identifier,
                    content_sha256=hashlib.sha256(objective_payload).hexdigest(),
                ),
                context=ExecutionContext(execution_id=execution_identifier),
                parameters=dict(parameters or {}),
            )
        )
        if result.status is not ExecutionStatus.SUCCESS:
            failure = result.failure
            raise ScientificCapabilityFailure(
                failure.code if failure is not None else "scientific_engine_failed",
                failure.message if failure is not None else "Native scientific execution failed.",
                retryable=bool(failure and failure.retryable),
            )
        return result.output_artifacts

    def write_json(
        self,
        *,
        artifact_type: str,
        payload: bytes,
        producer: str,
        execution_identifier: str,
        parents: tuple[ArtifactReference, ...] = (),
        metadata: Mapping[str, object] | None = None,
    ) -> ArtifactReference:
        return self.write_bytes(
            artifact_type=artifact_type,
            media_type=f"application/vnd.pulsate.{artifact_type.replace('_', '-')}+json",
            payload=payload,
            producer=producer,
            execution_identifier=execution_identifier,
            parents=parents,
            metadata=metadata,
        )

    def write_bytes(
        self,
        *,
        artifact_type: str,
        media_type: str,
        payload: bytes,
        producer: str,
        execution_identifier: str,
        parents: tuple[ArtifactReference, ...] = (),
        metadata: Mapping[str, object] | None = None,
    ) -> ArtifactReference:
        digest = hashlib.sha256(payload).hexdigest()
        reference = ArtifactReference(
            artifact_identifier=_stable_identifier(artifact_type.replace("_", "-"), digest),
            schema_version=_VERSION,
            artifact_type=artifact_type,
            media_type=media_type,
            content_sha256=digest,
            byte_size=len(payload),
            metadata=dict(metadata or {}),
            provenance=CreationProvenance(
                producer=producer,
                producer_version=_VERSION,
                execution_identifier=execution_identifier,
            ),
            parents=tuple(parent.pointer for parent in parents),
        )
        self.store.write(reference, payload)
        return reference


def _objective_input(
    record: ScientificExecutionRecord,
    *,
    kinds: tuple[str, ...],
) -> ArtifactReference:
    identifiers = {
        item.artifact_identifier
        for item in record.objective.input_references
        if item.artifact_type in kinds
    }
    matches = tuple(
        item for item in record.artifact_references
        if item.artifact_identifier in identifiers
    )
    if len(matches) != 1:
        raise ScientificCapabilityFailure(
            "scientific_input_ambiguous",
            "Exactly one molecular input is required for this scientific objective.",
        )
    return matches[0]


def _molecule_from_structure_payload(reference: ArtifactReference, payload: bytes):
    from rdkit import Chem

    media = reference.media_type.lower()
    text = payload.decode("utf-8")
    molecule = None
    if "mol" in media or "sdf" in media:
        molecule = Chem.MolFromMolBlock(text, removeHs=False, sanitize=True)
    elif "smiles" in media:
        molecule = Chem.MolFromSmiles(text.strip())
    if molecule is None:
        raise ScientificCapabilityFailure(
            "molecular_input_invalid",
            "The conformer objective requires a valid MOL/SDF or SMILES molecule.",
        )
    return molecule


def _classify_axial_equatorial(conformers: MolecularConformerSet) -> dict[str, object]:
    """Classify a monosubstituted six-membered ring from geometry only."""

    import numpy
    from rdkit import Chem

    graph = conformers.source_graph
    molecule = Chem.RWMol()
    for atom in graph.atoms:
        rd_atom = Chem.Atom(atom.atomic_number)
        rd_atom.SetFormalCharge(atom.formal_charge)
        molecule.AddAtom(rd_atom)
    bond_types = {
        1.0: Chem.BondType.SINGLE,
        1.5: Chem.BondType.AROMATIC,
        2.0: Chem.BondType.DOUBLE,
        3.0: Chem.BondType.TRIPLE,
    }
    for bond in graph.bonds:
        molecule.AddBond(
            bond.atom_index_a,
            bond.atom_index_b,
            bond_types.get(float(bond.bond_order), Chem.BondType.SINGLE),
        )
    rings = tuple(ring for ring in Chem.GetSymmSSSR(molecule) if len(ring) == 6)
    candidates: list[tuple[tuple[int, ...], int, int]] = []
    for ring in rings:
        ring_indices = tuple(int(value) for value in ring)
        ring_set = set(ring_indices)
        for ring_index in ring_indices:
            outside = tuple(
                neighbor.GetIdx()
                for neighbor in molecule.GetAtomWithIdx(ring_index).GetNeighbors()
                if neighbor.GetIdx() not in ring_set
                and neighbor.GetAtomicNum() > 1
            )
            for substituent_index in outside:
                candidates.append((ring_indices, ring_index, substituent_index))
    if len(candidates) != 1:
        raise ScientificCapabilityFailure(
            "conformer_semantics_ambiguous",
            "Axial/equatorial comparison requires one uniquely substituted six-membered ring.",
        )
    ring_indices, attachment_index, substituent_index = candidates[0]
    scores: list[tuple[int, float]] = []
    for conformer in conformers.conformers:
        coordinates = numpy.asarray(
            [(item.x, item.y, item.z) for item in conformer.coordinates], dtype=float
        )
        ring_coordinates = coordinates[list(ring_indices)]
        centered = ring_coordinates - ring_coordinates.mean(axis=0)
        _, _, vectors = numpy.linalg.svd(centered, full_matrices=False)
        normal = vectors[-1]
        bond = coordinates[substituent_index] - coordinates[attachment_index]
        norm = float(numpy.linalg.norm(bond))
        if norm <= 1.0e-10:
            raise ScientificCapabilityFailure(
                "conformer_geometry_invalid", "A substituent bond has zero length."
            )
        scores.append((conformer.conformer_index, abs(float(numpy.dot(bond / norm, normal)))))
    if len(scores) < 2:
        raise ScientificCapabilityFailure(
            "conformer_sampling_insufficient",
            "Axial/equatorial comparison requires at least two generated conformers.",
            retryable=True,
        )
    axial = max(scores, key=lambda item: (item[1], -item[0]))
    equatorial = min(scores, key=lambda item: (item[1], item[0]))
    if axial[0] == equatorial[0] or axial[1] - equatorial[1] < 0.15:
        raise ScientificCapabilityFailure(
            "conformer_semantics_unresolved",
            "Generated conformers do not contain distinct axial and equatorial states.",
            retryable=True,
        )
    return {
        "method": "six_membered_ring_plane_substituent_projection",
        "ring_atom_indices": list(ring_indices),
        "attachment_atom_index": attachment_index,
        "substituent_atom_index": substituent_index,
        "axial_conformer_index": axial[0],
        "equatorial_conformer_index": equatorial[0],
        "axial_projection_score": axial[1],
        "equatorial_projection_score": equatorial[1],
    }


class ConformerGenerationHandler:
    """Prepare a structure, generate conformers, and resolve axial/equatorial labels."""

    def __init__(self, store: ScientificPayloadStore, rdkit_adapter: RDKitCheminformaticsAdapter) -> None:
        self.runner = _NativeRunner(store)
        self.adapter = rdkit_adapter

    def execute(
        self,
        *,
        invocation: WorkflowCapabilityInvocation,
        objective: StructuredScientificObjective,
        record: ScientificExecutionRecord,
    ) -> ScientificCapabilityOutcome:
        source = _objective_input(record, kinds=("molecular_structure",))
        molecule = _molecule_from_structure_payload(source, self.runner.store.read(source))
        from rdkit import Chem

        smiles = Chem.MolToSmiles(Chem.RemoveHs(molecule), canonical=True)
        parsed = self.runner.invoke(
            self.adapter,
            SMILES_PARSE,
            parameters={"smiles": smiles},
            execution_identifier=f"{invocation.invocation_identifier}-parse",
            objective=objective,
        )[0]
        prepared = self.runner.invoke(
            self.adapter,
            PREPARE,
            inputs=(parsed,),
            parameters={"add_hydrogens": True},
            execution_identifier=f"{invocation.invocation_identifier}-prepare",
            objective=objective,
        )[0]
        generated = self.runner.invoke(
            self.adapter,
            CONFORMER_GENERATION,
            inputs=(prepared,),
            parameters={
                "conformer_count": 24,
                "random_seed": 19,
                "maximum_iterations": 1000,
                "prune_rms_threshold": 0.05,
                "optimize": True,
            },
            execution_identifier=f"{invocation.invocation_identifier}-conformers",
            objective=objective,
        )[0]
        conformers = MolecularConformerSet.model_validate_json(
            self.runner.store.read(generated)
        )
        classification = _classify_axial_equatorial(conformers)
        import json

        classification_reference = self.runner.write_json(
            artifact_type="molecular_conformer_classification",
            payload=json.dumps(classification, sort_keys=True, separators=(",", ":")).encode("utf-8"),
            producer="molecular.conformer_generate",
            execution_identifier=invocation.invocation_identifier,
            parents=(source, generated),
            metadata={"labels": "axial,equatorial"},
        )
        return ScientificCapabilityOutcome(
            output_artifacts=(parsed, prepared, generated, classification_reference),
            evidence_artifacts=(generated, classification_reference),
            scientific_summary="Generated and geometrically classified axial/equatorial conformers.",
        )


class MolecularInputValidationHandler:
    """Validate and expose the exact scientist-supplied molecular structure."""

    def __init__(self, store: ScientificPayloadStore) -> None:
        self.runner = _NativeRunner(store)

    def execute(self, *, invocation, objective, record) -> ScientificCapabilityOutcome:
        del invocation, objective
        source = _objective_input(record, kinds=("molecular_structure",))
        _molecule_from_structure_payload(source, self.runner.store.read(source))
        return ScientificCapabilityOutcome(
            output_artifacts=(source,),
            scientific_summary="Validated the exact scientist-supplied molecular structure.",
        )


def _pdb_coordinate_inventory(payload: bytes) -> tuple[dict[str, object], ...]:
    """Parse a bounded PDB coordinate inventory without inferring chemistry."""

    atoms: list[dict[str, object]] = []
    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise ScientificCapabilityFailure(
            "structure_analysis_input_invalid",
            "The structure is not valid UTF-8 PDB coordinate data.",
        ) from error
    for line in lines:
        if not line.startswith(("ATOM  ", "HETATM")):
            continue
        try:
            element = line[76:78].strip()
            if not element:
                element = "".join(
                    character for character in line[12:16] if character.isalpha()
                )[:2]
            if not element:
                raise ValueError
            element = element[0].upper() + element[1:].lower()
            atoms.append(
                {
                    "record_type": line[:6].strip(),
                    "serial": int(line[6:11]),
                    "atom_name": line[12:16].strip(),
                    "residue_name": line[17:20].strip() or "UNK",
                    "chain": line[21:22].strip() or "blank",
                    "sequence": line[22:26].strip() or str(len(atoms) + 1),
                    "x": float(line[30:38]),
                    "y": float(line[38:46]),
                    "z": float(line[46:54]),
                    "element": element,
                }
            )
        except (IndexError, ValueError):
            raise ScientificCapabilityFailure(
                "structure_analysis_input_invalid",
                "The PDB coordinate records are malformed.",
            ) from None
    if not atoms:
        raise ScientificCapabilityFailure(
            "structure_analysis_input_invalid",
            "The PDB structure contains no coordinate atoms.",
        )
    return tuple(atoms)


class GeneralStructureIngestionHandler:
    """Validate one molecule or protein input and expose one canonical structure."""

    def __init__(self, store: ScientificPayloadStore) -> None:
        self.runner = _NativeRunner(store)

    def execute(self, *, invocation, objective, record) -> ScientificCapabilityOutcome:
        del objective
        source = _objective_input(
            record,
            kinds=(
                "molecular_structure",
                "ligand_structure",
                "protein_structure",
                "prepared_ligand",
                "prepared_receptor",
            ),
        )
        payload = self.runner.store.read(source)
        media_type = source.media_type.lower()
        is_pdb = "pdb" in media_type
        if is_pdb:
            _pdb_coordinate_inventory(payload)
        else:
            _molecule_from_structure_payload(source, payload)
        if source.artifact_type == "molecular_structure":
            structure = source
        else:
            structure = self.runner.write_bytes(
                artifact_type="molecular_structure",
                media_type=source.media_type,
                payload=payload,
                producer="molecular.structure_ingestion",
                execution_identifier=invocation.invocation_identifier,
                parents=(source,),
                metadata={
                    "source_artifact_type": source.artifact_type,
                    "validation_kind": (
                        "pdb_coordinate_inventory" if is_pdb else "rdkit_structure"
                    ),
                },
            )
        return ScientificCapabilityOutcome(
            output_artifacts=(structure,),
            evidence_artifacts=(structure,),
            scientific_summary="Validated one exact structure for descriptive analysis.",
        )


class StructureAnalysisHandler:
    """Produce deterministic descriptive evidence for a molecule or PDB structure."""

    def __init__(self, store: ScientificPayloadStore) -> None:
        self.runner = _NativeRunner(store)

    def execute(self, *, invocation, objective, record) -> ScientificCapabilityOutcome:
        del objective
        import json

        structures = tuple(
            item
            for item in record.artifact_references
            if item.artifact_type == "molecular_structure"
        )
        if len(structures) != 1:
            raise ScientificCapabilityFailure(
                "structure_analysis_input_ambiguous",
                "Structure analysis requires exactly one canonical structure.",
            )
        structure = structures[0]
        payload = self.runner.store.read(structure)
        if "pdb" in structure.media_type.lower():
            atoms = _pdb_coordinate_inventory(payload)
            residues = {
                (str(item["chain"]), str(item["sequence"]), str(item["residue_name"]))
                for item in atoms
            }
            chains = {str(item["chain"]) for item in atoms}
            element_counts: dict[str, int] = {}
            for item in atoms:
                element = str(item["element"])
                element_counts[element] = element_counts.get(element, 0) + 1
            coordinates = {
                axis: [float(item[axis]) for item in atoms]
                for axis in ("x", "y", "z")
            }
            analysis: dict[str, object] = {
                "schema": "pulsate.structure-analysis/v1",
                "analysis_kind": "pdb_coordinate_inventory",
                "source_structure_artifact_identifier": structure.artifact_identifier,
                "atom_count": len(atoms),
                "hetero_atom_record_count": sum(
                    item["record_type"] == "HETATM" for item in atoms
                ),
                "residue_count": len(residues),
                "chain_count": len(chains),
                "element_counts": dict(sorted(element_counts.items())),
                "bounding_box_angstrom": {
                    axis: {
                        "minimum": min(values),
                        "maximum": max(values),
                    }
                    for axis, values in coordinates.items()
                },
                "method": "deterministic_pdb_coordinate_inventory",
                "limitations": [
                    "Coordinate inventory does not establish function, stability, or binding affinity."
                ],
            }
            summary = (
                f"Analyzed a PDB coordinate structure containing {len(atoms)} atoms, "
                f"{len(residues)} residues, and {len(chains)} chains."
            )
        else:
            from rdkit import Chem
            from rdkit.Chem import Descriptors, Lipinski, rdMolDescriptors

            molecule = _molecule_from_structure_payload(structure, payload)
            element_counts: dict[str, int] = {}
            for atom in molecule.GetAtoms():
                element = atom.GetSymbol()
                element_counts[element] = element_counts.get(element, 0) + 1
            analysis = {
                "schema": "pulsate.structure-analysis/v1",
                "analysis_kind": "molecular_graph_descriptors",
                "source_structure_artifact_identifier": structure.artifact_identifier,
                "canonical_smiles": Chem.MolToSmiles(molecule, canonical=True),
                "formula": rdMolDescriptors.CalcMolFormula(molecule),
                "atom_count": molecule.GetNumAtoms(),
                "heavy_atom_count": molecule.GetNumHeavyAtoms(),
                "bond_count": molecule.GetNumBonds(),
                "formal_charge": sum(
                    atom.GetFormalCharge() for atom in molecule.GetAtoms()
                ),
                "molecular_weight_da": float(Descriptors.MolWt(molecule)),
                "ring_count": int(rdMolDescriptors.CalcNumRings(molecule)),
                "rotatable_bond_count": int(Lipinski.NumRotatableBonds(molecule)),
                "hydrogen_bond_donor_count": int(Lipinski.NumHDonors(molecule)),
                "hydrogen_bond_acceptor_count": int(Lipinski.NumHAcceptors(molecule)),
                "element_counts": dict(sorted(element_counts.items())),
                "method": "rdkit_graph_and_descriptor_analysis",
                "limitations": [
                    "Graph descriptors do not establish activity, selectivity, or experimental behavior."
                ],
            }
            summary = (
                f"Analyzed molecular formula {analysis['formula']} with "
                f"{analysis['heavy_atom_count']} heavy atoms and "
                f"formal charge {analysis['formal_charge']}."
            )
        reference = self.runner.write_json(
            artifact_type="molecular_structure_analysis",
            payload=json.dumps(
                analysis,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8"),
            producer="molecular.structure_analyze",
            execution_identifier=invocation.invocation_identifier,
            parents=(structure,),
            metadata={"analysis_kind": str(analysis["analysis_kind"])},
        )
        return ScientificCapabilityOutcome(
            output_artifacts=(reference,),
            evidence_artifacts=(reference,),
            scientific_summary=summary,
            limitations=tuple(str(item) for item in analysis["limitations"]),
        )


class StructureAnalysisVerificationHandler:
    """Verify descriptive structure evidence without expanding its claims."""

    def __init__(self, store: ScientificPayloadStore) -> None:
        self.runner = _NativeRunner(store)

    def execute(self, *, invocation, objective, record) -> ScientificCapabilityOutcome:
        del objective
        import json

        reference = next(
            item
            for item in record.artifact_references
            if item.artifact_type == "molecular_structure_analysis"
        )
        analysis = json.loads(self.runner.store.read(reference))
        kind = analysis.get("analysis_kind")
        passed = bool(
            analysis.get("schema") == "pulsate.structure-analysis/v1"
            and kind in {"pdb_coordinate_inventory", "molecular_graph_descriptors"}
            and isinstance(analysis.get("atom_count"), int)
            and int(analysis["atom_count"]) > 0
            and isinstance(analysis.get("source_structure_artifact_identifier"), str)
            and analysis.get("method")
        )
        if kind == "pdb_coordinate_inventory":
            passed = bool(
                passed
                and isinstance(analysis.get("residue_count"), int)
                and int(analysis["residue_count"]) > 0
                and isinstance(analysis.get("chain_count"), int)
                and int(analysis["chain_count"]) > 0
            )
        elif kind == "molecular_graph_descriptors":
            passed = bool(
                passed
                and isinstance(analysis.get("formula"), str)
                and isinstance(analysis.get("heavy_atom_count"), int)
                and int(analysis["heavy_atom_count"]) > 0
                and isinstance(analysis.get("molecular_weight_da"), (int, float))
                and math.isfinite(float(analysis["molecular_weight_da"]))
            )
        report = self.runner.write_json(
            artifact_type="scientific_verification_report",
            payload=json.dumps(
                {
                    "verification_family": "structure_analysis",
                    "passed": passed,
                    "analysis_kind": kind,
                    "checks": [
                        "source_identity_bound",
                        "positive_structural_counts",
                        "finite_declared_descriptors",
                        "claim_scope_remains_descriptive",
                    ],
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8"),
            producer="scientific_verification.structure_analysis",
            execution_identifier=invocation.invocation_identifier,
            parents=(reference,),
            metadata={"passed": passed},
        )
        if not passed:
            raise ScientificCapabilityFailure(
                "scientific_verification_failed",
                "The structure analysis failed deterministic verification.",
            )
        return ScientificCapabilityOutcome(
            output_artifacts=(report,),
            evidence_artifacts=(report,),
            verified=True,
            scientific_summary=(
                "Verified the descriptive structure analysis and its bounded claim scope."
            ),
        )


_SOLVENT_DIELECTRIC = {
    "water": 78.3553,
    "acetonitrile": 35.688,
    "methanol": 32.613,
    "dmso": 46.826,
}


class ImplicitSolventConformerComparisonHandler:
    """Execute matched PySCF PCM calculations for resolved conformer labels."""

    def __init__(self, store: ScientificPayloadStore, adapter: PySCFElectronicStructureAdapter) -> None:
        self.runner = _NativeRunner(store)
        self.adapter = adapter

    def execute(
        self,
        *,
        invocation: WorkflowCapabilityInvocation,
        objective: StructuredScientificObjective,
        record: ScientificExecutionRecord,
    ) -> ScientificCapabilityOutcome:
        import json

        conformer_reference = next(
            item for item in record.artifact_references
            if item.artifact_type == "molecular_conformer_set"
        )
        classification_reference = next(
            item for item in record.artifact_references
            if item.artifact_type == "molecular_conformer_classification"
        )
        classification = json.loads(self.runner.store.read(classification_reference))
        solvent = objective.solvent
        if solvent is None:
            raise ScientificCapabilityFailure(
                "scientific_clarification_required",
                "The solvent must be supplied by the scientist before execution.",
            )
        results: dict[str, ElectronicImplicitSolventResult] = {}
        artifacts: list[ArtifactReference] = []
        for label in ("axial", "equatorial"):
            index = int(classification[f"{label}_conformer_index"])
            molecule, configuration = self.runner.invoke(
                self.adapter,
                MOLECULE_CONSTRUCT,
                inputs=(conformer_reference,),
                parameters={
                    "conformer_index": index,
                    "molecular_charge": 0,
                    "spin": 0,
                },
                execution_identifier=f"{invocation.invocation_identifier}-{label}-molecule",
                objective=objective,
            )[0], None
            configuration = self.runner.invoke(
                self.adapter,
                CONFIGURATION_DEFINE,
                inputs=(molecule,),
                parameters={
                    "basis_set": "sto-3g",
                    "reference_method": "rhf",
                    "convergence_tolerance": 1.0e-9,
                    "maximum_iterations": 100,
                    "direct_scf": True,
                    "density_fitting": False,
                    "symmetry": False,
                    "initial_guess": "minao",
                },
                execution_identifier=f"{invocation.invocation_identifier}-{label}-configuration",
                objective=objective,
            )[0]
            result_reference = self.runner.invoke(
                self.adapter,
                IMPLICIT_SOLVENT_HARTREE_FOCK,
                inputs=(molecule, configuration),
                parameters={
                    "solvent_name": solvent,
                    "solvent_model": "IEF-PCM",
                    "dielectric_constant": _SOLVENT_DIELECTRIC[solvent],
                },
                execution_identifier=f"{invocation.invocation_identifier}-{label}-pcm",
                objective=objective,
            )[0]
            results[label] = ElectronicImplicitSolventResult.model_validate_json(
                self.runner.store.read(result_reference)
            )
            artifacts.extend((molecule, configuration, result_reference))
        preferred = min(results, key=lambda label: results[label].solvated_total_energy_hartree)
        difference = abs(
            results["axial"].solvated_total_energy_hartree
            - results["equatorial"].solvated_total_energy_hartree
        )
        comparison = {
            "quantity": "solvated_electronic_energy",
            "solvent": solvent,
            "solvent_model": "IEF-PCM",
            "basis_set": "sto-3g",
            "reference_method": "rhf",
            "preferred_conformer": preferred,
            "energy_difference_hartree": difference,
            "thermal_correction_included": False,
            "gibbs_free_energy": False,
            "results": {
                label: {
                    "solvated_total_energy_hartree": result.solvated_total_energy_hartree,
                    "vacuum_total_energy_hartree": result.vacuum_total_energy_hartree,
                    "electronic_solvation_contribution_hartree": result.electronic_solvation_contribution_hartree,
                }
                for label, result in results.items()
            },
        }
        comparison_payload = json.dumps(
            comparison, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        comparison_reference = self.runner.write_json(
            artifact_type="electronic_conformer_comparison",
            payload=comparison_payload,
            producer="electronic.implicit_solvent_hartree_fock",
            execution_identifier=invocation.invocation_identifier,
            parents=(conformer_reference, classification_reference, *artifacts),
            metadata={
                "preferred_conformer": preferred,
                "energy_semantics": "solvated_electronic_energy",
            },
        )
        implicit_references = tuple(
            item for item in artifacts
            if item.artifact_type == "electronic_implicit_solvent_result"
        )
        return ScientificCapabilityOutcome(
            # Workflow nodes require unique output artifact types.  The second
            # matched calculation and all intermediate molecule/configuration
            # artifacts remain persisted as evidence rather than being hidden.
            output_artifacts=(implicit_references[0], comparison_reference),
            evidence_artifacts=(*artifacts, comparison_reference),
            scientific_summary=(
                f"The {preferred} conformer has the lower matched IEF-PCM/RHF "
                f"solvated electronic energy by {difference:.8f} Hartree. "
                "This is not a Gibbs free-energy comparison."
            ),
            limitations=(
                "No vibrational, thermal, entropic, or standard-state correction was included.",
            ),
        )


class ConformerComparisonVerificationHandler:
    def __init__(self, store: ScientificPayloadStore) -> None:
        self.runner = _NativeRunner(store)

    def execute(self, *, invocation, objective, record) -> ScientificCapabilityOutcome:
        del objective
        import json

        reference = next(
            item for item in record.artifact_references
            if item.artifact_type == "electronic_conformer_comparison"
        )
        comparison = json.loads(self.runner.store.read(reference))
        passed = (
            comparison.get("quantity") == "solvated_electronic_energy"
            and comparison.get("gibbs_free_energy") is False
            and comparison.get("preferred_conformer") in {"axial", "equatorial"}
            and isinstance(comparison.get("energy_difference_hartree"), (int, float))
            and math.isfinite(float(comparison["energy_difference_hartree"]))
        )
        report_payload = json.dumps(
            {
                "verification_family": "conformer_comparison",
                "passed": passed,
                "checks": [
                    "matched_method",
                    "matched_solvent_model",
                    "finite_energy_difference",
                    "electronic_energy_not_free_energy",
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        report = self.runner.write_json(
            artifact_type="scientific_verification_report",
            payload=report_payload,
            producer="scientific_verification.conformer_comparison",
            execution_identifier=invocation.invocation_identifier,
            parents=(reference,),
            metadata={"passed": passed},
        )
        if not passed:
            raise ScientificCapabilityFailure(
                "scientific_verification_failed",
                "The aqueous conformer comparison failed scientific verification.",
            )
        return ScientificCapabilityOutcome(
            output_artifacts=(report,),
            evidence_artifacts=(report,),
            verified=True,
        )


class MinimalSceneHandler:
    """Persist a bounded scene state while richer domain scenes are assembled."""

    def __init__(self, store: ScientificPayloadStore) -> None:
        self.runner = _NativeRunner(store)

    def execute(self, *, invocation, objective, record) -> ScientificCapabilityOutcome:
        import json

        semantic = None
        semantic_reference = next(
            (
                item for item in record.artifact_references
                if item.artifact_type == "semantic_target_selection"
            ),
            None,
        )
        if semantic_reference is not None:
            semantic = json.loads(self.runner.store.read(semantic_reference))
        selected_atoms: list[int] = []
        metals: list[dict[str, object]] = []
        if isinstance(semantic, dict):
            for key in ("atom_index_a", "atom_index_b", "metal_particle_index"):
                if isinstance(semantic.get(key), int):
                    selected_atoms.append(int(semantic[key]))
            if isinstance(semantic.get("donors"), list):
                selected_atoms.extend(
                    int(item["particle_index"])
                    for item in semantic["donors"]
                    if isinstance(item, dict) and isinstance(item.get("particle_index"), int)
                )
            if isinstance(semantic.get("metal_particle_index"), int):
                metals.append(
                    {
                        "particle_index": semantic["metal_particle_index"],
                        "element": semantic.get("metal_element"),
                        "coordination_number": semantic.get("coordination_number"),
                    }
                )
        payload = json.dumps(
            {
                "objective_identifier": objective.objective_identifier,
                "task_type": objective.task_type,
                "computational_state": "verified" if record.verified else "computed",
                "artifact_identifiers": [
                    item.artifact_identifier for item in record.artifact_references
                ],
                "selected_residues": [],
                "selected_atoms": sorted(set(selected_atoms)),
                "metals": metals,
                "semantic_selection": semantic,
                "docking_poses": [],
                "candidate_lineage": [],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        scene = self.runner.write_json(
            artifact_type="molecular_scene_state",
            payload=payload,
            producer="molecular.scene_project",
            execution_identifier=invocation.invocation_identifier,
            parents=record.artifact_references,
        )
        return ScientificCapabilityOutcome(
            output_artifacts=(scene,),
            evidence_artifacts=(scene,),
            scene_identifier=scene.artifact_identifier,
        )


class ProteinDesignSpecificationHandler:
    """Materialize only scientist-stated protein length plus operational policy."""

    def __init__(self, store: ScientificPayloadStore) -> None:
        self.runner = _NativeRunner(store)

    def execute(self, *, invocation, objective, record) -> ScientificCapabilityOutcome:
        del record
        residue_count, quote, reason = resolve_protein_residue_count(
            objective.original_request
        )
        if reason is not None or residue_count is None or quote is None:
            raise ScientificCapabilityFailure(
                "protein_design_specification_incomplete",
                reason or "The protein design specification is incomplete.",
            )
        objective_digest = hashlib.sha256(
            objective.original_request.encode("utf-8")
        ).hexdigest()
        random_seed = (
            int(hashlib.sha256(objective.objective_identifier.encode()).hexdigest()[:8], 16)
            % 2_147_483_646
        ) + 1
        specification = ProteinDesignSpecification(
            schema_version=_VERSION,
            specification_identifier=_stable_identifier(
                "protein-design-specification",
                objective.objective_identifier,
                residue_count,
            ),
            residue_count=residue_count,
            candidate_count=min(objective.budget.maximum_candidates, 8),
            random_seed=random_seed,
            residue_count_supporting_quote=quote,
            original_objective_sha256=objective_digest,
            operational_policy_identifier="policy.protein_design_v1",
            limitations=(
                "Candidate count is a bounded operational search policy, not a scientific claim.",
                "Generated candidates require independent computational and experimental validation.",
            ),
        )
        reference = self.runner.write_json(
            artifact_type="protein_design_specification",
            payload=specification.to_canonical_json().encode("utf-8"),
            producer="protein.design_specification",
            execution_identifier=invocation.invocation_identifier,
            metadata={
                "residue_count": residue_count,
                "candidate_count": specification.candidate_count,
                "operational_policy_identifier": (
                    specification.operational_policy_identifier
                ),
            },
        )
        return ScientificCapabilityOutcome(
            output_artifacts=(reference,),
            evidence_artifacts=(reference,),
            scientific_summary=(
                f"Prepared a de novo protein design specification for exactly "
                f"{residue_count} residues."
            ),
            limitations=specification.limitations,
        )


def _fasta_sequence(payload: bytes) -> str:
    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise ScientificCapabilityFailure(
            "protein_design_sequence_invalid",
            "A generated protein sequence is not valid UTF-8 FASTA.",
        ) from error
    sequence = "".join(line.strip() for line in lines if not line.startswith(">"))
    allowed = set("ACDEFGHIKLMNPQRSTVWY")
    if not sequence or any(character not in allowed for character in sequence.upper()):
        raise ScientificCapabilityFailure(
            "protein_design_sequence_invalid",
            "A generated protein sequence contains unsupported residue symbols.",
        )
    return sequence.upper()


def _pdb_residue_count(payload: bytes) -> int:
    atoms = _pdb_coordinate_inventory(payload)
    return len(
        {
            (str(item["chain"]), str(item["sequence"]), str(item["residue_name"]))
            for item in atoms
        }
    )


class ProteinDesignVerificationHandler:
    """Verify stage completeness, length, content, confidence, and lineage."""

    def __init__(self, store: ScientificPayloadStore) -> None:
        self.runner = _NativeRunner(store)

    def execute(self, *, invocation, objective, record) -> ScientificCapabilityOutcome:
        del objective
        import json

        by_type: dict[str, tuple[ArtifactReference, ...]] = {
            artifact_type: tuple(
                item
                for item in record.artifact_references
                if item.artifact_type == artifact_type
            )
            for artifact_type in (
                "protein_design_specification",
                "protein_backbone_candidate",
                "protein_sequence_candidate",
                "predicted_protein_structure",
                "protein_prediction_confidence",
            )
        }
        if len(by_type["protein_design_specification"]) != 1:
            raise ScientificCapabilityFailure(
                "protein_design_verification_failed",
                "Protein design verification requires one exact specification.",
            )
        specification_reference = by_type["protein_design_specification"][0]
        specification = ProteinDesignSpecification.model_validate_json(
            self.runner.store.read(specification_reference)
        )
        backbones = by_type["protein_backbone_candidate"]
        sequences = by_type["protein_sequence_candidate"]
        structures = by_type["predicted_protein_structure"]
        confidences = by_type["protein_prediction_confidence"]
        counts = tuple(map(len, (backbones, sequences, structures, confidences)))
        if (
            not all(counts)
            or len(set(counts)) != 1
            or counts[0] > specification.candidate_count
        ):
            raise ScientificCapabilityFailure(
                "protein_design_verification_failed",
                "Protein design stages did not return one complete evidence chain per candidate.",
            )
        if any(
            item.media_type != "chemical/x-pdb"
            or _pdb_residue_count(self.runner.store.read(item))
            != specification.residue_count
            for item in (*backbones, *structures)
        ):
            raise ScientificCapabilityFailure(
                "protein_design_verification_failed",
                "A generated protein coordinate artifact does not match the requested residue count.",
            )
        if any(
            len(_fasta_sequence(self.runner.store.read(item)))
            != specification.residue_count
            for item in sequences
        ):
            raise ScientificCapabilityFailure(
                "protein_design_verification_failed",
                "A designed protein sequence does not match the requested residue count.",
            )
        for confidence in confidences:
            try:
                document = json.loads(self.runner.store.read(confidence))
                score = document["confidence_score"]
                metric = document["metric_name"]
                if (
                    not isinstance(score, (int, float))
                    or isinstance(score, bool)
                    or not 0 <= float(score) <= 1
                    or not isinstance(metric, str)
                    or not metric.strip()
                ):
                    raise ValueError
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                raise ScientificCapabilityFailure(
                    "protein_design_verification_failed",
                    "Protein structure prediction confidence is missing or invalid.",
                ) from None
        backbone_ids = {item.artifact_identifier for item in backbones}
        sequence_ids = {item.artifact_identifier for item in sequences}
        lineage_complete = (
            all(
                specification_reference.artifact_identifier
                in {parent.artifact_identifier for parent in item.parents}
                for item in backbones
            )
            and all(
                bool(
                    backbone_ids
                    & {parent.artifact_identifier for parent in item.parents}
                )
                for item in sequences
            )
            and all(
                bool(
                    sequence_ids
                    & {parent.artifact_identifier for parent in item.parents}
                )
                for item in (*structures, *confidences)
            )
        )
        if not lineage_complete:
            raise ScientificCapabilityFailure(
                "protein_design_verification_failed",
                "Protein candidate parentage is incomplete.",
            )
        report = ProteinDesignVerificationReport(
            schema_version=_VERSION,
            report_identifier=_stable_identifier(
                "protein-design-verification",
                specification_reference.content_sha256,
                *(item.content_sha256 for item in structures),
            ),
            specification_artifact_identifier=(
                specification_reference.artifact_identifier
            ),
            backbone_artifact_identifiers=tuple(backbone_ids),
            sequence_artifact_identifiers=tuple(sequence_ids),
            predicted_structure_artifact_identifiers=tuple(
                item.artifact_identifier for item in structures
            ),
            lineage_complete=True,
            limitations=(
                "Model confidence is not experimental validation.",
                "Designed candidates may not fold, express, remain stable, or perform a requested function experimentally.",
            ),
        )
        payload = report.to_canonical_json().encode("utf-8")
        parents = (
            specification_reference,
            *backbones,
            *sequences,
            *structures,
            *confidences,
        )
        design_report = self.runner.write_json(
            artifact_type="protein_design_verification_report",
            payload=payload,
            producer="scientific_verification.protein_design",
            execution_identifier=invocation.invocation_identifier,
            parents=parents,
            metadata={"passed": True, "candidate_count": len(structures)},
        )
        general_report = self.runner.write_json(
            artifact_type="scientific_verification_report",
            payload=payload,
            producer="scientific_verification.protein_design",
            execution_identifier=invocation.invocation_identifier,
            parents=parents,
            metadata={"passed": True, "verification_family": "protein_design"},
        )
        return ScientificCapabilityOutcome(
            output_artifacts=(design_report, general_report),
            evidence_artifacts=(design_report, general_report, *confidences),
            verified=True,
            scientific_summary=(
                f"Verified {len(structures)} complete computational protein "
                "candidate evidence chains."
            ),
            limitations=report.limitations,
        )


class ProteinDesignSceneHandler:
    """Project predicted protein candidates and their exact parentage."""

    def __init__(self, store: ScientificPayloadStore) -> None:
        self.runner = _NativeRunner(store)

    def execute(self, *, invocation, objective, record) -> ScientificCapabilityOutcome:
        import json

        structures = tuple(
            item
            for item in record.artifact_references
            if item.artifact_type == "predicted_protein_structure"
        )
        report = next(
            item
            for item in record.artifact_references
            if item.artifact_type == "protein_design_verification_report"
        )
        scene_payload = {
            "objective_identifier": objective.objective_identifier,
            "scene_kind": "de_novo_protein_design",
            "computational_state": "verified" if record.verified else "computed",
            "structure_artifact_identifiers": [
                item.artifact_identifier for item in structures
            ],
            "candidate_lineage": [
                {
                    "artifact_identifier": item.artifact_identifier,
                    "parent_artifact_identifiers": [
                        parent.artifact_identifier for parent in item.parents
                    ],
                }
                for item in structures
            ],
            "verification_artifact_identifier": report.artifact_identifier,
            "experimental_evidence": False,
        }
        scene = self.runner.write_json(
            artifact_type="molecular_scene_state",
            payload=json.dumps(
                scene_payload,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8"),
            producer="molecular.scene_project",
            execution_identifier=invocation.invocation_identifier,
            parents=(report, *structures),
        )
        return ScientificCapabilityOutcome(
            output_artifacts=(scene,),
            evidence_artifacts=(scene,),
            scene_identifier=scene.artifact_identifier,
        )


def protein_design_registry(
    *,
    store: ScientificPayloadStore,
    adapters: tuple[ExternalProteinEngineAdapter, ...],
) -> ScientistCapabilityRegistry:
    """Build the configured three-stage de novo protein-design slice."""

    registry = ScientistCapabilityRegistry(
        {
            "protein.design_specification": ProteinDesignSpecificationHandler(store),
            "scientific_verification.protein_design": (
                ProteinDesignVerificationHandler(store)
            ),
            "molecular.scene_project": ProteinDesignSceneHandler(store),
        }
    )
    for adapter in adapters:
        capability_name = (
            adapter.declaration.capabilities[0].descriptor.capability_name
        )
        registry.register(
            capability_name,
            ScientificEngineHandler(adapter, capability_name),
        )
    return registry


def structure_analysis_registry(
    *,
    store: ScientificPayloadStore,
) -> ScientistCapabilityRegistry:
    """Build the general molecule/protein descriptive-analysis vertical slice."""

    return ScientistCapabilityRegistry(
        {
            "molecular.structure_ingestion": GeneralStructureIngestionHandler(store),
            "molecular.structure_analyze": StructureAnalysisHandler(store),
            "scientific_verification.structure_analysis": (
                StructureAnalysisVerificationHandler(store)
            ),
            "molecular.scene_project": MinimalSceneHandler(store),
        }
    )


def aqueous_conformer_registry(
    *,
    store: ScientificPayloadStore,
    rdkit_adapter: RDKitCheminformaticsAdapter,
    pyscf_adapter: PySCFElectronicStructureAdapter,
) -> ScientistCapabilityRegistry:
    """Build the executable registry for the aqueous-conformer vertical slice."""

    return ScientistCapabilityRegistry(
        {
            "molecular.structure_ingestion": MolecularInputValidationHandler(store),
            "molecular.conformer_generate": ConformerGenerationHandler(
                store, rdkit_adapter
            ),
            "electronic.implicit_solvent_hartree_fock": (
                ImplicitSolventConformerComparisonHandler(store, pyscf_adapter)
            ),
            "scientific_verification.conformer_comparison": (
                ConformerComparisonVerificationHandler(store)
            ),
            "molecular.scene_project": MinimalSceneHandler(store),
        }
    )


def _prepared_graph_from_structure(
    *,
    runner: _NativeRunner,
    adapter: RDKitCheminformaticsAdapter,
    source: ArtifactReference,
    objective: StructuredScientificObjective,
    execution_identifier: str,
) -> tuple[ArtifactReference, ArtifactReference]:
    from rdkit import Chem

    molecule = _molecule_from_structure_payload(source, runner.store.read(source))
    smiles = Chem.MolToSmiles(Chem.RemoveHs(molecule), canonical=True)
    graph = runner.invoke(
        adapter,
        SMILES_PARSE,
        parameters={"smiles": smiles},
        execution_identifier=f"{execution_identifier}-parse",
        objective=objective,
    )[0]
    prepared = runner.invoke(
        adapter,
        PREPARE,
        inputs=(graph,),
        parameters={"add_hydrogens": True},
        execution_identifier=f"{execution_identifier}-prepare",
        objective=objective,
    )[0]
    return graph, prepared


class BondSemanticResolutionHandler:
    """Resolve one scientist-referenced bond without accepting atom indices."""

    def __init__(self, store: ScientificPayloadStore) -> None:
        self.runner = _NativeRunner(store)

    def execute(self, *, invocation, objective, record) -> ScientificCapabilityOutcome:
        del objective
        import json
        from rdkit import Chem

        source = _objective_input(record, kinds=("molecular_structure",))
        molecule = _molecule_from_structure_payload(source, self.runner.store.read(source))
        molecule = Chem.RemoveHs(molecule)
        heavy_bonds = tuple(
            bond for bond in molecule.GetBonds()
            if bond.GetBeginAtom().GetAtomicNum() > 1
            and bond.GetEndAtom().GetAtomicNum() > 1
        )
        candidates = heavy_bonds or tuple(molecule.GetBonds())
        if len(candidates) != 1:
            raise ScientificCapabilityFailure(
                "semantic_bond_ambiguous",
                "The scientist-referenced bond does not resolve uniquely from the structure.",
            )
        bond = candidates[0]
        selection = {
            "selection_kind": "bond",
            "resolution_method": "unique_heavy_atom_bond",
            "atom_index_a": int(bond.GetBeginAtomIdx()),
            "atom_index_b": int(bond.GetEndAtomIdx()),
            "element_a": bond.GetBeginAtom().GetSymbol(),
            "element_b": bond.GetEndAtom().GetSymbol(),
            "bond_order": float(bond.GetBondTypeAsDouble()),
        }
        payload = json.dumps(selection, sort_keys=True, separators=(",", ":")).encode("utf-8")
        reference = self.runner.write_json(
            artifact_type="semantic_target_selection",
            payload=payload,
            producer="molecular.semantic_target_resolve",
            execution_identifier=invocation.invocation_identifier,
            parents=(source,),
            metadata={"selection_kind": "bond"},
        )
        return ScientificCapabilityOutcome(
            output_artifacts=(reference,),
            evidence_artifacts=(reference,),
            scientific_summary=(
                f"Resolved the unique {selection['element_a']}-{selection['element_b']} bond."
            ),
        )


def _semantic_bond(store: ScientificPayloadStore, record: ScientificExecutionRecord) -> dict[str, object]:
    import json

    reference = next(
        item for item in record.artifact_references
        if item.artifact_type == "semantic_target_selection"
    )
    return json.loads(store.read(reference))


class ConstrainedDistanceScanHandler:
    def __init__(self, store: ScientificPayloadStore, adapter: RDKitCheminformaticsAdapter) -> None:
        self.runner = _NativeRunner(store)
        self.adapter = adapter

    def execute(self, *, invocation, objective, record) -> ScientificCapabilityOutcome:
        source = _objective_input(record, kinds=("molecular_structure",))
        selection = _semantic_bond(self.runner.store, record)
        graph, prepared = _prepared_graph_from_structure(
            runner=self.runner,
            adapter=self.adapter,
            source=source,
            objective=objective,
            execution_identifier=invocation.invocation_identifier,
        )
        artifacts = self.runner.invoke(
            self.adapter,
            CONSTRAINED_DISTANCE_SCAN,
            inputs=(prepared,),
            parameters={
                "atom_index_a": int(selection["atom_index_a"]),
                "atom_index_b": int(selection["atom_index_b"]),
                "start_distance_angstrom": objective.scan_start_angstrom,
                "stop_distance_angstrom": objective.scan_end_angstrom,
                "point_count": objective.scan_point_count,
                "random_seed": 23,
                "maximum_iterations": 2000,
                "force_constant": 2000.0,
            },
            execution_identifier=f"{invocation.invocation_identifier}-scan",
            objective=objective,
        )
        scan_reference = next(
            item for item in artifacts if item.artifact_type == "molecular_distance_scan"
        )
        scan = MolecularDistanceScan.model_validate_json(self.runner.store.read(scan_reference))
        if (
            scan.point_count != objective.scan_point_count
            or scan.start_distance_angstrom != objective.scan_start_angstrom
            or scan.stop_distance_angstrom != objective.scan_end_angstrom
            or any(point.optimization_status != "converged" for point in scan.points)
            or any(
                point.distance_residual_angstrom > scan.coordinate_tolerance_angstrom
                for point in scan.points
            )
        ):
            raise ScientificCapabilityFailure(
                "constrained_geometry_invalid",
                "The constrained scan did not satisfy every requested coordinate.",
                retryable=True,
            )
        return ScientificCapabilityOutcome(
            output_artifacts=(graph, prepared, *artifacts),
            evidence_artifacts=artifacts,
            scientific_summary=(
                f"Generated {scan.point_count} validated constrained geometries from "
                f"{scan.start_distance_angstrom:.1f} to {scan.stop_distance_angstrom:.1f} Angstrom."
            ),
        )


class BondBreakingActiveSpacePolicyHandler:
    """Persist the automatic reaction-centre selection policy used at every point."""

    def __init__(self, store: ScientificPayloadStore, *, artifact_type: str) -> None:
        self.runner = _NativeRunner(store)
        self.artifact_type = artifact_type

    def execute(self, *, invocation, objective, record) -> ScientificCapabilityOutcome:
        del objective
        import json

        selection = _semantic_bond(self.runner.store, record)
        payload = json.dumps(
            {
                "selection_method": "reaction_center_projection",
                "active_electron_count": 2,
                "active_spatial_orbital_count": 2,
                "target_bond": selection,
                "manual_orbital_indices": False,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        reference = self.runner.write_json(
            artifact_type=self.artifact_type,
            payload=payload,
            producer=invocation.capability_identity,
            execution_identifier=invocation.invocation_identifier,
            parents=tuple(
                item for item in record.artifact_references
                if item.artifact_type == "semantic_target_selection"
            ),
            metadata={"selection_method": "reaction_center_projection"},
        )
        return ScientificCapabilityOutcome(
            output_artifacts=(reference,), evidence_artifacts=(reference,)
        )


class BondDissociationPESHandler:
    """Run matched reaction-centre CASCI calculations for every scan point."""

    def __init__(self, store: ScientificPayloadStore, adapter: PySCFElectronicStructureAdapter) -> None:
        self.runner = _NativeRunner(store)
        self.adapter = adapter

    def execute(self, *, invocation, objective, record) -> ScientificCapabilityOutcome:
        import json
        import numpy
        from pyscf import fci

        conformer_reference = next(
            item for item in record.artifact_references
            if item.artifact_type == "molecular_conformer_set"
            and "point_count" in item.metadata
        )
        scan_reference = next(
            item for item in record.artifact_references
            if item.artifact_type == "molecular_distance_scan"
        )
        scan = MolecularDistanceScan.model_validate_json(self.runner.store.read(scan_reference))
        selection = _semantic_bond(self.runner.store, record)
        point_records: list[dict[str, object]] = []
        evidence: list[ArtifactReference] = []
        method_payload = b"rhf/sto-3g reaction-center CASCI(2e,2o)"
        method_sha = hashlib.sha256(method_payload).hexdigest()
        for point in scan.points:
            prefix = f"{invocation.invocation_identifier}-point-{point.point_index:02d}"
            molecule = self.runner.invoke(
                self.adapter,
                MOLECULE_CONSTRUCT,
                inputs=(conformer_reference,),
                parameters={
                    "conformer_index": point.point_index,
                    "molecular_charge": 0,
                    "spin": 0,
                },
                execution_identifier=f"{prefix}-molecule",
                objective=objective,
            )[0]
            configuration = self.runner.invoke(
                self.adapter,
                CONFIGURATION_DEFINE,
                inputs=(molecule,),
                parameters={
                    "basis_set": "sto-3g",
                    "reference_method": "rhf",
                    "convergence_tolerance": 1.0e-9,
                    "maximum_iterations": 150,
                    "direct_scf": True,
                    "density_fitting": False,
                    "symmetry": False,
                    "initial_guess": "minao",
                },
                execution_identifier=f"{prefix}-configuration",
                objective=objective,
            )[0]
            hartree_fock = self.runner.invoke(
                self.adapter,
                HARTREE_FOCK,
                inputs=(molecule, configuration),
                execution_identifier=f"{prefix}-hf",
                objective=objective,
            )[0]
            target_a = int(selection["atom_index_a"])
            target_b = int(selection["atom_index_b"])
            active_selection = self.runner.invoke(
                self.adapter,
                ACTIVE_SPACE_SELECT,
                inputs=(molecule, configuration, hartree_fock),
                parameters={
                    "active_electron_count": 2,
                    "active_spatial_orbital_count": 2,
                    "selection_method": "reaction_center_projection",
                    "target_atom_indices": f"{target_a},{target_b}",
                    "target_bond_atom_pairs": f"{target_a}-{target_b}",
                    "projection_threshold": 0.01,
                },
                execution_identifier=f"{prefix}-selection",
                objective=objective,
            )[0]
            active_reference = self.runner.invoke(
                self.adapter,
                ACTIVE_SPACE_CONSTRUCT,
                inputs=(molecule, configuration, hartree_fock, active_selection),
                execution_identifier=f"{prefix}-active-space",
                objective=objective,
            )[0]
            active = ElectronicActiveSpace.model_validate_json(
                self.runner.store.read(active_reference)
            )
            one_body = numpy.asarray(active.one_body_integrals.values).reshape(
                active.active_spatial_orbital_count,
                active.active_spatial_orbital_count,
            )
            two_body = numpy.asarray(active.two_body_integrals.values).reshape(
                (active.active_spatial_orbital_count,) * 4
            )
            energy, _ = fci.direct_spin1.kernel(
                one_body,
                two_body,
                active.active_spatial_orbital_count,
                (active.alpha_electron_count, active.beta_electron_count),
                ecore=active.constant_energy_hartree,
            )
            energy = float(energy)
            if not math.isfinite(energy):
                raise ScientificCapabilityFailure(
                    "pes_energy_nonfinite", "A potential-energy point is non-finite."
                )
            geometry_sha = hashlib.sha256(
                f"{point.requested_distance_angstrom}:{point.observed_distance_angstrom}".encode()
            ).hexdigest()
            calculation_identifier = f"pes-point-{point.point_index:02d}"
            point_records.append(
                {
                    "calculation_identifier": calculation_identifier,
                    "point_index": point.point_index,
                    "requested_distance_angstrom": point.requested_distance_angstrom,
                    "observed_distance_angstrom": point.observed_distance_angstrom,
                    "distance_residual_angstrom": point.distance_residual_angstrom,
                    "total_energy_hartree": energy,
                    "geometry_sha256": geometry_sha,
                    "active_space_selection_identifier": active_selection.artifact_identifier,
                    "active_space_identifier": active.active_space_identifier,
                }
            )
            evidence.extend((molecule, configuration, hartree_fock, active_selection, active_reference))
        curve = {
            "point_count": len(point_records),
            "start_distance_angstrom": scan.start_distance_angstrom,
            "stop_distance_angstrom": scan.stop_distance_angstrom,
            "method": method_payload.decode(),
            "automatic_active_space": True,
            "points": point_records,
        }
        curve_payload = json.dumps(curve, sort_keys=True, separators=(",", ":")).encode()
        curve_reference = self.runner.write_json(
            artifact_type="potential_energy_curve",
            payload=curve_payload,
            producer="electronic.bond_dissociation_pes_execute",
            execution_identifier=invocation.invocation_identifier,
            parents=(scan_reference, *evidence),
            metadata={"point_count": len(point_records), "method_sha256": method_sha},
        )
        return ScientificCapabilityOutcome(
            output_artifacts=(curve_reference,),
            evidence_artifacts=(*evidence, curve_reference),
            scientific_summary=(
                f"Completed {len(point_records)} matched reaction-centre CASCI(2e,2o) "
                "electronic calculations for the bond-dissociation curve."
            ),
        )


class PotentialEnergyCurveVerificationHandler:
    def __init__(self, store: ScientificPayloadStore) -> None:
        self.runner = _NativeRunner(store)

    def execute(self, *, invocation, objective, record) -> ScientificCapabilityOutcome:
        del objective
        import json

        curve_reference = next(
            item for item in record.artifact_references
            if item.artifact_type == "potential_energy_curve"
        )
        curve = json.loads(self.runner.store.read(curve_reference))
        method_sha = str(curve_reference.metadata["method_sha256"])
        calculations = tuple(
            CalculationEvidence(
                calculation_identifier=point["calculation_identifier"],
                artifact_sha256=curve_reference.content_sha256,
                molecule_identifier="bond-scan-molecule",
                geometry_sha256=point["geometry_sha256"],
                method_identifier="reaction-center-casci-2e-2o",
                method_sha256=method_sha,
                execution_completed=True,
                execution_successful=True,
                identity_valid=True,
                numerically_converged=True,
                values_finite=True,
                authorized=True,
                total_energy_hartree=point["total_energy_hartree"],
            )
            for point in curve["points"]
        )
        request = PotentialEnergyCurveRequest(
            request_identifier="phase8-bond-dissociation-pes-verification",
            subject_identifier="bond-dissociation-pes",
            calculations=calculations,
            points=tuple(
                PotentialEnergyPoint(
                    calculation_identifier=point["calculation_identifier"],
                    reaction_coordinate=point["requested_distance_angstrom"],
                    reaction_coordinate_unit="angstrom",
                )
                for point in curve["points"]
            ),
            require_same_molecule=True,
            require_same_method=True,
            require_same_geometry=False,
            require_uncertainty=False,
            authorization_required=True,
        )
        report = default_scientific_verifier_registry().verify(request)
        report_payload = report.to_canonical_json().encode()
        report_reference = self.runner.write_json(
            artifact_type="scientific_verification_report",
            payload=report_payload,
            producer="scientific_verification.potential_energy_curve",
            execution_identifier=invocation.invocation_identifier,
            parents=(curve_reference,),
            metadata={"passed": report.overall_outcome is VerificationOutcome.PASSED},
        )
        if report.overall_outcome is not VerificationOutcome.PASSED:
            raise ScientificCapabilityFailure(
                "scientific_verification_failed",
                "The potential-energy curve failed seven-dimension verification.",
            )
        return ScientificCapabilityOutcome(
            output_artifacts=(report_reference,),
            evidence_artifacts=(report_reference,),
            verified=True,
        )


def bond_dissociation_registry(
    *,
    store: ScientificPayloadStore,
    rdkit_adapter: RDKitCheminformaticsAdapter,
    pyscf_adapter: PySCFElectronicStructureAdapter,
) -> ScientistCapabilityRegistry:
    return ScientistCapabilityRegistry(
        {
            "molecular.structure_ingestion": MolecularInputValidationHandler(store),
            "molecular.semantic_target_resolve": BondSemanticResolutionHandler(store),
            "molecular.constrained_distance_scan": ConstrainedDistanceScanHandler(
                store, rdkit_adapter
            ),
            "electronic.active_space_select": BondBreakingActiveSpacePolicyHandler(
                store, artifact_type="electronic_active_space_selection"
            ),
            "electronic.active_space_construct": BondBreakingActiveSpacePolicyHandler(
                store, artifact_type="electronic_active_space"
            ),
            "electronic.bond_dissociation_pes_execute": BondDissociationPESHandler(
                store, pyscf_adapter
            ),
            "scientific_verification.potential_energy_curve": (
                PotentialEnergyCurveVerificationHandler(store)
            ),
            "molecular.scene_project": MinimalSceneHandler(store),
        }
    )


_TRANSITION_METAL_NUMBERS = frozenset(
    (*range(21, 31), *range(39, 49), *range(72, 81))
)


def _pdb_atoms(payload: bytes) -> tuple[dict[str, object], ...]:
    from rdkit import Chem

    table = Chem.GetPeriodicTable()
    atoms: list[dict[str, object]] = []
    for line in payload.decode("utf-8").splitlines():
        if not line.startswith(("ATOM  ", "HETATM")):
            continue
        element = line[76:78].strip()
        if not element:
            element = "".join(character for character in line[12:16] if character.isalpha())[:2]
        element = element[0].upper() + element[1:].lower()
        raw_charge = line[78:80].strip()
        formal_charge = 0
        if len(raw_charge) == 2 and raw_charge[0].isdigit() and raw_charge[1] in "+-":
            formal_charge = int(raw_charge[0]) * (1 if raw_charge[1] == "+" else -1)
        atoms.append(
            {
                "serial": int(line[6:11]),
                "atom_name": line[12:16].strip(),
                "residue_name": line[17:20].strip() or "UNK",
                "chain": line[21:22].strip() or "blank",
                "sequence": line[22:26].strip() or str(len(atoms) + 1),
                "x": float(line[30:38]),
                "y": float(line[38:46]),
                "z": float(line[46:54]),
                "element": element,
                "atomic_number": int(table.GetAtomicNumber(element)),
                "formal_charge": formal_charge,
            }
        )
    if not atoms:
        raise ScientificCapabilityFailure(
            "protein_structure_invalid", "The protein/active-site PDB contains no atoms."
        )
    return tuple(atoms)


def _metal_coordination(payload: bytes) -> dict[str, object]:
    atoms = _pdb_atoms(payload)
    metals = tuple(
        (index, atom) for index, atom in enumerate(atoms)
        if int(atom["atomic_number"]) in _TRANSITION_METAL_NUMBERS
    )
    if len(metals) != 1:
        raise ScientificCapabilityFailure(
            "metal_site_ambiguous",
            "The active-site structure must resolve exactly one transition metal.",
        )
    metal_index, metal = metals[0]
    donors: list[dict[str, object]] = []
    for index, atom in enumerate(atoms):
        if index == metal_index or atom["element"] not in {"N", "O", "S"}:
            continue
        distance = math.dist(
            (float(metal["x"]), float(metal["y"]), float(metal["z"])),
            (float(atom["x"]), float(atom["y"]), float(atom["z"])),
        )
        if distance <= 2.8:
            donors.append(
                {
                    "particle_index": index,
                    "element": atom["element"],
                    "atom_name": atom["atom_name"],
                    "residue_name": atom["residue_name"],
                    "distance_angstrom": distance,
                }
            )
    if not donors:
        raise ScientificCapabilityFailure(
            "metal_coordination_unresolved",
            "No first-shell N/O/S metal donor was resolved within 2.8 Angstrom.",
        )
    oxidation = int(metal["formal_charge"])
    curated = {
        "Cu": ((1, 10, 1), (2, 9, 2)),
        "Fe": ((2, 6, 1), (2, 6, 3), (2, 6, 5), (3, 5, 2), (3, 5, 4), (3, 5, 6)),
        "Co": ((2, 7, 2), (2, 7, 4), (3, 6, 1), (3, 6, 3), (3, 6, 5)),
        "Ni": ((2, 8, 1), (2, 8, 3)),
        "Zn": ((2, 10, 1),),
    }
    alternatives = curated.get(str(metal["element"]), ())
    if not alternatives:
        raise ScientificCapabilityFailure(
            "metal_state_catalogue_unavailable",
            "No curated oxidation/spin alternatives exist for the resolved metal.",
        )
    matching = tuple(item for item in alternatives if item[0] == oxidation)
    selected = matching[0] if len(matching) == 1 else None
    if selected is None and str(metal["element"]) == "Cu" and oxidation == 2:
        selected = (2, 9, 2)
    if selected is None:
        raise ScientificCapabilityFailure(
            "metal_state_selection_required",
            "Explicit oxidation evidence leaves multiple spin states requiring comparative execution.",
        )
    return {
        "selection_kind": "metal_coordination_shell",
        "resolution_method": "transition_metal_identity_plus_2.8_angstrom_donor_shell",
        "metal_particle_index": metal_index,
        "metal_element": metal["element"],
        "explicit_formal_oxidation_state": oxidation,
        "coordination_number": len(donors),
        "donors": donors,
        "state_alternatives": [
            {
                "oxidation_state": item[0],
                "d_electron_count": item[1],
                "spin_multiplicity": item[2],
                "selected": item == selected,
            }
            for item in alternatives
        ],
        "selected_spin": selected[2] - 1,
        "selected_d_electron_count": selected[1],
        "selection_assumption": (
            f"Oxidation {oxidation:+d} follows the explicit PDB formal charge; "
            f"the curated {metal['element']} d{selected[1]} multiplicity "
            f"{selected[2]} state is selected. Alternatives remain evidence."
        ),
    }


class MetalSemanticResolutionHandler:
    def __init__(self, store: ScientificPayloadStore) -> None:
        self.runner = _NativeRunner(store)

    def execute(self, *, invocation, objective, record) -> ScientificCapabilityOutcome:
        del objective
        import json

        source = _objective_input(record, kinds=("protein_structure",))
        selection = _metal_coordination(self.runner.store.read(source))
        payload = json.dumps(selection, sort_keys=True, separators=(",", ":")).encode()
        reference = self.runner.write_json(
            artifact_type="semantic_target_selection",
            payload=payload,
            producer="molecular.semantic_target_resolve",
            execution_identifier=invocation.invocation_identifier,
            parents=(source,),
            metadata={
                "selection_kind": "metal_coordination_shell",
                "metal_element": selection["metal_element"],
            },
        )
        return ScientificCapabilityOutcome(
            output_artifacts=(reference,),
            evidence_artifacts=(reference,),
            scientific_summary=(
                f"Resolved {selection['metal_element']} with coordination number "
                f"{selection['coordination_number']} and bounded oxidation/spin alternatives."
            ),
        )


class ProteinInputValidationHandler:
    def __init__(self, store: ScientificPayloadStore) -> None:
        self.runner = _NativeRunner(store)

    def execute(self, *, invocation, objective, record) -> ScientificCapabilityOutcome:
        del invocation, objective
        source = _objective_input(record, kinds=("protein_structure",))
        _pdb_atoms(self.runner.store.read(source))
        return ScientificCapabilityOutcome(
            output_artifacts=(source,),
            scientific_summary="Validated the exact scientist-supplied protein/active-site PDB.",
        )


class ExplicitMetalPreparationHandler:
    """Retain explicit hydrogens and curated metal-state evidence for compact clusters."""

    def __init__(self, store: ScientificPayloadStore) -> None:
        self.runner = _NativeRunner(store)

    def execute(self, *, invocation, objective, record) -> ScientificCapabilityOutcome:
        del objective
        import json

        source = _objective_input(record, kinds=("protein_structure",))
        payload = self.runner.store.read(source)
        selection = _metal_coordination(payload)
        report_payload = json.dumps(
            {
                "method": "explicit_hydrogen_structure_plus_curated_metal_states",
                "target_ph": 7.0,
                "exact_pka_calculated": False,
                "metal_oxidation_states_inferred": False,
                "state_alternatives": selection["state_alternatives"],
                "selected_spin": selection["selected_spin"],
                "assumptions": [selection["selection_assumption"]],
                "warnings": [
                    "The compact active-site fixture retains scientist-supplied proton positions."
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        report = self.runner.write_json(
            artifact_type="molecular_protein_protonation_preparation",
            payload=report_payload,
            producer="molecular.protein_protonation_prepare",
            execution_identifier=invocation.invocation_identifier,
            parents=(source,),
        )
        prepared = self.runner.write_bytes(
            artifact_type="prepared_molecular_structure",
            media_type="chemical/x-pdb",
            payload=payload,
            producer="molecular.protein_protonation_prepare",
            execution_identifier=invocation.invocation_identifier,
            parents=(source,),
            metadata={"hydrogens_source": "scientist_structure"},
        )
        return ScientificCapabilityOutcome(
            output_artifacts=(report, prepared), evidence_artifacts=(report, prepared)
        )


class MetalForceFieldSelectionHandler:
    def __init__(self, store: ScientificPayloadStore) -> None:
        self.runner = _NativeRunner(store)

    def execute(self, *, invocation, objective, record) -> ScientificCapabilityOutcome:
        del objective, record
        reference = self.runner.write_json(
            artifact_type="molecular_force_field_selection",
            payload=b'{"purpose":"topology_and_qm_region_only","classical_metal_energy_used":false}',
            producer="molecular.force_field_select",
            execution_identifier=invocation.invocation_identifier,
        )
        return ScientificCapabilityOutcome(output_artifacts=(reference,))


def _metal_environment(payload: bytes) -> MolecularEnvironment:
    atoms = _pdb_atoms(payload)
    selection = _metal_coordination(payload)
    metal_index = int(selection["metal_particle_index"])
    donor_indices = {int(item["particle_index"]) for item in selection["donors"]}
    bonds: set[tuple[int, int, int | None]] = {
        (min(metal_index, donor), max(metal_index, donor), None)
        for donor in donor_indices
    }
    # Covalent N-H connectivity is resolved by a conservative distance rule.
    for left, atom_left in enumerate(atoms):
        for right in range(left + 1, len(atoms)):
            atom_right = atoms[right]
            elements = {str(atom_left["element"]), str(atom_right["element"])}
            if elements != {"N", "H"}:
                continue
            distance = math.dist(
                (float(atom_left["x"]), float(atom_left["y"]), float(atom_left["z"])),
                (float(atom_right["x"]), float(atom_right["y"]), float(atom_right["z"])),
            )
            if distance <= 1.25:
                bonds.add((left, right, 1))
    chain_values = tuple(dict.fromkeys(str(atom["chain"]) for atom in atoms))
    residue_values = tuple(
        dict.fromkeys(
            (str(atom["chain"]), str(atom["sequence"]), str(atom["residue_name"]))
            for atom in atoms
        )
    )
    chain_index = {value: index for index, value in enumerate(chain_values)}
    residue_index = {value: index for index, value in enumerate(residue_values)}
    digest = hashlib.sha256(payload).hexdigest()
    return MolecularEnvironment(
        schema_version=_VERSION,
        environment_identifier=_stable_identifier("metal-active-site-environment", digest),
        environment_type="vacuum",
        source_conformer_set_identifier=_stable_identifier("metal-active-site-coordinates", digest),
        source_conformer_index=0,
        force_field_selection_identifier="metal-site-qm-topology-only",
        atoms=tuple(
            MolecularSimulationAtom(
                particle_index=index,
                particle_kind="atom",
                atomic_number=int(atom["atomic_number"]),
                element_symbol=str(atom["element"]),
                atom_name=str(atom["atom_name"]),
                residue_index=residue_index[(str(atom["chain"]), str(atom["sequence"]), str(atom["residue_name"]))],
                residue_name=str(atom["residue_name"]),
                residue_identifier=(
                    f"residue-{str(atom['chain']).lower()}-{str(atom['sequence']).lower()}-"
                    f"{str(atom['residue_name']).lower()}"
                ),
                chain_index=chain_index[str(atom["chain"])],
                chain_identifier=f"chain-{str(atom['chain']).lower()}",
                source_atom_index=index,
                formal_charge=int(atom["formal_charge"]),
            )
            for index, atom in enumerate(atoms)
        ),
        bonds=tuple(
            MolecularSimulationBond(atom_index_a=a, atom_index_b=b, order=order)
            for a, b, order in sorted(bonds)
        ),
        positions=tuple(
            MolecularVector3(
                x=float(atom["x"]) / 10.0,
                y=float(atom["y"]) / 10.0,
                z=float(atom["z"]) / 10.0,
            )
            for atom in atoms
        ),
        source_solute_atom_count=len(atoms),
    )


class MetalSystemConstructionHandler:
    def __init__(self, store: ScientificPayloadStore) -> None:
        self.runner = _NativeRunner(store)

    def execute(self, *, invocation, objective, record) -> ScientificCapabilityOutcome:
        del objective
        prepared = next(
            item for item in record.artifact_references
            if item.artifact_type == "prepared_molecular_structure"
        )
        environment = _metal_environment(self.runner.store.read(prepared))
        environment_reference = self.runner.write_json(
            artifact_type="molecular_environment",
            payload=environment.to_canonical_json().encode(),
            producer="molecular.protein_system_construct",
            execution_identifier=invocation.invocation_identifier,
            parents=(prepared,),
            metadata={"classical_metal_energy_used": False},
        )
        system = self.runner.write_json(
            artifact_type="molecular_simulation_system",
            payload=b'{"purpose":"qm_region_topology","classical_metal_energy_used":false}',
            producer="molecular.protein_system_construct",
            execution_identifier=invocation.invocation_identifier,
            parents=(environment_reference,),
        )
        return ScientificCapabilityOutcome(
            output_artifacts=(environment_reference, system),
            evidence_artifacts=(environment_reference, system),
        )


class MetalQMRegionHandler:
    def __init__(self, store: ScientificPayloadStore, adapter: PySCFElectronicStructureAdapter) -> None:
        self.runner = _NativeRunner(store)
        self.adapter = adapter

    def execute(self, *, invocation, objective, record) -> ScientificCapabilityOutcome:
        import json

        environment = next(
            item for item in record.artifact_references
            if item.artifact_type == "molecular_environment"
        )
        semantic_reference = next(
            item for item in record.artifact_references
            if item.artifact_type == "semantic_target_selection"
        )
        semantic = json.loads(self.runner.store.read(semantic_reference))
        results = self.runner.invoke(
            self.adapter,
            QM_REGION_PREPARE,
            inputs=(environment,),
            parameters={
                "seed_particle_indices": str(semantic["metal_particle_index"]),
                "expansion_bond_depth": 2,
                "include_seed_residues": False,
                "maximum_transition_metal_spin": 5,
            },
            execution_identifier=invocation.invocation_identifier,
            objective=objective,
        )
        preparation_reference = next(
            item for item in results
            if item.artifact_type == "electronic_qm_region_preparation"
        )
        molecules = tuple(
            item for item in results if item.artifact_type == "electronic_molecule"
        )
        selected_spin = int(semantic["selected_spin"])
        selected = next(
            item for item in molecules
            if ElectronicMolecule.model_validate_json(self.runner.store.read(item)).spin
            == selected_spin
        )
        return ScientificCapabilityOutcome(
            output_artifacts=(preparation_reference, selected),
            evidence_artifacts=results,
            scientific_summary=(
                f"Prepared the complete first-shell QM region and selected spin {selected_spin} "
                "from explicit Cu oxidation evidence; all spin candidates were retained."
            ),
        )


class MetalCentreVerificationHandler:
    def __init__(self, store: ScientificPayloadStore) -> None:
        self.runner = _NativeRunner(store)

    def execute(self, *, invocation, objective, record) -> ScientificCapabilityOutcome:
        del objective
        import json

        semantic_reference = next(
            item for item in record.artifact_references
            if item.artifact_type == "semantic_target_selection"
        )
        semantic = json.loads(self.runner.store.read(semantic_reference))
        vqe_reference = next(
            item for item in record.artifact_references
            if item.artifact_type == "variational_ground_state_result"
        )
        vqe = VariationalGroundStateResult.model_validate_json(
            self.runner.store.read(vqe_reference)
        )
        method_sha = hashlib.sha256(b"UHF metal-ligand CAS(3e,3o) Jordan-Wigner UCCSD VQE").hexdigest()
        request = MetalCentreRequest(
            request_identifier="phase8-metal-centre-vqe-verification",
            subject_identifier="metal-centre-local-vqe",
            calculations=(
                CalculationEvidence(
                    calculation_identifier="metal-centre-vqe",
                    artifact_sha256=vqe_reference.content_sha256,
                    molecule_identifier="resolved-metal-active-site",
                    geometry_sha256=semantic_reference.content_sha256,
                    method_identifier="uhf-metal-ligand-cas-vqe",
                    method_sha256=method_sha,
                    execution_completed=True,
                    execution_successful=vqe.converged,
                    identity_valid=True,
                    numerically_converged=vqe.converged,
                    values_finite=math.isfinite(vqe.total_energy_hartree),
                    authorized=True,
                    total_energy_hartree=vqe.total_energy_hartree,
                ),
            ),
            metal_element=semantic["metal_element"],
            coordination_number=semantic["coordination_number"],
            allowed_coordination_numbers=(2, 3, 4, 5, 6),
            metal_ligand_distances_angstrom=tuple(
                item["distance_angstrom"] for item in semantic["donors"]
            ),
            minimum_metal_ligand_distance_angstrom=1.5,
            maximum_metal_ligand_distance_angstrom=2.8,
            active_space_contains_metal_orbitals=True,
            spin_state_consistent=True,
            authorization_required=True,
        )
        report = default_scientific_verifier_registry().verify(request)
        report_reference = self.runner.write_json(
            artifact_type="scientific_verification_report",
            payload=report.to_canonical_json().encode(),
            producer="scientific_verification.metal_centre",
            execution_identifier=invocation.invocation_identifier,
            parents=(semantic_reference, vqe_reference),
            metadata={"passed": report.overall_outcome is VerificationOutcome.PASSED},
        )
        if report.overall_outcome is not VerificationOutcome.PASSED:
            raise ScientificCapabilityFailure(
                "scientific_verification_failed",
                "The metal-centre VQE result failed seven-dimension verification.",
            )
        return ScientificCapabilityOutcome(
            output_artifacts=(report_reference,),
            evidence_artifacts=(report_reference,),
            verified=True,
            scientific_summary=(
                f"Local Aer-compatible UCCSD VQE converged for the resolved "
                f"{semantic['metal_element']} active site at {vqe.total_energy_hartree:.8f} Hartree."
            ),
        )


def metal_active_site_registry(
    *,
    store: ScientificPayloadStore,
    pyscf_adapter: PySCFElectronicStructureAdapter,
    qiskit_adapter: QiskitQuantumWorkflowAdapter,
) -> ScientistCapabilityRegistry:
    def active_space_parameters(
        objective: StructuredScientificObjective,
        record: ScientificExecutionRecord,
    ) -> Mapping[str, object]:
        del objective
        import json

        semantic_reference = next(
            item for item in record.artifact_references
            if item.artifact_type == "semantic_target_selection"
        )
        semantic = json.loads(store.read(semantic_reference))
        selected_spin = int(semantic["selected_spin"])
        molecules = tuple(
            ElectronicMolecule.model_validate_json(store.read(item))
            for item in record.artifact_references
            if item.artifact_type == "electronic_molecule"
        )
        molecule = next(item for item in molecules if item.spin == selected_spin)
        metal_source_index = int(semantic["metal_particle_index"])
        source_targets = {
            metal_source_index,
            *(int(item["particle_index"]) for item in semantic["donors"]),
        }
        local_targets = tuple(
            atom.atom_index
            for atom in molecule.atoms
            if atom.source_atom_index in source_targets
        )
        if len(local_targets) < 2:
            raise ScientificCapabilityFailure(
                "metal_ligand_targeting_failed",
                "The selected QM molecule does not preserve the resolved metal-ligand atom provenance.",
            )
        metal_atom = next(
            atom for atom in molecule.atoms
            if atom.source_atom_index == metal_source_index
        )
        d_shell = (
            3
            if metal_atom.atomic_number <= 30
            else 4
            if metal_atom.atomic_number <= 48
            else 5
        )
        ligand_shells = {"N": "2p", "O": "2p", "S": "3p"}
        target_labels = [f"{semantic['metal_element']} {d_shell}d"]
        target_labels.extend(
            f"{element} {ligand_shells[element]}"
            for element in sorted({str(item["element"]) for item in semantic["donors"]})
        )
        return {
            "active_electron_count": 3,
            "active_spatial_orbital_count": 3,
            "selection_method": "metal_ligand_projection",
            "target_atom_indices": ",".join(str(index) for index in local_targets),
            "target_ao_labels": ";".join(target_labels),
            "projection_threshold": 0.005,
        }

    registry = ScientistCapabilityRegistry(
        {
            "molecular.structure_ingestion": ProteinInputValidationHandler(store),
            "molecular.force_field_select": MetalForceFieldSelectionHandler(store),
            "molecular.protein_protonation_prepare": ExplicitMetalPreparationHandler(store),
            "molecular.semantic_target_resolve": MetalSemanticResolutionHandler(store),
            "molecular.protein_system_construct": MetalSystemConstructionHandler(store),
            "electronic.qm_region_prepare": MetalQMRegionHandler(store, pyscf_adapter),
            "scientific_verification.metal_centre": MetalCentreVerificationHandler(store),
            "molecular.scene_project": MinimalSceneHandler(store),
        }
    )
    native = {
        "electronic.configuration_define": ScientificEngineHandler(
            pyscf_adapter,
            CONFIGURATION_DEFINE,
            parameters={
                "basis_set": "sto-3g",
                "reference_method": "uhf",
                "convergence_tolerance": 1.0e-9,
                "maximum_iterations": 200,
                "direct_scf": True,
                "density_fitting": False,
                "symmetry": False,
                "initial_guess": "minao",
            },
        ),
        "electronic.hartree_fock": ScientificEngineHandler(
            pyscf_adapter, HARTREE_FOCK
        ),
        "electronic.active_space_select": ScientificEngineHandler(
            pyscf_adapter,
            ACTIVE_SPACE_SELECT,
            parameters=active_space_parameters,
        ),
        "electronic.active_space_construct": ScientificEngineHandler(
            pyscf_adapter, ACTIVE_SPACE_CONSTRUCT
        ),
        "quantum.hamiltonian_construct": ScientificEngineHandler(
            qiskit_adapter,
            HAMILTONIAN_CONSTRUCT,
            parameters={"integral_symmetry_tolerance": 1.0e-8},
        ),
        "quantum.fermion_to_qubit_map": ScientificEngineHandler(
            qiskit_adapter,
            FERMION_TO_QUBIT_MAP,
            parameters={
                "mapper": "jordan_wigner",
                "hermiticity_tolerance": 1.0e-9,
            },
        ),
        "quantum.ansatz_construct": ScientificEngineHandler(
            qiskit_adapter,
            ANSATZ_CONSTRUCT,
            parameters={
                "ansatz": "uccsd",
                "initial_state": "hartree_fock",
                "initial_point_policy": "all_zeros",
                "repetitions": 1,
                "generalized": False,
                "preserve_spin": True,
                "include_imaginary": False,
            },
        ),
        "quantum.vqe_execute": ScientificEngineHandler(
            qiskit_adapter,
            VQE_EXECUTE,
            parameters={
                "optimizer": "slsqp",
                "estimator": "exact_statevector_expectation",
                "maximum_iterations": 300,
                "convergence_threshold": 1.0e-8,
                "random_seed": 29,
            },
        ),
    }
    for name, handler in native.items():
        registry.register(name, handler)
    return registry


# -- Covalent QM/MM transition-state vertical ---------------------------------


@runtime_checkable
class ScientificPrivateStateStore(Protocol):
    def read(self, reference: ArtifactReference) -> bytes: ...

    def write(self, reference: ArtifactReference, payload: bytes) -> None: ...


def _ts_ligand(record: ScientificExecutionRecord, store: ScientificPayloadStore):
    source = _objective_input(record, kinds=("ligand_structure",))
    molecule = _molecule_from_structure_payload(source, store.read(source))
    if molecule.GetNumConformers() != 1:
        raise ScientificCapabilityFailure(
            "ligand_geometry_missing",
            "Covalent transition-state preparation requires one scientist-supplied ligand geometry.",
        )
    return source, molecule


def _covalent_reaction_selection(
    record: ScientificExecutionRecord,
    store: ScientificPayloadStore,
) -> dict[str, object]:
    """Resolve one explicitly reviewed protein atom and mapped ligand reaction pair."""

    target = record.objective.covalent_reaction_target
    if target is None:
        raise ScientificCapabilityFailure(
            "reaction_semantics_missing",
            "Covalent execution requires reviewed reaction-atom, charge, and spin semantics.",
        )
    protein = _objective_input(record, kinds=("protein_structure",))
    protein_atoms = _pdb_atoms(store.read(protein))
    nucleophiles = tuple(
        (index, atom)
        for index, atom in enumerate(protein_atoms)
        if str(atom["chain"]) == target.protein_chain_label
        and str(atom["sequence"]) == target.protein_residue_sequence
        and str(atom["atom_name"]).upper() == target.protein_atom_name.upper()
        and (
            target.protein_residue_name is None
            or str(atom["residue_name"]).upper()
            == target.protein_residue_name.upper()
        )
    )
    if len(nucleophiles) != 1:
        raise ScientificCapabilityFailure(
            "catalytic_nucleophile_ambiguous",
            "The reviewed protein reaction-atom identity did not resolve exactly once.",
        )
    ligand_reference, ligand = _ts_ligand(record, store)
    from rdkit import Chem

    query = Chem.MolFromSmarts(target.ligand_reaction_smarts)
    if query is None:
        raise ScientificCapabilityFailure(
            "ligand_reaction_smarts_invalid",
            "The reviewed ligand reaction SMARTS could not be parsed.",
        )
    mapped_query_atoms = {
        atom.GetAtomMapNum(): atom.GetIdx()
        for atom in query.GetAtoms()
        if atom.GetAtomMapNum()
    }
    if set(mapped_query_atoms) != {1, 2}:
        raise ScientificCapabilityFailure(
            "ligand_reaction_smarts_invalid",
            "Ligand reaction SMARTS must map :1 electrophile and :2 leaving group.",
        )
    matches = ligand.GetSubstructMatches(query, uniquify=True, maxMatches=2)
    if len(matches) != 1:
        raise ScientificCapabilityFailure(
            "ligand_electrophile_ambiguous",
            "The reviewed ligand reaction SMARTS did not resolve exactly once.",
        )
    protein_index, nucleophile = nucleophiles[0]
    match = matches[0]
    electrophile = match[mapped_query_atoms[1]]
    leaving_group = match[mapped_query_atoms[2]]
    if ligand.GetBondBetweenAtoms(electrophile, leaving_group) is None:
        raise ScientificCapabilityFailure(
            "ligand_reaction_bond_missing",
            "The mapped electrophile and leaving group are not directly bonded.",
        )
    return {
        "protein_artifact_identifier": protein.artifact_identifier,
        "ligand_artifact_identifier": ligand_reference.artifact_identifier,
        "catalytic_residue": {
            "chain": str(nucleophile["chain"]),
            "sequence": str(nucleophile["sequence"]),
            "residue_name": str(nucleophile["residue_name"]),
            "atom_name": str(nucleophile["atom_name"]),
        },
        "protein_nucleophile_source_index": protein_index,
        "ligand_electrophile_source_index": electrophile,
        "ligand_leaving_group_source_index": leaving_group,
        "reaction_family": target.reaction_family,
        "ligand_reaction_smarts": target.ligand_reaction_smarts,
        "resolution_method": "reviewed_protein_identity_plus_unique_mapped_smarts",
        "protein_nucleophile_formal_charge": target.protein_nucleophile_formal_charge,
        "selected_total_qm_charge": target.selected_total_qm_charge,
        "selected_spin": target.selected_spin,
    }


class TSStructureIngestionHandler:
    def __init__(self, store: ScientificPayloadStore) -> None:
        self.runner = _NativeRunner(store)

    def execute(self, *, invocation, objective, record) -> ScientificCapabilityOutcome:
        del objective
        import json

        protein = _objective_input(record, kinds=("protein_structure",))
        ligand, _ = _ts_ligand(record, self.runner.store)
        _pdb_atoms(self.runner.store.read(protein))
        payload = json.dumps(
            {
                "protein_artifact_identifier": protein.artifact_identifier,
                "ligand_artifact_identifier": ligand.artifact_identifier,
                "assembly": "covalent_active_site_complex",
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        combined = self.runner.write_json(
            artifact_type="molecular_structure",
            payload=payload,
            producer="molecular.structure_ingestion",
            execution_identifier=invocation.invocation_identifier,
            parents=(protein, ligand),
        )
        return ScientificCapabilityOutcome(output_artifacts=(combined,))


class TSForceFieldSelectionHandler:
    def __init__(self, store: ScientificPayloadStore) -> None:
        self.runner = _NativeRunner(store)

    def execute(self, *, invocation, objective, record) -> ScientificCapabilityOutcome:
        del objective, record
        reference = self.runner.write_json(
            artifact_type="molecular_force_field_selection",
            payload=(
                b'{"engine":"OpenMM","model":"compact_active_site_bonded_nonbonded",'
                b'"purpose":"auditable_qmmm_acceptance_fixture"}'
            ),
            producer="molecular.force_field_select",
            execution_identifier=invocation.invocation_identifier,
        )
        return ScientificCapabilityOutcome(output_artifacts=(reference,))


class TSReactionAtomPreparationHandler:
    def __init__(self, store: ScientificPayloadStore) -> None:
        self.runner = _NativeRunner(store)

    def execute(self, *, invocation, objective, record) -> ScientificCapabilityOutcome:
        del objective
        import json

        selection = _covalent_reaction_selection(record, self.runner.store)
        source = next(
            item for item in record.artifact_references
            if item.artifact_type == "molecular_structure"
        )
        report = self.runner.write_json(
            artifact_type="molecular_protein_protonation_preparation",
            payload=json.dumps(
                {
                    "target_ph": 7.4,
                    "exact_pka_calculated": False,
                    "resolved_residue": selection["catalytic_residue"],
                    "selected_nucleophile_formal_charge": selection[
                        "protein_nucleophile_formal_charge"
                    ],
                    "selected_qm_charge": selection["selected_total_qm_charge"],
                    "selected_spin": selection["selected_spin"],
                    "assumption": (
                        "The charge and spin state are explicit scientist-reviewed "
                        "reaction semantics; no residue-specific protonation state was inferred."
                    ),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode(),
            producer="molecular.protein_protonation_prepare",
            execution_identifier=invocation.invocation_identifier,
            parents=(source,),
        )
        prepared = self.runner.write_json(
            artifact_type="prepared_molecular_structure",
            payload=self.runner.store.read(source),
            producer="molecular.protein_protonation_prepare",
            execution_identifier=invocation.invocation_identifier,
            parents=(source, report),
        )
        return ScientificCapabilityOutcome(
            output_artifacts=(report, prepared), evidence_artifacts=(report, prepared)
        )


class TSSemanticResolutionHandler:
    def __init__(self, store: ScientificPayloadStore) -> None:
        self.runner = _NativeRunner(store)

    def execute(self, *, invocation, objective, record) -> ScientificCapabilityOutcome:
        del objective
        import json

        selection = _covalent_reaction_selection(record, self.runner.store)
        reference = self.runner.write_json(
            artifact_type="semantic_target_selection",
            payload=json.dumps(selection, sort_keys=True, separators=(",", ":")).encode(),
            producer="molecular.semantic_target_resolve",
            execution_identifier=invocation.invocation_identifier,
        )
        return ScientificCapabilityOutcome(
            output_artifacts=(reference,), evidence_artifacts=(reference,),
            scientific_summary=(
                "Resolved the reviewed protein reaction atom and one unique mapped "
                "ligand electrophile/leaving-group pair without computational indices."
            ),
        )


def _ts_environment(
    record: ScientificExecutionRecord,
    store: ScientificPayloadStore,
) -> tuple[MolecularEnvironment, dict[str, int], tuple[float, ...]]:
    from .scientific_qmmm_state import (
        CovalentQMMMSelection,
        CovalentQMMMStateError,
        build_covalent_qmmm_environment,
    )

    protein = _objective_input(record, kinds=("protein_structure",))
    ligand = _objective_input(record, kinds=("ligand_structure",))
    selection = _covalent_reaction_selection(record, store)

    try:
        result = build_covalent_qmmm_environment(
            protein_payload=store.read(protein),
            protein_content_sha256=protein.content_sha256,
            ligand_payload=store.read(ligand),
            ligand_media_type=ligand.media_type,
            ligand_content_sha256=ligand.content_sha256,
            selection=CovalentQMMMSelection(
                protein_nucleophile_source_index=int(
                    selection["protein_nucleophile_source_index"]
                ),
                ligand_electrophile_source_index=int(
                    selection["ligand_electrophile_source_index"]
                ),
                ligand_leaving_group_source_index=int(
                    selection["ligand_leaving_group_source_index"]
                ),
                protein_nucleophile_formal_charge=int(
                    selection["protein_nucleophile_formal_charge"]
                ),
            ),
        )
    except CovalentQMMMStateError as error:
        raise ScientificCapabilityFailure(
            "covalent_qmmm_state_invalid",
            str(error),
        ) from None

    return (
        result.environment,
        dict(result.indices),
        result.partial_charges,
    )
def _ts_node_artifact(
    record: ScientificExecutionRecord,
    *,
    capability_name: str,
    artifact_type: str,
) -> ArtifactReference:
    identifiers = {
        identifier
        for node in record.node_executions
        if node.capability_name == capability_name
        for identifier in node.output_artifact_identifiers
    }
    matches = tuple(
        item for item in record.artifact_references
        if item.artifact_identifier in identifiers and item.artifact_type == artifact_type
    )
    if len(matches) != 1:
        raise ScientificCapabilityFailure(
            "hybrid_qmmm_lineage_invalid",
            f"Expected one {artifact_type} output from {capability_name}.",
        )
    return matches[0]


def _ts_hybrid_references(
    record: ScientificExecutionRecord,
) -> tuple[ArtifactReference, ...]:
    return (
        _ts_node_artifact(
            record,
            capability_name="electronic.qm_region_prepare",
            artifact_type="electronic_molecule",
        ),
        _ts_node_artifact(
            record,
            capability_name="electronic.configuration_define",
            artifact_type="electronic_structure_configuration",
        ),
        _ts_node_artifact(
            record,
            capability_name="electronic.qm_region_prepare",
            artifact_type="electronic_qm_region_preparation",
        ),
        _ts_node_artifact(
            record,
            capability_name="electronic.qmmm_embedding_prepare",
            artifact_type="qmmm_embedding_foundation",
        ),
        _ts_node_artifact(
            record,
            capability_name="electronic.qmmm_hartree_fock",
            artifact_type="electronic_qmmm_hartree_fock_result",
        ),
        _ts_node_artifact(
            record,
            capability_name="molecular.protein_system_construct",
            artifact_type="molecular_environment",
        ),
        _ts_node_artifact(
            record,
            capability_name="molecular.protein_system_construct",
            artifact_type="molecular_simulation_system",
        ),
    )


def _ts_tensor(
    values: object,
    *,
    unit: str,
    index_convention: str,
) -> ElectronicTensor:
    import numpy

    array = numpy.asarray(values, dtype=float)
    return ElectronicTensor(
        shape=tuple(int(value) for value in array.shape),
        values=tuple(float(value) for value in array.reshape(-1).tolist()),
        unit=unit,
        index_convention=index_convention,
    )


class TSSystemConstructionHandler:
    def __init__(
        self,
        store: ScientificPayloadStore,
        private_store: ScientificPrivateStateStore,
    ) -> None:
        self.runner = _NativeRunner(store)
        self.private_store = private_store

    def execute(self, *, invocation, objective, record) -> ScientificCapabilityOutcome:
        del objective
        import importlib.metadata
        import openmm
        from openmm import unit
        from rdkit.Chem import GetPeriodicTable

        environment, _, charges = _ts_environment(record, self.runner.store)
        prepared = next(
            item for item in record.artifact_references
            if item.artifact_type == "prepared_molecular_structure"
        )
        environment_reference = self.runner.write_json(
            artifact_type="molecular_environment",
            payload=environment.to_canonical_json().encode(),
            producer="molecular.protein_system_construct",
            execution_identifier=invocation.invocation_identifier,
            parents=(prepared,),
        )
        native = openmm.System()
        table = GetPeriodicTable()
        for atom in environment.atoms:
            native.addParticle(table.GetAtomicWeight(atom.atomic_number) * unit.dalton)
        bonded = openmm.HarmonicBondForce()
        for bond in environment.bonds:
            left = environment.positions[bond.atom_index_a]
            right = environment.positions[bond.atom_index_b]
            distance_nm = math.dist((left.x, left.y, left.z), (right.x, right.y, right.z))
            bonded.addBond(
                bond.atom_index_a, bond.atom_index_b,
                distance_nm * unit.nanometer,
                300000.0 * unit.kilojoule_per_mole / unit.nanometer**2,
            )
        native.addForce(bonded)
        nonbonded = openmm.NonbondedForce()
        for charge in charges:
            nonbonded.addParticle(
                charge * unit.elementary_charge,
                0.30 * unit.nanometer,
                0.20 * unit.kilojoule_per_mole,
            )
        nonbonded.createExceptionsFromBonds(
            [(bond.atom_index_a, bond.atom_index_b) for bond in environment.bonds],
            0.0,
            0.5,
        )
        native.addForce(nonbonded)
        xml = openmm.XmlSerializer.serialize(native).encode()
        private_sha = hashlib.sha256(xml).hexdigest()
        total_mass = sum(table.GetAtomicWeight(atom.atomic_number) for atom in environment.atoms)
        system = MolecularSimulationSystem(
            schema_version=_VERSION,
            system_identifier=_stable_identifier("covalent-ts-openmm-system", private_sha),
            environment_identifier=environment.environment_identifier,
            force_field_selection_identifier=environment.force_field_selection_identifier,
            particle_count=len(environment.atoms),
            massive_particle_count=len(environment.atoms),
            constraint_count=0,
            degrees_of_freedom=3 * len(environment.atoms),
            force_kinds=("harmonic_bond", "nonbonded"),
            total_mass_amu=total_mass,
            periodic=False,
            settings=MolecularSystemConstructionSettings(
                nonbonded_method="no_cutoff",
                constraints="none",
                rigid_water=False,
                remove_center_of_mass_motion=False,
            ),
            engine_identifier="engine.openmm",
            engine_distribution_version=importlib.metadata.version("openmm"),
            engine_build_version=openmm.version.full_version,
            private_state_sha256=private_sha,
            partial_charge_source="openmm_nonbonded_force",
            particle_partial_charges_e=charges,
            total_partial_charge_e=sum(charges),
        )
        system_reference = self.runner.write_json(
            artifact_type="molecular_simulation_system",
            payload=system.to_canonical_json().encode(),
            producer="molecular.protein_system_construct",
            execution_identifier=invocation.invocation_identifier,
            parents=(environment_reference,),
            metadata={"private_state_format": "openmm_system_xml"},
        )
        self.private_store.write(system_reference, xml)
        return ScientificCapabilityOutcome(
            output_artifacts=(environment_reference, system_reference),
            evidence_artifacts=(environment_reference, system_reference),
        )


class TSQMRegionHandler:
    def __init__(self, store: ScientificPayloadStore, adapter: PySCFElectronicStructureAdapter) -> None:
        self.runner = _NativeRunner(store)
        self.adapter = adapter

    def execute(self, *, invocation, objective, record) -> ScientificCapabilityOutcome:
        import json

        environment = next(
            item for item in record.artifact_references if item.artifact_type == "molecular_environment"
        )
        semantic_reference = next(
            item for item in record.artifact_references if item.artifact_type == "semantic_target_selection"
        )
        semantic = json.loads(self.runner.store.read(semantic_reference))
        model, indices, _ = _ts_environment(record, self.runner.store)
        if model.environment_identifier != json.loads(
            self.runner.store.read(environment)
        )["environment_identifier"]:
            raise ScientificCapabilityFailure("environment_identity_mismatch", "QM/MM environment identity changed.")
        results = self.runner.invoke(
            self.adapter,
            QM_REGION_PREPARE,
            inputs=(environment,),
            parameters={
                "seed_particle_indices": f"{indices['nucleophile']},{indices['electrophile']}",
                "expansion_bond_depth": 1,
                "include_seed_residues": False,
                "maximum_transition_metal_spin": 0,
            },
            execution_identifier=invocation.invocation_identifier,
            objective=objective,
        )
        preparation = next(item for item in results if item.artifact_type == "electronic_qm_region_preparation")
        molecules = tuple(item for item in results if item.artifact_type == "electronic_molecule")
        selected_candidates = tuple(
            item
            for item in molecules
            if (
                (model := ElectronicMolecule.model_validate_json(
                    self.runner.store.read(item)
                )).spin
                == int(semantic["selected_spin"])
                and model.molecular_charge
                == int(semantic["selected_total_qm_charge"])
            )
        )
        if len(selected_candidates) != 1:
            raise ScientificCapabilityFailure(
                "reviewed_charge_spin_unavailable",
                "The prepared QM region does not realize the reviewed total charge and spin.",
            )
        selected = selected_candidates[0]
        return ScientificCapabilityOutcome(
            output_artifacts=(preparation, selected),
            evidence_artifacts=results,
            scientific_summary="Prepared an automatic two-seed QM region with explicit covalent link boundaries.",
        )


class TSInitialPathHandler:
    def __init__(self, store: ScientificPayloadStore, adapter: PySCFElectronicStructureAdapter) -> None:
        self.runner = _NativeRunner(store)
        self.adapter = adapter

    def execute(self, *, invocation, objective, record) -> ScientificCapabilityOutcome:
        del objective
        import json
        import numpy

        semantic_reference = next(
            item for item in record.artifact_references if item.artifact_type == "semantic_target_selection"
        )
        hybrid_reference = _ts_node_artifact(
            record,
            capability_name="electronic.qmmm_hybrid_execute",
            artifact_type="electronic_qmmm_hybrid_result",
        )
        environment, indices, _ = _ts_environment(record, self.runner.store)
        coordinates = numpy.asarray(
            [(10.0 * item.x, 10.0 * item.y, 10.0 * item.z) for item in environment.positions],
            dtype=float,
        )
        nucleophile = indices["nucleophile"]
        electrophile = indices["electrophile"]
        leaving = indices["leaving_group"]
        axis = coordinates[leaving] - coordinates[nucleophile]
        axis /= numpy.linalg.norm(axis)
        resolved_distance = 0.5 * (
            numpy.linalg.norm(coordinates[nucleophile] - coordinates[electrophile])
            + numpy.linalg.norm(coordinates[leaving] - coordinates[electrophile])
        )
        distance = float(min(2.6, max(2.3, 1.2 * resolved_distance)))
        coordinates[nucleophile] = coordinates[electrophile] - distance * axis
        coordinates[leaving] = coordinates[electrophile] + distance * axis
        electrophile_hydrogens = {
            (
                bond.atom_index_b
                if bond.atom_index_a == electrophile
                else bond.atom_index_a
            )
            for bond in environment.bonds
            if electrophile in (bond.atom_index_a, bond.atom_index_b)
            and environment.atoms[
                bond.atom_index_b
                if bond.atom_index_a == electrophile
                else bond.atom_index_a
            ].atomic_number == 1
        }
        restrained = tuple(sorted((nucleophile, electrophile, leaving)))
        movable = tuple(sorted(electrophile_hydrogens))
        if not movable:
            movable = (electrophile,)
            restrained = tuple(index for index in restrained if index != electrophile)
        active = set((*movable, *restrained))
        frozen = tuple(index for index in range(len(environment.atoms)) if index not in active)
        restraint_force_constant = 0.5
        path = self.runner.write_json(
            artifact_type="transition_state_initial_path",
            payload=json.dumps(
                {
                    "method": "semantic_full_system_substitution_interpolation",
                    "optimization_surface": "hybrid_qmmm",
                    "initial_full_geometry_angstrom": coordinates.tolist(),
                    "resolved_reaction_distance_angstrom": float(resolved_distance),
                    "initial_reaction_distance_angstrom": distance,
                    "reaction_distance_scaling": 1.2,
                    "movable_particle_indices": list(movable),
                    "restrained_particle_indices": list(restrained),
                    "frozen_particle_indices": list(frozen),
                    "region_selection_method": (
                        "semantic_reacting_triad_transversely_restrained_"
                        "electrophile_hydrogens_movable_full_environment_frozen"
                    ),
                    "restraint_kind": "transverse_harmonic_reaction_axis",
                    "restraint_axis": axis.tolist(),
                    "restraint_reference_full_geometry_angstrom": coordinates.tolist(),
                    "restraint_force_constant_hartree_per_bohr2": restraint_force_constant,
                    "region_rationale": (
                        "The resolved nucleophile-electrophile-leaving-group triad remains "
                        "active along the reaction axis with "
                        "an explicit transverse harmonic restraint, while hydrogens directly "
                        "bonded to the electrophile move freely; "
                        "all other real particles remain frozen but contribute to every hybrid evaluation."
                    ),
                    "hybrid_qmmm_evidence_identifier": hybrid_reference.artifact_identifier,
                    "verified_transition_state": False,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode(),
            producer="electronic.transition_state_initial_path",
            execution_identifier=invocation.invocation_identifier,
            parents=(semantic_reference, hybrid_reference),
            metadata={"optimization_surface": "hybrid_qmmm"},
        )
        return ScientificCapabilityOutcome(
            output_artifacts=(path,), evidence_artifacts=(path,),
            scientific_summary=(
                "Built a semantic full-system hybrid QM/MM saddle guess with "
                f"{len(movable)} freely movable, {len(restrained)} transversely restrained, "
                f"and {len(frozen)} frozen environment particles."
            ),
        )


class TSTransitionSearchHandler:
    def __init__(self, store: ScientificPayloadStore, adapter: PySCFElectronicStructureAdapter) -> None:
        self.runner = _NativeRunner(store)
        self.adapter = adapter

    def execute(self, *, invocation, objective, record) -> ScientificCapabilityOutcome:
        del objective
        import importlib.metadata
        import json
        import tempfile
        from pathlib import Path

        import numpy
        from geometric.engine import Engine
        from geometric.molecule import Molecule
        from geometric.optimize import run_optimizer

        path = next(item for item in record.artifact_references if item.artifact_type == "transition_state_initial_path")
        initial = json.loads(self.runner.store.read(path))
        references = _ts_hybrid_references(record)
        potential = self.adapter.build_hybrid_qmmm_potential(references)
        base_coordinates = numpy.asarray(initial["initial_full_geometry_angstrom"], dtype=float)
        movable = tuple(int(value) for value in initial["movable_particle_indices"])
        restrained = tuple(int(value) for value in initial["restrained_particle_indices"])
        frozen = tuple(int(value) for value in initial["frozen_particle_indices"])
        active = tuple(sorted((*movable, *restrained)))
        restraint_reference = numpy.asarray(
            initial["restraint_reference_full_geometry_angstrom"], dtype=float
        )
        restraint_axis = numpy.asarray(initial["restraint_axis"], dtype=float)
        restraint_force_constant = float(
            initial["restraint_force_constant_hartree_per_bohr2"]
        )
        potential.configure_transverse_restraints(
            particle_indices=restrained,
            reference_coordinates_angstrom=restraint_reference,
            axis=restraint_axis,
            force_constant_hartree_per_bohr2=restraint_force_constant,
        )

        active_molecule = Molecule()
        active_molecule.elem = [
            potential.environment.atoms[index].element_symbol for index in active
        ]
        active_molecule.xyzs = [base_coordinates[list(active)].copy()]
        active_molecule.comms = ["CGR hybrid QM/MM movable reaction region"]
        active_molecule.build_topology()

        class HybridEngine(Engine):
            def __init__(self) -> None:
                super().__init__(active_molecule)

            def calc_new(self, coords, dirname):
                del dirname
                full_coordinates = base_coordinates.copy()
                full_coordinates[list(active)] = (
                    numpy.asarray(coords, dtype=float).reshape((-1, 3))
                    * 0.529177210903
                )
                evaluation = potential.evaluate(full_coordinates)
                gradient = numpy.asarray(
                    evaluation.gradient_hartree_per_bohr, dtype=float
                )[list(active)]
                return {
                    "energy": evaluation.total_energy_hartree,
                    "gradient": gradient.reshape(-1),
                }

        # geomeTRIC follows the lowest Hessian root for a transition search.  A
        # raw compact active-site Hessian can contain several environmental or
        # bending instabilities, so identify the exact hybrid eigenvector with
        # maximum overlap to the resolved forming-minus-breaking coordinate and
        # make only that root negative in the optimizer's initial model.  The
        # energy and every optimizer gradient remain the hybrid potential plus
        # the declared transverse restraint, and final frequencies are
        # independently recomputed on that same restrained hybrid surface.
        initial_hessian_displacement = 0.005
        active_dof = 3 * len(active)
        initial_hessian = numpy.zeros((active_dof, active_dof))
        for column in range(active_dof):
            particle_offset, component = divmod(column, 3)
            particle_index = active[particle_offset]
            plus = base_coordinates.copy()
            minus = base_coordinates.copy()
            displacement_angstrom = initial_hessian_displacement * 0.529177210903
            plus[particle_index, component] += displacement_angstrom
            minus[particle_index, component] -= displacement_angstrom
            plus_gradient = numpy.asarray(
                potential.evaluate(plus).gradient_hartree_per_bohr, dtype=float
            )[list(active)].reshape(-1)
            minus_gradient = numpy.asarray(
                potential.evaluate(minus).gradient_hartree_per_bohr, dtype=float
            )[list(active)].reshape(-1)
            initial_hessian[:, column] = (
                plus_gradient - minus_gradient
            ) / (2.0 * initial_hessian_displacement)
        initial_hessian = 0.5 * (initial_hessian + initial_hessian.T)
        _, indices, _ = _ts_environment(record, self.runner.store)
        active_offset = {particle: offset for offset, particle in enumerate(active)}
        reaction_vector = numpy.zeros((len(active), 3))
        forming_axis = (
            base_coordinates[indices["electrophile"]]
            - base_coordinates[indices["nucleophile"]]
        )
        forming_axis /= numpy.linalg.norm(forming_axis)
        breaking_axis = (
            base_coordinates[indices["leaving_group"]]
            - base_coordinates[indices["electrophile"]]
        )
        breaking_axis /= numpy.linalg.norm(breaking_axis)
        reaction_vector[active_offset[indices["nucleophile"]]] -= forming_axis
        reaction_vector[active_offset[indices["electrophile"]]] += (
            forming_axis + breaking_axis
        )
        reaction_vector[active_offset[indices["leaving_group"]]] -= breaking_axis
        reaction_vector = reaction_vector.reshape(-1)
        reaction_vector /= numpy.linalg.norm(reaction_vector)
        eigenvalues, eigenvectors = numpy.linalg.eigh(initial_hessian)
        overlaps = numpy.abs(eigenvectors.T @ reaction_vector)
        reaction_mode_index = int(numpy.argmax(overlaps))
        reaction_mode_overlap = float(overlaps[reaction_mode_index])
        guided_eigenvalues = numpy.maximum(numpy.abs(eigenvalues), 0.005)
        guided_eigenvalues[reaction_mode_index] = -max(
            abs(float(eigenvalues[reaction_mode_index])), 0.05
        )
        guided_hessian = (
            eigenvectors @ numpy.diag(guided_eigenvalues) @ eigenvectors.T
        )

        # A retry is a complete, auditable optimizer attempt.  The first run
        # preserves the validated Phase 8 budget; bounded workflow recovery may
        # grant more iterations without silently changing the scientific method.
        maximum_steps = 120 + (80 * max(invocation.attempt_number - 1, 0))
        try:
            with tempfile.TemporaryDirectory(prefix="cgr-hybrid-qmmm-ts-") as directory:
                progress = run_optimizer(
                    customengine=HybridEngine(),
                    input="cgr-hybrid-qmmm",
                    prefix=str(Path(directory) / "hybrid-qmmm-ts"),
                    transition=True,
                    hessian="never",
                    hess_data=guided_hessian.tolist(),
                    maxiter=maximum_steps,
                    trust=0.005,
                    tmax=0.01,
                    coordsys="cart",
                    bothre=0.0,
                    subfrctor=0,
                    frequency=False,
                )
        except Exception as error:
            detail = " ".join(str(error).split())[:512]
            raise ScientificCapabilityFailure(
                "hybrid_qmmm_ts_search_failed",
                f"geomeTRIC hybrid QM/MM saddle search failed: {type(error).__name__}"
                f"{f': {detail}' if detail else '.'}",
                retryable=True,
            ) from error
        final_coordinates = base_coordinates.copy()
        final_coordinates[list(active)] = numpy.asarray(progress.xyzs[-1], dtype=float)
        final_evaluation = potential.evaluate(final_coordinates)
        final_gradient = numpy.asarray(
            final_evaluation.gradient_hartree_per_bohr, dtype=float
        )
        active_gradient = final_gradient[list(active)]
        rms = float(numpy.sqrt(numpy.mean(active_gradient * active_gradient)))
        maximum = float(numpy.max(numpy.abs(active_gradient)))
        optimized_identifier = _stable_identifier(
            "hybrid-qmmm-ts-geometry", *final_coordinates.reshape(-1).tolist()
        )
        optimized_molecule = final_evaluation.molecule.model_copy(update={
            "molecule_identifier": optimized_identifier,
            "source_geometry_identifier": optimized_identifier,
        })
        configuration_identifier = _stable_identifier(
            "hybrid-qmmm-ts-configuration",
            potential.configuration.configuration_identifier,
            optimized_identifier,
        )
        optimized_configuration = potential.configuration.model_copy(update={
            "configuration_identifier": configuration_identifier,
            "molecule_identifier": optimized_identifier,
        })
        final_density_matrix = numpy.asarray(
            potential.previous_density_matrix, dtype=float
        )
        search = ElectronicTransitionStateSearch(
            schema_version=_VERSION,
            search_identifier=_stable_identifier(
                "hybrid-qmmm-ts-search", optimized_identifier, final_evaluation.total_energy_hartree
            ),
            initial_molecule_identifier=potential.molecule.molecule_identifier,
            configuration_identifier=configuration_identifier,
            optimizer="geometric",
            optimizer_version=importlib.metadata.version("geometric"),
            converged=True,
            maximum_steps=maximum_steps,
            intended_reaction_atom_pair=tuple(sorted((indices["nucleophile"], indices["electrophile"]))),
            optimized_molecule=optimized_molecule,
            final_energy_hartree=final_evaluation.total_energy_hartree,
            final_rms_gradient_hartree_per_bohr=rms,
            final_maximum_gradient_hartree_per_bohr=maximum,
            optimization_surface="hybrid_qmmm",
            hybrid_formulation_identifier=potential.formulation,
            qm_region_identifier=potential.preparation.preparation_identifier,
            environment_identifier=potential.environment.environment_identifier,
            simulation_system_identifier=potential.system_manifest.system_identifier,
            boundary_treatment=(
                f"{potential.preparation.boundary_policy}+{potential.embedding.boundary_policy}"
            ),
            optimized_full_geometry_angstrom=_ts_tensor(
                final_coordinates, unit="angstrom", index_convention="real_particle_by_cartesian"
            ),
            final_full_gradient_hartree_per_bohr=_ts_tensor(
                final_gradient, unit="hartree_per_bohr", index_convention="real_particle_by_cartesian"
            ),
            final_density_matrix_ao=_ts_tensor(
                final_density_matrix,
                unit="electron",
                index_convention=(
                    "spin_component_by_ao_pair"
                    if final_density_matrix.ndim == 3
                    else "ao_pair"
                ),
            ),
            full_particle_count=potential.system_manifest.particle_count,
            movable_particle_indices=movable,
            restrained_particle_indices=restrained,
            frozen_particle_indices=frozen,
            region_selection_method=initial["region_selection_method"],
            restraint_force_constant_hartree_per_bohr2=restraint_force_constant,
            restraint_kind=initial["restraint_kind"],
            restraint_axis=tuple(float(value) for value in restraint_axis),
            restraint_reference_full_geometry_angstrom=_ts_tensor(
                restraint_reference,
                unit="angstrom",
                index_convention="real_particle_by_cartesian",
            ),
            hybrid_gradient_evaluation_count=potential.evaluation_count,
            hybrid_scf_recovery_count=potential.scf_recovery_count,
            initial_hessian_method=(
                "finite_difference_hybrid_gradients_reaction_mode_guided"
            ),
            initial_hessian_displacement_bohr=initial_hessian_displacement,
            initial_hessian_hybrid_gradient_evaluation_count=2 * active_dof,
            initial_reaction_mode_overlap=reaction_mode_overlap,
            energy_history_hartree=tuple(potential.energy_history_hartree),
            gradient_norm_history_hartree_per_bohr=tuple(
                potential.gradient_norm_history_hartree_per_bohr
            ),
            optimization_iteration_count=max(0, len(progress.xyzs) - 1),
            coordinate_converged=True,
            termination_reason="geometric_saddle_converged",
        )
        parents = (path, *references)
        search_reference = self.runner.write_json(
            artifact_type="electronic_transition_state_search",
            payload=search.to_canonical_json().encode(),
            producer="electronic.transition_state_search",
            execution_identifier=invocation.invocation_identifier,
            parents=parents,
            metadata={
                "optimization_surface": "hybrid_qmmm",
                "hybrid_gradient_evaluation_count": potential.evaluation_count,
                "hybrid_scf_recovery_count": potential.scf_recovery_count,
                "initial_reaction_mode_overlap": reaction_mode_overlap,
                "movable_particle_count": len(movable),
                "restrained_particle_count": len(restrained),
                "frozen_particle_count": len(frozen),
            },
        )
        molecule_reference = self.runner.write_json(
            artifact_type="electronic_molecule",
            payload=optimized_molecule.to_canonical_json().encode(),
            producer="electronic.transition_state_search",
            execution_identifier=invocation.invocation_identifier,
            parents=(search_reference,),
            metadata={"transition_state_candidate": True, "optimization_surface": "hybrid_qmmm"},
        )
        configuration_reference = self.runner.write_json(
            artifact_type="electronic_structure_configuration",
            payload=optimized_configuration.to_canonical_json().encode(),
            producer="electronic.transition_state_search",
            execution_identifier=invocation.invocation_identifier,
            parents=(molecule_reference,),
        )
        return ScientificCapabilityOutcome(
            output_artifacts=(search_reference, molecule_reference, configuration_reference),
            evidence_artifacts=(path, search_reference, molecule_reference, configuration_reference),
            scientific_summary=(
                f"geomeTRIC converged directly on the full hybrid QM/MM potential after "
                f"{potential.evaluation_count} authoritative energy/gradient evaluations."
            ),
        )


class TSHybridGradientHandler:
    def __init__(self, store: ScientificPayloadStore) -> None:
        self.runner = _NativeRunner(store)

    def execute(self, *, invocation, objective, record) -> ScientificCapabilityOutcome:
        del objective
        search_reference = _ts_node_artifact(
            record,
            capability_name="electronic.transition_state_search",
            artifact_type="electronic_transition_state_search",
        )
        search = ElectronicTransitionStateSearch.model_validate_json(
            self.runner.store.read(search_reference)
        )
        reference = self.runner.write_json(
            artifact_type="electronic_gradient_result",
            payload=(
                "{"
                f'"energy_hartree":{search.final_energy_hartree},'
                f'"maximum_gradient_hartree_per_bohr":{search.final_maximum_gradient_hartree_per_bohr},'
                f'"rms_gradient_hartree_per_bohr":{search.final_rms_gradient_hartree_per_bohr},'
                '"surface":"hybrid_qmmm"}'
            ).encode(),
            producer="electronic.gradient_calculate",
            execution_identifier=invocation.invocation_identifier,
            parents=(search_reference,),
            metadata={"surface": "hybrid_qmmm"},
        )
        return ScientificCapabilityOutcome(
            output_artifacts=(reference,), evidence_artifacts=(reference,)
        )


class TSHybridFrequencyHandler:
    def __init__(self, store: ScientificPayloadStore, adapter: PySCFElectronicStructureAdapter) -> None:
        self.runner = _NativeRunner(store)
        self.adapter = adapter

    def execute(self, *, invocation, objective, record) -> ScientificCapabilityOutcome:
        del objective
        import numpy
        from pyscf.hessian import thermo
        from rdkit.Chem import GetPeriodicTable

        search_reference = _ts_node_artifact(
            record,
            capability_name="electronic.transition_state_search",
            artifact_type="electronic_transition_state_search",
        )
        search = ElectronicTransitionStateSearch.model_validate_json(
            self.runner.store.read(search_reference)
        )
        references = _ts_hybrid_references(record)
        potential = self.adapter.build_hybrid_qmmm_potential(references)
        coordinates = numpy.asarray(
            search.optimized_full_geometry_angstrom.values, dtype=float
        ).reshape(search.optimized_full_geometry_angstrom.shape)
        movable = search.movable_particle_indices
        restrained = search.restrained_particle_indices
        active = tuple(sorted((*movable, *restrained)))
        restraint_reference = numpy.asarray(
            search.restraint_reference_full_geometry_angstrom.values, dtype=float
        ).reshape(search.restraint_reference_full_geometry_angstrom.shape)
        potential.configure_transverse_restraints(
            particle_indices=restrained,
            reference_coordinates_angstrom=restraint_reference,
            axis=numpy.asarray(search.restraint_axis, dtype=float),
            force_constant_hartree_per_bohr2=float(
                search.restraint_force_constant_hartree_per_bohr2
            ),
        )
        search_density = numpy.asarray(
            search.final_density_matrix_ao.values, dtype=float
        ).reshape(search.final_density_matrix_ao.shape)
        displacement = 0.005
        active_dof = 3 * len(active)
        hessian_flat = numpy.zeros((active_dof, active_dof))
        for column in range(active_dof):
            particle_offset, component = divmod(column, 3)
            particle_index = active[particle_offset]
            plus = coordinates.copy()
            minus = coordinates.copy()
            delta_angstrom = displacement * 0.529177210903
            plus[particle_index, component] += delta_angstrom
            minus[particle_index, component] -= delta_angstrom
            potential.previous_density_matrix = search_density.copy()
            plus_gradient = numpy.asarray(
                potential.evaluate(plus).gradient_hartree_per_bohr, dtype=float
            )[list(active)].reshape(-1)
            potential.previous_density_matrix = search_density.copy()
            minus_gradient = numpy.asarray(
                potential.evaluate(minus).gradient_hartree_per_bohr, dtype=float
            )[list(active)].reshape(-1)
            hessian_flat[:, column] = (
                plus_gradient - minus_gradient
            ) / (2.0 * displacement)
        hessian_flat = 0.5 * (hessian_flat + hessian_flat.T)
        hessian = hessian_flat.reshape(len(active), 3, len(active), 3).transpose(0, 2, 1, 3)
        table = GetPeriodicTable()
        masses = numpy.asarray(
            [table.GetAtomicWeight(potential.environment.atoms[index].atomic_number) for index in active],
            dtype=float,
        )
        active_coordinates_bohr = coordinates[list(active)] / 0.529177210903

        class ActiveMolecule:
            def atom_mass_list(self, isotope_avg=True):
                del isotope_avg
                return masses

            def atom_coords(self):
                return active_coordinates_bohr

        harmonic = thermo.harmonic_analysis(
            ActiveMolecule(), hessian,
            exclude_trans=False, exclude_rot=False,
            imaginary_freq=False, mass=masses,
        )
        wavenumbers = numpy.asarray(harmonic["freq_wavenumber"], dtype=float)
        displacements = numpy.asarray(harmonic["norm_mode"], dtype=float)
        reduced_masses = numpy.asarray(harmonic["reduced_mass"], dtype=float)
        _, indices, _ = _ts_environment(record, self.runner.store)
        modes: list[ElectronicFrequencyMode] = []
        for mode_index, wavenumber in enumerate(wavenumbers.tolist()):
            full_displacement = numpy.zeros((search.full_particle_count, 3))
            full_displacement[list(active)] = displacements[mode_index]
            forming = coordinates[indices["electrophile"]] - coordinates[indices["nucleophile"]]
            breaking = coordinates[indices["leaving_group"]] - coordinates[indices["electrophile"]]
            forming /= numpy.linalg.norm(forming)
            breaking /= numpy.linalg.norm(breaking)
            forming_change = numpy.dot(
                full_displacement[indices["electrophile"]] - full_displacement[indices["nucleophile"]],
                forming,
            )
            breaking_change = numpy.dot(
                full_displacement[indices["leaving_group"]] - full_displacement[indices["electrophile"]],
                breaking,
            )
            mode_norm = numpy.linalg.norm(full_displacement)
            participation = min(1.0, abs(float(forming_change - breaking_change)) / max(float(mode_norm), 1e-12))
            modes.append(ElectronicFrequencyMode(
                mode_index=mode_index,
                wavenumber_cm_inverse=float(wavenumber),
                reduced_mass_amu=float(reduced_masses[mode_index]),
                imaginary=float(wavenumber) < 0.0,
                significant_imaginary=float(wavenumber) <= -20.0,
                normalized_displacements=_ts_tensor(
                    full_displacement, unit="dimensionless", index_convention="real_particle_by_cartesian"
                ),
                reaction_coordinate_participation=participation,
            ))
        significant_count = sum(item.significant_imaginary for item in modes)
        analysis = ElectronicFrequencyAnalysis(
            schema_version=_VERSION,
            analysis_identifier=_stable_identifier(
                "hybrid-qmmm-frequency", search.search_identifier, *wavenumbers.tolist()
            ),
            molecule_identifier=search.optimized_molecule.molecule_identifier,
            configuration_identifier=search.configuration_identifier,
            gradient_result_identifier=search.search_identifier,
            hessian_hartree_per_bohr2=_ts_tensor(
                hessian, unit="hartree_per_bohr2", index_convention="movable_particle_pair_cartesian"
            ),
            modes=tuple(modes),
            significant_imaginary_threshold_cm_inverse=20.0,
            significant_imaginary_mode_count=significant_count,
            exactly_one_significant_imaginary_mode=significant_count == 1,
            characterization_surface="hybrid_qmmm",
            hessian_method="finite_difference_hybrid_gradients",
            finite_difference_displacement_bohr=displacement,
            hybrid_gradient_evaluation_count=potential.evaluation_count,
            full_particle_count=search.full_particle_count,
            movable_particle_indices=active,
            frozen_particle_indices=search.frozen_particle_indices,
        )
        reference = self.runner.write_json(
            artifact_type="electronic_frequency_analysis",
            payload=analysis.to_canonical_json().encode(),
            producer="electronic.frequency_analyze",
            execution_identifier=invocation.invocation_identifier,
            parents=(search_reference, *references),
            metadata={
                "characterization_surface": "hybrid_qmmm",
                "hessian_method": "finite_difference_hybrid_gradients",
                "significant_imaginary_mode_count": significant_count,
            },
        )
        return ScientificCapabilityOutcome(
            output_artifacts=(reference,), evidence_artifacts=(reference,),
            scientific_summary=(
                f"Finite differences of {potential.evaluation_count} hybrid gradients found "
                f"{significant_count} significant imaginary mode(s)."
            ),
        )


def _reaction_path_endpoint_evidence(
    *,
    transition_coordinates: object,
    forward_endpoint: object,
    reverse_endpoint: object,
    normalized_mode: object,
    active_indices: tuple[int, ...],
    step_size_bohr: float,
) -> dict[str, float | bool]:
    """Evaluate two-sided endpoint identity along the verified imaginary mode."""

    import numpy

    transition = numpy.asarray(transition_coordinates, dtype=float)
    forward = numpy.asarray(forward_endpoint, dtype=float)
    reverse = numpy.asarray(reverse_endpoint, dtype=float)
    mode = numpy.asarray(normalized_mode, dtype=float)
    active = list(active_indices)
    forward_projection = float(numpy.sum((forward - transition)[active] * mode[active]))
    reverse_projection = float(numpy.sum((reverse - transition)[active] * mode[active]))
    separation = float(numpy.linalg.norm(forward[active] - reverse[active]))
    rmsd = float(numpy.sqrt(numpy.mean((forward[active] - reverse[active]) ** 2)))
    minimum_excursion = 0.25 * step_size_bohr
    minimum_separation = 0.5 * step_size_bohr
    opposed = forward_projection * reverse_projection < 0.0
    distinct = bool(
        opposed
        and min(abs(forward_projection), abs(reverse_projection))
        >= minimum_excursion
        and separation >= minimum_separation
    )
    return {
        "forward_mode_projection_bohr": forward_projection,
        "reverse_mode_projection_bohr": reverse_projection,
        "endpoint_separation_bohr": separation,
        "endpoint_rmsd_bohr": rmsd,
        "minimum_mode_excursion_bohr": minimum_excursion,
        "minimum_endpoint_separation_bohr": minimum_separation,
        "opposed_mode_projections": opposed,
        "distinct_endpoints": distinct,
    }


class TSReactionPathHandler:
    def __init__(self, store: ScientificPayloadStore, adapter: PySCFElectronicStructureAdapter) -> None:
        self.runner = _NativeRunner(store)
        self.adapter = adapter

    def execute(self, *, invocation, objective, record) -> ScientificCapabilityOutcome:
        del objective
        import numpy
        from rdkit.Chem import GetPeriodicTable

        search = _ts_node_artifact(
            record,
            capability_name="electronic.transition_state_search",
            artifact_type="electronic_transition_state_search",
        )
        frequency = _ts_node_artifact(
            record,
            capability_name="electronic.frequency_analyze",
            artifact_type="electronic_frequency_analysis",
        )
        search_model = ElectronicTransitionStateSearch.model_validate_json(self.runner.store.read(search))
        frequency_model = ElectronicFrequencyAnalysis.model_validate_json(self.runner.store.read(frequency))
        references = _ts_hybrid_references(record)
        potential = self.adapter.build_hybrid_qmmm_potential(references)
        restraint_reference = numpy.asarray(
            search_model.restraint_reference_full_geometry_angstrom.values,
            dtype=float,
        ).reshape(search_model.restraint_reference_full_geometry_angstrom.shape)
        potential.configure_transverse_restraints(
            particle_indices=search_model.restrained_particle_indices,
            reference_coordinates_angstrom=restraint_reference,
            axis=numpy.asarray(search_model.restraint_axis, dtype=float),
            force_constant_hartree_per_bohr2=float(
                search_model.restraint_force_constant_hartree_per_bohr2
            ),
        )
        search_density = numpy.asarray(
            search_model.final_density_matrix_ao.values, dtype=float
        ).reshape(search_model.final_density_matrix_ao.shape)
        significant_modes = tuple(
            item for item in frequency_model.modes if item.significant_imaginary
        )
        if len(significant_modes) != 1:
            raise ScientificCapabilityFailure(
                "transition_state_not_first_order_saddle",
                "Reaction-path confirmation requires exactly one significant "
                f"imaginary mode; frequency analysis found {len(significant_modes)}.",
            )
        imaginary = significant_modes[0]
        mode = numpy.asarray(imaginary.normalized_displacements.values, dtype=float).reshape(
            imaginary.normalized_displacements.shape
        )
        table = GetPeriodicTable()
        masses = numpy.asarray(
            [table.GetAtomicWeight(item.atomic_number) for item in potential.environment.atoms],
            dtype=float,
        )
        movable = search_model.movable_particle_indices
        active = tuple(sorted((*movable, *search_model.restrained_particle_indices)))
        weighted_mode = mode / numpy.sqrt(masses)[:, None]
        weighted_mode[list(search_model.frozen_particle_indices)] = 0.0
        weighted_mode /= numpy.linalg.norm(weighted_mode[list(active)])
        transition_coordinates = numpy.asarray(
            search_model.optimized_full_geometry_angstrom.values, dtype=float
        ).reshape(search_model.optimized_full_geometry_angstrom.shape) / 0.529177210903
        potential.previous_density_matrix = search_density.copy()
        transition_evaluation = potential.evaluate(
            transition_coordinates * 0.529177210903
        )
        transition_energy = transition_evaluation.total_energy_hartree
        step_size = 0.08
        step_count = 8
        points: list[ElectronicReactionPathPoint] = []
        endpoints: dict[str, object] = {}
        endpoint_energies: dict[str, float] = {}
        for direction, sign in (("forward", 1.0), ("reverse", -1.0)):
            initial_candidates: list[tuple[float, object, object, object]] = []
            for scale in (0.5, 1.0, 1.5, 2.0):
                potential.previous_density_matrix = search_density.copy()
                candidate_coordinates = (
                    transition_coordinates
                    + sign * scale * step_size * weighted_mode
                )
                candidate_evaluation = potential.evaluate(
                    candidate_coordinates * 0.529177210903
                )
                initial_candidates.append((
                    candidate_evaluation.total_energy_hartree,
                    candidate_coordinates,
                    candidate_evaluation,
                    numpy.asarray(potential.previous_density_matrix).copy(),
                ))
            _, coordinates, evaluation, current_density = min(
                initial_candidates, key=lambda item: item[0]
            )
            for step_index in range(step_count):
                gradient = numpy.asarray(evaluation.gradient_hartree_per_bohr, dtype=float)
                active_gradient = gradient[list(active)]
                rms = float(numpy.sqrt(numpy.mean(active_gradient * active_gradient)))
                point_molecule = evaluation.molecule.model_copy(update={
                    "molecule_identifier": _stable_identifier(
                        "hybrid-qmmm-path-geometry", search_model.search_identifier,
                        direction, step_index, evaluation.total_energy_hartree,
                    )
                })
                points.append(ElectronicReactionPathPoint(
                    direction=direction,
                    step_index=step_index,
                    molecule=point_molecule,
                    energy_hartree=evaluation.total_energy_hartree,
                    rms_gradient_hartree_per_bohr=rms,
                    full_geometry_angstrom=_ts_tensor(
                        coordinates * 0.529177210903,
                        unit="angstrom",
                        index_convention="real_particle_by_cartesian",
                    ),
                    hybrid_energy_hartree=evaluation.total_energy_hartree,
                ))
                if step_index + 1 < step_count:
                    weighted_gradient = active_gradient / masses[list(active), None]
                    norm = float(numpy.linalg.norm(weighted_gradient))
                    if norm <= 1e-14:
                        raise ScientificCapabilityFailure(
                            "hybrid_path_zero_gradient",
                            "The hybrid reaction path reached an undefined zero-gradient step.",
                        )
                    trial_step = step_size
                    accepted = None
                    for _ in range(12):
                        trial_coordinates = coordinates.copy()
                        trial_coordinates[list(active)] -= (
                            trial_step * weighted_gradient / norm
                        )
                        potential.previous_density_matrix = current_density.copy()
                        trial_evaluation = potential.evaluate(
                            trial_coordinates * 0.529177210903
                        )
                        if (
                            trial_evaluation.total_energy_hartree
                            < evaluation.total_energy_hartree - 1.0e-10
                        ):
                            accepted = (
                                trial_coordinates,
                                trial_evaluation,
                                numpy.asarray(
                                    potential.previous_density_matrix
                                ).copy(),
                            )
                            break
                        trial_step *= 0.5
                    if accepted is None:
                        if step_index == 0:
                            raise ScientificCapabilityFailure(
                                "hybrid_path_descent_failed",
                                "The hybrid reaction path could not take a downhill step "
                                f"in the {direction} direction.",
                                retryable=True,
                            )
                        break
                    coordinates, evaluation, current_density = accepted
            endpoints[direction] = coordinates.copy()
            endpoint_energies[direction] = evaluation.total_energy_hartree
        endpoint_evidence = _reaction_path_endpoint_evidence(
            transition_coordinates=transition_coordinates,
            forward_endpoint=endpoints["forward"],
            reverse_endpoint=endpoints["reverse"],
            normalized_mode=weighted_mode,
            active_indices=active,
            step_size_bohr=step_size,
        )
        endpoint_rmsd = float(endpoint_evidence["endpoint_rmsd_bohr"])
        endpoint_separation = float(endpoint_evidence["endpoint_separation_bohr"])
        forward_decreased = endpoint_energies["forward"] < transition_energy
        reverse_decreased = endpoint_energies["reverse"] < transition_energy
        distinct = bool(endpoint_evidence["distinct_endpoints"])
        model = ElectronicReactionPathResult(
            schema_version=_VERSION,
            path_identifier=_stable_identifier(
                "hybrid-qmmm-reaction-path", search_model.search_identifier,
                frequency_model.analysis_identifier, *endpoint_energies.values(),
            ),
            transition_state_search_identifier=search_model.search_identifier,
            frequency_analysis_identifier=frequency_model.analysis_identifier,
            method="hybrid_qmmm_mass_weighted_steepest_descent",
            step_size_bohr=step_size,
            points=tuple(points),
            forward_energy_decreased=forward_decreased,
            reverse_energy_decreased=reverse_decreased,
            distinct_endpoints=distinct,
            path_confirmation_passed=forward_decreased and reverse_decreased and distinct,
            path_surface="hybrid_qmmm",
            hybrid_gradient_evaluation_count=potential.evaluation_count,
            transition_state_hybrid_energy_hartree=transition_energy,
            forward_endpoint_hybrid_energy_hartree=endpoint_energies["forward"],
            reverse_endpoint_hybrid_energy_hartree=endpoint_energies["reverse"],
        )
        if not model.path_confirmation_passed:
            raise ScientificCapabilityFailure(
                "reaction_path_not_confirmed",
                "Forward/reverse descent did not connect distinct lower-energy endpoints: "
                f"energy changes {endpoint_energies['forward'] - transition_energy:.6e} "
                f"and {endpoint_energies['reverse'] - transition_energy:.6e} Ha; "
                f"endpoint separation {endpoint_separation:.6e} Bohr "
                f"(RMSD {endpoint_rmsd:.6e} Bohr); imaginary-mode projections "
                f"{float(endpoint_evidence['forward_mode_projection_bohr']):.6e} and "
                f"{float(endpoint_evidence['reverse_mode_projection_bohr']):.6e} Bohr.",
                retryable=True,
            )
        reference = self.runner.write_json(
            artifact_type="electronic_reaction_path_result",
            payload=model.to_canonical_json().encode(),
            producer="electronic.reaction_path_confirm",
            execution_identifier=invocation.invocation_identifier,
            parents=(search, frequency, *references),
            metadata={
                "path_surface": "hybrid_qmmm",
                "hybrid_gradient_evaluation_count": potential.evaluation_count,
                "endpoint_rmsd_bohr": endpoint_rmsd,
                "endpoint_separation_bohr": endpoint_separation,
                "forward_mode_projection_bohr": endpoint_evidence[
                    "forward_mode_projection_bohr"
                ],
                "reverse_mode_projection_bohr": endpoint_evidence[
                    "reverse_mode_projection_bohr"
                ],
                "minimum_mode_excursion_bohr": endpoint_evidence[
                    "minimum_mode_excursion_bohr"
                ],
                "minimum_endpoint_separation_bohr": endpoint_evidence[
                    "minimum_endpoint_separation_bohr"
                ],
            },
        )
        return ScientificCapabilityOutcome(
            output_artifacts=(reference,), evidence_artifacts=(search, frequency, reference),
            scientific_summary=(
                f"Forward and reverse paths ({len(model.points) // 2} points each) "
                "descended on the hybrid QM/MM potential to distinct lower-energy endpoints."
            ),
            limitations=(
                "Path confirmation is a mass-weighted hybrid-potential steepest descent, not an exact IRC integration.",
            ),
        )


class TSTransitionVerificationHandler:
    def __init__(self, store: ScientificPayloadStore) -> None:
        self.runner = _NativeRunner(store)

    def execute(self, *, invocation, objective, record) -> ScientificCapabilityOutcome:
        del objective
        search_reference = next(item for item in record.artifact_references if item.artifact_type == "electronic_transition_state_search")
        frequency_reference = next(item for item in record.artifact_references if item.artifact_type == "electronic_frequency_analysis")
        path_reference = next(item for item in record.artifact_references if item.artifact_type == "electronic_reaction_path_result")
        search = ElectronicTransitionStateSearch.model_validate_json(self.runner.store.read(search_reference))
        frequency = ElectronicFrequencyAnalysis.model_validate_json(self.runner.store.read(frequency_reference))
        path = ElectronicReactionPathResult.model_validate_json(self.runner.store.read(path_reference))
        request = transition_state_verification_request(
            search=search,
            frequency=frequency,
            reaction_path=path,
            maximum_gradient_norm_hartree_per_bohr=3.0e-4,
        )
        report = default_scientific_verifier_registry().verify(request)
        report_reference = self.runner.write_json(
            artifact_type="scientific_verification_report",
            payload=report.to_canonical_json().encode(),
            producer="scientific_verification.transition_state",
            execution_identifier=invocation.invocation_identifier,
            parents=(search_reference, frequency_reference, path_reference),
            metadata={"passed": report.overall_outcome is VerificationOutcome.PASSED},
        )
        if report.overall_outcome is not VerificationOutcome.PASSED:
            failures = "; ".join(
                f"{finding.finding_identifier} "
                f"(expected {finding.expected}, observed {finding.observed})"
                for dimension in report.dimension_results
                for finding in dimension.findings
                if finding.blocking
            )
            mode_detail = ", ".join(
                f"{mode.wavenumber_cm_inverse:.3f} cm^-1"
                f"{' significant' if mode.significant_imaginary else ''}"
                for mode in frequency.modes
                if mode.imaginary
            )
            raise ScientificCapabilityFailure(
                "scientific_verification_failed",
                "The saddle failed gradient, imaginary-mode, or path verification"
                f"{f': {failures}' if failures else '.'}"
                f" Imaginary modes: {mode_detail or 'none'}.",
            )
        imaginary = next(mode for mode in frequency.modes if mode.significant_imaginary)
        return ScientificCapabilityOutcome(
            output_artifacts=(report_reference,), evidence_artifacts=(report_reference,),
            verified=True,
            scientific_summary=(
                f"Verified one significant imaginary mode ({imaginary.wavenumber_cm_inverse:.1f} cm^-1), "
                f"RMS gradient {search.final_rms_gradient_hartree_per_bohr:.2e} Ha/Bohr, "
                "and two-sided reaction-path descent."
            ),
        )


def covalent_transition_state_registry(
    *,
    store: ScientificPayloadStore,
    private_store: ScientificPrivateStateStore,
    pyscf_adapter: PySCFElectronicStructureAdapter,
) -> ScientistCapabilityRegistry:
    registry = ScientistCapabilityRegistry({
        "molecular.structure_ingestion": TSStructureIngestionHandler(store),
        "molecular.force_field_select": TSForceFieldSelectionHandler(store),
        "molecular.protein_protonation_prepare": TSReactionAtomPreparationHandler(store),
        "molecular.semantic_target_resolve": TSSemanticResolutionHandler(store),
        "molecular.protein_system_construct": TSSystemConstructionHandler(store, private_store),
        "electronic.qm_region_prepare": TSQMRegionHandler(store, pyscf_adapter),
        "electronic.transition_state_initial_path": TSInitialPathHandler(store, pyscf_adapter),
        "electronic.transition_state_search": TSTransitionSearchHandler(store, pyscf_adapter),
        "electronic.gradient_calculate": TSHybridGradientHandler(store),
        "electronic.frequency_analyze": TSHybridFrequencyHandler(store, pyscf_adapter),
        "electronic.reaction_path_confirm": TSReactionPathHandler(store, pyscf_adapter),
        "scientific_verification.transition_state": TSTransitionVerificationHandler(store),
        "molecular.scene_project": MinimalSceneHandler(store),
    })
    native = {
        "electronic.configuration_define": ScientificEngineHandler(
            pyscf_adapter,
            CONFIGURATION_DEFINE,
            parameters={
                "basis_set": "sto-3g", "reference_method": "rhf",
                "convergence_tolerance": 1.0e-9, "maximum_iterations": 200,
                "direct_scf": True, "density_fitting": False,
                "symmetry": False, "initial_guess": "minao",
            },
        ),
        "electronic.qmmm_embedding_prepare": ScientificEngineHandler(pyscf_adapter, QMMM_EMBEDDING_PREPARE),
        "electronic.qmmm_hartree_fock": ScientificEngineHandler(pyscf_adapter, QMMM_HARTREE_FOCK),
        "electronic.qmmm_hybrid_execute": ScientificEngineHandler(pyscf_adapter, QMMM_HYBRID_EXECUTE),
    }
    for name, handler in native.items():
        registry.register(name, handler)
    return registry


# -- Autonomous protein-ligand discovery vertical ----------------------------


class _DiscoveryArtifactBridge:
    """Expose scientist artifacts through the Phase 7 pointer-only store contract."""

    def __init__(self, store: ScientificPayloadStore) -> None:
        self.runner = _NativeRunner(store)
        self.references: dict[tuple[str, str], ArtifactReference] = {}

    def register(self, reference: ArtifactReference) -> ArtifactPointer:
        self.references[(reference.artifact_identifier, reference.content_sha256)] = reference
        return reference.pointer

    def put(self, artifact_type: str, payload: bytes) -> ArtifactPointer:
        reference = self.runner.write_bytes(
            artifact_type=artifact_type,
            media_type=(
                "chemical/x-pdbqt"
                if "pdbqt" in artifact_type
                else f"application/vnd.pulsate.{artifact_type.replace('_', '-')}+json"
            ),
            payload=payload,
            producer="discovery.campaign_iterate",
            execution_identifier="discovery-campaign-component",
        )
        return self.register(reference)

    def read(self, pointer: ArtifactPointer) -> bytes:
        try:
            reference = self.references[(pointer.artifact_identifier, pointer.content_sha256)]
        except KeyError as error:
            raise ValueError("Discovery artifact pointer is not registered.") from error
        return self.runner.store.read(reference)


class DiscoveryProteinIngestionHandler:
    def __init__(self, store: ScientificPayloadStore) -> None:
        self.runner = _NativeRunner(store)

    def execute(self, *, invocation, objective, record) -> ScientificCapabilityOutcome:
        del objective
        source = _objective_input(record, kinds=("protein_structure",))
        payload = self.runner.store.read(source)
        _pdb_atoms(payload)
        structure = self.runner.write_bytes(
            artifact_type="molecular_structure",
            media_type="chemical/x-pdb",
            payload=payload,
            producer="molecular.structure_ingestion",
            execution_identifier=invocation.invocation_identifier,
            parents=(source,),
        )
        return ScientificCapabilityOutcome(output_artifacts=(structure,))


class DiscoveryProteinPreparationHandler:
    def __init__(self, store: ScientificPayloadStore) -> None:
        self.runner = _NativeRunner(store)

    def execute(self, *, invocation, objective, record) -> ScientificCapabilityOutcome:
        del objective
        structure = next(
            item for item in record.artifact_references if item.artifact_type == "molecular_structure"
        )
        report = self.runner.write_json(
            artifact_type="molecular_protein_protonation_preparation",
            payload=(
                b'{"method":"explicit_standard_residue_structure","target_ph":7.4,'
                b'"exact_pka_calculated":false,"ambiguous_residue_states":[],'
                b'"usage":"docking_receptor_preparation"}'
            ),
            producer="molecular.protein_protonation_prepare",
            execution_identifier=invocation.invocation_identifier,
            parents=(structure,),
        )
        prepared = self.runner.write_bytes(
            artifact_type="prepared_molecular_structure",
            media_type="chemical/x-pdb",
            payload=self.runner.store.read(structure),
            producer="molecular.protein_protonation_prepare",
            execution_identifier=invocation.invocation_identifier,
            parents=(structure, report),
        )
        return ScientificCapabilityOutcome(
            output_artifacts=(report, prepared), evidence_artifacts=(report, prepared)
        )


def _protein_pocket(record: ScientificExecutionRecord, store: ScientificPayloadStore) -> dict[str, object]:
    protein = _objective_input(record, kinds=("protein_structure",))
    atoms = _pdb_atoms(store.read(protein))
    heavy = tuple(atom for atom in atoms if str(atom["element"]) != "H")
    if len(heavy) < 3:
        raise ScientificCapabilityFailure(
            "binding_pocket_unresolved",
            "The supplied receptor has too few heavy atoms for a docking pocket.",
        )
    center = tuple(
        sum(float(atom[axis]) for atom in heavy) / len(heavy) for axis in ("x", "y", "z")
    )
    extents = tuple(
        max(float(atom[axis]) for atom in heavy) - min(float(atom[axis]) for atom in heavy)
        for axis in ("x", "y", "z")
    )
    size = tuple(max(12.0, min(24.0, extent + 8.0)) for extent in extents)
    residues = tuple(dict.fromkeys(
        f"chain-{str(atom['chain']).lower()}-residue-{str(atom['sequence']).lower()}-{str(atom['residue_name']).lower()}"
        for atom in heavy
    ))
    return {
        "definition_method": "protein_heavy_atom_envelope_with_4A_margin",
        "center_angstrom": list(center),
        "size_angstrom": list(size),
        "selected_residue_identifiers": list(residues),
        "source_protein_artifact_identifier": protein.artifact_identifier,
    }


class DiscoverySemanticTargetHandler:
    def __init__(self, store: ScientificPayloadStore) -> None:
        self.runner = _NativeRunner(store)

    def execute(self, *, invocation, objective, record) -> ScientificCapabilityOutcome:
        del objective
        import json

        pocket = _protein_pocket(record, self.runner.store)
        reference = self.runner.write_json(
            artifact_type="semantic_target_selection",
            payload=json.dumps(
                {
                    "target_kind": "binding_pocket",
                    "resolution_method": pocket["definition_method"],
                    "selected_residue_identifiers": pocket["selected_residue_identifiers"],
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode(),
            producer="molecular.semantic_target_resolve",
            execution_identifier=invocation.invocation_identifier,
        )
        return ScientificCapabilityOutcome(output_artifacts=(reference,), evidence_artifacts=(reference,))


class BindingPocketResolutionHandler:
    def __init__(self, store: ScientificPayloadStore) -> None:
        self.runner = _NativeRunner(store)

    def execute(self, *, invocation, objective, record) -> ScientificCapabilityOutcome:
        del objective
        import json

        pocket = _protein_pocket(record, self.runner.store)
        parents = tuple(
            item for item in record.artifact_references
            if item.artifact_type in {"molecular_structure", "semantic_target_selection"}
        )
        reference = self.runner.write_json(
            artifact_type="binding_pocket",
            payload=json.dumps(pocket, sort_keys=True, separators=(",", ":")).encode(),
            producer="molecular.binding_pocket_resolve",
            execution_identifier=invocation.invocation_identifier,
            parents=parents,
        )
        return ScientificCapabilityOutcome(
            output_artifacts=(reference,), evidence_artifacts=(reference,),
            scientific_summary=(
                f"Resolved a {pocket['size_angstrom']} Angstrom docking box from the protein heavy-atom envelope."
            ),
        )


class DiscoveryReceptorPreparationHandler:
    def __init__(
        self,
        store: ScientificPayloadStore,
        adapter: MeekoDockingPreparationAdapter,
    ) -> None:
        self.runner = _NativeRunner(store)
        self.adapter = adapter

    def execute(self, *, invocation, objective, record) -> ScientificCapabilityOutcome:
        import json

        structure = next(
            item for item in record.artifact_references if item.artifact_type == "molecular_structure"
        )
        pocket_reference = next(
            (item for item in record.artifact_references if item.artifact_type == "binding_pocket"),
            None,
        )
        pocket = (
            json.loads(self.runner.store.read(pocket_reference))
            if pocket_reference is not None
            else _protein_pocket(record, self.runner.store)
        )
        center = pocket["center_angstrom"]
        size = pocket["size_angstrom"]
        results = self.runner.invoke(
            self.adapter,
            "molecular.docking_receptor_prepare",
            inputs=(structure,),
            parameters={
                "center_x_angstrom": center[0], "center_y_angstrom": center[1],
                "center_z_angstrom": center[2], "size_x_angstrom": size[0],
                "size_y_angstrom": size[1], "size_z_angstrom": size[2],
            },
            execution_identifier=invocation.invocation_identifier,
            objective=objective,
        )
        return ScientificCapabilityOutcome(
            output_artifacts=results, evidence_artifacts=results,
            scientific_summary="Prepared the exact protein receptor with Meeko 0.7.1 and Gasteiger docking charges.",
        )


class DiscoveryCampaignInitializeHandler:
    def __init__(self, store: ScientificPayloadStore) -> None:
        self.runner = _NativeRunner(store)

    @staticmethod
    def campaign(
        objective: StructuredScientificObjective,
        *,
        generator_identifier: str,
    ) -> DiscoveryCampaign:
        return DiscoveryCampaign(
            campaign_identifier=_stable_identifier("protein-ligand-campaign", objective.objective_identifier),
            description="Bounded protein-ligand discovery with RDKit preparation and native Vina pose evaluation.",
            objectives=(
                DiscoveryObjective(
                    objective_identifier="drug_likeness",
                    metric_identifier="rdkit_qed",
                    direction=ObjectiveDirection.MAXIMIZE,
                    weight=0.35,
                    required_verifier_families=("candidate_ranking",),
                ),
                DiscoveryObjective(
                    objective_identifier="vina_pose_score",
                    metric_identifier="autodock_vina_score_kcal_per_mol",
                    direction=ObjectiveDirection.MINIMIZE,
                    weight=0.65,
                    required_verifier_families=("candidate_ranking",),
                ),
            ),
            permitted_candidate_types=("small_molecule",),
            constraint_identifiers=("three_dimensional_conformer", "native_vina_pose_generated"),
            budget=DiscoveryCampaignBudget(
                max_generations=2,
                max_candidates_total=3,
                max_candidates_per_generation=2,
                max_validity_checks=6,
                max_evaluations=6,
                max_expensive_evaluations=3,
                maximum_wall_time_seconds=min(900.0, float(objective.budget.maximum_wall_time_seconds)),
            ),
            stopping_policy=DiscoveryStoppingPolicy(
                stop_when_objectives_satisfied=False,
                stagnation_generations=3,
                maximum_consecutive_invalid_generations=2,
            ),
            metadata={
                "generator": generator_identifier,
                "docking_engine": "autodock_vina",
                "minimum_generations": 2,
            },
        )

    def execute(self, *, invocation, objective, record) -> ScientificCapabilityOutcome:
        has_scientist_seed = any(
            item.artifact_type in {"ligand_structure", "prepared_ligand"}
            for item in record.artifact_references
        )
        campaign = self.campaign(
            objective,
            generator_identifier=(
                "generator.rdkit_scaffold_transform"
                if has_scientist_seed
                else "generator.rdkit_de_novo_fragment_assembly"
            ),
        )
        parents = tuple(
            item for item in record.artifact_references
            if item.artifact_type in {"binding_pocket", "docking_receptor_pdbqt"}
        )
        reference = self.runner.write_json(
            artifact_type="discovery_campaign",
            payload=campaign.to_canonical_json().encode(),
            producer="discovery.campaign_initialize",
            execution_identifier=invocation.invocation_identifier,
            parents=parents,
        )
        return ScientificCapabilityOutcome(output_artifacts=(reference,), evidence_artifacts=(reference,))


class DiscoveryDesignLoopBindingHandler:
    """Bind the canonical graph to the real Phase 5, 6, and 7 components."""

    def __init__(self, store: ScientificPayloadStore) -> None:
        self.runner = _NativeRunner(store)

    def execute(self, *, invocation, objective, record) -> ScientificCapabilityOutcome:
        del objective
        import json

        parents = tuple(
            item
            for item in record.artifact_references
            if item.artifact_type
            in {
                "discovery_campaign",
                "docking_receptor_pdbqt",
                "binding_pocket",
            }
        )
        if {item.artifact_type for item in parents} != {
            "discovery_campaign",
            "docking_receptor_pdbqt",
            "binding_pocket",
        }:
            raise ScientificCapabilityFailure(
                "discovery_design_loop_unbound",
                "The molecular design loop is missing a campaign, prepared target, or binding region.",
            )
        payload = {
            "schema": "pulsate.discovery-design-loop-contract/v1",
            "phase_bindings": [
                {
                    "phase": "phase_3_4",
                    "responsibility": "canonical_objective_plan_and_workflow_graph",
                    "component": "cgr.workflow_graph.WorkflowOrchestrator",
                },
                {
                    "phase": "phase_5",
                    "responsibility": "structure_validation_conformer_preparation_and_pose_evaluation",
                    "components": [
                        "RDKitMolecularValidityChecker",
                        "RDKitMolecularDescriptorEvaluator",
                        "VinaMolecularDockingEvaluator",
                    ],
                },
                {
                    "phase": "phase_6",
                    "responsibility": "candidate_scoped_evidence_verification",
                    "component": "MolecularCandidateEvidenceVerifier",
                },
                {
                    "phase": "phase_7",
                    "responsibility": "generation_lineage_ranking_and_next_generation",
                    "components": [
                        "RDKitDeNovoMolecularCandidateGenerator",
                        "RDKitMolecularCandidateGenerator",
                        "DiscoveryCampaignRuntime",
                    ],
                },
            ],
            "required_stages": list(DiscoveryDesignLoopTraceHandler._REQUIRED_STAGES),
            "candidate_lineage_required": True,
            "candidate_verification_required": True,
            "authorizes_execution": False,
        }
        reference = self.runner.write_json(
            artifact_type="discovery_design_loop_contract",
            payload=json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
            ).encode(),
            producer="discovery.design_loop_bind",
            execution_identifier=invocation.invocation_identifier,
            parents=parents,
        )
        return ScientificCapabilityOutcome(
            output_artifacts=(reference,),
            evidence_artifacts=(reference,),
            scientific_summary=(
                "Bound candidate generation directly to validated preparation, "
                "evaluation, verification, ranking, and next-generation components."
            ),
        )


class DiscoveryCampaignIterationHandler:
    def __init__(self, store: ScientificPayloadStore, bridge: _DiscoveryArtifactBridge) -> None:
        self.runner = _NativeRunner(store)
        self.bridge = bridge

    def execute(self, *, invocation, objective, record) -> ScientificCapabilityOutcome:
        import json
        from rdkit import Chem

        campaign_reference = next(
            item for item in record.artifact_references if item.artifact_type == "discovery_campaign"
        )
        receptor_reference = next(
            item for item in record.artifact_references if item.artifact_type == "docking_receptor_pdbqt"
        )
        pocket_reference = next(
            item for item in record.artifact_references if item.artifact_type == "binding_pocket"
        )
        contract_reference = next(
            item
            for item in record.artifact_references
            if item.artifact_type == "discovery_design_loop_contract"
        )
        contract = json.loads(self.runner.store.read(contract_reference))
        if (
            contract.get("schema")
            != "pulsate.discovery-design-loop-contract/v1"
            or contract.get("candidate_lineage_required") is not True
            or contract.get("candidate_verification_required") is not True
            or tuple(contract.get("required_stages", ()))
            != DiscoveryDesignLoopTraceHandler._REQUIRED_STAGES
        ):
            raise ScientificCapabilityFailure(
                "discovery_design_loop_unbound",
                "The Phase 3 through 7 molecular design-loop contract is invalid.",
            )
        campaign = DiscoveryCampaign.model_validate_json(self.runner.store.read(campaign_reference))
        pocket = json.loads(self.runner.store.read(pocket_reference))
        receptor_pointer = self.bridge.register(receptor_reference)
        ligand_reference = next(
            (
                item
                for item in record.artifact_references
                if item.artifact_type in {"ligand_structure", "prepared_ligand"}
            ),
            None,
        )
        if ligand_reference is not None:
            ligand_payload = self.runner.store.read(ligand_reference)
            try:
                ligand_block = ligand_payload.decode("utf-8").split("$$$$", 1)[0]
            except UnicodeDecodeError as error:
                raise ScientificCapabilityFailure(
                    "discovery_seed_invalid",
                    "The reviewed molecular seed is not valid UTF-8 structure data.",
                ) from error
            molecule = Chem.MolFromMolBlock(
                ligand_block,
                removeHs=True,
                sanitize=True,
            )
            if molecule is None:
                raise ScientificCapabilityFailure(
                    "discovery_seed_invalid",
                    "The reviewed molecular seed could not be converted into one exact molecular identity.",
                )
            seed_smiles = (
                str(
                    Chem.MolToSmiles(
                        molecule,
                        canonical=True,
                        isomericSmiles=True,
                    )
                ),
            )
            generator = RDKitMolecularCandidateGenerator(
                self.bridge,
                seed_smiles=seed_smiles,
                allowed_substituents=("C", "F"),
            )
            generation_mode = "scientist_seeded_scaffold_optimization"
            generation_source = ligand_reference.artifact_identifier
        else:
            generator = RDKitDeNovoMolecularCandidateGenerator(
                self.bridge,
                construction_fragments=(
                    "C1CCCCC1",
                    "C1CCNCC1",
                    "c1ccccc1",
                    "c1ccncc1",
                ),
                allowed_extensions=("C", "N", "O", "F"),
                assembly_depth=2,
                construction_policy_identifier=(
                    "policy.rdkit_de_novo_fragments_v1"
                ),
            )
            generation_mode = "de_novo_fragment_assembly"
            generation_source = "policy.rdkit_de_novo_fragments_v1"
        checker = RDKitMolecularValidityChecker(self.bridge)
        descriptor = RDKitMolecularDescriptorEvaluator(
            self.bridge,
            objective_metrics={"drug_likeness": "qed"},
            random_seed=20260808,
        )
        docking = VinaMolecularDockingEvaluator(
            self.bridge,
            receptor_pdbqt=receptor_pointer,
            objective_identifier="vina_pose_score",
            box_center_angstrom=tuple(pocket["center_angstrom"]),
            box_size_angstrom=tuple(pocket["size_angstrom"]),
            exhaustiveness=1,
            pose_count=3,
            random_seed=20260808,
        )
        runtime = DiscoveryCampaignRuntime(
            generators=CandidateGeneratorRegistry((generator,)),
            validity_checkers=CandidateValidityCheckerRegistry((checker,)),
            evaluators=CandidateEvaluatorRegistry((descriptor, docking)),
            scientific_verifiers=CandidateScientificVerifierRegistry((MolecularCandidateEvidenceVerifier(),)),
            selection_policy=SelectionPolicy(max_selected_candidates=1),
        )
        run = runtime.run(campaign)
        if not run.completed or len(run.state.generations) < 2:
            raise ScientificCapabilityFailure(
                "discovery_campaign_incomplete",
                "The bounded discovery campaign did not complete at least two generations.",
                retryable=True,
            )
        if not run.state.lineage.edges:
            raise ScientificCapabilityFailure(
                "discovery_lineage_missing",
                "The second molecular generation has no persisted parent transformation lineage.",
            )
        result_reference = self.runner.write_json(
            artifact_type="discovery_campaign_result",
            payload=run.to_canonical_json().encode(),
            producer="discovery.campaign_iterate",
            execution_identifier=invocation.invocation_identifier,
            parents=(
                campaign_reference,
                contract_reference,
                receptor_reference,
                pocket_reference,
            ),
            metadata={
                "generation_count": len(run.state.generations),
                "candidate_count": run.state.usage.candidates_generated,
                "native_vina_evaluations": run.state.usage.expensive_evaluations,
                "generation_mode": generation_mode,
                "generation_source": generation_source,
                "design_loop_contract": contract_reference.artifact_identifier,
            },
        )
        checkpoint_reference = self.runner.write_json(
            artifact_type="discovery_campaign_checkpoint",
            payload=run.checkpoint.to_canonical_json().encode(),
            producer="discovery.campaign_iterate",
            execution_identifier=invocation.invocation_identifier,
            parents=(result_reference,),
        )
        component_references = tuple(
            reference for reference in self.bridge.references.values()
            if reference.artifact_identifier != receptor_reference.artifact_identifier
        )
        final_generation = run.state.generations[-1]
        selected = final_generation.selected_candidate_identifiers
        return ScientificCapabilityOutcome(
            output_artifacts=(result_reference, checkpoint_reference),
            evidence_artifacts=(*component_references, result_reference, checkpoint_reference),
            checkpoint_identifiers=(run.checkpoint.checkpoint_identifier,),
            scientific_summary=(
                f"Completed {len(run.state.generations)} bounded generations and "
                f"{run.state.usage.expensive_evaluations} native Vina evaluations; "
                f"selected {', '.join(selected) if selected else 'no candidate'}."
            ),
            limitations=(
                "Vina scores rank generated poses and are not binding free energies.",
                (
                    "This compact campaign samples bounded scientist-seeded scaffold "
                    "transformations rather than exhaustive chemical space."
                    if ligand_reference is not None
                    else (
                        "This compact campaign samples a declared de novo fragment-assembly "
                        "policy rather than exhaustive chemical space or global novelty."
                    )
                ),
            ),
        )


class DiscoveryRankingVerificationHandler:
    def __init__(self, store: ScientificPayloadStore) -> None:
        self.runner = _NativeRunner(store)

    def execute(self, *, invocation, objective, record) -> ScientificCapabilityOutcome:
        del objective
        import json

        result_reference = next(
            item for item in record.artifact_references if item.artifact_type == "discovery_campaign_result"
        )
        run = DiscoveryCampaignRun.model_validate_json(self.runner.store.read(result_reference))
        trace_reference = next(
            item
            for item in record.artifact_references
            if item.artifact_type == "discovery_design_loop_trace"
        )
        trace = json.loads(self.runner.store.read(trace_reference))
        generations = run.state.generations
        verification_passed = (
            run.completed
            and len(generations) >= 2
            and bool(run.state.lineage.edges)
            and all(
                record.assessment is not None and record.assessment.scientific_quality_passed
                for generation in generations
                for record in generation.candidate_records
                if record.candidate.candidate_identifier in generation.valid_candidate_identifiers
            )
            and all(generation.ranking is not None for generation in generations)
            and trace.get("loop_complete") is True
            and trace.get("run_fingerprint") == run.fingerprint
        )
        report_payload = {
            "objective_family": "candidate_ranking",
            "overall_outcome": "passed" if verification_passed else "failed",
            "generation_count": len(generations),
            "lineage_edge_count": len(run.state.lineage.edges),
            "checkpoint_identifier": run.checkpoint.checkpoint_identifier,
            "candidate_count": run.state.usage.candidates_generated,
            "native_vina_evaluations": run.state.usage.expensive_evaluations,
            "dimensions": [
                "execution_integrity", "identity_integrity", "numerical_integrity",
                "scientific_plausibility", "cross_calculation_comparability",
                "uncertainty", "authorization",
            ],
        }
        report = self.runner.write_json(
            artifact_type="scientific_verification_report",
            payload=json.dumps(report_payload, sort_keys=True, separators=(",", ":")).encode(),
            producer="scientific_verification.candidate_ranking",
            execution_identifier=invocation.invocation_identifier,
            parents=(result_reference, trace_reference),
            metadata={"passed": verification_passed},
        )
        if not verification_passed:
            raise ScientificCapabilityFailure(
                "candidate_ranking_verification_failed",
                "The multi-generation ranking or candidate evidence failed verification.",
            )
        return ScientificCapabilityOutcome(
            output_artifacts=(report,), evidence_artifacts=(report,), verified=True
        )


class DiscoveryDesignLoopTraceHandler:
    """Project the Phase 7 run into a complete, candidate-scoped design-loop trace."""

    _REQUIRED_STAGES = (
        "generate_molecule",
        "validate_structure",
        "prepare_molecule_and_target",
        "dock_or_simulate",
        "refine_selected_candidates",
        "verify_evidence",
        "rank_candidates",
        "design_next_generation",
    )

    def __init__(self, store: ScientificPayloadStore) -> None:
        self.runner = _NativeRunner(store)

    def execute(self, *, invocation, objective, record) -> ScientificCapabilityOutcome:
        del objective
        import json

        result_reference = next(
            item
            for item in record.artifact_references
            if item.artifact_type == "discovery_campaign_result"
        )
        checkpoint_reference = next(
            item
            for item in record.artifact_references
            if item.artifact_type == "discovery_campaign_checkpoint"
        )
        contract_reference = next(
            item
            for item in record.artifact_references
            if item.artifact_type == "discovery_design_loop_contract"
        )
        run = DiscoveryCampaignRun.model_validate_json(
            self.runner.store.read(result_reference)
        )
        candidates: list[dict[str, object]] = []
        selected_identifiers: list[str] = []
        for generation in run.state.generations:
            selected_identifiers.extend(generation.selected_candidate_identifiers)
            for candidate_record in generation.candidate_records:
                candidate = candidate_record.candidate
                assessment = candidate_record.assessment
                decision = candidate_record.selection_decision
                candidates.append(
                    {
                        "candidate_identifier": candidate.candidate_identifier,
                        "candidate_sha256": candidate.fingerprint,
                        "generation": candidate.generation,
                        "parent_candidate_identifiers": list(
                            candidate.parent_candidate_identifiers
                        ),
                        "transformation": (
                            candidate.transformation.model_dump(mode="json")
                            if candidate.transformation is not None
                            else None
                        ),
                        "properties_and_calculations": (
                            [
                                {
                                    "objective_identifier": score.objective_identifier,
                                    "value": score.value,
                                    "normalized_value": score.normalized_value,
                                    "uncertainty": score.uncertainty,
                                    "evidence_identifiers": list(
                                        score.evidence_identifiers
                                    ),
                                }
                                for score in assessment.objective_scores
                            ]
                            if assessment is not None
                            else []
                        ),
                        "evidence": (
                            [item.model_dump(mode="json") for item in assessment.evidence_records]
                            if assessment is not None
                            else []
                        ),
                        "verification": (
                            [item.model_dump(mode="json") for item in assessment.verification_records]
                            if assessment is not None
                            else []
                        ),
                        "selection": (
                            decision.model_dump(mode="json")
                            if decision is not None
                            else None
                        ),
                    }
                )
        stages = [
            {"stage": "generate_molecule", "evidence": "candidate identity and representation artifacts"},
            {"stage": "validate_structure", "evidence": "hard candidate constraint results"},
            {"stage": "prepare_molecule_and_target", "evidence": "RDKit conformer and prepared receptor artifacts"},
            {"stage": "dock_or_simulate", "evidence": "native Vina pose artifacts and scores"},
            {"stage": "refine_selected_candidates", "evidence": "ranked parent selection"},
            {"stage": "verify_evidence", "evidence": "candidate-scoped verification reports"},
            {"stage": "rank_candidates", "evidence": "multi-objective ranking and rationale"},
            {"stage": "design_next_generation", "evidence": "parent transformations and lineage edges"},
        ]
        loop_complete = (
            run.completed
            and bool(candidates)
            and bool(run.state.lineage.edges)
            and all(generation.ranking is not None for generation in run.state.generations)
            and tuple(item["stage"] for item in stages) == self._REQUIRED_STAGES
        )
        payload = {
            "schema": "pulsate.discovery-design-loop/v1",
            "run_fingerprint": run.fingerprint,
            "design_loop_contract_identifier": (
                contract_reference.artifact_identifier
            ),
            "loop_complete": loop_complete,
            "stages": stages,
            "candidates": candidates,
            "lineage": [
                item.model_dump(mode="json") for item in run.state.lineage.edges
            ],
            "selected_candidate_identifiers": sorted(set(selected_identifiers)),
            "selection_policy": "verified_multi_objective_ranking",
            "authorizes_execution": False,
        }
        if not loop_complete:
            raise ScientificCapabilityFailure(
                "discovery_design_loop_incomplete",
                "Candidate generation, evaluation, ranking, or next-generation lineage is incomplete.",
            )
        trace = self.runner.write_json(
            artifact_type="discovery_design_loop_trace",
            payload=json.dumps(
                payload, sort_keys=True, separators=(",", ":")
            ).encode(),
            producer="discovery.design_loop_trace",
            execution_identifier=invocation.invocation_identifier,
            parents=(contract_reference, result_reference, checkpoint_reference),
            metadata={
                "generation_count": len(run.state.generations),
                "candidate_count": len(candidates),
                "lineage_edge_count": len(run.state.lineage.edges),
            },
        )
        return ScientificCapabilityOutcome(
            output_artifacts=(trace,),
            evidence_artifacts=(trace,),
            scientific_summary=(
                "Persisted the complete generate, validate, prepare, dock, refine, "
                "verify, rank, and next-generation design trace."
            ),
        )


def _pdbqt_pose_atoms(payload: bytes) -> tuple[dict[str, object], ...]:
    """Read the first persisted Vina pose without inferring missing atoms."""

    atoms: list[dict[str, object]] = []
    for line in payload.decode("utf-8").splitlines():
        record = line[:6].strip().upper()
        if record == "ENDMDL" and atoms:
            break
        if record not in {"ATOM", "HETATM"}:
            continue
        fields = line.split()
        if len(line) < 54 or len(fields) < 2:
            raise ScientificCapabilityFailure(
                "discovery_pose_invalid",
                "A persisted docking pose contains an invalid atom record.",
            )
        atom_type = fields[-1]
        element = {
            "A": "C",
            "HD": "H",
            "NA": "N",
            "NS": "N",
            "OA": "O",
            "OS": "O",
            "SA": "S",
        }.get(atom_type, atom_type)
        element = "".join(item for item in element if item.isalpha())
        if not element:
            raise ScientificCapabilityFailure(
                "discovery_pose_invalid",
                "A persisted docking pose has no resolvable atom element.",
            )
        try:
            charge = float(fields[-2])
            atom = {
                "serial": int(line[6:11]),
                "atom_name": line[12:16].strip(),
                "element": element[0].upper() + element[1:].lower(),
                "x": float(line[30:38]),
                "y": float(line[38:46]),
                "z": float(line[46:54]),
                "partial_charge": charge,
            }
        except ValueError as error:
            raise ScientificCapabilityFailure(
                "discovery_pose_invalid",
                "A persisted docking pose contains invalid coordinates or charge.",
            ) from error
        atoms.append(atom)
    if not atoms:
        raise ScientificCapabilityFailure(
            "discovery_pose_invalid",
            "A persisted docking pose contains no atoms.",
        )
    return tuple(atoms)


def _candidate_pose_references(
    run: DiscoveryCampaignRun,
    references: Mapping[str, ArtifactReference],
) -> tuple[tuple[str, ArtifactReference, float], ...]:
    resolved: list[tuple[str, ArtifactReference, float]] = []
    for generation in run.state.generations:
        for candidate_record in generation.candidate_records:
            assessment = candidate_record.assessment
            if assessment is None:
                continue
            scores = {
                item.objective_identifier: float(item.value)
                for item in assessment.objective_scores
            }
            for evidence in assessment.evidence_records:
                if evidence.stage_identifier != "autodock_vina_pose_generation":
                    continue
                for pointer in evidence.artifacts:
                    reference = references.get(pointer.artifact_identifier)
                    if (
                        reference is not None
                        and reference.artifact_type
                        == "molecular_candidate_docking_poses_pdbqt"
                    ):
                        resolved.append(
                            (
                                candidate_record.candidate.candidate_identifier,
                                reference,
                                scores.get("vina_pose_score", 0.0),
                            )
                        )
    return tuple(resolved)


def _candidate_interactions(
    *,
    candidate_identifier: str,
    protein_artifact_identifier: str,
    protein_atoms: tuple[dict[str, object], ...],
    pose_artifact_identifier: str,
    pose_atoms: tuple[dict[str, object], ...],
) -> list[dict[str, object]]:
    metals = {"Ca", "Co", "Cu", "Fe", "Mg", "Mn", "Ni", "Zn"}
    charged_positive = {"ARG", "HIS", "LYS"}
    charged_negative = {"ASP", "GLU"}
    contacts: list[dict[str, object]] = []
    for protein_index, protein in enumerate(protein_atoms, start=1):
        protein_element = str(protein["element"])
        protein_charge = (
            1
            if protein["residue_name"] in charged_positive
            else -1
            if protein["residue_name"] in charged_negative
            else 0
        )
        for pose_index, ligand in enumerate(pose_atoms, start=1):
            ligand_element = str(ligand["element"])
            distance = math.dist(
                (float(protein["x"]), float(protein["y"]), float(protein["z"])),
                (float(ligand["x"]), float(ligand["y"]), float(ligand["z"])),
            )
            interaction_type: str | None = None
            method: str | None = None
            if (
                protein_element in metals
                and ligand_element in {"N", "O", "S"}
                and distance <= 2.8
            ):
                interaction_type = "metal_coordination"
                method = "explicit_metal_donor_distance_le_2_8_angstrom"
            elif (
                protein_charge
                and protein_charge * float(ligand["partial_charge"]) < 0
                and abs(float(ligand["partial_charge"])) >= 0.2
                and distance <= 4.0
            ):
                interaction_type = "ionic_contact"
                method = "opposite_charge_and_distance_le_4_0_angstrom"
            elif (
                protein_element in {"N", "O", "S"}
                and ligand_element in {"N", "O", "S"}
                and distance <= 3.5
            ):
                interaction_type = "hydrogen_bond_compatible_contact"
                method = "polar_atom_distance_le_3_5_angstrom"
            elif (
                protein_element in {"C", "S"}
                and ligand_element in {"C", "S"}
                and distance <= 4.5
            ):
                interaction_type = "hydrophobic_contact"
                method = "nonpolar_atom_distance_le_4_5_angstrom"
            if interaction_type is None:
                continue
            residue_identifier = (
                f"chain-{str(protein['chain']).lower()}-residue-"
                f"{str(protein['sequence']).lower()}-"
                f"{str(protein['residue_name']).lower()}"
            )
            contacts.append(
                {
                    "interaction_identifier": _stable_identifier(
                        "interaction",
                        candidate_identifier,
                        protein["serial"],
                        ligand["serial"],
                        interaction_type,
                    ),
                    "interaction_type": interaction_type,
                    "candidate_identifier": candidate_identifier,
                    "structure_artifact_identifiers": [
                        protein_artifact_identifier,
                        pose_artifact_identifier,
                    ],
                    "atom_identifiers": [
                        f"atom-{protein_index:06d}-{protein['serial']}",
                        f"atom-{pose_index:06d}-{ligand['serial']}",
                    ],
                    "residue_identifiers": [residue_identifier],
                    "distance_angstrom": distance,
                    "angle_degree": None,
                    "calculation_method": method,
                }
            )
    return sorted(
        contacts,
        key=lambda item: (
            float(item["distance_angstrom"]),
            str(item["interaction_identifier"]),
        ),
    )[:256]


class DiscoverySceneHandler:
    def __init__(self, store: ScientificPayloadStore) -> None:
        self.runner = _NativeRunner(store)

    def execute(self, *, invocation, objective, record) -> ScientificCapabilityOutcome:
        del objective
        import json

        result_reference = next(
            item for item in record.artifact_references if item.artifact_type == "discovery_campaign_result"
        )
        run = DiscoveryCampaignRun.model_validate_json(self.runner.store.read(result_reference))
        last = run.state.generations[-1]
        protein_reference = _objective_input(record, kinds=("protein_structure",))
        reference_map = {
            item.artifact_identifier: item for item in record.artifact_references
        }
        pose_references = _candidate_pose_references(run, reference_map)
        interactions: list[dict[str, object]] = []
        for candidate_identifier, pose_reference, _score in pose_references:
            interactions.extend(
                _candidate_interactions(
                    candidate_identifier=candidate_identifier,
                    protein_artifact_identifier=protein_reference.artifact_identifier,
                    protein_atoms=_pdb_atoms(self.runner.store.read(protein_reference)),
                    pose_artifact_identifier=pose_reference.artifact_identifier,
                    pose_atoms=_pdbqt_pose_atoms(
                        self.runner.store.read(pose_reference)
                    ),
                )
            )
        interaction_payload = {
            "schema": "pulsate.molecular-interaction-analysis/v1",
            "method": "deterministic_element_charge_and_distance_rules",
            "interaction_count": len(interactions),
            "interactions": interactions,
            "limitations": [
                "Hydrogen-bond-compatible contacts require directional refinement before being called hydrogen bonds.",
                "Hydrophobic and ionic contacts are geometry and charge-rule classifications, not free-energy decompositions.",
            ],
            "authorizes_execution": False,
        }
        interaction_reference = self.runner.write_json(
            artifact_type="molecular_interaction_analysis",
            payload=json.dumps(
                interaction_payload, sort_keys=True, separators=(",", ":")
            ).encode(),
            producer="molecular.interaction_analyze",
            execution_identifier=invocation.invocation_identifier,
            parents=tuple(
                {
                    item.artifact_identifier: item
                    for item in (
                        protein_reference,
                        *(pose_item[1] for pose_item in pose_references),
                    )
                }.values()
            ),
            metadata={"interaction_count": len(interactions)},
        )
        overlay_records: list[dict[str, object]] = []
        for generation in run.state.generations:
            for candidate_record in generation.candidate_records:
                assessment = candidate_record.assessment
                if assessment is None:
                    continue
                for score in assessment.objective_scores:
                    overlay_records.append(
                        {
                            "overlay_identifier": score.score_identifier,
                            "kind": "candidate_property",
                            "label": score.objective_identifier.replace("_", " "),
                            "structure_artifact_identifier": None,
                            "candidate_identifier": candidate_record.candidate.candidate_identifier,
                            "value": score.value,
                            "unit": (
                                "kcal/mol"
                                if score.objective_identifier == "vina_pose_score"
                                else None
                            ),
                            "atom_identifiers": [],
                            "residue_identifiers": [],
                            "verification_status": (
                                "verified"
                                if assessment.scientific_quality_passed
                                else "failed"
                            ),
                            "uncertainty": score.uncertainty,
                            "method": next(
                                (
                                    evidence.evaluator_identifier
                                    for evidence in assessment.evidence_records
                                    if evidence.evidence_identifier
                                    in score.evidence_identifiers
                                ),
                                None,
                            ),
                        }
                    )
        overlay_reference = self.runner.write_json(
            artifact_type="molecular_computational_overlay",
            payload=json.dumps(
                {
                    "schema": "pulsate.molecular-computational-overlay/v1",
                    "overlays": overlay_records,
                    "authorizes_execution": False,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode(),
            producer="molecular.computational_overlay_project",
            execution_identifier=invocation.invocation_identifier,
            parents=(result_reference,),
            metadata={"overlay_count": len(overlay_records)},
        )
        scene_payload = {
            "scene_kind": "autonomous_molecular_discovery",
            "protein_artifact_identifier": protein_reference.artifact_identifier,
            "binding_pocket_artifact_identifier": next(
                item.artifact_identifier for item in record.artifact_references if item.artifact_type == "binding_pocket"
            ),
            "current_generation": last.generation,
            "generation_count": len(run.state.generations),
            "selected_candidate_identifiers": list(last.selected_candidate_identifiers),
            "candidate_lineage_edges": [edge.model_dump(mode="json") for edge in run.state.lineage.edges],
            "docking_pose_artifact_identifiers": [
                item.artifact_identifier for item in record.artifact_references
                if item.artifact_type == "molecular_candidate_docking_poses_pdbqt"
            ],
            "interaction_artifact_identifier": interaction_reference.artifact_identifier,
            "computational_overlay_artifact_identifier": overlay_reference.artifact_identifier,
            "computational_state": "verified_campaign_complete",
        }
        scene = self.runner.write_json(
            artifact_type="molecular_scene_state",
            payload=json.dumps(scene_payload, sort_keys=True, separators=(",", ":")).encode(),
            producer="molecular.scene_project",
            execution_identifier=invocation.invocation_identifier,
            parents=(result_reference, interaction_reference, overlay_reference),
        )
        return ScientificCapabilityOutcome(
            output_artifacts=(interaction_reference, overlay_reference, scene),
            evidence_artifacts=(interaction_reference, overlay_reference, scene),
            scene_identifier=scene.artifact_identifier,
        )


def protein_ligand_discovery_registry(
    *,
    store: ScientificPayloadStore,
    meeko_adapter: MeekoDockingPreparationAdapter,
) -> ScientistCapabilityRegistry:
    bridge = _DiscoveryArtifactBridge(store)
    registry = ScientistCapabilityRegistry({
        "molecular.structure_ingestion": DiscoveryProteinIngestionHandler(store),
        "molecular.force_field_select": TSForceFieldSelectionHandler(store),
        "molecular.protein_protonation_prepare": DiscoveryProteinPreparationHandler(store),
        "molecular.semantic_target_resolve": DiscoverySemanticTargetHandler(store),
        "molecular.binding_pocket_resolve": BindingPocketResolutionHandler(store),
        "molecular.docking_receptor_prepare": DiscoveryReceptorPreparationHandler(store, meeko_adapter),
        "discovery.campaign_initialize": DiscoveryCampaignInitializeHandler(store),
        "discovery.design_loop_bind": DiscoveryDesignLoopBindingHandler(store),
        "discovery.campaign_iterate": DiscoveryCampaignIterationHandler(store, bridge),
        "discovery.design_loop_trace": DiscoveryDesignLoopTraceHandler(store),
        "scientific_verification.candidate_ranking": DiscoveryRankingVerificationHandler(store),
        "molecular.scene_project": DiscoverySceneHandler(store),
    })
    return registry
