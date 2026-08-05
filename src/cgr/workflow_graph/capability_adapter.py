"""Generic capability invocation boundary for the Phase 4 orchestrator."""

from __future__ import annotations

import math
import threading
from collections.abc import Callable, Mapping
from enum import Enum
from typing import Protocol, Self

from pydantic import Field, field_validator, model_validator

from cgr.science.artifacts import ArtifactPointer, ArtifactReference
from cgr.science.canonical import CanonicalModel, validate_identifier, validate_sha256

from .contracts import FrozenDict, NodeKind, _freeze_json, _frozen_numeric_map, _frozen_string_map


class CapabilityInvocationStatus(str, Enum):
    """Closed outcomes returned by capability adapters."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class CapabilityInvocation(CanonicalModel):
    """Immutable, idempotent request from orchestration to one capability."""

    invocation_identifier: str
    idempotency_identifier: str
    graph_run_identifier: str
    graph_definition_fingerprint: str
    node_identifier: str
    attempt_number: int = Field(ge=1, le=10)
    capability_identity: str
    node_kind: NodeKind
    molecular_project_identifier: str | None = None
    molecular_system_identifiers: tuple[str, ...] = ()
    molecular_region_identifiers: tuple[str, ...] = ()
    execution_target: str | None = None
    input_artifacts: tuple[ArtifactPointer, ...] = ()
    input_values: FrozenDict = Field(default_factory=FrozenDict)
    parameters: FrozenDict = Field(default_factory=FrozenDict)
    timeout_seconds: float | None = Field(default=None, gt=0)
    approved: bool = False
    cancellation_requested: bool = False
    correlation_identifier: str | None = None

    @field_validator(
        "invocation_identifier",
        "idempotency_identifier",
        "graph_run_identifier",
        "node_identifier",
        "capability_identity",
        "molecular_project_identifier",
        "execution_target",
        "correlation_identifier",
    )
    @classmethod
    def validate_identifiers(cls, value: str | None) -> str | None:
        return validate_identifier(value) if value is not None else None


    @field_validator(
        "molecular_system_identifiers", "molecular_region_identifiers"
    )
    @classmethod
    def order_molecular_context(
        cls, value: tuple[str, ...]
    ) -> tuple[str, ...]:
        normalized = tuple(validate_identifier(item) for item in value)
        if len(normalized) != len(set(normalized)):
            raise ValueError("Capability invocation molecular identities must be unique.")
        if len(normalized) > 100:
            raise ValueError("Capability invocation molecular context is too large.")
        return tuple(sorted(normalized))

    @field_validator("graph_definition_fingerprint")
    @classmethod
    def validate_graph_fingerprint(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("input_artifacts")
    @classmethod
    def order_input_artifacts(
        cls, value: tuple[ArtifactPointer, ...]
    ) -> tuple[ArtifactPointer, ...]:
        identities = [(item.artifact_identifier, item.content_sha256) for item in value]
        if len(identities) != len(set(identities)):
            raise ValueError("Capability invocation input artifacts must be unique.")
        return tuple(
            sorted(value, key=lambda item: (item.artifact_identifier, item.content_sha256))
        )

    @field_validator("input_values", "parameters", mode="before")
    @classmethod
    def validate_json_maps(cls, value: object) -> FrozenDict:
        frozen = _freeze_json(value)
        if not isinstance(frozen, FrozenDict):
            raise ValueError("Capability invocation values must be JSON objects.")
        return frozen

    @field_validator("timeout_seconds")
    @classmethod
    def validate_timeout(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("Capability invocation timeout must be finite.")
        return value


class CapabilityInvocationResult(CanonicalModel):
    """Immutable result returned across the generic capability boundary."""

    invocation_identifier: str
    status: CapabilityInvocationStatus
    output_artifacts: tuple[ArtifactReference, ...] = ()
    output_values: FrozenDict = Field(default_factory=FrozenDict)
    verification_classification: str | None = None
    actual_cost: float | None = None
    resources_consumed: FrozenDict = Field(default_factory=FrozenDict)
    unknown_consumption: tuple[str, ...] = ()
    retryable: bool = False
    error_code: str | None = None
    error_message: str | None = None
    execution_metadata: FrozenDict = Field(default_factory=FrozenDict)

    @field_validator(
        "invocation_identifier",
        "verification_classification",
        "error_code",
    )
    @classmethod
    def validate_identifiers(cls, value: str | None) -> str | None:
        return validate_identifier(value) if value is not None else None

    @field_validator("output_artifacts")
    @classmethod
    def order_output_artifacts(
        cls, value: tuple[ArtifactReference, ...]
    ) -> tuple[ArtifactReference, ...]:
        identities = [item.artifact_identifier for item in value]
        if len(identities) != len(set(identities)):
            raise ValueError("Capability result output artifacts must be unique.")
        return tuple(sorted(value, key=lambda item: item.artifact_identifier))

    @field_validator("output_values", mode="before")
    @classmethod
    def validate_output_values(cls, value: object) -> FrozenDict:
        frozen = _freeze_json(value)
        if not isinstance(frozen, FrozenDict):
            raise ValueError("Capability result values must be a JSON object.")
        return frozen

    @field_validator("actual_cost")
    @classmethod
    def validate_actual_cost(cls, value: float | None) -> float | None:
        if value is None:
            return None
        if not math.isfinite(value) or value < 0:
            raise ValueError("Capability result actual cost must be finite and non-negative.")
        return value

    @field_validator("resources_consumed")
    @classmethod
    def validate_resources(cls, value: FrozenDict) -> FrozenDict:
        return _frozen_numeric_map(value, label="capability resource consumption")

    @field_validator("unknown_consumption")
    @classmethod
    def order_unknown_consumption(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(validate_identifier(item) for item in value)
        if len(normalized) != len(set(normalized)):
            raise ValueError("Unknown consumption dimensions must be unique.")
        return tuple(sorted(normalized))

    @field_validator("error_message")
    @classmethod
    def validate_error_message(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized or len(normalized) > 4096:
            raise ValueError("Capability error messages must be nonempty and bounded.")
        return normalized

    @field_validator("execution_metadata")
    @classmethod
    def validate_metadata(cls, value: FrozenDict) -> FrozenDict:
        return _frozen_string_map(value, label="capability execution metadata")

    @model_validator(mode="after")
    def validate_outcome(self) -> Self:
        successful = self.status is CapabilityInvocationStatus.SUCCEEDED
        if successful and (self.error_code is not None or self.error_message is not None):
            raise ValueError("Successful capability results cannot carry an error.")
        if not successful and (self.error_code is None or self.error_message is None):
            raise ValueError("Unsuccessful capability results require a safe error.")
        if self.retryable and self.status is not CapabilityInvocationStatus.FAILED:
            raise ValueError("Only failed capability results may be retryable.")
        return self


class CapabilityAdapter(Protocol):
    """Stable adapter boundary implemented by Phase 5 engines and existing runtimes."""

    def invoke(self, invocation: CapabilityInvocation) -> CapabilityInvocationResult:
        ...


CapabilityHandler = Callable[[CapabilityInvocation], CapabilityInvocationResult]


class CapabilityAdapterRegistry:
    """Thread-safe exact-identity registry for generic capability handlers."""

    def __init__(self, handlers: Mapping[str, CapabilityHandler] | None = None) -> None:
        self._handlers: dict[str, CapabilityHandler] = {}
        self._lock = threading.RLock()
        for identity, handler in (handlers or {}).items():
            self.register(identity, handler)

    def register(self, capability_identity: str, handler: CapabilityHandler) -> None:
        identity = validate_identifier(capability_identity, label="capability identity")
        if not callable(handler):
            raise TypeError("Capability handlers must be callable.")
        with self._lock:
            if identity in self._handlers:
                raise ValueError("Capability identity is already registered.")
            self._handlers[identity] = handler

    def identities(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._handlers))

    def invoke(self, invocation: CapabilityInvocation) -> CapabilityInvocationResult:
        with self._lock:
            handler = self._handlers.get(invocation.capability_identity)
        if handler is None:
            return CapabilityInvocationResult(
                invocation_identifier=invocation.invocation_identifier,
                status=CapabilityInvocationStatus.BLOCKED,
                error_code="capability_unavailable",
                error_message="The requested capability is unavailable.",
            )
        result = handler(invocation)
        if not isinstance(result, CapabilityInvocationResult):
            raise TypeError("Capability handlers must return CapabilityInvocationResult.")
        if result.invocation_identifier != invocation.invocation_identifier:
            raise ValueError("Capability result does not match its invocation identity.")
        return result
