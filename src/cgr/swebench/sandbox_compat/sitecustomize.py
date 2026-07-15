"""Windows Git-Bash transport for SWE-ReX's real local runtime.

Activated only in the full-cycle sandbox subprocess. It leaves pristine
SWE-agent source untouched and adapts the POSIX-only local session transport to
the Git Bash executable available on this Windows host.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any


if os.getenv("CGR_PHASE_GATE_CONFIG"):
    from cgr.swebench.phase_gate import install_sweagent_phase_gate

    install_sweagent_phase_gate()


if os.getenv("CGR_SANDBOX_WINDOWS_SWEREX") == "1":
    import pexpect  # type: ignore[import-untyped]

    # SWE-ReX's unused POSIX BashSession evaluates this annotation on import.
    pexpect.spawn = object  # type: ignore[attr-defined]

    import swerex.runtime.local as _local  # type: ignore[import-not-found]
    from swerex.exceptions import (  # type: ignore[import-not-found]
        BashIncorrectSyntaxError,
        CommandTimeoutError,
        NonZeroExitCodeError,
        SessionExistsError,
    )
    from swerex.runtime.abstract import (  # type: ignore[import-not-found]
        BashAction,
        BashInterruptAction,
        BashObservation,
        CloseBashSessionResponse,
        CreateBashSessionRequest,
        CreateBashSessionResponse,
        ReadFileResponse,
        UploadResponse,
        WriteFileResponse,
    )

    _BASH = os.environ["CGR_SANDBOX_GIT_BASH"]
    _RUNTIME_ROOT = Path(os.environ["CGR_SANDBOX_RUNTIME_ROOT"])
    _STATUS = "__CGR_SWEREX_STATUS__"
    _CWD = "__CGR_SWEREX_CWD__"

    def _host_path(value: str) -> Path:
        if value == "/root":
            return _RUNTIME_ROOT
        if value.startswith("/root/"):
            return _RUNTIME_ROOT / value.removeprefix("/root/")
        drive_path = re.fullmatch(r"/([a-zA-Z])/(.*)", value)
        if drive_path:
            return Path(f"{drive_path.group(1)}:/{drive_path.group(2)}")
        return Path(value)

    def _posix_path(value: Path) -> str:
        path = value.absolute().as_posix()
        return f"/{path[0].lower()}{path[2:]}" if len(path) > 2 and path[1] == ":" else path

    def _check_bash_command(command: str) -> None:
        result = subprocess.run(
            [_BASH, "--noprofile", "--norc", "-n"],
            input=command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.returncode:
            raise BashIncorrectSyntaxError(
                "Git Bash rejected the proposed action.",
                extra_info={"bash_stdout": result.stdout, "bash_stderr": result.stderr},
            )

    class WindowsGitBashSession:
        def __init__(self, request: CreateBashSessionRequest) -> None:
            self.request = request
            self.cwd = _posix_path(Path.cwd())
            self.environment = os.environ.copy()

        async def start(self) -> CreateBashSessionResponse:
            return CreateBashSessionResponse(output="")

        async def run(self, action: BashAction | BashInterruptAction) -> BashObservation:
            if isinstance(action, BashInterruptAction):
                return BashObservation(output="", exit_code=130, expect_string="interrupt")
            _check_bash_command(action.command)
            script = (
                f"cd -- {shlex.quote(self.cwd)}\n"
                f"{action.command}\n"
                "_cgr_status=$?\n"
                f"printf '\\n{_STATUS}%s\\n' \"$_cgr_status\"\n"
                f"printf '{_CWD}%s\\n' \"$PWD\"\n"
            )
            try:
                result = subprocess.run(
                    [_BASH, "--noprofile", "--norc"],
                    input=script,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=self.environment,
                    timeout=action.timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise CommandTimeoutError(
                    f"timeout after {action.timeout} seconds while running {action.command!r}"
                ) from exc
            status_match = re.search(rf"{_STATUS}(\d+)", result.stdout)
            cwd_match = re.search(rf"{_CWD}([^\r\n]+)", result.stdout)
            if status_match is None or cwd_match is None:
                raise RuntimeError("Git Bash sandbox transport did not return command metadata.")
            exit_code = int(status_match.group(1))
            self.cwd = cwd_match.group(1)
            output = result.stdout[: status_match.start()].rstrip("\r\n")
            if action.check == "raise" and exit_code != 0:
                raise NonZeroExitCodeError(
                    f"Command {action.command!r} failed with exit code {exit_code}: {output}"
                )
            return BashObservation(
                output=output,
                exit_code=exit_code,
                expect_string="git-bash-complete",
            )

        async def close(self) -> CloseBashSessionResponse:
            return CloseBashSessionResponse()

    async def _create_session(self: Any, request: Any) -> CreateBashSessionResponse:
        if request.session in self.sessions:
            raise SessionExistsError(f"session {request.session} already exists")
        if not isinstance(request, CreateBashSessionRequest):
            raise ValueError(f"Unsupported sandbox session request: {request!r}")
        session = WindowsGitBashSession(request)
        self.sessions[request.session] = session
        return await session.start()

    async def _read_file(self: Any, request: Any) -> ReadFileResponse:
        path = _host_path(str(request.path))
        return ReadFileResponse(
            content=path.read_text(encoding=request.encoding, errors=request.errors)
        )

    async def _write_file(self: Any, request: Any) -> WriteFileResponse:
        path = _host_path(str(request.path))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(request.content, encoding="utf-8")
        return WriteFileResponse()

    async def _upload(self: Any, request: Any) -> UploadResponse:
        raise RuntimeError("The Windows sandbox uses a preexisting repository; upload is disabled.")

    _local._check_bash_command = _check_bash_command
    _local.LocalRuntime.create_session = _create_session  # type: ignore[method-assign]
    _local.LocalRuntime.read_file = _read_file  # type: ignore[method-assign]
    _local.LocalRuntime.write_file = _write_file  # type: ignore[method-assign]
    _local.LocalRuntime.upload = _upload  # type: ignore[method-assign]
