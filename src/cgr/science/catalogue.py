"""Immutable production capability-catalogue declarations."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Self

from pydantic import Field, field_validator, model_validator

from cgr.kernel.contracts import CapabilityVersion

from .artifacts import ArtifactPointer
from .canonical import (
    BoundedMetadata,
    CanonicalModel,
    canonical_json,
    sha256_fingerprint,
    validate_bounded_metadata,
    validate_identifier,
    validate_sha256,
)
from .capabilities import CapabilityExecutionEnvelope


PRODUCTION_CATALOGUE_SCHEMA_VERSION = "cgr.pulsate-capability-catalogue/1.0.0"


class ProductionCapabilityKind(str, Enum):
    """Closed v1 classification for declared scientific capabilities."""

    STRUCTURE_PREPARATION = "structure_preparation"
    MOLECULAR_SIMULATION = "molecular_simulation"
    ELECTRONIC_STRUCTURE = "electronic_structure"
    QUANTUM_SIMULATION = "quantum_simulation"
    QUANTUM_HARDWARE = "quantum_hardware"
    VISUAL_ANALYSIS = "visual_analysis"
    SCIENTIFIC_VERIFICATION = "scientific_verification"
    GENERIC_COMPUTATION = "generic_computation"


class CapabilityAvailabilityClassification(str, Enum):
    """Availability declared by validated production evidence, never probing."""

    AVAILABLE = "available"
    CONDITIONAL = "conditional"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


def _entry_identity(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != "entry_fingerprint"}


def capability_entry_fingerprint(value: dict[str, Any]) -> str:
    """Compute the declared entry fingerprint from its semantic content."""

    return sha256_fingerprint(_entry_identity(value))


class ProductionCapabilityEntry(CanonicalModel):
    """One evidence-backed production declaration around an execution envelope."""

    capability_kind: ProductionCapabilityKind
    provider_identity: str
    implementation_identity: str
    configuration_schema_version: CapabilityVersion
    supported_operations: tuple[str, ...] = Field(min_length=1, max_length=128)
    availability: CapabilityAvailabilityClassification
    declared_limitations: tuple[str, ...] = Field(default=(), max_length=128)
    evidence_references: tuple[ArtifactPointer, ...] = Field(default=(), max_length=128)
    envelope: CapabilityExecutionEnvelope
    entry_fingerprint: str

    @field_validator("provider_identity", "implementation_identity")
    @classmethod
    def validate_identities(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("supported_operations")
    @classmethod
    def order_operations(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted(validate_identifier(item) for item in value))
        if len(normalized) != len(set(normalized)):
            raise ValueError("Supported operations must be unique.")
        return normalized

    @field_validator("declared_limitations")
    @classmethod
    def order_limitations(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted(item.strip() for item in value))
        if any(not item or len(item) > 4096 for item in normalized):
            raise ValueError("Catalogue limitations are invalid.")
        if len(normalized) != len(set(normalized)):
            raise ValueError("Catalogue limitations must be unique.")
        return normalized

    @field_validator("evidence_references")
    @classmethod
    def order_evidence(
        cls, value: tuple[ArtifactPointer, ...]
    ) -> tuple[ArtifactPointer, ...]:
        identities = tuple(
            sorted(value, key=lambda item: (item.artifact_identifier, item.content_sha256))
        )
        if len(identities) != len(set(identities)):
            raise ValueError("Catalogue evidence references must be unique.")
        return identities

    @field_validator("entry_fingerprint")
    @classmethod
    def validate_entry_fingerprint(cls, value: str) -> str:
        return validate_sha256(value)

    @model_validator(mode="after")
    def validate_evidence_and_fingerprint(self) -> Self:
        if self.availability in {
            CapabilityAvailabilityClassification.AVAILABLE,
            CapabilityAvailabilityClassification.CONDITIONAL,
        } and not self.evidence_references:
            raise ValueError(
                "Selectable catalogue availability requires declared evidence."
            )
        if self.availability == CapabilityAvailabilityClassification.CONDITIONAL and not (
            self.envelope.prerequisites
            or self.envelope.approval_requirements
            or self.envelope.verification_requirements
        ):
            raise ValueError(
                "Conditional catalogue availability requires declared conditions."
            )
        expected = capability_entry_fingerprint(self.model_dump(mode="json"))
        if self.entry_fingerprint != expected:
            raise ValueError("Capability entry fingerprint is invalid.")
        return self


def catalogue_source_fingerprint(value: dict[str, Any]) -> str:
    """Hash catalogue source content excluding both declared aggregate hashes."""

    return sha256_fingerprint(
        {
            key: item
            for key, item in value.items()
            if key not in {"source_fingerprint", "catalogue_fingerprint"}
        }
    )


def complete_catalogue_fingerprint(value: dict[str, Any]) -> str:
    """Hash complete canonical catalogue content excluding only its own hash."""

    return sha256_fingerprint(
        {key: item for key, item in value.items() if key != "catalogue_fingerprint"}
    )


class ProductionCapabilityCatalogueDocument(CanonicalModel):
    """Canonical, versioned production catalogue document."""

    schema_version: str
    catalogue_identifier: str
    catalogue_version: CapabilityVersion
    entries: tuple[ProductionCapabilityEntry, ...] = Field(max_length=4096)
    released_at: datetime
    release_metadata: BoundedMetadata = Field(default_factory=dict)
    source_fingerprint: str
    catalogue_fingerprint: str

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, value: str) -> str:
        if value != PRODUCTION_CATALOGUE_SCHEMA_VERSION:
            raise ValueError("Capability catalogue schema version is unsupported.")
        return value

    @field_validator("catalogue_identifier")
    @classmethod
    def validate_catalogue_identifier(cls, value: str) -> str:
        return validate_identifier(value, label="catalogue identifier")

    @field_validator("release_metadata")
    @classmethod
    def validate_release_metadata(cls, value: BoundedMetadata) -> BoundedMetadata:
        return validate_bounded_metadata(value)

    @field_validator("released_at")
    @classmethod
    def require_release_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Catalogue release timestamps must be timezone-aware.")
        return value

    @field_validator("source_fingerprint", "catalogue_fingerprint")
    @classmethod
    def validate_fingerprints(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("entries")
    @classmethod
    def order_entries(
        cls, value: tuple[ProductionCapabilityEntry, ...]
    ) -> tuple[ProductionCapabilityEntry, ...]:
        ordered = tuple(
            sorted(
                value,
                key=lambda item: (
                    item.envelope.descriptor.capability_name,
                    item.envelope.descriptor.version.major,
                    item.envelope.descriptor.version.minor,
                    item.envelope.descriptor.version.patch,
                ),
            )
        )
        identities = tuple(
            (
                item.envelope.descriptor.capability_name,
                item.envelope.descriptor.version.major,
                item.envelope.descriptor.version.minor,
                item.envelope.descriptor.version.patch,
            )
            for item in ordered
        )
        if len(identities) != len(set(identities)):
            raise ValueError("Capability catalogue identities must be unique.")
        if value != ordered:
            raise ValueError("Capability catalogue entries must be deterministically ordered.")
        return value

    @model_validator(mode="after")
    def validate_document_fingerprints(self) -> Self:
        document = self.model_dump(mode="json")
        if self.source_fingerprint != catalogue_source_fingerprint(document):
            raise ValueError("Capability catalogue source fingerprint is invalid.")
        if self.catalogue_fingerprint != complete_catalogue_fingerprint(document):
            raise ValueError("Capability catalogue fingerprint is invalid.")
        return self

    def to_document_json(self) -> str:
        """Return the exact canonical source representation required on disk."""

        return canonical_json(self.model_dump(mode="json"))
