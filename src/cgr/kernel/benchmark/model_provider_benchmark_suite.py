"""Small benchmark task suite for real model provider plugins."""

from .benchmark_task import BenchmarkTask


def create_model_provider_benchmark_tasks() -> list[BenchmarkTask]:
    """Return low-cost schema-verified reasoning tasks for model providers."""
    required_keys = {"text", "model_id", "usage", "metadata"}
    prompts = [
        (
            "provider.short_explanation",
            "Provider short explanation",
            "Explain Capability Graph Runtime in one short paragraph.",
        ),
        (
            "provider.routing_explanation",
            "Provider routing explanation",
            "Explain why capability routing is useful in AI systems.",
        ),
        (
            "provider.benchmark_summary",
            "Provider benchmark summary",
            "Summarize the purpose of a benchmark harness in one paragraph.",
        ),
    ]
    return [
        BenchmarkTask(
            id=task_id,
            name=name,
            capability_id="model.reason",
            payload={
                "messages": [{"role": "user", "content": prompt}],
            },
            required_output_keys=required_keys,
        )
        for task_id, name, prompt in prompts
    ]
