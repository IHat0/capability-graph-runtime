"""Pinned Meeko receptor and ligand preparation for native Vina docking."""

from __future__ import annotations

import hashlib
import importlib.metadata
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path

from cgr.kernel.contracts import CapabilityVersion, ExecutionStatus, HealthStatus
from cgr.science import (
    ArtifactLineageEdge,
    ArtifactReference,
    CapabilityDescriptor,
    CapabilityExecutionEnvelope,
    CapabilityInvocation,
    CapabilityResult,
    CreationProvenance,
    DeterminismClassification,
    FailureInformation,
    ScientificEngineAdapterDeclaration,
    ScientificEngineHealthReport,
    ScientificEngineIdentity,
)

from .cheminformatics import MolecularConformerSet
from .rdkit_adapter import MolecularArtifactPayloadStore, _molecule_from_graph

_VERSION = CapabilityVersion(major=1, minor=0, patch=0)
_MEEKO_VERSION = "0.7.1"
DOCKING_RECEPTOR_PREPARE = "molecular.docking_receptor_prepare"
DOCKING_LIGAND_PREPARE = "molecular.docking_ligand_prepare"


class _MeekoPreparationFailure(ValueError):
    def __init__(self, message: str, *, diagnostics: Mapping[str, object]) -> None:
        super().__init__(message)
        self.diagnostics = dict(diagnostics)


def meeko_capability_envelopes() -> tuple[CapabilityExecutionEnvelope, ...]:
    return tuple(
        CapabilityExecutionEnvelope(
            descriptor=CapabilityDescriptor(
                capability_name=name,
                version=_VERSION,
                accepted_artifact_types=accepted,
                produced_artifact_types=produced,
                required_tools=("meeko", "rdkit"),
                required_runtime="python",
                determinism=DeterminismClassification.DETERMINISTIC,
            ),
            supported_objective_types=("protein_ligand_docking", "pose_generation"),
            execution_targets=("local_cpu",),
            known_limitations=(
                "Receptor preparation supports complete standard-residue PDB inputs and fails closed for unresolved templates.",
                "Gasteiger preparation charges are docking charges, not a condensed-phase force-field parameterization.",
            ),
            metadata={"scientific_domain": "molecular_docking_preparation"},
        )
        for name, accepted, produced in (
            (
                DOCKING_RECEPTOR_PREPARE,
                ("molecular_structure",),
                ("docking_receptor_pdbqt",),
            ),
            (
                DOCKING_LIGAND_PREPARE,
                ("molecular_conformer_set",),
                ("docking_ligand_pdbqt",),
            ),
        )
    )


