"""Output verifier contract."""

from typing import Any, Protocol, runtime_checkable

from .verification_result import VerificationResult


@runtime_checkable
class Verifier(Protocol):
    """Contract implemented by output verifiers."""

    @property
    def verifier_id(self) -> str:
        """Return the verifier identifier."""
        ...

    def verify(self, output: Any) -> VerificationResult:
        """Verify one output."""
        ...
