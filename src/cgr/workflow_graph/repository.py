"""Integrity-checked persistence for workflow definitions and mutable run snapshots."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import threading
import time
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from cgr.molecular._publication import _rename_directory_no_replace

from cgr.science.canonical import canonical_json, validate_identifier, validate_sha256

from .contracts import WorkflowGraphDefinition
from .state import WorkflowRunSnapshot

_MAX_DEFINITION_BYTES = 64 * 1024 * 1024
_MAX_SNAPSHOT_BYTES = 128 * 1024 * 1024
_RECORD_MAXIMUM_BYTES = 32 * 1024
_DEFINITIONS_DIRECTORY = "workflow-definitions"
_RUNS_DIRECTORY = "workflow-runs"
_LOCK_FILENAME = ".workflow-repository.lock"
_DEFINITION_FORMAT = "cgr.workflow-definition-record/1.0.0"
_RUN_FORMAT = "cgr.workflow-run-record/1.0.0"


class WorkflowRepositoryError(RuntimeError):
    """Base path-safe workflow repository error."""


class WorkflowRepositoryNotFoundError(WorkflowRepositoryError):
    """Requested workflow evidence does not exist."""


class WorkflowRepositoryConflictError(WorkflowRepositoryError):
    """An immutable identity or optimistic revision conflicts."""


class WorkflowRepositoryIntegrityError(WorkflowRepositoryError):
    """Persisted workflow evidence is malformed or unsafe."""


def _is_link_or_reparse(path: Path, mode: int) -> bool:
    if stat.S_ISLNK(mode):
        return True
    attributes = getattr(path.stat(follow_symlinks=False), "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(attributes & reparse_flag)


def _canonical_bytes(value: object) -> bytes:
    return canonical_json(value).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _identity_hash(*parts: object) -> str:
    return _sha256(_canonical_bytes(parts))


class _FileLock(AbstractContextManager["_FileLock"]):
    """Bounded cross-process lock on a normal file beneath the trusted root."""

    def __init__(self, root: Path, timeout_seconds: float = 10.0) -> None:
        self._path = root / _LOCK_FILENAME
        self._timeout = timeout_seconds
        self._handle: Any | None = None

    def __enter__(self) -> _FileLock:
        deadline = time.monotonic() + self._timeout
        try:
            descriptor = os.open(
                self._path,
                os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            handle = os.fdopen(descriptor, "r+b")
        except OSError:
            raise WorkflowRepositoryIntegrityError(
                "Workflow repository lock is unavailable."
            ) from None
        try:
            metadata = self._path.lstat()
            if _is_link_or_reparse(self._path, metadata.st_mode) or not stat.S_ISREG(
                metadata.st_mode
            ):
                raise OSError
            if handle.seek(0, os.SEEK_END) == 0:
                handle.write(b"0")
                handle.flush()
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
                except OSError:
                    if time.monotonic() >= deadline:
                        raise WorkflowRepositoryConflictError(
                            "Workflow repository is busy."
                        ) from None
                    time.sleep(0.01)
        except Exception:
            handle.close()
            raise
        self._handle = handle
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type, exc, traceback
        handle = self._handle
        self._handle = None
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


class _TrustedRepository:
    def __init__(self, root: Path, child: str) -> None:
        self.configured_root = Path(os.path.abspath(Path(root)))
        self.root = self.configured_root
        self._child_name = child
        self._storage_root = self.configured_root / child
        self._thread_lock = threading.RLock()
        self._started = False

    def start(self) -> None:
        with self._thread_lock:
            if self._started:
                return
            self.root = self._validated_directory(self.configured_root, create=True)
            child = self.configured_root / self._child_name
            self._storage_root = self._validated_directory(
                child, create=True, expected_parent=self.root
            )
            self._started = True

    def close(self) -> None:
        with self._thread_lock:
            self._started = False

    def ready(self) -> bool:
        return self._started

    def _require_started(self) -> None:
        if not self._started:
            raise WorkflowRepositoryError("Workflow repository is not started.")

    def _validated_directory(
        self,
        path: Path,
        *,
        create: bool,
        expected_parent: Path | None = None,
    ) -> Path:
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            if not create:
                raise WorkflowRepositoryNotFoundError(
                    "Workflow repository entry was not found."
                ) from None
            try:
                path.mkdir(parents=expected_parent is None)
                metadata = path.lstat()
            except OSError:
                raise WorkflowRepositoryIntegrityError(
                    "Workflow repository directory could not be created."
                ) from None
        except OSError:
            raise WorkflowRepositoryIntegrityError(
                "Workflow repository directory is unsafe."
            ) from None
        if _is_link_or_reparse(path, metadata.st_mode) or not stat.S_ISDIR(
            metadata.st_mode
        ):
            raise WorkflowRepositoryIntegrityError(
                "Workflow repository directory is unsafe."
            )
        try:
            resolved = path.resolve(strict=True)
        except (OSError, RuntimeError):
            raise WorkflowRepositoryIntegrityError(
                "Workflow repository directory is unsafe."
            ) from None
        if expected_parent is not None and resolved.parent != expected_parent:
            raise WorkflowRepositoryIntegrityError(
                "Workflow repository directory escaped its trusted root."
            )
        return resolved

    def _entry_directory(self, identity_hash: str, *, create: bool) -> Path:
        validate_sha256(identity_hash)
        storage = self._validated_directory(
            self.configured_root / self._child_name,
            create=False,
            expected_parent=self.root,
        )
        shard = self._validated_directory(
            storage / identity_hash[:2], create=create, expected_parent=storage
        )
        return shard / identity_hash

    @staticmethod
    def _read_bounded_file(path: Path, maximum: int) -> bytes:
        try:
            metadata = path.lstat()
            if _is_link_or_reparse(path, metadata.st_mode) or not stat.S_ISREG(
                metadata.st_mode
            ):
                raise OSError
            if metadata.st_size <= 0 or metadata.st_size > maximum:
                raise OSError
            data = path.read_bytes()
        except OSError:
            raise WorkflowRepositoryIntegrityError(
                "Persisted workflow evidence is unsafe."
            ) from None
        if len(data) != metadata.st_size:
            raise WorkflowRepositoryIntegrityError(
                "Persisted workflow evidence changed during read."
            )
        return data

    @staticmethod
    def _write_atomic(path: Path, data: bytes, maximum: int) -> None:
        if not data or len(data) > maximum:
            raise WorkflowRepositoryIntegrityError(
                "Workflow evidence exceeds its bounded size."
            )
        directory = path.parent
        temporary: Path | None = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{path.name}.", suffix=".tmp", dir=directory
            )
            temporary = Path(temporary_name)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            if hasattr(os, "O_DIRECTORY"):
                directory_descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory_descriptor)
                finally:
                    os.close(directory_descriptor)
        except OSError:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
            raise WorkflowRepositoryError(
                "Workflow evidence publication failed safely."
            ) from None


class WorkflowGraphRepository(_TrustedRepository):
    """Immutable graph-definition repository keyed by graph identifier and version."""

    def __init__(self, root: Path) -> None:
        super().__init__(root, _DEFINITIONS_DIRECTORY)

    def put(self, graph: WorkflowGraphDefinition) -> WorkflowGraphDefinition:
        with self._thread_lock, _FileLock(self.root):
            self._require_started()
            validated = WorkflowGraphDefinition.model_validate(
                graph.model_dump(mode="json")
            )
            identity_hash = _identity_hash(
                validated.graph_identifier, validated.version
            )
            directory = self._entry_directory(identity_hash, create=True)
            definition_bytes = _canonical_bytes(validated.model_dump(mode="json"))
            record = {
                "format": _DEFINITION_FORMAT,
                "graph_identifier": validated.graph_identifier,
                "graph_version": validated.version,
                "graph_definition_fingerprint": validated.definition_fingerprint,
                "definition_sha256": _sha256(definition_bytes),
                "definition_byte_size": len(definition_bytes),
            }
            record_bytes = _canonical_bytes(record)
            if directory.exists():
                existing = self.get(validated.graph_identifier, validated.version)
                if existing.definition_fingerprint != validated.definition_fingerprint:
                    raise WorkflowRepositoryConflictError(
                        "Workflow graph identity is already bound to other evidence."
                    )
                return existing

            temporary = Path(
                tempfile.mkdtemp(prefix=".workflow-definition.", dir=directory.parent)
            )
            published = False
            try:
                self._write_atomic(
                    temporary / "definition.json", definition_bytes, _MAX_DEFINITION_BYTES
                )
                self._write_atomic(
                    temporary / "record.json", record_bytes, _RECORD_MAXIMUM_BYTES
                )
                try:
                    _rename_directory_no_replace(temporary, directory)
                    published = True
                except FileExistsError:
                    existing = self.get(validated.graph_identifier, validated.version)
                    if existing.definition_fingerprint != validated.definition_fingerprint:
                        raise WorkflowRepositoryConflictError(
                            "Workflow graph identity is already bound to other evidence."
                        )
                    return existing
                except OSError:
                    raise WorkflowRepositoryError(
                        "Workflow graph publication failed safely."
                    ) from None
            finally:
                if not published and temporary.exists():
                    import shutil

                    shutil.rmtree(temporary, ignore_errors=True)
            return validated

    def get(self, graph_identifier: str, version: int) -> WorkflowGraphDefinition:
        with self._thread_lock:
            self._require_started()
            identifier = validate_identifier(
                graph_identifier, label="workflow graph identifier"
            )
            if version < 1:
                raise WorkflowRepositoryNotFoundError(
                    "Workflow graph definition was not found."
                )
            directory = self._entry_directory(
                _identity_hash(identifier, version), create=False
            )
            if not directory.exists():
                raise WorkflowRepositoryNotFoundError(
                    "Workflow graph definition was not found."
                )
            directory = self._validated_directory(
                directory, create=False, expected_parent=directory.parent.resolve()
            )
            record_bytes = self._read_bounded_file(
                directory / "record.json", _RECORD_MAXIMUM_BYTES
            )
            definition_bytes = self._read_bounded_file(
                directory / "definition.json", _MAX_DEFINITION_BYTES
            )
            try:
                record = json.loads(record_bytes)
                if set(record) != {
                    "format",
                    "graph_identifier",
                    "graph_version",
                    "graph_definition_fingerprint",
                    "definition_sha256",
                    "definition_byte_size",
                }:
                    raise ValueError
                if (
                    record["format"] != _DEFINITION_FORMAT
                    or record["graph_identifier"] != identifier
                    or record["graph_version"] != version
                    or record["definition_sha256"] != _sha256(definition_bytes)
                    or record["definition_byte_size"] != len(definition_bytes)
                ):
                    raise ValueError
                validate_sha256(record["graph_definition_fingerprint"])
                graph = WorkflowGraphDefinition.model_validate_json(definition_bytes)
            except (TypeError, ValueError, json.JSONDecodeError, ValidationError):
                raise WorkflowRepositoryIntegrityError(
                    "Persisted workflow definition failed integrity validation."
                ) from None
            if (
                graph.graph_identifier != identifier
                or graph.version != version
                or graph.definition_fingerprint
                != record["graph_definition_fingerprint"]
            ):
                raise WorkflowRepositoryIntegrityError(
                    "Persisted workflow definition identity is inconsistent."
                )
            return graph


class GraphRunRepository(_TrustedRepository):
    """Atomic optimistic persistence for complete mutable graph-run snapshots."""

    def __init__(self, root: Path) -> None:
        super().__init__(root, _RUNS_DIRECTORY)

    def create(self, snapshot: WorkflowRunSnapshot) -> WorkflowRunSnapshot:
        with self._thread_lock, _FileLock(self.root):
            self._require_started()
            validated = WorkflowRunSnapshot.model_validate(
                snapshot.model_dump(mode="json")
            )
            if validated.revision != 0:
                raise WorkflowRepositoryConflictError(
                    "New workflow runs must start at revision zero."
                )
            identity_hash = _identity_hash(
                validated.graph_state.graph_run_identifier
            )
            directory = self._entry_directory(identity_hash, create=True)
            if directory.exists():
                existing = self.get(validated.graph_state.graph_run_identifier)
                if existing.snapshot_fingerprint == validated.snapshot_fingerprint:
                    return existing
                raise WorkflowRepositoryConflictError(
                    "Workflow run identity already exists."
                )
            try:
                directory.mkdir()
            except FileExistsError:
                raise WorkflowRepositoryConflictError(
                    "Workflow run identity already exists."
                ) from None
            except OSError:
                raise WorkflowRepositoryError(
                    "Workflow run creation failed safely."
                ) from None
            self._publish_snapshot(directory, validated)
            return validated

    def update(
        self,
        snapshot: WorkflowRunSnapshot,
        *,
        expected_revision: int,
    ) -> WorkflowRunSnapshot:
        with self._thread_lock, _FileLock(self.root):
            self._require_started()
            current = self.get(snapshot.graph_state.graph_run_identifier)
            if current.revision != expected_revision:
                raise WorkflowRepositoryConflictError(
                    "Workflow run revision is stale."
                )
            if snapshot.revision != expected_revision + 1:
                raise WorkflowRepositoryConflictError(
                    "Workflow run revisions must increase by exactly one."
                )
            validated = WorkflowRunSnapshot.model_validate(
                snapshot.model_dump(mode="json")
            )
            directory = self._entry_directory(
                _identity_hash(validated.graph_state.graph_run_identifier),
                create=False,
            )
            self._publish_snapshot(directory, validated)
            return validated

    def get(self, graph_run_identifier: str) -> WorkflowRunSnapshot:
        with self._thread_lock:
            self._require_started()
            run_id = validate_identifier(
                graph_run_identifier, label="workflow run identifier"
            )
            directory = self._entry_directory(_identity_hash(run_id), create=False)
            if not directory.exists():
                raise WorkflowRepositoryNotFoundError(
                    "Workflow run was not found."
                )
            directory = self._validated_directory(
                directory, create=False, expected_parent=directory.parent.resolve()
            )
            envelope_bytes = self._read_bounded_file(
                directory / "run.json", _MAX_SNAPSHOT_BYTES
            )
            try:
                envelope = json.loads(envelope_bytes)
                if set(envelope) != {
                    "format",
                    "graph_run_identifier",
                    "revision",
                    "snapshot_fingerprint",
                    "snapshot",
                }:
                    raise ValueError
                if (
                    envelope["format"] != _RUN_FORMAT
                    or envelope["graph_run_identifier"] != run_id
                ):
                    raise ValueError
                validate_sha256(envelope["snapshot_fingerprint"])
                snapshot = WorkflowRunSnapshot.model_validate(envelope["snapshot"])
            except (TypeError, ValueError, json.JSONDecodeError, ValidationError):
                raise WorkflowRepositoryIntegrityError(
                    "Persisted workflow run failed integrity validation."
                ) from None
            if (
                snapshot.graph_state.graph_run_identifier != run_id
                or snapshot.revision != envelope["revision"]
                or snapshot.snapshot_fingerprint != envelope["snapshot_fingerprint"]
            ):
                raise WorkflowRepositoryIntegrityError(
                    "Persisted workflow run identity is inconsistent."
                )
            return snapshot

    def _publish_snapshot(
        self, directory: Path, snapshot: WorkflowRunSnapshot
    ) -> None:
        envelope = {
            "format": _RUN_FORMAT,
            "graph_run_identifier": snapshot.graph_state.graph_run_identifier,
            "revision": snapshot.revision,
            "snapshot_fingerprint": snapshot.snapshot_fingerprint,
            "snapshot": snapshot.model_dump(mode="json"),
        }
        self._write_atomic(
            directory / "run.json",
            _canonical_bytes(envelope),
            _MAX_SNAPSHOT_BYTES,
        )
