"""Isolated network-enabled worker for one IBM Runtime EstimatorV2 job."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import io
import json
import math
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from cgr.quantum_preflight.artifacts import artifact_reference, write_json_atomic
from cgr.quantum_preflight.contracts import ManifestEnvelope
from cgr.quantum_preflight.reference import (
    CANONICAL_QPY_MAXIMUM_BYTES,
    canonical_qpy_bytes,
    canonical_qpy_sha256,
    canonical_sparse_pauli_op_payload,
    canonical_sparse_pauli_op_sha256,
    prepare_problem,
)
from cgr.science import sha256_fingerprint

from .ibm import (
    IBMPreparedSubmissionEvidence,
    IBMRuntimeResult,
    IBMSubmissionAttempt,
    IBMSubmissionBundle,
    IBMWorkerFailureEnvelope,
)

_MAXIMUM_BYTES = 2 * 1024 * 1024
_STATUS_POLL_INTERVAL_SECONDS = 1.0
_NUCLEAR_CONSTANT_KEY = "nuclear_repulsion_energy"


class _ControlledWorkerFailure(RuntimeError):
    def __init__(self, category: str, *, status: str | None = None) -> None:
        super().__init__(category)
        self.category = category
        self.status = status


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _validated_paths(arguments: argparse.Namespace) -> tuple[Path, Path, Path, Path]:
    submission = Path(arguments.submission)
    manifest = Path(arguments.manifest)
    job_record = Path(arguments.job_record)
    result = Path(arguments.result_envelope)
    raw_parents = tuple(
        path.parent for path in (submission, manifest, job_record, result)
    )
    if any(parent.is_symlink() for parent in raw_parents):
        raise ValueError("IBM worker control directory must not be a symbolic link.")
    parents = {parent.resolve(strict=True) for parent in raw_parents}
    if len(parents) != 1:
        raise ValueError("IBM worker control files must share one directory.")
    parent = next(iter(parents))
    if (
        parent.name != "ibm-worker"
        or not parent.is_dir()
        or re.fullmatch(r"run-[0-9a-f]{32}", parent.parent.name) is None
    ):
        raise ValueError("IBM worker control directory is invalid.")
    if result.name != "result.json" or job_record.name != "job.json":
        raise ValueError("IBM worker output filenames are invalid.")
    if result.exists() or result.is_symlink():
        raise ValueError("IBM worker result envelope must not pre-exist.")
    if job_record.is_symlink():
        raise ValueError("IBM worker job record must not be a symbolic link.")
    return submission, manifest, job_record, result


def _read_object(path: Path) -> dict[str, Any]:
    metadata = path.lstat()
    if path.is_symlink() or not path.is_file() or metadata.st_size > _MAXIMUM_BYTES:
        raise ValueError("IBM worker input is not a bounded regular file.")
    value = json.loads(path.read_bytes().decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("IBM worker input is not a JSON object.")
    return value


def _write_binary_atomic(path: Path, payload: bytes) -> None:
    if not payload or len(payload) > CANONICAL_QPY_MAXIMUM_BYTES:
        raise ValueError("Prepared IBM circuit evidence is empty or oversized.")
    if path.exists() or path.is_symlink():
        raise ValueError("Prepared IBM circuit evidence already exists.")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _read_binary(path: Path) -> bytes:
    metadata = path.lstat()
    if (
        path.is_symlink()
        or not path.is_file()
        or metadata.st_size <= 0
        or metadata.st_size > CANONICAL_QPY_MAXIMUM_BYTES
    ):
        raise ValueError("Prepared IBM circuit evidence is not a bounded regular file.")
    return path.read_bytes()


def _backend_target_sha256(backend: Any) -> str | None:
    target = getattr(backend, "target", None)
    if target is None:
        return None
    operations = sorted(str(value) for value in getattr(target, "operation_names", ()))
    instruction_properties: list[dict[str, Any]] = []
    for operation_name in operations[:256]:
        try:
            properties = target[operation_name]
            items = properties.items() if hasattr(properties, "items") else ()
            for qubits, value in items:
                if len(instruction_properties) >= 1024:
                    raise ValueError("IBM backend target calibration evidence is oversized.")
                instruction_properties.append(
                    {
                        "operation": operation_name,
                        "qubits": [int(qubit) for qubit in qubits],
                        "duration": _json_safe(getattr(value, "duration", None)),
                        "error": _json_safe(getattr(value, "error", None)),
                    }
                )
        except (KeyError, TypeError, AttributeError):
            continue
    payload = {
        "backend_name": str(getattr(backend, "name", "")),
        "backend_version": str(getattr(backend, "backend_version", "")),
        "number_of_qubits": int(getattr(backend, "num_qubits", 0)),
        "target_number_of_qubits": int(getattr(target, "num_qubits", 0)),
        "operation_names": operations[:256],
        "dt": _json_safe(getattr(target, "dt", None)),
        "instruction_properties": instruction_properties,
    }
    return sha256_fingerprint(payload)


def _observable_from_payload(payload: dict[str, Any]) -> Any:
    from qiskit.quantum_info import SparsePauliOp  # type: ignore[import-not-found]

    terms = payload.get("terms")
    if not isinstance(terms, list) or not terms:
        raise ValueError("Prepared IBM observable evidence is malformed.")
    values: list[tuple[str, complex]] = []
    for term in terms:
        coefficient = term.get("coefficient") if isinstance(term, dict) else None
        if not isinstance(coefficient, dict):
            raise ValueError("Prepared IBM observable coefficient is malformed.")
        values.append(
            (
                str(term["label"]),
                complex(
                    float.fromhex(str(coefficient["real_hex"])),
                    float.fromhex(str(coefficient["imag_hex"])),
                ),
            )
        )
    observable = SparsePauliOp.from_list(values)
    if int(observable.num_qubits) != int(payload.get("number_of_qubits", 0)):
        raise ValueError("Prepared IBM observable qubit count is inconsistent.")
    return observable


def _persist_prepared_submission(
    directory: Path,
    *,
    bundle: IBMSubmissionBundle,
    backend: Any,
    isa_circuit: Any,
    isa_observable: Any,
    source_bound_circuit_sha256: str,
    source_observable_sha256: str,
    mapper: str,
) -> tuple[Any, Any, IBMPreparedSubmissionEvidence]:
    qpy_path = directory / "prepared-isa-circuit.qpy"
    observable_path = directory / "prepared-isa-observable.json"
    evidence_path = directory / "prepared-submission.json"
    if any(path.exists() or path.is_symlink() for path in (qpy_path, observable_path, evidence_path)):
        raise ValueError("Prepared IBM submission evidence already exists.")
    qpy_payload = canonical_qpy_bytes(isa_circuit)
    observable_payload = canonical_sparse_pauli_op_payload(
        isa_observable, mapper=mapper
    )
    physical_qubits = _layout_indices(isa_circuit)
    evidence = IBMPreparedSubmissionEvidence(
        bundle_sha256=bundle.bundle_sha256,
        source_bound_circuit_sha256=source_bound_circuit_sha256,
        transpiled_circuit_sha256=hashlib.sha256(qpy_payload).hexdigest(),
        source_observable_sha256=source_observable_sha256,
        transpiled_observable_sha256=sha256_fingerprint(observable_payload),
        physical_qubits=physical_qubits,
        layout_sha256=sha256_fingerprint({"physical_qubits": physical_qubits}),
        seed_transpiler=bundle.seed_transpiler,
        optimization_level=bundle.optimization_level,
        backend_name=bundle.backend_name,
        backend_target_sha256=_backend_target_sha256(backend),
        qiskit_version=importlib.metadata.version("qiskit"),
        observable_file_sha256=sha256_fingerprint(observable_payload),
    )
    _write_binary_atomic(qpy_path, qpy_payload)
    write_json_atomic(observable_path, observable_payload, maximum_bytes=_MAXIMUM_BYTES)
    write_json_atomic(
        evidence_path, evidence.model_dump(mode="json"), maximum_bytes=_MAXIMUM_BYTES
    )
    return isa_circuit, isa_observable, evidence


def _load_prepared_submission(
    directory: Path,
    *,
    bundle: IBMSubmissionBundle,
) -> tuple[Any, Any, IBMPreparedSubmissionEvidence]:
    from qiskit import qpy  # type: ignore[import-not-found]

    evidence = IBMPreparedSubmissionEvidence.model_validate(
        _read_object(directory / "prepared-submission.json")
    )
    expected = {
        "bundle_sha256": bundle.bundle_sha256,
        "source_bound_circuit_sha256": bundle.source_bound_circuit_sha256,
        "source_observable_sha256": bundle.source_observable_sha256,
        "seed_transpiler": bundle.seed_transpiler,
        "optimization_level": bundle.optimization_level,
        "backend_name": bundle.backend_name,
    }
    if any(getattr(evidence, key) != value for key, value in expected.items()):
        raise ValueError("Prepared IBM submission evidence identity mismatch.")
    qpy_payload = _read_binary(directory / evidence.qpy_filename)
    if hashlib.sha256(qpy_payload).hexdigest() != evidence.transpiled_circuit_sha256:
        raise ValueError("Prepared IBM circuit evidence identity mismatch.")
    circuits = qpy.load(io.BytesIO(qpy_payload))
    if len(circuits) != 1:
        raise ValueError("Prepared IBM circuit evidence must contain exactly one circuit.")
    observable_payload = _read_object(directory / evidence.observable_filename)
    if sha256_fingerprint(observable_payload) != evidence.observable_file_sha256:
        raise ValueError("Prepared IBM observable file identity mismatch.")
    if sha256_fingerprint(observable_payload) != evidence.transpiled_observable_sha256:
        raise ValueError("Prepared IBM observable identity mismatch.")
    if evidence.layout_sha256 != sha256_fingerprint(
        {"physical_qubits": evidence.physical_qubits}
    ):
        raise ValueError("Prepared IBM layout identity mismatch.")
    return circuits[0], _observable_from_payload(observable_payload), evidence


def _layout_indices(circuit: Any) -> tuple[int, ...]:
    layout = getattr(circuit, "layout", None)
    if layout is None:
        raise ValueError("Transpiled IBM circuit has no layout.")
    values = tuple(int(value) for value in layout.final_index_layout())
    if not values or len(set(values)) != len(values):
        raise ValueError("Transpiled IBM circuit has an invalid layout.")
    return values


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if hasattr(value, "to_dict"):
        return _json_safe(value.to_dict())
    return str(value)[:1000]


def partition_hamiltonian_constants(
    entries: Iterable[tuple[Any, Any]],
) -> tuple[float, dict[str, float]]:
    """Separate the explicit nuclear constant from bounded electronic offsets."""
    nuclear_values: list[float] = []
    electronic: dict[str, float] = {}
    count = 0
    for raw_key, raw_value in entries:
        count += 1
        if count > 64:
            raise ValueError("Active-problem Hamiltonian constants are oversized.")
        key = str(raw_key)
        if not key or len(key) > 128:
            raise ValueError("Active-problem Hamiltonian constant key is invalid.")
        value = complex(raw_value)
        if (
            not math.isfinite(value.real)
            or not math.isfinite(value.imag)
            or abs(value.imag) > 1e-12
        ):
            raise ValueError("Active-problem Hamiltonian constant is non-finite or complex.")
        finite_value = float(value.real)
        if key == _NUCLEAR_CONSTANT_KEY:
            nuclear_values.append(finite_value)
        else:
            if key in electronic:
                raise ValueError("Duplicate active-problem Hamiltonian constant.")
            electronic[key] = finite_value
    if len(nuclear_values) != 1:
        raise ValueError(
            "Active-problem Hamiltonian must contain exactly one nuclear_repulsion_energy constant."
        )
    return nuclear_values[0], dict(sorted(electronic.items()))


def _normalized_job_status(job: Any) -> str:
    raw = job.status()
    name = getattr(raw, "name", raw)
    value = str(name).upper()
    if "." in value:
        value = value.rsplit(".", 1)[-1]
    aliases = {"PENDING": "QUEUED", "DONE": "COMPLETED"}
    return aliases.get(value, value)


def _write_runtime_status(
    path: Path,
    bundle: IBMSubmissionBundle,
    *,
    job_identifier: str,
    runtime_status: str,
) -> None:
    write_json_atomic(
        path,
        {
            "bundle_sha256": bundle.bundle_sha256,
            "job_identifier": job_identifier,
            "backend_name": bundle.backend_name,
            "runtime_status": runtime_status,
            "observed_at": _utc_now(),
        },
        maximum_bytes=_MAXIMUM_BYTES,
    )


def _matching_jobs(service: Any, bundle: IBMSubmissionBundle) -> list[Any]:
    try:
        matches = list(
            service.jobs(
                backend_name=bundle.backend_name,
                job_tags=[bundle.job_correlation_identifier],
                limit=3,
                descending=True,
            )
        )
    except Exception as exc:
        raise _ControlledWorkerFailure("transient_service_failure") from exc
    exact: list[Any] = []
    for job in matches:
        tags = getattr(job, "tags", None)
        if callable(tags):
            tags = tags()
        if tags is None or bundle.job_correlation_identifier in tags:
            exact.append(job)
    return exact


def _persist_job_record(
    path: Path,
    bundle: IBMSubmissionBundle,
    *,
    job_identifier: str,
    submitted_at: str,
) -> None:
    write_json_atomic(
        path,
        {
            "bundle_sha256": bundle.bundle_sha256,
            "bundle_identifier": bundle.bundle_identifier,
            "job_identifier": job_identifier,
            "backend_name": bundle.backend_name,
            "submitted_at": submitted_at,
            "job_correlation_identifier": bundle.job_correlation_identifier,
        },
        maximum_bytes=_MAXIMUM_BYTES,
    )


def _validate_attempt(
    attempt: IBMSubmissionAttempt, bundle: IBMSubmissionBundle
) -> None:
    expected = {
        "bundle_sha256": bundle.bundle_sha256,
        "bundle_identifier": bundle.bundle_identifier,
        "backend_name": bundle.backend_name,
        "job_correlation_identifier": bundle.job_correlation_identifier,
    }
    for field, value in expected.items():
        if getattr(attempt, field) != value:
            raise ValueError(f"IBM submission attempt {field} mismatch.")


def _obtain_job(
    *,
    service: Any,
    backend: Any,
    estimator_type: Any,
    bundle: IBMSubmissionBundle,
    isa_circuit: Any,
    isa_observable: Any,
    attempt_path: Path,
    job_record_path: Path,
) -> tuple[Any, str, str, dict[str, Any]]:
    runtime_options = {
        "max_execution_time": bundle.maximum_execution_time_seconds,
        "job_tags": [bundle.job_correlation_identifier],
    }
    if job_record_path.exists():
        record = _read_object(job_record_path)
        if (
            record.get("bundle_sha256") != bundle.bundle_sha256
            or record.get("bundle_identifier") != bundle.bundle_identifier
            or record.get("job_correlation_identifier")
            != bundle.job_correlation_identifier
        ):
            raise ValueError("Persisted IBM job belongs to a different submission bundle.")
        job_identifier = str(record.get("job_identifier"))
        try:
            job = service.job(job_identifier)
        except Exception as exc:
            raise _ControlledWorkerFailure("transient_service_failure") from exc
        return job, job_identifier, str(record.get("submitted_at")), runtime_options

    if attempt_path.exists():
        attempt = IBMSubmissionAttempt.model_validate(_read_object(attempt_path))
        _validate_attempt(attempt, bundle)
        matches = _matching_jobs(service, bundle)
        if not matches:
            raise _ControlledWorkerFailure("submission_indeterminate")
        if len(matches) > 1:
            raise RuntimeError("Duplicate IBM submissions matched the controlled job tag.")
        job = matches[0]
        job_identifier = str(job.job_id())
        _persist_job_record(
            job_record_path,
            bundle,
            job_identifier=job_identifier,
            submitted_at=attempt.created_at,
        )
        write_json_atomic(
            attempt_path,
            IBMSubmissionAttempt(
                bundle_sha256=bundle.bundle_sha256,
                bundle_identifier=bundle.bundle_identifier,
                backend_name=bundle.backend_name,
                submission_state="job_identifier_persisted",
                created_at=attempt.created_at,
                job_correlation_identifier=bundle.job_correlation_identifier,
                job_identifier=job_identifier,
            ).model_dump(mode="json"),
            maximum_bytes=_MAXIMUM_BYTES,
        )
        return job, job_identifier, attempt.created_at, runtime_options

    submitted_at = _utc_now()
    attempt = IBMSubmissionAttempt(
        bundle_sha256=bundle.bundle_sha256,
        bundle_identifier=bundle.bundle_identifier,
        backend_name=bundle.backend_name,
        submission_state="submission_started",
        created_at=submitted_at,
        job_correlation_identifier=bundle.job_correlation_identifier,
    )
    write_json_atomic(
        attempt_path,
        attempt.model_dump(mode="json"),
        maximum_bytes=_MAXIMUM_BYTES,
    )
    estimator = estimator_type(mode=backend)
    estimator.options.max_execution_time = bundle.maximum_execution_time_seconds
    estimator.options.environment.job_tags = [bundle.job_correlation_identifier]
    try:
        job = estimator.run(
            [(isa_circuit, isa_observable)], precision=bundle.target_precision
        )
    except Exception as exc:
        raise _ControlledWorkerFailure("submission_indeterminate") from exc
    job_identifier = str(job.job_id())
    _persist_job_record(
        job_record_path,
        bundle,
        job_identifier=job_identifier,
        submitted_at=submitted_at,
    )
    write_json_atomic(
        attempt_path,
        IBMSubmissionAttempt(
            bundle_sha256=bundle.bundle_sha256,
            bundle_identifier=bundle.bundle_identifier,
            backend_name=bundle.backend_name,
            submission_state="job_identifier_persisted",
            created_at=submitted_at,
            job_correlation_identifier=bundle.job_correlation_identifier,
            job_identifier=job_identifier,
        ).model_dump(mode="json"),
        maximum_bytes=_MAXIMUM_BYTES,
    )
    return job, job_identifier, submitted_at, runtime_options


def execute(
    bundle: IBMSubmissionBundle,
    manifest: ManifestEnvelope,
    *,
    job_record_path: Path,
) -> IBMRuntimeResult:
    from qiskit.transpiler.preset_passmanagers import (  # type: ignore[import-not-found]
        generate_preset_pass_manager,
    )
    from qiskit_ibm_runtime import (  # type: ignore[import-not-found]
        EstimatorV2,
        QiskitRuntimeService,
    )
    from qiskit_nature.second_q.circuit.library import (  # type: ignore[import-not-found]
        HartreeFock,
        UCCSD,
    )

    token = os.environ.get("PULSATE_IBM_QUANTUM_TOKEN")
    instance = os.environ.get("PULSATE_IBM_QUANTUM_INSTANCE")
    backend_name = os.environ.get("PULSATE_IBM_QUANTUM_BACKEND")
    image_identifier = os.environ.get("PULSATE_IBM_IMAGE_IDENTIFIER")
    if (
        not token
        or not instance
        or not backend_name
        or backend_name != bundle.backend_name
        or not image_identifier
        or image_identifier != bundle.ibm_runtime_image_identifier
    ):
        raise ValueError("IBM worker server configuration is incomplete or mismatched.")

    prepared = prepare_problem(manifest.experiment)
    hamiltonian_sha = artifact_reference(
        "qubit_hamiltonian",
        "qubit_hamiltonian",
        prepared.payloads["qubit_hamiltonian"],
        filename="qubit-hamiltonian.json",
    ).content_sha256
    if hamiltonian_sha != bundle.hamiltonian_sha256:
        raise ValueError("Reconstructed IBM Hamiltonian identity mismatch.")
    problem = prepared.active_problem
    initial_state = HartreeFock(
        problem.num_spatial_orbitals, problem.num_particles, prepared.mapper
    )
    ansatz = UCCSD(
        problem.num_spatial_orbitals,
        problem.num_particles,
        prepared.mapper,
        initial_state=initial_state,
    )
    if int(ansatz.num_parameters) != len(bundle.optimized_parameters):
        raise ValueError("Optimized parameter count does not match the reconstructed ansatz.")
    bound = ansatz.assign_parameters(list(bundle.optimized_parameters), inplace=False)
    source_bound_circuit_sha = canonical_qpy_sha256(bound)
    source_observable_sha = canonical_sparse_pauli_op_sha256(
        prepared.qubit_operator,
        mapper=manifest.experiment.quantum_model.mapper,
    )
    if source_bound_circuit_sha != bundle.source_bound_circuit_sha256:
        raise ValueError("Reconstructed bound source circuit identity mismatch.")
    if source_observable_sha != bundle.source_observable_sha256:
        raise ValueError("Reconstructed source observable identity mismatch.")
    ansatz_payload = {
        "schema_version": "cgr.ansatz-manifest/1.0.0",
        "ansatz": manifest.experiment.quantum_model.ansatz,
        "number_of_qubits": int(ansatz.num_qubits),
        "number_of_parameters": int(ansatz.num_parameters),
        "initial_state": manifest.experiment.quantum_model.initial_state,
        "mapper": manifest.experiment.quantum_model.mapper,
        "active_space_sha256": sha256_fingerprint(prepared.payloads["active_space"]),
        "hamiltonian_sha256": hamiltonian_sha,
        "initial_point_sha256": sha256_fingerprint([0.0] * int(ansatz.num_parameters)),
        "optimized_parameters_sha256": bundle.optimized_parameters_sha256,
        "circuit_depth": int(ansatz.decompose().depth()),
        "operation_counts": dict(ansatz.decompose().count_ops()),
        "qiskit_version": importlib.metadata.version("qiskit"),
    }
    ansatz_sha = artifact_reference(
        "ansatz_manifest",
        "circuit_ansatz_manifest",
        ansatz_payload,
        filename="ansatz-manifest.json",
    ).content_sha256
    if ansatz_sha != bundle.ansatz_sha256:
        raise ValueError("Reconstructed IBM ansatz identity mismatch.")

    service = QiskitRuntimeService(
        channel="ibm_quantum_platform", token=token, instance=instance
    )
    backend = service.backend(backend_name)
    if int(backend.num_qubits) < bundle.required_qubits:
        raise ValueError("Configured IBM backend does not have enough qubits.")
    attempt_path = job_record_path.with_name("submission-attempt.json")
    status_path = job_record_path.with_name("status.json")
    evidence_path = job_record_path.with_name("prepared-submission.json")
    if attempt_path.exists() or job_record_path.exists() or evidence_path.exists():
        try:
            isa_circuit, isa_observable, evidence = _load_prepared_submission(
                job_record_path.parent, bundle=bundle
            )
        except Exception as exc:
            raise _ControlledWorkerFailure("prepared_evidence_failure") from exc
    else:
        pass_manager = generate_preset_pass_manager(
            backend=backend,
            optimization_level=bundle.optimization_level,
            seed_transpiler=bundle.seed_transpiler,
        )
        isa_circuit = pass_manager.run(bound)
        isa_observable = prepared.qubit_operator.apply_layout(isa_circuit.layout)
        isa_circuit, isa_observable, evidence = _persist_prepared_submission(
            job_record_path.parent,
            bundle=bundle,
            backend=backend,
            isa_circuit=isa_circuit,
            isa_observable=isa_observable,
            source_bound_circuit_sha256=source_bound_circuit_sha,
            source_observable_sha256=source_observable_sha,
            mapper=manifest.experiment.quantum_model.mapper,
        )
    job, job_identifier, submitted_at, runtime_options = _obtain_job(
        service=service,
        backend=backend,
        estimator_type=EstimatorV2,
        bundle=bundle,
        isa_circuit=isa_circuit,
        isa_observable=isa_observable,
        attempt_path=attempt_path,
        job_record_path=job_record_path,
    )
    while True:
        try:
            status = _normalized_job_status(job)
        except Exception as exc:
            raise _ControlledWorkerFailure("transient_status_failure") from exc
        _write_runtime_status(
            status_path,
            bundle,
            job_identifier=job_identifier,
            runtime_status=status,
        )
        if status in {"QUEUED", "INITIALIZING", "RUNNING"}:
            time.sleep(_STATUS_POLL_INTERVAL_SECONDS)
            continue
        if status == "COMPLETED":
            break
        if status in {"CANCELLED", "ERROR", "FAILED"}:
            raise _ControlledWorkerFailure("terminal_job_failure", status=status)
        raise _ControlledWorkerFailure("transient_status_failure", status="UNKNOWN")

    try:
        primitive_result = job.result()
    except Exception as exc:
        raise _ControlledWorkerFailure(
            "transient_result_failure", status="COMPLETED"
        ) from exc
    publication = primitive_result[0]
    raw_expectation = float(publication.data.evs)
    standard_error_value = getattr(publication.data, "stds", None)
    standard_error = (
        float(standard_error_value) if standard_error_value is not None else None
    )
    constants = getattr(problem.hamiltonian, "constants", None)
    if constants is None or not hasattr(constants, "items"):
        raise ValueError("Active-problem Hamiltonian constants are unavailable.")
    nuclear, electronic_offsets = partition_hamiltonian_constants(constants.items())
    non_nuclear_shift = math.fsum(electronic_offsets.values())
    electronic_energy = raw_expectation + non_nuclear_shift
    completed_at = _utc_now()
    return IBMRuntimeResult(
        bundle_sha256=bundle.bundle_sha256,
        job_identifier=job_identifier,
        backend_name=str(backend.name),
        primitive_version=importlib.metadata.version("qiskit-ibm-runtime"),
        submitted_at=submitted_at,
        completed_at=completed_at,
        job_status="completed",
        target_precision=bundle.target_precision,
        raw_qubit_expectation_hartree=raw_expectation,
        non_nuclear_electronic_shift_hartree=non_nuclear_shift,
        electronic_constant_offsets_hartree=electronic_offsets,
        nuclear_repulsion_energy_hartree=nuclear,
        ibm_electronic_energy_hartree=electronic_energy,
        standard_error=standard_error,
        execution_metadata={
            "runtime": _json_safe(getattr(publication, "metadata", {})),
        },
        optimization_level=bundle.optimization_level,
        layout_sha256=evidence.layout_sha256,
        physical_qubits=evidence.physical_qubits,
        source_bound_circuit_sha256=evidence.source_bound_circuit_sha256,
        transpiled_circuit_sha256=evidence.transpiled_circuit_sha256,
        source_observable_sha256=evidence.source_observable_sha256,
        transpiled_observable_sha256=evidence.transpiled_observable_sha256,
        optimized_parameters_sha256=bundle.optimized_parameters_sha256,
        experiment_sha256=bundle.experiment_sha256,
        structure_sha256=bundle.structure_sha256,
        hamiltonian_sha256=bundle.hamiltonian_sha256,
        package_versions={
            name: importlib.metadata.version(name)
            for name in ("qiskit", "qiskit-nature", "qiskit-ibm-runtime")
        },
        runtime_options=runtime_options,
        execution_image_identifier=image_identifier,
    )


def _write_failure_envelope(
    directory: Path,
    *,
    error: BaseException,
) -> None:
    job_identifier: str | None = None
    last_status: str | None = None
    job_path = directory / "job.json"
    attempt_path = directory / "submission-attempt.json"
    status_path = directory / "status.json"
    if job_path.is_file() and not job_path.is_symlink():
        record = _read_object(job_path)
        candidate = record.get("job_identifier")
        if isinstance(candidate, str) and re.fullmatch(r"[A-Za-z0-9._:-]{3,256}", candidate):
            job_identifier = candidate
    if status_path.is_file() and not status_path.is_symlink():
        status = _read_object(status_path)
        candidate_status = str(status.get("runtime_status", "")).upper()
        if candidate_status in {
            "QUEUED", "INITIALIZING", "RUNNING", "COMPLETED",
            "CANCELLED", "ERROR", "FAILED",
        }:
            last_status = candidate_status
    if isinstance(error, _ControlledWorkerFailure):
        category = error.category
        last_status = error.status or last_status
    elif job_identifier is not None:
        category = "transient_service_failure"
    elif attempt_path.is_file() and not attempt_path.is_symlink():
        category = "submission_indeterminate"
    else:
        category = "pre_submission_failure"
    recoverable = (
        job_identifier is not None
        and category
        in {
            "transient_service_failure",
            "transient_status_failure",
            "transient_result_failure",
        }
        and last_status not in {"CANCELLED", "ERROR", "FAILED"}
    )
    envelope = IBMWorkerFailureEnvelope(
        category=category,
        job_identifier_persisted=job_identifier is not None,
        job_identifier=job_identifier,
        last_controlled_ibm_status=last_status,
        retrieval_recoverable=recoverable,
    )
    write_json_atomic(
        directory / "failure.json",
        envelope.model_dump(mode="json"),
        maximum_bytes=_MAXIMUM_BYTES,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--submission", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--job-record", required=True)
    parser.add_argument("--result-envelope", required=True)
    arguments = parser.parse_args()
    controlled_directory: Path | None = None
    try:
        submission_path, manifest_path, job_record_path, result_path = _validated_paths(arguments)
        controlled_directory = result_path.parent
        bundle = IBMSubmissionBundle.model_validate(_read_object(submission_path))
        manifest = ManifestEnvelope.model_validate(_read_object(manifest_path))
        result = execute(bundle, manifest, job_record_path=job_record_path)
        write_json_atomic(
            result_path,
            result.model_dump(mode="json"),
            maximum_bytes=_MAXIMUM_BYTES,
        )
        return 0
    except Exception as exc:
        # Provider exception strings and credentials never cross this process boundary.
        if controlled_directory is not None:
            try:
                _write_failure_envelope(controlled_directory, error=exc)
            except Exception:
                # A malformed controlled directory must remain failed closed.
                pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
