"""Content-addressed scientific artifacts and lineage contracts."""

from __future__ import annotations

import re
from typing import Self

from pydantic import Field, field_validator, model_validator

from cgr.kernel.contracts import CapabilityVersion

from .canonical import (
    BoundedMetadata,
    CanonicalModel,
    validate_bounded_metadata,
    validate_identifier,
    validate_sha256,
    validate_storage_location,
)

_MEDIA_TYPE = re.compile(r"^[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+$")


class CreationProvenance(CanonicalModel):
    """Stable provenance for creation of a scientific artifact."""

    producer: str
    producer_version: CapabilityVersion | None = None
    execution_identifier: str | None = None
    source: str = "cgr"

    @field_validator("producer", "source")
    @classmethod
    def validate_names(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("execution_identifier")
    @classmethod
    def validate_execution_identifier(cls, value: str | None) -> str | None:
        return validate_identifier(value) if value is not None else None


class ArtifactPointer(CanonicalModel):
    """Compact immutable pointer to a content-addressed artifact."""

    artifact_identifier: str
    content_sha256: str

    @field_validator("artifact_identifier")
    @classmethod
    def validate_artifact_identifier(cls, value: str) -> str:
        return validate_identifier(value, label="artifact identifier")

    @field_validator("content_sha256")
    @classmethod
    def validate_content_sha256(cls, value: str) -> str:
        return validate_sha256(value)

class ArtifactReference(CanonicalModel):
    """Immutable reference to artifact bytes stored outside the contract."""

    artifact_identifier: str
    schema_version: CapabilityVersion
    artifact_type: str
    media_type: str = Field(min_length=3, max_length=127)
    content_sha256: str
    byte_size: int | None = Field(default=None, ge=0)
    storage_location: str | None = None
    metadata: BoundedMetadata = Field(default_factory=dict)
    provenance: CreationProvenance
    parents: tuple[ArtifactPointer, ...] = ()

    @field_validator("artifact_identifier", "artifact_type")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("content_sha256")
    @classmethod
    def validate_content_sha256(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("media_type")
    @classmethod
    def validate_media_type(cls, value: str) -> str:
        if not _MEDIA_TYPE.fullmatch(value):
            raise ValueError("Artifact media type must use a valid type/subtype form.")
        return value.lower()

    @field_validator("storage_location")
    @classmethod
    def validate_location(cls, value: str | None) -> str | None:
        return validate_storage_location(value)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: BoundedMetadata) -> BoundedMetadata:
        return validate_bounded_metadata(value)

    @field_validator("parents")
    @classmethod
    def order_parents(
        cls, value: tuple[ArtifactPointer, ...]
    ) -> tuple[ArtifactPointer, ...]:
        if len(value) != len(set(value)):
            raise ValueError("Parent artifact references must be unique.")
        return tuple(sorted(value, key=lambda item: (item.artifact_identifier, item.content_sha256)))

    @property
    def pointer(self) -> ArtifactPointer:
        """Return the compact content identity for this artifact."""
        return ArtifactPointer(
            artifact_identifier=self.artifact_identifier,
            content_sha256=self.content_sha256,
        )


class ArtifactLineageEdge(CanonicalModel):
    """Immutable relationship produced by one capability execution."""

    source: ArtifactPointer
    destination: ArtifactPointer
    relationship_type: str
    producing_capability: str
    producing_capability_version: CapabilityVersion
    execution_identifier: str | None = None
    verification_evidence: tuple[ArtifactPointer, ...] = ()

    @field_validator("relationship_type", "producing_capability")
    @classmethod
    def validate_names(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("execution_identifier")
    @classmethod
    def validate_execution_identifier(cls, value: str | None) -> str | None:
        return validate_identifier(value) if value is not None else None

    @field_validator("verification_evidence")
    @classmethod
    def order_evidence(
        cls, value: tuple[ArtifactPointer, ...]
    ) -> tuple[ArtifactPointer, ...]:
        return tuple(sorted(value, key=lambda item: (item.artifact_identifier, item.content_sha256)))

    @model_validator(mode="after")
    def reject_self_reference(self) -> Self:
        if self.source == self.destination:
            raise ValueError("Artifact lineage cannot reference itself.")
        return self


class ArtifactLineageGraph(CanonicalModel):
    """Deterministically serialized collection of unique lineage edges."""

    edges: tuple[ArtifactLineageEdge, ...] = ()

    @field_validator("edges")
    @classmethod
    def validate_and_order_edges(
        cls, value: tuple[ArtifactLineageEdge, ...]
    ) -> tuple[ArtifactLineageEdge, ...]:
        fingerprints = [edge.fingerprint for edge in value]
        if len(fingerprints) != len(set(fingerprints)):
            raise ValueError("Duplicate artifact lineage edges are not meaningful.")
        return tuple(sorted(value, key=lambda edge: edge.fingerprint))

    def add(self, edge: ArtifactLineageEdge) -> ArtifactLineageGraph:
        """Return a new graph containing one additional unique edge."""
        return ArtifactLineageGraph(edges=(*self.edges, edge))
