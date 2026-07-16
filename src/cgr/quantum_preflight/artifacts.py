"""Atomic content-addressed JSON evidence built on cgr.science artifacts."""

from __future__ import annotations

import os
import hashlib
from pathlib import Path
from typing import Any

from cgr.kernel.contracts import CapabilityVersion
from cgr.science import ArtifactPointer, ArtifactReference, CreationProvenance
from cgr.science.canonical import canonical_json, validate_identifier

SCHEMA_VERSION = CapabilityVersion(major=1, minor=0, patch=0)


def artifact_document(artifact_type: str, payload: Any) -> dict[str, Any]:
    validate_identifier(artifact_type, label="artifact type")
    return {
        "artifact_schema": "cgr.quantum-preflight-artifact/1.0.0",
        "artifact_type": artifact_type,
        "payload": payload,
    }


def artifact_reference(
    artifact_identifier: str,
    artifact_type: str,
    payload: Any,
    *,
    filename: str,
    parents: tuple[ArtifactPointer, ...] = (),
) -> ArtifactReference:
    document = artifact_document(artifact_type, payload)
    encoded = (canonical_json(document) + "\n").encode("utf-8")
    return ArtifactReference(
        artifact_identifier=artifact_identifier,
        schema_version=SCHEMA_VERSION,
        artifact_type=artifact_type,
        media_type="application/json",
        content_sha256=hashlib.sha256(encoded).hexdigest(),
        byte_size=len(encoded),
        storage_location=filename,
        provenance=CreationProvenance(
            producer="cgr.quantum_preflight", producer_version=SCHEMA_VERSION
        ),
        parents=parents,
    )


def write_json_atomic(path: Path, value: Any, *, maximum_bytes: int) -> None:
    """Write canonical JSON by fsync and same-directory atomic replacement."""
    data = (canonical_json(value) + "\n").encode("utf-8")
    if len(data) > maximum_bytes:
        raise ValueError(f"Evidence exceeds the {maximum_bytes}-byte policy.")
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def verify_artifact_bytes(path: Path, reference: ArtifactReference) -> bool:
    """Detect evidence substitution after verification."""
    try:
        import json

        data = path.read_bytes()
        document = json.loads(data.decode("utf-8"))
    except (OSError, ValueError):
        return False
    return (
        document.get("artifact_type") == reference.artifact_type
        and hashlib.sha256(data).hexdigest() == reference.content_sha256
        and len(data) == reference.byte_size
    )
