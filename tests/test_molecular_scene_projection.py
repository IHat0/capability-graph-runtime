"""Universal molecular structure-to-scene projection regressions."""

from __future__ import annotations

import hashlib
import json
import stat
from dataclasses import FrozenInstanceError, dataclass, fields
from pathlib import Path
from typing import Any, TypeVar

import pytest

from cgr.kernel.contracts import CapabilityVersion
from cgr.molecular import (
    MolecularArtifactRepository,
    MolecularComponent,
    MolecularMemberReference,
    MolecularProject,
    MolecularRegion,
    MolecularSceneManifest,
    MolecularSceneProjectionIntegrityError,
    MolecularSceneProjectionNotFoundError,
    MolecularSelection,
    MolecularStructure,
    MolecularSystem,
    MolecularTopologyAtom,
    MolecularTopologyIndex,
    ProjectedMolecularStructure,
    ingest_structure_bytes,
    project_molecular_scene,
    read_projected_structure_bytes,
)
from cgr.science import (
    ArtifactPointer,
    ArtifactReference,
    CanonicalModel,
    CreationProvenance,
)

VERSION = CapabilityVersion(major=1, minor=0, patch=0)
PROVENANCE = CreationProvenance(
    producer="molecular-scene-projection-tests",
    producer_version=VERSION,
    execution_identifier="execution-scene-projection-tests",
    source="test-suite",
)
FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "molecular-ingestion"

_CanonicalContract = TypeVar("_CanonicalContract", bound=CanonicalModel)


@dataclass(frozen=True, slots=True)
class _StructureFixture:
    source_payload: bytes
    source_artifact: ArtifactReference
    topology: MolecularTopologyIndex
    topology_payload: bytes
    topology_artifact: ArtifactReference
    structure: MolecularStructure


def _validated_replace(
    contract: _CanonicalContract,
    **updates: Any,
) -> _CanonicalContract:
    values = {
        field_name: getattr(contract, field_name)
        for field_name in type(contract).model_fields
    }
    values.update(updates)
    return type(contract)(**values)


def _artifact(
    *,
    identifier: str,
    payload: bytes,
    artifact_type: str,
    media_type: str,
    structure_format: str | None = None,
    storage_location: str | None = None,
    parents: tuple[ArtifactPointer, ...] = (),
) -> ArtifactReference:
    metadata: dict[str, str] = {}
    if structure_format is not None:
        metadata["structure_format"] = structure_format
    return ArtifactReference(
        artifact_identifier=identifier,
        schema_version=VERSION,
        artifact_type=artifact_type,
        media_type=media_type,
        content_sha256=hashlib.sha256(payload).hexdigest(),
        byte_size=len(payload),
        storage_location=storage_location,
        metadata=metadata,
        provenance=PROVENANCE,
        parents=parents,
    )


