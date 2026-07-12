"""One-command, production-shaped CGR + official SWE-agent sandbox cycle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Sequence


SWE_AGENT_COMMIT = "0f3acafacabc0def8cc76b4e48acb4b6cf302cb9"
SANDBOX_TASK_ID = "cgr-full-cycle-addition-v1"
SANDBOX_MODEL = "cgr-deterministic-sandbox-model"


def sandbox_full_cycle_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the complete local CGR/SWE-agent sandbox cycle.")
    parser.add_argument(
        "--result-root", type=Path, default=Path("benchmark-results/sweagent-full-cycle-sandbox")
    )
    parser.add_argument("--sweagent-source", type=Path, default=Path(".sandbox-sweagent-src"))
    parser.add_argument(
        "--sweagent-python", type=Path, default=Path(".sandbox-sweagent-venv/Scripts/python.exe")
    )
    args = parser.parse_args(argv)
    result_root = args.result_root.absolute()
    attempt = _allocate_attempt(result_root)
    final_path = attempt / "final-result.json"
    server: ThreadingHTTPServer | None = None
    thread: threading.Thread | None = None
    started = time.perf_counter()
    result: dict[str, Any] = {
        "task_id": SANDBOX_TASK_ID,
        "infrastructure_status": "failed",
        "classification": "infrastructure_failure",
        "top_level_exit_code": 1,
        "artifact_directory": str(attempt),
    }
    try:
        source = args.sweagent_source.absolute()
        sweagent_python = args.sweagent_python.absolute()
        runtime_identity = _verify_runtime(source, sweagent_python)
        workspace = attempt / "workspace"
        runtime_root = attempt / "runtime-root"
        runtime_root.mkdir()
        _prepare_repository(workspace)
        problem_path = attempt / "problem-statement.md"
        problem_path.write_text(
            "Fix math_utils.py so add(2, 3) returns 5. Preserve the existing function and verify the change.\n",
            encoding="utf-8",
        )
        initial_status = _git(workspace, "status", "--porcelain=v1").stdout
        (attempt / "initial-git-status.txt").write_text(initial_status, encoding="utf-8")

        interaction_path = attempt / "model-interactions.jsonl"
        submission_path = _git_bash_path(runtime_root / "model.patch")
        server = _model_server(interaction_path, submission_path)
        thread = threading.Thread(target=server.serve_forever, name="sandbox-model", daemon=True)
        thread.start()
        endpoint = f"http://127.0.0.1:{server.server_port}/v1"
        _wait_for_server(endpoint)

        overlay_path = attempt / "sandbox-sweagent.yaml"
        overlay_path.write_text(_sandbox_overlay(), encoding="utf-8")
        deployed_repo_name = _git_bash_path(workspace).lstrip("/")
        adapter_command = [
            str(sweagent_python),
            "-m",
            "cgr.swebench.swe_agent_adapter",
            "--workspace",
            str(workspace),
            "--problem-file",
            str(problem_path),
            "--mode",
            "baseline",
            "--max-steps",
            "8",
            "--max-calls",
            "6",
            "--deployment-type",
            "local",
            "--deployed-repo-name",
            deployed_repo_name,
            "--config-overlay",
            str(overlay_path),
            "--repository-shared-with-agent",
        ]
        (attempt / "adapter-command.json").write_text(
            json.dumps(adapter_command, indent=2), encoding="utf-8"
        )
        environment = os.environ.copy()
        environment.update(
            {
                "CGR_DRAFT_BASE_URL": endpoint,
                "CGR_DRAFT_API_KEY": "sandbox-key",
                "CGR_DRAFT_MODEL": SANDBOX_MODEL,
                "CGR_DRAFT_MAX_MODEL_LEN": "8192",
                "CGR_SWE_AGENT_SOURCE": str(source),
                "CGR_SWE_AGENT_EXECUTABLE": str(sweagent_python.parent / "sweagent.exe"),
                "CGR_SANDBOX_WINDOWS_SWEREX": "1",
                "CGR_SANDBOX_GIT_BASH": _git_bash_executable(),
                "CGR_SANDBOX_RUNTIME_ROOT": str(runtime_root),
                "PYTHONUTF8": "1",
                "PYTHONIOENCODING": "utf-8",
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "safe.directory",
                "GIT_CONFIG_VALUE_0": str(source),
            }
        )
        environment.pop("CGR_ACTION_VALIDATOR_COMMAND", None)
        environment.pop("CGR_ACTION_VALIDATION_LOG", None)
        compat = Path(__file__).with_name("sandbox_compat").absolute()
        environment["PYTHONPATH"] = str(compat) + os.pathsep + environment.get("PYTHONPATH", "")
        (attempt / "environment.json").write_text(
            json.dumps(
                {
                    "model_endpoint": endpoint,
                    "model_identifier": SANDBOX_MODEL,
                    "api_key": "[REDACTED]",
                    "sweagent_source": str(source),
                    "sweagent_python": str(sweagent_python),
                    "deployment": "SWE-ReX local via Git Bash compatibility",
                    "action_level_interception": False,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        process = subprocess.run(
            adapter_command,
            cwd=Path.cwd(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
            check=False,
            env=environment,
        )
        (attempt / "adapter.stdout.log").write_text(process.stdout, encoding="utf-8")
        (attempt / "adapter.stderr.log").write_text(process.stderr, encoding="utf-8")
        adapter_result = _last_json_object(process.stdout)
        (attempt / "adapter-result.json").write_text(
            json.dumps(adapter_result, indent=2), encoding="utf-8"
        )
        if process.returncode or not adapter_result.get("ok"):
            raise RuntimeError(
                f"The real SWE-agent adapter failed with exit code {process.returncode}: "
                f"{adapter_result.get('error', 'unknown adapter error')}"
            )

        final_status = _git(workspace, "status", "--porcelain=v1").stdout
        diff = _git(workspace, "diff", "--binary", "HEAD", "--").stdout
        (attempt / "final-git-status.txt").write_text(final_status, encoding="utf-8")
        (attempt / "workspace.patch").write_text(diff, encoding="utf-8")
        verifier_command = [str(sweagent_python), "-m", "unittest", "-q"]
        verifier = subprocess.run(
            verifier_command,
            cwd=workspace,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
        (attempt / "verifier.stdout.log").write_text(verifier.stdout, encoding="utf-8")
        (attempt / "verifier.stderr.log").write_text(verifier.stderr, encoding="utf-8")
        (attempt / "verifier-command.json").write_text(
            json.dumps(verifier_command, indent=2), encoding="utf-8"
        )
        trajectory = _one_artifact(attempt, "*.traj")
        prediction = _optional_artifact(attempt, "*.pred")
        classification = "resolved" if diff.strip() and verifier.returncode == 0 else "tests_failed"
        result.update(
            {
                "cgr_commit": _git(Path.cwd(), "rev-parse", "HEAD").stdout.strip(),
                "sweagent_commit": SWE_AGENT_COMMIT,
                "sweagent_version": "1.1.0",
                "swe_rex_version": runtime_identity["swe_rex_version"],
                "litellm_version": runtime_identity["litellm_version"],
                "sweagent_import_path": runtime_identity["sweagent_import_path"],
                "sweagent_source_modified": False,
                "model_endpoint": endpoint,
                "model_identifier": SANDBOX_MODEL,
                "model_requests": _jsonl_count(interaction_path),
                "repository_root": str(workspace),
                "initial_repository_clean": initial_status == "",
                "termination_reason": _trajectory_exit_status(trajectory),
                "trajectory_path": str(trajectory),
                "prediction_path": str(prediction) if prediction else None,
                "patch_status": "patch" if diff.strip() else "no_patch",
                "patch_size": len(diff.encode("utf-8")),
                "verifier_command": verifier_command,
                "verifier_stdout": verifier.stdout,
                "verifier_stderr": verifier.stderr,
                "verifier_exit_code": verifier.returncode,
                "classification": classification,
                "infrastructure_status": "completed",
                "top_level_exit_code": 0,
                "elapsed_seconds": time.perf_counter() - started,
                "artifact_hash_manifest": str(attempt / "artifact-sha256.json"),
            }
        )
    except Exception as exc:
        (attempt / "failure-traceback.log").write_text(traceback.format_exc(), encoding="utf-8")
        result.update(
            {
                "error": str(exc),
                "elapsed_seconds": time.perf_counter() - started,
            }
        )
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=5)
        result["auxiliary_processes_stopped"] = thread is None or not thread.is_alive()
        final_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        _write_hash_manifest(attempt)
    print(json.dumps(result, indent=2))
    return int(result["top_level_exit_code"])


def _sandbox_overlay() -> str:
    return """env:
  post_startup_commands:
    - git config core.fileMode false
    - git diff --quiet --ignore-submodules --
    - git diff --cached --quiet --ignore-submodules --
