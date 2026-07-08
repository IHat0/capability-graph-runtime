"""Coding task contract."""

from pydantic import BaseModel, ConfigDict, Field

from .code_test_case import CodeTestCase


class CodingTask(BaseModel):
    """Immutable coding issue plus the files available to an agent."""

    model_config = ConfigDict(frozen=True)

    issue: str = Field(min_length=1)
    files: dict[str, str] = Field(min_length=1)
    test_files: dict[str, str] = Field(default_factory=dict)
    test_commands: list[CodeTestCase] = Field(default_factory=list)
    hidden_test_files: dict[str, str] = Field(default_factory=dict)
    hidden_test_commands: list[CodeTestCase] = Field(default_factory=list)
    metadata: dict[str, str] = Field(default_factory=dict)
