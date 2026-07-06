"""
Kernel runtime for the Capability Graph Runtime.

The KernelRuntime coordinates plugin registration and execution.
It does not know about concrete plugin implementations.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from cgr.kernel.contracts import (
    ExecutionRequest,
    ExecutionResult,
    HealthStatus,
    Plugin,
)
from cgr.kernel.exceptions import PluginAlreadyRegisteredError
from cgr.kernel.loader import PluginLoader
from cgr.kernel.registry import PluginRegistry
from cgr.kernel.router import CapabilityRouter
from cgr.shared.events import Event, EventBus, EventType

from .runtime_health import PluginHealthSnapshot, RuntimeHealthSnapshot


class KernelRuntime:
    """
    Minimal runtime kernel.

    This class is responsible for executing requests through registered plugins.
    """

    def __init__(
        self,
        registry: PluginRegistry | None = None,
        event_bus: EventBus | None = None,
        router: CapabilityRouter | None = None,
    ) -> None:
        self._registry = registry if registry is not None else PluginRegistry()
        self._event_bus = event_bus if event_bus is not None else EventBus()
        self._router = (
            router if router is not None else CapabilityRouter(self._registry)
        )

    @property
    def registry(self) -> PluginRegistry:
        """Return the plugin registry."""
        return self._registry

    @property
    def event_bus(self) -> EventBus:
        """Return the runtime event bus."""
        return self._event_bus

    @property
    def router(self) -> CapabilityRouter:
        """Return the runtime capability router."""
        return self._router

    def register_plugin(self, plugin: Plugin[Any, Any]) -> None:
        """Initialize and register a plugin with the runtime."""
        plugin.initialize()
        try:
            self._registry.register(plugin)
        except PluginAlreadyRegisteredError:
            plugin.shutdown()
            raise

        self._publish_plugin_event(EventType.PLUGIN_REGISTERED, plugin)

    def load_plugins(self, import_paths: Iterable[str]) -> None:
        """Load and register plugins from Python import paths."""
        for plugin in PluginLoader().load_many(import_paths):
            self.register_plugin(plugin)

    def unregister_plugin(self, plugin_id: str) -> None:
        """Shutdown and unregister a plugin if it exists."""
        if plugin_id not in self._registry:
            return

        plugin = self._registry.get(plugin_id)
        plugin.shutdown()
        self._registry.unregister(plugin_id)
        self._publish_plugin_event(EventType.PLUGIN_UNREGISTERED, plugin)

    def shutdown(self) -> None:
        """Shutdown and unregister every plugin managed by the runtime."""
        for plugin_id in self._registry.plugin_ids():
            self.unregister_plugin(plugin_id)

    def health_snapshot(self) -> RuntimeHealthSnapshot:
        """Return current runtime and registered plugin health information."""
        plugins = [
            PluginHealthSnapshot(
                plugin_id=plugin.metadata.id,
                plugin_name=plugin.metadata.name,
                plugin_version=plugin.metadata.version,
                state=plugin.state,
                health=plugin.health,
                capabilities=[
                    capability.id for capability in plugin.metadata.capabilities
                ],
            )
            for plugin in self._registry.all()
        ]
        return RuntimeHealthSnapshot(
            healthy=all(
                plugin.health == HealthStatus.HEALTHY for plugin in plugins
            ),
            plugin_count=len(plugins),
            plugins=plugins,
        )

    def execute(
        self,
        plugin_id: str,
        request: ExecutionRequest[Any],
    ) -> ExecutionResult[Any]:
        """
        Execute a request using a specific plugin.

        Args:
            plugin_id:
                ID of the plugin to execute.
            request:
                Execution request.

        Returns:
            ExecutionResult from the plugin.
        """
        return self._execute_plugin(plugin_id, request)

    def execute_capability(
        self,
        request: ExecutionRequest[Any],
    ) -> ExecutionResult[Any]:
        """Execute a request using the first plugin supporting its capability."""
        plugin = self._router.select_plugin(request)
        return self._execute_plugin(plugin.metadata.id, request)

    def _execute_plugin(
        self,
        plugin_id: str,
        request: ExecutionRequest[Any],
    ) -> ExecutionResult[Any]:
        """Execute a plugin and publish its execution lifecycle events."""
        capability_id = request.capability.id
        self._event_bus.publish(
            Event(
                type=EventType.EXECUTION_STARTED,
                source="kernel.runtime",
                correlation_id=request.context.correlation_id,
                execution_id=request.context.execution_id,
                payload={
                    "plugin_id": plugin_id,
                    "capability_id": capability_id,
                },
            )
        )

        try:
            plugin = self._registry.get(plugin_id)
            result = plugin.execute(request)
        except Exception as exc:
            self._event_bus.publish(
                Event(
                    type=EventType.EXECUTION_FAILED,
                    source="kernel.runtime",
                    correlation_id=request.context.correlation_id,
                    execution_id=request.context.execution_id,
                    payload={
                        "plugin_id": plugin_id,
                        "capability_id": capability_id,
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                        "duration_ms": 0.0,
                    },
                )
            )
            raise

        self._event_bus.publish(
            Event(
                type=EventType.EXECUTION_COMPLETED,
                source="kernel.runtime",
                correlation_id=request.context.correlation_id,
                execution_id=request.context.execution_id,
                payload={
                    "plugin_id": plugin_id,
                    "capability_id": capability_id,
                    "status": result.status.value,
                    "duration_ms": result.duration_ms,
                },
            )
        )
        return result

    def _publish_plugin_event(
        self,
        event_type: EventType,
        plugin: Plugin[Any, Any],
    ) -> None:
        """Publish a plugin lifecycle event."""
        self._event_bus.publish(
            Event(
                type=event_type,
                source="kernel.runtime",
                payload={
                    "plugin_id": plugin.metadata.id,
                    "plugin_name": plugin.metadata.name,
                    "plugin_version": plugin.metadata.version,
                },
            )
        )
