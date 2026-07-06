"""
Execution context for the Capability Graph Runtime.

The ExecutionContext carries metadata that accompanies every execution
through the runtime. It provides traceability, correlation, timing,
and arbitrary metadata without coupling components together.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class ExecutionContext(BaseModel):
    """
    Context information shared across a single execution.

    Every request entering the runtime receives exactly one
    ExecutionContext which is propagated unchanged unless
    explicitly enriched by the runtime.
    """

    model_config = ConfigDict(frozen=True)

    execution_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="Globally unique execution identifier.",
    )

    correlation_id: str | None = Field(
        default=None,
        description="Identifier used to correlate related executions.",
    )

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="UTC timestamp when the execution context was created.",
    )

    metadata: dict[str, str] = Field(
        default_factory=dict,
        description="Arbitrary execution metadata.",
    )

    def with_metadata(self, key: str, value: str) -> "ExecutionContext":
        """
        Return a new ExecutionContext with additional metadata.

        Because ExecutionContext is immutable, this method returns
        a copy rather than modifying the existing instance.
        """
        new_metadata = dict(self.metadata)
        new_metadata[key] = value

        return self.model_copy(
            update={
                "metadata": new_metadata,
            }
        )