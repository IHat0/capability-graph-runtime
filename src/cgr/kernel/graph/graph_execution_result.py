"""Structured results from capability graph execution."""

from typing import Any

from pydantic import BaseModel, ConfigDict

from cgr.kernel.contracts import ExecutionResult


class GraphExecutionResult(BaseModel):
    """Immutable aggregate result from executing a capability graph."""

    model_config = ConfigDict(frozen=True)

    graph_succeeded: bool
    executed_node_ids: list[str]
    failed_node_id: str | None = None
    node_results: dict[str, ExecutionResult[Any]]
