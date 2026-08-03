"""Fail-closed molecular artifact repository regressions."""

from __future__ import annotations

import hashlib
import json
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

import cgr.molecular._publication as publication_module
import cgr.molecular.artifact_repository as artifact_repository_module
from cgr.kernel.contracts import CapabilityVersion
from cgr.molecular import (
    MolecularArtifactConflictError,
    MolecularArtifactIntegrityError,
    MolecularArtifactNotFoundError,
    MolecularArtifactRepository,
    MolecularArtifactRepositoryError,
    MolecularTopologyAtom,
    MolecularTopologyIndex,
)
from cgr.science import ArtifactPointer, ArtifactReference, CreationProvenance

VERSION = CapabilityVersion(major=1, minor=0, patch=0)
PROVENANCE = CreationProvenance(
    producer="molecular-artifact-repository-tests",
    producer_version=VERSION,
    execution_identifier="execution-artifact-repository-tests",
    source="test-suite",
)


def _reference(
    payload: bytes,
    *,
    artifact_identifier: str = "artifact-molecular-structure",
    artifact_type: str = "molecular_structure",
    media_type: str = "application/octet-stream",
    byte_size: int | None | object = ...,
    content_sha256: str | None = None,
    storage_location: str | None = None,
    metadata: dict[str, str | int | float | bool | None] | None = None,
) -> ArtifactReference:
    declared_size = len(payload) if byte_size is ... else byte_size
    return ArtifactReference(
        artifact_identifier=artifact_identifier,
        schema_version=VERSION,
        artifact_type=artifact_type,
        media_type=media_type,
        content_sha256=content_sha256 or hashlib.sha256(payload).hexdigest(),
        byte_size=declared_size,
        storage_location=storage_location,
        metadata=metadata or {},
        provenance=PROVENANCE,
    )


def _replace_reference(
    reference: ArtifactReference,
    **updates: Any,
) -> ArtifactReference:
    values = {
        field_name: getattr(reference, field_name)
        for field_name in type(reference).model_fields
    }
    values.update(updates)
    return ArtifactReference(**values)


def _object_key(reference: ArtifactReference) -> str:
    return hashlib.sha256(
        reference.pointer.to_canonical_json().encode("utf-8")
    ).hexdigest()


def _object_directory(root: Path, reference: ArtifactReference) -> Path:
    key = _object_key(reference)
    return root / "objects" / key[:2] / key


def _repository_with_object(
    tmp_path: Path,
    *,
    payload: bytes = b"\x00molecular\xffpayload\n",
    reference: ArtifactReference | None = None,
) -> tuple[MolecularArtifactRepository, ArtifactReference, Path]:
    repository = MolecularArtifactRepository(tmp_path / "repository")
    repository.start()
    selected_reference = reference or _reference(payload)
    repository.put(selected_reference, payload)
    return (
        repository,
        selected_reference,
        _object_directory(tmp_path / "repository", selected_reference),
    )


def _create_symlink(
    link: Path,
    target: Path,
    *,
    target_is_directory: bool,
) -> None:
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"Symbolic links are unavailable on this platform: {exc}")


