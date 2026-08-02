"""Scientific capability declarations and a deterministic in-memory catalogue."""

from __future__ import annotations

import math
from collections.abc import Iterable
from enum import Enum
from typing import Self

from pydantic import Field, field_validator, model_validator

from cgr.kernel.contracts import CapabilityVersion

from .artifacts import ArtifactPointer
from .canonical import (
    BoundedMetadata,
    CanonicalModel,
    validate_bounded_metadata,
    validate_identifier,
)
from .contracts import CapabilityDescriptor
from .resources import ResourceQuantity, ResourceValue
from .verification import ScientificVerificationOutcome

_TEXT_MAXIMUM_LENGTH = 4096


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
        if len(item) > _TEXT_MAXIMUM_LENGTH:
            raise ValueError(f"{label.capitalize()} values are too long.")
        normalized.append(item)
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{label.capitalize()} values must be unique.")
    return tuple(sorted(normalized))


def _ordered_artifacts(
    values: tuple[ArtifactPointer, ...], *, label: str
) -> tuple[ArtifactPointer, ...]:
    identities = [(item.artifact_identifier, item.content_sha256) for item in values]
    if len(identities) != len(set(identities)):
        raise ValueError(f"{label.capitalize()} artifact pointers must be unique.")
    return tuple(
        sorted(values, key=lambda item: (item.artifact_identifier, item.content_sha256))
    )


class CapabilityPrerequisiteType(str, Enum):
    """Kinds of declared scientific capability prerequisites."""

    ARTIFACT_TYPE = "artifact_type"
    CAPABILITY = "capability"
    RUNTIME = "runtime"
    TOOL = "tool"
    APPROVAL = "approval"
    VERIFICATION = "verification"
    REGION = "region"
    PARAMETER = "parameter"