def _structure_fixture(
    identifier: str,
    *,
    structure_format: str = "xyz",
    coordinate_unit: str = "angstrom",
    source_payload: bytes | None = None,
    topology_updates: dict[str, Any] | None = None,
    structure_updates: dict[str, Any] | None = None,
) -> _StructureFixture:
    source = source_payload or f"native-{structure_format}-{identifier}".encode()
    source_artifact = _artifact(
        identifier=f"artifact-source-{identifier}",
        payload=source,
        artifact_type="molecular_structure",
        media_type=f"chemical/x-{structure_format}",
        structure_format=structure_format,
        storage_location=f"artifact://molecular/{identifier}",
    )
    topology = MolecularTopologyIndex(
        schema_version=VERSION,
        structure_identifier=identifier,
        source_structure=source_artifact.pointer,
        structure_format=structure_format,
        parser_identifier="synthetic-projection-test",
        parser_version="1.0.0",
        atoms=(
            MolecularTopologyAtom(
                atom_identifier=f"atom-{identifier}-0",
                atom_index=0,
                element_symbol="H",
            ),
            MolecularTopologyAtom(
                atom_identifier=f"atom-{identifier}-1",
                atom_index=1,
                element_symbol="He",
            ),
        ),
        model_count=1,
        frame_count=1,
        coordinate_unit=coordinate_unit,
    )
    if topology_updates:
        topology = _validated_replace(topology, **topology_updates)
    topology_payload = topology.to_canonical_json().encode("utf-8")
    topology_artifact = _artifact(
        identifier=f"artifact-topology-{identifier}",
        payload=topology_payload,
        artifact_type="molecular_topology",
        media_type="application/vnd.pulsate.molecular-topology+json",
        structure_format=topology.structure_format,
        parents=(source_artifact.pointer,),
    )
    structure = MolecularStructure(
        structure_identifier=identifier,
        system_identifier="system-projection",
        coordinate_unit=coordinate_unit,
        structure_artifact=source_artifact,
        topology_artifact=topology_artifact,
        atom_count=len(topology.atoms),
        bond_count=len(topology.bonds),
        residue_count=len(topology.residues),
        chain_count=len(topology.chains),
        model_count=topology.model_count,
        frame_count=topology.frame_count,
    )
    if structure_updates:
        structure = _validated_replace(structure, **structure_updates)
    return _StructureFixture(
        source_payload=source,
        source_artifact=source_artifact,
        topology=topology,
        topology_payload=topology_payload,
        topology_artifact=topology_artifact,
        structure=structure,
    )


def _fixture_with_topology_payload(
    fixture: _StructureFixture,
    payload: bytes,
) -> _StructureFixture:
    topology_artifact = _artifact(
        identifier=fixture.topology_artifact.artifact_identifier,
        payload=payload,
        artifact_type="molecular_topology",
        media_type="application/vnd.pulsate.molecular-topology+json",
        structure_format=fixture.topology.structure_format,
        parents=(fixture.source_artifact.pointer,),
    )
    structure = _validated_replace(
        fixture.structure,
        topology_artifact=topology_artifact,
    )
    return _StructureFixture(
        source_payload=fixture.source_payload,
        source_artifact=fixture.source_artifact,
        topology=fixture.topology,
        topology_payload=payload,
        topology_artifact=topology_artifact,
        structure=structure,
    )


def _project(
    fixtures: tuple[_StructureFixture, ...],
    *,
    scene_identifier: str = "scene-projection",
    primary_structure_identifier: str | None = None,
    scene_artifacts: tuple[ArtifactReference, ...] = (),
    components: tuple[MolecularComponent, ...] = (),
    selections: tuple[MolecularSelection, ...] = (),
    regions: tuple[MolecularRegion, ...] = (),
) -> MolecularProject:
    structure_identifiers = tuple(
        fixture.structure.structure_identifier for fixture in fixtures
    )
    primary = primary_structure_identifier or structure_identifiers[0]
    system = MolecularSystem(
        system_identifier="system-projection",
        structure_identifiers=structure_identifiers,
        default_structure_identifier=structure_identifiers[0],
    )
    scene = MolecularSceneManifest(
        scene_identifier=scene_identifier,
        schema_version=VERSION,
        project_identifier="project-projection",
        structure_identifiers=structure_identifiers,
        primary_structure_identifier=primary,
        selection_identifiers=tuple(
            selection.selection_identifier for selection in selections
        ),
        region_identifiers=tuple(
            region.region_identifier for region in regions
        ),
        artifact_references=scene_artifacts,
    )
    return MolecularProject(
        project_identifier="project-projection",
        schema_version=VERSION,
        systems=(system,),
        structures=tuple(fixture.structure for fixture in fixtures),
        components=components,
        selections=selections,
        regions=regions,
        scenes=(scene,),
        provenance=PROVENANCE,
    )


