"""Structured result from the deterministic model pipeline."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ModelPipelineResult(BaseModel):
    """Immutable aggregate result from model pipeline execution."""

    model_config = ConfigDict(frozen=True)

    prompt: str = Field(min_length=1)
    reasoning_output: dict[str, Any] | None = None
    coding_output: dict[str, Any] | None = None
    fused_output: Any | None = None
    verified: bool = False
