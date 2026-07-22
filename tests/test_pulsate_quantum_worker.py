from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

import pytest

from cgr.pulsate_api.app import _load_preset
from cgr.pulsate_api.quantum_worker import (
    WORKER_EXIT_COMPLETED,
    WORKER_EXIT_FAILED,
    WORKER_EXIT_TIMED_OUT,
    WORKER_EXIT_VERIFICATION_FAILED,
    WORKER_RESULT_MAXIMUM_BYTES,
    WORKER_RESULT_SCHEMA,
    run_worker,
)
from cgr.pulsate_api.runs import (
    WORKER_LOG_MAXIMUM_BYTES,
    ExecutionOutput,
    ExistingQuantumPreflightExecutor,
)
from cgr.quantum_preflight.artifacts import write_json_atomic
from cgr.quantum_preflight.errors import QuantumTimeoutError, QuantumVerificationError


ROOT = Path(__file__).resolve().parents[1]
RUN_IDENTIFIER = "run-" + "a" * 32


def _worker_layout(tmp_path: Path) -> tuple[Any, Path, Path, Path, Path]:
    manifest = _load_preset("h2-ground-state-v1")
    run_directory = tmp_path / RUN_IDENTIFIER
    worker_directory = run_directory / "quantum-worker"
    worker_directory.mkdir(parents=True)
    manifest_path = worker_directory / "manifest.json"
    result_root = run_directory / "runner-artifacts"
    result_envelope = worker_directory / "result.json"
    write_json_atomic(
        manifest_path, manifest.model_dump(mode="json"), maximum_bytes=2 * 1024 * 1024
    )
    return manifest, manifest_path, result_root, result_envelope, run_directory