def _tree_snapshot(root: Path) -> tuple[tuple[str, int, int, int, str | None], ...]:
    entries: list[tuple[str, int, int, int, str | None]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        metadata = path.lstat()
        digest = (
            hashlib.sha256(path.read_bytes()).hexdigest()
            if stat.S_ISREG(metadata.st_mode)
            else None
        )
        entries.append(
            (
                path.relative_to(root).as_posix(),
                metadata.st_mode,
                metadata.st_size,
                metadata.st_mtime_ns,
                digest,
            )
        )
    return tuple(entries)


def test_root_creation_normal_lifecycle_and_idempotent_start(
    tmp_path: Path,
) -> None:
    root = tmp_path / "new-repository"
    repository = MolecularArtifactRepository(root)

    assert not root.exists()
    repository.start()
    repository.start()

    assert root.is_dir()
    assert (root / "objects").is_dir()
    repository.close()
    assert root.is_dir()
    assert (root / "objects").is_dir()


def test_operations_are_rejected_before_start_and_after_close(
    tmp_path: Path,
) -> None:
    payload = b"lifecycle"
    reference = _reference(payload)
    repository = MolecularArtifactRepository(tmp_path / "repository")

    with pytest.raises(MolecularArtifactRepositoryError, match="not started"):
        repository.put(reference, payload)
    with pytest.raises(MolecularArtifactRepositoryError, match="not started"):
        repository.read(reference)

    repository.start()
    repository.close()

    with pytest.raises(MolecularArtifactRepositoryError, match="not started"):
        repository.put(reference, payload)
    with pytest.raises(MolecularArtifactRepositoryError, match="not started"):
        repository.read(reference)


def test_root_resolution_failure_is_controlled_and_path_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    repository = MolecularArtifactRepository(root)
    original_resolve = Path.resolve

    def fail_configured_root_resolution(
        path: Path,
        *,
        strict: bool = False,
    ) -> Path:
        if path == root:
            raise OSError(f"simulated resolution failure for {root}")
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", fail_configured_root_resolution)

    with pytest.raises(MolecularArtifactRepositoryError) as captured:
        repository.start()

    assert str(root) not in str(captured.value)
    with pytest.raises(MolecularArtifactRepositoryError, match="not started"):
        repository.read(_reference(b"repository-remains-stopped"))


def test_objects_directory_create_race_inspection_failure_is_controlled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    objects = root / "objects"
    repository = MolecularArtifactRepository(root)
    original_lstat = Path.lstat
    original_mkdir = Path.mkdir
    objects_lstat_calls = 0

    def race_lstat(path: Path) -> Any:
        nonlocal objects_lstat_calls
        if path == objects:
            objects_lstat_calls += 1
            if objects_lstat_calls == 1:
                raise FileNotFoundError("simulated missing objects directory")
            raise OSError(f"simulated inspection failure for {objects}")
        return original_lstat(path)

    def race_mkdir(path: Path, *args: Any, **kwargs: Any) -> None:
        if path == objects:
            raise FileExistsError("simulated objects-directory create race")
        original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", race_lstat)
    monkeypatch.setattr(Path, "mkdir", race_mkdir)

    with pytest.raises(MolecularArtifactRepositoryError) as captured:
        repository.start()

    assert str(objects) not in str(captured.value)


def test_shard_create_race_inspection_failure_is_controlled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"shard-create-race"
    reference = _reference(payload)
    repository = MolecularArtifactRepository(tmp_path / "repository")
    repository.start()
    key = _object_key(reference)
    shard = tmp_path / "repository" / "objects" / key[:2]
    original_lstat = Path.lstat
    original_mkdir = Path.mkdir
    shard_lstat_calls = 0

    def race_lstat(path: Path) -> Any:
        nonlocal shard_lstat_calls
        if path == shard:
            shard_lstat_calls += 1
            if shard_lstat_calls == 1:
                raise FileNotFoundError("simulated missing shard")
            raise OSError(f"simulated inspection failure for {shard}")
        return original_lstat(path)

    def race_mkdir(path: Path, *args: Any, **kwargs: Any) -> None:
        if path == shard:
            raise FileExistsError("simulated shard create race")
        original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", race_lstat)
    monkeypatch.setattr(Path, "mkdir", race_mkdir)

    with pytest.raises(MolecularArtifactIntegrityError) as captured:
        repository.put(reference, payload)

    assert str(shard) not in str(captured.value)


def test_temporary_directory_creation_failure_is_controlled_and_unpublished(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"temporary-directory-create-failure"
    reference = _reference(payload)
    repository = MolecularArtifactRepository(tmp_path / "repository")
    repository.start()
    key = _object_key(reference)
    shard = tmp_path / "repository" / "objects" / key[:2]
    object_directory = shard / key

    def fail_temporary_directory_creation(
        **arguments: Any,
    ) -> str:
        raise OSError(
            "simulated temporary-directory failure in "
            f"{arguments['dir']}/{arguments['prefix']}"
        )

    monkeypatch.setattr(
        "cgr.molecular.artifact_repository.tempfile.mkdtemp",
        fail_temporary_directory_creation,
    )

    with pytest.raises(MolecularArtifactRepositoryError) as captured:
        repository.put(reference, payload)

    assert str(tmp_path) not in str(captured.value)
    assert shard.is_dir()
    assert list(shard.iterdir()) == []
    assert not object_directory.exists()


def test_exact_binary_round_trip_and_storage_location_preservation(
    tmp_path: Path,
) -> None:
    payload = bytes(range(256)) + b"\x00\xff"
    reference = _reference(
        payload,
        storage_location="artifact://remote/molecular-object",
    )
    repository = MolecularArtifactRepository(tmp_path / "repository")
    repository.start()

    returned = repository.put(reference, payload)

    assert returned is reference
    assert returned.storage_location == "artifact://remote/molecular-object"
    assert repository.read(reference) == payload
    assert reference.storage_location == "artifact://remote/molecular-object"


def test_exact_topology_json_payload_round_trip(tmp_path: Path) -> None:
    topology = MolecularTopologyIndex(
        schema_version=VERSION,
        structure_identifier="structure-topology",
        source_structure=ArtifactPointer(
            artifact_identifier="artifact-source",
            content_sha256="a" * 64,
        ),
        structure_format="xyz",
        parser_identifier="pulsate.xyz",
        parser_version="1.0.0",
        atoms=(
            MolecularTopologyAtom(
                atom_identifier="atom-0",
                atom_index=0,
                element_symbol="C",
            ),
        ),
        model_count=1,
        frame_count=1,
        coordinate_unit="angstrom",
    )
    payload = topology.to_canonical_json().encode("utf-8")
    reference = _reference(
        payload,
        artifact_identifier="artifact-topology",
        artifact_type="molecular_topology",
        media_type="application/vnd.pulsate.molecular-topology+json",
    )
    repository = MolecularArtifactRepository(tmp_path / "repository")
    repository.start()

    repository.put(reference, payload)

    assert repository.read(reference) == payload


def test_idempotent_second_put_does_not_mutate_object(tmp_path: Path) -> None:
    payload = b"idempotent-object"
    repository, reference, object_directory = _repository_with_object(
        tmp_path,
        payload=payload,
    )
    before = _tree_snapshot(object_directory)

    returned = repository.put(reference, payload)

    assert returned is reference
    assert _tree_snapshot(object_directory) == before


def test_repeated_independent_artifact_publication(tmp_path: Path) -> None:
    repository = MolecularArtifactRepository(tmp_path / "repository")
    repository.start()
    published: list[tuple[ArtifactReference, bytes]] = []
    for index in range(32):
        payload = f"independent-artifact-{index}".encode()
        reference = _reference(
            payload,
            artifact_identifier=f"artifact-independent-{index}",
        )
        repository.put(reference, payload)
        published.append((reference, payload))

    for reference, payload in published:
        assert repository.read(reference) == payload


def test_concurrent_identical_artifact_publication_is_idempotent(
    tmp_path: Path,
) -> None:
    payload = b"concurrent-identical-artifact"
    reference = _reference(payload)
    repository = MolecularArtifactRepository(tmp_path / "repository")
    repository.start()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(lambda _: repository.put(reference, payload), range(2)))

    assert results == (reference, reference)
    assert repository.read(reference) == payload


def test_concurrent_conflicting_artifact_publication_fails_closed(
    tmp_path: Path,
) -> None:
    payload = b"concurrent-conflicting-artifact"
    original = _reference(payload, metadata={"variant": "original"})
    conflicting = _replace_reference(
        original,
        media_type="application/x-conflicting",
        metadata={"variant": "conflicting"},
    )
    repository = MolecularArtifactRepository(tmp_path / "repository")
    repository.start()
    repository.put(original, payload)

    def publish(
        reference: ArtifactReference,
    ) -> type[MolecularArtifactConflictError] | None:
        try:
            repository.put(reference, payload)
        except MolecularArtifactConflictError as error:
            return type(error)
        return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(publish, (original, conflicting)))

    assert results == (None, MolecularArtifactConflictError)
    assert repository.read(original) == payload


