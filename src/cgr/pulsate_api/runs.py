"""Durable single-process coordinator for trusted Pulsate preset runs."""

from __future__ import annotations

import json
import os
import re
import signal
import stat
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Callable, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from cgr.quantum_preflight.artifacts import artifact_reference, write_json_atomic
from cgr.quantum_preflight.contracts import ManifestEnvelope
from cgr.quantum_preflight.errors import QuantumTimeoutError
from cgr.quantum_preflight.identities import ScientificResultArtifact
from cgr.quantum_preflight.receipt import QuantumPreflightReceipt, verify_receipt_identities
from cgr.quantum_preflight.operators import encode_float
from cgr.quantum_preflight.verification import blocking_findings
from cgr.quantum_preflight.warnings import CompatibilityWarningEvidence
from cgr.pulsate_api.quantum_worker import (
    WORKER_EXIT_CODES,
    WORKER_MANIFEST_MAXIMUM_BYTES,
    WORKER_RESULT_MAXIMUM_BYTES,
    WorkerResultEnvelope,
)

RunStatus = Literal[
    "queued", "validating", "running_quantum_workflow",
    "authorized", "rejected", "failed", "interrupted",
]
TERMINAL_STATUSES = frozenset({"authorized", "rejected", "failed", "interrupted"})
ACTIVE_STATUSES = frozenset({"queued", "validating", "running_quantum_workflow"})
_RUN_IDENTIFIER = re.compile(r"^run-[0-9a-f]{32}$")
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
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
            assert_public_response_safe(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            assert_public_response_safe(item)
    elif isinstance(value, str) and _contains_absolute_path(value):
        raise ValueError("Public response contains an absolute filesystem path.")


def _public_error_message(exc: Exception) -> str:
    message = str(exc).strip() or type(exc).__name__
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


class PublicRunSourceIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: Literal["preset", "dynamic_experiment"] = "preset"
    source_identifier: str | None = None
    preset_identifier: str | None

    @model_validator(mode="after")
    def validate_source_identity(self) -> "PublicRunSourceIdentity":
        experiment_identifier = getattr(self, "experiment_identifier", None)
        if self.source_type == "preset":
            if not self.preset_identifier:
                raise ValueError("Preset source identity requires a preset identifier.")
            if self.source_identifier is None:
                self.source_identifier = self.preset_identifier
            if self.source_identifier != self.preset_identifier:
                raise ValueError("Preset source identity is mismatched.")
        else:
            if self.preset_identifier is not None:
                raise ValueError("Dynamic experiment source must not publish a preset identifier.")
            if not self.source_identifier or self.source_identifier != experiment_identifier:
                raise ValueError("Dynamic experiment source identity is mismatched.")
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


@dataclass(frozen=True)
class ExecutionOutput:
    results: dict[str, Any]
    verification: dict[str, Any]
    receipt: dict[str, Any]
    runner_summary: dict[str, Any]


def _bind_public_source_identity(
    output: ExecutionOutput,
    *,
    source_type: Literal["preset", "dynamic_experiment"],
    source_identifier: str,
    preset_identifier: str | None,
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
        projections.append(bound)
    summary = dict(output.runner_summary)
    summary.update(
        {
            "source_type": source_type,
            "source_identifier": source_identifier,
            "preset_identifier": preset_identifier,
        }
    )
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


class _BoundedLogCollector:
    """Continuously drain one worker pipe while retaining a bounded prefix."""

    def __init__(self, stream: Any, path: Path, *, thread_name: str) -> None:
        self.stream = stream
        self.path = path
        self.buffer = bytearray()
        self.truncated = False
        self.error: Exception | None = None
        self.persisted = False
        self.started = False
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
        result_root = run_directory / "runner-artifacts"
        if result_root.is_symlink() or result_root.exists():
            raise ValueError("The quantum runner artifact root must not pre-exist.")
        maximum_bytes = manifest.experiment.execution_policy.maximum_result_bytes
        worker_directory = self._create_worker_directory(run_directory)
        manifest_path = worker_directory / "manifest.json"
        result_envelope_path = worker_directory / "result.json"
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
        process_options: dict[str, Any] = {
            "cwd": self.repository_root,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "shell": False,
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
        for response in (results, verification, public_receipt, safe_summary):
            assert_public_response_safe(response)
        return ExecutionOutput(results, verification, public_receipt, safe_summary)


class RunNotFoundError(LookupError): pass
class ArtifactUnavailableError(RuntimeError): pass
class IdempotencyConflictError(RuntimeError): pass
class InvalidIdempotencyKeyError(ValueError): pass
class RunRootOwnershipError(RuntimeError): pass
class CoordinatorConfigurationError(ValueError): pass


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
        experiment_resolver: Callable[[str], tuple[ManifestEnvelope, dict[str, Any]]] | None = None,
        unavailable_reason: str | None = None,
        max_workers: int = 1,
        max_run_seconds: int | str = _DEFAULT_MAX_RUN_SECONDS,
        precondition_check: Callable[[], str | None] | None = None,
    ) -> None:
        self.configured_run_root = Path(run_root)
        self.run_root = self.configured_run_root
        self.manifest_resolver = manifest_resolver
        self.experiment_resolver = experiment_resolver
        self.executor = executor
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
        available = self._started and self._accepting and self.enabled
        if available:
            reason = None
        elif not self._started:
            reason = "The local run coordinator lifecycle has not started."
        else:
            reason = self.unavailable_reason or "Local quantum execution is unavailable."
        response = {
            "available": available,
            "execution_targets": ["local_simulator"] if available else [],
            "reason": reason,
            "maximum_run_seconds": self.max_run_seconds,
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
    ) -> tuple[dict[str, Any], bool]:
        if execution_target != "local_simulator":
            raise ValueError("unsupported_execution_target")
        if idempotency_key is not None and not _IDEMPOTENCY_KEY.fullmatch(idempotency_key):
            raise InvalidIdempotencyKeyError("Invalid Idempotency-Key header.")
        if (preset_identifier is None) == (experiment_identifier is None):
            raise ValueError("exactly_one_experiment_source_required")
        molecule: dict[str, Any] | None = None
        if experiment_identifier is not None:
            if self.experiment_resolver is None:
                raise RunNotFoundError("Experiment not found.")
            manifest, molecule = self.experiment_resolver(experiment_identifier)
            source_type: Literal["preset", "dynamic_experiment"] = "dynamic_experiment"
            source_identifier = experiment_identifier
            request = {
                "experiment_identifier": experiment_identifier,
                "execution_target": execution_target,
            }
        else:
            assert preset_identifier is not None
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
            )
            request_document = {**request, "idempotency_key": idempotency_key, "created_at": now}
            state = {
                "run_identifier": run_identifier, **identity, "execution_target": execution_target,
                "status": "queued", "created_at": now, "updated_at": now,
                "status_history": [{"status": "queued", "timestamp": now}],
                "status_url": f"/api/v1/runs/{run_identifier}",
            }
            _write_json_atomic(directory / "request.json", request_document)
            if experiment_identifier is not None:
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
        if value["run_identifier"] != run_identifier:
            raise ValueError("Persisted public projection has a mismatched run identifier.")
        for field in ("source_type", "source_identifier", "preset_identifier"):
            if value[field] != state[field]:
                raise ValueError(f"Persisted public projection has a mismatched {field}.")
        assert_public_response_safe(value)
        return value

    def _execute(
        self,
        run_identifier: str,
        source_type: Literal["preset", "dynamic_experiment"],
        source_identifier: str,
        preset_identifier: str | None,
        manifest: ManifestEnvelope,
        expected_structure_sha256: str | None,
    ) -> None:
        try:
            self._transition(run_identifier, "validating")
            if manifest.experiment.fingerprint != manifest.expected_experiment_sha256:
                raise ValueError("Manifest experiment fingerprint does not match its expected identity.")
            self._transition(run_identifier, "running_quantum_workflow")
            assert self.max_run_seconds is not None
            effective_timeout = min(
                self.max_run_seconds,
                manifest.experiment.execution_policy.maximum_duration_seconds,
            )
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
            )
            output = validate_execution_output(
                output,
                manifest=manifest,
                source_type=source_type,
                source_identifier=source_identifier,
                preset_identifier=preset_identifier,
                run_identifier=run_identifier,
                expected_structure_sha256=expected_structure_sha256,
            )
            directory = self._directory(run_identifier)
            _write_json_atomic(directory / "runner-summary.json", output.runner_summary)
            _write_json_atomic(directory / "results.json", output.results)
            _write_json_atomic(directory / "verification.json", output.verification)
            _write_json_atomic(directory / "receipt.json", output.receipt)
            terminal: RunStatus = "authorized" if output.receipt["authorized"] else "rejected"
            self._transition(run_identifier, terminal, {
                "structure_sha256": output.results["structure_sha256"],
                "hamiltonian_sha256": output.results["hamiltonian_sha256"],
                "receipt_sha256": output.results["receipt_sha256"],
                "execution_environment_identity": output.results["execution_environment_identity"],
            })
        except Exception as exc:
            message = _public_error_message(exc)
            error = {"code": "run_execution_failed", "message": message, "type": type(exc).__name__}
            _write_json_atomic(self._directory(run_identifier) / "error.json", error)
            self._transition(run_identifier, "failed", {"error": {"code": error["code"], "message": message}})

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
        source_type: Literal["preset", "dynamic_experiment"],
        source_identifier: str,
        preset_identifier: str | None,
        molecule: dict[str, Any] | None = None,
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

    def _read_validated_state(self, run_identifier: str) -> dict[str, Any]:
        directory = self._directory(run_identifier)
        state = _controlled_run_json(directory, "state.json")
        request = _controlled_run_json(directory, "request.json")
        if state.get("run_identifier") != run_identifier:
            raise ValueError("Persisted run state has a mismatched run identifier.")
        preset_identifier = request.get("preset_identifier")
        experiment_identifier = request.get("experiment_identifier")
        if (isinstance(preset_identifier, str)) == (isinstance(experiment_identifier, str)):
            raise ValueError("Persisted run request has an invalid experiment source.")
        source_identifier = experiment_identifier or preset_identifier
        source_type = "dynamic_experiment" if experiment_identifier is not None else "preset"
        expected_preset = None if experiment_identifier is not None else preset_identifier
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
            _write_json_atomic(directory / "state.json", state)
        if (
            state.get("source_type") != source_type
            or state.get("source_identifier") != source_identifier
            or state.get("preset_identifier") != expected_preset
        ):
            raise ValueError("Persisted run state has a mismatched source identity.")
        if experiment_identifier is not None and state.get("experiment_identifier") != experiment_identifier:
            raise ValueError("Persisted run state has a mismatched experiment identifier.")
        return state

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
                preset_identifier = request.get("preset_identifier")
                experiment_identifier = request.get("experiment_identifier")
                if (isinstance(preset_identifier, str)) == (isinstance(experiment_identifier, str)):
                    continue
                source_identifier = experiment_identifier or preset_identifier
                source_type = "dynamic_experiment" if experiment_identifier is not None else "preset"
                expected_preset = None if experiment_identifier is not None else preset_identifier
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
                            **(
                                {"experiment_identifier": experiment_identifier}
                                if experiment_identifier is not None
                                else {"preset_identifier": preset_identifier}
                            ),
                            "execution_target": request["execution_target"],
                        },
                        directory.name,
                    )
                if state.get("status") in ACTIVE_STATUSES:
                    now = utc_now()
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
    source_type: Literal["preset", "dynamic_experiment"] = "preset",
    source_identifier: str | None = None,
    expected_structure_sha256: str | None = None,
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
    }
    for field, expected_value in summary_comparisons.items():
        if field in output.runner_summary and output.runner_summary[field] != expected_value:
            raise ValueError(f"Executor runner summary {field} is inconsistent.")
    for value in (output.results, output.verification, output.receipt, output.runner_summary):
        assert_public_response_safe(value)
    return ExecutionOutput(
        results.model_dump(mode="json"), verification.model_dump(mode="json"),
        receipt.model_dump(mode="json"), dict(output.runner_summary),
    )
