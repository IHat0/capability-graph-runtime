from __future__ import annotations

import math
import os
import sys
import time
import types
import io
import inspect
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from cgr.quantum_preflight.artifacts import write_json_atomic
from cgr.quantum_preflight.reference import (
    canonical_qpy_sha256,
    canonical_sparse_pauli_op_sha256,
)
from cgr.pulsate_api.app import _load_preset, create_app
from cgr.pulsate_api.experiments import ExperimentStore
from cgr.pulsate_api.ibm import (
    HARDWARE_ROLE,
    IBM_PREFLIGHT_LAUNCHER_SCHEMA,
    IBMQuantumConfiguration,
    IBMQuantumRunExecutor,
    IBMPreparedSubmissionEvidence,
    IBMRuntimeResult,
    IBMSubmissionAttempt,
    IBMSubmissionBundle,
    IBMWorkerFailureEnvelope,
    RunBoundIsolatedIBMPreflightExecutor,
    SubprocessIBMRuntimeAdapter,
    UnavailableIBMPreflightExecutor,
)
from cgr.pulsate_api.ibm_worker import (
    _load_prepared_submission,
    _layout_indices,
    _obtain_job,
    _persist_prepared_submission,
    execute as execute_ibm_worker,
    partition_hamiltonian_constants,
)
from cgr.pulsate_api.runs import (
    ExecutionOutput,
    RecoverableIBMJobError,
    TerminalIBMJobError,
    RunCoordinator,
)
from test_pulsate_runs import ControlledExecutor, wait_for_terminal


def configuration(**changes: Any) -> IBMQuantumConfiguration:
    base = IBMQuantumConfiguration(
        token="server-secret-token",
        instance="server-instance",
        backend_name="ibm_fake_backend",
        target_precision=0.015,
        optimization_level=2,
        maximum_seconds=60,
        dependency_available=True,
        image_identifier="sha256:" + "b" * 64,
        backend_qubit_capacity=8,
    )
    return replace(base, **changes)


class AuthorizedLocalExecutor(ControlledExecutor):
    proven_no_network = True

    def __init__(
        self,
        *,
        nuclear_repulsion_energy_hartree: float = 0.7,
        total_energy_hartree: float = -1.13730603575,
    ) -> None:
        super().__init__()
        self.nuclear_repulsion_energy_hartree = nuclear_repulsion_energy_hartree
        self.total_energy_hartree = total_energy_hartree

    def execute(self, *args: Any, **kwargs: Any) -> ExecutionOutput:
        output = super().execute(*args, **kwargs)
        results = {
            **output.results,
            "exact_total_energy_hartree": self.total_energy_hartree,
            "vqe_total_energy_hartree": self.total_energy_hartree,
        }
        parameter_sha256 = __import__(
            "cgr.science", fromlist=["sha256_fingerprint"]
        ).sha256_fingerprint([0.125, -0.25])
        receipt = {
            **output.receipt,
            "artifacts": [
                {
                    "artifact_identifier": "ansatz_manifest",
                    "artifact_type": "circuit_ansatz_manifest",
                    "content_sha256": "a" * 64,
                }
            ],
        }
        summary = {
            **output.runner_summary,
            "exact_total_energy_hartree": results["exact_total_energy_hartree"],
            "vqe_total_energy_hartree": results["vqe_total_energy_hartree"],
            "absolute_difference_hartree": results["absolute_difference_hartree"],
            "tolerance_hartree": results["tolerance_hartree"],
            "optimized_parameters_sha256": parameter_sha256,
            "ibm_preflight": {
                "ansatz_sha256": "a" * 64,
                "optimized_parameters": [0.125, -0.25],
                "optimized_parameters_sha256": parameter_sha256,
                "source_bound_circuit_sha256": "1" * 64,
                "source_observable_sha256": "2" * 64,
                "number_of_qubits": 4,
                "number_of_parameters": 2,
                "circuit_depth": 24,
                "nuclear_repulsion_energy_hartree": self.nuclear_repulsion_energy_hartree,
                "artifact_lineage_validated": True,
                "scientific_preflight_image_identifier": "sha256:" + "c" * 64,
                "ibm_runtime_image_identifier": "sha256:" + "b" * 64,
            },
        }
        return ExecutionOutput(results, output.verification, receipt, summary)


class FakeIBMAdapter:
    def __init__(self, *, mutations: dict[str, Any] | None = None) -> None:
        self.mutations = mutations or {}
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
            record = __import__("json").loads(job_record_path.read_text(encoding="utf-8"))
            job_identifier = record["job_identifier"]
        else:
            self.submissions += 1
            job_identifier = "fake-job-0001"
            write_json_atomic(
                job_record_path,
                {
                    "bundle_sha256": bundle.bundle_sha256,
                    "job_identifier": job_identifier,
                    "backend_name": bundle.backend_name,
                    "submitted_at": "2026-07-22T00:00:00Z",
                },
                maximum_bytes=100_000,
            )
        values = {
            "bundle_sha256": bundle.bundle_sha256,
            "job_identifier": job_identifier,
            "backend_name": bundle.backend_name,
            "primitive_version": "0.48.0",
            "submitted_at": "2026-07-22T00:00:00Z",
            "completed_at": "2026-07-22T00:01:00Z",
            "job_status": "completed",
            "target_precision": bundle.target_precision,
            "raw_qubit_expectation_hartree": -1.85,
            "non_nuclear_electronic_shift_hartree": 0.01269396425,
            "electronic_constant_offsets_hartree": {
                "ActiveSpaceTransformer": 0.01269396425
            },
            "nuclear_repulsion_energy_hartree": 0.7,
            "ibm_electronic_energy_hartree": -1.83730603575,
            "standard_error": 0.01,
            "execution_metadata": {"shots": 4096},
            "optimization_level": bundle.optimization_level,
            "layout_sha256": __import__(
                "cgr.science", fromlist=["sha256_fingerprint"]
            ).sha256_fingerprint({"physical_qubits": (0, 1, 2, 3)}),
            "physical_qubits": (0, 1, 2, 3),
            "source_bound_circuit_sha256": bundle.source_bound_circuit_sha256,
            "transpiled_circuit_sha256": "2" * 64,
            "source_observable_sha256": bundle.source_observable_sha256,
            "transpiled_observable_sha256": "3" * 64,
            "optimized_parameters_sha256": bundle.optimized_parameters_sha256,
            "experiment_sha256": bundle.experiment_sha256,
            "structure_sha256": bundle.structure_sha256,
            "hamiltonian_sha256": bundle.hamiltonian_sha256,
            "package_versions": {"qiskit": "2.3.1", "qiskit-ibm-runtime": "0.48.0"},
            "runtime_options": {
                "max_execution_time": bundle.maximum_execution_time_seconds,
                "job_tags": [bundle.job_correlation_identifier],
            },
            "execution_image_identifier": bundle.ibm_runtime_image_identifier,
        }
        values.update(self.mutations)
        status_callback("queued_on_ibm", {"ibm_job_identifier": job_identifier})
        status_callback("running_on_ibm", {"ibm_job_identifier": job_identifier})
        return IBMRuntimeResult.model_validate(values)


