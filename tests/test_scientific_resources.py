"""Resource-domain scientific contract regressions."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from cgr.kernel.contracts import CapabilityVersion
from cgr.science.artifacts import CreationProvenance
from cgr.science.resources import ResourceAvailabilitySnapshot, ResourceQuantity

VERSION = CapabilityVersion(major=1, minor=0, patch=0)
PROVENANCE = CreationProvenance(producer="science.resource.fixture")


def _snapshot(**updates: object) -> ResourceAvailabilitySnapshot:
    values: dict[str, object] = {
        "snapshot_identifier": "snapshot.resources-001",
        "schema_version": VERSION,
        "observed_at": datetime(2026, 1, 1, tzinfo=UTC),
        "execution_environment": "environment.declared",
        "available_resources": (
            ResourceQuantity(dimension="memory_mib", unit="mib", value=4096),
            ResourceQuantity(dimension="cpu_cores", unit="count", value=4),
        ),
        "available_execution_targets": ("target.two", "target.one"),
        "unavailable_reasons": {"target.three": "Not declared available."},
        "provenance": PROVENANCE,
    }
    values.update(updates)
    return ResourceAvailabilitySnapshot.model_validate(values)


@pytest.mark.parametrize("value", (True, False, 0, -2, 1.5, -3.25))
def test_resource_quantity_accepts_canonical_numeric_and_boolean_values(
    value: bool | int | float,
) -> None:
    quantity = ResourceQuantity(dimension="resource.example", unit="unit.example", value=value)

    assert quantity.value == value
    assert type(quantity.value) is type(value)


@pytest.mark.parametrize("field", ("dimension", "unit"))
def test_resource_quantity_rejects_blank_identifiers(field: str) -> None:
    values = {"dimension": "cpu_cores", "unit": "count", "value": 1}
    values[field] = " "
    with pytest.raises(ValidationError, match="stable identifier"):
        ResourceQuantity.model_validate(values)


@pytest.mark.parametrize("value", (float("nan"), float("inf"), float("-inf")))
def test_resource_quantity_rejects_nonfinite_values(value: float) -> None:
    with pytest.raises(ValidationError):
        ResourceQuantity(dimension="memory_mib", unit="mib", value=value)


def test_resource_quantity_does_not_coerce_strings_and_preserves_booleans() -> None:
    with pytest.raises(ValidationError):
        ResourceQuantity(dimension="cpu_cores", unit="count", value="1")

    quantity = ResourceQuantity(dimension="network_access", unit="boolean", value=True)
    assert quantity.value is True
    assert json.loads(quantity.to_canonical_json())["value"] is True


def test_resource_quantity_has_stable_canonical_identity() -> None:
    first = ResourceQuantity(dimension="cpu_cores", unit="count", value=8)
    second = ResourceQuantity.model_validate(first.model_dump())

    assert first.to_canonical_json() == second.to_canonical_json()
    assert first.fingerprint == second.fingerprint


def test_resource_snapshot_sorts_declared_resources_targets_and_reasons() -> None:
    snapshot = _snapshot(
        unavailable_reasons={
            "target.z": "Not configured.",
            "target.a": "Not enabled.",
        }
    )

    assert [(item.dimension, item.unit) for item in snapshot.available_resources] == [
        ("cpu_cores", "count"),
        ("memory_mib", "mib"),
    ]
    assert snapshot.available_execution_targets == ("target.one", "target.two")
    assert tuple(snapshot.unavailable_reasons) == ("target.a", "target.z")


def test_resource_snapshot_rejects_duplicate_dimension_and_unit_pair() -> None:
    duplicate = ResourceQuantity(dimension="cpu_cores", unit="count", value=16)
    with pytest.raises(ValidationError, match="pairs must be unique"):
        _snapshot(
            available_resources=(
                duplicate,
                ResourceQuantity(dimension="cpu_cores", unit="count", value=8),
            )
        )


def test_resource_snapshot_rejects_naive_observation_time() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        _snapshot(observed_at=datetime(2026, 1, 1))


def test_resource_snapshot_rejects_target_conflicts_and_unsafe_reasons() -> None:
    with pytest.raises(ValidationError, match="both available and unavailable"):
        _snapshot(unavailable_reasons={"target.one": "Unavailable."})
    with pytest.raises(ValidationError, match="cannot be blank"):
        _snapshot(unavailable_reasons={"target.three": "  "})
    with pytest.raises(ValidationError, match="Sensitive metadata key"):
        _snapshot(unavailable_reasons={"target.token": "Not configured."})


def test_resource_snapshot_rejects_normalized_unavailable_target_collisions() -> None:
    with pytest.raises(ValidationError, match="must be unique after normalization"):
        _snapshot(
            unavailable_reasons={
                "target.same": "First declaration.",
                " target.same ": "Conflicting declaration.",
            }
        )


def test_resource_lookup_is_safe_and_does_not_mutate_snapshot() -> None:
    snapshot = _snapshot()
    original_fingerprint = snapshot.fingerprint

    found = snapshot.lookup("cpu_cores", "count")

    assert found == ResourceQuantity(dimension="cpu_cores", unit="count", value=4)
    assert snapshot.lookup("gpu_count", "count") is None
    assert snapshot.fingerprint == original_fingerprint
    with pytest.raises(ValidationError):
        found.value = 5  # type: ignore[misc]
