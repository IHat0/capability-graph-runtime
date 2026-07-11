"""CLI entrypoints for the frozen SWE-bench Verified pilot."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from .integration import (
    DATASET_NAME,
    DEFAULT_BUDGETS,
    DEFAULT_MANIFEST,
    MODES,
    RESULT_ROOT,
    Prediction,
    capture_git_patch,
    doctor_report,
    filter_model_instance,
    freeze_manifest,
    generation_result_template,
    integrity_check,
    load_manifest,
    load_verified_records,
    materialize_repository,
    official_harness_command,
    run_external_agent,
    validate_prediction_hash,
    verify_patch_applies,
    write_predictions,
)


def doctor_main(argv: list[str] | None = None) -> int:
    """Report prerequisites without contacting a model."""
    parser = argparse.ArgumentParser(description="Check SWE-bench pilot prerequisites.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args(argv)
    report = doctor_report(args.manifest)
    print(json.dumps(report, indent=2))
    return 0 if report["git_available"] else 1


def freeze_pilot_main(argv: list[str] | None = None) -> int:
    """Freeze ten instances using safe metadata before model inference."""
    parser = argparse.ArgumentParser(description="Freeze the Verified ten-instance pilot.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--dataset-revision")
    parser.add_argument("--force-development", action="store_true")
    args = parser.parse_args(argv)
    records, fingerprint = load_verified_records()
    manifest = freeze_manifest(
        records,
        args.manifest,
        dataset_revision=args.dataset_revision,
        dataset_fingerprint=fingerprint,
        force_development=args.force_development,
    )
    print(
        json.dumps(
            {
                "manifest": str(args.manifest),
                "status": manifest.status,
                "instances": len(manifest.instances),
                "selected_ids_sha256": manifest.selected_ids_sha256,
            },
            indent=2,
        )
    )
    return 0


def integrity_check_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify frozen pilot integrity.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--result-root", type=Path, default=RESULT_ROOT)
    args = parser.parse_args(argv)
    try:
        result = integrity_check(args.manifest, args.result_root)
    except Exception as exc:
        print(json.dumps({"passed": False, "error": str(exc)}, indent=2))
        return 1
    print(json.dumps(result, indent=2))
    return 0


def gold_smoke_main(argv: list[str] | None = None) -> int:
    """Run one official gold evaluation without calling Qwen."""
    parser = argparse.ArgumentParser(description="Run official SWE-bench gold smoke.")
    parser.add_argument("--instance-id", default="sympy__sympy-20590")
    parser.add_argument("--run-id", default="cgr-gold-smoke")
    parser.add_argument("--result-root", type=Path, default=RESULT_ROOT)
    args = parser.parse_args(argv)
    report = doctor_report()
    if not report["swebench_package_available"]:
        print(json.dumps({"error": "SWE-bench package unavailable."}))
        return 2
    if not report["docker_cli_available"] or not report["docker_daemon_available"]:
        print(json.dumps({"error": "Docker CLI/daemon unavailable.", "doctor": report}))
        return 2
    command = official_harness_command("gold", [args.instance_id], args.run_id)
    started = time.perf_counter()
    process = subprocess.run(command, capture_output=True, text=True, check=False)
    output_root = args.result_root / "official-evaluation" / "gold-smoke"
    output_root.mkdir(parents=True, exist_ok=True)
    resolved, result_path = _find_official_result(
        args.run_id, args.instance_id, process.stdout
    )
    result = {
        "command": command,
        "exit_code": process.returncode,
        "run_id": args.run_id,
        "instance_id": args.instance_id,
        "elapsed_seconds": time.perf_counter() - started,
        "log_locations": [f"logs/run_evaluation/{args.run_id}"],
        "evaluation_result_location": result_path,
        "resolved": resolved,
        "stdout_preview": process.stdout[-2000:],
        "stderr_preview": process.stderr[-2000:],
    }
    (output_root / "result.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, indent=2))
    return 0 if process.returncode == 0 and resolved is True else 1


def pilot_main(argv: list[str] | None = None) -> int:
    """Generate frozen predictions or evaluate already-locked predictions."""
    parser = argparse.ArgumentParser(description="Run the frozen SWE-bench Verified pilot.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--mode", choices=MODES)
    mode.add_argument("--all-modes", action="store_true")
    parser.add_argument("--instance-id")
    phase = parser.add_mutually_exclusive_group()
    phase.add_argument("--generate-only", action="store_true")
    phase.add_argument("--evaluate-only", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--debug-trace", action="store_true")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--result-root", type=Path, default=RESULT_ROOT)
    args = parser.parse_args(argv)
    manifest = load_manifest(args.manifest)
    modes = list(MODES) if args.all_modes else [args.mode or "baseline"]
    instances = manifest.instances
    if args.instance_id:
        instances = [item for item in instances if item.instance_id == args.instance_id]
        if not instances:
            parser.error("--instance-id is not in the frozen pilot")
    plan = {
        "dataset": DATASET_NAME,
        "manifest_hash": manifest.selected_ids_sha256,
        "modes": modes,
        "instance_ids": [item.instance_id for item in instances],
        "phase": "evaluate" if args.evaluate_only else "generate",
        "budgets": {mode: DEFAULT_BUDGETS[mode].model_dump() for mode in modes},
    }
    if args.dry_run:
        print(json.dumps(plan, indent=2))
        return 0
    args.result_root.mkdir(parents=True, exist_ok=True)
    (args.result_root / "manifest.json").write_text(
        json.dumps(manifest.model_dump(mode="json"), indent=2) + "\n",
        encoding="utf-8",
    )
    if args.evaluate_only:
        return _evaluate_predictions(modes, instances, args.result_root)
    return _generate_predictions(
        modes,
        instances,
        manifest.selected_ids_sha256,
        args.result_root,
        resume=args.resume,
        debug_trace=args.debug_trace,
    )


def _generate_predictions(
    modes: list[str],
    instances: list[Any],
    manifest_hash: str,
    result_root: Path,
    *,
    resume: bool,
    debug_trace: bool,
) -> int:
    adapter = os.getenv("CGR_SWEBENCH_AGENT_COMMAND", "")
    scaffold = os.getenv("CGR_SWEBENCH_SCAFFOLD_ID", "")
    model = os.getenv("CGR_DRAFT_MODEL", "")
    provider = os.getenv("CGR_DRAFT_BASE_URL", "")
    if (
        not adapter
        or not scaffold
        or not model
        or not provider
        or not os.getenv("CGR_DRAFT_API_KEY")
    ):
        print(
            json.dumps(
                {
                    "error": (
                        "Generation requires CGR_DRAFT_API_KEY, CGR_DRAFT_BASE_URL, "
                        "CGR_DRAFT_MODEL, CGR_SWEBENCH_AGENT_COMMAND, and "
                        "CGR_SWEBENCH_SCAFFOLD_ID."
                    )
                }
            )
        )
        return 2
    records, _ = load_verified_records()
    by_id = {str(record["instance_id"]): record for record in records}
    result_root.mkdir(parents=True, exist_ok=True)
    environment = doctor_report()
    environment.update({"manifest_hash": manifest_hash, "model": model, "provider": provider})
    (result_root / "environment.json").write_text(
        json.dumps(environment, indent=2) + "\n", encoding="utf-8"
    )
    failures_by_mode: dict[str, list[str]] = {}
    successful_by_mode: dict[str, list[str]] = {}
    for selected_mode in modes:
        mode_root = result_root / selected_mode
        predictions_path = mode_root / "predictions.jsonl"
        existing: list[Prediction] = []
        if resume and predictions_path.exists():
            existing = [
                Prediction.model_validate_json(line)
                for line in predictions_path.read_text(encoding="utf-8").splitlines()
            ]
        completed = {prediction.instance_id for prediction in existing}
        predictions = list(existing)
        generation_rows: list[dict[str, Any]] = []
        failed_instances: list[str] = []
        successful_instances: list[str] = []
        for pilot_instance in instances:
            if pilot_instance.instance_id in completed:
                continue
            started = time.perf_counter()
            process: subprocess.CompletedProcess[str] | None = None
            try:
                record = by_id.get(pilot_instance.instance_id)
                if record is None:
                    raise RuntimeError(
                        f"Dataset unavailable for {pilot_instance.instance_id}"
                    )
                safe_instance = filter_model_instance(record)
                with tempfile.TemporaryDirectory(prefix="cgr-swebench-workspace-") as temp:
                    workspace = Path(temp) / "repo"
                    safe_instance = safe_instance.model_copy(
                        update={"workspace_path": str(workspace)}
                    )
                    materialize_repository(safe_instance, workspace)
                    process = run_external_agent(
                        adapter,
                        safe_instance,
                        selected_mode,
                        workspace,
                        DEFAULT_BUDGETS[selected_mode],
                        debug_trace=debug_trace,
                    )
                    if process.returncode:
                        raise RuntimeError(_agent_failure_message(process))
                    patch, changed = capture_git_patch(workspace)
                    verify_patch_applies(workspace, patch, safe_instance.base_commit)
            except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
                failed_instances.append(pilot_instance.instance_id)
                diagnostics = _agent_diagnostics(process)
                generation_rows.append(
                    {
                        **generation_result_template(
                            selected_mode, model, provider, scaffold
                        ),
                        "instance_id": pilot_instance.instance_id,
                        "repo": pilot_instance.repo,
                        "base_commit": pilot_instance.base_commit,
                        "elapsed_seconds": time.perf_counter() - started,
                        "generation_error": str(exc),
                        **diagnostics,
                    }
                )
                continue
            predictions.append(
                Prediction(
                    instance_id=safe_instance.instance_id,
                    model_name_or_path=model,
                    model_patch=patch,
                )
            )
            successful_instances.append(safe_instance.instance_id)
            row = generation_result_template(selected_mode, model, provider, scaffold)
            row.update(
                {
                    "instance_id": safe_instance.instance_id,
                    "repo": safe_instance.repo,
                    "base_commit": safe_instance.base_commit,
                    "elapsed_seconds": time.perf_counter() - started,
                    "candidate_count": DEFAULT_BUDGETS[selected_mode].trajectories,
                    "final_changed_files": changed,
                    "final_patch_size": len(patch.encode()),
                    "local_verification_passed": True,
                    "local_verification_summary": "Agent completed and patch applies at base_commit.",
                    "debug_trace": process.stdout[-4000:] if debug_trace else None,
                }
            )
            generation_rows.append(row)
        mode_root.mkdir(parents=True, exist_ok=True)
        (mode_root / "generation-results.json").write_text(
            json.dumps(generation_rows, indent=2) + "\n", encoding="utf-8"
        )
        if not failed_instances:
            write_predictions(
                predictions_path, sorted(predictions, key=lambda item: item.instance_id)
            )
        else:
            failures_by_mode[selected_mode] = failed_instances
        if successful_instances:
            successful_by_mode[selected_mode] = successful_instances
    generated = not failures_by_mode
    print(
        json.dumps(
            {
                "generated": generated,
                "modes": modes,
                "successful_instances": successful_by_mode,
                "failed_instances": failures_by_mode,
            },
            indent=2,
        )
    )
    return 0 if generated else 1


def _agent_failure_message(process: subprocess.CompletedProcess[str]) -> str:
    """Preserve an adapter's JSON/stdout error before falling back to stderr."""
    stdout = process.stdout.strip()
    if stdout:
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict) and isinstance(payload.get("error"), str):
            return payload["error"]
    message = "\n".join(part for part in (stdout, process.stderr.strip()) if part)
    return message[-4000:] or f"Repository agent exited with code {process.returncode}."


