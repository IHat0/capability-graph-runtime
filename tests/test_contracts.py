from typing import TypedDict

import pytest
from pydantic import ValidationError

from cgr.kernel.contracts import (
    Capability,
    CapabilityVersion,
    ExecutionContext,
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
    HealthStatus,
    PluginMetadata,
    PluginState,
)


class ExamplePayload(TypedDict):
    message: str


@pytest.fixture
def capability() -> Capability:
    return Capability(
        id="echo",
        name="Echo",
        version=CapabilityVersion(major=1, minor=2, patch=3),
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1.2.3", CapabilityVersion(major=1, minor=2, patch=3)),
        (" 10.0.7 ", CapabilityVersion(major=10, minor=0, patch=7)),
    ],
)
def test_capability_version_parsing(
    raw: str,
    expected: CapabilityVersion,
) -> None:
    assert CapabilityVersion.parse(raw) == expected


@pytest.mark.parametrize("raw", ["1.2", "1.2.3.4", "one.2.3", "1.-2.3"])
def test_capability_version_rejects_invalid_values(raw: str) -> None:
    with pytest.raises((ValueError, ValidationError)):
        CapabilityVersion.parse(raw)


def test_capability_version_ordering_equality_and_string_conversion() -> None:
    first = CapabilityVersion(major=1, minor=2, patch=3)
    equal = CapabilityVersion(major=1, minor=2, patch=3)
    later_patch = CapabilityVersion(major=1, minor=2, patch=4)
    later_minor = CapabilityVersion(major=1, minor=3, patch=0)
    later_major = CapabilityVersion(major=2, minor=0, patch=0)

    assert first == equal
    assert first != later_patch
    assert first < later_patch < later_minor < later_major
    assert later_major > first
    assert str(first) == "1.2.3"


def test_capability_qualified_name(capability: Capability) -> None:
    assert capability.qualified_name == "echo@1.2.3"


def test_execution_context_is_immutable() -> None:
    context = ExecutionContext(execution_id="execution-1")

    with pytest.raises(ValidationError):
        context.execution_id = "execution-2"


def test_execution_context_with_metadata_returns_enriched_copy() -> None:
    context = ExecutionContext(
        execution_id="execution-1",
        metadata={"source": "test"},
    )

    enriched = context.with_metadata("trace", "enabled")

    assert enriched is not context
    assert context.metadata == {"source": "test"}
    assert enriched.metadata == {"source": "test", "trace": "enabled"}
    assert enriched.execution_id == context.execution_id


def test_execution_request_preserves_typed_payload(
    capability: Capability,
) -> None:
    payload: ExamplePayload = {"message": "Hello CGR!"}
    request = ExecutionRequest[ExamplePayload](
        capability=capability,
        context=ExecutionContext(),
        payload=payload,
    )

    assert request.payload == payload
    assert request.payload["message"] == "Hello CGR!"


@pytest.mark.parametrize(
    ("status", "succeeded", "failed"),
    [
        (ExecutionStatus.SUCCESS, True, False),
        (ExecutionStatus.FAILED, False, True),
        (ExecutionStatus.RUNNING, False, False),
    ],
)
def test_execution_result_status_helpers(
    status: ExecutionStatus,
    succeeded: bool,
    failed: bool,
) -> None:
    result = ExecutionResult[dict[str, str]](
        context=ExecutionContext(),
        status=status,
        output={},
    )

    assert result.succeeded is succeeded
    assert result.failed is failed


def test_enum_values() -> None:
    assert [status.value for status in ExecutionStatus] == [
        "pending",
        "running",
        "success",
        "failed",
        "cancelled",
        "timeout",
    ]
    assert [state.value for state in PluginState] == [
        "discovered",
        "registered",
        "initialized",
        "running",
        "stopped",
        "failed",
    ]
    assert [status.value for status in HealthStatus] == [
        "healthy",
        "degraded",
        "unavailable",
    ]


def test_plugin_metadata_supports(capability: Capability) -> None:
    metadata = PluginMetadata(
        id="example",
        name="Example Plugin",
        version="1.0.0",
        capabilities=[capability],
    )

    assert metadata.supports("echo")
    assert not metadata.supports("reasoning")
