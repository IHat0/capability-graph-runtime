"""Aggregate benchmark comparison for the Booster Engine."""

from typing import Any

from .booster_engine import BoosterEngine
from .booster_task import BoosterTask


class BoosterBenchmarkRunner:
    def __init__(self, engine: BoosterEngine) -> None:
        self._engine = engine

    def run(
        self,
        suite_name: str,
        tasks: list[BoosterTask],
        include_multi: bool = True,
    ) -> dict[str, Any]:
        comparisons = [
            self._engine.compare(task, include_multi=include_multi) for task in tasks
        ]
        total = len(comparisons)
        baseline_average = self._average(
            [comparison.baseline.score for comparison in comparisons]
        )
        single_average = self._average(
            [comparison.boosted_single.score for comparison in comparisons]
        )
        multi_results = [
            comparison.boosted_multi
            for comparison in comparisons
            if comparison.boosted_multi is not None
        ]
        multi_average = (
            self._average([result.score for result in multi_results])
            if include_multi
            else None
        )
        single_rate = (
            sum(comparison.single_improved for comparison in comparisons) / total
            if total
            else 0.0
        )
        multi_rate = (
            sum(comparison.multi_improved for comparison in comparisons) / total
            if total and include_multi
            else (0.0 if include_multi else None)
        )
        return {
            "suite_name": suite_name,
            "total_tasks": total,
            "baseline_average_score": baseline_average,
            "boosted_single_average_score": single_average,
            "boosted_multi_average_score": multi_average,
            "single_improvement_rate": single_rate,
            "multi_improvement_rate": multi_rate,
            "comparisons": [
                comparison.model_dump(mode="json") for comparison in comparisons
            ],
        }

    @staticmethod
    def _average(values: list[float]) -> float:
        return sum(values) / len(values) if values else 0.0
