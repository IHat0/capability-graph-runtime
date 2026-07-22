from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cgr.pulsate_api.app import REPO_ROOT, _load_preset, create_app
from cgr.pulsate_api.runs import ExistingQuantumPreflightExecutor, RunCoordinator


@pytest.mark.quantum_integration
def test_real_preset_completes_through_http_api(tmp_path: Path) -> None:
    """Opt-in end-to-end HTTP check for the pinned quantum environment."""
    if os.environ.get("PULSATE_RUN_HTTP_INTEGRATION", "").lower() not in {"1", "true"}:
        pytest.skip("Set PULSATE_RUN_HTTP_INTEGRATION=true in the pinned Linux quantum environment.")
    coordinator = RunCoordinator(
        run_root=tmp_path / "runs",
        manifest_resolver=_load_preset,
        executor=ExistingQuantumPreflightExecutor(
            repository_root=REPO_ROOT,
            image_identifier=os.environ.get("PULSATE_QUANTUM_IMAGE_IDENTIFIER", "quantum-preflight-integration"),
        ),
        enabled=True,
    )
    with TestClient(create_app(coordinator=coordinator)) as client:
        created = client.post(
            "/api/v1/runs",
            headers={"Idempotency-Key": "real-http-integration-0001"},
            json={"preset_identifier": "h2-ground-state-v1", "execution_target": "local_simulator"},
        )
        assert created.status_code == 202
        run_identifier = created.json()["run_identifier"]
        for _ in range(360):
            state = client.get(f"/api/v1/runs/{run_identifier}").json()
            if state["status"] in {"authorized", "rejected", "failed", "interrupted"}:
                break
            time.sleep(0.5)
        assert state["status"] == "authorized", state
        assert client.get(f"/api/v1/runs/{run_identifier}/results").status_code == 200
        assert client.get(f"/api/v1/runs/{run_identifier}/verification").json()["verification_passed"] is True
        assert client.get(f"/api/v1/runs/{run_identifier}/receipt").json()["authorized"] is True


@pytest.mark.quantum_integration
def test_real_dynamic_lih_1_8_angstrom_completes_through_http_api(tmp_path: Path) -> None:
    """Opt-in proof that dynamic intake reaches the same pinned scientific path."""
    if os.environ.get("PULSATE_RUN_HTTP_INTEGRATION", "").lower() not in {"1", "true"}:
        pytest.skip("Set PULSATE_RUN_HTTP_INTEGRATION=true in the pinned Linux quantum environment.")
    def reject_preset_load(_identifier: str):
        raise AssertionError("The dynamic integration must not load a preset manifest.")

    coordinator = RunCoordinator(
        run_root=tmp_path / "runs",
        manifest_resolver=reject_preset_load,
        executor=ExistingQuantumPreflightExecutor(
            repository_root=REPO_ROOT,
            image_identifier=os.environ.get("PULSATE_QUANTUM_IMAGE_IDENTIFIER", "quantum-preflight-integration"),
        ),
        enabled=True,
    )
    with TestClient(create_app(coordinator=coordinator)) as client:
        planned = client.post(
            "/api/v1/experiments/plan",
            json={"question": "Compute the ground-state energy of LiH at 1.8 angstrom"},
        )
        assert planned.status_code == 201
        plan = planned.json()
        assert plan["ready_for_execution"] is True
        assert plan["molecule"]["elements"] == ["Li", "H"]

        created = client.post(
            "/api/v1/runs",
            headers={"Idempotency-Key": "real-dynamic-lih-1-8-0001"},
            json={
                "experiment_identifier": plan["experiment_identifier"],
                "execution_target": "local_simulator",
            },
        )
        assert created.status_code == 202
        run_identifier = created.json()["run_identifier"]
        for _ in range(360):
            state = client.get(f"/api/v1/runs/{run_identifier}").json()
            if state["status"] in {"authorized", "rejected", "failed", "interrupted"}:
                break
            time.sleep(0.5)
        assert state["status"] == "authorized", state
        assert state["molecule"] == plan["molecule"]
        assert state["source_type"] == "dynamic_experiment"
        assert state["source_identifier"] == plan["experiment_identifier"]
        assert state["preset_identifier"] is None
        assert state["experiment_fingerprint"] == plan["experiment_fingerprint"]
        assert state["expected_experiment_sha256"] == plan["expected_experiment_sha256"]
        assert state["structure_sha256"] == plan["molecule"]["structure_hash"]

        results_response = client.get(f"/api/v1/runs/{run_identifier}/results")
        verification = client.get(f"/api/v1/runs/{run_identifier}/verification").json()
        receipt = client.get(f"/api/v1/runs/{run_identifier}/receipt").json()
        assert results_response.status_code == 200
        assert verification["verification_passed"] is True
        assert receipt["authorized"] is True
        assert receipt["verification_passed"] is True
        assert receipt["structure_sha256"] == plan["molecule"]["structure_hash"]
        assert receipt["experiment_identifier"] == plan["experiment_identifier"]
        assert receipt["experiment_fingerprint"] == plan["experiment_fingerprint"]
        assert receipt["expected_experiment_sha256"] == plan["expected_experiment_sha256"]
