#!/usr/bin/env python3
"""Run the real approved-experiment Qiskit preflight without IBM submission."""

from __future__ import annotations

import json
import math
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import types
import uuid
from datetime import datetime, timezone
from importlib import import_module
from importlib.machinery import ModuleSpec
from pathlib import Path
from typing import Any, BinaryIO

import cgr
from cgr.quantum_preflight.artifacts import write_json_atomic
from cgr.science import sha256_fingerprint


def _install_side_effect_free_pulsate_api_package() -> None:
    """Load selected production modules without importing the FastAPI/IBM app."""
    package_name = "cgr.pulsate_api"
    if package_name in sys.modules:
        raise RuntimeError(
            "The Pulsate API package was imported before the acceptance boundary."
        )
    package_root = (
        Path(__file__).resolve().parent.parent / "src" / "cgr" / "pulsate_api"
    )
    if not package_root.is_dir():
        raise RuntimeError("The production Pulsate API package is unavailable.")
    package = types.ModuleType(package_name)
    package.__file__ = str(package_root / "__init__.py")
    package.__package__ = package_name
    package.__path__ = [str(package_root)]
    package.__spec__ = ModuleSpec(package_name, loader=None, is_package=True)
    assert package.__spec__.submodule_search_locations is not None
    package.__spec__.submodule_search_locations.append(str(package_root))
    sys.modules[package_name] = package
    setattr(cgr, "pulsate_api", package)


_install_side_effect_free_pulsate_api_package()

_approved_experiments = import_module("cgr.pulsate_api.approved_experiments")
_natural_language = import_module("cgr.pulsate_api.natural_language")
_quantum_worker = import_module("cgr.pulsate_api.quantum_worker")
_runs = import_module("cgr.pulsate_api.runs")

ApprovedExperimentExecutionResolver = (
    _approved_experiments.ApprovedExperimentExecutionResolver
)
ApprovalRequest = _natural_language.ApprovalRequest
NaturalLanguageInterpretationStore = (
    _natural_language.NaturalLanguageInterpretationStore
)
OpenAICompatibleModelProvider = _natural_language.OpenAICompatibleModelProvider
WORKER_EXIT_COMPLETED = _quantum_worker.WORKER_EXIT_COMPLETED
WORKER_MANIFEST_MAXIMUM_BYTES = _quantum_worker.WORKER_MANIFEST_MAXIMUM_BYTES
WORKER_RESULT_MAXIMUM_BYTES = _quantum_worker.WORKER_RESULT_MAXIMUM_BYTES
WorkerResultEnvelope = _quantum_worker.WorkerResultEnvelope
_AUTHORITATIVE_ARTIFACTS = _runs._AUTHORITATIVE_ARTIFACTS
ExistingQuantumPreflightExecutor = _runs.ExistingQuantumPreflightExecutor