def test_failed_publication_removes_only_its_temporary_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"controlled-publication-failure"
    reference = _reference(payload)
    repository = MolecularArtifactRepository(tmp_path / "repository")
    repository.start()
    original_write = repository._write_fsynced

    def fail_reference_write(path: Path, content: bytes) -> None:
        if path.name == "reference.json":
            raise OSError("controlled test failure")
        original_write(path, content)

    monkeypatch.setattr(repository, "_write_fsynced", fail_reference_write)

    with pytest.raises(
        MolecularArtifactRepositoryError,
        match="publication failed safely",
    ):
        repository.put(reference, payload)

    key = _object_key(reference)
    shard = tmp_path / "repository" / "objects" / key[:2]
    assert shard.is_dir()
    assert list(shard.iterdir()) == []


def test_failed_rename_exposes_only_path_free_internal_diagnostic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"diagnostic-artifact-publication"
    reference = _reference(payload)
    repository = MolecularArtifactRepository(tmp_path / "private-repository")
    repository.start()
    error = PermissionError(13, "Access is denied")
    error.winerror = 5  # type: ignore[attr-defined]

    def fail_rename(source: Path, destination: Path) -> None:
        raise error

    monkeypatch.setattr(
        artifact_repository_module,
        "_rename_directory_no_replace",
        fail_rename,
    )
    with pytest.raises(MolecularArtifactRepositoryError) as captured:
        repository.put(reference, payload)

    diagnostic = captured.value.__cause__
    assert diagnostic is not None
    assert diagnostic.exception_type == "PermissionError"
    assert diagnostic.errno == 13
    assert diagnostic.winerror == 5
    assert diagnostic.source_exists is True
    assert diagnostic.destination_exists is False
    assert diagnostic.temporary_entries == ("payload.bin", "reference.json")
    assert diagnostic.writers_closed is True
    assert str(tmp_path) not in str(captured.value)
    assert str(tmp_path) not in str(diagnostic)


