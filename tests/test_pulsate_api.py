"""Pulsate product API regressions."""

from __future__ import annotations

import math

from fastapi.testclient import TestClient

from cgr.pulsate_api.app import app

CLIENT = TestClient(app)


def test_health_endpoint() -> None:
    response = CLIENT.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "service": "pulsate-api",
        "status": "healthy",
        "version": "0.1.0",
    }


def test_presets_are_discovered_from_manifests() -> None:
    response = CLIENT.get("/api/v1/experiments/presets")

    assert response.status_code == 200
    payload = response.json()
    identifiers = {
        item["preset_identifier"]
        for item in payload["presets"]
    }

    assert "lih-ground-state-v1" in identifiers
    assert "h2-ground-state-v1" in identifiers
    assert payload["count"] >= 2


def test_h2_scene_uses_manifest_atom_data() -> None:
    response = CLIENT.get(
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


def test_unknown_preset_is_not_found() -> None:
    response = CLIENT.get(
        "/api/v1/experiments/presets/not-a-real-experiment/scene"
    )

    assert response.status_code == 404
