"""Simple dictionary key schema verification."""

from typing import Any

from .verification_result import VerificationResult
from .verification_status import VerificationStatus


class SchemaVerifier:
    """Verify that dictionary outputs contain a required set of keys."""

    def __init__(self, verifier_id: str, required_keys: set[str]) -> None:
        self._verifier_id = verifier_id
        self._required_keys = set(required_keys)

    @property
    def verifier_id(self) -> str:
        """Return the verifier identifier."""
        return self._verifier_id

    def verify(self, output: Any) -> VerificationResult:
        """Verify the output is a dictionary containing all required keys."""
        if not isinstance(output, dict):
            return VerificationResult(
                status=VerificationStatus.FAILED,
                verifier_id=self.verifier_id,
                message="Output is not a dictionary.",
            )

        missing_keys = sorted(self._required_keys.difference(output))
        if missing_keys:
            return VerificationResult(
                status=VerificationStatus.FAILED,
                verifier_id=self.verifier_id,
                message="Output is missing required keys.",
                details={"missing_keys": ",".join(missing_keys)},
            )

        return VerificationResult(
            status=VerificationStatus.PASSED,
            verifier_id=self.verifier_id,
            message="Output satisfies required schema.",
        )
