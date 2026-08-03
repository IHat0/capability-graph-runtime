"""Pulsate product API regressions."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from cgr.pulsate_api.app import _load_preset, create_app
from cgr.pulsate_api.experiments import ExperimentStore
from cgr.pulsate_api.natural_language import NaturalLanguageInterpretationStore
from cgr.pulsate_api.runs import RunCoordinator


class _NoExecution:
    def execute(self, *_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("Pulsate API tests cannot execute runs.")


@pytest.fixture
def client(tmp_path: Path):
    application = create_app(
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
            unavailable_reason="disabled for API tests",
        ),
    )
    with TestClient(application) as active_client:
        yield active_client


def test_health_endpoint(client: TestClient) -> None:
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "service": "pulsate-api",
        "status": "healthy",
        "version": "0.2.0",
    }


def test_presets_are_discovered_from_manifests(client: TestClient) -> None:
    response = client.get("/api/v1/experiments/presets")

    assert response.status_code == 200
    payload = response.json()
    identifiers = {
        item["preset_identifier"]
        for item in payload["presets"]
    }

    assert "lih-ground-state-v1" in identifiers
    assert "h2-ground-state-v1" in identifiers
    assert payload["count"] >= 2


def test_h2_scene_uses_manifest_atom_data(client: TestClient) -> None:
    response = client.get(
        "/api/v1/experiments/presets/h2-ground-state-v1/scene"
    )

    assert response.status_code == 200
    scene = response.json()

    assert scene["scene_stage"] == "declared"
    assert [atom["element"] for atom in scene["atoms"]] == ["H", "H"]
    assert scene["quantum_region"]["atom_identifiers"] == ["h-1", "h-2"]

    bond = scene["bonds"][0]
    assert math.isclose(bond["declared_distance"], 0.735, abs_tol=1e-12)
    assert math.isclose(bond["derived_distance"], 0.735, abs_tol=1e-12)


def test_unknown_preset_is_not_found(client: TestClient) -> None:
    response = client.get(
        "/api/v1/experiments/presets/not-a-real-experiment/scene"
    )

    assert response.status_code == 404
