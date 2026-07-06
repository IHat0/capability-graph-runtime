"""Capability-based plugin selection for the runtime."""

from typing import Any

from cgr.kernel.contracts import ExecutionRequest, Plugin
from cgr.kernel.exceptions import CapabilityNotFoundError
from cgr.kernel.registry import PluginRegistry


class CapabilityRouter:
    """Select the first registered plugin supporting a requested capability."""

    def __init__(self, registry: PluginRegistry) -> None:
        self._registry = registry

    def select_plugin(
        self,
        request: ExecutionRequest[Any],
    ) -> Plugin[Any, Any]:
        """Return the first plugin supporting the request capability."""
        plugins = self._registry.find_by_capability(request.capability)
        if not plugins:
            raise CapabilityNotFoundError(
                f"No plugin registered for capability '{request.capability.id}'."
            )

        return plugins[0]