class MeekoDockingPreparationAdapter:
    def __init__(self, payload_store: MolecularArtifactPayloadStore) -> None:
        if not isinstance(payload_store, MolecularArtifactPayloadStore):
            raise ValueError("Meeko requires a molecular artifact payload store.")
        self._store = payload_store
        version = self._version()
        self._declaration = ScientificEngineAdapterDeclaration(
            adapter_identifier="adapter.meeko.docking-preparation",
            adapter_version=_VERSION,
            engine=ScientificEngineIdentity(
                engine_identifier="engine.meeko",
                engine_version=version or "unavailable",
            ),
            capabilities=meeko_capability_envelopes(),
            metadata={"native_objects_public": False},
        )

    @staticmethod
    def _version() -> str | None:
        try:
            return importlib.metadata.version("meeko")
        except importlib.metadata.PackageNotFoundError:
            return None

    @property
    def declaration(self) -> ScientificEngineAdapterDeclaration:
        return self._declaration

    def health(self) -> ScientificEngineHealthReport:
        version = self._version()
        if version is None:
            return ScientificEngineHealthReport(
                status=HealthStatus.UNAVAILABLE,
                reason_code="dependency_missing",
                message="The optional Meeko dependency is unavailable.",
                diagnostics={"expected_version": _MEEKO_VERSION},
            )
        if version != _MEEKO_VERSION:
            return ScientificEngineHealthReport(
                status=HealthStatus.DEGRADED,
                reason_code="dependency_version_mismatch",
                message="The installed Meeko version does not match the pin.",
                diagnostics={"expected_version": _MEEKO_VERSION, "installed_version": version},
            )
        try:
            from meeko import MoleculePreparation, PDBQTWriterLegacy
            import rdkit

            del MoleculePreparation, PDBQTWriterLegacy, rdkit
        except ImportError:
            return ScientificEngineHealthReport(
                status=HealthStatus.UNAVAILABLE,
                reason_code="meeko_runtime_import_failed",
                message="The pinned Meeko/RDKit runtime could not be imported.",
            )
        return ScientificEngineHealthReport(
            status=HealthStatus.HEALTHY,
            diagnostics={"installed_version": version},
        )

    @staticmethod
    def _failure(
        code: str,
        message: str,
        *,
        details: Mapping[str, object] | None = None,
    ) -> CapabilityResult:
        return CapabilityResult(
            status=ExecutionStatus.FAILED,
            failure=FailureInformation(
                code=code,
                message=message,
                details=dict(details or {}),
            ),
        )

    def invoke(self, invocation: CapabilityInvocation) -> CapabilityResult:
        declared = {
            item.descriptor.capability_name: item.descriptor
            for item in self._declaration.capabilities
        }
        expected = declared.get(invocation.capability.capability_name)
        if expected is None or invocation.capability != expected:
            return self._failure("capability_not_declared", "The Meeko capability is not declared.")
        health = self.health()
        if health.status is not HealthStatus.HEALTHY:
            return self._failure(
                health.reason_code or "adapter_unhealthy",
                health.message or "Meeko is unavailable.",
            )
        try:
            if invocation.capability.capability_name == DOCKING_RECEPTOR_PREPARE:
                return self._prepare_receptor(invocation)
            return self._prepare_ligand(invocation)
        except _MeekoPreparationFailure as error:
            return self._failure(
                "docking_preparation_invalid",
                "Docking preparation failed controlled chemical validation.",
                details=error.diagnostics,
            )
        except (ValueError, KeyError, UnicodeDecodeError, subprocess.SubprocessError):
            return self._failure(
                "docking_preparation_invalid",
                "Docking preparation failed controlled chemical validation.",
            )
        except Exception:
            return self._failure(
                "docking_preparation_failed",
                "Docking preparation failed without valid PDBQT evidence.",
            )

    def _read(self, reference: ArtifactReference) -> bytes:
        payload = self._store.read(reference)
        if hashlib.sha256(payload).hexdigest() != reference.content_sha256:
            raise ValueError("Docking preparation input failed content verification.")
        return payload

    def _write(
        self,
        invocation: CapabilityInvocation,
        artifact_type: str,
        payload: bytes,
        parents: tuple[ArtifactReference, ...],
    ) -> ArtifactReference:
        digest = hashlib.sha256(payload).hexdigest()
        reference = ArtifactReference(
            artifact_identifier=f"{artifact_type}-{digest[:32]}",
            schema_version=_VERSION,
            artifact_type=artifact_type,
            media_type="chemical/x-pdbqt",
            content_sha256=digest,
            byte_size=len(payload),
            provenance=CreationProvenance(
                producer=invocation.capability.capability_name,
                producer_version=invocation.capability.version,
                execution_identifier=invocation.context.execution_id,
            ),
            parents=tuple(parent.pointer for parent in parents),
            metadata={
                "preparation_engine": "meeko",
                "preparation_engine_version": _MEEKO_VERSION,
                "charge_model": "gasteiger",
            },
        )
        self._store.write(reference, payload)
        return reference

    def _prepare_receptor(self, invocation: CapabilityInvocation) -> CapabilityResult:
        if len(invocation.input_artifacts) != 1:
            raise ValueError("Receptor preparation requires one structure artifact.")
        source = invocation.input_artifacts[0]
        if source.artifact_type != "molecular_structure":
            raise ValueError("Receptor preparation requires PDB molecular structure bytes.")
        pdb_bytes = self._read(source)
        if len(pdb_bytes) > 64 * 1024 * 1024 or b"ATOM" not in pdb_bytes:
            raise ValueError("Receptor PDB bytes are invalid or too large.")
        executable = shutil.which("mk_prepare_receptor.py") or shutil.which("mk_prepare_receptor")
        if executable is None:
            adjacent = Path(sys.executable).resolve().parent / "mk_prepare_receptor.py"
            if adjacent.is_file():
                executable = str(adjacent)
        if executable is None:
            raise ValueError("The pinned Meeko receptor preparation command is unavailable.")
        parameters = dict(invocation.parameters)
        center = tuple(float(parameters[f"center_{axis}_angstrom"]) for axis in "xyz")
        size = tuple(float(parameters[f"size_{axis}_angstrom"]) for axis in "xyz")
        if any(value <= 0.0 or value > 126.0 for value in size):
            raise ValueError("Docking box dimensions are outside Vina bounds.")
        with tempfile.TemporaryDirectory(prefix="cgr-meeko-receptor-") as directory:
            root = Path(directory)
            input_path = root / "receptor.pdb"
            output_path = root / "receptor.pdbqt"
            input_path.write_bytes(pdb_bytes)
            command = [
                executable,
                "--read_pdb",
                str(input_path),
                "--write_pdbqt",
                str(output_path),
                "--box_center",
                *(f"{value:.8f}" for value in center),
                "--box_size",
                *(f"{value:.8f}" for value in size),
                "--charge_model",
                "gasteiger",
            ]
            completed = subprocess.run(
                command,
                cwd=root,
                check=False,
                capture_output=True,
                timeout=120,
            )
            if completed.returncode != 0 or not output_path.is_file():
                stderr = " ".join(
                    completed.stderr.decode("utf-8", errors="replace").split()
                )[-2048:]
                stdout = " ".join(
                    completed.stdout.decode("utf-8", errors="replace").split()
                )[-2048:]
                raise _MeekoPreparationFailure(
                    "Meeko receptor preparation did not produce PDBQT.",
                    diagnostics={
                        "return_code": completed.returncode,
                        "stderr_tail": stderr or "none",
                        "stdout_tail": stdout or "none",
                    },
                )
            payload = output_path.read_bytes()
        if b"ATOM" not in payload and b"HETATM" not in payload:
            raise ValueError("Prepared receptor PDBQT contains no atoms.")
        artifact = self._write(
            invocation, "docking_receptor_pdbqt", payload, (source,)
        )
        return self._success(invocation, source, artifact, "prepared_receptor_for_docking")

    def _prepare_ligand(self, invocation: CapabilityInvocation) -> CapabilityResult:
        if len(invocation.input_artifacts) != 1:
            raise ValueError("Ligand preparation requires one conformer set.")
        source = invocation.input_artifacts[0]
        if source.artifact_type != "molecular_conformer_set":
            raise ValueError("Ligand preparation requires a conformer set.")
        conformers = MolecularConformerSet.model_validate_json(self._read(source))
        conformer_index = int(dict(invocation.parameters).get("conformer_index", 0))
        try:
            selected = conformers.conformers[conformer_index]
        except IndexError:
            raise ValueError("Requested ligand conformer does not exist.") from None
        molecule = _molecule_from_graph(conformers.source_graph)
        from rdkit import Chem
        from rdkit.Geometry import Point3D
        from meeko import MoleculePreparation, PDBQTWriterLegacy

        native_conformer = Chem.Conformer(molecule.GetNumAtoms())
        coordinates = {item.atom_index: item for item in selected.coordinates}
        if len(coordinates) != molecule.GetNumAtoms():
            raise ValueError("Ligand conformer coordinates are incomplete.")
        for atom_index in range(molecule.GetNumAtoms()):
            coordinate = coordinates[atom_index]
            native_conformer.SetAtomPosition(
                atom_index, Point3D(coordinate.x, coordinate.y, coordinate.z)
            )
        molecule.RemoveAllConformers()
        molecule.AddConformer(native_conformer, assignId=True)
        preparation = MoleculePreparation(
            charge_model="gasteiger",
            add_index_map=True,
        )
        setups = preparation.prepare(molecule, conformer_id=0)
        if len(setups) != 1:
            raise ValueError("Meeko ligand preparation must yield exactly one setup.")
        pdbqt, success, error = PDBQTWriterLegacy.write_string(
            setups[0], add_index_map=True, remove_smiles=False
        )
        if not success or error or "ROOT" not in pdbqt or "TORSDOF" not in pdbqt:
            raise ValueError("Meeko ligand PDBQT is incomplete.")
        artifact = self._write(
            invocation, "docking_ligand_pdbqt", pdbqt.encode("utf-8"), (source,)
        )
        return self._success(invocation, source, artifact, "prepared_ligand_for_docking")

    @staticmethod
    def _success(
        invocation: CapabilityInvocation,
        source: ArtifactReference,
        artifact: ArtifactReference,
        relationship: str,
    ) -> CapabilityResult:
        return CapabilityResult(
            status=ExecutionStatus.SUCCESS,
            output_artifacts=(artifact,),
            lineage=(ArtifactLineageEdge(
                source=source.pointer,
                destination=artifact.pointer,
                relationship_type=relationship,
                producing_capability=invocation.capability.capability_name,
                producing_capability_version=invocation.capability.version,
                execution_identifier=invocation.context.execution_id,
            ),),
            diagnostics={
                "meeko_version": _MEEKO_VERSION,
                "charge_model": "gasteiger",
                "atom_index_mapping_embedded": True,
            },
        )
