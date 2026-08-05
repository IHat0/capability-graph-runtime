"""Authenticated HTTP routes for the Phase 4 workflow service."""

from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from pydantic import ValidationError

from .runs import assert_public_response_safe
from .security import SecurityServices, current_security_context
from .workflows import (
    WORKFLOW_REQUEST_MAXIMUM_BYTES,
    WorkflowApprovalDecisionRequest,
    WorkflowCancelRequest,
    WorkflowCompileRequest,
    WorkflowConflictError,
    WorkflowInvalidRequestError,
    WorkflowNotFoundError,
    WorkflowResumeRequest,
    WorkflowRunCreateRequest,
    WorkflowService,
    WorkflowServiceError,
    WorkflowUnavailableError,
)


def _typed_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


async def _read_bounded_json_object(request: Request) -> dict[str, Any]:
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type != "application/json":
        raise _typed_error(
            415, "invalid_workflow_request", "Workflow requests must use JSON."
        )
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            size = int(declared)
        except ValueError:
            raise _typed_error(
                422, "invalid_workflow_request", "Workflow request is invalid."
            ) from None
        if size < 0:
            raise _typed_error(
                422, "invalid_workflow_request", "Workflow request is invalid."
            )
        if size > WORKFLOW_REQUEST_MAXIMUM_BYTES:
            raise _typed_error(
                413,
                "workflow_request_too_large",
                "Workflow request exceeds its size limit.",
            )
    payload = bytearray()
    try:
        async for chunk in request.stream():
            if len(payload) + len(chunk) > WORKFLOW_REQUEST_MAXIMUM_BYTES:
                raise _typed_error(
                    413,
                    "workflow_request_too_large",
                    "Workflow request exceeds its size limit.",
                )
            payload.extend(chunk)
        value = json.loads(payload)
    except HTTPException:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise _typed_error(
            422, "invalid_workflow_request", "Workflow request is invalid."
        ) from None
    if not isinstance(value, dict):
        raise _typed_error(
            422, "invalid_workflow_request", "Workflow request is invalid."
        )
    return value


def _context() -> tuple[str, str, str]:
    context = current_security_context()
    if context is None:
        raise _typed_error(
            503,
            "workflow_security_context_unavailable",
            "Workflow security context is unavailable.",
        )
    return (
        context.principal.tenant_identifier,
        context.principal.subject_identifier,
        context.correlation_identifier,
    )


def _sanitize_public(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _sanitize_public(item)
            for key, item in value.items()
            if key != "storage_location"
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize_public(item) for item in value]
    return value


def _public_payload(value: Any) -> Any:
    raw = value.model_dump(mode="json") if hasattr(value, "model_dump") else value
    payload = _sanitize_public(raw)
    assert_public_response_safe(payload)
    return payload


def _map_error(exc: WorkflowServiceError) -> HTTPException:
    if isinstance(exc, WorkflowNotFoundError):
        return _typed_error(404, "workflow_not_found", "Requested workflow resource was not found.")
    if isinstance(exc, WorkflowInvalidRequestError):
        return _typed_error(422, "invalid_workflow_request", "Workflow request is invalid.")
    if isinstance(exc, WorkflowConflictError):
        return _typed_error(409, "workflow_conflict", "Workflow request conflicts with current state.")
    if isinstance(exc, WorkflowUnavailableError):
        return _typed_error(503, "workflow_service_unavailable", "Workflow service is unavailable.")
    return _typed_error(500, "workflow_failed", "Workflow operation failed safely.")


