import json
from email.message import Message
from io import BytesIO
from typing import Any
from urllib.error import HTTPError, URLError

import pytest
from pydantic import ValidationError

from cgr.kernel.contracts import (
    ExecutionContext,
    ExecutionRequest,
    PluginState,
)
from cgr.kernel.model import ModelMessage, ModelRequest, ModelRole
from cgr.kernel.runtime import create_runtime
from cgr.plugins.providers.openai import (
    OpenAIProviderConfig,
    OpenAIResponsesClient,
    OpenAIResponsesModelPlugin,
    UrllibOpenAIResponsesClient,
)
from cgr.plugins.providers.openai import openai_client as openai_client_module


class FakeOpenAIResponsesClient:
    """Test client returning a configured provider response."""

    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.received_config: OpenAIProviderConfig | None = None
        self.received_messages: list[dict[str, str]] | None = None

    def create_response(
        self,
        config: OpenAIProviderConfig,
        input_messages: list[dict[str, str]],
    ) -> dict[str, Any]:
        self.received_config = config
        self.received_messages = input_messages
        return self.response


class RaisingOpenAIResponsesClient:
    """Test client propagating a provider failure."""

    def create_response(
        self,
        config: OpenAIProviderConfig,
        input_messages: list[dict[str, str]],
    ) -> dict[str, Any]:
        raise RuntimeError("provider unavailable")


class FakeHTTPResponse:
    """Context-managed urllib response test double."""

    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> "FakeHTTPResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


def make_config() -> OpenAIProviderConfig:
    return OpenAIProviderConfig(api_key="secret-key", model="test-model")


def make_model_request() -> ModelRequest:
    return ModelRequest(
        messages=[
            ModelMessage(role=ModelRole.SYSTEM, content="Be concise"),
            ModelMessage(role=ModelRole.USER, content="Explain CGR"),
        ]
    )


def make_execution_request(
    plugin: OpenAIResponsesModelPlugin,
    payload: Any,
) -> ExecutionRequest[ModelRequest | dict[str, Any]]:
    return ExecutionRequest[ModelRequest | dict[str, Any]].model_construct(
        capability=plugin.metadata.capabilities[0],
        context=ExecutionContext(),
        payload=payload,
    )


@pytest.mark.parametrize(
    "values",
    [
        {"api_key": ""},
        {"api_key": "key", "model": ""},
        {"api_key": "key", "timeout_seconds": 0},
    ],
)
def test_openai_config_validates_required_values(values: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        OpenAIProviderConfig.model_validate(values)


def test_openai_config_from_env_and_secret_repr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "environment-secret")
    monkeypatch.setenv("OPENAI_MODEL", "environment-model")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("OPENAI_TIMEOUT_SECONDS", "12.5")

    config = OpenAIProviderConfig.from_env()

    assert config.api_key == "environment-secret"
    assert config.model == "environment-model"
    assert config.base_url == "https://example.test/v1"
    assert config.timeout_seconds == 12.5
    assert "environment-secret" not in repr(config)


def test_openai_config_from_env_requires_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(ValueError, match="OPENAI_API_KEY is not set"):
        OpenAIProviderConfig.from_env()


def test_urllib_client_posts_expected_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_urlopen(request: Any, timeout: float) -> FakeHTTPResponse:
        captured["url"] = request.full_url
        captured["authorization"] = request.get_header("Authorization")
        captured["content_type"] = request.get_header("Content-type")
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeHTTPResponse(b'{"output_text": "hello"}')

    monkeypatch.setattr(openai_client_module, "urlopen", fake_urlopen)
    messages = [{"role": "user", "content": "hello"}]

    response = UrllibOpenAIResponsesClient().create_response(
        make_config(),
        messages,
    )

    assert response == {"output_text": "hello"}
    assert captured == {
        "url": "https://api.openai.com/v1/responses",
        "authorization": "Bearer secret-key",
        "content_type": "application/json",
        "body": {"model": "test-model", "input": messages},
        "timeout": 60.0,
    }


def test_urllib_client_translates_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = HTTPError(
        "https://example.test",
        401,
        "Unauthorized",
        hdrs=Message(),
        fp=BytesIO(b"denied"),
    )
    monkeypatch.setattr(
        openai_client_module,
        "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(error),
    )

    with pytest.raises(RuntimeError, match="401 denied"):
        UrllibOpenAIResponsesClient().create_response(make_config(), [])