def executor(
    tmp_path: Path,
    *,
    local: ControlledExecutor | None = None,
    adapter: FakeIBMAdapter | None = None,
    config: IBMQuantumConfiguration | None = None,
) -> tuple[IBMQuantumRunExecutor, FakeIBMAdapter]:
    del tmp_path
    fake = adapter or FakeIBMAdapter()
    return IBMQuantumRunExecutor(
        local_executor=local or AuthorizedLocalExecutor(),
        adapter=fake,
        configuration=config or configuration(),
    ), fake


def controlled_bundle() -> IBMSubmissionBundle:
    return IBMSubmissionBundle(
        bundle_identifier="ibm-submission-" + "a" * 32,
        experiment_identifier="experiment-controlled",
        experiment_sha256="1" * 64,
        structure_sha256="2" * 64,
        hamiltonian_sha256="3" * 64,
        ansatz_sha256="4" * 64,
        optimized_parameters=(0.125,),
        optimized_parameters_sha256="5" * 64,
        source_bound_circuit_sha256="6" * 64,
        source_observable_sha256="7" * 64,
        required_qubits=1,
        circuit_depth=1,
        backend_name="ibm_fake_backend",
        target_precision=0.015,
        optimization_level=2,
        maximum_execution_time_seconds=60,
        job_correlation_identifier="pulsate-" + "8" * 40,
        ibm_runtime_image_identifier="sha256:" + "b" * 64,
    )


def test_prepared_isa_evidence_is_loaded_without_retranspiling_on_recovery(
    tmp_path: Path,
) -> None:
    pytest.importorskip("qiskit")
    from qiskit import QuantumCircuit, transpile
    from qiskit.providers.fake_provider import GenericBackendV2
    from qiskit.quantum_info import SparsePauliOp

    bundle = controlled_bundle()
    backend = GenericBackendV2(num_qubits=2, seed=91)
    source = QuantumCircuit(2)
    source.h(0)
    source.cx(0, 1)
    prepared = transpile(
        source,
        backend=backend,
        optimization_level=bundle.optimization_level,
        seed_transpiler=bundle.seed_transpiler,
    )
    observable = SparsePauliOp.from_list([("ZZ", 0.75), ("IX", -0.25)])
    directory = tmp_path / "ibm-worker"
    directory.mkdir()
    _, _, evidence = _persist_prepared_submission(
        directory,
        bundle=bundle,
        backend=backend,
        isa_circuit=prepared,
        isa_observable=observable.apply_layout(prepared.layout),
        source_bound_circuit_sha256=bundle.source_bound_circuit_sha256,
        source_observable_sha256=bundle.source_observable_sha256,
        mapper="jordan_wigner",
    )
    alternative = QuantumCircuit(2)
    alternative.x(0)
    assert canonical_qpy_sha256(alternative) != evidence.transpiled_circuit_sha256

    recovered_circuit, _, recovered = _load_prepared_submission(
        directory, bundle=bundle
    )
    assert canonical_qpy_sha256(recovered_circuit) == evidence.transpiled_circuit_sha256
    assert recovered.seed_transpiler == bundle.seed_transpiler
    assert recovered.layout_sha256 == evidence.layout_sha256
    worker_source = inspect.getsource(execute_ibm_worker)
    recovery_branch = worker_source.index("if attempt_path.exists()")
    generation_branch = worker_source.index("else:\n        pass_manager", recovery_branch)
    assert recovery_branch < worker_source.index(
        "_load_prepared_submission", recovery_branch
    ) < generation_branch


def test_prepared_isa_evidence_rejects_bundle_or_file_mutation(tmp_path: Path) -> None:
    evidence = IBMPreparedSubmissionEvidence(
        bundle_sha256="1" * 64,
        source_bound_circuit_sha256="2" * 64,
        transpiled_circuit_sha256="3" * 64,
        source_observable_sha256="4" * 64,
        transpiled_observable_sha256="5" * 64,
        physical_qubits=(0, 1),
        layout_sha256=__import__(
            "cgr.science", fromlist=["sha256_fingerprint"]
        ).sha256_fingerprint({"physical_qubits": (0, 1)}),
        seed_transpiler=7341,
        optimization_level=2,
        backend_name="ibm_fake_backend",
        qiskit_version="2.3.1",
        observable_file_sha256="6" * 64,
    )
    assert evidence.bundle_sha256 == "1" * 64
    with pytest.raises(ValueError):
        IBMPreparedSubmissionEvidence.model_validate(
            {**evidence.model_dump(mode="json"), "layout_sha256": "not-a-hash"}
        )


def test_worker_recovery_branch_loads_prepared_isa_before_generation_branch() -> None:
    worker_source = inspect.getsource(execute_ibm_worker)
    recovery_branch = worker_source.index("if attempt_path.exists()")
    generation_branch = worker_source.index("else:\n        pass_manager", recovery_branch)
    assert recovery_branch < worker_source.index(
        "_load_prepared_submission", recovery_branch
    ) < generation_branch
    assert "seed_transpiler=bundle.seed_transpiler" in worker_source


