"""Deterministic identity helpers for scientific contracts."""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, TypeAlias
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict

JsonScalar: TypeAlias = str | int | float | bool | None
BoundedMetadata: TypeAlias = dict[str, JsonScalar]

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SENSITIVE_KEY = re.compile(
    r"(?:^|[._-])(api[_-]?key|password|secret|token|credential)(?:$|[._-])",
    re.IGNORECASE,
)
_WINDOWS_ABSOLUTE_PATH = re.compile(r"^[A-Za-z]:[\\/]")


def canonical_json(value: Any) -> str:
    """Serialize JSON-compatible data deterministically as UTF-8 text."""
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def sha256_fingerprint(value: Any) -> str:
    """Return the SHA-256 of a canonical JSON identity."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def validate_sha256(value: str) -> str:
    """Validate and return a lowercase SHA-256 hexadecimal digest."""
    if not _SHA256.fullmatch(value):
        raise ValueError("SHA-256 fingerprints must be 64 lowercase hexadecimal characters.")
    return value


def validate_identifier(value: str, *, label: str = "identifier") -> str:
    """Validate an extensible stable identifier."""
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{label} must be a stable identifier without whitespace.")
    return value


def validate_storage_location(value: str | None) -> str | None:
    """Reject local absolute paths from portable scientific references."""
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        raise ValueError("Storage locations cannot be empty.")
    parsed = urlparse(stripped)
    if parsed.scheme and len(parsed.scheme) > 1:
        return stripped
    if (
        stripped.startswith(("/", "\\\\"))
        or _WINDOWS_ABSOLUTE_PATH.match(stripped)
    ):
        raise ValueError("Local absolute paths are not portable scientific identities.")
    return stripped


def validate_bounded_metadata(
    value: BoundedMetadata,
    *,
    max_entries: int = 32,
    max_string_length: int = 1024,
) -> BoundedMetadata:
    """Validate bounded scalar metadata and reject secret-bearing keys."""
    if len(value) > max_entries:
        raise ValueError(f"Metadata is limited to {max_entries} entries.")
    normalized: BoundedMetadata = {}
    for key, item in value.items():
        validate_identifier(key, label="metadata key")
        if _SENSITIVE_KEY.search(key):
            raise ValueError(f"Sensitive metadata key '{key}' is prohibited.")
        if isinstance(item, str):
            if len(item) > max_string_length:
                raise ValueError("Metadata string values are too long.")
            validate_storage_location(item)
        elif isinstance(item, float) and not math.isfinite(item):
            raise ValueError("Metadata numbers must be finite.")
        normalized[key] = item
    return normalized


class CanonicalModel(BaseModel):
    """Frozen model with deterministic canonical identity and fingerprinting."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    def canonical_identity(self) -> Any:
        """Return the semantic data included in this object's fingerprint."""
        return self.model_dump(mode="json")

    def to_canonical_json(self) -> str:
        """Return deterministic canonical JSON for this object's identity."""
        return canonical_json(self.canonical_identity())

    @property
    def fingerprint(self) -> str:
        """Return the SHA-256 fingerprint of the canonical identity."""
        return sha256_fingerprint(self.canonical_identity())
