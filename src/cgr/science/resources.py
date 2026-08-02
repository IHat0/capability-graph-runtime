"""Immutable declarations of scientific resource availability and quantities."""

from __future__ import annotations

import math
from datetime import datetime
from typing import TypeAlias

from pydantic import (
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    field_validator,
    model_validator,
)

from cgr.kernel.contracts import CapabilityVersion

from .artifacts import CreationProvenance
from .canonical import CanonicalModel, validate_bounded_metadata, validate_identifier

ResourceValue: TypeAlias = StrictBool | StrictInt | StrictFloat


class ResourceQuantity(CanonicalModel):
    """One typed resource value without domain-specific interpretation."""

    dimension: str
    unit: str
    value: ResourceValue

    @field_validator("dimension", "unit")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("value")
    @classmethod
    def validate_value(cls, value: ResourceValue) -> ResourceValue:
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("Resource values must be finite.")
        return value


class ResourceAvailabilitySnapshot(CanonicalModel):
    """A declared, non-probing snapshot of resources and execution targets."""

    snapshot_identifier: str
    schema_version: CapabilityVersion
    observed_at: datetime
    execution_environment: str
    available_resources: tuple[ResourceQuantity, ...] = ()
    available_execution_targets: tuple[str, ...] = ()
    unavailable_reasons: dict[str, str] = Field(default_factory=dict)
    provenance: CreationProvenance

    @field_validator("snapshot_identifier", "execution_environment")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("observed_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Resource observation timestamps must be timezone-aware.")
        return value

    @field_validator("available_resources")
    @classmethod
    def order_resources(
        cls, value: tuple[ResourceQuantity, ...]
    ) -> tuple[ResourceQuantity, ...]:
        identities = [(item.dimension, item.unit) for item in value]
        if len(identities) != len(set(identities)):
            raise ValueError(
                "Available resource dimension and unit pairs must be unique."
            )
        return tuple(sorted(value, key=lambda item: (item.dimension, item.unit)))

    @field_validator("available_execution_targets")
    @classmethod
    def order_targets(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        validated = tuple(
            validate_identifier(item, label="execution target") for item in value
        )
        if len(validated) != len(set(validated)):
            raise ValueError("Available execution targets must be unique.")
        return tuple(sorted(validated))

    @field_validator("unavailable_reasons")
    @classmethod
    def validate_unavailable_reasons(cls, value: dict[str, str]) -> dict[str, str]:
        validated: dict[str, str] = {}
        for target, reason in value.items():
            target = validate_identifier(target.strip(), label="execution target")
            if target in validated:
                raise ValueError(
                    "Unavailable execution targets must be unique after normalization."
                )
            reason = reason.strip()
            if not reason:
                raise ValueError(
                    "Unavailable execution-target reasons cannot be blank."
                )
            validated[target] = reason
        bounded = validate_bounded_metadata(validated)
        return {key: str(bounded[key]) for key in sorted(bounded)}

    @model_validator(mode="after")
    def reject_target_conflicts(self) -> ResourceAvailabilitySnapshot:
        conflict = set(self.available_execution_targets).intersection(
            self.unavailable_reasons
        )
        if conflict:
            raise ValueError(
                "An execution target cannot be both available and unavailable."
            )
        return self

    def lookup(self, dimension: str, unit: str) -> ResourceQuantity | None:
        """Return a matching immutable quantity without changing the snapshot."""
        dimension = validate_identifier(dimension, label="resource dimension")
        unit = validate_identifier(unit, label="resource unit")
        return next(
            (
                resource
                for resource in self.available_resources
                if resource.dimension == dimension and resource.unit == unit
            ),
            None,
        )
