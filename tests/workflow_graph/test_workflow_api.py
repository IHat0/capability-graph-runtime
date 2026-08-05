"""Authenticated tenant-isolated Phase 4 workflow API tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from cgr.kernel.contracts import CapabilityVersion
from cgr.pulsate_api.app import _load_preset, create_app
from cgr.pulsate_api.experiments import ExperimentStore
from cgr.pulsate_api.natural_language import NaturalLanguageInterpretationStore
from cgr.pulsate_api.runs import RunCoordinator
from cgr.pulsate_api.security import (
    InMemoryAuditSink,
    InMemoryGrantProvider,
    SecurityServices,
    StaticAuthenticator,
)
from cgr.pulsate_api.security_contracts import (
    AuthenticatedPrincipal,
    ProtectedAction,
    ResourceGrant,
)
from cgr.pulsate_api.workflows import WorkflowService
from cgr.science.artifacts import CreationProvenance
from cgr.science.capabilities import CapabilityExecutionEnvelope, ScientificCapabilityCatalog
from cgr.science.contracts import CapabilityDescriptor, DeterminismClassification
from cgr.science.feasibility import construct_candidate_research_plan
from cgr.science.planning import PlanningConstraints, PlanningFactSet, ScientificObjective
from cgr.science.resources import ResourceAvailabilitySnapshot
from cgr.workflow_graph import (
    CapabilityAdapterRegistry,
    CapabilityInvocation,
    CapabilityInvocationResult,
    CapabilityInvocationStatus,
    ApprovalRequirement,
    ExecutionPolicy,
    FrozenDict,
    NodeKind,
    WorkflowGraphDefinition,
    WorkflowGraphMetadata,
    WorkflowNode,
)

TOKEN = "workflow-api-token"
VERSION = CapabilityVersion(major=1, minor=0, patch=0)
PROVENANCE = CreationProvenance(producer="workflow-api-tests", producer_version=VERSION)


class _NoExecution:
    def execute(self, *_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("Workflow API tests cannot invoke the legacy execution worker.")


def _plan() -> Any:
    objective = ScientificObjective(
        objective_identifier="objective.api",
        schema_version=VERSION,
        objective_type="property_estimation",
        original_description="Estimate a property.",
        normalized_description="Estimate a property.",
        project_identifier="project.api",
        molecular_system_identifiers=("system.api",),
        structure_identifiers=("structure.api",),
        provenance=PROVENANCE,
    )
    resources = ResourceAvailabilitySnapshot(
        snapshot_identifier="resources.api",
        schema_version=VERSION,
        observed_at=datetime(2026, 8, 4, tzinfo=UTC),
        execution_environment="test",
        available_execution_targets=("local_simulator",),
        provenance=PROVENANCE,
    )
    descriptor = CapabilityDescriptor(
        capability_name="capability.alpha",
        version=VERSION,
        accepted_artifact_types=(),
        produced_artifact_types=("result",),
        determinism=DeterminismClassification.DETERMINISTIC,
    )
    envelope = CapabilityExecutionEnvelope(
        descriptor=descriptor,
        supported_objective_types=("property_estimation",),
        execution_targets=("local_simulator",),
    )
    return construct_candidate_research_plan(
        objective=objective,
        constraints=PlanningConstraints(),
        facts=PlanningFactSet(),
        resources=resources,
        catalogue=ScientificCapabilityCatalog((envelope,)),
        provenance=PROVENANCE,
    )


def _principal(tenant: str = "tenant-alpha") -> AuthenticatedPrincipal:
    actions = tuple(action.value for action in sorted(ProtectedAction, key=str))
    return AuthenticatedPrincipal(
        subject_identifier="scientist-alpha",
        tenant_identifier=tenant,
        authentication_method="test-bearer",
        scopes=actions,
        roles=("scientist",),
        audit_identifier="scientist-alpha",
    )


def _security(tenant: str = "tenant-alpha", *, grant: bool = True) -> SecurityServices:
    principal = _principal(tenant)
    grants: tuple[ResourceGrant, ...] = ()
    if grant:
        grants = tuple(
            ResourceGrant(
                tenant_identifier=tenant,
                subject_identifier=principal.subject_identifier,
                resource_type=resource_type,
                resource_identifier="*",
                allowed_actions=tuple(sorted(ProtectedAction, key=str)),
            )
            for resource_type in ("workflow", "workflow-run")
        )
    return SecurityServices(
        authenticator=StaticAuthenticator({TOKEN: principal}),
        grant_provider=InMemoryGrantProvider(grants, allow_wildcards=True),
        audit_sink=InMemoryAuditSink(),
    )


def _workflow_service(root: Path) -> WorkflowService:
    def handler(invocation: CapabilityInvocation) -> CapabilityInvocationResult:
        return CapabilityInvocationResult(
            invocation_identifier=invocation.invocation_identifier,
            status=CapabilityInvocationStatus.SUCCEEDED,
            output_values=FrozenDict({"value": 7.0}),
            verification_classification="verified",
            actual_cost=0.0,
        )

    return WorkflowService(
        root=root,
        capability_adapter=CapabilityAdapterRegistry({"capability.alpha": handler}),
        maximum_parallelism=2,
    )


def _app(tmp_path: Path, *, tenant: str = "tenant-alpha", grant: bool = True):
    return create_app(
        coordinator=RunCoordinator(
            run_root=tmp_path / "runs",
            manifest_resolver=_load_preset,
            executor=_NoExecution(),
            enabled=False,
        ),
        experiment_store=ExperimentStore(tmp_path / "experiments"),
        natural_language_store=NaturalLanguageInterpretationStore(
            tmp_path / "interpretations",
            None,
            unavailable_reason="disabled for workflow API tests",
        ),
        security_services=_security(tenant, grant=grant),
        workflow_service=_workflow_service(tmp_path / "workflows"),
    )


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}", "X-Correlation-ID": "correlation-api"}


def test_complete_workflow_api_lifecycle_is_authenticated_and_audited(tmp_path: Path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        compiled = client.post(
            "/api/v1/workflows/compile",
            headers=_headers(),
            json={
                "plan": _plan().model_dump(mode="json"),
                "graph_identifier": "workflow.api",
                "graph_version": 1,
            },
        )
        assert compiled.status_code == 200, compiled.text
        graph = compiled.json()
        assert graph["graph_identifier"] == "workflow.api"
        assert graph["metadata"]["tenant_identifier"] == "tenant-alpha"
        assert graph["metadata"]["molecular_system_identifiers"] == ["system.api"]
        assert graph["metadata"]["unresolved_requirement_identifiers"] == []

        validation = client.post(
            "/api/v1/workflows/workflow.api/versions/1/validate",
            headers=_headers(),
        )
        assert validation.status_code == 200
        assert validation.json()["valid"] is True

        created = client.post(
            "/api/v1/workflow-runs",
            headers=_headers(),
            json={
                "graph_identifier": "workflow.api",
                "graph_version": 1,
                "graph_run_identifier": "workflow-run.api",
            },
        )
        assert created.status_code == 200, created.text
        assert created.json()["graph_state"]["status"] in {"ready", "awaiting_approval"}

        resumed = client.post(
            "/api/v1/workflow-runs/workflow-run.api/resume",
            headers=_headers(),
            json={"maximum_cycles": 10},
        )
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["graph_state"]["status"] == "succeeded"

        nodes = client.get(
            "/api/v1/workflow-runs/workflow-run.api/nodes", headers=_headers()
        )
        assert nodes.status_code == 200
        assert nodes.json()["nodes"][0]["status"] == "succeeded"

        evidence = client.get(
            "/api/v1/workflow-runs/workflow-run.api/evidence", headers=_headers()
        )
        assert evidence.status_code == 200
        assert "snapshot_fingerprint" in evidence.json()
        assert "storage_location" not in evidence.text

    audit = app.state.security_services.audit_sink.records
    actions = {record.action for record in audit}
    assert ProtectedAction.WORKFLOW_CREATE in actions
    assert ProtectedAction.WORKFLOW_EXECUTE in actions
    assert ProtectedAction.WORKFLOW_READ in actions


def test_workflow_routes_fail_closed_without_credentials_or_grants(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path, grant=False)) as client:
        missing = client.get("/api/v1/workflow-runs/unknown")
        denied = client.get(
            "/api/v1/workflow-runs/unknown", headers=_headers()
        )
    assert missing.status_code == 401
    assert denied.status_code == 403


def test_cross_tenant_workflow_lookup_is_enumeration_resistant(tmp_path: Path) -> None:
    shared_root = tmp_path / "workflows"
    first_service = _workflow_service(shared_root)
    first_service.start()
    graph = first_service.compiler.compile(
        _plan(),
        created_by_principal="scientist-alpha",
        tenant_identifier="tenant-alpha",
        graph_identifier="workflow.private",
    )
    first_service.graph_repository.put(graph)
    first_service.close()

    app = create_app(
        coordinator=RunCoordinator(
            run_root=tmp_path / "runs-beta",
            manifest_resolver=_load_preset,
            executor=_NoExecution(),
            enabled=False,
        ),
        experiment_store=ExperimentStore(tmp_path / "experiments-beta"),
        natural_language_store=NaturalLanguageInterpretationStore(
            tmp_path / "interpretations-beta",
            None,
            unavailable_reason="disabled for workflow API tests",
        ),
        security_services=_security("tenant-beta"),
        workflow_service=_workflow_service(shared_root),
    )
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/workflows/workflow.private/versions/1", headers=_headers()
        )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "workflow_not_found"



def test_workflow_approval_and_cancellation_routes_are_bound_and_audited(
    tmp_path: Path,
) -> None:
    workflow_service = _workflow_service(tmp_path / "workflows-controlled")
    workflow_service.start()
    approval = ApprovalRequirement(
        approval_identifier="approval.api",
        graph_identifier="workflow.controlled",
        graph_version=1,
        node_identifier="node.controlled",
        operation_identity="workflow.node.execute",
        capability_identity="capability.alpha",
    )
    workflow_service.graph_repository.put(
        WorkflowGraphDefinition(
            graph_identifier="workflow.controlled",
            version=1,
            nodes=(
                WorkflowNode(
                    node_identifier="node.controlled",
                    node_kind=NodeKind.CLASSICAL,
                    capability_identity="capability.alpha",
                    execution_policy=ExecutionPolicy(
                        requires_approval=True,
                        approval_requirements=(approval,),
                    ),
                ),
            ),
            root_node_ids=("node.controlled",),
            terminal_node_ids=("node.controlled",),
            metadata=WorkflowGraphMetadata(tenant_identifier="tenant-alpha"),
        )
    )
    workflow_service.close()
    app = create_app(
        coordinator=RunCoordinator(
            run_root=tmp_path / "runs-controlled",
            manifest_resolver=_load_preset,
            executor=_NoExecution(),
            enabled=False,
        ),
        experiment_store=ExperimentStore(tmp_path / "experiments-controlled"),
        natural_language_store=NaturalLanguageInterpretationStore(
            tmp_path / "interpretations-controlled",
            None,
            unavailable_reason="disabled for workflow API tests",
        ),
        security_services=_security(),
        workflow_service=workflow_service,
    )
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/workflow-runs",
            headers=_headers(),
            json={
                "graph_identifier": "workflow.controlled",
                "graph_version": 1,
                "graph_run_identifier": "workflow-run.controlled",
            },
        )
        assert created.status_code == 200
        assert created.json()["graph_state"]["status"] == "awaiting_approval"

        approved = client.post(
            "/api/v1/workflow-runs/workflow-run.controlled/approvals/approval.api",
            headers=_headers(),
            json={"granted": True, "decision_reason": "Scientist approved execution."},
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["approval_states"][0]["status"] == "granted"

        completed = client.post(
            "/api/v1/workflow-runs/workflow-run.controlled/resume",
            headers=_headers(),
            json={"maximum_cycles": 10},
        )
        assert completed.status_code == 200
        assert completed.json()["graph_state"]["status"] == "succeeded"

        second = client.post(
            "/api/v1/workflow-runs",
            headers=_headers(),
            json={
                "graph_identifier": "workflow.controlled",
                "graph_version": 1,
                "graph_run_identifier": "workflow-run.cancel-api",
            },
        )
        assert second.status_code == 200
        cancelled = client.post(
            "/api/v1/workflow-runs/workflow-run.cancel-api/cancel",
            headers=_headers(),
            json={"reason": "Operator cancelled before approval."},
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["graph_state"]["status"] == "cancelled"

    actions = {record.action for record in app.state.security_services.audit_sink.records}
    assert ProtectedAction.WORKFLOW_APPROVE in actions
    assert ProtectedAction.WORKFLOW_CANCEL in actions


def test_workflow_mutation_requests_are_json_bounded_and_strict(tmp_path: Path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        wrong_media_type = client.post(
            "/api/v1/workflow-runs/unknown/resume",
            headers={**_headers(), "Content-Type": "text/plain"},
            content="{}",
        )
        malformed = client.post(
            "/api/v1/workflow-runs/unknown/cancel",
            headers=_headers(),
            json={"reason": None, "unexpected": True},
        )
        oversized = client.post(
            "/api/v1/workflow-runs/unknown/approvals/approval.unknown",
            headers={
                **_headers(),
                "Content-Type": "application/json",
                "Content-Length": str(8 * 1024 * 1024 + 1),
            },
            content=b"{}",
        )
    assert wrong_media_type.status_code == 415
    assert wrong_media_type.json()["detail"]["code"] == "invalid_workflow_request"
    assert malformed.status_code == 422
    assert malformed.json()["detail"]["code"] == "invalid_workflow_request"
    assert oversized.status_code == 413
    assert oversized.json()["detail"]["code"] == "workflow_request_too_large"
