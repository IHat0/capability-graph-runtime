"""Candidate model used during capability routing."""

from pydantic import BaseModel, ConfigDict, Field


class RouteCandidate(BaseModel):
    """Immutable candidate plugin for a requested capability."""

    model_config = ConfigDict(frozen=True)

    plugin_id: str
    plugin_name: str
    plugin_version: str
    capability_id: str
    priority: int = 0
    healthy: bool = True
    success_rate: float | None = Field(default=None, ge=0, le=1)
    average_duration_ms: float | None = Field(default=None, ge=0)
    total_executions: int | None = Field(default=None, ge=0)
