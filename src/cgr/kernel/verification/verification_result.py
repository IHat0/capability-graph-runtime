"""Structured output verification results."""

from pydantic import BaseModel, ConfigDict, Field

from .verification_status import VerificationStatus


class VerificationResult(BaseModel):
    """Immutable result produced by an output verifier."""

    model_config = ConfigDict(frozen=True)

    status: VerificationStatus
    verifier_id: str
    message: str = ""
    details: dict[str, str] = Field(default_factory=dict)

    @property
    def passed(self) -> bool:
        """Return whether verification passed."""
        return self.status == VerificationStatus.PASSED

    @property
    def failed(self) -> bool:
        """Return whether verification failed."""
        return self.status == VerificationStatus.FAILED

    @property
    def skipped(self) -> bool:
        """Return whether verification was skipped."""
        return self.status == VerificationStatus.SKIPPED
