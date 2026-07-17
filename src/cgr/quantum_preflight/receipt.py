"""Fail-closed authorization receipts for one execution and its scientific outcome."""

from __future__ import annotations

from typing import Any, Self

from pydantic import field_validator, model_validator

from cgr.science import ArtifactPointer, CanonicalModel, ScientificVerificationResult
from cgr.science.canonical import validate_identifier, validate_sha256

from .artifacts import artifact_reference
from .identities import AuthorizedScientificOutcome, inspect_result_artifact
from .verification import blocking_findings

RECEIPT_SCHEMA = "cgr.quantum-preflight-receipt/2.0.0"


class QuantumPreflightReceipt(CanonicalModel):
    """Run-specific receipt carrying separately recomputable stable identities."""

    schema_version: str = RECEIPT_SCHEMA
    execution_identifier: str
    experiment: ArtifactPointer
    artifacts: tuple[ArtifactPointer, ...]
    verification_results: tuple[ScientificVerificationResult, ...]
    lineage: ArtifactPointer
    compatibility_warnings: ArtifactPointer
    compatibility_status: str
    exact_scientific_result_sha256: str
    vqe_scientific_result_sha256: str
    scientific_outcome: AuthorizedScientificOutcome
    scientific_outcome_sha256: str
    execution_completed: bool
    scientific_verification_passed: bool
    artifact_lineage_passed: bool
    authorized: bool
    authorization_policy: str

    @field_validator("schema_version")
    @classmethod
    def valid_schema(cls, value: str) -> str:
        if value != RECEIPT_SCHEMA:
            raise ValueError("Unsupported hardened receipt schema.")
        return value

    @field_validator("execution_identifier", "authorization_policy", "compatibility_status")
    @classmethod
    def valid_identifiers(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator(
        "exact_scientific_result_sha256",
        "vqe_scientific_result_sha256",
        "scientific_outcome_sha256",
    )
    @classmethod
    def valid_hashes(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("artifacts")
    @classmethod
    def unique_artifacts(cls, value: tuple[ArtifactPointer, ...]) -> tuple[ArtifactPointer, ...]:
        if len(value) != len(set(value)):
            raise ValueError("Receipt artifact pointers must be unique.")
        return tuple(sorted(value, key=lambda item: (item.artifact_identifier, item.content_sha256)))

    @model_validator(mode="after")
    def fail_closed(self) -> Self:
        if self.scientific_outcome_sha256 != self.scientific_outcome.fingerprint:
            raise ValueError("Scientific-outcome SHA-256 does not match its canonical projection.")
        if self.exact_scientific_result_sha256 != self.scientific_outcome.exact_scientific_result_sha256:
            raise ValueError("Exact scientific-result identity disagrees with the outcome.")
        if self.vqe_scientific_result_sha256 != self.scientific_outcome.vqe_scientific_result_sha256:
            raise ValueError("VQE scientific-result identity disagrees with the outcome.")
        if self.compatibility_status != self.scientific_outcome.compatibility_status:
            raise ValueError("Compatibility status disagrees with the scientific outcome.")
        expected = (
            self.execution_completed
            and self.scientific_verification_passed
            and self.artifact_lineage_passed
            and not blocking_findings(self.verification_results)
            and self.compatibility_status != "blocking"
            and self.scientific_outcome.authorization_decision
        )
        if self.authorized != expected:
            raise ValueError("Receipt authorization does not match blocking evidence.")
        return self


class ReceiptInspection(CanonicalModel):
    hardened: bool
    legacy: bool
    reason: str | None = None
    receipt: QuantumPreflightReceipt | None = None


def inspect_receipt(value: dict[str, Any]) -> ReceiptInspection:
    """Permit historical inspection without silently granting hardened authorization."""
    if value.get("schema_version") != RECEIPT_SCHEMA:
        return ReceiptInspection(
            hardened=False,
            legacy=True,
            reason="legacy_receipt_missing_recomputed_scientific_outcome",
        )
    return ReceiptInspection(
        hardened=True,
        legacy=False,
        receipt=QuantumPreflightReceipt.model_validate(value),
    )


def assemble_receipt(
    *,
    execution_identifier: str,
    experiment: ArtifactPointer,
    artifacts: tuple[ArtifactPointer, ...],
    verification_results: tuple[ScientificVerificationResult, ...],
    lineage: ArtifactPointer,
    compatibility_warnings: ArtifactPointer,
    scientific_outcome: AuthorizedScientificOutcome,
    execution_completed: bool,
) -> QuantumPreflightReceipt:
    verification_passed = not blocking_findings(verification_results)
    lineage_results = [
        result for result in verification_results if result.verifier_identifier == "quantum.lineage"
    ]
    lineage_passed = bool(lineage_results) and all(result.passed for result in lineage_results)
    authorized = (
        execution_completed
        and verification_passed
        and lineage_passed
        and scientific_outcome.authorization_decision
        and scientific_outcome.compatibility_status != "blocking"
    )
    return QuantumPreflightReceipt(
        execution_identifier=execution_identifier,
        experiment=experiment,
        artifacts=artifacts,
        verification_results=verification_results,
        lineage=lineage,
        compatibility_warnings=compatibility_warnings,
        compatibility_status=scientific_outcome.compatibility_status,
        exact_scientific_result_sha256=scientific_outcome.exact_scientific_result_sha256,
        vqe_scientific_result_sha256=scientific_outcome.vqe_scientific_result_sha256,
        scientific_outcome=scientific_outcome,
        scientific_outcome_sha256=scientific_outcome.fingerprint,
        execution_completed=execution_completed,
        scientific_verification_passed=verification_passed,
        artifact_lineage_passed=lineage_passed,
        authorized=authorized,
        authorization_policy="all_blocking_verifiers_must_pass",
    )


def verify_receipt_identities(
    receipt: QuantumPreflightReceipt,
    *,
    exact_result: dict[str, Any],
    vqe_result: dict[str, Any],
    exact_result_pointer: ArtifactPointer,
    vqe_result_pointer: ArtifactPointer,
    expected_outcome: AuthorizedScientificOutcome,
) -> tuple[str, ...]:
    """Recompute cross-artifact identity links; return stable blocking finding codes."""
    failures: list[str] = []
    try:
        exact = inspect_result_artifact(exact_result)
    except (TypeError, ValueError):
        exact = None
        failures.append("receipt.exact_scientific_identity_invalid")
    try:
        vqe = inspect_result_artifact(vqe_result)
    except (TypeError, ValueError):
        vqe = None
        failures.append("receipt.vqe_scientific_identity_invalid")
    if exact is None or not exact.hardened or exact.artifact is None:
        failures.append("receipt.legacy_exact_result")
    if vqe is None or not vqe.hardened or vqe.artifact is None:
        failures.append("receipt.legacy_vqe_result")
    pointers = set(receipt.artifacts)
    if exact_result_pointer not in pointers or vqe_result_pointer not in pointers:
        failures.append("receipt.run_specific_artifact_substitution")
    recomputed_exact = artifact_reference(
        "exact_result",
        "exact_ground_state_result",
        exact_result,
        filename="exact-result.json",
    ).pointer
    recomputed_vqe = artifact_reference(
        "vqe_result",
        "vqe_ground_state_result",
        vqe_result,
        filename="vqe-result.json",
    ).pointer
    if recomputed_exact != exact_result_pointer:
        failures.append("receipt.exact_result_content_mismatch")
    if recomputed_vqe != vqe_result_pointer:
        failures.append("receipt.vqe_result_content_mismatch")
    if exact is not None and exact.artifact is not None and (
        exact.artifact.scientific_result_sha256 != receipt.exact_scientific_result_sha256
    ):
        failures.append("receipt.exact_scientific_identity_mismatch")
    if vqe is not None and vqe.artifact is not None and (
        vqe.artifact.scientific_result_sha256 != receipt.vqe_scientific_result_sha256
    ):
        failures.append("receipt.vqe_scientific_identity_mismatch")
    if receipt.scientific_outcome_sha256 != expected_outcome.fingerprint:
        failures.append("receipt.scientific_outcome_mismatch")
    return tuple(sorted(set(failures)))
