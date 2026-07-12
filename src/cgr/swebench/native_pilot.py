"""Native official SWE-agent benchmark runner for the frozen pilot."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Sequence

from .integration import (
    DATASET_NAME,
    DEFAULT_BUDGETS,
    DEFAULT_MANIFEST,
    PilotInstance,
    load_manifest,
    load_verified_records,
    official_harness_command,
)
from .swe_agent_adapter import LOCAL_QWEN_OVERLAY


NATIVE_RESULT_ROOT = Path("benchmark-results/swebench-native-pilot-v1")
SWE_AGENT_COMMIT = "0f3acafacabc0def8cc76b4e48acb4b6cf302cb9"
SWE_AGENT_PATCH = (
    Path(__file__).resolve().parents[3]
    / "patches"
    / "sweagent-v1.1.0-strict-thought-action.patch"
)
SWE_AGENT_PATCH_SHA256 = "5914d306f77feaf5e1252de96b14357822127f898b574f93e2468cab3c3f4a28"
NATIVE_MODES = ("baseline", "cgr")
NATIVE_CALL_BUDGET = DEFAULT_BUDGETS["baseline"].maximum_model_calls
NATIVE_STEP_BUDGET = DEFAULT_BUDGETS["baseline"].maximum_steps
NATIVE_TIMEOUT_SECONDS = DEFAULT_BUDGETS["baseline"].timeout_seconds
SWEBENCH_EVALUATOR_VERSION = "3.0.17"
_METADATA_FILES = {
    "generation-result.json",
    "evaluation-result.json",
    "artifact-sha256.json",
    "evaluation-artifact-sha256.json",
}


@dataclass(frozen=True)
class ModelEndpoint:
    mode: Literal["baseline", "cgr"]
    base_url: str
    api_key: str
    model: str

    @property
    def sweagent_model(self) -> str:
        return self.model if self.model.startswith("openai/") else f"openai/{self.model}"


@dataclass(frozen=True)
class EvaluatorRuntime:
    python: str
    version: str
    package_path: str
    harness_path: str


def native_pilot_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run official SWE-agent natively on one frozen SWE-bench instance."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--mode", choices=NATIVE_MODES)
    mode.add_argument("--compare", action="store_true")
    phase = parser.add_mutually_exclusive_group(required=True)
    phase.add_argument("--generate-only", action="store_true")
    phase.add_argument("--evaluate-only", action="store_true")
    phase.add_argument("--generate-and-evaluate", action="store_true")
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--result-root", type=Path, default=NATIVE_RESULT_ROOT)
    parser.add_argument("--sweagent-source", type=Path)
    parser.add_argument("--sweagent-executable")
    args = parser.parse_args(argv)

    try:
        manifest = load_manifest(args.manifest)
        instance = _selected_instance(manifest.instances, args.instance_id)
        modes = list(NATIVE_MODES) if args.compare else [str(args.mode)]
        if args.evaluate_only:
            evaluation_runtime = verify_evaluator_runtime()
            evaluation_summaries = []
            overall = 0
            for selected_mode in modes:
                attempt = _latest_completed_attempt(
                    args.result_root, selected_mode, instance.instance_id
                )
                evaluation = evaluate_attempt(
                    attempt,
                    instance=instance,
                    manifest_hash=manifest.selected_ids_sha256,
                    evaluator=evaluation_runtime,
                )
                evaluation_summaries.append(evaluation)
                overall = overall or int(evaluation["exit_code"] != 0)
            print(json.dumps({"ok": overall == 0, "runs": evaluation_summaries}, indent=2))
            return overall

        generation_evaluator = (
            verify_evaluator_runtime() if args.generate_and_evaluate else None
        )
        records, fingerprint = load_verified_records()
        record = _verified_record(records, instance)
        if manifest.dataset_fingerprint and fingerprint != manifest.dataset_fingerprint:
            raise ValueError("Loaded SWE-bench dataset fingerprint differs from the frozen manifest.")
        endpoints = {selected: _model_endpoint(selected) for selected in modes}
        if args.compare:
            _verify_distinct_comparison_endpoints(endpoints["baseline"], endpoints["cgr"])

        source = _sweagent_source(args.sweagent_source)
        executable = _sweagent_executable(args.sweagent_executable)
        source_identity = verify_pinned_sweagent(source, executable)
        summaries: list[dict[str, Any]] = []
        overall = 0
        for selected_mode in modes:
            endpoint = endpoints[selected_mode]
            generation = generate_attempt(
                result_root=args.result_root,
                mode=selected_mode,
                instance=instance,
                problem_statement=str(record["problem_statement"]),
                endpoint=endpoint,
                source=source,
                executable=executable,
                manifest_hash=manifest.selected_ids_sha256,
                dataset_fingerprint=manifest.dataset_fingerprint,
                source_identity=source_identity,
            )
            summary: dict[str, Any] = {"generation": generation}
            if generation["infrastructure_status"] != "completed":
                overall = 1
                summaries.append(summary)
                if args.compare and selected_mode == "baseline":
                    break
                continue
            if args.generate_and_evaluate:
                evaluation = evaluate_attempt(
                    Path(generation["artifact_directory"]),
                    instance=instance,
                    manifest_hash=manifest.selected_ids_sha256,
                    evaluator=generation_evaluator,
                )
                summary["evaluation"] = evaluation
                overall = overall or int(evaluation["exit_code"] != 0)
            summaries.append(summary)
            if (
                args.compare
                and selected_mode == "baseline"
                and args.generate_and_evaluate
                and summary["evaluation"]["exit_code"] != 0
            ):
                break
        output = {"ok": overall == 0, "runs": summaries}
        print(json.dumps(output, indent=2))
        return overall
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(json.dumps({"ok": False, "error": _redact(str(exc), _configured_secrets())}))
        return 1


def generate_attempt(
    *,
    result_root: Path,
    mode: str,
    instance: PilotInstance,
    problem_statement: str,
    endpoint: ModelEndpoint,
    source: Path,
    executable: str,
    manifest_hash: str,
    dataset_fingerprint: str | None,
    source_identity: dict[str, str],
) -> dict[str, Any]:
    attempt = _allocate_attempt(result_root, mode, instance.instance_id)
    started = time.perf_counter()
    process: subprocess.CompletedProcess[str] | None = None
    secrets = [endpoint.api_key]
    result: dict[str, Any] = {
        "mode": mode,
        "instance_id": instance.instance_id,
        "repository": instance.repo,
        "base_commit": instance.base_commit,
        "dataset_name": DATASET_NAME,
        "dataset_fingerprint": dataset_fingerprint,
        "manifest_hash": manifest_hash,
        "artifact_directory": str(attempt.resolve()),
        "model_endpoint": endpoint.base_url,
        "model_identifier": endpoint.model,
        "context_length": _context_length(),
        "temperature": 0.0,
        "call_budget": NATIVE_CALL_BUDGET,
        "step_budget": NATIVE_STEP_BUDGET,
        "upstream_commit": source_identity["upstream_commit"],
        "parser_patch_sha256": source_identity["parser_patch_sha256"],
        "imported_sweagent_path": source_identity["imported_sweagent_path"],
        "infrastructure_status": "failed",
        "prediction_status": None,
        "prediction_path": None,
        "prediction_sha256": None,
        "generation_exit_code": None,
    }
    try:
        _check_endpoint(endpoint)
        problem_path = attempt / "problem-statement.md"
        problem_path.write_text(problem_statement, encoding="utf-8")
        overlay_path = attempt / "effective-config.yaml"
        overlay_path.write_text(
            render_native_overlay(instance, endpoint, problem_path), encoding="utf-8"
        )
        command = build_native_sweagent_command(
            executable=executable,
            source=source,
            overlay_path=overlay_path,
            output_dir=attempt,
        )
        _write_json(attempt / "sweagent-command.json", command)
        _write_json(
            attempt / "environment.json",
            {
                "CGR_NATIVE_API_KEY": "[REDACTED]",
                "SWE_AGENT_CONFIG_ROOT": str(source),
                "model_endpoint": endpoint.base_url,
                "model_identifier": endpoint.model,
            },
        )
        environment = os.environ.copy()
        environment["CGR_NATIVE_API_KEY"] = endpoint.api_key
        environment["SWE_AGENT_CONFIG_ROOT"] = str(source)
        process = subprocess.run(
            command,
            cwd=source,
            capture_output=True,
            text=True,
            timeout=NATIVE_TIMEOUT_SECONDS,
            check=False,
            env=environment,
        )
        result["generation_exit_code"] = process.returncode
        (attempt / "sweagent.stdout.log").write_text(
            _redact(process.stdout, secrets), encoding="utf-8"
        )
        (attempt / "sweagent.stderr.log").write_text(
            _redact(process.stderr, secrets), encoding="utf-8"
        )
        _redact_artifact_tree(attempt, secrets)
        if process.returncode:
            raise RuntimeError(f"Official SWE-agent exited with code {process.returncode}.")
        prediction_path, prediction = _official_prediction(attempt, instance.instance_id)
        prediction_sha256 = _sha256_file(prediction_path)
        (attempt / "prediction.sha256").write_text(prediction_sha256 + "\n", encoding="ascii")
        result.update(
            {
                "infrastructure_status": "completed",
                "prediction_status": (
                    "unresolved" if prediction["model_patch"] is None else "patch_submitted"
                ),
                "prediction_path": str(prediction_path.resolve()),
                "prediction_sha256": prediction_sha256,
            }
        )
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        result["generation_error"] = _redact(str(exc), secrets)
        (attempt / "failure-traceback.txt").write_text(
            _redact(traceback.format_exc(), secrets), encoding="utf-8"
        )
        if process is not None and not (attempt / "sweagent.stdout.log").exists():
            (attempt / "sweagent.stdout.log").write_text(
                _redact(process.stdout, secrets), encoding="utf-8"
            )
            (attempt / "sweagent.stderr.log").write_text(
                _redact(process.stderr, secrets), encoding="utf-8"
            )
    result["elapsed_seconds"] = time.perf_counter() - started
    result["artifact_hashes"] = _hash_artifacts(attempt)
    _write_json(attempt / "artifact-sha256.json", result["artifact_hashes"])
    _write_json(attempt / "generation-result.json", result)
    _assert_no_secrets(attempt, secrets)
    return result


def evaluate_attempt(
    attempt: Path,
    *,
    instance: PilotInstance,
    manifest_hash: str,
    evaluator: EvaluatorRuntime | None = None,
) -> dict[str, Any]:
    evaluator = evaluator or verify_evaluator_runtime()
    generation_path = attempt / "generation-result.json"
    if not generation_path.is_file():
        raise FileNotFoundError("Native generation metadata is missing.")
    generation = json.loads(generation_path.read_text(encoding="utf-8"))
    _verify_generation_identity(generation, instance, manifest_hash)
    prediction_path = Path(str(generation["prediction_path"])).resolve(strict=True)
    expected_hash = str(generation["prediction_sha256"])
    actual_hash = _sha256_file(prediction_path)
    if actual_hash != expected_hash:
        raise ValueError("Official prediction changed after generation.")
    _official_prediction(attempt, instance.instance_id, expected_path=prediction_path)

    evaluation_dir = attempt / "official-evaluation"
    evaluation_dir.mkdir(exist_ok=False)
    evaluator_prediction = _official_evaluator_prediction(prediction_path, evaluation_dir)
    run_id = f"cgr-native-{generation['mode']}-{instance.instance_id}"
    command = official_harness_command(str(evaluator_prediction), [instance.instance_id], run_id)
    command[0] = evaluator.python
    _write_json(evaluation_dir / "evaluator-command.json", command)
    started = time.perf_counter()
    process = subprocess.run(
        command,
        cwd=evaluation_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    stdout = _redact(process.stdout, _configured_secrets())
    stderr = _redact(process.stderr, _configured_secrets())
    (evaluation_dir / "evaluator.stdout.log").write_text(stdout, encoding="utf-8")
    (evaluation_dir / "evaluator.stderr.log").write_text(stderr, encoding="utf-8")
    from .cli import _find_official_result

    resolved, report_path = _find_official_result(
        run_id, instance.instance_id, stdout, evaluation_dir
    )
    infrastructure_status = (
        "completed" if process.returncode == 0 and resolved is not None else "failed"
    )
    result = {
        "mode": generation["mode"],
        "instance_id": instance.instance_id,
        "base_commit": instance.base_commit,
        "manifest_hash": manifest_hash,
        "evaluator_python": evaluator.python,
        "evaluator_version": evaluator.version,
        "evaluator_package_path": evaluator.package_path,
        "evaluator_harness_path": evaluator.harness_path,
        "prediction_path": str(prediction_path),
        "evaluator_prediction_path": str(evaluator_prediction),
        "prediction_sha256": actual_hash,
        "evaluator_command": command,
        "evaluation_exit_code": process.returncode,
        "elapsed_seconds": time.perf_counter() - started,
        "resolved": resolved,
        "official_report_path": report_path,
        "infrastructure_status": infrastructure_status,
        "exit_code": 0 if infrastructure_status == "completed" else 1,
    }
    if infrastructure_status != "completed":
        result["evaluation_error"] = "Official evaluator produced no trustworthy completed result."
    result["artifact_hashes"] = _hash_artifacts(evaluation_dir)
    _write_json(evaluation_dir / "evaluation-artifact-sha256.json", result["artifact_hashes"])
    _write_json(evaluation_dir / "evaluation-result.json", result)
    _assert_no_secrets(evaluation_dir, _configured_secrets())
    return result


def render_native_overlay(
    instance: PilotInstance,
    endpoint: ModelEndpoint,
    problem_path: Path,
) -> str:
    repo_name = instance.repo.rsplit("/", 1)[-1]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", repo_name):
        raise ValueError("Repository name is not safe for SWE-agent deployment commands.")
    base = LOCAL_QWEN_OVERLAY.replace("git -C /repo", f"git -C /{repo_name}")
    env_fields = (
        "env:\n"
        "  deployment:\n"
        "    type: docker\n"
        "    image: python:3.12\n"
        "  repo:\n"
        "    type: github\n"
        f"    github_url: {json.dumps('https://github.com/' + instance.repo)}\n"
        f"    base_commit: {json.dumps(instance.base_commit)}\n"
    )
    base = base.replace("env:\n", env_fields, 1)
    model_fields = (
        "problem_statement:\n"
        "  type: text_file\n"
        f"  path: {json.dumps(str(problem_path.resolve()))}\n"
        f"  id: {json.dumps(instance.instance_id)}\n"
        "agent:\n"
        "  model:\n"
        f"    name: {json.dumps(endpoint.sweagent_model)}\n"
        f"    api_base: {json.dumps(endpoint.base_url)}\n"
        "    api_key: $CGR_NATIVE_API_KEY\n"
        "    temperature: 0.0\n"
        "    per_instance_cost_limit: 0\n"
        "    total_cost_limit: 0\n"
        f"    per_instance_call_limit: {NATIVE_CALL_BUDGET}\n"
        f"    max_input_tokens: {_context_length() - 2048}\n"
        "    max_output_tokens: 2048\n"
    )
    return base.replace("agent:\n", model_fields, 1)


def build_native_sweagent_command(
    *, executable: str, source: Path, overlay_path: Path, output_dir: Path
) -> list[str]:
    return [
        executable,
        "run",
        "--config",
        str((source / "config" / "default.yaml").resolve(strict=True)),
        "--config",
        str(overlay_path.resolve(strict=True)),
        "--output_dir",
        str(output_dir.resolve(strict=True)),
    ]


def verify_pinned_sweagent(source: Path, executable: str) -> dict[str, str]:
    source = source.resolve(strict=True)
    patch = SWE_AGENT_PATCH.resolve(strict=True)
    patch_sha = _sha256_file(patch)
    if patch_sha != SWE_AGENT_PATCH_SHA256:
        raise ValueError("Maintained SWE-agent parser patch SHA-256 differs from the pin.")
    commit = _run_checked(["git", "-C", str(source), "rev-parse", "HEAD"]).stdout.strip()
    if commit != SWE_AGENT_COMMIT:
        raise ValueError("Pinned SWE-agent source commit differs from the required commit.")
    reverse_check = subprocess.run(
        ["git", "-C", str(source), "apply", "--reverse", "--check", str(patch)],
        capture_output=True,
        text=True,
        check=False,
    )
    if reverse_check.returncode:
        raise ValueError("Maintained strict parser patch is not applied to SWE-agent source.")
    parsing = (source / "sweagent" / "tools" / "parsing.py").read_text(encoding="utf-8")
    if "class StrictThoughtActionParser" not in parsing or '"strict_thought_action"' not in parsing:
        raise ValueError("strict_thought_action is absent from pinned SWE-agent source.")
    python = _sweagent_python(executable)
    imported = _run_checked(
        [
            python,
            "-c",
            "import pathlib,sweagent; print(pathlib.Path(sweagent.__file__).resolve())",
        ]
    ).stdout.strip()
    imported_path = Path(imported).resolve(strict=True)
    if source not in imported_path.parents:
        raise ValueError("Imported sweagent package resolves outside .swe-agent-src.")
    return {
        "upstream_commit": commit,
        "parser_patch_sha256": patch_sha,
        "imported_sweagent_path": str(imported_path),
    }


def verify_evaluator_runtime() -> EvaluatorRuntime:
    configured = os.getenv("CGR_SWEBENCH_EVALUATOR_PYTHON", "").strip()
    if not configured:
        raise ValueError(
            "CGR_SWEBENCH_EVALUATOR_PYTHON is required for official evaluation. "
            "Run scripts/setup_swebench_evaluator.sh first."
        )
    python = Path(configured).expanduser()
    if not python.is_file() or not os.access(python, os.X_OK):
        raise ValueError(
            "CGR_SWEBENCH_EVALUATOR_PYTHON is not an executable file: " + str(python)
        )
    probe = subprocess.run(
        [
            str(python.resolve()),
            "-c",
            (
                "import importlib.metadata,json,pathlib,swebench,swebench.harness;"
                "print(json.dumps({"
                "'version':importlib.metadata.version('swebench'),"
                "'package_path':str(pathlib.Path(swebench.__file__).resolve()),"
                "'harness_path':str(pathlib.Path(swebench.harness.__file__).resolve())}))"
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode:
        detail = (probe.stderr or probe.stdout).strip()[-1000:]
        raise ValueError(
            "CGR_SWEBENCH_EVALUATOR_PYTHON cannot import swebench and "
            f"swebench.harness: {detail}"
        )
    try:
        identity = json.loads(probe.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("Evaluator Python returned malformed package identity output.") from exc
    if not isinstance(identity, dict) or identity.get("version") != SWEBENCH_EVALUATOR_VERSION:
        found = identity.get("version") if isinstance(identity, dict) else None
        raise ValueError(
            "Evaluator version differs from the frozen configuration: "
            f"expected {SWEBENCH_EVALUATOR_VERSION}, found {found}."
        )
    package_path = identity.get("package_path")
    harness_path = identity.get("harness_path")
    if not isinstance(package_path, str) or not isinstance(harness_path, str):
        raise ValueError("Evaluator Python returned incomplete package identity output.")
    return EvaluatorRuntime(
        python=str(python.resolve()),
        version=SWEBENCH_EVALUATOR_VERSION,
        package_path=package_path,
        harness_path=harness_path,
    )


def _model_endpoint(mode: str) -> ModelEndpoint:
    prefix = "CGR_DRAFT" if mode == "baseline" else "CGR_RUNTIME"
    values = {
        name: os.getenv(f"{prefix}_{name}", "").strip()
        for name in ("BASE_URL", "API_KEY", "MODEL")
    }
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise ValueError(f"{mode} mode requires {', '.join(prefix + '_' + name for name in missing)}.")
    return ModelEndpoint(
        mode=mode,  # type: ignore[arg-type]
        base_url=values["BASE_URL"].rstrip("/"),
        api_key=values["API_KEY"],
        model=values["MODEL"],
    )


def _verify_distinct_comparison_endpoints(
    baseline: ModelEndpoint, cgr: ModelEndpoint
) -> None:
    if (baseline.base_url, baseline.model) == (cgr.base_url, cgr.model):
        raise ValueError("Baseline and CGR comparison endpoints resolve to the same identity.")


def _check_endpoint(endpoint: ModelEndpoint) -> None:
    request = urllib.request.Request(
        endpoint.base_url + "/models",
        headers={"Authorization": f"Bearer {endpoint.api_key}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            if response.status >= 400:
                raise RuntimeError(f"Model endpoint returned HTTP {response.status}.")
    except (OSError, urllib.error.URLError) as exc:
        raise RuntimeError(f"Model endpoint is unavailable: {exc}") from exc


def _selected_instance(instances: Sequence[PilotInstance], instance_id: str) -> PilotInstance:
    for instance in instances:
        if instance.instance_id == instance_id:
            return instance
    raise ValueError("Requested instance is not in the frozen manifest.")


def _verified_record(records: Sequence[dict[str, Any]], instance: PilotInstance) -> dict[str, Any]:
    for record in records:
        if record.get("instance_id") != instance.instance_id:
            continue
        if record.get("repo") != instance.repo or record.get("base_commit") != instance.base_commit:
            raise ValueError("Frozen instance repository identity differs from the dataset record.")
        if not isinstance(record.get("problem_statement"), str):
            raise ValueError("Frozen dataset record has no problem statement.")
        return record
    raise ValueError("Frozen instance is absent from the loaded SWE-bench dataset.")


def _official_prediction(
    attempt: Path, instance_id: str, *, expected_path: Path | None = None
) -> tuple[Path, dict[str, Any]]:
    path = expected_path or attempt / instance_id / f"{instance_id}.pred"
    if not path.is_file():
        raise FileNotFoundError("Official SWE-agent produced no .pred artifact.")
    try:
        prediction = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("Official SWE-agent .pred is not valid JSON.") from exc
    if not isinstance(prediction, dict):
        raise ValueError("Official SWE-agent .pred is not a JSON object.")
    if prediction.get("instance_id") != instance_id:
        raise ValueError("Official SWE-agent .pred instance ID differs from the request.")
    if "model_patch" not in prediction:
        raise ValueError("Official SWE-agent .pred is missing model_patch.")
    if prediction["model_patch"] is not None and not isinstance(prediction["model_patch"], str):
        raise ValueError("Official SWE-agent .pred model_patch has an invalid type.")
    return path, prediction


def _verify_generation_identity(
    generation: dict[str, Any], instance: PilotInstance, manifest_hash: str
) -> None:
    if generation.get("infrastructure_status") != "completed":
        raise ValueError("Generation did not complete successfully; evaluation is refused.")
    if generation.get("instance_id") != instance.instance_id:
        raise ValueError("Generation instance ID differs from the evaluation request.")
    if generation.get("repository") != instance.repo:
        raise ValueError("Generation repository differs from the frozen manifest.")
    if generation.get("base_commit") != instance.base_commit:
        raise ValueError("Generation base commit differs from the frozen manifest.")
    if generation.get("dataset_name") != DATASET_NAME:
        raise ValueError("Generation dataset identity differs from SWE-bench Verified.")
    if generation.get("manifest_hash") != manifest_hash:
        raise ValueError("Generation manifest hash differs from the frozen manifest.")


def _official_evaluator_prediction(prediction: Path, evaluation_dir: Path) -> Path:
    """Expose the official .pred bytes under the extension required by SWE-bench."""
    evaluator_path = evaluation_dir / "official-sweagent-prediction.json"
    os.link(prediction, evaluator_path)
    if not evaluator_path.samefile(prediction):
        raise ValueError("Evaluator prediction is not the official SWE-agent .pred artifact.")
    return evaluator_path


def _allocate_attempt(result_root: Path, mode: str, instance_id: str) -> Path:
    root = result_root / mode / instance_id
    root.mkdir(parents=True, exist_ok=True)
    attempt_number = 1
    while (root / f"attempt-{attempt_number:03d}").exists():
        attempt_number += 1
    attempt = root / f"attempt-{attempt_number:03d}"
    attempt.mkdir()
    return attempt


def _latest_completed_attempt(result_root: Path, mode: str, instance_id: str) -> Path:
    root = result_root / mode / instance_id
    for attempt in sorted(root.glob("attempt-*"), reverse=True):
        result_path = attempt / "generation-result.json"
        if not result_path.is_file():
            continue
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if result.get("infrastructure_status") == "completed":
            return attempt
    raise FileNotFoundError("No completed native generation attempt is available for evaluation.")


def _hash_artifacts(root: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name in _METADATA_FILES:
            continue
        hashes[path.relative_to(root).as_posix()] = _sha256_file(path)
    return hashes


def _redact_artifact_tree(root: Path, secrets: Sequence[str]) -> None:
    encoded = [secret.encode() for secret in secrets if secret]
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix == ".pred":
            continue
        payload = path.read_bytes()
        redacted = payload
        for secret in encoded:
            redacted = redacted.replace(secret, b"[REDACTED]")
        if redacted != payload:
            path.write_bytes(redacted)


def _assert_no_secrets(root: Path, secrets: Sequence[str]) -> None:
    encoded = [secret.encode() for secret in secrets if secret]
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        payload = path.read_bytes()
        if any(secret in payload for secret in encoded):
            raise ValueError(f"API key leaked into retained artifact: {path.name}")


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _context_length() -> int:
    raw = os.getenv("CGR_DRAFT_MAX_MODEL_LEN", "16384")
    try:
        context = int(raw)
    except ValueError as exc:
        raise ValueError("CGR_DRAFT_MAX_MODEL_LEN must be an integer.") from exc
    if context != 16384:
        raise ValueError("Native pilot context length must remain frozen at 16384.")
    return context


def _sweagent_source(configured: Path | None) -> Path:
    source = configured or Path(os.getenv("CGR_SWE_AGENT_SOURCE", ".swe-agent-src"))
    return source.expanduser().resolve(strict=True)


def _sweagent_executable(configured: str | None) -> str:
    executable = configured or os.getenv("CGR_SWE_AGENT_EXECUTABLE", "sweagent")
    if not executable:
        raise ValueError("Official SWE-agent executable is not configured.")
    return executable


def _sweagent_python(executable: str) -> str:
    configured = os.getenv("CGR_SWE_AGENT_PYTHON")
    if configured:
        return configured
    path = Path(executable)
    if path.is_absolute():
        candidate = path.parent / ("python.exe" if os.name == "nt" else "python")
        if candidate.is_file():
            return str(candidate)
    return sys.executable


def _configured_secrets() -> list[str]:
    return [
        value
        for name in ("CGR_DRAFT_API_KEY", "CGR_RUNTIME_API_KEY")
        if (value := os.getenv(name, ""))
    ]


def _redact(value: str, secrets: Sequence[str]) -> str:
    for secret in secrets:
        if secret:
            value = value.replace(secret, "[REDACTED]")
    return value


def _run_checked(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=True)


if __name__ == "__main__":
    raise SystemExit(native_pilot_main())
