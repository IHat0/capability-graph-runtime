"""Structured full-file coding patch contract."""

from pydantic import BaseModel, ConfigDict, Field


class CodingPatch(BaseModel):
    """Immutable mapping of file names to their complete patched contents."""

    model_config = ConfigDict(frozen=True)

    files: dict[str, str] = Field(min_length=1)
    explanation: str = ""
    placeholder_filename_remapped: bool = False
    placeholder_filename_original: str | None = None
    placeholder_filename_target: str | None = None
