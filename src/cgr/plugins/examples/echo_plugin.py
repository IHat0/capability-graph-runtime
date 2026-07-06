"""
Reference Echo Plugin for the Capability Graph Runtime.

This plugin simply returns the payload it receives.

Its purpose is to verify that the runtime contracts work correctly
before integrating real AI models.
"""

from __future__ import annotations

from typing import Any

from cgr.kernel.contracts import (
    Capability,
    CapabilityVersion,
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
    HealthStatus,
    Plugin,
    PluginMetadata,
    PluginState,
)


class EchoPlugin(Plugin[Any, Any]):
    """
    Simple reference plugin.

    Returns exactly the payload that it receives.
    """

    def __init__(self) -> None:
        self._state = PluginState.DISCOVERED
        self._health = HealthStatus.HEALTHY

        self._metadata = PluginMetadata(
            id="echo",
            name="Echo Plugin",
            version="1.0.0",
            author="Capability Graph Runtime",
            description="Reference implementation used for testing.",
            capabilities=[
                Capability(
                    id="echo",
                    name="Echo",
                    description="Echo capability",
                    version=CapabilityVersion(major=1, minor=0, patch=0),
                    tags=["example", "test"],
                )
            ],
        )

    @property
    def metadata(self) -> PluginMetadata:
        return self._metadata

    @property
    def state(self) -> PluginState:
        return self._state

    @property
    def health(self) -> HealthStatus:
        return self._health

    def initialize(self) -> None:
        self._state = PluginState.RUNNING

    def shutdown(self) -> None:
        self._state = PluginState.STOPPED

    def execute(
        self,
        request: ExecutionRequest[Any],
    ) -> ExecutionResult[Any]:
        return ExecutionResult(
            context=request.context,
            status=ExecutionStatus.SUCCESS,
            output=request.payload,
            duration_ms=0.0,
        )
