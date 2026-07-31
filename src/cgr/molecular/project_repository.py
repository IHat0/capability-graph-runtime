"""Immutable fail-closed persistence for canonical molecular projects."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from .contracts import MolecularProject

_PROJECT_JSON_MAXIMUM_BYTES = 64 * 1024 * 1024
_RECORD_JSON_MAXIMUM_BYTES = 16 * 1024
_PROJECTS_DIRECTORY_NAME = "projects"
_RECORD_FILENAME = "record.json"
_PROJECT_FILENAME = "project.json"
_RECORD_FORMAT = "cgr.molecular-project-record.v1"
_SHA256_LENGTH = 64
_READ_CHUNK_BYTES = 1024 * 1024
_AT_FDCWD = -100
_RENAME_NOREPLACE = 1


class MolecularProjectRepositoryError(ValueError):
    """Base error for controlled molecular project repository failures."""


class MolecularProjectNotFoundError(MolecularProjectRepositoryError):
    """Raised when a requested molecular project is absent."""


class MolecularProjectIntegrityError(MolecularProjectRepositoryError):
    """Raised when molecular project evidence is unsafe or inconsistent."""


class MolecularProjectConflictError(MolecularProjectRepositoryError):
    """Raised when a project identifier is already bound to other evidence."""


@dataclass(frozen=True, slots=True)
class _ProjectRecord:
    record_format: str
    project_identifier: str
    identifier_sha256: str
    project_sha256: str
    project_byte_size: int

    @classmethod
    def create(
        cls,
        *,
        project_identifier: str,
        identifier_sha256: str,
        project_bytes: bytes,
    ) -> _ProjectRecord:
        return cls(
            record_format=_RECORD_FORMAT,
            project_identifier=project_identifier,
            identifier_sha256=identifier_sha256,
            project_sha256=hashlib.sha256(project_bytes).hexdigest(),
            project_byte_size=len(project_bytes),
        )

    @classmethod
    def from_value(cls, value: object) -> _ProjectRecord:
        if not isinstance(value, dict):
            raise MolecularProjectIntegrityError(
                "Molecular project record must be a JSON object."
            )
        expected_keys = {
            "record_format",
            "project_identifier",
            "identifier_sha256",
            "project_sha256",
            "project_byte_size",
        }
        if set(value) != expected_keys:
            raise MolecularProjectIntegrityError(
                "Molecular project record fields are inconsistent."
            )
        if any(
            type(value[name]) is not str
            for name in (
                "record_format",
                "project_identifier",
                "identifier_sha256",
                "project_sha256",
            )
        ) or type(value["project_byte_size"]) is not int:
            raise MolecularProjectIntegrityError(
                "Molecular project record field types are inconsistent."
            )
        record = cls(
            record_format=value["record_format"],
            project_identifier=value["project_identifier"],
            identifier_sha256=value["identifier_sha256"],
            project_sha256=value["project_sha256"],
            project_byte_size=value["project_byte_size"],
        )
        if record.record_format != _RECORD_FORMAT:
            raise MolecularProjectIntegrityError(
                "Molecular project record format is unsupported."
            )
        if not _is_sha256(record.identifier_sha256) or not _is_sha256(
            record.project_sha256
        ):
            raise MolecularProjectIntegrityError(
                "Molecular project record hashes are malformed."
            )
        if record.project_byte_size <= 0:
            raise MolecularProjectIntegrityError(
                "Molecular project record byte size is invalid."
            )
        if record.project_byte_size > _PROJECT_JSON_MAXIMUM_BYTES:
            raise MolecularProjectIntegrityError(
                "Molecular project evidence exceeds its size limit."
            )
        return record

    def to_bytes(self) -> bytes:
        value = {
            "identifier_sha256": self.identifier_sha256,
            "project_byte_size": self.project_byte_size,
            "project_identifier": self.project_identifier,
            "project_sha256": self.project_sha256,
            "record_format": self.record_format,
        }
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")


class MolecularProjectRepository:
    """Persist complete canonical projects under hashed immutable identities."""

    def __init__(self, root: Path) -> None:
        self.configured_root = Path(os.path.abspath(Path(root)))
        self.root = self.configured_root
        self._projects_root = self.configured_root / _PROJECTS_DIRECTORY_NAME
        self._lock = threading.RLock()
        self._started = False

    def start(self) -> None:
        """Create and validate the repository layout."""

        with self._lock:
            if self._started:
                return
            trusted_root = self._validate_or_create_root()
            trusted_projects_root = self._validate_or_create_directory(
                self.configured_root / _PROJECTS_DIRECTORY_NAME,
                parent=trusted_root,
                error_message="Molecular project storage root is unsafe.",
            )
            self.root = trusted_root
            self._projects_root = trusted_projects_root
            self._started = True

    def close(self) -> None:
        """Close this repository instance without changing persisted evidence."""

        with self._lock:
            self._started = False

    def put(self, project: MolecularProject) -> None:
        """Atomically publish one complete canonical molecular project."""

        with self._lock:
            self._require_started()
            project_bytes, validated_project = self._validate_supplied_project(
                project
            )
            identifier = validated_project.project_identifier
            identifier_sha256 = _identifier_sha256(identifier)
            record = _ProjectRecord.create(
                project_identifier=identifier,
                identifier_sha256=identifier_sha256,
                project_bytes=project_bytes,
            )
            record_bytes = record.to_bytes()
            if not record_bytes or len(record_bytes) > _RECORD_JSON_MAXIMUM_BYTES:
                raise MolecularProjectIntegrityError(
                    "Molecular project record exceeds its size limit."
                )

            projects_root = self._validated_projects_root()
            prefix = self._validated_prefix(
                projects_root,
                identifier_sha256[:2],
                create=True,
            )
            final_directory = prefix / identifier_sha256
            if self._entry_exists(final_directory):
                self._require_idempotent_existing(
                    identifier,
                    project_bytes,
                    projects_root=projects_root,
                )
                return

            try:
                temporary = Path(
                    tempfile.mkdtemp(prefix=".project-tmp-", dir=prefix)
                )
            except OSError:
                raise MolecularProjectRepositoryError(
                    "Temporary molecular project publication could not be created."
                ) from None
            published = False
            try:
                if temporary.parent != prefix:
                    raise MolecularProjectRepositoryError(
                        "Temporary molecular project publication is unsafe."
                    )
                temporary = self._validated_temporary_directory(temporary, prefix)
                self._write_fsynced(
                    temporary / _RECORD_FILENAME,
                    record_bytes,
                )
                self._write_fsynced(
                    temporary / _PROJECT_FILENAME,
                    project_bytes,
                )
                self._fsync_directory(temporary)

                if self._entry_exists(final_directory):
                    self._require_idempotent_existing(
                        identifier,
                        project_bytes,
                        projects_root=projects_root,
                    )
                    return
                try:
                    _rename_directory_no_replace(temporary, final_directory)
                    published = True
                except OSError:
                    if not self._entry_exists(final_directory):
                        raise MolecularProjectRepositoryError(
                            "Molecular project publication failed safely."
                        ) from None
                    self._require_idempotent_existing(
                        identifier,
                        project_bytes,
                        projects_root=projects_root,
                    )
                if published:
                    self._fsync_directory(prefix)
            except MolecularProjectRepositoryError:
                raise
            except OSError:
                raise MolecularProjectRepositoryError(
                    "Molecular project publication failed safely."
                ) from None
            finally:
                if not published:
                    self._discard_temporary_directory(temporary, prefix)

    def read(self, project_identifier: str) -> MolecularProject:
        """Read one project after validating all immutable evidence."""

        with self._lock:
            self._require_started()
            identifier = self._validate_lookup_identifier(project_identifier)
            projects_root = self._validated_projects_root()
            project, _ = self._read_existing_project(
                identifier,
                projects_root=projects_root,
            )
            return project

    def resolve(self, project_identifier: str) -> MolecularProject | None:
        """Resolve one project, returning ``None`` only when it is absent."""

        try:
            return self.read(project_identifier)
        except MolecularProjectNotFoundError:
            return None

    def _validate_or_create_root(self) -> Path:
        try:
            metadata = self.configured_root.lstat()
        except FileNotFoundError:
            try:
                self.configured_root.mkdir(parents=True)
                metadata = self.configured_root.lstat()
            except OSError:
                raise MolecularProjectRepositoryError(
                    "Molecular project repository root could not be created."
                ) from None
        except OSError:
            raise MolecularProjectRepositoryError(
                "Molecular project repository root is unsafe."
            ) from None
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise MolecularProjectRepositoryError(
                "Molecular project repository root must be a normal directory."
            )
        try:
            return self.configured_root.resolve(strict=True)
        except (OSError, RuntimeError):
            raise MolecularProjectRepositoryError(
                "Molecular project repository root is unsafe."
            ) from None

    @staticmethod
    def _validate_or_create_directory(
        path: Path,
        *,
        parent: Path,
        error_message: str,
    ) -> Path:
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            try:
                path.mkdir()
                metadata = path.lstat()
            except FileExistsError:
                try:
                    metadata = path.lstat()
                except OSError:
                    raise MolecularProjectRepositoryError(
                        error_message
                    ) from None
            except OSError:
                raise MolecularProjectRepositoryError(error_message) from None
        except OSError:
            raise MolecularProjectRepositoryError(error_message) from None
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise MolecularProjectRepositoryError(error_message)
        try:
            resolved = path.resolve(strict=True)
        except (OSError, RuntimeError):
            raise MolecularProjectRepositoryError(error_message) from None
        if resolved.parent != parent:
            raise MolecularProjectRepositoryError(error_message)
        return resolved

    def _require_started(self) -> None:
        if not self._started:
            raise MolecularProjectRepositoryError(
                "Molecular project repository is not started."
            )

    def _validated_projects_root(self) -> Path:
        configured_projects = self.configured_root / _PROJECTS_DIRECTORY_NAME
        try:
            root_metadata = self.configured_root.lstat()
            projects_metadata = configured_projects.lstat()
        except OSError:
            raise MolecularProjectIntegrityError(
                "Molecular project repository layout is incomplete."
            ) from None
        if (
            stat.S_ISLNK(root_metadata.st_mode)
            or not stat.S_ISDIR(root_metadata.st_mode)
            or stat.S_ISLNK(projects_metadata.st_mode)
            or not stat.S_ISDIR(projects_metadata.st_mode)
        ):
            raise MolecularProjectIntegrityError(
                "Molecular project repository layout is unsafe."
            )
        try:
            resolved_root = self.configured_root.resolve(strict=True)
            resolved_projects = configured_projects.resolve(strict=True)
        except (OSError, RuntimeError):
            raise MolecularProjectIntegrityError(
                "Molecular project repository layout is unsafe."
            ) from None
        if (
            resolved_root != self.root
            or resolved_projects != self._projects_root
            or resolved_projects.parent != self.root
        ):
            raise MolecularProjectIntegrityError(
                "Molecular project repository layout escaped its trusted root."
            )
        return resolved_projects

    def _validated_prefix(
        self,
        projects_root: Path,
        prefix_name: str,
        *,
        create: bool,
    ) -> Path:
        if len(prefix_name) != 2 or not _is_lowercase_hex(prefix_name):
            raise MolecularProjectIntegrityError(
                "Molecular project prefix identity is invalid."
            )
        prefix = projects_root / prefix_name
        try:
            metadata = prefix.lstat()
        except FileNotFoundError:
            if not create:
                raise MolecularProjectNotFoundError(
                    "Molecular project was not found."
                ) from None
            try:
                prefix.mkdir()
                metadata = prefix.lstat()
            except FileExistsError:
                try:
                    metadata = prefix.lstat()
                except OSError:
                    raise MolecularProjectIntegrityError(
                        "Molecular project prefix is unsafe."
                    ) from None
            except OSError:
                raise MolecularProjectRepositoryError(
                    "Molecular project prefix could not be created safely."
                ) from None
        except OSError:
            raise MolecularProjectIntegrityError(
                "Molecular project prefix is unsafe."
            ) from None
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise MolecularProjectIntegrityError(
                "Molecular project prefix is unsafe."
            )
        try:
            resolved = prefix.resolve(strict=True)
        except (OSError, RuntimeError):
            raise MolecularProjectIntegrityError(
                "Molecular project prefix is unsafe."
            ) from None
        if resolved.parent != projects_root or self.root not in resolved.parents:
            raise MolecularProjectIntegrityError(
                "Molecular project prefix escaped its trusted root."
            )
        return resolved

    def _read_existing_project(
        self,
        requested_identifier: str,
        *,
        projects_root: Path,
    ) -> tuple[MolecularProject, bytes]:
        identifier_sha256 = _identifier_sha256(requested_identifier)
        prefix = self._validated_prefix(
            projects_root,
            identifier_sha256[:2],
            create=False,
        )
        project_directory = self._validated_project_directory(
            prefix,
            identifier_sha256,
        )
        record_bytes = self._read_regular_file(
            project_directory,
            _RECORD_FILENAME,
            maximum_bytes=_RECORD_JSON_MAXIMUM_BYTES,
            evidence_label="record",
        )
        record = self._parse_record(record_bytes)
        if record.project_identifier != requested_identifier:
            raise MolecularProjectIntegrityError(
                "Molecular project record identifier is inconsistent."
            )
        if record.identifier_sha256 != identifier_sha256:
            raise MolecularProjectIntegrityError(
                "Molecular project record identity hash is inconsistent."
            )

        project_bytes = self._read_regular_file(
            project_directory,
            _PROJECT_FILENAME,
            maximum_bytes=_PROJECT_JSON_MAXIMUM_BYTES,
            expected_bytes=record.project_byte_size,
            evidence_label="document",
        )
        if hashlib.sha256(project_bytes).hexdigest() != record.project_sha256:
            raise MolecularProjectIntegrityError(
                "Molecular project document hash is inconsistent."
            )
        project = self._parse_project(project_bytes)
        if project.project_identifier != requested_identifier:
            raise MolecularProjectIntegrityError(
                "Molecular project document identifier is inconsistent."
            )
        return project, project_bytes

    def _validated_project_directory(
        self,
        prefix: Path,
        identifier_sha256: str,
    ) -> Path:
        if not _is_sha256(identifier_sha256):
            raise MolecularProjectIntegrityError(
                "Molecular project object identity is invalid."
            )
        directory = prefix / identifier_sha256
        try:
            metadata = directory.lstat()
        except FileNotFoundError:
            raise MolecularProjectNotFoundError(
                "Molecular project was not found."
            ) from None
        except OSError:
            raise MolecularProjectIntegrityError(
                "Molecular project object is unsafe."
            ) from None
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise MolecularProjectIntegrityError(
                "Molecular project object is unsafe."
            )
        try:
            resolved = directory.resolve(strict=True)
        except (OSError, RuntimeError):
            raise MolecularProjectIntegrityError(
                "Molecular project object is unsafe."
            ) from None
        if resolved.parent != prefix or self.root not in resolved.parents:
            raise MolecularProjectIntegrityError(
                "Molecular project object escaped its trusted root."
            )
        try:
            entries = {item.name for item in resolved.iterdir()}
        except OSError:
            raise MolecularProjectIntegrityError(
                "Molecular project object is unreadable."
            ) from None
        if entries != {_RECORD_FILENAME, _PROJECT_FILENAME}:
            raise MolecularProjectIntegrityError(
                "Molecular project object is incomplete or inconsistent."
            )
        return resolved

    def _read_regular_file(
        self,
        directory: Path,
        filename: str,
        *,
        maximum_bytes: int,
        evidence_label: str,
        expected_bytes: int | None = None,
    ) -> bytes:
        path = directory / filename
        try:
            metadata = path.lstat()
        except OSError:
            raise MolecularProjectIntegrityError(
                f"Molecular project {evidence_label} is incomplete."
            ) from None
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise MolecularProjectIntegrityError(
                f"Molecular project {evidence_label} must be a regular file."
            )
        if metadata.st_size <= 0:
            raise MolecularProjectIntegrityError(
                f"Molecular project {evidence_label} is empty."
            )
        if metadata.st_size > maximum_bytes:
            raise MolecularProjectIntegrityError(
                f"Molecular project {evidence_label} exceeds its size limit."
            )
        if expected_bytes is not None and metadata.st_size != expected_bytes:
            raise MolecularProjectIntegrityError(
                "Molecular project document byte size is inconsistent."
            )
        try:
            resolved = path.resolve(strict=True)
        except (OSError, RuntimeError):
            raise MolecularProjectIntegrityError(
                f"Molecular project {evidence_label} is unsafe."
            ) from None
        if resolved.parent != directory or self.root not in resolved.parents:
            raise MolecularProjectIntegrityError(
                f"Molecular project {evidence_label} escaped its trusted root."
            )
        return self._read_bounded_file(
            path,
            metadata,
            maximum_bytes=maximum_bytes,
            expected_bytes=expected_bytes,
            evidence_label=evidence_label,
        )

    @staticmethod
    def _read_bounded_file(
        path: Path,
        metadata: os.stat_result,
        *,
        maximum_bytes: int,
        expected_bytes: int | None,
        evidence_label: str,
    ) -> bytes:
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
        except OSError:
            raise MolecularProjectIntegrityError(
                f"Molecular project {evidence_label} is unreadable."
            ) from None
        try:
            opened_metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened_metadata.st_mode)
                or opened_metadata.st_size <= 0
                or opened_metadata.st_size > maximum_bytes
                or _file_identity(opened_metadata) != _file_identity(metadata)
            ):
                raise MolecularProjectIntegrityError(
                    f"Molecular project {evidence_label} changed during validation."
                )
            if (
                expected_bytes is not None
                and opened_metadata.st_size != expected_bytes
            ):
                raise MolecularProjectIntegrityError(
                    "Molecular project document byte size is inconsistent."
                )
            chunks: list[bytes] = []
            remaining = maximum_bytes + 1
            while remaining > 0:
                chunk = os.read(descriptor, min(_READ_CHUNK_BYTES, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            payload = b"".join(chunks)
            final_metadata = os.fstat(descriptor)
        except MolecularProjectRepositoryError:
            raise
        except OSError:
            raise MolecularProjectIntegrityError(
                f"Molecular project {evidence_label} is unreadable."
            ) from None
        finally:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if (
            not payload
            or len(payload) > maximum_bytes
            or len(payload) != opened_metadata.st_size
            or _file_identity(final_metadata) != _file_identity(opened_metadata)
        ):
            raise MolecularProjectIntegrityError(
                f"Molecular project {evidence_label} changed during reading."
            )
        if expected_bytes is not None and len(payload) != expected_bytes:
            raise MolecularProjectIntegrityError(
                "Molecular project document byte size is inconsistent."
            )
        try:
            after_metadata = path.lstat()
        except OSError:
            raise MolecularProjectIntegrityError(
                f"Molecular project {evidence_label} changed during reading."
            ) from None
        if (
            stat.S_ISLNK(after_metadata.st_mode)
            or _file_identity(after_metadata) != _file_identity(metadata)
        ):
            raise MolecularProjectIntegrityError(
                f"Molecular project {evidence_label} changed during reading."
            )
        return payload

    @staticmethod
    def _parse_record(raw: bytes) -> _ProjectRecord:
        try:
            decoded = raw.decode("utf-8", errors="strict")
            value = json.loads(decoded)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise MolecularProjectIntegrityError(
                "Molecular project record is malformed."
            ) from None
        record = _ProjectRecord.from_value(value)
        if raw != record.to_bytes():
            raise MolecularProjectIntegrityError(
                "Molecular project record is not canonical."
            )
        return record

    @staticmethod
    def _parse_project(raw: bytes) -> MolecularProject:
        try:
            decoded = raw.decode("utf-8", errors="strict")
            value = json.loads(decoded)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise MolecularProjectIntegrityError(
                "Molecular project document is malformed."
            ) from None
        if not isinstance(value, dict):
            raise MolecularProjectIntegrityError(
                "Molecular project document must be a JSON object."
            )
        try:
            project = MolecularProject.model_validate(value)
        except ValidationError:
            raise MolecularProjectIntegrityError(
                "Molecular project document failed validation."
            ) from None
        if raw != project.to_canonical_json().encode("utf-8"):
            raise MolecularProjectIntegrityError(
                "Molecular project document is not canonical."
            )
        return project

    @classmethod
    def _validate_supplied_project(
        cls,
        project: MolecularProject,
    ) -> tuple[bytes, MolecularProject]:
        if not isinstance(project, MolecularProject):
            raise MolecularProjectIntegrityError(
                "Molecular project value must be a MolecularProject."
            )
        try:
            project_bytes = project.to_canonical_json().encode("utf-8")
        except (TypeError, ValueError):
            raise MolecularProjectIntegrityError(
                "Molecular project value could not be serialized canonically."
            ) from None
        if not project_bytes:
            raise MolecularProjectIntegrityError(
                "Molecular project value cannot be empty."
            )
        if len(project_bytes) > _PROJECT_JSON_MAXIMUM_BYTES:
            raise MolecularProjectIntegrityError(
                "Molecular project value exceeds its size limit."
            )
        validated = cls._parse_project(project_bytes)
        if validated != project:
            raise MolecularProjectIntegrityError(
                "Molecular project value failed canonical reconstruction."
            )
        if validated.to_canonical_json().encode("utf-8") != project_bytes:
            raise MolecularProjectIntegrityError(
                "Molecular project value is not canonical."
            )
        return project_bytes, validated

    def _require_idempotent_existing(
        self,
        identifier: str,
        project_bytes: bytes,
        *,
        projects_root: Path,
    ) -> None:
        _, persisted_bytes = self._read_existing_project(
            identifier,
            projects_root=projects_root,
        )
        if persisted_bytes != project_bytes:
            raise MolecularProjectConflictError(
                "Molecular project identifier is already bound to other evidence."
            )

    @staticmethod
    def _validate_lookup_identifier(project_identifier: str) -> str:
        if type(project_identifier) is not str or not project_identifier:
            raise MolecularProjectRepositoryError(
                "Molecular project lookup identifier is invalid."
            )
        try:
            project_identifier.encode("utf-8", errors="strict")
        except UnicodeEncodeError:
            raise MolecularProjectRepositoryError(
                "Molecular project lookup identifier is invalid."
            ) from None
        return project_identifier

    @staticmethod
    def _validated_temporary_directory(temporary: Path, prefix: Path) -> Path:
        try:
            metadata = temporary.lstat()
            resolved = temporary.resolve(strict=True)
        except (OSError, RuntimeError):
            raise MolecularProjectRepositoryError(
                "Temporary molecular project publication is unsafe."
            ) from None
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or resolved.parent != prefix
            or not temporary.name.startswith(".project-tmp-")
        ):
            raise MolecularProjectRepositoryError(
                "Temporary molecular project publication is unsafe."
            )
        return resolved

    @staticmethod
    def _write_fsynced(path: Path, payload: bytes) -> None:
        try:
            with path.open("xb") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
        except OSError:
            raise MolecularProjectRepositoryError(
                "Molecular project evidence could not be written safely."
            ) from None

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        if os.name == "nt":
            return
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        try:
            descriptor = os.open(directory, flags)
        except OSError:
            raise MolecularProjectRepositoryError(
                "Molecular project directory could not be synchronized."
            ) from None
        try:
            os.fsync(descriptor)
        except OSError:
            raise MolecularProjectRepositoryError(
                "Molecular project directory could not be synchronized."
            ) from None
        finally:
            try:
                os.close(descriptor)
            except OSError:
                pass

    @staticmethod
    def _entry_exists(path: Path) -> bool:
        try:
            path.lstat()
        except FileNotFoundError:
            return False
        except OSError:
            raise MolecularProjectIntegrityError(
                "Molecular project object is unsafe."
            ) from None
        return True

    @staticmethod
    def _discard_temporary_directory(temporary: Path, prefix: Path) -> None:
        if (
            temporary.parent != prefix
            or not temporary.name.startswith(".project-tmp-")
        ):
            return
        try:
            metadata = temporary.lstat()
        except FileNotFoundError:
            return
        except OSError:
            raise MolecularProjectRepositoryError(
                "Temporary molecular project publication could not be inspected."
            ) from None
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise MolecularProjectIntegrityError(
                "Temporary molecular project publication is unsafe."
            )
        try:
            shutil.rmtree(temporary)
        except OSError:
            raise MolecularProjectRepositoryError(
                "Temporary molecular project publication could not be cleaned up."
            ) from None


def _identifier_sha256(project_identifier: str) -> str:
    return hashlib.sha256(project_identifier.encode("utf-8")).hexdigest()


def _is_lowercase_hex(value: str) -> bool:
    return bool(value) and all(character in "0123456789abcdef" for character in value)


def _is_sha256(value: str) -> bool:
    return len(value) == _SHA256_LENGTH and _is_lowercase_hex(value)


def _file_identity(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_dev, metadata.st_ino


def _rename_directory_no_replace(source: Path, destination: Path) -> None:
    """Atomically rename a directory while refusing an existing destination."""

    if sys.platform == "win32":
        # Windows rename is documented to fail when the destination exists.
        os.rename(source, destination)
        return
    if not sys.platform.startswith("linux"):
        raise MolecularProjectRepositoryError(
            "Atomic no-replace project publication is unavailable."
        )
    try:
        standard_library = ctypes.CDLL(None, use_errno=True)
        renameat2 = standard_library.renameat2
    except (AttributeError, OSError):
        raise MolecularProjectRepositoryError(
            "Atomic no-replace project publication is unavailable."
        ) from None

    renameat2.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameat2.restype = ctypes.c_int
    try:
        encoded_source = os.fsencode(source)
        encoded_destination = os.fsencode(destination)
    except (TypeError, UnicodeError):
        raise MolecularProjectRepositoryError(
            "Atomic no-replace project publication is unavailable."
        ) from None

    ctypes.set_errno(0)
    result = renameat2(
        _AT_FDCWD,
        encoded_source,
        _AT_FDCWD,
        encoded_destination,
        _RENAME_NOREPLACE,
    )
    if result == 0:
        return
    error_number = ctypes.get_errno() or errno.EIO
    if error_number == errno.EEXIST:
        raise FileExistsError(error_number, os.strerror(error_number))
    if error_number == errno.ENOSYS:
        raise MolecularProjectRepositoryError(
            "Atomic no-replace project publication is unavailable."
        )
    raise OSError(error_number, os.strerror(error_number))


__all__ = [
    "MolecularProjectConflictError",
    "MolecularProjectIntegrityError",
    "MolecularProjectNotFoundError",
    "MolecularProjectRepository",
    "MolecularProjectRepositoryError",
]
