"""Aggregate SWE A/B evaluation result."""

from pydantic import BaseModel, ConfigDict, Field

from .swe_case_result import SWECaseResult


class SWEEvalResult(BaseModel):
    """Immutable baseline versus CGR coding-agent evaluation summary."""

    model_config = ConfigDict(frozen=True)

    suite_name: str = Field(min_length=1)
    total_tasks: int = Field(ge=0)
    pass_rates: dict[str, float]
    deltas: dict[str, float]
    results: list[SWECaseResult]