def _read_result(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_worker_executes_validated_manifest_on_main_thread_and_forwards_timeout(
    tmp_path: Path,
) -> None:
    manifest, manifest_path, result_root, result_envelope, _ = _worker_layout(tmp_path)
    observed: dict[str, Any] = {}

    def runner(received: Any, **arguments: Any) -> dict[str, Any]:
        observed.update(arguments)
        observed["manifest"] = received
        observed["thread"] = threading.current_thread()
        return {"authorized": True, "receipt_path": str(result_root / "receipt.json")}

    exit_code = run_worker(
        manifest_path=manifest_path,
        result_root=result_root,
        lock_path=ROOT / "requirements/quantum-preflight.lock",
        image_identifier="sha256:controlled",
        maximum_seconds=37,
        result_envelope_path=result_envelope,
        runner=runner,
    )

    assert exit_code == WORKER_EXIT_COMPLETED
    assert observed["manifest"] == manifest
    assert observed["thread"] is threading.main_thread()
    assert observed["maximum_seconds"] == 37
    assert observed["result_root"] == result_root
    result = _read_result(result_envelope)
    assert result["schema_version"] == WORKER_RESULT_SCHEMA
    assert result["outcome"] == "completed"
    assert result["summary"]["authorized"] is True
    assert result_envelope.stat().st_size <= WORKER_RESULT_MAXIMUM_BYTES
    assert not result_envelope.with_name(".result.json.tmp").exists()


@pytest.mark.parametrize(
    ("error", "outcome", "exit_code"),
    [
        (QuantumVerificationError("not authorized"), "verification_failed", WORKER_EXIT_VERIFICATION_FAILED),
        (QuantumTimeoutError("bounded timeout"), "timed_out", WORKER_EXIT_TIMED_OUT),
        (RuntimeError("controlled failure"), "failed", WORKER_EXIT_FAILED),
    ],
)
def test_worker_writes_bounded_structured_failure_envelopes(
    tmp_path: Path, error: Exception, outcome: str, exit_code: int,
) -> None:
    _, manifest_path, result_root, result_envelope, _ = _worker_layout(tmp_path)

    def runner(*args: Any, **kwargs: Any) -> dict[str, Any]:
        del args, kwargs
        raise error

    observed_exit = run_worker(
        manifest_path=manifest_path,
        result_root=result_root,
        lock_path=ROOT / "requirements/quantum-preflight.lock",
        image_identifier="sha256:controlled",
        maximum_seconds=11,
        result_envelope_path=result_envelope,
        runner=runner,
    )

    result = _read_result(result_envelope)
    assert observed_exit == exit_code
    assert result == {
        "error": {"error_type": type(error).__name__, "message": str(error)},
        "outcome": outcome,
        "schema_version": WORKER_RESULT_SCHEMA,
        "summary": None,
    }
    assert "traceback" not in result
    assert result_envelope.stat().st_size <= WORKER_RESULT_MAXIMUM_BYTES


def test_worker_rejects_invalid_manifest_before_calling_runner(tmp_path: Path) -> None:
    _, manifest_path, result_root, result_envelope, _ = _worker_layout(tmp_path)
    manifest_path.write_text("{}", encoding="utf-8")
    called = False

    def runner(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal called
        del args, kwargs
        called = True
        return {}

    exit_code = run_worker(
        manifest_path=manifest_path,
        result_root=result_root,
        lock_path=ROOT / "requirements/quantum-preflight.lock",
        image_identifier="sha256:controlled",
        maximum_seconds=11,
        result_envelope_path=result_envelope,
        runner=runner,
    )

    assert exit_code == WORKER_EXIT_FAILED
    assert not called
    assert _read_result(result_envelope)["outcome"] == "failed"


class _ControlledProcess:
    def __init__(self, return_code: int) -> None:
        self.return_code = return_code
        self.killed = False
        self.wait_calls = 0
        self.stdout = io.BytesIO()
        self.stderr = io.BytesIO()

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self.wait_calls += 1
        return self.return_code

    def kill(self) -> None:
        self.killed = True


def _argument(command: list[str], name: str) -> str:
    return command[command.index(name) + 1]


def test_parent_subprocess_command_uses_only_controlled_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _load_preset("h2-ground-state-v1")
    run_directory = tmp_path / RUN_IDENTIFIER
    run_directory.mkdir()
    captured: dict[str, Any] = {}

    def process_factory(command: list[str], **options: Any) -> _ControlledProcess:
        captured.update(command=command, options=options)
        result_root = Path(_argument(command, "--result-root"))
        artifact_directory = result_root / "controlled" / "run-001"
        artifact_directory.mkdir(parents=True)
        write_json_atomic(
            Path(_argument(command, "--result-envelope")),
            {
                "schema_version": WORKER_RESULT_SCHEMA,
                "outcome": "completed",
                "summary": {"receipt_path": str(artifact_directory / "receipt.json")},
                "error": None,
            },
            maximum_bytes=WORKER_RESULT_MAXIMUM_BYTES,
        )
        return _ControlledProcess(WORKER_EXIT_COMPLETED)

    expected = ExecutionOutput({}, {}, {}, {})
    monkeypatch.setattr(
        ExistingQuantumPreflightExecutor,
        "_project",
        staticmethod(lambda *args, **kwargs: expected),
    )
    executor = ExistingQuantumPreflightExecutor(
        repository_root=ROOT,
        image_identifier="sha256:server-controlled",
        _process_factory=process_factory,
    )
    assert executor.execute(
        manifest,
        preset_identifier="h2-ground-state-v1",
        run_directory=run_directory,
        maximum_seconds=29,
    ) is expected

    command = captured["command"]
    options = captured["options"]
    assert command[:3] == [sys.executable, "-m", "cgr.pulsate_api.quantum_worker"]
    assert options["shell"] is False
    assert options["cwd"] == ROOT
    if os.name == "posix":
        assert options["start_new_session"] is True
    else:
        assert "start_new_session" not in options
    assert _argument(command, "--manifest-json") == str(
        run_directory / "quantum-worker/manifest.json"
    )
    assert _argument(command, "--result-root") == str(run_directory / "runner-artifacts")
    assert _argument(command, "--scientific-lock") == str(
        ROOT / "requirements/quantum-preflight.lock"
    )
    assert _argument(command, "--result-envelope") == str(
        run_directory / "quantum-worker/result.json"
    )
    assert _argument(command, "--maximum-seconds") == "29"
    assert "h2-ground-state-v1" not in command
    assert (run_directory / "quantum-worker/stdout.log").is_file()
    assert (run_directory / "quantum-worker/stderr.log").is_file()


def test_parent_timeout_kills_and_collects_real_child_without_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _load_preset("h2-ground-state-v1")
    run_directory = tmp_path / RUN_IDENTIFIER
    run_directory.mkdir()
    observed: dict[str, subprocess.Popen[Any]] = {}
    projected = False

    def sleeper_factory(command: list[str], **options: Any) -> subprocess.Popen[Any]:
        del command
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import os,time; os.write(1,b'before-timeout'); "
                "os.write(2,b'timeout-error'); time.sleep(60)",
            ],
            **options,
        )
        observed["process"] = process
        return process

    def forbidden_projection(*args: Any, **kwargs: Any) -> ExecutionOutput:
        nonlocal projected
        del args, kwargs
        projected = True
        raise AssertionError("Timed-out evidence must not be projected.")

    monkeypatch.setattr(
        ExistingQuantumPreflightExecutor, "_project", staticmethod(forbidden_projection)
    )
    executor = ExistingQuantumPreflightExecutor(
        repository_root=ROOT,
        image_identifier="sha256:server-controlled",
        _process_factory=sleeper_factory,
        _worker_timeout_override_seconds=0.1,
    )
    with pytest.raises(QuantumTimeoutError, match="outer process timeout") as raised:
        executor.execute(
            manifest,
            preset_identifier="h2-ground-state-v1",
            run_directory=run_directory,
            maximum_seconds=29,
        )
    assert observed["process"].poll() is not None
    assert not projected
    assert not os.path.isabs(str(raised.value))
    worker_directory = run_directory / "quantum-worker"
    assert (worker_directory / "stdout.log").read_bytes() == b"before-timeout"
    assert (worker_directory / "stderr.log").read_bytes() == b"timeout-error"
    assert not any(
        thread.name.startswith(f"pulsate-worker-log-{RUN_IDENTIFIER}")
        for thread in threading.enumerate()
    )


