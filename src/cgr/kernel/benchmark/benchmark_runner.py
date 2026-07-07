"""Deterministic local benchmark execution harness."""

from time import perf_counter
from typing import Any

from cgr.kernel.contracts import (
    Capability,
    CapabilityVersion,
    ExecutionContext,
    ExecutionRequest,
    ExecutionStatus,
)
from cgr.kernel.runtime import KernelRuntime
from cgr.kernel.verification import SchemaVerifier

from .benchmark_case_result import BenchmarkCaseResult
from .benchmark_suite_result import BenchmarkSuiteResult
from .benchmark_task import BenchmarkTask


class BenchmarkRunner:
    """Run benchmark tasks through a CGR runtime."""

    def __init__(self, runtime: KernelRuntime) -> None:
        self._runtime = runtime

    def run_suite(
        self,
        suite_name: str,
        tasks: list[BenchmarkTask],
    ) -> BenchmarkSuiteResult:
        """Execute every task and return an aggregate suite result."""
        results = [self._run_task(task) for task in tasks]
        total_tasks = len(results)
        succeeded_tasks = sum(result.succeeded for result in results)
        verified_tasks = sum(result.verified for result in results)
        average_duration_ms = (
            sum(result.duration_ms for result in results) / total_tasks
            if total_tasks
            else 0.0
        )
        return BenchmarkSuiteResult(
            suite_name=suite_name,
            total_tasks=total_tasks,
            succeeded_tasks=succeeded_tasks,
            verified_tasks=verified_tasks,
            failed_tasks=total_tasks - succeeded_tasks,
            average_duration_ms=average_duration_ms,
            results=results,
        )

    def _run_task(self, task: BenchmarkTask) -> BenchmarkCaseResult:
        capability = Capability(
            id=task.capability_id,
            name=task.name,
            description=f"Benchmark capability: {task.name}",
            version=CapabilityVersion(major=1, minor=0, patch=0),
        )
        request = ExecutionRequest[dict[str, Any]](
            capability=capability,
            context=ExecutionContext(),
            payload=task.payload,
        )
        started = perf_counter()
        try:
            execution_result = self._runtime.execute_capability(request)
        except Exception as exc:
            duration_ms = (perf_counter() - started) * 1000
            return BenchmarkCaseResult(
                task_id=task.id,
                capability_id=task.capability_id,
                plugin_id=None,
                succeeded=False,
                verified=False,
                duration_ms=duration_ms,
                error_type=type(exc).__name__,
                error_message=str(exc),
            )

        duration_ms = (perf_counter() - started) * 1000
        succeeded = execution_result.status == ExecutionStatus.SUCCESS
        output = (
            dict(execution_result.output)
            if isinstance(execution_result.output, dict)
            else None
        )
        if task.expected_output is not None:
            verified = execution_result.output == task.expected_output
        elif task.required_output_keys:
            verified = SchemaVerifier(
                "benchmark-schema",
                task.required_output_keys,
            ).verify(execution_result.output).passed
        else:
            verified = succeeded
        return BenchmarkCaseResult(
            task_id=task.id,
            capability_id=task.capability_id,
            plugin_id=None,
            succeeded=succeeded,
            verified=verified,
            duration_ms=duration_ms,
            output=output,
        )