def _agent_diagnostics(
    process: subprocess.CompletedProcess[str] | None,
) -> dict[str, Any]:
    if process is None:
        return {
            "agent_exit_code": None,
            "agent_stdout": None,
            "agent_stderr": None,
            "agent_debug_trace": None,
        }
    stdout = _redact_agent_output(process.stdout[-4000:])
    stderr = _redact_agent_output(process.stderr[-4000:])
    trace: Any = None
    try:
        payload = json.loads(process.stdout)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict) and isinstance(payload.get("debug_trace"), list):
        trace = payload["debug_trace"]
    return {
        "agent_exit_code": process.returncode,
        "agent_stdout": stdout,
        "agent_stderr": stderr,
        "agent_debug_trace": trace,
    }


def _redact_agent_output(value: str) -> str:
    api_key = os.getenv("CGR_DRAFT_API_KEY", "")
    return value.replace(api_key, "[REDACTED]") if api_key else value


def _evaluate_predictions(modes: list[str], instances: list[Any], result_root: Path) -> int:
    doctor = doctor_report()
    if not doctor["swebench_package_available"]:
        print(json.dumps({"error": "SWE-bench package unavailable."}))
        return 2
    if not doctor["docker_cli_available"] or not doctor["docker_daemon_available"]:
        print(json.dumps({"error": "Docker CLI/daemon unavailable.", "doctor": doctor}))
        return 2
    overall = 0
    ids = [item.instance_id for item in instances]
    for mode in modes:
        predictions = result_root / mode / "predictions.jsonl"
        validate_prediction_hash(predictions)
        run_id = f"cgr-{mode}-swebench-verified-pilot-v1"
        command = official_harness_command(str(predictions), ids, run_id)
        process = subprocess.run(command, check=False)
        overall = overall or process.returncode
    _write_final_summary(modes, instances, result_root)
    return overall


