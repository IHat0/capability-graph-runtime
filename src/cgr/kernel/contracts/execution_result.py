"""
Execution result contract for the Capability Graph Runtime.

An ExecutionResult represents the outcome of executing a capability.
Every plugin returns one of these to the runtime.
"""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from .execution_context import ExecutionContext
from .execution_status import ExecutionStatus

TOutput = TypeVar("TOutput")


class ExecutionResult(BaseModel, Generic[TOutput]):
    """
    Immutable execution result.

    Every plugin returns this object after execution.
    """

    model_config = ConfigDict(frozen=True)

    context: ExecutionContext = Field(
        description="Execution context associated with this result.",
    )

    status: ExecutionStatus = Field(
        description="Outcome of the execution.",
    )

    output: TOutput = Field(
        description="Typed output produced by the capability.",
    )

    error: str | None = Field(
        default=None,
        description="Error message if execution failed.",
    )

    duration_ms: float = Field(
        default=0.0,
        ge=0,
        description="Execution duration in milliseconds.",
    )

    @property
    def execution_id(self) -> str:
        """Convenience accessor for the execution identifier."""
        return self.context.execution_id

    @property
    def succeeded(self) -> bool:
        """Return True if execution completed successfully."""
        return self.status == ExecutionStatus.SUCCESS

    @property
    def failed(self) -> bool:
        """Return True if execution failed."""
        return self.status == ExecutionStatus.FAILED