def controlled_runtime_result(
    bundle: IBMSubmissionBundle,
    *,
    job_identifier: str = "fake-job-completed",
) -> IBMRuntimeResult:
    return IBMRuntimeResult(
        bundle_sha256=bundle.bundle_sha256,
        job_identifier=job_identifier,
        backend_name=bundle.backend_name,
        primitive_version="0.48.0",
        submitted_at="2026-07-22T00:00:00Z",
        completed_at="2026-07-22T00:01:00Z",
        job_status="completed",
        target_precision=bundle.target_precision,
        raw_qubit_expectation_hartree=-1.85,
        non_nuclear_electronic_shift_hartree=0.012,
        electronic_constant_offsets_hartree={"shift": 0.012},
        nuclear_repulsion_energy_hartree=0.7,
        ibm_electronic_energy_hartree=-1.838,
        standard_error=0.01,
        execution_metadata={},
        optimization_level=bundle.optimization_level,
        layout_sha256="8" * 64,
        physical_qubits=(0,),
        source_bound_circuit_sha256=bundle.source_bound_circuit_sha256,
        transpiled_circuit_sha256="9" * 64,
        source_observable_sha256=bundle.source_observable_sha256,
        transpiled_observable_sha256="a" * 64,
        optimized_parameters_sha256=bundle.optimized_parameters_sha256,
        experiment_sha256=bundle.experiment_sha256,
        structure_sha256=bundle.structure_sha256,
        hamiltonian_sha256=bundle.hamiltonian_sha256,
        package_versions={},
        runtime_options={
            "max_execution_time": bundle.maximum_execution_time_seconds,
            "job_tags": [bundle.job_correlation_identifier],
        },
        execution_image_identifier=bundle.ibm_runtime_image_identifier,
    )


def test_missing_credentials_make_ibm_capability_unavailable_and_never_echo_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PULSATE_IBM_QUANTUM_TOKEN", "never-echo-this-token")
    monkeypatch.delenv("PULSATE_IBM_QUANTUM_INSTANCE", raising=False)
    monkeypatch.setenv("PULSATE_IBM_QUANTUM_BACKEND", "ibm_safe_name")
    monkeypatch.setenv("PULSATE_IBM_IMAGE_IDENTIFIER", "sha256:" + "b" * 64)
    config = IBMQuantumConfiguration.from_environment()
    capability = config.capability()
    assert capability["available"] is False
    assert "INSTANCE" in capability["reason"]
    assert "never-echo-this-token" not in str(capability)


@pytest.mark.parametrize(
    "image_identifier",
    ("", "unknown", "local-uncontainerized", "sha256:not-an-image"),
)
def test_ibm_capability_rejects_unpinned_runtime_image(
    image_identifier: str,
) -> None:
    capability = configuration(image_identifier=image_identifier).capability()
    assert capability["available"] is False
    assert "exact pinned image" in capability["reason"]


def test_run_bound_launcher_requires_live_readiness_not_an_empty_directory(
    tmp_path: Path,
) -> None:
    exchange = tmp_path / "exchange"
    exchange.mkdir()
    (exchange / "requests").mkdir()
    (exchange / "handoffs").mkdir()
    executor = RunBoundIsolatedIBMPreflightExecutor(
        exchange,
        scientific_preflight_image_identifier="sha256:" + "c" * 64,
        ibm_runtime_image_identifier="sha256:" + "b" * 64,
    )
    assert "unavailable" in str(executor.unavailable_reason()).lower()


def test_run_bound_launcher_creates_and_consumes_random_run_handoff(
    tmp_path: Path,
) -> None:
    exchange = tmp_path / "exchange"
    requests = exchange / "requests"
    handoffs = exchange / "handoffs"
    requests.mkdir(parents=True)
    handoffs.mkdir()
    scientific = "sha256:" + "c" * 64
    ibm_runtime = "sha256:" + "b" * 64
    write_json_atomic(
        exchange / "launcher-readiness.json",
        {
            "schema_version": IBM_PREFLIGHT_LAUNCHER_SCHEMA,
            "launcher_mode": "run_bound_file_coordinator",
            "network_boundary": "docker_network_none",
            "network_disabled": True,
            "scientific_preflight_image_identifier": scientific,
            "ibm_runtime_image_identifier": ibm_runtime,
            "observed_at_epoch": time.time(),
        },
        maximum_bytes=100_000,
    )
    run_identifier = "run-" + "7" * 32
    run_directory = tmp_path / run_identifier
    run_directory.mkdir()
    manifest = _load_preset("h2-ground-state-v1")
    local = AuthorizedLocalExecutor().execute(
        manifest,
        preset_identifier="experiment-test",
        run_directory=run_directory,
        maximum_seconds=30,
    )

    def produce_handoff() -> None:
        request_path = requests / f"{run_identifier}.json"
        deadline = time.monotonic() + 2
        while not request_path.exists():
            if time.monotonic() >= deadline:
                raise AssertionError("Run-bound request was not created.")
            time.sleep(0.01)
        request = __import__("json").loads(request_path.read_text(encoding="utf-8"))
        assert request["run_identifier"] == run_identifier
        write_json_atomic(
            handoffs / f"{run_identifier}.json",
            {
                "schema_version": "cgr.pulsate-ibm-preflight-handoff/1.0.0",
                "run_identifier": run_identifier,
                "experiment_sha256": manifest.experiment.fingerprint,
                "network_boundary": "docker_network_none",
                "network_disabled": True,
                "scientific_preflight_image_identifier": scientific,
                "ibm_runtime_image_identifier": ibm_runtime,
                "output": {
                    "results": local.results,
                    "verification": local.verification,
                    "receipt": local.receipt,
                    "runner_summary": local.runner_summary,
                },
            },
            maximum_bytes=2 * 1024 * 1024,
        )

    producer = __import__("threading").Thread(target=produce_handoff)
    producer.start()
    observed = RunBoundIsolatedIBMPreflightExecutor(
        exchange,
        scientific_preflight_image_identifier=scientific,
        ibm_runtime_image_identifier=ibm_runtime,
    ).execute(
        manifest,
        preset_identifier="experiment-test",
        run_directory=run_directory,
        maximum_seconds=2,
    )
    producer.join(timeout=2)
    assert not producer.is_alive()
    assert (
        observed.runner_summary["ibm_preflight"][
            "scientific_preflight_image_identifier"
        ]
        == scientific
    )
    assert (
        observed.runner_summary["ibm_preflight"][
            "ibm_runtime_image_identifier"
        ]
        == ibm_runtime
    )


def test_explicit_ibm_plan_is_ready_only_with_injected_available_capability(tmp_path: Path) -> None:
    store = ExperimentStore(tmp_path / "experiments", ibm_capability=configuration().capability)
    store.start()
    plan = store.plan("Calculate the ground-state energy of H2 at 0.9 Å on IBM Quantum")
    assert plan["ready_for_execution"] is True
    assert plan["requested_execution_target"] == "ibm_quantum"
    assert plan["specification"]["execution_target"] == "ibm_quantum"


