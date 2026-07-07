from typing import Any

import pytest
from pydantic import ValidationError

from cgr.kernel.contracts import ExecutionContext, ExecutionRequest
from cgr.kernel.model import ModelMessage, ModelRequest, ModelRole
from cgr.plugins.providers.openai_compatible import (
    OpenAICompatibleChatConfig,
    OpenAICompatibleChatPlugin,
)


class FakeChatClient:
    def __init__(self) -> None:
        self.messages: list[dict[str, str]] = []

    def create_chat_completion(
        self,
        config: OpenAICompatibleChatConfig,
        messages: list[dict[str, str]],
    ) -> dict[str, Any]:
        self.messages = messages
        return {
            "choices": [{"message": {"content": "provider answer"}}],
            "usage": {"prompt_tokens": 4, "ignored": 1.5},
        }


@pytest.mark.parametrize(
    ("field", "value"),
    [("api_key", ""), ("model", ""), ("base_url", ""), ("provider_name", "")],
)
def test_chat_config_rejects_empty_required_values(field: str, value: str) -> None:
    values = {
        "api_key": "secret",
        "model": "model",
        "base_url": "https://example.test/v1",
        "provider_name": "provider",
    }
    values[field] = value

    with pytest.raises(ValidationError):
        OpenAICompatibleChatConfig.model_validate(values)


def test_chat_config_rejects_non_positive_timeout() -> None:
    with pytest.raises(ValidationError):
        OpenAICompatibleChatConfig(
            api_key="secret", model="model", base_url="https://example.test", timeout_seconds=0
        )


def test_chat_config_from_env_reads_prefixed_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALT_API_KEY", "secret")
    monkeypatch.setenv("ALT_MODEL", "glm-4.7")
    monkeypatch.setenv("ALT_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("ALT_TIMEOUT_SECONDS", "12.5")
    monkeypatch.setenv("ALT_PROVIDER_NAME", "glm")

    config = OpenAICompatibleChatConfig.from_env("ALT")

    assert config.model == "glm-4.7"
    assert config.timeout_seconds == 12.5
    assert config.provider_name == "glm"
    assert "secret" not in repr(config)


@pytest.mark.parametrize("suffix", ["API_KEY", "MODEL", "BASE_URL"])
def test_chat_config_from_env_requires_core_values(
    suffix: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("API_KEY", "MODEL", "BASE_URL"):
        monkeypatch.setenv(f"REQ_{name}", "value")
    monkeypatch.delenv(f"REQ_{suffix}")

    with pytest.raises(ValueError, match=f"REQ_{suffix} is not set"):
        OpenAICompatibleChatConfig.from_env("REQ")


def test_chat_plugin_converts_messages_and_extracts_completion() -> None:
    client = FakeChatClient()
    plugin = OpenAICompatibleChatPlugin(
        config=OpenAICompatibleChatConfig(
            api_key="secret",
            model="glm-4.7",
            base_url="https://example.test/v1",
            provider_name="glm",
        ),
        client=client,
    )
    request = ExecutionRequest[ModelRequest | dict[str, Any]](
        capability=plugin.metadata.capabilities[0],
        context=ExecutionContext(),
        payload=ModelRequest(
            messages=[ModelMessage(role=ModelRole.USER, content="hello")]
        ),
    )

    result = plugin.execute(request)

    assert client.messages == [{"role": "user", "content": "hello"}]
    assert result.output == {
        "text": "provider answer",
        "model_id": "glm-4.7",
        "usage": {"prompt_tokens": 4},
        "metadata": {"provider": "glm"},
    }


def test_chat_plugin_rejects_missing_completion_text() -> None:
    class EmptyClient:
        def create_chat_completion(
            self,
            config: OpenAICompatibleChatConfig,
            messages: list[dict[str, str]],
        ) -> dict[str, Any]:
            return {"choices": []}

    plugin = OpenAICompatibleChatPlugin(
        config=OpenAICompatibleChatConfig(
            api_key="secret", model="model", base_url="https://example.test"
        ),
        client=EmptyClient(),
    )

    with pytest.raises(RuntimeError, match="did not contain text"):
        plugin.execute(
            ExecutionRequest(
                capability=plugin.metadata.capabilities[0],
                context=ExecutionContext(),
                payload={"messages": [{"role": "user", "content": "hello"}]},
            )
        )
