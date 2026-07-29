"""Durable single-process coordinator for trusted Pulsate preset runs."""

from __future__ import annotations

import json
import math
import os
import re
import signal
import stat
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from cgr.pulsate_api.quantum_worker import (
    WORKER_EXIT_CODES,
    WORKER_MANIFEST_MAXIMUM_BYTES,
    WORKER_RESULT_MAXIMUM_BYTES,
    WorkerResultEnvelope,
)
from cgr.quantum_preflight.artifacts import artifact_reference, write_json_atomic
from cgr.quantum_preflight.contracts import ManifestEnvelope
from cgr.quantum_preflight.errors import QuantumTimeoutError
from cgr.quantum_preflight.identities import ScientificResultArtifact
from cgr.quantum_preflight.operators import encode_float
from cgr.quantum_preflight.receipt import (
    QuantumPreflightReceipt,
    verify_receipt_identities,
)
from cgr.quantum_preflight.verification import blocking_findings
from cgr.quantum_preflight.warnings import CompatibilityWarningEvidence
from cgr.science import sha256_fingerprint

RunStatus = Literal[
    "queued", "validating", "running_quantum_workflow",
    "running_local_preflight", "awaiting_ibm_submission", "queued_on_ibm",
    "running_on_ibm", "verifying_ibm_result",
    "authorized", "rejected", "failed", "interrupted",
]
RunSourceType = Literal["preset", "dynamic_experiment", "approved_experiment"]
TERMINAL_STATUSES = frozenset({"authorized", "rejected", "failed", "interrupted"})
ACTIVE_STATUSES = frozenset({
    "queued", "validating", "running_quantum_workflow", "running_local_preflight",
    "awaiting_ibm_submission", "queued_on_ibm", "running_on_ibm",
    "verifying_ibm_result",
})
_RUN_IDENTIFIER = re.compile(r"^run-[0-9a-f]{32}$")
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_QUOTED_ABSOLUTE_PATH = re.compile(r"(['\"])(?:[A-Za-z]:[\\/]|/)[^'\"\r\n]+\1")
_WINDOWS_ABSOLUTE_PATH = re.compile(r"(?i)[A-Z]:[\\/][^\r\n,;]+")
_POSIX_ABSOLUTE_PATH = re.compile(r"(?<![A-Za-z0-9:])/(?:[^\s/]+/)+[^\r\n,;]+")
_DEFAULT_MAX_RUN_SECONDS = 180
_MAX_CONFIGURED_RUN_SECONDS = 3600
_DEFAULT_MAX_ARTIFACT_BYTES = 10 * 1024 * 1024
_MAX_RUN_ENVELOPE_BYTES = 2 * 1024 * 1024
_MAX_COMPILED_MANIFEST_BYTES = 2 * 1024 * 1024
_WORKER_SHUTDOWN_GRACE_SECONDS = 5
_WORKER_TERMINATION_GRACE_SECONDS = 1.0
_WORKER_COLLECTION_TIMEOUT_SECONDS = 2.0
_WORKER_LOG_JOIN_TIMEOUT_SECONDS = 2.0
WORKER_LOG_MAXIMUM_BYTES = 1 * 1024 * 1024
_WORKER_LOG_TRUNCATION_MARKER = b"\n...[worker output truncated]...\n"