agent:
  history_processors: []
  templates:
    system_template: |-
      You are operating a shell in a small Git repository. Follow the task and use one Bash action per response.
    instance_template: |-
      Task:
      {{problem_statement}}
    next_step_template: |-
      OBSERVATION:
      {{observation}}
  tools:
    bundles: []
    enable_bash_tool: true
    parse_function:
      type: thought_action
"""


def _model_server(
    log_path: Path,
    submission_path: str,
    actions: Sequence[str] | None = None,
    discussions: Sequence[str] | None = None,
) -> ThreadingHTTPServer:
    scripted_actions = list(actions) if actions is not None else [
        "pwd && git status --short && sed -n '1,80p' math_utils.py",
        "sed -i 's/return a - b/return a + b/' math_utils.py",
        "git diff -- math_utils.py && grep -q 'return a + b' math_utils.py",
        f"git diff --binary HEAD -- > {submission_path} && printf '<<SWE_AGENT_SUBMISSION>>\\n'",
    ]
    state = {"calls": 0}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path.rstrip("/") == "/v1/models":
                self._send({"object": "list", "data": [{"id": SANDBOX_MODEL, "object": "model"}]})
            else:
                self.send_error(404)

        def do_POST(self) -> None:
            if self.path.rstrip("/") != "/v1/chat/completions":
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
            call = state["calls"]
            state["calls"] += 1
            action = scripted_actions[min(call, len(scripted_actions) - 1)]
            discussion = (
                discussions[min(call, len(discussions) - 1)]
                if discussions
                else "Proceed with the next deterministic repository action."
            )
            response_text = f"DISCUSSION\n{discussion}\n\n```bash\n{action}\n```"
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "request_index": call + 1,
                            "messages": payload.get("messages"),
                            "response": response_text,
                        },
                        sort_keys=True,
                    )
                    + "\n"
                )
            self._send(
                {
                    "id": f"sandbox-{call + 1}",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": SANDBOX_MODEL,
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": response_text},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 32, "completion_tokens": 24, "total_tokens": 56},
                }
            )

        def log_message(self, _format: str, *_args: Any) -> None:
            return None

        def _send(self, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return ThreadingHTTPServer(("127.0.0.1", 0), Handler)


def _prepare_repository(workspace: Path) -> None:
    workspace.mkdir()
    (workspace / "math_utils.py").write_text(
        "def add(a, b):\n    return a - b\n", encoding="utf-8"
    )
    (workspace / "test_math_utils.py").write_text(
        "import unittest\n\nfrom math_utils import add\n\n\n"
        "class AddTests(unittest.TestCase):\n"
        "    def test_add(self):\n"
        "        self.assertEqual(add(2, 3), 5)\n\n\n"
        "if __name__ == '__main__':\n"
        "    unittest.main()\n",
        encoding="utf-8",
    )
    _run(["git", "init", "-q"], cwd=workspace)
    _run(["git", "config", "user.email", "sandbox@example.invalid"], cwd=workspace)
    _run(["git", "config", "user.name", "CGR Sandbox"], cwd=workspace)
    _run(["git", "add", "math_utils.py", "test_math_utils.py"], cwd=workspace)
    _run(["git", "commit", "-qm", "initial sandbox task"], cwd=workspace)


def _verify_runtime(source: Path, python: Path) -> dict[str, str]:
    if not python.is_file():
        raise RuntimeError(f"Sandbox SWE-agent Python is missing: {python}")
    if not (source / "config" / "default.yaml").is_file():
        raise RuntimeError(f"Pinned SWE-agent source is incomplete: {source}")
    commit = _git(source, "rev-parse", "HEAD").stdout.strip()
    if commit != SWE_AGENT_COMMIT:
        raise RuntimeError(f"SWE-agent commit mismatch: {commit}")
    if _git(source, "status", "--porcelain=v1").stdout:
        raise RuntimeError("Sandbox requires pristine pinned SWE-agent source.")
    probe = _run(
        [
            str(python),
            "-c",
            (
                "import importlib.metadata as m,importlib.util,json;"
                "print(json.dumps({'sweagent_version':m.version('sweagent'),"
                "'swe_rex_version':m.version('swe-rex'),'litellm_version':m.version('litellm'),"
                "'sweagent_import_path':importlib.util.find_spec('sweagent').origin}))"
            ),
        ],
        env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
    )
    try:
        identity = json.loads(probe.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Sandbox SWE-agent identity probe returned malformed JSON.") from exc
    if identity.get("sweagent_version") != "1.1.0" or str(source).lower() not in str(
        identity.get("sweagent_import_path", "")
    ).lower():
        raise RuntimeError("The sandbox Python does not import pinned editable SWE-agent v1.1.0.")
    return {key: str(value) for key, value in identity.items()}


def _allocate_attempt(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    numbers = [int(path.name.split("-")[-1]) for path in root.glob("attempt-*") if path.is_dir()]
    attempt = root / f"attempt-{max(numbers, default=0) + 1:03d}"
    attempt.mkdir()
    return attempt


def _run(
    command: Sequence[str], *, cwd: Path | None = None, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(command), cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if result.returncode:
        raise RuntimeError(f"Command failed ({result.returncode}): {command!r}\n{result.stderr}")
    return result


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return _run(["git", "-c", f"safe.directory={cwd.absolute()}", *args], cwd=cwd)


def _git_bash_executable() -> str:
    candidates = (shutil.which("bash"), "C:/Program Files/Git/bin/bash.exe")
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate).absolute())
    raise RuntimeError("Git Bash is required for the Windows SWE-ReX sandbox.")


def _git_bash_path(path: Path) -> str:
    value = path.absolute().as_posix()
    if len(value) > 2 and value[1] == ":":
        return f"/{value[0].lower()}{value[2:]}"
    return value


def _wait_for_server(endpoint: str) -> None:
    import urllib.request

    for _ in range(30):
        try:
            with urllib.request.urlopen(endpoint + "/models", timeout=1) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("Deterministic model endpoint did not start.")


def _last_json_object(stdout: str) -> dict[str, Any]:
    for line in reversed(stdout.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise RuntimeError("Adapter stdout contained no structured result JSON.")


def _one_artifact(root: Path, pattern: str) -> Path:
    paths = sorted(root.rglob(pattern))
    if not paths:
        raise RuntimeError(f"Required SWE-agent artifact is missing: {pattern}")
    return paths[-1]


def _optional_artifact(root: Path, pattern: str) -> Path | None:
    paths = sorted(root.rglob(pattern))
    return paths[-1] if paths else None


def _trajectory_exit_status(path: Path) -> str | None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    info = payload.get("info") if isinstance(payload, dict) else None
    value = info.get("exit_status") if isinstance(info, dict) else None
    return value if isinstance(value, str) else None


def _jsonl_count(path: Path) -> int:
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def _write_hash_manifest(root: Path) -> None:
    manifest: dict[str, str] = {}
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        if path.name == "artifact-sha256.json":
            continue
        manifest[str(path.relative_to(root)).replace("\\", "/")] = hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
    (root / "artifact-sha256.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(sandbox_full_cycle_main())
