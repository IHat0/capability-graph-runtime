"""Sequential plugin competition for capability execution."""

from typing import Any

from cgr.kernel.contracts import (
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
)
from cgr.kernel.exceptions import CapabilityNotFoundError
from cgr.kernel.runtime import KernelRuntime

from .competition_result import CompetitionResult


class CompetitionEngine:
    """Execute compatible plugins and retain the first successful result."""

    def __init__(self, runtime: KernelRuntime) -> None:
        self._runtime = runtime

    def compete(self, request: ExecutionRequest[Any]) -> CompetitionResult:
        """Execute all candidates sequentially and select the first success."""
        plugins = self._runtime.registry.find_by_capability(request.capability)
        if not plugins:
            raise CapabilityNotFoundError(
                f"No plugin registered for capability '{request.capability.id}'."
            )

        attempted_plugin_ids: list[str] = []
        successful_plugin_ids: list[str] = []
        failed_plugin_ids: list[str] = []
        winner_plugin_id: str | None = None
        winner_result: ExecutionResult[Any] | None = None

        for plugin in plugins:
            plugin_id = plugin.metadata.id
            attempted_plugin_ids.append(plugin_id)
            try:
                result = self._runtime.execute(plugin_id, request)
            except Exception:
                failed_plugin_ids.append(plugin_id)
                continue

            if result.status == ExecutionStatus.SUCCESS:
                successful_plugin_ids.append(plugin_id)
                if winner_plugin_id is None:
                    winner_plugin_id = plugin_id
                    winner_result = result
            else:
                failed_plugin_ids.append(plugin_id)

        return CompetitionResult(
            capability_id=request.capability.id,
            winner_plugin_id=winner_plugin_id,
            attempted_plugin_ids=attempted_plugin_ids,
            successful_plugin_ids=successful_plugin_ids,
            failed_plugin_ids=failed_plugin_ids,
            result=winner_result,
        )
