from __future__ import annotations

import json
import io
import os
import shutil
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient

from cgr.quantum_preflight.artifacts import artifact_document, artifact_reference, write_json_atomic
from cgr.quantum_preflight.identities import ScientificResultIdentity
from cgr.quantum_preflight.receipt import assemble_receipt
from cgr.quantum_preflight.runner import _ARTIFACT_TYPES, _FILENAMES
from cgr.quantum_preflight.verification import blocking_findings, verify_execution
from cgr.pulsate_api.app import _load_preset, create_app
from cgr.pulsate_api.quantum_worker import (
    WORKER_EXIT_COMPLETED,
    WORKER_EXIT_VERIFICATION_FAILED,
    WORKER_RESULT_MAXIMUM_BYTES,
    WORKER_RESULT_SCHEMA,
)
from cgr.pulsate_api.runs import (
    CoordinatorConfigurationError,
    ExecutionOutput,
    ExistingQuantumPreflightExecutor,
    RunCoordinator,
    RunRootOwnershipError,
    TERMINAL_STATUSES,
    assert_public_response_safe,
)
from test_quantum_preflight import _synthetic_evidence, _synthetic_outcome


class ControlledExecutor:
    def __init__(
        self,
        outcome: str = "authorized",
        gate: threading.Event | None = None,
        failure_message: str | None = None,
        mutate: Any | None = None,
    ) -> None:
        self.outcome = outcome
        self.gate = gate
        self.failure_message = failure_message
        self.mutate = mutate
        self.calls = 0
        self.maximum_seconds: list[int] = []

    def execute(
        self,
        manifest: Any,
        *,
        preset_identifier: str,
        run_directory: Path,
        maximum_seconds: int,
    ) -> ExecutionOutput:
        self.calls += 1
        self.maximum_seconds.append(maximum_seconds)
        if self.gate is not None:
            assert self.gate.wait(3)
        if self.outcome == "failed":
            raise RuntimeError(self.failure_message or "controlled executor failure")
        authorized = self.outcome == "authorized"
        experiment = manifest.experiment
        molecule = experiment.molecular_system
        structure_sha256 = artifact_reference(
            "molecular_structure",
            "molecular_structure",
            {
                **molecule.model_dump(mode="json"),
                "driver_spin": molecule.driver_spin,
                "total_electron_count": molecule.total_electron_count,
            },
            filename="molecular-structure.json",
        ).content_sha256
        common = {
            "preset_identifier": preset_identifier,
            "experiment_identifier": experiment.experiment_identifier,
            "experiment_fingerprint": experiment.fingerprint,
            "expected_experiment_sha256": manifest.expected_experiment_sha256,
            "structure_identifier": experiment.molecular_system.structure_artifact_identifier,
        }
        results = {
            "run_identifier": run_directory.name,
            **common,
            "structure_sha256": structure_sha256,
            "hamiltonian_sha256": "b" * 64,
            "exact_scientific_result_sha256": "e" * 64,
            "vqe_scientific_result_sha256": "f" * 64,
            "scientific_outcome_sha256": "g" * 64,
            "exact_total_energy_hartree": -1.1373060358,
            "vqe_total_energy_hartree": -1.1373060357,
            "absolute_difference_hartree": 1e-10,
            "tolerance_hartree": 1e-6,
            "energy_unit": "hartree",
            "exact_solver_metadata": {"solver_identifier": "numpy_eigensolver", "completed": True},
            "vqe_solver_metadata": {"solver_identifier": "statevector_estimator", "optimizer_status": "converged"},
            "optimizer_evaluations": 9,
            "converged": True,
            "compatibility_warnings": [],
            "execution_environment_identity": "c" * 64,
            "receipt_sha256": "d" * 64,
        }
        verification = {
            "run_identifier": run_directory.name,
            **common,
            "structure_sha256": structure_sha256,
            "verification_completed": True,
            "verification_passed": authorized,
            "authorization_state": self.outcome,
            "blocking_findings": [] if authorized else [{"code": "controlled.rejection", "blocking": True}],
            "nonblocking_findings": [],
            "tolerance_check": {"passed": authorized, "tolerance_hartree": 1e-6},
            "scientific_identity_checks": [],
            "artifact_integrity_checks": [],
            "checks": [],
            "compatibility_warnings": [],
        }
        receipt = {
            "schema_version": "test.receipt/1.0.0",
            "run_identifier": run_directory.name,
            **common,
            "execution_identifier": "controlled-execution",
            "structure_sha256": structure_sha256,
            "hamiltonian_sha256": "b" * 64,
            "exact_scientific_result_sha256": "e" * 64,
            "vqe_scientific_result_sha256": "f" * 64,
            "scientific_outcome_sha256": "g" * 64,
            "execution_environment_identity": "c" * 64,
            "receipt_sha256": "d" * 64,
            "verification_passed": authorized,
            "authorization_state": self.outcome,
            "authorized": authorized,
            "artifacts": [],
        }
        output = ExecutionOutput(results, verification, receipt, {"authorized": authorized})
        return self.mutate(output) if self.mutate is not None else output


