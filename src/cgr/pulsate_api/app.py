"""FastAPI surface for the Pulsate Labs scientific workspace."""

from __future__ import annotations

import math
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict

from cgr.quantum_preflight.contracts import ManifestEnvelope
from cgr.quantum_preflight.manifests import load_manifest
from cgr.quantum_preflight.environment import require_dependencies
from cgr.quantum_preflight.errors import QuantumDependencyError
from .runs import (
    ArtifactUnavailableError,
    ExistingQuantumPreflightExecutor,
    IdempotencyConflictError,
    InvalidIdempotencyKeyError,
    RunCoordinator,
    RunNotFoundError,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
MANIFEST_ROOT = REPO_ROOT / "benchmark-manifests" / "quantum-preflight"

_PRESET_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{0,126}$")

def _manifest_paths() -> tuple[Path, ...]:
    """Discover every declared quantum-preflight preset."""

    return tuple(sorted(MANIFEST_ROOT.glob("*.json")))


def _resolve_manifest(preset_identifier: str) -> Path:
    """Resolve one preset without permitting path traversal."""

    if not _PRESET_IDENTIFIER.fullmatch(preset_identifier):
        raise HTTPException(status_code=404, detail="Experiment preset not found.")

    path = MANIFEST_ROOT / f"{preset_identifier}.json"

    if not path.is_file() or path.resolve().parent != MANIFEST_ROOT.resolve():
        raise HTTPException(status_code=404, detail="Experiment preset not found.")

    return path


def _load_preset(preset_identifier: str) -> ManifestEnvelope:
    return load_manifest(_resolve_manifest(preset_identifier))


def _preset_summary(path: Path) -> dict[str, Any]:
    manifest = load_manifest(path)
    experiment = manifest.experiment
    molecule = experiment.molecular_system

    return {
        "preset_identifier": path.stem,
        "experiment_identifier": experiment.experiment_identifier,
        "elements": [atom.element for atom in molecule.atoms],
        "atom_count": len(molecule.atoms),
        "coordinate_unit": molecule.coordinate_unit,
        "declared_bond_distance": molecule.declared_bond_distance,
        "molecular_charge": molecule.molecular_charge,
        "spin_multiplicity": molecule.spin_multiplicity,
        "basis_set": experiment.electronic_structure.basis_set,
        "experiment_fingerprint": experiment.fingerprint,
    }


def _declared_scene(manifest: ManifestEnvelope) -> dict[str, Any]:
    """Create a viewer payload directly from the declared experiment."""

    experiment = manifest.experiment
    molecule = experiment.molecular_system
    atoms = [
        {
            "atom_identifier": atom.atom_identifier,
            "element": atom.element,
            "coordinates": list(atom.coordinates),
        }
        for atom in molecule.atoms
    ]

    first, second = molecule.atoms
    derived_distance = math.dist(first.coordinates, second.coordinates)

    return {
        "scene_identifier": f"scene.{experiment.experiment_identifier}",
        "scene_stage": "declared",
        "experiment_identifier": experiment.experiment_identifier,
        "experiment_fingerprint": experiment.fingerprint,
        "coordinate_unit": molecule.coordinate_unit,
        "atoms": atoms,
        "bonds": [
            {
                "bond_identifier": "bond.0-1",
                "atom_identifiers": [
                    first.atom_identifier,
                    second.atom_identifier,
                ],
                "declared_distance": molecule.declared_bond_distance,
                "derived_distance": derived_distance,
            }
        ],
        "quantum_region": {
            "selection_identifier": "selection.full-diatomic-system",
            "atom_identifiers": [
                atom.atom_identifier
                for atom in molecule.atoms
            ],
        },
        "scientific_model": {
            "charge": molecule.molecular_charge,
            "spin_multiplicity": molecule.spin_multiplicity,
            "basis_set": experiment.electronic_structure.basis_set,
            "reference_method": experiment.electronic_structure.reference_method,
            "active_electron_count": (
                experiment.electronic_structure.active_electron_count
            ),
            "active_spatial_orbital_count": (
                experiment.electronic_structure.active_spatial_orbital_count
            ),
            "mapper": experiment.quantum_model.mapper,
            "ansatz": experiment.quantum_model.ansatz,
        },
    }


class CreateRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preset_identifier: str
    execution_target: Literal["local_simulator"]


def _typed_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _configured_coordinator() -> RunCoordinator:
    run_root = Path(os.environ.get("PULSATE_RUN_ROOT", str(REPO_ROOT / ".pulsate-runs")))
    enabled = os.environ.get("PULSATE_EXECUTION_ENABLED", "false").strip().lower() in {"1", "true", "yes"}
    lock_path = REPO_ROOT / "requirements" / "quantum-preflight.lock"

    def execution_precondition() -> str | None:
        if not lock_path.is_file():
            return "The pinned dependency lock is unavailable."
        try:
            require_dependencies()
        except QuantumDependencyError:
            return "The required pinned quantum dependencies are unavailable."
        return None

    try:
        workers = int(os.environ.get("PULSATE_MAX_CONCURRENT_RUNS", "1"))
    except ValueError:
        workers = 1
    executor = ExistingQuantumPreflightExecutor(
        repository_root=REPO_ROOT,
        image_identifier=os.environ.get("PULSATE_QUANTUM_IMAGE_IDENTIFIER", "local-uncontainerized"),
    )
    return RunCoordinator(
        run_root=run_root,
        manifest_resolver=_load_preset,
        executor=executor,
        enabled=enabled,
        max_workers=workers,
        max_run_seconds=os.environ.get("PULSATE_MAX_RUN_SECONDS", "180"),
        precondition_check=execution_precondition,
    )


def create_app(*, coordinator: RunCoordinator | None = None) -> FastAPI:
    run_coordinator = coordinator or _configured_coordinator()

    @asynccontextmanager
    async def lifespan(_application: FastAPI):
        run_coordinator.start()
        try:
            yield
        finally:
            run_coordinator.close()

    application = FastAPI(
        title="Pulsate Labs API",
        version="0.2.0",
        description="Coordinates discovered presets through the verified quantum-preflight workflow.",
        lifespan=lifespan,
    )
    application.state.run_coordinator = run_coordinator

    @application.get("/api/v1/health")
    def health() -> dict[str, str]:
        return {
            "service": "pulsate-api", "status": "healthy", "version": "0.2.0",
        }

    @application.get("/api/v1/runs/capability")
    def run_capability() -> dict[str, Any]:
        return run_coordinator.capability()

    @application.get("/api/v1/experiments/presets")
    def list_presets() -> dict[str, Any]:
        presets = [_preset_summary(path) for path in _manifest_paths()]
        return {"presets": presets, "count": len(presets)}

    @application.get("/api/v1/experiments/presets/{preset_identifier}")
    def read_preset(preset_identifier: str) -> dict[str, Any]:
        manifest = _load_preset(preset_identifier)
        return {"preset_identifier": preset_identifier, "manifest": manifest.model_dump(mode="json")}

    @application.get("/api/v1/experiments/presets/{preset_identifier}/scene")
    def read_preset_scene(preset_identifier: str) -> dict[str, Any]:
        return _declared_scene(_load_preset(preset_identifier))

    @application.post("/api/v1/runs", status_code=202)
    def create_run(
        request: CreateRunRequest,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        try:
            state, _created = run_coordinator.create(
                request.preset_identifier, request.execution_target, idempotency_key
            )
            return state
        except HTTPException as exc:
            if exc.status_code == 404:
                raise _typed_error(404, "preset_not_found", "Experiment preset not found.") from None
            raise
        except RunNotFoundError:
            raise _typed_error(404, "preset_not_found", "Experiment preset not found.") from None
        except InvalidIdempotencyKeyError as exc:
            raise _typed_error(400, "invalid_idempotency_key", str(exc)) from None
        except IdempotencyConflictError as exc:
            raise _typed_error(409, "idempotency_conflict", str(exc)) from None
        except RuntimeError as exc:
            if str(exc) == "execution_unavailable":
                raise _typed_error(503, "execution_unavailable", "Local quantum execution is not enabled on this backend.") from None
            raise

    @application.get("/api/v1/runs/{run_identifier}")
    def read_run(run_identifier: str) -> dict[str, Any]:
        try:
            return run_coordinator.get(run_identifier)
        except (RunNotFoundError, FileNotFoundError):
            raise _typed_error(404, "run_not_found", "Run not found.") from None

    def read_artifact(run_identifier: str, name: Literal["results", "verification", "receipt"]) -> dict[str, Any]:
        try:
            return run_coordinator.artifact(run_identifier, name)
        except (RunNotFoundError, FileNotFoundError):
            raise _typed_error(404, "run_not_found", "Run not found.") from None
        except ArtifactUnavailableError as exc:
            raise _typed_error(409, f"{name}_unavailable", str(exc)) from None

    @application.get("/api/v1/runs/{run_identifier}/results")
    def read_results(run_identifier: str) -> dict[str, Any]:
        return read_artifact(run_identifier, "results")

    @application.get("/api/v1/runs/{run_identifier}/verification")
    def read_verification(run_identifier: str) -> dict[str, Any]:
        return read_artifact(run_identifier, "verification")

    @application.get("/api/v1/runs/{run_identifier}/receipt")
    def read_receipt(run_identifier: str) -> dict[str, Any]:
        return read_artifact(run_identifier, "receipt")

    return application


app = create_app()