_AUTHORITATIVE_ARTIFACTS = {
    "experiment": ("quantum_chemistry_experiment", "experiment.json"),
    "molecular_structure": ("molecular_structure", "molecular-structure.json"),
    "environment": ("environment_manifest", "environment.json"),
    "qcschema": ("qcschema", "qcschema.json"),
    "electronic_problem": ("electronic_structure_problem_summary", "electronic-problem.json"),
    "active_space": ("active_space", "active-space.json"),
    "fermionic_hamiltonian": ("fermionic_hamiltonian", "fermionic-hamiltonian.json"),
    "qubit_hamiltonian": ("qubit_hamiltonian", "qubit-hamiltonian.json"),
    "exact_result": ("exact_ground_state_result", "exact-result.json"),
    "vqe_result": ("vqe_ground_state_result", "vqe-result.json"),
    "optimization_trace": ("optimization_trace", "optimization-trace.json"),
    "ansatz_manifest": ("circuit_ansatz_manifest", "ansatz-manifest.json"),
    "compatibility_warnings": ("compatibility_warnings", "compatibility-warnings.json"),
    "verification_report": ("verification_report", "verification-report.json"),
    "lineage": ("artifact_lineage", "lineage.json"),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    temporary.write_text(data + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _contains_absolute_path(value: str) -> bool:
    stripped = value.strip().strip("'\"")
    # Public API route references are deliberately relative to the origin. They
    # are not filesystem disclosures even though POSIX path helpers regard a
    # leading slash as absolute.
    if re.fullmatch(r"/api(?:/[A-Za-z0-9._~-]+)+/?", stripped):
        return False
    candidates = [stripped]
    candidates.extend(re.split(r"[\s,;]+", value))
    if _QUOTED_ABSOLUTE_PATH.search(value) or _WINDOWS_ABSOLUTE_PATH.search(value) or _POSIX_ABSOLUTE_PATH.search(value):
        return True
    for candidate in candidates:
        cleaned = candidate.strip("'\"()[]{}")
        if not cleaned:
            continue
        if PureWindowsPath(cleaned).is_absolute() or PurePosixPath(cleaned).is_absolute():
            return True
    return False


def assert_public_response_safe(value: Any) -> None:
    """Recursively reject server path material from every public response."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"receipt_path", "result_root", "storage_location", "filename", "path"}:
                raise ValueError(f"Public response contains forbidden field {key}.")
            if re.search(r"(?:token|credential|password|api[_-]?key)", str(key), re.IGNORECASE):
                raise ValueError("Public response contains a forbidden credential field.")
            assert_public_response_safe(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            assert_public_response_safe(item)
    elif isinstance(value, str):
        if _contains_absolute_path(value):
            raise ValueError("Public response contains an absolute filesystem path.")
        for variable in ("PULSATE_IBM_QUANTUM_TOKEN", "PULSATE_IBM_QUANTUM_INSTANCE"):
            secret = os.environ.get(variable)
            if secret and secret in value:
                raise ValueError("Public response contains configured credential material.")


def _public_error_message(exc: Exception) -> str:
    message = str(exc).strip() or type(exc).__name__
    for variable in ("PULSATE_IBM_QUANTUM_TOKEN", "PULSATE_IBM_QUANTUM_INSTANCE"):
        secret = os.environ.get(variable)
        if secret:
            message = message.replace(secret, "[redacted]")
    if _contains_absolute_path(message):
        return "The trusted local execution failed while processing server-controlled evidence."
    return message[:1000]


def _controlled_file(directory: Path, filename: str, *, maximum_bytes: int) -> Path:
    if Path(filename).name != filename:
        raise ValueError("Authoritative artifact filename is invalid.")
    resolved_directory = directory.resolve(strict=True)
    path = directory / filename
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise ValueError(f"Required authoritative artifact {filename} is missing.") from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise ValueError(f"Authoritative artifact {filename} must not be a symbolic link.")
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"Authoritative artifact {filename} is not a regular file.")
    resolved = path.resolve(strict=True)
    if resolved.parent != resolved_directory:
        raise ValueError(f"Authoritative artifact {filename} escaped its controlled directory.")
    if metadata.st_size > maximum_bytes:
        raise ValueError(f"Authoritative artifact {filename} exceeds the JSON size limit.")
    return resolved


def _controlled_json(directory: Path, filename: str, *, maximum_bytes: int) -> dict[str, Any]:
    path = _controlled_file(directory, filename, maximum_bytes=maximum_bytes)
    data = path.read_bytes()
    if len(data) > maximum_bytes:
        raise ValueError(f"Authoritative artifact {filename} exceeds the JSON size limit.")
    value = json.loads(data.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Authoritative artifact {filename} is not a JSON object.")
    return value


def _artifact_payload(
    directory: Path,
    filename: str,
    *,
    maximum_bytes: int,
    expected_artifact_type: str | None = None,
) -> dict[str, Any]:
    document = _controlled_json(directory, filename, maximum_bytes=maximum_bytes)
    if document.get("artifact_schema") != "cgr.quantum-preflight-artifact/1.0.0":
        raise ValueError(f"Authoritative artifact {filename} has an unsupported document schema.")
    if expected_artifact_type is not None and document.get("artifact_type") != expected_artifact_type:
        raise ValueError(f"Authoritative artifact {filename} has an unexpected artifact type.")
    payload = document.get("payload")
    if not isinstance(payload, dict):
        raise ValueError(f"Authoritative artifact {filename} has no object payload.")
    return payload


@dataclass(frozen=True)
class ValidatedReceiptArtifact:
    pointer: Any
    artifact_type: str
    filename: str
    payload: Any


def _validated_receipt_artifact(
    receipt: QuantumPreflightReceipt,
    directory: Path,
    artifact_identifier: str,
    *,
    maximum_bytes: int,
) -> ValidatedReceiptArtifact:
    """Load one receipt-linked document and recompute its canonical content identity."""
    expected = _AUTHORITATIVE_ARTIFACTS.get(artifact_identifier)
    if expected is None:
        raise ValueError("Receipt contains an unknown authoritative artifact identifier.")
    artifact_type, filename = expected
    matches = [
        pointer for pointer in receipt.artifacts
        if pointer.artifact_identifier == artifact_identifier
    ]
    if len(matches) != 1:
        raise ValueError(
            f"Receipt must contain exactly one {artifact_identifier} artifact pointer."
        )
    document = _controlled_json(directory, filename, maximum_bytes=maximum_bytes)
    if document.get("artifact_schema") != "cgr.quantum-preflight-artifact/1.0.0":
        raise ValueError(f"Authoritative artifact {filename} has an unsupported document schema.")
    document_type = document.get("artifact_type")
    if document_type != artifact_type:
        raise ValueError(
            f"Authoritative artifact {artifact_identifier} has an unexpected artifact type."
        )
    if "payload" not in document:
        raise ValueError(f"Authoritative artifact {filename} has no payload.")
    payload = document["payload"]
    recomputed = artifact_reference(
        artifact_identifier,
        document_type,
        payload,
        filename=filename,
    )
    pointer = matches[0]
    if recomputed.content_sha256 != pointer.content_sha256:
        raise ValueError(
            f"Authoritative artifact {artifact_identifier} content does not match its receipt pointer."
        )
    return ValidatedReceiptArtifact(pointer, document_type, filename, payload)


def _controlled_run_json(directory: Path, filename: str) -> dict[str, Any]:
    """Read one bounded, direct run-envelope JSON object without following symlinks."""
    return _controlled_json(
        directory, filename, maximum_bytes=_MAX_RUN_ENVELOPE_BYTES
    )


class PublicArtifactIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifact_identifier: str
    artifact_type: str
    content_sha256: str


class ApprovedExecutionProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    interpretation_identifier: str
    approved_experiment_identifier: str
    approved_scientific_objective: str
    approved_requested_quantity: str
    approved_specification_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    compiled_execution_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    compiled_objective: Literal["molecular_ground_state_energy"]
    molecule_structure_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    mapper: str
    ansatz: str
    optimizer: str
    optimizer_tolerance: float = Field(gt=0)
    execution_target: Literal["ibm_quantum"]
    requested_precision: float = Field(gt=0, le=1)
    requested_backend: str | None = None


class PublicRunSourceIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: RunSourceType = "preset"
    source_identifier: str | None = None
    preset_identifier: str | None
    approved_experiment: ApprovedExecutionProvenance | None = None

    @model_validator(mode="after")
    def validate_source_identity(self) -> PublicRunSourceIdentity:
        experiment_identifier = getattr(self, "experiment_identifier", None)
        if self.source_type == "preset":
            if not self.preset_identifier:
                raise ValueError("Preset source identity requires a preset identifier.")
            if self.source_identifier is None:
                self.source_identifier = self.preset_identifier
            if self.source_identifier != self.preset_identifier:
                raise ValueError("Preset source identity is mismatched.")
            if self.approved_experiment is not None:
                raise ValueError("Preset source must not publish approved-experiment provenance.")
        elif self.source_type == "dynamic_experiment":
            if self.preset_identifier is not None:
                raise ValueError("Dynamic experiment source must not publish a preset identifier.")
            if not self.source_identifier or self.source_identifier != experiment_identifier:
                raise ValueError("Dynamic experiment source identity is mismatched.")
            if self.approved_experiment is not None:
                raise ValueError(
                    "Dynamic experiment source must not publish approval provenance."
                )
        else:
            if self.preset_identifier is not None:
                raise ValueError("Approved experiment source must not publish a preset identifier.")
            if not self.source_identifier or self.source_identifier != experiment_identifier:
                raise ValueError("Approved experiment source identity is mismatched.")
            if (
                self.approved_experiment is None
                or self.approved_experiment.approved_experiment_identifier
                != self.source_identifier
            ):
                raise ValueError("Approved experiment provenance is mismatched.")
        return self


class PublicReceipt(PublicRunSourceIdentity):
    schema_version: str
    run_identifier: str
    execution_identifier: str
    experiment_identifier: str
    experiment_fingerprint: str
    expected_experiment_sha256: str
    structure_identifier: str
    structure_sha256: str
    hamiltonian_sha256: str
    exact_scientific_result_sha256: str
    vqe_scientific_result_sha256: str
    scientific_outcome_sha256: str
    execution_environment_identity: str
    receipt_sha256: str
    verification_passed: bool
    authorization_state: Literal["authorized", "rejected"]
    authorized: bool
    artifacts: list[PublicArtifactIdentity]
    ibm_execution: dict[str, Any] | None = None


class PublicResults(PublicRunSourceIdentity):
    run_identifier: str
    experiment_identifier: str
    experiment_fingerprint: str
    expected_experiment_sha256: str
    structure_identifier: str
    structure_sha256: str
    hamiltonian_sha256: str
    exact_scientific_result_sha256: str
    vqe_scientific_result_sha256: str
    scientific_outcome_sha256: str
    exact_total_energy_hartree: float
    vqe_total_energy_hartree: float
    absolute_difference_hartree: float = Field(ge=0)
    tolerance_hartree: float = Field(gt=0)
    energy_unit: Literal["hartree"]
    exact_solver_metadata: dict[str, Any]
    vqe_solver_metadata: dict[str, Any]
    optimizer_evaluations: int | None
    converged: bool | None
    compatibility_warnings: list[Any]
    execution_environment_identity: str
    receipt_sha256: str
    ibm_execution: dict[str, Any] | None = None


class PublicVerification(PublicRunSourceIdentity):
    run_identifier: str
    experiment_identifier: str
    experiment_fingerprint: str
    expected_experiment_sha256: str
    structure_identifier: str
    structure_sha256: str
    verification_completed: bool
    verification_passed: bool
    authorization_state: Literal["authorized", "rejected"]
    blocking_findings: list[Any]
    nonblocking_findings: list[Any]
    tolerance_check: dict[str, Any] | None
    scientific_identity_checks: list[Any]
    artifact_integrity_checks: list[Any]
    checks: list[Any]
    compatibility_warnings: list[Any]
    ibm_execution: dict[str, Any] | None = None


@dataclass(frozen=True)
class ExecutionOutput:
    results: dict[str, Any]
    verification: dict[str, Any]
    receipt: dict[str, Any]
    runner_summary: dict[str, Any]


def _bind_public_source_identity(
    output: ExecutionOutput,
    *,
    source_type: RunSourceType,
    source_identifier: str,
    preset_identifier: str | None,
    approved_experiment: dict[str, Any] | None = None,
) -> ExecutionOutput:
    """Bind server-owned source identity without changing scientific evidence."""
    projections: list[dict[str, Any]] = []
    for projection in (output.results, output.verification, output.receipt):
        bound = dict(projection)
        bound.update(
            {
                "source_type": source_type,
                "source_identifier": source_identifier,
                "preset_identifier": preset_identifier,
            }
        )
        if approved_experiment is not None:
            bound["approved_experiment"] = approved_experiment
        projections.append(bound)
    summary = dict(output.runner_summary)
    summary.update(
        {
            "source_type": source_type,
            "source_identifier": source_identifier,
            "preset_identifier": preset_identifier,
        }
    )
    if approved_experiment is not None:
        summary["approved_experiment"] = approved_experiment
    return ExecutionOutput(*projections, summary)


class PresetRunExecutor(Protocol):
    def execute(
        self,
        manifest: ManifestEnvelope,
        *,
        preset_identifier: str,
        run_directory: Path,
        maximum_seconds: int,
    ) -> ExecutionOutput: ...


class IBMRunExecutor(Protocol):
    def capability(self) -> dict[str, Any]: ...

    def execute(
        self,
        manifest: ManifestEnvelope,
        *,
        preset_identifier: str,
        run_directory: Path,
        maximum_seconds: int,
        status_callback: Callable[[str, dict[str, Any] | None], None],
        source_type: RunSourceType,
        source_identifier: str,
        source_preset_identifier: str | None,
        expected_structure_sha256: str | None,
        run_identifier: str,
        requested_precision: float | None,
        requested_backend: str | None,
        approved_experiment: dict[str, Any] | None,
    ) -> ExecutionOutput: ...


class _BoundedLogCollector:
    """Continuously drain one worker pipe while retaining a bounded prefix."""

    def __init__(
        self,
        stream: Any,
        path: Path,
        *,
        thread_name: str,
        redactions: tuple[bytes, ...] = (),
    ) -> None:
        self.stream = stream
        self.path = path
        self.buffer = bytearray()
        self.truncated = False
        self.error: Exception | None = None
        self.persisted = False
        self.started = False
        self.redactions = tuple(value for value in redactions if value)
        self.thread = threading.Thread(
            target=self._drain, name=thread_name, daemon=True
        )

    def start(self) -> None:
        self.thread.start()
        self.started = True

    def _drain(self) -> None:
        try:
            while True:
                chunk = self.stream.read(64 * 1024)
                if not chunk:
                    break
                remaining = WORKER_LOG_MAXIMUM_BYTES - len(self.buffer)
                if remaining > 0:
                    self.buffer.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    self.truncated = True
        except Exception as exc:
            self.error = exc
        finally:
            try:
                self.stream.close()
            except Exception as exc:
                if self.error is None:
                    self.error = exc

    def finish(self) -> None:
        if self.persisted:
            if self.error is not None:
                raise RuntimeError("A quantum worker log reader failed.") from self.error
            return
        if self.started:
            self.thread.join(timeout=_WORKER_LOG_JOIN_TIMEOUT_SECONDS)
        if self.started and self.thread.is_alive():
            close_error: Exception | None = None
            try:
                self.stream.close()
            except Exception as exc:
                close_error = exc
            self.thread.join(timeout=_WORKER_LOG_JOIN_TIMEOUT_SECONDS)
            if close_error is not None:
                raise RuntimeError("A quantum worker log pipe could not be closed.") from close_error
        if self.started and self.thread.is_alive():
            raise RuntimeError("A quantum worker log reader could not be collected.")
        if not self.started:
            try:
                self.stream.close()
            except Exception as exc:
                raise RuntimeError("A quantum worker log pipe could not be closed.") from exc

        payload = bytes(self.buffer)
        for secret in self.redactions:
            payload = payload.replace(secret, b"[redacted]")
        if self.truncated:
            retained = WORKER_LOG_MAXIMUM_BYTES - len(
                _WORKER_LOG_TRUNCATION_MARKER
            )
            payload = payload[:retained] + _WORKER_LOG_TRUNCATION_MARKER
        if len(payload) > WORKER_LOG_MAXIMUM_BYTES:
            raise RuntimeError("A quantum worker log exceeded its size ceiling.")
        with self.path.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        self.persisted = True
        if self.error is not None:
            raise RuntimeError("A quantum worker log reader failed.") from self.error


class ExistingQuantumPreflightExecutor:
    """Fail-closed process adapter over the trusted quantum-preflight runner."""

    def __init__(
        self,
        *,
        repository_root: Path,
        image_identifier: str,
        _process_factory: Callable[..., Any] | None = None,
        _worker_timeout_override_seconds: float | None = None,
    ) -> None:
        self.repository_root = repository_root.resolve()
        self.image_identifier = image_identifier
        self._process_factory = _process_factory or subprocess.Popen
        self._worker_timeout_override_seconds = _worker_timeout_override_seconds

    def execute(
        self,
        manifest: ManifestEnvelope,
        *,
        preset_identifier: str,
        run_directory: Path,
        maximum_seconds: int,
    ) -> ExecutionOutput:
        return self._execute(
            manifest,
            preset_identifier=preset_identifier,
            run_directory=run_directory,
            maximum_seconds=maximum_seconds,
            capture_ibm_preflight=False,
        )

    def execute_ibm_preflight(
        self,
        manifest: ManifestEnvelope,
        *,
        preset_identifier: str,
        run_directory: Path,
        maximum_seconds: int,
    ) -> ExecutionOutput:
        return self._execute(
            manifest,
            preset_identifier=preset_identifier,
            run_directory=run_directory,
            maximum_seconds=maximum_seconds,
            capture_ibm_preflight=True,
        )

    def _execute(
        self,
        manifest: ManifestEnvelope,
        *,
        preset_identifier: str,
        run_directory: Path,
        maximum_seconds: int,
        capture_ibm_preflight: bool,
    ) -> ExecutionOutput:
        result_root = run_directory / "runner-artifacts"
        if result_root.is_symlink() or result_root.exists():
            raise ValueError("The quantum runner artifact root must not pre-exist.")
        maximum_bytes = manifest.experiment.execution_policy.maximum_result_bytes
        worker_directory = self._create_worker_directory(run_directory)
        manifest_path = worker_directory / "manifest.json"
        result_envelope_path = worker_directory / "result.json"
        ibm_preflight_evidence_path = (
            worker_directory / "ibm-preflight-evidence.json"
            if capture_ibm_preflight
            else None
        )
        stdout_path = worker_directory / "stdout.log"
        stderr_path = worker_directory / "stderr.log"
        lock_path = self.repository_root / "requirements" / "quantum-preflight.lock"
        if lock_path.is_symlink() or not lock_path.is_file():
            raise ValueError("The scientific dependency lock is unavailable.")
        write_json_atomic(
            manifest_path,
            manifest.model_dump(mode="json"),
            maximum_bytes=WORKER_MANIFEST_MAXIMUM_BYTES,
        )
        command = [
            sys.executable,
            "-m",
            "cgr.pulsate_api.quantum_worker",
            "--manifest-json",
            str(manifest_path),
            "--result-root",
            str(result_root),
            "--scientific-lock",
            str(lock_path),
            "--image-identifier",
            self.image_identifier,
            "--maximum-seconds",
            str(maximum_seconds),
            "--result-envelope",
            str(result_envelope_path),
        ]
        if ibm_preflight_evidence_path is not None:
            command.extend(
                [
                    "--ibm-preflight-evidence",
                    str(ibm_preflight_evidence_path),
                ]
            )
        process_options: dict[str, Any] = {
            "cwd": self.repository_root,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "shell": False,
            "env": {
                key: value
                for key, value in os.environ.items()
                if key
                not in {
                    "PULSATE_IBM_QUANTUM_TOKEN",
                    "PULSATE_IBM_QUANTUM_INSTANCE",
                }
            },
        }
        if os.name == "posix":
            process_options["start_new_session"] = True
        process = self._process_factory(command, **process_options)
        if process.stdout is None or process.stderr is None:
            self._terminate_worker(process)
            raise RuntimeError("The quantum worker output pipes were not created.")
        collectors = (
            _BoundedLogCollector(
                process.stdout,
                stdout_path,
                thread_name=f"pulsate-worker-log-{run_directory.name}-stdout",
            ),
            _BoundedLogCollector(
                process.stderr,
                stderr_path,
                thread_name=f"pulsate-worker-log-{run_directory.name}-stderr",
            ),
        )
        try:
            for collector in collectors:
                collector.start()
        except BaseException:
            cleanup_errors: list[Exception] = []
            try:
                self._terminate_worker(process)
            except Exception as cleanup_exc:
                cleanup_errors.append(cleanup_exc)
            try:
                self._finish_log_collectors(collectors)
            except Exception as cleanup_exc:
                cleanup_errors.append(cleanup_exc)
            if cleanup_errors:
                raise RuntimeError(
                    "Quantum worker startup cleanup could not be confirmed."
                ) from cleanup_errors[0]
            raise

        timeout = (
            self._worker_timeout_override_seconds
            if self._worker_timeout_override_seconds is not None
            else maximum_seconds + _WORKER_SHUTDOWN_GRACE_SECONDS
        )
        try:
            return_code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            cleanup_errors: list[Exception] = []
            try:
                self._terminate_worker(process)
            except Exception as cleanup_exc:
                cleanup_errors.append(cleanup_exc)
            try:
                self._finish_log_collectors(collectors)
            except Exception as cleanup_exc:
                cleanup_errors.append(cleanup_exc)
            if cleanup_errors:
                raise QuantumTimeoutError(
                    "The trusted quantum worker timed out and cleanup could not be confirmed."
                ) from cleanup_errors[0]
            raise QuantumTimeoutError(
                "The trusted quantum worker exceeded its outer process timeout."
            ) from exc
        except BaseException:
            cleanup_errors = []
            try:
                self._terminate_worker(process)
            except Exception as cleanup_exc:
                cleanup_errors.append(cleanup_exc)
            try:
                self._finish_log_collectors(collectors)
            except Exception as cleanup_exc:
                cleanup_errors.append(cleanup_exc)
            if cleanup_errors:
                raise RuntimeError(
                    "Quantum worker cleanup could not be confirmed."
                ) from cleanup_errors[0]
            raise
        try:
            self._finish_log_collectors(collectors)
        except Exception as exc:
            try:
                self._terminate_worker(process)
                self._finish_log_collectors(collectors)
            except Exception as cleanup_exc:
                raise RuntimeError(
                    "Quantum worker output cleanup could not be confirmed."
                ) from cleanup_exc
            raise RuntimeError("Quantum worker output collection failed.") from exc

        envelope = WorkerResultEnvelope.model_validate(
            _controlled_json(
                worker_directory,
                result_envelope_path.name,
                maximum_bytes=WORKER_RESULT_MAXIMUM_BYTES,
            )
        )
        expected_exit_code = WORKER_EXIT_CODES[envelope.outcome]
        if return_code != expected_exit_code:
            raise ValueError("Quantum worker exit status disagrees with its result envelope.")

        if envelope.outcome == "completed":
            assert envelope.summary is not None
            summary = envelope.summary
            receipt_path = summary.get("receipt_path")
            if not isinstance(receipt_path, str):
                raise ValueError("Quantum worker summary has no receipt path.")
            artifact_directory = self._validated_artifact_directory(
                Path(receipt_path).parent, result_root
            )
        elif envelope.outcome == "verification_failed":
            self._validated_result_root(result_root)
            candidates = sorted(
                result_root.glob("*/*-failed"), key=lambda path: path.lstat().st_mtime
            )
            if not candidates:
                raise ValueError("Quantum verification failed without controlled evidence.")
            artifact_directory = self._validated_artifact_directory(candidates[-1], result_root)
            summary = _controlled_json(artifact_directory, "summary.json", maximum_bytes=maximum_bytes)
        elif envelope.outcome == "timed_out":
            raise QuantumTimeoutError("The trusted quantum workflow exceeded its scientific timeout.")
        else:
            assert envelope.error is not None
            raise RuntimeError(
                f"Quantum worker failed ({envelope.error.error_type}): {envelope.error.message}"
            )
        return self._project(
            manifest, preset_identifier, run_directory.name, artifact_directory, summary,
            maximum_bytes=maximum_bytes,
            ibm_preflight_evidence_path=ibm_preflight_evidence_path,
        )

    @staticmethod
    def _finish_log_collectors(
        collectors: tuple[_BoundedLogCollector, ...],
    ) -> None:
        errors: list[Exception] = []
        for collector in collectors:
            try:
                collector.finish()
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise RuntimeError("Quantum worker log collection failed.") from errors[0]

    @staticmethod
    def _process_group_has_live_members(process_group: int) -> bool:
        proc_root = Path("/proc")
        if proc_root.is_dir():
            for status_path in proc_root.glob("[0-9]*/stat"):
                try:
                    remainder = status_path.read_text(encoding="utf-8").rsplit(")", 1)[1]
                    fields = remainder.split()
                    state = fields[0]
                    member_group = int(fields[2])
                except (FileNotFoundError, IndexError, ValueError):
                    continue
                if member_group == process_group and state != "Z":
                    return True
            return False
        try:
            os.killpg(process_group, 0)
        except ProcessLookupError:
            return False
        return True

    @classmethod
    def _terminate_worker(cls, process: Any) -> None:
        if os.name == "posix":
            process_group = process.pid
            try:
                os.killpg(process_group, signal.SIGTERM)
            except ProcessLookupError:
                pass
            direct_collected = False
            try:
                process.wait(timeout=_WORKER_TERMINATION_GRACE_SECONDS)
                direct_collected = True
            except subprocess.TimeoutExpired:
                pass
            if cls._process_group_has_live_members(process_group):
                try:
                    os.killpg(process_group, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if not direct_collected:
                try:
                    process.wait(timeout=_WORKER_COLLECTION_TIMEOUT_SECONDS)
                except subprocess.TimeoutExpired as exc:
                    raise RuntimeError(
                        "The quantum worker direct process could not be collected."
                    ) from exc
            deadline = time.monotonic() + _WORKER_COLLECTION_TIMEOUT_SECONDS
            while cls._process_group_has_live_members(process_group):
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        "The quantum worker process group could not be fully terminated."
                    )
                time.sleep(0.01)
            return

        try:
            process.kill()
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=_WORKER_COLLECTION_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                "The quantum worker direct process could not be collected."
            ) from exc

    @staticmethod
    def _create_worker_directory(run_directory: Path) -> Path:
        if run_directory.is_symlink() or not run_directory.is_dir():
            raise ValueError("The server-created run directory is invalid.")
        resolved_run = run_directory.resolve(strict=True)
        worker_directory = run_directory / "quantum-worker"
        worker_directory.mkdir(mode=0o700)
        metadata = worker_directory.lstat()
        resolved_worker = worker_directory.resolve(strict=True)
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or resolved_worker.parent != resolved_run
        ):
            raise ValueError("The quantum worker control directory escaped its run directory.")
        return resolved_worker

    @staticmethod
    def _validated_artifact_directory(directory: Path, result_root: Path) -> Path:
        resolved_root = ExistingQuantumPreflightExecutor._validated_result_root(
            result_root
        )
        if directory.is_symlink():
            raise ValueError("Runner artifact directory must not be a symbolic link.")
        resolved = directory.resolve(strict=True)
        if not resolved.is_dir() or resolved == resolved_root or resolved_root not in resolved.parents:
            raise ValueError("Runner artifact directory escaped the controlled run root.")
        return resolved

    @staticmethod
    def _validated_result_root(result_root: Path) -> Path:
        try:
            metadata = result_root.lstat()
        except FileNotFoundError as exc:
            raise ValueError("Runner artifact root is missing.") from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise ValueError("Runner artifact root must be a regular directory.")
        return result_root.resolve(strict=True)

    @staticmethod
    def _project(
        manifest: ManifestEnvelope,
        preset_identifier: str,
        run_identifier: str,
        directory: Path,
        summary: dict[str, Any],
        *,
        maximum_bytes: int = _DEFAULT_MAX_ARTIFACT_BYTES,
        ibm_preflight_evidence_path: Path | None = None,
    ) -> ExecutionOutput:
        if manifest.experiment.fingerprint != manifest.expected_experiment_sha256:
            raise ValueError("Manifest experiment fingerprint does not match its expected identity.")

        receipt_payload = _artifact_payload(
            directory, "receipt.json", maximum_bytes=maximum_bytes,
            expected_artifact_type="quantum_preflight_receipt",
        )
        receipt = QuantumPreflightReceipt.model_validate(receipt_payload)
        exact_evidence = _validated_receipt_artifact(
            receipt, directory, "exact_result", maximum_bytes=maximum_bytes
        )
        vqe_evidence = _validated_receipt_artifact(
            receipt, directory, "vqe_result", maximum_bytes=maximum_bytes
        )
        report_evidence = _validated_receipt_artifact(
            receipt, directory, "verification_report", maximum_bytes=maximum_bytes
        )
        warning_evidence = _validated_receipt_artifact(
            receipt, directory, "compatibility_warnings", maximum_bytes=maximum_bytes
        )
        exact_payload = exact_evidence.payload
        vqe_payload = vqe_evidence.payload
        report = report_evidence.payload
        warning_payload = warning_evidence.payload
        if not all(
            isinstance(payload, dict)
            for payload in (exact_payload, vqe_payload, report, warning_payload)
        ):
            raise ValueError("A consumed authoritative artifact has no object payload.")
        validated_artifacts = {
            evidence.pointer.artifact_identifier: evidence
            for evidence in (
                exact_evidence, vqe_evidence, report_evidence, warning_evidence
            )
        }

        exact_artifact = ScientificResultArtifact.model_validate(exact_payload)
        vqe_artifact = ScientificResultArtifact.model_validate(vqe_payload)
        warnings = CompatibilityWarningEvidence.model_validate(warning_payload)
        failures = verify_receipt_identities(
            receipt,
            exact_result=exact_payload,
            vqe_result=vqe_payload,
            exact_result_pointer=exact_evidence.pointer,
            vqe_result_pointer=vqe_evidence.pointer,
            expected_outcome=receipt.scientific_outcome,
        )
        if failures:
            raise ValueError("Receipt identity verification failed: " + ", ".join(failures))

        experiment = manifest.experiment
        experiment_pointer = artifact_reference(
            "experiment", "quantum_chemistry_experiment",
            experiment.model_dump(mode="json"), filename="experiment.json",
        ).pointer
        outcome = receipt.scientific_outcome
        checks = report.get("results")
        if not isinstance(checks, list):
            raise ValueError("Verification report results are malformed.")
        receipt_checks = [item.model_dump(mode="json") for item in receipt.verification_results]
        if checks != receipt_checks:
            raise ValueError("Verification report disagrees with the authoritative receipt.")
        findings = [
            {**finding, "verifier_identifier": check.get("verifier_identifier")}
            for check in checks if isinstance(check, dict)
            for finding in check.get("findings", []) if isinstance(finding, dict)
        ]
        blocking = [item for item in findings if item.get("blocking") is True]
        verification_passed = not blocking_findings(receipt.verification_results)

        exact_identity = exact_artifact.scientific_identity
        vqe_identity = vqe_artifact.scientific_identity
        exact = exact_artifact.execution_result
        vqe = vqe_artifact.execution_result
        agreement = report.get("numerical_agreement")
        if not isinstance(agreement, dict):
            raise ValueError("Verification numerical agreement is malformed.")
        expected_agreement = {
            "exact_total_energy_hex": encode_float(exact.total_energy_hartree),
            "vqe_total_energy_hex": encode_float(vqe.total_energy_hartree),
            "absolute_difference_hartree": outcome.absolute_difference,
            "tolerance_hartree": outcome.tolerance,
            "units": outcome.units,
            "passed": outcome.comparison_passed,
        }
        if agreement != expected_agreement:
            raise ValueError(
                "Verification numerical agreement disagrees with the authoritative scientific outcome."
            )
        receipt_reference = artifact_reference(
            "receipt", "quantum_preflight_receipt", receipt_payload, filename="receipt.json"
        )
        required_equalities = (
            (receipt.experiment, experiment_pointer, "receipt experiment identity"),
            (summary.get("experiment_fingerprint"), experiment.fingerprint, "summary experiment fingerprint"),
            (summary.get("structure_sha256"), outcome.molecular_structure_sha256, "structure SHA-256"),
            (summary.get("qubit_hamiltonian_sha256"), outcome.qubit_hamiltonian_sha256, "Hamiltonian SHA-256"),
            (exact_identity.experiment_sha256, experiment.fingerprint, "exact experiment identity"),
            (vqe_identity.experiment_sha256, experiment.fingerprint, "VQE experiment identity"),
            (exact_identity.molecular_structure_sha256, outcome.molecular_structure_sha256, "exact structure identity"),
            (vqe_identity.molecular_structure_sha256, outcome.molecular_structure_sha256, "VQE structure identity"),
            (exact_identity.qubit_hamiltonian_sha256, outcome.qubit_hamiltonian_sha256, "exact Hamiltonian identity"),
            (vqe_identity.qubit_hamiltonian_sha256, outcome.qubit_hamiltonian_sha256, "VQE Hamiltonian identity"),
            (exact.total_energy_hartree, outcome.exact_total_energy, "exact result energy"),
            (vqe.total_energy_hartree, outcome.vqe_total_energy, "VQE result energy"),
            (summary.get("receipt_sha256"), receipt_reference.content_sha256, "receipt SHA-256"),
            (summary.get("scientific_verification_passed"), verification_passed, "verification decision"),
            (summary.get("authorized"), receipt.authorized, "authorization decision"),
            (receipt.scientific_verification_passed, verification_passed, "receipt verification decision"),
        )
        for observed, expected, label in required_equalities:
            if observed != expected:
                raise ValueError(f"Authoritative {label} mismatch.")
        if receipt.authorized and blocking:
            raise ValueError("An authorized receipt contains blocking findings.")
        if receipt.authorized != outcome.authorization_decision:
            raise ValueError("Receipt authorization disagrees with its scientific outcome.")

        artifact_identities: list[dict[str, str]] = []
        for pointer in receipt.artifacts:
            evidence = validated_artifacts.get(pointer.artifact_identifier)
            if evidence is None:
                evidence = _validated_receipt_artifact(
                    receipt, directory, pointer.artifact_identifier,
                    maximum_bytes=maximum_bytes,
                )
                validated_artifacts[pointer.artifact_identifier] = evidence
            artifact_identities.append({
                "artifact_identifier": pointer.artifact_identifier,
                "artifact_type": evidence.artifact_type,
                "content_sha256": pointer.content_sha256,
            })

        results = PublicResults(
            run_identifier=run_identifier,
            preset_identifier=preset_identifier,
            experiment_identifier=experiment.experiment_identifier,
            experiment_fingerprint=experiment.fingerprint,
            expected_experiment_sha256=manifest.expected_experiment_sha256,
            structure_identifier=experiment.molecular_system.structure_artifact_identifier,
            structure_sha256=outcome.molecular_structure_sha256,
            hamiltonian_sha256=outcome.qubit_hamiltonian_sha256,
            exact_scientific_result_sha256=receipt.exact_scientific_result_sha256,
            vqe_scientific_result_sha256=receipt.vqe_scientific_result_sha256,
            scientific_outcome_sha256=receipt.scientific_outcome_sha256,
            exact_total_energy_hartree=exact.total_energy_hartree,
            vqe_total_energy_hartree=vqe.total_energy_hartree,
            absolute_difference_hartree=outcome.absolute_difference,
            tolerance_hartree=outcome.tolerance,
            energy_unit="hartree",
            exact_solver_metadata={
                key: getattr(exact, key) for key in (
                    "solver_identifier", "solver_version", "completed", "duration_seconds",
                    "number_of_qubits", "particle_count",
                )
            },
            vqe_solver_metadata={
                key: getattr(vqe, key, None) for key in (
                    "solver_identifier", "solver_version", "optimizer_identifier",
                    "optimizer_status", "ansatz_identifier", "initial_state_identifier",
                    "duration_seconds", "number_of_qubits",
                )
            },
            optimizer_evaluations=getattr(vqe, "optimizer_evaluations", None),
            converged=getattr(vqe, "converged", None),
            compatibility_warnings=[item.model_dump(mode="json") for item in warnings.warnings],
            execution_environment_identity=outcome.environment_compatibility_sha256,
            receipt_sha256=receipt_reference.content_sha256,
        ).model_dump(mode="json")
        verification = PublicVerification(
            run_identifier=run_identifier,
            preset_identifier=preset_identifier,
            experiment_identifier=experiment.experiment_identifier,
            experiment_fingerprint=experiment.fingerprint,
            expected_experiment_sha256=manifest.expected_experiment_sha256,
            structure_identifier=experiment.molecular_system.structure_artifact_identifier,
            structure_sha256=outcome.molecular_structure_sha256,
            verification_completed=True,
            verification_passed=verification_passed,
            authorization_state="authorized" if receipt.authorized else "rejected",
            blocking_findings=blocking,
            nonblocking_findings=[item for item in findings if item.get("blocking") is not True],
            tolerance_check=agreement,
            scientific_identity_checks=[
                item for item in checks if isinstance(item, dict) and str(item.get("verifier_identifier", ""))
                in {
                    "quantum.specification", "quantum.molecular", "quantum.electronic",
                    "quantum.hamiltonian", "quantum.exact", "quantum.vqe",
                }
            ],
            artifact_integrity_checks=[
                item for item in checks if isinstance(item, dict)
                and str(item.get("verifier_identifier", "")) == "quantum.lineage"
            ],
            checks=checks,
            compatibility_warnings=[item.model_dump(mode="json") for item in warnings.warnings],
        ).model_dump(mode="json")
        public_receipt = PublicReceipt(
            schema_version=receipt.schema_version,
            run_identifier=run_identifier,
            preset_identifier=preset_identifier,
            execution_identifier=receipt.execution_identifier,
            experiment_identifier=experiment.experiment_identifier,
            experiment_fingerprint=experiment.fingerprint,
            expected_experiment_sha256=manifest.expected_experiment_sha256,
            structure_identifier=experiment.molecular_system.structure_artifact_identifier,
            structure_sha256=outcome.molecular_structure_sha256,
            hamiltonian_sha256=outcome.qubit_hamiltonian_sha256,
            exact_scientific_result_sha256=receipt.exact_scientific_result_sha256,
            vqe_scientific_result_sha256=receipt.vqe_scientific_result_sha256,
            scientific_outcome_sha256=receipt.scientific_outcome_sha256,
            execution_environment_identity=outcome.environment_compatibility_sha256,
            receipt_sha256=receipt_reference.content_sha256,
            verification_passed=verification_passed,
            authorization_state="authorized" if receipt.authorized else "rejected",
            authorized=receipt.authorized,
            artifacts=artifact_identities,
        ).model_dump(mode="json")
        safe_summary = {key: value for key, value in summary.items() if key != "receipt_path"}
        if ibm_preflight_evidence_path is not None:
            if (
                ibm_preflight_evidence_path.name != "ibm-preflight-evidence.json"
                or ibm_preflight_evidence_path.parent.name != "quantum-worker"
            ):
                raise ValueError("IBM preflight evidence path is uncontrolled.")
            sidecar = _controlled_json(
                ibm_preflight_evidence_path.parent,
                ibm_preflight_evidence_path.name,
                maximum_bytes=maximum_bytes,
            )
            ansatz_evidence = _validated_receipt_artifact(
                receipt, directory, "ansatz_manifest", maximum_bytes=maximum_bytes
            )
            electronic_evidence = _validated_receipt_artifact(
                receipt, directory, "electronic_problem", maximum_bytes=maximum_bytes
            )
            electronic_payload = electronic_evidence.payload
            if not isinstance(electronic_payload, dict):
                raise ValueError("IBM electronic preflight evidence is malformed.")
            parameters = sidecar.get("optimized_parameters")
            parameter_sha256 = sidecar.get("optimized_parameters_sha256")
            source_bound_circuit_sha256 = sidecar.get(
                "source_bound_circuit_sha256"
            )
            source_observable_sha256 = sidecar.get("source_observable_sha256")
            nuclear_repulsion = sidecar.get("nuclear_repulsion_energy_hartree")
            electronic_nuclear_repulsion = electronic_payload.get(
                "nuclear_repulsion_energy_hartree"
            )
            if (
                sidecar.get("schema_version")
                != "cgr.pulsate-ibm-local-preflight/1.0.0"
                or sidecar.get("local_receipt_sha256")
                != receipt_reference.content_sha256
                or sidecar.get("ansatz_sha256")
                != ansatz_evidence.pointer.content_sha256
                or not isinstance(parameters, list)
                or not parameters
                or not all(
                    isinstance(value, (int, float))
                    and math.isfinite(float(value))
                    for value in parameters
                )
                or sha256_fingerprint([float(value) for value in parameters])
                != parameter_sha256
                or parameter_sha256 != summary.get("optimized_parameters_sha256")
                or parameter_sha256 != vqe.optimized_parameters_sha256
                or not isinstance(source_bound_circuit_sha256, str)
                or not _SHA256.fullmatch(source_bound_circuit_sha256)
                or not isinstance(source_observable_sha256, str)
                or not _SHA256.fullmatch(source_observable_sha256)
                or not isinstance(nuclear_repulsion, (int, float))
                or not math.isfinite(float(nuclear_repulsion))
                or not isinstance(electronic_nuclear_repulsion, (int, float))
                or float(nuclear_repulsion)
                != float(electronic_nuclear_repulsion)
            ):
                raise ValueError("IBM preflight sidecar identity validation failed.")
            safe_summary["ibm_preflight"] = {
                "ansatz_sha256": ansatz_evidence.pointer.content_sha256,
                "optimized_parameters": [float(value) for value in parameters],
                "optimized_parameters_sha256": parameter_sha256,
                "source_bound_circuit_sha256": source_bound_circuit_sha256,
                "source_observable_sha256": source_observable_sha256,
                "number_of_qubits": sidecar.get("number_of_qubits"),
                "number_of_parameters": sidecar.get("number_of_parameters"),
                "circuit_depth": sidecar.get("circuit_depth"),
                "nuclear_repulsion_energy_hartree": float(nuclear_repulsion),
                "artifact_lineage_validated": True,
                "local_receipt_sha256": receipt_reference.content_sha256,
                "ibm_preflight_evidence_sha256": sha256_fingerprint(sidecar),
            }
        for response in (results, verification, public_receipt, safe_summary):
            assert_public_response_safe(response)
        return ExecutionOutput(results, verification, public_receipt, safe_summary)


class RunNotFoundError(LookupError): pass
class ArtifactUnavailableError(RuntimeError): pass
class SceneUnavailableError(RuntimeError): pass
class IdempotencyConflictError(RuntimeError): pass
class InvalidIdempotencyKeyError(ValueError): pass
class RunRootOwnershipError(RuntimeError): pass
class CoordinatorConfigurationError(ValueError): pass
class RecoverableIBMJobError(RuntimeError):
    def __init__(self, message: str, *, job_identifier: str) -> None:
        super().__init__(message)
        self.job_identifier = job_identifier


class TerminalIBMJobError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        job_identifier: str,
        backend_name: str | None,
        runtime_status: str | None,
    ) -> None:
        super().__init__(message)
        self.job_identifier = job_identifier
        self.backend_name = backend_name
        self.runtime_status = runtime_status


class _RunRootLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle: Any | None = None

    def acquire(self) -> None:
        handle = self.path.open("a+b")
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            handle.close()
            raise RunRootOwnershipError("The Pulsate run root is already owned by another live coordinator.") from exc
        self.handle = handle

    def release(self) -> None:
        if self.handle is None:
            return
        try:
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None


class RunCoordinator:
    """A bounded, durable, explicitly single-process development coordinator."""

    def __init__(
        self,
        *,
        run_root: Path,
        manifest_resolver: Callable[[str], ManifestEnvelope],
        executor: PresetRunExecutor,
        enabled: bool,
        experiment_resolver: Callable[[str], tuple[Any, ...]] | None = None,
        approved_experiment_resolver: Callable[[str], Any] | None = None,
        ibm_executor: IBMRunExecutor | None = None,
        unavailable_reason: str | None = None,
        max_workers: int = 1,
        max_run_seconds: int | str = _DEFAULT_MAX_RUN_SECONDS,
        precondition_check: Callable[[], str | None] | None = None,
    ) -> None:
        self.configured_run_root = Path(run_root)
        self.run_root = self.configured_run_root
        self.manifest_resolver = manifest_resolver
        self.experiment_resolver = experiment_resolver
        self.approved_experiment_resolver = approved_experiment_resolver
        self.executor = executor
        self.ibm_executor = ibm_executor
        self.configured_enabled = enabled
        self.enabled = False
        self.unavailable_reason = unavailable_reason
        self.max_workers = max(1, min(max_workers, 4))
        self.configured_max_run_seconds = max_run_seconds
        self.max_run_seconds: int | None = None
        self.precondition_check = precondition_check
        self._lock = threading.RLock()
        self._pool: ThreadPoolExecutor | None = None
        self._futures: dict[Future[Any], str] = {}
        self._idempotency: dict[str, tuple[dict[str, str], str]] = {}
        self._root_lock: _RunRootLock | None = None
        self._recoverable_ibm_runs: list[
            tuple[
                str,
                RunSourceType,
                str,
                str | None,
                ManifestEnvelope,
                str | None,
                dict[str, Any] | None,
            ]
        ] = []
        self._started = False
        self._accepting = False

    @property
    def started(self) -> bool:
        return self._started

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            timeout = self._validate_timeout(self.configured_max_run_seconds)
            configured_root = self.configured_run_root
            if configured_root.is_symlink():
                raise CoordinatorConfigurationError("Pulsate run root must be a normal directory.")
            if configured_root.exists() and not configured_root.is_dir():
                raise CoordinatorConfigurationError("Pulsate run root must be a normal directory.")
            configured_root.mkdir(parents=True, exist_ok=True)
            self.run_root = configured_root.resolve(strict=True)
            root_lock = _RunRootLock(self.run_root / ".coordinator.lock")
            try:
                root_lock.acquire()
                self._root_lock = root_lock
                self.max_run_seconds = timeout
                self._idempotency.clear()
                self._futures.clear()
                self._recoverable_ibm_runs.clear()
                self._recover()
                self.enabled = self.configured_enabled
                if self.enabled and self.precondition_check is not None:
                    reason = self.precondition_check()
                    if reason is not None:
                        self.enabled = False
                        self.unavailable_reason = reason
                if not self.configured_enabled and self.unavailable_reason is None:
                    self.unavailable_reason = "Local quantum execution is not enabled on this backend."
                self._pool = ThreadPoolExecutor(
                    max_workers=self.max_workers, thread_name_prefix="pulsate-run"
                )
                self._accepting = True
                self._started = True
                for recovered in self._recoverable_ibm_runs:
                    future = self._pool.submit(self._execute, *recovered, "ibm_quantum")
                    self._futures[future] = recovered[0]
                    future.add_done_callback(self._discard_future)
                self._recoverable_ibm_runs.clear()
            except Exception:
                root_lock.release()
                self._root_lock = None
                self.enabled = False
                self._accepting = False
                raise

    def close(self) -> None:
        with self._lock:
            self._accepting = False
            pool = self._pool
            self._pool = None
            futures = tuple(self._futures.items())
        for future, run_identifier in futures:
            if future.cancel():
                self._interrupt_queued_run(run_identifier)
        if pool is not None:
            pool.shutdown(wait=True, cancel_futures=True)
        with self._lock:
            self._futures.clear()
            if self._root_lock is not None:
                self._root_lock.release()
                self._root_lock = None
            self._started = False
            self.enabled = False

    @staticmethod
    def _validate_timeout(value: int | str) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise CoordinatorConfigurationError("PULSATE_MAX_RUN_SECONDS must be an integer.") from exc
        if parsed <= 0 or parsed > _MAX_CONFIGURED_RUN_SECONDS:
            raise CoordinatorConfigurationError(
                f"PULSATE_MAX_RUN_SECONDS must be between 1 and {_MAX_CONFIGURED_RUN_SECONDS}."
            )
        return parsed

    def capability(self) -> dict[str, Any]:
        local_available = self._started and self._accepting and self.enabled
        if local_available:
            reason = None
        elif not self._started:
            reason = "The local run coordinator lifecycle has not started."
        else:
            reason = self.unavailable_reason or "Local quantum execution is unavailable."
        ibm = (
            self.ibm_executor.capability()
            if self.ibm_executor is not None
            else {
                "available": False,
                "backend_name": None,
                "reason": "IBM Quantum execution is not configured on this server.",
                "maximum_run_seconds": None,
                "target_precision": None,
            }
        )
        if not local_available and ibm.get("available") is True:
            ibm = {**ibm, "available": False, "reason": "The trusted local preflight is unavailable."}
        targets = (["local_simulator"] if local_available else []) + (
            ["ibm_quantum"] if ibm.get("available") is True else []
        )
        response = {
            "available": local_available,
            "execution_targets": targets,
            "reason": reason,
            "maximum_run_seconds": self.max_run_seconds,
            "local_simulator": {
                "available": local_available,
                "reason": reason,
                "maximum_run_seconds": self.max_run_seconds,
            },
            "ibm_quantum": ibm,
        }
        assert_public_response_safe(response)
        return response

    def create(
        self,
        preset_identifier: str | None,
        execution_target: str,
        idempotency_key: str | None,
        *,
        experiment_identifier: str | None = None,
        approved_experiment_identifier: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        if execution_target not in {"local_simulator", "ibm_quantum"}:
            raise ValueError("unsupported_execution_target")
        if idempotency_key is not None and not _IDEMPOTENCY_KEY.fullmatch(idempotency_key):
            raise InvalidIdempotencyKeyError("Invalid Idempotency-Key header.")
        if (
            sum(
                source is not None
                for source in (
                    preset_identifier,
                    experiment_identifier,
                    approved_experiment_identifier,
                )
            )
            != 1
        ):
            raise ValueError("exactly_one_experiment_source_required")
        molecule: dict[str, Any] | None = None
        approved_provenance: dict[str, Any] | None = None
        if approved_experiment_identifier is not None:
            if self.approved_experiment_resolver is None:
                raise RunNotFoundError("Approved experiment not found.")
            resolved = self.approved_experiment_resolver(
                approved_experiment_identifier
            )
            manifest = resolved.manifest
            molecule = resolved.molecule
            requested_target = resolved.execution_target
            approved_provenance = ApprovedExecutionProvenance.model_validate(
                resolved.provenance
            ).model_dump(mode="json")
            if execution_target != requested_target:
                raise ValueError("execution_target_mismatch")
            source_type: RunSourceType = "approved_experiment"
            source_identifier = approved_experiment_identifier
            request = {
                "approved_experiment_identifier": approved_experiment_identifier,
                "execution_target": execution_target,
            }
        elif experiment_identifier is not None:
            if self.experiment_resolver is None:
                raise RunNotFoundError("Experiment not found.")
            resolved = self.experiment_resolver(experiment_identifier)
            if len(resolved) == 3:
                manifest, molecule, requested_target = resolved
            elif len(resolved) == 2:
                manifest, molecule = resolved
                requested_target = "local_simulator"
            else:
                raise ValueError("Experiment resolver returned an invalid result.")
            if execution_target != requested_target:
                raise ValueError("execution_target_mismatch")
            source_type = "dynamic_experiment"
            source_identifier = experiment_identifier
            request = {
                "experiment_identifier": experiment_identifier,
                "execution_target": execution_target,
            }
        else:
            assert preset_identifier is not None
            if execution_target != "local_simulator":
                raise ValueError("execution_target_mismatch")
            manifest = self.manifest_resolver(preset_identifier)
            source_type = "preset"
            source_identifier = preset_identifier
            request = {
                "preset_identifier": preset_identifier,
                "execution_target": execution_target,
            }
        with self._lock:
            if not self._started or not self._accepting or not self.enabled or self._pool is None:
                raise RuntimeError("execution_unavailable")
            if execution_target == "ibm_quantum":
                if self.ibm_executor is None or self.ibm_executor.capability().get("available") is not True:
                    raise RuntimeError("ibm_execution_unavailable")
            if idempotency_key in self._idempotency:
                previous, run_identifier = self._idempotency[idempotency_key]
                if previous != request:
                    raise IdempotencyConflictError("Idempotency key was already used for a different request.")
                return self.get(run_identifier), False
            run_identifier = f"run-{uuid.uuid4().hex}"
            directory = self._directory(run_identifier)
            directory.mkdir()
            now = utc_now()
            identity = self._identity(
                manifest,
                source_type=source_type,
                source_identifier=source_identifier,
                preset_identifier=preset_identifier,
                molecule=molecule,
                approved_experiment=approved_provenance,
            )
            request_document = {**request, "idempotency_key": idempotency_key, "created_at": now}
            state = {
                "run_identifier": run_identifier, **identity, "execution_target": execution_target,
                "status": "queued", "created_at": now, "updated_at": now,
                "status_history": [{"status": "queued", "timestamp": now}],
                "status_url": f"/api/v1/runs/{run_identifier}",
            }
            _write_json_atomic(directory / "request.json", request_document)
            if source_type != "preset":
                write_json_atomic(
                    directory / "compiled-manifest.json",
                    manifest.model_dump(mode="json"),
                    maximum_bytes=_MAX_COMPILED_MANIFEST_BYTES,
                )
            _write_json_atomic(directory / "state.json", state)
            if idempotency_key is not None:
                self._idempotency[idempotency_key] = (request, run_identifier)
            future = self._pool.submit(
                self._execute,
                run_identifier,
                source_type,
                source_identifier,
                preset_identifier,
                manifest,
                molecule.get("structure_hash") if molecule is not None else None,
                approved_provenance,
                execution_target,
            )
            self._futures[future] = run_identifier
            future.add_done_callback(self._discard_future)
            assert_public_response_safe(state)
            return state, True

    def get(self, run_identifier: str) -> dict[str, Any]:
        with self._lock:
            if not self._started:
                raise RunNotFoundError("Run coordinator is not started.")
            state = self._read_validated_state(run_identifier)
            assert_public_response_safe(state)
            return state

    def scene_manifest(
        self, run_identifier: str
    ) -> tuple[dict[str, Any], ManifestEnvelope]:
        """Load a run's declared scene inputs without executing or recovering it."""

        with self._lock:
            if not self._started:
                raise RunNotFoundError("Run coordinator is not started.")
            if not _RUN_IDENTIFIER.fullmatch(run_identifier):
                raise RunNotFoundError("Run not found.")
            unresolved_directory = self.run_root / run_identifier
            try:
                metadata = unresolved_directory.lstat()
            except FileNotFoundError as exc:
                raise RunNotFoundError("Run not found.") from exc
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise SceneUnavailableError("Run scene is unavailable.")
            directory = self._directory(run_identifier)

            try:
                state = self._read_validated_state(
                    run_identifier, persist_legacy_identity=False
                )
                source_type = state["source_type"]
                source_identifier = state["source_identifier"]
                if source_type == "preset":
                    resolved = self.manifest_resolver(source_identifier)
                    manifest = ManifestEnvelope.model_validate(
                        resolved.model_dump(mode="json")
                    )
                elif source_type in {"dynamic_experiment", "approved_experiment"}:
                    document = _controlled_json(
                        directory,
                        "compiled-manifest.json",
                        maximum_bytes=_MAX_COMPILED_MANIFEST_BYTES,
                    )
                    manifest = ManifestEnvelope.model_validate(document)
                else:
                    raise ValueError("Persisted run has an invalid source type.")

                experiment = manifest.experiment
                if experiment.fingerprint != manifest.expected_experiment_sha256:
                    raise ValueError(
                        "Scene manifest experiment identity is inconsistent."
                    )
                expected_identity = {
                    "experiment_identifier": experiment.experiment_identifier,
                    "experiment_fingerprint": experiment.fingerprint,
                    "expected_experiment_sha256": manifest.expected_experiment_sha256,
                    "structure_identifier": (
                        experiment.molecular_system.structure_artifact_identifier
                    ),
                }
                if any(
                    state.get(name) != expected
                    for name, expected in expected_identity.items()
                ):
                    raise ValueError(
                        "Persisted run state does not match its scene manifest."
                    )
                if (
                    source_type != "preset"
                    and experiment.experiment_identifier != source_identifier
                ):
                    raise ValueError(
                        "Persisted run source does not match its scene manifest."
                    )
                return state, manifest
            except SceneUnavailableError:
                raise
            except Exception as exc:
                raise SceneUnavailableError("Run scene is unavailable.") from exc

    def artifact(self, run_identifier: str, name: Literal["results", "verification", "receipt"]) -> dict[str, Any]:
        state = self.get(run_identifier)
        directory = self._directory(run_identifier)
        if state["status"] not in {"authorized", "rejected"}:
            raise ArtifactUnavailableError(
                f"{name.capitalize()} are not available while run {run_identifier} is {state['status']}."
            )
        models = {
            "results": PublicResults,
            "verification": PublicVerification,
            "receipt": PublicReceipt,
        }
        value = models[name].model_validate(
            _controlled_run_json(directory, f"{name}.json")
        ).model_dump(mode="json")
        if value.get("approved_experiment") is None:
            value.pop("approved_experiment", None)
        if value["run_identifier"] != run_identifier:
            raise ValueError("Persisted public projection has a mismatched run identifier.")
        for field in (
            "source_type",
            "source_identifier",
            "preset_identifier",
            "approved_experiment",
        ):
            if value.get(field) != state.get(field):
                raise ValueError(f"Persisted public projection has a mismatched {field}.")
        assert_public_response_safe(value)
        return value

    def _execute(
        self,
        run_identifier: str,
        source_type: RunSourceType,
        source_identifier: str,
        preset_identifier: str | None,
        manifest: ManifestEnvelope,
        expected_structure_sha256: str | None,
        approved_experiment: dict[str, Any] | None,
        execution_target: Literal["local_simulator", "ibm_quantum"],
    ) -> None:
        try:
            self._transition(run_identifier, "validating")
            if manifest.experiment.fingerprint != manifest.expected_experiment_sha256:
                raise ValueError("Manifest experiment fingerprint does not match its expected identity.")
            self._validate_approved_execution_binding(
                manifest=manifest,
                approved_experiment=approved_experiment,
                expected_structure_sha256=expected_structure_sha256,
                execution_target=execution_target,
            )
            self._transition(
                run_identifier,
                "running_local_preflight" if execution_target == "ibm_quantum" else "running_quantum_workflow",
            )
            assert self.max_run_seconds is not None
            effective_timeout = min(
                self.max_run_seconds,
                manifest.experiment.execution_policy.maximum_duration_seconds,
            )
            if execution_target == "ibm_quantum":
                if self.ibm_executor is None:
                    raise RuntimeError("IBM Quantum execution is unavailable.")

                def ibm_status(status: str, additions: dict[str, Any] | None = None) -> None:
                    if status not in ACTIVE_STATUSES:
                        raise ValueError("IBM executor attempted an invalid coordinator status.")
                    self._transition(run_identifier, status, additions)

                output = self.ibm_executor.execute(
                    manifest,
                    preset_identifier=source_identifier,
                    run_directory=self._directory(run_identifier),
                    maximum_seconds=effective_timeout,
                    status_callback=ibm_status,
                    source_type=source_type,
                    source_identifier=source_identifier,
                    source_preset_identifier=preset_identifier,
                    expected_structure_sha256=expected_structure_sha256,
                    run_identifier=run_identifier,
                    requested_precision=(
                        approved_experiment.get("requested_precision")
                        if approved_experiment is not None
                        else None
                    ),
                    requested_backend=(
                        approved_experiment.get("requested_backend")
                        if approved_experiment is not None
                        else None
                    ),
                    approved_experiment=approved_experiment,
                )
            else:
                output = self.executor.execute(
                    manifest,
                    preset_identifier=source_identifier,
                    run_directory=self._directory(run_identifier),
                    maximum_seconds=effective_timeout,
                )
            output = _bind_public_source_identity(
                output,
                source_type=source_type,
                source_identifier=source_identifier,
                preset_identifier=preset_identifier,
                approved_experiment=approved_experiment,
            )
            output = validate_execution_output(
                output,
                manifest=manifest,
                source_type=source_type,
                source_identifier=source_identifier,
                preset_identifier=preset_identifier,
                run_identifier=run_identifier,
                expected_structure_sha256=expected_structure_sha256,
                approved_experiment=approved_experiment,
            )
            directory = self._directory(run_identifier)
            _write_json_atomic(directory / "runner-summary.json", output.runner_summary)
            _write_json_atomic(directory / "results.json", output.results)
            _write_json_atomic(directory / "verification.json", output.verification)
            _write_json_atomic(directory / "receipt.json", output.receipt)
            terminal: RunStatus = "authorized" if output.receipt["authorized"] else "rejected"
            terminal_additions = {
                "structure_sha256": output.results["structure_sha256"],
                "hamiltonian_sha256": output.results["hamiltonian_sha256"],
                "receipt_sha256": output.results["receipt_sha256"],
                "execution_environment_identity": output.results["execution_environment_identity"],
            }
            ibm_evidence = output.results.get("ibm_execution")
            if isinstance(ibm_evidence, dict):
                terminal_additions["ibm_job_identifier"] = ibm_evidence.get("job_identifier")
                terminal_additions["ibm_backend_name"] = ibm_evidence.get("backend_name")
            self._transition(run_identifier, terminal, terminal_additions)
        except RecoverableIBMJobError as exc:
            self._transition(
                run_identifier,
                "queued_on_ibm",
                {
                    "ibm_job_identifier": exc.job_identifier,
                    "recoverable_ibm_retrieval": True,
                },
            )
        except TerminalIBMJobError as exc:
            message = _public_error_message(exc)
            error = {
                "code": "run_execution_failed",
                "message": message,
                "type": type(exc).__name__,
            }
            _write_json_atomic(self._directory(run_identifier) / "error.json", error)
            self._transition(
                run_identifier,
                "failed",
                {
                    "ibm_job_identifier": exc.job_identifier,
                    "ibm_backend_name": exc.backend_name,
                    "ibm_runtime_status": exc.runtime_status,
                    "error": {"code": error["code"], "message": message},
                },
            )
        except Exception as exc:
            message = _public_error_message(exc)
            error = {"code": "run_execution_failed", "message": message, "type": type(exc).__name__}
            _write_json_atomic(self._directory(run_identifier) / "error.json", error)
            self._transition(run_identifier, "failed", {"error": {"code": error["code"], "message": message}})

    @staticmethod
    def _validate_approved_execution_binding(
        *,
        manifest: ManifestEnvelope,
        approved_experiment: dict[str, Any] | None,
        expected_structure_sha256: str | None,
        execution_target: str,
    ) -> None:
        if approved_experiment is None:
            return
        provenance = ApprovedExecutionProvenance.model_validate(
            approved_experiment
        )
        execution_parameters = (
            manifest.experiment.parent_experiment.execution_policy.parameters
        )
        if (
            provenance.approved_experiment_identifier
            != manifest.experiment.experiment_identifier
            or provenance.compiled_execution_manifest_sha256
            != sha256_fingerprint(manifest.model_dump(mode="json"))
            or provenance.molecule_structure_sha256
            != expected_structure_sha256
            or provenance.execution_target != execution_target
            or provenance.mapper != manifest.experiment.quantum_model.mapper
            or provenance.ansatz != manifest.experiment.quantum_model.ansatz
            or provenance.optimizer != manifest.experiment.quantum_model.optimizer
            or provenance.optimizer_tolerance
            != manifest.experiment.quantum_model.convergence_threshold
            or execution_parameters.get("approved_scientific_objective")
            != provenance.approved_scientific_objective
            or execution_parameters.get("approved_requested_quantity")
            != provenance.approved_requested_quantity
            or execution_parameters.get("compiled_objective")
            != provenance.compiled_objective
            or execution_parameters.get("approved_specification_sha256")
            != provenance.approved_specification_sha256
        ):
            raise ValueError(
                "Approved experiment provenance does not match the compiled execution manifest."
            )

    def _transition(self, run_identifier: str, status: RunStatus, additions: dict[str, Any] | None = None) -> None:
        with self._lock:
            state = self._read_validated_state(run_identifier)
            if state["status"] in TERMINAL_STATUSES:
                return
            now = utc_now()
            state.update(additions or {})
            state.update({"status": status, "updated_at": now})
            state.setdefault("status_history", []).append({"status": status, "timestamp": now})
            _write_json_atomic(self._state_path(run_identifier), state)

    @staticmethod
    def _identity(
        manifest: ManifestEnvelope,
        *,
        source_type: RunSourceType,
        source_identifier: str,
        preset_identifier: str | None,
        molecule: dict[str, Any] | None = None,
        approved_experiment: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        experiment = manifest.experiment
        identity = {
            "source_type": source_type,
            "source_identifier": source_identifier,
            "preset_identifier": preset_identifier,
            "experiment_identifier": experiment.experiment_identifier,
            "experiment_fingerprint": experiment.fingerprint,
            "expected_experiment_sha256": manifest.expected_experiment_sha256,
            "structure_identifier": experiment.molecular_system.structure_artifact_identifier,
        }
        if approved_experiment is not None:
            identity["approved_experiment"] = approved_experiment
        if molecule is not None:
            identity["molecule"] = molecule
        return identity

    def _directory(self, run_identifier: str) -> Path:
        if not _RUN_IDENTIFIER.fullmatch(run_identifier):
            raise RunNotFoundError("Run not found.")
        path = (self.run_root / run_identifier).resolve()
        if path.parent != self.run_root:
            raise RunNotFoundError("Run not found.")
        return path

    def _state_path(self, run_identifier: str) -> Path:
        path = self._directory(run_identifier) / "state.json"
        if not path.is_file():
            raise RunNotFoundError("Run not found.")
        return path

    def _read_validated_state(
        self, run_identifier: str, *, persist_legacy_identity: bool = True
    ) -> dict[str, Any]:
        directory = self._directory(run_identifier)
        state = _controlled_run_json(directory, "state.json")
        request = _controlled_run_json(directory, "request.json")
        if state.get("run_identifier") != run_identifier:
            raise ValueError("Persisted run state has a mismatched run identifier.")
        source_type, source_identifier, expected_preset = self._request_source_identity(
            request
        )
        if "source_type" not in state and "source_identifier" not in state:
            if state.get("preset_identifier") != source_identifier:
                raise ValueError("Persisted legacy run state has a mismatched source identity.")
            state.update(
                {
                    "source_type": source_type,
                    "source_identifier": source_identifier,
                    "preset_identifier": expected_preset,
                }
            )
            if persist_legacy_identity:
                _write_json_atomic(directory / "state.json", state)
        if (
            state.get("source_type") != source_type
            or state.get("source_identifier") != source_identifier
            or state.get("preset_identifier") != expected_preset
        ):
            raise ValueError("Persisted run state has a mismatched source identity.")
        if source_type != "preset" and state.get("experiment_identifier") != source_identifier:
            raise ValueError("Persisted run state has a mismatched experiment identifier.")
        approved = state.get("approved_experiment")
        if source_type == "approved_experiment":
            validated_approved = ApprovedExecutionProvenance.model_validate(approved)
            if validated_approved.approved_experiment_identifier != source_identifier:
                raise ValueError("Persisted run state has mismatched approval provenance.")
        elif approved is not None:
            raise ValueError("Persisted run state has unexpected approval provenance.")
        return state

    @staticmethod
    def _request_source_identity(
        request: dict[str, Any],
    ) -> tuple[RunSourceType, str, str | None]:
        sources = {
            "preset": request.get("preset_identifier"),
            "dynamic_experiment": request.get("experiment_identifier"),
            "approved_experiment": request.get("approved_experiment_identifier"),
        }
        present = [
            (source_type, identifier)
            for source_type, identifier in sources.items()
            if isinstance(identifier, str)
        ]
        if len(present) != 1:
            raise ValueError("Persisted run request has an invalid experiment source.")
        source_type, source_identifier = present[0]
        return (
            source_type,
            source_identifier,
            source_identifier if source_type == "preset" else None,
        )

    def _discard_future(self, future: Future[Any]) -> None:
        with self._lock:
            self._futures.pop(future, None)

    def _interrupt_queued_run(self, run_identifier: str) -> None:
        try:
            state = self._read_validated_state(run_identifier)
        except (OSError, ValueError, RunNotFoundError):
            return
        if state.get("status") != "queued":
            return
        self._transition(run_identifier, "interrupted", {
            "error": {
                "code": "run_interrupted",
                "message": "The queued run was canceled during backend shutdown.",
            }
        })

    def _recover(self) -> None:
        for directory in self.run_root.iterdir():
            if not directory.is_dir() or directory.is_symlink() or not _RUN_IDENTIFIER.fullmatch(directory.name):
                continue
            state_path = directory / "state.json"
            request_path = directory / "request.json"
            try:
                state = _controlled_run_json(directory, state_path.name)
                request = _controlled_run_json(directory, request_path.name)
                if state.get("run_identifier") != directory.name:
                    continue
                source_type, source_identifier, expected_preset = (
                    self._request_source_identity(request)
                )
                if "source_type" not in state and "source_identifier" not in state:
                    if state.get("preset_identifier") != source_identifier:
                        continue
                    state.update(
                        {
                            "source_type": source_type,
                            "source_identifier": source_identifier,
                            "preset_identifier": expected_preset,
                        }
                    )
                    _write_json_atomic(state_path, state)
                if (
                    state.get("source_type") != source_type
                    or state.get("source_identifier") != source_identifier
                    or state.get("preset_identifier") != expected_preset
                ):
                    continue
                key = request.get("idempotency_key")
                if isinstance(key, str):
                    self._idempotency[key] = (
                        {
                            {
                                "preset": "preset_identifier",
                                "dynamic_experiment": "experiment_identifier",
                                "approved_experiment": "approved_experiment_identifier",
                            }[source_type]: source_identifier,
                            "execution_target": request["execution_target"],
                        },
                        directory.name,
                    )
                if state.get("status") in ACTIVE_STATUSES:
                    job_path = directory / "ibm-worker" / "job.json"
                    attempt_path = directory / "ibm-worker" / "submission-attempt.json"
                    compiled_path = directory / "compiled-manifest.json"
                    recover_ibm = (
                        request.get("execution_target") == "ibm_quantum"
                        and self.ibm_executor is not None
                        and (
                            (
                                job_path.is_file()
                                and not job_path.is_symlink()
                            )
                            or (
                                attempt_path.is_file()
                                and not attempt_path.is_symlink()
                            )
                        )
                        and compiled_path.is_file()
                        and not compiled_path.is_symlink()
                    )
                    now = utc_now()
                    if recover_ibm:
                        manifest = ManifestEnvelope.model_validate(
                            _controlled_run_json(directory, compiled_path.name)
                        )
                        molecule = state.get("molecule")
                        structure_hash = (
                            molecule.get("structure_hash")
                            if isinstance(molecule, dict)
                            else None
                        )
                        state.update({"status": "queued", "updated_at": now})
                        state.setdefault("status_history", []).append(
                            {"status": "queued", "timestamp": now, "reason": "recover_recorded_ibm_job"}
                        )
                        _write_json_atomic(state_path, state)
                        self._recoverable_ibm_runs.append(
                            (
                                directory.name,
                                source_type,
                                str(source_identifier),
                                expected_preset,
                                manifest,
                                structure_hash,
                                state.get("approved_experiment"),
                            )
                        )
                    else:
                        state.update({
                            "status": "interrupted", "updated_at": now,
                            "error": {"code": "run_interrupted", "message": "The backend restarted before this run reached a terminal state."},
                        })
                        state.setdefault("status_history", []).append({"status": "interrupted", "timestamp": now})
                        _write_json_atomic(state_path, state)
            except (KeyError, OSError, ValueError, json.JSONDecodeError):
                continue


def validate_execution_output(
    output: ExecutionOutput,
    *,
    manifest: ManifestEnvelope,
    preset_identifier: str | None,
    run_identifier: str,
    source_type: RunSourceType = "preset",
    source_identifier: str | None = None,
    expected_structure_sha256: str | None = None,
    approved_experiment: dict[str, Any] | None = None,
) -> ExecutionOutput:
    """Confirm injected or production projections agree with trusted identities."""
    results = PublicResults.model_validate(output.results)
    verification = PublicVerification.model_validate(output.verification)
    receipt = PublicReceipt.model_validate(output.receipt)
    experiment = manifest.experiment
    expected_source_identifier = source_identifier or preset_identifier
    if expected_source_identifier is None:
        raise ValueError("Executor output source identity is incomplete.")
    expected = {
        "source_type": source_type,
        "source_identifier": expected_source_identifier,
        "preset_identifier": preset_identifier,
        "experiment_identifier": experiment.experiment_identifier,
        "experiment_fingerprint": experiment.fingerprint,
        "expected_experiment_sha256": manifest.expected_experiment_sha256,
        "structure_identifier": experiment.molecular_system.structure_artifact_identifier,
    }
    for field, value in expected.items():
        if getattr(results, field) != value or getattr(verification, field) != value or getattr(receipt, field) != value:
            raise ValueError(f"Executor output {field} does not match the requested experiment.")
    expected_approval = (
        ApprovedExecutionProvenance.model_validate(
            approved_experiment
        ).model_dump(mode="json")
        if approved_experiment is not None
        else None
    )
    for projection in (results, verification, receipt):
        observed_approval = (
            projection.approved_experiment.model_dump(mode="json")
            if projection.approved_experiment is not None
            else None
        )
        if observed_approval != expected_approval:
            raise ValueError(
                "Executor output approved-experiment provenance is mismatched."
            )
    if receipt.run_identifier != run_identifier or verification.run_identifier != run_identifier:
        raise ValueError("Executor receipt run identifier mismatch.")
    if results.run_identifier != run_identifier:
        raise ValueError("Executor results run identifier mismatch.")
    comparisons = (
        (results.structure_sha256, receipt.structure_sha256, "structure SHA-256"),
        (verification.structure_sha256, receipt.structure_sha256, "verification structure SHA-256"),
        (results.hamiltonian_sha256, receipt.hamiltonian_sha256, "Hamiltonian SHA-256"),
        (results.exact_scientific_result_sha256, receipt.exact_scientific_result_sha256, "exact scientific result"),
        (results.vqe_scientific_result_sha256, receipt.vqe_scientific_result_sha256, "VQE scientific result"),
        (results.scientific_outcome_sha256, receipt.scientific_outcome_sha256, "scientific outcome"),
        (results.execution_environment_identity, receipt.execution_environment_identity, "execution environment"),
        (results.receipt_sha256, receipt.receipt_sha256, "receipt SHA-256"),
        (verification.verification_passed, receipt.verification_passed, "verification decision"),
        (verification.authorization_state, receipt.authorization_state, "authorization state"),
        (results.ibm_execution, receipt.ibm_execution, "IBM execution evidence"),
        (verification.ibm_execution, receipt.ibm_execution, "IBM verification evidence"),
    )
    for observed, trusted, label in comparisons:
        if observed != trusted:
            raise ValueError(f"Executor output {label} is inconsistent.")
    if (
        expected_structure_sha256 is not None
        and results.structure_sha256 != expected_structure_sha256
    ):
        raise ValueError(
            "Executor output structure SHA-256 does not match the planned molecule."
        )
    expected_state = "authorized" if receipt.authorized else "rejected"
    if receipt.authorization_state != expected_state:
        raise ValueError("Receipt authorization state is inconsistent.")
    if receipt.authorized and (not receipt.verification_passed or verification.blocking_findings):
        raise ValueError("Inconsistent executor output attempted to authorize blocking evidence.")
    summary_comparisons = {
        "experiment_fingerprint": results.experiment_fingerprint,
        "structure_sha256": results.structure_sha256,
        "qubit_hamiltonian_sha256": results.hamiltonian_sha256,
        "exact_scientific_result_sha256": results.exact_scientific_result_sha256,
        "vqe_scientific_result_sha256": results.vqe_scientific_result_sha256,
        "scientific_outcome_sha256": results.scientific_outcome_sha256,
        "receipt_sha256": results.receipt_sha256,
        "scientific_verification_passed": verification.verification_passed,
        "authorized": receipt.authorized,
        "exact_total_energy_hartree": results.exact_total_energy_hartree,
        "vqe_total_energy_hartree": results.vqe_total_energy_hartree,
        "absolute_difference_hartree": results.absolute_difference_hartree,
        "tolerance_hartree": results.tolerance_hartree,
    }
    for field, expected_value in summary_comparisons.items():
        if field in output.runner_summary and output.runner_summary[field] != expected_value:
            raise ValueError(f"Executor runner summary {field} is inconsistent.")
    for value in (output.results, output.verification, output.receipt, output.runner_summary):
        assert_public_response_safe(value)
    projections = [
        results.model_dump(mode="json"),
        verification.model_dump(mode="json"),
        receipt.model_dump(mode="json"),
    ]
    if approved_experiment is None:
        for projection in projections:
            projection.pop("approved_experiment", None)
    return ExecutionOutput(*projections, dict(output.runner_summary))
