"""Model conversation message contract."""

from pydantic import BaseModel, ConfigDict, Field

from .model_role import ModelRole


class ModelMessage(BaseModel):
    """Immutable message exchanged with a model plugin."""

    model_config = ConfigDict(frozen=True)

    role: ModelRole
    content: str = Field(min_length=1)
