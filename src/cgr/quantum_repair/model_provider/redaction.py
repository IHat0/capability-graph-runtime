"""Secret and trusted-answer redaction for provider evidence."""

from __future__ import annotations

import re
from pathlib import Path

_AUTHORIZATION = re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s\"']+")
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)((?:api[_-]?key|password|secret|credential)\s*[=:]\s*)[^\s,;\"']+"
)
_TRUSTED_ANSWER = re.compile(
    r"(?i)(?:(?:trusted[_ -](?:exact[_ -])?"
    r"(?:energy|hamiltonian|result|reference))|"
    r"scientific[_ -](?:outcome|result)[_ -]sha256|"
    r"(?:exact|vqe)[_ -]energy)\s*[=:]\s*(?:-?\d|[0-9a-f]{64})"
)
_TRUSTED_PATH = re.compile(
    r"(?i)(?:^|[/\\])trusted(?:-reference|-evidence)?(?:[/\\]|$)"
)
_WINDOWS_HOST_PATH = re.compile(r"(?i)\b[A-Z]:\\[^\s\"']+")
_POSIX_HOME_PATH = re.compile(r"(?<![A-Za-z0-9_])/(?:home/[^/\s]+|root)/[^\s\"']*")


class RedactionError(ValueError):
    pass


def sanitize_text(value: str, secrets: tuple[str, ...] = ()) -> str:
    sanitized = value
    for secret in secrets:
        if secret:
            sanitized = sanitized.replace(secret, "[REDACTED]")
    sanitized = _AUTHORIZATION.sub("[REDACTED]", sanitized)
    sanitized = _SECRET_ASSIGNMENT.sub(r"\1[REDACTED]", sanitized)
    sanitized = _WINDOWS_HOST_PATH.sub("[HOST_PATH]", sanitized)
    sanitized = _POSIX_HOME_PATH.sub("[HOST_PATH]", sanitized)
    return sanitized


def assert_no_secret(value: str, secrets: tuple[str, ...] = ()) -> None:
    if any(secret and secret in value for secret in secrets):
        raise RedactionError("Sensitive value remained after provider redaction.")
    if _AUTHORIZATION.search(value):
        raise RedactionError("Authorization header remained after provider redaction.")


def assert_prompt_safe(value: str, secrets: tuple[str, ...] = ()) -> None:
    assert_no_secret(value, secrets)
    if _TRUSTED_ANSWER.search(value) or _TRUSTED_PATH.search(value):
        raise RedactionError("Provider prompt contains trusted-answer material.")


def sanitize_artifact(
    source: Path, destination: Path, secrets: tuple[str, ...]
) -> bytes:
    try:
        raw = source.read_bytes()
        text = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise RedactionError(
            "Raw trajectory is not redaction-safe UTF-8 text."
        ) from exc
    sanitized = sanitize_text(text, secrets)
    assert_no_secret(sanitized, secrets)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(sanitized, encoding="utf-8", newline="\n")
    return destination.read_bytes()
