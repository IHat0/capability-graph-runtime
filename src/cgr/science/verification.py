"""Domain-neutral scientific verification contracts."""

from __future__ import annotations

from enum import Enum
from typing import Self

from pydantic import Field, field_validator, model_validator

from cgr.kernel.contracts import CapabilityVersion

from .artifacts import ArtifactPointer
from .canonical import CanonicalModel, JsonScalar, validate_identifier


class ScientificVerificationOutcome(str, Enum):
    """Supported scientific verification outcomes."""

    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"
    INCONCLUSIVE = "inconclusive"


class FindingSeverity(str, Enum):
    """Machine-readable finding severity."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class VerificationFinding(CanonicalModel):
    """One stable machine-readable verifier finding."""

    code: str
    severity: FindingSeverity
    message: str = Field(min_length=1, max_length=2048)
    location: str | None = Field(default=None, max_length=512)
    expected: JsonScalar = None
    observed: JsonScalar = None
    blocking: bool = False

    @field_validator("code")
    @classmethod
    def validate_code(cls, value: str) -> str:
        return validate_identifier(value, label="finding code")


class ScientificVerificationResult(CanonicalModel):
    """Immutable verification result tied to an exact artifact."""

    verifier_identifier: str
    verifier_version: CapabilityVersion
    subject: ArtifactPointer
    outcome: ScientificVerificationOutcome
    findings: tuple[VerificationFinding, ...] = ()
    summary: str = Field(min_length=1, max_length=4096)
    evidence: tuple[ArtifactPointer, ...] = ()

    @field_validator("verifier_identifier")
    @classmethod
    def validate_verifier_identifier(cls, value: str) -> str:
        return validate_identifier(value, label="verifier identifier")

    @field_validator("findings")
    @classmethod
    def order_findings(
        cls, value: tuple[VerificationFinding, ...]
    ) -> tuple[VerificationFinding, ...]:
        return tuple(sorted(value, key=lambda finding: (finding.code, finding.fingerprint)))

    @field_validator("evidence")
    @classmethod
    def order_evidence(
        cls, value: tuple[ArtifactPointer, ...]
    ) -> tuple[ArtifactPointer, ...]:
        return tuple(sorted(value, key=lambda item: (item.artifact_identifier, item.content_sha256)))

    @model_validator(mode="after")
    def validate_outcome(self) -> Self:
        blocking = any(finding.blocking for finding in self.findings)
        if blocking and self.outcome == ScientificVerificationOutcome.PASSED:
            raise ValueError("A passed verification cannot contain a blocking finding.")
        if self.outcome == ScientificVerificationOutcome.FAILED and not self.findings:
            raise ValueError("A failed verification must include at least one finding.")
        return self

    @property
    def passed(self) -> bool:
        return self.outcome == ScientificVerificationOutcome.PASSED

    @property
    def has_blocking_failure(self) -> bool:
        return self.outcome != ScientificVerificationOutcome.PASSED and any(
            finding.blocking for finding in self.findings
        )
