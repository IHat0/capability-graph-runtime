"""
Semantic version implementation for Capability Graph Runtime.

This module defines the immutable CapabilityVersion value object used
throughout the runtime to version capabilities and contracts.
"""

from __future__ import annotations

from functools import total_ordering

from pydantic import BaseModel, ConfigDict, Field


@total_ordering
class CapabilityVersion(BaseModel):
    """
    Immutable semantic version.

    Examples:
        1.0.0
        2.3.5
        10.1.17
    """

    model_config = ConfigDict(frozen=True)

    major: int = Field(ge=0)
    minor: int = Field(ge=0)
    patch: int = Field(ge=0)

    def __str__(self) -> str:
        """Return semantic version string."""
        return f"{self.major}.{self.minor}.{self.patch}"

    def __repr__(self) -> str:
        """Return developer representation."""
        return (
            "CapabilityVersion("
            f"major={self.major}, "
            f"minor={self.minor}, "
            f"patch={self.patch})"
        )

    def __eq__(self, other: object) -> bool:
        """Compare equality."""
        if not isinstance(other, CapabilityVersion):
            return NotImplemented

        return (
            self.major,
            self.minor,
            self.patch,
        ) == (
            other.major,
            other.minor,
            other.patch,
        )

    def __lt__(self, other: object) -> bool:
        """Compare ordering."""
        if not isinstance(other, CapabilityVersion):
            return NotImplemented

        return (
            self.major,
            self.minor,
            self.patch,
        ) < (
            other.major,
            other.minor,
            other.patch,
        )

    @classmethod
    def parse(cls, value: str) -> "CapabilityVersion":
        """
        Parse a semantic version string.

        Args:
            value:
                Version string formatted as MAJOR.MINOR.PATCH.

        Returns:
            CapabilityVersion instance.

        Raises:
            ValueError:
                If the version string is invalid.
        """
        parts = value.strip().split(".")

        if len(parts) != 3:
            raise ValueError(
                "Version must follow semantic versioning: MAJOR.MINOR.PATCH"
            )

        try:
            major, minor, patch = map(int, parts)
        except ValueError as exc:
            raise ValueError(
                "Semantic version components must be integers."
            ) from exc

        return cls(
            major=major,
            minor=minor,
            patch=patch,
        )