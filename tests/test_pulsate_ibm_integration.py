from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from cgr.pulsate_api.experiments import ExperimentStore
from cgr.pulsate_api.ibm import (
    IBMQuantumConfiguration,
    IBMQuantumRunExecutor,
    SubprocessIBMRuntimeAdapter,
)
from cgr.pulsate_api.runs import (
    ExecutionOutput,
    ExistingQuantumPreflightExecutor,
    _bind_public_source_identity,
    validate_execution_output,
)
from cgr.quantum_preflight.artifacts import write_json_atomic
from cgr.quantum_preflight.contracts import ManifestEnvelope
from cgr.science import sha256_fingerprint


_PHASE = os.environ.get("PULSATE_IBM_INTEGRATION_PHASE")
_SHARED_ROOT = Path(os.environ.get("PULSATE_IBM_SHARED_ROOT", "/pulsate-run"))
_RUN_IDENTIFIER = "run-" + "1" * 32


def _available_ibm_capability() -> dict[str, Any]:
    return {
        "available": True,
        "backend_name": "server-controlled-at-runtime",
        "reason": None,
        "maximum_run_seconds": 1800,
        "target_precision": 0.015,
        "optimization_level": 2,
        "hardware_role": "final_energy_evaluation_at_locally_optimized_parameters",
    }


def _local_payload(output: ExecutionOutput) -> dict[str, Any]:
    payload = {
        "results": output.results,
        "verification": output.verification,
        "receipt": output.receipt,
        "runner_summary": output.runner_summary,
    }
    return {**payload, "local_preflight_sha256": sha256_fingerprint(payload)}


@pytest.mark.skipif(
    _PHASE != "local_preflight",
    reason="runs only in the no-network local-preflight container phase",
)
def test_no_network_local_preflight_phase() -> None:
    assert "PULSATE_IBM_QUANTUM_TOKEN" not in os.environ
    assert "PULSATE_IBM_QUANTUM_INSTANCE" not in os.environ
    host = os.environ["PULSATE_FAKE_ENDPOINT_HOST"]
    port = int(os.environ["PULSATE_FAKE_ENDPOINT_PORT"])
    with pytest.raises(OSError):
        socket.create_connection((host, port), timeout=0.5)

    store = ExperimentStore(
        _SHARED_ROOT / "experiments",
        ibm_capability=_available_ibm_capability,
    )
    store.start()
    plan = store.plan(
        "Calculate the ground-state energy of H2 at 0.9 Å on IBM Quantum"
    )
    assert plan["ready_for_execution"] is True
    manifest, molecule, target = store.resolve_for_targeted_run(
        plan["experiment_identifier"]
    )
    assert target == "ibm_quantum"

    run_directory = _SHARED_ROOT / _RUN_IDENTIFIER
    run_directory.mkdir(mode=0o700)
    image_identifier = os.environ["PULSATE_QUANTUM_IMAGE_IDENTIFIER"]
    executor = ExistingQuantumPreflightExecutor(
        repository_root=Path("/app"),
        image_identifier=image_identifier,
    )
    output = executor.execute_ibm_preflight(
        manifest,
        preset_identifier=plan["experiment_identifier"],
        run_directory=run_directory,
        maximum_seconds=180,
    )
    environment_files = list(
        (run_directory / "runner-artifacts").glob("*/*/environment.json")
    )
    assert len(environment_files) == 1
    environment_document = json.loads(
        environment_files[0].read_text(encoding="utf-8")
    )
    assert (
        environment_document["payload"]["container_image_identifier"]
        == image_identifier
    )
    assert image_identifier != "local-uncontainerized"
    output = _bind_public_source_identity(
        output,
        source_type="dynamic_experiment",
        source_identifier=plan["experiment_identifier"],
        preset_identifier=None,
    )
    scientific_image_identifier = os.environ[
        "PULSATE_QUANTUM_IMAGE_IDENTIFIER"
    ]
    ibm_runtime_image_identifier = os.environ["PULSATE_IBM_IMAGE_IDENTIFIER"]
    assert scientific_image_identifier != ibm_runtime_image_identifier
    output = ExecutionOutput(
        output.results,
        output.verification,
        output.receipt,
        {
            **output.runner_summary,
            "ibm_preflight": {
                **output.runner_summary["ibm_preflight"],
                "scientific_preflight_image_identifier": (
                    scientific_image_identifier
                ),
                "ibm_runtime_image_identifier": ibm_runtime_image_identifier,
            },
        },
    )
    output = validate_execution_output(
        output,
        manifest=manifest,
        source_type="dynamic_experiment",
        source_identifier=plan["experiment_identifier"],
        preset_identifier=None,
        run_identifier=_RUN_IDENTIFIER,
        expected_structure_sha256=molecule["structure_hash"],
    )
    worker_directory = run_directory / "ibm-worker"
    worker_directory.mkdir(mode=0o700)
    write_json_atomic(
        worker_directory / "local-preflight.json",
        _local_payload(output),
        maximum_bytes=2 * 1024 * 1024,
    )
    write_json_atomic(
        run_directory / "compiled-manifest.json",
        manifest.model_dump(mode="json"),
        maximum_bytes=2 * 1024 * 1024,
    )
    write_json_atomic(
        run_directory / "integration-phase.json",
        {
            "experiment_identifier": plan["experiment_identifier"],
            "structure_sha256": molecule["structure_hash"],
            "scientific_preflight_image_identifier": image_identifier,
            "ibm_runtime_image_identifier": ibm_runtime_image_identifier,
            "execution_environment_identity": output.results[
                "execution_environment_identity"
            ],
        },
        maximum_bytes=100_000,
    )