def test_local_and_ibm_specs_cannot_be_promoted_or_downgraded(tmp_path: Path) -> None:
    ibm_executor, _ = executor(tmp_path)
    store = ExperimentStore(tmp_path / "experiments", ibm_capability=ibm_executor.capability)
    coordinator = RunCoordinator(
        run_root=tmp_path / "runs",
        manifest_resolver=_load_preset,
        experiment_resolver=store.resolve_for_targeted_run,
        executor=AuthorizedLocalExecutor(),
        ibm_executor=ibm_executor,
        enabled=True,
    )
    with TestClient(create_app(coordinator=coordinator, experiment_store=store)) as client:
        local_plan = client.post("/api/v1/experiments/plan", json={"question": "Calculate the ground-state energy of H2 at 0.9 Å"}).json()
        ibm_plan = client.post("/api/v1/experiments/plan", json={"question": "Calculate the ground-state energy of H2 at 0.9 Å on IBM Quantum"}).json()
        local_as_ibm = client.post("/api/v1/runs", json={"experiment_identifier": local_plan["experiment_identifier"], "execution_target": "ibm_quantum"})
        ibm_as_local = client.post("/api/v1/runs", json={"experiment_identifier": ibm_plan["experiment_identifier"], "execution_target": "local_simulator"})
    assert local_as_ibm.status_code == 422
    assert ibm_as_local.status_code == 422


def test_rejected_local_preflight_and_excessive_qubits_submit_nothing(tmp_path: Path) -> None:
    rejected_local = ControlledExecutor("rejected")
    rejected_local.proven_no_network = True
    rejected, rejected_adapter = executor(tmp_path, local=rejected_local)
    manifest = _load_preset("h2-ground-state-v1")
    output = rejected.execute(
        manifest,
        preset_identifier="experiment-test",
        run_directory=(tmp_path / "rejected"),
        maximum_seconds=30,
        status_callback=lambda *_: None,
    ) if (tmp_path / "rejected").mkdir() is None else None
    assert output is not None and output.receipt["ibm_execution"]["submission_status"] == "blocked_by_local_preflight"
    assert rejected_adapter.submissions == 0

    local = AuthorizedLocalExecutor()
    too_large, adapter = executor(tmp_path, local=local, config=configuration(backend_qubit_capacity=2))
    directory = tmp_path / "too-large"
    directory.mkdir()
    with pytest.raises(ValueError, match="qubit capacity"):
        too_large.execute(manifest, preset_identifier="experiment-test", run_directory=directory, maximum_seconds=30, status_callback=lambda *_: None)
    assert adapter.submissions == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("backend_name", "wrong-backend"),
        ("structure_sha256", "3" * 64),
        ("hamiltonian_sha256", "4" * 64),
        ("source_bound_circuit_sha256", "5" * 64),
        ("layout_sha256", ""),
        ("source_observable_sha256", "6" * 64),
    ],
)
def test_ibm_identity_mismatches_fail_closed(tmp_path: Path, field: str, value: Any) -> None:
    ibm, adapter = executor(tmp_path, adapter=FakeIBMAdapter(mutations={field: value}))
    directory = tmp_path / field
    directory.mkdir()
    with pytest.raises((ValueError, Exception)):
        ibm.execute(_load_preset("h2-ground-state-v1"), preset_identifier="experiment-test", run_directory=directory, maximum_seconds=30, status_callback=lambda *_: None)
    assert adapter.submissions == 1


@pytest.mark.parametrize("expectation", [math.nan, math.inf, -math.inf])
def test_nonfinite_ibm_expectation_fails_closed(tmp_path: Path, expectation: float) -> None:
    ibm, _ = executor(tmp_path, adapter=FakeIBMAdapter(mutations={"raw_qubit_expectation_hartree": expectation}))
    directory = tmp_path / f"nonfinite-{str(expectation).replace('-', 'n')}"
    directory.mkdir()
    with pytest.raises(ValueError, match="non-finite"):
        ibm.execute(_load_preset("h2-ground-state-v1"), preset_identifier="experiment-test", run_directory=directory, maximum_seconds=30, status_callback=lambda *_: None)


@pytest.mark.parametrize(
    ("molecule", "nuclear", "electronic", "offsets"),
    [
        ("H2", 0.7, -1.83730603575, {"ActiveSpaceTransformer": 0.01269396425}),
        ("LiH", 0.88, -8.75, {"ActiveSpaceTransformer": -0.125}),
    ],
)
def test_h2_and_lih_add_nuclear_repulsion_exactly_once(
    tmp_path: Path,
    molecule: str,
    nuclear: float,
    electronic: float,
    offsets: dict[str, float],
) -> None:
    total = electronic + nuclear
    local = AuthorizedLocalExecutor(
        nuclear_repulsion_energy_hartree=nuclear,
        total_energy_hartree=total,
    )
    shift = math.fsum(offsets.values())
    adapter = FakeIBMAdapter(
        mutations={
            "raw_qubit_expectation_hartree": electronic - shift,
            "non_nuclear_electronic_shift_hartree": shift,
            "electronic_constant_offsets_hartree": offsets,
            "nuclear_repulsion_energy_hartree": nuclear,
            "ibm_electronic_energy_hartree": electronic,
        }
    )
    ibm, _ = executor(tmp_path, local=local, adapter=adapter)
    directory = tmp_path / f"energy-{molecule.lower()}"
    directory.mkdir()
    if molecule == "LiH":
        store = ExperimentStore(
            tmp_path / "energy-experiments",
            ibm_capability=configuration().capability,
        )
        store.start()
        plan = store.plan(
            "Calculate the ground-state energy of LiH at 1.8 Å on IBM Quantum"
        )
        manifest, _projection, target = store.resolve_for_targeted_run(
            plan["experiment_identifier"]
        )
        assert target == "ibm_quantum"
        source_identifier = plan["experiment_identifier"]
    else:
        manifest = _load_preset("h2-ground-state-v1")
        source_identifier = "experiment-h2"
    output = ibm.execute(
        manifest,
        preset_identifier=source_identifier,
        run_directory=directory,
        maximum_seconds=30,
        status_callback=lambda *_: None,
    )
    evidence = output.receipt["ibm_execution"]
    assert evidence["ibm_electronic_energy_hartree"] == pytest.approx(electronic)
    assert evidence["nuclear_repulsion_energy_hartree"] == pytest.approx(nuclear)
    assert evidence["ibm_total_energy_hartree"] == pytest.approx(total)
    assert evidence["ibm_total_energy_hartree"] != pytest.approx(total + nuclear)


