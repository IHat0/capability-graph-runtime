"""Deterministic local CGR benchmark task suite."""

from .benchmark_task import BenchmarkTask


def create_local_benchmark_tasks() -> list[BenchmarkTask]:
    """Return local benchmark tasks for built-in and mock model plugins."""
    model_keys = {"text", "model_id", "usage", "metadata"}
    return [
        BenchmarkTask(
            id="calculator.simple_arithmetic",
            name="Calculator simple arithmetic",
            capability_id="calculator.evaluate",
            payload={"expression": "1 + 2 * 3"},
            expected_output={"expression": "1 + 2 * 3", "result": 7},
        ),
        BenchmarkTask(
            id="calculator.parentheses",
            name="Calculator parentheses",
            capability_id="calculator.evaluate",
            payload={"expression": "(10 + 5) / 3"},
            expected_output={"expression": "(10 + 5) / 3", "result": 5.0},
        ),
        BenchmarkTask(
            id="text_stats.simple",
            name="Text stats simple",
            capability_id="text.stats",
            payload={"text": "hello world"},
            expected_output={
                "character_count": 11,
                "word_count": 2,
                "line_count": 1,
                "non_empty_line_count": 1,
            },
        ),
        BenchmarkTask(
            id="text_stats.multiline",
            name="Text stats multiline",
            capability_id="text.stats",
            payload={"text": "CGR\n\nRuntime"},
            expected_output={
                "character_count": 12,
                "word_count": 2,
                "line_count": 3,
                "non_empty_line_count": 2,
            },
        ),
        BenchmarkTask(
            id="model.reason",
            name="Mock reasoning model",
            capability_id="model.reason",
            payload={
                "messages": [
                    {"role": "user", "content": "Explain routing."}
                ]
            },
            required_output_keys=model_keys,
        ),
        BenchmarkTask(
            id="model.code",
            name="Mock coding model",
            capability_id="model.code",
            payload={
                "messages": [
                    {"role": "user", "content": "Write a function."}
                ]
            },
            required_output_keys=model_keys,
        ),
    ]
