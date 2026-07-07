"""Primary Booster Engine result contract."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .booster_candidate import BoosterCandidate
from .booster_domain import BoosterDomain
from .booster_mode import BoosterMode
from .booster_trace import BoosterTrace


class BoosterResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_id: str = Field(min_length=1)
    domain: BoosterDomain
    mode: BoosterMode
    output_text: str
    structured_output: dict[str, Any] | None = None
    passed: bool
    score: float = Field(ge=0, le=1)
    candidates: list[BoosterCandidate]
    trace: BoosterTrace
    error_type: str | None = None
    error_message: str | None = None
    duration_ms: float = Field(default=0.0, ge=0)

    @model_validator(mode="after")
    def validate_output_or_error(self) -> "BoosterResult":
        if not self.output_text and self.error_type is None:
            raise ValueError("output_text may be empty only when error_type is set.")
        return self
