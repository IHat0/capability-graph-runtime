"""Native universal molecular scene API regressions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cgr.molecular import (
    MolecularArtifactRepository,
    MolecularArtifactRepositoryError,
    MolecularComponent,
    MolecularMemberReference,
    MolecularRegion,
    MolecularSelection,
)
from cgr.pulsate_api.app import _load_preset, create_app
from cgr.pulsate_api.experiments import ExperimentStore
from cgr.pulsate_api.molecular_scenes import (
    NativeMolecularResource,
    NativeMolecularSceneIntegrityError,
    NativeMolecularSceneNotFoundError,
    NativeMolecularSceneService,
    NativeMolecularSceneUnavailableError,
)
from cgr.pulsate_api.natural_language import (
    NaturalLanguageInterpretationStore,
)
from cgr.pulsate_api.runs import RunCoordinator
from cgr.science import ArtifactReference
from test_molecular_scene_projection import (
    _StructureFixture,
    _object_directory,
    _project,
    _structure_fixture,
    _tree_snapshot,
)

_PUBLIC_ARTIFACT_FIELDS = {
    "schema_version",
    "artifact_identifier",
    "artifact_type",
    "media_type",
    "content_sha256",
    "byte_size",
}


class _NoExecution:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, *_args: Any, **_kwargs: Any) -> Any:
        self.calls += 1
        raise AssertionError("Native molecular scene tests cannot execute runs.")


def _persist(
    tmp_path: Path,
    fixtures: tuple[_StructureFixture, ...],
    *,
    sources: bool = True,
    topologies: bool = True,
) -> MolecularArtifactRepository:
    repository = MolecularArtifactRepository(tmp_path / "molecular-artifacts")
    repository.start()
    for fixture in fixtures:
        if sources:
            repository.put(fixture.source_artifact, fixture.source_payload)
        if topologies:
            repository.put(
                fixture.topology_artifact,
                fixture.topology_payload,
            )
    repository.close()
    return repository


def _service(
    project: Any,
    repository: MolecularArtifactRepository,
) -> NativeMolecularSceneService:
    projects = {project.project_identifier: project}
    return NativeMolecularSceneService(
        project_resolver=projects.get,
        repository=repository,
    )


def _application(
    tmp_path: Path,
    *,
    service: NativeMolecularSceneService | None = None,
) -> tuple[FastAPI, RunCoordinator, _NoExecution]:
    executor = _NoExecution()
    coordinator = RunCoordinator(
        run_root=tmp_path / "runs",
        manifest_resolver=_load_preset,
        executor=executor,
        enabled=False,
    )
    application = create_app(
        coordinator=coordinator,
        experiment_store=ExperimentStore(tmp_path / "experiments"),
        natural_language_store=NaturalLanguageInterpretationStore(
            tmp_path / "interpretations",
            None,
            unavailable_reason="disabled for native scene tests",
        ),
        molecular_scene_service=service,
    )
    return application, coordinator, executor


def _multi_structure_project() -> tuple[
    Any,
    tuple[_StructureFixture, _StructureFixture],
]:
    fixture_a = _structure_fixture(
        "structure-a",
        source_payload=b"native-structure-a",
    )
    fixture_b = _structure_fixture(
        "structure-b",
        structure_format="pdb",
        source_payload=(
            b"ATOM      1  H   MOL A   1       0.000   0.000   0.000\nEND\n"
        ),
    )
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
                    member_kind="residue",
                    member_identifier="residue-b",
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
        (fixture_a, fixture_b),
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
    return project, (fixture_a, fixture_b)


def _query(
    *,
    project: str = "project-projection",
    scene: str = "scene-projection",
    structure: str | None = None,
) -> dict[str, str]:
    values = {
        "project_identifier": project,
        "scene_identifier": scene,
    }
    if structure is not None:
        values["structure_identifier"] = structure
    return values


def _contains_forbidden_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if any(
                token in str(key).lower()
                for token in ("token", "credential", "password", "api_key")
            ):
                return True
            if _contains_forbidden_key(item):
                return True
    elif isinstance(value, list):
        return any(_contains_forbidden_key(item) for item in value)
    return False


def test_default_app_constructs_without_native_service_and_routes_return_503() -> None:
    application = create_app()
    client = TestClient(application)

    assert application.state.molecular_scene_service is None
    for path, parameters in (
        ("/api/v1/molecular/scenes/projected", _query()),
        (
            "/api/v1/molecular/scenes/native-structure",
            _query(structure="structure-a"),
        ),
        (
            "/api/v1/molecular/scenes/topology",
            _query(structure="structure-a"),
        ),
    ):
        response = client.get(path, params=parameters)
        assert response.status_code == 503
        assert response.json() == {
            "detail": {
                "code": "molecular_scene_service_unavailable",
                "message": "Native molecular scene service is unavailable.",
            }
        }


def test_injected_service_state_and_lifespan_start_and_close(
    tmp_path: Path,
) -> None:
    fixture = _structure_fixture("structure-lifecycle")
    project = _project((fixture,))
    repository = _persist(tmp_path, (fixture,))
    service = _service(project, repository)
    application, coordinator, executor = _application(
        tmp_path,
        service=service,
    )

    assert application.state.molecular_scene_service is service
    with pytest.raises(NativeMolecularSceneUnavailableError):
        service.describe_scene(
            project_identifier=project.project_identifier,
            scene_identifier=project.scenes[0].scene_identifier,
        )

    with TestClient(application) as client:
        response = client.get(
            "/api/v1/molecular/scenes/projected",
            params=_query(
                project=project.project_identifier,
                scene=project.scenes[0].scene_identifier,
            ),
        )
        assert response.status_code == 200
        assert coordinator.started

    assert not coordinator.started
    with pytest.raises(NativeMolecularSceneUnavailableError):
        service.describe_scene(
            project_identifier=project.project_identifier,
            scene_identifier=project.scenes[0].scene_identifier,
        )
    with pytest.raises(MolecularArtifactRepositoryError, match="not started"):
        repository.read(fixture.topology_artifact)
    assert executor.calls == 0


def test_service_start_and_close_are_idempotent(tmp_path: Path) -> None:
    fixture = _structure_fixture("structure-idempotent-service")
    project = _project((fixture,))
    repository = _persist(tmp_path, (fixture,))
    service = _service(project, repository)

    service.start()
    service.start()
    assert service.describe_scene(
        project_identifier=project.project_identifier,
        scene_identifier=project.scenes[0].scene_identifier,
    )["project_identifier"] == project.project_identifier
    service.close()
    service.close()

    with pytest.raises(NativeMolecularSceneUnavailableError):
        service.describe_scene(
            project_identifier=project.project_identifier,
            scene_identifier=project.scenes[0].scene_identifier,
        )


def test_metadata_preserves_projection_order_and_reduces_artifact_evidence(
    tmp_path: Path,
) -> None:
    project, fixtures = _multi_structure_project()
    repository = _persist(tmp_path, fixtures)
    service = _service(project, repository)
    application, _, executor = _application(tmp_path, service=service)

    with TestClient(application) as client:
        response = client.get(
            "/api/v1/molecular/scenes/projected",
            params=_query(),
        )

    assert response.status_code == 200
    metadata = response.json()
    assert set(metadata) == {
        "schema_version",
        "project_identifier",
        "scene_identifier",
        "primary_structure_identifier",
        "structures",
        "components",
        "selections",
        "regions",
        "artifact_references",
    }
    assert metadata["project_identifier"] == project.project_identifier
    assert metadata["scene_identifier"] == project.scenes[0].scene_identifier
    assert metadata["primary_structure_identifier"] == "structure-a"
    assert [
        item["structure_identifier"] for item in metadata["structures"]
    ] == ["structure-b", "structure-a"]
    assert [
        item["component_identifier"] for item in metadata["components"]
    ] == ["component-a", "component-b"]
    assert [
        item["selection_identifier"] for item in metadata["selections"]
    ] == ["selection-b", "selection-a"]
    assert [
        item["region_identifier"] for item in metadata["regions"]
    ] == ["region-b", "region-a"]
    assert [
        item["artifact_identifier"]
        for item in metadata["artifact_references"]
    ] == [
        fixtures[1].source_artifact.artifact_identifier,
        fixtures[1].topology_artifact.artifact_identifier,
        fixtures[0].source_artifact.artifact_identifier,
        fixtures[0].topology_artifact.artifact_identifier,
    ]
    for structure in metadata["structures"]:
        assert set(structure["source_artifact"]) == _PUBLIC_ARTIFACT_FIELDS
        assert set(structure["topology_artifact"]) == _PUBLIC_ARTIFACT_FIELDS
    for reference in metadata["artifact_references"]:
        assert set(reference) == _PUBLIC_ARTIFACT_FIELDS
    assert executor.calls == 0


def test_metadata_has_no_source_topology_or_private_reference_content(
    tmp_path: Path,
) -> None:
    project, fixtures = _multi_structure_project()
    repository = _persist(tmp_path, fixtures)
    service = _service(project, repository)
    application, _, _ = _application(tmp_path, service=service)

    with TestClient(application) as client:
        response = client.get(
            "/api/v1/molecular/scenes/projected",
            params=_query(),
        )

    assert response.status_code == 200
    metadata = response.json()
    serialized = json.dumps(metadata, sort_keys=True)
    assert "source_payload" not in serialized
    assert "source_text" not in serialized
    assert '"coordinates"' not in serialized
    assert '"atoms"' not in serialized
    assert "atom_identifiers" not in serialized
    assert "storage_location" not in serialized
    assert '"metadata"' not in serialized
    assert '"provenance"' not in serialized
    assert '"parents"' not in serialized
    assert str(tmp_path) not in serialized
    assert fixtures[0].source_payload.decode("utf-8") not in serialized
    assert not _contains_forbidden_key(metadata)


def test_metadata_reads_topologies_but_not_source_structures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, fixtures = _multi_structure_project()
    repository = _persist(tmp_path, fixtures)
    service = _service(project, repository)
    reads: list[ArtifactReference] = []
    original_read = repository.read

    def track_read(reference: ArtifactReference) -> bytes:
        reads.append(reference)
        return original_read(reference)

    monkeypatch.setattr(repository, "read", track_read)
    application, _, _ = _application(tmp_path, service=service)

    with TestClient(application) as client:
        response = client.get(
            "/api/v1/molecular/scenes/projected",
            params=_query(),
        )

    assert response.status_code == 200
    assert reads == [
        fixtures[1].topology_artifact,
        fixtures[0].topology_artifact,
    ]


@pytest.mark.parametrize("structure_format", ["pdb", "mmcif", "mol", "sdf", "xyz"])
def test_native_endpoint_preserves_each_format_and_exact_headers(
    tmp_path: Path,
    structure_format: str,
) -> None:
    payload = (
        f"exact-native-{structure_format}\r\n".encode("utf-8")
        + b"\x00\xff"
    )
    fixture = _structure_fixture(
        f"structure-{structure_format}",
        structure_format=structure_format,
        source_payload=payload,
    )
    project = _project((fixture,))
    repository = _persist(tmp_path, (fixture,))
    service = _service(project, repository)
    application, _, executor = _application(tmp_path, service=service)

    with TestClient(application) as client:
        response = client.get(
            "/api/v1/molecular/scenes/native-structure",
            params=_query(structure=fixture.structure.structure_identifier),
        )

    assert response.status_code == 200
    assert response.content == payload
    assert response.headers["content-type"] == fixture.source_artifact.media_type
    assert response.headers["etag"] == (
        f'"{fixture.source_artifact.content_sha256}"'
    )
    assert response.headers["x-content-sha256"] == (
        fixture.source_artifact.content_sha256
    )
    assert int(response.headers["content-length"]) == len(payload)
    assert "content-disposition" not in response.headers
    assert executor.calls == 0


def test_topology_endpoint_returns_exact_canonical_bytes_without_source_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _structure_fixture("structure-topology-resource")
    project = _project((fixture,))
    repository = _persist(tmp_path, (fixture,))
    service = _service(project, repository)
    reads: list[ArtifactReference] = []
    original_read = repository.read

    def track_read(reference: ArtifactReference) -> bytes:
        reads.append(reference)
        return original_read(reference)

    monkeypatch.setattr(repository, "read", track_read)
    application, _, _ = _application(tmp_path, service=service)

    with TestClient(application) as client:
        response = client.get(
            "/api/v1/molecular/scenes/topology",
            params=_query(structure=fixture.structure.structure_identifier),
        )

    assert response.status_code == 200
    assert response.content == fixture.topology_payload
    assert response.headers["content-type"] == (
        fixture.topology_artifact.media_type
    )
    assert response.headers["etag"] == (
        f'"{fixture.topology_artifact.content_sha256}"'
    )
    assert response.headers["x-content-sha256"] == (
        fixture.topology_artifact.content_sha256
    )
    assert int(response.headers["content-length"]) == len(
        fixture.topology_payload
    )
    assert reads == [fixture.topology_artifact]


def test_unknown_project_scene_and_structure_return_controlled_404(
    tmp_path: Path,
) -> None:
    fixture = _structure_fixture("structure-known")
    project = _project((fixture,))
    repository = _persist(tmp_path, (fixture,))
    service = _service(project, repository)
    application, _, _ = _application(tmp_path, service=service)

    with TestClient(application) as client:
        responses = (
            client.get(
                "/api/v1/molecular/scenes/projected",
                params=_query(project="project-unknown"),
            ),
            client.get(
                "/api/v1/molecular/scenes/projected",
                params=_query(scene="scene-unknown"),
            ),
            client.get(
                "/api/v1/molecular/scenes/native-structure",
                params=_query(structure="structure-unknown"),
            ),
        )

    for response in responses:
        assert response.status_code == 404
        assert response.json() == {
            "detail": {
                "code": "molecular_resource_not_found",
                "message": "Requested molecular resource was not found.",
            }
        }


def test_wrong_resolved_project_identity_returns_controlled_409(
    tmp_path: Path,
) -> None:
    fixture = _structure_fixture("structure-wrong-project")
    project = _project((fixture,))
    repository = _persist(tmp_path, (fixture,))
    service = NativeMolecularSceneService(
        project_resolver=lambda _identifier: project,
        repository=repository,
    )
    application, _, _ = _application(tmp_path, service=service)

    with TestClient(application) as client:
        response = client.get(
            "/api/v1/molecular/scenes/projected",
            params=_query(project="project-different"),
        )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "molecular_scene_unavailable"


def test_unexpected_resolver_failure_returns_redacted_controlled_503(
    tmp_path: Path,
) -> None:
    fixture = _structure_fixture("structure-resolver-failure")
    repository = _persist(tmp_path, (fixture,))
    secret_message = f"resolver exploded at {tmp_path} with token-secret"

    def fail_resolver(_identifier: str) -> Any:
        raise RuntimeError(secret_message)

    service = NativeMolecularSceneService(
        project_resolver=fail_resolver,
        repository=repository,
    )
    application, _, _ = _application(tmp_path, service=service)

    with TestClient(application) as client:
        response = client.get(
            "/api/v1/molecular/scenes/projected",
            params=_query(),
        )

    assert response.status_code == 503
    body = response.text
    assert response.json()["detail"]["code"] == (
        "molecular_scene_service_unavailable"
    )
    assert secret_message not in body
    assert str(tmp_path) not in body
    assert "token-secret" not in body


def test_missing_or_corrupt_topology_returns_controlled_409(
    tmp_path: Path,
) -> None:
    missing_fixture = _structure_fixture("structure-missing-topology")
    missing_project = _project((missing_fixture,))
    missing_repository = _persist(
        tmp_path / "missing",
        (),
        topologies=False,
    )
    missing_service = _service(missing_project, missing_repository)
    missing_application, _, _ = _application(
        tmp_path / "missing-app",
        service=missing_service,
    )

    corrupt_fixture = _structure_fixture("structure-corrupt-topology")
    corrupt_project = _project((corrupt_fixture,))
    corrupt_repository = _persist(
        tmp_path / "corrupt",
        (corrupt_fixture,),
    )
    topology_path = _object_directory(
        tmp_path / "corrupt" / "molecular-artifacts",
        corrupt_fixture.topology_artifact,
    ) / "payload.bin"
    topology_path.write_bytes(b"x" * len(corrupt_fixture.topology_payload))
    corrupt_service = _service(corrupt_project, corrupt_repository)
    corrupt_application, _, _ = _application(
        tmp_path / "corrupt-app",
        service=corrupt_service,
    )

    for application in (missing_application, corrupt_application):
        with TestClient(application) as client:
            response = client.get(
                "/api/v1/molecular/scenes/projected",
                params=_query(),
            )
        assert response.status_code == 409
        assert response.json()["detail"] == {
            "code": "molecular_scene_unavailable",
            "message": (
                "Native molecular scene evidence is unavailable or invalid."
            ),
        }


def test_missing_source_fails_only_native_endpoint_and_corruption_is_controlled(
    tmp_path: Path,
) -> None:
    missing_fixture = _structure_fixture("structure-missing-source")
    missing_project = _project((missing_fixture,))
    missing_repository = _persist(
        tmp_path / "missing",
        (missing_fixture,),
        sources=False,
    )
    missing_service = _service(missing_project, missing_repository)
    missing_application, _, _ = _application(
        tmp_path / "missing-app",
        service=missing_service,
    )

    with TestClient(missing_application) as client:
        metadata = client.get(
            "/api/v1/molecular/scenes/projected",
            params=_query(),
        )
        native = client.get(
            "/api/v1/molecular/scenes/native-structure",
            params=_query(structure="structure-missing-source"),
        )

    assert metadata.status_code == 200
    assert native.status_code == 409

    corrupt_fixture = _structure_fixture("structure-corrupt-source")
    corrupt_project = _project((corrupt_fixture,))
    corrupt_repository = _persist(
        tmp_path / "corrupt",
        (corrupt_fixture,),
    )
    source_path = _object_directory(
        tmp_path / "corrupt" / "molecular-artifacts",
        corrupt_fixture.source_artifact,
    ) / "payload.bin"
    source_path.write_bytes(b"x" * len(corrupt_fixture.source_payload))
    corrupt_service = _service(corrupt_project, corrupt_repository)
    corrupt_application, _, _ = _application(
        tmp_path / "corrupt-app",
        service=corrupt_service,
    )

    with TestClient(corrupt_application) as client:
        response = client.get(
            "/api/v1/molecular/scenes/native-structure",
            params=_query(structure="structure-corrupt-source"),
        )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "molecular_scene_unavailable"


def test_query_identifiers_and_generated_relative_urls_round_trip(
    tmp_path: Path,
) -> None:
    fixture = _structure_fixture(
        "structure:namespace/value",
        source_payload=b"query-identity-bytes",
    )
    project = _project((fixture,))
    project_identifier = "project:namespace/value"
    scene_identifier = "scene with spaces/&"
    scene = project.scenes[0].model_copy(
        update={
            "project_identifier": project_identifier,
            "scene_identifier": scene_identifier,
        }
    )
    project = project.model_copy(
        update={
            "project_identifier": project_identifier,
            "scenes": (scene,),
        }
    )
    repository = _persist(tmp_path, (fixture,))
    seen_identifiers: list[str] = []

    def resolve(identifier: str) -> Any:
        seen_identifiers.append(identifier)
        return project if identifier == project_identifier else None

    service = NativeMolecularSceneService(
        project_resolver=resolve,
        repository=repository,
    )
    application, _, _ = _application(tmp_path, service=service)

    with TestClient(application) as client:
        metadata_response = client.get(
            "/api/v1/molecular/scenes/projected",
            params=_query(
                project=project_identifier,
                scene=scene_identifier,
            ),
        )
        assert metadata_response.status_code == 200
        metadata = metadata_response.json()
        native_url = metadata["structures"][0]["native_structure_url"]
        topology_url = metadata["structures"][0]["topology_url"]
        assert native_url.startswith(
            "/api/v1/molecular/scenes/native-structure?"
        )
        assert topology_url.startswith(
            "/api/v1/molecular/scenes/topology?"
        )
        assert "://" not in native_url
        assert "://" not in topology_url
        assert "%2F" in native_url
        assert "%3A" in native_url
        assert "%20" in native_url
        assert "%26" in native_url
        native_parts = urlsplit(native_url)
        topology_parts = urlsplit(topology_url)
        assert native_parts.path == (
            "/api/v1/molecular/scenes/native-structure"
        )
        assert topology_parts.path == "/api/v1/molecular/scenes/topology"
        expected_query = {
            "project_identifier": [project_identifier],
            "scene_identifier": [scene_identifier],
            "structure_identifier": [
                fixture.structure.structure_identifier
            ],
        }
        assert parse_qs(native_parts.query, strict_parsing=True) == expected_query
        assert parse_qs(topology_parts.query, strict_parsing=True) == expected_query
        native_response = client.get(native_url)
        topology_response = client.get(topology_url)

    assert native_response.status_code == 200
    assert native_response.content == fixture.source_payload
    assert topology_response.status_code == 200
    assert seen_identifiers == [
        project_identifier,
        project_identifier,
        project_identifier,
    ]


def test_health_and_legacy_preset_scene_remain_unchanged(tmp_path: Path) -> None:
    application, _, executor = _application(tmp_path)
    client = TestClient(application)

    health = client.get("/api/v1/health")
    preset = client.get(
        "/api/v1/experiments/presets/h2-ground-state-v1/scene"
    )

    assert health.status_code == 200
    assert health.json() == {
        "service": "pulsate-api",
        "status": "healthy",
        "version": "0.2.0",
    }
    assert preset.status_code == 200
    assert preset.json()["scene_stage"] == "declared"
    assert preset.json()["quantum_region"]["atom_identifiers"] == ["h-1", "h-2"]
    assert executor.calls == 0


def test_metadata_and_resources_do_not_mutate_project_or_repository(
    tmp_path: Path,
) -> None:
    fixture = _structure_fixture("structure-read-only-api")
    project = _project((fixture,))
    repository = _persist(tmp_path, (fixture,))
    service = _service(project, repository)
    application, _, executor = _application(tmp_path, service=service)
    project_json = project.to_canonical_json()
    repository_root = tmp_path / "molecular-artifacts"
    before = _tree_snapshot(repository_root)

    with TestClient(application) as client:
        assert client.get(
            "/api/v1/molecular/scenes/projected",
            params=_query(),
        ).status_code == 200
        assert client.get(
            "/api/v1/molecular/scenes/native-structure",
            params=_query(structure="structure-read-only-api"),
        ).status_code == 200
        assert client.get(
            "/api/v1/molecular/scenes/topology",
            params=_query(structure="structure-read-only-api"),
        ).status_code == 200

    assert project.to_canonical_json() == project_json
    assert _tree_snapshot(repository_root) == before
    assert executor.calls == 0


def test_native_resource_is_frozen_and_contains_no_repository_identity() -> None:
    payload = b"resource"
    resource = NativeMolecularResource(
        payload=payload,
        media_type="application/octet-stream",
        content_sha256=hashlib.sha256(payload).hexdigest(),
        byte_size=len(payload),
        artifact_identifier="artifact-resource",
    )

    assert not hasattr(resource, "storage_location")
    assert not hasattr(resource, "repository_path")
    with pytest.raises(FrozenInstanceError):
        resource.byte_size = 0  # type: ignore[misc]


def test_direct_service_not_found_and_identity_errors_are_controlled(
    tmp_path: Path,
) -> None:
    fixture = _structure_fixture("structure-direct-errors")
    project = _project((fixture,))
    repository = _persist(tmp_path, (fixture,))
    service = _service(project, repository)
    service.start()
    try:
        with pytest.raises(NativeMolecularSceneNotFoundError):
            service.describe_scene(
                project_identifier="project-unknown",
                scene_identifier="scene-projection",
            )
        wrong_service = NativeMolecularSceneService(
            project_resolver=lambda _identifier: project,
            repository=repository,
        )
        wrong_service.start()
        try:
            with pytest.raises(NativeMolecularSceneIntegrityError):
                wrong_service.describe_scene(
                    project_identifier="project-wrong",
                    scene_identifier="scene-projection",
                )
        finally:
            wrong_service.close()
    finally:
        service.close()