def test_worker_output_is_drained_without_deadlock_and_persisted_at_exact_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = _load_preset("h2-ground-state-v1")
    run_directory = tmp_path / RUN_IDENTIFIER
    run_directory.mkdir()

    def noisy_factory(command: list[str], **options: Any) -> subprocess.Popen[Any]:
        result_root = Path(_argument(command, "--result-root"))
        artifact_directory = result_root / "controlled" / "run-001"
        artifact_directory.mkdir(parents=True)
        write_json_atomic(
            Path(_argument(command, "--result-envelope")),
            {
                "schema_version": WORKER_RESULT_SCHEMA,
                "outcome": "completed",
                "summary": {"receipt_path": str(artifact_directory / "receipt.json")},
                "error": None,
            },
            maximum_bytes=WORKER_RESULT_MAXIMUM_BYTES,
        )
        output_size = WORKER_LOG_MAXIMUM_BYTES + 128 * 1024
        code = (
            "import os;"
            f"os.write(1,b'o'*{output_size});"
            f"os.write(2,b'e'*{output_size})"
        )
        return subprocess.Popen([sys.executable, "-c", code], **options)

    expected = ExecutionOutput({}, {}, {}, {})
    monkeypatch.setattr(
        ExistingQuantumPreflightExecutor,
        "_project",
        staticmethod(lambda *args, **kwargs: expected),
    )
    output = ExistingQuantumPreflightExecutor(
        repository_root=ROOT,
        image_identifier="sha256:server-controlled",
        _process_factory=noisy_factory,
    ).execute(
        manifest,
        preset_identifier="h2-ground-state-v1",
        run_directory=run_directory,
        maximum_seconds=10,
    )
    assert output is expected
    for name in ("stdout.log", "stderr.log"):
        data = (run_directory / "quantum-worker" / name).read_bytes()
        assert len(data) == WORKER_LOG_MAXIMUM_BYTES
        assert data.endswith(b"...[worker output truncated]...\n")


