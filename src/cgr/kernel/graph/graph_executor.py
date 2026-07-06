"""Deterministic sequential capability graph execution."""

from typing import Any

from cgr.kernel.contracts import (
    Capability,
    CapabilityVersion,
    ExecutionContext,
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
)
from cgr.kernel.runtime import KernelRuntime

from .capability_graph import CapabilityGraph
from .graph_execution_result import GraphExecutionResult


class GraphExecutor:
    """Execute graph nodes through the runtime in topological order."""

    def __init__(self, runtime: KernelRuntime) -> None:
        self._runtime = runtime

    def execute(
        self,
        graph: CapabilityGraph,
        payload: Any,
        stop_on_failure: bool = True,
    ) -> GraphExecutionResult:
        """Execute each graph node with the same original payload."""
        ordered_nodes = graph.topological_order()
        executed_node_ids: list[str] = []
        node_results: dict[str, ExecutionResult[Any]] = {}
        graph_succeeded = True

        for node in ordered_nodes:
            capability = Capability(
                id=node.capability_id,
                name=node.label,
                description=f"Graph node capability: {node.label}",
                version=CapabilityVersion(major=1, minor=0, patch=0),
            )
            request = ExecutionRequest[Any](
                capability=capability,
                context=ExecutionContext(),
                payload=payload,
            )
            try:
                result = self._runtime.execute_capability(request)
            except Exception:
                graph_succeeded = False
                if stop_on_failure:
                    return GraphExecutionResult(
                        graph_succeeded=False,
                        executed_node_ids=executed_node_ids,
                        failed_node_id=node.id,
                        node_results=node_results,
                    )
                continue

            executed_node_ids.append(node.id)
            node_results[node.id] = result
            if result.status != ExecutionStatus.SUCCESS:
                graph_succeeded = False
                if stop_on_failure:
                    return GraphExecutionResult(
                        graph_succeeded=False,
                        executed_node_ids=executed_node_ids,
                        failed_node_id=node.id,
                        node_results=node_results,
                    )

        return GraphExecutionResult(
            graph_succeeded=graph_succeeded,
            executed_node_ids=executed_node_ids,
            failed_node_id=None,
            node_results=node_results,
        )
