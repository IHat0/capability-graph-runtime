"""Model execution response contract."""

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ModelResponse(BaseModel):
    """Immutable response produced by a model plugin."""

    model_config = ConfigDict(frozen=True)

    text: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    usage: dict[str, int] = Field(default_factory=dict)
    metadata: dict[str, str] = Field(default_factory=dict)

    @field_validator("usage")
    @classmethod
    def validate_usage(cls, usage: dict[str, int]) -> dict[str, int]:
        """Reject negative usage counters."""
        if any(value < 0 for value in usage.values()):
            raise ValueError("Usage values must be non-negative.")
        return usage