@pytest.mark.skipif(
    _PHASE != "ibm_runtime"
    or os.environ.get("PULSATE_RUN_IBM_INTEGRATION", "").lower() != "true"
    or os.environ.get("PULSATE_IBM_ACKNOWLEDGE_COSTS", "").lower() != "true",
    reason="runs only in the explicitly cost-acknowledged IBM network phase",
)
def test_network_enabled_ibm_runtime_phase() -> None:
    phase = json.loads(
        (_SHARED_ROOT / _RUN_IDENTIFIER / "integration-phase.json").read_text(
            encoding="utf-8"
        )
    )
    manifest = ManifestEnvelope.model_validate_json(
        (
            _SHARED_ROOT / _RUN_IDENTIFIER / "compiled-manifest.json"
        ).read_text(encoding="utf-8")
    )
    configuration = IBMQuantumConfiguration.from_environment()
    assert configuration.unavailable_reason() is None
    adapter = SubprocessIBMRuntimeAdapter(
        repository_root=Path("/app"),
        configuration=configuration,
    )

    # Credentials are injected into the IBM worker environment, then removed
    # from the coordinator/local-science environment before any execution.
    worker_environment = adapter._worker_environment()
    assert worker_environment["PULSATE_IBM_QUANTUM_TOKEN"]
    assert worker_environment["PULSATE_IBM_QUANTUM_INSTANCE"]
    os.environ.pop("PULSATE_IBM_QUANTUM_TOKEN", None)
    os.environ.pop("PULSATE_IBM_QUANTUM_INSTANCE", None)
    assert "PULSATE_IBM_QUANTUM_TOKEN" not in os.environ
    assert "PULSATE_IBM_QUANTUM_INSTANCE" not in os.environ

    endpoint = os.environ["PULSATE_FAKE_ENDPOINT_URL"]
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import os,urllib.request;"
                "assert os.environ['PULSATE_IBM_QUANTUM_TOKEN'];"
                "urllib.request.urlopen(os.environ['PULSATE_FAKE_ENDPOINT_URL'],timeout=3).read(1)"
            ),
        ],
        cwd="/app",
        env={**worker_environment, "PULSATE_FAKE_ENDPOINT_URL": endpoint},
        shell=False,
        check=False,
        capture_output=True,
        timeout=10,
    )
    assert probe.returncode == 0

    class ForbiddenLocalExecutor:
        proven_no_network = True

        def execute(self, *args: Any, **kwargs: Any) -> ExecutionOutput:
            del args, kwargs
            raise AssertionError(
                "The network-enabled IBM phase must reuse validated no-network preflight."
            )

    executor = IBMQuantumRunExecutor(
        local_executor=ForbiddenLocalExecutor(),
        adapter=adapter,
        configuration=configuration,
    )
    output = executor.execute(
        manifest,
        preset_identifier=phase["experiment_identifier"],
        run_directory=_SHARED_ROOT / _RUN_IDENTIFIER,
        maximum_seconds=180,
        status_callback=lambda *_: None,
        source_type="dynamic_experiment",
        source_identifier=phase["experiment_identifier"],
        source_preset_identifier=None,
        expected_structure_sha256=phase["structure_sha256"],
        run_identifier=_RUN_IDENTIFIER,
    )
    evidence = output.receipt["ibm_execution"]
    assert output.results["structure_sha256"] == phase["structure_sha256"]
    assert (
        output.results["execution_environment_identity"]
        == phase["execution_environment_identity"]
    )
    assert (
        phase["scientific_preflight_image_identifier"]
        != phase["ibm_runtime_image_identifier"]
    )
    assert evidence["execution_integrity_passed"] is True
    assert evidence["job_identifier"]
    assert (
        evidence["scientific_preflight_image_identifier"]
        == phase["scientific_preflight_image_identifier"]
    )
    assert (
        evidence["ibm_runtime_image_identifier"]
        == phase["ibm_runtime_image_identifier"]
    )
    write_json_atomic(
        _SHARED_ROOT / _RUN_IDENTIFIER / "live-receipt.json",
        output.receipt,
        maximum_bytes=2 * 1024 * 1024,
    )
