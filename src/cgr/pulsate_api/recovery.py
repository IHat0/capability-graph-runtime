"""Offline, deterministic backup verification and restore for Pulsate state."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import tempfile
import time
from contextlib import AbstractContextManager, closing
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any, Self

from pydantic import Field, field_validator, model_validator

from cgr.molecular import MolecularArtifactRepository, MolecularProjectRepository
from cgr.molecular._publication import _rename_directory_no_replace
from cgr.science import ArtifactReference
from cgr.science.canonical import (
    CanonicalModel,
    canonical_json,
    sha256_fingerprint,
    validate_identifier,
    validate_sha256,
)

from .production_security import (
    AUDIT_GENESIS_FINGERPRINT,
    AUDIT_SCHEMA_VERSION,
    GRANT_SCHEMA_VERSION,
    _audit_fingerprint,
)


RECOVERY_SCHEMA_VERSION = "cgr.pulsate-recovery-manifest/1.0.0"
RECOVERY_RECEIPT_SCHEMA_VERSION = "cgr.pulsate-recovery-receipt/1.0.0"
RECOVERY_MAXIMUM_COMPONENTS = 32
RECOVERY_MAXIMUM_FILES = 100_000
RECOVERY_MAXIMUM_TOTAL_BYTES = 64 * 1024 * 1024 * 1024
RECOVERY_MAXIMUM_PATH_BYTES = 1024
RECOVERY_MAXIMUM_PATH_DEPTH = 32
_BACKUP_IDENTIFIER_PREFIX = "backup-"
_EXCLUDED_FILES = {".coordinator.lock", ".pulsate-recovery.lock"}
_EXCLUDED_SUFFIXES = {"-journal", "-shm", "-wal"}


class RecoveryError(RuntimeError):
    """Controlled, path-free recovery failure."""


class RecoveryFileClassification(str, Enum):
    """Closed safe classification for backed-up bytes."""

    IMMUTABLE_REPOSITORY = "immutable_repository"
    PERSISTED_APPLICATION_STATE = "persisted_application_state"
    SQLITE_SNAPSHOT = "sqlite_snapshot"


class RecoveryComponentRecord(CanonicalModel):
    """One authoritative component in the production persistence inventory."""

    component_identifier: str
    component_type: str
    schema_version: str
    required: bool
    relative_storage_identity: str
    validation_provider: str
    snapshot_method: str
    restore_validation_method: str

    @field_validator(
        "component_identifier",
        "component_type",
        "schema_version",
        "validation_provider",
        "snapshot_method",
        "restore_validation_method",
    )
    @classmethod
    def validate_identities(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("relative_storage_identity")
    @classmethod
    def validate_storage_identity(cls, value: str) -> str:
        return _canonical_relative_path(value)


class RecoveryFileRecord(CanonicalModel):
    """Content identity for one file in a recovery bundle."""

    component_identifier: str
    relative_path: str
    byte_length: int = Field(ge=0, le=RECOVERY_MAXIMUM_TOTAL_BYTES)
    content_sha256: str
    classification: RecoveryFileClassification

    @field_validator("component_identifier")
    @classmethod
    def validate_component_identifier(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        return _canonical_relative_path(value)

    @field_validator("content_sha256")
    @classmethod
    def validate_content_sha256(cls, value: str) -> str:
        return validate_sha256(value)


def recovery_manifest_fingerprint(value: dict[str, Any]) -> str:
    """Fingerprint semantic manifest content without its declared fingerprint."""

    return sha256_fingerprint(
        {key: item for key, item in value.items() if key != "manifest_fingerprint"}
    )


class RecoveryManifest(CanonicalModel):
    """Canonical inventory of one completed offline backup."""

    schema_version: str
    backup_identifier: str
    created_at: datetime
    safe_configuration_fingerprint: str
    application_revision: str | None = None
    consistency_model: str
    components: tuple[RecoveryComponentRecord, ...] = Field(
        min_length=1, max_length=RECOVERY_MAXIMUM_COMPONENTS
    )
    files: tuple[RecoveryFileRecord, ...] = Field(max_length=RECOVERY_MAXIMUM_FILES)
    total_file_count: int = Field(ge=0, le=RECOVERY_MAXIMUM_FILES)
    total_byte_count: int = Field(ge=0, le=RECOVERY_MAXIMUM_TOTAL_BYTES)
    manifest_fingerprint: str

    @field_validator("schema_version")
    @classmethod
    def validate_schema(cls, value: str) -> str:
        if value != RECOVERY_SCHEMA_VERSION:
            raise ValueError("Recovery manifest schema is unsupported.")
        return value

    @field_validator("backup_identifier")
    @classmethod
    def validate_backup_identifier(cls, value: str) -> str:
        return _validate_backup_identifier(value)

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Recovery timestamps must be timezone-aware.")
        return value.astimezone(UTC)

    @field_validator("safe_configuration_fingerprint", "manifest_fingerprint")
    @classmethod
    def validate_fingerprint(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("application_revision")
    @classmethod
    def validate_revision(cls, value: str | None) -> str | None:
        if value is not None and (
            len(value) != 40 or any(character not in "0123456789abcdef" for character in value)
        ):
            raise ValueError("Application revision is invalid.")
        return value

    @field_validator("consistency_model")
    @classmethod
    def validate_consistency_model(cls, value: str) -> str:
        if value != "offline_coordinated":
            raise ValueError("Recovery consistency model is unsupported.")
        return value

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        components = tuple(item.component_identifier for item in self.components)
        if components != tuple(sorted(components)) or len(components) != len(set(components)):
            raise ValueError("Recovery components must be unique and ordered.")
        paths = tuple(item.relative_path for item in self.files)
        if paths != tuple(sorted(paths)) or len(paths) != len(set(paths)):
            raise ValueError("Recovery files must be unique and ordered.")
        declared = set(components)
        if any(item.component_identifier not in declared for item in self.files):
            raise ValueError("Recovery file references an undeclared component.")
        if self.total_file_count != len(self.files):
            raise ValueError("Recovery file count is inconsistent.")
        if self.total_byte_count != sum(item.byte_length for item in self.files):
            raise ValueError("Recovery byte count is inconsistent.")
        expected = recovery_manifest_fingerprint(self.model_dump(mode="json"))
        if self.manifest_fingerprint != expected:
            raise ValueError("Recovery manifest fingerprint is invalid.")
        return self


class RecoveryVerificationResult(CanonicalModel):
    """Safe result of verifying a real bundle."""

    schema_version: str = "cgr.pulsate-recovery-verification/1.0.0"
    backup_identifier: str
    manifest_fingerprint: str
    verified: bool
    component_count: int = Field(ge=0, le=RECOVERY_MAXIMUM_COMPONENTS)
    file_count: int = Field(ge=0, le=RECOVERY_MAXIMUM_FILES)
    total_byte_count: int = Field(ge=0, le=RECOVERY_MAXIMUM_TOTAL_BYTES)

    @field_validator("schema_version")
    @classmethod
    def validate_schema(cls, value: str) -> str:
        if value != "cgr.pulsate-recovery-verification/1.0.0":
            raise ValueError("Recovery verification schema is unsupported.")
        return value

    @field_validator("backup_identifier")
    @classmethod
    def validate_backup_identifier(cls, value: str) -> str:
        return _validate_backup_identifier(value)

    @field_validator("manifest_fingerprint")
    @classmethod
    def validate_manifest_fingerprint(cls, value: str) -> str:
        return validate_sha256(value)


class RecoveryReceipt(CanonicalModel):
    """Safe receipt emitted after a validated restore."""

    schema_version: str = RECOVERY_RECEIPT_SCHEMA_VERSION
    receipt_identifier: str
    backup_identifier: str
    manifest_fingerprint: str
    restored_at: datetime
    target_identity_sha256: str
    component_count: int = Field(ge=0, le=RECOVERY_MAXIMUM_COMPONENTS)
    file_count: int = Field(ge=0, le=RECOVERY_MAXIMUM_FILES)
    receipt_fingerprint: str

    @field_validator("schema_version")
    @classmethod
    def validate_schema(cls, value: str) -> str:
        if value != RECOVERY_RECEIPT_SCHEMA_VERSION:
            raise ValueError("Recovery receipt schema is unsupported.")
        return value

    @field_validator("receipt_identifier")
    @classmethod
    def validate_receipt_identifier(cls, value: str) -> str:
        if not value.startswith("receipt-"):
            raise ValueError("Recovery receipt identifier is invalid.")
        return validate_identifier(value, label="receipt identifier")

    @field_validator("backup_identifier")
    @classmethod
    def validate_backup_identifier(cls, value: str) -> str:
        return _validate_backup_identifier(value)

    @field_validator(
        "manifest_fingerprint", "target_identity_sha256", "receipt_fingerprint"
    )
    @classmethod
    def validate_fingerprint(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("restored_at")
    @classmethod
    def validate_restored_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Recovery timestamps must be timezone-aware.")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_receipt(self) -> Self:
        value = self.model_dump(mode="json")
        expected = sha256_fingerprint(
            {key: item for key, item in value.items() if key != "receipt_fingerprint"}
        )
        if self.receipt_fingerprint != expected:
            raise ValueError("Recovery receipt fingerprint is invalid.")
        return self


def production_persistence_inventory() -> tuple[RecoveryComponentRecord, ...]:
    """Return the authoritative, sorted production data-root inventory."""

    records = (
        RecoveryComponentRecord(
            component_identifier="authorization-grants",
            component_type="sqlite",
            schema_version=f"grants/{GRANT_SCHEMA_VERSION}",
            required=True,
            relative_storage_identity="security/grants.sqlite3",
            validation_provider="sqlite-grant-provider",
            snapshot_method="sqlite-backup-api",
            restore_validation_method="sqlite-grant-schema",
        ),
        RecoveryComponentRecord(
            component_identifier="experiments",
            component_type="directory",
            schema_version="pulsate-experiments/v1",
            required=True,
            relative_storage_identity="experiments",
            validation_provider="experiment-store",
            snapshot_method="deterministic-file-copy",
            restore_validation_method="bounded-json-tree",
        ),
        RecoveryComponentRecord(
            component_identifier="interpretations",
            component_type="directory",
            schema_version="pulsate-interpretations/v1",
            required=True,
            relative_storage_identity="interpretations",
            validation_provider="interpretation-store",
            snapshot_method="deterministic-file-copy",
            restore_validation_method="bounded-json-tree",
        ),
        RecoveryComponentRecord(
            component_identifier="molecular-artifacts",
            component_type="immutable-repository",
            schema_version="molecular-artifacts/v1",
            required=True,
            relative_storage_identity="molecular-artifacts",
            validation_provider="molecular-artifact-repository",
            snapshot_method="deterministic-file-copy",
            restore_validation_method="molecular-artifact-repository",
        ),
        RecoveryComponentRecord(
            component_identifier="molecular-projects",
            component_type="immutable-repository",
            schema_version="molecular-projects/v1",
            required=True,
            relative_storage_identity="molecular-projects",
            validation_provider="molecular-project-repository",
            snapshot_method="deterministic-file-copy",
            restore_validation_method="molecular-project-repository",
        ),
        RecoveryComponentRecord(
            component_identifier="runs",
            component_type="directory",
            schema_version="pulsate-runs/v1",
            required=True,
            relative_storage_identity="runs",
            validation_provider="run-coordinator-storage",
            snapshot_method="deterministic-file-copy",
            restore_validation_method="bounded-json-tree",
        ),
        RecoveryComponentRecord(
            component_identifier="workflows",
            component_type="directory",
            schema_version="pulsate-workflows/v1",
            required=True,
            relative_storage_identity="workflows",
            validation_provider="workflow-graph-repositories",
            snapshot_method="deterministic-file-copy",
            restore_validation_method="bounded-json-tree",
        ),
        RecoveryComponentRecord(
            component_identifier="security-audit",
            component_type="sqlite",
            schema_version=f"audit/{AUDIT_SCHEMA_VERSION}",
            required=True,
            relative_storage_identity="security/audit.sqlite3",
            validation_provider="sqlite-audit-sink",
            snapshot_method="sqlite-backup-api",
            restore_validation_method="sqlite-audit-chain",
        ),
    )
    return tuple(sorted(records, key=lambda item: item.component_identifier))


class RecoveryLock(AbstractContextManager["RecoveryLock"]):
    """Bounded OS-backed exclusive lock beneath one validated recovery root."""

    def __init__(self, root: Path, *, timeout_seconds: float = 5.0) -> None:
        if timeout_seconds < 0 or timeout_seconds > 60:
            raise RecoveryError("Recovery lock timeout is invalid.")
        self._root = _validated_root(root)
        self._path = self._root / ".pulsate-recovery.lock"
        self._timeout = timeout_seconds
        self._handle: Any | None = None

    def acquire(self) -> None:
        handle: Any | None = None
        descriptor: int | None = None
        try:
            try:
                metadata = self._path.lstat()
            except FileNotFoundError:
                metadata = None
            if metadata is not None and (
                _is_link_or_junction(self._path, metadata.st_mode)
                or not stat.S_ISREG(metadata.st_mode)
            ):
                raise OSError
            flags = os.O_RDWR | os.O_CREAT
            no_follow = getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(self._path, flags | no_follow, 0o600)
            handle = os.fdopen(descriptor, "r+b")
            descriptor = None
            opened_metadata = os.fstat(handle.fileno())
            current_metadata = self._path.lstat()
            if (
                not stat.S_ISREG(opened_metadata.st_mode)
                or _is_link_or_junction(self._path, current_metadata.st_mode)
                or not stat.S_ISREG(current_metadata.st_mode)
                or opened_metadata.st_dev != current_metadata.st_dev
                or opened_metadata.st_ino != current_metadata.st_ino
            ):
                raise OSError
            if handle.seek(0, os.SEEK_END) == 0:
                handle.write(b"0")
                handle.flush()
            deadline = time.monotonic() + self._timeout
            while True:
                try:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except (OSError, BlockingIOError):
                    if time.monotonic() >= deadline:
                        handle.close()
                        raise RecoveryError("Recovery operation is already active.") from None
                    time.sleep(0.05)
            handle.seek(0)
            handle.truncate()
            handle.write(canonical_json({"schema_version": 1, "process_id": os.getpid()}).encode("utf-8"))
            handle.flush()
            self._handle = handle
        except RecoveryError:
            raise
        except OSError:
            if handle is not None:
                handle.close()
            elif descriptor is not None:
                os.close(descriptor)
            raise RecoveryError("Recovery lock is unavailable or unsafe.") from None

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
            self._handle = None

    def __enter__(self) -> RecoveryLock:
        self.acquire()
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()


def create_backup(
    *,
    data_root: Path,
    backup_root: Path,
    backup_identifier: str,
    safe_configuration_fingerprint: str,
    application_revision: str | None = None,
    created_at: datetime | None = None,
) -> RecoveryManifest:
    """Create, publish, and verify one offline coordinated backup."""

    data = _validated_root(data_root)
    backups = _validated_root(backup_root)
    _require_separate_roots(data, backups)
    identifier = _validate_backup_identifier(backup_identifier)
    validate_sha256(safe_configuration_fingerprint)
    destination = backups / identifier
    with RecoveryLock(backups):
        if destination.exists():
            raise RecoveryError("Backup identifier already exists.")
        try:
            staging = Path(tempfile.mkdtemp(prefix=".backup-tmp-", dir=backups))
        except OSError:
            raise RecoveryError("Backup staging could not be created.") from None
        published = False
        try:
            components_root = staging / "components"
            components_root.mkdir()
            inventory = production_persistence_inventory()
            for component in inventory:
                _snapshot_component(data, components_root, component)
            file_records = _bundle_file_records(components_root, inventory)
            manifest_value: dict[str, Any] = {
                "schema_version": RECOVERY_SCHEMA_VERSION,
                "backup_identifier": identifier,
                "created_at": (created_at or datetime.now(UTC))
                .astimezone(UTC)
                .isoformat()
                .replace("+00:00", "Z"),
                "safe_configuration_fingerprint": safe_configuration_fingerprint,
                "application_revision": application_revision,
                "consistency_model": "offline_coordinated",
                "components": [item.model_dump(mode="json") for item in inventory],
                "files": [item.model_dump(mode="json") for item in file_records],
                "total_file_count": len(file_records),
                "total_byte_count": sum(item.byte_length for item in file_records),
            }
            manifest_value["manifest_fingerprint"] = recovery_manifest_fingerprint(manifest_value)
            manifest = RecoveryManifest.model_validate(manifest_value)
            _write_exclusive(staging / "manifest.json", manifest.to_canonical_json().encode("utf-8"))
            _write_exclusive(staging / "COMPLETE", f"{manifest.manifest_fingerprint}\n".encode("ascii"))
            _rename_directory_no_replace(staging, destination)
            published = True
            verify_backup(backup_root=backups, backup_identifier=identifier)
            return manifest
        except RecoveryError:
            raise
        except (OSError, ValueError, sqlite3.Error):
            raise RecoveryError("Backup creation failed.") from None
        finally:
            if not published:
                _remove_task_owned_staging(staging, backups, ".backup-tmp-")


def verify_backup(*, backup_root: Path, backup_identifier: str) -> RecoveryVerificationResult:
    """Verify an existing bundle without modifying it."""

    backups = _validated_root(backup_root)
    identifier = _validate_backup_identifier(backup_identifier)
    bundle = _safe_direct_child_directory(backups, identifier)
    try:
        manifest_bytes = _read_regular_file(bundle / "manifest.json", maximum=16 * 1024 * 1024)
        raw = json.loads(manifest_bytes)
        manifest = RecoveryManifest.model_validate(raw)
        if manifest.backup_identifier != identifier:
            raise ValueError
        if manifest.components != production_persistence_inventory():
            raise ValueError
        if manifest_bytes != manifest.to_canonical_json().encode("utf-8"):
            raise ValueError
        component_types = {
            component.component_identifier: component.component_type
            for component in manifest.components
        }
        for record in manifest.files:
            expected_classification = (
                RecoveryFileClassification.SQLITE_SNAPSHOT
                if component_types[record.component_identifier] == "sqlite"
                else RecoveryFileClassification.IMMUTABLE_REPOSITORY
                if component_types[record.component_identifier]
                == "immutable-repository"
                else RecoveryFileClassification.PERSISTED_APPLICATION_STATE
            )
            if record.classification != expected_classification:
                raise ValueError
        marker = _read_regular_file(bundle / "COMPLETE", maximum=256)
        if marker != f"{manifest.manifest_fingerprint}\n".encode("ascii"):
            raise ValueError
        actual = _safe_file_inventory(bundle)
        expected = {"manifest.json", "COMPLETE", *(item.relative_path for item in manifest.files)}
        if set(actual) != expected:
            raise ValueError
        for record in manifest.files:
            payload = _read_regular_file(bundle / Path(record.relative_path), maximum=record.byte_length)
            if len(payload) != record.byte_length or hashlib.sha256(payload).hexdigest() != record.content_sha256:
                raise ValueError
        _validate_component_tree(bundle / "components", manifest.components)
        return RecoveryVerificationResult(
            backup_identifier=identifier,
            manifest_fingerprint=manifest.manifest_fingerprint,
            verified=True,
            component_count=len(manifest.components),
            file_count=len(manifest.files),
            total_byte_count=manifest.total_byte_count,
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError, sqlite3.Error):
        raise RecoveryError("Backup verification failed.") from None


def restore_backup(
    *,
    backup_root: Path,
    backup_identifier: str,
    target_data_root: Path,
    receipt_root: Path,
    restored_at: datetime | None = None,
) -> RecoveryReceipt:
    """Verify and restore a bundle into a new absent or explicitly empty root."""

    backups = _validated_root(backup_root)
    receipts = _validated_root(receipt_root)
    target = Path(os.path.abspath(target_data_root))
    _require_separate_roots(backups, receipts)
    try:
        target_present = target.exists() or target.is_symlink()
        if target_present:
            metadata = target.lstat()
            if _is_link_or_junction(target, metadata.st_mode) or not stat.S_ISDIR(
                metadata.st_mode
            ):
                raise OSError
            if any(target.iterdir()):
                raise RecoveryError("Restore target must be absent or empty.")
    except RecoveryError:
        raise
    except OSError:
        raise RecoveryError("Restore target is unavailable or unsafe.") from None
    parent = _validated_root(target.parent)
    if target.parent == backups or _overlaps(target, backups):
        raise RecoveryError("Restore and backup roots must be separate.")
    _require_separate_roots(target, receipts)
    with RecoveryLock(backups):
        verification = verify_backup(
            backup_root=backups, backup_identifier=backup_identifier
        )
        bundle = _safe_direct_child_directory(backups, backup_identifier)
        manifest = RecoveryManifest.model_validate_json(
            _read_regular_file(bundle / "manifest.json", maximum=16 * 1024 * 1024)
        )
        try:
            staging = Path(tempfile.mkdtemp(prefix=".restore-tmp-", dir=parent))
        except OSError:
            raise RecoveryError("Restore staging could not be created.") from None
        published = False
        removed_empty_target = False
        try:
            for component in manifest.components:
                destination = staging / component.relative_storage_identity
                source = bundle / "components" / component.component_identifier
                if component.component_type == "sqlite":
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source / "database.sqlite3", destination)
                else:
                    shutil.copytree(source, destination)
            _verify_restored_file_inventory(staging, manifest)
            _validate_component_tree(staging, manifest.components, restored_layout=True)
            if target_present:
                target.rmdir()
                removed_empty_target = True
            _rename_directory_no_replace(staging, target)
            published = True
            target_identity_sha256 = hashlib.sha256(os.fsencode(target)).hexdigest()
            receipt_identity = sha256_fingerprint(
                {
                    "backup_identifier": backup_identifier,
                    "manifest_fingerprint": verification.manifest_fingerprint,
                    "target_identity_sha256": target_identity_sha256,
                }
            )
            receipt_value: dict[str, Any] = {
                "schema_version": RECOVERY_RECEIPT_SCHEMA_VERSION,
                "receipt_identifier": f"receipt-{receipt_identity[:32]}",
                "backup_identifier": backup_identifier,
                "manifest_fingerprint": verification.manifest_fingerprint,
                "restored_at": (restored_at or datetime.now(UTC))
                .astimezone(UTC)
                .isoformat()
                .replace("+00:00", "Z"),
                "target_identity_sha256": target_identity_sha256,
                "component_count": verification.component_count,
                "file_count": verification.file_count,
            }
            receipt_value["receipt_fingerprint"] = sha256_fingerprint(receipt_value)
            receipt = RecoveryReceipt.model_validate(receipt_value)
            receipt_path = receipts / f"{receipt.receipt_identifier}.json"
            _write_exclusive(receipt_path, receipt.to_canonical_json().encode("utf-8"))
            return receipt
        except RecoveryError:
            raise
        except (OSError, ValueError, sqlite3.Error):
            raise RecoveryError("Restore failed.") from None
        finally:
            if not published:
                _remove_task_owned_staging(staging, parent, ".restore-tmp-")
                if removed_empty_target and not target.exists():
                    try:
                        target.mkdir()
                    except OSError:
                        pass


def _snapshot_component(data: Path, components: Path, component: RecoveryComponentRecord) -> None:
    source = data / component.relative_storage_identity
    destination = components / component.component_identifier
    try:
        metadata = source.lstat()
    except OSError:
        raise RecoveryError("Required recovery component is unavailable.") from None
    if component.component_type == "sqlite":
        if _is_link_or_junction(source, metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise RecoveryError("Required recovery component is unsafe.")
        destination.mkdir()
        _sqlite_backup(source, destination / "database.sqlite3")
        return
    if _is_link_or_junction(source, metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise RecoveryError("Required recovery component is unsafe.")
    destination.mkdir()
    directories, files = _walk_component_entries(source)
    for directory in directories:
        (destination / directory.relative_to(source)).mkdir()
    for path in files:
        relative = path.relative_to(source)
        target = destination / relative
        payload = _read_regular_file(path, maximum=RECOVERY_MAXIMUM_TOTAL_BYTES)
        _write_exclusive(target, payload)


def _sqlite_backup(source: Path, destination: Path) -> None:
    source_uri = f"file:{source.as_posix()}?mode=ro"
    with closing(sqlite3.connect(source_uri, uri=True)) as source_connection:
        with closing(sqlite3.connect(destination)) as destination_connection:
            source_connection.backup(destination_connection)
            if destination_connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise RecoveryError("SQLite recovery snapshot failed integrity validation.")


def _validate_component_tree(
    root: Path,
    components: tuple[RecoveryComponentRecord, ...],
    *,
    restored_layout: bool = False,
) -> None:
    for component in components:
        base = (
            root / component.relative_storage_identity
            if restored_layout
            else root / component.component_identifier
        )
        if component.component_type == "sqlite":
            path = base if restored_layout else base / "database.sqlite3"
            _verify_sqlite(path, component.component_identifier)
        elif component.component_identifier == "molecular-artifacts":
            _verify_artifact_repository(base)
        elif component.component_identifier == "molecular-projects":
            _verify_project_repository(base)
        else:
            _validate_json_tree(base)


def _verify_restored_file_inventory(root: Path, manifest: RecoveryManifest) -> None:
    components = {
        component.component_identifier: component for component in manifest.components
    }
    expected: dict[str, RecoveryFileRecord] = {}
    for record in manifest.files:
        parts = PurePosixPath(record.relative_path).parts
        if len(parts) < 3 or parts[0] != "components":
            raise RecoveryError("Recovery manifest file identity is invalid.")
        component = components[record.component_identifier]
        if parts[1] != component.component_identifier:
            raise RecoveryError("Recovery manifest file identity is invalid.")
        if component.component_type == "sqlite":
            if parts[2:] != ("database.sqlite3",):
                raise RecoveryError("Recovery SQLite file identity is invalid.")
            restored_relative = component.relative_storage_identity
        else:
            restored_relative = (
                PurePosixPath(component.relative_storage_identity)
                / PurePosixPath(*parts[2:])
            ).as_posix()
        if restored_relative in expected:
            raise RecoveryError("Recovery restore file identity is duplicated.")
        expected[restored_relative] = record
    actual = {
        PurePosixPath(*path.relative_to(root).parts).as_posix()
        for path in _walk_component_files(root)
    }
    if actual != set(expected):
        raise RecoveryError("Recovery restore file inventory is inconsistent.")
    for relative_path, record in expected.items():
        payload = _read_regular_file(
            root / Path(relative_path), maximum=record.byte_length
        )
        if (
            len(payload) != record.byte_length
            or hashlib.sha256(payload).hexdigest() != record.content_sha256
        ):
            raise RecoveryError("Recovery restored file identity is invalid.")


def _verify_sqlite(path: Path, component_identifier: str) -> None:
    uri = f"file:{path.as_posix()}?mode=ro&immutable=1"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise RecoveryError("SQLite recovery snapshot failed integrity validation.")
        if component_identifier == "authorization-grants":
            row = connection.execute(
                "SELECT version FROM schema_metadata WHERE component='grants'"
            ).fetchone()
            if row != (GRANT_SCHEMA_VERSION,):
                raise RecoveryError("Grant recovery schema is incompatible.")
        else:
            row = connection.execute(
                "SELECT version FROM schema_metadata WHERE component='audit'"
            ).fetchone()
            if row != (AUDIT_SCHEMA_VERSION,) or not _verify_audit_chain(connection):
                raise RecoveryError("Audit recovery evidence is invalid.")


def _verify_audit_chain(connection: sqlite3.Connection) -> bool:
    rows = connection.execute(
        "SELECT sequence_number, record_json, previous_fingerprint, record_fingerprint, schema_version "
        "FROM audit_records ORDER BY sequence_number"
    ).fetchall()
    previous = AUDIT_GENESIS_FINGERPRINT
    for expected_sequence, row in enumerate(rows, start=1):
        sequence, record_json, declared_previous, fingerprint, schema_version = row
        if (
            sequence != expected_sequence
            or declared_previous != previous
            or schema_version != AUDIT_SCHEMA_VERSION
            or _audit_fingerprint(sequence, previous, record_json) != fingerprint
        ):
            return False
        previous = fingerprint
    state = connection.execute(
        "SELECT head_sequence, head_fingerprint, schema_version FROM audit_state WHERE singleton=1"
    ).fetchone()
    return state == (len(rows), previous, AUDIT_SCHEMA_VERSION)


def _bundle_file_records(
    components_root: Path,
    inventory: tuple[RecoveryComponentRecord, ...],
) -> tuple[RecoveryFileRecord, ...]:
    classifications = {
        item.component_identifier: (
            RecoveryFileClassification.SQLITE_SNAPSHOT
            if item.component_type == "sqlite"
            else RecoveryFileClassification.IMMUTABLE_REPOSITORY
            if item.component_type == "immutable-repository"
            else RecoveryFileClassification.PERSISTED_APPLICATION_STATE
        )
        for item in inventory
    }
    records: list[RecoveryFileRecord] = []
    total = 0
    for path in _walk_component_files(components_root):
        relative = path.relative_to(components_root)
        component_identifier = relative.parts[0]
        payload = _read_regular_file(path, maximum=RECOVERY_MAXIMUM_TOTAL_BYTES)
        total += len(payload)
        if len(records) + 1 > RECOVERY_MAXIMUM_FILES or total > RECOVERY_MAXIMUM_TOTAL_BYTES:
            raise RecoveryError("Recovery bundle exceeds its configured limits.")
        records.append(
            RecoveryFileRecord(
                component_identifier=component_identifier,
                relative_path=(PurePosixPath("components") / PurePosixPath(*relative.parts)).as_posix(),
                byte_length=len(payload),
                content_sha256=hashlib.sha256(payload).hexdigest(),
                classification=classifications[component_identifier],
            )
        )
    return tuple(sorted(records, key=lambda item: item.relative_path))


def _walk_component_entries(root: Path) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    directories: list[Path] = []
    files: list[Path] = []
    try:
        for current_root, directory_names, file_names in os.walk(root, topdown=True, followlinks=False):
            current = Path(current_root)
            directory_names.sort()
            file_names.sort()
            for name in tuple(directory_names):
                path = current / name
                metadata = path.lstat()
                if _is_link_or_junction(path, metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                    raise RecoveryError("Recovery source contains an unsafe entry.")
                if name.startswith(".") and ("tmp" in name or "staging" in name):
                    raise RecoveryError("Recovery source contains incomplete publication state.")
                directories.append(path)
            for name in file_names:
                if name in _EXCLUDED_FILES or any(name.endswith(suffix) for suffix in _EXCLUDED_SUFFIXES):
                    continue
                path = current / name
                metadata = path.lstat()
                if _is_link_or_junction(path, metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                    raise RecoveryError("Recovery source contains an unsafe entry.")
                files.append(path)
    except OSError:
        raise RecoveryError("Recovery source is unavailable or unsafe.") from None
    return tuple(directories), tuple(files)


def _walk_component_files(root: Path) -> tuple[Path, ...]:
    return _walk_component_entries(root)[1]


def _verify_artifact_repository(root: Path) -> None:
    directories, files = _walk_component_entries(root)
    relative_directories = {path.relative_to(root).parts for path in directories}
    if ("objects",) not in relative_directories:
        raise RecoveryError("Molecular artifact recovery layout is incomplete.")
    hexadecimal = set("0123456789abcdef")
    for parts in relative_directories:
        if parts == ("objects",):
            continue
        if (
            parts[0] != "objects"
            or len(parts) not in {2, 3}
            or len(parts[1]) != 2
            or not set(parts[1]) <= hexadecimal
            or (
                len(parts) == 3
                and (
                    len(parts[2]) != 64
                    or not set(parts[2]) <= hexadecimal
                    or parts[1] != parts[2][:2]
                )
            )
        ):
            raise RecoveryError("Molecular artifact recovery layout is invalid.")
    references: list[ArtifactReference] = []
    object_files: dict[tuple[str, str], set[str]] = {}
    for path in files:
        parts = path.relative_to(root).parts
        if (
            len(parts) != 4
            or parts[0] != "objects"
            or len(parts[1]) != 2
            or len(parts[2]) != 64
            or parts[1] != parts[2][:2]
            or parts[3] not in {"payload.bin", "reference.json"}
        ):
            raise RecoveryError("Molecular artifact recovery layout is invalid.")
        object_files.setdefault((parts[1], parts[2]), set()).add(parts[3])
        if parts[3] == "reference.json":
            payload = _read_regular_file(path, maximum=1 * 1024 * 1024)
            reference = ArtifactReference.model_validate_json(payload)
            if payload != reference.to_canonical_json().encode("utf-8"):
                raise RecoveryError("Molecular artifact recovery evidence is noncanonical.")
            expected_key = hashlib.sha256(
                reference.pointer.to_canonical_json().encode("utf-8")
            ).hexdigest()
            if parts[2] != expected_key:
                raise RecoveryError("Molecular artifact recovery identity is invalid.")
            references.append(reference)
    if any(names != {"payload.bin", "reference.json"} for names in object_files.values()):
        raise RecoveryError("Molecular artifact recovery object is incomplete.")
    declared_objects = {
        (parts[1], parts[2]) for parts in relative_directories if len(parts) == 3
    }
    if set(object_files) != declared_objects:
        raise RecoveryError("Molecular artifact recovery object is incomplete.")
    repository = MolecularArtifactRepository(root)
    try:
        repository.start()
        for reference in references:
            repository.read(reference)
    finally:
        repository.close()


def _verify_project_repository(root: Path) -> None:
    directories, files = _walk_component_entries(root)
    relative_directories = {path.relative_to(root).parts for path in directories}
    if ("projects",) not in relative_directories:
        raise RecoveryError("Molecular project recovery layout is incomplete.")
    hexadecimal = set("0123456789abcdef")
    for parts in relative_directories:
        if parts == ("projects",):
            continue
        if (
            parts[0] != "projects"
            or len(parts) not in {2, 3}
            or len(parts[1]) != 2
            or not set(parts[1]) <= hexadecimal
            or (
                len(parts) == 3
                and (
                    len(parts[2]) != 64
                    or not set(parts[2]) <= hexadecimal
                    or parts[1] != parts[2][:2]
                )
            )
        ):
            raise RecoveryError("Molecular project recovery layout is invalid.")
    identifiers: list[str] = []
    project_files: dict[tuple[str, str], set[str]] = {}
    for path in files:
        parts = path.relative_to(root).parts
        if (
            len(parts) != 4
            or parts[0] != "projects"
            or len(parts[1]) != 2
            or len(parts[2]) != 64
            or parts[1] != parts[2][:2]
            or parts[3] not in {"project.json", "record.json"}
        ):
            raise RecoveryError("Molecular project recovery layout is invalid.")
        project_files.setdefault((parts[1], parts[2]), set()).add(parts[3])
        if parts[3] == "record.json":
            payload = _read_regular_file(path, maximum=1 * 1024 * 1024)
            value = json.loads(payload)
            identifier = value.get("project_identifier") if isinstance(value, dict) else None
            if not isinstance(identifier, str):
                raise RecoveryError("Molecular project recovery record is invalid.")
            if parts[2] != hashlib.sha256(identifier.encode("utf-8")).hexdigest():
                raise RecoveryError("Molecular project recovery identity is invalid.")
            identifiers.append(identifier)
    if any(names != {"project.json", "record.json"} for names in project_files.values()):
        raise RecoveryError("Molecular project recovery object is incomplete.")
    declared_projects = {
        (parts[1], parts[2]) for parts in relative_directories if len(parts) == 3
    }
    if set(project_files) != declared_projects:
        raise RecoveryError("Molecular project recovery object is incomplete.")
    repository = MolecularProjectRepository(root)
    try:
        repository.start()
        for identifier in identifiers:
            repository.read(identifier)
    finally:
        repository.close()


def _validate_json_tree(root: Path) -> None:
    for path in _walk_component_files(root):
        if path.suffix == ".json":
            payload = _read_regular_file(path, maximum=16 * 1024 * 1024)
            value = json.loads(payload)
            if not isinstance(value, (dict, list)):
                raise RecoveryError("Persisted JSON recovery evidence is invalid.")


def _safe_file_inventory(root: Path) -> tuple[str, ...]:
    return tuple(
        sorted(
            PurePosixPath(*path.relative_to(root).parts).as_posix()
            for path in _walk_component_files(root)
        )
    )


def _canonical_relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or "\\" in value
        or any(part in {"", ".", ".."} for part in path.parts)
        or len(path.parts) > RECOVERY_MAXIMUM_PATH_DEPTH
        or len(value.encode("utf-8")) > RECOVERY_MAXIMUM_PATH_BYTES
    ):
        raise ValueError("Recovery relative path is invalid.")
    canonical = path.as_posix()
    if canonical != value:
        raise ValueError("Recovery relative path is not canonical.")
    return canonical


def _validate_backup_identifier(value: str) -> str:
    if not value.startswith(_BACKUP_IDENTIFIER_PREFIX):
        raise ValueError("Backup identifier is invalid.")
    return validate_identifier(value, label="backup identifier")


def _is_link_or_junction(path: Path, mode: int) -> bool:
    junction = getattr(path, "is_junction", None)
    return stat.S_ISLNK(mode) or bool(junction and junction())


def _validated_root(path: Path) -> Path:
    candidate = Path(os.path.abspath(path))
    try:
        metadata = candidate.lstat()
        if _is_link_or_junction(candidate, metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise OSError
        return candidate.resolve(strict=True)
    except OSError:
        raise RecoveryError("Recovery root is unavailable or unsafe.") from None


def _safe_direct_child_directory(root: Path, name: str) -> Path:
    candidate = root / name
    try:
        metadata = candidate.lstat()
        if _is_link_or_junction(candidate, metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise OSError
        resolved = candidate.resolve(strict=True)
        if resolved.parent != root:
            raise OSError
        return resolved
    except OSError:
        raise RecoveryError("Recovery bundle is unavailable or unsafe.") from None


def _read_regular_file(path: Path, *, maximum: int) -> bytes:
    try:
        metadata = path.lstat()
        if _is_link_or_junction(path, metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise OSError
        if metadata.st_size > maximum:
            raise OSError
        payload = path.read_bytes()
        if len(payload) > maximum:
            raise OSError
        return payload
    except OSError:
        raise RecoveryError("Recovery file is unavailable or unsafe.") from None


def _write_exclusive(path: Path, payload: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _remove_task_owned_staging(path: Path, root: Path, prefix: str) -> None:
    try:
        if path.parent == root and path.name.startswith(prefix):
            shutil.rmtree(path)
    except OSError:
        pass


def _overlaps(first: Path, second: Path) -> bool:
    first = Path(os.path.abspath(first))
    second = Path(os.path.abspath(second))
    return first == second or first in second.parents or second in first.parents


def _require_separate_roots(first: Path, second: Path) -> None:
    if _overlaps(first, second):
        raise RecoveryError("Recovery roots must not overlap.")


def _safe_cli(operation: str, callback: Any) -> int:
    try:
        result = callback()
    except Exception:
        print(canonical_json({"operation": operation, "status": "failed"}))
        return 1
    print(canonical_json({"operation": operation, "status": "passed", "result": result.model_dump(mode="json")}))
    return 0


def backup_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cgr-pulsate-backup")
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--backup-root", required=True, type=Path)
    parser.add_argument("--backup-identifier", required=True)
    parser.add_argument("--configuration-fingerprint", required=True)
    parser.add_argument("--application-revision")
    arguments = parser.parse_args(argv)
    return _safe_cli(
        "backup",
        lambda: create_backup(
            data_root=arguments.data_root,
            backup_root=arguments.backup_root,
            backup_identifier=arguments.backup_identifier,
            safe_configuration_fingerprint=arguments.configuration_fingerprint,
            application_revision=arguments.application_revision,
        ),
    )


def verify_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cgr-pulsate-backup-verify")
    parser.add_argument("--backup-root", required=True, type=Path)
    parser.add_argument("--backup-identifier", required=True)
    arguments = parser.parse_args(argv)
    return _safe_cli(
        "backup-verify",
        lambda: verify_backup(
            backup_root=arguments.backup_root,
            backup_identifier=arguments.backup_identifier,
        ),
    )


def restore_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cgr-pulsate-restore")
    parser.add_argument("--backup-root", required=True, type=Path)
    parser.add_argument("--backup-identifier", required=True)
    parser.add_argument("--target-data-root", required=True, type=Path)
    parser.add_argument("--receipt-root", required=True, type=Path)
    arguments = parser.parse_args(argv)
    return _safe_cli(
        "restore",
        lambda: restore_backup(
            backup_root=arguments.backup_root,
            backup_identifier=arguments.backup_identifier,
            target_data_root=arguments.target_data_root,
            receipt_root=arguments.receipt_root,
        ),
    )


__all__ = [
    "RECOVERY_RECEIPT_SCHEMA_VERSION",
    "RECOVERY_SCHEMA_VERSION",
    "RecoveryComponentRecord",
    "RecoveryError",
    "RecoveryFileClassification",
    "RecoveryFileRecord",
    "RecoveryLock",
    "RecoveryManifest",
    "RecoveryReceipt",
    "RecoveryVerificationResult",
    "backup_main",
    "create_backup",
    "production_persistence_inventory",
    "recovery_manifest_fingerprint",
    "restore_backup",
    "restore_main",
    "verify_backup",
    "verify_main",
]