def test_urllib_client_translates_url_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        openai_client_module,
        "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(URLError("offline")),
    )

    with pytest.raises(RuntimeError, match="request failed: offline"):
        UrllibOpenAIResponsesClient().create_response(make_config(), [])


def test_urllib_client_rejects_invalid_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        openai_client_module,
        "urlopen",
        lambda *args, **kwargs: FakeHTTPResponse(b"not-json"),
    )

    with pytest.raises(RuntimeError, match="returned invalid JSON"):
        UrllibOpenAIResponsesClient().create_response(make_config(), [])


def test_openai_plugin_metadata_and_model_request_execution() -> None:
    client = FakeOpenAIResponsesClient(
        {
            "id": "response-1",
            "output_text": "Provider answer",
            "usage": {
                "input_tokens": 4,
                "output_tokens": 2.0,
                "ignored": "not numeric",
            },
        }
    )
    plugin = OpenAIResponsesModelPlugin(config=make_config(), client=client)

    result = plugin.execute(make_execution_request(plugin, make_model_request()))

    assert isinstance(client, OpenAIResponsesClient)
    assert plugin.metadata.id == "provider.openai.responses"
    assert plugin.metadata.supports("model.reason")
    assert client.received_messages == [
        {"role": "system", "content": "Be concise"},
        {"role": "user", "content": "Explain CGR"},
    ]
    assert result.output == {
        "text": "Provider answer",
        "model_id": "test-model",
        "usage": {"input_tokens": 4, "output_tokens": 2},
        "metadata": {"provider": "openai", "response_id": "response-1"},
    }
    assert result.duration_ms >= 0


def test_openai_plugin_accepts_compatible_dictionary() -> None:
    client = FakeOpenAIResponsesClient({"output_text": "Dictionary answer"})
    plugin = OpenAIResponsesModelPlugin(config=make_config(), client=client)
    payload = {"messages": [{"role": "user", "content": "hello"}]}

    result = plugin.execute(make_execution_request(plugin, payload))

    assert result.output["text"] == "Dictionary answer"


def test_openai_plugin_extracts_nested_output_text() -> None:
    client = FakeOpenAIResponsesClient(
        {
            "output": [
                {"content": [{"text": "first"}, {"text": "second"}]},
                {"content": [{"text": "third"}]},
            ]
        }
    )
    plugin = OpenAIResponsesModelPlugin(config=make_config(), client=client)

    result = plugin.execute(make_execution_request(plugin, make_model_request()))

    assert result.output["text"] == "first\nsecond\nthird"


def test_openai_plugin_raises_when_response_has_no_text() -> None:
    plugin = OpenAIResponsesModelPlugin(
        config=make_config(),
        client=FakeOpenAIResponsesClient({"output": []}),
    )

    with pytest.raises(RuntimeError, match="did not contain text output"):
        plugin.execute(make_execution_request(plugin, make_model_request()))


def test_openai_plugin_rejects_invalid_payload() -> None:
    plugin = OpenAIResponsesModelPlugin(
        config=make_config(),
        client=FakeOpenAIResponsesClient({"output_text": "unused"}),
    )

    with pytest.raises(ValueError):
        plugin.execute(make_execution_request(plugin, "invalid"))


def test_openai_plugin_propagates_client_runtime_error() -> None:
    plugin = OpenAIResponsesModelPlugin(
        config=make_config(),
        client=RaisingOpenAIResponsesClient(),
    )

    with pytest.raises(RuntimeError, match="provider unavailable"):
        plugin.execute(make_execution_request(plugin, make_model_request()))


def test_openai_plugin_lifecycle_and_health() -> None:
    plugin = OpenAIResponsesModelPlugin(
        config=make_config(),
        client=FakeOpenAIResponsesClient({"output_text": "answer"}),
    )
    assert plugin.state == PluginState.DISCOVERED

    plugin.initialize()
    assert plugin.state == PluginState.RUNNING

    plugin.shutdown()
    assert plugin.state == PluginState.STOPPED


def test_bootstrap_default_does_not_require_openai_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    runtime = create_runtime()

    assert "provider.openai.responses" not in runtime.registry


def test_bootstrap_registers_openai_provider_when_key_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    runtime = create_runtime(include_openai_provider=True)

    plugin = runtime.registry.get("provider.openai.responses")
    assert isinstance(plugin, OpenAIResponsesModelPlugin)
    assert plugin.state == PluginState.RUNNING


def test_bootstrap_openai_provider_requires_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(ValueError, match="OPENAI_API_KEY is not set"):
        create_runtime(include_openai_provider=True)
