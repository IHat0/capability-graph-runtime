"""Result from one benchmark task."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class BenchmarkCaseResult(BaseModel):
    """Immutable outcome of one benchmark task."""

    model_config = ConfigDict(frozen=True)

    task_id: str = Field(min_length=1)
    capability_id: str = Field(min_length=1)
    plugin_id: str | None
    succeeded: bool
    verified: bool
    duration_ms: float = Field(ge=0)
    output: dict[str, Any] | None = None
    error_type: str | None = None
    error_message: str | None = None
