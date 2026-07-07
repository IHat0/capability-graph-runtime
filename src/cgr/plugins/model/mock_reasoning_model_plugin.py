"""Deterministic mock reasoning model plugin."""

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
from cgr.kernel.model import ModelRequest, ModelResponse


class MockReasoningModelPlugin(Plugin[Any, Any]):
    """Produce deterministic reasoning responses without external services."""

    def __init__(self) -> None:
        self._state = PluginState.DISCOVERED
        self._metadata = PluginMetadata(
            id="mock.reasoning_model",
            name="Mock Reasoning Model",
            version="1.0.0",
            author="CGR",
            description="Deterministic mock reasoning model plugin.",
            capabilities=[
                Capability(
                    id="model.reason",
                    name="Model Reason",
                    description="Generate a deterministic reasoning response.",
                    version=CapabilityVersion(major=1, minor=0, patch=0),
                    tags=["model", "reasoning", "mock"],
                )
            ],
            tags=["model", "reasoning", "mock"],
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
        model_request = self._parse_request(request.payload)
        text = f"Reasoned answer: {model_request.latest_user_message}"
        response = ModelResponse(
            model_id=self.metadata.id,
            text=text,
            usage={
                "input_messages": len(model_request.messages),
                "output_characters": len(text),
            },
        )
        return ExecutionResult(
            context=request.context,
            status=ExecutionStatus.SUCCESS,
            output=response.model_dump(),
            duration_ms=0.0,
        )

    @staticmethod
    def _parse_request(payload: Any) -> ModelRequest:
        if isinstance(payload, ModelRequest):
            return payload
        if isinstance(payload, dict):
            return ModelRequest.model_validate(payload)
        raise ValueError("Model payload must be a ModelRequest or dictionary.")
