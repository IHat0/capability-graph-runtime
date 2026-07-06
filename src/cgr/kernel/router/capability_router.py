"""Capability-based plugin selection for the runtime."""

from typing import Any

from cgr.kernel.contracts import ExecutionRequest, Plugin
from cgr.kernel.exceptions import CapabilityNotFoundError
from cgr.kernel.registry import PluginRegistry

from .route_decision import RouteDecision


class CapabilityRouter:
    """Select the first registered plugin supporting a requested capability."""

    def __init__(self, registry: PluginRegistry) -> None:
        self._registry = registry

    def select_plugin(
        self,
        request: ExecutionRequest[Any],
    ) -> Plugin[Any, Any]:
        """Return the plugin selected by the route decision."""
        decision = self.route(request)
        return self._registry.get(decision.selected_plugin_id)

    def route(
        self,
        request: ExecutionRequest[Any],
    ) -> RouteDecision:
        """Return a structured first-match decision for the request."""
        plugins = self._registry.find_by_capability(request.capability)
        if not plugins:
            raise CapabilityNotFoundError(
                f"No plugin registered for capability '{request.capability.id}'."
            )

        return RouteDecision(
            capability_id=request.capability.id,
            selected_plugin_id=plugins[0].metadata.id,
            candidate_plugin_ids=[plugin.metadata.id for plugin in plugins],
            strategy="first_match",
            reason=(
                "Selected first plugin registered for requested capability."
            ),
        )
