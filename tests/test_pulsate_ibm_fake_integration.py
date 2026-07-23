"""Opt-in, non-paid two-container IBM orchestration acceptance."""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from cgr.pulsate_api.app import _load_preset, create_app
from cgr.pulsate_api.experiments import ExperimentStore
from cgr.pulsate_api.ibm import (
    IBMQuantumConfiguration,
    IBMQuantumRunExecutor,
    IBMRuntimeResult,
    RunBoundIsolatedIBMPreflightExecutor,
)
from cgr.pulsate_api.runs import (
    ExistingQuantumPreflightExecutor,
    RecoverableIBMJobError,
    RunCoordinator,
)
from cgr.quantum_preflight.artifacts import write_json_atomic
from cgr.science import sha256_fingerprint


_ENABLED = os.environ.get("PULSATE_IBM_FAKE_ACCEPTANCE") == "true"
pytestmark = pytest.mark.skipif(
    not _ENABLED,
    reason="Run through scripts/run-pulsate-ibm-fake-integration.sh.",
)


def _root() -> Path:
    return Path(os.environ["PULSATE_IBM_SHARED_ROOT"])


def _assert_no_live_ibm_authority() -> None:
    for variable in (
        "PULSATE_RUN_IBM_INTEGRATION",
        "PULSATE_IBM_ACKNOWLEDGE_COSTS",
        "PULSATE_IBM_QUANTUM_TOKEN",
        "PULSATE_IBM_QUANTUM_INSTANCE",
        "PULSATE_IBM_QUANTUM_BACKEND",
    ):
        assert not os.environ.get(variable)


class _NetworkLocalFakeAdapter:
    def __init__(self, *, fail_after_submit: bool) -> None:
        self.fail_after_submit = fail_after_submit

    def execute(
        self,
        bundle: Any,
        manifest: Any,
        *,
        work_directory: Path,
        job_record_path: Path,
        maximum_seconds: int,
        status_callback: Any,
    ) -> IBMRuntimeResult:
        del manifest, maximum_seconds
        with urllib.request.urlopen(
            os.environ["PULSATE_IBM_FAKE_ENDPOINT_URL"], timeout=5
        ) as response:
            assert response.status == 200
        if not job_record_path.exists():
            counter = _root() / "fake-submission-count.json"
            count = (
                0
                if not counter.exists()
                else json.loads(counter.read_text(encoding="utf-8"))["count"]
            )
            write_json_atomic(counter, {"count": count + 1}, maximum_bytes=1024)
            write_json_atomic(
                job_record_path,
                {
                    "bundle_sha256": bundle.bundle_sha256,
                    "job_identifier": "fake-network-job-0001",
                    "backend_name": bundle.backend_name,
                    "submitted_at": "2026-07-23T00:00:00Z",
                },
                maximum_bytes=100_000,
            )
            if self.fail_after_submit:
                raise RecoverableIBMJobError(
                    "Fake retrieval interruption.",
                    job_identifier="fake-network-job-0001",
                )
        local = json.loads(
            (work_directory / "local-preflight.json").read_text(encoding="utf-8")
        )
        nuclear = float(
            local["runner_summary"]["ibm_preflight"][
                "nuclear_repulsion_energy_hartree"
            ]
        )
        total = float(local["results"]["vqe_total_energy_hartree"])
        status_callback(
            "running_on_ibm", {"ibm_job_identifier": "fake-network-job-0001"}
        )
        return IBMRuntimeResult(
            bundle_sha256=bundle.bundle_sha256,
            job_identifier="fake-network-job-0001",
            backend_name=bundle.backend_name,
            primitive_version="fake-network-adapter/1",
            submitted_at="2026-07-23T00:00:00Z",
            completed_at="2026-07-23T00:00:01Z",
            job_status="completed",
            target_precision=bundle.target_precision,
            raw_qubit_expectation_hartree=total - nuclear,
            non_nuclear_electronic_shift_hartree=0.0,
            electronic_constant_offsets_hartree={},
            nuclear_repulsion_energy_hartree=nuclear,
            ibm_electronic_energy_hartree=total - nuclear,
            standard_error=0.0,
            execution_metadata={"adapter": "network_local_fake"},
            optimization_level=bundle.optimization_level,
            layout_sha256=sha256_fingerprint(
                {"physical_qubits": tuple(range(bundle.required_qubits))}
            ),
            physical_qubits=tuple(range(bundle.required_qubits)),
            source_bound_circuit_sha256=bundle.source_bound_circuit_sha256,
            transpiled_circuit_sha256="8" * 64,
            source_observable_sha256=bundle.source_observable_sha256,
            transpiled_observable_sha256="9" * 64,
            optimized_parameters_sha256=bundle.optimized_parameters_sha256,
            experiment_sha256=bundle.experiment_sha256,
            structure_sha256=bundle.structure_sha256,
            hamiltonian_sha256=bundle.hamiltonian_sha256,
            package_versions={"fake-adapter": "1"},
            runtime_options={
                "max_execution_time": bundle.maximum_execution_time_seconds,
                "job_tags": [bundle.job_correlation_identifier],
            },
            execution_image_identifier=bundle.ibm_runtime_image_identifier,
        )


