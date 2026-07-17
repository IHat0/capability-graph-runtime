"""Docker argument construction and bounded execution of hostile candidates."""

from __future__ import annotations

import hashlib
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Literal

from cgr.quantum_preflight.artifacts import write_json_atomic

from .contracts import CandidateExecutionEvidence, CandidateSandboxPolicy
from .protocol import (
    CandidateOutputPackage,
    collect_candidate_output,
    source_tree_sha256,
)


def candidate_docker_arguments(
    *,
    image_identifier: str,
    input_manifest: Path,
    candidate_directory: Path,
    output_directory: Path,
    policy: CandidateSandboxPolicy,
    container_name: str,
) -> list[str]:
    """Return an explicit no-shell Docker command with only the three allowed mounts."""
    return [
        "docker",
        "run",
        "--rm",
        "--name",
        container_name,
        "--network",
        "none",
        "--read-only",
        "--cpus",
        str(policy.cpu_limit),
        "--memory",
        f"{policy.memory_mib}m",
        "--pids-limit",
        str(policy.process_limit),
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--tmpfs",
        policy.tmpfs,
        "--user",
        str(policy.candidate_uid),
        "--mount",
        f"type=bind,src={input_manifest.resolve()},dst=/input/experiment.json,readonly",
        "--mount",
        f"type=bind,src={candidate_directory.resolve()},dst=/candidate,readonly",
        "--mount",
        f"type=bind,src={output_directory.resolve()},dst=/output",
        "--entrypoint",
        "python",
        image_identifier,
        "/candidate/main.py",
        "--input",
        "/input/experiment.json",
        "--output",
        "/output",
    ]


def execute_candidate(
    *,
    candidate_identifier: str,
    image_identifier: str,
    input_manifest: Path,
    input_manifest_sha256: str,
    candidate_directory: Path,
    output_directory: Path,
    evidence_directory: Path,
    policy: CandidateSandboxPolicy,
) -> tuple[CandidateExecutionEvidence, CandidateOutputPackage]:
    output_directory.mkdir(parents=True, exist_ok=False)
    output_directory.chmod(0o733)
    evidence_directory.mkdir(parents=True, exist_ok=True)
    container_name = f"cgr-quantum-candidate-{uuid.uuid4().hex[:12]}"
    command = candidate_docker_arguments(
        image_identifier=image_identifier,
        input_manifest=input_manifest,
        candidate_directory=candidate_directory,
        output_directory=output_directory,
        policy=policy,
        container_name=container_name,
    )
    started = time.monotonic()
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
    )
    stdout_reader = _BoundedStream(process.stdout, policy.maximum_stdout_bytes)
    stderr_reader = _BoundedStream(process.stderr, policy.maximum_stderr_bytes)
    stdout_reader.start()
    stderr_reader.start()
    timed_out = False
    try:
        process.wait(timeout=policy.wall_clock_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        subprocess.run(
            ["docker", "kill", container_name],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
            shell=False,
        )
        process.kill()
        process.wait(timeout=10)
    stdout_reader.join(timeout=10)
    stderr_reader.join(timeout=10)
    stdout = bytes(stdout_reader.data)
    stderr = bytes(stderr_reader.data)
    elapsed = time.monotonic() - started
    stdout_overflow = stdout_reader.overflow
    stderr_overflow = stderr_reader.overflow
    (evidence_directory / "stdout.log").write_bytes(stdout)
    (evidence_directory / "stderr.log").write_bytes(stderr)
    package = collect_candidate_output(output_directory, policy)
    output_directory.chmod(0o755)
    source_text = (candidate_directory / "main.py").read_text(
        encoding="utf-8", errors="ignore"
    )
    category = _execution_category(
        exit_code=process.returncode,
        timed_out=timed_out,
        stderr=stderr.decode("utf-8", errors="replace"),
        output_violated=bool(package.findings or stdout_overflow or stderr_overflow),
    )
    evidence = CandidateExecutionEvidence(
        candidate_identifier=candidate_identifier,
        source_tree_sha256=source_tree_sha256(candidate_directory),
        input_manifest_sha256=input_manifest_sha256,
        image_identifier=image_identifier,
        sandbox_policy_sha256=policy.fingerprint,
        mount_manifest=policy.mounts,
        execution_category=category,
        exit_code=None if timed_out else process.returncode,
        timed_out=timed_out,
        elapsed_seconds=elapsed,
        stdout_sha256=hashlib.sha256(stdout).hexdigest(),
        stderr_sha256=hashlib.sha256(stderr).hexdigest(),
        stdout_bytes=len(stdout),
        stderr_bytes=len(stderr),
        output_bytes=package.total_bytes,
        output_files=len(package.files),
        network_disabled=True,
        trusted_evidence_exposed=False,
        forbidden_cgr_import_attempted=(
            "import cgr" in source_text or "from cgr" in source_text
        ),
        network_access_attempted=any(
            token in source_text
            for token in ("socket.connect", "create_connection", "urlopen(")
        ),
        output_policy_violated=bool(
            package.findings or stdout_overflow or stderr_overflow
        ),
    )
    write_json_atomic(
        evidence_directory / "execution.json",
        evidence.model_dump(mode="json"),
        maximum_bytes=2 * 1024 * 1024,
    )
    return evidence, package


class _BoundedStream(threading.Thread):
    """Continuously drain a child stream while retaining only its bounded prefix."""

    def __init__(self, stream: object, limit: int) -> None:
        super().__init__(daemon=True)
        self.stream = stream
        self.limit = limit
        self.data = bytearray()
        self.overflow = False

    def run(self) -> None:
        read = getattr(self.stream, "read")
        while chunk := read(64 * 1024):
            remaining = self.limit - len(self.data)
            if remaining > 0:
                self.data.extend(chunk[:remaining])
            if len(chunk) > remaining:
                self.overflow = True


def _execution_category(
    *,
    exit_code: int | None,
    timed_out: bool,
    stderr: str,
    output_violated: bool,
) -> Literal[
    "completed",
    "syntax_error",
    "import_error",
    "runtime_error",
    "timeout",
    "output_violation",
]:
    if timed_out:
        return "timeout"
    if output_violated:
        return "output_violation"
    if exit_code == 0:
        return "completed"
    if "SyntaxError" in stderr or "IndentationError" in stderr:
        return "syntax_error"
    if "ImportError" in stderr or "ModuleNotFoundError" in stderr:
        return "import_error"
    return "runtime_error"
