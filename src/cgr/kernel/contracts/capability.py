"""
Capability definition for the Capability Graph Runtime.

A Capability represents an abstract ability that the runtime can execute.
The runtime routes requests to capabilities, never directly to
implementations.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .capability_version import CapabilityVersion


class Capability(BaseModel):
    """
    Immutable description of a runtime capability.

    A capability describes *what* can be done, not *how* it is
    implemented.

    Examples:
        - reasoning
        - coding
        - planning
        - search
        - formal_verification
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(
        min_length=1,
        description="Unique capability identifier.",
    )

    name: str = Field(
        min_length=1,
        description="Human-readable capability name.",
    )

    description: str = Field(
        default="",
        description="Detailed description of the capability.",
    )

    version: CapabilityVersion

    tags: list[str] = Field(
        default_factory=list,
        description="Searchable capability tags.",
    )

    def __str__(self) -> str:
        """Return a readable capability representation."""
        return f"{self.name} ({self.version})"

    @property
    def qualified_name(self) -> str:
        """
        Return the capability identifier including its version.

        Example:
            reasoning@1.0.0
        """
        return f"{self.id}@{self.version}"