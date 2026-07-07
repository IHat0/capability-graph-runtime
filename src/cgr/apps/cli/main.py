"""Minimal command-line smoke test for the Capability Graph Runtime."""

import json

from cgr.kernel.contracts import ExecutionContext, ExecutionRequest
from cgr.kernel.pipeline import ModelPipeline
from cgr.kernel.model import ModelMessage, ModelRequest, ModelRole
from cgr.kernel.runtime import create_runtime


def main() -> int:
    """Execute the example Echo capability and print its JSON output."""
    runtime = create_runtime(include_examples=True)
    echo_capability = runtime.registry.get("echo").metadata.capabilities[0]
    request = ExecutionRequest[dict[str, str]](
        capability=echo_capability,
        context=ExecutionContext(),
        payload={"message": "Hello CGR!"},
    )
    result = runtime.execute_capability(request)
    print(json.dumps(result.output))
    return 0


def model_demo_main() -> int:
    """Run the deterministic model pipeline and print its JSON result."""
    runtime = create_runtime(include_mock_models=True)
    result = ModelPipeline(runtime).run("Build a tiny calculator.")
    print(json.dumps(result.model_dump()))
    return 0


def demo_main() -> int:
    """Run the end-to-end CGR demo and print one JSON object."""
    runtime = create_runtime(
        include_examples=True,
        include_builtin=True,
        include_mock_models=True,
    )
    model_pipeline = ModelPipeline(runtime).run("Build a tiny calculator.")

    calculator = runtime.registry.get("builtin.calculator")
    calculator_result = runtime.execute_capability(
        ExecutionRequest[dict[str, str]](
            capability=calculator.metadata.capabilities[0],
            context=ExecutionContext(),
            payload={"expression": "1 + 2 * 3"},
        )
    )
    text_stats = runtime.registry.get("builtin.text_stats")
    text_stats_result = runtime.execute_capability(
        ExecutionRequest[dict[str, str]](
            capability=text_stats.metadata.capabilities[0],
            context=ExecutionContext(),
            payload={
                "text": (
                    "Capability Graph Runtime\n"
                    "routes, verifies, fuses, and learns."
                )
            },
        )
    )
    output = {
        "model_pipeline": model_pipeline.model_dump(mode="json"),
        "calculator": calculator_result.output,
        "text_stats": text_stats_result.output,
        "runtime_health": runtime.health_snapshot().model_dump(mode="json"),
    }
    print(json.dumps(output))
    return 0


def openai_demo_main() -> int:
    """Run the optional OpenAI provider demo and print JSON only."""
    try:
        runtime = create_runtime(include_openai_provider=True)
        plugin = runtime.registry.get("provider.openai.responses")
        request = ExecutionRequest[ModelRequest](
            capability=plugin.metadata.capabilities[0],
            context=ExecutionContext(),
            payload=ModelRequest(
                messages=[
                    ModelMessage(
                        role=ModelRole.USER,
                        content=(
                            "Explain Capability Graph Runtime in one short "
                            "paragraph."
                        ),
                    )
                ]
            ),
        )
        result = runtime.execute_capability(request)
        print(json.dumps(result.output))
        return 0
    except Exception as exc:
        print(json.dumps({"error": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
