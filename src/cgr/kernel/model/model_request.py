"""Model execution request payload contract."""

from pydantic import BaseModel, ConfigDict, Field

from .model_message import ModelMessage
from .model_role import ModelRole


class ModelRequest(BaseModel):
    """Immutable request payload accepted by model plugins."""

    model_config = ConfigDict(frozen=True)

    messages: list[ModelMessage] = Field(min_length=1)
    temperature: float = Field(default=0.0, ge=0, le=2)
    max_tokens: int | None = Field(default=None, gt=0)
    metadata: dict[str, str] = Field(default_factory=dict)

    @property
    def latest_user_message(self) -> str:
        """Return the content of the most recent user message."""
        for message in reversed(self.messages):
            if message.role == ModelRole.USER:
                return message.content
        return ""
