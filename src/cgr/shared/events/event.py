"""Structured event model for the Capability Graph Runtime."""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .event_type import EventType


class Event(BaseModel):
    """Immutable event published by a runtime component."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=lambda: str(uuid4()))
    type: EventType
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    correlation_id: str | None = None
    execution_id: str | None = None
    source: str
    payload: dict[str, Any] = Field(default_factory=dict)
