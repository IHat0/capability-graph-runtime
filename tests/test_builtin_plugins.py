import inspect
from typing import Any

import pytest

from cgr.kernel.contracts import ExecutionContext, ExecutionRequest, PluginState
from cgr.plugins.builtin import CalculatorPlugin, TextStatsPlugin


def execute_plugin(plugin: Any, payload: Any) -> Any:
    request = ExecutionRequest[Any](
        capability=plugin.metadata.capabilities[0],
        context=ExecutionContext(),
        payload=payload,
    )
    return plugin.execute(request)


def test_calculator_metadata_and_capability() -> None:
    plugin = CalculatorPlugin()

    assert plugin.metadata.id == "builtin.calculator"
    assert plugin.metadata.supports("calculator.evaluate")


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("1 + 2 * 3", 7),
        ("(1 + 2) * 3", 9),
        ("2.5 + 0.5", 3.0),
        ("-5 + 2", -3),
        ("9 // 2", 4),
        ("7 % 4", 3),
        ("2 ** 3", 8),
    ],
)
def test_calculator_evaluates_supported_arithmetic(
    expression: str,
    expected: int | float,
) -> None:
    result = execute_plugin(CalculatorPlugin(), {"expression": expression})

    assert result.output == {"expression": expression, "result": expected}
    assert result.duration_ms >= 0


@pytest.mark.parametrize("payload", [{}, {"expression": 123}, "1 + 2"])
def test_calculator_rejects_missing_or_invalid_expression(payload: Any) -> None:
    with pytest.raises(ValueError):
        execute_plugin(CalculatorPlugin(), payload)


@pytest.mark.parametrize("expression", ["x + 1", "sum([1, 2])"])
def test_calculator_rejects_unsupported_expression(expression: str) -> None:
    with pytest.raises(ValueError, match="Unsupported expression"):
        execute_plugin(CalculatorPlugin(), {"expression": expression})


def test_calculator_rejects_expression_over_500_characters() -> None:
    with pytest.raises(ValueError, match="maximum length"):
        execute_plugin(CalculatorPlugin(), {"expression": "1" * 501})


def test_calculator_does_not_use_dynamic_execution() -> None:
    source = inspect.getsource(CalculatorPlugin)

    assert "eval(" not in source
    assert "exec(" not in source
    assert "ast.parse" in source


def test_builtin_plugin_lifecycle_and_health() -> None:
    plugin = CalculatorPlugin()
    assert plugin.state == PluginState.DISCOVERED

    plugin.initialize()
    assert plugin.state == PluginState.RUNNING

    plugin.shutdown()
    assert plugin.state == PluginState.STOPPED


def test_text_stats_metadata_and_capability() -> None:
    plugin = TextStatsPlugin()

    assert plugin.metadata.id == "builtin.text_stats"
    assert plugin.metadata.supports("text.stats")


def test_text_stats_computes_all_counts() -> None:
    text = "Hello world\n\nSecond line"

    result = execute_plugin(TextStatsPlugin(), {"text": text})

    assert result.output == {
        "character_count": len(text),
        "word_count": 4,
        "line_count": 3,
        "non_empty_line_count": 2,
    }
    assert result.duration_ms >= 0


def test_text_stats_handles_empty_string() -> None:
    result = execute_plugin(TextStatsPlugin(), {"text": ""})

    assert result.output == {
        "character_count": 0,
        "word_count": 0,
        "line_count": 0,
        "non_empty_line_count": 0,
    }


@pytest.mark.parametrize("payload", [{}, {"text": 123}, "text"])
def test_text_stats_rejects_missing_or_invalid_text(payload: Any) -> None:
    with pytest.raises(ValueError):
        execute_plugin(TextStatsPlugin(), payload)
