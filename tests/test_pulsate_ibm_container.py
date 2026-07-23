from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_ibm_runtime_lock_is_exact_hashed_and_preserves_qiskit_base() -> None:
    lock = source("requirements/pulsate-ibm-runtime.lock")
    assert "qiskit-ibm-runtime==0.48.0" in lock
    assert "--hash=sha256:ecbd70a85efbbeec4add8de1c446ace21b196b2587c74306e9d3fe1e4fdddd2e" in lock
    for line in lock.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            assert "==" in stripped and "--hash=sha256:" in stripped
    assert not any(line.startswith("qiskit==") for line in lock.splitlines())


def test_ibm_image_is_pinned_http_derivative_and_runs_pip_check() -> None:
    dockerfile = source("docker/pulsate-ibm-runtime/Dockerfile")
    assert "ARG BASE_IMAGE=cgr-pulsate-http-integration:1.0.0" in dockerfile
    assert "FROM ${BASE_IMAGE}" in dockerfile
    assert "--no-deps --require-hashes" in dockerfile
    assert "python -m pip check" in dockerfile
    assert 'version("qiskit") == "2.3.1"' in dockerfile
    assert 'version("qiskit-ibm-runtime") == "0.48.0"' in dockerfile
    assert "USER 10001:10001" in dockerfile


def test_ibm_build_pins_exact_local_base_identity_and_provenance() -> None:
    build = source("scripts/build-pulsate-ibm-runtime-image.sh")
    assert 'base_image_id="$(docker image inspect --format \'{{.Id}}\' "$base_image")"' in build
    assert 'base_image_hex="${base_image_id#sha256:}"' in build
    assert 'docker image tag "$base_image_id" "$pinned_base_image"' in build
    assert '--build-arg "BASE_IMAGE=$pinned_base_image"' in build
    assert "--pull=false" in build
    assert 'docker image rm "$pinned_base_image"' in build
    assert "io.pulsate.base.image.id" in build
    assert "org.opencontainers.image.revision" in build


def test_live_script_requires_cost_gates_and_passes_credentials_by_name() -> None:
    run = source("scripts/run-pulsate-ibm-integration.sh")
    for name in (
        "PULSATE_RUN_IBM_INTEGRATION",
        "PULSATE_IBM_ACKNOWLEDGE_COSTS",
        "PULSATE_IBM_QUANTUM_TOKEN",
        "PULSATE_IBM_QUANTUM_INSTANCE",
        "PULSATE_IBM_QUANTUM_BACKEND",
    ):
        assert name in run
    assert "--network none" in run
    assert "PULSATE_IBM_INTEGRATION_PHASE=local_preflight" in run
    assert "PULSATE_IBM_INTEGRATION_PHASE=ibm_runtime" in run
    assert 'docker volume create "$run_volume"' in run
    assert '--volume "$run_volume:/pulsate-run"' in run
    local_phase = run.split("# Phase 1:", 1)[1].split("# Phase 2:", 1)[0]
    assert "--network none" in local_phase
    assert "PULSATE_IBM_QUANTUM_TOKEN" not in local_phase
    network_phase = run.split("# Phase 2:", 1)[1]
    assert '--network "$network_name"' in network_phase
    assert "--env PULSATE_IBM_QUANTUM_TOKEN" in run
    assert "PULSATE_IBM_IMAGE_IDENTIFIER" in run
    assert "tests/test_pulsate_ibm_integration.py" in run


def test_fake_integration_is_separate_nonpaid_and_network_local_only() -> None:
    run = source("scripts/run-pulsate-ibm-fake-integration.sh")
    acceptance = source("tests/test_pulsate_ibm_fake_integration.py")
    assert "unset PULSATE_RUN_IBM_INTEGRATION" in run
    assert "unset PULSATE_IBM_ACKNOWLEDGE_COSTS" in run
    assert "unset PULSATE_IBM_QUANTUM_TOKEN" in run
    assert "unset PULSATE_IBM_QUANTUM_INSTANCE" in run
    assert "unset PULSATE_IBM_QUANTUM_BACKEND" in run
    assert "docker network create --internal" in run
    assert "--network none" in run
    assert "PULSATE_IBM_FAKE_ENDPOINT_URL=http://127.0.0.1:8765" in run
    assert 'scientific_image_id="$(docker image inspect' in run
    assert '"$scientific_image_id"' in run
    assert '"$derived_image_id"' in run
    assert "--entrypoint python" in run
    assert "-m cgr.pulsate_api.ibm_preflight_coordinator" in run
    assert "PULSATE_IBM_FAKE_ACCEPTANCE=true" in run
    assert "PULSATE_IBM_SCIENTIFIC_IMAGE_IDENTIFIER" in run
    assert "tests/test_pulsate_ibm_fake_integration.py" in run
    assert "--env PULSATE_IBM_QUANTUM_TOKEN" not in run
    assert (
        "Calculate the ground-state energy of H2 at 0.735 "
        in acceptance
    )
    assert "assert plan_response.status_code == 201" in acceptance
    assert 'run_identifier != "run-" + "a" * 32' in acceptance
    assert "RunBoundIsolatedIBMPreflightExecutor" in acceptance