def _repository(
    tmp_path: Path,
    fixtures: tuple[_StructureFixture, ...],
    *,
    sources: bool = False,
    topologies: bool = True,
) -> MolecularArtifactRepository:
    repository = MolecularArtifactRepository(tmp_path / "artifact-repository")
    repository.start()
    for fixture in fixtures:
        if sources:
            repository.put(fixture.source_artifact, fixture.source_payload)
        if topologies:
            repository.put(
                fixture.topology_artifact,
                fixture.topology_payload,
            )
    return repository


def _object_directory(
    root: Path,
    reference: ArtifactReference,
) -> Path:
    key = hashlib.sha256(
        reference.pointer.to_canonical_json().encode("utf-8")
    ).hexdigest()
    return root / "objects" / key[:2] / key


def _tree_snapshot(
    root: Path,
) -> tuple[tuple[str, int, int, int, str | None], ...]:
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


def test_xyz_ingestion_persistence_projection_and_exact_source_read(
    tmp_path: Path,
) -> None:
    source_payload = (FIXTURE_ROOT / "minimal.xyz").read_bytes()
    source_artifact = _artifact(
        identifier="artifact-ingested-xyz",
        payload=source_payload,
        artifact_type="molecular_structure",
        media_type="chemical/x-xyz",
        structure_format="xyz",
    )
    ingestion = ingest_structure_bytes(
        source_bytes=source_payload,
        source_reference=source_artifact,
        structure_identifier="structure-ingested-xyz",
        system_identifier="system-projection",
        structure_format="xyz",
        coordinate_unit="angstrom",
        provenance=PROVENANCE,
    )
    fixture = _StructureFixture(
        source_payload=source_payload,
        source_artifact=source_artifact,
        topology=ingestion.topology,
        topology_payload=ingestion.topology_payload,
        topology_artifact=ingestion.topology_artifact,
        structure=ingestion.structure,
    )
    project = _project((fixture,))
    repository = _repository(tmp_path, (fixture,), sources=True)

    projection = project_molecular_scene(
        project=project,
        scene_identifier="scene-projection",
        repository=repository,
    )
    returned = read_projected_structure_bytes(
        projection=projection,
        structure_identifier="structure-ingested-xyz",
        repository=repository,
    )

    assert returned == source_payload
    assert projection.structures[0].native_format == "xyz"
    assert projection.structures[0].topology == ingestion.topology
    assert projection.structures[0].atom_identifiers == tuple(
        atom.atom_identifier for atom in ingestion.topology.atoms
    )


