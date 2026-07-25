"""Opt-in real-Qiskit approved-experiment preflight with fake IBM retrieval."""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from test_pulsate_ibm import AuthorizedLocalExecutor
from test_pulsate_natural_language import (
    LIH_QUESTION,
    ControlledProvider,
    _complete_draft,
)

from cgr.pulsate_api.app import REPO_ROOT, _load_preset, create_app
from cgr.pulsate_api.experiments import ExperimentStore
from cgr.pulsate_api.ibm import (
    IBMQuantumConfiguration,
    IBMQuantumRunExecutor,
    IBMRuntimeResult,
)
from cgr.pulsate_api.natural_language import NaturalLanguageInterpretationStore
from cgr.pulsate_api.runs import (
    ExecutionOutput,
    ExistingQuantumPreflightExecutor,
    RunCoordinator,
)
from cgr.quantum_preflight.artifacts import write_json_atomic
from cgr.science import sha256_fingerprint

_GATE = "PULSATE_RUN_APPROVED_EXECUTION_INTEGRATION"
_NETWORK_GATE = "PULSATE_APPROVED_EXECUTION_NETWORK_BOUNDARY"
_NETWORK_BOUNDARY = "docker_network_none"
_ENABLED = (
    sys.platform == "linux"
    and os.environ.get(_GATE, "").lower() in {"1", "true"}
    and os.environ.get(_NETWORK_GATE) == _NETWORK_BOUNDARY
)

pytestmark = [
    pytest.mark.quantum_integration,
    pytest.mark.skipif(
        not _ENABLED,
        reason=(
            f"Set {_GATE}=true and {_NETWORK_GATE}={_NETWORK_BOUNDARY} "
            "inside the pinned network-disabled Linux quantum environment."
        ),
    ),
]


def _wait_for_terminal(
    client: TestClient,
    run_identifier: str,
) -> dict[str, Any]:
    deadline = time.monotonic() + 700
    while time.monotonic() < deadline:
        state = client.get(f"/api/v1/runs/{run_identifier}").json()
        if state["status"] in {"authorized", "rejected", "failed", "interrupted"}:
            return state
        time.sleep(0.1)
    raise AssertionError("Approved real-Qiskit integration did not terminate.")


class _NetworkIsolatedExistingQuantumPreflightExecutor(
    ExistingQuantumPreflightExecutor
):
    """Run the real worker only inside the explicitly gated no-network image."""

    proven_no_network = True

    def __init__(
        self,
        *,
        scientific_image_identifier: str,
        ibm_runtime_image_identifier: str,
    ) -> None:
        super().__init__(
            repository_root=REPO_ROOT,
            image_identifier=scientific_image_identifier,
        )
        self.scientific_image_identifier = scientific_image_identifier
        self.ibm_runtime_image_identifier = ibm_runtime_image_identifier
        self.real_worker_calls = 0

    def execute(
        self,
        manifest: Any,
        *,
        preset_identifier: str,
        run_directory: Path,
        maximum_seconds: int,
    ) -> ExecutionOutput:
        if (
            sys.platform != "linux"
            or os.environ.get(_NETWORK_GATE) != _NETWORK_BOUNDARY
        ):
            raise RuntimeError(
                "The real approved preflight lacks its OS-enforced no-network gate."
            )
        self.real_worker_calls += 1
        output = super().execute_ibm_preflight(
            manifest,
            preset_identifier=preset_identifier,
            run_directory=run_directory,
            maximum_seconds=maximum_seconds,
        )
        preflight = output.runner_summary.get("ibm_preflight")
        if not isinstance(preflight, dict):
            raise TypeError(
                "The actual Qiskit worker did not produce IBM preflight evidence."
            )
        summary = {
            **output.runner_summary,
            "ibm_preflight": {
                **preflight,
                "scientific_preflight_image_identifier": (
                    self.scientific_image_identifier
                ),
                "ibm_runtime_image_identifier": (
                    self.ibm_runtime_image_identifier
                ),
            },
        }
        return ExecutionOutput(
            results=output.results,
            verification=output.verification,
            receipt=output.receipt,
            runner_summary=summary,
        )


