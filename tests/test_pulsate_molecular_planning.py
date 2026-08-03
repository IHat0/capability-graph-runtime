"""Read-only molecular-project planning API regressions."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import cgr.pulsate_api.molecular_planning as molecular_planning_module
from cgr.kernel.contracts import CapabilityVersion
from cgr.molecular import (
    MolecularArtifactRepository,
    MolecularArtifactRepositoryError,
    MolecularProject,
    MolecularProjectRepository,
    MolecularSystem,
)
from cgr.pulsate_api.app import _load_preset, create_app
from cgr.pulsate_api.experiments import ExperimentStore
from cgr.pulsate_api.molecular_planning import (
    MOLECULAR_PLANNING_CATALOGUE_MAXIMUM_CAPABILITIES,
    MOLECULAR_PLANNING_REQUEST_MAXIMUM_BYTES,
    MOLECULAR_PLANNING_TOPOLOGY_MAXIMUM_BYTES,
    MolecularPlanningService,
    MolecularPlanningUnavailableError,
)
from cgr.pulsate_api.natural_language import NaturalLanguageInterpretationStore
from cgr.pulsate_api.runs import RunCoordinator
from cgr.science import (
    CapabilityDescriptor,
    CapabilityEstimate,
    CapabilityEstimateType,
    CapabilityExecutionEnvelope,
    CapabilityFidelityDeclaration,
    CapabilityFidelityLevel,
    CapabilityPrerequisite,
    CapabilityPrerequisiteType,
    CapabilityVerificationRequirement,
    CreationProvenance,
    DeterminismClassification,
    ScientificCapabilityCatalog,
    ScientificVerificationOutcome,
)
from test_molecular_scene_projection import (
    _fixture_with_topology_payload,
    _project,
    _structure_fixture,
    _tree_snapshot,
    _validated_replace,
)

VERSION = CapabilityVersion(major=1, minor=0, patch=0)
PROVENANCE = CreationProvenance(
    producer="molecular-planning-api-tests",
    producer_version=VERSION,
    source="test-suite",
)


class _NoExecution:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, *_args: Any, **_kwargs: Any) -> Any:
        self.calls += 1
        raise AssertionError("Read-only molecular planning cannot execute runs.")


def _envelope(
    name: str,
    *,
    objective_type: str = "interaction_analysis",
    target: str = "target.local",
    conditional: bool = False,
) -> CapabilityExecutionEnvelope:
    prerequisite = (
        CapabilityPrerequisite(
            prerequisite_identifier=f"prerequisite-{name}",
            prerequisite_type=CapabilityPrerequisiteType.APPROVAL,
            required_identifier="approval.scientist",
            blocking=False,
            explanation="A scientist must approve later execution.",
        ),
    ) if conditional else ()
    return CapabilityExecutionEnvelope(
        descriptor=CapabilityDescriptor(
            capability_name=name,
            version=VERSION,
            accepted_artifact_types=("molecular_structure",),
            produced_artifact_types=("analysis_result",),
            determinism=DeterminismClassification.DETERMINISTIC,
        ),
        supported_objective_types=(objective_type,),
        prerequisites=prerequisite,
        execution_targets=(target,),
        estimates=(
            CapabilityEstimate(
                estimate_type=CapabilityEstimateType.RUNTIME,
                lower_bound=3.0,
                expected_value=4.0,
                upper_bound=5.0,
                unit="seconds",
                confidence=0.8,
                estimation_basis="Declared test estimate, not a guarantee.",
            ),
        ),
        fidelity_declarations=(
            CapabilityFidelityDeclaration(
                objective_type=objective_type,
                fidelity_level=CapabilityFidelityLevel.SCREENING,
                expected_error=CapabilityEstimate(
                    estimate_type=CapabilityEstimateType.SCIENTIFIC_ERROR,
                    expected_value=0.1,
                    unit="relative_error",
                    confidence=0.9,
                    estimation_basis="Declared test fidelity estimate.",
                ),
                limitations=("Requires later experimental validation.",),
            ),
        ),
        known_limitations=("Does not authorize or execute computation.",),
        verification_requirements=(
            CapabilityVerificationRequirement(
                verifier_identifier="verifier.result-integrity",
                subject_artifact_type="analysis_result",
                acceptable_outcomes=(ScientificVerificationOutcome.PASSED,),
                required_before_execution=False,
                required_after_execution=True,
                blocking=False,
            ),
        ),
        approval_requirements=("approval.scientist",),
    )


def _request(project: Any, **updates: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "objective": {
            "objective_identifier": "objective.workspace",
            "schema_version": VERSION.model_dump(mode="json"),
            "objective_type": "interaction_analysis",
            "original_description": "Compare the declared structures.",
            "normalized_description": "Compare the declared structures.",
            "project_identifier": project.project_identifier,
            "molecular_system_identifiers": [
                item.system_identifier for item in project.systems
            ],
            "structure_identifiers": [
                item.structure_identifier for item in project.structures
            ],
            "structural_region_identifiers": [
                item.region_identifier for item in project.regions
            ],
            "comparison_state_identifiers": [],
            "requested_property_identifiers": ["interaction_energy"],
            "scientific_constraints": [],
            "required_confidence": 0.8,
            "stopping_criteria": [],
            "assumptions": [],
            "provenance": PROVENANCE.model_dump(mode="json"),
        },
        "constraints": {
            "permitted_execution_targets": ["target.local"],
            "forbidden_execution_targets": [],
            "resource_ceilings": [],
            "cost_ceilings": [],
            "maximum_wall_time_seconds": 60.0,
            "minimum_fidelity": "screening",
            "required_confidence": 0.7,
            "required_verifications": [],
            "approval_requirements": [],
            "policy_notes": ["Planning is non-authorizing."],
        },
        "resources": {
            "snapshot_identifier": "resources.workspace",
            "schema_version": VERSION.model_dump(mode="json"),
            "observed_at": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
            "execution_environment": "workspace",
            "available_resources": [],
            "available_execution_targets": ["target.local"],
            "unavailable_reasons": {},
            "provenance": PROVENANCE.model_dump(mode="json"),
        },
        "requested_execution_target": "target.local",
        "selected_capabilities": [],
    }
    values.update(updates)
    return values


def _application(
    tmp_path: Path,
    *,
    project: Any,
    repository: MolecularArtifactRepository,
    catalogue: ScientificCapabilityCatalog | None = None,
    project_resolver: Callable[[str], MolecularProject | None] | None = None,
) -> tuple[FastAPI, _NoExecution]:
    executor = _NoExecution()
    service = MolecularPlanningService(
        project_resolver=(
            project_resolver or {project.project_identifier: project}.get
        ),
        artifact_repository=repository,
        catalogue=catalogue or ScientificCapabilityCatalog(),
    )
    application = create_app(
        coordinator=RunCoordinator(
            run_root=tmp_path / "runs",
            manifest_resolver=_load_preset,
            executor=executor,
            enabled=False,
        ),
        experiment_store=ExperimentStore(tmp_path / "experiments"),
        natural_language_store=NaturalLanguageInterpretationStore(
            tmp_path / "interpretations",
            None,
            unavailable_reason="disabled for molecular planning tests",
        ),
        molecular_planning_service=service,
    )
    return application, executor


def _repository(
    tmp_path: Path,
    fixtures: tuple[Any, ...],
) -> MolecularArtifactRepository:
    repository = MolecularArtifactRepository(tmp_path / "artifacts")
    repository.start()
    for fixture in fixtures:
        if fixture.structure.topology_artifact is not None:
            repository.put(fixture.topology_artifact, fixture.topology_payload)
    repository.close()
    return repository


def _endpoint(project_identifier: str) -> str:
    return f"/api/v1/molecular/projects/{project_identifier}/planning/evaluate"


def test_evaluate_project_plan_preserves_complete_plan_and_is_read_only(
    tmp_path: Path,
) -> None:
    fixture_a = _structure_fixture("structure-a")
    fixture_b = _structure_fixture("structure-b")
    project = _project((fixture_a, fixture_b))
    structure_a = _validated_replace(
        fixture_a.structure,
        system_identifier="system-a",
    )
    structure_b = _validated_replace(
        fixture_b.structure,
        system_identifier="system-b",
    )
    project = _validated_replace(
        project,
        systems=(
            MolecularSystem(
                system_identifier="system-a",
                structure_identifiers=("structure-a",),
                default_structure_identifier="structure-a",
            ),
            MolecularSystem(
                system_identifier="system-b",
                structure_identifiers=("structure-b",),
                default_structure_identifier="structure-b",
            ),
        ),
        structures=(structure_a, structure_b),
    )
    repository = _repository(tmp_path, (fixture_a, fixture_b))
    projects = MolecularProjectRepository(tmp_path / "projects")
    projects.start()
    projects.put(project)
    project_before = _tree_snapshot(tmp_path / "projects")
    artifacts_before = _tree_snapshot(tmp_path / "artifacts")
    catalogue = ScientificCapabilityCatalog(
        (
            _envelope("capability.feasible", conditional=True),
            _envelope("capability.rejected", objective_type="conformer_comparison"),
        )
    )
    application, executor = _application(
        tmp_path,
        project=project,
        repository=repository,
        catalogue=catalogue,
        project_resolver=projects.resolve,
    )

    try:
        with TestClient(application) as client:
            response = client.post(
                _endpoint(project.project_identifier),
                json=_request(project),
            )
            project_after_first_request = _tree_snapshot(tmp_path / "projects")
            artifacts_after_first_request = _tree_snapshot(tmp_path / "artifacts")
            repeated = client.post(
                _endpoint(project.project_identifier),
                json=_request(project),
            )
            project_after_repeated_request = _tree_snapshot(tmp_path / "projects")
            artifacts_after_repeated_request = _tree_snapshot(tmp_path / "artifacts")
    finally:
        projects.close()

    assert response.status_code == 200
    assert response.json() == repeated.json()
    payload = response.json()
    assert payload["project_identifier"] == project.project_identifier
    assert len(payload["plan_fingerprint"]) == 64
    assert len(payload["planning_facts_fingerprint"]) == 64
    assert payload["plan_identifier"] == payload["plan"]["plan_identifier"]
    assert len(payload["plan"]["selected_assignments"]) == 1
    assert (
        payload["plan"]["selected_assignments"][0]["feasibility"]["status"]
        == "conditional"
    )
    assert len(payload["plan"]["rejected_alternatives"]) == 1
    assert payload["plan"]["planning_facts"]["facts"]
    assert payload["plan"]["molecular_system_identifiers"] == [
        "system-a",
        "system-b",
    ]
    assert payload["plan"]["approval_requirements"] == ["approval.scientist"]
    assert payload["plan"]["verification_requirements"]
    assert payload["capability_declarations"][0]["known_limitations"]
    assert executor.calls == 0
    assert project_after_first_request == project_before
    assert project_after_repeated_request == project_before
    assert artifacts_after_first_request == artifacts_before
    assert artifacts_after_repeated_request == artifacts_before
    assert not any(path.is_dir() for path in (tmp_path / "runs").iterdir())
    for persistent_name in ("plans", "jobs", "workflows"):
        assert not (tmp_path / persistent_name).exists()


def test_missing_topology_and_empty_catalogue_return_valid_unassigned_plan(
    tmp_path: Path,
) -> None:
    fixture = _structure_fixture("structure-without-topology")
    structure = _validated_replace(fixture.structure, topology_artifact=None)
    fixture = fixture.__class__(
        source_payload=fixture.source_payload,
        source_artifact=fixture.source_artifact,
        topology=fixture.topology,
        topology_payload=fixture.topology_payload,
        topology_artifact=fixture.topology_artifact,
        structure=structure,
    )
    project = _project((fixture,))
    repository = _repository(tmp_path, ())
    application, executor = _application(
        tmp_path,
        project=project,
        repository=repository,
    )

    with TestClient(application) as client:
        response = client.post(
            _endpoint(project.project_identifier),
            json=_request(project),
        )

    assert response.status_code == 200
    plan = response.json()["plan"]
    assert plan["selected_assignments"] == []
    assert plan["rejected_alternatives"] == []
    assert plan["unresolved_goal_identifiers"] == ["objective.workspace"]
    assert executor.calls == 0


def test_planning_does_not_mutate_the_persisted_project_repository(
    tmp_path: Path,
) -> None:
    fixture = _structure_fixture("structure-persisted-project")
    project = _project((fixture,))
    artifacts = _repository(tmp_path, (fixture,))
    projects = MolecularProjectRepository(tmp_path / "projects")
    projects.start()
    projects.put(project)
    before = _tree_snapshot(tmp_path / "projects")
    application, executor = _application(
        tmp_path,
        project=project,
        repository=artifacts,
        project_resolver=projects.resolve,
    )

    try:
        with TestClient(application) as client:
            response = client.post(
                _endpoint(project.project_identifier),
                json=_request(project),
            )
    finally:
        projects.close()
    assert response.status_code == 200
    assert _tree_snapshot(tmp_path / "projects") == before
    assert executor.calls == 0


@pytest.mark.parametrize(
    ("field", "unknown"),
    [
        ("molecular_system_identifiers", "system.unknown"),
        ("structure_identifiers", "structure.unknown"),
        ("structural_region_identifiers", "region.unknown"),
    ],
)
def test_unknown_objective_references_fail_closed(
    tmp_path: Path,
    field: str,
    unknown: str,
) -> None:
    fixture = _structure_fixture("structure-reference")
    project = _project((fixture,))
    repository = _repository(tmp_path, (fixture,))
    application, executor = _application(
        tmp_path,
        project=project,
        repository=repository,
    )
    request = _request(project)
    request["objective"][field] = [unknown]

    with TestClient(application) as client:
        response = client.post(
            _endpoint(project.project_identifier),
            json=request,
        )

    assert response.status_code == 422
    assert (
        response.json()["detail"]["code"]
        == "invalid_molecular_planning_request"
    )
    assert str(tmp_path) not in response.text
    assert executor.calls == 0


def test_objective_project_identity_must_match_the_path(tmp_path: Path) -> None:
    fixture = _structure_fixture("structure-project-mismatch")
    project = _project((fixture,))
    repository = _repository(tmp_path, (fixture,))
    application, executor = _application(
        tmp_path,
        project=project,
        repository=repository,
    )
    request = _request(project)
    request["objective"]["project_identifier"] = "project.other"

    with TestClient(application) as client:
        response = client.post(
            _endpoint(project.project_identifier),
            json=request,
        )

    assert response.status_code == 422
    assert (
        response.json()["detail"]["code"]
        == "invalid_molecular_planning_request"
    )
    assert executor.calls == 0


def test_invalid_request_is_typed_and_does_not_echo_secret_values(
    tmp_path: Path,
) -> None:
    fixture = _structure_fixture("structure-invalid-request")
    project = _project((fixture,))
    repository = _repository(tmp_path, (fixture,))
    application, executor = _application(
        tmp_path,
        project=project,
        repository=repository,
    )
    request = _request(project)
    request["api_token"] = "must-not-be-returned"

    with TestClient(application) as client:
        response = client.post(
            _endpoint(project.project_identifier),
            json=request,
        )

    assert response.status_code == 422
    assert response.json() == {
        "detail": {
            "code": "invalid_molecular_planning_request",
            "message": "Molecular planning request is invalid.",
        }
    }
    assert "must-not-be-returned" not in response.text
    assert executor.calls == 0


def test_unknown_project_and_exact_capability_identity_are_typed(
    tmp_path: Path,
) -> None:
    fixture = _structure_fixture("structure-identity")
    project = _project((fixture,))
    repository = _repository(tmp_path, (fixture,))
    application, executor = _application(
        tmp_path,
        project=project,
        repository=repository,
        catalogue=ScientificCapabilityCatalog((_envelope("capability.available"),)),
    )
    request = _request(project)
    request["selected_capabilities"] = [
        {
            "capability_name": "capability.missing",
            "capability_version": VERSION.model_dump(mode="json"),
        }
    ]

    with TestClient(application) as client:
        unknown_project = client.post(
            "/api/v1/molecular/projects/project-missing/planning/evaluate",
            json={
                **request,
                "objective": {
                    **request["objective"],
                    "project_identifier": "project-missing",
                },
            },
        )
        unknown_capability = client.post(
            _endpoint(project.project_identifier),
            json=request,
        )

    assert unknown_project.status_code == 404
    assert unknown_project.json()["detail"]["code"] == "molecular_project_not_found"
    assert unknown_capability.status_code == 404
    assert (
        unknown_capability.json()["detail"]["code"]
        == "scientific_capability_not_found"
    )
    assert executor.calls == 0


def test_missing_or_noncanonical_declared_topology_is_controlled(
    tmp_path: Path,
) -> None:
    fixture = _structure_fixture("structure-invalid-topology")
    project = _project((fixture,))
    repository = _repository(tmp_path, ())
    application, executor = _application(
        tmp_path,
        project=project,
        repository=repository,
    )

    with TestClient(application) as client:
        response = client.post(
            _endpoint(project.project_identifier),
            json=_request(project),
        )

    assert response.status_code == 409
    assert response.json() == {
        "detail": {
            "code": "molecular_planning_evidence_invalid",
            "message": "Molecular planning evidence is unavailable or invalid.",
        }
    }
    assert str(tmp_path) not in response.text
    assert executor.calls == 0


@pytest.mark.parametrize("failure", ["malformed", "identity-mismatch"])
def test_invalid_declared_topology_fails_closed(
    tmp_path: Path,
    failure: str,
) -> None:
    fixture = _structure_fixture("structure-invalid-evidence")
    payload = (
        b"{}"
        if failure == "malformed"
        else _validated_replace(
            fixture.topology,
            structure_identifier="structure-other",
        ).to_canonical_json().encode("utf-8")
    )
    fixture = _fixture_with_topology_payload(fixture, payload)
    project = _project((fixture,))
    repository = _repository(tmp_path, (fixture,))
    application, executor = _application(
        tmp_path,
        project=project,
        repository=repository,
    )

    with TestClient(application) as client:
        response = client.post(
            _endpoint(project.project_identifier),
            json=_request(project),
        )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "molecular_planning_evidence_invalid"
    assert str(tmp_path) not in response.text
    assert executor.calls == 0


def test_planning_openapi_exposes_the_strict_request_and_response_contracts(
    tmp_path: Path,
) -> None:
    fixture = _structure_fixture("structure-openapi")
    project = _project((fixture,))
    application, _ = _application(
        tmp_path,
        project=project,
        repository=_repository(tmp_path, (fixture,)),
    )

    operation = application.openapi()["paths"][_endpoint("{project_identifier}")][
        "post"
    ]
    request_schema = operation["requestBody"]["content"]["application/json"][
        "schema"
    ]

    assert request_schema["additionalProperties"] is False
    assert set(request_schema["properties"]) == {
        "constraints",
        "objective",
        "requested_execution_target",
        "resources",
        "selected_capabilities",
    }
    assert "200" in operation["responses"]
    assert (
        operation["responses"]["200"]["content"]["application/json"]["schema"][
            "$ref"
        ]
        == "#/components/schemas/MolecularPlanningResponse"
    )


def test_request_body_and_collection_counts_are_bounded_before_planning(
    tmp_path: Path,
) -> None:
    fixture = _structure_fixture("structure-request-bounds")
    project = _project((fixture,))
    application, executor = _application(
        tmp_path,
        project=project,
        repository=_repository(tmp_path, (fixture,)),
    )
    too_many_capabilities = _request(project)
    too_many_capabilities["selected_capabilities"] = [
        {
            "capability_name": f"capability-{index}",
            "capability_version": VERSION.model_dump(mode="json"),
        }
        for index in range(MOLECULAR_PLANNING_CATALOGUE_MAXIMUM_CAPABILITIES + 1)
    ]
    too_many_structures = _request(project)
    too_many_structures["objective"]["structure_identifiers"] = [
        f"structure-{index}" for index in range(4097)
    ]
    excessive_wall_time = _request(project)
    excessive_wall_time["constraints"]["maximum_wall_time_seconds"] = 315_576_001
    oversized = b'{"padding":"' + (
        b"x" * MOLECULAR_PLANNING_REQUEST_MAXIMUM_BYTES
    ) + b'"}'

    with TestClient(application) as client:
        oversized_response = client.post(
            _endpoint(project.project_identifier),
            content=oversized,
            headers={"content-type": "application/json"},
        )
        capability_response = client.post(
            _endpoint(project.project_identifier),
            json=too_many_capabilities,
        )
        structure_response = client.post(
            _endpoint(project.project_identifier),
            json=too_many_structures,
        )
        wall_time_response = client.post(
            _endpoint(project.project_identifier),
            json=excessive_wall_time,
        )
        malformed_response = client.post(
            _endpoint(project.project_identifier),
            content=b'{"objective":',
            headers={"content-type": "application/json"},
        )
        unsupported_media_response = client.post(
            _endpoint(project.project_identifier),
            content=b"{}",
            headers={"content-type": "text/plain"},
        )

    assert oversized_response.status_code == 413
    assert oversized_response.json()["detail"]["code"] == (
        "molecular_planning_request_too_large"
    )
    for response in (
        capability_response,
        structure_response,
        wall_time_response,
        malformed_response,
    ):
        assert response.status_code == 422
        assert response.json() == {
            "detail": {
                "code": "invalid_molecular_planning_request",
                "message": "Molecular planning request is invalid.",
            }
        }
    assert unsupported_media_response.status_code == 415
    assert unsupported_media_response.json()["detail"]["code"] == (
        "invalid_molecular_planning_request"
    )
    assert executor.calls == 0


def test_declared_topology_bytes_are_bounded_before_repository_read(
    tmp_path: Path,
) -> None:
    fixture = _structure_fixture("structure-topology-limit")
    oversized_reference = _validated_replace(
        fixture.topology_artifact,
        byte_size=MOLECULAR_PLANNING_TOPOLOGY_MAXIMUM_BYTES + 1,
    )
    oversized_structure = _validated_replace(
        fixture.structure,
        topology_artifact=oversized_reference,
    )
    fixture = fixture.__class__(
        source_payload=fixture.source_payload,
        source_artifact=fixture.source_artifact,
        topology=fixture.topology,
        topology_payload=fixture.topology_payload,
        topology_artifact=oversized_reference,
        structure=oversized_structure,
    )
    project = _project((fixture,))
    application, executor = _application(
        tmp_path,
        project=project,
        repository=_repository(tmp_path, ()),
    )

    with TestClient(application) as client:
        response = client.post(
            _endpoint(project.project_identifier),
            json=_request(project),
        )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == (
        "molecular_planning_evidence_invalid"
    )
    assert executor.calls == 0


def test_unexpected_planner_failure_is_controlled_and_next_request_recovers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _structure_fixture("structure-planner-recovery")
    project = _project((fixture,))
    repository = _repository(tmp_path, (fixture,))
    application, executor = _application(
        tmp_path,
        project=project,
        repository=repository,
        catalogue=ScientificCapabilityCatalog((_envelope("capability-recovery"),)),
    )
    original = molecular_planning_module.construct_candidate_research_plan
    calls = 0

    def fail_once(**kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("internal path C:/private and secret material")
        return original(**kwargs)

    monkeypatch.setattr(
        molecular_planning_module,
        "construct_candidate_research_plan",
        fail_once,
    )
    before = _tree_snapshot(tmp_path / "artifacts")

    with TestClient(application) as client:
        failed = client.post(
            _endpoint(project.project_identifier),
            json=_request(project),
        )
        recovered = client.post(
            _endpoint(project.project_identifier),
            json=_request(project),
        )

    assert failed.status_code == 503
    assert failed.json() == {
        "detail": {
            "code": "molecular_planning_service_unavailable",
            "message": "Molecular planning service is unavailable.",
        }
    }
    assert "private" not in failed.text
    assert "secret" not in failed.text
    assert recovered.status_code == 200
    assert calls == 2
    assert executor.calls == 0
    assert _tree_snapshot(tmp_path / "artifacts") == before


def test_project_resolution_and_partial_startup_failures_are_controlled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _structure_fixture("structure-lifecycle-failure")
    project = _project((fixture,))
    repository = _repository(tmp_path, (fixture,))

    def unavailable_project(_identifier: str) -> MolecularProject | None:
        raise OSError("C:/private/project/repository")

    unavailable_app, unavailable_executor = _application(
        tmp_path / "unavailable",
        project=project,
        repository=repository,
        project_resolver=unavailable_project,
    )
    with TestClient(unavailable_app) as client:
        unavailable = client.post(
            _endpoint(project.project_identifier),
            json=_request(project),
        )
    assert unavailable.status_code == 503
    assert "private" not in unavailable.text
    assert unavailable_executor.calls == 0

    startup_repository = _repository(tmp_path / "startup", (fixture,))
    startup_app, startup_executor = _application(
        tmp_path / "startup-app",
        project=project,
        repository=startup_repository,
    )

    def fail_start() -> None:
        raise MolecularArtifactRepositoryError("C:/private/startup")

    monkeypatch.setattr(startup_repository, "start", fail_start)
    with pytest.raises(MolecularPlanningUnavailableError):
        with TestClient(startup_app):
            pass
    with pytest.raises(MolecularArtifactRepositoryError, match="not started"):
        startup_repository.read(fixture.topology_artifact)
    assert startup_executor.calls == 0


def test_concurrent_planning_is_deterministic_and_read_only(tmp_path: Path) -> None:
    fixture_a = _structure_fixture("structure-concurrent-a")
    fixture_b = _structure_fixture("structure-concurrent-b")

    def identified_project(fixture: Any, identifier: str) -> MolecularProject:
        project = _project((fixture,))
        scenes = tuple(
            _validated_replace(scene, project_identifier=identifier)
            for scene in project.scenes
        )
        return _validated_replace(
            project,
            project_identifier=identifier,
            scenes=scenes,
        )

    project_a = identified_project(fixture_a, "project-concurrent-a")
    project_b = identified_project(fixture_b, "project-concurrent-b")
    projects = {
        project_a.project_identifier: project_a,
        project_b.project_identifier: project_b,
    }
    repository = _repository(tmp_path, (fixture_a, fixture_b))
    service = MolecularPlanningService(
        project_resolver=projects.get,
        artifact_repository=repository,
        catalogue=ScientificCapabilityCatalog((_envelope("capability-concurrent"),)),
    )
    requests = {
        identifier: molecular_planning_module.MolecularPlanningRequest.model_validate(
            _request(project)
        )
        for identifier, project in projects.items()
    }
    before = _tree_snapshot(tmp_path / "artifacts")

    def evaluate(identifier: str) -> tuple[str, str]:
        response = service.evaluate(
            project_identifier=identifier,
            request=requests[identifier],
        )
        return response.project_identifier, response.plan_fingerprint

    service.start()
    try:
        identifiers = tuple(projects) * 8
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = tuple(pool.map(evaluate, identifiers))
    finally:
        service.close()

    for identifier in projects:
        fingerprints = {
            fingerprint
            for project_identifier, fingerprint in results
            if project_identifier == identifier
        }
        assert len(fingerprints) == 1
    assert {item[0] for item in results} == set(projects)
    assert _tree_snapshot(tmp_path / "artifacts") == before


def test_catalogue_size_is_bounded_at_service_configuration(tmp_path: Path) -> None:
    repository = MolecularArtifactRepository(tmp_path / "artifacts")
    catalogue = ScientificCapabilityCatalog(
        tuple(
            _envelope(f"capability-bounded-{index}")
            for index in range(
                MOLECULAR_PLANNING_CATALOGUE_MAXIMUM_CAPABILITIES + 1
            )
        )
    )

    with pytest.raises(ValueError, match="catalogue exceeds"):
        MolecularPlanningService(
            project_resolver=lambda _identifier: None,
            artifact_repository=repository,
            catalogue=catalogue,
        )
