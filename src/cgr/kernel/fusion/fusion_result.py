"""Structured results from plugin output fusion."""

from typing import Any

from pydantic import BaseModel, ConfigDict

from .fusion_strategy import FusionStrategy


class FusionResult(BaseModel):
    """Immutable aggregate result from fusing compatible plugin outputs."""

    model_config = ConfigDict(frozen=True)

    capability_id: str
    strategy: FusionStrategy
    attempted_plugin_ids: list[str]
    successful_plugin_ids: list[str]
    failed_plugin_ids: list[str]
    fused_output: Any | None
