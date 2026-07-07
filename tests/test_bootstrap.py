import pytest

from cgr.kernel.contracts import (
    ExecutionContext,
    ExecutionRequest,
    PluginState,
)
from cgr.kernel.runtime import KernelRuntime, create_runtime
from cgr.plugins.builtin import CalculatorPlugin, TextStatsPlugin
from cgr.plugins.examples import EchoPlugin
from cgr.plugins.model import MockCodingModelPlugin, MockReasoningModelPlugin
from cgr.shared.events import EventType


def test_create_runtime_returns_empty_kernel_runtime_by_default() -> None:
    runtime = create_runtime()

    assert isinstance(runtime, KernelRuntime)
    assert runtime.registry.plugin_ids() == []


def test_create_runtime_with_examples_registers_running_echo_plugin() -> None:
    runtime = create_runtime(include_examples=True)

    plugin = runtime.registry.get("echo")
    assert isinstance(plugin, EchoPlugin)
    assert plugin.state == PluginState.RUNNING
    assert runtime.registry.plugin_ids() == ["echo"]


def test_bootstrap_echo_executes_by_capability() -> None:
    runtime = create_runtime(include_examples=True)
    capability = runtime.registry.get("echo").metadata.capabilities[0]
    request = ExecutionRequest[dict[str, str]](
        capability=capability,
        context=ExecutionContext(),
        payload={"message": "Hello from bootstrap!"},
    )

    result = runtime.execute_capability(request)

    assert result.output == {"message": "Hello from bootstrap!"}


def test_bootstrap_with_examples_emits_plugin_registered_event() -> None:
    runtime = create_runtime(include_examples=True)

    events = runtime.event_bus.history_by_type(EventType.PLUGIN_REGISTERED)
    assert len(events) == 1
    assert events[0].payload == {
        "plugin_id": "echo",
        "plugin_name": "Echo Plugin",
        "plugin_version": "1.0.0",
    }


def test_create_runtime_with_builtin_registers_both_plugins() -> None:
    runtime = create_runtime(include_builtin=True)

    assert runtime.registry.plugin_ids() == [
        "builtin.calculator",
        "builtin.text_stats",
    ]
    assert isinstance(
        runtime.registry.get("builtin.calculator"),
        CalculatorPlugin,
    )
    assert isinstance(
        runtime.registry.get("builtin.text_stats"),
        TextStatsPlugin,
    )
    assert runtime.registry.get("builtin.calculator").state == PluginState.RUNNING
    assert runtime.registry.get("builtin.text_stats").state == PluginState.RUNNING


def test_bootstrap_calculator_executes_by_capability() -> None:
    runtime = create_runtime(include_builtin=True)
    plugin = runtime.registry.get("builtin.calculator")
    request = ExecutionRequest[dict[str, str]](
        capability=plugin.metadata.capabilities[0],
        context=ExecutionContext(),
        payload={"expression": "6 * 7"},
    )

    result = runtime.execute_capability(request)

    assert result.output == {"expression": "6 * 7", "result": 42}


def test_bootstrap_text_stats_executes_by_capability() -> None:
    runtime = create_runtime(include_builtin=True)
    plugin = runtime.registry.get("builtin.text_stats")
    request = ExecutionRequest[dict[str, str]](
        capability=plugin.metadata.capabilities[0],
        context=ExecutionContext(),
        payload={"text": "one two"},
    )

    result = runtime.execute_capability(request)

    assert result.output == {
        "character_count": 7,
        "word_count": 2,
        "line_count": 1,
        "non_empty_line_count": 1,
    }


def test_bootstrap_examples_and_builtin_register_all_plugins() -> None:
    runtime = create_runtime(include_examples=True, include_builtin=True)

    assert runtime.registry.plugin_ids() == [
        "echo",
        "builtin.calculator",
        "builtin.text_stats",
    ]


def test_create_runtime_with_mock_models_registers_both_plugins() -> None:
    runtime = create_runtime(include_mock_models=True)

    assert runtime.registry.plugin_ids() == [
        "mock.reasoning_model",
        "mock.coding_model",
    ]
    assert isinstance(
        runtime.registry.get("mock.reasoning_model"),
        MockReasoningModelPlugin,
    )
    assert isinstance(
        runtime.registry.get("mock.coding_model"),
        MockCodingModelPlugin,
    )


@pytest.mark.parametrize(
    ("plugin_id", "prompt", "expected_text"),
    [
        (
            "mock.reasoning_model",
            "reason",
            "Reasoned answer: reason",
        ),
        ("mock.coding_model", "code", "Code response: code"),
    ],
)
def test_bootstrap_mock_model_executes_by_capability(
    plugin_id: str,
    prompt: str,
    expected_text: str,
) -> None:
    runtime = create_runtime(include_mock_models=True)
    plugin = runtime.registry.get(plugin_id)
    request = ExecutionRequest[dict[str, object]](
        capability=plugin.metadata.capabilities[0],
        context=ExecutionContext(),
        payload={
            "messages": [{"role": "user", "content": prompt}],
        },
    )

    result = runtime.execute_capability(request)

    assert result.output["text"] == expected_text


def test_bootstrap_all_groups_registers_every_plugin() -> None:
    runtime = create_runtime(
        include_examples=True,
        include_builtin=True,
        include_mock_models=True,
    )

    assert runtime.registry.plugin_ids() == [
        "echo",
        "builtin.calculator",
        "builtin.text_stats",
        "mock.reasoning_model",
        "mock.coding_model",
    ]
