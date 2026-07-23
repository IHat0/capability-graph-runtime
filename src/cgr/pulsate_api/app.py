"""FastAPI surface for the Pulsate Labs scientific workspace."""

from __future__ import annotations

import math
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from cgr.quantum_preflight.contracts import ManifestEnvelope
from cgr.quantum_preflight.environment import require_dependencies
from cgr.quantum_preflight.errors import QuantumDependencyError
from cgr.quantum_preflight.manifests import load_manifest

from .experiments import (
    ExperimentNotFoundError,
    ExperimentStore,
    PlannerInputError,
)
from .ibm import (
    IBMQuantumConfiguration,
    IBMQuantumRunExecutor,
    RunBoundIsolatedIBMPreflightExecutor,
    SubprocessIBMRuntimeAdapter,
    UnavailableIBMPreflightExecutor,
)
from .natural_language import (
    ApprovalRequest,
    ApprovalValidationError,
    InterpretationNotFoundError,
    NaturalLanguageInterpretationError,
    NaturalLanguageInterpretationStore,
    NaturalLanguageUnavailableError,
)
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

    preset_identifier: str | None = None
    experiment_identifier: str | None = None
    execution_target: Literal["local_simulator", "ibm_quantum"]

    @model_validator(mode="after")
    def validate_source(self) -> CreateRunRequest:
        if (self.preset_identifier is None) == (self.experiment_identifier is None):
            raise ValueError("Exactly one experiment_identifier or preset_identifier is required.")
        return self


class PlanExperimentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=4096)


class InterpretQuestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=4096)


def _typed_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _configured_coordinator(experiment_store: ExperimentStore) -> RunCoordinator:
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
    ibm_configuration = IBMQuantumConfiguration.from_environment()
    handoff_root = os.environ.get("PULSATE_IBM_PREFLIGHT_HANDOFF_ROOT")
    scientific_image_identifier = os.environ.get(
        "PULSATE_IBM_SCIENTIFIC_IMAGE_IDENTIFIER"
    )
    ibm_preflight_executor = (
        RunBoundIsolatedIBMPreflightExecutor(
            Path(handoff_root),
            scientific_preflight_image_identifier=scientific_image_identifier,
            ibm_runtime_image_identifier=ibm_configuration.image_identifier,
        )
        if handoff_root and scientific_image_identifier
        else UnavailableIBMPreflightExecutor()
    )
    ibm_executor = IBMQuantumRunExecutor(
        local_executor=ibm_preflight_executor,
        adapter=SubprocessIBMRuntimeAdapter(
            repository_root=REPO_ROOT,
            configuration=ibm_configuration,
        ),
        configuration=ibm_configuration,
    )
    return RunCoordinator(
        run_root=run_root,
        manifest_resolver=_load_preset,
        experiment_resolver=experiment_store.resolve_for_targeted_run,
        executor=executor,
        ibm_executor=ibm_executor,
        enabled=enabled,
        max_workers=workers,
        max_run_seconds=os.environ.get("PULSATE_MAX_RUN_SECONDS", "180"),
        precondition_check=execution_precondition,
    )


