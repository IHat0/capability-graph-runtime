"""Plugin performance statistics computed by learning memory."""

from pydantic import BaseModel, ConfigDict, Field


class PluginPerformance(BaseModel):
    """Immutable aggregate performance for a plugin and capability."""

    model_config = ConfigDict(frozen=True)

    capability_id: str
    plugin_id: str
    total_executions: int = Field(ge=0)
    successful_executions: int = Field(ge=0)
    failed_executions: int = Field(ge=0)
    average_duration_ms: float = Field(ge=0)
    success_rate: float = Field(ge=0, le=1)
