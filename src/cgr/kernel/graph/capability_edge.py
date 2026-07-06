"""Capability graph edge model."""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CapabilityEdge(BaseModel):
    """Immutable directed edge between capability nodes."""

    model_config = ConfigDict(frozen=True)

    source_id: str = Field(min_length=1)
    target_id: str = Field(min_length=1)
    label: str = ""
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_distinct_nodes(self) -> Self:
        """Reject self-referencing edges."""
        if self.source_id == self.target_id:
            raise ValueError("Edge source and target must be different.")
        return self
