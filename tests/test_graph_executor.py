from typing import Any

import pytest
from pydantic import ValidationError

from cgr.kernel.contracts import (
    Capability,
    CapabilityVersion,
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
)
from cgr.kernel.graph import (
    CapabilityEdge,
    CapabilityGraph,
    CapabilityNode,
    GraphExecutionResult,
    GraphExecutor,
)
from cgr.kernel.runtime import KernelRuntime
from cgr.plugins.examples import EchoPlugin
from cgr.shared.events import EventType


def make_node(node_id: str, capability_id: str = "echo") -> CapabilityNode:
    return CapabilityNode(
        id=node_id,
        capability_id=capability_id,
        label=f"Node {node_id}",
    )


def make_graph(*nodes: CapabilityNode) -> CapabilityGraph:
    graph = CapabilityGraph()
    for node in nodes:
        graph.add_node(node)
    return graph


class CapabilityEchoPlugin(EchoPlugin):
    """Echo plugin advertising a configurable capability."""

    def __init__(self, plugin_id: str, capability_id: str) -> None:
        super().__init__()
        capability = Capability(
            id=capability_id,
            name=capability_id.title(),
            version=CapabilityVersion(major=1, minor=0, patch=0),
        )
        self._metadata = self.metadata.model_copy(
            update={"id": plugin_id, "capabilities": [capability]}
        )


class FailedResultPlugin(CapabilityEchoPlugin):
    """Plugin returning a failed execution result."""

    def execute(
        self,
        request: ExecutionRequest[Any],
    ) -> ExecutionResult[Any]:
        return ExecutionResult(
            context=request.context,
            status=ExecutionStatus.FAILED,
            output=None,
            error="node failed",
        )


class RaisingPlugin(CapabilityEchoPlugin):
    """Plugin raising during graph node execution."""

    def execute(
        self,
        request: ExecutionRequest[Any],
    ) -> ExecutionResult[Any]:
        raise RuntimeError("node exploded")


def test_graph_execution_result_is_immutable() -> None:
    result = GraphExecutionResult(
        graph_succeeded=True,
        executed_node_ids=[],
        node_results={},
    )

    with pytest.raises(ValidationError):
        result.graph_succeeded = False


def test_executor_executes_single_node_and_stores_result() -> None:
    runtime = KernelRuntime()
    runtime.register_plugin(EchoPlugin())
    graph = make_graph(make_node("one"))

    result = GraphExecutor(runtime).execute(graph, {"message": "graph"})

    assert result.graph_succeeded is True
    assert result.executed_node_ids == ["one"]
    assert result.failed_node_id is None
    assert list(result.node_results) == ["one"]
    assert result.node_results["one"].output == {"message": "graph"}


def test_executor_executes_chain_in_topological_order() -> None:
    runtime = KernelRuntime()
    runtime.register_plugin(EchoPlugin())
    graph = make_graph(make_node("one"), make_node("two"), make_node("three"))
    graph.add_edge(CapabilityEdge(source_id="one", target_id="two"))
    graph.add_edge(CapabilityEdge(source_id="two", target_id="three"))

    result = GraphExecutor(runtime).execute(graph, "payload")

    assert result.executed_node_ids == ["one", "two", "three"]
    assert list(result.node_results) == ["one", "two", "three"]
    assert result.graph_succeeded is True


def test_executor_executes_branching_graph_in_topological_order() -> None:
    runtime = KernelRuntime()
    runtime.register_plugin(EchoPlugin())
    graph = make_graph(
        make_node("root"),
        make_node("left"),
        make_node("right"),
        make_node("last"),
    )
    graph.add_edge(CapabilityEdge(source_id="root", target_id="right"))
    graph.add_edge(CapabilityEdge(source_id="root", target_id="left"))
    graph.add_edge(CapabilityEdge(source_id="left", target_id="last"))
    graph.add_edge(CapabilityEdge(source_id="right", target_id="last"))

    result = GraphExecutor(runtime).execute(graph, "payload")

    assert result.executed_node_ids == ["root", "left", "right", "last"]


def test_stop_on_failure_stops_after_failed_result() -> None:
    runtime = KernelRuntime()
    runtime.register_plugin(FailedResultPlugin("failed", "fail"))
    runtime.register_plugin(EchoPlugin())
    graph = make_graph(make_node("one", "fail"), make_node("two"))
    graph.add_edge(CapabilityEdge(source_id="one", target_id="two"))

    result = GraphExecutor(runtime).execute(graph, "payload")

    assert result.graph_succeeded is False
    assert result.failed_node_id == "one"
    assert result.executed_node_ids == ["one"]
    assert list(result.node_results) == ["one"]


def test_stop_on_failure_stops_after_raised_exception() -> None:
    runtime = KernelRuntime()
    runtime.register_plugin(RaisingPlugin("raising", "raise"))
    runtime.register_plugin(EchoPlugin())
    graph = make_graph(make_node("one", "raise"), make_node("two"))
    graph.add_edge(CapabilityEdge(source_id="one", target_id="two"))

    result = GraphExecutor(runtime).execute(graph, "payload")

    assert result.graph_succeeded is False
    assert result.failed_node_id == "one"
    assert result.executed_node_ids == []
    assert result.node_results == {}


def test_continue_after_failed_result_executes_remaining_nodes() -> None:
    runtime = KernelRuntime()
    runtime.register_plugin(FailedResultPlugin("failed", "fail"))
    runtime.register_plugin(EchoPlugin())
    graph = make_graph(make_node("one", "fail"), make_node("two"))
    graph.add_edge(CapabilityEdge(source_id="one", target_id="two"))

    result = GraphExecutor(runtime).execute(
        graph,
        "payload",
        stop_on_failure=False,
    )

    assert result.graph_succeeded is False
    assert result.failed_node_id is None
    assert result.executed_node_ids == ["one", "two"]
    assert list(result.node_results) == ["one", "two"]


def test_continue_after_raised_exception_executes_remaining_nodes() -> None:
    runtime = KernelRuntime()
    runtime.register_plugin(RaisingPlugin("raising", "raise"))
    runtime.register_plugin(EchoPlugin())
    graph = make_graph(make_node("one", "raise"), make_node("two"))
    graph.add_edge(CapabilityEdge(source_id="one", target_id="two"))

    result = GraphExecutor(runtime).execute(
        graph,
        "payload",
        stop_on_failure=False,
    )

    assert result.graph_succeeded is False
    assert result.failed_node_id is None
    assert result.executed_node_ids == ["two"]
    assert list(result.node_results) == ["two"]


def test_executor_emits_runtime_execution_events() -> None:
    runtime = KernelRuntime()
    runtime.register_plugin(EchoPlugin())

    GraphExecutor(runtime).execute(make_graph(make_node("one")), "payload")

    assert len(
        runtime.event_bus.history_by_type(EventType.EXECUTION_STARTED)
    ) == 1
    assert len(
        runtime.event_bus.history_by_type(EventType.EXECUTION_COMPLETED)
    ) == 1


def test_executor_propagates_cycle_error_before_execution() -> None:
    runtime = KernelRuntime()
    runtime.register_plugin(EchoPlugin())
    graph = make_graph(make_node("one"), make_node("two"))
    graph.add_edge(CapabilityEdge(source_id="one", target_id="two"))
    graph.add_edge(CapabilityEdge(source_id="two", target_id="one"))

    with pytest.raises(
        ValueError,
        match="Cannot topologically sort graph with cycles",
    ):
        GraphExecutor(runtime).execute(graph, "payload")

    assert runtime.event_bus.history_by_type(EventType.EXECUTION_STARTED) == []