@pytest.mark.skipif(
    os.name != "posix" or not hasattr(os, "killpg"),
    reason="POSIX process groups are unavailable.",
)
def test_posix_timeout_terminates_worker_and_long_lived_descendant(
    tmp_path: Path,
) -> None:
    manifest = _load_preset("h2-ground-state-v1")
    run_directory = tmp_path / RUN_IDENTIFIER
    run_directory.mkdir()
    descendant_pid_path = tmp_path / "descendant.pid"
    observed: dict[str, subprocess.Popen[Any]] = {}

    def descendant_factory(
        command: list[str], **options: Any,
    ) -> subprocess.Popen[Any]:
        del command
        code = (
            "import pathlib,subprocess,sys,time;"
            "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
            f"pathlib.Path({str(descendant_pid_path)!r}).write_text(str(child.pid));"
            "time.sleep(60)"
        )
        process = subprocess.Popen([sys.executable, "-c", code], **options)
        observed["process"] = process
        return process

    executor = ExistingQuantumPreflightExecutor(
        repository_root=ROOT,
        image_identifier="sha256:server-controlled",
        _process_factory=descendant_factory,
        _worker_timeout_override_seconds=0.5,
    )
    with pytest.raises(QuantumTimeoutError, match="outer process timeout"):
        executor.execute(
            manifest,
            preset_identifier="h2-ground-state-v1",
            run_directory=run_directory,
            maximum_seconds=29,
        )
    assert observed["process"].poll() is not None
    descendant_pid = int(descendant_pid_path.read_text(encoding="utf-8"))

    def descendant_alive() -> bool:
        try:
            os.kill(descendant_pid, 0)
        except ProcessLookupError:
            return False
        proc_status = Path(f"/proc/{descendant_pid}/stat")
        if proc_status.is_file():
            fields = proc_status.read_text(encoding="utf-8").split()
            if len(fields) > 2 and fields[2] == "Z":
                return False
        return True

    deadline = time.monotonic() + 2
    while descendant_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not descendant_alive()


def _invalid_result_factory(
    writer: Callable[[list[str]], None], return_code: int,
) -> Callable[..., _ControlledProcess]:
    def factory(command: list[str], **options: Any) -> _ControlledProcess:
        del options
        writer(command)
        return _ControlledProcess(return_code)

    return factory


@pytest.mark.parametrize("case", ["missing", "oversized", "malformed", "schema", "mismatch", "escaped"])
def test_parent_rejects_invalid_worker_evidence(tmp_path: Path, case: str) -> None:
    manifest = _load_preset("h2-ground-state-v1")
    run_directory = tmp_path / RUN_IDENTIFIER
    run_directory.mkdir()

    def writer(command: list[str]) -> None:
        envelope = Path(_argument(command, "--result-envelope"))
        if case == "missing":
            return
        if case == "oversized":
            envelope.write_bytes(b"x" * (WORKER_RESULT_MAXIMUM_BYTES + 1))
            return
        if case == "malformed":
            envelope.write_text("{", encoding="utf-8")
            return
        if case == "schema":
            document = {
                "schema_version": "unsupported/9.9.9",
                "outcome": "failed",
                "summary": None,
                "error": {"error_type": "Failure", "message": "failed"},
            }
        elif case == "mismatch":
            document = {
                "schema_version": WORKER_RESULT_SCHEMA,
                "outcome": "failed",
                "summary": None,
                "error": {"error_type": "Failure", "message": "failed"},
            }
        else:
            result_root = Path(_argument(command, "--result-root"))
            result_root.mkdir(parents=True)
            escaped = tmp_path / "outside"
            escaped.mkdir()
            document = {
                "schema_version": WORKER_RESULT_SCHEMA,
                "outcome": "completed",
                "summary": {"receipt_path": str(escaped / "receipt.json")},
                "error": None,
            }
        write_json_atomic(envelope, document, maximum_bytes=WORKER_RESULT_MAXIMUM_BYTES)

    return_code = WORKER_EXIT_COMPLETED if case != "schema" else WORKER_EXIT_FAILED
    executor = ExistingQuantumPreflightExecutor(
        repository_root=ROOT,
        image_identifier="sha256:server-controlled",
        _process_factory=_invalid_result_factory(writer, return_code),
    )
    with pytest.raises((ValueError, json.JSONDecodeError)):
        executor.execute(
            manifest,
            preset_identifier="h2-ground-state-v1",
            run_directory=run_directory,
            maximum_seconds=29,
        )