def test_projection_reads_only_topology_until_source_is_requested(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _structure_fixture("structure-read-boundary")
    project = _project((fixture,))
    repository = _repository(tmp_path, (fixture,), sources=True)
    original_read = repository.read
    reads: list[ArtifactReference] = []

    def track_read(reference: ArtifactReference) -> bytes:
        reads.append(reference)
        return original_read(reference)

    monkeypatch.setattr(repository, "read", track_read)

    projection = project_molecular_scene(
        project=project,
        scene_identifier="scene-projection",
        repository=repository,
    )

    assert reads == [fixture.topology_artifact]
    assert read_projected_structure_bytes(
        projection=projection,
        structure_identifier="structure-read-boundary",
        repository=repository,
    ) == fixture.source_payload
    assert reads == [fixture.topology_artifact, fixture.source_artifact]


@pytest.mark.parametrize("structure_format", ["pdb", "mmcif", "mol", "sdf", "xyz"])
def test_native_structure_format_is_preserved_without_optional_parsers(
    tmp_path: Path,
    structure_format: str,
) -> None:
    fixture = _structure_fixture(
        f"structure-{structure_format}",
        structure_format=structure_format,
    )
    project = _project((fixture,))
    repository = _repository(tmp_path, (fixture,))

    projection = project_molecular_scene(
        project=project,
        scene_identifier="scene-projection",
        repository=repository,
    )

    assert projection.structures[0].native_format == structure_format
    assert projection.structures[0].topology.structure_format == structure_format


def test_projection_has_no_source_payload_field_and_preserves_references(
    tmp_path: Path,
) -> None:
    fixture = _structure_fixture("structure-no-embedded-source")
    extra_payload = b"scene-declared-evidence"
    extra_reference = _artifact(
        identifier="artifact-scene-evidence",
        payload=extra_payload,
        artifact_type="molecular_scene_evidence",
        media_type="application/octet-stream",
        storage_location="artifact://remote/scene-evidence",
    )
    project = _project(
        (fixture,),
        scene_artifacts=(
            fixture.source_artifact,
            fixture.topology_artifact,
            extra_reference,
        ),
    )
    repository = _repository(tmp_path, (fixture,))

    projection = project_molecular_scene(
        project=project,
        scene_identifier="scene-projection",
        repository=repository,
    )
    projected = projection.structures[0]

    assert "source_payload" not in {
        field.name for field in fields(ProjectedMolecularStructure)
    }
    assert "source_bytes" not in {
        field.name for field in fields(ProjectedMolecularStructure)
    }
    assert projected.source_artifact is fixture.source_artifact
    assert projected.topology_artifact is fixture.topology_artifact
    assert projection.artifact_references == (
        fixture.source_artifact,
        fixture.topology_artifact,
        extra_reference,
    )
    assert extra_reference.storage_location == "artifact://remote/scene-evidence"


def test_multistructure_manifest_order_primary_and_scene_subsets_are_preserved(
    tmp_path: Path,
) -> None:
    fixture_a = _structure_fixture("structure-a")
    fixture_b = _structure_fixture("structure-b")
    fixture_c = _structure_fixture("structure-c")
    components = (
        MolecularComponent(
            component_identifier="component-a",
            structure_identifier="structure-a",
            component_type="ligand",
            members=(
                MolecularMemberReference(
                    member_kind="atom",
                    member_identifier="atom-structure-a-0",
                ),
            ),
        ),
        MolecularComponent(
            component_identifier="component-b",
            structure_identifier="structure-b",
            component_type="protein",
            members=(
                MolecularMemberReference(
                    member_kind="chain",
                    member_identifier="chain-b",
                ),
            ),
        ),
        MolecularComponent(
            component_identifier="component-c",
            structure_identifier="structure-c",
            component_type="solvent",
            members=(
                MolecularMemberReference(
                    member_kind="residue",
                    member_identifier="residue-c",
                ),
            ),
        ),
    )
    selections = (
        MolecularSelection(
            selection_identifier="selection-a",
            structure_identifier="structure-a",
            selection_source="user",
            members=(
                MolecularMemberReference(
                    member_kind="component",
                    member_identifier="component-a",
                ),
            ),
        ),
        MolecularSelection(
            selection_identifier="selection-b",
            structure_identifier="structure-b",
            selection_source="imported",
            members=(
                MolecularMemberReference(
                    member_kind="atom",
                    member_identifier="atom-structure-b-0",
                ),
            ),
        ),
    )
    regions = (
        MolecularRegion(
            region_identifier="region-a",
            structure_identifier="structure-a",
            region_type="selected_fragment",
            selection_identifier="selection-a",
        ),
        MolecularRegion(
            region_identifier="region-b",
            structure_identifier="structure-b",
            region_type="binding_pocket",
            selection_identifier="selection-b",
        ),
    )
    project = _project(
        (fixture_a, fixture_b, fixture_c),
        primary_structure_identifier="structure-a",
        components=components,
        selections=selections,
        regions=regions,
    )
    scene = project.scenes[0].model_copy(
        update={
            "structure_identifiers": ("structure-b", "structure-a"),
            "selection_identifiers": ("selection-b", "selection-a"),
            "region_identifiers": ("region-b", "region-a"),
        }
    )
    project = project.model_copy(update={"scenes": (scene,)})
    repository = _repository(
        tmp_path,
        (fixture_a, fixture_b, fixture_c),
    )

    projection = project_molecular_scene(
        project=project,
        scene_identifier="scene-projection",
        repository=repository,
    )

    assert tuple(
        item.structure_identifier for item in projection.structures
    ) == ("structure-b", "structure-a")
    assert projection.primary_structure.structure_identifier == "structure-a"
    assert tuple(
        item.component_identifier for item in projection.components
    ) == ("component-a", "component-b")
    assert tuple(
        item.selection_identifier for item in projection.selections
    ) == ("selection-b", "selection-a")
    assert tuple(
        item.region_identifier for item in projection.regions
    ) == ("region-b", "region-a")
    assert projection.selections[0].members == selections[1].members


def test_unknown_scene_identifier_is_controlled(
    tmp_path: Path,
) -> None:
    fixture = _structure_fixture("structure-known-scene")
    project = _project((fixture,))
    repository = _repository(tmp_path, (fixture,))

    with pytest.raises(
        MolecularSceneProjectionNotFoundError,
        match="scene not found",
    ):
        project_molecular_scene(
            project=project,
            scene_identifier="scene-unknown",
            repository=repository,
        )


def test_unknown_projected_structure_identifier_is_controlled(
    tmp_path: Path,
) -> None:
    fixture = _structure_fixture("structure-known")
    project = _project((fixture,))
    repository = _repository(tmp_path, (fixture,))
    projection = project_molecular_scene(
        project=project,
        scene_identifier="scene-projection",
        repository=repository,
    )

    with pytest.raises(
        MolecularSceneProjectionNotFoundError,
        match="structure not found",
    ):
        read_projected_structure_bytes(
            projection=projection,
            structure_identifier="structure-unknown",
            repository=repository,
        )


def test_scene_structure_reference_missing_from_project_fails_closed(
    tmp_path: Path,
) -> None:
    fixture = _structure_fixture("structure-present")
    project = _project((fixture,))
    scene = project.scenes[0].model_copy(
        update={"structure_identifiers": ("structure-missing",)}
    )
    project = project.model_copy(update={"scenes": (scene,)})
    repository = _repository(tmp_path, (fixture,))

    with pytest.raises(
        MolecularSceneProjectionIntegrityError,
        match="does not resolve",
    ):
        project_molecular_scene(
            project=project,
            scene_identifier="scene-projection",
            repository=repository,
        )


def test_missing_topology_artifact_fails_closed(tmp_path: Path) -> None:
    fixture = _structure_fixture("structure-no-topology")
    missing_topology = fixture.structure.model_copy(
        update={"topology_artifact": None}
    )
    project = _project((fixture,)).model_copy(
        update={"structures": (missing_topology,)}
    )
    repository = _repository(tmp_path, (), topologies=False)

    with pytest.raises(
        MolecularSceneProjectionIntegrityError,
        match="no topology artifact",
    ):
        project_molecular_scene(
            project=project,
            scene_identifier="scene-projection",
            repository=repository,
        )


def test_missing_topology_repository_object_fails_closed(
    tmp_path: Path,
) -> None:
    fixture = _structure_fixture("structure-missing-topology-object")
    project = _project((fixture,))
    repository = _repository(tmp_path, (), topologies=False)

    with pytest.raises(
        MolecularSceneProjectionIntegrityError,
        match="topology evidence",
    ) as captured:
        project_molecular_scene(
            project=project,
            scene_identifier="scene-projection",
            repository=repository,
        )

    assert str(tmp_path) not in str(captured.value)


def test_repository_not_started_is_a_controlled_projection_error(
    tmp_path: Path,
) -> None:
    fixture = _structure_fixture("structure-stopped-repository")
    project = _project((fixture,))
    repository = MolecularArtifactRepository(tmp_path / "stopped-repository")

    with pytest.raises(MolecularSceneProjectionIntegrityError) as captured:
        project_molecular_scene(
            project=project,
            scene_identifier="scene-projection",
            repository=repository,
        )

    assert str(tmp_path) not in str(captured.value)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"{malformed", "malformed"),
        (json.dumps(["not", "an", "object"]).encode("utf-8"), "JSON object"),
    ],
)
def test_malformed_or_non_object_topology_fails_closed(
    tmp_path: Path,
    payload: bytes,
    message: str,
) -> None:
    fixture = _fixture_with_topology_payload(
        _structure_fixture("structure-malformed-topology"),
        payload,
    )
    project = _project((fixture,))
    repository = _repository(tmp_path, (fixture,))

    with pytest.raises(
        MolecularSceneProjectionIntegrityError,
        match=message,
    ):
        project_molecular_scene(
            project=project,
            scene_identifier="scene-projection",
            repository=repository,
        )