@pytest.mark.parametrize(
    "entries",
    [
        [],
        [("shift", 1.0)],
        [
            ("nuclear_repulsion_energy", 0.7),
            ("nuclear_repulsion_energy", 0.7),
        ],
        [("nuclear_repulsion_energy", math.nan)],
        [("nuclear_repulsion_energy", 0.7), ("shift", math.inf)],
        [("nuclear_repulsion_energy", 0.7), ("shift", 1 + 1j)],
        [("nuclear_repulsion_energy", 0.7), ("shift", 1.0), ("shift", 2.0)],
    ],
)
def test_hamiltonian_constant_partition_rejects_missing_duplicate_or_invalid_offsets(
    entries: list[tuple[str, Any]],
) -> None:
    with pytest.raises(ValueError):
        partition_hamiltonian_constants(entries)


def test_canonical_identities_change_when_actual_objects_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    try:
        import qiskit
        from qiskit import quantum_info

        parameter = qiskit.circuit.Parameter("theta")
        source = qiskit.QuantumCircuit(1)
        source.ry(parameter, 0)
        first_bound = source.assign_parameters({parameter: 0.125}, inplace=False)
        second_bound = source.assign_parameters({parameter: 0.25}, inplace=False)
        mutated_circuit = first_bound.copy()
        mutated_circuit.x(0)
        observable = quantum_info.SparsePauliOp.from_list([("Z", 1.0)])
        mutated_observable = quantum_info.SparsePauliOp.from_list([("X", 1.0)])
    except ModuleNotFoundError:
        fake_qiskit = types.ModuleType("qiskit")

        class QPY:
            @staticmethod
            def dump(circuit: Any, stream: Any) -> None:
                stream.write(repr(circuit.operations).encode("utf-8"))

        fake_qiskit.qpy = QPY
        monkeypatch.setitem(sys.modules, "qiskit", fake_qiskit)

        class Circuit:
            def __init__(self, operations: tuple[Any, ...]) -> None:
                self.operations = operations

        class Observable:
            num_qubits = 1

            def __init__(self, label: str) -> None:
                self.label = label

            def to_list(self) -> list[tuple[str, complex]]:
                return [(self.label, 1 + 0j)]

        first_bound = Circuit((("ry", 0.125),))
        second_bound = Circuit((("ry", 0.25),))
        mutated_circuit = Circuit((("ry", 0.125), ("x", 0)))
        observable = Observable("Z")
        mutated_observable = Observable("X")

    assert canonical_qpy_sha256(first_bound) != canonical_qpy_sha256(second_bound)
    assert canonical_qpy_sha256(first_bound) != canonical_qpy_sha256(mutated_circuit)
    assert canonical_sparse_pauli_op_sha256(
        observable, mapper="jordan_wigner"
    ) != canonical_sparse_pauli_op_sha256(
        mutated_observable, mapper="jordan_wigner"
    )

    class Layout:
        def __init__(self, values: tuple[int, ...]) -> None:
            self.values = values

        def final_index_layout(self) -> tuple[int, ...]:
            return self.values

    first_layout = type("Circuit", (), {"layout": Layout((0, 2))})()
    second_layout = type("Circuit", (), {"layout": Layout((1, 2))})()
    assert _layout_indices(first_layout) != _layout_indices(second_layout)


@pytest.mark.parametrize(
    "mutation",
    [
        ("results", "exact_total_energy_hartree"),
        ("verification", "verification_passed"),
        ("receipt", "authorized"),
        ("results", "structure_sha256"),
        ("results", "hamiltonian_sha256"),
        ("runner_summary", "ansatz_sha256"),
        ("runner_summary", "optimized_parameters"),
    ],
)
def test_tampered_recovered_local_preflight_submits_zero_jobs(
    tmp_path: Path,
    mutation: tuple[str, str],
) -> None:
    directory = tmp_path / ("tamper-" + "-".join(mutation))
    directory.mkdir()
    manifest = _load_preset("h2-ground-state-v1")
    initial, _ = executor(tmp_path)
    initial.execute(
        manifest,
        preset_identifier="experiment-tamper",
        run_directory=directory,
        maximum_seconds=30,
        status_callback=lambda *_: None,
    )
    worker_directory = directory / "ibm-worker"
    local_path = worker_directory / "local-preflight.json"
    document = __import__("json").loads(local_path.read_text(encoding="utf-8"))
    section, field = mutation
    if section == "runner_summary":
        target = document[section]["ibm_preflight"]
    else:
        target = document[section]
    if field == "optimized_parameters":
        target[field] = [9.0, 9.0]
    elif isinstance(target[field], bool):
        target[field] = not target[field]
    elif isinstance(target[field], str):
        target[field] = "0" * 64
    else:
        target[field] = float(target[field]) + 1
    payload = {
        key: document[key]
        for key in ("results", "verification", "receipt", "runner_summary")
    }
    document["local_preflight_sha256"] = __import__(
        "cgr.science", fromlist=["sha256_fingerprint"]
    ).sha256_fingerprint(payload)
    write_json_atomic(local_path, document, maximum_bytes=2 * 1024 * 1024)
    (worker_directory / "job.json").unlink()

    recovered_adapter = FakeIBMAdapter()
    recovered, _ = executor(tmp_path, adapter=recovered_adapter)
    with pytest.raises(ValueError):
        recovered.execute(
            manifest,
            preset_identifier="experiment-tamper",
            run_directory=directory,
            maximum_seconds=30,
            status_callback=lambda *_: None,
        )
    assert recovered_adapter.submissions == 0
    assert recovered_adapter.retrievals == 0


def test_persisted_job_is_retrieved_without_second_submission(tmp_path: Path) -> None:
    ibm, adapter = executor(tmp_path)
    directory = tmp_path / "recovery"
    directory.mkdir()
    manifest = _load_preset("h2-ground-state-v1")
    first = ibm.execute(manifest, preset_identifier="experiment-test", run_directory=directory, maximum_seconds=30, status_callback=lambda *_: None)
    second = ibm.execute(manifest, preset_identifier="experiment-test", run_directory=directory, maximum_seconds=30, status_callback=lambda *_: None)
    assert adapter.submissions == 1
    assert adapter.retrievals == 1
    assert first.receipt["ibm_execution"]["job_identifier"] == second.receipt["ibm_execution"]["job_identifier"]


