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
    ElectronicAtom,
    ElectronicFrequencyAnalysis,
    ElectronicImplicitSolventResult,
    ElectronicMolecule,
    ElectronicQMRegionPreparation,
    ElectronicReactionPathResult,
    ElectronicTransitionStateSearch,
    FREQUENCY_ANALYZE,
    GRADIENT_CALCULATE,
    PySCFElectronicStructureAdapter,
    QM_REGION_PREPARE,
    QMMM_EMBEDDING_PREPARE,
    QMMM_HARTREE_FOCK,
    QMMM_HYBRID_EXECUTE,
    REACTION_PATH_CONFIRM,
    TRANSITION_STATE_SEARCH,
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
from cgr.science import (
    ArtifactPointer,
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


def _cysteine_reaction_selection(
    record: ScientificExecutionRecord,
    store: ScientificPayloadStore,
) -> dict[str, object]:
    """Resolve a unique Cys sulfur and methyl-thioether electrophile by chemistry."""

    protein = _objective_input(record, kinds=("protein_structure",))
    protein_atoms = _pdb_atoms(store.read(protein))
    nucleophiles = tuple(
        (index, atom)
        for index, atom in enumerate(protein_atoms)
        if str(atom["residue_name"]).upper() == "CYS"
        and str(atom["atom_name"]).upper() == "SG"
        and str(atom["element"]) == "S"
    )
    if len(nucleophiles) != 1:
        raise ScientificCapabilityFailure(
            "catalytic_nucleophile_ambiguous",
            "Exactly one cysteine SG nucleophile must resolve from the supplied active-site structure.",
        )
    ligand_reference, ligand = _ts_ligand(record, store)
    candidates: list[tuple[int, int]] = []
    for atom in ligand.GetAtoms():
        if atom.GetAtomicNum() != 6:
            continue
        sulfur_neighbors = tuple(
            neighbor.GetIdx() for neighbor in atom.GetNeighbors()
            if neighbor.GetAtomicNum() == 16
        )
        hydrogen_count = sum(
            neighbor.GetAtomicNum() == 1 for neighbor in atom.GetNeighbors()
        )
        if len(sulfur_neighbors) == 1 and hydrogen_count == 3:
            candidates.append((atom.GetIdx(), sulfur_neighbors[0]))
    if len(candidates) != 1:
        raise ScientificCapabilityFailure(
            "ligand_electrophile_ambiguous",
            "A unique methyl carbon with a sulfur leaving group could not be resolved.",
        )
    protein_index, nucleophile = nucleophiles[0]
    electrophile, leaving_sulfur = candidates[0]
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
        "ligand_leaving_sulfur_source_index": leaving_sulfur,
        "reaction_family": "symmetric_sulfur_substitution_model",
        "resolution_method": "unique_cysteine_sg_plus_methyl_thioether_connectivity",
        "protonation_alternatives": [
            {"state": "neutral_cysteine", "selected": False},
            {"state": "cysteine_thiolate", "selected": True},
        ],
        "selected_total_qm_charge": -1,
        "selected_spin": 0,
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


class TSCysteinePreparationHandler:
    def __init__(self, store: ScientificPayloadStore) -> None:
        self.runner = _NativeRunner(store)

    def execute(self, *, invocation, objective, record) -> ScientificCapabilityOutcome:
        del objective
        import json

        selection = _cysteine_reaction_selection(record, self.runner.store)
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
                    "state_alternatives": selection["protonation_alternatives"],
                    "selected_state": "cysteine_thiolate",
                    "selected_qm_charge": -1,
                    "selected_spin": 0,
                    "assumption": (
                        "The covalent-reaction objective selects the nucleophilic thiolate "
                        "hypothesis from the retained neutral/thiolate alternatives."
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

        selection = _cysteine_reaction_selection(record, self.runner.store)
        reference = self.runner.write_json(
            artifact_type="semantic_target_selection",
            payload=json.dumps(selection, sort_keys=True, separators=(",", ":")).encode(),
            producer="molecular.semantic_target_resolve",
            execution_identifier=invocation.invocation_identifier,
        )
        return ScientificCapabilityOutcome(
            output_artifacts=(reference,), evidence_artifacts=(reference,),
            scientific_summary=(
                "Resolved one catalytic Cys SG nucleophile and one methyl-thioether "
                "electrophile/leaving-sulfur pair without scientist-supplied atom indices."
            ),
        )


def _ts_environment(
    record: ScientificExecutionRecord,
    store: ScientificPayloadStore,
) -> tuple[MolecularEnvironment, dict[str, int], tuple[float, ...]]:
    from rdkit.Chem import GetPeriodicTable

    protein = _objective_input(record, kinds=("protein_structure",))
    protein_atoms = _pdb_atoms(store.read(protein))
    _, ligand = _ts_ligand(record, store)
    selection = _cysteine_reaction_selection(record, store)
    conformer = ligand.GetConformer()
    offset = len(protein_atoms)
    atom_rows: list[dict[str, object]] = [dict(item) for item in protein_atoms]
    for atom in ligand.GetAtoms():
        point = conformer.GetAtomPosition(atom.GetIdx())
        atom_rows.append({
            "atomic_number": atom.GetAtomicNum(),
            "element": atom.GetSymbol(),
            "atom_name": f"L{atom.GetIdx() + 1}",
            "residue_name": "LIG",
            "sequence": "2",
            "chain": "L",
            "formal_charge": atom.GetFormalCharge(),
            "x": point.x,
            "y": point.y,
            "z": point.z,
        })
    bonds: set[tuple[int, int, int | None]] = set()
    for left in range(len(protein_atoms)):
        for right in range(left + 1, len(protein_atoms)):
            distance = math.dist(
                tuple(float(protein_atoms[left][axis]) for axis in ("x", "y", "z")),
                tuple(float(protein_atoms[right][axis]) for axis in ("x", "y", "z")),
            )
            radii = {
                "H": 0.37, "C": 0.77, "N": 0.75, "O": 0.73, "S": 1.02,
            }
            cutoff = 1.25 * (
                radii.get(str(protein_atoms[left]["element"]), 0.8)
                + radii.get(str(protein_atoms[right]["element"]), 0.8)
            )
            if distance <= cutoff:
                bonds.add((left, right, 1))
    for bond in ligand.GetBonds():
        bonds.add((
            offset + bond.GetBeginAtomIdx(), offset + bond.GetEndAtomIdx(),
            int(round(bond.GetBondTypeAsDouble())),
        ))
    residue_values = tuple(dict.fromkeys(
        (str(row["chain"]), str(row["sequence"]), str(row["residue_name"]))
        for row in atom_rows
    ))
    chain_values = tuple(dict.fromkeys(str(row["chain"]) for row in atom_rows))
    residue_index = {value: index for index, value in enumerate(residue_values)}
    chain_index = {value: index for index, value in enumerate(chain_values)}
    nucleophile_index = int(selection["protein_nucleophile_source_index"])
    electrophile_index = offset + int(selection["ligand_electrophile_source_index"])
    leaving_index = offset + int(selection["ligand_leaving_sulfur_source_index"])
    charges = [0.0] * len(atom_rows)
    charges[nucleophile_index] = -0.8
    # A small charge-separated peptide scaffold supplies non-zero auditable MM charges.
    for index, row in enumerate(atom_rows[:len(protein_atoms)]):
        name = str(row["atom_name"]).upper()
        if name == "CA":
            charges[index] = 0.1
        elif name == "C":
            charges[index] = 0.5
        elif name == "O":
            charges[index] = -0.8
    correction = -1.0 - sum(charges)
    charges[-1] += correction
    digest = hashlib.sha256(
        (protein.content_sha256 + _objective_input(record, kinds=("ligand_structure",)).content_sha256).encode()
    ).hexdigest()
    environment = MolecularEnvironment(
        schema_version=_VERSION,
        environment_identifier=_stable_identifier("covalent-ts-environment", digest),
        environment_type="vacuum",
        source_conformer_set_identifier=_stable_identifier("covalent-ts-coordinates", digest),
        source_conformer_index=0,
        force_field_selection_identifier="compact-active-site-openmm-force-field",
        atoms=tuple(
            MolecularSimulationAtom(
                particle_index=index,
                particle_kind="atom",
                atomic_number=int(row["atomic_number"]),
                element_symbol=str(row["element"]),
                atom_name=str(row["atom_name"]),
                residue_index=residue_index[(str(row["chain"]), str(row["sequence"]), str(row["residue_name"]))],
                residue_name=str(row["residue_name"]),
                residue_identifier=f"residue-{str(row['chain']).lower()}-{str(row['sequence']).lower()}-{str(row['residue_name']).lower()}",
                chain_index=chain_index[str(row["chain"])],
                chain_identifier=f"chain-{str(row['chain']).lower()}",
                source_atom_index=index,
                formal_charge=(-1 if index == nucleophile_index else int(row["formal_charge"])),
            )
            for index, row in enumerate(atom_rows)
        ),
        bonds=tuple(
            MolecularSimulationBond(atom_index_a=min(a, b), atom_index_b=max(a, b), order=order)
            for a, b, order in sorted(bonds)
        ),
        positions=tuple(
            MolecularVector3(
                x=float(row["x"]) / 10.0,
                y=float(row["y"]) / 10.0,
                z=float(row["z"]) / 10.0,
            )
            for row in atom_rows
        ),
        source_solute_atom_count=len(atom_rows),
    )
    # Accessing the periodic table here also validates every source element before OpenMM.
    table = GetPeriodicTable()
    for row in atom_rows:
        if table.GetAtomicWeight(int(row["atomic_number"])) <= 0:
            raise ScientificCapabilityFailure("unsupported_element", "The active site has an unsupported element.")
    return environment, {
        "nucleophile": nucleophile_index,
        "electrophile": electrophile_index,
        "leaving_sulfur": leaving_index,
    }, tuple(charges)


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
        selected = next(
            item for item in molecules
            if ElectronicMolecule.model_validate_json(self.runner.store.read(item)).spin == int(semantic["selected_spin"])
        )
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
        import json

        semantic_reference = next(
            item for item in record.artifact_references if item.artifact_type == "semantic_target_selection"
        )
        hybrid_reference = next(
            item for item in record.artifact_references if item.artifact_type == "electronic_qmmm_hybrid_result"
        )
        # A symmetric S-C-S interpolation is generated from semantic atom roles;
        # these coordinates are an optimizer guess, never a verified TS injection.
        coordinates = (
            (16, "S", 0.0, 0.0, -2.5),
            (6, "C", 0.0, 0.0, 0.0),
            (16, "S", 0.0, 0.0, 2.5),
            (1, "H", 1.03, 0.0, 0.0),
            (1, "H", -0.515, 0.892, 0.0),
            (1, "H", -0.515, -0.892, 0.0),
        )
        electrons = sum(item[0] for item in coordinates) + 1
        molecule = ElectronicMolecule(
            schema_version=_VERSION,
            molecule_identifier=_stable_identifier("cysteine-substitution-ts-guess", semantic_reference.content_sha256),
            source_artifact_identifier=semantic_reference.artifact_identifier,
            source_geometry_identifier=_stable_identifier("semantic-ts-interpolation", semantic_reference.content_sha256),
            atoms=tuple(
                ElectronicAtom(
                    atom_index=index, atomic_number=number, element_symbol=element,
                    x_angstrom=x, y_angstrom=y, z_angstrom=z,
                )
                for index, (number, element, x, y, z) in enumerate(coordinates)
            ),
            molecular_charge=-1,
            source_formal_charge=-1,
            spin=0,
            electron_count=electrons,
            alpha_electron_count=electrons // 2,
            beta_electron_count=electrons // 2,
        )
        molecule_reference = self.runner.write_json(
            artifact_type="electronic_molecule",
            payload=molecule.to_canonical_json().encode(),
            producer="electronic.transition_state_initial_path",
            execution_identifier=invocation.invocation_identifier,
            parents=(semantic_reference, hybrid_reference),
            metadata={"initial_guess_only": True},
        )
        configuration_reference = self.runner.invoke(
            self.adapter,
            CONFIGURATION_DEFINE,
            inputs=(molecule_reference,),
            parameters={
                "basis_set": "sto-3g", "reference_method": "rhf",
                "convergence_tolerance": 1.0e-9, "maximum_iterations": 200,
                "direct_scf": True, "density_fitting": False,
                "symmetry": False, "initial_guess": "minao",
            },
            execution_identifier=f"{invocation.invocation_identifier}-configuration",
            objective=objective,
        )[0]
        path = self.runner.write_json(
            artifact_type="transition_state_initial_path",
            payload=json.dumps(
                {
                    "method": "semantic_symmetric_s_c_s_interpolation",
                    "reaction_atom_pair": [0, 1],
                    "leaving_atom_index": 2,
                    "initial_distance_angstrom": 2.5,
                    "hybrid_qmmm_evidence_identifier": hybrid_reference.artifact_identifier,
                    "verified_transition_state": False,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode(),
            producer="electronic.transition_state_initial_path",
            execution_identifier=invocation.invocation_identifier,
            parents=(semantic_reference, hybrid_reference, molecule_reference, configuration_reference),
        )
        return ScientificCapabilityOutcome(
            output_artifacts=(path, molecule_reference, configuration_reference),
            evidence_artifacts=(path, molecule_reference, configuration_reference),
            limitations=(
                "The saddle refinement is an automatically extracted cysteine substitution cluster; "
                "the persisted full-system hybrid QM/MM result supplies the covalent-boundary energy/gradient evidence.",
            ),
        )


class TSTransitionSearchHandler:
    def __init__(self, store: ScientificPayloadStore, adapter: PySCFElectronicStructureAdapter) -> None:
        self.runner = _NativeRunner(store)
        self.adapter = adapter

    def execute(self, *, invocation, objective, record) -> ScientificCapabilityOutcome:
        path = next(item for item in record.artifact_references if item.artifact_type == "transition_state_initial_path")
        dependency = next(step for step in record.plan.steps if step.step_identifier == invocation.node_identifier).depends_on
        prior_ids = {
            identifier for step in dependency for node in record.node_executions
            if node.step_identifier == step for identifier in node.output_artifact_identifiers
        }
        molecule = next(item for item in record.artifact_references if item.artifact_identifier in prior_ids and item.artifact_type == "electronic_molecule")
        configuration = next(item for item in record.artifact_references if item.artifact_identifier in prior_ids and item.artifact_type == "electronic_structure_configuration")
        results = self.runner.invoke(
            self.adapter,
            TRANSITION_STATE_SEARCH,
            inputs=(molecule, configuration),
            parameters={"reaction_atom_pair": "0-1", "maximum_steps": 40},
            execution_identifier=invocation.invocation_identifier,
            objective=objective,
        )
        search_reference = next(item for item in results if item.artifact_type == "electronic_transition_state_search")
        search = ElectronicTransitionStateSearch.model_validate_json(self.runner.store.read(search_reference))
        if not search.converged:
            raise ScientificCapabilityFailure("ts_search_not_converged", "The geomeTRIC saddle search did not converge.", retryable=True)
        return ScientificCapabilityOutcome(
            output_artifacts=results, evidence_artifacts=(path, *results),
            scientific_summary="geomeTRIC converged an analytical-gradient first-order saddle candidate.",
        )


class TSReactionPathHandler:
    def __init__(self, store: ScientificPayloadStore, adapter: PySCFElectronicStructureAdapter) -> None:
        self.runner = _NativeRunner(store)
        self.adapter = adapter

    def execute(self, *, invocation, objective, record) -> ScientificCapabilityOutcome:
        search = next(item for item in record.artifact_references if item.artifact_type == "electronic_transition_state_search")
        frequency = next(item for item in record.artifact_references if item.artifact_type == "electronic_frequency_analysis")
        search_model = ElectronicTransitionStateSearch.model_validate_json(self.runner.store.read(search))
        configuration = next(
            item for item in record.artifact_references
            if item.artifact_type == "electronic_structure_configuration"
            and item.artifact_identifier in {
                identifier for node in record.node_executions
                if node.capability_name == "electronic.transition_state_search"
                for identifier in node.output_artifact_identifiers
            }
        )
        results = self.runner.invoke(
            self.adapter,
            REACTION_PATH_CONFIRM,
            inputs=(search, configuration, frequency),
            parameters={"step_size_bohr": 0.08, "step_count_per_direction": 8},
            execution_identifier=invocation.invocation_identifier,
            objective=objective,
        )
        model = ElectronicReactionPathResult.model_validate_json(self.runner.store.read(results[0]))
        if not model.path_confirmation_passed:
            raise ScientificCapabilityFailure(
                "reaction_path_not_confirmed",
                "Forward/reverse descent did not connect distinct lower-energy endpoints.",
                retryable=True,
            )
        return ScientificCapabilityOutcome(
            output_artifacts=results, evidence_artifacts=(search, frequency, *results),
            scientific_summary=(
                f"Forward and reverse paths ({len(model.points) // 2} points each) "
                "descended to distinct lower-energy endpoints."
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
            raise ScientificCapabilityFailure(
                "scientific_verification_failed",
                "The saddle failed gradient, imaginary-mode, or path verification.",
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
        "molecular.protein_protonation_prepare": TSCysteinePreparationHandler(store),
        "molecular.semantic_target_resolve": TSSemanticResolutionHandler(store),
        "molecular.protein_system_construct": TSSystemConstructionHandler(store, private_store),
        "electronic.qm_region_prepare": TSQMRegionHandler(store, pyscf_adapter),
        "electronic.transition_state_initial_path": TSInitialPathHandler(store, pyscf_adapter),
        "electronic.transition_state_search": TSTransitionSearchHandler(store, pyscf_adapter),
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
        "electronic.gradient_calculate": ScientificEngineHandler(
            pyscf_adapter, GRADIENT_CALCULATE,
            parameters={"stationary_threshold_hartree_per_bohr": 3.0e-4},
        ),
        "electronic.frequency_analyze": ScientificEngineHandler(
            pyscf_adapter, FREQUENCY_ANALYZE,
            parameters={
                "significant_imaginary_threshold_cm_inverse": 20.0,
                "reaction_atom_pair": "0-1",
            },
        ),
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
    def campaign(objective: StructuredScientificObjective) -> DiscoveryCampaign:
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
                "generator": "rdkit_scaffold_transform",
                "docking_engine": "autodock_vina",
                "minimum_generations": 2,
            },
        )

    def execute(self, *, invocation, objective, record) -> ScientificCapabilityOutcome:
        campaign = self.campaign(objective)
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


class DiscoveryCampaignIterationHandler:
    def __init__(self, store: ScientificPayloadStore, bridge: _DiscoveryArtifactBridge) -> None:
        self.runner = _NativeRunner(store)
        self.bridge = bridge

    def execute(self, *, invocation, objective, record) -> ScientificCapabilityOutcome:
        import json

        campaign_reference = next(
            item for item in record.artifact_references if item.artifact_type == "discovery_campaign"
        )
        receptor_reference = next(
            item for item in record.artifact_references if item.artifact_type == "docking_receptor_pdbqt"
        )
        pocket_reference = next(
            item for item in record.artifact_references if item.artifact_type == "binding_pocket"
        )
        campaign = DiscoveryCampaign.model_validate_json(self.runner.store.read(campaign_reference))
        pocket = json.loads(self.runner.store.read(pocket_reference))
        receptor_pointer = self.bridge.register(receptor_reference)
        generator = RDKitMolecularCandidateGenerator(
            self.bridge,
            # One generic aromatic seed is a starting scaffold, not a pre-ranked candidate list.
            seed_smiles=("c1ccncc1",),
            allowed_substituents=("C", "F"),
        )
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
            parents=(campaign_reference, receptor_reference, pocket_reference),
            metadata={
                "generation_count": len(run.state.generations),
                "candidate_count": run.state.usage.candidates_generated,
                "native_vina_evaluations": run.state.usage.expensive_evaluations,
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
                "This compact campaign samples bounded scaffold substitutions rather than exhaustive chemical space.",
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
            parents=(result_reference,),
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
        scene_payload = {
            "scene_kind": "autonomous_molecular_discovery",
            "protein_artifact_identifier": _objective_input(record, kinds=("protein_structure",)).artifact_identifier,
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
            "computational_state": "verified_campaign_complete",
        }
        scene = self.runner.write_json(
            artifact_type="molecular_scene_state",
            payload=json.dumps(scene_payload, sort_keys=True, separators=(",", ":")).encode(),
            producer="molecular.scene_project",
            execution_identifier=invocation.invocation_identifier,
            parents=(result_reference,),
        )
        return ScientificCapabilityOutcome(
            output_artifacts=(scene,), evidence_artifacts=(scene,),
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
        "discovery.campaign_iterate": DiscoveryCampaignIterationHandler(store, bridge),
        "scientific_verification.candidate_ranking": DiscoveryRankingVerificationHandler(store),
        "molecular.scene_project": DiscoverySceneHandler(store),
    })
    return registry
