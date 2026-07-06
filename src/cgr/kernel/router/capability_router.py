"""Capability-based plugin selection for the runtime."""

from typing import Any

from cgr.kernel.contracts import ExecutionRequest, HealthStatus, Plugin
from cgr.kernel.exceptions import CapabilityNotFoundError
from cgr.kernel.registry import PluginRegistry

from .capability_classifier import CapabilityClassifier
from .plugin_selector import PluginSelector
from .route_candidate import RouteCandidate
from .route_decision import RouteDecision
from .route_strategy import RouteStrategy


class CapabilityRouter:
    """Select the first registered plugin supporting a requested capability."""

    def __init__(
        self,
        registry: PluginRegistry,
        classifier: CapabilityClassifier | None = None,
        selector: PluginSelector | None = None,
        strategy: RouteStrategy = RouteStrategy.FIRST_MATCH,
    ) -> None:
        self._registry = registry
        self._classifier = (
            classifier if classifier is not None else CapabilityClassifier()
        )
        self._selector = selector if selector is not None else PluginSelector()
        self._strategy = strategy

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
        """Classify, rank, and select a plugin for the request."""
        capability_id = self._classifier.classify(request)
        plugins = self._registry.find_by_capability(request.capability)
        if not plugins:
            raise CapabilityNotFoundError(
                f"No plugin registered for capability '{capability_id}'."
            )

        candidates = [
            RouteCandidate(
                plugin_id=plugin.metadata.id,
                plugin_name=plugin.metadata.name,
                plugin_version=plugin.metadata.version,
                capability_id=capability_id,
                healthy=plugin.health == HealthStatus.HEALTHY,
            )
            for plugin in plugins
            if plugin.health != HealthStatus.UNAVAILABLE
        ]
        if not candidates:
            raise CapabilityNotFoundError(
                f"No available plugin registered for capability '{capability_id}'."
            )

        selected = self._selector.select(candidates, self._strategy)
        reasons = {
            RouteStrategy.FIRST_MATCH: (
                "Selected first available plugin for requested capability."
            ),
            RouteStrategy.HIGHEST_PRIORITY: (
                "Selected highest priority plugin for requested capability."
            ),
        }
        return RouteDecision(
            capability_id=capability_id,
            selected_plugin_id=selected.plugin_id,
            candidate_plugin_ids=[candidate.plugin_id for candidate in candidates],
            strategy=self._strategy,
            reason=reasons[self._strategy],
        )