def test_crash_before_estimator_run_remains_indeterminate_without_submission(
    tmp_path: Path,
) -> None:
    bundle = controlled_bundle()
    attempt_path = tmp_path / "submission-attempt.json"
    job_path = tmp_path / "job.json"
    write_json_atomic(
        attempt_path,
        IBMSubmissionAttempt(
            bundle_sha256=bundle.bundle_sha256,
            bundle_identifier=bundle.bundle_identifier,
            backend_name=bundle.backend_name,
            submission_state="submission_started",
            created_at="2026-07-22T00:00:00Z",
            job_correlation_identifier=bundle.job_correlation_identifier,
        ).model_dump(mode="json"),
        maximum_bytes=100_000,
    )

    class Service:
        def jobs(self, **kwargs: Any) -> list[Any]:
            del kwargs
            return []

    class ForbiddenEstimator:
        def __init__(self, **kwargs: Any) -> None:
            del kwargs
            raise AssertionError("Recovery must not create another estimator.")

    with pytest.raises(RuntimeError, match="indeterminate"):
        _obtain_job(
            service=Service(),
            backend=object(),
            estimator_type=ForbiddenEstimator,
            bundle=bundle,
            isa_circuit=object(),
            isa_observable=object(),
            attempt_path=attempt_path,
            job_record_path=job_path,
        )
    assert not job_path.exists()


def test_crash_after_submission_before_job_id_reconciles_exactly_one_job(
    tmp_path: Path,
) -> None:
    bundle = controlled_bundle()
    attempt_path = tmp_path / "submission-attempt.json"
    job_path = tmp_path / "job.json"

    class Job:
        calls = 0

        def job_id(self) -> str:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("controlled crash after estimator.run")
            return "fake-job-reconciled"

    job = Job()

    class Service:
        def jobs(self, **kwargs: Any) -> list[Any]:
            del kwargs
            return [job]

    class Environment:
        job_tags: list[str] = []

    class Options:
        max_execution_time = 0
        environment = Environment()

    class Estimator:
        submissions = 0

        def __init__(self, **kwargs: Any) -> None:
            del kwargs
            self.options = Options()

        def run(self, *args: Any, **kwargs: Any) -> Job:
            del args, kwargs
            Estimator.submissions += 1
            return job

    with pytest.raises(RuntimeError, match="controlled crash"):
        _obtain_job(
            service=Service(),
            backend=object(),
            estimator_type=Estimator,
            bundle=bundle,
            isa_circuit=object(),
            isa_observable=object(),
            attempt_path=attempt_path,
            job_record_path=job_path,
        )
    recovered = _obtain_job(
        service=Service(),
        backend=object(),
        estimator_type=Estimator,
        bundle=bundle,
        isa_circuit=object(),
        isa_observable=object(),
        attempt_path=attempt_path,
        job_record_path=job_path,
    )
    assert Estimator.submissions == 1
    assert recovered[1] == "fake-job-reconciled"
    assert job_path.exists()


def test_crash_after_job_id_persistence_retrieves_without_resubmission(
    tmp_path: Path,
) -> None:
    bundle = controlled_bundle()
    attempt_path = tmp_path / "submission-attempt.json"
    job_path = tmp_path / "job.json"

    class Job:
        def job_id(self) -> str:
            return "fake-job-persisted"

    job = Job()

    class Service:
        retrievals = 0

        def job(self, identifier: str) -> Job:
            assert identifier == "fake-job-persisted"
            self.retrievals += 1
            return job

    service = Service()

    class Environment:
        job_tags: list[str] = []

    class Options:
        max_execution_time = 0
        environment = Environment()

    class Estimator:
        submissions = 0

        def __init__(self, **kwargs: Any) -> None:
            del kwargs
            self.options = Options()

        def run(self, *args: Any, **kwargs: Any) -> Job:
            del args, kwargs
            Estimator.submissions += 1
            return job

    _obtain_job(
        service=service,
        backend=object(),
        estimator_type=Estimator,
        bundle=bundle,
        isa_circuit=object(),
        isa_observable=object(),
        attempt_path=attempt_path,
        job_record_path=job_path,
    )
    _obtain_job(
        service=service,
        backend=object(),
        estimator_type=Estimator,
        bundle=bundle,
        isa_circuit=object(),
        isa_observable=object(),
        attempt_path=attempt_path,
        job_record_path=job_path,
    )
    assert Estimator.submissions == 1
    assert service.retrievals == 1


def test_duplicate_job_tag_matches_fail_closed(tmp_path: Path) -> None:
    bundle = controlled_bundle()
    attempt_path = tmp_path / "submission-attempt.json"
    write_json_atomic(
        attempt_path,
        IBMSubmissionAttempt(
            bundle_sha256=bundle.bundle_sha256,
            bundle_identifier=bundle.bundle_identifier,
            backend_name=bundle.backend_name,
            submission_state="submission_started",
            created_at="2026-07-22T00:00:00Z",
            job_correlation_identifier=bundle.job_correlation_identifier,
        ).model_dump(mode="json"),
        maximum_bytes=100_000,
    )
    job = type(
        "Job",
        (),
        {
            "tags": [bundle.job_correlation_identifier],
            "job_id": lambda self: "duplicate",
        },
    )

    class Service:
        def jobs(self, **kwargs: Any) -> list[Any]:
            del kwargs
            return [job(), job()]

    with pytest.raises(RuntimeError, match="Duplicate IBM submissions"):
        _obtain_job(
            service=Service(),
            backend=object(),
            estimator_type=object,
            bundle=bundle,
            isa_circuit=object(),
            isa_observable=object(),
            attempt_path=attempt_path,
            job_record_path=tmp_path / "job.json",
        )


def test_restart_with_persisted_result_starts_no_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = controlled_bundle()
    work_directory = tmp_path / "ibm-worker"
    work_directory.mkdir()
    job_path = work_directory / "job.json"
    write_json_atomic(
        job_path,
        {
            "bundle_sha256": bundle.bundle_sha256,
            "job_identifier": "fake-job-completed",
        },
        maximum_bytes=100_000,
    )
    result = controlled_runtime_result(bundle)
    write_json_atomic(
        work_directory / "result.json",
        result.model_dump(mode="json"),
        maximum_bytes=100_000,
    )
    monkeypatch.setattr(
        "cgr.pulsate_api.ibm.subprocess.Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("Persisted result recovery must not start a worker.")
        ),
    )
    adapter = SubprocessIBMRuntimeAdapter(
        repository_root=Path(__file__).resolve().parents[1],
        configuration=configuration(),
    )
    observed = adapter.execute(
        bundle,
        _load_preset("h2-ground-state-v1"),
        work_directory=work_directory,
        job_record_path=job_path,
        maximum_seconds=1,
        status_callback=lambda *_: None,
    )
    assert observed.job_identifier == "fake-job-completed"


