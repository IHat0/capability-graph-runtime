"""One mode/task result from an SWE A/B evaluation."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


SWEMode = Literal["baseline", "cgr_single", "cgr_multi"]


class SWECaseResult(BaseModel):
    """Immutable normalized result for one evaluated mode and task."""

    model_config = ConfigDict(frozen=True)

    task_id: str
    mode: SWEMode
    plugin_id: str
    passed: bool
    files: dict[str, str] | None = None
    error_type: str | None = None
    error_message: str | None = None
    raw_output_preview: str | None = None
    attempts_count: int | None = None
    candidates_count: int | None = None
    repair_attempts_count: int | None = None
    selected_candidate_id: str | None = None
    verifier_messages_preview: str | None = None
    repair_prompt_preview: str | None = None
    candidate_scores: dict[str, float] | None = None
    candidate_file_previews: dict[str, dict[str, str]] | None = None
