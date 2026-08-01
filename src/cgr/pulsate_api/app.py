"""FastAPI surface for the Pulsate Labs scientific workspace."""

from __future__ import annotations

import math
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, Header, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from cgr.molecular import (
    MolecularArtifactRepository,
    MolecularProjectRepository,
)
from cgr.quantum_preflight.contracts import ManifestEnvelope
from cgr.quantum_preflight.environment import require_dependencies
from cgr.quantum_preflight.errors import QuantumDependencyError
from cgr.quantum_preflight.manifests import load_manifest

from .approved_experiments import (
    ApprovedExperimentExecutionResolver,
    ApprovedExperimentNotFoundError,
    ApprovedExperimentValidationError,
)
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
from .molecular_scenes import (
    NativeMolecularSceneError,
    NativeMolecularSceneNotFoundError,
    NativeMolecularSceneService,
    NativeMolecularSceneUnavailableError,
    NativeMolecularResource,
)
from .runs import (
    ArtifactUnavailableError,
    ExistingQuantumPreflightExecutor,
    IdempotencyConflictError,
    InvalidIdempotencyKeyError,
    RunCoordinator,
    RunNotFoundError,
    SceneUnavailableError,
    assert_public_response_safe,
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
    approved_experiment_identifier: str | None = None
    execution_target: Literal["local_simulator", "ibm_quantum"]

    @model_validator(mode="after")
    def validate_source(self) -> CreateRunRequest:
        if (
            sum(
                source is not None
                for source in (
                    self.preset_identifier,
                    self.experiment_identifier,
                    self.approved_experiment_identifier,
                )
            )
            != 1
        ):
            raise ValueError(
                "Exactly one approved_experiment_identifier, "
                "experiment_identifier, or preset_identifier is required."
            )
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
    molecular_scene_service: NativeMolecularSceneService | None = None,
) -> FastAPI:
    molecular_project_repository: MolecularProjectRepository | None = None
    molecular_artifact_repository: MolecularArtifactRepository | None = None
    if molecular_scene_service is None:
        project_root = os.environ.get("PULSATE_MOLECULAR_PROJECT_ROOT")
        artifact_root = os.environ.get("PULSATE_MOLECULAR_ARTIFACT_ROOT")
        if (project_root is None) != (artifact_root is None):
            raise ValueError(
                "Molecular project and artifact roots must be configured together."
            )
        if project_root is not None and artifact_root is not None:
            if not project_root.strip() or not artifact_root.strip():
                raise ValueError(
                    "Molecular project and artifact roots cannot be empty."
                )
            molecular_project_repository = MolecularProjectRepository(
                Path(project_root)
            )
            molecular_artifact_repository = MolecularArtifactRepository(
                Path(artifact_root)
            )
            molecular_scene_service = NativeMolecularSceneService(
                project_resolver=molecular_project_repository.resolve,
                repository=molecular_artifact_repository,
            )
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
    if run_coordinator.approved_experiment_resolver is None:
        run_coordinator.approved_experiment_resolver = (
            ApprovedExperimentExecutionResolver(natural_language_store)
        )

    @asynccontextmanager
    async def lifespan(_application: FastAPI):
        try:
            experiment_store.start()
            natural_language_store.start()
            run_coordinator.start()
            if molecular_project_repository is not None:
                molecular_project_repository.start()
            if molecular_scene_service is not None:
                molecular_scene_service.start()
            yield
        finally:
            try:
                if molecular_scene_service is not None:
                    molecular_scene_service.close()
            finally:
                try:
                    if molecular_project_repository is not None:
                        molecular_project_repository.close()
                finally:
                    try:
                        run_coordinator.close()
                    finally:
                        try:
                            natural_language_store.close()
                        finally:
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
    application.state.molecular_scene_service = molecular_scene_service
    application.state.molecular_project_repository = molecular_project_repository
    application.state.molecular_artifact_repository = molecular_artifact_repository

    def require_molecular_scene_service() -> NativeMolecularSceneService:
        if molecular_scene_service is None:
            raise _typed_error(
                503,
                "molecular_scene_service_unavailable",
                "Native molecular scene service is unavailable.",
            )
        return molecular_scene_service

    def map_molecular_scene_error(exc: NativeMolecularSceneError) -> HTTPException:
        if isinstance(exc, NativeMolecularSceneUnavailableError):
            return _typed_error(
                503,
                "molecular_scene_service_unavailable",
                "Native molecular scene service is unavailable.",
            )
        if isinstance(exc, NativeMolecularSceneNotFoundError):
            return _typed_error(
                404,
                "molecular_resource_not_found",
                "Requested molecular resource was not found.",
            )
        return _typed_error(
            409,
            "molecular_scene_unavailable",
            "Native molecular scene evidence is unavailable or invalid.",
        )

    def molecular_resource_response(resource: NativeMolecularResource) -> Response:
        return Response(
            content=resource.payload,
            headers={
                "Content-Type": resource.media_type,
                "ETag": f'"{resource.content_sha256}"',
                "X-Content-SHA256": resource.content_sha256,
            },
        )

    def assert_molecular_scene_metadata_safe(
        metadata: dict[str, object],
    ) -> None:
        structures = metadata.get("structures")
        if not isinstance(structures, list):
            raise ValueError("Molecular scene structures are malformed.")
        safe_structures: list[dict[str, object]] = []
        expected_paths = {
            "native_structure_url": (
                "/api/v1/molecular/scenes/native-structure"
            ),
            "topology_url": "/api/v1/molecular/scenes/topology",
        }
        for structure in structures:
            if not isinstance(structure, dict):
                raise ValueError("Molecular scene structures are malformed.")
            safe_structure = dict(structure)
            for field, expected_path in expected_paths.items():
                value = safe_structure.get(field)
                if not isinstance(value, str):
                    raise ValueError("Molecular resource URL is malformed.")
                parsed = urlsplit(value)
                if (
                    parsed.scheme
                    or parsed.netloc
                    or parsed.fragment
                    or parsed.path != expected_path
                    or not parsed.query
                ):
                    raise ValueError("Molecular resource URL is malformed.")
                safe_structure[field] = parsed.path
            safe_structures.append(safe_structure)
        assert_public_response_safe(
            {**metadata, "structures": safe_structures}
        )

    @application.get("/api/v1/health")
    def health() -> dict[str, str]:
        return {
            "service": "pulsate-api", "status": "healthy", "version": "0.2.0",
        }

    @application.get("/api/v1/molecular/scenes/projected")
    def read_projected_molecular_scene(
        project_identifier: str,
        scene_identifier: str,
    ) -> dict[str, object]:
        try:
            metadata = require_molecular_scene_service().describe_scene(
                project_identifier=project_identifier,
                scene_identifier=scene_identifier,
            )
            assert_molecular_scene_metadata_safe(metadata)
            return metadata
        except NativeMolecularSceneError as exc:
            raise map_molecular_scene_error(exc) from None
        except (ValueError, TypeError):
            raise _typed_error(
                409,
                "molecular_scene_unavailable",
                "Native molecular scene evidence is unavailable or invalid.",
            ) from None

    @application.get("/api/v1/molecular/scenes/native-structure")
    def read_native_molecular_structure(
        project_identifier: str,
        scene_identifier: str,
        structure_identifier: str,
    ) -> Response:
        try:
            resource = require_molecular_scene_service().read_native_structure(
                project_identifier=project_identifier,
                scene_identifier=scene_identifier,
                structure_identifier=structure_identifier,
            )
            return molecular_resource_response(resource)
        except NativeMolecularSceneError as exc:
            raise map_molecular_scene_error(exc) from None

    @application.get("/api/v1/molecular/scenes/topology")
    def read_native_molecular_topology(
        project_identifier: str,
        scene_identifier: str,
        structure_identifier: str,
    ) -> Response:
        try:
            resource = require_molecular_scene_service().read_topology(
                project_identifier=project_identifier,
                scene_identifier=scene_identifier,
                structure_identifier=structure_identifier,
            )
            return molecular_resource_response(resource)
        except NativeMolecularSceneError as exc:
            raise map_molecular_scene_error(exc) from None

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
                approved_experiment_identifier=(
                    request.approved_experiment_identifier
                ),
            )
            return state
        except HTTPException as exc:
            if exc.status_code == 404:
                raise _typed_error(404, "preset_not_found", "Experiment preset not found.") from None
            raise
        except RunNotFoundError:
            if request.approved_experiment_identifier:
                code = "approved_experiment_not_found"
                message = "Approved experiment not found."
            elif request.experiment_identifier:
                code = "experiment_not_found"
                message = "Experiment not found."
            else:
                code = "preset_not_found"
                message = "Experiment preset not found."
            raise _typed_error(404, code, message) from None
        except ExperimentNotFoundError:
            raise _typed_error(404, "experiment_not_found", "Experiment not found.") from None
        except ApprovedExperimentNotFoundError:
            raise _typed_error(
                404,
                "approved_experiment_not_found",
                "Approved experiment not found.",
            ) from None
        except ApprovedExperimentValidationError as exc:
            raise _typed_error(
                422,
                "approved_experiment_not_executable",
                str(exc),
            ) from None
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

    @application.get("/api/v1/runs/{run_identifier}/scene")
    def read_run_scene(run_identifier: str) -> dict[str, Any]:
        try:
            state, manifest = run_coordinator.scene_manifest(run_identifier)
            scene = _declared_scene(manifest)
            if (
                scene.get("experiment_identifier")
                != state.get("experiment_identifier")
                or scene.get("experiment_fingerprint")
                != state.get("experiment_fingerprint")
            ):
                raise SceneUnavailableError("Run scene is unavailable.")
            assert_public_response_safe(scene)
            return scene
        except (RunNotFoundError, FileNotFoundError):
            raise _typed_error(404, "run_not_found", "Run not found.") from None
        except (SceneUnavailableError, ValueError, TypeError, IndexError):
            raise _typed_error(
                409, "scene_unavailable", "Run scene is unavailable."
            ) from None

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
