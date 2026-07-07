"""SWE-style task definition."""

from pydantic import BaseModel, ConfigDict, Field


class SWETask(BaseModel):
    """Immutable local coding task with exact expected file contents."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    issue: str = Field(min_length=1)
    files: dict[str, str] = Field(min_length=1)
    expected_files: dict[str, str] = Field(min_length=1)