class _RealPreflightFakeIBMAdapter:
    """Deterministic, local-only fake result bound to real preflight evidence."""

    def __init__(self) -> None:
        self.submissions = 0
        self.retrievals = 0

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
        if job_record_path.exists():
            self.retrievals += 1
            job = json.loads(job_record_path.read_text(encoding="utf-8"))
        else:
            self.submissions += 1
            job = {
                "bundle_sha256": bundle.bundle_sha256,
                "job_identifier": "fake-approved-real-qiskit-job-0001",
                "backend_name": bundle.backend_name,
                "submitted_at": "2026-07-25T00:00:00Z",
            }
            write_json_atomic(
                job_record_path,
                job,
                maximum_bytes=100_000,
            )
        local = json.loads(
            (work_directory / "local-preflight.json").read_text(
                encoding="utf-8"
            )
        )
        nuclear = float(
            local["runner_summary"]["ibm_preflight"][
                "nuclear_repulsion_energy_hartree"
            ]
        )
        total = float(local["results"]["vqe_total_energy_hartree"])
        qubits = tuple(range(bundle.required_qubits))
        status_callback(
            "queued_on_ibm",
            {"ibm_job_identifier": job["job_identifier"]},
        )
        status_callback(
            "running_on_ibm",
            {"ibm_job_identifier": job["job_identifier"]},
        )
        return IBMRuntimeResult(
            bundle_sha256=bundle.bundle_sha256,
            job_identifier=job["job_identifier"],
            backend_name=bundle.backend_name,
            primitive_version="approved-real-qiskit-fake/1",
            submitted_at=job["submitted_at"],
            completed_at="2026-07-25T00:00:01Z",
            job_status="completed",
            target_precision=bundle.target_precision,
            raw_qubit_expectation_hartree=total - nuclear,
            non_nuclear_electronic_shift_hartree=0.0,
            electronic_constant_offsets_hartree={},
            nuclear_repulsion_energy_hartree=nuclear,
            ibm_electronic_energy_hartree=total - nuclear,
            standard_error=0.0,
            execution_metadata={"adapter": "local_only_fake"},
            optimization_level=bundle.optimization_level,
            layout_sha256=sha256_fingerprint({"physical_qubits": qubits}),
            physical_qubits=qubits,
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


def _coordinator(
    root: Path,
    preflight: _NetworkIsolatedExistingQuantumPreflightExecutor,
    adapter: _RealPreflightFakeIBMAdapter,
    *,
    ibm_runtime_image_identifier: str,
) -> RunCoordinator:
    assert type(preflight) is _NetworkIsolatedExistingQuantumPreflightExecutor
    assert isinstance(preflight, ExistingQuantumPreflightExecutor)
    configuration = IBMQuantumConfiguration(
        token=None,
        instance=None,
        backend_name="approved_real_qiskit_fake_backend",
        target_precision=0.5,
        optimization_level=2,
        maximum_seconds=600,
        dependency_available=True,
        image_identifier=ibm_runtime_image_identifier,
        backend_qubit_capacity=32,
        requires_provider_credentials=False,
    )
    return RunCoordinator(
        run_root=root / "runs",
        manifest_resolver=_load_preset,
        executor=preflight,
        ibm_executor=IBMQuantumRunExecutor(
            local_executor=preflight,
            adapter=adapter,
            configuration=configuration,
        ),
        enabled=True,
        max_run_seconds=600,
    )


def test_real_qiskit_approved_lih_preflight_reaches_only_fake_ibm(
    tmp_path: Path,
) -> None:
    for variable in (
        "PULSATE_RUN_IBM_INTEGRATION",
        "PULSATE_IBM_ACKNOWLEDGE_COSTS",
        "PULSATE_IBM_QUANTUM_TOKEN",
        "PULSATE_IBM_QUANTUM_INSTANCE",
    ):
        assert not os.environ.get(variable)
    scientific_image_identifier = os.environ[
        "PULSATE_IBM_SCIENTIFIC_IMAGE_IDENTIFIER"
    ]
    ibm_runtime_image_identifier = os.environ[
        "PULSATE_IBM_IMAGE_IDENTIFIER"
    ]
    assert re.fullmatch(
        r"sha256:[0-9a-f]{64}",
        scientific_image_identifier,
    )
    assert re.fullmatch(
        r"sha256:[0-9a-f]{64}",
        ibm_runtime_image_identifier,
    )
    assert scientific_image_identifier != ibm_runtime_image_identifier

    provider = ControlledProvider([json.dumps(_complete_draft())])
    natural_store = NaturalLanguageInterpretationStore(
        tmp_path / "interpretations",
        provider,
    )
    experiment_store = ExperimentStore(tmp_path / "dynamic-experiments")
    adapter = _RealPreflightFakeIBMAdapter()
    preflight = _NetworkIsolatedExistingQuantumPreflightExecutor(
        scientific_image_identifier=scientific_image_identifier,
        ibm_runtime_image_identifier=ibm_runtime_image_identifier,
    )
    coordinator = _coordinator(
        tmp_path,
        preflight,
        adapter,
        ibm_runtime_image_identifier=ibm_runtime_image_identifier,
    )
    with TestClient(
        create_app(
            coordinator=coordinator,
            experiment_store=experiment_store,
            natural_language_store=natural_store,
        )
    ) as client:
        interpreted = client.post(
            "/api/v1/experiments/interpret",
            json={"question": LIH_QUESTION},
        )
        assert interpreted.status_code == 201
        interpretation = interpreted.json()
        approved_response = client.post(
            f"/api/v1/experiments/{interpretation['interpretation_identifier']}/approve",
            json={
                "specification": interpretation["specification"],
                "accepted_assumptions": True,
            },
        )
        assert approved_response.status_code == 201
        approved = approved_response.json()
        created = client.post(
            "/api/v1/runs",
            headers={
                "Idempotency-Key": "approved-real-qiskit-fake-ibm-0001"
            },
            json={
                "approved_experiment_identifier": approved[
                    "experiment_identifier"
                ],
                "execution_target": "ibm_quantum",
            },
        )
        assert created.status_code == 202, created.json()
        terminal = _wait_for_terminal(
            client,
            created.json()["run_identifier"],
        )
        assert terminal["status"] == "authorized", terminal
        results = client.get(
            f"/api/v1/runs/{terminal['run_identifier']}/results"
        ).json()
        verification = client.get(
            f"/api/v1/runs/{terminal['run_identifier']}/verification"
        ).json()
        receipt = client.get(
            f"/api/v1/runs/{terminal['run_identifier']}/receipt"
        ).json()

    assert preflight.real_worker_calls == 1
    assert adapter.submissions == 1
    assert provider.request_count == 1
    run_directory = tmp_path / "runs" / terminal["run_identifier"]
    manifest = json.loads(
        (run_directory / "compiled-manifest.json").read_text(encoding="utf-8")
    )
    provenance = receipt["approved_experiment"]
    assert (
        provenance["approved_specification_sha256"]
        == approved["specification_sha256"]
    )
    assert (
        provenance["compiled_execution_manifest_sha256"]
        == sha256_fingerprint(manifest)
    )
    assert provenance["molecule_structure_sha256"] == results["structure_sha256"]
    assert results["hamiltonian_sha256"] == receipt["hamiltonian_sha256"]
    ibm = receipt["ibm_execution"]
    assert ibm["source_bound_circuit_sha256"]
    assert ibm["source_observable_sha256"]
    assert ibm["prepared_submission_sha256"]
    assert ibm["job_identifier"] == "fake-approved-real-qiskit-job-0001"
    assert results["receipt_sha256"] == receipt["receipt_sha256"]
    assert verification["verification_passed"] is True
    assert receipt["authorized"] is True

    artifact_files = {
        path.name
        for path in (run_directory / "runner-artifacts").rglob("*.json")
    }
    assert {
        "active-space.json",
        "fermionic-hamiltonian.json",
        "qubit-hamiltonian.json",
        "ansatz-manifest.json",
    }.issubset(artifact_files)
    assert (
        receipt["ibm_execution"]["source_bound_circuit_sha256"]
        == json.loads(
            (
                run_directory
                / "quantum-worker"
                / "ibm-preflight-evidence.json"
            ).read_text(encoding="utf-8")
        )["source_bound_circuit_sha256"]
    )
    assert (
        receipt["ibm_execution"]["source_observable_sha256"]
        == json.loads(
            (
                run_directory
                / "quantum-worker"
                / "ibm-preflight-evidence.json"
            ).read_text(encoding="utf-8")
        )["source_observable_sha256"]
    )

    state_path = run_directory / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["status"] = "running_on_ibm"
    write_json_atomic(state_path, state, maximum_bytes=2 * 1024 * 1024)
    recovered = _coordinator(
        tmp_path,
        preflight,
        adapter,
        ibm_runtime_image_identifier=ibm_runtime_image_identifier,
    )
    with TestClient(
        create_app(
            coordinator=recovered,
            experiment_store=experiment_store,
            natural_language_store=natural_store,
        )
    ) as client:
        recovered_state = _wait_for_terminal(
            client,
            terminal["run_identifier"],
        )
        recovered_receipt = client.get(
            f"/api/v1/runs/{terminal['run_identifier']}/receipt"
        ).json()
    assert recovered_state["status"] == "authorized"
    assert (
        recovered_receipt["ibm_execution"]["job_identifier"]
        == "fake-approved-real-qiskit-job-0001"
    )
    assert preflight.real_worker_calls == 1
    assert adapter.submissions == 1
    assert adapter.retrievals == 1
    assert provider.request_count == 1


def test_integration_uses_real_existing_worker_type() -> None:
    assert issubclass(
        _NetworkIsolatedExistingQuantumPreflightExecutor,
        ExistingQuantumPreflightExecutor,
    )
    assert not issubclass(
        _NetworkIsolatedExistingQuantumPreflightExecutor,
        AuthorizedLocalExecutor,
    )
