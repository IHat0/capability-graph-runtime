"""Executable test command contract for generated code."""

from pydantic import BaseModel, ConfigDict, Field


class CodeTestCase(BaseModel):
    """Immutable command and expected exit status for one code check."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    command: list[str] = Field(min_length=1)
    expected_exit_code: int = 0
