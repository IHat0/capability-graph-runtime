"""OpenAI-compatible Responses API model plugin."""

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

from .openai_client import OpenAIResponsesClient, UrllibOpenAIResponsesClient
from .openai_config import OpenAIProviderConfig


class OpenAIResponsesModelPlugin(
    Plugin[ModelRequest | dict[str, Any], dict[str, Any]]
):
    """Call an OpenAI-compatible Responses API through CGR contracts."""

    def __init__(
        self,
        config: OpenAIProviderConfig | None = None,
        client: OpenAIResponsesClient | None = None,
        capability_id: str = "model.reason",
        plugin_id: str = "provider.openai.responses",
    ) -> None:
        self._config = config if config is not None else OpenAIProviderConfig.from_env()
        self._client = (
            client if client is not None else UrllibOpenAIResponsesClient()
        )
        self._state = PluginState.DISCOVERED
        tags = ["model", "provider", "openai", "responses"]
        self._metadata = PluginMetadata(
            id=plugin_id,
            name="OpenAI Responses Model",
            version="1.0.0",
            author="CGR",
            description="Calls an OpenAI-compatible Responses API model.",
            capabilities=[
                Capability(
                    id=capability_id,
                    name="OpenAI Responses Model",
                    description=(
                        "Generate a real model response using the OpenAI "
                        "Responses API."
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
        if self._state == PluginState.RUNNING:
            return HealthStatus.HEALTHY
        return HealthStatus.DEGRADED

    def initialize(self) -> None:
        self._state = PluginState.RUNNING

    def shutdown(self) -> None:
        self._state = PluginState.STOPPED

    def execute(
        self,
        request: ExecutionRequest[ModelRequest | dict[str, Any]],
    ) -> ExecutionResult[dict[str, Any]]:
        model_request = self._parse_request(request.payload)
        input_messages = [
            {"role": message.role.value, "content": message.content}
            for message in model_request.messages
        ]
        started = perf_counter()
        response = self._client.create_response(self._config, input_messages)
        duration_ms = (perf_counter() - started) * 1000
        metadata = {"provider": "openai"}
        response_id = response.get("id")
        if isinstance(response_id, str):
            metadata["response_id"] = response_id
        model_response = ModelResponse(
            text=self._extract_text(response),
            model_id=self._config.model,
            usage=self._extract_usage(response),
            metadata=metadata,
        )
        return ExecutionResult(
            context=request.context,
            status=ExecutionStatus.SUCCESS,
            output=model_response.model_dump(),
            duration_ms=duration_ms,
        )

    @staticmethod
    def _parse_request(
        payload: ModelRequest | dict[str, Any],
    ) -> ModelRequest:
        if isinstance(payload, ModelRequest):
            return payload
        if isinstance(payload, dict):
            return ModelRequest.model_validate(payload)
        raise ValueError("Model payload must be a ModelRequest or dictionary.")

    @staticmethod
    def _extract_text(response: dict[str, Any]) -> str:
        output_text = response.get("output_text")
        if isinstance(output_text, str) and output_text:
            return output_text

        texts: list[str] = []
        output = response.get("output")
        if isinstance(output, list):
            for item in output:
                if not isinstance(item, dict):
                    continue
                content = item.get("content")
                if not isinstance(content, list):
                    continue
                for content_item in content:
                    if not isinstance(content_item, dict):
                        continue
                    text = content_item.get("text")
                    if isinstance(text, str) and text:
                        texts.append(text)
        if texts:
            return "\n".join(texts)
        raise RuntimeError(
            "OpenAI Responses API response did not contain text output."
        )

    @staticmethod
    def _extract_usage(response: dict[str, Any]) -> dict[str, int]:
        usage = response.get("usage")
        if not isinstance(usage, dict):
            return {}
        extracted: dict[str, int] = {}
        for key, value in usage.items():
            if not isinstance(key, str) or isinstance(value, bool):
                continue
            if isinstance(value, int):
                extracted[key] = value
            elif isinstance(value, float) and value.is_integer():
                extracted[key] = int(value)
        return extracted
