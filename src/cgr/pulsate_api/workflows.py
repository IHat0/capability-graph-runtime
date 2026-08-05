"""Authenticated Phase 4 workflow application service and API contracts."""

from __future__ import annotations

from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from cgr.science.canonical import validate_identifier
from cgr.science.planning import CandidateResearchPlan
from cgr.workflow_graph import (
    CapabilityAdapter,
    ExternalInputState,
    GraphRunRepository,
    WorkflowApprovalError,
    WorkflowCompilationError,
    WorkflowCompilationPolicy,
    WorkflowGraphCompiler,
    WorkflowGraphDefinition,
    WorkflowGraphRepository,
    WorkflowGraphValidator,
    WorkflowObserver,
    WorkflowOrchestrationError,
    WorkflowOrchestrator,
    WorkflowRepositoryConflictError,
    WorkflowRepositoryError,
    WorkflowRepositoryIntegrityError,
    WorkflowRepositoryNotFoundError,
    WorkflowRunSnapshot,
    WorkflowRunTerminalError,
    WorkflowStateIntegrityError,
)

WORKFLOW_REQUEST_MAXIMUM_BYTES = 8 * 1024 * 1024


class WorkflowServiceError(RuntimeError):
    """Controlled service-layer workflow error."""


class WorkflowNotFoundError(WorkflowServiceError):
    """Workflow definition or run is unavailable to this tenant."""


class WorkflowConflictError(WorkflowServiceError):
    """Workflow request conflicts with immutable or current state."""


class WorkflowInvalidRequestError(WorkflowServiceError):
    """Workflow request is malformed or statically invalid."""


class WorkflowUnavailableError(WorkflowServiceError):
    """Workflow persistence or orchestration is unavailable."""


class WorkflowCompileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    plan: CandidateResearchPlan
    policy: WorkflowCompilationPolicy = WorkflowCompilationPolicy()
    graph_identifier: str | None = None
    graph_version: int = Field(default=1, ge=1)

    @field_validator("graph_identifier")
    @classmethod
    def validate_graph_identifier(cls, value: str | None) -> str | None:
        return validate_identifier(value) if value is not None else None


class WorkflowRunCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    graph_identifier: str
    graph_version: int = Field(default=1, ge=1)
    graph_run_identifier: str | None = None
    external_inputs: tuple[ExternalInputState, ...] = ()

    @field_validator("graph_identifier", "graph_run_identifier")
    @classmethod
    def validate_identifiers(cls, value: str | None) -> str | None:
        return validate_identifier(value) if value is not None else None


class WorkflowApprovalDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    granted: bool
    decision_reason: str | None = Field(default=None, max_length=4096)

    @field_validator("decision_reason")
    @classmethod
    def normalize_reason(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("Approval decision reason cannot be blank.")
        return normalized


class WorkflowResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    maximum_cycles: int = Field(default=1000, ge=1, le=10_000)


class WorkflowCancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reason: str | None = Field(default=None, max_length=4096)


class WorkflowValidationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    valid: bool
    topological_order: tuple[str, ...] = ()
    errors: tuple[dict[str, object], ...] = ()
    warnings: tuple[dict[str, object], ...] = ()


class WorkflowService:
    """Tenant-isolated facade over compiler, repositories and orchestrator."""

    def __init__(
        self,
        *,
        root: Path,
        capability_adapter: CapabilityAdapter,
        maximum_parallelism: int = 8,
        observer: WorkflowObserver | None = None,
    ) -> None:
        self.root = Path(root)
        self.graph_repository = WorkflowGraphRepository(self.root)
        self.run_repository = GraphRunRepository(self.root)
        self.compiler = WorkflowGraphCompiler()
        self.orchestrator = WorkflowOrchestrator(
            graph_repository=self.graph_repository,
            run_repository=self.run_repository,
            capability_adapter=capability_adapter,
            maximum_parallelism=maximum_parallelism,
            observer=observer,
        )
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self.graph_repository.start()
        try:
            self.run_repository.start()
        except Exception:
            self.graph_repository.close()
            raise
        self._started = True

    def close(self) -> None:
        self.run_repository.close()
        self.graph_repository.close()
        self._started = False

    def ready(self) -> bool:
        return (
            self._started
            and self.graph_repository.ready()
            and self.run_repository.ready()
        )

    def compile_and_store(
        self,
        request: WorkflowCompileRequest,
        *,
        tenant_identifier: str,
        principal_identifier: str,
    ) -> WorkflowGraphDefinition:
        self._require_ready()
        try:
            graph = self.compiler.compile(
                request.plan,
                created_by_principal=principal_identifier,
                tenant_identifier=tenant_identifier,
                policy=request.policy,
                graph_identifier=request.graph_identifier,
                graph_version=request.graph_version,
            )
            return self.graph_repository.put(graph)
        except WorkflowRepositoryConflictError as exc:
            raise WorkflowConflictError("Workflow graph identity already exists.") from exc
        except WorkflowRepositoryError as exc:
            raise WorkflowUnavailableError("Workflow graph persistence is unavailable.") from exc
        except (WorkflowCompilationError, ValidationError, TypeError, ValueError) as exc:
            raise WorkflowInvalidRequestError("Workflow graph compilation failed.") from exc

    def get_graph(
        self,
        graph_identifier: str,
        graph_version: int,
        *,
        tenant_identifier: str,
    ) -> WorkflowGraphDefinition:
        self._require_ready()
        try:
            graph = self.graph_repository.get(graph_identifier, graph_version)
        except WorkflowRepositoryNotFoundError as exc:
            raise WorkflowNotFoundError("Workflow graph was not found.") from exc
        except WorkflowRepositoryIntegrityError as exc:
            raise WorkflowUnavailableError("Workflow graph evidence is invalid.") from exc
        self._require_graph_tenant(graph, tenant_identifier)
        return graph

    def validate_graph(
        self,
        graph_identifier: str,
        graph_version: int,
        *,
        tenant_identifier: str,
    ) -> WorkflowValidationResponse:
        graph = self.get_graph(
            graph_identifier, graph_version, tenant_identifier=tenant_identifier
        )
        result = WorkflowGraphValidator(graph).validate()
        order = WorkflowGraphValidator(graph).topological_order() if result.valid else ()
        return WorkflowValidationResponse(
            valid=result.valid,
            topological_order=order,
            errors=tuple(item.model_dump(mode="json") for item in result.errors),
            warnings=tuple(item.model_dump(mode="json") for item in result.warnings),
        )

    def create_run(
        self,
        request: WorkflowRunCreateRequest,
        *,
        tenant_identifier: str,
        principal_identifier: str,
    ) -> WorkflowRunSnapshot:
        self.get_graph(
            request.graph_identifier,
            request.graph_version,
            tenant_identifier=tenant_identifier,
        )
        try:
            return self.orchestrator.create_run(
                graph_identifier=request.graph_identifier,
                graph_version=request.graph_version,
                graph_run_identifier=request.graph_run_identifier,
                external_inputs=request.external_inputs,
                tenant_identifier=tenant_identifier,
                created_by_principal=principal_identifier,
            )
        except WorkflowRepositoryConflictError as exc:
            raise WorkflowConflictError("Workflow run identity already exists.") from exc
        except (WorkflowStateIntegrityError, ValueError) as exc:
            raise WorkflowInvalidRequestError("Workflow run request is invalid.") from exc
        except WorkflowRepositoryError as exc:
            raise WorkflowUnavailableError("Workflow run persistence is unavailable.") from exc

    def get_run(
        self, graph_run_identifier: str, *, tenant_identifier: str
    ) -> WorkflowRunSnapshot:
        self._require_ready()
        try:
            snapshot = self.run_repository.get(graph_run_identifier)
        except WorkflowRepositoryNotFoundError as exc:
            raise WorkflowNotFoundError("Workflow run was not found.") from exc
        except WorkflowRepositoryIntegrityError as exc:
            raise WorkflowUnavailableError("Workflow run evidence is invalid.") from exc
        self._require_run_tenant(snapshot, tenant_identifier)
        return snapshot

    def approve(
        self,
        graph_run_identifier: str,
        approval_identifier: str,
        request: WorkflowApprovalDecisionRequest,
        *,
        tenant_identifier: str,
        principal_identifier: str,
        correlation_identifier: str,
    ) -> WorkflowRunSnapshot:
        self.get_run(graph_run_identifier, tenant_identifier=tenant_identifier)
        try:
            return self.orchestrator.approve(
                graph_run_identifier=graph_run_identifier,
                approval_identifier=approval_identifier,
                approver_principal=principal_identifier,
                granted=request.granted,
                correlation_identifier=correlation_identifier,
                decision_reason=request.decision_reason,
            )
        except (WorkflowApprovalError, WorkflowRunTerminalError) as exc:
            raise WorkflowConflictError("Workflow approval cannot be applied.") from exc
        except WorkflowRepositoryError as exc:
            raise WorkflowUnavailableError("Workflow approval persistence failed.") from exc

    def resume(
        self,
        graph_run_identifier: str,
        request: WorkflowResumeRequest,
        *,
        tenant_identifier: str,
        correlation_identifier: str,
    ) -> WorkflowRunSnapshot:
        self.get_run(graph_run_identifier, tenant_identifier=tenant_identifier)
        try:
            return self.orchestrator.resume(
                graph_run_identifier,
                correlation_identifier=correlation_identifier,
                maximum_cycles=request.maximum_cycles,
            )
        except WorkflowOrchestrationError as exc:
            raise WorkflowConflictError("Workflow run could not continue.") from exc
        except WorkflowRepositoryError as exc:
            raise WorkflowUnavailableError("Workflow run persistence failed.") from exc

    def cancel(
        self,
        graph_run_identifier: str,
        request: WorkflowCancelRequest,
        *,
        tenant_identifier: str,
        principal_identifier: str,
        correlation_identifier: str,
    ) -> WorkflowRunSnapshot:
        self.get_run(graph_run_identifier, tenant_identifier=tenant_identifier)
        try:
            return self.orchestrator.cancel(
                graph_run_identifier=graph_run_identifier,
                requested_by_principal=principal_identifier,
                correlation_identifier=correlation_identifier,
                reason=request.reason,
            )
        except WorkflowRunTerminalError as exc:
            raise WorkflowConflictError("Workflow run is already terminal.") from exc
        except WorkflowRepositoryError as exc:
            raise WorkflowUnavailableError("Workflow cancellation persistence failed.") from exc

    def node_states(
        self, graph_run_identifier: str, *, tenant_identifier: str
    ) -> tuple[dict[str, object], ...]:
        snapshot = self.get_run(
            graph_run_identifier, tenant_identifier=tenant_identifier
        )
        return tuple(item.model_dump(mode="json") for item in snapshot.node_states)

    def evidence(
        self, graph_run_identifier: str, *, tenant_identifier: str
    ) -> dict[str, object]:
        snapshot = self.get_run(
            graph_run_identifier, tenant_identifier=tenant_identifier
        )
        return {
            "graph_run_identifier": graph_run_identifier,
            "graph_definition_fingerprint": (
                snapshot.graph_state.graph_definition_fingerprint
            ),
            "snapshot_fingerprint": snapshot.snapshot_fingerprint,
            "revision": snapshot.revision,
            "decisions": [
                item.model_dump(mode="json") for item in snapshot.decision_states
            ],
            "retries": [
                item.model_dump(mode="json") for item in snapshot.retry_states
            ],
            "approvals": [
                item.model_dump(mode="json") for item in snapshot.approval_states
            ],
            "artifacts": [
                artifact.pointer.model_dump(mode="json")
                for state in snapshot.node_states
                for artifact in state.output_artifacts
            ],
        }

    def _require_ready(self) -> None:
        if not self.ready():
            raise WorkflowUnavailableError("Workflow service is unavailable.")

    @staticmethod
    def _require_graph_tenant(
        graph: WorkflowGraphDefinition, tenant_identifier: str
    ) -> None:
        tenant = validate_identifier(tenant_identifier)
        if (
            graph.metadata is None
            or graph.metadata.tenant_identifier is None
            or graph.metadata.tenant_identifier != tenant
        ):
            raise WorkflowNotFoundError("Workflow graph was not found.")

    @staticmethod
    def _require_run_tenant(
        snapshot: WorkflowRunSnapshot, tenant_identifier: str
    ) -> None:
        tenant = validate_identifier(tenant_identifier)
        if snapshot.graph_state.tenant_identifier != tenant:
            raise WorkflowNotFoundError("Workflow run was not found.")
