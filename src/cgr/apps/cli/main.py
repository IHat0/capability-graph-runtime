"""Command-line demos for the Capability Graph Runtime."""

import argparse
import json
import os

from cgr.kernel.benchmark import (
    BenchmarkExporter,
    BenchmarkRunner,
    create_local_benchmark_tasks,
    create_model_provider_benchmark_tasks,
)
from cgr.kernel.booster import (
    BoosterBenchmarkRunner,
    BoosterDomain,
    BoosterEngine,
    BoosterTask,
)
from cgr.kernel.coding import CodeTestCase, PythonTestRunner
from cgr.kernel.coding.hard_coding_suite import create_hard_coding_tasks
from cgr.kernel.coding.repo_v0_benchmarks import create_repo_v0_tasks
from cgr.kernel.coding.v1_benchmarks import create_coding_v1_tasks
from cgr.kernel.coding.v1_runner import CodingV1Runner
from cgr.kernel.contracts import ExecutionContext, ExecutionRequest
from cgr.kernel.model import ModelMessage, ModelRequest, ModelRole
from cgr.kernel.pipeline import ModelPipeline
from cgr.kernel.runtime import KernelRuntime, create_runtime
from cgr.kernel.swe import SWEABRunner, SWEEvalResult, SWETask, create_local_swe_tasks
from cgr.plugins.agents import (
    LocalBaselineCodingProvider,
    LocalBoosterBaseModelPlugin,
    LocalBoosterCriticModelPlugin,
    LocalMultiCodingProvider,
    LocalSingleCodingProvider,
    MultiModelCodingAgentPlugin,
    SingleModelCodingAgentPlugin,
)
from cgr.plugins.providers.openai_compatible import (
    OpenAICompatibleChatConfig,
    OpenAICompatibleChatPlugin,
)


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


def benchmark_main(argv: list[str] | None = None) -> int:
    """Run the deterministic local benchmark suite and optionally export it."""
    parser = argparse.ArgumentParser(description="Run the local CGR benchmark.")
    parser.add_argument("--json-out", help="Path for formatted JSON results.")
    parser.add_argument("--markdown-out", help="Path for the Markdown report.")
    args = parser.parse_args(argv)

    runtime = create_runtime(include_builtin=True, include_mock_models=True)
    result = BenchmarkRunner(runtime).run_suite(
        "CGR Local Benchmark",
        create_local_benchmark_tasks(),
    )
    exporter = BenchmarkExporter()
    if args.json_out is not None:
        exporter.write_json(result, args.json_out)
    if args.markdown_out is not None:
        exporter.write_markdown(result, args.markdown_out)
    print(json.dumps(result.model_dump(mode="json")))
    return 0


def openai_benchmark_main(argv: list[str] | None = None) -> int:
    """Run the optional OpenAI provider benchmark and print JSON only."""
    parser = argparse.ArgumentParser(
        description="Run the optional OpenAI provider benchmark."
    )
    parser.add_argument("--json-out", help="Path for formatted JSON results.")
    parser.add_argument("--markdown-out", help="Path for the Markdown report.")
    args = parser.parse_args(argv)

    if not os.getenv("OPENAI_API_KEY"):
        print(json.dumps({"error": "OPENAI_API_KEY is not set."}))
        return 1

    try:
        runtime = create_runtime(include_openai_provider=True)
        result = BenchmarkRunner(runtime).run_suite(
            "openai_provider",
            create_model_provider_benchmark_tasks(),
        )
        exporter = BenchmarkExporter()
        if args.json_out is not None:
            exporter.write_json(result, args.json_out)
        if args.markdown_out is not None:
            exporter.write_markdown(result, args.markdown_out)
        print(json.dumps(result.model_dump(mode="json")))
        return 0
    except Exception as exc:
        print(json.dumps({"error": str(exc)}))
        return 1


def coding_ab_local_main() -> int:
    """Run the local baseline versus coding-agent evaluation."""
    runtime = KernelRuntime()
    baseline = LocalBaselineCodingProvider()
    single_provider = LocalSingleCodingProvider()
    multi_provider = LocalMultiCodingProvider()
    runtime.register_plugin(baseline)
    runtime.register_plugin(single_provider)
    runtime.register_plugin(multi_provider)
    single = SingleModelCodingAgentPlugin(
        runtime, model_capability_id="model.code.single"
    )
    multi = MultiModelCodingAgentPlugin(
        runtime,
        draft_capability_id="model.code.multi",
        critique_capability_id="model.reason.multi",
    )
    runtime.register_plugin(single)
    runtime.register_plugin(multi)
    result = SWEABRunner(runtime).run_suite(
        "local_coding_ab",
        create_local_swe_tasks(),
        baseline.metadata.id,
        single.metadata.id,
        multi.metadata.id,
    )
    print(json.dumps(result.model_dump(mode="json")))
    return 0


def coding_ab_real_main() -> int:
    """Run coding A/B evaluation against explicit real provider settings."""
    try:
        result = _run_real_coding_ab("real_coding_ab", create_local_swe_tasks())
        print(json.dumps(result.model_dump(mode="json")))
        return 0
    except Exception as exc:
        print(json.dumps({"error": str(exc)}))
        return 1


