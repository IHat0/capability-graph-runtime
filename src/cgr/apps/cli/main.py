"""Minimal command-line smoke test for the Capability Graph Runtime."""

import json

from cgr.kernel.contracts import ExecutionContext, ExecutionRequest
from cgr.kernel.pipeline import ModelPipeline
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


if __name__ == "__main__":
    raise SystemExit(main())
