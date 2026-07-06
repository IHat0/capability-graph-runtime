"""Verification outcome states."""

from enum import Enum


class VerificationStatus(str, Enum):
    """Possible outcomes from verifying a plugin output."""

    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