@pytest.mark.parametrize(
    ("reference", "payload", "message"),
    [
        (
            _reference(b"expected", content_sha256="0" * 64),
            b"expected",
            "hash",
        ),
        (
            _reference(b"expected", byte_size=9),
            b"expected",
            "byte size",
        ),
        (
            _reference(b"expected", byte_size=None),
            b"expected",
            "byte size",
        ),
    ],
)
def test_invalid_payload_evidence_is_rejected_before_writing(
    tmp_path: Path,
    reference: ArtifactReference,
    payload: bytes,
    message: str,
) -> None:
    repository = MolecularArtifactRepository(tmp_path / "repository")
    repository.start()

    with pytest.raises(MolecularArtifactRepositoryError, match=message):
        repository.put(reference, payload)

    assert list((tmp_path / "repository" / "objects").iterdir()) == []


def test_put_requires_exact_bytes_type(tmp_path: Path) -> None:
    payload = b"bytes-only"
    reference = _reference(payload)
    repository = MolecularArtifactRepository(tmp_path / "repository")
    repository.start()

    with pytest.raises(MolecularArtifactRepositoryError, match="must be bytes"):
        repository.put(reference, bytearray(payload))  # type: ignore[arg-type]


def test_missing_object_raises_not_found(tmp_path: Path) -> None:
    payload = b"missing"
    reference = _reference(payload)
    repository = MolecularArtifactRepository(tmp_path / "repository")
    repository.start()

    with pytest.raises(MolecularArtifactNotFoundError):
        repository.read(reference)


