"""AutoDock Vina adapter for already prepared receptor and ligand PDBQT artifacts."""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import tempfile
from collections.abc import Mapping
from pathlib import Path

from cgr.kernel.contracts import CapabilityVersion, ExecutionStatus, HealthStatus
from cgr.science import (
    ArtifactLineageEdge, ArtifactReference, CapabilityDescriptor,
    CapabilityExecutionEnvelope, CapabilityInvocation, CapabilityResult,
    CreationProvenance, DeterminismClassification, ExecutionEvidence,
    FailureInformation, ScientificEngineAdapterDeclaration,
    ScientificEngineHealthReport, ScientificEngineIdentity,
)

from .docking import MolecularDockingPocket, MolecularDockingPose, MolecularDockingResult
from .rdkit_adapter import MolecularArtifactPayloadStore

_VERSION = CapabilityVersion(major=1, minor=0, patch=0)
_EXPECTED_VINA_VERSION = "1.2.7"
DOCKING_POSE_GENERATE = "molecular.docking_pose_generate"


def vina_capability_envelopes() -> tuple[CapabilityExecutionEnvelope, ...]:
    descriptor = CapabilityDescriptor(
        capability_name=DOCKING_POSE_GENERATE,
        version=_VERSION,
        accepted_artifact_types=("docking_receptor_pdbqt", "docking_ligand_pdbqt"),
        produced_artifact_types=("molecular_docking_result", "docking_pose_pdbqt"),
        required_tools=("vina", "meeko"),
        required_runtime="python",
        determinism=DeterminismClassification.DETERMINISTIC,
    )
    return (CapabilityExecutionEnvelope(
        descriptor=descriptor,
        supported_objective_types=("protein_ligand_docking", "pose_generation"),
        execution_targets=("local_cpu",),
        known_limitations=(
            "Inputs must be explicit, prepared PDBQT artifacts with hydrogens, charges and atom types.",
            "Vina scores rank poses and are not binding free energies.",
        ),
        metadata={"scientific_domain": "molecular_docking", "adapter_family": "vina"},
    ),)


