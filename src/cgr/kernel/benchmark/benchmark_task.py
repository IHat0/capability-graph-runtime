"""Benchmark task definition."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class BenchmarkTask(BaseModel):
    """Immutable task executed by the benchmark runner."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    capability_id: str = Field(min_length=1)
    payload: dict[str, Any]
    expected_output: dict[str, Any] | None = None
    required_output_keys: set[str] = Field(default_factory=set)
    metadata: dict[str, str] = Field(default_factory=dict)
