"""Phase 4 compiler, persistence, adapter and orchestration tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import threading
from typing import Any

import pytest

from cgr.kernel.contracts import CapabilityVersion
from cgr.science.artifacts import CreationProvenance
from cgr.science.capabilities import (
    CapabilityExecutionEnvelope,
    ScientificCapabilityCatalog,
)
from cgr.science.contracts import CapabilityDescriptor, DeterminismClassification
from cgr.science.feasibility import construct_candidate_research_plan
from cgr.science.planning import (
    CandidatePlanAssignment,
    CandidateResearchPlan,
    CapabilityFeasibilityResult,
    FeasibilityStatus,
    PlanningConstraints,
    PlanningFactSet,
    ScientificObjective,
)
from cgr.science.resources import ResourceAvailabilitySnapshot
from cgr.workflow_graph.capability_adapter import (
    CapabilityAdapterRegistry,
    CapabilityInvocation,
    CapabilityInvocationResult,
    CapabilityInvocationStatus,
)
from cgr.workflow_graph.compiler import WorkflowCompilationError, WorkflowGraphCompiler
from cgr.workflow_graph.contracts import (
    ApprovalRequirement,
    ComparisonGroup,
    ComparisonKind,
    ConditionKind,
    ConditionalEdge,
    CostCeiling,
    EdgeKind,
    ExecutionPolicy,
    FailureAction,
    FailureRecoveryPolicy,
    FrozenDict,
    InputPort,
    NodeKind,
    OutputPort,
    ParameterSweep,
    PortBinding,
    PortType,
    RetryPolicy,
    QuantumEscalationPolicy,
    WorkflowEdge,
    WorkflowGraphDefinition,
    WorkflowNode,
)
from cgr.workflow_graph.orchestrator import WorkflowOrchestrator
from cgr.workflow_graph.repository import (
    GraphRunRepository,
    WorkflowGraphRepository,
    WorkflowRepositoryConflictError,
    WorkflowRepositoryIntegrityError,
)
from cgr.workflow_graph.validation import WorkflowGraphValidator
from cgr.workflow_graph.state import (
    ApprovalStatus,
    DecisionKind,
    GraphStatus,
    NodeStatus,
)

VERSION = CapabilityVersion(major=1, minor=0, patch=0)
PROVENANCE = CreationProvenance(producer="workflow-tests", producer_version=VERSION)


def _plan() -> Any:
    objective = ScientificObjective(
        objective_identifier="objective.one",
        schema_version=VERSION,
        objective_type="property_estimation",
        original_description="Estimate a property.",
        normalized_description="Estimate a property.",
        project_identifier="project.one",
        molecular_system_identifiers=("system.one",),
        structure_identifiers=("structure.one",),
        provenance=PROVENANCE,
    )
    resources = ResourceAvailabilitySnapshot(
        snapshot_identifier="resources.one",
        schema_version=VERSION,
        observed_at=datetime(2026, 1, 1, tzinfo=UTC),
        execution_environment="test",
        available_execution_targets=("local_simulator",),
        provenance=PROVENANCE,
    )
    descriptor = CapabilityDescriptor(
        capability_name="capability.alpha",
        version=VERSION,
        accepted_artifact_types=("structure",),
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


def _assignment(
    *,
    identifier: str,
    capability: str,
    inputs: tuple[str, ...],
    outputs: tuple[str, ...],
) -> CandidatePlanAssignment:
    feasibility = CapabilityFeasibilityResult(
        evaluation_identifier=f"evaluation.{identifier}",
        objective_identifier="objective.one",
        capability_name=capability,
        capability_version=VERSION,
        status=FeasibilityStatus.FEASIBLE,
        execution_target="local_simulator",
    )
    return CandidatePlanAssignment(
        assignment_identifier=identifier,
        objective_identifier="objective.one",
        capability_name=capability,
        capability_version=VERSION,
        execution_target="local_simulator",
        feasibility=feasibility,
        input_artifact_types=inputs,
        produced_artifact_types=outputs,
    )


def _plan_with_assignments(
    assignments: tuple[CandidatePlanAssignment, ...],
    *,
    approvals: tuple[str, ...] = (),
) -> CandidateResearchPlan:
    base = _plan()
    return CandidateResearchPlan.model_validate(
        {
            **base.model_dump(mode="json"),
            "selected_assignments": [
                item.model_dump(mode="json") for item in assignments
            ],
            "approval_requirements": approvals,
        }
    )


def _value_graph(
    *,
    approval: bool = False,
    retry: bool = False,
    cost_ceiling: float | None = None,
) -> WorkflowGraphDefinition:
    approval_requirement = ApprovalRequirement(
        approval_identifier="approval.node-a",
        graph_identifier="graph.linear",
        graph_version=1,
        node_identifier="node-a",
        operation_identity="workflow.execute",
        capability_identity="capability.a",
    )
    first = WorkflowNode(
        node_identifier="node-a",
        node_kind=NodeKind.CLASSICAL,
        capability_identity="capability.a",
        output_ports=(
            OutputPort(port_identifier="value-out", port_type=PortType.VALUE),
        ),
        execution_policy=(
            ExecutionPolicy(
                requires_approval=True,
                approval_requirements=(approval_requirement,),
            )
            if approval
            else None
        ),
        retry_policy=(
            RetryPolicy(max_attempts=2, retryable_error_codes=("temporary",))
            if retry
            else None
        ),
        failure_recovery_policy=(
            FailureRecoveryPolicy(
                on_failure=FailureAction.RETRY,
                retry_policy=RetryPolicy(
                    max_attempts=2, retryable_error_codes=("temporary",)
                ),
            )
            if retry
            else None
        ),
        estimated_cost=2.0 if cost_ceiling is not None else None,
        estimated_cost_currency="USD" if cost_ceiling is not None else None,
    )
    second = WorkflowNode(
        node_identifier="node-b",
        node_kind=NodeKind.CLASSICAL,
        capability_identity="capability.b",
        input_ports=(
            InputPort(port_identifier="value-in", port_type=PortType.VALUE),
        ),
        output_ports=(
            OutputPort(port_identifier="value-out", port_type=PortType.VALUE),
        ),
    )
    edge = WorkflowEdge(
        edge_identifier="edge-a-b",
        edge_kind=EdgeKind.DATA_FLOW,
        source_node_id="node-a",
        target_node_id="node-b",
        source_port_id="value-out",
        target_port_id="value-in",
    )
    binding = PortBinding(
        binding_identifier="binding-a-b",
        source_node_id="node-a",
        source_port_id="value-out",
        destination_node_id="node-b",
        destination_port_id="value-in",
    )
    return WorkflowGraphDefinition(
        graph_identifier="graph.linear",
        version=1,
        nodes=(first, second),
        edges=(edge,),
        bindings=(binding,),
        root_node_ids=("node-a",),
        terminal_node_ids=("node-b",),
        cost_ceilings=(
            (CostCeiling(currency="USD", amount=cost_ceiling),)
            if cost_ceiling is not None
            else ()
        ),
    )


def _repositories(root: Path) -> tuple[WorkflowGraphRepository, GraphRunRepository]:
    graph_repository = WorkflowGraphRepository(root)
    run_repository = GraphRunRepository(root)
    graph_repository.start()
    run_repository.start()
    return graph_repository, run_repository


def test_compiler_is_deterministic_and_non_executing() -> None:
    plan = _plan()
    compiler = WorkflowGraphCompiler()
    first = compiler.compile(plan, created_by_principal="scientist.one")
    second = compiler.compile(plan, created_by_principal="scientist.one")

    assert first.definition_fingerprint == second.definition_fingerprint
    assert first.metadata is not None
    assert first.metadata.objective_identifier == plan.objective_identifier
    assert first.metadata.molecular_project_identifier == plan.project_identifier
    assert first.metadata.molecular_system_identifiers == plan.molecular_system_identifiers
    assert first.metadata.candidate_plan_fingerprint == plan.fingerprint
    assert first.metadata.rejected_alternative_identifiers == tuple(
        item.alternative_identifier for item in plan.rejected_alternatives
    )
    assert (
        first.metadata.unresolved_requirement_identifiers
        == plan.unresolved_requirement_identifiers
    )
    assert first.metadata.unresolved_goal_identifiers == plan.unresolved_goal_identifiers
    assert first.nodes[0].capability_identity == "capability.alpha"
    assert first.nodes[0].node_kind is NodeKind.QUANTUM_LOCAL
    assert not hasattr(compiler, "execute")
    assert not hasattr(compiler, "submit")


def test_compiler_preserves_artifact_dependencies_and_parallel_roots() -> None:
    producer = _assignment(
        identifier="assignment.producer",
        capability="capability.producer",
        inputs=("structure",),
        outputs=("intermediate",),
    )
    consumer = _assignment(
        identifier="assignment.consumer",
        capability="capability.consumer",
        inputs=("intermediate",),
        outputs=("result",),
    )
    independent = _assignment(
        identifier="assignment.independent",
        capability="capability.independent",
        inputs=("structure",),
        outputs=("diagnostic",),
    )
    plan = _plan_with_assignments(
        (consumer, independent, producer), approvals=("approval.plan",)
    )

    graph = WorkflowGraphCompiler().compile(
        plan, created_by_principal="scientist.one"
    )
    by_capability = {item.capability_identity: item for item in graph.nodes}
    producer_id = by_capability["capability.producer"].node_identifier
    consumer_id = by_capability["capability.consumer"].node_identifier
    independent_id = by_capability["capability.independent"].node_identifier
    assert set(graph.root_node_ids) == {producer_id, independent_id}
    assert graph.terminal_node_ids == tuple(sorted((consumer_id, independent_id)))
    assert any(
        edge.source_node_id == producer_id and edge.target_node_id == consumer_id
        for edge in graph.edges
    )
    assert not any(
        independent_id in {edge.source_node_id, edge.target_node_id}
        for edge in graph.edges
    )
    assert graph.global_execution_policy is not None
    assert {
        item.approval_identifier
        for item in graph.global_execution_policy.approval_requirements
    } == {"approval.plan"}


def test_compiler_rejects_ambiguous_or_cyclic_artifact_flow() -> None:
    first = _assignment(
        identifier="assignment.first",
        capability="capability.first",
        inputs=(),
        outputs=("shared",),
    )
    second = _assignment(
        identifier="assignment.second",
        capability="capability.second",
        inputs=(),
        outputs=("shared",),
    )
    consumer = _assignment(
        identifier="assignment.consumer",
        capability="capability.consumer",
        inputs=("shared",),
        outputs=("result",),
    )
    with pytest.raises(WorkflowCompilationError, match="ambiguous"):
        WorkflowGraphCompiler().compile(
            _plan_with_assignments((first, second, consumer)),
            created_by_principal="scientist.one",
        )

    cycle_a = _assignment(
        identifier="assignment.cycle-a",
        capability="capability.cycle-a",
        inputs=("artifact-b",),
        outputs=("artifact-a",),
    )
    cycle_b = _assignment(
        identifier="assignment.cycle-b",
        capability="capability.cycle-b",
        inputs=("artifact-a",),
        outputs=("artifact-b",),
    )
    with pytest.raises(WorkflowCompilationError, match="directed cycle"):
        WorkflowGraphCompiler().compile(
            _plan_with_assignments((cycle_a, cycle_b)),
            created_by_principal="scientist.one",
        )


def test_graph_repository_is_immutable_and_detects_tampering(tmp_path: Path) -> None:
    graphs, _runs = _repositories(tmp_path)
    graph = _value_graph()
    assert graphs.put(graph) == graph
    assert graphs.put(graph) == graph

    conflicting = graph.model_copy(
        update={
            "nodes": (
                graph.nodes[0].model_copy(update={"capability_identity": "capability.other"}),
                graph.nodes[1],
            ),
            "definition_fingerprint": None,
        }
    )
    with pytest.raises(WorkflowRepositoryConflictError):
        graphs.put(conflicting)

    definition_path = next(tmp_path.glob("workflow-definitions/*/*/definition.json"))
    definition_path.write_text("{}", encoding="utf-8")
    with pytest.raises(WorkflowRepositoryIntegrityError):
        graphs.get(graph.graph_identifier, graph.version)


def test_adapter_registry_fails_closed_for_unknown_capability() -> None:
    registry = CapabilityAdapterRegistry()
    invocation = CapabilityInvocation(
        invocation_identifier="invocation.one",
        idempotency_identifier="idempotency.one",
        graph_run_identifier="run.one",
        graph_definition_fingerprint="a" * 64,
        node_identifier="node.one",
        attempt_number=1,
        capability_identity="capability.missing",
        node_kind=NodeKind.CLASSICAL,
    )
    result = registry.invoke(invocation)
    assert result.status is CapabilityInvocationStatus.BLOCKED
    assert result.error_code == "capability_unavailable"


def test_orchestrator_executes_linear_graph_and_transfers_value(tmp_path: Path) -> None:
    graphs, runs = _repositories(tmp_path)
    graph = graphs.put(_value_graph())
    observed: list[CapabilityInvocation] = []

    def handler(invocation: CapabilityInvocation) -> CapabilityInvocationResult:
        observed.append(invocation)
        if invocation.capability_identity == "capability.a":
            return CapabilityInvocationResult(
                invocation_identifier=invocation.invocation_identifier,
                status=CapabilityInvocationStatus.SUCCEEDED,
                output_values=FrozenDict({"value-out": 7}),
            )
        assert invocation.input_values["value-in"] == 7
        return CapabilityInvocationResult(
            invocation_identifier=invocation.invocation_identifier,
            status=CapabilityInvocationStatus.SUCCEEDED,
            output_values=FrozenDict({"value-out": 14}),
        )

    registry = CapabilityAdapterRegistry(
        {"capability.a": handler, "capability.b": handler}
    )
    orchestrator = WorkflowOrchestrator(
        graph_repository=graphs,
        run_repository=runs,
        capability_adapter=registry,
        clock=lambda: 100.0,
    )
    created = orchestrator.create_run(
        graph_identifier=graph.graph_identifier,
        graph_version=graph.version,
        graph_run_identifier="run.linear",
        tenant_identifier="tenant.one",
        created_by_principal="scientist.one",
    )
    completed = orchestrator.resume(created.graph_state.graph_run_identifier)

    assert completed.graph_state.status is GraphStatus.SUCCEEDED
    latest = {state.node_id: state for state in completed.node_states}
    assert latest["node-a"].status is NodeStatus.SUCCEEDED
    assert latest["node-b"].output_values["value-out"] == 14
    assert len(observed) == 2
    assert completed.binding_states[0].value_transferred


def test_orchestrator_pauses_for_bound_approval(tmp_path: Path) -> None:
    graphs, runs = _repositories(tmp_path)
    graph = graphs.put(_value_graph(approval=True))
    registry = CapabilityAdapterRegistry(
        {
            "capability.a": lambda invocation: CapabilityInvocationResult(
                invocation_identifier=invocation.invocation_identifier,
                status=CapabilityInvocationStatus.SUCCEEDED,
                output_values=FrozenDict({"value-out": 1}),
            ),
            "capability.b": lambda invocation: CapabilityInvocationResult(
                invocation_identifier=invocation.invocation_identifier,
                status=CapabilityInvocationStatus.SUCCEEDED,
                output_values=FrozenDict({"value-out": 2}),
            ),
        }
    )
    orchestrator = WorkflowOrchestrator(
        graph_repository=graphs,
        run_repository=runs,
        capability_adapter=registry,
        clock=lambda: 100.0,
    )
    created = orchestrator.create_run(
        graph_identifier=graph.graph_identifier,
        graph_version=1,
        graph_run_identifier="run.approval",
        tenant_identifier="tenant.one",
        created_by_principal="scientist.one",
    )
    assert created.graph_state.status is GraphStatus.AWAITING_APPROVAL
    assert created.approval_states[0].status is ApprovalStatus.PENDING

    approved = orchestrator.approve(
        graph_run_identifier="run.approval",
        approval_identifier="approval.node-a",
        approver_principal="scientist.approver",
        granted=True,
        correlation_identifier="correlation.one",
    )
    assert approved.approval_states[0].status is ApprovalStatus.GRANTED
    completed = orchestrator.resume("run.approval")
    assert completed.graph_state.status is GraphStatus.SUCCEEDED
    assert any(
        decision.decision_kind is DecisionKind.APPROVAL
        for decision in completed.decision_states
    )


def test_orchestrator_retries_once_and_preserves_attempt_history(tmp_path: Path) -> None:
    graphs, runs = _repositories(tmp_path)
    graph = graphs.put(_value_graph(retry=True))
    attempts = 0

    def first(invocation: CapabilityInvocation) -> CapabilityInvocationResult:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return CapabilityInvocationResult(
                invocation_identifier=invocation.invocation_identifier,
                status=CapabilityInvocationStatus.FAILED,
                retryable=True,
                error_code="temporary",
                error_message="Temporary failure.",
            )
        return CapabilityInvocationResult(
            invocation_identifier=invocation.invocation_identifier,
            status=CapabilityInvocationStatus.SUCCEEDED,
            output_values=FrozenDict({"value-out": 1}),
        )

    registry = CapabilityAdapterRegistry(
        {
            "capability.a": first,
            "capability.b": lambda invocation: CapabilityInvocationResult(
                invocation_identifier=invocation.invocation_identifier,
                status=CapabilityInvocationStatus.SUCCEEDED,
                output_values=FrozenDict({"value-out": 2}),
            ),
        }
    )
    orchestrator = WorkflowOrchestrator(
        graph_repository=graphs,
        run_repository=runs,
        capability_adapter=registry,
        clock=lambda: 100.0,
    )
    orchestrator.create_run(
        graph_identifier=graph.graph_identifier,
        graph_version=1,
        graph_run_identifier="run.retry",
        tenant_identifier="tenant.one",
        created_by_principal="scientist.one",
    )
    completed = orchestrator.resume("run.retry")

    assert completed.graph_state.status is GraphStatus.SUCCEEDED
    node_a_attempts = [item for item in completed.node_states if item.node_id == "node-a"]
    assert [item.attempt_number for item in node_a_attempts] == [1, 2]
    assert node_a_attempts[0].status is NodeStatus.FAILED
    assert node_a_attempts[1].status is NodeStatus.SUCCEEDED
    assert completed.retry_states[0].outcome is not None
    assert completed.retry_states[0].executed_at_epoch is not None


def test_orchestrator_blocks_before_execution_when_budget_would_be_exceeded(
    tmp_path: Path,
) -> None:
    graphs, runs = _repositories(tmp_path)
    graph = graphs.put(_value_graph(cost_ceiling=1.0))
    calls = 0

    def handler(invocation: CapabilityInvocation) -> CapabilityInvocationResult:
        nonlocal calls
        calls += 1
        return CapabilityInvocationResult(
            invocation_identifier=invocation.invocation_identifier,
            status=CapabilityInvocationStatus.SUCCEEDED,
        )

    registry = CapabilityAdapterRegistry(
        {"capability.a": handler, "capability.b": handler}
    )
    orchestrator = WorkflowOrchestrator(
        graph_repository=graphs,
        run_repository=runs,
        capability_adapter=registry,
        clock=lambda: 100.0,
    )
    orchestrator.create_run(
        graph_identifier=graph.graph_identifier,
        graph_version=1,
        graph_run_identifier="run.budget",
        tenant_identifier="tenant.one",
        created_by_principal="scientist.one",
    )
    completed = orchestrator.resume("run.budget")

    assert completed.graph_state.status is GraphStatus.BLOCKED
    assert calls == 0
    assert any(
        decision.decision_kind is DecisionKind.BUDGET
        for decision in completed.decision_states
    )


def test_parameter_sweep_expands_deterministically(tmp_path: Path) -> None:
    graphs, runs = _repositories(tmp_path)
    node = WorkflowNode(
        node_identifier="node-sweep",
        node_kind=NodeKind.CLASSICAL,
        capability_identity="capability.sweep",
    )
    graph = WorkflowGraphDefinition(
        graph_identifier="graph.sweep",
        version=1,
        nodes=(node,),
        root_node_ids=("node-sweep",),
        terminal_node_ids=("node-sweep",),
        parameter_sweeps=(
            ParameterSweep(
                sweep_identifier="sweep.one",
                base_node_id="node-sweep",
                parameter_name="alpha",
                parameter_values=(2, 1),
            ),
        ),
    )
    graphs.put(graph)
    seen: list[Any] = []

    def handler(invocation: CapabilityInvocation) -> CapabilityInvocationResult:
        seen.append(invocation.parameters["alpha"])
        return CapabilityInvocationResult(
            invocation_identifier=invocation.invocation_identifier,
            status=CapabilityInvocationStatus.SUCCEEDED,
        )

    orchestrator = WorkflowOrchestrator(
        graph_repository=graphs,
        run_repository=runs,
        capability_adapter=CapabilityAdapterRegistry({"capability.sweep": handler}),
        maximum_parallelism=2,
        clock=lambda: 100.0,
    )
    created = orchestrator.create_run(
        graph_identifier="graph.sweep",
        graph_version=1,
        graph_run_identifier="run.sweep",
        tenant_identifier="tenant.one",
        created_by_principal="scientist.one",
    )
    completed = orchestrator.resume(created.graph_state.graph_run_identifier)

    assert completed.graph_state.status is GraphStatus.SUCCEEDED
    assert sorted(seen) == [1, 2]
    assert len({item.node_id for item in completed.node_states}) == 2
    assert any(
        decision.decision_kind is DecisionKind.SWEEP_EXPANSION
        for decision in completed.decision_states
    )


def test_condition_false_skips_target_and_records_decision(tmp_path: Path) -> None:
    graphs, runs = _repositories(tmp_path)
    first = WorkflowNode(
        node_identifier="node-a",
        node_kind=NodeKind.CLASSICAL,
        capability_identity="capability.a",
    )
    second = WorkflowNode(
        node_identifier="node-b",
        node_kind=NodeKind.CLASSICAL,
        capability_identity="capability.b",
    )
    condition = ConditionalEdge(
        edge_identifier="edge-condition",
        source_node_id="node-a",
        target_node_id="node-b",
        condition_kind=ConditionKind.NUMERIC_COMPARE,
        condition_expression=FrozenDict(
            {"source": "output.score", "operator": "gt", "threshold": 10}
        ),
    )
    graph = WorkflowGraphDefinition(
        graph_identifier="graph.condition",
        version=1,
        nodes=(first, second),
        edges=(
            WorkflowEdge(
                edge_identifier="edge-condition",
                edge_kind=EdgeKind.CONDITIONAL,
                source_node_id="node-a",
                target_node_id="node-b",
                condition=condition,
            ),
        ),
        root_node_ids=("node-a",),
        terminal_node_ids=("node-b",),
    )
    graphs.put(graph)
    calls: list[str] = []

    def handler(invocation: CapabilityInvocation) -> CapabilityInvocationResult:
        calls.append(invocation.capability_identity)
        return CapabilityInvocationResult(
            invocation_identifier=invocation.invocation_identifier,
            status=CapabilityInvocationStatus.SUCCEEDED,
            output_values=FrozenDict({"score": 5}),
        )

    orchestrator = WorkflowOrchestrator(
        graph_repository=graphs,
        run_repository=runs,
        capability_adapter=CapabilityAdapterRegistry(
            {"capability.a": handler, "capability.b": handler}
        ),
        clock=lambda: 100.0,
    )
    orchestrator.create_run(
        graph_identifier="graph.condition",
        graph_version=1,
        graph_run_identifier="run.condition",
        tenant_identifier="tenant.one",
        created_by_principal="scientist.one",
    )
    completed = orchestrator.resume("run.condition")

    latest = {item.node_id: item for item in completed.node_states}
    assert completed.graph_state.status is GraphStatus.SUCCEEDED
    assert latest["node-b"].status is NodeStatus.SKIPPED_BY_CONDITION
    assert calls == ["capability.a"]
    assert any(
        decision.decision_kind is DecisionKind.CONDITION
        and decision.outcome == "false"
        for decision in completed.decision_states
    )


def test_comparison_selects_declared_metric_and_records_decision(tmp_path: Path) -> None:
    graphs, runs = _repositories(tmp_path)
    members = tuple(
        WorkflowNode(
            node_identifier=f"node-{name}",
            node_kind=NodeKind.CLASSICAL,
            capability_identity=f"capability.{name}",
        )
        for name in ("a", "b")
    )
    aggregate = WorkflowNode(
        node_identifier="node-select",
        node_kind=NodeKind.COMPARISON,
    )
    graph = WorkflowGraphDefinition(
        graph_identifier="graph.compare",
        version=1,
        nodes=(*members, aggregate),
        edges=(
            WorkflowEdge(
                edge_identifier="edge-a-select",
                edge_kind=EdgeKind.DEPENDENCY,
                source_node_id="node-a",
                target_node_id="node-select",
            ),
            WorkflowEdge(
                edge_identifier="edge-b-select",
                edge_kind=EdgeKind.DEPENDENCY,
                source_node_id="node-b",
                target_node_id="node-select",
            ),
        ),
        root_node_ids=("node-a", "node-b"),
        terminal_node_ids=("node-select",),
        comparison_groups=(
            ComparisonGroup(
                group_identifier="comparison.one",
                member_node_ids=("node-a", "node-b"),
                aggregation_node_id="node-select",
                comparison_kind=ComparisonKind.BEST_SUCCESS,
                selection_criteria=FrozenDict({"metric": "score", "direction": "max"}),
            ),
        ),
    )
    graphs.put(graph)

    def handler(invocation: CapabilityInvocation) -> CapabilityInvocationResult:
        score = 1 if invocation.capability_identity.endswith("a") else 2
        return CapabilityInvocationResult(
            invocation_identifier=invocation.invocation_identifier,
            status=CapabilityInvocationStatus.SUCCEEDED,
            output_values=FrozenDict({"score": score}),
        )

    orchestrator = WorkflowOrchestrator(
        graph_repository=graphs,
        run_repository=runs,
        capability_adapter=CapabilityAdapterRegistry(
            {"capability.a": handler, "capability.b": handler}
        ),
        maximum_parallelism=2,
        clock=lambda: 100.0,
    )
    orchestrator.create_run(
        graph_identifier="graph.compare",
        graph_version=1,
        graph_run_identifier="run.compare",
        tenant_identifier="tenant.one",
        created_by_principal="scientist.one",
    )
    completed = orchestrator.resume("run.compare")

    latest = {item.node_id: item for item in completed.node_states}
    assert latest["node-select"].output_values["selected_node_identifier"] == "node-b"
    assert any(
        decision.decision_kind is DecisionKind.COMPARISON
        for decision in completed.decision_states
    )


def test_quantum_ibm_node_never_invokes_adapter_without_policy_authorization(
    tmp_path: Path,
) -> None:
    graphs, runs = _repositories(tmp_path)
    node = WorkflowNode(
        node_identifier="node-ibm",
        node_kind=NodeKind.QUANTUM_IBM,
        capability_identity="capability.ibm",
        quantum_escalation_policy=QuantumEscalationPolicy(
            allow_ibm_submission=False
        ),
    )
    graph = WorkflowGraphDefinition(
        graph_identifier="graph.ibm",
        version=1,
        nodes=(node,),
        root_node_ids=("node-ibm",),
        terminal_node_ids=("node-ibm",),
    )
    graphs.put(graph)
    calls = 0

    def handler(invocation: CapabilityInvocation) -> CapabilityInvocationResult:
        nonlocal calls
        calls += 1
        return CapabilityInvocationResult(
            invocation_identifier=invocation.invocation_identifier,
            status=CapabilityInvocationStatus.SUCCEEDED,
        )

    orchestrator = WorkflowOrchestrator(
        graph_repository=graphs,
        run_repository=runs,
        capability_adapter=CapabilityAdapterRegistry({"capability.ibm": handler}),
        clock=lambda: 100.0,
    )
    orchestrator.create_run(
        graph_identifier="graph.ibm",
        graph_version=1,
        graph_run_identifier="run.ibm",
        tenant_identifier="tenant.one",
        created_by_principal="scientist.one",
    )
    completed = orchestrator.resume("run.ibm")
    assert completed.graph_state.status is GraphStatus.BLOCKED
    assert calls == 0


def test_failure_skip_policy_preserves_independent_progress(tmp_path: Path) -> None:
    graphs, runs = _repositories(tmp_path)
    failing = WorkflowNode(
        node_identifier="node-fail",
        node_kind=NodeKind.CLASSICAL,
        capability_identity="capability.fail",
        failure_recovery_policy=FailureRecoveryPolicy(on_failure=FailureAction.SKIP),
    )
    downstream = WorkflowNode(
        node_identifier="node-after",
        node_kind=NodeKind.CLASSICAL,
        capability_identity="capability.after",
    )
    edge = WorkflowEdge(
        edge_identifier="edge-fail-after",
        edge_kind=EdgeKind.DEPENDENCY,
        source_node_id="node-fail",
        target_node_id="node-after",
    )
    graph = WorkflowGraphDefinition(
        graph_identifier="graph.skip",
        version=1,
        nodes=(failing, downstream),
        edges=(edge,),
        root_node_ids=("node-fail",),
        terminal_node_ids=("node-after",),
    )
    graphs.put(graph)
    called: list[str] = []

    def handler(invocation: CapabilityInvocation) -> CapabilityInvocationResult:
        called.append(invocation.capability_identity)
        if invocation.capability_identity == "capability.fail":
            return CapabilityInvocationResult(
                invocation_identifier=invocation.invocation_identifier,
                status=CapabilityInvocationStatus.FAILED,
                error_code="scientific_failure",
                error_message="Candidate did not pass.",
            )
        return CapabilityInvocationResult(
            invocation_identifier=invocation.invocation_identifier,
            status=CapabilityInvocationStatus.SUCCEEDED,
        )

    orchestrator = WorkflowOrchestrator(
        graph_repository=graphs,
        run_repository=runs,
        capability_adapter=CapabilityAdapterRegistry(
            {"capability.fail": handler, "capability.after": handler}
        ),
        clock=lambda: 100.0,
    )
    orchestrator.create_run(
        graph_identifier="graph.skip",
        graph_version=1,
        graph_run_identifier="run.skip",
        tenant_identifier="tenant.one",
        created_by_principal="scientist.one",
    )
    completed = orchestrator.resume("run.skip")
    latest = {item.node_id: item for item in completed.node_states}
    assert completed.graph_state.status is GraphStatus.SUCCEEDED
    assert latest["node-fail"].status is NodeStatus.SKIPPED_BY_POLICY
    assert latest["node-after"].status is NodeStatus.SUCCEEDED
    assert called == ["capability.fail", "capability.after"]


def test_cancel_before_execution_is_persisted_without_adapter_call(tmp_path: Path) -> None:
    graphs, runs = _repositories(tmp_path)
    graph = graphs.put(_value_graph())
    calls = 0

    def handler(invocation: CapabilityInvocation) -> CapabilityInvocationResult:
        nonlocal calls
        calls += 1
        return CapabilityInvocationResult(
            invocation_identifier=invocation.invocation_identifier,
            status=CapabilityInvocationStatus.SUCCEEDED,
        )

    orchestrator = WorkflowOrchestrator(
        graph_repository=graphs,
        run_repository=runs,
        capability_adapter=CapabilityAdapterRegistry(
            {"capability.a": handler, "capability.b": handler}
        ),
        clock=lambda: 100.0,
    )
    orchestrator.create_run(
        graph_identifier=graph.graph_identifier,
        graph_version=1,
        graph_run_identifier="run.cancel",
        tenant_identifier="tenant.one",
        created_by_principal="scientist.one",
    )
    cancelled = orchestrator.cancel(
        graph_run_identifier="run.cancel",
        requested_by_principal="scientist.one",
    )
    assert cancelled.graph_state.status is GraphStatus.CANCELLED
    assert all(
        state.status is NodeStatus.CANCELLED for state in cancelled.node_states
    )
    assert calls == 0


def test_run_repository_rejects_stale_revision(tmp_path: Path) -> None:
    graphs, runs = _repositories(tmp_path)
    graph = graphs.put(_value_graph())
    orchestrator = WorkflowOrchestrator(
        graph_repository=graphs,
        run_repository=runs,
        capability_adapter=CapabilityAdapterRegistry(),
        clock=lambda: 100.0,
    )
    snapshot = orchestrator.create_run(
        graph_identifier=graph.graph_identifier,
        graph_version=1,
        graph_run_identifier="run.stale",
        tenant_identifier="tenant.one",
        created_by_principal="scientist.one",
    )
    revised = snapshot.model_copy(
        update={"revision": 1, "snapshot_fingerprint": None}
    )
    revised = type(snapshot).model_validate(
        revised.model_dump(mode="json", exclude={"snapshot_fingerprint"})
    )
    runs.update(revised, expected_revision=0)
    with pytest.raises(WorkflowRepositoryConflictError):
        runs.update(revised, expected_revision=0)


def test_reported_actual_cost_over_ceiling_blocks_run_and_records_decision(
    tmp_path: Path,
) -> None:
    graphs, runs = _repositories(tmp_path)
    graph = graphs.put(_value_graph(cost_ceiling=2.0))

    def handler(invocation: CapabilityInvocation) -> CapabilityInvocationResult:
        return CapabilityInvocationResult(
            invocation_identifier=invocation.invocation_identifier,
            status=CapabilityInvocationStatus.SUCCEEDED,
            actual_cost=3.0,
        )

    orchestrator = WorkflowOrchestrator(
        graph_repository=graphs,
        run_repository=runs,
        capability_adapter=CapabilityAdapterRegistry(
            {"capability.a": handler, "capability.b": handler}
        ),
        clock=lambda: 100.0,
    )
    orchestrator.create_run(
        graph_identifier=graph.graph_identifier,
        graph_version=1,
        graph_run_identifier="run.actual-over-budget",
        tenant_identifier="tenant.one",
        created_by_principal="scientist.one",
    )
    completed = orchestrator.resume("run.actual-over-budget")
    latest = {item.node_id: item for item in completed.node_states}
    assert completed.graph_state.status is GraphStatus.BLOCKED
    assert completed.budget_state.terminal_over_budget
    assert latest["node-a"].status is NodeStatus.BLOCKED
    assert latest["node-a"].error_code == "actual_budget_exceeded"
    assert any(
        decision.decision_kind is DecisionKind.BUDGET
        and decision.outcome == "actual_over_budget"
        for decision in completed.decision_states
    )


def test_retry_backoff_persists_not_before_time(tmp_path: Path) -> None:
    graphs, runs = _repositories(tmp_path)
    base = _value_graph(retry=True)
    delayed = RetryPolicy(
        max_attempts=2,
        retry_delay_seconds=10.0,
        retryable_error_codes=("temporary",),
    )
    first = base.nodes[0].model_copy(
        update={
            "retry_policy": delayed,
            "failure_recovery_policy": FailureRecoveryPolicy(
                on_failure=FailureAction.RETRY,
                retry_policy=delayed,
            ),
        }
    )
    graph = WorkflowGraphDefinition.model_validate(
        {
            **base.model_dump(mode="json", exclude={"definition_fingerprint"}),
            "nodes": (first, base.nodes[1]),
            "definition_fingerprint": None,
        }
    )
    graphs.put(graph)
    now = [100.0]
    calls = 0

    def handler(invocation: CapabilityInvocation) -> CapabilityInvocationResult:
        nonlocal calls
        calls += 1
        if invocation.capability_identity == "capability.a" and calls == 1:
            return CapabilityInvocationResult(
                invocation_identifier=invocation.invocation_identifier,
                status=CapabilityInvocationStatus.FAILED,
                retryable=True,
                error_code="temporary",
                error_message="Try again later.",
            )
        return CapabilityInvocationResult(
            invocation_identifier=invocation.invocation_identifier,
            status=CapabilityInvocationStatus.SUCCEEDED,
        )

    orchestrator = WorkflowOrchestrator(
        graph_repository=graphs,
        run_repository=runs,
        capability_adapter=CapabilityAdapterRegistry(
            {"capability.a": handler, "capability.b": handler}
        ),
        clock=lambda: now[0],
    )
    orchestrator.create_run(
        graph_identifier=graph.graph_identifier,
        graph_version=1,
        graph_run_identifier="run.delayed-retry",
        tenant_identifier="tenant.one",
        created_by_principal="scientist.one",
    )
    paused = orchestrator.resume("run.delayed-retry")
    assert paused.retry_states[0].scheduled_at_epoch == 110.0
    assert calls == 1

    now[0] = 109.0
    still_paused = orchestrator.resume("run.delayed-retry")
    assert still_paused.snapshot_fingerprint == paused.snapshot_fingerprint
    assert calls == 1

    now[0] = 110.0
    completed = orchestrator.resume("run.delayed-retry")
    assert completed.graph_state.status is GraphStatus.SUCCEEDED
    assert calls == 3


def test_persisted_partial_run_resumes_without_duplicate_successful_work(
    tmp_path: Path,
) -> None:
    graphs, runs = _repositories(tmp_path)
    graph = graphs.put(_value_graph())
    calls: list[str] = []

    def handler(invocation: CapabilityInvocation) -> CapabilityInvocationResult:
        calls.append(invocation.capability_identity)
        return CapabilityInvocationResult(
            invocation_identifier=invocation.invocation_identifier,
            status=CapabilityInvocationStatus.SUCCEEDED,
            output_values=FrozenDict({"value": len(calls)}),
        )

    adapter = CapabilityAdapterRegistry(
        {"capability.a": handler, "capability.b": handler}
    )
    first_process = WorkflowOrchestrator(
        graph_repository=graphs,
        run_repository=runs,
        capability_adapter=adapter,
        maximum_parallelism=1,
        clock=lambda: 100.0,
    )
    first_process.create_run(
        graph_identifier=graph.graph_identifier,
        graph_version=1,
        graph_run_identifier="run.restart",
        tenant_identifier="tenant.one",
        created_by_principal="scientist.one",
    )
    with pytest.raises(Exception, match="bounded cycle limit"):
        first_process.resume("run.restart", maximum_cycles=1)
    assert calls == ["capability.a"]

    restarted = WorkflowOrchestrator(
        graph_repository=graphs,
        run_repository=runs,
        capability_adapter=adapter,
        maximum_parallelism=1,
        clock=lambda: 101.0,
    )
    completed = restarted.resume("run.restart")
    assert completed.graph_state.status is GraphStatus.SUCCEEDED
    assert calls == ["capability.a", "capability.b"]


def test_cancellation_records_principal_correlation_and_reason(tmp_path: Path) -> None:
    graphs, runs = _repositories(tmp_path)
    graph = graphs.put(_value_graph())
    orchestrator = WorkflowOrchestrator(
        graph_repository=graphs,
        run_repository=runs,
        capability_adapter=CapabilityAdapterRegistry(),
        clock=lambda: 100.0,
    )
    orchestrator.create_run(
        graph_identifier=graph.graph_identifier,
        graph_version=1,
        graph_run_identifier="run.cancel-evidence",
        tenant_identifier="tenant.one",
        created_by_principal="scientist.one",
    )
    cancelled = orchestrator.cancel(
        graph_run_identifier="run.cancel-evidence",
        requested_by_principal="scientist.one",
        correlation_identifier="correlation.one",
        reason="Operator requested cancellation.",
    )
    decision = cancelled.decision_states[-1]
    assert decision.decision_kind is DecisionKind.CANCELLATION
    assert decision.metadata["requested_by_principal"] == "scientist.one"
    assert decision.metadata["correlation_identifier"] == "correlation.one"
    assert decision.metadata["reason"] == "Operator requested cancellation."



def test_independent_ready_nodes_execute_with_bounded_parallelism(tmp_path: Path) -> None:
    graphs, runs = _repositories(tmp_path)
    nodes = tuple(
        WorkflowNode(
            node_identifier=f"node-{suffix}",
            node_kind=NodeKind.CLASSICAL,
            capability_identity=f"capability.{suffix}",
        )
        for suffix in ("a", "b")
    )
    graph = WorkflowGraphDefinition(
        graph_identifier="graph.parallel",
        version=1,
        nodes=nodes,
        root_node_ids=("node-a", "node-b"),
        terminal_node_ids=("node-a", "node-b"),
        global_execution_policy=ExecutionPolicy(concurrency_limit=2),
    )
    graphs.put(graph)
    barrier = threading.Barrier(2, timeout=2.0)
    calls: list[str] = []
    lock = threading.Lock()

    def handler(invocation: CapabilityInvocation) -> CapabilityInvocationResult:
        with lock:
            calls.append(invocation.capability_identity)
        barrier.wait()
        return CapabilityInvocationResult(
            invocation_identifier=invocation.invocation_identifier,
            status=CapabilityInvocationStatus.SUCCEEDED,
        )

    orchestrator = WorkflowOrchestrator(
        graph_repository=graphs,
        run_repository=runs,
        capability_adapter=CapabilityAdapterRegistry(
            {"capability.a": handler, "capability.b": handler}
        ),
        maximum_parallelism=2,
        clock=lambda: 100.0,
    )
    orchestrator.create_run(
        graph_identifier=graph.graph_identifier,
        graph_version=1,
        graph_run_identifier="run.parallel",
        tenant_identifier="tenant.one",
        created_by_principal="scientist.one",
    )
    completed = orchestrator.resume("run.parallel")
    assert completed.graph_state.status is GraphStatus.SUCCEEDED
    assert sorted(calls) == ["capability.a", "capability.b"]


def test_fallback_policy_activates_declared_successor(tmp_path: Path) -> None:
    graphs, runs = _repositories(tmp_path)
    failing = WorkflowNode(
        node_identifier="node-primary",
        node_kind=NodeKind.CLASSICAL,
        capability_identity="capability.primary",
        failure_recovery_policy=FailureRecoveryPolicy(
            on_failure=FailureAction.FALLBACK,
            fallback_node_id="node-fallback",
        ),
    )
    fallback = WorkflowNode(
        node_identifier="node-fallback",
        node_kind=NodeKind.CLASSICAL,
        capability_identity="capability.fallback",
    )
    graph = WorkflowGraphDefinition(
        graph_identifier="graph.fallback-runtime",
        version=1,
        nodes=(failing, fallback),
        edges=(
            WorkflowEdge(
                edge_identifier="edge-primary-fallback",
                edge_kind=EdgeKind.DEPENDENCY,
                source_node_id="node-primary",
                target_node_id="node-fallback",
            ),
        ),
        root_node_ids=("node-primary",),
        terminal_node_ids=("node-fallback",),
    )
    assert WorkflowGraphValidator(graph).validate().valid
    graphs.put(graph)
    calls: list[str] = []

    def handler(invocation: CapabilityInvocation) -> CapabilityInvocationResult:
        calls.append(invocation.capability_identity)
        if invocation.capability_identity == "capability.primary":
            return CapabilityInvocationResult(
                invocation_identifier=invocation.invocation_identifier,
                status=CapabilityInvocationStatus.FAILED,
                error_code="primary_failed",
                error_message="Primary route failed.",
            )
        return CapabilityInvocationResult(
            invocation_identifier=invocation.invocation_identifier,
            status=CapabilityInvocationStatus.SUCCEEDED,
        )

    orchestrator = WorkflowOrchestrator(
        graph_repository=graphs,
        run_repository=runs,
        capability_adapter=CapabilityAdapterRegistry(
            {"capability.primary": handler, "capability.fallback": handler}
        ),
        clock=lambda: 100.0,
    )
    orchestrator.create_run(
        graph_identifier=graph.graph_identifier,
        graph_version=1,
        graph_run_identifier="run.fallback-runtime",
        tenant_identifier="tenant.one",
        created_by_principal="scientist.one",
    )
    completed = orchestrator.resume("run.fallback-runtime")
    latest = {item.node_id: item for item in completed.node_states}
    assert completed.graph_state.status is GraphStatus.SUCCEEDED
    assert latest["node-primary"].status is NodeStatus.SKIPPED_BY_POLICY
    assert latest["node-fallback"].status is NodeStatus.SUCCEEDED
    assert calls == ["capability.primary", "capability.fallback"]
    assert any(
        item.decision_kind is DecisionKind.FAILURE_PROPAGATION
        and item.outcome == "fallback_activated"
        for item in completed.decision_states
    )


def test_pending_approval_expires_before_execution(tmp_path: Path) -> None:
    graphs, runs = _repositories(tmp_path)
    approval = ApprovalRequirement(
        approval_identifier="approval.expiring",
        graph_identifier="graph.expiring-approval",
        graph_version=1,
        node_identifier="node-a",
        operation_identity="workflow.execute",
        capability_identity="capability.a",
        validity_epoch=95.0,
    )
    graph = WorkflowGraphDefinition(
        graph_identifier="graph.expiring-approval",
        version=1,
        nodes=(
            WorkflowNode(
                node_identifier="node-a",
                node_kind=NodeKind.CLASSICAL,
                capability_identity="capability.a",
                execution_policy=ExecutionPolicy(
                    requires_approval=True,
                    approval_requirements=(approval,),
                ),
            ),
        ),
        root_node_ids=("node-a",),
        terminal_node_ids=("node-a",),
    )
    graphs.put(graph)
    now = [90.0]
    calls = 0

    def handler(invocation: CapabilityInvocation) -> CapabilityInvocationResult:
        nonlocal calls
        calls += 1
        return CapabilityInvocationResult(
            invocation_identifier=invocation.invocation_identifier,
            status=CapabilityInvocationStatus.SUCCEEDED,
        )

    orchestrator = WorkflowOrchestrator(
        graph_repository=graphs,
        run_repository=runs,
        capability_adapter=CapabilityAdapterRegistry({"capability.a": handler}),
        clock=lambda: now[0],
    )
    orchestrator.create_run(
        graph_identifier=graph.graph_identifier,
        graph_version=1,
        graph_run_identifier="run.expiring-approval",
        tenant_identifier="tenant.one",
        created_by_principal="scientist.one",
    )
    now[0] = 100.0
    completed = orchestrator.resume("run.expiring-approval")
    assert completed.graph_state.status is GraphStatus.BLOCKED
    assert completed.approval_states[0].status is ApprovalStatus.EXPIRED
    assert calls == 0


def test_interrupted_running_attempt_is_recovered_with_stable_identity(
    tmp_path: Path,
) -> None:
    graphs, runs = _repositories(tmp_path)
    graph = graphs.put(_value_graph())
    calls: list[tuple[str, str]] = []

    def handler(invocation: CapabilityInvocation) -> CapabilityInvocationResult:
        calls.append((invocation.capability_identity, invocation.invocation_identifier))
        return CapabilityInvocationResult(
            invocation_identifier=invocation.invocation_identifier,
            status=CapabilityInvocationStatus.SUCCEEDED,
            output_values=FrozenDict({"value": 1}),
        )

    first_process = WorkflowOrchestrator(
        graph_repository=graphs,
        run_repository=runs,
        capability_adapter=CapabilityAdapterRegistry(
            {"capability.a": handler, "capability.b": handler}
        ),
        maximum_parallelism=1,
        clock=lambda: 100.0,
    )
    created = first_process.create_run(
        graph_identifier=graph.graph_identifier,
        graph_version=1,
        graph_run_identifier="run.interrupted",
        tenant_identifier="tenant.one",
        created_by_principal="scientist.one",
    )
    ready = first_process._refresh_statuses(created, graph)
    running = first_process._mark_running(ready, ("node-a",))
    persisted = first_process._persist_if_changed(created.snapshot_fingerprint, running)
    running_state = next(
        item for item in persisted.node_states if item.node_id == "node-a"
    )
    expected_identity = first_process._invocation_identifier(persisted, running_state)

    restarted = WorkflowOrchestrator(
        graph_repository=graphs,
        run_repository=runs,
        capability_adapter=CapabilityAdapterRegistry(
            {"capability.a": handler, "capability.b": handler}
        ),
        maximum_parallelism=1,
        clock=lambda: 101.0,
    )
    completed = restarted.resume("run.interrupted")
    assert completed.graph_state.status is GraphStatus.SUCCEEDED
    assert completed.graph_state.recovery_evidence_fingerprint is not None
    assert calls[0] == ("capability.a", expected_identity)
    assert [item[0] for item in calls] == ["capability.a", "capability.b"]
    assert any(
        item.decision_kind is DecisionKind.RECOVERY
        and item.outcome == "interrupted_nodes_requeued"
        for item in completed.decision_states
    )
