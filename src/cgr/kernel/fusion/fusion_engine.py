"""Sequential fusion of compatible plugin outputs."""

from typing import Any

from cgr.kernel.contracts import ExecutionRequest, ExecutionStatus
from cgr.kernel.exceptions import CapabilityNotFoundError
from cgr.kernel.runtime import KernelRuntime

from .fusion_result import FusionResult
from .fusion_strategy import FusionStrategy


class FusionEngine:
    """Execute compatible plugins and fuse their successful outputs."""

    def __init__(self, runtime: KernelRuntime) -> None:
        self._runtime = runtime

    def fuse(
        self,
        request: ExecutionRequest[Any],
        strategy: FusionStrategy = FusionStrategy.COLLECT_ALL,
    ) -> FusionResult:
        """Execute all candidates and fuse successful outputs."""
        plugins = self._runtime.registry.find_by_capability(request.capability)
        if not plugins:
            raise CapabilityNotFoundError(
                f"No plugin registered for capability '{request.capability.id}'."
            )

        attempted_plugin_ids: list[str] = []
        successful_plugin_ids: list[str] = []
        failed_plugin_ids: list[str] = []
        successful_outputs: list[Any] = []

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
                successful_outputs.append(result.output)
            else:
                failed_plugin_ids.append(plugin_id)

        fused_output: Any | None
        if strategy == FusionStrategy.FIRST_SUCCESS:
            fused_output = successful_outputs[0] if successful_outputs else None
        else:
            fused_output = successful_outputs

        return FusionResult(
            capability_id=request.capability.id,
            strategy=strategy,
            attempted_plugin_ids=attempted_plugin_ids,
            successful_plugin_ids=successful_plugin_ids,
            failed_plugin_ids=failed_plugin_ids,
            fused_output=fused_output,
        )
