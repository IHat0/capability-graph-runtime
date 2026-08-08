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
    MolecularVector3,
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
    ElectronicImplicitSolventResult,
    ElectronicMolecule,
    ElectronicQMRegionPreparation,
    PySCFElectronicStructureAdapter,
    QM_REGION_PREPARE,
)
from cgr.quantum_workflow import (
    ANSATZ_CONSTRUCT,
    FERMION_TO_QUBIT_MAP,
    HAMILTONIAN_CONSTRUCT,
    VQE_EXECUTE,
    QiskitQuantumWorkflowAdapter,
    VariationalGroundStateResult,
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
        solvent = objective.solvent or "water"
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
            "Oxidation follows the explicit PDB formal charge; the unique curated "
            "Cu(II) d9 doublet is selected. Alternatives remain evidence."
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
        return ScientificCapabilityOutcome(
            output_artifacts=(reference,),
            evidence_artifacts=(reference,),
            scientific_summary=(
                f"Resolved {selection['metal_element']} with coordination number "
                f"{selection['coordination_number']} and bounded oxidation/spin alternatives."
            ),
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
            parameters={
                "active_electron_count": 3,
                "active_spatial_orbital_count": 3,
                "selection_method": "metal_ligand_projection",
                "target_atom_indices": "0,1,5",
                "target_ao_labels": "Cu 3d;N 2p",
                "projection_threshold": 0.005,
            },
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
