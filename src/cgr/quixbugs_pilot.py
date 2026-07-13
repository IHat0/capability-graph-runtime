"""One-task QuixBugs pilot using the proven CGR + official SWE-agent cycle."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any

from cgr.swebench import sandbox_full_cycle as cycle
from cgr.quixbugs_diagnosis import build_corrective_message, diagnose_attempt


DEFAULT_MANIFEST = Path("benchmark-manifests/quixbugs-python-pilot-v1.json")
DEFAULT_RESULT_ROOT = Path("benchmark-results/quixbugs-python-pilot-v1")


def quixbugs_pilot_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one pinned Python QuixBugs task through SWE-agent.")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--quixbugs-root", type=Path, required=True)
    parser.add_argument("--result-root", type=Path, default=DEFAULT_RESULT_ROOT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--mode", choices=("baseline", "cgr"), default="baseline")
    parser.add_argument("--max-attempts", type=int)
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
    parser.add_argument("--attempt-parent", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--correction-file", type=Path, help=argparse.SUPPRESS)
    parser.add_argument(
        "--deterministic-profile",
        choices=("success", "failed", "misassessment", "noop_edit", "recovery"),
        default="success",
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)

    max_attempts = args.max_attempts
    if max_attempts is None:
        max_attempts = 1 if args.mode == "baseline" else 2
    if max_attempts < 1:
        parser.error("--max-attempts must be at least 1")
    if args.mode == "baseline" and max_attempts != 1:
        parser.error("baseline mode supports exactly one attempt")
    if args.mode == "cgr" and max_attempts > 3:
        parser.error("cgr mode supports at most three attempts in this version")
    if args.mode == "cgr":
        return _run_cgr(args, max_attempts)

    attempt_root = (
        args.attempt_parent.absolute()
        if args.attempt_parent is not None
        else args.result_root.absolute() / args.task_id
    )
    attempt = cycle._allocate_attempt(attempt_root)
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
        deployment_type = args.deployment_type or ("local" if os.name == "nt" else "docker")
        agent_python = (
            cycle._git_bash_path(sweagent_python)
            if deployment_type == "local" and os.name == "nt"
            else "python"
        )
        task = dict(task)
        task["agent_verifier_command"] = str(task["agent_verifier_command"]).replace(
            "{agent_python}", agent_python
        )
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
        problem = str(task["problem_statement"]) + "\n"
        if args.correction_file is not None:
            problem += "\n" + args.correction_file.read_text(encoding="utf-8")
        problem_path.write_text(problem, encoding="utf-8")

        verifier_command = _verifier_command(task, sweagent_python)
        pre_verifier = _run_verifier(verifier_command, source_root, int(task["timeout_seconds"]))
        _write_process_artifacts(attempt, "pre-agent-verifier", verifier_command, pre_verifier)
        if pre_verifier.returncode == 0:
            raise RuntimeError("Selected QuixBugs task does not fail before the agent run.")

        workspace = attempt / "workspace"
        _clone_attempt(source_root, workspace, str(task["pinned_commit"]))
        test_runtime = _prepare_agent_test_runtime(workspace, sweagent_python)
        test_runtime["preflight_command"] = _agent_test_preflight(agent_python)
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
            actions = _deterministic_actions(
                task,
                sweagent_python,
                runtime_root,
                profile=args.deterministic_profile,
            )
            server = cycle._model_server(
                interaction_path,
                cycle._git_bash_path(runtime_root / "model.patch"),
                actions=actions,
                discussions=_deterministic_discussions(args.deterministic_profile),
            )
            server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            server_thread.start()
            endpoint = f"http://127.0.0.1:{server.server_port}/v1"
            model = cycle.SANDBOX_MODEL
            api_key = "quixbugs-local-key"
            cycle._wait_for_server(endpoint)

        overlay_path = attempt / "sweagent-config.yaml"
        overlay_path.write_text(_quixbugs_overlay(agent_python), encoding="utf-8")
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
        model_requests = (
            cycle._jsonl_count(interaction_path)
            if interaction_path and interaction_path.is_file()
            else _trajectory_step_count(trajectory)
        )
        preflight_status = "passed" if model_requests else "failed_or_unconfirmed"

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
                "model_requests": model_requests,
                "model_requests_source": "model_interactions_jsonl"
                if interaction_path
                else "trajectory_steps",
                "repository_root": str(workspace),
                "initial_repository_clean": initial_status == "",
                "advertised_test_command": task["agent_verifier_command"],
                "agent_test_runtime": test_runtime,
                "preflight_test_command": test_runtime["preflight_command"],
                "preflight_test_command_status": preflight_status,
                "preflight_test_command_evidence": (
                    "post_startup_gate_completed_before_model_request"
                    if model_requests
                    else "no_model_request_proved_the_post_startup_gate_completed"
                ),
                "agent_editing_mechanisms": ["python", "sed", "cat_heredoc"],
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


def _run_cgr(args: argparse.Namespace, max_attempts: int) -> int:
    started = time.perf_counter()
    run = _allocate_run(args.result_root.absolute() / args.task_id)
    result_path = run / "run-result.json"
    result: dict[str, Any] = {
        "run_id": run.name,
        "task_id": args.task_id,
        "mode": "cgr",
        "maximum_attempts": max_attempts,
        "configured_base_attempts": max_attempts,
        "actionable_recovery_attempts": 0,
        "absolute_hard_cap": 4 if max_attempts >= 3 else max_attempts,
        "attempts_started": 0,
        "attempts_completed": 0,
        "child_artifact_paths": [],
        "selected_attempt": None,
        "final_classification": "infrastructure_error",
        "infrastructure_status": "failed",
        "recovery_occurred": False,
        "top_level_exit_code": 1,
        "diagnoses_generated": [],
        "corrective_messages_generated": [],
        "attempt_lineage": [],
    }
    try:
        task, _manifest = _load_task(args.manifest.absolute(), args.task_id)
        parent_deployment = args.deployment_type or ("local" if os.name == "nt" else "docker")
        parent_agent_python = (
            cycle._git_bash_path(args.sweagent_python.absolute())
            if parent_deployment == "local" and os.name == "nt"
            else "python"
        )
        task = dict(task)
        task["agent_verifier_command"] = str(task["agent_verifier_command"]).replace(
            "{agent_python}", parent_agent_python
        )
        child_results: list[dict[str, Any]] = []
        diagnoses: list[dict[str, Any] | None] = []
        latest_correction: Path | None = None
        profiles = ("failed", "misassessment", "noop_edit", "recovery")
        attempt_limit = max_attempts
        attempt_index = 1
        while attempt_index <= attempt_limit:
            child = _launch_child_attempt(
                args,
                run,
                correction=latest_correction,
                profile=profiles[attempt_index - 1],
            )
            child_results.append(child)
            result["attempts_started"] = attempt_index
            result["attempts_completed"] = attempt_index
            result["child_artifact_paths"].append(child["artifact_directory"])
            if child.get("infrastructure_status") != "completed":
                raise RuntimeError(
                    f"QuixBugs child attempt {attempt_index} had an infrastructure failure."
                )
            lineage = {
                "attempt": f"attempt-{attempt_index:03d}",
                "artifact_path": child["artifact_directory"],
                "correction_used": str(latest_correction) if latest_correction else None,
                "classification": child.get("classification"),
                "model_requests": child.get("model_requests"),
                "model_requests_source": child.get("model_requests_source"),
            }
            result["attempt_lineage"].append(lineage)
            if child.get("classification") == "resolved":
                diagnoses.append(None)
                break

            diagnosis = diagnose_attempt(
                _result_path(child.get("trajectory_path")),
                Path(str(child["repository_root"])),
                child,
                task,
            )
            diagnoses.append(diagnosis)
            diagnosis_path = run / f"diagnosis-{attempt_index:03d}.json"
            diagnosis_path.write_text(json.dumps(diagnosis, indent=2), encoding="utf-8")
            result["diagnoses_generated"].append(
                {
                    "attempt": f"attempt-{attempt_index:03d}",
                    "path": str(diagnosis_path),
                    "failure_types": diagnosis["failure_types"],
                }
            )
            lineage["diagnosis_path"] = str(diagnosis_path)
            if (
                attempt_index == attempt_limit
                and attempt_limit < result["absolute_hard_cap"]
                and _qualifies_for_actionable_recovery(diagnosis)
            ):
                attempt_limit += 1
                result["actionable_recovery_attempts"] += 1
                result["maximum_attempts_with_recovery"] = attempt_limit
            if attempt_index < attempt_limit:
                correction_path = run / f"corrective-message-{attempt_index:03d}.md"
                correction_path.write_text(
                    build_corrective_message(diagnosis, task), encoding="utf-8"
                )
                result["corrective_messages_generated"].append(
                    {
                        "after_attempt": f"attempt-{attempt_index:03d}",
                        "path": str(correction_path),
                        "required_next_phase": diagnosis.get("required_next_phase"),
                    }
                )
                latest_correction = correction_path
            attempt_index += 1

        result["recovery_occurred"] = len(child_results) > 1
        selected_index = _select_attempt(child_results, diagnoses)
        selected = child_results[selected_index]
        selected_name = f"attempt-{selected_index + 1:03d}"
        rationale = _selection_rationale(selected, diagnoses[selected_index])
        result.update(
            {
                "selected_attempt": selected_name,
                "selected_attempt_path": selected["artifact_directory"],
                "selection_rationale": rationale,
                "final_classification": selected.get("classification"),
                "final_patch_path": None,
                "final_verifier_exit_code": selected.get("verifier_exit_code"),
                "infrastructure_status": "completed",
                "top_level_exit_code": 0,
                "attempt_results": child_results,
                "total_model_attempts": len(child_results),
                "recovery_stage": selected_index + 1,
            }
        )
        patch_path = _result_path(selected.get("submitted_patch_path"))
        if patch_path is not None and patch_path.is_file() and patch_path.stat().st_size:
            parent_patch = run / "selected.patch"
            shutil.copyfile(patch_path, parent_patch)
            result["final_patch_path"] = str(parent_patch)
    except Exception as exc:
        (run / "failure-traceback.log").write_text(traceback.format_exc(), encoding="utf-8")
        result["error"] = str(exc)
    finally:
        result["total_elapsed_seconds"] = time.perf_counter() - started
        result["artifact_hash_manifest"] = str(run / "artifact-sha256.json")
        result_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        cycle._write_hash_manifest(run)
    print(json.dumps(result, indent=2))
    return int(result["top_level_exit_code"])


def _launch_child_attempt(
    args: argparse.Namespace,
    run: Path,
    *,
    correction: Path | None = None,
    profile: str,
) -> dict[str, Any]:
    command = [
        sys.executable,
        "-m",
        "cgr.quixbugs_pilot",
        "--mode",
        "baseline",
        "--max-attempts",
        "1",
        "--task-id",
        args.task_id,
        "--quixbugs-root",
        str(args.quixbugs_root.absolute()),
        "--result-root",
        str(args.result_root.absolute()),
        "--manifest",
        str(args.manifest.absolute()),
        "--sweagent-source",
        str(args.sweagent_source.absolute()),
        "--sweagent-python",
        str(args.sweagent_python.absolute()),
        "--attempt-parent",
        str(run),
        "--deterministic-profile",
        profile,
    ]
    if args.deployment_type:
        command.extend(["--deployment-type", args.deployment_type])
    if args.deterministic_model:
        command.append("--deterministic-model")
    if correction is not None:
        command.extend(["--correction-file", str(correction)])
    index = len(list(run.glob("attempt-*/final-result.json"))) + 1
    process = subprocess.run(
        command,
        cwd=Path.cwd(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    (run / f"attempt-{index:03d}.launcher.stdout.log").write_text(
        process.stdout, encoding="utf-8"
    )
    (run / f"attempt-{index:03d}.launcher.stderr.log").write_text(
        process.stderr, encoding="utf-8"
    )
    attempt_path = run / f"attempt-{index:03d}" / "final-result.json"
    if not attempt_path.is_file():
        raise RuntimeError(
            f"Child attempt {index} produced no final result (exit {process.returncode})."
        )
    child = json.loads(attempt_path.read_text(encoding="utf-8"))
    if process.returncode != int(child.get("top_level_exit_code", 1)):
        raise RuntimeError(f"Child attempt {index} exit code disagrees with its result.")
    return child


def _allocate_run(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for index in range(1, 10000):
        candidate = root / f"run-{index:03d}"
        try:
            candidate.mkdir()
        except FileExistsError:
            continue
        return candidate
    raise RuntimeError("No available QuixBugs CGR run directory.")


def _select_attempt(
    results: list[dict[str, Any]], diagnoses: list[dict[str, Any] | None] | None = None
) -> int:
    evidence = diagnoses or [None] * len(results)

    def score(item: tuple[int, dict[str, Any]]) -> tuple[int, int, int, int, int, int, int]:
        index, result = item
        diagnosis = evidence[index] or {}
        verified = int(
            result.get("classification") == "resolved"
            and result.get("verifier_exit_code") == 0
        )
        patch = int(bool(result.get("patch_size")))
        test_passed = int(
            bool(diagnosis.get("test_passed") or result.get("verifier_exit_code") == 0)
        )
        test_failed = int(bool(diagnosis.get("test_failed")))
        changed = int(bool(diagnosis.get("tracked_change_observed") or result.get("patch_size")))
        inspected = int(bool(diagnosis.get("inspected_source_paths")))
        return verified, patch, test_passed, test_failed, changed, inspected, index

    return max(enumerate(results), key=score)[0]


def _selection_rationale(
    result: dict[str, Any], diagnosis: dict[str, Any] | None
) -> list[str]:
    evidence = diagnosis or {}
    rationale = []
    if result.get("classification") == "resolved" and result.get("verifier_exit_code") == 0:
        rationale.append("verifier_passed")
    if result.get("patch_size"):
        rationale.append("nonempty_patch")
    if evidence.get("test_passed") or result.get("verifier_exit_code") == 0:
        rationale.append("focused_test_passed")
    elif evidence.get("test_failed"):
        rationale.append("focused_test_failed")
    if evidence.get("tracked_change_observed") or result.get("patch_size"):
        rationale.append("tracked_change_observed")
    if evidence.get("inspected_source_paths"):
        rationale.append("target_inspection_observed")
    return rationale or ["later_attempt_tiebreak"]


def _result_path(value: Any) -> Path | None:
    return Path(value) if isinstance(value, str) and value else None


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
        "agent_verifier_command",
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
    cycle._git(workspace, "bundle", "create", ".git/cgr-origin.bundle", "HEAD")
    cycle._git(
        workspace,
        "remote",
        "set-url",
        "origin",
        "./.git/cgr-origin.bundle",
    )
    cycle._git(workspace, "clean", "-fd")


def _prepare_agent_test_runtime(workspace: Path, python: Path) -> dict[str, Any]:
    module_names = (
        "pytest",
        "_pytest",
        "pluggy",
        "iniconfig",
        "packaging",
        "pygments",
        "py",
    )
    distribution_names = ("pytest", "pluggy", "iniconfig", "packaging", "pygments")
    discovery = (
        "import importlib.metadata as m, importlib.util as u, json; "
        f"mods={module_names!r}; dists={distribution_names!r}; "
        "print(json.dumps({'modules': {n: "
        "(list(u.find_spec(n).submodule_search_locations)[0] if "
        "u.find_spec(n).submodule_search_locations else u.find_spec(n).origin) "
        "for n in mods}, 'metadata': {n: str(m.distribution(n)._path) for n in dists}}))"
    )
    process = subprocess.run(
        [str(python), "-c", discovery],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if process.returncode != 0:
        raise RuntimeError(f"Could not locate pinned pytest runtime: {process.stderr.strip()}")
    located = json.loads(process.stdout)
    runtime = workspace / ".git" / "cgr-test-runtime"
    runtime.mkdir()
    copied: list[str] = []
    for source_value in [*located["modules"].values(), *located["metadata"].values()]:
        source = Path(source_value)
        target = runtime / source.name
        if target.exists():
            continue
        if source.is_dir():
            shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copy2(source, target)
        copied.append(source.name)
    return {
        "runtime_path": str(runtime),
        "copied_entries": copied,
    }


def _quixbugs_overlay(agent_python: str = "python") -> str:
    preflight = _agent_test_preflight(agent_python)
    python_gate = (
        "command -v python >/dev/null"
        if agent_python == "python"
        else f"test -x {shlex_quote(agent_python)}"
    )
    editor_gate = f"{python_gate} && command -v sed >/dev/null"
    anchor = "    - git diff --cached --quiet --ignore-submodules --"
    additions = "\n".join((anchor, f"    - {json.dumps(preflight)}", f"    - {json.dumps(editor_gate)}"))
    return cycle._sandbox_overlay().replace(anchor, additions)


def _agent_test_preflight(agent_python: str) -> str:
    return (
        f"PYTHONPATH=.git/cgr-test-runtime {agent_python} -c \"import pytest; "
        "print('CGR_PYTEST_READY=' + pytest.__version__)\""
    )


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
    task: dict[str, Any], _python: Path, runtime_root: Path, *, profile: str = "success"
) -> list[str]:
    source = str(task["source_file"])
    test = str(task["test_file"])
    submission = cycle._git_bash_path(runtime_root / "model.patch")
    if profile == "failed":
        failed = (
            f"git add {shlex_quote(source)} test_gcd.py 2>&1\n"
            'git commit -m "Fix gcd function to return the greatest common divisor" 2>&1'
        )
        return [failed]
    if profile == "misassessment":
        return [str(task["agent_verifier_command"])]
    if profile == "noop_edit":
        return [
            f"sed -i 's/return gcd(b, a % b)/return gcd(a % b, b)/' {shlex_quote(source)}",
            str(task["agent_verifier_command"]),
        ]
    if profile == "recovery":
        return [
            f"sed -i 's/return gcd(a % b, b)/return gcd(b, a % b)/' {shlex_quote(source)}",
            f"sed -n '1,30p' {shlex_quote(source)}",
            f"git diff -- {shlex_quote(source)}",
            str(task["agent_verifier_command"]),
            f"git diff -- {shlex_quote(source)}",
            f"git diff --binary HEAD -- > {shlex_quote(submission)} && printf '<<SWE_AGENT_SUBMISSION>>\n'",
        ]
    return [
        f"sed -n '1,100p' {shlex_quote(source)} && sed -n '1,120p' {shlex_quote(test)}",
        f"sed -i 's/return gcd(a % b, b)/return gcd(b, a % b)/' {shlex_quote(source)}",
        str(task["agent_verifier_command"]),
        f"git diff -- {shlex_quote(source)}",
        f"git diff --binary HEAD -- > {shlex_quote(submission)} && printf '<<SWE_AGENT_SUBMISSION>>\\n'",
    ]


def _deterministic_discussions(profile: str) -> list[str] | None:
    if profile not in {"misassessment", "noop_edit"}:
        return None
    return [
        (
            "The source should be updated to an iterative Euclidean implementation: "
            "def gcd(a, b): while b != 0: a, b = b, a % b; return a. "
            "Run the focused test to verify it."
        )
    ]


def _qualifies_for_actionable_recovery(diagnosis: dict[str, Any]) -> bool:
    return bool(
        diagnosis.get("required_next_phase") == "edit"
        and diagnosis.get("no_op_edits")
        and diagnosis.get("phase_exit_condition", {}).get("requires_nonempty_diff")
    )


def _trajectory_step_count(path: Path | None) -> int | None:
    if path is None or not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    steps = payload.get("trajectory") if isinstance(payload, dict) else None
    return len(steps) if isinstance(steps, list) else None


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
