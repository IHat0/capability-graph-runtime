"""Repeated-run aggregation for the Coding v1 A/B benchmark."""

import time
from collections.abc import Callable
from typing import Any

from cgr.kernel.swe import SWECaseResult, SWEEvalResult, SWETask
from cgr.kernel.swe.swe_case_result import SWEMode


V1Evaluator = Callable[[list[SWETask], bool], SWEEvalResult]
_MODES: tuple[SWEMode, ...] = ("baseline", "cgr_single", "cgr_multi")
_PREFERRED_MODES: tuple[SWEMode, ...] = (
    "cgr_multi",
    "cgr_single",
    "baseline",
)


class CodingV1Runner:
    """Aggregate existing SWE A/B evaluations into a stable v1 report."""

    def __init__(self, evaluator: V1Evaluator) -> None:
        self._evaluator = evaluator

    def run(
        self,
        tasks: list[SWETask],
        runs: int = 1,
        debug_trace: bool = False,
    ) -> dict[str, Any]:
        if runs <= 0:
            raise ValueError("--runs must be positive.")
        started = time.perf_counter()
        evaluations = [self._evaluator(tasks, debug_trace) for _ in range(runs)]
        rates = {
            mode: [evaluation.pass_rates[mode] for evaluation in evaluations]
            for mode in _MODES
        }
        means = {
            mode: sum(values) / len(values) if values else 0.0
            for mode, values in rates.items()
        }
        elapsed_by_mode = {
            mode: sum(
                result.elapsed_seconds or 0.0
                for evaluation in evaluations
                for result in evaluation.results
                if result.mode == mode
            )
            for mode in _MODES
        }
        per_task = self._per_task(evaluations, tasks)
        first = evaluations[0] if evaluations else self._empty_result()
        return {
            "suite_name": "coding_v1",
            "total_tasks": len(tasks),
            "pass_rates": means,
            "deltas": {
                "cgr_single_minus_baseline": (
                    means["cgr_single"] - means["baseline"]
                ),
                "cgr_multi_minus_baseline": (
                    means["cgr_multi"] - means["baseline"]
                ),
            },
            "results": [
                result.model_dump(mode="json", exclude_none=not debug_trace)
                for result in first.results
            ],
            "summary": self._summary(first.results, tasks),
            "stability": {
                "runs": runs,
                "mode_pass_rate_mean": means,
                "mode_pass_rate_min": {
                    mode: min(values) if values else 0.0
                    for mode, values in rates.items()
                },
                "mode_pass_rate_max": {
                    mode: max(values) if values else 0.0
                    for mode, values in rates.items()
                },
                "per_task": per_task,
                "regressions": self._pair_changes(
                    evaluations, improved=False
                ),
                "improvements": self._pair_changes(
                    evaluations, improved=True
                ),
            },
            "efficiency": {
                "suite_elapsed_seconds": time.perf_counter() - started,
                "mode_elapsed_seconds": elapsed_by_mode,
                "usage": {
                    mode: {
                        "prompt_tokens": None,
                        "completion_tokens": None,
                        "total_tokens": None,
                        "estimated_cost": None,
                    }
                    for mode in _MODES
                },
            },
        }

    @staticmethod
    def _per_task(
        evaluations: list[SWEEvalResult], tasks: list[SWETask]
    ) -> dict[str, dict[str, float]]:
        runs = len(evaluations)
        return {
            task.id: {
                mode: (
                    sum(
                        result.passed
                        for evaluation in evaluations
                        for result in evaluation.results
                        if result.task_id == task.id and result.mode == mode
                    )
                    / runs
                    if runs
                    else 0.0
                )
                for mode in _MODES
            }
            for task in tasks
        }

    @staticmethod
    def _summary(
        results: list[SWECaseResult], tasks: list[SWETask]
    ) -> dict[str, Any]:
        passed = {
            (result.task_id, result.mode): result.passed for result in results
        }

        def mode_passes(mode: SWEMode) -> list[str]:
            return [task.id for task in tasks if passed.get((task.id, mode), False)]

        baseline = set(mode_passes("baseline"))
        single = set(mode_passes("cgr_single"))
        multi = set(mode_passes("cgr_multi"))
        best_mode: dict[str, str | None] = {}
        for task in tasks:
            best_mode[task.id] = next(
                (
                    mode
                    for mode in _PREFERRED_MODES
                    if passed.get((task.id, mode), False)
                ),
                None,
            )
        return {
            "baseline_passed": len(baseline),
            "cgr_single_passed": len(single),
            "cgr_multi_passed": len(multi),
            "baseline_failed_tasks": [
                task.id for task in tasks if task.id not in baseline
            ],
            "single_improved_tasks": sorted(single - baseline),
            "multi_improved_tasks": sorted(multi - baseline),
            "single_regressed_tasks": sorted(baseline - single),
            "multi_regressed_tasks": sorted(baseline - multi),
            "multi_not_monotonic_tasks": sorted(single - multi),
            "best_mode_by_task": best_mode,
        }

    @staticmethod
    def _pair_changes(
        evaluations: list[SWEEvalResult], improved: bool
    ) -> list[str]:
        changes: set[str] = set()
        for evaluation in evaluations:
            passed = {
                (result.task_id, result.mode): result.passed
                for result in evaluation.results
            }
            for task_id, mode in {
                (result.task_id, result.mode) for result in evaluation.results
            }:
                if mode == "baseline":
                    continue
                baseline = passed.get((task_id, "baseline"), False)
                boosted = passed.get((task_id, mode), False)
                if (boosted and not baseline) == improved and baseline != boosted:
                    changes.add(f"{task_id}:{mode}")
        return sorted(changes)

    @staticmethod
    def _empty_result() -> SWEEvalResult:
        return SWEEvalResult(
            suite_name="coding_v1",
            total_tasks=0,
            pass_rates={mode: 0.0 for mode in _MODES},
            deltas={},
            results=[],
        )