def test_adapter_projects_only_observed_runtime_statuses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = controlled_bundle()
    work_directory = tmp_path / "status-worker"
    work_directory.mkdir()
    write_json_atomic(
        work_directory / "submission.json",
        bundle.model_dump(mode="json"),
        maximum_bytes=100_000,
    )
    job_path = work_directory / "job.json"
    statuses = iter(("QUEUED", "RUNNING", "COMPLETED"))

    class Process:
        def __init__(self, command: list[str], **options: Any) -> None:
            del command, options
            self.stdout = io.BytesIO()
            self.stderr = io.BytesIO()
            self.completed = False
            write_json_atomic(
                job_path,
                {
                    "bundle_sha256": bundle.bundle_sha256,
                    "job_identifier": "fake-job-status",
                    "backend_name": bundle.backend_name,
                },
                maximum_bytes=100_000,
            )

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            try:
                runtime_status = next(statuses)
            except StopIteration:
                write_json_atomic(
                    work_directory / "result.json",
                    controlled_runtime_result(
                        bundle, job_identifier="fake-job-status"
                    ).model_dump(mode="json"),
                    maximum_bytes=100_000,
                )
                self.completed = True
                return 0
            write_json_atomic(
                work_directory / "status.json",
                {
                    "bundle_sha256": bundle.bundle_sha256,
                    "job_identifier": "fake-job-status",
                    "backend_name": bundle.backend_name,
                    "runtime_status": runtime_status,
                    "observed_at": "2026-07-22T00:00:00Z",
                },
                maximum_bytes=100_000,
            )
            raise subprocess.TimeoutExpired(["controlled"], 0.01)

        def poll(self) -> int | None:
            return 0 if self.completed else None

    monkeypatch.setattr("cgr.pulsate_api.ibm.subprocess.Popen", Process)
    observed_statuses: list[str] = []
    adapter = SubprocessIBMRuntimeAdapter(
        repository_root=Path(__file__).resolve().parents[1],
        configuration=configuration(),
    )
    result = adapter.execute(
        bundle,
        _load_preset("h2-ground-state-v1"),
        work_directory=work_directory,
        job_record_path=job_path,
        maximum_seconds=5,
        status_callback=lambda status, additions=None: observed_statuses.append(status),
    )
    assert result.job_identifier == "fake-job-status"
    assert observed_statuses == [
        "queued_on_ibm",
        "running_on_ibm",
        "verifying_ibm_result",
    ]


def test_adapter_timeout_after_job_identity_is_recoverable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = controlled_bundle()
    work_directory = tmp_path / "timeout-worker"
    work_directory.mkdir()
    write_json_atomic(
        work_directory / "submission.json",
        bundle.model_dump(mode="json"),
        maximum_bytes=100_000,
    )
    job_path = work_directory / "job.json"

    class Process:
        def __init__(self, command: list[str], **options: Any) -> None:
            del command, options
            self.stdout = io.BytesIO()
            self.stderr = io.BytesIO()
            self.killed = False
            write_json_atomic(
                job_path,
                {
                    "bundle_sha256": bundle.bundle_sha256,
                    "job_identifier": "fake-job-recoverable",
                    "backend_name": bundle.backend_name,
                },
                maximum_bytes=100_000,
            )
            write_json_atomic(
                work_directory / "status.json",
                {
                    "bundle_sha256": bundle.bundle_sha256,
                    "job_identifier": "fake-job-recoverable",
                    "backend_name": bundle.backend_name,
                    "runtime_status": "RUNNING",
                    "observed_at": "2026-07-22T00:00:00Z",
                },
                maximum_bytes=100_000,
            )

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            if self.killed:
                return 0
            raise subprocess.TimeoutExpired(["controlled"], 0.001)

        def poll(self) -> int | None:
            return 0 if self.killed else None

        def kill(self) -> None:
            self.killed = True

    monkeypatch.setattr("cgr.pulsate_api.ibm.subprocess.Popen", Process)
    adapter = SubprocessIBMRuntimeAdapter(
        repository_root=Path(__file__).resolve().parents[1],
        configuration=configuration(),
    )
    with pytest.raises(RecoverableIBMJobError) as raised:
        adapter.execute(
            bundle,
            _load_preset("h2-ground-state-v1"),
            work_directory=work_directory,
            job_record_path=job_path,
            maximum_seconds=0.01,
            status_callback=lambda *_: None,
        )
    assert raised.value.job_identifier == "fake-job-recoverable"
    assert job_path.exists()


@pytest.mark.parametrize(
    ("category", "runtime_status", "recoverable", "error_type"),
    [
        ("transient_result_failure", "COMPLETED", True, RecoverableIBMJobError),
        ("terminal_job_failure", "FAILED", False, TerminalIBMJobError),
    ],
)
def test_worker_failure_envelope_preserves_recovery_or_terminal_job_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    category: str,
    runtime_status: str,
    recoverable: bool,
    error_type: type[Exception],
) -> None:
    bundle = controlled_bundle()
    work_directory = tmp_path / "failure-worker"
    work_directory.mkdir()
    write_json_atomic(
        work_directory / "submission.json",
        bundle.model_dump(mode="json"),
        maximum_bytes=100_000,
    )
    job_path = work_directory / "job.json"

    class Process:
        def __init__(self, command: list[str], **options: Any) -> None:
            del command, options
            self.stdout = io.BytesIO()
            self.stderr = io.BytesIO(b"provider detail must stay private")
            write_json_atomic(
                job_path,
                {
                    "bundle_sha256": bundle.bundle_sha256,
                    "job_identifier": "fake-job-envelope",
                    "backend_name": bundle.backend_name,
                },
                maximum_bytes=100_000,
            )
            write_json_atomic(
                work_directory / "failure.json",
                IBMWorkerFailureEnvelope(
                    category=category,
                    job_identifier_persisted=True,
                    job_identifier="fake-job-envelope",
                    last_controlled_ibm_status=runtime_status,
                    retrieval_recoverable=recoverable,
                ).model_dump(mode="json"),
                maximum_bytes=100_000,
            )

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            return 1

        @staticmethod
        def poll() -> int:
            return 1

    monkeypatch.setattr("cgr.pulsate_api.ibm.subprocess.Popen", Process)
    adapter = SubprocessIBMRuntimeAdapter(
        repository_root=Path(__file__).resolve().parents[1],
        configuration=configuration(),
    )
    with pytest.raises(error_type) as raised:
        adapter.execute(
            bundle,
            _load_preset("h2-ground-state-v1"),
            work_directory=work_directory,
            job_record_path=job_path,
            maximum_seconds=1,
            status_callback=lambda *_: None,
        )
    assert getattr(raised.value, "job_identifier") == "fake-job-envelope"
    assert "provider detail" not in str(raised.value)


