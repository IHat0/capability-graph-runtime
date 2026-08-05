"""Stable scientific-engine adapter contracts for Product Phase 5."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import Field, field_validator, model_validator

from cgr.kernel.contracts import CapabilityVersion, HealthStatus

from .canonical import (
    BoundedMetadata,
    CanonicalModel,
    validate_bounded_metadata,
    validate_identifier,
)
from .capabilities import CapabilityExecutionEnvelope
from .contracts import CapabilityInvocation, CapabilityResult


class ScientificEngineIdentity(CanonicalModel):
    """Exact identity of one external scientific engine installation."""

    engine_identifier: str
    engine_version: str = Field(min_length=1, max_length=128)

    @field_validator("engine_identifier")
    @classmethod
    def validate_engine_identifier(cls, value: str) -> str:
        return validate_identifier(value, label="scientific engine identifier")

    @field_validator("engine_version")
    @classmethod
    def validate_engine_version(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Scientific engine version cannot be blank.")
        return normalized


class ScientificEngineAdapterDeclaration(CanonicalModel):
    """Immutable declaration connecting an adapter to planner capabilities."""

    adapter_identifier: str
    adapter_version: CapabilityVersion
    engine: ScientificEngineIdentity
    capabilities: tuple[CapabilityExecutionEnvelope, ...]
    metadata: BoundedMetadata = Field(default_factory=dict)

    @field_validator("adapter_identifier")
    @classmethod
    def validate_adapter_identifier(cls, value: str) -> str:
        return validate_identifier(value, label="scientific adapter identifier")

    @field_validator("capabilities")
    @classmethod
    def validate_and_order_capabilities(
        cls,
        value: tuple[CapabilityExecutionEnvelope, ...],
    ) -> tuple[CapabilityExecutionEnvelope, ...]:
        if not value:
            raise ValueError(
                "Scientific engine adapters must declare at least one capability."
            )

        identities = [
            (
                envelope.descriptor.capability_name,
                envelope.descriptor.version.major,
                envelope.descriptor.version.minor,
                envelope.descriptor.version.patch,
            )
            for envelope in value
        ]
        if len(identities) != len(set(identities)):
            raise ValueError(
                "Scientific engine adapter capability identities must be unique."
            )

        return tuple(
            sorted(
                value,
                key=lambda envelope: (
                    envelope.descriptor.capability_name,
                    envelope.descriptor.version.major,
                    envelope.descriptor.version.minor,
                    envelope.descriptor.version.patch,
                ),
            )
        )

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: BoundedMetadata) -> BoundedMetadata:
        return validate_bounded_metadata(value)

    @property
    def capability_identities(
        self,
    ) -> tuple[tuple[str, CapabilityVersion], ...]:
        """Return exact declared capability identities in deterministic order."""

        return tuple(envelope.capability_identity for envelope in self.capabilities)


class ScientificEngineHealthReport(CanonicalModel):
    """Bounded operational health information for one scientific adapter."""

    status: HealthStatus
    reason_code: str | None = None
    message: str | None = Field(default=None, max_length=4096)
    diagnostics: BoundedMetadata = Field(default_factory=dict)

    @field_validator("reason_code")
    @classmethod
    def validate_reason_code(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_identifier(value, label="adapter health reason code")

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("Adapter health messages cannot be blank.")
        return normalized

    @field_validator("diagnostics")
    @classmethod
    def validate_diagnostics(cls, value: BoundedMetadata) -> BoundedMetadata:
        return validate_bounded_metadata(value)

    @model_validator(mode="after")
    def validate_status_details(self) -> "ScientificEngineHealthReport":
        has_reason = self.reason_code is not None
        has_message = self.message is not None

        if self.status is HealthStatus.HEALTHY:
            if has_reason or has_message:
                raise ValueError(
                    "Healthy scientific adapters cannot carry a failure reason."
                )
            return self

        if not has_reason or not has_message:
            raise ValueError(
                "Degraded or unavailable scientific adapters require a safe reason."
            )
        return self


@runtime_checkable
class ScientificEngineAdapter(Protocol):
    """Structural boundary implemented by every Phase 5 scientific engine."""

    @property
    def declaration(self) -> ScientificEngineAdapterDeclaration:
        """Return immutable adapter, engine and capability declarations."""
        ...

    def health(self) -> ScientificEngineHealthReport:
        """Return the adapter's current bounded operational health."""
        ...

    def invoke(self, invocation: CapabilityInvocation) -> CapabilityResult:
        """Execute one declared capability using generic Pulsate contracts."""
        ...
