"""SWE-style task definition."""

from pydantic import BaseModel, ConfigDict, Field

from cgr.kernel.coding import CodeTestCase


class SWETask(BaseModel):
    """Immutable local coding task with exact expected file contents."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    issue: str = Field(min_length=1)
    files: dict[str, str] = Field(min_length=1)
    expected_files: dict[str, str] = Field(min_length=1)
    test_files: dict[str, str] = Field(default_factory=dict)
    test_commands: list[CodeTestCase] = Field(default_factory=list)
    visible_test_files: dict[str, str] = Field(default_factory=dict)
    hidden_test_files: dict[str, str] = Field(default_factory=dict)
    visible_test_commands: list[CodeTestCase] = Field(default_factory=list)
    hidden_test_commands: list[CodeTestCase] = Field(default_factory=list)

    @property
    def prompt_test_files(self) -> dict[str, str]:
        """Tests an agent may inspect; hidden sources are deliberately excluded."""
        return self.visible_test_files or self.test_files

    @property
    def prompt_test_commands(self) -> list[CodeTestCase]:
        """Commands safe to run during agent repair."""
        return self.visible_test_commands or self.test_commands

    @property
    def scoring_test_files(self) -> dict[str, str]:
        """All test sources used only by the final benchmark scorer."""
        return {
            **self.test_files,
            **self.visible_test_files,
            **self.hidden_test_files,
        }

    @property
    def scoring_test_commands(self) -> list[CodeTestCase]:
        """All commands used for final scoring."""
        if not self.visible_test_commands and not self.hidden_test_commands:
            return self.test_commands
        return [*self.visible_test_commands, *self.hidden_test_commands]
