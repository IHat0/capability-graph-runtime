"""Server-controlled IBM Quantum execution layered on trusted local preflight."""

from __future__ import annotations

import importlib.util
import math
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from cgr.quantum_preflight.artifacts import write_json_atomic
from cgr.quantum_preflight.contracts import ManifestEnvelope
from cgr.science import sha256_fingerprint

from .runs import (
    ExecutionOutput,
    ExistingQuantumPreflightExecutor,
    PresetRunExecutor,
    _BoundedLogCollector,
    _bind_public_source_identity,
    _controlled_json,
    assert_public_response_safe,
    RecoverableIBMJobError,
    TerminalIBMJobError,
    utc_now,
    validate_execution_output,
)

HARDWARE_ROLE = "final_energy_evaluation_at_locally_optimized_parameters"
IBM_SUBMISSION_SCHEMA = "cgr.pulsate-ibm-submission/1.0.0"
IBM_SUBMISSION_ATTEMPT_SCHEMA = "cgr.pulsate-ibm-submission-attempt/1.0.0"
IBM_RESULT_SCHEMA = "cgr.pulsate-ibm-result/1.0.0"
IBM_PREPARED_SUBMISSION_SCHEMA = "cgr.pulsate-ibm-prepared-submission/1.0.0"
IBM_WORKER_FAILURE_SCHEMA = "cgr.pulsate-ibm-worker-failure/1.0.0"
IBM_PRIMITIVE_IDENTIFIER = "EstimatorV2"
IBM_MAXIMUM_QUBITS = 32
IBM_MAXIMUM_CIRCUIT_DEPTH = 100_000
IBM_QUALITY_MAXIMUM_EXACT_DIFFERENCE_HARTREE = 0.05
IBM_QUALITY_MAXIMUM_LOCAL_VQE_DIFFERENCE_HARTREE = 0.05
IBM_QUALITY_MAXIMUM_STANDARD_ERROR_HARTREE = 0.05
IBM_ENERGY_IDENTITY_TOLERANCE_HARTREE = 1e-12
IBM_RETRIEVAL_GRACE_SECONDS = 60
IBM_DEFAULT_TRANSPILER_SEED = 7341
IBM_PREFLIGHT_HANDOFF_SCHEMA = "cgr.pulsate-ibm-preflight-handoff/1.0.0"
IBM_PREFLIGHT_REQUEST_SCHEMA = "cgr.pulsate-ibm-preflight-request/1.0.0"
IBM_PREFLIGHT_LAUNCHER_SCHEMA = "cgr.pulsate-ibm-preflight-launcher/1.0.0"
IBM_PREFLIGHT_LAUNCHER_MAXIMUM_AGE_SECONDS = 10
_IBM_FILE_MAXIMUM_BYTES = 2 * 1024 * 1024
_IBM_JOB_IDENTIFIER = re.compile(r"^[A-Za-z0-9._:-]{3,256}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class IBMQuantumConfiguration:
    token: str | None
    instance: str | None
    backend_name: str | None
    target_precision: float
    optimization_level: int
    maximum_seconds: int
    dependency_available: bool
    seed_transpiler: int = IBM_DEFAULT_TRANSPILER_SEED
    image_identifier: str = "local-uncontainerized"
    backend_qubit_capacity: int | None = None
    configuration_error: str | None = None
    requires_provider_credentials: bool = True

    @classmethod
    def from_environment(cls) -> "IBMQuantumConfiguration":
        error: str | None = None
        try:
            precision = float(os.environ.get("PULSATE_IBM_TARGET_PRECISION", "0.015"))
            if not math.isfinite(precision) or precision <= 0 or precision > 1:
                raise ValueError
        except ValueError:
            precision = 0.015
            error = "PULSATE_IBM_TARGET_PRECISION must be a finite number in (0, 1]."
        try:
            optimization = int(os.environ.get("PULSATE_IBM_OPTIMIZATION_LEVEL", "2"))
            if optimization not in {0, 1, 2, 3}:
                raise ValueError
        except ValueError:
            optimization = 2
            error = error or "PULSATE_IBM_OPTIMIZATION_LEVEL must be between 0 and 3."
        try:
            maximum = int(os.environ.get("PULSATE_IBM_MAXIMUM_SECONDS", "1800"))
            if maximum <= 0 or maximum > 7200:
                raise ValueError
        except ValueError:
            maximum = 1800
            error = error or "PULSATE_IBM_MAXIMUM_SECONDS must be between 1 and 7200."
        try:
            seed_transpiler = int(
                os.environ.get(
                    "PULSATE_IBM_SEED_TRANSPILER",
                    str(IBM_DEFAULT_TRANSPILER_SEED),
                )
            )
            if seed_transpiler < 0 or seed_transpiler > 2**31 - 1:
                raise ValueError
        except ValueError:
            seed_transpiler = IBM_DEFAULT_TRANSPILER_SEED
            error = error or "PULSATE_IBM_SEED_TRANSPILER must be a non-negative 32-bit integer."
        return cls(
            token=os.environ.get("PULSATE_IBM_QUANTUM_TOKEN") or None,
            instance=os.environ.get("PULSATE_IBM_QUANTUM_INSTANCE") or None,
            backend_name=os.environ.get("PULSATE_IBM_QUANTUM_BACKEND") or None,
            target_precision=precision,
            optimization_level=optimization,
            maximum_seconds=maximum,
            dependency_available=importlib.util.find_spec("qiskit_ibm_runtime") is not None,
            seed_transpiler=seed_transpiler,
            image_identifier=os.environ.get(
                "PULSATE_IBM_IMAGE_IDENTIFIER", "local-uncontainerized"
            ),
            configuration_error=error,
        )

    def unavailable_reason(self) -> str | None:
        if self.configuration_error:
            return self.configuration_error
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", self.image_identifier):
            return "PULSATE_IBM_IMAGE_IDENTIFIER must be an exact pinned image ID."
        if self.requires_provider_credentials and not self.token:
            return "PULSATE_IBM_QUANTUM_TOKEN is not configured."
        if self.requires_provider_credentials and not self.instance:
            return "PULSATE_IBM_QUANTUM_INSTANCE is not configured."
        if not self.backend_name:
            return "PULSATE_IBM_QUANTUM_BACKEND is not configured."
        if not self.dependency_available:
            return "The pinned qiskit-ibm-runtime dependency is unavailable."
        return None

    def capability(self) -> dict[str, Any]:
        reason = self.unavailable_reason()
        result = {
            "available": reason is None,
            "backend_name": self.backend_name,
            "reason": reason,
            "maximum_run_seconds": self.maximum_seconds,
            "target_precision": self.target_precision,
            "optimization_level": self.optimization_level,
            "hardware_role": HARDWARE_ROLE,
        }
        assert_public_response_safe(result)
        return result


class IBMSubmissionBundle(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[IBM_SUBMISSION_SCHEMA] = IBM_SUBMISSION_SCHEMA
    bundle_identifier: str
    experiment_identifier: str
    experiment_sha256: str
    structure_sha256: str
    hamiltonian_sha256: str
    ansatz_sha256: str
    optimized_parameters: tuple[float, ...]
    optimized_parameters_sha256: str
    source_bound_circuit_sha256: str
    source_observable_sha256: str
    required_qubits: int = Field(gt=0)
    circuit_depth: int = Field(ge=0)
    backend_name: str
    target_precision: float = Field(gt=0, le=1)
    optimization_level: int = Field(ge=0, le=3)
    seed_transpiler: int = Field(
        default=IBM_DEFAULT_TRANSPILER_SEED, ge=0, le=2**31 - 1
    )
    maximum_execution_time_seconds: int = Field(gt=0, le=7200)
    job_correlation_identifier: str
    ibm_runtime_image_identifier: str
    hardware_role: Literal[HARDWARE_ROLE] = HARDWARE_ROLE

    @field_validator("optimized_parameters")
    @classmethod
    def finite_parameters(cls, values: tuple[float, ...]) -> tuple[float, ...]:
        if not values or not all(math.isfinite(value) for value in values):
            raise ValueError("Optimized parameters must be a non-empty finite vector.")
        return values

    @property
    def bundle_sha256(self) -> str:
        return sha256_fingerprint(self.model_dump(mode="json"))


class IBMRuntimeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[IBM_RESULT_SCHEMA] = IBM_RESULT_SCHEMA
    bundle_sha256: str
    job_identifier: str
    backend_name: str
    primitive_identifier: Literal[IBM_PRIMITIVE_IDENTIFIER] = IBM_PRIMITIVE_IDENTIFIER
    primitive_version: str
    submitted_at: str
    completed_at: str
    job_status: Literal["completed"]
    target_precision: float
    raw_qubit_expectation_hartree: float
    non_nuclear_electronic_shift_hartree: float
    electronic_constant_offsets_hartree: dict[str, float]
    nuclear_repulsion_energy_hartree: float
    ibm_electronic_energy_hartree: float
    standard_error: float | None = None
    execution_metadata: dict[str, Any]
    optimization_level: int
    layout_sha256: str
    physical_qubits: tuple[int, ...]
    source_bound_circuit_sha256: str
    transpiled_circuit_sha256: str
    source_observable_sha256: str
    transpiled_observable_sha256: str
    optimized_parameters_sha256: str
    experiment_sha256: str
    structure_sha256: str
    hamiltonian_sha256: str
    package_versions: dict[str, str]
    runtime_options: dict[str, Any]
    execution_image_identifier: str

    @field_validator("electronic_constant_offsets_hartree")
    @classmethod
    def bounded_finite_offsets(cls, values: dict[str, float]) -> dict[str, float]:
        if len(values) > 64:
            raise ValueError("IBM electronic constant offsets are oversized.")
        for key, value in values.items():
            if (
                not key
                or len(key) > 128
                or key == "nuclear_repulsion_energy"
                or not math.isfinite(value)
            ):
                raise ValueError("IBM electronic constant offsets are invalid.")
        return values


class IBMPreparedSubmissionEvidence(BaseModel):
    """Identity of the exact ISA publication persisted before submission."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[IBM_PREPARED_SUBMISSION_SCHEMA] = (
        IBM_PREPARED_SUBMISSION_SCHEMA
    )
    bundle_sha256: str
    source_bound_circuit_sha256: str
    transpiled_circuit_sha256: str
    source_observable_sha256: str
    transpiled_observable_sha256: str
    physical_qubits: tuple[int, ...]
    layout_sha256: str
    seed_transpiler: int = Field(ge=0, le=2**31 - 1)
    optimization_level: int = Field(ge=0, le=3)
    backend_name: str
    backend_target_sha256: str | None = None
    qiskit_version: str
    qpy_filename: Literal["prepared-isa-circuit.qpy"] = "prepared-isa-circuit.qpy"
    observable_filename: Literal["prepared-isa-observable.json"] = (
        "prepared-isa-observable.json"
    )
    observable_file_sha256: str

    @field_validator(
        "bundle_sha256",
        "source_bound_circuit_sha256",
        "transpiled_circuit_sha256",
        "source_observable_sha256",
        "transpiled_observable_sha256",
        "layout_sha256",
        "observable_file_sha256",
    )
    @classmethod
    def sha256_value(cls, value: str) -> str:
        if not _SHA256.fullmatch(value):
            raise ValueError("Prepared IBM evidence contains an invalid SHA-256 value.")
        return value

    @field_validator("backend_target_sha256")
    @classmethod
    def optional_sha256_value(cls, value: str | None) -> str | None:
        if value is not None and not _SHA256.fullmatch(value):
            raise ValueError("Prepared IBM backend target identity is invalid.")
        return value

    @field_validator("physical_qubits")
    @classmethod
    def distinct_qubits(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if not value or min(value) < 0 or len(set(value)) != len(value):
            raise ValueError("Prepared IBM evidence contains an invalid layout.")
        return value


class IBMWorkerFailureEnvelope(BaseModel):
    """Bounded, provider-text-free failure state crossing the worker boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[IBM_WORKER_FAILURE_SCHEMA] = IBM_WORKER_FAILURE_SCHEMA
    category: Literal[
        "pre_submission_failure",
        "submission_indeterminate",
        "transient_service_failure",
        "transient_status_failure",
        "transient_result_failure",
        "terminal_job_failure",
        "prepared_evidence_failure",
    ]
    job_identifier_persisted: bool
    job_identifier: str | None = None
    last_controlled_ibm_status: Literal[
        "QUEUED",
        "INITIALIZING",
        "RUNNING",
        "COMPLETED",
        "CANCELLED",
        "ERROR",
        "FAILED",
        "UNKNOWN",
    ] | None = None
    retrieval_recoverable: bool

    @field_validator("job_identifier")
    @classmethod
    def valid_job_identifier(cls, value: str | None) -> str | None:
        if value is not None and not _IBM_JOB_IDENTIFIER.fullmatch(value):
            raise ValueError("IBM worker failure contains an invalid job identifier.")
        return value

    @model_validator(mode="after")
    def consistent_state(self) -> "IBMWorkerFailureEnvelope":
        if self.job_identifier_persisted != (self.job_identifier is not None):
            raise ValueError("IBM worker failure job persistence state is inconsistent.")
        if self.retrieval_recoverable and not self.job_identifier_persisted:
            raise ValueError("IBM worker failure cannot be recoverable without a job.")
        if (
            self.last_controlled_ibm_status in {"CANCELLED", "ERROR", "FAILED"}
            and self.retrieval_recoverable
        ):
            raise ValueError("A terminal IBM job failure cannot be recoverable.")
        return self


class IBMSubmissionAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[IBM_SUBMISSION_ATTEMPT_SCHEMA] = (
        IBM_SUBMISSION_ATTEMPT_SCHEMA
    )
    bundle_sha256: str
    bundle_identifier: str
    backend_name: str
    submission_state: Literal["submission_started", "job_identifier_persisted"]
    created_at: str
    job_correlation_identifier: str
    job_identifier: str | None = None


class IBMRuntimeAdapter(Protocol):
    def execute(
        self,
        bundle: IBMSubmissionBundle,
        manifest: ManifestEnvelope,
        *,
        work_directory: Path,
        job_record_path: Path,
        maximum_seconds: int,
        status_callback: Callable[[str, dict[str, Any] | None], None],
    ) -> IBMRuntimeResult: ...


class PersistedIBMPreflightHandoffExecutor:
    """Consume only preflight produced by a separate OS-isolated container."""

    # A passive directory reader is not an operational isolated launcher and
    # must never make API capability available by itself.
    proven_no_network = False

    def __init__(self, handoff_root: Path) -> None:
        self.handoff_root = Path(handoff_root)

    def unavailable_reason(self) -> str | None:
        try:
            metadata = self.handoff_root.lstat()
        except FileNotFoundError:
            return "The isolated IBM preflight handoff directory is unavailable."
        if (
            self.handoff_root.is_symlink()
            or not self.handoff_root.is_dir()
            or metadata.st_size < 0
        ):
            return "The isolated IBM preflight handoff directory is invalid."
        return None

    def execute(
        self,
        manifest: ManifestEnvelope,
        *,
        preset_identifier: str,
        run_directory: Path,
        maximum_seconds: int,
    ) -> ExecutionOutput:
        del preset_identifier, maximum_seconds
        reason = self.unavailable_reason()
        if reason is not None:
            raise RuntimeError(reason)
        if re.fullmatch(r"run-[0-9a-f]{32}", run_directory.name) is None:
            raise ValueError("IBM preflight handoff run identity is invalid.")
        filename = f"{run_directory.name}.json"
        document = _controlled_json(
            self.handoff_root.resolve(strict=True),
            filename,
            maximum_bytes=_IBM_FILE_MAXIMUM_BYTES,
        )
        if (
            document.get("schema_version") != IBM_PREFLIGHT_HANDOFF_SCHEMA
            or document.get("experiment_sha256")
            != manifest.experiment.fingerprint
            or document.get("run_identifier") != run_directory.name
            or document.get("network_boundary") != "docker_network_none"
            or document.get("network_disabled") is not True
            or not isinstance(document.get("network_namespace_sha256"), str)
            or not _SHA256.fullmatch(document["network_namespace_sha256"])
            or not isinstance(document.get("scientific_image_identifier"), str)
            or document["scientific_image_identifier"] == "local-uncontainerized"
        ):
            raise ValueError("Isolated IBM preflight handoff attestation is invalid.")
        output = document.get("output")
        if not isinstance(output, dict):
            raise ValueError("Isolated IBM preflight handoff output is malformed.")
        return ExecutionOutput(
            results=dict(output["results"]),
            verification=dict(output["verification"]),
            receipt=dict(output["receipt"]),
            runner_summary=dict(output["runner_summary"]),
        )


class IBMRunBoundPreflightRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[IBM_PREFLIGHT_REQUEST_SCHEMA] = (
        IBM_PREFLIGHT_REQUEST_SCHEMA
    )
    run_identifier: str
    preset_identifier: str
    manifest: dict[str, Any]
    maximum_seconds: int = Field(gt=0, le=7200)
    scientific_preflight_image_identifier: str
    ibm_runtime_image_identifier: str

    @field_validator("run_identifier")
    @classmethod
    def valid_run_identifier(cls, value: str) -> str:
        if re.fullmatch(r"run-[0-9a-f]{32}", value) is None:
            raise ValueError("IBM preflight request run identity is invalid.")
        return value

    @field_validator(
        "scientific_preflight_image_identifier",
        "ibm_runtime_image_identifier",
    )
    @classmethod
    def exact_image_identifier(cls, value: str) -> str:
        if re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
            raise ValueError("IBM preflight request image identity is not exact.")
        return value

    @model_validator(mode="after")
    def distinct_images(self) -> "IBMRunBoundPreflightRequest":
        if (
            self.scientific_preflight_image_identifier
            == self.ibm_runtime_image_identifier
        ):
            raise ValueError("Scientific and IBM Runtime images must be distinct.")
        return self


class RunBoundIsolatedIBMPreflightExecutor:
    """Request and validate preflight from a live no-network coordinator."""

    proven_no_network = True

    def __init__(
        self,
        exchange_root: Path,
        *,
        scientific_preflight_image_identifier: str,
        ibm_runtime_image_identifier: str,
    ) -> None:
        self.exchange_root = Path(exchange_root)
        self.scientific_preflight_image_identifier = (
            scientific_preflight_image_identifier
        )
        self.ibm_runtime_image_identifier = ibm_runtime_image_identifier

    def _root(self) -> Path:
        metadata = self.exchange_root.lstat()
        root = self.exchange_root.resolve(strict=True)
        if self.exchange_root.is_symlink() or not root.is_dir() or metadata.st_size < 0:
            raise ValueError("The isolated IBM preflight exchange root is invalid.")
        return root

    def unavailable_reason(self) -> str | None:
        if re.fullmatch(
            r"sha256:[0-9a-f]{64}",
            self.scientific_preflight_image_identifier,
        ) is None:
            return "The exact scientific preflight image identity is unavailable."
        if re.fullmatch(
            r"sha256:[0-9a-f]{64}", self.ibm_runtime_image_identifier
        ) is None:
            return "The exact IBM Runtime image identity is unavailable."
        if (
            self.scientific_preflight_image_identifier
            == self.ibm_runtime_image_identifier
        ):
            return "Scientific preflight and IBM Runtime images must be distinct."
        try:
            root = self._root()
            readiness = _controlled_json(
                root,
                "launcher-readiness.json",
                maximum_bytes=100_000,
            )
            observed_at = float(readiness.get("observed_at_epoch", 0.0))
        except (FileNotFoundError, OSError, ValueError, TypeError):
            return "The run-bound no-network preflight launcher is unavailable."
        if (
            readiness.get("schema_version") != IBM_PREFLIGHT_LAUNCHER_SCHEMA
            or readiness.get("launcher_mode")
            != "run_bound_file_coordinator"
            or readiness.get("network_boundary") != "docker_network_none"
            or readiness.get("network_disabled") is not True
            or readiness.get("scientific_preflight_image_identifier")
            != self.scientific_preflight_image_identifier
            or readiness.get("ibm_runtime_image_identifier")
            != self.ibm_runtime_image_identifier
            or time.time() - observed_at
            > IBM_PREFLIGHT_LAUNCHER_MAXIMUM_AGE_SECONDS
            or observed_at > time.time() + 1
        ):
            return "The run-bound no-network preflight launcher is not operational."
        return None

    def execute(
        self,
        manifest: ManifestEnvelope,
        *,
        preset_identifier: str,
        run_directory: Path,
        maximum_seconds: int,
    ) -> ExecutionOutput:
        reason = self.unavailable_reason()
        if reason is not None:
            raise RuntimeError(reason)
        run_identifier = run_directory.name
        if (
            re.fullmatch(r"run-[0-9a-f]{32}", run_identifier) is None
            or run_directory.is_symlink()
            or not run_directory.is_dir()
        ):
            raise ValueError("IBM preflight run directory is invalid.")
        root = self._root()
        requests = root / "requests"
        handoffs = root / "handoffs"
        for directory in (requests, handoffs):
            if (
                directory.is_symlink()
                or not directory.is_dir()
                or directory.resolve(strict=True).parent != root
            ):
                raise ValueError("IBM preflight exchange directory is invalid.")
        request_path = requests / f"{run_identifier}.json"
        handoff_path = handoffs / f"{run_identifier}.json"
        request = IBMRunBoundPreflightRequest(
            run_identifier=run_identifier,
            preset_identifier=preset_identifier,
            manifest=manifest.model_dump(mode="json"),
            maximum_seconds=maximum_seconds,
            scientific_preflight_image_identifier=(
                self.scientific_preflight_image_identifier
            ),
            ibm_runtime_image_identifier=self.ibm_runtime_image_identifier,
        )
        if not request_path.exists():
            write_json_atomic(
                request_path,
                request.model_dump(mode="json"),
                maximum_bytes=_IBM_FILE_MAXIMUM_BYTES,
            )
        else:
            persisted_request = IBMRunBoundPreflightRequest.model_validate(
                _controlled_json(
                    requests,
                    request_path.name,
                    maximum_bytes=_IBM_FILE_MAXIMUM_BYTES,
                )
            )
            if persisted_request != request:
                raise ValueError("Persisted IBM preflight request identity mismatch.")

        deadline = time.monotonic() + maximum_seconds
        while not handoff_path.is_file() or handoff_path.is_symlink():
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    "The run-bound no-network preflight handoff timed out."
                )
            reason = self.unavailable_reason()
            if reason is not None:
                raise RuntimeError(reason)
            time.sleep(0.05)
        document = _controlled_json(
            handoffs,
            handoff_path.name,
            maximum_bytes=_IBM_FILE_MAXIMUM_BYTES,
        )
        if (
            document.get("schema_version") != IBM_PREFLIGHT_HANDOFF_SCHEMA
            or document.get("experiment_sha256")
            != manifest.experiment.fingerprint
            or document.get("run_identifier") != run_identifier
            or document.get("network_boundary") != "docker_network_none"
            or document.get("network_disabled") is not True
            or document.get("scientific_preflight_image_identifier")
            != self.scientific_preflight_image_identifier
            or document.get("ibm_runtime_image_identifier")
            != self.ibm_runtime_image_identifier
            or self.scientific_preflight_image_identifier
            == self.ibm_runtime_image_identifier
        ):
            raise ValueError("Run-bound IBM preflight handoff identity mismatch.")
        output = document.get("output")
        if not isinstance(output, dict):
            raise ValueError("Run-bound IBM preflight handoff output is malformed.")
        runner_summary = dict(output["runner_summary"])
        preflight = runner_summary.get("ibm_preflight")
        if not isinstance(preflight, dict):
            raise ValueError("Run-bound IBM preflight evidence is unavailable.")
        runner_summary["ibm_preflight"] = {
            **preflight,
            "scientific_preflight_image_identifier": (
                self.scientific_preflight_image_identifier
            ),
            "ibm_runtime_image_identifier": self.ibm_runtime_image_identifier,
        }
        return ExecutionOutput(
            results=dict(output["results"]),
            verification=dict(output["verification"]),
            receipt=dict(output["receipt"]),
            runner_summary=runner_summary,
        )


class UnavailableIBMPreflightExecutor:
    proven_no_network = False

    @staticmethod
    def unavailable_reason() -> str:
        return "An OS-isolated IBM local-preflight handoff is not configured."

    def execute(self, *args: Any, **kwargs: Any) -> ExecutionOutput:
        del args, kwargs
        raise RuntimeError(self.unavailable_reason())


class IBMQuantumRunExecutor:
    """Require authorized local evidence before one idempotent IBM evaluation."""

    def __init__(
        self,
        *,
        local_executor: PresetRunExecutor,
        adapter: IBMRuntimeAdapter,
        configuration: IBMQuantumConfiguration,
    ) -> None:
        self.local_executor = local_executor
        self.adapter = adapter
        self.configuration = configuration

    def capability(self) -> dict[str, Any]:
        result = self.configuration.capability()
        boundary_reason = (
            self.local_executor.unavailable_reason()
            if hasattr(self.local_executor, "unavailable_reason")
            else None
        )
        if getattr(self.local_executor, "proven_no_network", False) is not True:
            boundary_reason = (
                boundary_reason
                or "An OS-isolated IBM local-preflight handoff is not configured."
            )
        if boundary_reason is not None:
            result = {**result, "available": False, "reason": boundary_reason}
        assert_public_response_safe(result)
        return result

    def execute(
        self,
        manifest: ManifestEnvelope,
        *,
        preset_identifier: str,
        run_directory: Path,
        maximum_seconds: int,
        status_callback: Callable[[str, dict[str, Any] | None], None],
        source_type: Literal["preset", "dynamic_experiment"] = "preset",
        source_identifier: str | None = None,
        source_preset_identifier: str | None = None,
        expected_structure_sha256: str | None = None,
        run_identifier: str | None = None,
    ) -> ExecutionOutput:
        capability = self.capability()
        reason = capability.get("reason") if capability.get("available") is not True else None
        if reason is not None:
            raise RuntimeError(reason)
        ibm_directory = run_directory / "ibm-worker"
        ibm_directory.mkdir(mode=0o700, exist_ok=True)
        if (
            ibm_directory.is_symlink()
            or not ibm_directory.is_dir()
            or ibm_directory.resolve(strict=True).parent != run_directory.resolve(strict=True)
        ):
            raise ValueError("IBM worker directory escaped the controlled run directory.")
        local_preflight_path = ibm_directory / "local-preflight.json"
        effective_source_identifier = source_identifier or preset_identifier
        effective_preset_identifier = (
            source_preset_identifier
            if source_type == "dynamic_experiment"
            else (source_preset_identifier or preset_identifier)
        )
        effective_run_identifier = run_identifier or run_directory.name
        if local_preflight_path.exists():
            persisted = _controlled_json(
                ibm_directory,
                local_preflight_path.name,
                maximum_bytes=_IBM_FILE_MAXIMUM_BYTES,
            )
            persisted_payload = {
                key: persisted[key]
                for key in ("results", "verification", "receipt", "runner_summary")
            }
            if (
                persisted.get("local_preflight_sha256")
                != sha256_fingerprint(persisted_payload)
            ):
                raise ValueError("Persisted local preflight envelope identity mismatch.")
            local = ExecutionOutput(
                results=dict(persisted["results"]),
                verification=dict(persisted["verification"]),
                receipt=dict(persisted["receipt"]),
                runner_summary=dict(persisted["runner_summary"]),
            )
            local = self._validate_local_preflight(
                local,
                manifest=manifest,
                source_type=source_type,
                source_identifier=effective_source_identifier,
                preset_identifier=effective_preset_identifier,
                run_identifier=effective_run_identifier,
                expected_structure_sha256=expected_structure_sha256,
            )
        else:
            local = self.local_executor.execute(
                manifest,
                preset_identifier=preset_identifier,
                run_directory=run_directory,
                maximum_seconds=maximum_seconds,
            )
            local = _bind_public_source_identity(
                local,
                source_type=source_type,
                source_identifier=effective_source_identifier,
                preset_identifier=effective_preset_identifier,
            )
            local = self._validate_local_preflight(
                local,
                manifest=manifest,
                source_type=source_type,
                source_identifier=effective_source_identifier,
                preset_identifier=effective_preset_identifier,
                run_identifier=effective_run_identifier,
                expected_structure_sha256=expected_structure_sha256,
            )
            local_payload = {
                "results": local.results,
                "verification": local.verification,
                "receipt": local.receipt,
                "runner_summary": local.runner_summary,
            }
            write_json_atomic(
                local_preflight_path,
                {
                    **local_payload,
                    "local_preflight_sha256": sha256_fingerprint(local_payload),
                },
                maximum_bytes=_IBM_FILE_MAXIMUM_BYTES,
            )
        if local.receipt.get("authorized") is not True:
            return self._blocked_preflight(local)

        self._validate_preflight_image_identities(local)
        bundle = self._bundle(manifest, local)
        self._enforce_submission_policy(bundle)
        write_json_atomic(
            ibm_directory / "submission.json",
            bundle.model_dump(mode="json"),
            maximum_bytes=_IBM_FILE_MAXIMUM_BYTES,
        )
        job_record = ibm_directory / "job.json"
        status_callback("awaiting_ibm_submission", {"ibm_backend_name": bundle.backend_name})
        result = self.adapter.execute(
            bundle,
            manifest,
            work_directory=ibm_directory,
            job_record_path=job_record,
            maximum_seconds=(
                self.configuration.maximum_seconds + IBM_RETRIEVAL_GRACE_SECONDS
            ),
            status_callback=status_callback,
        )
        record = _controlled_json(ibm_directory, "job.json", maximum_bytes=_IBM_FILE_MAXIMUM_BYTES)
        if (
            record.get("bundle_sha256") != bundle.bundle_sha256
            or record.get("job_identifier") != result.job_identifier
        ):
            raise ValueError("Persisted IBM job identity does not match the result.")
        status_callback(
            "verifying_ibm_result",
            {"ibm_job_identifier": result.job_identifier, "ibm_backend_name": result.backend_name},
        )
        evidence = self._verify_result(bundle, local, result)
        return self._augment(local, evidence)

    @staticmethod
    def _validate_local_preflight(
        local: ExecutionOutput,
        *,
        manifest: ManifestEnvelope,
        source_type: Literal["preset", "dynamic_experiment"],
        source_identifier: str,
        preset_identifier: str | None,
        run_identifier: str,
        expected_structure_sha256: str | None,
    ) -> ExecutionOutput:
        validated = validate_execution_output(
            local,
            manifest=manifest,
            source_type=source_type,
            source_identifier=source_identifier,
            preset_identifier=preset_identifier,
            run_identifier=run_identifier,
            expected_structure_sha256=expected_structure_sha256,
        )
        if validated.receipt.get("authorized") is True:
            preflight = validated.runner_summary.get("ibm_preflight")
            if (
                not isinstance(preflight, dict)
                or preflight.get("artifact_lineage_validated") is not True
            ):
                raise ValueError(
                    "Trusted local preflight did not prove authoritative artifact lineage."
                )
            for field in (
                "exact_total_energy_hartree",
                "vqe_total_energy_hartree",
                "absolute_difference_hartree",
                "tolerance_hartree",
            ):
                if field not in validated.runner_summary:
                    raise ValueError(
                        f"Trusted local preflight did not retain {field} evidence."
                    )
            if (
                validated.runner_summary.get("optimized_parameters_sha256")
                != preflight.get("optimized_parameters_sha256")
            ):
                raise ValueError(
                    "Trusted local preflight optimized parameters disagree with its authoritative summary."
                )
            ansatz_artifacts = [
                artifact
                for artifact in validated.receipt.get("artifacts", [])
                if artifact.get("artifact_identifier") == "ansatz_manifest"
            ]
            if (
                len(ansatz_artifacts) != 1
                or ansatz_artifacts[0].get("content_sha256")
                != preflight.get("ansatz_sha256")
            ):
                raise ValueError(
                    "Trusted local preflight ansatz identity disagrees with its receipt lineage."
                )
        return validated

    def _validate_preflight_image_identities(self, local: ExecutionOutput) -> None:
        preflight = local.runner_summary.get("ibm_preflight")
        if not isinstance(preflight, dict):
            raise ValueError("Trusted local preflight image evidence is unavailable.")
        scientific = preflight.get("scientific_preflight_image_identifier")
        ibm_runtime = preflight.get("ibm_runtime_image_identifier")
        if (
            not isinstance(scientific, str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", scientific) is None
            or ibm_runtime != self.configuration.image_identifier
            or scientific == ibm_runtime
        ):
            raise ValueError("Trusted preflight image identities are invalid.")

    def _bundle(self, manifest: ManifestEnvelope, local: ExecutionOutput) -> IBMSubmissionBundle:
        preflight = local.runner_summary.get("ibm_preflight")
        if not isinstance(preflight, dict):
            raise ValueError("Trusted local preflight did not provide IBM submission evidence.")
        parameters = preflight.get("optimized_parameters")
        if not isinstance(parameters, list):
            raise ValueError("Trusted local preflight did not provide optimized parameters.")
        parameter_sha = sha256_fingerprint([float(value) for value in parameters])
        if parameter_sha != preflight.get("optimized_parameters_sha256"):
            raise ValueError("Optimized parameter fingerprint mismatch.")
        source_bound_circuit_sha = str(
            preflight.get("source_bound_circuit_sha256", "")
        )
        source_observable_sha = str(preflight.get("source_observable_sha256", ""))
        if not (
            _SHA256.fullmatch(source_bound_circuit_sha)
            and _SHA256.fullmatch(source_observable_sha)
        ):
            raise ValueError(
                "Trusted local preflight did not provide canonical circuit and observable identities."
            )
        bundle_identifier = "ibm-submission-" + sha256_fingerprint(
            {
                "experiment_sha256": manifest.experiment.fingerprint,
                "local_receipt_sha256": local.receipt["receipt_sha256"],
                "backend_name": self.configuration.backend_name,
                "target_precision": self.configuration.target_precision,
                "optimization_level": self.configuration.optimization_level,
            }
        )[:32]
        job_correlation_identifier = "pulsate-" + sha256_fingerprint(
            {"bundle_identifier": bundle_identifier}
        )[:40]
        return IBMSubmissionBundle(
            bundle_identifier=bundle_identifier,
            experiment_identifier=manifest.experiment.experiment_identifier,
            experiment_sha256=manifest.experiment.fingerprint,
            structure_sha256=local.results["structure_sha256"],
            hamiltonian_sha256=local.results["hamiltonian_sha256"],
            ansatz_sha256=str(preflight.get("ansatz_sha256")),
            optimized_parameters=tuple(float(value) for value in parameters),
            optimized_parameters_sha256=parameter_sha,
            source_bound_circuit_sha256=source_bound_circuit_sha,
            source_observable_sha256=source_observable_sha,
            required_qubits=int(preflight.get("number_of_qubits")),
            circuit_depth=int(preflight.get("circuit_depth")),
            backend_name=str(self.configuration.backend_name),
            target_precision=self.configuration.target_precision,
            optimization_level=self.configuration.optimization_level,
            seed_transpiler=self.configuration.seed_transpiler,
            maximum_execution_time_seconds=self.configuration.maximum_seconds,
            job_correlation_identifier=job_correlation_identifier,
            ibm_runtime_image_identifier=self.configuration.image_identifier,
        )

    def _enforce_submission_policy(self, bundle: IBMSubmissionBundle) -> None:
        maximum_qubits = self.configuration.backend_qubit_capacity or IBM_MAXIMUM_QUBITS
        if bundle.required_qubits > maximum_qubits:
            raise ValueError("The authorized circuit exceeds the configured backend qubit capacity.")
        if bundle.circuit_depth > IBM_MAXIMUM_CIRCUIT_DEPTH:
            raise ValueError("The authorized circuit exceeds the IBM circuit-depth policy.")

    @staticmethod
    def _blocked_preflight(local: ExecutionOutput) -> ExecutionOutput:
        evidence = {
            "hardware_role": HARDWARE_ROLE,
            "submission_status": "blocked_by_local_preflight",
            "execution_integrity_passed": False,
            "scientific_quality_passed": False,
            "job_identifier": None,
            "backend_name": None,
        }
        return IBMQuantumRunExecutor._augment(local, evidence, preserve_authorization=True)

    @staticmethod
    def _verify_result(
        bundle: IBMSubmissionBundle,
        local: ExecutionOutput,
        result: IBMRuntimeResult,
    ) -> dict[str, Any]:
        equalities = {
            "bundle_sha256": bundle.bundle_sha256,
            "backend_name": bundle.backend_name,
            "target_precision": bundle.target_precision,
            "optimization_level": bundle.optimization_level,
            "source_bound_circuit_sha256": bundle.source_bound_circuit_sha256,
            "source_observable_sha256": bundle.source_observable_sha256,
            "optimized_parameters_sha256": bundle.optimized_parameters_sha256,
            "experiment_sha256": bundle.experiment_sha256,
            "structure_sha256": bundle.structure_sha256,
            "hamiltonian_sha256": bundle.hamiltonian_sha256,
            "execution_image_identifier": bundle.ibm_runtime_image_identifier,
        }
        for field, expected in equalities.items():
            if getattr(result, field) != expected:
                raise ValueError(f"IBM result {field} mismatch.")
        if not _IBM_JOB_IDENTIFIER.fullmatch(result.job_identifier):
            raise ValueError("IBM result contains an invalid job identifier.")
        energy_values = (
            result.raw_qubit_expectation_hartree,
            result.non_nuclear_electronic_shift_hartree,
            result.nuclear_repulsion_energy_hartree,
            result.ibm_electronic_energy_hartree,
        )
        if not all(math.isfinite(value) for value in energy_values):
            raise ValueError("IBM Runtime returned non-finite energy evidence.")
        if result.standard_error is not None and (
            not math.isfinite(result.standard_error) or result.standard_error < 0
        ):
            raise ValueError("IBM Runtime returned an invalid standard error.")
        expected_runtime_options = {
            "max_execution_time": bundle.maximum_execution_time_seconds,
            "job_tags": [bundle.job_correlation_identifier],
        }
        if result.runtime_options != expected_runtime_options:
            raise ValueError("IBM Runtime effective options do not match the immutable bundle.")
        if not result.physical_qubits or len(set(result.physical_qubits)) != len(result.physical_qubits):
            raise ValueError("IBM result contains an invalid physical-qubit layout.")
        if result.layout_sha256 != sha256_fingerprint(
            {"physical_qubits": result.physical_qubits}
        ):
            raise ValueError("IBM result physical-qubit layout identity mismatch.")
        for field in (
            "source_bound_circuit_sha256",
            "transpiled_circuit_sha256",
            "source_observable_sha256",
            "transpiled_observable_sha256",
            "layout_sha256",
        ):
            if not _SHA256.fullmatch(getattr(result, field)):
                raise ValueError(f"IBM result contains an invalid {field} identity.")
        preflight = local.runner_summary["ibm_preflight"]
        scientific_image_identifier = str(
            preflight["scientific_preflight_image_identifier"]
        )
        nuclear = float(preflight["nuclear_repulsion_energy_hartree"])
        if abs(result.nuclear_repulsion_energy_hartree - nuclear) > IBM_ENERGY_IDENTITY_TOLERANCE_HARTREE:
            raise ValueError("IBM worker nuclear repulsion disagrees with trusted local preflight.")
        offset_sum = math.fsum(result.electronic_constant_offsets_hartree.values())
        if abs(offset_sum - result.non_nuclear_electronic_shift_hartree) > IBM_ENERGY_IDENTITY_TOLERANCE_HARTREE:
            raise ValueError("IBM worker electronic constant offsets are inconsistent.")
        expected_electronic = (
            result.raw_qubit_expectation_hartree
            + result.non_nuclear_electronic_shift_hartree
        )
        if abs(expected_electronic - result.ibm_electronic_energy_hartree) > IBM_ENERGY_IDENTITY_TOLERANCE_HARTREE:
            raise ValueError("IBM worker electronic energy reconstruction is inconsistent.")
        ibm_total = result.ibm_electronic_energy_hartree + nuclear
        exact = float(local.results["exact_total_energy_hartree"])
        vqe = float(local.results["vqe_total_energy_hartree"])
        exact_difference = abs(ibm_total - exact)
        vqe_difference = abs(ibm_total - vqe)
        quality = (
            exact_difference <= IBM_QUALITY_MAXIMUM_EXACT_DIFFERENCE_HARTREE
            and vqe_difference <= IBM_QUALITY_MAXIMUM_LOCAL_VQE_DIFFERENCE_HARTREE
            and result.standard_error is not None
            and result.standard_error <= IBM_QUALITY_MAXIMUM_STANDARD_ERROR_HARTREE
        )
        evidence = {
            "submission_status": "completed",
            "hardware_role": HARDWARE_ROLE,
            "job_identifier": result.job_identifier,
            "backend_name": result.backend_name,
            "primitive_identifier": result.primitive_identifier,
            "primitive_version": result.primitive_version,
            "submitted_at": result.submitted_at,
            "completed_at": result.completed_at,
            "job_status": result.job_status,
            "target_precision": result.target_precision,
            "raw_qubit_expectation_hartree": result.raw_qubit_expectation_hartree,
            "non_nuclear_electronic_shift_hartree": result.non_nuclear_electronic_shift_hartree,
            "electronic_constant_offsets_hartree": result.electronic_constant_offsets_hartree,
            "returned_standard_error": result.standard_error,
            "execution_metadata": result.execution_metadata,
            "transpilation_optimization_level": result.optimization_level,
            "physical_qubits": list(result.physical_qubits),
            "layout_sha256": result.layout_sha256,
            "source_bound_circuit_sha256": result.source_bound_circuit_sha256,
            "transpiled_circuit_sha256": result.transpiled_circuit_sha256,
            "source_observable_sha256": result.source_observable_sha256,
            "transpiled_observable_sha256": result.transpiled_observable_sha256,
            "optimized_parameters_sha256": result.optimized_parameters_sha256,
            "experiment_sha256": result.experiment_sha256,
            "structure_sha256": result.structure_sha256,
            "hamiltonian_sha256": result.hamiltonian_sha256,
            "ibm_electronic_energy_hartree": result.ibm_electronic_energy_hartree,
            "nuclear_repulsion_energy_hartree": nuclear,
            "ibm_total_energy_hartree": ibm_total,
            "local_exact_total_energy_hartree": exact,
            "local_vqe_total_energy_hartree": vqe,
            "hardware_minus_exact_hartree": ibm_total - exact,
            "hardware_minus_local_vqe_hartree": ibm_total - vqe,
            "package_versions": result.package_versions,
            "runtime_options": result.runtime_options,
            "execution_image_identifier": result.execution_image_identifier,
            "scientific_preflight_image_identifier": scientific_image_identifier,
            "ibm_runtime_image_identifier": result.execution_image_identifier,
            "execution_integrity_passed": True,
            "scientific_quality_passed": quality,
            "scientific_quality_policy": {
                "maximum_exact_difference_hartree": IBM_QUALITY_MAXIMUM_EXACT_DIFFERENCE_HARTREE,
                "maximum_local_vqe_difference_hartree": IBM_QUALITY_MAXIMUM_LOCAL_VQE_DIFFERENCE_HARTREE,
                "maximum_standard_error_hartree": IBM_QUALITY_MAXIMUM_STANDARD_ERROR_HARTREE,
                "observed_exact_difference_hartree": exact_difference,
                "observed_local_vqe_difference_hartree": vqe_difference,
            },
        }
        evidence["ibm_receipt_sha256"] = sha256_fingerprint(
            {"local_receipt_sha256": local.receipt["receipt_sha256"], "ibm_execution": evidence}
        )
        assert_public_response_safe(evidence)
        return evidence

    @staticmethod
    def _augment(
        local: ExecutionOutput,
        evidence: dict[str, Any],
        preserve_authorization: bool = False,
    ) -> ExecutionOutput:
        results = {**local.results, "ibm_execution": evidence}
        verification = {**local.verification, "ibm_execution": evidence}
        receipt = {**local.receipt, "ibm_execution": evidence}
        if not preserve_authorization:
            passed = bool(
                local.receipt.get("authorized")
                and evidence.get("execution_integrity_passed")
                and evidence.get("scientific_quality_passed")
            )
            verification.update(
                {
                    "verification_passed": passed,
                    "authorization_state": "authorized" if passed else "rejected",
                }
            )
            if not passed:
                verification["blocking_findings"] = [
                    *verification.get("blocking_findings", []),
                    {
                        "finding_identifier": "ibm.scientific_quality",
                        "message": "IBM hardware evidence did not satisfy the server scientific-quality policy.",
                        "blocking": True,
                    },
                ]
            receipt.update(
                {
                    "verification_passed": passed,
                    "authorization_state": "authorized" if passed else "rejected",
                    "authorized": passed,
                }
            )
        summary = {**local.runner_summary, "ibm_execution": evidence}
        for value in (results, verification, receipt, summary):
            assert_public_response_safe(value)
        return ExecutionOutput(results, verification, receipt, summary)


class SubprocessIBMRuntimeAdapter:
    """Killable, bounded worker boundary for the network-enabled IBM runtime."""

    def __init__(self, *, repository_root: Path, configuration: IBMQuantumConfiguration) -> None:
        self.repository_root = repository_root.resolve()
        self.configuration = configuration

    def _worker_environment(self) -> dict[str, str]:
        allowed_runtime_variables = {
            "PATH",
            "PYTHONHOME",
            "PYTHONPATH",
            "PYTHONUTF8",
            "PYTHONIOENCODING",
            "SYSTEMROOT",
            "WINDIR",
            "COMSPEC",
            "PATHEXT",
            "TEMP",
            "TMP",
            "LANG",
            "LC_ALL",
            "TZ",
            "SSL_CERT_FILE",
            "SSL_CERT_DIR",
            "REQUESTS_CA_BUNDLE",
            "CURL_CA_BUNDLE",
        }
        environment = {
            key: value
            for key, value in os.environ.items()
            if key.upper() in allowed_runtime_variables
        }
        values = {
            "PULSATE_IBM_QUANTUM_TOKEN": self.configuration.token,
            "PULSATE_IBM_QUANTUM_INSTANCE": self.configuration.instance,
            "PULSATE_IBM_QUANTUM_BACKEND": self.configuration.backend_name,
            "PULSATE_IBM_IMAGE_IDENTIFIER": self.configuration.image_identifier,
        }
        for key, value in values.items():
            if value is None:
                environment.pop(key, None)
            else:
                environment[key] = value
        return environment

    def execute(
        self,
        bundle: IBMSubmissionBundle,
        manifest: ManifestEnvelope,
        *,
        work_directory: Path,
        job_record_path: Path,
        maximum_seconds: int,
        status_callback: Callable[[str, dict[str, Any] | None], None],
    ) -> IBMRuntimeResult:
        manifest_path = work_directory / "manifest.json"
        result_path = work_directory / "result.json"
        write_json_atomic(
            manifest_path,
            manifest.model_dump(mode="json"),
            maximum_bytes=_IBM_FILE_MAXIMUM_BYTES,
        )
        if result_path.exists():
            persisted_result = IBMRuntimeResult.model_validate(
                _controlled_json(
                    work_directory,
                    result_path.name,
                    maximum_bytes=_IBM_FILE_MAXIMUM_BYTES,
                )
            )
            job = _controlled_json(
                work_directory,
                job_record_path.name,
                maximum_bytes=_IBM_FILE_MAXIMUM_BYTES,
            )
            if (
                persisted_result.bundle_sha256 != bundle.bundle_sha256
                or job.get("bundle_sha256") != bundle.bundle_sha256
                or job.get("job_identifier") != persisted_result.job_identifier
            ):
                raise ValueError("Persisted IBM result does not match its job and submission identities.")
            return persisted_result
        command = [
            sys.executable,
            "-m",
            "cgr.pulsate_api.ibm_worker",
            "--submission", str(work_directory / "submission.json"),
            "--manifest", str(manifest_path),
            "--job-record", str(job_record_path),
            "--result-envelope", str(result_path),
        ]
        options: dict[str, Any] = {
            "cwd": self.repository_root,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "shell": False,
            "env": self._worker_environment(),
        }
        if os.name == "posix":
            options["start_new_session"] = True
        process = subprocess.Popen(command, **options)
        if process.stdout is None or process.stderr is None:
            ExistingQuantumPreflightExecutor._terminate_worker(process)
            raise RuntimeError("IBM worker output pipes were not created.")
        redactions = tuple(
            value.encode("utf-8")
            for value in (self.configuration.token, self.configuration.instance)
            if value
        )
        collectors = (
            _BoundedLogCollector(
                process.stdout,
                work_directory / "stdout.log",
                thread_name="ibm-worker-stdout",
                redactions=redactions,
            ),
            _BoundedLogCollector(
                process.stderr,
                work_directory / "stderr.log",
                thread_name="ibm-worker-stderr",
                redactions=redactions,
            ),
        )
        try:
            for collector in collectors:
                collector.start()
        except BaseException:
            cleanup_errors: list[Exception] = []
            try:
                ExistingQuantumPreflightExecutor._terminate_worker(process)
            except Exception as exc:
                cleanup_errors.append(exc)
            try:
                ExistingQuantumPreflightExecutor._finish_log_collectors(collectors)
            except Exception as exc:
                cleanup_errors.append(exc)
            if cleanup_errors:
                raise RuntimeError(
                    "IBM worker startup cleanup could not be confirmed."
                ) from cleanup_errors[0]
            raise
        deadline = time.monotonic() + maximum_seconds
        status_path = work_directory / "status.json"
        last_runtime_status: tuple[str, str] | None = None

        def publish_runtime_status() -> None:
            nonlocal last_runtime_status
            if not status_path.is_file() or status_path.is_symlink():
                return
            observed = _controlled_json(
                work_directory,
                status_path.name,
                maximum_bytes=_IBM_FILE_MAXIMUM_BYTES,
            )
            if observed.get("bundle_sha256") != bundle.bundle_sha256:
                raise ValueError("IBM Runtime status belongs to another bundle.")
            job_identifier = str(observed.get("job_identifier", ""))
            if not _IBM_JOB_IDENTIFIER.fullmatch(job_identifier):
                raise ValueError("IBM Runtime status has an invalid job identifier.")
            runtime_status = str(observed.get("runtime_status", "")).upper()
            identity = (job_identifier, runtime_status)
            if identity == last_runtime_status:
                return
            additions = {
                "ibm_job_identifier": job_identifier,
                "ibm_backend_name": observed.get("backend_name"),
                "ibm_runtime_status": runtime_status,
            }
            if runtime_status in {"QUEUED", "INITIALIZING"}:
                status_callback("queued_on_ibm", additions)
            elif runtime_status == "RUNNING":
                status_callback("running_on_ibm", additions)
            elif runtime_status == "COMPLETED":
                status_callback("verifying_ibm_result", additions)
            elif runtime_status in {"CANCELLED", "ERROR", "FAILED"}:
                status_callback("running_on_ibm", additions)
            else:
                raise ValueError("IBM Runtime status is unsupported.")
            last_runtime_status = identity

        try:
            while True:
                try:
                    remaining = max(0.01, deadline - time.monotonic())
                    return_code = process.wait(timeout=min(0.25, remaining))
                    break
                except subprocess.TimeoutExpired as exc:
                    publish_runtime_status()
                    if time.monotonic() >= deadline:
                        if job_record_path.is_file() and not job_record_path.is_symlink():
                            job = _controlled_json(
                                work_directory,
                                job_record_path.name,
                                maximum_bytes=_IBM_FILE_MAXIMUM_BYTES,
                            )
                            job_identifier = str(job.get("job_identifier", ""))
                            if not _IBM_JOB_IDENTIFIER.fullmatch(job_identifier):
                                raise ValueError(
                                    "Persisted IBM job has an invalid identifier."
                                ) from exc
                            raise RecoverableIBMJobError(
                                "IBM result retrieval exceeded its bounded local grace period.",
                                job_identifier=job_identifier,
                            ) from exc
                        raise TimeoutError(
                            "IBM submission worker timed out before a job identity was persisted."
                        ) from exc
            publish_runtime_status()
            if job_record_path.is_file():
                job = _controlled_json(
                    work_directory,
                    job_record_path.name,
                    maximum_bytes=_IBM_FILE_MAXIMUM_BYTES,
                )
        except BaseException:
            cleanup_errors: list[Exception] = []
            try:
                if process.poll() is None:
                    ExistingQuantumPreflightExecutor._terminate_worker(process)
            except Exception as exc:
                cleanup_errors.append(exc)
            try:
                ExistingQuantumPreflightExecutor._finish_log_collectors(collectors)
            except Exception as exc:
                cleanup_errors.append(exc)
            if cleanup_errors:
                raise RuntimeError("IBM worker cleanup could not be confirmed.") from cleanup_errors[0]
            raise
        ExistingQuantumPreflightExecutor._finish_log_collectors(collectors)
        if return_code != 0:
            failure_path = work_directory / "failure.json"
            if failure_path.is_file() and not failure_path.is_symlink():
                failure = IBMWorkerFailureEnvelope.model_validate(
                    _controlled_json(
                        work_directory,
                        failure_path.name,
                        maximum_bytes=_IBM_FILE_MAXIMUM_BYTES,
                    )
                )
                if (
                    failure.retrieval_recoverable
                    and failure.job_identifier_persisted
                    and failure.job_identifier is not None
                ):
                    raise RecoverableIBMJobError(
                        "IBM result retrieval remains recoverable.",
                        job_identifier=failure.job_identifier,
                    )
                if (
                    failure.job_identifier_persisted
                    and failure.job_identifier is not None
                    and failure.category
                    in {"terminal_job_failure", "prepared_evidence_failure"}
                ):
                    raise TerminalIBMJobError(
                        (
                            "The IBM Runtime job reached a terminal failure state."
                            if failure.category == "terminal_job_failure"
                            else "The persisted IBM workload evidence failed validation."
                        ),
                        job_identifier=failure.job_identifier,
                        backend_name=bundle.backend_name,
                        runtime_status=failure.last_controlled_ibm_status,
                    )
            raise RuntimeError("The isolated IBM Runtime worker failed.")
        return IBMRuntimeResult.model_validate(
            _controlled_json(work_directory, "result.json", maximum_bytes=_IBM_FILE_MAXIMUM_BYTES)
        )
