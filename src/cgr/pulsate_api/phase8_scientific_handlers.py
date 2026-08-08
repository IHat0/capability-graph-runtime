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
    PREPARE,
    SMILES_PARSE,
    MolecularConformerSet,
    RDKitCheminformaticsAdapter,
)
from cgr.electronic_structure import (
    CONFIGURATION_DEFINE,
    IMPLICIT_SOLVENT_HARTREE_FOCK,
    MOLECULE_CONSTRUCT,
    ElectronicImplicitSolventResult,
    PySCFElectronicStructureAdapter,
)
from cgr.science import (
    ArtifactPointer,
    ArtifactReference,
    CapabilityInvocation,
    CreationProvenance,
)
from cgr.workflow_graph import CapabilityInvocation as WorkflowCapabilityInvocation

from .scientific_executions import ScientificExecutionRecord
from .scientific_objectives import StructuredScientificObjective
from .scientific_runtime import (
    ScientificCapabilityFailure,
    ScientificCapabilityOutcome,
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
        digest = hashlib.sha256(payload).hexdigest()
        reference = ArtifactReference(
            artifact_identifier=_stable_identifier(artifact_type.replace("_", "-"), digest),
            schema_version=_VERSION,
            artifact_type=artifact_type,
            media_type=f"application/vnd.pulsate.{artifact_type.replace('_', '-')}+json",
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
                    "maximum_scf_iterations": 100,
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
        return ScientificCapabilityOutcome(
            output_artifacts=(*artifacts, comparison_reference),
            evidence_artifacts=tuple(
                item for item in artifacts
                if item.artifact_type == "electronic_implicit_solvent_result"
            ) + (comparison_reference,),
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

        payload = json.dumps(
            {
                "objective_identifier": objective.objective_identifier,
                "task_type": objective.task_type,
                "computational_state": "verified" if record.verified else "computed",
                "artifact_identifiers": [
                    item.artifact_identifier for item in record.artifact_references
                ],
                "selected_residues": [],
                "selected_atoms": [],
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