def test_same_pointer_with_different_complete_reference_conflicts(
    tmp_path: Path,
) -> None:
    payload = b"conflict"
    original = _reference(payload, metadata={"variant": "original"})
    conflicting = _replace_reference(
        original,
        media_type="application/x-conflicting",
        metadata={"variant": "conflicting"},
    )
    repository = MolecularArtifactRepository(tmp_path / "repository")
    repository.start()
    repository.put(original, payload)

    with pytest.raises(MolecularArtifactConflictError):
        repository.put(conflicting, payload)

    assert repository.read(original) == payload


@pytest.mark.parametrize(
    "replacement",
    [
        b"{malformed",
        json.dumps(["not", "an", "object"]).encode("utf-8"),
    ],
)
def test_malformed_or_non_object_reference_json_fails_closed(
    tmp_path: Path,
    replacement: bytes,
) -> None:
    repository, reference, object_directory = _repository_with_object(tmp_path)
    (object_directory / "reference.json").write_bytes(replacement)

    with pytest.raises(MolecularArtifactIntegrityError):
        repository.read(reference)


def test_oversized_reference_json_fails_closed(tmp_path: Path) -> None:
    repository, reference, object_directory = _repository_with_object(tmp_path)
    (object_directory / "reference.json").write_bytes(
        b"{" + b" " * (1024 * 1024) + b"}"
    )

    with pytest.raises(MolecularArtifactIntegrityError, match="size limit"):
        repository.read(reference)


def test_persisted_reference_identity_mismatch_fails_closed(
    tmp_path: Path,
) -> None:
    repository, reference, object_directory = _repository_with_object(tmp_path)
    conflicting = _replace_reference(
        reference,
        media_type="application/x-conflicting",
    )
    (object_directory / "reference.json").write_bytes(
        conflicting.to_canonical_json().encode("utf-8")
    )

    with pytest.raises(MolecularArtifactIntegrityError, match="does not match"):
        repository.read(reference)


def test_truncated_payload_fails_closed(tmp_path: Path) -> None:
    repository, reference, object_directory = _repository_with_object(tmp_path)
    payload_path = object_directory / "payload.bin"
    payload_path.write_bytes(payload_path.read_bytes()[:-1])

    with pytest.raises(MolecularArtifactIntegrityError, match="size"):
        repository.read(reference)


def test_same_size_payload_tampering_fails_sha_validation(tmp_path: Path) -> None:
    repository, reference, object_directory = _repository_with_object(tmp_path)
    payload_path = object_directory / "payload.bin"
    original = payload_path.read_bytes()
    payload_path.write_bytes(bytes([original[0] ^ 0xFF]) + original[1:])

    with pytest.raises(MolecularArtifactIntegrityError, match="hash"):
        repository.read(reference)


@pytest.mark.parametrize("filename", ["reference.json", "payload.bin"])
def test_missing_object_file_fails_closed(
    tmp_path: Path,
    filename: str,
) -> None:
    repository, reference, object_directory = _repository_with_object(tmp_path)
    (object_directory / filename).unlink()

    with pytest.raises(MolecularArtifactIntegrityError, match="incomplete"):
        repository.read(reference)


def test_symlinked_configured_root_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    configured_root = tmp_path / "repository"
    _create_symlink(configured_root, target, target_is_directory=True)

    with pytest.raises(MolecularArtifactRepositoryError, match="normal directory"):
        MolecularArtifactRepository(configured_root).start()


def test_existing_non_directory_root_is_rejected(tmp_path: Path) -> None:
    configured_root = tmp_path / "repository"
    configured_root.write_bytes(b"not-a-directory")

    with pytest.raises(MolecularArtifactRepositoryError, match="normal directory"):
        MolecularArtifactRepository(configured_root).start()


def test_symlinked_objects_directory_is_rejected(tmp_path: Path) -> None:
    configured_root = tmp_path / "repository"
    configured_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    _create_symlink(
        configured_root / "objects",
        outside,
        target_is_directory=True,
    )

    with pytest.raises(MolecularArtifactRepositoryError, match="objects root"):
        MolecularArtifactRepository(configured_root).start()


