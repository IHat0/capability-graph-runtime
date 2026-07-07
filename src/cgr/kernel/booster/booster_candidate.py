"""Candidate generated or repaired by the Booster Engine."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .booster_mode import BoosterMode


class BoosterCandidate(BaseModel):
    model_config = ConfigDict(frozen=True)

    candidate_id: str = Field(min_length=1)
    mode: BoosterMode
    text: str = Field(min_length=1)
    structured_output: dict[str, Any] | None = None
    score: float = Field(default=0.0, ge=0, le=1)
    verified: bool = False
    critique: str | None = None
    repair_of: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
