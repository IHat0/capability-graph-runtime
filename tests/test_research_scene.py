"""Exact-evidence research scene regressions."""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from cgr.kernel.contracts import CapabilityVersion
from cgr.pulsate_api.app import _load_preset, create_app
from cgr.pulsate_api.experiments import ExperimentStore
from cgr.pulsate_api.natural_language import NaturalLanguageInterpretationStore
from cgr.pulsate_api.runs import RunCoordinator
from cgr.pulsate_api.scientific_production import DurableScientificPayloadStore
from cgr.pulsate_api.security import (
    InMemoryAuditSink,
    InMemoryGrantProvider,
    SecurityServices,
    StaticAuthenticator,
)
from cgr.pulsate_api.security_contracts import (
    AuthenticatedPrincipal,
    ProtectedAction,
    ResourceGrant,
)
from cgr.pulsate_api.workflows import WorkflowService
from cgr.workflow_graph import CapabilityAdapterRegistry


class _NoExecution:
    def execute(self, *_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("Research scene tests cannot execute preset runs.")


def _pdb_payload() -> bytes:
    return (
        "ATOM      1  C1  LIG A   1      10.125  11.250  12.375  1.00 20.00           C  \n"
        "ATOM      2  O1  LIG A   1      11.125  11.250  12.375  1.00 20.00           O  \n"
        "CONECT    1    2\n"
        "END\n"
    ).encode("utf-8")


def _application(tmp_path: Path):
    principal = AuthenticatedPrincipal(
        subject_identifier="scientist-alpha",
        tenant_identifier="tenant-alpha",
        authentication_method="test-bearer",
        scopes=tuple(action.value for action in ProtectedAction),
        audit_identifier="scientist-alpha",
    )
    return create_app(
        coordinator=RunCoordinator(
            run_root=tmp_path / "runs",
            manifest_resolver=_load_preset,
            executor=_NoExecution(),
            enabled=False,
        ),
        experiment_store=ExperimentStore(tmp_path / "experiments"),
        natural_language_store=NaturalLanguageInterpretationStore(
            tmp_path / "interpretations",
            None,
            unavailable_reason="disabled for research scene tests",
        ),
        workflow_service=WorkflowService(
            root=tmp_path / "workflows",
            capability_adapter=CapabilityAdapterRegistry(),
            maximum_parallelism=1,
        ),
        scientific_payload_store=DurableScientificPayloadStore(
            tmp_path / "scientific-artifacts"
        ),
        security_services=SecurityServices(
            authenticator=StaticAuthenticator({"alpha-token": principal}),
            grant_provider=InMemoryGrantProvider(
                (
                    ResourceGrant(
                        tenant_identifier=principal.tenant_identifier,
                        subject_identifier=principal.subject_identifier,
                        resource_type="scientific-execution",
                        resource_identifier="*",
                        allowed_actions=tuple(ProtectedAction),
                    ),
                ),
                allow_wildcards=True,
            ),
            audit_sink=InMemoryAuditSink(),
        ),
    )


def test_research_scene_projects_only_exact_uploaded_coordinates(tmp_path: Path) -> None:
    payload = _pdb_payload()
    digest = hashlib.sha256(payload).hexdigest()
    headers = {"Authorization": "Bearer alpha-token"}
    with TestClient(_application(tmp_path)) as client:
        uploaded = client.post(
            "/api/v1/scientific/artifacts",
            headers=headers,
            json={
                "reference": {
                    "artifact_identifier": f"scientific-input-{digest[:32]}",
                    "schema_version": CapabilityVersion(
                        major=1, minor=0, patch=0
                    ).model_dump(mode="json"),
                    "artifact_type": "molecular_structure",
                    "media_type": "chemical/x-pdb",
                    "content_sha256": digest,
                    "byte_size": len(payload),
                    "storage_location": None,
                    "metadata": {},
                    "provenance": {
                        "producer": "research-scene-tests",
                        "producer_version": {
                            "major": 1,
                            "minor": 0,
                            "patch": 0,
                        },
                        "execution_identifier": (
                            f"scientific-input-upload-{digest[:24]}"
                        ),
                        "source": "cgr",
                    },
                    "parents": [],
                },
                "payload_base64": base64.b64encode(payload).decode("ascii"),
            },
        )
        assert uploaded.status_code == 201, uploaded.text
        artifact = uploaded.json()
        created = client.post(
            "/api/v1/research/sessions",
            headers=headers,
            json={
                "question": "Please investigate this system.",
                "input_references": [
                    {
                        "reference_identifier": "input-01-molecular-structure",
                        "artifact_type": "molecular_structure",
                        "artifact_identifier": artifact["artifact_identifier"],
                    }
                ],
                "artifact_references": [artifact],
            },
        )
        assert created.status_code == 201, created.text

        scene = client.get(
            f"/api/v1/research/sessions/{created.json()['session_identifier']}/scene",
            params={"artifact_identifier": artifact["artifact_identifier"]},
            headers=headers,
        )
        visualization = client.get(
            f"/api/v1/research/sessions/{created.json()['session_identifier']}/visualization",
            headers=headers,
        )
        downloaded = client.get(
            f"/api/v1/research/sessions/{created.json()['session_identifier']}/artifacts/{artifact['artifact_identifier']}",
            headers=headers,
        )

    assert scene.status_code == 200, scene.text
    body = scene.json()
    assert body["scene_stage"] == "scientist_evidence"
    assert body["structure_hash"] == digest
    assert [atom["coordinates"] for atom in body["atoms"]] == [
        [10.125, 11.25, 12.375],
        [11.125, 11.25, 12.375],
    ]
    assert body["bonds"][0]["atom_identifiers"] == [
        body["atoms"][0]["atom_identifier"],
        body["atoms"][1]["atom_identifier"],
    ]
    assert visualization.status_code == 200, visualization.text
    visualization_body = visualization.json()
    assert visualization_body["grounding_policy"] == (
        "persisted_artifact_or_deterministic_computation_only"
    )
    assert visualization_body["structures"][0]["artifact_identifier"] == (
        artifact["artifact_identifier"]
    )
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.content == payload
    assert downloaded.headers["x-content-sha256"] == digest


def test_research_scene_never_exposes_an_artifact_from_another_session(
    tmp_path: Path,
) -> None:
    payload = _pdb_payload()
    digest = hashlib.sha256(payload).hexdigest()
    headers = {"Authorization": "Bearer alpha-token"}
    with TestClient(_application(tmp_path)) as client:
        uploaded = client.post(
            "/api/v1/scientific/artifacts",
            headers=headers,
            json={
                "reference": {
                    "artifact_identifier": f"scientific-input-{digest[:32]}",
                    "schema_version": {"major": 1, "minor": 0, "patch": 0},
                    "artifact_type": "molecular_structure",
                    "media_type": "chemical/x-pdb",
                    "content_sha256": digest,
                    "byte_size": len(payload),
                    "storage_location": None,
                    "metadata": {},
                    "provenance": {
                        "producer": "research-scene-tests",
                        "producer_version": None,
                        "execution_identifier": None,
                        "source": "cgr",
                    },
                    "parents": [],
                },
                "payload_base64": base64.b64encode(payload).decode("ascii"),
            },
        ).json()
        created = client.post(
            "/api/v1/research/sessions",
            headers=headers,
            json={"question": "Please investigate a different system."},
        ).json()
        response = client.get(
            f"/api/v1/research/sessions/{created['session_identifier']}/scene",
            params={"artifact_identifier": uploaded["artifact_identifier"]},
            headers=headers,
        )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "research_scene_not_found"
