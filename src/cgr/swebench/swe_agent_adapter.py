"""Thin production adapter for the pinned upstream SWE-agent CLI.

This module deliberately does not implement a repository-action protocol.  It
starts the official SWE-agent executable, collects its trajectory artifacts, and
applies the official candidate patch to CGR's isolated workspace for the existing
integrity and candidate-verification pipeline.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Sequence

from .integration import MODES


SWE_AGENT_UPSTREAM = "https://github.com/SWE-agent/SWE-agent"
SWE_AGENT_TAG = "v1.1.0"
SWE_AGENT_COMMIT = "0f3acaf"
SWE_AGENT_PYTHON_REQUIRES = ">=3.11"
_PATCH_KEYS = ("patch", "model_patch", "submission")
_SECRET_VALUES = ("CGR_DRAFT_API_KEY",)


def build_sweagent_command(
    *,
    executable: str,
    workspace: Path,
    problem_file: Path,
    output_dir: Path,
    max_calls: int,
    max_steps: int,
    environment: dict[str, str] | None = None,
) -> list[str]:
    """Create the documented official CLI invocation for a local vLLM model."""
    env = os.environ if environment is None else environment
    base_url = _required_env(env, "CGR_DRAFT_BASE_URL")
    model = _required_env(env, "CGR_DRAFT_MODEL")
    context = _positive_int(env.get("CGR_DRAFT_MAX_MODEL_LEN", "16384"), "CGR_DRAFT_MAX_MODEL_LEN")
    max_output = min(2048, max(512, context // 8))
    max_input = context - max_output
    if max_input <= 0:
        raise ValueError("CGR_DRAFT_MAX_MODEL_LEN is too small for SWE-agent output.")
    # `openai/` is LiteLLM's supported provider prefix for OpenAI-compatible proxies.
    return [
        executable,
        "run",
        "--config",
        "config/default.yaml",
        "--output_dir",
        str(output_dir),
        "--env.repo.path",
        str(workspace),
        "--env.deployment.image",
        "python:3.12",
        "--problem_statement.path",
        str(problem_file),
        "--agent.model.name",
        f"openai/{model}",
        "--agent.model.api_base",
        base_url,
        "--agent.model.api_key",
        "$CGR_DRAFT_API_KEY",
        "--agent.model.temperature",
        "0.0",
        "--agent.model.per_instance_cost_limit",
        "0",
        "--agent.model.total_cost_limit",
        "0",
        "--agent.model.per_instance_call_limit",
        str(max_calls),
        "--agent.model.max_input_tokens",
        str(max_input),
        "--agent.model.max_output_tokens",
        str(max_output),
        "--agent.tools.parse_function.type",
        "thought_action",
    ]


def run_official_sweagent(
    command: Sequence[str], *, workspace: Path, timeout: int
) -> subprocess.CompletedProcess[str]:
    """Run the official executable with no shell and bounded captured output."""
    return subprocess.run(
        list(command),
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env=os.environ.copy(),
    )


def collect_official_patch(output_dir: Path) -> tuple[str, Path]:
    """Extract a non-empty patch from a documented trajectory artifact shape."""
    candidates = sorted(output_dir.rglob("*.patch"))
    for path in candidates:
        patch = path.read_text(encoding="utf-8", errors="replace")
        if _looks_like_patch(patch):
            return patch, path
    for path in sorted(output_dir.rglob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        candidate_patch = _find_patch(payload)
        if candidate_patch and _looks_like_patch(candidate_patch):
            return candidate_patch, path
    raise ValueError("Official SWE-agent produced no non-empty unified patch artifact.")


def apply_official_patch(workspace: Path, patch: str) -> None:
    """Apply only a safe, checkable official patch to the supplied worktree."""
    _validate_patch_paths(patch)
    check = subprocess.run(
        ["git", "apply", "--check", "-"], cwd=workspace, input=patch, text=True,
        capture_output=True, check=False,
    )
    if check.returncode:
        raise ValueError(f"Official SWE-agent patch does not apply: {check.stderr[-1000:]}")
    applied = subprocess.run(
        ["git", "apply", "-"], cwd=workspace, input=patch, text=True,
        capture_output=True, check=False,
    )
    if applied.returncode:
        raise ValueError(f"Official SWE-agent patch could not be applied: {applied.stderr[-1000:]}")


def adapter_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the pinned official SWE-agent for CGR.")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--problem-file", type=Path, required=True)
    parser.add_argument("--mode", choices=MODES, required=True)
    parser.add_argument("--max-steps", type=int, required=True)
    parser.add_argument("--max-calls", type=int, required=True)
    args = parser.parse_args(argv)
    started = time.perf_counter()
    try:
        workspace = args.workspace.resolve(strict=True)
        problem_file = args.problem_file.resolve(strict=True)
        if not workspace.is_dir() or not (workspace / ".git").exists():
            raise ValueError("--workspace must be an existing isolated Git worktree.")
        if workspace.parent != problem_file.parent:
            # CGR stores the safe problem text alongside its temporary worktree only.
            raise ValueError("--problem-file must be inside the workspace parent directory.")
        if args.max_steps <= 0 or args.max_calls <= 0:
            raise ValueError("--max-steps and --max-calls must be positive.")
        executable = os.getenv("CGR_SWE_AGENT_EXECUTABLE") or "sweagent"
        if shutil.which(executable) is None:
            raise RuntimeError("Pinned official SWE-agent executable is unavailable: " + executable)
        output_dir = workspace.parent / ".cgr-sweagent-trajectories"
        output_dir.mkdir(parents=True, exist_ok=True)
        command = build_sweagent_command(
            executable=executable,
            workspace=workspace,
            problem_file=problem_file,
            output_dir=output_dir,
            max_calls=args.max_calls,
            max_steps=args.max_steps,
        )
        process = run_official_sweagent(command, workspace=workspace, timeout=_timeout(args.mode))
        if process.returncode:
            raise RuntimeError("Official SWE-agent exited with code " + str(process.returncode))
        patch, artifact = collect_official_patch(output_dir)
        apply_official_patch(workspace, patch)
        verification = _verify_applied_patch(workspace)
        payload = {
            "ok": True,
            "finished": True,
            "mode": args.mode,
            "official_sweagent": {
                "upstream": SWE_AGENT_UPSTREAM,
                "tag": SWE_AGENT_TAG,
                "commit": SWE_AGENT_COMMIT,
                "trajectory_artifact": str(artifact),
            },
            "final_patch_size": len(patch.encode()),
            "successful_verification_commands": [verification],
            "failed_verification_commands": [],
            "elapsed_seconds": time.perf_counter() - started,
        }
        print(json.dumps(payload))
        return 0
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(json.dumps({"ok": False, "error": _redact(str(exc)), "elapsed_seconds": time.perf_counter() - started}))
        return 1


def _verify_applied_patch(workspace: Path) -> dict[str, Any]:
    """Record a real local verification, never inferred from a trajectory string."""
    command = ["git", "diff", "--check"]
    result = subprocess.run(command, cwd=workspace, capture_output=True, text=True, check=False)
    if result.returncode:
        raise ValueError("Applied official SWE-agent patch fails git diff --check.")
    return {"command": command, "exit_code": 0, "stdout": result.stdout[-2000:], "stderr": result.stderr[-2000:]}


def _find_patch(value: Any) -> str | None:
    if isinstance(value, dict):
        for key in _PATCH_KEYS:
            candidate = value.get(key)
            if isinstance(candidate, str) and _looks_like_patch(candidate):
                return candidate
        for child in value.values():
            patch = _find_patch(child)
            if patch:
                return patch
    elif isinstance(value, list):
        for child in value:
            patch = _find_patch(child)
            if patch:
                return patch
    return None


def _looks_like_patch(value: str) -> bool:
    return bool(value.strip() and "diff --git a/" in value and "\n+++ " in value)


def _validate_patch_paths(patch: str) -> None:
    for line in patch.splitlines():
        if not line.startswith("diff --git "):
            continue
        fields = line.split()
        if len(fields) != 4:
            raise ValueError("Official SWE-agent patch has malformed file headers.")
        for raw in fields[2:]:
            relative = raw.removeprefix("a/").removeprefix("b/")
            if relative == ".git" or relative.startswith(".git/") or ".." in Path(relative).parts:
                raise ValueError("Official SWE-agent patch targets a forbidden path.")


def _required_env(environment: dict[str, str] | os._Environ[str], name: str) -> str:
    value = environment.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required for the official SWE-agent adapter.")
    return value


def _positive_int(value: str, name: str) -> int:
    try:
        result = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer.") from exc
    if result <= 0:
        raise ValueError(f"{name} must be positive.")
    return result


def _timeout(mode: str) -> int:
    return {"baseline": 1800, "cgr_single": 2100, "cgr_multi": 3600}[mode]


def _redact(value: str) -> str:
    for name in _SECRET_VALUES:
        secret = os.getenv(name, "")
        if secret:
            value = value.replace(secret, "[REDACTED]")
    return value[-4000:]


if __name__ == "__main__":
    sys.exit(adapter_main())
