from typing import Any

import pytest
from pydantic import ValidationError

from cgr.kernel.competition import CompetitionEngine
from cgr.kernel.contracts import ExecutionContext, ExecutionRequest, PluginState
from cgr.kernel.fusion import FusionEngine
from cgr.kernel.model import ModelMessage, ModelRequest, ModelResponse, ModelRole
from cgr.kernel.runtime import KernelRuntime
from cgr.kernel.verification import SchemaVerifier
from cgr.plugins.model import MockCodingModelPlugin, MockReasoningModelPlugin


def make_model_request(prompt: str = "Solve this") -> ModelRequest:
    return ModelRequest(
        messages=[
            ModelMessage(role=ModelRole.SYSTEM, content="Be deterministic"),
            ModelMessage(role=ModelRole.USER, content=prompt),
        ]
    )


def execute_model_plugin(plugin: Any, payload: Any) -> Any:
    request = ExecutionRequest[Any](
        capability=plugin.metadata.capabilities[0],
        context=ExecutionContext(),
        payload=payload,
    )
    return plugin.execute(request)


def test_model_role_values() -> None:
    assert [role.value for role in ModelRole] == [
        "system",
        "user",
        "assistant",
        "tool",
    ]


def test_model_message_is_immutable_and_rejects_empty_content() -> None:
    message = ModelMessage(role=ModelRole.USER, content="hello")

    with pytest.raises(ValidationError):
        message.content = "changed"
    with pytest.raises(ValidationError):
        ModelMessage(role=ModelRole.USER, content="")


def test_model_request_is_immutable_and_rejects_empty_messages() -> None:
    request = make_model_request()

    with pytest.raises(ValidationError):
        request.temperature = 1.0
    with pytest.raises(ValidationError):
        ModelRequest(messages=[])


@pytest.mark.parametrize("temperature", [-0.1, 2.1])
def test_model_request_rejects_invalid_temperature(temperature: float) -> None:
    with pytest.raises(ValidationError):
        ModelRequest(
            messages=[ModelMessage(role=ModelRole.USER, content="hello")],
            temperature=temperature,
        )


@pytest.mark.parametrize("max_tokens", [0, -1])
def test_model_request_rejects_non_positive_max_tokens(max_tokens: int) -> None:
    with pytest.raises(ValidationError):
        ModelRequest(
            messages=[ModelMessage(role=ModelRole.USER, content="hello")],
            max_tokens=max_tokens,
        )


def test_latest_user_message_returns_most_recent_user_content() -> None:
    request = ModelRequest(
        messages=[
            ModelMessage(role=ModelRole.USER, content="first"),
            ModelMessage(role=ModelRole.ASSISTANT, content="reply"),
            ModelMessage(role=ModelRole.USER, content="latest"),
        ]
    )

    assert request.latest_user_message == "latest"


def test_latest_user_message_returns_empty_without_user_message() -> None:
    request = ModelRequest(
        messages=[ModelMessage(role=ModelRole.SYSTEM, content="system")]
    )

    assert request.latest_user_message == ""


def test_model_response_is_immutable_and_validates_required_text() -> None:
    response = ModelResponse(text="response", model_id="model")

    with pytest.raises(ValidationError):
        response.text = "changed"
    with pytest.raises(ValidationError):
        ModelResponse(text="", model_id="model")
    with pytest.raises(ValidationError):
        ModelResponse(text="response", model_id="")


def test_model_response_rejects_negative_usage() -> None:
    with pytest.raises(ValidationError):
        ModelResponse(
            text="response",
            model_id="model",
            usage={"tokens": -1},
        )