class VinaDockingAdapter:
    def __init__(self, payload_store: MolecularArtifactPayloadStore) -> None:
        if not isinstance(payload_store, MolecularArtifactPayloadStore):
            raise ValueError("Vina requires a molecular artifact payload store.")
        self._store = payload_store
        version = self._version()
        self._declaration = ScientificEngineAdapterDeclaration(
            adapter_identifier="adapter.vina.docking",
            adapter_version=_VERSION,
            engine=ScientificEngineIdentity(
                engine_identifier="engine.autodock_vina",
                engine_version=version or "unavailable",
            ),
            capabilities=vina_capability_envelopes(),
            metadata={"native_objects_public": False},
        )

    @staticmethod
    def _version() -> str | None:
        try:
            return importlib.metadata.version("vina")
        except importlib.metadata.PackageNotFoundError:
            return None

    @property
    def declaration(self) -> ScientificEngineAdapterDeclaration:
        return self._declaration

    def health(self) -> ScientificEngineHealthReport:
        version = self._version()
        if version is None:
            return ScientificEngineHealthReport(
                status=HealthStatus.UNAVAILABLE, reason_code="dependency_missing",
                message="The optional AutoDock Vina dependency is unavailable.",
                diagnostics={"expected_version": _EXPECTED_VINA_VERSION},
            )
        try:
            importlib.import_module("vina")
        except Exception as exc:
            return ScientificEngineHealthReport(
                status=HealthStatus.UNAVAILABLE,
                reason_code="dependency_import_failed",
                message="The AutoDock Vina Python module cannot be imported.",
                diagnostics={
                    "installed_version": version,
                    "exception_class": type(exc).__name__,
                },
            )
        if version != _EXPECTED_VINA_VERSION:
            return ScientificEngineHealthReport(
                status=HealthStatus.DEGRADED, reason_code="dependency_version_mismatch",
                message="The installed AutoDock Vina version does not match the pin.",
                diagnostics={"expected_version": _EXPECTED_VINA_VERSION, "installed_version": version},
            )
        return ScientificEngineHealthReport(status=HealthStatus.HEALTHY, diagnostics={"installed_version": version})

    @staticmethod
    def _failure(code: str, message: str) -> CapabilityResult:
        return CapabilityResult(status=ExecutionStatus.FAILED, failure=FailureInformation(code=code, message=message))

    def _read(self, reference: ArtifactReference) -> bytes:
        payload = self._store.read(reference)
        if hashlib.sha256(payload).hexdigest() != reference.content_sha256:
            raise ValueError("Docking input artifact failed content verification.")
        return payload

    def _write(self, invocation: CapabilityInvocation, artifact_type: str, media_type: str, payload: bytes, parents: tuple[ArtifactReference, ...]) -> ArtifactReference:
        digest = hashlib.sha256(payload).hexdigest()
        reference = ArtifactReference(
            artifact_identifier=f"{artifact_type}-{digest[:32]}", schema_version=_VERSION,
            artifact_type=artifact_type, media_type=media_type, content_sha256=digest,
            byte_size=len(payload), provenance=CreationProvenance(
                producer=invocation.capability.capability_name,
                producer_version=invocation.capability.version,
                execution_identifier=invocation.context.execution_id,
            ), parents=tuple(parent.pointer for parent in parents),
        )
        self._store.write(reference, payload)
        return reference

    def invoke(self, invocation: CapabilityInvocation) -> CapabilityResult:
        if invocation.capability != self._declaration.capabilities[0].descriptor:
            return self._failure("capability_not_declared", "The Vina capability is not declared.")
        health = self.health()
        if health.status is not HealthStatus.HEALTHY:
            return self._failure(health.reason_code or "adapter_unhealthy", health.message or "Vina is unavailable.")
        try:
            return self._dock(invocation)
        except (ValueError, KeyError, UnicodeDecodeError):
            return self._failure("docking_input_invalid", "Docking inputs failed controlled validation.")
        except Exception:
            return self._failure("docking_execution_failed", "Vina failed without valid pose evidence.")

    def _dock(self, invocation: CapabilityInvocation) -> CapabilityResult:
        by_type = {item.artifact_type: item for item in invocation.input_artifacts}
        receptor = by_type["docking_receptor_pdbqt"]
        ligand = by_type["docking_ligand_pdbqt"]
        if len(by_type) != 2 or len(invocation.input_artifacts) != 2:
            raise ValueError("Docking needs one receptor and one ligand PDBQT.")
        receptor_text = self._read(receptor).decode("utf-8")
        ligand_text = self._read(ligand).decode("utf-8")
        if "ATOM" not in receptor_text and "HETATM" not in receptor_text:
            raise ValueError("Prepared receptor has no atoms.")
        if "ROOT" not in ligand_text or "TORSDOF" not in ligand_text:
            raise ValueError("Prepared ligand is not a complete Vina PDBQT.")
        parameters = dict(invocation.parameters)
        center = tuple(float(parameters[f"center_{axis}_angstrom"]) for axis in "xyz")
        size = tuple(float(parameters[f"size_{axis}_angstrom"]) for axis in "xyz")
        seed = int(parameters.get("random_seed", 7))
        exhaustiveness = int(parameters.get("exhaustiveness", 8))
        requested = int(parameters.get("pose_count", 9))
        energy_range = float(parameters.get("energy_range_kcal_mol", 3.0))
        candidate = str(parameters["candidate_identifier"])
        receptor_method = str(parameters["receptor_preparation_method"])
        ligand_method = str(parameters["ligand_preparation_method"])
        if not (1 <= seed <= 2_147_483_647 and 1 <= exhaustiveness <= 128 and 1 <= requested <= 50):
            raise ValueError("Docking search controls are out of bounds.")
        pocket = MolecularDockingPocket(
            pocket_identifier=f"docking-pocket-{hashlib.sha256(repr((center, size)).encode()).hexdigest()[:24]}",
            definition_method="explicit_center_box", center_angstrom=center, size_angstrom=size,
            selected_residue_identifiers=tuple(parameters.get("selected_residue_identifiers", ())),
        )
        from vina import Vina
        with tempfile.TemporaryDirectory(prefix="cgr-vina-") as directory:
            receptor_path = Path(directory) / "receptor.pdbqt"
            receptor_path.write_text(receptor_text, encoding="utf-8")
            engine = Vina(sf_name="vina", cpu=1, seed=seed, no_refine=False, verbosity=0)
            engine.set_receptor(rigid_pdbqt_filename=str(receptor_path))
            engine.set_ligand_from_string(ligand_text)
            engine.compute_vina_maps(center=list(center), box_size=list(size))
            engine.dock(exhaustiveness=exhaustiveness, n_poses=requested)
            pose_text = str(engine.poses(n_poses=requested, energy_range=energy_range))
            coordinates = engine.poses(n_poses=requested, energy_range=energy_range, coordinates_only=True)
            energies = engine.energies(n_poses=requested, energy_range=energy_range)
        serials = tuple(int(line[6:11]) for line in ligand_text.splitlines() if line.startswith(("ATOM", "HETATM")))
        pose_count = min(len(coordinates), len(energies))
        poses = tuple(MolecularDockingPose(
            pose_identifier=f"docking-pose-{candidate}-{rank + 1}", rank=rank + 1,
            vina_score_kilocalorie_per_mole=float(energies[rank][0]),
            intermolecular_score_kilocalorie_per_mole=float(energies[rank][1]),
            intramolecular_score_kilocalorie_per_mole=float(energies[rank][2]),
            torsional_score_kilocalorie_per_mole=float(energies[rank][3]),
            ligand_atom_serials=serials,
            coordinates_angstrom=tuple(tuple(float(value) for value in xyz) for xyz in coordinates[rank]),
        ) for rank in range(pose_count))
        result = MolecularDockingResult(
            schema_version=_VERSION,
            result_identifier=f"molecular-docking-{hashlib.sha256(pose_text.encode()).hexdigest()[:24]}",
            receptor_artifact_identifier=receptor.artifact_identifier,
            ligand_artifact_identifier=ligand.artifact_identifier,
            candidate_identifier=candidate, engine_version=self._version() or "unknown",
            random_seed=seed, exhaustiveness=exhaustiveness, pocket=pocket, poses=poses,
            receptor_preparation_method=receptor_method,
            ligand_preparation_method=ligand_method,
        )
        parents = (receptor, ligand)
        pose_artifact = self._write(invocation, "docking_pose_pdbqt", "chemical/x-pdbqt", pose_text.encode(), parents)
        result_artifact = self._write(invocation, "molecular_docking_result", "application/vnd.pulsate.molecular-docking+json", result.to_canonical_json().encode(), (*parents, pose_artifact))
        lineage = tuple(ArtifactLineageEdge(
            source=parent.pointer, destination=result_artifact.pointer,
            relationship_type="docked_from", producing_capability=invocation.capability.capability_name,
            producing_capability_version=invocation.capability.version,
            execution_identifier=invocation.context.execution_id,
        ) for parent in parents)
        return CapabilityResult(
            status=ExecutionStatus.SUCCESS, output_artifacts=(result_artifact, pose_artifact), lineage=lineage,
            diagnostics={"pose_count": len(poses), "best_vina_score_kcal_mol": poses[0].vina_score_kilocalorie_per_mole, "binding_free_energy_calculated": False},
            execution_evidence=ExecutionEvidence(runtime_identifier="vina.local_cpu", exit_code=0, evidence_artifacts=(result_artifact.pointer, pose_artifact.pointer), details={"vina_version": self._version() or "unknown"}),
        )