def create_app(
    *,
    coordinator: RunCoordinator | None = None,
    experiment_store: ExperimentStore | None = None,
    natural_language_store: NaturalLanguageInterpretationStore | None = None,
) -> FastAPI:
    if experiment_store is None:
        if coordinator is not None:
            experiment_root = coordinator.configured_run_root.parent / "experiments"
        else:
            experiment_root = Path(
                os.environ.get(
                    "PULSATE_EXPERIMENT_ROOT", str(REPO_ROOT / ".pulsate-experiments")
                )
            )
        experiment_store = ExperimentStore(experiment_root)
    run_coordinator = coordinator or _configured_coordinator(experiment_store)
    if run_coordinator.experiment_resolver is None:
        run_coordinator.experiment_resolver = experiment_store.resolve_for_targeted_run
    experiment_store.ibm_capability = lambda: run_coordinator.capability()["ibm_quantum"]
    if natural_language_store is None:
        interpretation_root = Path(
            os.environ.get(
                "PULSATE_INTERPRETATION_ROOT",
                str(REPO_ROOT / ".pulsate-interpretations"),
            )
        )
        natural_language_store = NaturalLanguageInterpretationStore.from_environment(
            interpretation_root
        )

    @asynccontextmanager
    async def lifespan(_application: FastAPI):
        experiment_store.start()
        natural_language_store.start()
        try:
            run_coordinator.start()
            try:
                yield
            finally:
                run_coordinator.close()
        finally:
            natural_language_store.close()
            experiment_store.close()

    application = FastAPI(
        title="Pulsate Labs API",
        version="0.2.0",
        description=(
            "Interprets reviewable scientific questions and coordinates approved "
            "or discovered experiments through controlled workflows."
        ),
        lifespan=lifespan,
    )
    application.state.run_coordinator = run_coordinator
    application.state.experiment_store = experiment_store
    application.state.natural_language_store = natural_language_store

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

    @application.post("/api/v1/experiments/plan", status_code=201)
    def plan_experiment(request: PlanExperimentRequest) -> dict[str, Any]:
        try:
            return experiment_store.plan(request.question)
        except PlannerInputError as exc:
            raise _typed_error(422, "invalid_experiment_question", str(exc)) from None

    @application.get("/api/v1/experiments/interpreter/capability")
    def interpretation_capability() -> dict[str, Any]:
        return natural_language_store.capability()

    @application.post("/api/v1/experiments/interpret", status_code=201)
    def interpret_question(request: InterpretQuestionRequest) -> dict[str, Any]:
        try:
            return natural_language_store.interpret(request.question).model_dump(
                mode="json"
            )
        except NaturalLanguageUnavailableError as exc:
            raise _typed_error(
                503, "natural_language_interpreter_unavailable", str(exc)
            ) from None
        except NaturalLanguageInterpretationError as exc:
            raise _typed_error(
                502, "natural_language_interpretation_failed", str(exc)
            ) from None
        except ApprovalValidationError as exc:
            raise _typed_error(
                422, "invalid_experiment_question", str(exc)
            ) from None

    @application.post(
        "/api/v1/experiments/{interpretation_identifier}/approve",
        status_code=201,
    )
    def approve_interpretation(
        interpretation_identifier: str, request: ApprovalRequest
    ) -> dict[str, Any]:
        try:
            return natural_language_store.approve(
                interpretation_identifier, request
            ).model_dump(mode="json")
        except InterpretationNotFoundError:
            raise _typed_error(
                404, "interpretation_not_found", "Interpretation not found."
            ) from None
        except ApprovalValidationError as exc:
            raise _typed_error(
                422, "interpretation_approval_rejected", str(exc)
            ) from None

    @application.get("/api/v1/experiments/{experiment_identifier}")
    def read_experiment(experiment_identifier: str) -> dict[str, Any]:
        try:
            return experiment_store.get(experiment_identifier)
        except ExperimentNotFoundError:
            raise _typed_error(404, "experiment_not_found", "Experiment not found.") from None

    @application.post("/api/v1/runs", status_code=202)
    def create_run(
        request: CreateRunRequest,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        try:
            state, _created = run_coordinator.create(
                request.preset_identifier,
                request.execution_target,
                idempotency_key,
                experiment_identifier=request.experiment_identifier,
            )
            return state
        except HTTPException as exc:
            if exc.status_code == 404:
                raise _typed_error(404, "preset_not_found", "Experiment preset not found.") from None
            raise
        except RunNotFoundError:
            code = "experiment_not_found" if request.experiment_identifier else "preset_not_found"
            message = "Experiment not found." if request.experiment_identifier else "Experiment preset not found."
            raise _typed_error(404, code, message) from None
        except ExperimentNotFoundError:
            raise _typed_error(404, "experiment_not_found", "Experiment not found.") from None
        except InvalidIdempotencyKeyError as exc:
            raise _typed_error(400, "invalid_idempotency_key", str(exc)) from None
        except IdempotencyConflictError as exc:
            raise _typed_error(409, "idempotency_conflict", str(exc)) from None
        except ValueError as exc:
            if str(exc) in {"unsupported_execution_target", "execution_target_mismatch"}:
                raise _typed_error(422, str(exc), "The execution target does not match the experiment specification.") from None
            raise
        except RuntimeError as exc:
            if str(exc) == "execution_unavailable":
                raise _typed_error(503, "execution_unavailable", "Local quantum execution is not enabled on this backend.") from None
            if str(exc) == "ibm_execution_unavailable":
                raise _typed_error(503, "ibm_execution_unavailable", "IBM Quantum execution is unavailable on this backend.") from None
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