def _wait(client: TestClient, run_identifier: str, expected: set[str]) -> dict[str, Any]:
    deadline = time.monotonic() + 700
    while time.monotonic() < deadline:
        state = client.get(f"/api/v1/runs/{run_identifier}").json()
        if state["status"] in expected:
            return state
        time.sleep(0.1)
    raise AssertionError("Fake IBM run did not reach its expected state.")


def _configured_api(
    *,
    fail_after_submit: bool,
) -> tuple[RunCoordinator, ExperimentStore]:
    scientific_image = os.environ["PULSATE_IBM_SCIENTIFIC_IMAGE_IDENTIFIER"]
    ibm_image = os.environ["PULSATE_IBM_IMAGE_IDENTIFIER"]
    preflight = RunBoundIsolatedIBMPreflightExecutor(
        _root() / "exchange",
        scientific_preflight_image_identifier=scientific_image,
        ibm_runtime_image_identifier=ibm_image,
    )
    configuration = IBMQuantumConfiguration(
        token=None,
        instance=None,
        backend_name="network_local_fake_backend",
        target_precision=0.015,
        optimization_level=2,
        maximum_seconds=120,
        dependency_available=True,
        image_identifier=ibm_image,
        backend_qubit_capacity=32,
        requires_provider_credentials=False,
    )
    ibm = IBMQuantumRunExecutor(
        local_executor=preflight,
        adapter=_NetworkLocalFakeAdapter(fail_after_submit=fail_after_submit),
        configuration=configuration,
    )
    store = ExperimentStore(_root() / "experiments")
    coordinator = RunCoordinator(
        run_root=_root() / "runs",
        manifest_resolver=_load_preset,
        experiment_resolver=store.resolve_for_targeted_run,
        executor=ExistingQuantumPreflightExecutor(
            repository_root=Path(__file__).resolve().parents[1],
            image_identifier=scientific_image,
        ),
        ibm_executor=ibm,
        enabled=True,
        max_run_seconds=600,
    )
    return coordinator, store


def test_nonpaid_run_bound_fake_acceptance() -> None:
    _assert_no_live_ibm_authority()
    scientific_image = os.environ["PULSATE_IBM_SCIENTIFIC_IMAGE_IDENTIFIER"]
    ibm_image = os.environ["PULSATE_IBM_IMAGE_IDENTIFIER"]
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", scientific_image)
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", ibm_image)
    assert scientific_image != ibm_image

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"network-local fake IBM endpoint")

        def log_message(self, *_args: Any) -> None:
            return None

    endpoint = ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
    endpoint_thread = threading.Thread(target=endpoint.serve_forever, daemon=True)
    endpoint_thread.start()
    try:
        first, first_store = _configured_api(fail_after_submit=True)
        with TestClient(
            create_app(coordinator=first, experiment_store=first_store)
        ) as client:
            capability = client.get("/api/v1/runs/capability").json()
            assert capability["ibm_quantum"]["available"] is True
            plan_response = client.post(
                "/api/v1/experiments/plan",
                json={
                    "question": (
                        "Calculate the ground-state energy of H2 at 0.735 "
                        "angstrom on IBM Quantum"
                    )
                },
            )
            assert plan_response.status_code == 201
            plan = plan_response.json()
            created_response = client.post(
                "/api/v1/runs",
                headers={"Idempotency-Key": "fake-two-container-ibm-0001"},
                json={
                    "experiment_identifier": plan["experiment_identifier"],
                    "execution_target": "ibm_quantum",
                },
            )
            assert created_response.status_code == 202
            created = created_response.json()
            run_identifier = created["run_identifier"]
            assert re.fullmatch(r"run-[0-9a-f]{32}", run_identifier)
            assert run_identifier != "run-" + "a" * 32
            queued = _wait(client, run_identifier, {"queued_on_ibm", "failed"})
            assert queued["status"] == "queued_on_ibm"
            assert queued["recoverable_ibm_retrieval"] is True

        handoff_path = (
            _root() / "exchange" / "handoffs" / f"{run_identifier}.json"
        )
        handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
        assert handoff["run_identifier"] == run_identifier
        assert (
            handoff["scientific_preflight_image_identifier"] == scientific_image
        )
        assert handoff["ibm_runtime_image_identifier"] == ibm_image

        second, second_store = _configured_api(fail_after_submit=False)
        with TestClient(
            create_app(coordinator=second, experiment_store=second_store)
        ) as client:
            terminal = _wait(client, run_identifier, {"authorized", "failed"})
            results = client.get(
                f"/api/v1/runs/{run_identifier}/results"
            ).json()
            receipt = client.get(
                f"/api/v1/runs/{run_identifier}/receipt"
            ).json()
        assert terminal["status"] == "authorized"
        assert (
            json.loads(
                (_root() / "fake-submission-count.json").read_text(
                    encoding="utf-8"
                )
            )["count"]
            == 1
        )
        evidence = receipt["ibm_execution"]
        assert results["structure_sha256"] == plan["structure_hash"]
        assert evidence["structure_sha256"] == plan["structure_hash"]
        assert evidence["scientific_preflight_image_identifier"] == scientific_image
        assert evidence["ibm_runtime_image_identifier"] == ibm_image
        assert receipt["authorized"] is True
    finally:
        endpoint.shutdown()
        endpoint.server_close()
        endpoint_thread.join(timeout=2)
