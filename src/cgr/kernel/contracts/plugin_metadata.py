"""
Plugin metadata contract for the Capability Graph Runtime.

PluginMetadata describes a plugin without loading or executing it.
The registry uses this information during discovery, compatibility
checks, and routing decisions.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .capability import Capability


class PluginMetadata(BaseModel):
    """
    Immutable metadata describing a plugin.

    This class contains everything the runtime needs to know about a
    plugin before it is instantiated.
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(
        min_length=1,
        description="Globally unique plugin identifier.",
    )

    name: str = Field(
        min_length=1,
        description="Human-readable plugin name.",
    )

    version: str = Field(
        min_length=1,
        description="Plugin version.",
    )

    author: str = Field(
        default="Unknown",
        description="Plugin author.",
    )

    description: str = Field(
        default="",
        description="Plugin description.",
    )

    capabilities: list[Capability] = Field(
        default_factory=list,
        description="Capabilities implemented by this plugin.",
    )

    tags: list[str] = Field(
        default_factory=list,
        description="Searchable plugin tags.",
    )

    homepage: str | None = Field(
        default=None,
        description="Optional project homepage.",
    )

    license: str | None = Field(
        default=None,
        description="Plugin license.",
    )

    minimum_runtime_version: str = Field(
        default="0.1.0",
        description="Minimum compatible CGR runtime version.",
    )

    @property
    def qualified_name(self) -> str:
        """
        Return a unique human-readable plugin identifier.

        Example:
            glm47@1.2.0
        """
        return f"{self.id}@{self.version}"

    def supports(self, capability_id: str) -> bool:
        """
        Return True if this plugin implements the given capability.
        """
        return any(
            capability.id == capability_id
            for capability in self.capabilities
        )