def coding_ab_hard_main(argv: list[str] | None = None) -> int:
    """Run the hard executable coding suite against explicit real providers."""
    parser = argparse.ArgumentParser(description="Run the hard coding A/B suite.")
    parser.add_argument("--max-tasks", type=int, help="Run only the first N tasks.")
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Allow one additional multi-model semantic repair attempt.",
    )
    parser.add_argument("--task-id", help="Run one hard-suite task by id.")
    parser.add_argument(
        "--debug-trace",
        action="store_true",
        help="Include coding-agent candidate and repair trace fields.",
    )
    args = parser.parse_args(argv)
    try:
        tasks = create_hard_coding_tasks()
        if args.max_tasks is not None:
            if args.max_tasks <= 0:
                raise ValueError("--max-tasks must be positive.")
            tasks = tasks[: args.max_tasks]
        if args.task_id is not None:
            tasks = [task for task in tasks if task.id == args.task_id]
            if not tasks:
                raise ValueError(f"Unknown hard coding task '{args.task_id}'.")
        result = _run_real_coding_ab(
            "hard_coding_ab",
            tasks,
            multi_repair_attempts=4 if args.retry_failed else 3,
            debug_trace=args.debug_trace,
        )
        print(
            json.dumps(
                result.model_dump(
                    mode="json", exclude_none=not args.debug_trace
                )
            )
        )
        return 0
    except Exception as exc:
        print(json.dumps({"error": str(exc)}))
        return 1


def coding_ab_v1_main(argv: list[str] | None = None) -> int:
    """Run the 26-task Coding v1 suite against explicit real providers."""
    parser = argparse.ArgumentParser(description="Run the Coding v1 A/B suite.")
    parser.add_argument("--max-tasks", type=int, help="Run only the first N tasks.")
    parser.add_argument("--task-id", help="Run one Coding v1 task by id.")
    parser.add_argument("--runs", type=int, default=1, help="Repeat the suite N times.")
    parser.add_argument(
        "--reference-check",
        action="store_true",
        help="Run bundled reference files against visible and hidden tests only.",
    )
    parser.add_argument(
        "--debug-trace",
        action="store_true",
        help="Include coding-agent candidate and repair trace fields.",
    )
    args = parser.parse_args(argv)
    try:
        tasks = create_coding_v1_tasks()
        if args.task_id is not None:
            tasks = [task for task in tasks if task.id == args.task_id]
            if not tasks:
                raise ValueError(f"Unknown Coding v1 task '{args.task_id}'.")
        if args.max_tasks is not None:
            if args.max_tasks <= 0:
                raise ValueError("--max-tasks must be positive.")
            tasks = tasks[: args.max_tasks]
        if args.reference_check:
            cases = []
            for task in tasks:
                passed, messages = PythonTestRunner().run(
                    task.expected_files,
                    task.scoring_test_files,
                    task.scoring_test_commands,
                )
                cases.append(
                    {
                        "task_id": task.id,
                        "passed": passed,
                        "failure_summary": None if passed else messages[-1],
                    }
                )
            output = {
                "suite_name": "coding_v1_reference",
                "total_tasks": len(tasks),
                "passed_tasks": sum(
                    1 for case in cases if case["passed"] is True
                ),
                "results": cases,
            }
            print(json.dumps(output))
            return 0 if all(case["passed"] is True for case in cases) else 1
        report = CodingV1Runner(
            lambda selected, debug: _run_real_coding_ab(
                "coding_v1",
                selected,
                debug_trace=debug,
            )
        ).run(tasks, runs=args.runs, debug_trace=args.debug_trace)
        print(json.dumps(report))
        return 0
    except Exception as exc:
        print(json.dumps({"error": str(exc)}))
        return 1


def coding_ab_repo_v0_main(argv: list[str] | None = None) -> int:
    """Run the repo-style Coding v0 suite against explicit real providers."""
    parser = argparse.ArgumentParser(
        description="Run the repo-style Coding v0 A/B suite."
    )
    parser.add_argument("--max-tasks", type=int, help="Run only the first N tasks.")
    parser.add_argument("--task-id", help="Run one repo-v0 task by id.")
    parser.add_argument("--runs", type=int, default=1, help="Repeat the suite N times.")
    parser.add_argument(
        "--reference-check",
        action="store_true",
        help="Run bundled reference files against visible and hidden tests only.",
    )
    parser.add_argument(
        "--debug-trace",
        action="store_true",
        help="Include coding-agent candidate and repair trace fields.",
    )
    args = parser.parse_args(argv)
    try:
        tasks = create_repo_v0_tasks()
        if args.task_id is not None:
            tasks = [task for task in tasks if task.id == args.task_id]
            if not tasks:
                raise ValueError(f"Unknown repo-v0 task '{args.task_id}'.")
        if args.max_tasks is not None:
            if args.max_tasks <= 0:
                raise ValueError("--max-tasks must be positive.")
            tasks = tasks[: args.max_tasks]
        if args.reference_check:
            cases = []
            for task in tasks:
                files = {**task.files, **task.expected_files}
                passed, messages = PythonTestRunner().run(
                    files,
                    task.scoring_test_files,
                    task.scoring_test_commands,
                )
                cases.append(
                    {
                        "task_id": task.id,
                        "passed": passed,
                        "failure_summary": None if passed else messages[-1],
                    }
                )
            output = {
                "suite_name": "coding_repo_v0_reference",
                "total_tasks": len(tasks),
                "passed_tasks": sum(
                    1 for case in cases if case["passed"] is True
                ),
                "results": cases,
            }
            print(json.dumps(output))
            return 0 if all(case["passed"] is True for case in cases) else 1
        report = CodingV1Runner(
            lambda selected, debug: _run_real_coding_ab(
                "coding_repo_v0",
                selected,
                debug_trace=debug,
            ),
            suite_name="coding_repo_v0",
        ).run(tasks, runs=args.runs, debug_trace=args.debug_trace)
        print(json.dumps(report))
        return 0
    except Exception as exc:
        print(json.dumps({"error": str(exc)}))
        return 1