def test_noncanonical_valid_topology_json_fails_closed(
    tmp_path: Path,
) -> None:
    fixture = _structure_fixture("structure-noncanonical-topology")
    noncanonical = json.dumps(
        fixture.topology.model_dump(mode="json"),
        indent=2,
        sort_keys=False,
    ).encode("utf-8")
    fixture = _fixture_with_topology_payload(fixture, noncanonical)
    project = _project((fixture,))
    repository = _repository(tmp_path, (fixture,))

    with pytest.raises(
        MolecularSceneProjectionIntegrityError,
        match="not canonical",
    ):
        project_molecular_scene(
            project=project,
            scene_identifier="scene-projection",
            repository=repository,
        )


@pytest.mark.parametrize(
    ("fixture", "message"),
    [
        (
            _structure_fixture(
                "structure-identity",
                topology_updates={
                    "structure_identifier": "structure-other",
                },
            ),
            "structure identity",
        ),
        (
            _structure_fixture(
                "structure-source",
                topology_updates={
                    "source_structure": ArtifactPointer(
                        artifact_identifier="artifact-other-source",
                        content_sha256="0" * 64,
                    ),
                },
            ),
            "source identity",
        ),
        (
            _structure_fixture(
                "structure-format",
                structure_format="xyz",
                topology_updates={"structure_format": "pdb"},
            ),
            "structure format",
        ),
        (
            _structure_fixture(
                "structure-coordinate",
                coordinate_unit="angstrom",
                topology_updates={"coordinate_unit": "bohr"},
            ),
            "coordinate unit",
        ),
        (
            _structure_fixture(
                "structure-atoms",
                structure_updates={"atom_count": 3},
            ),
            "atom count",
        ),
        (
            _structure_fixture(
                "structure-residues",
                structure_updates={"residue_count": 1},
            ),
            "residue count",
        ),
        (
            _structure_fixture(
                "structure-chains",
                structure_updates={"chain_count": 1},
            ),
            "chain count",
        ),
        (
            _structure_fixture(
                "structure-models",
                structure_updates={"model_count": 2},
            ),
            "model count",
        ),
        (
            _structure_fixture(
                "structure-frames",
                structure_updates={"frame_count": 2},
            ),
            "frame count",
        ),
    ],
)
def test_cross_contract_topology_mismatches_fail_closed(
    tmp_path: Path,
    fixture: _StructureFixture,
    message: str,
) -> None:
    project = _project((fixture,))
    repository = _repository(tmp_path, (fixture,))

    with pytest.raises(
        MolecularSceneProjectionIntegrityError,
        match=message,
    ):
        project_molecular_scene(
            project=project,
            scene_identifier="scene-projection",
            repository=repository,
        )


