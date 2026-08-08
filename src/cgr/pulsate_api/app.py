"""FastAPI surface for the Pulsate Labs scientific workspace."""

from __future__ import annotations

import json
import math
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from cgr.molecular import (
    MolecularArtifactRepository,
    MolecularProjectRepository,
)
from cgr.quantum_preflight.contracts import ManifestEnvelope
from cgr.quantum_preflight.environment import require_dependencies
from cgr.quantum_preflight.errors import QuantumDependencyError
from cgr.quantum_preflight.manifests import load_manifest
from cgr.science import ScientificCapabilityCatalog
from cgr.workflow_graph import CapabilityAdapterRegistry

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
from .molecular_planning import (
    MolecularPlanningCapabilityNotFoundError,
    MolecularPlanningError,
    MolecularPlanningIntegrityError,
    MolecularPlanningProjectNotFoundError,
    MolecularPlanningRequest,
    MolecularPlanningRequestError,
    MolecularPlanningResponse,
    MolecularPlanningService,
    MolecularPlanningUnavailableError,
    MOLECULAR_PLANNING_REQUEST_MAXIMUM_BYTES,
)
from .production_catalogue import ProductionCapabilityCatalogue
from .scientific_executions import (
    ScientificExecutionRepository,
    ScientificObjectiveCompileRequest,
)
from .scientific_runtime import (
    ScientificCapabilityFailure,
    ScientificObjectiveRuntime,
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
from .workflow_quantum import (
    RunCoordinatorWorkflowAdapter,
    WorkflowCapabilityRouter,
)
from .workflow_routes import install_workflow_routes
from .workflows import WorkflowService
from .security import (
    CORRELATION_HEADER,
    PUBLIC_ROUTES,
    ROUTE_PROTECTIONS,
    SecurityBoundaryError,
    SecurityServices,
    monotonic_time,
    request_identifier as new_request_identifier,
    route_resource_identifier,
    safe_digest,
    valid_or_generated_correlation,
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


async def _read_bounded_json_object(
    request: Request,
    *,
    maximum_bytes: int,
    error_code: str,
    error_message: str,
    too_large_code: str,
    too_large_message: str,
) -> dict[str, Any]:
    """Read one bounded JSON object without echoing invalid request content."""

    media_type = request.headers.get("content-type", "").split(";", 1)[0]
    if media_type.strip().lower() != "application/json":
        raise _typed_error(415, error_code, error_message)
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared_bytes = int(content_length)
        except ValueError:
            raise _typed_error(422, error_code, error_message) from None
        if declared_bytes < 0:
            raise _typed_error(422, error_code, error_message)
        if declared_bytes > maximum_bytes:
            raise _typed_error(
                413,
                too_large_code,
                too_large_message,
            )

    payload = bytearray()
    try:
        async for chunk in request.stream():
            if len(payload) + len(chunk) > maximum_bytes:
                raise _typed_error(
                    413,
                    too_large_code,
                    too_large_message,
                )
            payload.extend(chunk)
        value = json.loads(payload)
    except HTTPException:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise _typed_error(422, error_code, error_message) from None
    if not isinstance(value, dict):
        raise _typed_error(422, error_code, error_message)
    return value


def _configured_coordinator(
    experiment_store: ExperimentStore,
    *,
    configured_run_root: Path | None = None,
) -> RunCoordinator:
    run_root = configured_run_root or Path(
        os.environ.get("PULSATE_RUN_ROOT", str(REPO_ROOT / ".pulsate-runs"))
    )
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
    molecular_planning_service: MolecularPlanningService | None = None,
    scientific_capability_catalogue: ScientificCapabilityCatalog | None = None,
    security_services: SecurityServices | None = None,
    workflow_service: WorkflowService | None = None,
    scientific_execution_repository: ScientificExecutionRepository | None = None,
    scientific_objective_runtime: ScientificObjectiveRuntime | None = None,
) -> FastAPI:
    security_services = security_services or SecurityServices.from_environment()
    runtime_configuration = getattr(security_services, "configuration", None)
    application_data_root = (
        runtime_configuration.repositories.application_data_root
        if runtime_configuration is not None
        else None
    )
    production_capability_catalogue: ProductionCapabilityCatalogue | None = None
    if scientific_capability_catalogue is None and runtime_configuration is not None:
        production_capability_catalogue = ProductionCapabilityCatalogue(
            runtime_configuration.catalogue,
            trusted_configuration_root=(
                runtime_configuration.repositories.trusted_configuration_root
            ),
            event_logger=security_services.events,
        )
        scientific_capability_catalogue = production_capability_catalogue
    elif scientific_capability_catalogue is None:
        scientific_capability_catalogue = ScientificCapabilityCatalog()
    molecular_project_repository: MolecularProjectRepository | None = None
    molecular_artifact_repository: MolecularArtifactRepository | None = None
    if molecular_scene_service is None:
        if application_data_root is not None:
            project_root = str(application_data_root / "molecular-projects")
            artifact_root = str(application_data_root / "molecular-artifacts")
        else:
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
            if molecular_planning_service is None:
                molecular_planning_service = MolecularPlanningService(
                    project_resolver=molecular_project_repository.resolve,
                    artifact_repository=molecular_artifact_repository,
                    catalogue=scientific_capability_catalogue,
                )
    if experiment_store is None:
        if coordinator is not None:
            experiment_root = coordinator.configured_run_root.parent / "experiments"
        else:
            experiment_root = Path(
                (
                    str(application_data_root / "experiments")
                    if application_data_root is not None
                    else os.environ.get(
                        "PULSATE_EXPERIMENT_ROOT",
                        str(REPO_ROOT / ".pulsate-experiments"),
                    )
                )
            )
        experiment_store = ExperimentStore(experiment_root)
    run_coordinator = coordinator or _configured_coordinator(
        experiment_store,
        configured_run_root=(
            application_data_root / "runs"
            if application_data_root is not None
            else None
        ),
    )
    if run_coordinator.experiment_resolver is None:
        run_coordinator.experiment_resolver = experiment_store.resolve_for_targeted_run
    experiment_store.ibm_capability = lambda: run_coordinator.capability()["ibm_quantum"]
    if natural_language_store is None:
        interpretation_root = Path(
            (
                str(application_data_root / "interpretations")
                if application_data_root is not None
                else os.environ.get(
                    "PULSATE_INTERPRETATION_ROOT",
                    str(REPO_ROOT / ".pulsate-interpretations"),
                )
            )
        )
        natural_language_store = NaturalLanguageInterpretationStore.from_environment(
            interpretation_root
        )
    if run_coordinator.approved_experiment_resolver is None:
        run_coordinator.approved_experiment_resolver = (
            ApprovedExperimentExecutionResolver(natural_language_store)
        )

    if workflow_service is None:
        configured_workflow_root = (
            application_data_root / "workflows"
            if application_data_root is not None
            else Path(
                os.environ.get(
                    "PULSATE_WORKFLOW_ROOT",
                    str(REPO_ROOT / ".pulsate-workflows"),
                )
            )
        )
        if runtime_configuration is not None:
            maximum_parallelism = runtime_configuration.workflow.maximum_parallelism
        else:
            maximum_parallelism_raw = os.environ.get(
                "PULSATE_WORKFLOW_MAX_PARALLELISM", "8"
            )
            try:
                maximum_parallelism = int(maximum_parallelism_raw)
            except ValueError:
                raise ValueError(
                    "Workflow maximum parallelism configuration is invalid."
                ) from None
            if not 1 <= maximum_parallelism <= 64:
                raise ValueError(
                    "Workflow maximum parallelism configuration is invalid."
                )
        workflow_adapter = WorkflowCapabilityRouter(
            classical_adapter=CapabilityAdapterRegistry(),
            quantum_adapter=RunCoordinatorWorkflowAdapter(run_coordinator),
        )

        def workflow_observer(
            event: str, fields: dict[str, str | int | float | bool]
        ) -> None:
            security_services.events.emit(event, **fields)

        workflow_service = WorkflowService(
            root=configured_workflow_root,
            capability_adapter=workflow_adapter,
            maximum_parallelism=maximum_parallelism,
            observer=workflow_observer,
        )

    if scientific_execution_repository is None:
        scientific_execution_root = (
            application_data_root / "scientific-executions"
            if application_data_root is not None
            else Path(
                os.environ.get(
                    "PULSATE_SCIENTIFIC_EXECUTION_ROOT",
                    str(REPO_ROOT / ".pulsate-scientific-executions"),
                )
            )
        )
        scientific_execution_repository = ScientificExecutionRepository(
            scientific_execution_root
        )

    @asynccontextmanager
    async def lifespan(_application: FastAPI):
        _application.state.lifecycle_ready = False
        try:
            try:
                security_services.start()
            except Exception:
                security_services.events.emit(
                    "repository.unavailable", category="security_startup"
                )
            if production_capability_catalogue is not None:
                try:
                    production_capability_catalogue.start()
                except Exception:
                    security_services.events.emit(
                        "repository.unavailable", category="catalogue_startup"
                    )
                    if runtime_configuration.catalogue.required:
                        security_services.close()
            try:
                experiment_store.start()
                natural_language_store.start()
                run_coordinator.start()
                workflow_service.start()
                scientific_execution_repository.start()
                if scientific_objective_runtime is not None:
                    scientific_objective_runtime.start()
                if molecular_project_repository is not None:
                    molecular_project_repository.start()
                if molecular_scene_service is not None:
                    molecular_scene_service.start()
                if molecular_planning_service is not None:
                    molecular_planning_service.start()
            except Exception:
                security_services.metrics.increment(
                    "repository_failures", category="repository_startup"
                )
                security_services.events.emit(
                    "repository.unavailable", category="repository_startup"
                )
                raise
            _application.state.lifecycle_ready = True
            if security_services.ready():
                security_services.events.emit("application.ready")
            yield
        finally:
            _application.state.lifecycle_ready = False
            try:
                if molecular_planning_service is not None:
                    molecular_planning_service.close()
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
                            if scientific_objective_runtime is not None:
                                scientific_objective_runtime.close()
                            scientific_execution_repository.close()
                        finally:
                            try:
                                workflow_service.close()
                            finally:
                                try:
                                    run_coordinator.close()
                                finally:
                                    try:
                                        natural_language_store.close()
                                    finally:
                                        try:
                                            experiment_store.close()
                                        finally:
                                            try:
                                                if production_capability_catalogue is not None:
                                                    production_capability_catalogue.close()
                                            finally:
                                                security_services.close()

    async def enforce_security(request: Request) -> None:
        route = request.scope.get("route")
        route_path = getattr(route, "path", request.url.path)
        route_key = (request.method, route_path)
        if route_key in PUBLIC_ROUTES:
            return
        protection = ROUTE_PROTECTIONS.get(route_key)
        if protection is None:
            raise _typed_error(
                503,
                "route_security_unavailable",
                "Route security policy is unavailable.",
            )
        correlation_identifier = request.state.correlation_identifier
        generated_request_identifier = request.state.request_identifier
        try:
            context = security_services.authenticate(
                request.headers.get("Authorization"),
                correlation_identifier,
                generated_request_identifier,
            )
            try:
                resource_identifier = route_resource_identifier(
                    protection,
                    path_params=request.path_params,
                    query_params=request.query_params,
                )
            except SecurityBoundaryError:
                security_services.audit_invalid_resource_denial(
                    context, protection
                )
                raise
            security_services.authorize(context, protection, resource_identifier)
        except SecurityBoundaryError as exc:
            headers = (
                {"WWW-Authenticate": "Bearer"}
                if exc.status_code == 401
                else None
            )
            raise HTTPException(
                status_code=exc.status_code,
                detail={"code": exc.category, "message": exc.public_message},
                headers=headers,
            ) from None

    application = FastAPI(
        title="Pulsate Labs API",
        version="0.2.0",
        description=(
            "Interprets reviewable scientific questions and coordinates approved "
            "or discovered experiments through controlled workflows."
        ),
        lifespan=lifespan,
        dependencies=[Depends(enforce_security)],
    )
    application.state.run_coordinator = run_coordinator
    application.state.workflow_service = workflow_service
    application.state.experiment_store = experiment_store
    application.state.natural_language_store = natural_language_store
    application.state.molecular_scene_service = molecular_scene_service
    application.state.molecular_project_repository = molecular_project_repository
    application.state.molecular_artifact_repository = molecular_artifact_repository
    application.state.molecular_planning_service = molecular_planning_service
    application.state.scientific_capability_catalogue = (
        molecular_planning_service.catalogue
        if molecular_planning_service is not None
        else scientific_capability_catalogue
    )
    application.state.production_capability_catalogue = (
        production_capability_catalogue
    )
    application.state.security_services = security_services
    application.state.scientific_execution_repository = (
        scientific_execution_repository
    )
    application.state.scientific_objective_runtime = scientific_objective_runtime
    application.state.lifecycle_ready = False

    @application.exception_handler(SecurityBoundaryError)
    async def controlled_security_failure(
        _request: Request, exc: SecurityBoundaryError
    ) -> JSONResponse:
        headers = (
            {"WWW-Authenticate": "Bearer"}
            if exc.status_code == 401
            else None
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "detail": {
                    "code": exc.category,
                    "message": exc.public_message,
                }
            },
            headers=headers,
        )

    @application.middleware("http")
    async def security_request_identity(request: Request, call_next):
        correlation_identifier = valid_or_generated_correlation(
            request.headers.get(CORRELATION_HEADER)
        )
        generated_request_identifier = new_request_identifier()
        request.state.correlation_identifier = correlation_identifier
        request.state.request_identifier = generated_request_identifier
        started = monotonic_time()
        response: Response | None = None
        try:
            response = await call_next(request)
            return response
        finally:
            duration = monotonic_time() - started
            route = request.scope.get("route")
            route_path = getattr(route, "path", "unmatched")
            status_code = response.status_code if response is not None else 500
            if response is not None:
                response.headers[CORRELATION_HEADER] = correlation_identifier
            security_services.metrics.increment(
                "request_count",
                method=request.method,
                route=safe_digest(route_path),
                status=str(status_code),
            )
            security_services.metrics.observe(
                "request_duration_seconds",
                duration,
                method=request.method,
                route=safe_digest(route_path),
                status=str(status_code),
            )
            security_services.events.emit(
                "request.completed",
                correlation_identifier=correlation_identifier,
                request_identifier=generated_request_identifier,
                method=request.method,
                route=safe_digest(route_path),
                status=status_code,
                duration_ms=round(duration * 1000, 3),
            )
            security_services.clear_context()

    def custom_openapi() -> dict[str, Any]:
        if application.openapi_schema is not None:
            return application.openapi_schema
        schema = get_openapi(
            title=application.title,
            version=application.version,
            description=application.description,
            routes=application.routes,
        )
        components = schema.setdefault("components", {})
        components.setdefault("securitySchemes", {})["BearerAuth"] = {
            "type": "http",
            "scheme": "bearer",
        }
        for path, operations in schema.get("paths", {}).items():
            if not isinstance(operations, dict):
                continue
            for method, operation in operations.items():
                if not isinstance(operation, dict):
                    continue
                key = (method.upper(), path)
                operation["security"] = [] if key in PUBLIC_ROUTES else [
                    {"BearerAuth": []}
                ]
        application.openapi_schema = schema
        return schema

    application.openapi = custom_openapi

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
            security_services.metrics.increment(
                "repository_failures", category="scene_service_unavailable"
            )
            security_services.events.emit(
                "repository.unavailable", category="scene_service_unavailable"
            )
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
        security_services.metrics.increment(
            "repository_failures", category="scene_integrity_failure"
        )
        security_services.events.emit(
            "repository.unavailable", category="scene_integrity_failure"
        )
        return _typed_error(
            409,
            "molecular_scene_unavailable",
            "Native molecular scene evidence is unavailable or invalid.",
        )

    def require_molecular_planning_service() -> MolecularPlanningService:
        if molecular_planning_service is None:
            raise _typed_error(
                503,
                "molecular_planning_service_unavailable",
                "Molecular planning service is unavailable.",
            )
        return molecular_planning_service

    def map_molecular_planning_error(exc: MolecularPlanningError) -> HTTPException:
        if isinstance(exc, MolecularPlanningProjectNotFoundError):
            return _typed_error(
                404,
                "molecular_project_not_found",
                "Requested molecular project was not found.",
            )
        if isinstance(exc, MolecularPlanningCapabilityNotFoundError):
            return _typed_error(
                404,
                "scientific_capability_not_found",
                "Requested scientific capability was not found.",
            )
        if isinstance(exc, MolecularPlanningRequestError):
            return _typed_error(
                422,
                "invalid_molecular_planning_request",
                str(exc),
            )
        if isinstance(exc, MolecularPlanningUnavailableError):
            security_services.metrics.increment(
                "repository_failures", category="planning_service_unavailable"
            )
            security_services.events.emit(
                "repository.unavailable", category="planning_service_unavailable"
            )
            return _typed_error(
                503,
                "molecular_planning_service_unavailable",
                "Molecular planning service is unavailable.",
            )
        if isinstance(exc, MolecularPlanningIntegrityError):
            return _typed_error(
                409,
                "molecular_planning_evidence_invalid",
                "Molecular planning evidence is unavailable or invalid.",
            )
        return _typed_error(
            409,
            "molecular_planning_failed",
            "Molecular planning could not be completed safely.",
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

    @application.post("/api/v1/scientific/objectives/compile", status_code=201)
    async def compile_scientific_request(request: Request):
        payload = await _read_bounded_json_object(
            request,
            maximum_bytes=2 * 1024 * 1024,
            error_code="scientific_objective_invalid",
            error_message="Scientific objective request is invalid.",
            too_large_code="scientific_objective_too_large",
            too_large_message="Scientific objective request exceeds its size limit.",
        )
        try:
            compile_request = ScientificObjectiveCompileRequest.model_validate(payload)
            return scientific_execution_repository.create(compile_request)
        except ValidationError:
            raise _typed_error(
                422,
                "scientific_objective_invalid",
                "Scientific objective request is invalid.",
            ) from None
        except ValueError:
            raise _typed_error(
                422,
                "scientific_objective_unsupported",
                "Scientific objective is unsupported or ambiguous.",
            ) from None
        except RuntimeError:
            raise _typed_error(
                503,
                "scientific_execution_unavailable",
                "Scientific execution persistence is unavailable.",
            ) from None

    @application.get("/api/v1/scientific/executions/{execution_identifier}")
    def get_scientific_execution(execution_identifier: str):
        try:
            return scientific_execution_repository.get(execution_identifier)
        except KeyError:
            raise _typed_error(
                404,
                "scientific_execution_not_found",
                "Scientific execution was not found.",
            ) from None
        except RuntimeError:
            raise _typed_error(
                503,
                "scientific_execution_unavailable",
                "Scientific execution persistence is unavailable.",
            ) from None

    @application.post(
        "/api/v1/scientific/executions/{execution_identifier}/execute"
    )
    def execute_scientific_objective(execution_identifier: str):
        if scientific_objective_runtime is None:
            raise _typed_error(
                503,
                "scientific_runtime_unavailable",
                "Scientific objective execution is unavailable.",
            )
        try:
            return scientific_objective_runtime.execute(execution_identifier)
        except KeyError:
            raise _typed_error(
                404,
                "scientific_execution_not_found",
                "Scientific execution was not found.",
            ) from None
        except ScientificCapabilityFailure as error:
            raise _typed_error(409, error.code, error.public_message) from None
        except RuntimeError:
            raise _typed_error(
                503,
                "scientific_execution_failed",
                "Scientific execution failed safely without fabricated results.",
            ) from None

    @application.get("/live")
    def liveness() -> dict[str, str]:
        return {"status": "alive"}

    @application.get("/ready")
    def readiness() -> dict[str, object]:
        configured_services = (
            experiment_store,
            natural_language_store,
            run_coordinator,
            molecular_project_repository,
            molecular_artifact_repository,
            molecular_scene_service,
            molecular_planning_service,
            workflow_service,
            scientific_execution_repository,
        )
        repositories_ready = all(
            service is None or bool(getattr(service, "_started", True))
            for service in configured_services
        )
        if (
            not application.state.lifecycle_ready
            or not security_services.ready()
            or not repositories_ready
            or (
                production_capability_catalogue is not None
                and not production_capability_catalogue.ready()
            )
        ):
            raise _typed_error(
                503,
                "service_not_ready",
                "The service is not ready.",
            )
        return {
            "status": "ready",
            "components": {
                "application": "ready",
                "security": "ready",
                "repositories": "ready",
                "catalogue": "ready",
            },
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

    @application.post(
        "/api/v1/molecular/projects/{project_identifier}/planning/evaluate",
        response_model=MolecularPlanningResponse,
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": MolecularPlanningRequest.model_json_schema()
                    }
                },
            }
        },
    )
    async def evaluate_molecular_project_plan(
        project_identifier: str,
        http_request: Request,
    ) -> dict[str, Any]:
        security_services.events.emit("planning.started")
        try:
            request_payload = await _read_bounded_json_object(
                http_request,
                maximum_bytes=MOLECULAR_PLANNING_REQUEST_MAXIMUM_BYTES,
                error_code="invalid_molecular_planning_request",
                error_message="Molecular planning request is invalid.",
                too_large_code="molecular_planning_request_too_large",
                too_large_message=(
                    "Molecular planning request exceeds its size limit."
                ),
            )
            try:
                request = MolecularPlanningRequest.model_validate(request_payload)
            except (TypeError, ValueError):
                raise _typed_error(
                    422,
                    "invalid_molecular_planning_request",
                    "Molecular planning request is invalid.",
                ) from None
            response = require_molecular_planning_service().evaluate(
                project_identifier=project_identifier,
                request=request,
            )
            payload = response.model_dump(mode="json")
            security_services.audit_planning_result(response.plan_fingerprint)
            security_services.metrics.increment("planning_evaluations")
            security_services.metrics.increment(
                "selected_capability_count",
                len(response.plan.selected_assignments),
                category="selected",
            )
            security_services.metrics.increment(
                "rejected_capability_count",
                len(response.plan.rejected_alternatives),
                category="rejected",
            )
            security_services.events.emit(
                "planning.completed",
                selected_count=len(response.plan.selected_assignments),
                rejected_count=len(response.plan.rejected_alternatives),
            )
            assert_public_response_safe(payload)
            return payload
        except MolecularPlanningError as exc:
            security_services.metrics.increment(
                "planning_failures", category=type(exc).__name__[:64]
            )
            security_services.events.emit(
                "planning.failed", category="controlled_planning_error"
            )
            raise map_molecular_planning_error(exc) from None
        except (TypeError, ValueError):
            security_services.metrics.increment(
                "planning_failures", category="invalid_planning_evidence"
            )
            security_services.events.emit(
                "planning.failed", category="invalid_planning_evidence"
            )
            raise _typed_error(
                409,
                "molecular_planning_failed",
                "Molecular planning could not be completed safely.",
            ) from None

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

    install_workflow_routes(
        application, service=workflow_service, security_services=security_services
    )

    return application


app = create_app()
