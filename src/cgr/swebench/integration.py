"""Core models and integrity controls for the SWE-bench Verified pilot."""

from __future__ import annotations

import hashlib
import ast
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


DATASET_NAME = "princeton-nlp/SWE-bench_Verified"
PILOT_NAME = "swebench-verified-pilot-v1"
SELECTION_SEED = "cgr-swebench-verified-pilot-v1"
DEFAULT_MANIFEST = Path("benchmark-manifests/swebench-verified-pilot-v1.json")
RESULT_ROOT = Path("benchmark-results/swebench-verified-pilot-v1")
MODES = ("baseline", "cgr_single", "cgr_multi")
FORBIDDEN_MODEL_FIELDS = frozenset(
    {
        "patch",
        "test_patch",
        "gold_patch",
        "FAIL_TO_PASS",
        "PASS_TO_PASS",
        "version",
        "environment_setup_commit",
        "expected_files",
    }
)


class SafeInstance(BaseModel):
    """Only dataset fields approved for model-facing use."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str
    hints_text: str = ""
    workspace_path: str | None = None
    safe_metadata: dict[str, str] = Field(default_factory=dict)


class PilotInstance(BaseModel):
    model_config = ConfigDict(frozen=True)

    instance_id: str
    repo: str
    base_commit: str


class SwebenchManifest(BaseModel):
    model_config = ConfigDict(frozen=True)

    pilot_name: str = PILOT_NAME
    dataset_name: str = DATASET_NAME
    dataset_revision: str | None = None
    dataset_fingerprint: str | None = None
    selection_algorithm: str
    selection_seed: str = SELECTION_SEED
    created_at: str
    selected_ids_sha256: str
    status: Literal["frozen"] = "frozen"
    instances: list[PilotInstance]


class Prediction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    instance_id: str
    model_name_or_path: str
    model_patch: str


class ModeBudget(BaseModel):
    model_config = ConfigDict(frozen=True)

    trajectories: int
    maximum_model_calls: int
    maximum_steps: int
    timeout_seconds: int


DEFAULT_BUDGETS = {
    "baseline": ModeBudget(
        trajectories=1, maximum_model_calls=8, maximum_steps=20, timeout_seconds=1800
    ),
    "cgr_single": ModeBudget(
        trajectories=1, maximum_model_calls=10, maximum_steps=24, timeout_seconds=2100
    ),
    "cgr_multi": ModeBudget(
        trajectories=3, maximum_model_calls=18, maximum_steps=36, timeout_seconds=3600
    ),
}


def filter_model_instance(
    record: Mapping[str, Any], workspace_path: str | None = None
) -> SafeInstance:
    """Construct a model-facing object without answer or evaluator fields."""
    required = ("instance_id", "repo", "base_commit", "problem_statement")
    missing = [key for key in required if not isinstance(record.get(key), str)]
    if missing:
        raise ValueError(f"Dataset record is missing safe fields: {', '.join(missing)}")
    return SafeInstance(
        instance_id=record["instance_id"],
        repo=record["repo"],
        base_commit=record["base_commit"],
        problem_statement=record["problem_statement"],
        hints_text=record.get("hints_text", "") or "",
        workspace_path=workspace_path,
        safe_metadata={
            "created_at": str(record.get("created_at", "")),
            "dataset": DATASET_NAME,
        },
    )


def selected_ids_hash(instance_ids: Sequence[str]) -> str:
    payload = "\n".join(sorted(instance_ids)).encode()
    return hashlib.sha256(payload).hexdigest()


def deterministic_select(
    records: Iterable[Mapping[str, Any]], count: int = 10
) -> list[PilotInstance]:
    """Select by seeded ID hash, preferring one instance per repository first."""
    safe = [
        PilotInstance(
            instance_id=str(record["instance_id"]),
            repo=str(record["repo"]),
            base_commit=str(record["base_commit"]),
        )
        for record in records
    ]
    unique = {instance.instance_id: instance for instance in safe}
    ranked = sorted(
        unique.values(),
        key=lambda item: hashlib.sha256(
            f"{SELECTION_SEED}\0{item.instance_id}".encode()
        ).hexdigest(),
    )
    selected: list[PilotInstance] = []
    seen_repos: set[str] = set()
    for instance in ranked:
        if instance.repo in seen_repos:
            continue
        selected.append(instance)
        seen_repos.add(instance.repo)
        if len(selected) == count:
            break
    if len(selected) < count:
        selected_ids = {instance.instance_id for instance in selected}
        selected.extend(
            instance
            for instance in ranked
            if instance.instance_id not in selected_ids
        )
    selected = selected[:count]
    if len(selected) != count:
        raise ValueError(f"Dataset contains fewer than {count} unique instances.")
    return sorted(selected, key=lambda item: item.instance_id)


def freeze_manifest(
    records: Iterable[Mapping[str, Any]],
    path: Path = DEFAULT_MANIFEST,
    *,
    dataset_revision: str | None = None,
    dataset_fingerprint: str | None = None,
    force_development: bool = False,
) -> SwebenchManifest:
    if path.exists() and not force_development:
        existing = SwebenchManifest.model_validate_json(path.read_text(encoding="utf-8"))
        if existing.status == "frozen":
            raise FileExistsError(f"Refusing to overwrite frozen manifest: {path}")
    instances = deterministic_select(records)
    manifest = SwebenchManifest(
        dataset_revision=dataset_revision,
        dataset_fingerprint=dataset_fingerprint,
        selection_algorithm=(
            "SHA-256(seed + NUL + instance_id), first unique repository pass, "
            "then ranked fill; final IDs sorted"
        ),
        created_at=datetime.now(UTC).isoformat(),
        selected_ids_sha256=selected_ids_hash(
            [instance.instance_id for instance in instances]
        ),
        instances=instances,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest.model_dump(mode="json"), indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def load_manifest(path: Path = DEFAULT_MANIFEST) -> SwebenchManifest:
    if not path.exists():
        raise FileNotFoundError(f"Frozen pilot manifest does not exist: {path}")
    manifest = SwebenchManifest.model_validate_json(path.read_text(encoding="utf-8"))
    validate_manifest(manifest)
    return manifest


def validate_manifest(manifest: SwebenchManifest) -> None:
    ids = [instance.instance_id for instance in manifest.instances]
    if manifest.status != "frozen":
        raise ValueError("Pilot manifest is not frozen.")
    if len(ids) != 10 or len(set(ids)) != 10:
        raise ValueError("Pilot manifest must contain exactly ten unique IDs.")
    if ids != sorted(ids):
        raise ValueError("Pilot instance IDs must be sorted.")
    if len({instance.repo for instance in manifest.instances}) < 5:
        raise ValueError("Pilot must include at least five repositories.")
    if manifest.selected_ids_sha256 != selected_ids_hash(ids):
        raise ValueError("Pilot selected-ID hash does not match.")
    if manifest.dataset_name != DATASET_NAME:
        raise ValueError("Pilot dataset identifier does not match Verified.")


def write_predictions(path: Path, predictions: Sequence[Prediction]) -> str:
    if len({prediction.instance_id for prediction in predictions}) != len(predictions):
        raise ValueError("Prediction instance IDs must be unique.")
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(item.model_dump(mode="json"), sort_keys=True) for item in predictions]
    payload = ("\n".join(lines) + "\n").encode()
    path.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    path.with_suffix(".sha256").write_text(digest + "\n", encoding="ascii")
    return digest


def validate_prediction_hash(path: Path) -> None:
    hash_path = path.with_suffix(".sha256")
    expected = hash_path.read_text(encoding="ascii").strip()
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if expected != actual:
        raise ValueError(f"Prediction hash mismatch: {path}")


def capture_git_patch(repository: Path) -> tuple[str, list[str]]:
    _run(["git", "add", "--intent-to-add", "--", "."], repository)
    result = _run(["git", "diff", "--no-ext-diff", "--full-index"], repository)
    patch = result.stdout
    if not patch.strip():
        raise ValueError("Generated SWE-bench patch is empty.")
    if "GIT binary patch" in patch or "Binary files " in patch:
        raise ValueError("Binary patches are not supported by the first pilot.")
    changed = sorted(
        line.split(" b/", 1)[1]
        for line in patch.splitlines()
        if line.startswith("diff --git a/") and " b/" in line
    )
    if not changed:
        raise ValueError("Patch does not contain unified Git file changes.")
    if any(path == ".git" or path.startswith(".git/") or ".." in Path(path).parts for path in changed):
        raise ValueError("Patch changes a forbidden path.")
    return patch, changed


def verify_patch_applies(repository: Path, patch: str, base_commit: str) -> None:
    with tempfile.TemporaryDirectory(prefix="cgr-swebench-apply-") as temp:
        checkout = Path(temp) / "checkout"
        _run(["git", "clone", "--quiet", "--no-hardlinks", str(repository), str(checkout)])
        _run(["git", "checkout", "--quiet", base_commit], checkout)
        process = subprocess.run(
            ["git", "apply", "--check", "-"],
            cwd=checkout,
            input=patch,
            text=True,
            capture_output=True,
            check=False,
        )
        if process.returncode:
            raise ValueError(f"Patch does not apply at base_commit: {process.stderr.strip()}")


def official_harness_command(
    predictions_path: str,
    instance_ids: Sequence[str],
    run_id: str,
    *,
    max_workers: int = 1,
) -> list[str]:
    return [
        os.fspath(Path(sys.executable)),
        "-m",
        "swebench.harness.run_evaluation",
        "--dataset_name",
        DATASET_NAME,
        "--predictions_path",
        predictions_path,
        "--max_workers",
        str(max_workers),
        "--instance_ids",
        *instance_ids,
        "--run_id",
        run_id,
    ]


def doctor_report(manifest_path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    docker_cli = shutil.which("docker")
    docker_daemon = False
    docker_error: str | None = None
    if docker_cli:
        result = subprocess.run(
            [docker_cli, "info"], capture_output=True, text=True, check=False
        )
        docker_daemon = result.returncode == 0
        docker_error = None if docker_daemon else (result.stderr or result.stdout)[-500:]
    disk = shutil.disk_usage(Path.cwd())
    return {
        "docker_cli_available": docker_cli is not None,
        "docker_daemon_available": docker_daemon,
        "docker_error": docker_error,
        "platform": platform.platform(),
        "system": platform.system(),
        "architecture": platform.machine(),
        "supported_platform": platform.system() == "Linux",
        "disk_free_bytes": disk.free,
        "disk_space_warning": disk.free < 120 * 1024**3,
        "swebench_package_available": importlib.util.find_spec("swebench") is not None,
        "datasets_package_available": importlib.util.find_spec("datasets") is not None,
        "git_available": shutil.which("git") is not None,
        "qwen_endpoint_configured": bool(
            os.getenv("CGR_DRAFT_API_KEY")
            and os.getenv("CGR_DRAFT_BASE_URL")
            and os.getenv("CGR_DRAFT_MODEL")
        ),
        "frozen_manifest_exists": manifest_path.exists(),
    }


def load_verified_records() -> tuple[list[dict[str, Any]], str | None]:
    try:
        datasets = __import__("datasets", fromlist=["load_dataset"])
    except ImportError as exc:
        raise RuntimeError("The optional 'datasets' package is required.") from exc
    dataset = datasets.load_dataset(DATASET_NAME, split="test")
    return [dict(record) for record in dataset], getattr(dataset, "_fingerprint", None)


def materialize_repository(instance: SafeInstance, destination: Path) -> Path:
    if destination.exists():
        raise FileExistsError(f"Workspace already exists: {destination}")
    url = f"https://github.com/{instance.repo}.git"
    try:
        _run(["git", "clone", "--no-checkout", url, str(destination)])
        _run(["git", "checkout", "--detach", instance.base_commit], destination)
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    return destination


class RepositoryActions:
    """Bounded repository interaction surface shared by all experiment modes."""

    SAFE_COMMANDS = {"pytest", "python", "python3", "ruff", "mypy", "tox", "nox"}

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def list_files(self, limit: int = 2000) -> list[str]:
        return [
            path.relative_to(self.root).as_posix()
            for path in sorted(self.root.rglob("*"))
            if path.is_file() and ".git" not in path.parts
        ][:limit]

    def search_text(self, pattern: str, limit: int = 200) -> list[str]:
        command = ["rg", "-n", "--", pattern, "."]
        result = _run(command, self.root, check=False)
        return result.stdout.splitlines()[:limit]

    def read_file(self, relative_path: str, start: int = 1, end: int = 400) -> str:
        path = self._safe_path(relative_path)
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[max(0, start - 1) : end])

    def inspect_symbols(self, relative_path: str) -> list[dict[str, Any]]:
        path = self._safe_path(relative_path)
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        return [
            {
                "name": node.name,
                "kind": "class" if isinstance(node, ast.ClassDef) else "function",
                "line": node.lineno,
            }
            for node in tree.body
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        ]

    def write_file(self, relative_path: str, content: str) -> None:
        path = self._safe_path(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def apply_patch(self, patch: str) -> None:
        _validate_workspace_patch(patch)
        process = subprocess.run(
            ["git", "apply", "-"], cwd=self.root, input=patch, text=True, check=False
        )
        if process.returncode:
            raise ValueError("Candidate patch could not be applied.")

    def run_safe(self, command: Sequence[str], timeout: int = 600) -> subprocess.CompletedProcess[str]:
        if not command or Path(command[0]).name not in self.SAFE_COMMANDS:
            raise ValueError("Repository command is not on the safe allowlist.")
        executable = Path(command[0]).name
        if executable in {"python", "python3"} and (
            "-c" in command or ("-m" in command and "pip" in command)
        ):
            raise ValueError("Arbitrary Python and package installation are forbidden.")
        return subprocess.run(
            list(command),
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

    def git_diff(self) -> str:
        return _run(["git", "diff", "--no-ext-diff"], self.root).stdout

    def revert_candidate(self) -> None:
        _run(["git", "restore", "--worktree", "."], self.root)
        _run(["git", "clean", "-fd"], self.root)

    def _safe_path(self, relative_path: str) -> Path:
        if relative_path.startswith(".git"):
            raise ValueError("Access to .git is forbidden.")
        path = (self.root / relative_path).resolve()
        if self.root not in path.parents and path != self.root:
            raise ValueError("Path escapes repository workspace.")
        return path


def generation_result_template(
    mode: str, model: str, provider: str, scaffold: str
) -> dict[str, Any]:
    if mode not in MODES:
        raise ValueError(f"Unknown mode: {mode}")
    return {
        "mode": mode,
        "model_identifier": model,
        "provider_identifier": provider,
        "scaffold_identifier": scaffold,
        "budget": DEFAULT_BUDGETS[mode].model_dump(),
        "prompt_tokens": None,
        "completion_tokens": None,
        "elapsed_seconds": 0.0,
        "local_tests_invoked": [],
        "candidate_count": 0,
        "final_changed_files": [],
        "final_patch_size": 0,
        "local_verification_passed": False,
        "local_verification_summary": "Not run",
        "official_evaluation_run": False,
        "official_resolved": None,
        "official_evaluation_log_path": None,
    }


def run_external_agent(
    command_template: str,
    instance: SafeInstance,
    mode: str,
    workspace: Path,
    budget: ModeBudget,
) -> subprocess.CompletedProcess[str]:
    """Invoke an explicitly configured repository-agent adapter."""
    if not command_template.strip():
        raise RuntimeError(
            "Set CGR_SWEBENCH_AGENT_COMMAND to a mini-SWE-agent/SWE-agent adapter."
        )
    problem_path = workspace.parent / ".cgr-problem-statement.txt"
    problem_path.write_text(instance.problem_statement, encoding="utf-8")
    arguments = json.loads(command_template)
    if not isinstance(arguments, list) or not all(isinstance(item, str) for item in arguments):
        raise ValueError("CGR_SWEBENCH_AGENT_COMMAND must be a JSON string array.")
    replacements = {
        "{workspace}": str(workspace),
        "{problem_file}": str(problem_path),
        "{mode}": mode,
        "{max_steps}": str(budget.maximum_steps),
        "{max_calls}": str(budget.maximum_model_calls),
    }
    command = [
        next((value for marker, value in replacements.items() if item == marker), item)
        for item in arguments
    ]
    return subprocess.run(
        command,
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=budget.timeout_seconds,
        check=False,
    )


def integrity_check(
    manifest_path: Path = DEFAULT_MANIFEST,
    result_root: Path = RESULT_ROOT,
) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    violations: list[str] = []
    expected_ids = [instance.instance_id for instance in manifest.instances]
    expected_bases = {
        instance.instance_id: instance.base_commit for instance in manifest.instances
    }
    model_ids: set[str] = set()
    prediction_presence = {
        mode: (result_root / mode / "predictions.jsonl").exists() for mode in MODES
    }
    if any(prediction_presence.values()) and not all(prediction_presence.values()):
        violations.append("All three mode prediction files must be locked together")
    for mode in MODES:
        mode_root = result_root / mode
        predictions = mode_root / "predictions.jsonl"
        if predictions.exists():
            try:
                validate_prediction_hash(predictions)
            except Exception as exc:
                violations.append(str(exc))
            rows = [json.loads(line) for line in predictions.read_text(encoding="utf-8").splitlines()]
            if sorted(row["instance_id"] for row in rows) != expected_ids:
                violations.append(f"{mode}: prediction IDs differ from manifest")
            model_ids.update(str(row["model_name_or_path"]) for row in rows)
        generation = mode_root / "generation-results.json"
        if generation.exists():
            text = generation.read_text(encoding="utf-8", errors="replace")
            try:
                generation_rows = json.loads(text)
            except json.JSONDecodeError:
                generation_rows = []
                violations.append(f"{mode}: generation log is not valid JSON")
            if isinstance(generation_rows, list):
                for row in generation_rows:
                    if not isinstance(row, dict) or "instance_id" not in row:
                        continue
                    expected_base = expected_bases.get(str(row["instance_id"]))
                    if expected_base is None:
                        violations.append(f"{mode}: generation contains unknown instance")
                    elif row.get("base_commit") != expected_base:
                        violations.append(f"{mode}: generation base_commit differs from manifest")
            for forbidden in FORBIDDEN_MODEL_FIELDS:
                if f'"{forbidden}"' in text:
                    violations.append(f"{mode}: forbidden field in generation log: {forbidden}")
            if "official_evaluation" in text and '"official_evaluation_run": false' not in text.lower():
                violations.append(f"{mode}: official evaluation mixed into generation log")
    if len(model_ids) > 1:
        violations.append("Modes use inconsistent model identities")
    source_text = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in Path("src").rglob("*.py")
    )
    for instance_id in expected_ids:
        if instance_id in source_text:
            violations.append(f"Task-specific source rule references {instance_id}")
    if violations:
        raise ValueError("SWE-bench integrity check failed:\n" + "\n".join(violations))
    return {"passed": True, "manifest_hash": manifest.selected_ids_sha256}


def _run(
    command: Sequence[str],
    cwd: Path | None = None,
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(command), cwd=cwd, capture_output=True, text=True, check=False
    )
    if check and result.returncode:
        raise RuntimeError(
            f"Command failed ({result.returncode}): {' '.join(command)}\n"
            f"{result.stderr[-1000:]}"
        )
    return result


def _validate_workspace_patch(patch: str) -> None:
    """Reject patch targets that could escape the isolated worktree."""
    paths: list[str] = []
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            fields = line.split()
            paths.extend(fields[2:4])
        elif line.startswith(("--- ", "+++ ")):
            paths.append(line[4:].split("\t", 1)[0])
    for raw_path in paths:
        if raw_path == "/dev/null":
            continue
        path = raw_path.removeprefix("a/").removeprefix("b/")
        if path == ".git" or path.startswith(".git/") or ".." in Path(path).parts:
            raise ValueError("Patch targets .git or escapes the repository workspace.")
