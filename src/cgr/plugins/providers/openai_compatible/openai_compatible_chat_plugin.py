"""Provider-neutral OpenAI-compatible chat model plugin."""

from time import perf_counter
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

from .chat_client import (
    OpenAICompatibleChatClient,
    UrllibOpenAICompatibleChatClient,
)
from .chat_config import OpenAICompatibleChatConfig


class OpenAICompatibleChatPlugin(
    Plugin[ModelRequest | dict[str, Any], dict[str, Any]]
):
    """Execute CGR model requests through a chat-completions endpoint."""

    def __init__(
        self,
        config: OpenAICompatibleChatConfig | None = None,
        client: OpenAICompatibleChatClient | None = None,
        capability_id: str = "model.reason",
        plugin_id: str | None = None,
    ) -> None:
        self._config = config or OpenAICompatibleChatConfig.from_env()
        self._client = client or UrllibOpenAICompatibleChatClient()
        self._state = PluginState.DISCOVERED
        resolved_id = plugin_id or (
            f"provider.{self._config.provider_name}.{self._config.model}"
        )
        tags = [
            "model",
            "provider",
            "openai-compatible",
            self._config.provider_name,
        ]
        self._metadata = PluginMetadata(
            id=resolved_id,
            name=f"{self._config.provider_name} {self._config.model}",
            version="1.0.0",
            author="CGR",
            description="Calls an OpenAI-compatible chat completions API model.",
            capabilities=[
                Capability(
                    id=capability_id,
                    name="OpenAI-Compatible Chat Model",
                    description=(
                        "Generate a model response using an OpenAI-compatible "
                        "chat completions API."
                    ),
                    version=CapabilityVersion(major=1, minor=0, patch=0),
                    tags=tags,
                )
            ],
            tags=tags,
        )

    @property
    def metadata(self) -> PluginMetadata:
        return self._metadata

    @property
    def state(self) -> PluginState:
        return self._state

    @property
    def health(self) -> HealthStatus:
        return (
            HealthStatus.HEALTHY
            if self._state == PluginState.RUNNING
            else HealthStatus.DEGRADED
        )

    def initialize(self) -> None:
        self._state = PluginState.RUNNING

    def shutdown(self) -> None:
        self._state = PluginState.STOPPED

    def execute(
        self,
        request: ExecutionRequest[ModelRequest | dict[str, Any]],
    ) -> ExecutionResult[dict[str, Any]]:
        model_request = self._parse_request(request.payload)
        messages = [
            {"role": message.role.value, "content": message.content}
            for message in model_request.messages
        ]
        started = perf_counter()
        response = self._client.create_chat_completion(self._config, messages)
        duration_ms = (perf_counter() - started) * 1000
        model_response = ModelResponse(
            text=self._extract_text(response),
            model_id=self._config.model,
            usage=self._extract_usage(response),
            metadata={"provider": self._config.provider_name},
        )
        return ExecutionResult(
            context=request.context,
            status=ExecutionStatus.SUCCESS,
            output=model_response.model_dump(),
            duration_ms=duration_ms,
        )

    @staticmethod
    def _parse_request(payload: ModelRequest | dict[str, Any]) -> ModelRequest:
        if isinstance(payload, ModelRequest):
            return payload
        if isinstance(payload, dict):
            return ModelRequest.model_validate(payload)
        raise ValueError("Model payload must be a ModelRequest or dictionary.")

    @staticmethod
    def _extract_text(response: dict[str, Any]) -> str:
        try:
            text = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(
                "OpenAI-compatible chat response did not contain text output."
            ) from exc
        if not isinstance(text, str) or not text:
            raise RuntimeError(
                "OpenAI-compatible chat response did not contain text output."
            )
        return text

    @staticmethod
    def _extract_usage(response: dict[str, Any]) -> dict[str, int]:
        usage = response.get("usage")
        if not isinstance(usage, dict):
            return {}
        return {
            key: value
            for key, value in usage.items()
            if isinstance(key, str)
            and isinstance(value, int)
            and not isinstance(value, bool)
            and value >= 0
        }
