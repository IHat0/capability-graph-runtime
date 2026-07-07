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
