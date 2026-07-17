"""Bounded whole-process supervision for pristine SWE-agent."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .redaction import sanitize_text


@dataclass(frozen=True)
class BoundedProcessResult:
    exit_code: int | None
    timed_out: bool
    elapsed_seconds: float
    stdout: str
    stderr: str


def run_bounded_process(
    command: Sequence[str],
    *,
    cwd: Path,
    environment: dict[str, str],
    timeout_seconds: int,
    maximum_output_bytes: int,
    secrets: tuple[str, ...],
    heartbeat_seconds: int,
    heartbeat: Callable[[], None],
) -> BoundedProcessResult:
    started = time.monotonic()
    process = subprocess.Popen(
        list(command),
        cwd=cwd,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
        start_new_session=os.name != "nt",
        creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0),
    )
    stdout_buffer = bytearray()
    stderr_buffer = bytearray()
    buffer_lock = threading.Lock()
    output_exceeded = threading.Event()

    def drain(stream: object, destination: bytearray) -> None:
        reader = getattr(stream, "read")
        while chunk := reader(64 * 1024):
            with buffer_lock:
                remaining = (
                    maximum_output_bytes + 1 - (len(stdout_buffer) + len(stderr_buffer))
                )
                if remaining > 0:
                    destination.extend(chunk[:remaining])
                if len(chunk) > remaining or (
                    len(stdout_buffer) + len(stderr_buffer) > maximum_output_bytes
                ):
                    output_exceeded.set()
                    return

    readers = (
        threading.Thread(
            target=drain, args=(process.stdout, stdout_buffer), daemon=True
        ),
        threading.Thread(
            target=drain, args=(process.stderr, stderr_buffer), daemon=True
        ),
    )
    for reader in readers:
        reader.start()
    timed_out = False
    while process.poll() is None:
        elapsed = time.monotonic() - started
        if output_exceeded.is_set():
            _terminate_process_group(process)
            break
        if elapsed >= timeout_seconds:
            timed_out = True
            _terminate_process_group(process)
            break
        heartbeat()
        time.sleep(min(heartbeat_seconds, 0.25))
    if process.poll() is None:
        _terminate_process_group(process)
    for reader in readers:
        reader.join(timeout=5)
    if any(reader.is_alive() for reader in readers):
        process.kill()
        raise ValueError("SWE-agent output streams did not close after termination.")
    if output_exceeded.is_set():
        raise ValueError("SWE-agent process output exceeded its byte budget.")
    stdout_raw = bytes(stdout_buffer)
    stderr_raw = bytes(stderr_buffer)
    stdout = sanitize_text(stdout_raw.decode("utf-8", errors="replace"), secrets)
    stderr = sanitize_text(stderr_raw.decode("utf-8", errors="replace"), secrets)
    return BoundedProcessResult(
        exit_code=process.returncode,
        timed_out=timed_out,
        elapsed_seconds=time.monotonic() - started,
        stdout=stdout,
        stderr=stderr,
    )


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            getattr(os, "killpg")(process.pid, signal.SIGTERM)
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        if os.name != "nt":
            try:
                getattr(os, "killpg")(process.pid, getattr(signal, "SIGKILL"))
            except OSError:
                pass
        process.kill()