@pytest.mark.parametrize(
    ("plugin_type", "plugin_id", "capability_id", "prefix"),
    [
        (
            MockReasoningModelPlugin,
            "mock.reasoning_model",
            "model.reason",
            "Reasoned answer:",
        ),
        (
            MockCodingModelPlugin,
            "mock.coding_model",
            "model.code",
            "Code response:",
        ),
    ],
)
def test_mock_model_metadata_and_model_request_execution(
    plugin_type: type[MockReasoningModelPlugin] | type[MockCodingModelPlugin],
    plugin_id: str,
    capability_id: str,
    prefix: str,
) -> None:
    plugin = plugin_type()

    assert plugin.metadata.id == plugin_id
    assert plugin.metadata.supports(capability_id)
    result = execute_model_plugin(plugin, make_model_request("answer me"))
    assert result.output["text"] == f"{prefix} answer me"
    assert result.output["model_id"] == plugin_id
    assert result.output["usage"] == {
        "input_messages": 2,
        "output_characters": len(f"{prefix} answer me"),
    }


@pytest.mark.parametrize(
    ("plugin", "prefix"),
    [
        (MockReasoningModelPlugin(), "Reasoned answer:"),
        (MockCodingModelPlugin(), "Code response:"),
    ],
)
def test_mock_models_execute_compatible_dictionary(
    plugin: MockReasoningModelPlugin | MockCodingModelPlugin,
    prefix: str,
) -> None:
    result = execute_model_plugin(
        plugin,
        {"messages": [{"role": "user", "content": "from dict"}]},
    )

    assert result.output["text"] == f"{prefix} from dict"


@pytest.mark.parametrize(
    "plugin",
    [MockReasoningModelPlugin(), MockCodingModelPlugin()],
)
def test_mock_models_reject_invalid_payload(
    plugin: MockReasoningModelPlugin | MockCodingModelPlugin,
) -> None:
    with pytest.raises(ValueError):
        execute_model_plugin(plugin, "invalid")


def test_mock_model_lifecycle() -> None:
    plugin = MockReasoningModelPlugin()
    assert plugin.state == PluginState.DISCOVERED

    plugin.initialize()
    assert plugin.state == PluginState.RUNNING

    plugin.shutdown()
    assert plugin.state == PluginState.STOPPED


class SecondReasoningPlugin(MockReasoningModelPlugin):
    """Second deterministic reasoning candidate for architecture tests."""

    def __init__(self) -> None:
        super().__init__()
        self._metadata = self.metadata.model_copy(
            update={"id": "mock.reasoning_model.second"}
        )


def make_reasoning_execution_request() -> ExecutionRequest[ModelRequest]:
    plugin = MockReasoningModelPlugin()
    return ExecutionRequest(
        capability=plugin.metadata.capabilities[0],
        context=ExecutionContext(),
        payload=make_model_request("integrate"),
    )


def test_competition_engine_competes_over_reasoning_models() -> None:
    runtime = KernelRuntime()
    runtime.register_plugin(MockReasoningModelPlugin())
    runtime.register_plugin(SecondReasoningPlugin())

    result = CompetitionEngine(runtime).compete(
        make_reasoning_execution_request()
    )

    assert result.winner_plugin_id == "mock.reasoning_model"
    assert result.successful_plugin_ids == [
        "mock.reasoning_model",
        "mock.reasoning_model.second",
    ]


def test_fusion_engine_collects_model_outputs() -> None:
    runtime = KernelRuntime()
    runtime.register_plugin(MockReasoningModelPlugin())
    runtime.register_plugin(SecondReasoningPlugin())

    result = FusionEngine(runtime).fuse(make_reasoning_execution_request())

    fused_output = result.fused_output
    assert isinstance(fused_output, list)
    assert len(fused_output) == 2
    assert fused_output[0]["model_id"] == "mock.reasoning_model"
    assert fused_output[1]["model_id"] == (
        "mock.reasoning_model.second"
    )


def test_schema_verifier_accepts_model_response_dictionary() -> None:
    output = execute_model_plugin(
        MockReasoningModelPlugin(),
        make_model_request(),
    ).output

    verification = SchemaVerifier(
        "model-response",
        {"text", "model_id"},
    ).verify(output)

    assert verification.passed is True
