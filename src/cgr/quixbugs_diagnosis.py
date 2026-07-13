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
    ("failed_test", re.compile(r"(?:\d+\s+failed|failures?=\d+|assertionerror|^FAILED\s)", re.I)),
    ("syntax_failure", re.compile(r"syntaxerror|syntax error|incorrectsyntax", re.I)),
    ("git_push_failure", re.compile(r"failed to push some refs|cannot update ref", re.I)),
)


def normalize_action(action: str) -> str:
    """Normalize irrelevant whitespace while preserving quoting and command order."""
    lines = action.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(_collapse_unquoted_spaces(line.rstrip()) for line in lines).strip()


def diagnose_attempt(
    trajectory_path: Path | None,
    workspace: Path,
    attempt_result: dict[str, Any],
    task: dict[str, Any] | None = None,
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
    target = str(task.get("source_file", "")) if task else ""
    focused_test = str(task.get("test_file", "")) if task else ""
    inspected_source_paths = sorted(
        {
            target
            for _index, _raw, action in actions
            if target and target in action and _is_inspection(action)
        }
    )
    target_displayed = bool(
        inspected_source_paths
        and any("def gcd" in observation for _step, observation in observations)
    )
    prose = "\n".join(
        str(step.get(field, ""))
        for step in steps
        for field in ("response", "thought")
    )
    claimed_correct = bool(
        re.search(
            r"(?:implementation|function|code)\s+(?:appears|seems|is)\s+(?:to be\s+)?(?:correct|.+?correctly)|no bug (?:exists|is present)",
            prose,
            re.I,
        )
    )
    test_telemetry = _test_telemetry(actions, steps, focused_test)
    tests_run = [
        item["normalized_action"]
        for item in test_telemetry["attempts"]
        if item["test_result_observed"]
    ]
    workspace_diff = _workspace_diff(workspace)
    tracked_change = bool(attempt_result.get("patch_size") or workspace_diff.strip())
    target_changed = bool(target and _diff_changes_path(workspace_diff, target))
    focused_test_run = any(
        item["focused_test"] and item["test_result_observed"]
        for item in test_telemetry["attempts"]
    )
    git_diff_observed = any(
        re.search(r"(?:^|\s)git\s+diff(?:\s|$)", action) for action in normalized_actions
    )
    commit_attempted = any(
        re.search(r"(?:^|\s)git\s+commit(?:\s|$)", action) for action in normalized_actions
    )
    push_occurrences = [
        (index, normalized)
        for index, _raw, normalized in actions
        if re.search(r"(?:^|\s)git\s+push(?:\s|$)", normalized)
    ]
    push_groups: dict[str, list[int]] = defaultdict(list)
    for index, normalized in push_occurrences:
        push_groups[normalized].append(index)
    push_actions = [
        {
            "normalized_action": action,
            "count": len(indices),
            "first_step": indices[0],
            "last_step": indices[-1],
            "repeated_error": _repeated_step_observation(steps, indices),
        }
        for action, indices in push_groups.items()
    ]
    origin_url = _origin_url(workspace)
    origin_type = "portable_bundle" if origin_url.endswith(".bundle") else "other"
    unavailable_tools = _unavailable_tools(actions, steps)
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
        "inspected_source_paths": inspected_source_paths,
        "target_source_displayed": target_displayed,
        "target_file_changed": target_changed,
        "source_assessment_claimed_correct": claimed_correct,
        "tracked_change_observed": tracked_change,
        "git_diff_observed": git_diff_observed,
        "tests_run": tests_run,
        "test_command_observed": test_telemetry["test_command_observed"],
        "test_command_attempted": test_telemetry["test_command_attempted"],
        "test_process_started": test_telemetry["test_process_started"],
        "test_result_observed": test_telemetry["test_result_observed"],
        "test_environment_failure": test_telemetry["test_environment_failure"],
        "test_passed": test_telemetry["test_passed"],
        "test_failed": test_telemetry["test_failed"],
        "tests_executed": test_telemetry["tests_executed"],
        "test_attempts": test_telemetry["attempts"],
        "unavailable_tools": unavailable_tools,
        "editing_attempt_failed": bool(unavailable_tools and not target_changed),
        "commit_attempted": commit_attempted,
        "push_attempted": bool(push_occurrences),
        "push_actions": push_actions,
        "configured_origin": {"url": origin_url, "type": origin_type},
        "patch_submitted": bool(
            attempt_result.get("patch_status") == "patch"
            and attempt_result.get("patch_size")
        ),
        "termination_reason": termination,
        "attempt_classification": attempt_result.get("classification"),
        "trajectory_steps": len(steps),
        "budget_exhausted": budget_exhausted,
        "required_actions": {
            "inspect_target": bool(inspected_source_paths),
            "edit_target": target_changed,
            "run_focused_test": focused_test_run,
            "inspect_diff": git_diff_observed,
            "submit_patch": bool(
                attempt_result.get("patch_status") == "patch"
                and attempt_result.get("patch_size")
            ),
        },
    }
    possible_incorrect_assessment = bool(
        target_displayed
        and claimed_correct
        and attempt_result.get("pre_agent_verifier_exit_code") not in (None, 0)
        and not target_changed
        and not focused_test_run
    )
    diagnosis["possible_incorrect_source_assessment"] = possible_incorrect_assessment
    diagnosis["observed_source_evidence"] = [
        line.strip()
        for _step, observation in observations
        for line in observation.splitlines()
        if line.strip() == "return gcd(a % b, b)"
    ][:1]
    failure_types: list[str] = diagnosis["failure_types"]
    if repeated_actions:
        failure_types.append("repeated_failed_action")
    if any(not item["workspace_exists"] for item in missing_paths.values()):
        failure_types.append("missing_path_reference")
    if not tracked_change:
        failure_types.append("no_repository_change")
    if not tests_run:
        failure_types.append("no_tests_run")
    if inspected_source_paths and not target_changed:
        failure_types.append("source_inspected_no_edit")
    if commit_attempted:
        failure_types.append("unnecessary_git_commit")
    if push_occurrences:
        failure_types.append("unnecessary_git_push")
    if possible_incorrect_assessment:
        failure_types.append("possible_incorrect_source_assessment")
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
    verifier = str(
        task.get("agent_verifier_command")
        or " ".join(str(part).replace("{python}", "python") for part in task["verifier_command"])
    ).replace("{agent_python}", "python")
    specialized = bool(
        diagnosis.get("target_source_displayed")
        and not diagnosis.get("target_file_changed")
        and diagnosis.get("attempt_classification") != "resolved"
    )
    if specialized or diagnosis.get("possible_incorrect_source_assessment") or diagnosis.get("push_attempted"):
        source_evidence = diagnosis.get("observed_source_evidence") or []
        evidence = (
            [
                "",
                "Source evidence:",
                f"- The recursive call currently uses `{source_evidence[0].removeprefix('return ')}`.",
                "- This keeps `b` in the second argument position and may repeat without reaching the base case.",
            ]
            if source_evidence
            else []
        )
        return "\n".join(
            [
                "## CGR corrective evidence",
                "",
                "Previous attempt outcome:",
                f"- You inspected {source}.",
                "- The focused verifier is known to fail.",
                "- No tracked file changed.",
                "- You did not successfully run the focused test.",
                "- You attempted an unnecessary Git commit.",
                "- You repeated an unnecessary Git push.",
                "- Do not commit, push, or modify Git remotes.",
                *evidence,
                "",
                "Required recovery:",
                "1. Reinspect the recursive argument order.",
                "2. Edit the existing source file using a noninteractive shell command or available file-editing mechanism.",
                "3. Do not use nano or other interactive editors.",
                f"4. Run the verified in-environment test command: `{verifier}`.",
                "5. Inspect `git diff`.",
                "6. Submit the worktree patch without committing or pushing.",
                "",
            ]
        )
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


