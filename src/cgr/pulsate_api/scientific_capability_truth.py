"""Ground scientific planning in exact production executor registrations.

The Phase 8 artifact catalogue describes what a capability would consume and
produce.  The scientist runtime registry describes which handlers can actually
execute.  This module joins those declarations without probing tools or
silently treating a test double as production capability.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable, Mapping
from enum import Enum

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from cgr.science.canonical import validate_identifier

from .scientific_capability_catalogue import (
    ScientificCapabilityCatalogue,
    public_capability_name,
)


class ScientificCapabilityImplementation(str, Enum):
    """Closed implementation classes exposed at the product boundary."""

    PRODUCTION_EXECUTOR = "production_executor"
    EXTERNAL_EXECUTOR = "external_executor"
    DETERMINISTIC_FALLBACK = "deterministic_fallback"
    TEST_ONLY = "test_only"
    UNAVAILABLE = "unavailable"


class ScientificCapabilityTruth(BaseModel):
    """One planner-visible capability backed by exact runtime evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    capability_name: str
    implementation: ScientificCapabilityImplementation
    declared: bool
    handler_registered: bool
    configured: bool
    selectable: bool
    supported_task_types: tuple[str, ...] = ()
    reason: str

    @field_validator("capability_name")
    @classmethod
    def capability_identifier(cls, value: str) -> str:
        return validate_identifier(value, label="scientific capability name")

    @field_validator("supported_task_types")
    @classmethod
    def task_identifiers(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted(validate_identifier(item) for item in value))
        if len(normalized) != len(set(normalized)):
            raise ValueError("Scientific capability task declarations must be unique.")
        return normalized

    @field_validator("reason")
    @classmethod
    def nonempty_reason(cls, value: str) -> str:
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError("Scientific capability truth requires a reason.")
        return normalized

    @model_validator(mode="after")
    def selection_is_evidence_bound(self) -> ScientificCapabilityTruth:
        if self.selectable and not (
            self.declared and self.handler_registered and self.configured
        ):
            raise ValueError(
                "Selectable scientific capabilities require a declaration, "
                "registered handler, and configured implementation."
            )
        if self.selectable and self.implementation in {
            ScientificCapabilityImplementation.TEST_ONLY,
            ScientificCapabilityImplementation.UNAVAILABLE,
        }:
            raise ValueError(
                "Test-only or unavailable scientific capabilities cannot be selected."
            )
        return self


class ScientificCapabilityGroundingError(ValueError):
    """Raised before persistence when a plan names no production executor."""

    def __init__(self, capability_names: Iterable[str]) -> None:
        self.capability_names = tuple(sorted(set(capability_names)))
        super().__init__(
            "Required scientific capabilities are not production-selectable: "
            + ", ".join(self.capability_names)
        )


