"""Fail-closed authorization receipt assembled only from verified artifacts."""

from __future__ import annotations

from typing import Self

from pydantic import field_validator, model_validator

from cgr.science import ArtifactPointer, CanonicalModel, ScientificVerificationResult
from cgr.science.canonical import validate_identifier

from .verification import blocking_findings


class QuantumPreflightReceipt(CanonicalModel):
    schema_version: str
    experiment: ArtifactPointer
    artifacts: tuple[ArtifactPointer, ...]
    verification_results: tuple[ScientificVerificationResult, ...]
    lineage: ArtifactPointer
    execution_completed: bool
    scientific_verification_passed: bool
    artifact_lineage_passed: bool
    authorized: bool
    authorization_policy: str

    @field_validator("schema_version")
    @classmethod
    def valid_schema(cls, value: str) -> str:
        if value != "cgr.quantum-preflight-receipt/1.0.0":
            raise ValueError("Unsupported receipt schema.")
        return value

    @field_validator("authorization_policy")
    @classmethod
    def valid_policy(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("artifacts")
    @classmethod
    def unique_artifacts(cls, value: tuple[ArtifactPointer, ...]) -> tuple[ArtifactPointer, ...]:
        if len(value) != len(set(value)):
            raise ValueError("Receipt artifact pointers must be unique.")
        return tuple(sorted(value, key=lambda item: (item.artifact_identifier, item.content_sha256)))

    @model_validator(mode="after")
    def fail_closed(self) -> Self:
        expected = (
            self.execution_completed
            and self.scientific_verification_passed
            and self.artifact_lineage_passed
            and not blocking_findings(self.verification_results)
        )
        if self.authorized != expected:
            raise ValueError("Receipt authorization does not match blocking evidence.")
        return self


def assemble_receipt(
    *,
    experiment: ArtifactPointer,
    artifacts: tuple[ArtifactPointer, ...],
    verification_results: tuple[ScientificVerificationResult, ...],
    lineage: ArtifactPointer,
    execution_completed: bool,
) -> QuantumPreflightReceipt:
    verification_passed = not blocking_findings(verification_results)
    lineage_results = [
        result for result in verification_results if result.verifier_identifier == "quantum.lineage"
    ]
    lineage_passed = bool(lineage_results) and all(result.passed for result in lineage_results)
    authorized = execution_completed and verification_passed and lineage_passed
    return QuantumPreflightReceipt(
        schema_version="cgr.quantum-preflight-receipt/1.0.0",
        experiment=experiment,
        artifacts=artifacts,
        verification_results=verification_results,
        lineage=lineage,
        execution_completed=execution_completed,
        scientific_verification_passed=verification_passed,
        artifact_lineage_passed=lineage_passed,
        authorized=authorized,
        authorization_policy="all_blocking_verifiers_must_pass",
    )