ACCEPTANCE_SCHEMA = "cgr.pulsate-approved-qiskit-preflight-acceptance/1.0.0"
REQUIRED_PRODUCTION_BASE = "d4002efefde4d8681a086a380acd9d6925c79d86"
ALLOWED_COMMITTED_PATHS = (
    "scripts/run-pulsate-approved-qiskit-preflight-acceptance.py",
    "scripts/run-pulsate-approved-qiskit-preflight-acceptance.sh",
    "src/cgr/pulsate_api/approved_experiments.py",
    "tests/test_pulsate_approved_execution.py",
)
QUESTION = (
    "Calculate the ground-state energy of lithium hydride at a bond length of "
    "1.6 angstrom using STO-3G on IBM Quantum."
)
IMAGE_IDENTIFIER = re.compile(r"^sha256:[0-9a-f]{64}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
EVIDENCE_ROOT = Path("/home/ubuntu/cgr-evidence")
SCIENTIFIC_TIMEOUT_SECONDS = 180
DOCKER_OUTER_GRACE_SECONDS = 30
DOCKER_STREAM_MAXIMUM_BYTES = 384 * 1024
EVIDENCE_JSON_MAXIMUM_BYTES = 256 * 1024
EVIDENCE_LOG_MAXIMUM_BYTES = 1024 * 1024
STREAM_TRUNCATION_MARKER = b"\n...[docker output truncated]...\n"
SENSITIVE_ENVIRONMENT_NAMES = (
    "PULSATE_RUN_IBM_INTEGRATION",
    "PULSATE_IBM_ACKNOWLEDGE_COSTS",
    "PULSATE_IBM_QUANTUM_TOKEN",
    "PULSATE_IBM_QUANTUM_INSTANCE",
    "PULSATE_IBM_QUANTUM_BACKEND",
)
REQUIRED_MODEL_ENVIRONMENT_NAMES = (
    "PULSATE_NL_MODEL_BASE_URL",
    "PULSATE_NL_MODEL_API_KEY",
    "PULSATE_NL_MODEL_NAME",
    "PULSATE_NL_MODEL_TIMEOUT_SECONDS",
)
FORBIDDEN_IBM_RECORD_NAMES = frozenset(
    {
        "submission.json",
        "job.json",
        "prepared-submission.json",
        "submitted-job.json",
        "submission-attempt.json",
    }
)
FORBIDDEN_IBM_JOB_KEYS = frozenset(
    {
        "ibm_job_identifier",
        "job_identifier",
        "submission_attempt_identifier",
    }
)
FORBIDDEN_RUNTIME_MODULES = frozenset(
    {
        "cgr.pulsate_api.ibm",
        "test_pulsate_ibm",
        "test_pulsate_natural_language",
        "tests.test_pulsate_ibm",
        "tests.test_pulsate_natural_language",
    }
)


class AcceptanceFailure(RuntimeError):
    """A pre-submission acceptance invariant failed."""


class BoundedStreamCollector:
    """Drain a process pipe without backpressure while retaining a bounded prefix."""

    def __init__(self, source: BinaryIO, maximum_bytes: int) -> None:
        self.source = source
        self.maximum_bytes = maximum_bytes
        self.buffer = bytearray()
        self.truncated = False
        self.error: Exception | None = None
        self.thread = threading.Thread(
            target=self._read,
            name=f"pulsate-acceptance-log-{uuid.uuid4().hex}",
            daemon=True,
        )

    def start(self) -> None:
        self.thread.start()

    def _read(self) -> None:
        try:
            while True:
                chunk = self.source.read(64 * 1024)
                if not chunk:
                    break
                remaining = self.maximum_bytes - len(self.buffer)
                if remaining > 0:
                    self.buffer.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    self.truncated = True
        except Exception as exc:
            self.error = exc
        finally:
            self.source.close()

    def finish(self) -> bytes:
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            raise AcceptanceFailure("A Docker output reader could not be collected.")
        if self.error is not None:
            raise AcceptanceFailure("A Docker output reader failed.") from self.error
        payload = bytes(self.buffer)
        if self.truncated:
            retained = max(0, self.maximum_bytes - len(STREAM_TRUNCATION_MARKER))
            payload = payload[:retained] + STREAM_TRUNCATION_MARKER
        if len(payload) > self.maximum_bytes:
            raise AcceptanceFailure("A Docker output stream exceeded its byte ceiling.")
        return payload


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AcceptanceFailure(message)


def _run_checked(
    arguments: list[str],
    *,
    cwd: Path,
    timeout: float = 30,
) -> str:
    completed = subprocess.run(
        arguments,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()[:1000]
        raise AcceptanceFailure(
            f"Command {arguments[0]!r} failed with status "
            f"{completed.returncode}: {detail}"
        )
    return completed.stdout.decode("utf-8", errors="strict").strip()


def _source_identity(
    repository_root: Path,
) -> tuple[str, str, tuple[str, ...], str]:
    _run_checked(
        ["git", "cat-file", "-e", f"{REQUIRED_PRODUCTION_BASE}^{{commit}}"],
        cwd=repository_root,
    )
    _run_checked(
        [
            "git",
            "merge-base",
            "--is-ancestor",
            REQUIRED_PRODUCTION_BASE,
            "HEAD",
        ],
        cwd=repository_root,
    )
    head = _run_checked(["git", "rev-parse", "HEAD"], cwd=repository_root)
    subject = _run_checked(
        ["git", "show", "-s", "--format=%s", "HEAD"], cwd=repository_root
    )
    committed_paths = tuple(
        sorted(
            filter(
                None,
                _run_checked(
                    [
                        "git",
                        "diff",
                        "--name-only",
                        "--diff-filter=ACDMRTUXB",
                        f"{REQUIRED_PRODUCTION_BASE}..HEAD",
                        "--",
                    ],
                    cwd=repository_root,
                ).splitlines(),
            )
        )
    )
    _require(
        committed_paths == tuple(sorted(ALLOWED_COMMITTED_PATHS)),
        "Committed changes above the production base must exactly match the "
        "approved preflight change set.",
    )
    tracked_status = _run_checked(
        ["git", "status", "--porcelain=v1", "--untracked-files=no"],
        cwd=repository_root,
    )
    _require(
        not tracked_status,
        "Tracked working-tree changes are forbidden during acceptance.",
    )
    return head, subject, committed_paths, tracked_status


def _validate_environment() -> str:
    _require(sys.platform.startswith("linux"), "This acceptance runs only on Linux.")
    for name in SENSITIVE_ENVIRONMENT_NAMES:
        _require(not os.environ.get(name), f"{name} must be empty for this acceptance.")
    for name in REQUIRED_MODEL_ENVIRONMENT_NAMES:
        _require(bool(os.environ.get(name)), f"{name} is required.")
    image_identifier = os.environ.get(
        "PULSATE_APPROVED_QISKIT_IMAGE_IDENTIFIER", ""
    )
    _require(
        IMAGE_IDENTIFIER.fullmatch(image_identifier) is not None,
        "PULSATE_APPROVED_QISKIT_IMAGE_IDENTIFIER must be an exact image ID.",
    )
    return image_identifier


def _timestamped_paths() -> tuple[Path, Path, Path]:
    EVIDENCE_ROOT.mkdir(parents=True, exist_ok=True)
    _require(
        EVIDENCE_ROOT.is_dir() and not EVIDENCE_ROOT.is_symlink(),
        "The acceptance evidence root must be a normal directory.",
    )
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = uuid.uuid4().hex[:12]
    prefix = f"pulsate-approved-qiskit-preflight-{timestamp}-{suffix}"
    acceptance_root = Path(
        tempfile.mkdtemp(prefix=f".{prefix}-", dir=EVIDENCE_ROOT)
    ).resolve(strict=True)
    return (
        acceptance_root,
        EVIDENCE_ROOT / f"{prefix}.json",
        EVIDENCE_ROOT / f"{prefix}.log",
    )


def _write_bytes_atomic(path: Path, payload: bytes, *, maximum_bytes: int) -> None:
    _require(len(payload) <= maximum_bytes, f"{path.name} exceeded its byte ceiling.")
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with temporary.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _redact(value: str, secrets: tuple[str, ...]) -> str:
    redacted = value
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "[redacted]")
    redacted = re.sub(
        r"(?i)(authorization|api[_-]?key|token|bearer)"
        r"(\s*[:=]\s*|\s+)[^\s,;]+",
        r"\1\2[redacted]",
        redacted,
    )
    return redacted


def _artifact_payload(path: Path, expected_type: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    _require(
        isinstance(payload, dict)
        and payload.get("artifact_schema")
        == "cgr.quantum-preflight-artifact/1.0.0"
        and payload.get("artifact_type") == expected_type
        and isinstance(payload.get("payload"), dict),
        f"{path.name} is not the expected controlled artifact.",
    )
    return payload["payload"]


def _authoritative_artifact_path(
    artifact_directory: Path,
    artifact_identifier: str,
) -> tuple[Path, str]:
    contract = _AUTHORITATIVE_ARTIFACTS.get(artifact_identifier)
    _require(
        contract is not None,
        f"Production has no authoritative contract for {artifact_identifier}.",
    )
    assert contract is not None
    artifact_type, filename = contract
    path = artifact_directory / filename
    _require(
        path.is_file()
        and not path.is_symlink()
        and path.resolve(strict=True).parent == artifact_directory.resolve(strict=True),
        f"The receipt-linked {artifact_identifier} artifact is unavailable.",
    )
    return path, artifact_type


def _compile_real_approval(
    interpretation_root: Path,
) -> tuple[Any, Any, Any, dict[str, int]]:
    provider = OpenAICompatibleModelProvider.from_environment()
    store = NaturalLanguageInterpretationStore(interpretation_root, provider)
    store.start()
    try:
        before = provider.request_count
        interpretation = store.interpret(QUESTION)
        after = provider.request_count
        delta = after - before
        _require(
            delta == interpretation.model_provenance.request_count_for_interpretation
            and delta in {1, 2},
            "The bounded model request count disagrees with interpretation provenance.",
        )
        _require(
            interpretation.interpretation_status == "ready_for_review",
            "The real interpretation is not ready for scientist review.",
        )
        _require(
            interpretation.execution_support_status == "supported",
            "The real interpretation is not supported by the approved compiler.",
        )
        _require(
            not interpretation.missing_required_information,
            "The real interpretation still has missing required information.",
        )
        approved = store.approve(
            interpretation.interpretation_identifier,
            ApprovalRequest(
                specification=interpretation.specification,
                accepted_assumptions=True,
            ),
        )
        _require(
            approved.status == "ready_for_ibm_submission",
            "Scientist approval did not reach the IBM pre-submission state.",
        )
        _require(
            approved.requested_execution_target == "ibm_quantum",
            "Scientist approval did not preserve the IBM Quantum target.",
        )
        compiled = ApprovedExperimentExecutionResolver(store)(
            approved.experiment_identifier
        )
        return (
            interpretation,
            approved,
            compiled,
            {"before": before, "after": after, "delta": delta},
        )
    finally:
        store.close()


def _validate_compiled_approval(approved: Any, compiled: Any) -> str:
    manifest = compiled.manifest
    experiment = manifest.experiment
    molecular = experiment.molecular_system
    electronic = experiment.electronic_structure
    quantum = experiment.quantum_model
    approved_specification = approved.specification
    parameters = experiment.parent_experiment.execution_policy.parameters
    manifest_payload = manifest.model_dump(mode="json")
    manifest_sha256 = sha256_fingerprint(manifest_payload)

    _require(compiled.execution_target == "ibm_quantum", "Compiled target changed.")
    _require(compiled.molecule.get("molecular_formula") == "LiH", "Formula is not LiH.")
    _require(
        tuple(atom.element for atom in molecular.atoms) == ("Li", "H"),
        "Ordered compiled atoms are not Li, H.",
    )
    _require(
        tuple(molecular.atoms[0].coordinates) == (-0.8, 0.0, 0.0)
        and tuple(molecular.atoms[1].coordinates) == (0.8, 0.0, 0.0),
        "Compiled LiH coordinates changed.",
    )
    _require(
        math.isclose(molecular.declared_bond_distance, 1.6, abs_tol=1e-12),
        "Compiled LiH bond distance changed.",
    )
    _require(
        molecular.coordinate_unit == "angstrom"
        and molecular.molecular_charge == 0
        and molecular.spin_multiplicity == 1,
        "Compiled molecular charge, multiplicity, or unit changed.",
    )
    _require(
        electronic.basis_set == "sto-3g"
        and electronic.reference_method == "restricted_hartree_fock",
        "Compiled basis or reference method changed.",
    )
    _require(
        electronic.active_electron_count == 2
        and electronic.active_spatial_orbital_count == 2
        and tuple(electronic.active_orbital_indices) == (1, 2),
        "Compiled active space changed.",
    )
    _require(
        quantum.mapper == "jordan_wigner"
        and quantum.ansatz == "uccsd"
        and quantum.optimizer == "slsqp",
        "Compiled mapper, ansatz, or optimizer changed.",
    )
    _require(
        quantum.convergence_threshold == approved_specification.tolerance.value,
        "Compiled optimizer tolerance changed.",
    )
    _require(
        parameters.get("requested_precision")
        == approved_specification.precision.value,
        "Compiled requested precision changed.",
    )
    _require(
        parameters.get("compiled_objective") == "molecular_ground_state_energy",
        "Compiled objective is not canonical.",
    )
    _require(
        parameters.get("approved_specification_sha256")
        == approved.specification_sha256,
        "Compiled manifest lost the approved specification identity.",
    )
    _require(
        compiled.provenance.get("compiled_execution_manifest_sha256")
        == manifest_sha256
        and compiled.molecule.get("compiled_execution_manifest_sha256")
        == manifest_sha256,
        "Compiled-manifest identity was not recorded consistently.",
    )
    _require(
        manifest.expected_experiment_sha256 == experiment.fingerprint,
        "Compiled expected experiment identity is stale.",
    )
    return manifest_sha256


def _start_collectors(
    process: subprocess.Popen[bytes],
) -> tuple[BoundedStreamCollector, BoundedStreamCollector]:
    _require(
        process.stdout is not None and process.stderr is not None,
        "Docker output pipes were not created.",
    )
    stdout = BoundedStreamCollector(process.stdout, DOCKER_STREAM_MAXIMUM_BYTES)
    stderr = BoundedStreamCollector(process.stderr, DOCKER_STREAM_MAXIMUM_BYTES)
    stdout.start()
    stderr.start()
    return stdout, stderr


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        process.wait(timeout=5)
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=5)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired as exc:
        raise AcceptanceFailure("The Docker process could not be collected.") from exc


def _remove_container(repository_root: Path, container_name: str) -> None:
    try:
        completed = subprocess.run(
            ["docker", "container", "rm", "--force", container_name],
            cwd=repository_root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise AcceptanceFailure(
            "The Docker acceptance container cleanup could not be confirmed."
        ) from exc
    stderr = completed.stderr.decode("utf-8", errors="replace")
    if completed.returncode != 0 and "No such container" not in stderr:
        raise AcceptanceFailure(
            "The Docker acceptance container cleanup could not be confirmed."
        )


def _run_worker_in_docker(
    *,
    repository_root: Path,
    acceptance_root: Path,
    run_directory: Path,
    image_identifier: str,
    manifest_path: Path,
    result_root: Path,
    result_envelope_path: Path,
    ibm_preflight_path: Path,
    log_messages: list[str],
) -> tuple[int, bytes, bytes]:
    inspected_identifier = _run_checked(
        ["docker", "image", "inspect", "--format", "{{.Id}}", image_identifier],
        cwd=repository_root,
    )
    _require(
        inspected_identifier == image_identifier,
        "The configured scientific image reference did not resolve to its exact ID.",
    )
    lock_path = repository_root / "requirements" / "quantum-preflight.lock"
    _require(lock_path.is_file() and not lock_path.is_symlink(), "Lock is unavailable.")
    container_name = f"pulsate-approved-qiskit-preflight-{uuid.uuid4().hex[:16]}"
    uid = os.getuid()
    gid = os.getgid()
    command = [
        "docker",
        "run",
        "--rm",
        "--pull=never",
        "--name",
        container_name,
        "--network",
        "none",
        "--read-only",
        "--cpus",
        "2",
        "--memory",
        "4g",
        "--pids-limit",
        "256",
        "--security-opt",
        "no-new-privileges",
        "--cap-drop",
        "ALL",
        "--user",
        f"{uid}:{gid}",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,size=1g,mode=1777",
        "--volume",
        f"{repository_root}:{repository_root}:ro",
        "--volume",
        f"{acceptance_root}:{acceptance_root}:rw",
        "--workdir",
        str(repository_root),
        "--env",
        f"PYTHONPATH={repository_root / 'src'}",
        "--env",
        "HOME=/tmp",
        "--env",
        "PYTHONDONTWRITEBYTECODE=1",
        "--env",
        "PYTHONHASHSEED=0",
        "--env",
        "OMP_NUM_THREADS=1",
        "--env",
        "OPENBLAS_NUM_THREADS=1",
        "--env",
        "MKL_NUM_THREADS=1",
        "--env",
        "NUMEXPR_NUM_THREADS=1",
        "--entrypoint",
        "python",
        image_identifier,
        "-m",
        "cgr.pulsate_api.quantum_worker",
        "--manifest-json",
        str(manifest_path),
        "--result-root",
        str(result_root),
        "--scientific-lock",
        str(lock_path),
        "--image-identifier",
        image_identifier,
        "--maximum-seconds",
        str(SCIENTIFIC_TIMEOUT_SECONDS),
        "--result-envelope",
        str(result_envelope_path),
        "--ibm-preflight-evidence",
        str(ibm_preflight_path),
    ]
    _require(
        any(
            command[index : index + 2] == ["--network", "none"]
            for index in range(len(command) - 1)
        ),
        "The Docker command lost its OS-enforced no-network boundary.",
    )
    _require(
        any(
            command[index : index + 2]
            == ["-m", "cgr.pulsate_api.quantum_worker"]
            for index in range(len(command) - 1)
        ),
        "The Docker command lost the production quantum-worker module.",
    )
    process = subprocess.Popen(
        command,
        cwd=repository_root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        start_new_session=True,
    )
    collectors = _start_collectors(process)
    pending_error: BaseException | None = None
    return_code = -1
    try:
        try:
            return_code = process.wait(
                timeout=SCIENTIFIC_TIMEOUT_SECONDS + DOCKER_OUTER_GRACE_SECONDS
            )
        except subprocess.TimeoutExpired as exc:
            pending_error = AcceptanceFailure(
                "The network-isolated Docker worker exceeded its outer timeout."
            )
            pending_error.__cause__ = exc
            cleanup_errors: list[Exception] = []
            try:
                _terminate_process_group(process)
            except Exception as cleanup_exc:
                cleanup_errors.append(cleanup_exc)
            try:
                _remove_container(repository_root, container_name)
            except Exception as cleanup_exc:
                cleanup_errors.append(cleanup_exc)
            if cleanup_errors:
                pending_error = AcceptanceFailure(
                    "The timed-out Docker worker cleanup could not be confirmed."
                )
                pending_error.__cause__ = cleanup_errors[0]
    except BaseException:
        cleanup_errors = []
        try:
            _terminate_process_group(process)
        except Exception as cleanup_exc:
            cleanup_errors.append(cleanup_exc)
        try:
            _remove_container(repository_root, container_name)
        except Exception as cleanup_exc:
            cleanup_errors.append(cleanup_exc)
        if cleanup_errors:
            raise AcceptanceFailure(
                "Docker worker cleanup could not be confirmed."
            ) from cleanup_errors[0]
        raise
    finally:
        stdout = collectors[0].finish()
        stderr = collectors[1].finish()
        secrets = _secret_values()
        log_messages.extend(
            [
                "Docker stdout (bounded):",
                _redact(stdout.decode("utf-8", errors="replace"), secrets),
                "Docker stderr (bounded):",
                _redact(stderr.decode("utf-8", errors="replace"), secrets),
            ]
        )
    if pending_error is not None:
        raise pending_error
    return return_code, stdout, stderr


def _load_worker_result(
    result_envelope_path: Path,
    return_code: int,
) -> WorkerResultEnvelope:
    _require(
        result_envelope_path.is_file()
        and not result_envelope_path.is_symlink()
        and result_envelope_path.stat().st_size <= WORKER_RESULT_MAXIMUM_BYTES,
        "The controlled worker result envelope is unavailable.",
    )
    envelope = WorkerResultEnvelope.model_validate_json(
        result_envelope_path.read_text(encoding="utf-8")
    )
    _require(
        return_code == WORKER_EXIT_COMPLETED and envelope.outcome == "completed",
        "The real quantum worker did not complete successfully.",
    )
    _require(envelope.summary is not None, "Worker summary is missing.")
    return envelope


def _scan_for_ibm_submission(root: Path) -> None:
    forbidden_files = [
        path
        for path in root.rglob("*")
        if path.is_file() and path.name in FORBIDDEN_IBM_RECORD_NAMES
    ]
    _require(not forbidden_files, "An IBM submission or job record was created.")
    for path in root.rglob("*.json"):
        if path.is_symlink() or path.stat().st_size > 10 * 1024 * 1024:
            raise AcceptanceFailure("An uncontrolled JSON file was found.")
        payload = json.loads(path.read_text(encoding="utf-8"))
        stack = [payload]
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                _require(
                    not FORBIDDEN_IBM_JOB_KEYS.intersection(value),
                    "IBM job identity evidence was created.",
                )
                stack.extend(value.values())
            elif isinstance(value, list):
                stack.extend(value)


def _prove_no_ibm_operation(root: Path) -> dict[str, Any]:
    for name in SENSITIVE_ENVIRONMENT_NAMES:
        _require(
            not os.environ.get(name),
            f"{name} became populated during acceptance.",
        )
    loaded_forbidden = sorted(FORBIDDEN_RUNTIME_MODULES.intersection(sys.modules))
    _require(
        not loaded_forbidden,
        "A forbidden IBM or test module was loaded during local preflight.",
    )
    _scan_for_ibm_submission(root)
    return {
        "ibm_adapter_instantiated": False,
        "ibm_network_operation_called": False,
        "ibm_submission_attempted": False,
        "ibm_job_identifier": None,
        "prepared_ibm_submission_transmitted": False,
    }


def _validate_execution(
    *,
    compiled: Any,
    approved: Any,
    run_directory: Path,
    result_root: Path,
    ibm_preflight_path: Path,
    envelope: WorkerResultEnvelope,
    compiled_manifest_sha256: str,
) -> dict[str, Any]:
    assert envelope.summary is not None
    receipt_path = envelope.summary.get("receipt_path")
    _require(isinstance(receipt_path, str), "Worker receipt path is missing.")
    artifact_directory = Path(receipt_path).resolve(strict=True).parent
    _require(
        result_root.resolve(strict=True) in artifact_directory.parents,
        "Worker artifacts escaped the isolated result root.",
    )
    output = ExistingQuantumPreflightExecutor._project(
        compiled.manifest,
        approved.experiment_identifier,
        run_directory.name,
        artifact_directory,
        envelope.summary,
        maximum_bytes=compiled.manifest.experiment.execution_policy.maximum_result_bytes,
        ibm_preflight_evidence_path=ibm_preflight_path,
    )
    results = output.results
    verification = output.verification
    receipt = output.receipt
    summary = output.runner_summary
    preflight = summary.get("ibm_preflight")
    _require(isinstance(preflight, dict), "IBM-ready local evidence is missing.")
    _require(
        verification.get("verification_completed") is True
        and verification.get("verification_passed") is True,
        "Scientific verification did not complete and pass.",
    )
    _require(
        receipt.get("authorized") is True
        and receipt.get("authorization_state") == "authorized",
        "Scientific receipt is not authorized.",
    )
    _require(
        results.get("hamiltonian_sha256")
        and results.get("structure_sha256")
        and results.get("structure_sha256")
        == compiled.molecule.get("structure_hash"),
        "Executed Hamiltonian or approved LiH structure identity is missing.",
    )
    _require(
        results.get("experiment_fingerprint")
        == compiled.manifest.experiment.fingerprint
        and results.get("expected_experiment_sha256")
        == compiled.manifest.expected_experiment_sha256,
        "Executed experiment identity disagrees with the compiled manifest.",
    )
    _require(
        compiled.provenance.get("compiled_execution_manifest_sha256")
        == compiled_manifest_sha256
        and compiled.provenance.get("approved_specification_sha256")
        == approved.specification_sha256,
        "Compiled or approved identity linkage changed during execution.",
    )
    exact = results.get("exact_total_energy_hartree")
    vqe = results.get("vqe_total_energy_hartree")
    difference = results.get("absolute_difference_hartree")
    tolerance = results.get("tolerance_hartree")
    _require(
        all(
            isinstance(value, (int, float)) and math.isfinite(float(value))
            for value in (exact, vqe, difference, tolerance)
        ),
        "Real exact/VQE numerical evidence is incomplete.",
    )
    _require(
        math.isclose(abs(float(exact) - float(vqe)), float(difference), abs_tol=1e-12)
        and float(difference) <= float(tolerance)
        and float(tolerance) == approved.specification.tolerance.value,
        "Real exact/VQE agreement exceeds the approved tolerance.",
    )
    number_of_qubits = preflight.get("number_of_qubits")
    circuit_depth = preflight.get("circuit_depth")
    _require(
        isinstance(number_of_qubits, int)
        and number_of_qubits > 0
        and isinstance(circuit_depth, int)
        and circuit_depth > 0,
        "The IBM-ready circuit dimensions are invalid.",
    )
    for key in ("source_bound_circuit_sha256", "source_observable_sha256"):
        _require(
            isinstance(preflight.get(key), str)
            and SHA256.fullmatch(preflight[key]) is not None,
            f"{key} is not a canonical SHA-256.",
        )

    artifacts_by_identifier = {
        item["artifact_identifier"]: item
        for item in receipt.get("artifacts", [])
        if isinstance(item, dict)
    }
    required_artifacts = (
        "molecular_structure",
        "electronic_problem",
        "active_space",
        "fermionic_hamiltonian",
        "qubit_hamiltonian",
        "ansatz_manifest",
        "exact_result",
        "vqe_result",
        "verification_report",
        "environment",
    )
    _require(
        all(identifier in artifacts_by_identifier for identifier in required_artifacts),
        "The authorized receipt is missing required scientific artifacts.",
    )
    execution_paths: dict[str, str] = {}
    for identifier in required_artifacts:
        path, artifact_type = _authoritative_artifact_path(
            artifact_directory,
            identifier,
        )
        receipt_artifact = artifacts_by_identifier[identifier]
        _require(
            receipt_artifact.get("artifact_type") == artifact_type
            and SHA256.fullmatch(
                str(receipt_artifact.get("content_sha256", ""))
            )
            is not None,
            f"The receipt identity for {identifier} is invalid.",
        )
        execution_paths[identifier] = str(path)
    receipt_file = Path(receipt_path).resolve(strict=True)
    _require(
        receipt_file.is_file()
        and not receipt_file.is_symlink()
        and receipt_file.parent == artifact_directory,
        "The production worker receipt location is uncontrolled.",
    )
    execution_paths["scientific_receipt"] = str(receipt_file)
    execution_paths["ibm_preflight_evidence"] = str(ibm_preflight_path)
    for path in execution_paths.values():
        _require(Path(path).is_file(), f"Required execution artifact is missing: {path}")

    environment_path, environment_type = _authoritative_artifact_path(
        artifact_directory, "environment"
    )
    environment = _artifact_payload(environment_path, environment_type)
    versions = environment.get("direct_package_versions")
    _require(isinstance(versions, dict), "Scientific dependency versions are missing.")
    _require(
        environment.get("container_image_identifier")
        == os.environ["PULSATE_APPROVED_QISKIT_IMAGE_IDENTIFIER"]
        and environment.get("network_disabled") is True
        and not environment.get("credential_variable_names_present"),
        "Scientific image, network, or credential evidence is invalid.",
    )

    artifact_hashes = {
        identifier: artifacts_by_identifier[identifier]["content_sha256"]
        for identifier in required_artifacts
    }
    evidence = {
        "interpretation_identifier": approved.interpretation_identifier,
        "approved_experiment_identifier": approved.experiment_identifier,
        "approved_specification_sha256": approved.specification_sha256,
        "compiled_manifest_sha256": compiled_manifest_sha256,
        "structure_sha256": results["structure_sha256"],
        "hamiltonian_sha256": results["hamiltonian_sha256"],
        "active_space_sha256": artifact_hashes["active_space"],
        "fermionic_hamiltonian_sha256": artifact_hashes[
            "fermionic_hamiltonian"
        ],
        "qubit_hamiltonian_sha256": artifact_hashes["qubit_hamiltonian"],
        "ansatz_manifest_sha256": artifact_hashes["ansatz_manifest"],
        "ibm_preflight_ansatz_sha256": preflight["ansatz_sha256"],
        "source_bound_circuit_sha256": preflight["source_bound_circuit_sha256"],
        "source_observable_sha256": preflight["source_observable_sha256"],
        "exact_total_energy": float(exact),
        "vqe_total_energy": float(vqe),
        "absolute_energy_difference": float(difference),
        "approved_tolerance": float(tolerance),
        "number_of_qubits": number_of_qubits,
        "circuit_depth": circuit_depth,
        "optimizer_evaluations": results.get("optimizer_evaluations"),
        "verification_status": "passed",
        "authorization_status": "authorized",
        "receipt_sha256": results["receipt_sha256"],
        "qiskit_version": versions.get("qiskit"),
        "qiskit_nature_version": versions.get("qiskit-nature"),
        "pyscf_version": versions.get("pyscf"),
        "execution_artifact_paths": execution_paths,
    }
    _require(
        summary.get("active_space_sha256") == artifact_hashes["active_space"]
        and summary.get("fermionic_hamiltonian_sha256")
        == artifact_hashes["fermionic_hamiltonian"]
        and summary.get("qubit_hamiltonian_sha256")
        == artifact_hashes["qubit_hamiltonian"]
        and results.get("hamiltonian_sha256")
        == artifact_hashes["qubit_hamiltonian"]
        and preflight.get("ansatz_sha256")
        == artifact_hashes["ansatz_manifest"],
        "Worker summary, preflight, or Hamiltonian identities disagree with "
        "the authorized receipt content hashes.",
    )
    return evidence


def _secret_values() -> tuple[str, ...]:
    names = (
        "PULSATE_NL_MODEL_API_KEY",
        "PULSATE_IBM_QUANTUM_TOKEN",
        "PULSATE_IBM_QUANTUM_INSTANCE",
        "PULSATE_IBM_QUANTUM_BACKEND",
    )
    return tuple(value for name in names if (value := os.environ.get(name)))


def _assert_secret_absent(payloads: tuple[bytes, ...], secrets: tuple[str, ...]) -> None:
    for secret in secrets:
        encoded = secret.encode("utf-8")
        _require(
            all(encoded not in payload for payload in payloads),
            "A credential value appeared in acceptance evidence.",
        )


def _assert_tree_secret_absent(root: Path, secrets: tuple[str, ...]) -> None:
    encoded_secrets = tuple(secret.encode("utf-8") for secret in secrets)
    if not encoded_secrets:
        return
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        _require(not path.is_symlink(), "Acceptance evidence contains a symbolic link.")
        _require(
            path.stat().st_size <= 10 * 1024 * 1024,
            "Acceptance evidence contains an oversized file.",
        )
        payload = path.read_bytes()
        _require(
            all(secret not in payload for secret in encoded_secrets),
            "A credential value appeared in controlled acceptance evidence.",
        )


def run_acceptance(
    repository_root: Path,
    acceptance_root: Path,
    image_identifier: str,
    log_messages: list[str],
) -> dict[str, Any]:
    interpretation_root = acceptance_root / "interpretations"
    manifest_root = acceptance_root / "manifests"
    worker_root = acceptance_root / "quantum-worker-output"
    isolated_evidence_root = acceptance_root / "evidence"
    for directory in (
        interpretation_root,
        manifest_root,
        worker_root,
        isolated_evidence_root,
    ):
        directory.mkdir(mode=0o700)

    interpretation, approved, compiled, counts = _compile_real_approval(
        interpretation_root
    )
    compiled_manifest_sha256 = _validate_compiled_approval(approved, compiled)
    compiled_manifest_path = manifest_root / "compiled-manifest.json"
    write_json_atomic(
        compiled_manifest_path,
        compiled.manifest.model_dump(mode="json"),
        maximum_bytes=WORKER_MANIFEST_MAXIMUM_BYTES,
    )

    run_directory = worker_root / f"run-{uuid.uuid4().hex}"
    run_directory.mkdir(mode=0o700)
    worker_directory = run_directory / "quantum-worker"
    worker_directory.mkdir(mode=0o700)
    worker_manifest_path = worker_directory / "manifest.json"
    write_json_atomic(
        worker_manifest_path,
        compiled.manifest.model_dump(mode="json"),
        maximum_bytes=WORKER_MANIFEST_MAXIMUM_BYTES,
    )
    _require(
        compiled_manifest_path.read_bytes() == worker_manifest_path.read_bytes(),
        "The controlled worker manifest differs from the compiled manifest.",
    )
    result_root = run_directory / "runner-artifacts"
    result_envelope_path = worker_directory / "result.json"
    ibm_preflight_path = worker_directory / "ibm-preflight-evidence.json"
    return_code, stdout, stderr = _run_worker_in_docker(
        repository_root=repository_root,
        acceptance_root=acceptance_root,
        run_directory=run_directory,
        image_identifier=image_identifier,
        manifest_path=worker_manifest_path,
        result_root=result_root,
        result_envelope_path=result_envelope_path,
        ibm_preflight_path=ibm_preflight_path,
        log_messages=log_messages,
    )
    secrets = _secret_values()
    _assert_secret_absent((stdout, stderr), secrets)
    envelope = _load_worker_result(result_envelope_path, return_code)
    execution = _validate_execution(
        compiled=compiled,
        approved=approved,
        run_directory=run_directory,
        result_root=result_root,
        ibm_preflight_path=ibm_preflight_path,
        envelope=envelope,
        compiled_manifest_sha256=compiled_manifest_sha256,
    )
    execution["execution_artifact_paths"].update(
        {
            "compiled_manifest": str(compiled_manifest_path),
            "worker_manifest": str(worker_manifest_path),
            "worker_result_envelope": str(result_envelope_path),
        }
    )
    _assert_tree_secret_absent(acceptance_root, secrets)
    non_submission_evidence = _prove_no_ibm_operation(acceptance_root)
    current_status = _run_checked(
        ["git", "status", "--porcelain=v1", "--untracked-files=no"],
        cwd=repository_root,
    )
    _require(
        current_status == _SOURCE_TRACKED_STATUS,
        "The acceptance changed tracked repository state.",
    )
    return {
        **execution,
        "exact_question": QUESTION,
        "model_provider_kind": interpretation.model_provenance.provider_kind,
        "model_name": interpretation.model_provenance.model_name,
        "model_request_count_before": counts["before"],
        "model_request_count_after": counts["after"],
        "model_request_delta": counts["delta"],
        "scientific_docker_image_identifier": image_identifier,
        "docker_network_mode": "none",
        **non_submission_evidence,
    }


_SOURCE_TRACKED_STATUS = ""


def main() -> int:
    global _SOURCE_TRACKED_STATUS

    repository_root = Path(__file__).resolve().parent.parent
    acceptance_root: Path | None = None
    evidence_json_path: Path | None = None
    evidence_log_path: Path | None = None
    log_messages: list[str] = []
    evidence: dict[str, Any] = {
        "acceptance_schema": ACCEPTANCE_SCHEMA,
        "status": "failed",
    }
    exit_code = 1
    secrets = _secret_values()
    try:
        image_identifier = _validate_environment()
        (
            source_head,
            source_subject,
            committed_paths,
            tracked_status,
        ) = _source_identity(repository_root)
        _SOURCE_TRACKED_STATUS = tracked_status
        acceptance_root, evidence_json_path, evidence_log_path = _timestamped_paths()
        evidence.update(
            {
                "source_commit": source_head,
                "production_base_commit": REQUIRED_PRODUCTION_BASE,
                "source_subject": source_subject,
                "committed_paths_above_production_base": list(committed_paths),
                "scientific_docker_image_identifier": image_identifier,
                "docker_network_mode": "none",
            }
        )
        evidence.update(
            run_acceptance(
                repository_root,
                acceptance_root,
                image_identifier,
                log_messages,
            )
        )
        evidence["status"] = "passed"
        exit_code = 0
    except Exception as exc:
        message = _redact(str(exc).strip() or type(exc).__name__, secrets)[:2000]
        evidence["failure"] = {
            "error_type": type(exc).__name__,
            "message": message,
        }
        log_messages.append(f"Acceptance failure: {type(exc).__name__}: {message}")
    finally:
        if evidence_json_path is None or evidence_log_path is None:
            try:
                acceptance_root, evidence_json_path, evidence_log_path = (
                    _timestamped_paths()
                )
            except Exception as exc:
                print(
                    f"Unable to create bounded acceptance evidence: {exc}",
                    file=sys.stderr,
                )
                return 1
        try:
            non_submission_evidence = _prove_no_ibm_operation(acceptance_root)
        except Exception as proof_exc:
            proof_message = _redact(
                str(proof_exc).strip() or type(proof_exc).__name__,
                secrets,
            )[:2000]
            evidence["ibm_non_submission_proof"] = {
                "status": "failed",
                "error_type": type(proof_exc).__name__,
                "message": proof_message,
            }
            log_messages.append(
                "IBM non-submission proof failure: "
                f"{type(proof_exc).__name__}: {proof_message}"
            )
            evidence["status"] = "failed"
            exit_code = 1
        else:
            evidence.update(non_submission_evidence)
            evidence["ibm_non_submission_proof"] = {"status": "passed"}
        evidence["acceptance_output_root"] = str(acceptance_root)
        log_text = _redact("\n".join(log_messages).strip() + "\n", secrets)
        log_payload = log_text.encode("utf-8")[:EVIDENCE_LOG_MAXIMUM_BYTES]
        if len(log_text.encode("utf-8")) > EVIDENCE_LOG_MAXIMUM_BYTES:
            retained = EVIDENCE_LOG_MAXIMUM_BYTES - len(STREAM_TRUNCATION_MARKER)
            log_payload = (
                log_text.encode("utf-8")[:retained] + STREAM_TRUNCATION_MARKER
            )
        try:
            encoded_evidence = (
                json.dumps(
                    evidence,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                )
                + "\n"
            ).encode("utf-8")
            _assert_secret_absent((encoded_evidence, log_payload), secrets)
            _require(
                len(encoded_evidence) <= EVIDENCE_JSON_MAXIMUM_BYTES,
                "Acceptance JSON evidence exceeded its byte ceiling.",
            )
            write_json_atomic(
                evidence_json_path,
                evidence,
                maximum_bytes=EVIDENCE_JSON_MAXIMUM_BYTES,
            )
            _write_bytes_atomic(
                evidence_log_path,
                log_payload,
                maximum_bytes=EVIDENCE_LOG_MAXIMUM_BYTES,
            )
        except Exception as exc:
            print(f"Unable to persist bounded acceptance evidence: {exc}", file=sys.stderr)
            return 1

    print(f"status: {evidence['status']}")
    print(f"source HEAD: {evidence.get('source_commit', 'unavailable')}")
    print(f"evidence log path: {evidence_log_path}")
    print(f"evidence JSON path: {evidence_json_path}")
    if evidence["status"] == "passed":
        print(f"exact energy: {evidence['exact_total_energy']}")
        print(f"VQE energy: {evidence['vqe_total_energy']}")
        print(f"absolute difference: {evidence['absolute_energy_difference']}")
        print(f"Hamiltonian SHA-256: {evidence['hamiltonian_sha256']}")
        print(f"circuit SHA-256: {evidence['source_bound_circuit_sha256']}")
        print(f"observable SHA-256: {evidence['source_observable_sha256']}")
        print(f"verification result: {evidence['verification_status']}")
    if evidence.get("ibm_non_submission_proof", {}).get("status") == "passed":
        print("No IBM submission occurred.")
    else:
        print("IBM non-submission could not be proven.", file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
