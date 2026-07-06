"""Capability graph node model."""

from pydantic import BaseModel, ConfigDict, Field


class CapabilityNode(BaseModel):
    """Immutable capability node in a workflow graph."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    capability_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    metadata: dict[str, str] = Field(default_factory=dict)
