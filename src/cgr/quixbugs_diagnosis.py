"""Deterministic attempt-level diagnosis for the QuixBugs pilot."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ERROR_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("missing_path", re.compile(r"fatal: pathspec '([^']+)' did not match any files", re.I)),
    ("git_identity", re.compile(r"author identity unknown|unable to auto-detect email", re.I)),
    ("command_not_found", re.compile(r"(?:command not found|not recognized as .* command)", re.I)),
    ("permission_failure", re.compile(r"permission denied|operation not permitted", re.I)),
    ("failed_test", re.compile(r"(?:\bfailed\b|failures?=\d+|assertionerror)", re.I)),
    ("syntax_failure", re.compile(r"syntaxerror|syntax error|incorrectsyntax", re.I)),
)


def normalize_action(action: str) -> str:
    """Normalize irrelevant whitespace while preserving quoting and command order."""
    lines = action.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(_collapse_unquoted_spaces(line.rstrip()) for line in lines).strip()


def diagnose_attempt(
    trajectory_path: Path | None,
    workspace: Path,
    attempt_result: dict[str, Any],
) -> dict[str, Any]:
    steps = _trajectory_steps(trajectory_path)
    actions: list[tuple[int, str, str]] = []
    observations: list[tuple[int, str]] = []
    for index, step in enumerate(steps, start=1):
        action = step.get("action")
        observation = step.get("observation")
        if isinstance(action, str) and action.strip():
            actions.append((index, action, normalize_action(action)))
        if isinstance(observation, str) and observation.strip():
            observations.append((index, observation))

    grouped_actions: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for index, _raw, normalized in actions:
        grouped_actions[normalized].append((index, _observation_for_step(steps, index)))
    repeated_actions = []
    for normalized, occurrences in grouped_actions.items():
        if len(occurrences) < 2:
            continue
        fingerprints = [_observation_fingerprint(value) for _, value in occurrences if value]
        repeated = Counter(fingerprints).most_common(1)
        repeated_actions.append(
            {
                "normalized_action": normalized,
                "count": len(occurrences),
                "first_step": occurrences[0][0],
                "last_step": occurrences[-1][0],
                "repeated_observation_fingerprint": repeated[0][0]
                if repeated and repeated[0][1] > 1
                else None,
            }
        )

    errors: dict[tuple[str, str], dict[str, Any]] = {}
    missing_paths: dict[str, dict[str, Any]] = {}
    for observation_step, observation in observations:
        for line in observation.splitlines():
            clean = line.strip()
            if not clean:
                continue
            for category, pattern in ERROR_PATTERNS:
                match = pattern.search(clean)
                if not match:
                    continue
                fingerprint = f"{category}:{hashlib.sha256(clean.lower().encode()).hexdigest()[:16]}"
                key = (category, fingerprint)
                item = errors.setdefault(
                    key,
                    {
                        "category": category,
                        "fingerprint": fingerprint,
                        "evidence": clean,
                        "count": 0,
                        "first_step": observation_step,
                        "last_step": observation_step,
                    },
                )
                item["count"] += 1
                item["last_step"] = observation_step
                if category == "missing_path":
                    referenced = match.group(1)
                    missing_paths[referenced] = {
                        "path": referenced,
                        "workspace_exists": (workspace / referenced).exists(),
                        "evidence": clean,
                    }

    normalized_actions = [normalized for _, _, normalized in actions]
    tests_run = [
        normalized
        for normalized in normalized_actions
        if re.search(r"(?:^|[\s;&|])(?:pytest|[^\s]+\s+-m\s+pytest|unittest|tox)(?:[\s;&|]|$)", normalized)
    ]
    tracked_change = bool(attempt_result.get("patch_size") or _workspace_diff(workspace).strip())
    termination = attempt_result.get("termination_reason")
    budget_exhausted = bool(
        attempt_result.get("classification") == "budget_exhausted"
        or (isinstance(termination, str) and re.search(r"cost|call|budget", termination, re.I))
    )
    diagnosis: dict[str, Any] = {
        "failure_types": [],
        "repeated_actions": sorted(repeated_actions, key=lambda item: item["first_step"]),
        "repeated_errors": sorted(errors.values(), key=lambda item: item["first_step"]),
        "missing_paths": list(missing_paths.values()),
        "repository_inspection_observed": any(_is_inspection(action) for action in normalized_actions),
        "tracked_change_observed": tracked_change,
        "git_diff_observed": any(re.search(r"(?:^|\s)git\s+diff(?:\s|$)", action) for action in normalized_actions),
        "tests_run": tests_run,
        "commit_attempted": any(re.search(r"(?:^|\s)git\s+commit(?:\s|$)", action) for action in normalized_actions),
        "patch_submitted": bool(
            attempt_result.get("patch_status") == "patch"
            and attempt_result.get("patch_size")
        ),
        "termination_reason": termination,
        "attempt_classification": attempt_result.get("classification"),
        "trajectory_steps": len(steps),
        "budget_exhausted": budget_exhausted,
    }
    failure_types: list[str] = diagnosis["failure_types"]
    if repeated_actions:
        failure_types.append("repeated_failed_action")
    if any(not item["workspace_exists"] for item in missing_paths.values()):
        failure_types.append("missing_path_reference")
    if not tracked_change:
        failure_types.append("no_repository_change")
    if not tests_run:
        failure_types.append("no_tests_run")
    if budget_exhausted:
        failure_types.append("budget_exhausted")
    if attempt_result.get("classification") == "model_failure":
        failure_types.append("model_failure")
    if attempt_result.get("classification") == "agent_failure":
        failure_types.append("agent_failure")
    if attempt_result.get("classification") == "tests_failed":
        failure_types.append("unresolved_verifier")
    return diagnosis


def build_corrective_message(diagnosis: dict[str, Any], task: dict[str, Any]) -> str:
    source = str(task["source_file"])
    missing = [
        str(item["path"])
        for item in diagnosis.get("missing_paths", [])
        if not item.get("workspace_exists")
    ]
    outcome: list[str] = []
    if not diagnosis.get("tracked_change_observed"):
        outcome.append("- No tracked files changed.")
    if not diagnosis.get("tests_run"):
        outcome.append("- No tests ran.")
    if diagnosis.get("repeated_actions"):
        outcome.append("- The same failed command was repeated.")
    outcome.extend(f"- {path} does not exist." for path in missing)
    if diagnosis.get("commit_attempted"):
        outcome.append("- A Git commit is not required.")
    verifier = " ".join(str(part).replace("{python}", "python") for part in task["verifier_command"])
    avoid = missing[0] if missing else "nonexistent paths"
    return "\n".join(
        [
            "## CGR corrective evidence",
            "",
            "Previous attempt outcome:",
            *outcome,
            "",
            "Required recovery:",
            f"1. Inspect {source}.",
            "2. Edit the existing source file.",
            f"3. Do not create or reference {avoid}.",
            f"4. Use the existing focused test: `{verifier}`.",
            "5. Inspect `git diff`.",
            "6. Submit after a real tracked change exists.",
            "",
        ]
    )


def _collapse_unquoted_spaces(line: str) -> str:
    output: list[str] = []
    quote: str | None = None
    escaped = False
    pending_space = False
    for char in line:
        if escaped:
            if pending_space:
                output.append(" ")
                pending_space = False
            output.append(char)
            escaped = False
            continue
        if char == "\\" and quote != "'":
            if pending_space:
                output.append(" ")
                pending_space = False
            output.append(char)
            escaped = True
            continue
        if char in ("'", '"'):
            if pending_space:
                output.append(" ")
                pending_space = False
            quote = None if quote == char else char if quote is None else quote
            output.append(char)
            continue
        if char.isspace() and quote is None:
            pending_space = bool(output)
            continue
        if pending_space:
            output.append(" ")
            pending_space = False
        output.append(char)
    return "".join(output)


def _trajectory_steps(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.is_file():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    steps = payload.get("trajectory") if isinstance(payload, dict) else None
    return [step for step in steps if isinstance(step, dict)] if isinstance(steps, list) else []


def _observation_for_step(steps: list[dict[str, Any]], index: int) -> str:
    value = steps[index - 1].get("observation")
    return value if isinstance(value, str) else ""


def _observation_fingerprint(observation: str) -> str:
    normalized = "\n".join(line.rstrip() for line in observation.replace("\r\n", "\n").split("\n")).strip()
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def _is_inspection(action: str) -> bool:
    return bool(re.search(r"(?:^|[\s;&|])(?:cat|sed\s+-n|head|tail|less|rg|grep|ls)(?:[\s;&|]|$)", action))


def _workspace_diff(workspace: Path) -> str:
    import subprocess

    process = subprocess.run(
        ["git", "-c", f"safe.directory={workspace}", "diff", "--binary", "HEAD", "--"],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=False,
    )
    return process.stdout if process.returncode == 0 else ""
