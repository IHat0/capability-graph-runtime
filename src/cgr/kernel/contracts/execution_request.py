"""
Execution request contract for the Capability Graph Runtime.

An ExecutionRequest represents a request made to the runtime to execute
a specific capability.
"""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from .capability import Capability
from .execution_context import ExecutionContext

TInput = TypeVar("TInput")


class ExecutionRequest(BaseModel, Generic[TInput]):
    """
    Immutable execution request.

    Every request entering the runtime is wrapped in this object.
    It combines the capability being requested, the execution
    context, and a strongly typed input payload.
    """

    model_config = ConfigDict(frozen=True)

    capability: Capability = Field(
        description="Capability requested by the caller.",
    )

    context: ExecutionContext = Field(
        description="Execution context associated with this request.",
    )

    payload: TInput = Field(
        description="Typed input payload for the capability.",
    )

    priority: int = Field(
        default=0,
        ge=0,
        description="Execution priority.",
    )

    timeout_seconds: float | None = Field(
        default=None,
        gt=0,
        description="Optional execution timeout.",
    )

    @property
    def execution_id(self) -> str:
        """Convenience accessor for the execution identifier."""
        return self.context.execution_id