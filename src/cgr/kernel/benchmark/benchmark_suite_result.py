"""Aggregate benchmark suite result."""

from pydantic import BaseModel, ConfigDict, Field

from .benchmark_case_result import BenchmarkCaseResult


class BenchmarkSuiteResult(BaseModel):
    """Immutable aggregate result for a benchmark suite."""

    model_config = ConfigDict(frozen=True)

    suite_name: str = Field(min_length=1)
    total_tasks: int = Field(ge=0)
    succeeded_tasks: int = Field(ge=0)
    verified_tasks: int = Field(ge=0)
    failed_tasks: int = Field(ge=0)
    average_duration_ms: float = Field(ge=0)
    results: list[BenchmarkCaseResult]

    @property
    def success_rate(self) -> float:
        """Return the fraction of tasks that succeeded."""
        if self.total_tasks == 0:
            return 0.0
        return self.succeeded_tasks / self.total_tasks

    @property
    def verification_rate(self) -> float:
        """Return the fraction of tasks that verified."""
        if self.total_tasks == 0:
            return 0.0
        return self.verified_tasks / self.total_tasks
