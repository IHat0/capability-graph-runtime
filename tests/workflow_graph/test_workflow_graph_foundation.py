"""Focused tests for Phase 4 workflow graph contracts, state, and validation."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError

from cgr.workflow_graph import (
    ApprovalRequirement,
    ApprovalState,
    ApprovalStatus,
    BudgetState,
    BudgetStatus,
    ComparisonGroup,
    ConditionalEdge,
    ConditionKind,
    ConditionOutcome,
    EdgeKind,
    ExecutionPolicy,
    FailureAction,
    FailureRecoveryPolicy,
    FrozenDict,
    GraphState,
    GraphStatus,
    InputPort,
    NodeKind,
    NodeState,
    NodeStatus,
    OutputPort,
    ParameterSweep,
    PortBinding,
    PortType,
    QuantumEscalationPolicy,
    RetryOutcome,
    RetryPolicy,
    RetryState,
    WorkflowEdge,
    WorkflowGraphDefinition,
    WorkflowGraphValidator,
    WorkflowNode,
)
from cgr.workflow_graph.contracts import _MAX_GRAPH_DEPTH, _MAX_NODES


def node(
    identifier: str,
    *,
    kind: NodeKind = NodeKind.CLASSICAL,
    input_ports: tuple[InputPort, ...] = (),
    output_ports: tuple[OutputPort, ...] = (),
    **kwargs: object,
) -> WorkflowNode:
    return WorkflowNode(
        node_identifier=identifier,
        node_kind=kind,
        input_ports=input_ports,
        output_ports=output_ports,
        **kwargs,
    )


def edge(identifier: str, source: str, target: str) -> WorkflowEdge:
    return WorkflowEdge(
        edge_identifier=identifier,
        edge_kind=EdgeKind.DEPENDENCY,
        source_node_id=source,
        target_node_id=target,
    )


def linear_graph() -> WorkflowGraphDefinition:
    return WorkflowGraphDefinition(
        graph_identifier="graph.linear",
        version=1,
        nodes=(node("node.c"), node("node.a"), node("node.b")),
        edges=(edge("edge.bc", "node.b", "node.c"), edge("edge.ab", "node.a", "node.b")),
        root_node_ids=("node.a",),
        terminal_node_ids=("node.c",),
    )


def test_package_import_does_not_export_unimplemented_components() -> None:
    import cgr.workflow_graph as workflow_graph

    assert workflow_graph.WorkflowGraphDefinition is WorkflowGraphDefinition
    assert not hasattr(workflow_graph, "WorkflowGraphCompiler")
    assert not hasattr(workflow_graph, "WorkflowOrchestrator")


def test_contracts_use_pydantic_validation_and_are_frozen() -> None:
    item = node("node.a", metadata={"label": "A"})
    with pytest.raises(PydanticValidationError):
        node("invalid node")
    with pytest.raises(PydanticValidationError):
        item.node_identifier = "node.b"  # type: ignore[misc]
    with pytest.raises(TypeError):
        item.metadata["other"] = "B"


def test_graph_fingerprint_is_immediate_deterministic_and_order_independent() -> None:
    first = linear_graph()
    second = WorkflowGraphDefinition(
        graph_identifier="graph.linear",
        version=1,
        nodes=tuple(reversed(first.nodes)),
        edges=tuple(reversed(first.edges)),
        root_node_ids=("node.a",),
        terminal_node_ids=("node.c",),
    )
    assert first.definition_fingerprint == first.fingerprint
    assert first.definition_fingerprint == second.definition_fingerprint
    assert len(first.definition_fingerprint or "") == 64


def test_graph_fingerprint_round_trip_and_tamper_rejection() -> None:
    graph = linear_graph()
    encoded = graph.model_dump_json()
    restored = WorkflowGraphDefinition.model_validate_json(encoded)
    assert restored.definition_fingerprint == graph.definition_fingerprint

    payload = json.loads(encoded)
    payload["version"] = 2
    with pytest.raises(PydanticValidationError, match="fingerprint"):
        WorkflowGraphDefinition.model_validate(payload)


def test_graph_fingerprint_changes_with_semantic_content() -> None:
    original = linear_graph()
    changed = original.model_copy(
        update={"graph_identifier": "graph.changed", "definition_fingerprint": None}
    )
    changed = WorkflowGraphDefinition.model_validate(changed.model_dump(mode="json"))
    assert changed.definition_fingerprint != original.definition_fingerprint


def test_graph_node_and_retry_bounds_are_enforced() -> None:
    with pytest.raises(PydanticValidationError, match="cannot exceed"):
        WorkflowGraphDefinition(
            graph_identifier="graph.large",
            version=1,
            nodes=tuple(node(f"node.{index}") for index in range(_MAX_NODES + 1)),
            root_node_ids=("node.0",),
            terminal_node_ids=(f"node.{_MAX_NODES}",),
        )
    with pytest.raises(PydanticValidationError):
        RetryPolicy(max_attempts=11)
    with pytest.raises(PydanticValidationError):
        RetryPolicy(max_attempts=2, retry_delay_seconds=float("nan"))


def test_ports_are_closed_typed_and_artifact_kinds_are_required() -> None:
    with pytest.raises(PydanticValidationError, match="artifact_kind"):
        InputPort(port_identifier="input", port_type=PortType.ARTIFACT)
    with pytest.raises(PydanticValidationError, match="Only artifact"):
        OutputPort(
            port_identifier="output",
            port_type=PortType.VALUE,
            artifact_kind="structure",
        )


def test_conditions_are_bounded_json_and_immutable() -> None:
    condition = ConditionalEdge(
        edge_identifier="edge.condition",
        source_node_id="node.a",
        target_node_id="node.b",
        condition_kind=ConditionKind.NUMERIC_COMPARE,
        condition_expression={"operator": "gt", "threshold": 0.5},
    )
    assert isinstance(condition.condition_expression, FrozenDict)
    with pytest.raises(TypeError):
        condition.condition_expression["threshold"] = 1.0
    with pytest.raises(PydanticValidationError):
        ConditionalEdge(
            edge_identifier="edge.condition",
            source_node_id="node.a",
            target_node_id="node.b",
            condition_kind=ConditionKind.SUCCESS,
            condition_expression={"bad": object()},
        )


def test_approval_requirement_is_bound_to_exact_graph_context() -> None:
    graph = linear_graph()
    approval = ApprovalRequirement(
        approval_identifier="approval.run",
        graph_identifier=graph.graph_identifier,
        graph_version=graph.version,
        graph_definition_fingerprint=graph.definition_fingerprint,
        node_identifier="node.b",
        capability_identity="capability.example",
    )
    assert approval.graph_definition_fingerprint == graph.definition_fingerprint


def test_quantum_submission_policy_is_fail_closed() -> None:
    with pytest.raises(PydanticValidationError, match="IBM submission requires"):
        QuantumEscalationPolicy(allow_ibm_submission=True)
    safe = QuantumEscalationPolicy()
    assert safe.allow_ibm_submission is False


def test_missing_nodes_return_deterministic_errors_without_exceptions() -> None:
    graph = WorkflowGraphDefinition(
        graph_identifier="graph.missing",
        version=1,
        nodes=(node("node.a"),),
        edges=(edge("edge.missing", "node.missing", "node.a"),),
        root_node_ids=("node.missing",),
        terminal_node_ids=("node.a",),
    )
    result = WorkflowGraphValidator(graph).validate()
    codes = tuple(item.code for item in result.errors)
    assert result.valid is False
    assert "MISSING_SOURCE_NODE" in codes
    assert "MISSING_ROOT_NODE" in codes
    assert codes == tuple(sorted(codes))


def test_self_edges_and_cycles_are_rejected() -> None:
    self_graph = WorkflowGraphDefinition(
        graph_identifier="graph.self",
        version=1,
        nodes=(node("node.a"),),
        edges=(edge("edge.self", "node.a", "node.a"),),
        root_node_ids=("node.a",),
        terminal_node_ids=("node.a",),
    )
    assert "SELF_EDGE" in {
        item.code for item in WorkflowGraphValidator(self_graph).validate().errors
    }

    cycle = WorkflowGraphDefinition(
        graph_identifier="graph.cycle",
        version=1,
        nodes=(node("node.a"), node("node.b")),
        edges=(edge("edge.ab", "node.a", "node.b"), edge("edge.ba", "node.b", "node.a")),
        root_node_ids=("node.a",),
        terminal_node_ids=("node.b",),
    )
    result = WorkflowGraphValidator(cycle).validate()
    assert "DIRECTED_CYCLE" in {item.code for item in result.errors}


def test_deterministic_topological_order_is_dependency_valid() -> None:
    graph = linear_graph()
    validator = WorkflowGraphValidator(graph)
    assert validator.topological_order() == ("node.a", "node.b", "node.c")
    assert validator.validate().maximum_depth == 3


def test_semantic_duplicate_edges_and_bindings_are_rejected() -> None:
    source = node(
        "node.a",
        output_ports=(
            OutputPort(
                port_identifier="out",
                port_type=PortType.ARTIFACT,
                artifact_kind="structure",
            ),
        ),
    )
    target = node(
        "node.b",
        input_ports=(
            InputPort(
                port_identifier="in",
                port_type=PortType.ARTIFACT,
                artifact_kind="structure",
            ),
        ),
    )
    graph = WorkflowGraphDefinition(
        graph_identifier="graph.duplicates",
        version=1,
        nodes=(source, target),
        edges=(edge("edge.one", "node.a", "node.b"), edge("edge.two", "node.a", "node.b")),
        bindings=(
            PortBinding(
                binding_identifier="binding.one",
                source_node_id="node.a",
                source_port_id="out",
                destination_node_id="node.b",
                destination_port_id="in",
            ),
            PortBinding(
                binding_identifier="binding.two",
                source_node_id="node.a",
                source_port_id="out",
                destination_node_id="node.b",
                destination_port_id="in",
            ),
        ),
        root_node_ids=("node.a",),
        terminal_node_ids=("node.b",),
    )
    codes = {item.code for item in WorkflowGraphValidator(graph).validate().errors}
    assert "DUPLICATE_EDGE_SEMANTICS" in codes
    assert "DUPLICATE_BINDING_SEMANTICS" in codes
    assert "MULTIPLE_BINDINGS_FOR_SINGULAR_INPUT" in codes


def test_port_type_and_artifact_kind_compatibility_are_enforced() -> None:
    source = node(
        "node.a",
        output_ports=(OutputPort(port_identifier="out", port_type=PortType.VALUE),),
    )
    target = node(
        "node.b",
        input_ports=(
            InputPort(
                port_identifier="in",
                port_type=PortType.ARTIFACT,
                artifact_kind="structure",
            ),
        ),
    )
    graph = WorkflowGraphDefinition(
        graph_identifier="graph.ports",
        version=1,
        nodes=(source, target),
        edges=(edge("edge.ab", "node.a", "node.b"),),
        bindings=(
            PortBinding(
                binding_identifier="binding.ab",
                source_node_id="node.a",
                source_port_id="out",
                destination_node_id="node.b",
                destination_port_id="in",
            ),
        ),
        root_node_ids=("node.a",),
        terminal_node_ids=("node.b",),
    )
    codes = {item.code for item in WorkflowGraphValidator(graph).validate().errors}
    assert "INCOMPATIBLE_PORT_TYPE" in codes


def test_missing_required_input_and_external_root_input_are_distinguished() -> None:
    missing = WorkflowGraphDefinition(
        graph_identifier="graph.missing.input",
        version=1,
        nodes=(
            node(
                "node.a",
                input_ports=(InputPort(port_identifier="input", port_type=PortType.VALUE),),
            ),
        ),
        root_node_ids=("node.a",),
        terminal_node_ids=("node.a",),
    )
    assert "MISSING_REQUIRED_INPUT" in {
        item.code for item in WorkflowGraphValidator(missing).validate().errors
    }

    external = WorkflowGraphDefinition(
        graph_identifier="graph.external.input",
        version=1,
        nodes=(
            node(
                "node.a",
                input_ports=(
                    InputPort(
                        port_identifier="input",
                        port_type=PortType.VALUE,
                        external=True,
                    ),
                ),
            ),
        ),
        root_node_ids=("node.a",),
        terminal_node_ids=("node.a",),
    )
    assert WorkflowGraphValidator(external).validate().valid is True


def test_unreachable_and_undeclared_boundary_nodes_are_errors() -> None:
    graph = WorkflowGraphDefinition(
        graph_identifier="graph.unreachable",
        version=1,
        nodes=(node("node.a"), node("node.b")),
        root_node_ids=("node.a",),
        terminal_node_ids=("node.a",),
    )
    codes = {item.code for item in WorkflowGraphValidator(graph).validate().errors}
    assert "UNREACHABLE_NODE" in codes
    assert "UNDECLARED_ROOT_NODE" in codes
    assert "UNDECLARED_TERMINAL_NODE" in codes


def test_parameter_sweep_and_comparison_validation_are_exact() -> None:
    sweep_graph = WorkflowGraphDefinition(
        graph_identifier="graph.sweep",
        version=1,
        nodes=(node("node.sweep", kind=NodeKind.CLASSICAL),),
        parameter_sweeps=(
            ParameterSweep(
                sweep_identifier="sweep.one",
                base_node_id="node.sweep",
                parameter_name="temperature",
                parameter_values=(1, 2),
            ),
        ),
        root_node_ids=("node.sweep",),
        terminal_node_ids=("node.sweep",),
    )
    assert "INVALID_SWEEP_BASE_KIND" in {
        item.code for item in WorkflowGraphValidator(sweep_graph).validate().errors
    }

    comparison_graph = WorkflowGraphDefinition(
        graph_identifier="graph.comparison",
        version=1,
        nodes=(node("node.a"), node("node.b")),
        comparison_groups=(
            ComparisonGroup(
                group_identifier="comparison.one",
                member_node_ids=("node.a", "node.missing"),
            ),
        ),
        root_node_ids=("node.a", "node.b"),
        terminal_node_ids=("node.a", "node.b"),
    )
    assert "UNKNOWN_COMPARISON_MEMBER" in {
        item.code for item in WorkflowGraphValidator(comparison_graph).validate().errors
    }


def test_failure_fallback_policy_references_are_validated() -> None:
    graph = WorkflowGraphDefinition(
        graph_identifier="graph.fallback",
        version=1,
        nodes=(
            node(
                "node.a",
                failure_recovery_policy=FailureRecoveryPolicy(
                    on_failure=FailureAction.FALLBACK,
                    fallback_node_id="node.missing",
                ),
            ),
        ),
        root_node_ids=("node.a",),
        terminal_node_ids=("node.a",),
    )
    assert "UNKNOWN_FALLBACK_NODE" in {
        item.code for item in WorkflowGraphValidator(graph).validate().errors
    }


def test_approval_context_mismatch_is_rejected_statically() -> None:
    provisional = linear_graph()
    approval = ApprovalRequirement(
        approval_identifier="approval.wrong",
        graph_identifier=provisional.graph_identifier,
        graph_version=provisional.version,
        graph_definition_fingerprint="0" * 64,
        node_identifier="node.b",
    )
    protected = node(
        "node.b",
        execution_policy=ExecutionPolicy(
            requires_approval=True,
            approval_requirements=(approval,),
        ),
    )
    graph = WorkflowGraphDefinition(
        graph_identifier="graph.linear",
        version=1,
        nodes=(node("node.a"), protected, node("node.c")),
        edges=(edge("edge.ab", "node.a", "node.b"), edge("edge.bc", "node.b", "node.c")),
        root_node_ids=("node.a",),
        terminal_node_ids=("node.c",),
    )
    assert "APPROVAL_CONTEXT_MISMATCH" in {
        item.code for item in WorkflowGraphValidator(graph).validate().errors
    }


def test_graph_state_validates_counts_timestamps_and_terminal_semantics() -> None:
    fingerprint = linear_graph().definition_fingerprint
    with pytest.raises(PydanticValidationError, match="counts exceed"):
        GraphState(
            graph_run_identifier="run.one",
            graph_identifier="graph.linear",
            graph_version=1,
            graph_definition_fingerprint=fingerprint,
            tenant_identifier="tenant.one",
            created_by_principal="principal.one",
            created_at_epoch=10,
            total_nodes=2,
            completed_nodes=2,
            failed_nodes=1,
        )
    succeeded = GraphState(
        graph_run_identifier="run.one",
        graph_identifier="graph.linear",
        graph_version=1,
        graph_definition_fingerprint=fingerprint,
        status=GraphStatus.SUCCEEDED,
        tenant_identifier="tenant.one",
        created_by_principal="principal.one",
        created_at_epoch=10,
        started_at_epoch=11,
        completed_at_epoch=12,
        total_nodes=2,
        completed_nodes=2,
    )
    assert succeeded.status is GraphStatus.SUCCEEDED


def test_node_state_validates_attempts_costs_and_condition_skip() -> None:
    fingerprint = linear_graph().definition_fingerprint
    with pytest.raises(PydanticValidationError, match="attempt_number"):
        NodeState(
            node_id="node.a",
            graph_run_identifier="run.one",
            graph_definition_fingerprint=fingerprint,
            attempt_number=2,
            max_attempts=1,
        )
    skipped = NodeState(
        node_id="node.a",
        graph_run_identifier="run.one",
        graph_definition_fingerprint=fingerprint,
        status=NodeStatus.SKIPPED_BY_CONDITION,
        attempt_number=1,
        completed_at_epoch=10,
        condition_outcome=ConditionOutcome.FALSE,
    )
    assert skipped.status is NodeStatus.SKIPPED_BY_CONDITION
    with pytest.raises(PydanticValidationError):
        NodeState(
            node_id="node.a",
            graph_run_identifier="run.one",
            graph_definition_fingerprint=fingerprint,
            cost_consumed=float("inf"),
        )


def test_approval_state_is_exactly_bound_and_uses_closed_status() -> None:
    fingerprint = linear_graph().definition_fingerprint
    granted = ApprovalState(
        approval_id="approval.one",
        graph_run_identifier="run.one",
        graph_identifier="graph.linear",
        graph_version=1,
        graph_definition_fingerprint=fingerprint,
        node_id="node.b",
        status=ApprovalStatus.GRANTED,
        approver_principal="principal.approver",
        requested_at_epoch=10,
        decided_at_epoch=11,
    )
    assert granted.status is ApprovalStatus.GRANTED
    with pytest.raises(PydanticValidationError):
        ApprovalState(
            approval_id="approval.one",
            graph_run_identifier="run.one",
            graph_identifier="graph.linear",
            graph_version=1,
            graph_definition_fingerprint=fingerprint,
            node_id="node.b",
            status="invented",
            requested_at_epoch=10,
        )


def test_retry_state_uses_closed_outcome_and_timestamp_order() -> None:
    policy = RetryPolicy(max_attempts=2)
    completed = RetryState(
        retry_id="retry.one",
        graph_run_identifier="run.one",
        node_id="node.a",
        attempt_number=2,
        original_failure_code="temporary.failure",
        original_failure_message="Temporary failure.",
        scheduled_at_epoch=10,
        executed_at_epoch=11,
        outcome=RetryOutcome.SUCCEEDED,
        retry_policy_fingerprint=policy.fingerprint,
    )
    assert completed.outcome is RetryOutcome.SUCCEEDED


def test_budget_state_separates_unknown_and_known_consumption() -> None:
    known = BudgetState(
        graph_run_identifier="run.one",
        ceiling_values={"usd": 100.0},
        actual_consumption={"usd": 25.0},
        reserved_budget={"usd": 25.0},
        remaining_budget={"usd": 50.0},
        status=BudgetStatus.WITHIN_BUDGET,
        terminal_over_budget=False,
        last_updated_epoch=10,
    )
    assert known.remaining_budget["usd"] == 50.0
    unknown = BudgetState(
        graph_run_identifier="run.one",
        unknown_consumption={"usd": 1.0},
        status=BudgetStatus.UNKNOWN,
        terminal_over_budget=False,
        last_updated_epoch=10,
    )
    assert unknown.status is BudgetStatus.UNKNOWN


def test_depth_limit_is_actually_exceeded() -> None:
    count = _MAX_GRAPH_DEPTH + 1
    graph = WorkflowGraphDefinition(
        graph_identifier="graph.deep",
        version=1,
        nodes=tuple(node(f"node.{index:03d}") for index in range(count)),
        edges=tuple(
            edge(f"edge.{index:03d}", f"node.{index:03d}", f"node.{index + 1:03d}")
            for index in range(count - 1)
        ),
        root_node_ids=("node.000",),
        terminal_node_ids=(f"node.{count - 1:03d}",),
    )
    result = WorkflowGraphValidator(graph).validate()
    assert result.maximum_depth == count
    assert "EXCESSIVE_DEPTH" in {item.code for item in result.errors}


def test_foundation_modules_do_not_import_execution_or_network_stacks() -> None:
    package = Path(__file__).parents[2] / "src" / "cgr" / "workflow_graph"
    forbidden = {"asyncio", "httpx", "qiskit", "requests", "socket", "subprocess"}
    observed: set[str] = set()
    for path in package.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for item in ast.walk(tree):
            if isinstance(item, ast.Import):
                observed.update(alias.name.split(".")[0] for alias in item.names)
            elif isinstance(item, ast.ImportFrom) and item.module:
                observed.add(item.module.split(".")[0])
    assert observed.isdisjoint(forbidden)