def install_workflow_routes(
    application: FastAPI,
    *,
    service: WorkflowService,
    security_services: SecurityServices,
) -> None:
    """Install the complete authenticated Phase 4 HTTP surface."""

    def record(event: str, *, category: str | None = None) -> None:
        fields: dict[str, str] = {}
        if category is not None:
            fields["category"] = category[:64]
        security_services.events.emit(event, **fields)

    @application.post("/api/v1/workflows/compile")
    async def compile_workflow(http_request: Request) -> dict[str, Any]:
        tenant, principal, _correlation = _context()
        record("workflow.compile.started")
        try:
            try:
                request = WorkflowCompileRequest.model_validate(
                    await _read_bounded_json_object(http_request)
                )
            except ValidationError:
                raise _typed_error(
                    422, "invalid_workflow_request", "Workflow request is invalid."
                ) from None
            graph = service.compile_and_store(
                request,
                tenant_identifier=tenant,
                principal_identifier=principal,
            )
            security_services.metrics.increment("workflow_compilations")
            record("workflow.compile.completed")
            return _public_payload(graph)
        except WorkflowServiceError as exc:
            security_services.metrics.increment(
                "workflow_failures", category=type(exc).__name__[:64]
            )
            record("workflow.compile.failed", category=type(exc).__name__)
            raise _map_error(exc) from None

    @application.get(
        "/api/v1/workflows/{graph_identifier}/versions/{graph_version}"
    )
    def read_workflow(graph_identifier: str, graph_version: int) -> dict[str, Any]:
        tenant, _principal, _correlation = _context()
        try:
            return _public_payload(
                service.get_graph(
                    graph_identifier,
                    graph_version,
                    tenant_identifier=tenant,
                )
            )
        except WorkflowServiceError as exc:
            raise _map_error(exc) from None

    @application.post(
        "/api/v1/workflows/{graph_identifier}/versions/{graph_version}/validate"
    )
    def validate_workflow(graph_identifier: str, graph_version: int) -> dict[str, Any]:
        tenant, _principal, _correlation = _context()
        try:
            response = service.validate_graph(
                graph_identifier,
                graph_version,
                tenant_identifier=tenant,
            )
            security_services.metrics.increment(
                "workflow_validations", category="valid" if response.valid else "invalid"
            )
            return _public_payload(response)
        except WorkflowServiceError as exc:
            raise _map_error(exc) from None

    @application.post("/api/v1/workflow-runs")
    async def create_workflow_run(http_request: Request) -> dict[str, Any]:
        tenant, principal, _correlation = _context()
        try:
            try:
                request = WorkflowRunCreateRequest.model_validate(
                    await _read_bounded_json_object(http_request)
                )
            except ValidationError:
                raise _typed_error(
                    422, "invalid_workflow_request", "Workflow request is invalid."
                ) from None
            snapshot = service.create_run(
                request,
                tenant_identifier=tenant,
                principal_identifier=principal,
            )
            security_services.metrics.increment("workflow_runs_created")
            record("workflow.run.created")
            return _public_payload(snapshot)
        except WorkflowServiceError as exc:
            record("workflow.run.create_failed", category=type(exc).__name__)
            raise _map_error(exc) from None

    @application.get("/api/v1/workflow-runs/{graph_run_identifier}")
    def read_workflow_run(graph_run_identifier: str) -> dict[str, Any]:
        tenant, _principal, _correlation = _context()
        try:
            return _public_payload(
                service.get_run(graph_run_identifier, tenant_identifier=tenant)
            )
        except WorkflowServiceError as exc:
            raise _map_error(exc) from None

    @application.post(
        "/api/v1/workflow-runs/{graph_run_identifier}/approvals/{approval_identifier}"
    )
    async def decide_workflow_approval(
        graph_run_identifier: str,
        approval_identifier: str,
        http_request: Request,
    ) -> dict[str, Any]:
        tenant, principal, correlation = _context()
        try:
            try:
                request = WorkflowApprovalDecisionRequest.model_validate(
                    await _read_bounded_json_object(http_request)
                )
            except ValidationError:
                raise _typed_error(
                    422, "invalid_workflow_request", "Workflow request is invalid."
                ) from None
            snapshot = service.approve(
                graph_run_identifier,
                approval_identifier,
                request,
                tenant_identifier=tenant,
                principal_identifier=principal,
                correlation_identifier=correlation,
            )
            security_services.metrics.increment(
                "workflow_approval_decisions",
                category="granted" if request.granted else "denied",
            )
            record("workflow.approval.decided")
            return _public_payload(snapshot)
        except WorkflowServiceError as exc:
            record("workflow.approval.failed", category=type(exc).__name__)
            raise _map_error(exc) from None

    @application.post("/api/v1/workflow-runs/{graph_run_identifier}/resume")
    async def resume_workflow_run(
        graph_run_identifier: str, http_request: Request
    ) -> dict[str, Any]:
        tenant, _principal, correlation = _context()
        try:
            try:
                request = WorkflowResumeRequest.model_validate(
                    await _read_bounded_json_object(http_request)
                )
            except ValidationError:
                raise _typed_error(
                    422, "invalid_workflow_request", "Workflow request is invalid."
                ) from None
            snapshot = service.resume(
                graph_run_identifier,
                request,
                tenant_identifier=tenant,
                correlation_identifier=correlation,
            )
            security_services.metrics.increment(
                "workflow_resumes", category=snapshot.graph_state.status.value
            )
            record("workflow.run.resumed")
            return _public_payload(snapshot)
        except WorkflowServiceError as exc:
            record("workflow.run.resume_failed", category=type(exc).__name__)
            raise _map_error(exc) from None

    @application.post("/api/v1/workflow-runs/{graph_run_identifier}/cancel")
    async def cancel_workflow_run(
        graph_run_identifier: str, http_request: Request
    ) -> dict[str, Any]:
        tenant, principal, correlation = _context()
        try:
            try:
                request = WorkflowCancelRequest.model_validate(
                    await _read_bounded_json_object(http_request)
                )
            except ValidationError:
                raise _typed_error(
                    422, "invalid_workflow_request", "Workflow request is invalid."
                ) from None
            snapshot = service.cancel(
                graph_run_identifier,
                request,
                tenant_identifier=tenant,
                principal_identifier=principal,
                correlation_identifier=correlation,
            )
            security_services.metrics.increment("workflow_cancellations")
            record("workflow.run.cancelled")
            return _public_payload(snapshot)
        except WorkflowServiceError as exc:
            record("workflow.run.cancel_failed", category=type(exc).__name__)
            raise _map_error(exc) from None

    @application.get("/api/v1/workflow-runs/{graph_run_identifier}/nodes")
    def read_workflow_nodes(graph_run_identifier: str) -> dict[str, Any]:
        tenant, _principal, _correlation = _context()
        try:
            nodes = service.node_states(
                graph_run_identifier, tenant_identifier=tenant
            )
            payload = {"graph_run_identifier": graph_run_identifier, "nodes": nodes}
            return _public_payload(payload)
        except WorkflowServiceError as exc:
            raise _map_error(exc) from None

    @application.get("/api/v1/workflow-runs/{graph_run_identifier}/evidence")
    def read_workflow_evidence(graph_run_identifier: str) -> dict[str, Any]:
        tenant, _principal, _correlation = _context()
        try:
            return _public_payload(
                service.evidence(graph_run_identifier, tenant_identifier=tenant)
            )
        except WorkflowServiceError as exc:
            raise _map_error(exc) from None