class ScientificCapabilityTruthCatalogue:
    """Immutable, fingerprinted join of declarations and runtime handlers."""

    def __init__(self, entries: Iterable[ScientificCapabilityTruth]) -> None:
        values = tuple(sorted(entries, key=lambda item: item.capability_name))
        names = [item.capability_name for item in values]
        if len(names) != len(set(names)):
            raise ValueError("Scientific capability truth identities must be unique.")
        self.entries = values
        payload = [item.model_dump(mode="json") for item in values]
        self.fingerprint = hashlib.sha256(
            json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    @classmethod
    def from_runtime(
        cls,
        *,
        catalogue: ScientificCapabilityCatalogue,
        registered_capability_names: Iterable[str],
        implementation_overrides: Mapping[
            str, ScientificCapabilityImplementation
        ] | None = None,
        configured_external_capability_names: Iterable[str] = (),
    ) -> ScientificCapabilityTruthCatalogue:
        """Build truth from declarations, registrations, and explicit configuration."""

        registered = {
            validate_identifier(item, label="registered scientific capability")
            for item in registered_capability_names
        }
        configured_external = {
            validate_identifier(item, label="configured external capability")
            for item in configured_external_capability_names
        }
        overrides = {
            validate_identifier(name, label="scientific capability override"): value
            for name, value in (implementation_overrides or {}).items()
        }
        declared_tasks: dict[str, set[str]] = defaultdict(set)
        for definition in catalogue.definitions:
            name = public_capability_name(definition.capability_name)
            declared_tasks[name].update(definition.supported_task_types)

        unknown_overrides = set(overrides) - (set(declared_tasks) | registered)
        if unknown_overrides:
            raise ValueError(
                "Scientific capability implementation overrides are undeclared: "
                + ", ".join(sorted(unknown_overrides))
            )

        entries: list[ScientificCapabilityTruth] = []
        for name in sorted(set(declared_tasks) | registered):
            declared = name in declared_tasks
            handler_registered = name in registered
            implementation = overrides.get(
                name,
                (
                    ScientificCapabilityImplementation.PRODUCTION_EXECUTOR
                    if declared and handler_registered
                    else ScientificCapabilityImplementation.UNAVAILABLE
                ),
            )
            configured = (
                name in configured_external
                if implementation
                is ScientificCapabilityImplementation.EXTERNAL_EXECUTOR
                else implementation
                is not ScientificCapabilityImplementation.UNAVAILABLE
            )
            selectable = bool(
                declared
                and handler_registered
                and configured
                and implementation
                not in {
                    ScientificCapabilityImplementation.TEST_ONLY,
                    ScientificCapabilityImplementation.UNAVAILABLE,
                }
            )
            if not declared:
                reason = "A runtime handler exists without a planner declaration."
                implementation = ScientificCapabilityImplementation.UNAVAILABLE
                configured = False
                selectable = False
            elif not handler_registered:
                reason = "The planner declaration has no registered runtime handler."
                implementation = ScientificCapabilityImplementation.UNAVAILABLE
                configured = False
                selectable = False
            elif implementation is ScientificCapabilityImplementation.TEST_ONLY:
                reason = "The registered handler is explicitly restricted to tests."
            elif implementation is ScientificCapabilityImplementation.EXTERNAL_EXECUTOR:
                reason = (
                    "The external executor is registered and configured."
                    if configured
                    else "The external executor is registered but not configured."
                )
            elif implementation is ScientificCapabilityImplementation.DETERMINISTIC_FALLBACK:
                reason = "A declared deterministic fallback is registered."
            elif implementation is ScientificCapabilityImplementation.PRODUCTION_EXECUTOR:
                reason = "A declared production executor is registered."
            else:
                reason = "No selectable production implementation is registered."

            entries.append(
                ScientificCapabilityTruth(
                    capability_name=name,
                    implementation=implementation,
                    declared=declared,
                    handler_registered=handler_registered,
                    configured=configured,
                    selectable=selectable,
                    supported_task_types=tuple(sorted(declared_tasks.get(name, ()))),
                    reason=reason,
                )
            )
        return cls(entries)

    def get(self, capability_name: str) -> ScientificCapabilityTruth | None:
        name = validate_identifier(
            capability_name, label="scientific capability name"
        )
        return next(
            (item for item in self.entries if item.capability_name == name),
            None,
        )

    def selectable_capability_names(self) -> tuple[str, ...]:
        return tuple(item.capability_name for item in self.entries if item.selectable)

    def require_selectable(self, capability_names: Iterable[str]) -> None:
        requested = tuple(
            validate_identifier(item, label="planned scientific capability")
            for item in capability_names
        )
        selectable = set(self.selectable_capability_names())
        missing = tuple(sorted(set(requested) - selectable))
        if missing:
            raise ScientificCapabilityGroundingError(missing)

    def summary(self) -> dict[str, object]:
        counts = {
            implementation.value: sum(
                item.implementation is implementation for item in self.entries
            )
            for implementation in ScientificCapabilityImplementation
        }
        return {
            "available": True,
            "policy": "declared_and_registered_production_executors_only",
            "fingerprint": self.fingerprint,
            "entry_count": len(self.entries),
            "selectable_count": len(self.selectable_capability_names()),
            "classification_counts": counts,
            "capabilities": tuple(
                item.model_dump(mode="json") for item in self.entries
            ),
        }


__all__ = [
    "ScientificCapabilityGroundingError",
    "ScientificCapabilityImplementation",
    "ScientificCapabilityTruth",
    "ScientificCapabilityTruthCatalogue",
]