def _repeated_step_observation(
    steps: list[dict[str, Any]], indices: list[int]
) -> dict[str, Any] | None:
    observations = [_observation_for_step(steps, index).strip() for index in indices]
    nonempty = [value for value in observations if value]
    if not nonempty:
        return None
    fingerprints = Counter(_observation_fingerprint(value) for value in nonempty)
    fingerprint, count = fingerprints.most_common(1)[0]
    evidence = next(
        value for value in nonempty if _observation_fingerprint(value) == fingerprint
    )
    return {
        "fingerprint": fingerprint,
        "count": count,
        "evidence": evidence,
    }


def _is_inspection(action: str) -> bool:
    return bool(re.search(r"(?:^|[\s;&|])(?:cat|sed\s+-n|head|tail|less|rg|grep|ls)(?:[\s;&|]|$)", action))


def _test_telemetry(
    actions: list[tuple[int, str, str]],
    steps: list[dict[str, Any]],
    focused_test: str,
) -> dict[str, Any]:
    attempts: list[dict[str, Any]] = []
    command_pattern = re.compile(
        r"(?:^|[\s;&|])(?:pytest|[^\s]+\s+-m\s+pytest|unittest|tox)(?:[\s;&|]|$)"
    )
    environment_pattern = re.compile(
        r"no module named pytest|command not found|executable .* not found|permission denied",
        re.I,
    )
    result_pattern = re.compile(
        r"(?:\d+\s+passed|\d+\s+failed|=+\s*test session starts|collected\s+\d+)",
        re.I,
    )
    for index, _raw, normalized in actions:
        if not command_pattern.search(normalized):
            continue
        observation = _observation_for_step(steps, index)
        environment_failure = bool(environment_pattern.search(observation))
        result_observed = bool(result_pattern.search(observation)) and not environment_failure
        passed = bool(re.search(r"\d+\s+passed", observation, re.I)) and not bool(
            re.search(r"\d+\s+failed", observation, re.I)
        )
        failed = bool(re.search(r"\d+\s+failed|^FAILED\s", observation, re.I | re.M))
        attempts.append(
            {
                "step": index,
                "normalized_action": normalized,
                "observation_evidence": observation.strip(),
                "focused_test": bool(focused_test and focused_test in normalized),
                "test_command_observed": True,
                "test_command_attempted": True,
                "test_process_started": result_observed,
                "test_result_observed": result_observed,
                "test_environment_failure": environment_failure,
                "test_passed": passed,
                "test_failed": failed and result_observed,
            }
        )
    return {
        "test_command_observed": bool(attempts),
        "test_command_attempted": bool(attempts),
        "test_process_started": any(item["test_process_started"] for item in attempts),
        "test_result_observed": any(item["test_result_observed"] for item in attempts),
        "test_environment_failure": any(
            item["test_environment_failure"] for item in attempts
        ),
        "test_passed": any(item["test_passed"] for item in attempts),
        "test_failed": any(item["test_failed"] for item in attempts),
        "tests_executed": any(item["test_result_observed"] for item in attempts),
        "attempts": attempts,
    }


def _unavailable_tools(
    actions: list[tuple[int, str, str]], steps: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    unavailable = []
    for index, _raw, normalized in actions:
        if not re.search(r"(?:^|[\s;&|])nano(?:[\s;&|]|$)", normalized):
            continue
        observation = _observation_for_step(steps, index)
        if re.search(r"nano:\s*(?:command not found|not found)", observation, re.I):
            unavailable.append(
                {
                    "tool": "nano",
                    "step": index,
                    "normalized_action": normalized,
                    "evidence": observation.strip(),
                }
            )
    return unavailable


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


def _diff_changes_path(diff: str, path: str) -> bool:
    return f"diff --git a/{path} b/{path}" in diff


def _origin_url(workspace: Path) -> str:
    import subprocess

    process = subprocess.run(
        ["git", "-c", f"safe.directory={workspace}", "remote", "get-url", "origin"],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=False,
    )
    return process.stdout.strip() if process.returncode == 0 else ""