def test_same_pointer_with_different_complete_reference_fails_closed(
    tmp_path: Path,
) -> None:
    fixture = _structure_fixture("structure-pointer-conflict")
    conflicting = _validated_replace(
        fixture.source_artifact,
        media_type="application/octet-stream",
        metadata={"structure_format": "xyz", "variant": "conflicting"},
    )
    project = _project((fixture,))
    scene = project.scenes[0].model_copy(
        update={"artifact_references": (conflicting,)}
    )
    project = project.model_copy(update={"scenes": (scene,)})
    repository = _repository(tmp_path, (fixture,))

    with pytest.raises(
        MolecularSceneProjectionIntegrityError,
        match="pointer evidence conflicts",
    ):
        project_molecular_scene(
            project=project,
            scene_identifier="scene-projection",
            repository=repository,
        )


def test_missing_source_object_fails_only_when_bytes_are_requested(
    tmp_path: Path,
) -> None:
    fixture = _structure_fixture("structure-source-delayed")
    project = _project((fixture,))
    repository = _repository(tmp_path, (fixture,), sources=False)

    projection = project_molecular_scene(
        project=project,
        scene_identifier="scene-projection",
        repository=repository,
    )

    with pytest.raises(
        MolecularSceneProjectionIntegrityError,
        match="bytes are unavailable",
    ):
        read_projected_structure_bytes(
            projection=projection,
            structure_identifier="structure-source-delayed",
            repository=repository,
        )