def test_ibm_worker_environment_is_strictly_allowlisted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "must-not-cross")
    monkeypatch.setenv("DATABASE_URL", "must-not-cross")
    monkeypatch.setenv("SMTP_PASSWORD", "must-not-cross")
    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))
    adapter = SubprocessIBMRuntimeAdapter(
        repository_root=Path(__file__).resolve().parents[1],
        configuration=configuration(),
    )
    environment = adapter._worker_environment()
    assert environment["PULSATE_IBM_QUANTUM_TOKEN"] == "server-secret-token"
    assert environment["PULSATE_IBM_QUANTUM_INSTANCE"] == "server-instance"
    assert environment["PULSATE_IBM_QUANTUM_BACKEND"] == "ibm_fake_backend"
    assert "AWS_SECRET_ACCESS_KEY" not in environment
    assert "DATABASE_URL" not in environment
    assert "SMTP_PASSWORD" not in environment


def test_ibm_capability_fails_closed_without_isolated_preflight_handoff() -> None:
    executor = IBMQuantumRunExecutor(
        local_executor=UnavailableIBMPreflightExecutor(),
        adapter=FakeIBMAdapter(),
        configuration=configuration(),
    )
    capability = executor.capability()
    assert capability["available"] is False
    assert "isolated" in capability["reason"].lower()


def test_coordinator_restart_recovers_recorded_job_without_resubmission(tmp_path: Path) -> None:
    adapter = FakeIBMAdapter()
    first_ibm, _ = executor(tmp_path, adapter=adapter)
    store = ExperimentStore(tmp_path / "experiments", ibm_capability=first_ibm.capability)

    def coordinator(ibm_executor: IBMQuantumRunExecutor) -> RunCoordinator:
        return RunCoordinator(
            run_root=tmp_path / "runs",
            manifest_resolver=_load_preset,
            experiment_resolver=store.resolve_for_targeted_run,
            executor=AuthorizedLocalExecutor(),
            ibm_executor=ibm_executor,
            enabled=True,
        )

    with TestClient(create_app(coordinator=coordinator(first_ibm), experiment_store=store)) as client:
        plan = client.post("/api/v1/experiments/plan", json={"question": "Calculate the ground-state energy of H2 at 0.9 Å on IBM Quantum"}).json()
        created = client.post(
            "/api/v1/runs",
            headers={"Idempotency-Key": "ibm-restart-recovery-0001"},
            json={"experiment_identifier": plan["experiment_identifier"], "execution_target": "ibm_quantum"},
        ).json()
        completed = wait_for_terminal(client, created["run_identifier"])
        assert completed["status"] == "authorized"

    state_path = tmp_path / "runs" / created["run_identifier"] / "state.json"
    state = __import__("json").loads(state_path.read_text(encoding="utf-8"))
    state["status"] = "running_on_ibm"
    write_json_atomic(state_path, state, maximum_bytes=2 * 1024 * 1024)

    recovered_ibm, _ = executor(tmp_path, adapter=adapter)
    with TestClient(create_app(coordinator=coordinator(recovered_ibm), experiment_store=store)) as client:
        recovered = wait_for_terminal(client, created["run_identifier"])
    assert recovered["status"] == "authorized"
    assert adapter.submissions == 1
    assert adapter.retrievals == 1


def test_fake_adapter_end_to_end_receipt_and_quality_projection(tmp_path: Path) -> None:
    ibm_executor, adapter = executor(tmp_path)
    store = ExperimentStore(tmp_path / "experiments", ibm_capability=ibm_executor.capability)
    coordinator = RunCoordinator(
        run_root=tmp_path / "runs",
        manifest_resolver=_load_preset,
        experiment_resolver=store.resolve_for_targeted_run,
        executor=AuthorizedLocalExecutor(),
        ibm_executor=ibm_executor,
        enabled=True,
    )
    with TestClient(create_app(coordinator=coordinator, experiment_store=store)) as client:
        capability = client.get("/api/v1/runs/capability").json()
        assert capability["ibm_quantum"]["available"] is True
        assert "server-secret-token" not in str(capability)
        plan = client.post("/api/v1/experiments/plan", json={"question": "Calculate the ground-state energy of H2 at 0.9 Å on IBM Quantum"}).json()
        response = client.post(
            "/api/v1/runs",
            headers={"Idempotency-Key": "ibm-fake-integration-0001"},
            json={"experiment_identifier": plan["experiment_identifier"], "execution_target": "ibm_quantum"},
        )
        assert response.status_code == 202
        state = wait_for_terminal(client, response.json()["run_identifier"])
        results = client.get(f"/api/v1/runs/{state['run_identifier']}/results").json()
        verification = client.get(f"/api/v1/runs/{state['run_identifier']}/verification").json()
        receipt = client.get(f"/api/v1/runs/{state['run_identifier']}/receipt").json()

    ibm = receipt["ibm_execution"]
    assert state["status"] == "authorized"
    assert adapter.submissions == 1
    assert ibm["hardware_role"] == HARDWARE_ROLE
    assert ibm["job_identifier"] == "fake-job-0001"
    assert ibm["backend_name"] == "ibm_fake_backend"
    assert ibm["execution_integrity_passed"] is True
    assert ibm["scientific_quality_passed"] is True
    assert results["structure_sha256"] == plan["structure_hash"] == ibm["structure_sha256"]
    assert results["ibm_execution"] == verification["ibm_execution"] == ibm
    assert "server-secret-token" not in str((state, results, verification, receipt))


def test_poor_hardware_value_is_rejected_by_separate_quality_policy(tmp_path: Path) -> None:
    ibm, _ = executor(
        tmp_path,
        adapter=FakeIBMAdapter(
            mutations={
                "raw_qubit_expectation_hartree": -5.0,
                "ibm_electronic_energy_hartree": -4.98730603575,
            }
        ),
    )
    directory = tmp_path / "poor-quality"
    directory.mkdir()
    output = ibm.execute(_load_preset("h2-ground-state-v1"), preset_identifier="experiment-test", run_directory=directory, maximum_seconds=30, status_callback=lambda *_: None)
    assert output.receipt["ibm_execution"]["execution_integrity_passed"] is True
    assert output.receipt["ibm_execution"]["scientific_quality_passed"] is False
    assert output.receipt["authorized"] is False
