"""Coding task contract."""

from pydantic import BaseModel, ConfigDict, Field


class CodingTask(BaseModel):
    """Immutable coding issue plus the files available to an agent."""

    model_config = ConfigDict(frozen=True)

    issue: str = Field(min_length=1)
    files: dict[str, str] = Field(min_length=1)
    metadata: dict[str, str] = Field(default_factory=dict)