def test_symlinked_shard_is_rejected(tmp_path: Path) -> None:
    repository, reference, object_directory = _repository_with_object(tmp_path)
    shard = object_directory.parent
    saved_shard = tmp_path / "saved-shard"
    publication_module._rename_directory_no_replace(shard, saved_shard)
    _create_symlink(shard, saved_shard, target_is_directory=True)

    with pytest.raises(MolecularArtifactIntegrityError, match="shard"):
        repository.read(reference)


def test_symlinked_object_directory_is_rejected(tmp_path: Path) -> None:
    repository, reference, object_directory = _repository_with_object(tmp_path)
    saved_object = tmp_path / "saved-object"
    publication_module._rename_directory_no_replace(object_directory, saved_object)
    _create_symlink(
        object_directory,
        saved_object,
        target_is_directory=True,
    )

    with pytest.raises(MolecularArtifactIntegrityError, match="object"):
        repository.read(reference)


@pytest.mark.parametrize("filename", ["reference.json", "payload.bin"])
def test_symlinked_object_file_is_rejected(
    tmp_path: Path,
    filename: str,
) -> None:
    repository, reference, object_directory = _repository_with_object(tmp_path)
    path = object_directory / filename
    outside = tmp_path / f"outside-{filename}"
    outside.write_bytes(path.read_bytes())
    path.unlink()
    _create_symlink(path, outside, target_is_directory=False)

    with pytest.raises(MolecularArtifactIntegrityError, match="regular file"):
        repository.read(reference)


@pytest.mark.parametrize("filename", ["reference.json", "payload.bin"])
def test_non_regular_object_file_is_rejected(
    tmp_path: Path,
    filename: str,
) -> None:
    repository, reference, object_directory = _repository_with_object(tmp_path)
    path = object_directory / filename
    path.unlink()
    path.mkdir()

    with pytest.raises(MolecularArtifactIntegrityError, match="regular file"):
        repository.read(reference)


def test_read_performs_no_filesystem_mutation(tmp_path: Path) -> None:
    repository, reference, _ = _repository_with_object(tmp_path)
    repository_root = tmp_path / "repository"
    before = _tree_snapshot(repository_root)

    assert repository.read(reference)

    assert _tree_snapshot(repository_root) == before


def test_raw_legal_path_characters_never_become_path_components(
    tmp_path: Path,
) -> None:
    payload = b"opaque-layout"
    raw_identifier = "namespace:project/artifact"
    reference = _reference(
        payload,
        artifact_identifier=raw_identifier,
        artifact_type="molecular:structure/type",
        metadata={"source": "namespace:metadata/value"},
    )
    repository = MolecularArtifactRepository(tmp_path / "repository")
    repository.start()
    repository.put(reference, payload)

    object_directory = _object_directory(tmp_path / "repository", reference)
    relative_parts = object_directory.relative_to(tmp_path / "repository").parts
    assert relative_parts[0] == "objects"
    assert len(relative_parts[1]) == 2
    assert len(relative_parts[2]) == 64
    assert all(character in "0123456789abcdef" for character in relative_parts[2])
    assert raw_identifier not in relative_parts
    assert repository.read(reference) == payload


def test_no_absolute_path_is_inserted_or_exposed(tmp_path: Path) -> None:
    payload = b"portable-reference"
    reference = _reference(payload, storage_location=None)
    repository = MolecularArtifactRepository(tmp_path / "repository")
    repository.start()

    returned = repository.put(reference, payload)

    assert returned is reference
    assert returned.storage_location is None
    assert str(tmp_path) not in returned.to_canonical_json()

    missing_reference = _reference(
        b"missing-portable-reference",
        artifact_identifier="artifact-missing",
    )
    with pytest.raises(MolecularArtifactNotFoundError) as captured:
        repository.read(missing_reference)
    assert str(tmp_path) not in str(captured.value)