class CapabilityPrerequisite(CanonicalModel):
    """One descriptive prerequisite for a scientific capability."""

    prerequisite_identifier: str
    prerequisite_type: CapabilityPrerequisiteType
    required_identifier: str
    blocking: bool
    explanation: str = Field(min_length=1, max_length=_TEXT_MAXIMUM_LENGTH)

    @field_validator("prerequisite_identifier", "required_identifier")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("explanation")
    @classmethod
    def normalize_explanation(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Prerequisite explanations cannot be blank.")
        return value


class CapabilityLimitOperator(str, Enum):
    """Comparison operators for declared applicability limits."""

    EQUAL = "equal"
    NOT_EQUAL = "not_equal"
    LESS_THAN = "less_than"
    LESS_THAN_OR_EQUAL = "less_than_or_equal"
    GREATER_THAN = "greater_than"
    GREATER_THAN_OR_EQUAL = "greater_than_or_equal"


class CapabilityLimit(CanonicalModel):
    """A domain-neutral applicability limit declaration."""

    dimension: str
    operator: CapabilityLimitOperator
    threshold: ResourceValue
    unit: str
    explanation: str = Field(min_length=1, max_length=_TEXT_MAXIMUM_LENGTH)

    @field_validator("dimension", "unit")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("threshold")
    @classmethod
    def validate_threshold(cls, value: ResourceValue) -> ResourceValue:
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("Capability limit thresholds must be finite.")
        return value

    @field_validator("explanation")
    @classmethod
    def normalize_explanation(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Capability limit explanations cannot be blank.")
        return value


class CapabilityEstimateType(str, Enum):
    """Kinds of explicitly uncertain capability estimates."""

    RUNTIME = "runtime"
    MONETARY_COST = "monetary_cost"
    QUANTUM_USAGE = "quantum_usage"
    MEMORY = "memory"
    STORAGE = "storage"
    SCIENTIFIC_ERROR = "scientific_error"


_NONNEGATIVE_ESTIMATE_TYPES = frozenset(
    {
        CapabilityEstimateType.RUNTIME,
        CapabilityEstimateType.MONETARY_COST,
        CapabilityEstimateType.QUANTUM_USAGE,
        CapabilityEstimateType.MEMORY,
        CapabilityEstimateType.STORAGE,
    }
)


class CapabilityEstimate(CanonicalModel):
    """A bounded estimate, explicitly distinct from a guarantee."""

    estimate_type: CapabilityEstimateType
    lower_bound: float | None = None
    expected_value: float | None = None
    upper_bound: float | None = None
    unit: str
    confidence: float | None = Field(default=None, ge=0, le=1)
    estimation_basis: str = Field(min_length=1, max_length=_TEXT_MAXIMUM_LENGTH)
    evidence_artifacts: tuple[ArtifactPointer, ...] = ()

    @field_validator("unit")
    @classmethod
    def validate_unit(cls, value: str) -> str:
        return validate_identifier(value, label="estimate unit")

    @field_validator("lower_bound", "expected_value", "upper_bound", mode="before")
    @classmethod
    def validate_numeric_values(cls, value: object) -> object:
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, (int, float))
        ):
            raise ValueError("Capability estimate values must be numeric.")
        if value is not None and not math.isfinite(value):
            raise ValueError("Capability estimate values must be finite.")
        return value

    @field_validator("confidence", mode="before")
    @classmethod
    def validate_confidence(cls, value: object) -> object:
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, (int, float))
        ):
            raise ValueError("Capability estimate confidence must be numeric.")
        return value

    @field_validator("estimation_basis")
    @classmethod
    def normalize_basis(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("An estimation basis cannot be blank.")
        return value

    @field_validator("evidence_artifacts")
    @classmethod
    def order_evidence(
        cls, value: tuple[ArtifactPointer, ...]
    ) -> tuple[ArtifactPointer, ...]:
        return _ordered_artifacts(value, label="estimate evidence")

    @model_validator(mode="after")
    def validate_bounds(self) -> Self:
        values = (self.lower_bound, self.expected_value, self.upper_bound)
        if all(value is None for value in values):
            raise ValueError(
                "A capability estimate requires at least one numeric value."
            )
        if self.lower_bound is not None and self.expected_value is not None:
            if self.lower_bound > self.expected_value:
                raise ValueError(
                    "Estimate lower bound cannot exceed its expected value."
                )
        if self.expected_value is not None and self.upper_bound is not None:
            if self.expected_value > self.upper_bound:
                raise ValueError(
                    "Estimate expected value cannot exceed its upper bound."
                )
        if self.lower_bound is not None and self.upper_bound is not None:
            if self.lower_bound > self.upper_bound:
                raise ValueError("Estimate lower bound cannot exceed its upper bound.")
        if self.estimate_type in _NONNEGATIVE_ESTIMATE_TYPES and any(
            value is not None and value < 0 for value in values
        ):
            raise ValueError(
                "The selected capability estimate type cannot contain negative values."
            )
        return self


class CapabilityFidelityLevel(str, Enum):
    """Broad planning classification for declared scientific fidelity."""

    EXPLORATORY = "exploratory"
    SCREENING = "screening"
    QUANTITATIVE = "quantitative"
    REFERENCE = "reference"
    UNKNOWN = "unknown"


class CapabilityFidelityDeclaration(CanonicalModel):
    """Conditional, evidence-bearing fidelity claimed for an objective type."""

    objective_type: str
    fidelity_level: CapabilityFidelityLevel
    metric_identifier: str | None = None
    expected_error: CapabilityEstimate | None = None
    conditions: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    evidence_artifacts: tuple[ArtifactPointer, ...] = ()

    @field_validator("objective_type")
    @classmethod
    def validate_objective_type(cls, value: str) -> str:
        return validate_identifier(value, label="objective type")

    @field_validator("metric_identifier")
    @classmethod
    def validate_metric(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_identifier(value, label="metric identifier")

    @field_validator("conditions", "limitations")
    @classmethod
    def order_text(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _ordered_text(value, label="fidelity declaration")

    @field_validator("evidence_artifacts")
    @classmethod
    def order_evidence(
        cls, value: tuple[ArtifactPointer, ...]
    ) -> tuple[ArtifactPointer, ...]:
        return _ordered_artifacts(value, label="fidelity evidence")

    @model_validator(mode="after")
    def validate_expected_error(self) -> Self:
        if (
            self.expected_error is not None
            and self.expected_error.estimate_type
            != CapabilityEstimateType.SCIENTIFIC_ERROR
        ):
            raise ValueError(
                "Fidelity expected error must be a scientific-error estimate."
            )
        return self


class CapabilityVerificationRequirement(CanonicalModel):
    """A declaration that a verifier outcome is required at a specific time."""

    verifier_identifier: str
    subject_artifact_type: str
    acceptable_outcomes: tuple[ScientificVerificationOutcome, ...]
    required_before_execution: bool = False
    required_after_execution: bool = False
    blocking: bool

    @field_validator("verifier_identifier", "subject_artifact_type")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("acceptable_outcomes")
    @classmethod
    def order_outcomes(
        cls, value: tuple[ScientificVerificationOutcome, ...]
    ) -> tuple[ScientificVerificationOutcome, ...]:
        if not value:
            raise ValueError(
                "At least one acceptable verification outcome is required."
            )
        if len(value) != len(set(value)):
            raise ValueError("Acceptable verification outcomes must be unique.")
        return tuple(sorted(value, key=lambda outcome: outcome.value))

    @model_validator(mode="after")
    def validate_timing(self) -> Self:
        if not (self.required_before_execution or self.required_after_execution):
            raise ValueError("A verification requirement must declare when it applies.")
        return self


class CapabilityExecutionEnvelope(CanonicalModel):
    """A descriptive scientific planning envelope around a capability identity."""

    descriptor: CapabilityDescriptor
    supported_objective_types: tuple[str, ...] = ()
    prerequisites: tuple[CapabilityPrerequisite, ...] = ()
    execution_targets: tuple[str, ...] = ()
    resource_requirements: tuple[ResourceQuantity, ...] = ()
    applicability_limits: tuple[CapabilityLimit, ...] = ()
    estimates: tuple[CapabilityEstimate, ...] = ()
    fidelity_declarations: tuple[CapabilityFidelityDeclaration, ...] = ()
    known_limitations: tuple[str, ...] = ()
    verification_requirements: tuple[CapabilityVerificationRequirement, ...] = ()
    approval_requirements: tuple[str, ...] = ()
    metadata: BoundedMetadata = Field(default_factory=dict)

    @field_validator(
        "supported_objective_types", "execution_targets", "approval_requirements"
    )
    @classmethod
    def order_identifiers(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _ordered_identifiers(value, label="envelope identifier")

    @field_validator("prerequisites")
    @classmethod
    def order_prerequisites(
        cls, value: tuple[CapabilityPrerequisite, ...]
    ) -> tuple[CapabilityPrerequisite, ...]:
        identities = [item.prerequisite_identifier for item in value]
        if len(identities) != len(set(identities)):
            raise ValueError("Capability prerequisite identifiers must be unique.")
        return tuple(sorted(value, key=lambda item: item.prerequisite_identifier))

    @field_validator("resource_requirements")
    @classmethod
    def order_resources(
        cls, value: tuple[ResourceQuantity, ...]
    ) -> tuple[ResourceQuantity, ...]:
        identities = [(item.dimension, item.unit) for item in value]
        if len(identities) != len(set(identities)):
            raise ValueError(
                "Capability resource dimension and unit pairs must be unique."
            )
        return tuple(sorted(value, key=lambda item: (item.dimension, item.unit)))

    @field_validator("applicability_limits", "estimates", "fidelity_declarations")
    @classmethod
    def order_fingerprinted_contracts(
        cls, value: tuple[CanonicalModel, ...]
    ) -> tuple[CanonicalModel, ...]:
        fingerprints = [item.fingerprint for item in value]
        if len(fingerprints) != len(set(fingerprints)):
            raise ValueError("Envelope declarations must be unique.")
        return tuple(sorted(value, key=lambda item: item.fingerprint))

    @field_validator("known_limitations")
    @classmethod
    def order_limitations(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _ordered_text(value, label="known limitation")

    @field_validator("verification_requirements")
    @classmethod
    def order_verifications(
        cls, value: tuple[CapabilityVerificationRequirement, ...]
    ) -> tuple[CapabilityVerificationRequirement, ...]:
        identities = [
            (item.verifier_identifier, item.subject_artifact_type) for item in value
        ]
        if len(identities) != len(set(identities)):
            raise ValueError("Capability verification requirements must be unique.")
        return tuple(
            sorted(
                value,
                key=lambda item: (item.verifier_identifier, item.subject_artifact_type),
            )
        )

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: BoundedMetadata) -> BoundedMetadata:
        return validate_bounded_metadata(value)

    @property
    def capability_identity(self) -> tuple[str, CapabilityVersion]:
        """Return the existing descriptor's exact name/version identity."""
        return self.descriptor.capability_name, self.descriptor.version


class ScientificCapabilityCatalogError(Exception):
    """Base class for controlled scientific catalogue errors."""


class DuplicateScientificCapabilityError(ScientificCapabilityCatalogError):
    """Raised when an exact scientific capability identity already exists."""


class ScientificCapabilityNotFoundError(ScientificCapabilityCatalogError):
    """Raised when an exact scientific capability identity is not registered."""


_CatalogKey = tuple[str, int, int, int]


def _catalog_key(name: str, version: CapabilityVersion) -> _CatalogKey:
    return name, version.major, version.minor, version.patch


class ScientificCapabilityCatalog:
    """Deterministic, non-executing catalogue of immutable envelopes."""

    def __init__(self, envelopes: Iterable[CapabilityExecutionEnvelope] = ()) -> None:
        self._envelopes: dict[_CatalogKey, CapabilityExecutionEnvelope] = {}
        self.register_many(envelopes)

    def register(self, envelope: CapabilityExecutionEnvelope) -> None:
        """Register one envelope under its exact descriptor identity."""
        self.register_many((envelope,))

    def register_many(self, envelopes: Iterable[CapabilityExecutionEnvelope]) -> None:
        """Atomically register envelopes after validating every supplied identity."""
        supplied = tuple(envelopes)
        pending: dict[_CatalogKey, CapabilityExecutionEnvelope] = {}
        for envelope in supplied:
            if not isinstance(envelope, CapabilityExecutionEnvelope):
                raise ScientificCapabilityCatalogError(
                    "Scientific capability catalogues accept execution envelopes only."
                )
            key = _catalog_key(*envelope.capability_identity)
            if key in self._envelopes or key in pending:
                raise DuplicateScientificCapabilityError(
                    "The exact scientific capability identity is already registered."
                )
            pending[key] = envelope
        self._envelopes.update(pending)

    def get(
        self, capability_name: str, version: CapabilityVersion
    ) -> CapabilityExecutionEnvelope:
        """Return an envelope matching an exact name and version."""
        capability_name = validate_identifier(capability_name, label="capability name")
        if not isinstance(version, CapabilityVersion):
            raise ScientificCapabilityCatalogError(
                "Capability version must be a CapabilityVersion."
            )
        try:
            return self._envelopes[_catalog_key(capability_name, version)]
        except KeyError as exc:
            raise ScientificCapabilityNotFoundError(
                "The exact scientific capability identity is not registered."
            ) from exc

    def envelopes(self) -> tuple[CapabilityExecutionEnvelope, ...]:
        """Return every envelope in deterministic name/version order."""
        return tuple(self._envelopes[key] for key in sorted(self._envelopes))

    def capability_names(self) -> tuple[str, ...]:
        """Return sorted unique registered capability names."""
        return tuple(sorted({key[0] for key in self._envelopes}))

    def find(
        self,
        *,
        objective_type: str | None = None,
        input_artifact_types: tuple[str, ...] = (),
        execution_target: str | None = None,
    ) -> tuple[CapabilityExecutionEnvelope, ...]:
        """Apply deterministic declared-support filters without probing or execution."""
        if objective_type is not None:
            objective_type = validate_identifier(objective_type, label="objective type")
        requested_artifacts = _ordered_identifiers(
            input_artifact_types, label="input artifact type"
        )
        if execution_target is not None:
            execution_target = validate_identifier(
                execution_target, label="execution target"
            )

        matches: list[CapabilityExecutionEnvelope] = []
        for envelope in self.envelopes():
            if objective_type is not None and (
                envelope.supported_objective_types
                and objective_type not in envelope.supported_objective_types
            ):
                continue
            accepted_artifacts = set(envelope.descriptor.accepted_artifact_types)
            if (
                requested_artifacts
                and accepted_artifacts
                and not set(requested_artifacts).issubset(accepted_artifacts)
            ):
                continue
            if execution_target is not None and (
                envelope.execution_targets
                and execution_target not in envelope.execution_targets
            ):
                continue
            matches.append(envelope)
        return tuple(matches)
