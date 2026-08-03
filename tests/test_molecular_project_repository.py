from __future__ import annotations

import hashlib
import inspect
import json
import shutil
import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Callable
from pathlib import Path

import pytest

import cgr.molecular._publication as publication_module
import cgr.molecular.project_repository as repository_module
from cgr.kernel.contracts import CapabilityVersion
from cgr.molecular import (
    MolecularComponent,
    MolecularMemberReference,
    MolecularProject,
    MolecularProjectConflictError,
    MolecularProjectIntegrityError,
    MolecularProjectNotFoundError,
    MolecularProjectRepository,
    MolecularProjectRepositoryError,
    MolecularRegion,
    MolecularSceneManifest,
    MolecularSelection,
    MolecularStructure,
    MolecularSystem,
)
from cgr.science import (
    ArtifactLineageEdge,
    ArtifactLineageGraph,
    ArtifactReference,
    CreationProvenance,
)

_VERSION = CapabilityVersion(major=1, minor=0, patch=0)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _project(
    project_identifier: str = "project/universal:1",
    *,
    label: str = "Complete molecular project",
) -> MolecularProject:
    provenance = CreationProvenance(
        producer="project-repository-tests",
        producer_version=_VERSION,
        execution_identifier="execution-project-repository",
        source="test-suite",
    )
    structure_artifact = ArtifactReference(
        artifact_identifier="artifact/structure:1",
        schema_version=_VERSION,
        artifact_type="molecular_structure",
        media_type="chemical/x-mmcif",
        content_sha256=_sha256("structure"),
        byte_size=8192,
        storage_location="artifact://molecular/structure",
        metadata={"description": "protein, ligand & solvent #1"},
        provenance=provenance,
    )
    topology_artifact = ArtifactReference(
        artifact_identifier="artifact/topology:1",
        schema_version=_VERSION,
        artifact_type="molecular_topology",
        media_type="application/json",
        content_sha256=_sha256("topology"),
        byte_size=4096,
        storage_location="artifact://molecular/topology",
        provenance=provenance,
    )
    structure = MolecularStructure(
        structure_identifier="structure/alpha:1",
        system_identifier="system/complex:1",
        coordinate_unit="angstrom",
        structure_artifact=structure_artifact,
        topology_artifact=topology_artifact,
        atom_count=120_000,
        bond_count=121_000,
        residue_count=680,
        chain_count=4,
        model_count=1,
        frame_count=250,
    )
    selection = MolecularSelection(
        selection_identifier="selection/pocket:1",
        structure_identifier=structure.structure_identifier,
        label="Pocket selection",
        selection_source="imported",
        members=(
            MolecularMemberReference(
                member_kind="atom",
                member_identifier="atom/42:CA",
            ),
            MolecularMemberReference(
                member_kind="chain",
                member_identifier="chain/A:1",
            ),
        ),
    )
    region = MolecularRegion(
        region_identifier="region/pocket:1",
        structure_identifier=structure.structure_identifier,
        region_type="binding_pocket",
        selection_identifier=selection.selection_identifier,
    )
    scene = MolecularSceneManifest(
        scene_identifier="scene/complex:1",
        schema_version=_VERSION,
        project_identifier=project_identifier,
        structure_identifiers=(structure.structure_identifier,),
        primary_structure_identifier=structure.structure_identifier,
        selection_identifiers=(selection.selection_identifier,),
        region_identifiers=(region.region_identifier,),
        artifact_references=(topology_artifact, structure_artifact),
    )
    lineage = ArtifactLineageGraph(
        edges=(
            ArtifactLineageEdge(
                source=structure_artifact.pointer,
                destination=topology_artifact.pointer,
                relationship_type="declares_topology",
                producing_capability="molecular-import",
                producing_capability_version=_VERSION,
                execution_identifier="execution-project-repository",
            ),
        )
    )
    return MolecularProject(
        project_identifier=project_identifier,
        schema_version=_VERSION,
        systems=(
            MolecularSystem(
                system_identifier=structure.system_identifier,
                label=label,
                structure_identifiers=(structure.structure_identifier,),
                default_structure_identifier=structure.structure_identifier,
            ),
        ),
        structures=(structure,),
        components=(
            MolecularComponent(
                component_identifier="component/ligand:1",
                structure_identifier=structure.structure_identifier,
                component_type="ligand",
                label="Bound ligand",
                members=(
                    MolecularMemberReference(
                        member_kind="atom",
                        member_identifier="atom/42:CA",
                    ),
                ),
            ),
        ),
        selections=(selection,),
        regions=(region,),
        scenes=(scene,),
        provenance=provenance,
        lineage=lineage,
    )