def coordinator_for(
    tmp_path: Path,
    executor: ControlledExecutor,
    *,
    enabled: bool = True,
    max_run_seconds: int | str = 180,
    unavailable_reason: str | None = None,
) -> RunCoordinator:
    return RunCoordinator(
        run_root=tmp_path / "runs",
        manifest_resolver=_load_preset,
        executor=executor,
        enabled=enabled,
        unavailable_reason=unavailable_reason,
        max_workers=1,
        max_run_seconds=max_run_seconds,
    )


@contextmanager
def running_client(
    tmp_path: Path,
    executor: ControlledExecutor,
    **options: Any,
) -> Iterator[tuple[TestClient, RunCoordinator]]:
    coordinator = coordinator_for(tmp_path, executor, **options)
    with TestClient(create_app(coordinator=coordinator)) as client:
        yield client, coordinator
    assert not coordinator.started


def wait_for_terminal(client: TestClient, run_identifier: str) -> dict[str, Any]:
    for _ in range(200):
        state = client.get(f"/api/v1/runs/{run_identifier}").json()
        if state["status"] in {"authorized", "rejected", "failed", "interrupted"}:
            return state
        time.sleep(0.01)
    raise AssertionError("run did not reach a terminal state")


def create_run(client: TestClient, *, key: str = "test-key-0001") -> dict[str, Any]:
    response = client.post(
        "/api/v1/runs", headers={"Idempotency-Key": key},
        json={"preset_identifier": "h2-ground-state-v1", "execution_target": "local_simulator"},
    )
    assert response.status_code == 202
    return response.json()


