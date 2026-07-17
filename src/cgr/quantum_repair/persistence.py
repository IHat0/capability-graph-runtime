"""Crash-safe source snapshots and immutable repair evidence persistence."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import uuid
from pathlib import Path
from typing import Any

from cgr.quantum_preflight.artifacts import write_json_atomic

from .contracts import SourceEntry, SourceManifest, sealed_values


class RepairPersistenceError(ValueError):
    """Raised when repair evidence cannot be persisted or verified safely."""


def create_source_manifest(root: Path, source_identifier: str) -> SourceManifest:
    resolved_root = root.resolve(strict=True)
    entries: list[SourceEntry] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise RepairPersistenceError(f"Symbolic links are prohibited: {relative}")
        if stat.S_ISDIR(metadata.st_mode):
            continue
        if not stat.S_ISREG(metadata.st_mode):
            raise RepairPersistenceError(
                f"Special source file is prohibited: {relative}"
            )
        if metadata.st_nlink > 1:
            raise RepairPersistenceError(
                f"Hard-linked source file is prohibited: {relative}"
            )
        resolved = path.resolve(strict=True)
        if resolved_root not in resolved.parents:
            raise RepairPersistenceError(f"Source path escapes workspace: {relative}")
        data = path.read_bytes()
        entries.append(
            SourceEntry(
                relative_path=relative,
                content_sha256=hashlib.sha256(data).hexdigest(),
                byte_size=len(data),
                file_mode=stat.S_IMODE(metadata.st_mode),
                executable=bool(metadata.st_mode & stat.S_IXUSR),
            )
        )
    values: dict[str, Any] = {
        "source_identifier": source_identifier,
        "entries": tuple(entries),
        "total_bytes": sum(item.byte_size for item in entries),
    }
    return SourceManifest.model_validate(
        sealed_values(values, "source_manifest_sha256")
    )


def verify_source_manifest(root: Path, expected: SourceManifest) -> None:
    observed = create_source_manifest(root, expected.source_identifier)
    if observed != expected:
        raise RepairPersistenceError(
            "Source workspace differs from its immutable manifest."
        )


def copy_source_tree(source: Path, destination: Path) -> None:
    if destination.exists():
        raise RepairPersistenceError("Fresh repair workspace already exists.")
    source_manifest = create_source_manifest(source, "copy-preflight")
    destination.mkdir(parents=True)
    for entry in source_manifest.entries:
        source_path = source / entry.relative_path
        target = destination / entry.relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source_path.read_bytes())
        target.chmod(entry.file_mode)


def atomic_directory(parent: Path, final_name: str) -> tuple[Path, Path]:
    parent.mkdir(parents=True, exist_ok=True)
    final = parent / final_name
    if final.exists():
        raise RepairPersistenceError(f"Evidence directory already exists: {final_name}")
    temporary = parent / f".{final_name}.tmp-{uuid.uuid4().hex}"
    temporary.mkdir()
    return temporary, final


def finalize_directory(temporary: Path, final: Path) -> None:
    if final.exists() or not temporary.is_dir():
        raise RepairPersistenceError(
            "Atomic evidence finalization preconditions failed."
        )
    os.replace(temporary, final)


def discard_temporary_directory(temporary: Path) -> None:
    if (
        temporary.name.startswith(".")
        and ".tmp-" in temporary.name
        and temporary.is_dir()
    ):
        shutil.rmtree(temporary)


def write_evidence(
    path: Path, value: Any, maximum_bytes: int = 4 * 1024 * 1024
) -> None:
    payload = value.model_dump(mode="json") if hasattr(value, "model_dump") else value
    write_json_atomic(path, payload, maximum_bytes=maximum_bytes)


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RepairPersistenceError(
            f"Repair evidence is unreadable: {path.name}"
        ) from exc
