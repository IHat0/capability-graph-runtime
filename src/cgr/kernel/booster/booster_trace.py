"""Explainable Booster Engine execution trace."""

from pydantic import BaseModel, ConfigDict, Field

from .booster_mode import BoosterMode


class BoosterTrace(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_id: str = Field(min_length=1)
    mode: BoosterMode
    steps: list[str]
    candidate_ids: list[str]
    selected_candidate_id: str | None = None
    verifier_messages: list[str] = Field(default_factory=list)
    model_calls: int = Field(default=0, ge=0)