def _object_paths(root: Path, project_identifier: str) -> tuple[Path, Path, Path]:
    digest = _sha256(project_identifier)
    prefix = root / "projects" / digest[:2]
    directory = prefix / digest
    return prefix, directory / "record.json", directory / "project.json"


def _snapshot(root: Path) -> dict[str, tuple[str, bytes | None, int]]:
    snapshot: dict[str, tuple[str, bytes | None, int]] = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        metadata = path.lstat()
        relative = path.relative_to(root).as_posix()
        if path.is_file() and not path.is_symlink():
            snapshot[relative] = ("file", path.read_bytes(), metadata.st_mtime_ns)
        elif path.is_dir() and not path.is_symlink():
            snapshot[relative] = ("directory", None, metadata.st_mtime_ns)
        else:
            snapshot[relative] = ("other", None, metadata.st_mtime_ns)
    return snapshot


def _record_for(identifier: str, project_bytes: bytes) -> dict[str, object]:
    return {
        "identifier_sha256": _sha256(identifier),
        "project_byte_size": len(project_bytes),
        "project_identifier": identifier,
        "project_sha256": hashlib.sha256(project_bytes).hexdigest(),
        "record_format": "cgr.molecular-project-record.v1",
    }


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _replace_evidence(
    root: Path,
    identifier: str,
    project_bytes: bytes,
    *,
    record: dict[str, object] | None = None,
) -> None:
    _, record_path, project_path = _object_paths(root, identifier)
    project_path.write_bytes(project_bytes)
    record_path.write_bytes(
        _canonical_json_bytes(record or _record_for(identifier, project_bytes))
    )


def _started_repository(root: Path) -> MolecularProjectRepository:
    repository = MolecularProjectRepository(root)
    repository.start()
    return repository


def _make_symlink(link: Path, target: Path, *, directory: bool) -> None:
    try:
        link.symlink_to(target, target_is_directory=directory)
    except (NotImplementedError, OSError) as error:
        pytest.skip(f"The platform cannot create the required symlink: {error}")


def test_public_exports_and_error_hierarchy() -> None:
    import cgr.molecular as molecular

    assert molecular.MolecularProjectRepository is MolecularProjectRepository
    assert issubclass(MolecularProjectRepositoryError, ValueError)
    assert issubclass(
        MolecularProjectNotFoundError, MolecularProjectRepositoryError
    )
    assert issubclass(
        MolecularProjectIntegrityError, MolecularProjectRepositoryError
    )
    assert issubclass(
        MolecularProjectConflictError, MolecularProjectRepositoryError
    )


@pytest.mark.parametrize("operation", ["put", "read", "resolve"])
def test_explicit_lifecycle_is_required(tmp_path: Path, operation: str) -> None:
    repository = MolecularProjectRepository(tmp_path / "repository")

    with pytest.raises(MolecularProjectRepositoryError):
        if operation == "put":
            repository.put(_project())
        else:
            getattr(repository, operation)("project/universal:1")


def test_start_and_close_are_idempotent(tmp_path: Path) -> None:
    repository = MolecularProjectRepository(tmp_path / "repository")
    repository.start()
    repository.start()
    repository.close()
    repository.close()
    with pytest.raises(MolecularProjectRepositoryError):
        repository.read("project/universal:1")
    repository.start()
    assert repository.resolve("project/universal:1") is None