def test_worker_persists_exact_prepared_isa_and_typed_failure_state() -> None:
    worker = source("src/cgr/pulsate_api/ibm_worker.py")
    models = source("src/cgr/pulsate_api/ibm.py")
    assert "prepared-isa-circuit.qpy" in worker
    assert "prepared-isa-observable.json" in worker
    assert "prepared-submission.json" in worker
    assert "seed_transpiler=bundle.seed_transpiler" in worker
    assert "_load_prepared_submission" in worker
    assert "IBMWorkerFailureEnvelope" in worker
    assert "retrieval_recoverable" in models
    assert "dict(os.environ)" not in models


def test_normal_api_uses_operational_run_bound_preflight_launcher() -> None:
    application = source("src/cgr/pulsate_api/app.py")
    models = source("src/cgr/pulsate_api/ibm.py")
    coordinator = source("src/cgr/pulsate_api/ibm_preflight_coordinator.py")
    assert "RunBoundIsolatedIBMPreflightExecutor" in application
    assert "PersistedIBMPreflightHandoffExecutor(Path(handoff_root))" not in application
    assert "launcher-readiness.json" in models
    assert "observed_at_epoch" in models
    assert "IBMRunBoundPreflightRequest" in models
    assert "_assert_no_network_boundary" in coordinator
    assert 'observed != {"lo"}' in coordinator
    assert "execute_ibm_preflight" in coordinator


def test_ibm_material_is_sidecar_only_and_local_ansatz_schema_remains_frozen() -> None:
    reference = source("src/cgr/quantum_preflight/reference.py")
    canonical_ansatz = reference.split("ansatz_manifest = {", 1)[1].split(
        "ibm_preflight:", 1
    )[0]
    assert '"optimized_parameters":' not in canonical_ansatz
    assert '"source_bound_circuit_sha256":' not in canonical_ansatz
    assert '"source_observable_sha256":' not in canonical_ansatz
    assert "if capture_ibm_preflight:" in reference
    worker = source("src/cgr/pulsate_api/quantum_worker.py")
    assert "--ibm-preflight-evidence" in worker


def test_existing_local_worker_remains_network_disabled() -> None:
    local = source("scripts/run-pulsate-http-integration.sh")
    assert local.count("--network none") >= 3
    assert "PULSATE_IBM_QUANTUM_TOKEN" not in local


def test_worker_uses_isolated_bounded_boundary_and_platform_channel() -> None:
    adapter = source("src/cgr/pulsate_api/ibm.py")
    worker = source("src/cgr/pulsate_api/ibm_worker.py")
    assert 'options["start_new_session"] = True' in adapter
    assert "_BoundedLogCollector" in adapter
    assert "_terminate_worker(process)" in adapter
    assert 'channel="ibm_quantum_platform"' in worker
    assert "EstimatorV2" in worker
    assert "job.job_id()" in worker
    assert "service.job(job_identifier)" in worker
    assert "submission-attempt.json" in worker
    assert "job_correlation_identifier" in worker
    assert "service.jobs(" in worker
    assert "options.max_execution_time" in worker
    assert "status.json" in worker
    assert "nuclear_repulsion_energy" in worker
    assert "canonical_qpy_sha256" in worker
    assert "canonical_sparse_pauli_op_sha256" in worker
    assert "save_account" not in worker


def test_local_worker_environment_strips_ibm_credentials() -> None:
    local = source("src/cgr/pulsate_api/runs.py")
    assert '"PULSATE_IBM_QUANTUM_TOKEN"' in local
    assert '"PULSATE_IBM_QUANTUM_INSTANCE"' in local
    assert '"env": {' in local
