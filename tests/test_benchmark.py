import pytest
from pydantic import ValidationError

from cgr.kernel.benchmark import (
    BenchmarkCaseResult,
    BenchmarkRunner,
    BenchmarkSuiteResult,
    BenchmarkTask,
    create_local_benchmark_tasks,
)
from cgr.kernel.runtime import create_runtime


def calculator_task() -> BenchmarkTask:
    return BenchmarkTask(
        id="calculator.test",
        name="Calculator test",
        capability_id="calculator.evaluate",
        payload={"expression": "2 + 3"},
        expected_output={"expression": "2 + 3", "result": 5},
    )


def test_benchmark_task_is_immutable() -> None:
    task = calculator_task()

    with pytest.raises(ValidationError):
        task.name = "Changed"


@pytest.mark.parametrize("field", ["id", "name", "capability_id"])
def test_benchmark_task_rejects_empty_required_field(field: str) -> None:
    values = {
        "id": "task",
        "name": "Task",
        "capability_id": "echo",
        "payload": {},
    }
    values[field] = ""

    with pytest.raises(ValidationError):
        BenchmarkTask.model_validate(values)


def test_benchmark_case_result_is_immutable_and_validates_duration() -> None:
    result = BenchmarkCaseResult(
        task_id="task",
        capability_id="echo",
        plugin_id=None,
        succeeded=True,
        verified=True,
        duration_ms=0.0,
    )

    with pytest.raises(ValidationError):
        result.succeeded = False
    with pytest.raises(ValidationError):
        BenchmarkCaseResult.model_validate(
            {**result.model_dump(), "duration_ms": -1.0}
        )


def make_suite_result(
    total_tasks: int,
    succeeded_tasks: int,
    verified_tasks: int,
) -> BenchmarkSuiteResult:
    return BenchmarkSuiteResult(
        suite_name="Suite",
        total_tasks=total_tasks,
        succeeded_tasks=succeeded_tasks,
        verified_tasks=verified_tasks,
        failed_tasks=total_tasks - succeeded_tasks,
        average_duration_ms=0.0,
        results=[],
    )


def test_benchmark_suite_result_is_immutable_and_computes_rates() -> None:
    result = make_suite_result(4, 3, 2)

    assert result.success_rate == 0.75
    assert result.verification_rate == 0.5
    with pytest.raises(ValidationError):
        result.suite_name = "Changed"


def test_benchmark_suite_zero_task_rates_are_zero() -> None:
    result = make_suite_result(0, 0, 0)

    assert result.success_rate == 0.0
    assert result.verification_rate == 0.0


def test_local_benchmark_tasks_are_unique_and_cover_capabilities() -> None:
    tasks = create_local_benchmark_tasks()

    assert tasks
    assert len({task.id for task in tasks}) == len(tasks)
    capability_ids = {task.capability_id for task in tasks}
    assert {
        "calculator.evaluate",
        "text.stats",
        "model.reason",
        "model.code",
    }.issubset(capability_ids)


def test_runner_executes_and_exactly_verifies_calculator_task() -> None:
    runtime = create_runtime(include_builtin=True)

    suite = BenchmarkRunner(runtime).run_suite("Calculator", [calculator_task()])

    assert suite.total_tasks == 1
    assert suite.succeeded_tasks == 1
    assert suite.verified_tasks == 1
    assert suite.failed_tasks == 0
    assert suite.average_duration_ms >= 0
    case = suite.results[0]
    assert case.succeeded is True
    assert case.verified is True
    assert case.output == {"expression": "2 + 3", "result": 5}


def test_runner_verifies_required_output_keys() -> None:
    runtime = create_runtime(include_mock_models=True)
    task = BenchmarkTask(
        id="reasoning.schema",
        name="Reasoning schema",
        capability_id="model.reason",
        payload={
            "messages": [{"role": "user", "content": "Explain routing."}]
        },
        required_output_keys={"text", "model_id", "usage", "metadata"},
    )

    case = BenchmarkRunner(runtime).run_suite("Schema", [task]).results[0]

    assert case.succeeded is True
    assert case.verified is True


def test_runner_continues_after_failure_and_records_error() -> None:
    runtime = create_runtime(include_builtin=True)
    missing = BenchmarkTask(
        id="missing",
        name="Missing capability",
        capability_id="missing.capability",
        payload={},
    )

    suite = BenchmarkRunner(runtime).run_suite(
        "Continue",
        [missing, calculator_task()],
    )

    assert suite.total_tasks == 2
    assert suite.succeeded_tasks == 1
    assert suite.verified_tasks == 1
    assert suite.failed_tasks == 1
    assert [result.task_id for result in suite.results] == [
        "missing",
        "calculator.test",
    ]
    failure = suite.results[0]
    assert failure.error_type == "CapabilityNotFoundError"
    assert "missing.capability" in (failure.error_message or "")


def test_full_local_benchmark_succeeds_and_verifies_all_tasks() -> None:
    runtime = create_runtime(include_builtin=True, include_mock_models=True)
    tasks = create_local_benchmark_tasks()

    suite = BenchmarkRunner(runtime).run_suite("CGR Local Benchmark", tasks)

    assert suite.total_tasks == 6
    assert suite.succeeded_tasks == 6
    assert suite.verified_tasks == 6
    assert suite.failed_tasks == 0
    assert suite.success_rate == 1.0
    assert suite.verification_rate == 1.0
