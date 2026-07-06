"""Structured results from plugin competition."""

from typing import Any

from pydantic import BaseModel, ConfigDict

from cgr.kernel.contracts import ExecutionResult


class CompetitionResult(BaseModel):
    """Immutable aggregate result from competing compatible plugins."""

    model_config = ConfigDict(frozen=True)

    capability_id: str
    winner_plugin_id: str | None
    attempted_plugin_ids: list[str]
    successful_plugin_ids: list[str]
    failed_plugin_ids: list[str]
    result: ExecutionResult[Any] | None
