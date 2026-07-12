"""One-task QuixBugs pilot using the proven CGR + official SWE-agent cycle."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import threading
import time
import traceback
from pathlib import Path
from typing import Any

from cgr.swebench import sandbox_full_cycle as cycle


DEFAULT_MANIFEST = Path("benchmark-manifests/quixbugs-python-pilot-v1.json")
DEFAULT_RESULT_ROOT = Path("benchmark-results/quixbugs-python-pilot-v1")


def quixbugs_pilot_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one pinned Python QuixBugs task through SWE-agent.")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--quixbugs-root", type=Path, required=True)
    parser.add_argument("--result-root", type=Path, default=DEFAULT_RESULT_ROOT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--deterministic-model", action="store_true")
    parser.add_argument("--deployment-type", choices=("docker", "local"))
    parser.add_argument(
        "--sweagent-source",
        type=Path,
        default=Path(os.getenv("CGR_SWE_AGENT_SOURCE", ".sandbox-sweagent-src")),
    )
    parser.add_argument(
        "--sweagent-python",
        type=Path,
        default=Path(os.getenv("CGR_SWE_AGENT_PYTHON", ".sandbox-sweagent-venv/Scripts/python.exe")),
    )
    args = parser.parse_args(argv)

    attempt = cycle._allocate_attempt(args.result_root.absolute() / args.task_id)
    final_path = attempt / "final-result.json"
    server = None
    server_thread: threading.Thread | None = None
    started = time.perf_counter()
    result: dict[str, Any] = {
        "task_id": args.task_id,
        "benchmark": "QuixBugs",
        "artifact_directory": str(attempt),
        "classification": "infrastructure_error",
        "infrastructure_status": "failed",
        "top_level_exit_code": 1,
    }
    try:
        task, manifest = _load_task(args.manifest.absolute(), args.task_id)
        source_root = args.quixbugs_root.absolute()
        sweagent_source = args.sweagent_source.absolute()
        sweagent_python = args.sweagent_python.absolute()
        runtime_identity = cycle._verify_runtime(sweagent_source, sweagent_python)
        _verify_quixbugs_checkout(source_root, task)
        (attempt / "task-manifest.json").write_text(
            json.dumps(task, indent=2), encoding="utf-8"
        )
        (attempt / "manifest-snapshot.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
        (attempt / "quixbugs-commit.txt").write_text(
            str(task["pinned_commit"]) + "\n", encoding="utf-8"
        )
        problem_path = attempt / "problem-statement.md"
        problem_path.write_text(str(task["problem_statement"]) + "\n", encoding="utf-8")

        verifier_command = _verifier_command(task, sweagent_python)
        pre_verifier = _run_verifier(verifier_command, source_root, int(task["timeout_seconds"]))
        _write_process_artifacts(attempt, "pre-agent-verifier", verifier_command, pre_verifier)
        if pre_verifier.returncode == 0:
            raise RuntimeError("Selected QuixBugs task does not fail before the agent run.")

        workspace = attempt / "workspace"
        _clone_attempt(source_root, workspace, str(task["pinned_commit"]))
        initial_status = cycle._git(workspace, "status", "--porcelain=v1").stdout
        (attempt / "initial-git-status.txt").write_text(initial_status, encoding="utf-8")
        if initial_status:
            raise RuntimeError("Disposable QuixBugs workspace is not initially clean.")

        runtime_root = attempt / "runtime-root"
        runtime_root.mkdir()
        endpoint, model, api_key = _configured_model()
        interaction_path: Path | None = None
        if args.deterministic_model:
            interaction_path = attempt / "model-interactions.jsonl"
            actions = _deterministic_actions(task, sweagent_python, runtime_root)
            server = cycle._model_server(
                interaction_path,
                cycle._git_bash_path(runtime_root / "model.patch"),
                actions=actions,
            )
            server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            server_thread.start()
            endpoint = f"http://127.0.0.1:{server.server_port}/v1"
            model = cycle.SANDBOX_MODEL
            api_key = "quixbugs-local-key"
            cycle._wait_for_server(endpoint)

        overlay_path = attempt / "sweagent-config.yaml"
        overlay_path.write_text(cycle._sandbox_overlay(), encoding="utf-8")
        deployment_type = args.deployment_type or ("local" if os.name == "nt" else "docker")
        adapter_command = _adapter_command(
            sweagent_python,
            workspace,
            problem_path,
            overlay_path,
            deployment_type,
        )
        (attempt / "adapter-command.json").write_text(
            json.dumps(adapter_command, indent=2), encoding="utf-8"
        )
        environment = _adapter_environment(
            endpoint=endpoint,
            model=model,
            api_key=api_key,
            sweagent_source=sweagent_source,
            sweagent_python=sweagent_python,
            runtime_root=runtime_root,
            deployment_type=deployment_type,
        )
        (attempt / "environment.json").write_text(
            json.dumps(
                {
                    "model_endpoint": endpoint,
                    "model_identifier": model,
                    "api_key": "[REDACTED]",
                    "deployment_type": deployment_type,
                    "sweagent_source": str(sweagent_source),
                    "sweagent_python": str(sweagent_python),
                    "action_level_interception": False,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        adapter = subprocess.run(
            adapter_command,
            cwd=Path.cwd(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=int(task["timeout_seconds"]),
            check=False,
            env=environment,
        )
        (attempt / "adapter.stdout.log").write_text(adapter.stdout, encoding="utf-8")
        (attempt / "adapter.stderr.log").write_text(adapter.stderr, encoding="utf-8")
        adapter_result = cycle._last_json_object(adapter.stdout)
        (attempt / "adapter-result.json").write_text(
            json.dumps(adapter_result, indent=2), encoding="utf-8"
        )

        final_status = cycle._git(workspace, "status", "--porcelain=v1").stdout
        diff = cycle._git(workspace, "diff", "--binary", "HEAD", "--").stdout
        (attempt / "final-git-status.txt").write_text(final_status, encoding="utf-8")
        (attempt / "workspace.patch").write_text(diff, encoding="utf-8")
        trajectory = cycle._optional_artifact(attempt, "*.traj")
        prediction = cycle._optional_artifact(attempt, "*.pred")
        submitted_patch = cycle._optional_artifact(attempt, "*.patch")
        termination = cycle._trajectory_exit_status(trajectory) if trajectory else None

        verifier: subprocess.CompletedProcess[str] | None = None
        classification: str
        if adapter.returncode == 0 and adapter_result.get("ok"):
            if not diff.strip():
                classification = "no_patch"
            else:
                try:
                    verifier = _run_verifier(
                        verifier_command, workspace, int(task["timeout_seconds"])
                    )
                    classification = "resolved" if verifier.returncode == 0 else "tests_failed"
                except (OSError, subprocess.SubprocessError):
                    classification = "verifier_error"
        else:
            classification = _classify_agent_failure(adapter_result, termination)
        if verifier is not None:
            _write_process_artifacts(attempt, "verifier", verifier_command, verifier)
        else:
            (attempt / "verifier-command.json").write_text(
                json.dumps(verifier_command, indent=2), encoding="utf-8"
            )

        result.update(
            {
                "pinned_quixbugs_commit": task["pinned_commit"],
                "cgr_commit": cycle._git(Path.cwd(), "rev-parse", "HEAD").stdout.strip(),
                "sweagent_commit": cycle.SWE_AGENT_COMMIT,
                "sweagent_version": runtime_identity["sweagent_version"],
                "swe_rex_version": runtime_identity["swe_rex_version"],
                "litellm_version": runtime_identity["litellm_version"],
                "sweagent_source_modified": False,
                "model_endpoint": endpoint,
                "model_identifier": model,
                "model_requests": cycle._jsonl_count(interaction_path)
                if interaction_path
                else None,
                "repository_root": str(workspace),
                "initial_repository_clean": initial_status == "",
                "pre_agent_verifier_exit_code": pre_verifier.returncode,
                "termination_reason": termination,
                "trajectory_path": str(trajectory) if trajectory else None,
                "prediction_path": str(prediction) if prediction else None,
                "submitted_patch_path": str(submitted_patch) if submitted_patch else None,
                "patch_status": "patch" if diff.strip() else "no_patch",
                "patch_size": len(diff.encode("utf-8")),
                "verifier_command": verifier_command,
                "verifier_stdout": verifier.stdout if verifier else None,
                "verifier_stderr": verifier.stderr if verifier else None,
                "verifier_exit_code": verifier.returncode if verifier else None,
                "verifier_status": "executed" if verifier else "skipped",
                "classification": classification,
                "infrastructure_status": "completed",
                "top_level_exit_code": 0,
                "adapter_exit_code": adapter.returncode,
                "elapsed_seconds": time.perf_counter() - started,
                "artifact_hash_manifest": str(attempt / "artifact-sha256.json"),
            }
        )
    except Exception as exc:
        (attempt / "failure-traceback.log").write_text(traceback.format_exc(), encoding="utf-8")
        result.update({"error": str(exc), "elapsed_seconds": time.perf_counter() - started})
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        if server_thread is not None:
            server_thread.join(timeout=5)
        result["auxiliary_processes_stopped"] = server_thread is None or not server_thread.is_alive()
        final_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        cycle._write_hash_manifest(attempt)
    print(json.dumps(result, indent=2))
    return int(result["top_level_exit_code"])


def _load_task(path: Path, task_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    tasks = manifest.get("tasks") if isinstance(manifest, dict) else None
    if not isinstance(tasks, list):
        raise ValueError("QuixBugs manifest has no task list.")
    matches = [task for task in tasks if isinstance(task, dict) and task.get("task_id") == task_id]
    if len(matches) != 1:
        raise ValueError(f"QuixBugs task ID is not uniquely defined: {task_id}")
    task = matches[0]
    for key in (
        "pinned_commit",
        "source_file",
        "test_file",
        "verifier_command",
        "problem_statement",
        "timeout_seconds",
    ):
        if key not in task:
            raise ValueError(f"QuixBugs task is missing {key}.")
    for key in ("source_file", "test_file"):
        relative = Path(str(task[key]))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"QuixBugs task has unsafe path: {task[key]}")
    return task, manifest


def _verify_quixbugs_checkout(root: Path, task: dict[str, Any]) -> None:
    if cycle._git(root, "rev-parse", "HEAD").stdout.strip() != task["pinned_commit"]:
        raise RuntimeError("QuixBugs checkout differs from the pinned task commit.")
    if cycle._git(root, "status", "--porcelain=v1").stdout:
        raise RuntimeError("Canonical QuixBugs checkout must be clean.")
    for key in ("source_file", "test_file"):
        if not (root / str(task[key])).is_file():
            raise RuntimeError(f"QuixBugs checkout is missing {task[key]}.")


def _clone_attempt(source: Path, workspace: Path, commit: str) -> None:
    cycle._run(
        [
            "git",
            "-c",
            f"safe.directory={source}",
            "-c",
            f"safe.directory={source / '.git'}",
            "clone",
            "--quiet",
            "--no-hardlinks",
            str(source),
            str(workspace),
        ]
    )
    cycle._git(workspace, "checkout", "--quiet", "--detach", commit)
    cycle._git(workspace, "reset", "--hard", commit)
    cycle._git(workspace, "clean", "-fd")


def _verifier_command(task: dict[str, Any], python: Path) -> list[str]:
    command = task["verifier_command"]
    if not isinstance(command, list) or not all(isinstance(value, str) for value in command):
        raise ValueError("QuixBugs verifier command must be a list of strings.")
    return [value.replace("{python}", str(python)) for value in command]


def _run_verifier(command: list[str], cwd: Path, timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def _write_process_artifacts(
    attempt: Path, prefix: str, command: list[str], process: subprocess.CompletedProcess[str]
) -> None:
    (attempt / f"{prefix}-command.json").write_text(
        json.dumps(command, indent=2), encoding="utf-8"
    )
    (attempt / f"{prefix}.stdout.log").write_text(process.stdout, encoding="utf-8")
    (attempt / f"{prefix}.stderr.log").write_text(process.stderr, encoding="utf-8")
    (attempt / f"{prefix}-exit-code.txt").write_text(
        str(process.returncode) + "\n", encoding="utf-8"
    )


def _configured_model() -> tuple[str, str, str]:
    values = (
        os.getenv("CGR_DRAFT_BASE_URL", ""),
        os.getenv("CGR_DRAFT_MODEL", ""),
        os.getenv("CGR_DRAFT_API_KEY", ""),
    )
    return tuple(value.strip() for value in values)  # type: ignore[return-value]


def _deterministic_actions(
    task: dict[str, Any], python: Path, runtime_root: Path
) -> list[str]:
    source = str(task["source_file"])
    test = str(task["test_file"])
    python_posix = cycle._git_bash_path(python)
    submission = cycle._git_bash_path(runtime_root / "model.patch")
    return [
        f"sed -n '1,100p' {shlex_quote(source)} && sed -n '1,120p' {shlex_quote(test)}",
        f"sed -i 's/return gcd(a % b, b)/return gcd(b, a % b)/' {shlex_quote(source)}",
        f"{shlex_quote(python_posix)} -m pytest -q {shlex_quote(test)}",
        f"git diff -- {shlex_quote(source)}",
        f"git diff --binary HEAD -- > {shlex_quote(submission)} && printf '<<SWE_AGENT_SUBMISSION>>\\n'",
    ]


def shlex_quote(value: str) -> str:
    import shlex

    return shlex.quote(value)


def _adapter_command(
    python: Path,
    workspace: Path,
    problem: Path,
    overlay: Path,
    deployment_type: str,
) -> list[str]:
    command = [
        str(python),
        "-m",
        "cgr.swebench.swe_agent_adapter",
        "--workspace",
        str(workspace),
        "--problem-file",
        str(problem),
        "--mode",
        "baseline",
        "--max-steps",
        "10",
        "--max-calls",
        "8",
        "--deployment-type",
        deployment_type,
        "--config-overlay",
        str(overlay),
    ]
    if deployment_type == "local":
        command.extend(
            [
                "--deployed-repo-name",
                cycle._git_bash_path(workspace).lstrip("/"),
                "--repository-shared-with-agent",
            ]
        )
    return command


def _adapter_environment(
    *,
    endpoint: str,
    model: str,
    api_key: str,
    sweagent_source: Path,
    sweagent_python: Path,
    runtime_root: Path,
    deployment_type: str,
) -> dict[str, str]:
    if not endpoint or not model or not api_key:
        raise ValueError("CGR_DRAFT_BASE_URL, CGR_DRAFT_MODEL, and CGR_DRAFT_API_KEY are required.")
    environment = os.environ.copy()
    environment.update(
        {
            "CGR_DRAFT_BASE_URL": endpoint,
            "CGR_DRAFT_MODEL": model,
            "CGR_DRAFT_API_KEY": api_key,
            "CGR_DRAFT_MAX_MODEL_LEN": os.getenv("CGR_DRAFT_MAX_MODEL_LEN", "8192"),
            "CGR_SWE_AGENT_SOURCE": str(sweagent_source),
            "CGR_SWE_AGENT_EXECUTABLE": os.getenv(
                "CGR_SWE_AGENT_EXECUTABLE", str(sweagent_python.parent / "sweagent.exe")
            ),
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "safe.directory",
            "GIT_CONFIG_VALUE_0": str(sweagent_source),
        }
    )
    environment.pop("CGR_ACTION_VALIDATOR_COMMAND", None)
    environment.pop("CGR_ACTION_VALIDATION_LOG", None)
    if deployment_type == "local" and os.name == "nt":
        environment.update(
            {
                "CGR_SANDBOX_WINDOWS_SWEREX": "1",
                "CGR_SANDBOX_GIT_BASH": cycle._git_bash_executable(),
                "CGR_SANDBOX_RUNTIME_ROOT": str(runtime_root),
            }
        )
        compat = Path(cycle.__file__).with_name("sandbox_compat").absolute()
        environment["PYTHONPATH"] = str(compat) + os.pathsep + environment.get("PYTHONPATH", "")
    return environment


def _classify_agent_failure(adapter_result: dict[str, Any], termination: str | None) -> str:
    if termination and re.search(r"cost|call|budget", termination, re.IGNORECASE):
        return "budget_exhausted"
    detail = json.dumps(adapter_result).lower()
    if re.search(r"litellm|provider|api call|connection|model response", detail):
        return "model_failure"
    if "no non-empty unified patch" in detail or termination == "submitted":
        return "no_patch"
    return "agent_failure"


if __name__ == "__main__":
    raise SystemExit(quixbugs_pilot_main())
