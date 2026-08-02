"""Pure scientific objective and planning-policy contracts."""

from __future__ import annotations

import math
from typing import Self

from pydantic import Field, field_validator, model_validator

from cgr.kernel.contracts import CapabilityVersion

from .artifacts import CreationProvenance
from .canonical import CanonicalModel, validate_identifier
from .capabilities import CapabilityFidelityLevel, CapabilityVerificationRequirement
from .contracts import ScientificAssumption
from .resources import ResourceQuantity

_DESCRIPTION_MAXIMUM_LENGTH = 16_384
_DECLARATION_MAXIMUM_LENGTH = 4096


def _ordered_identifiers(values: tuple[str, ...], *, label: str) -> tuple[str, ...]:
    validated = tuple(validate_identifier(item, label=label) for item in values)
    if len(validated) != len(set(validated)):
        raise ValueError(f"{label.capitalize()} values must be unique.")
    return tuple(sorted(validated))


def _ordered_text(values: tuple[str, ...], *, label: str) -> tuple[str, ...]:
    normalized: list[str] = []
    for item in values:
        item = item.strip()
        if not item:
            raise ValueError(f"{label.capitalize()} values cannot be blank.")
        if len(item) > _DECLARATION_MAXIMUM_LENGTH:
            raise ValueError(f"{label.capitalize()} values are too long.")
        normalized.append(item)
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{label.capitalize()} values must be unique.")
    return tuple(sorted(normalized))


class ScientificObjective(CanonicalModel):
    """A normalized, attributable scientific objective without execution authority."""

    objective_identifier: str
    schema_version: CapabilityVersion
    objective_type: str
    original_description: str = Field(
        min_length=1, max_length=_DESCRIPTION_MAXIMUM_LENGTH
    )
    normalized_description: str = Field(
        min_length=1, max_length=_DESCRIPTION_MAXIMUM_LENGTH
    )
    project_identifier: str
    molecular_system_identifiers: tuple[str, ...] = ()
    structure_identifiers: tuple[str, ...] = ()
    structural_region_identifiers: tuple[str, ...] = ()
    comparison_state_identifiers: tuple[str, ...] = ()
    requested_property_identifiers: tuple[str, ...] = ()
    scientific_constraints: tuple[str, ...] = ()
    required_confidence: float | None = Field(default=None, ge=0, le=1)
    stopping_criteria: tuple[str, ...] = ()
    assumptions: tuple[ScientificAssumption, ...] = ()
    provenance: CreationProvenance

    @field_validator("objective_identifier", "objective_type", "project_identifier")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("original_description", "normalized_description")
    @classmethod
    def normalize_descriptions(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Scientific objective descriptions cannot be blank.")
        return value

    @field_validator(
        "molecular_system_identifiers",
        "structure_identifiers",
        "structural_region_identifiers",
        "comparison_state_identifiers",
        "requested_property_identifiers",
    )
    @classmethod
    def order_identifier_collections(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _ordered_identifiers(value, label="objective identifier")

    @field_validator("scientific_constraints", "stopping_criteria")
    @classmethod
    def order_text_collections(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _ordered_text(value, label="objective declaration")

    @field_validator("required_confidence", mode="before")
    @classmethod
    def validate_confidence(cls, value: object) -> object:
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, (int, float))
        ):
            raise ValueError("Required confidence must be numeric.")
        if value is not None and not math.isfinite(value):
            raise ValueError("Required confidence must be finite.")
        return value

    @field_validator("assumptions")
    @classmethod
    def order_assumptions(
        cls, value: tuple[ScientificAssumption, ...]
    ) -> tuple[ScientificAssumption, ...]:
        identifiers = [item.assumption_identifier for item in value]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError(
                "Scientific objective assumption identifiers must be unique."
            )
        return tuple(sorted(value, key=lambda item: item.assumption_identifier))


class PlanningConstraints(CanonicalModel):
    """Non-authorizing policy constraints for a future scientific planner."""

    permitted_execution_targets: tuple[str, ...] = ()
    forbidden_execution_targets: tuple[str, ...] = ()
    resource_ceilings: tuple[ResourceQuantity, ...] = ()
    cost_ceilings: tuple[ResourceQuantity, ...] = ()
    maximum_wall_time_seconds: float | None = Field(default=None, gt=0)
    minimum_fidelity: CapabilityFidelityLevel | None = None
    required_confidence: float | None = Field(default=None, ge=0, le=1)
    required_verifications: tuple[CapabilityVerificationRequirement, ...] = ()
    approval_requirements: tuple[str, ...] = ()
    policy_notes: tuple[str, ...] = ()

    @field_validator(
        "permitted_execution_targets",
        "forbidden_execution_targets",
        "approval_requirements",
    )
    @classmethod
    def order_identifiers(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _ordered_identifiers(value, label="planning identifier")

    @field_validator("resource_ceilings", "cost_ceilings")
    @classmethod
    def order_ceilings(
        cls, value: tuple[ResourceQuantity, ...]
    ) -> tuple[ResourceQuantity, ...]:
        identities = [(item.dimension, item.unit) for item in value]
        if len(identities) != len(set(identities)):
            raise ValueError(
                "Planning ceiling dimension and unit pairs must be unique."
            )
        return tuple(sorted(value, key=lambda item: (item.dimension, item.unit)))

    @field_validator(
        "maximum_wall_time_seconds", "required_confidence", mode="before"
    )
    @classmethod
    def validate_finite_numbers(cls, value: object) -> object:
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, (int, float))
        ):
            raise ValueError("Planning numeric constraints must be numeric.")
        if value is not None and not math.isfinite(value):
            raise ValueError("Planning numeric constraints must be finite.")
        return value

    @field_validator("required_verifications")
    @classmethod
    def order_verifications(
        cls, value: tuple[CapabilityVerificationRequirement, ...]
    ) -> tuple[CapabilityVerificationRequirement, ...]:
        identities = [
            (item.verifier_identifier, item.subject_artifact_type) for item in value
        ]
        if len(identities) != len(set(identities)):
            raise ValueError("Planning verification requirements must be unique.")
        return tuple(
            sorted(
                value,
                key=lambda item: (item.verifier_identifier, item.subject_artifact_type),
            )
        )

    @field_validator("policy_notes")
    @classmethod
    def order_policy_notes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _ordered_text(value, label="policy note")

    @model_validator(mode="after")
    def reject_target_conflicts(self) -> Self:
        if set(self.permitted_execution_targets).intersection(
            self.forbidden_execution_targets
        ):
            raise ValueError(
                "An execution target cannot be both permitted and forbidden."
            )
        return self
