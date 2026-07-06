from typing import Any

import pytest
from pydantic import ValidationError

from cgr.kernel.verification import (
    SchemaVerifier,
    VerificationResult,
    VerificationStatus,
    Verifier,
)


def test_verification_status_values() -> None:
    assert [status.value for status in VerificationStatus] == [
        "passed",
        "failed",
        "skipped",
    ]


def test_verification_result_is_immutable() -> None:
    result = VerificationResult(
        status=VerificationStatus.PASSED,
        verifier_id="schema",
    )

    with pytest.raises(ValidationError):
        result.status = VerificationStatus.FAILED


@pytest.mark.parametrize(
    ("status", "passed", "failed", "skipped"),
    [
        (VerificationStatus.PASSED, True, False, False),
        (VerificationStatus.FAILED, False, True, False),
        (VerificationStatus.SKIPPED, False, False, True),
    ],
)
def test_verification_result_status_properties(
    status: VerificationStatus,
    passed: bool,
    failed: bool,
    skipped: bool,
) -> None:
    result = VerificationResult(status=status, verifier_id="schema")

    assert result.passed is passed
    assert result.failed is failed
    assert result.skipped is skipped


def test_schema_verifier_satisfies_verifier_protocol() -> None:
    verifier = SchemaVerifier("schema", {"message"})

    assert isinstance(verifier, Verifier)


def test_schema_verifier_passes_when_required_keys_exist() -> None:
    result = SchemaVerifier("schema", {"message", "code"}).verify(
        {"message": "done", "code": 200, "extra": True}
    )

    assert result.status == VerificationStatus.PASSED
    assert result.message == "Output satisfies required schema."
    assert result.details == {}


@pytest.mark.parametrize("output", ["not a dict", ["message"], None])
def test_schema_verifier_fails_when_output_is_not_dictionary(output: Any) -> None:
    result = SchemaVerifier("schema", {"message"}).verify(output)

    assert result.status == VerificationStatus.FAILED
    assert result.message == "Output is not a dictionary."


def test_schema_verifier_reports_sorted_missing_keys() -> None:
    result = SchemaVerifier("schema", {"zeta", "alpha", "middle"}).verify(
        {"middle": "present"}
    )

    assert result.status == VerificationStatus.FAILED
    assert result.message == "Output is missing required keys."
    assert result.details == {"missing_keys": "alpha,zeta"}


def test_schema_verifier_does_not_mutate_output() -> None:
    output = {"message": "unchanged"}
    original = dict(output)

    SchemaVerifier("schema", {"message", "missing"}).verify(output)

    assert output == original


def test_schema_verifier_with_no_required_keys_passes_any_dictionary() -> None:
    result = SchemaVerifier("schema", set()).verify({})

    assert result.passed is True
