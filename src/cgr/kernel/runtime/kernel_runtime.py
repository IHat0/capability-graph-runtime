"""
Kernel runtime for the Capability Graph Runtime.

The KernelRuntime coordinates plugin registration and execution.
It does not know about concrete plugin implementations.
"""

from __future__ import annotations

from typing import Any

from cgr.kernel.contracts import ExecutionRequest, ExecutionResult
from cgr.kernel.registry import PluginRegistry
from cgr.shared.events import Event, EventBus, EventType


class KernelRuntime:
    """
    Minimal runtime kernel.

    This class is responsible for executing requests through registered plugins.
    """

    def __init__(
        self,
        registry: PluginRegistry | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        self._registry = registry if registry is not None else PluginRegistry()
        self._event_bus = event_bus if event_bus is not None else EventBus()

    @property
    def registry(self) -> PluginRegistry:
        """Return the plugin registry."""
        return self._registry

    @property
    def event_bus(self) -> EventBus:
        """Return the runtime event bus."""
        return self._event_bus

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
        plugins = self._registry.find_by_capability(request.capability)
        if not plugins:
            raise LookupError(
                f"No plugin registered for capability '{request.capability.id}'."
            )

        return self._execute_plugin(plugins[0].metadata.id, request)

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
                },
            )
        )
        return result