def test_parent_rejects_symlinked_worker_envelope(tmp_path: Path) -> None:
    manifest = _load_preset("h2-ground-state-v1")
    run_directory = tmp_path / RUN_IDENTIFIER
    run_directory.mkdir()

    def writer(command: list[str]) -> None:
        outside = tmp_path / "outside-result.json"
        outside.write_text("{}", encoding="utf-8")
        try:
            Path(_argument(command, "--result-envelope")).symlink_to(outside)
        except OSError:
            pytest.skip("Creating file symbolic links is unavailable in this environment.")

    executor = ExistingQuantumPreflightExecutor(
        repository_root=ROOT,
        image_identifier="sha256:server-controlled",
        _process_factory=_invalid_result_factory(writer, WORKER_EXIT_FAILED),
    )
    with pytest.raises(ValueError, match="symbolic link"):
        executor.execute(
            manifest,
            preset_identifier="h2-ground-state-v1",
            run_directory=run_directory,
            maximum_seconds=29,
        )


@pytest.mark.parametrize("case", ["wrong_name", "escaped_worker", "existing"])
def test_invalid_envelope_destination_is_never_created_or_replaced(
    tmp_path: Path, case: str,
) -> None:
    _, manifest_path, result_root, result_envelope, _ = _worker_layout(tmp_path)
    if case == "wrong_name":
        destination = result_envelope.with_name("wrong-result.json")
    elif case == "escaped_worker":
        escaped_worker = tmp_path / "outside" / "quantum-worker"
        escaped_worker.mkdir(parents=True)
        destination = escaped_worker / "result.json"
    else:
        destination = result_envelope
        destination.write_bytes(b"existing-envelope")
    before = destination.read_bytes() if destination.exists() else None

    exit_code = run_worker(
        manifest_path=manifest_path,
        result_root=result_root,
        lock_path=ROOT / "requirements/quantum-preflight.lock",
        image_identifier="sha256:controlled",
        maximum_seconds=11,
        result_envelope_path=destination,
        runner=lambda *args, **kwargs: {},
    )

    assert exit_code == WORKER_EXIT_FAILED
    if before is None:
        assert not destination.exists()
    else:
        assert destination.read_bytes() == before


def test_symlinked_envelope_parent_is_rejected_without_writing(tmp_path: Path) -> None:
    run_directory = tmp_path / RUN_IDENTIFIER
    run_directory.mkdir()
    real_worker = tmp_path / "real-worker"
    real_worker.mkdir()
    linked_worker = run_directory / "quantum-worker"
    try:
        linked_worker.symlink_to(real_worker, target_is_directory=True)
    except OSError:
        pytest.skip("Creating directory symbolic links is unavailable in this environment.")
    destination = linked_worker / "result.json"

    exit_code = run_worker(
        manifest_path=linked_worker / "manifest.json",
        result_root=run_directory / "runner-artifacts",
        lock_path=ROOT / "requirements/quantum-preflight.lock",
        image_identifier="sha256:controlled",
        maximum_seconds=11,
        result_envelope_path=destination,
        runner=lambda *args, **kwargs: {},
    )

    assert exit_code == WORKER_EXIT_FAILED
    assert not destination.exists()
    assert not (real_worker / "result.json").exists()


def test_result_root_validation_failure_writes_controlled_failed_envelope(
    tmp_path: Path,
) -> None:
    _, manifest_path, _, result_envelope, _ = _worker_layout(tmp_path)
    escaped_result_root = tmp_path / "outside" / "runner-artifacts"
    called = False

    def runner(*args: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal called
        del args, kwargs
        called = True
        return {}

    exit_code = run_worker(
        manifest_path=manifest_path,
        result_root=escaped_result_root,
        lock_path=ROOT / "requirements/quantum-preflight.lock",
        image_identifier="sha256:controlled",
        maximum_seconds=11,
        result_envelope_path=result_envelope,
        runner=runner,
    )

    assert exit_code == WORKER_EXIT_FAILED
    assert not called
    result = _read_result(result_envelope)
    assert result["outcome"] == "failed"
    assert result["error"]["error_type"] == "FileNotFoundError"
