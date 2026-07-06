"""Execution observations stored by learning memory."""

from pydantic import BaseModel, ConfigDict, Field

from cgr.kernel.contracts import ExecutionStatus


class ExecutionObservation(BaseModel):
    """Immutable observed outcome of one plugin execution."""

    model_config = ConfigDict(frozen=True)

    execution_id: str
    capability_id: str
    plugin_id: str
    status: ExecutionStatus
    duration_ms: float = Field(default=0.0, ge=0)
    error_type: str | None = None
    error_message: str | None = None