def _write_final_summary(modes: list[str], instances: list[Any], result_root: Path) -> None:
    matrix: dict[str, dict[str, bool | None]] = {
        item.instance_id: {} for item in instances
    }
    mode_summary: dict[str, dict[str, Any]] = {}
    for mode in modes:
        run_id = f"cgr-{mode}-swebench-verified-pilot-v1"
        resolved_count = 0
        harness_failures = 0
        for item in instances:
            resolved, _ = _find_official_result(run_id, item.instance_id)
            matrix[item.instance_id][mode] = resolved
            resolved_count += resolved is True
            harness_failures += resolved is None
        generation_path = result_root / mode / "generation-results.json"
        rows = (
            json.loads(generation_path.read_text(encoding="utf-8"))
            if generation_path.exists()
            else []
        )
        mode_summary[mode] = {
            "resolved_count": resolved_count,
            "resolved_rate": resolved_count / len(instances) if instances else 0.0,
            "model_calls": sum(
                int(row.get("model_calls", 0)) for row in rows if isinstance(row, dict)
            ),
            "elapsed_seconds": sum(
                float(row.get("elapsed_seconds", 0))
                for row in rows
                if isinstance(row, dict)
            ),
            "patch_generation_failures": sum(
                "generation_error" in row for row in rows if isinstance(row, dict)
            ),
            "empty_patch_count": sum(
                row.get("final_patch_size") == 0 for row in rows if isinstance(row, dict)
            ),
            "official_harness_failures": harness_failures,
            "prediction_hash": (
                (result_root / mode / "predictions.sha256").read_text().strip()
                if (result_root / mode / "predictions.sha256").exists()
                else None
            ),
        }
    baseline_resolved = {
        instance_id
        for instance_id, row in matrix.items()
        if row.get("baseline") is True
    }
    summary = {
        "dataset_name": DATASET_NAME,
        "total_instances": len(instances),
        "modes": mode_summary,
        "resolution_matrix": matrix,
        "baseline_to_single_delta": (
            mode_summary.get("cgr_single", {}).get("resolved_count", 0)
            - mode_summary.get("baseline", {}).get("resolved_count", 0)
        ),
        "baseline_to_multi_delta": (
            mode_summary.get("cgr_multi", {}).get("resolved_count", 0)
            - mode_summary.get("baseline", {}).get("resolved_count", 0)
        ),
        "regressions_relative_to_baseline": {
            mode: sorted(
                instance_id
                for instance_id in baseline_resolved
                if matrix[instance_id].get(mode) is not True
            )
            for mode in ("cgr_single", "cgr_multi")
            if mode in modes
        },
        "improvements_relative_to_baseline": {
            mode: sorted(
                instance_id
                for instance_id, row in matrix.items()
                if row.get("baseline") is not True and row.get(mode) is True
            )
            for mode in ("cgr_single", "cgr_multi")
            if mode in modes
        },
        "software_versions": {"python": sys.version},
    }
    (result_root / "final-summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )


def _find_official_result(
    run_id: str,
    instance_id: str,
    stdout: str = "",
    search_root: Path | None = None,
) -> tuple[bool | None, str | None]:
    """Read an official report; never infer resolution from process status."""
    root = (search_root or Path.cwd()).resolve()
    for report in _official_report_candidates(run_id, stdout, root):
        try:
            data = json.loads(report.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        resolved = _official_instance_resolution(data, instance_id)
        if resolved is not None:
            return resolved, str(report.resolve())
    return None, None


def _official_report_candidates(run_id: str, stdout: str, root: Path) -> list[Path]:
    """Find paths reported by the harness and its conventional gold summary path."""
    candidates: list[Path] = []
    for match in re.finditer(r"(?im)^\s*Report written to\s+(.+?)\s*$", stdout):
        raw_path = match.group(1).strip().strip("'\"")
        path = Path(raw_path)
        candidates.append(path if path.is_absolute() else root / path)
    candidates.extend(
        [
            root / f"gold.{run_id}.json",
            root / "logs" / "run_evaluation" / run_id / f"gold.{run_id}.json",
        ]
    )
    log_root = root / "logs" / "run_evaluation" / run_id
    if log_root.exists():
        candidates.extend(log_root.rglob("report.json"))
    unique: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved not in seen and resolved.is_file():
            seen.add(resolved)
            unique.append(resolved)
    return unique


def _official_instance_resolution(data: Any, instance_id: str) -> bool | None:
    """Return a status only when the official report explicitly names the instance."""
    if not isinstance(data, dict):
        return None
    for container_key in ("instances", "results", "reports"):
        container = data.get(container_key)
        if isinstance(container, dict) and instance_id in container:
            status = _resolved_flag(container[instance_id])
            if status is not None:
                return status
        if isinstance(container, list):
            for entry in container:
                if isinstance(entry, dict) and entry.get("instance_id") == instance_id:
                    status = _resolved_flag(entry)
                    if status is not None:
                        return status
    if instance_id in data:
        status = _resolved_flag(data[instance_id])
        if status is not None:
            return status
    for key, value in data.items():
        if key in {"resolved", "resolved_ids", "resolved_instances"} and _contains_id(
            value, instance_id
        ):
            return True
        if key in {"unresolved", "unresolved_ids", "unresolved_instances"} and _contains_id(
            value, instance_id
        ):
            return False
    return None


def _resolved_flag(value: Any) -> bool | None:
    if isinstance(value, dict) and isinstance(value.get("resolved"), bool):
        return value["resolved"]
    return None


def _contains_id(value: Any, instance_id: str) -> bool:
    if isinstance(value, list):
        return any(
            item == instance_id
            or (isinstance(item, dict) and item.get("instance_id") == instance_id)
            for item in value
        )
    if isinstance(value, dict):
        return instance_id in value
    return False


if __name__ == "__main__":
    sys.exit(pilot_main())
