"""Fail-closed content-addressed persistence for molecular artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
import threading
from pathlib import Path

from pydantic import ValidationError

from cgr.science.artifacts import ArtifactReference

_REFERENCE_JSON_MAXIMUM_BYTES = 1 * 1024 * 1024
_REFERENCE_FILENAME = "reference.json"
_PAYLOAD_FILENAME = "payload.bin"


class MolecularArtifactRepositoryError(ValueError):
    """Base error for controlled molecular artifact repository failures."""


class MolecularArtifactNotFoundError(MolecularArtifactRepositoryError):
    """Raised when a requested content-addressed object is absent."""


class MolecularArtifactIntegrityError(MolecularArtifactRepositoryError):
    """Raised when persisted molecular artifact evidence is unsafe or corrupt."""


class MolecularArtifactConflictError(MolecularArtifactRepositoryError):
    """Raised when one pointer is already bound to different complete evidence."""


class MolecularArtifactRepository:
    """Immutable repository addressed only by canonical artifact pointers."""

    def __init__(self, root: Path) -> None:
        self.configured_root = Path(os.path.abspath(Path(root)))
        self.root = self.configured_root
        self._objects_root = self.configured_root / "objects"
        self._lock = threading.RLock()
        self._started = False

    def start(self) -> None:
        """Create and validate the repository root exactly once."""

        with self._lock:
            if self._started:
                return
            trusted_root = self._validate_or_create_configured_root()
            objects_root = self.configured_root / "objects"
            trusted_objects_root = self._validate_or_create_directory(
                objects_root,
                parent=trusted_root,
                error_message="Molecular artifact objects root is unsafe.",
            )
            self.root = trusted_root
            self._objects_root = trusted_objects_root
            self._started = True

    def close(self) -> None:
        """Stop repository operations without deleting persisted objects."""

        with self._lock:
            self._started = False

    def put(
        self,
        reference: ArtifactReference,
        payload: bytes,
    ) -> ArtifactReference:
        """Atomically publish exact bytes under their canonical artifact pointer."""

        with self._lock:
            self._require_started()
            self._validate_payload(reference, payload)
            objects_root = self._validated_objects_root()
            object_key = self._object_key(reference)
            shard = self._validated_shard(
                objects_root,
                object_key[:2],
                create=True,
            )
            object_directory = shard / object_key
            if self._entry_exists(object_directory):
                persisted_reference, persisted_payload = self._read_existing_object(
                    reference,
                    objects_root=objects_root,
                    conflict_on_reference_mismatch=True,
                )
                if persisted_reference != reference or persisted_payload != payload:
                    raise MolecularArtifactConflictError(
                        "Artifact pointer is already bound to different evidence."
                    )
                return reference

            try:
                temporary = Path(
                    tempfile.mkdtemp(
                        prefix=f".{object_key}.tmp-",
                        dir=shard,
                    )
                )
            except OSError:
                raise MolecularArtifactRepositoryError(
                    "Temporary artifact publication could not be created."
                ) from None
            published = False
            try:
                self._write_fsynced(temporary / _PAYLOAD_FILENAME, payload)
                reference_payload = reference.to_canonical_json().encode("utf-8")
                if len(reference_payload) > _REFERENCE_JSON_MAXIMUM_BYTES:
                    raise MolecularArtifactRepositoryError(
                        "Artifact reference exceeds the repository size limit."
                    )
                self._write_fsynced(
                    temporary / _REFERENCE_FILENAME,
                    reference_payload,
                )
                if self._entry_exists(object_directory):
                    persisted_reference, persisted_payload = (
                        self._read_existing_object(
                            reference,
                            objects_root=objects_root,
                            conflict_on_reference_mismatch=True,
                        )
                    )
                    if (
                        persisted_reference != reference
                        or persisted_payload != payload
                    ):
                        raise MolecularArtifactConflictError(
                            "Artifact pointer is already bound to different evidence."
                        )
                    return reference
                try:
                    os.rename(temporary, object_directory)
                    published = True
                except OSError:
                    if not self._entry_exists(object_directory):
                        raise MolecularArtifactRepositoryError(
                            "Artifact publication failed safely."
                        ) from None
                    persisted_reference, persisted_payload = (
                        self._read_existing_object(
                            reference,
                            objects_root=objects_root,
                            conflict_on_reference_mismatch=True,
                        )
                    )
                    if (
                        persisted_reference != reference
                        or persisted_payload != payload
                    ):
                        raise MolecularArtifactConflictError(
                            "Artifact pointer is already bound to different evidence."
                        )
                return reference
            except MolecularArtifactRepositoryError:
                raise
            except OSError:
                raise MolecularArtifactRepositoryError(
                    "Artifact publication failed safely."
                ) from None
            finally:
                if not published:
                    self._discard_temporary_directory(temporary, shard)

    def read(self, reference: ArtifactReference) -> bytes:
        """Return exact bytes only after complete persisted-evidence validation."""

        with self._lock:
            self._require_started()
            if reference.byte_size is None:
                raise MolecularArtifactIntegrityError(
                    "Artifact reference must declare a byte size."
                )
            objects_root = self._validated_objects_root()
            _, payload = self._read_existing_object(
                reference,
                objects_root=objects_root,
                conflict_on_reference_mismatch=False,
            )
            return payload

    def _validate_or_create_configured_root(self) -> Path:
        try:
            metadata = self.configured_root.lstat()
        except FileNotFoundError:
            try:
                self.configured_root.mkdir(parents=True)
                metadata = self.configured_root.lstat()
            except OSError:
                raise MolecularArtifactRepositoryError(
                    "Molecular artifact repository root could not be created."
                ) from None
        except OSError:
            raise MolecularArtifactRepositoryError(
                "Molecular artifact repository root is unsafe."
            ) from None
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise MolecularArtifactRepositoryError(
                "Molecular artifact repository root must be a normal directory."
            )
        try:
            return self.configured_root.resolve(strict=True)
        except (OSError, RuntimeError):
            raise MolecularArtifactRepositoryError(
                "Molecular artifact repository root is unsafe."
            ) from None

    def _validate_or_create_directory(
        self,
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
                    raise MolecularArtifactRepositoryError(
                        error_message
                    ) from None
            except OSError:
                raise MolecularArtifactRepositoryError(error_message) from None
        except OSError:
            raise MolecularArtifactRepositoryError(error_message) from None
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise MolecularArtifactRepositoryError(error_message)
        try:
            resolved = path.resolve(strict=True)
        except (OSError, RuntimeError):
            raise MolecularArtifactRepositoryError(error_message) from None
        if resolved.parent != parent:
            raise MolecularArtifactRepositoryError(error_message)
        return resolved

    def _require_started(self) -> None:
        if not self._started:
            raise MolecularArtifactRepositoryError(
                "Molecular artifact repository is not started."
            )

    def _validated_objects_root(self) -> Path:
        try:
            root_metadata = self.configured_root.lstat()
            objects_metadata = (
                self.configured_root / "objects"
            ).lstat()
        except OSError:
            raise MolecularArtifactIntegrityError(
                "Molecular artifact repository layout is incomplete."
            ) from None
        if (
            stat.S_ISLNK(root_metadata.st_mode)
            or not stat.S_ISDIR(root_metadata.st_mode)
            or stat.S_ISLNK(objects_metadata.st_mode)
            or not stat.S_ISDIR(objects_metadata.st_mode)
        ):
            raise MolecularArtifactIntegrityError(
                "Molecular artifact repository layout is unsafe."
            )
        try:
            resolved_root = self.configured_root.resolve(strict=True)
            resolved_objects = (self.configured_root / "objects").resolve(
                strict=True
            )
        except (OSError, RuntimeError):
            raise MolecularArtifactIntegrityError(
                "Molecular artifact repository layout is unsafe."
            ) from None
        if resolved_root != self.root or resolved_objects.parent != self.root:
            raise MolecularArtifactIntegrityError(
                "Molecular artifact repository layout escaped its trusted root."
            )
        return resolved_objects

    def _validated_shard(
        self,
        objects_root: Path,
        shard_name: str,
        *,
        create: bool,
    ) -> Path:
        shard = objects_root / shard_name
        try:
            metadata = shard.lstat()
        except FileNotFoundError:
            if not create:
                raise MolecularArtifactNotFoundError("Molecular artifact not found.")
            try:
                shard.mkdir()
                metadata = shard.lstat()
            except FileExistsError:
                try:
                    metadata = shard.lstat()
                except OSError:
                    raise MolecularArtifactIntegrityError(
                        "Molecular artifact shard is unsafe."
                    ) from None
            except OSError:
                raise MolecularArtifactRepositoryError(
                    "Molecular artifact shard could not be created safely."
                ) from None
        except OSError:
            raise MolecularArtifactIntegrityError(
                "Molecular artifact shard is unsafe."
            ) from None
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise MolecularArtifactIntegrityError(
                "Molecular artifact shard is unsafe."
            )
        try:
            resolved = shard.resolve(strict=True)
        except (OSError, RuntimeError):
            raise MolecularArtifactIntegrityError(
                "Molecular artifact shard is unsafe."
            ) from None
        if resolved.parent != objects_root or self.root not in resolved.parents:
            raise MolecularArtifactIntegrityError(
                "Molecular artifact shard escaped its trusted root."
            )
        return resolved

    def _read_existing_object(
        self,
        requested_reference: ArtifactReference,
        *,
        objects_root: Path,
        conflict_on_reference_mismatch: bool,
    ) -> tuple[ArtifactReference, bytes]:
        object_key = self._object_key(requested_reference)
        shard = self._validated_shard(
            objects_root,
            object_key[:2],
            create=False,
        )
        object_directory = self._validated_object_directory(
            shard,
            object_key,
        )
        reference_path = self._validated_regular_file(
            object_directory,
            _REFERENCE_FILENAME,
            maximum_bytes=_REFERENCE_JSON_MAXIMUM_BYTES,
        )
        persisted_reference = self._read_reference(reference_path)
        if persisted_reference.pointer != requested_reference.pointer:
            raise MolecularArtifactIntegrityError(
                "Persisted artifact pointer does not match its object key."
            )
        if persisted_reference.byte_size is None:
            raise MolecularArtifactIntegrityError(
                "Persisted artifact reference has no byte size."
            )

        payload_path = self._validated_regular_file(
            object_directory,
            _PAYLOAD_FILENAME,
            expected_bytes=persisted_reference.byte_size,
        )
        payload = self._read_exact_payload(
            payload_path,
            persisted_reference,
        )
        if persisted_reference != requested_reference:
            if conflict_on_reference_mismatch:
                raise MolecularArtifactConflictError(
                    "Artifact pointer is already bound to a different reference."
                )
            raise MolecularArtifactIntegrityError(
                "Persisted artifact reference does not match the request."
            )
        return persisted_reference, payload

    def _validated_object_directory(
        self,
        shard: Path,
        object_key: str,
    ) -> Path:
        object_directory = shard / object_key
        try:
            metadata = object_directory.lstat()
        except FileNotFoundError:
            raise MolecularArtifactNotFoundError(
                "Molecular artifact not found."
            ) from None
        except OSError:
            raise MolecularArtifactIntegrityError(
                "Molecular artifact object is unsafe."
            ) from None
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise MolecularArtifactIntegrityError(
                "Molecular artifact object is unsafe."
            )
        try:
            resolved = object_directory.resolve(strict=True)
        except (OSError, RuntimeError):
            raise MolecularArtifactIntegrityError(
                "Molecular artifact object is unsafe."
            ) from None
        if resolved.parent != shard or self.root not in resolved.parents:
            raise MolecularArtifactIntegrityError(
                "Molecular artifact object escaped its trusted root."
            )
        return resolved

    def _validated_regular_file(
        self,
        directory: Path,
        filename: str,
        *,
        maximum_bytes: int | None = None,
        expected_bytes: int | None = None,
    ) -> Path:
        path = directory / filename
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            raise MolecularArtifactIntegrityError(
                "Molecular artifact object is incomplete."
            ) from None
        except OSError:
            raise MolecularArtifactIntegrityError(
                "Molecular artifact evidence is unsafe."
            ) from None
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise MolecularArtifactIntegrityError(
                "Molecular artifact evidence must be a regular file."
            )
        if maximum_bytes is not None and metadata.st_size > maximum_bytes:
            raise MolecularArtifactIntegrityError(
                "Molecular artifact reference exceeds its size limit."
            )
        if expected_bytes is not None and metadata.st_size != expected_bytes:
            raise MolecularArtifactIntegrityError(
                "Molecular artifact payload size is inconsistent."
            )
        try:
            resolved = path.resolve(strict=True)
        except (OSError, RuntimeError):
            raise MolecularArtifactIntegrityError(
                "Molecular artifact evidence is unsafe."
            ) from None
        if resolved.parent != directory or self.root not in resolved.parents:
            raise MolecularArtifactIntegrityError(
                "Molecular artifact evidence escaped its trusted root."
            )
        return resolved

    @staticmethod
    def _read_reference(path: Path) -> ArtifactReference:
        try:
            raw = path.read_bytes()
        except OSError:
            raise MolecularArtifactIntegrityError(
                "Molecular artifact reference is unreadable."
            ) from None
        if len(raw) > _REFERENCE_JSON_MAXIMUM_BYTES:
            raise MolecularArtifactIntegrityError(
                "Molecular artifact reference exceeds its size limit."
            )
        try:
            decoded = raw.decode("utf-8", errors="strict")
            value = json.loads(decoded)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise MolecularArtifactIntegrityError(
                "Molecular artifact reference is malformed."
            ) from None
        if not isinstance(value, dict):
            raise MolecularArtifactIntegrityError(
                "Molecular artifact reference must be a JSON object."
            )
        try:
            reference = ArtifactReference.model_validate(value)
        except ValidationError:
            raise MolecularArtifactIntegrityError(
                "Molecular artifact reference failed validation."
            ) from None
        if raw != reference.to_canonical_json().encode("utf-8"):
            raise MolecularArtifactIntegrityError(
                "Molecular artifact reference is not canonical."
            )
        return reference

    @staticmethod
    def _read_exact_payload(
        path: Path,
        reference: ArtifactReference,
    ) -> bytes:
        if reference.byte_size is None:
            raise MolecularArtifactIntegrityError(
                "Molecular artifact reference has no byte size."
            )
        try:
            payload = path.read_bytes()
        except OSError:
            raise MolecularArtifactIntegrityError(
                "Molecular artifact payload is unreadable."
            ) from None
        if len(payload) != reference.byte_size:
            raise MolecularArtifactIntegrityError(
                "Molecular artifact payload size is inconsistent."
            )
        if hashlib.sha256(payload).hexdigest() != reference.content_sha256:
            raise MolecularArtifactIntegrityError(
                "Molecular artifact payload hash is inconsistent."
            )
        return payload

    @staticmethod
    def _validate_payload(
        reference: ArtifactReference,
        payload: bytes,
    ) -> None:
        if not isinstance(payload, bytes):
            raise MolecularArtifactRepositoryError(
                "Molecular artifact payload must be bytes."
            )
        if reference.byte_size is None:
            raise MolecularArtifactRepositoryError(
                "Artifact reference must declare a byte size."
            )
        if reference.byte_size != len(payload):
            raise MolecularArtifactRepositoryError(
                "Artifact payload byte size does not match its reference."
            )
        if hashlib.sha256(payload).hexdigest() != reference.content_sha256:
            raise MolecularArtifactRepositoryError(
                "Artifact payload hash does not match its reference."
            )

    @staticmethod
    def _write_fsynced(path: Path, payload: bytes) -> None:
        with path.open("xb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())

    @staticmethod
    def _entry_exists(path: Path) -> bool:
        try:
            path.lstat()
        except FileNotFoundError:
            return False
        except OSError:
            raise MolecularArtifactIntegrityError(
                "Molecular artifact object is unsafe."
            ) from None
        return True

    @staticmethod
    def _discard_temporary_directory(temporary: Path, shard: Path) -> None:
        if (
            temporary.parent == shard
            and temporary.name.startswith(".")
            and ".tmp-" in temporary.name
        ):
            try:
                metadata = temporary.lstat()
            except FileNotFoundError:
                return
            except OSError:
                raise MolecularArtifactRepositoryError(
                    "Temporary artifact publication could not be inspected."
                ) from None
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(
                metadata.st_mode
            ):
                raise MolecularArtifactIntegrityError(
                    "Temporary artifact publication is unsafe."
                )
            try:
                shutil.rmtree(temporary)
            except OSError:
                raise MolecularArtifactRepositoryError(
                    "Temporary artifact publication could not be cleaned up."
                ) from None

    @staticmethod
    def _object_key(reference: ArtifactReference) -> str:
        return hashlib.sha256(
            reference.pointer.to_canonical_json().encode("utf-8")
        ).hexdigest()