def test_tampered_source_payload_fails_when_bytes_are_requested(
    tmp_path: Path,
) -> None:
    fixture = _structure_fixture("structure-source-tampered")
    project = _project((fixture,))
    repository = _repository(tmp_path, (fixture,), sources=True)
    source_path = _object_directory(
        tmp_path / "artifact-repository",
        fixture.source_artifact,
    ) / "payload.bin"
    source_path.write_bytes(b"x" * len(fixture.source_payload))
    projection = project_molecular_scene(
        project=project,
        scene_identifier="scene-projection",
        repository=repository,
    )

    with pytest.raises(
        MolecularSceneProjectionIntegrityError,
        match="bytes are unavailable",
    ):
        read_projected_structure_bytes(
            projection=projection,
            structure_identifier="structure-source-tampered",
            repository=repository,
        )


def test_projection_and_read_are_read_only_and_preserve_inputs(
    tmp_path: Path,
) -> None:
    fixture = _structure_fixture("structure-read-only")
    project = _project((fixture,))
    repository = _repository(tmp_path, (fixture,), sources=True)
    repository_root = tmp_path / "artifact-repository"
    project_json = project.to_canonical_json()
    scene_json = project.scenes[0].to_canonical_json()
    source_json = fixture.source_artifact.to_canonical_json()
    topology_json = fixture.topology_artifact.to_canonical_json()
    before = _tree_snapshot(repository_root)

    projection = project_molecular_scene(
        project=project,
        scene_identifier="scene-projection",
        repository=repository,
    )
    returned = read_projected_structure_bytes(
        projection=projection,
        structure_identifier="structure-read-only",
        repository=repository,
    )

    assert returned == fixture.source_payload
    assert project.to_canonical_json() == project_json
    assert project.scenes[0].to_canonical_json() == scene_json
    assert fixture.source_artifact.to_canonical_json() == source_json
    assert fixture.topology_artifact.to_canonical_json() == topology_json
    assert _tree_snapshot(repository_root) == before
    assert projection.scene is project.scenes[0]


def test_projection_result_dataclasses_are_frozen(tmp_path: Path) -> None:
    fixture = _structure_fixture("structure-frozen")
    project = _project((fixture,))
    repository = _repository(tmp_path, (fixture,))
    projection = project_molecular_scene(
        project=project,
        scene_identifier="scene-projection",
        repository=repository,
    )

    with pytest.raises(FrozenInstanceError):
        projection.project_identifier = "project-mutated"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        projection.structures[0].native_format = "pdb"  # type: ignore[misc]


def test_public_projection_errors_do_not_expose_repository_paths(
    tmp_path: Path,
) -> None:
    fixture = _structure_fixture("structure-path-free")
    project = _project((fixture,))
    repository = _repository(tmp_path, (), topologies=False)

    with pytest.raises(MolecularSceneProjectionIntegrityError) as captured:
        project_molecular_scene(
            project=project,
            scene_identifier="scene-projection",
            repository=repository,
        )

    message = str(captured.value)
    assert str(tmp_path) not in message
    assert fixture.topology_payload.decode("utf-8") not in message
