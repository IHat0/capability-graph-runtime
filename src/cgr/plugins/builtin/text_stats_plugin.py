"""Built-in deterministic text statistics plugin."""

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


class TextStatsPlugin(Plugin[Any, Any]):
    """Compute character, word, and line counts for text."""

    def __init__(self) -> None:
        self._state = PluginState.DISCOVERED
        self._metadata = PluginMetadata(
            id="builtin.text_stats",
            name="Built-in Text Stats",
            version="1.0.0",
            author="CGR",
            description="Computes simple statistics for text.",
            capabilities=[
                Capability(
                    id="text.stats",
                    name="Text Stats",
                    description="Compute simple statistics for text.",
                    version=CapabilityVersion(major=1, minor=0, patch=0),
                    tags=["builtin", "tool", "text"],
                )
            ],
            tags=["builtin", "tool", "text"],
        )

    @property
    def metadata(self) -> PluginMetadata:
        return self._metadata

    @property
    def state(self) -> PluginState:
        return self._state

    @property
    def health(self) -> HealthStatus:
        if self._state == PluginState.RUNNING:
            return HealthStatus.HEALTHY
        return HealthStatus.DEGRADED

    def initialize(self) -> None:
        self._state = PluginState.RUNNING

    def shutdown(self) -> None:
        self._state = PluginState.STOPPED

    def execute(
        self,
        request: ExecutionRequest[Any],
    ) -> ExecutionResult[Any]:
        payload = request.payload
        if not isinstance(payload, dict):
            raise ValueError("Text stats payload must be a dictionary.")
        text = payload.get("text")
        if not isinstance(text, str):
            raise ValueError("Text stats payload must contain string text.")

        lines = text.splitlines()
        return ExecutionResult(
            context=request.context,
            status=ExecutionStatus.SUCCESS,
            output={
                "character_count": len(text),
                "word_count": len(text.split()),
                "line_count": len(lines),
                "non_empty_line_count": sum(bool(line.strip()) for line in lines),
            },
            duration_ms=0.0,
        )