def test_import_has_no_run_root_side_effects(tmp_path: Path) -> None:
    run_root = tmp_path / "must-not-exist"
    environment = {**os.environ, "PULSATE_RUN_ROOT": str(run_root), "PULSATE_EXECUTION_ENABLED": "false"}
    completed = subprocess.run(
        [sys.executable, "-c", "import cgr.pulsate_api.app"],
        cwd=Path(__file__).resolve().parents[1], env=environment,
        capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert not run_root.exists()


def test_lifespan_starts_and_closes_coordinator(tmp_path: Path) -> None:
    coordinator = coordinator_for(tmp_path, ControlledExecutor())
    assert not coordinator.started and not (tmp_path / "runs").exists()
    with TestClient(create_app(coordinator=coordinator)) as client:
        assert coordinator.started
        assert client.get("/api/v1/health").json()["version"] == "0.2.0"
    assert not coordinator.started


def test_second_coordinator_cannot_own_same_root(tmp_path: Path) -> None:
    first = coordinator_for(tmp_path, ControlledExecutor())
    second = coordinator_for(tmp_path, ControlledExecutor())
    first.start()
    try:
        with pytest.raises(RunRootOwnershipError, match="already owned"):
            second.start()
    finally:
        first.close()
        second.close()


def test_configured_run_root_symlink_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "actual-runs"
    target.mkdir()
    configured = tmp_path / "configured-runs"
    try:
        configured.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("Creating directory symbolic links is not available in this environment.")
    coordinator = RunCoordinator(
        run_root=configured, manifest_resolver=_load_preset,
        executor=ControlledExecutor(), enabled=True,
    )
    with pytest.raises(CoordinatorConfigurationError, match="normal directory"):
        coordinator.start()


def test_run_creation_is_async_and_server_identified(tmp_path: Path) -> None:
    gate = threading.Event()
    with running_client(tmp_path, ControlledExecutor(gate=gate)) as (client, _coordinator):
        try:
            state = create_run(client)
            assert state["status"] == "queued"
            assert state["run_identifier"].startswith("run-")
            request = json.loads((tmp_path / "runs" / state["run_identifier"] / "request.json").read_text())
            assert "run_identifier" not in request
        finally:
            gate.set()


def test_untrusted_request_fields_and_targets_are_rejected(tmp_path: Path) -> None:
    with running_client(tmp_path, ControlledExecutor()) as (client, _coordinator):
        assert client.post("/api/v1/runs", json={"preset_identifier": "missing", "execution_target": "local_simulator"}).status_code == 404
        assert client.post("/api/v1/runs", json={"preset_identifier": "h2-ground-state-v1", "execution_target": "ibm"}).status_code == 422
        arbitrary = client.post("/api/v1/runs", json={
            "preset_identifier": "h2-ground-state-v1", "execution_target": "local_simulator",
            "manifest_path": "../../secret", "result_directory": "C:\\private",
        })
        assert arbitrary.status_code == 422
        assert client.get("/api/v1/runs/..%2F..%2Fsecret").status_code in {404, 422}


def test_disabled_execution_capability_returns_503(tmp_path: Path) -> None:
    with running_client(
        tmp_path, ControlledExecutor(), enabled=False,
        unavailable_reason="Pinned quantum dependencies are unavailable.",
    ) as (client, _coordinator):
        capability = client.get("/api/v1/runs/capability").json()
        assert capability == {
            "available": False, "execution_targets": [],
            "reason": "Pinned quantum dependencies are unavailable.", "maximum_run_seconds": 180,
        }
        response = client.post("/api/v1/runs", json={
            "preset_identifier": "h2-ground-state-v1", "execution_target": "local_simulator",
        })
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "execution_unavailable"


@pytest.mark.parametrize("outcome", ["authorized", "rejected", "failed"])
def test_injected_executor_drives_truthful_terminal_state(tmp_path: Path, outcome: str) -> None:
    with running_client(tmp_path, ControlledExecutor(outcome)) as (client, _coordinator):
        run = create_run(client)
        state = wait_for_terminal(client, run["run_identifier"])
        assert state["status"] == outcome
        if outcome == "failed":
            assert state["error"]["code"] == "run_execution_failed"
            assert client.get(f"/api/v1/runs/{run['run_identifier']}/results").status_code == 409
        else:
            results = client.get(f"/api/v1/runs/{run['run_identifier']}/results")
            assert results.status_code == 200
            assert results.json()["exact_total_energy_hartree"] == -1.1373060358
            public_receipt = client.get(f"/api/v1/runs/{run['run_identifier']}/receipt").json()
            assert public_receipt["authorized"] is (outcome == "authorized")
            assert "scientific_outcome" not in public_receipt


def test_receipt_is_unavailable_before_completion(tmp_path: Path) -> None:
    gate = threading.Event()
    with running_client(tmp_path, ControlledExecutor(gate=gate)) as (client, _coordinator):
        try:
            run = create_run(client)
            response = client.get(f"/api/v1/runs/{run['run_identifier']}/receipt")
            assert response.status_code == 409
            assert response.json()["detail"]["code"] == "receipt_unavailable"
        finally:
            gate.set()


def test_idempotency_deduplicates_and_rejects_conflicts(tmp_path: Path) -> None:
    gate = threading.Event()
    executor = ControlledExecutor(gate=gate)
    with running_client(tmp_path, executor) as (client, _coordinator):
        try:
            first = create_run(client, key="same-key-0001")
            second = create_run(client, key="same-key-0001")
            assert second["run_identifier"] == first["run_identifier"]
            conflict = client.post(
                "/api/v1/runs", headers={"Idempotency-Key": "same-key-0001"},
                json={"preset_identifier": "lih-ground-state-v1", "execution_target": "local_simulator"},
            )
            assert conflict.status_code == 409
            for _ in range(100):
                if executor.calls:
                    break
                time.sleep(0.01)
            assert executor.calls == 1
        finally:
            gate.set()


def test_startup_recovers_active_run_as_interrupted(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    directory = root / ("run-" + "1" * 32)
    directory.mkdir(parents=True)
    (directory / "request.json").write_text(json.dumps({
        "preset_identifier": "h2-ground-state-v1", "execution_target": "local_simulator", "idempotency_key": None,
    }), encoding="utf-8")
    (directory / "state.json").write_text(json.dumps({
        "run_identifier": directory.name, "preset_identifier": "h2-ground-state-v1", "status": "running_quantum_workflow",
        "status_history": [], "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
    }), encoding="utf-8")
    coordinator = RunCoordinator(
        run_root=root, manifest_resolver=_load_preset, executor=ControlledExecutor(), enabled=True,
    )
    coordinator.start()
    try:
        assert coordinator.get(directory.name)["status"] == "interrupted"
    finally:
        coordinator.close()


def test_concurrent_idempotent_requests_do_not_corrupt_state(tmp_path: Path) -> None:
    gate = threading.Event()
    executor = ControlledExecutor(gate=gate)
    identifiers: list[str] = []
    with running_client(tmp_path, executor) as (client, _coordinator):
        try:
            def submit() -> None:
                identifiers.append(create_run(client, key="concurrent-key-01")["run_identifier"])
            threads = [threading.Thread(target=submit) for _ in range(8)]
            for thread in threads: thread.start()
            for thread in threads: thread.join()
            assert len(set(identifiers)) == 1
            state_file = tmp_path / "runs" / identifiers[0] / "state.json"
            assert isinstance(json.loads(state_file.read_text(encoding="utf-8")), dict)
            for _ in range(100):
                if executor.calls:
                    break
                time.sleep(0.01)
            assert executor.calls == 1
        finally:
            gate.set()


def test_timeout_is_forwarded_and_bounded_by_manifest_policy(tmp_path: Path) -> None:
    executor = ControlledExecutor()
    with running_client(tmp_path, executor, max_run_seconds=73) as (client, _coordinator):
        run = create_run(client)
        wait_for_terminal(client, run["run_identifier"])
        assert executor.maximum_seconds == [73]


def test_existing_executor_runs_from_real_coordinator_thread_via_worker_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    direct_runner_called = False
    process_threads: list[str] = []

    def forbidden_direct_runner(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal direct_runner_called
        del args, kwargs
        direct_runner_called = True
        raise AssertionError("The coordinator worker must not call trusted science directly.")

    monkeypatch.setattr(
        "cgr.quantum_preflight.runner.run_trusted_reference", forbidden_direct_runner
    )

    class CompletedProcess:
        def __init__(self) -> None:
            self.stdout = io.BytesIO()
            self.stderr = io.BytesIO()

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            return WORKER_EXIT_COMPLETED

        def kill(self) -> None:
            raise AssertionError("The completed worker must not be killed.")

    def argument(command: list[str], name: str) -> Path:
        return Path(command[command.index(name) + 1])

    def process_factory(command: list[str], **options: Any) -> CompletedProcess:
        del options
        process_threads.append(threading.current_thread().name)
        result_root = argument(command, "--result-root")
        artifact_directory = result_root / "controlled" / "run-001"
        artifact_directory.mkdir(parents=True)
        write_json_atomic(
            argument(command, "--result-envelope"),
            {
                "schema_version": WORKER_RESULT_SCHEMA,
                "outcome": "completed",
                "summary": {"receipt_path": str(artifact_directory / "receipt.json")},
                "error": None,
            },
            maximum_bytes=WORKER_RESULT_MAXIMUM_BYTES,
        )
        return CompletedProcess()

    projector = ControlledExecutor()

    def controlled_projection(
        manifest: Any,
        preset_identifier: str,
        run_identifier: str,
        *args: Any,
        **kwargs: Any,
    ) -> ExecutionOutput:
        del args, kwargs
        return projector.execute(
            manifest,
            preset_identifier=preset_identifier,
            run_directory=Path(run_identifier),
            maximum_seconds=1,
        )

    monkeypatch.setattr(
        ExistingQuantumPreflightExecutor,
        "_project",
        staticmethod(controlled_projection),
    )
    executor = ExistingQuantumPreflightExecutor(
        repository_root=Path(__file__).resolve().parents[1],
        image_identifier="sha256:controlled",
        _process_factory=process_factory,
    )
    coordinator = RunCoordinator(
        run_root=tmp_path / "runs",
        manifest_resolver=_load_preset,
        executor=executor,
        enabled=True,
    )
    coordinator.start()
    try:
        state, _ = coordinator.create(
            "h2-ground-state-v1", "local_simulator", "worker-boundary-0001"
        )
        for _ in range(200):
            state = coordinator.get(state["run_identifier"])
            if state["status"] in TERMINAL_STATUSES:
                break
            time.sleep(0.01)
        assert state["status"] == "authorized", state
    finally:
        coordinator.close()
    assert process_threads and process_threads[0].startswith("pulsate-run")
    assert not direct_runner_called


@pytest.mark.parametrize("value", ["invalid", 0, -1, 3601])
def test_invalid_timeout_fails_at_startup(tmp_path: Path, value: int | str) -> None:
    coordinator = coordinator_for(tmp_path, ControlledExecutor(), max_run_seconds=value)
    with pytest.raises(CoordinatorConfigurationError):
        with TestClient(create_app(coordinator=coordinator)):
            pass
    assert not coordinator.started


def test_inconsistent_executor_output_cannot_authorize(tmp_path: Path) -> None:
    def mutate(output: ExecutionOutput) -> ExecutionOutput:
        receipt = {**output.receipt, "structure_sha256": "9" * 64}
        return ExecutionOutput(output.results, output.verification, receipt, output.runner_summary)

    with running_client(tmp_path, ControlledExecutor(mutate=mutate)) as (client, _coordinator):
        run = create_run(client)
        state = wait_for_terminal(client, run["run_identifier"])
        assert state["status"] == "failed"
        assert client.get(f"/api/v1/runs/{run['run_identifier']}/results").status_code == 409


def test_quoted_paths_and_paths_with_spaces_are_sanitized(tmp_path: Path) -> None:
    message = 'failed at "C:\\Secret Folder\\private key.txt" and \'/var/private data/token.txt\''
    executor = ControlledExecutor("failed", failure_message=message)
    with running_client(tmp_path, executor) as (client, _coordinator):
        run = create_run(client)
        state = wait_for_terminal(client, run["run_identifier"])
        assert "Secret Folder" not in state["error"]["message"]
        assert "/var/private" not in state["error"]["message"]
        assert_public_response_safe(state)


def test_all_public_responses_are_recursively_path_free(tmp_path: Path) -> None:
    with running_client(tmp_path, ControlledExecutor()) as (client, _coordinator):
        run = create_run(client)
        wait_for_terminal(client, run["run_identifier"])
        responses = [
            client.get("/api/v1/health").json(),
            client.get("/api/v1/runs/capability").json(),
            client.get(f"/api/v1/runs/{run['run_identifier']}").json(),
            *[
                client.get(f"/api/v1/runs/{run['run_identifier']}/{name}").json()
                for name in ("results", "verification", "receipt")
            ],
        ]
        for response in responses:
            assert_public_response_safe(response)


@pytest.mark.parametrize("projection_name", ["results", "receipt"])
def test_persisted_public_projection_symlink_is_rejected(
    tmp_path: Path, projection_name: str,
) -> None:
    with running_client(tmp_path, ControlledExecutor()) as (client, coordinator):
        run = create_run(client)
        wait_for_terminal(client, run["run_identifier"])
        path = tmp_path / "runs" / run["run_identifier"] / f"{projection_name}.json"
        outside = tmp_path / f"outside-{projection_name}.json"
        outside.write_bytes(path.read_bytes())
        path.unlink()
        try:
            path.symlink_to(outside)
        except OSError:
            pytest.skip("Creating symbolic links is not available in this environment.")
        with pytest.raises(ValueError, match="symbolic link"):
            coordinator.artifact(run["run_identifier"], projection_name)  # type: ignore[arg-type]


def test_oversized_persisted_public_projection_is_rejected(tmp_path: Path) -> None:
    with running_client(tmp_path, ControlledExecutor()) as (client, coordinator):
        run = create_run(client)
        wait_for_terminal(client, run["run_identifier"])
        path = tmp_path / "runs" / run["run_identifier"] / "results.json"
        path.write_text(json.dumps({"padding": "x" * (2 * 1024 * 1024)}), encoding="utf-8")
        with pytest.raises(ValueError, match="size limit"):
            coordinator.artifact(run["run_identifier"], "results")


@pytest.mark.parametrize(
    ("projection_name", "field", "value"),
    [
        ("results", "run_identifier", "run-" + "9" * 32),
        ("receipt", "preset_identifier", "different-preset"),
    ],
)
def test_persisted_public_projection_identity_mismatch_is_rejected(
    tmp_path: Path, projection_name: str, field: str, value: str,
) -> None:
    with running_client(tmp_path, ControlledExecutor()) as (client, coordinator):
        run = create_run(client)
        wait_for_terminal(client, run["run_identifier"])
        path = tmp_path / "runs" / run["run_identifier"] / f"{projection_name}.json"
        projection = json.loads(path.read_text(encoding="utf-8"))
        projection[field] = value
        path.write_text(json.dumps(projection), encoding="utf-8")
        with pytest.raises(ValueError, match="mismatched"):
            coordinator.artifact(run["run_identifier"], projection_name)  # type: ignore[arg-type]


def test_malformed_persisted_public_projection_is_rejected(tmp_path: Path) -> None:
    with running_client(tmp_path, ControlledExecutor()) as (client, coordinator):
        run = create_run(client)
        wait_for_terminal(client, run["run_identifier"])
        path = tmp_path / "runs" / run["run_identifier"] / "verification.json"
        path.write_text(json.dumps({"run_identifier": run["run_identifier"]}), encoding="utf-8")
        with pytest.raises(ValueError):
            coordinator.artifact(run["run_identifier"], "verification")


def test_shutdown_interrupts_queued_run_without_overwriting_active_result(tmp_path: Path) -> None:
    gate = threading.Event()
    executor = ControlledExecutor(gate=gate)
    coordinator = coordinator_for(tmp_path, executor)
    coordinator.start()
    first, _ = coordinator.create("h2-ground-state-v1", "local_simulator", "shutdown-first-01")
    second, _ = coordinator.create("h2-ground-state-v1", "local_simulator", "shutdown-second-01")
    for _ in range(100):
        if executor.calls == 1:
            break
        time.sleep(0.01)
    assert executor.calls == 1
    closer = threading.Thread(target=coordinator.close)
    closer.start()
    second_path = tmp_path / "runs" / second["run_identifier"] / "state.json"
    for _ in range(100):
        queued_state = json.loads(second_path.read_text(encoding="utf-8"))
        if queued_state["status"] == "interrupted":
            break
        time.sleep(0.01)
    assert queued_state["status"] == "interrupted"
    gate.set()
    closer.join(timeout=5)
    assert not closer.is_alive()
    first_state = json.loads(
        (tmp_path / "runs" / first["run_identifier"] / "state.json").read_text(encoding="utf-8")
    )
    assert first_state["status"] == "authorized"
    assert json.loads(second_path.read_text(encoding="utf-8"))["status"] == "interrupted"


def _projection_bundle(
    directory: Path, *, authorized: bool = True,
) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    """Write deterministic, model-valid evidence without invoking a quantum backend."""
    manifest = _load_preset("lih-ground-state-v1")
    if not authorized:
        manifest_data = manifest.model_dump(mode="json")
        manifest_data["expected_experiment_sha256"] = None
        manifest_data["experiment"]["verification_policy"][
            "energy_difference_tolerance_hartree"
        ] = 1e-9
        mutated_manifest = type(manifest).model_validate(manifest_data)
        manifest = mutated_manifest.model_copy(
            update={
                "expected_experiment_sha256": mutated_manifest.experiment.fingerprint
            }
        )
    payloads, references, lineage = _synthetic_evidence(manifest)
    results = verify_execution(manifest.experiment, references, payloads, lineage)
    outcome = _synthetic_outcome(manifest, payloads, references, results)
    report = {
        "schema_version": "cgr.quantum-verification-report/1.0.0",
        "results": [result.model_dump(mode="json") for result in results],
        "numerical_agreement": payloads["numerical_agreement"],
    }
    references["verification_report"] = artifact_reference(
        "verification_report", _ARTIFACT_TYPES["verification_report"], report,
        filename=_FILENAMES["verification_report"],
    )
    lineage_reference = artifact_reference(
        "lineage", "artifact_lineage", lineage.model_dump(mode="json"),
        filename=_FILENAMES["lineage"],
    )
    references["lineage"] = lineage_reference
    receipt = assemble_receipt(
        execution_identifier="projection-test",
        experiment=references["experiment"].pointer,
        artifacts=tuple(reference.pointer for reference in references.values()),
        verification_results=results,
        lineage=lineage_reference.pointer,
        compatibility_warnings=references["compatibility_warnings"].pointer,
        scientific_outcome=outcome,
        execution_completed=True,
    )
    receipt_payload = receipt.model_dump(mode="json")
    directory.mkdir(parents=True)
    bundle_payloads = {
        **payloads,
        "verification_report": report,
        "lineage": lineage.model_dump(mode="json"),
    }
    for identifier in references:
        write_json_atomic(
            directory / _FILENAMES[identifier],
            artifact_document(_ARTIFACT_TYPES[identifier], bundle_payloads[identifier]),
            maximum_bytes=1_000_000,
        )
    write_json_atomic(
        directory / _FILENAMES["receipt"],
        artifact_document(_ARTIFACT_TYPES["receipt"], receipt_payload),
        maximum_bytes=1_000_000,
    )
    receipt_reference = artifact_reference(
        "receipt", _ARTIFACT_TYPES["receipt"], receipt_payload, filename="receipt.json"
    )
    summary = {
        "experiment_fingerprint": manifest.experiment.fingerprint,
        "structure_sha256": outcome.molecular_structure_sha256,
        "qubit_hamiltonian_sha256": outcome.qubit_hamiltonian_sha256,
        "receipt_sha256": receipt_reference.content_sha256,
        "scientific_verification_passed": not blocking_findings(results),
        "authorized": receipt.authorized,
    }
    return manifest, summary, payloads


def _read_artifact_payload(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))["payload"]


def _rewrite_artifact(path: Path, artifact_type: str, payload: dict[str, Any]) -> None:
    write_json_atomic(path, artifact_document(artifact_type, payload), maximum_bytes=1_000_000)


def _rewrite_receipt(directory: Path, summary: dict[str, Any], receipt: dict[str, Any]) -> None:
    _rewrite_artifact(directory / "receipt.json", _ARTIFACT_TYPES["receipt"], receipt)
    summary["receipt_sha256"] = artifact_reference(
        "receipt", _ARTIFACT_TYPES["receipt"], receipt, filename="receipt.json"
    ).content_sha256


def _replace_receipt_pointer(
    directory: Path,
    summary: dict[str, Any],
    artifact_identifier: str,
    payload: dict[str, Any],
) -> None:
    receipt = _read_artifact_payload(directory / "receipt.json")
    pointer = artifact_reference(
        artifact_identifier, _ARTIFACT_TYPES[artifact_identifier], payload,
        filename=_FILENAMES[artifact_identifier],
    ).pointer.model_dump(mode="json")
    receipt["artifacts"] = [
        pointer if item["artifact_identifier"] == artifact_identifier else item
        for item in receipt["artifacts"]
    ]
    _rewrite_receipt(directory, summary, receipt)


def _project_bundle(directory: Path, manifest: Any, summary: dict[str, Any]) -> ExecutionOutput:
    return ExistingQuantumPreflightExecutor._project(
        manifest, "lih-ground-state-v1", "run-" + "2" * 32, directory, summary
    )


def test_worker_verification_failure_with_valid_evidence_projects_rejected(
    tmp_path: Path,
) -> None:
    run_directory = tmp_path / ("run-" + "3" * 32)
    run_directory.mkdir()
    result_root = run_directory / "runner-artifacts"
    prepared_directory = tmp_path / "prepared-rejected-evidence"
    manifest, summary, _ = _projection_bundle(prepared_directory, authorized=False)
    write_json_atomic(
        prepared_directory / "summary.json", summary, maximum_bytes=1_000_000
    )

    class VerificationFailedProcess:
        def __init__(self) -> None:
            self.stdout = io.BytesIO()
            self.stderr = io.BytesIO()

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            return WORKER_EXIT_VERIFICATION_FAILED

        def kill(self) -> None:
            raise AssertionError("The completed worker must not be killed.")

    def process_factory(command: list[str], **options: Any) -> VerificationFailedProcess:
        del options
        artifact_directory = result_root / "controlled" / "run-001-failed"
        shutil.copytree(prepared_directory, artifact_directory)
        envelope = Path(command[command.index("--result-envelope") + 1])
        write_json_atomic(
            envelope,
            {
                "schema_version": WORKER_RESULT_SCHEMA,
                "outcome": "verification_failed",
                "summary": None,
                "error": {
                    "error_type": "QuantumVerificationError",
                    "message": "Trusted execution was not authorized.",
                },
            },
            maximum_bytes=WORKER_RESULT_MAXIMUM_BYTES,
        )
        return VerificationFailedProcess()

    output = ExistingQuantumPreflightExecutor(
        repository_root=Path(__file__).resolve().parents[1],
        image_identifier="sha256:controlled",
        _process_factory=process_factory,
    ).execute(
        manifest,
        preset_identifier="lih-ground-state-v1",
        run_directory=run_directory,
        maximum_seconds=30,
    )
    assert output.receipt["authorized"] is False
    assert output.receipt["authorization_state"] == "rejected"
    assert output.verification["verification_passed"] is False


def test_real_projection_accepts_canonical_model_valid_evidence(tmp_path: Path) -> None:
    directory = tmp_path / "evidence"
    manifest, summary, _ = _projection_bundle(directory)
    output = _project_bundle(directory, manifest, summary)
    assert output.receipt["authorized"] is True
    assert output.results["experiment_fingerprint"] == manifest.experiment.fingerprint
    for value in (output.results, output.verification, output.receipt, output.runner_summary):
        assert_public_response_safe(value)


@pytest.mark.parametrize(
    ("artifact_identifier", "filename"),
    [
        ("verification_report", "verification-report.json"),
        ("compatibility_warnings", "compatibility-warnings.json"),
    ],
)
def test_real_projection_rejects_consumed_payload_changed_without_receipt_update(
    tmp_path: Path, artifact_identifier: str, filename: str,
) -> None:
    directory = tmp_path / "evidence"
    manifest, summary, _ = _projection_bundle(directory)
    payload = _read_artifact_payload(directory / filename)
    payload["untrusted_mutation"] = True
    _rewrite_artifact(directory / filename, _ARTIFACT_TYPES[artifact_identifier], payload)
    with pytest.raises(ValueError, match="does not match its receipt pointer"):
        _project_bundle(directory, manifest, summary)


def test_real_projection_rejects_changed_numerical_agreement_with_valid_pointer(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "evidence"
    manifest, summary, _ = _projection_bundle(directory)
    report = _read_artifact_payload(directory / "verification-report.json")
    report["numerical_agreement"]["absolute_difference_hartree"] = 0.5
    _rewrite_artifact(
        directory / "verification-report.json", _ARTIFACT_TYPES["verification_report"], report
    )
    _replace_receipt_pointer(directory, summary, "verification_report", report)
    with pytest.raises(ValueError, match="numerical agreement disagrees"):
        _project_bundle(directory, manifest, summary)


def test_real_projection_rejects_artifact_document_type_disagreement(tmp_path: Path) -> None:
    directory = tmp_path / "evidence"
    manifest, summary, _ = _projection_bundle(directory)
    report = _read_artifact_payload(directory / "verification-report.json")
    _rewrite_artifact(directory / "verification-report.json", "compatibility_warnings", report)
    with pytest.raises(ValueError, match="unexpected artifact type"):
        _project_bundle(directory, manifest, summary)


@pytest.mark.parametrize(
    "artifact_identifier", ["verification_report", "compatibility_warnings"]
)
def test_real_projection_rejects_consumed_receipt_pointer_hash_change(
    tmp_path: Path, artifact_identifier: str,
) -> None:
    directory = tmp_path / "evidence"
    manifest, summary, _ = _projection_bundle(directory)
    receipt = _read_artifact_payload(directory / "receipt.json")
    pointer = next(
        item for item in receipt["artifacts"]
        if item["artifact_identifier"] == artifact_identifier
    )
    pointer["content_sha256"] = "6" * 64
    _rewrite_receipt(directory, summary, receipt)
    with pytest.raises(ValueError, match="does not match its receipt pointer"):
        _project_bundle(directory, manifest, summary)


@pytest.mark.parametrize("artifact_name", ["exact", "vqe"])
def test_real_projection_rejects_cross_linked_scientific_result(
    tmp_path: Path, artifact_name: str,
) -> None:
    directory = tmp_path / "evidence"
    manifest, summary, _ = _projection_bundle(directory)
    filename = f"{artifact_name}-result.json"
    payload = _read_artifact_payload(directory / filename)
    payload["scientific_identity"]["solver_identifier"] += "_substitute"
    payload["execution_result"]["solver_identifier"] += "_substitute"
    identity = ScientificResultIdentity.model_validate(payload["scientific_identity"])
    payload["scientific_result_sha256"] = identity.fingerprint
    _rewrite_artifact(directory / filename, _ARTIFACT_TYPES[f"{artifact_name}_result"], payload)
    receipt = _read_artifact_payload(directory / "receipt.json")
    replacement = artifact_reference(
        f"{artifact_name}_result", _ARTIFACT_TYPES[f"{artifact_name}_result"], payload,
        filename=filename,
    ).pointer.model_dump(mode="json")
    receipt["artifacts"] = [
        replacement if item["artifact_identifier"] == f"{artifact_name}_result" else item
        for item in receipt["artifacts"]
    ]
    _rewrite_receipt(directory, summary, receipt)
    with pytest.raises(ValueError, match="scientific_identity_mismatch"):
        _project_bundle(directory, manifest, summary)


def test_real_projection_rejects_altered_receipt_pointer_hash(tmp_path: Path) -> None:
    directory = tmp_path / "evidence"
    manifest, summary, _ = _projection_bundle(directory)
    receipt = _read_artifact_payload(directory / "receipt.json")
    exact_pointer = next(
        item for item in receipt["artifacts"] if item["artifact_identifier"] == "exact_result"
    )
    exact_pointer["content_sha256"] = "9" * 64
    _rewrite_receipt(directory, summary, receipt)
    with pytest.raises(ValueError, match="does not match its receipt pointer"):
        _project_bundle(directory, manifest, summary)


def test_real_projection_rejects_altered_scientific_result_identity(tmp_path: Path) -> None:
    directory = tmp_path / "evidence"
    manifest, summary, _ = _projection_bundle(directory)
    payload = _read_artifact_payload(directory / "exact-result.json")
    payload["scientific_result_sha256"] = "8" * 64
    _rewrite_artifact(directory / "exact-result.json", _ARTIFACT_TYPES["exact_result"], payload)
    _replace_receipt_pointer(directory, summary, "exact_result", payload)
    with pytest.raises(ValueError, match="Scientific-result SHA-256"):
        _project_bundle(directory, manifest, summary)


@pytest.mark.parametrize("field", ["structure_sha256", "qubit_hamiltonian_sha256"])
def test_real_projection_rejects_summary_scientific_identity_mismatch(
    tmp_path: Path, field: str,
) -> None:
    directory = tmp_path / "evidence"
    manifest, summary, _ = _projection_bundle(directory)
    summary[field] = "7" * 64
    with pytest.raises(ValueError, match="mismatch"):
        _project_bundle(directory, manifest, summary)


def test_real_projection_rejects_malformed_receipt(tmp_path: Path) -> None:
    directory = tmp_path / "evidence"
    manifest, summary, _ = _projection_bundle(directory)
    _rewrite_artifact(directory / "receipt.json", _ARTIFACT_TYPES["receipt"], {"schema_version": "invalid"})
    with pytest.raises(ValueError):
        _project_bundle(directory, manifest, summary)


def test_real_projection_rejects_missing_artifact(tmp_path: Path) -> None:
    directory = tmp_path / "evidence"
    manifest, summary, _ = _projection_bundle(directory)
    (directory / "vqe-result.json").unlink()
    with pytest.raises(ValueError, match="missing"):
        _project_bundle(directory, manifest, summary)


@pytest.mark.parametrize("field", ["scientific_verification_passed", "authorized"])
def test_real_projection_rejects_summary_decision_disagreement(tmp_path: Path, field: str) -> None:
    directory = tmp_path / "evidence"
    manifest, summary, _ = _projection_bundle(directory)
    summary[field] = False
    with pytest.raises(ValueError, match="decision mismatch"):
        _project_bundle(directory, manifest, summary)


def test_real_projection_rejects_symlink_escape(tmp_path: Path) -> None:
    directory = tmp_path / "evidence"
    manifest, summary, _ = _projection_bundle(directory)
    outside = tmp_path / "outside.json"
    outside.write_bytes((directory / "exact-result.json").read_bytes())
    (directory / "exact-result.json").unlink()
    try:
        (directory / "exact-result.json").symlink_to(outside)
    except OSError:
        pytest.skip("Creating symbolic links is not available in this environment.")
    with pytest.raises(ValueError, match="symbolic link"):
        _project_bundle(directory, manifest, summary)