def test_new_repository_is_created_and_recovered_by_another_instance(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    project = _project()
    writer = _started_repository(root)
    writer.put(project)
    writer.close()

    reader = _started_repository(root)
    assert reader.read(project.project_identifier) == project


def test_canonical_round_trip_preserves_complete_project(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    project = _project()
    repository = _started_repository(root)
    repository.put(project)

    loaded = repository.read(project.project_identifier)
    _, _, project_path = _object_paths(root, project.project_identifier)
    assert loaded == project
    assert project_path.read_bytes() == project.to_canonical_json().encode("utf-8")
    assert loaded.systems == project.systems
    assert loaded.structures == project.structures
    assert loaded.components == project.components
    assert loaded.selections == project.selections
    assert loaded.regions == project.regions
    assert loaded.scenes == project.scenes
    assert loaded.provenance == project.provenance
    assert loaded.lineage == project.lineage
    assert loaded.scenes[0].artifact_references == project.scenes[0].artifact_references


def test_raw_identifiers_never_become_path_components(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    project = _project("project/with:delimiters")
    repository = _started_repository(root)
    repository.put(project)

    relative_paths = [path.relative_to(root).parts for path in root.rglob("*")]
    flattened = {component for parts in relative_paths for component in parts}
    digest = _sha256(project.project_identifier)
    assert "project" not in flattened
    assert "with:delimiters" not in flattened
    assert digest[:2] in flattened
    assert digest in flattened
    assert all(
        len(component) in {2, 64}
        and set(component) <= set("0123456789abcdef")
        for parts in relative_paths
        for component in parts[1:-1]
    )


@pytest.mark.parametrize(
    "identifier",
    ["project/one:two", "space value", "value%25", "value?x", "value#x", "a&b"],
)
def test_lookup_identifiers_are_hashed_without_path_creation(
    tmp_path: Path,
    identifier: str,
) -> None:
    root = tmp_path / "repository"
    repository = _started_repository(root)
    before = _snapshot(root)
    assert repository.resolve(identifier) is None
    assert _snapshot(root) == before


def test_identical_put_and_read_do_not_change_filesystem_snapshot(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    project = _project()
    repository = _started_repository(root)
    repository.put(project)
    original = _snapshot(root)

    repository.put(project)
    assert _snapshot(root) == original
    assert repository.read(project.project_identifier) == project
    assert _snapshot(root) == original


def test_conflict_preserves_original_evidence(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    repository = _started_repository(root)
    original = _project()
    conflicting = _project(label="Changed project label")
    repository.put(original)
    before = _snapshot(root)

    with pytest.raises(MolecularProjectConflictError):
        repository.put(conflicting)

    assert _snapshot(root) == before
    assert repository.read(original.project_identifier) == original


def test_missing_and_resolver_semantics(tmp_path: Path) -> None:
    repository = _started_repository(tmp_path / "repository")
    with pytest.raises(MolecularProjectNotFoundError):
        repository.read("missing/project:1")
    assert repository.resolve("missing/project:1") is None

    project = _project()
    repository.put(project)
    assert repository.resolve(project.project_identifier) == project


def test_resolve_propagates_integrity_failure(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    project = _project()
    repository = _started_repository(root)
    repository.put(project)
    _, record_path, _ = _object_paths(root, project.project_identifier)
    record_path.write_bytes(b"not-json")

    with pytest.raises(MolecularProjectIntegrityError):
        repository.resolve(project.project_identifier)


def test_different_identifiers_store_independently(tmp_path: Path) -> None:
    repository = _started_repository(tmp_path / "repository")
    projects = tuple(_project(f"project/independent:{index}") for index in range(32))
    for project in projects:
        repository.put(project)
    for project in projects:
        assert repository.read(project.project_identifier) == project


def test_concurrent_identical_project_publication_is_idempotent(
    tmp_path: Path,
) -> None:
    repository = _started_repository(tmp_path / "repository")
    project = _project()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(lambda _: repository.put(project), range(2)))

    assert results == (None, None)
    assert repository.read(project.project_identifier) == project


def test_concurrent_conflicting_project_publication_fails_closed(
    tmp_path: Path,
) -> None:
    repository = _started_repository(tmp_path / "repository")
    original = _project()
    conflicting = _project(label="Concurrent conflicting project")
    repository.put(original)

    def publish(
        project: MolecularProject,
    ) -> type[MolecularProjectConflictError] | None:
        try:
            repository.put(project)
        except MolecularProjectConflictError as error:
            return type(error)
        return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(pool.map(publish, (original, conflicting)))

    assert results == (None, MolecularProjectConflictError)
    assert repository.read(original.project_identifier) == original


@pytest.mark.parametrize("invalid", [None, {}, "project", 42])
def test_non_project_values_are_rejected(tmp_path: Path, invalid: object) -> None:
    repository = _started_repository(tmp_path / "repository")
    with pytest.raises(MolecularProjectIntegrityError):
        repository.put(invalid)  # type: ignore[arg-type]


def test_constructed_invalid_project_is_revalidated(tmp_path: Path) -> None:
    repository = _started_repository(tmp_path / "repository")
    invalid = _project().model_copy(update={"project_identifier": "space value"})
    with pytest.raises(MolecularProjectIntegrityError):
        repository.put(invalid)


def test_oversized_project_write_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project()
    monkeypatch.setattr(
        repository_module,
        "_PROJECT_JSON_MAXIMUM_BYTES",
        len(project.to_canonical_json().encode("utf-8")) - 1,
    )
    repository = _started_repository(tmp_path / "repository")
    with pytest.raises(MolecularProjectIntegrityError, match="size limit"):
        repository.put(project)


@pytest.mark.parametrize("target", ["record", "project"])
def test_oversized_reads_are_rejected_before_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
) -> None:
    root = tmp_path / "repository"
    project = _project()
    repository = _started_repository(root)
    repository.put(project)
    _, record_path, project_path = _object_paths(root, project.project_identifier)
    selected = record_path if target == "record" else project_path
    limit_name = (
        "_RECORD_JSON_MAXIMUM_BYTES"
        if target == "record"
        else "_PROJECT_JSON_MAXIMUM_BYTES"
    )
    monkeypatch.setattr(repository_module, limit_name, selected.stat().st_size - 1)
    original_open = repository_module.os.open

    def guarded_open(path: object, *args: object, **kwargs: object) -> int:
        if Path(path) == selected:
            raise AssertionError("oversized evidence was opened")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(repository_module.os, "open", guarded_open)
    with pytest.raises(MolecularProjectIntegrityError, match="size limit"):
        repository.read(project.project_identifier)


@pytest.mark.parametrize("target", ["record", "project"])
def test_zero_length_files_are_rejected(tmp_path: Path, target: str) -> None:
    root = tmp_path / "repository"
    project = _project()
    repository = _started_repository(root)
    repository.put(project)
    _, record_path, project_path = _object_paths(root, project.project_identifier)
    (record_path if target == "record" else project_path).write_bytes(b"")
    with pytest.raises(MolecularProjectIntegrityError, match="empty"):
        repository.read(project.project_identifier)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"\xff", "malformed"),
        (b"{", "malformed"),
        (b"[]", "JSON object"),
        (_canonical_json_bytes({"project_identifier": "invalid"}), "validation"),
    ],
)
def test_invalid_project_documents_are_rejected(
    tmp_path: Path,
    payload: bytes,
    message: str,
) -> None:
    root = tmp_path / "repository"
    project = _project()
    repository = _started_repository(root)
    repository.put(project)
    _replace_evidence(root, project.project_identifier, payload)
    with pytest.raises(MolecularProjectIntegrityError, match=message):
        repository.read(project.project_identifier)


def test_noncanonical_project_document_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    project = _project()
    repository = _started_repository(root)
    repository.put(project)
    pretty = json.dumps(project.model_dump(mode="json"), indent=2).encode("utf-8")
    _replace_evidence(root, project.project_identifier, pretty)
    with pytest.raises(MolecularProjectIntegrityError, match="not canonical"):
        repository.read(project.project_identifier)


def test_noncanonical_record_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    project = _project()
    repository = _started_repository(root)
    repository.put(project)
    _, record_path, project_path = _object_paths(root, project.project_identifier)
    record = _record_for(project.project_identifier, project_path.read_bytes())
    record_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    with pytest.raises(MolecularProjectIntegrityError, match="not canonical"):
        repository.read(project.project_identifier)


@pytest.mark.parametrize("missing", ["record", "project"])
def test_missing_evidence_file_is_rejected(tmp_path: Path, missing: str) -> None:
    root = tmp_path / "repository"
    project = _project()
    repository = _started_repository(root)
    repository.put(project)
    _, record_path, project_path = _object_paths(root, project.project_identifier)
    (record_path if missing == "record" else project_path).unlink()
    with pytest.raises(MolecularProjectIntegrityError, match="incomplete"):
        repository.read(project.project_identifier)


@pytest.mark.parametrize(
    "mutator",
    [
        lambda value: value.pop("project_sha256"),
        lambda value: value.update({"extra": "value"}),
        lambda value: value.update({"project_byte_size": "1"}),
        lambda value: value.update({"record_format": "other.v1"}),
        lambda value: value.update({"project_identifier": "other/project:1"}),
        lambda value: value.update({"identifier_sha256": "0" * 64}),
        lambda value: value.update({"project_sha256": "0" * 64}),
        lambda value: value.update({"project_byte_size": 1}),
    ],
    ids=[
        "missing-field",
        "extra-field",
        "mistyped-field",
        "wrong-format",
        "wrong-identifier",
        "wrong-identifier-hash",
        "wrong-project-hash",
        "wrong-byte-size",
    ],
)
def test_record_tampering_is_rejected(
    tmp_path: Path,
    mutator: Callable[[dict[str, object]], object],
) -> None:
    root = tmp_path / "repository"
    project = _project()
    repository = _started_repository(root)
    repository.put(project)
    _, record_path, project_path = _object_paths(root, project.project_identifier)
    record = _record_for(project.project_identifier, project_path.read_bytes())
    mutator(record)
    record_path.write_bytes(_canonical_json_bytes(record))
    with pytest.raises(MolecularProjectIntegrityError):
        repository.read(project.project_identifier)


def test_requested_and_document_identifier_mismatch_is_rejected(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    requested = _project()
    other = _project("other/project:1")
    repository = _started_repository(root)
    repository.put(requested)
    other_bytes = other.to_canonical_json().encode("utf-8")
    _replace_evidence(
        root,
        requested.project_identifier,
        other_bytes,
        record=_record_for(requested.project_identifier, other_bytes),
    )
    with pytest.raises(MolecularProjectIntegrityError, match="document identifier"):
        repository.read(requested.project_identifier)


def test_symlinked_configured_root_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "repository"
    _make_symlink(link, target, directory=True)
    repository = MolecularProjectRepository(link)
    with pytest.raises(MolecularProjectRepositoryError):
        repository.start()


def test_regular_file_configured_root_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    root.write_bytes(b"not-a-directory")
    with pytest.raises(MolecularProjectRepositoryError):
        MolecularProjectRepository(root).start()


@pytest.mark.parametrize("entry_kind", ["symlink", "file"])
def test_unsafe_projects_entry_is_rejected(
    tmp_path: Path,
    entry_kind: str,
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    projects = root / "projects"
    if entry_kind == "file":
        projects.write_bytes(b"unsafe")
    else:
        target = tmp_path / "target"
        target.mkdir()
        _make_symlink(projects, target, directory=True)
    with pytest.raises(MolecularProjectRepositoryError):
        MolecularProjectRepository(root).start()


def test_symlinked_prefix_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    project = _project()
    repository = _started_repository(root)
    prefix, _, _ = _object_paths(root, project.project_identifier)
    outside = tmp_path / "outside"
    outside.mkdir()
    _make_symlink(prefix, outside, directory=True)
    with pytest.raises(MolecularProjectIntegrityError):
        repository.read(project.project_identifier)


def test_symlinked_project_directory_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    project = _project()
    repository = _started_repository(root)
    repository.put(project)
    prefix, record_path, _ = _object_paths(root, project.project_identifier)
    directory = record_path.parent
    moved = tmp_path / "moved-project"
    publication_module._rename_directory_no_replace(directory, moved)
    _make_symlink(prefix / directory.name, moved, directory=True)
    with pytest.raises(MolecularProjectIntegrityError):
        repository.read(project.project_identifier)


@pytest.mark.parametrize("target", ["record", "project"])
def test_symlinked_evidence_file_is_rejected(tmp_path: Path, target: str) -> None:
    root = tmp_path / "repository"
    project = _project()
    repository = _started_repository(root)
    repository.put(project)
    _, record_path, project_path = _object_paths(root, project.project_identifier)
    selected = record_path if target == "record" else project_path
    moved = tmp_path / f"moved-{target}"
    publication_module._rename_directory_no_replace(selected, moved)
    _make_symlink(selected, moved, directory=False)
    with pytest.raises(MolecularProjectIntegrityError, match="regular file"):
        repository.read(project.project_identifier)


@pytest.mark.parametrize("target", ["prefix", "project-directory", "record", "project"])
def test_wrong_file_types_are_rejected(tmp_path: Path, target: str) -> None:
    root = tmp_path / "repository"
    project = _project()
    repository = _started_repository(root)
    repository.put(project)
    prefix, record_path, project_path = _object_paths(root, project.project_identifier)
    if target == "prefix":
        shutil.rmtree(prefix)
        prefix.write_bytes(b"wrong")
    elif target == "project-directory":
        shutil.rmtree(record_path.parent)
        record_path.parent.write_bytes(b"wrong")
    else:
        selected = record_path if target == "record" else project_path
        selected.unlink()
        selected.mkdir()
    with pytest.raises(MolecularProjectIntegrityError):
        repository.read(project.project_identifier)


def test_root_resolution_failure_leaves_repository_unstarted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repository"
    original_resolve = Path.resolve

    def failed_resolve(path: Path, strict: bool = False) -> Path:
        if path == root:
            raise OSError("controlled resolution failure")
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", failed_resolve)
    repository = MolecularProjectRepository(root)
    with pytest.raises(MolecularProjectRepositoryError) as error:
        repository.start()
    assert str(root) not in str(error.value)
    with pytest.raises(MolecularProjectRepositoryError, match="not started"):
        repository.put(_project())


def test_publication_failure_leaves_no_partial_object_and_cleans_only_own_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repository"
    project = _project()
    repository = _started_repository(root)
    prefix, record_path, _ = _object_paths(root, project.project_identifier)
    prefix.mkdir()
    historical = prefix / ".historical-keep"
    historical.mkdir()

    def fail_write(path: Path, payload: bytes) -> None:
        raise MolecularProjectRepositoryError("controlled publication failure")

    monkeypatch.setattr(repository, "_write_fsynced", fail_write)
    with pytest.raises(MolecularProjectRepositoryError):
        repository.put(project)
    assert not record_path.parent.exists()
    assert historical.is_dir()
    assert not list(prefix.glob(".project-tmp-*"))


def test_temporary_directory_creation_failure_is_controlled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repository"
    project = _project()
    repository = _started_repository(root)
    _, record_path, _ = _object_paths(root, project.project_identifier)

    def fail_creation(*args: object, **kwargs: object) -> str:
        raise OSError("controlled temporary-directory failure")

    monkeypatch.setattr(repository_module.tempfile, "mkdtemp", fail_creation)
    with pytest.raises(MolecularProjectRepositoryError) as error:
        repository.put(project)
    assert not record_path.parent.exists()
    assert str(root) not in str(error.value)


def test_failed_rename_exposes_only_path_free_internal_diagnostic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "private-repository"
    project = _project("private/project:identifier")
    repository = _started_repository(root)
    error = PermissionError(13, "Access is denied")
    error.winerror = 5  # type: ignore[attr-defined]

    def fail_rename(source: Path, destination: Path) -> None:
        raise error

    monkeypatch.setattr(
        repository_module,
        "_rename_directory_no_replace",
        fail_rename,
    )
    with pytest.raises(MolecularProjectRepositoryError) as captured:
        repository.put(project)

    diagnostic = captured.value.__cause__
    assert diagnostic is not None
    assert diagnostic.exception_type == "PermissionError"
    assert diagnostic.errno == 13
    assert diagnostic.winerror == 5
    assert diagnostic.source_exists is True
    assert diagnostic.destination_exists is False
    assert diagnostic.temporary_entries == ("project.json", "record.json")
    assert diagnostic.writers_closed is True
    assert str(root) not in str(captured.value)
    assert str(root) not in str(diagnostic)


def test_no_replace_helper_publishes_when_destination_is_absent(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    (source / "evidence").write_bytes(b"complete")

    publication_module._rename_directory_no_replace(source, destination)

    assert not source.exists()
    assert (destination / "evidence").read_bytes() == b"complete"


def test_no_replace_helper_preserves_existing_empty_destination(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    (source / "evidence").write_bytes(b"complete")
    destination.mkdir()
    original_identity = destination.stat().st_ino

    with pytest.raises(FileExistsError):
        publication_module._rename_directory_no_replace(source, destination)

    assert source.is_dir()
    assert (source / "evidence").read_bytes() == b"complete"
    assert destination.stat().st_ino == original_identity
    assert list(destination.iterdir()) == []


def test_linux_no_replace_helper_uses_renameat2_and_preserves_eexist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    calls: list[tuple[object, ...]] = []

    class FakeRenameAt2:
        argtypes: object = None
        restype: object = None

        def __call__(self, *args: object) -> int:
            calls.append(args)
            publication_module.ctypes.set_errno(publication_module.errno.EEXIST)
            return -1

    class FakeStandardLibrary:
        renameat2 = FakeRenameAt2()

    monkeypatch.setattr(publication_module.sys, "platform", "linux")
    monkeypatch.setattr(
        publication_module.ctypes,
        "CDLL",
        lambda *args, **kwargs: FakeStandardLibrary(),
    )

    with pytest.raises(FileExistsError) as error:
        publication_module._rename_directory_no_replace(source, destination)

    assert error.value.errno == publication_module.errno.EEXIST
    assert len(calls) == 1
    assert calls[0][-1] == publication_module._RENAME_NOREPLACE
    assert calls[0][0] == publication_module._AT_FDCWD
    assert calls[0][2] == publication_module._AT_FDCWD
    assert source.is_dir()
    assert destination.is_dir()


def test_no_replace_helper_fails_closed_when_primitive_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    monkeypatch.setattr(publication_module.sys, "platform", "linux")
    monkeypatch.setattr(
        publication_module.ctypes,
        "CDLL",
        lambda *args, **kwargs: object(),
    )

    with pytest.raises(OSError, match="unavailable"):
        publication_module._rename_directory_no_replace(source, destination)

    assert source.is_dir()
    assert not destination.exists()


def _windows_access_denied() -> PermissionError:
    error = PermissionError(13, "Access is denied")
    error.winerror = 5  # type: ignore[attr-defined]
    return error


def test_windows_access_denied_is_retried_after_state_revalidation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    (source / "evidence").write_bytes(b"complete")
    attempts = 0
    actual_rename = publication_module.os.rename

    def transient_rename(current: Path, final: Path) -> None:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise _windows_access_denied()
        actual_rename(current, final)

    monkeypatch.setattr(publication_module.sys, "platform", "win32")
    monkeypatch.setattr(publication_module, "_windows_rename", transient_rename)

    publication_module._rename_directory_no_replace(source, destination)

    assert attempts == 3
    assert not source.exists()
    assert (destination / "evidence").read_bytes() == b"complete"


def test_windows_retry_refuses_destination_created_after_transient_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    (source / "evidence").write_bytes(b"complete")
    attempts = 0

    def competing_rename(current: Path, final: Path) -> None:
        nonlocal attempts
        attempts += 1
        final.mkdir()
        raise _windows_access_denied()

    monkeypatch.setattr(publication_module.sys, "platform", "win32")
    monkeypatch.setattr(publication_module, "_windows_rename", competing_rename)

    with pytest.raises(FileExistsError):
        publication_module._rename_directory_no_replace(source, destination)

    assert attempts == 1
    assert source.is_dir()
    assert destination.is_dir()
    assert list(destination.iterdir()) == []


def test_windows_access_denied_exhaustion_remains_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    attempts = 0

    def persistent_rename(current: Path, final: Path) -> None:
        nonlocal attempts
        attempts += 1
        raise _windows_access_denied()

    monkeypatch.setattr(publication_module.sys, "platform", "win32")
    monkeypatch.setattr(publication_module, "_windows_rename", persistent_rename)

    with pytest.raises(PermissionError) as captured:
        publication_module._rename_directory_no_replace(source, destination)

    assert captured.value.winerror == 5
    assert attempts == publication_module._WINDOWS_PUBLICATION_ATTEMPTS
    assert source.is_dir()
    assert not destination.exists()


def test_windows_unobserved_error_is_not_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    attempts = 0
    error = PermissionError(13, "Sharing violation")
    error.winerror = 32  # type: ignore[attr-defined]

    def fail_rename(current: Path, final: Path) -> None:
        nonlocal attempts
        attempts += 1
        raise error

    monkeypatch.setattr(publication_module.sys, "platform", "win32")
    monkeypatch.setattr(publication_module, "_windows_rename", fail_rename)

    with pytest.raises(PermissionError) as captured:
        publication_module._rename_directory_no_replace(source, destination)

    assert captured.value.winerror == 32
    assert attempts == 1
    assert source.is_dir()
    assert not destination.exists()


def test_put_does_not_replace_empty_destination_created_during_race(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repository"
    project = _project()
    repository = _started_repository(root)
    prefix, record_path, _ = _object_paths(root, project.project_identifier)
    actual_no_replace = repository_module._rename_directory_no_replace
    competing_identity: int | None = None

    def create_competing_destination(source: Path, destination: Path) -> None:
        nonlocal competing_identity
        destination.mkdir()
        competing_identity = destination.stat().st_ino
        actual_no_replace(source, destination)

    monkeypatch.setattr(
        repository_module,
        "_rename_directory_no_replace",
        create_competing_destination,
    )
    with pytest.raises(MolecularProjectIntegrityError):
        repository.put(project)

    destination = record_path.parent
    assert competing_identity is not None
    assert destination.stat().st_ino == competing_identity
    assert destination.is_dir()
    assert list(destination.iterdir()) == []
    assert not list(prefix.glob(".project-tmp-*"))


def test_publication_path_does_not_call_plain_os_rename() -> None:
    source = inspect.getsource(MolecularProjectRepository.put)

    assert "os.rename" not in source
    assert "_rename_directory_no_replace" in source


def test_simulated_competing_identical_publication_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repository"
    project = _project()
    repository = _started_repository(root)

    def competing_rename(source: Path, destination: Path) -> None:
        shutil.copytree(source, destination)
        raise FileExistsError("competing writer")

    monkeypatch.setattr(
        repository_module,
        "_rename_directory_no_replace",
        competing_rename,
    )
    repository.put(project)
    assert repository.read(project.project_identifier) == project


def test_simulated_competing_conflicting_publication_raises_conflict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repository"
    project = _project()
    conflicting = _project(label="Competing writer content")
    repository = _started_repository(root)
    conflicting_bytes = conflicting.to_canonical_json().encode("utf-8")

    def competing_rename(source: Path, destination: Path) -> None:
        destination.mkdir()
        (destination / "project.json").write_bytes(conflicting_bytes)
        (destination / "record.json").write_bytes(
            _canonical_json_bytes(
                _record_for(project.project_identifier, conflicting_bytes)
            )
        )
        raise FileExistsError("competing writer")

    monkeypatch.setattr(
        repository_module,
        "_rename_directory_no_replace",
        competing_rename,
    )
    with pytest.raises(MolecularProjectConflictError):
        repository.put(project)


def test_corrupt_preexisting_evidence_is_never_overwritten(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    project = _project()
    repository = _started_repository(root)
    prefix, record_path, _ = _object_paths(root, project.project_identifier)
    prefix.mkdir()
    record_path.parent.mkdir()
    before = _snapshot(root)
    with pytest.raises(MolecularProjectIntegrityError):
        repository.put(project)
    assert _snapshot(root) == before


def test_controlled_errors_are_path_free(tmp_path: Path) -> None:
    root = tmp_path / "private-root-name"
    project = _project("private/project:identifier")
    repository = _started_repository(root)
    repository.put(project)
    _, record_path, _ = _object_paths(root, project.project_identifier)
    record_path.write_bytes(b"bad")
    with pytest.raises(MolecularProjectRepositoryError) as error:
        repository.read(project.project_identifier)
    message = str(error.value)
    assert str(root) not in message
    assert project.project_identifier not in message
    assert "record.json" not in message


def test_repository_has_no_mutation_or_path_api() -> None:
    prohibited = {
        "delete",
        "list",
        "path",
        "remove",
        "rename",
        "replace",
        "update",
    }
    assert prohibited.isdisjoint(vars(MolecularProjectRepository))


def test_operations_do_not_use_network_or_scientific_subprocesses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("external execution was attempted")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    repository = _started_repository(tmp_path / "repository")
    project = _project()
    repository.put(project)
    assert repository.read(project.project_identifier) == project
    assert repository.resolve("missing/project:1") is None