def _run_real_coding_ab(
    suite_name: str,
    tasks: list[SWETask],
    multi_repair_attempts: int = 3,
    debug_trace: bool = False,
) -> SWEEvalResult:
    draft_config = OpenAICompatibleChatConfig.from_env("CGR_DRAFT")
    critic_config = OpenAICompatibleChatConfig.from_env("CGR_CRITIC")
    runtime = KernelRuntime()
    draft = OpenAICompatibleChatPlugin(
        config=draft_config,
        capability_id="model.code",
        plugin_id="provider.coding.draft",
    )
    critic = OpenAICompatibleChatPlugin(
        config=critic_config,
        capability_id="model.reason",
        plugin_id="provider.coding.critic",
    )
    runtime.register_plugin(draft)
    runtime.register_plugin(critic)
    single = SingleModelCodingAgentPlugin(runtime)
    multi = MultiModelCodingAgentPlugin(
        runtime, max_repair_attempts=multi_repair_attempts
    )
    runtime.register_plugin(single)
    runtime.register_plugin(multi)
    return SWEABRunner(runtime).run_suite(
        suite_name,
        tasks,
        draft.metadata.id,
        single.metadata.id,
        multi.metadata.id,
        debug_trace=debug_trace,
    )


def boost_local_main() -> int:
    """Run the deterministic local Booster Engine comparison path."""
    runtime = KernelRuntime()
    runtime.register_plugin(LocalBoosterBaseModelPlugin())
    runtime.register_plugin(LocalBoosterCriticModelPlugin())
    engine = BoosterEngine(
        runtime,
        base_capability_id="model.code",
        critic_capability_id="model.reason",
    )
    tasks = [
        BoosterTask(
            id="local.greeting",
            domain=BoosterDomain.CODING,
            prompt='Change the program so it prints "hello CGR".',
            input_data={"files": {"app.py": 'print("hello")\n'}},
            expected_output={"app.py": 'print("hello CGR")\n'},
            test_files={
                "test_task.py": (
                    "import subprocess, sys\n"
                    "result = subprocess.run([sys.executable, 'app.py'], "
                    "capture_output=True, text=True)\n"
                    "assert result.stdout == 'hello CGR\\n'\n"
                )
            },
            test_commands=[
                CodeTestCase(
                    name="run_greeting_test", command=["python", "test_task.py"]
                )
            ],
        ),
        BoosterTask(
            id="local.add",
            domain=BoosterDomain.CODING,
            prompt="Fix add so it returns a + b.",
            input_data={
                "files": {
                    "math_utils.py": "def add(a, b):\n    return a - b\n"
                }
            },
            expected_output={
                "math_utils.py": "def add(a, b):\n    return a + b\n"
            },
            test_files={
                "test_task.py": (
                    "from math_utils import add\n"
                    "assert add(1, 2) == 3\n"
                    "assert add(-5, 5) == 0\n"
                    "assert add(10, -3) == 7\n"
                )
            },
            test_commands=[
                CodeTestCase(name="run_add_test", command=["python", "test_task.py"])
            ],
        ),
        BoosterTask(
            id="local.is_even",
            domain=BoosterDomain.CODING,
            prompt=(
                "Fix is_even so it returns True for even numbers and False "
                "for odd numbers."
            ),
            input_data={
                "files": {
                    "number_utils.py": "def is_even(n):\n    return n % 2 == 1\n"
                }
            },
            expected_output={
                "number_utils.py": "def is_even(n):\n    return n % 2 == 0\n"
            },
            test_files={
                "test_task.py": (
                    "from number_utils import is_even\n"
                    "assert is_even(2) is True\n"
                    "assert is_even(3) is False\n"
                    "assert is_even(0) is True\n"
                    "assert is_even(-4) is True\n"
                )
            },
            test_commands=[
                CodeTestCase(
                    name="run_is_even_test", command=["python", "test_task.py"]
                )
            ],
        ),
    ]
    report = BoosterBenchmarkRunner(engine).run("local_booster", tasks)
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
