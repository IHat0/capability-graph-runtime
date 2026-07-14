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
    required_phase: str | None = None,
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
    traceback_evidence = _traceback_source_evidence(observations, target)
    repeated_tests = _repeated_tests_without_change(
        test_telemetry["attempts"], tracked_change, target_changed
    )
    edit_proposed = bool(
        re.search(
            r"source should be (?:updated|changed|edited)|replace (?:the )?(?:function|implementation)|def\s+gcd\s*\([^)]*\).*while\s+b",
            prose,
            re.I | re.S,
        )
    )
    edit_action_observed = any(_is_edit_action(action) for action in normalized_actions)
    edit_commands = _edit_command_telemetry(actions, steps, target, traceback_evidence)
    if target_changed:
        target_edits = [item for item in edit_commands if item["target_named"]]
        if target_edits:
            target_edits[-1]["edit_effect_observed"] = True
    edit_command_attempted = any(item["command_attempted"] for item in edit_commands)
    edit_command_succeeded = any(item["command_succeeded"] for item in edit_commands)
    edit_effect_observed = target_changed
    no_op_edits = [
        {
            "command": item["command"],
            "target": item["target"],
            "mechanism": item["mechanism"],
            "command_completed": item["command_succeeded"],
            "target_changed": False,
            "reason": "replacement_pattern_not_found_or_no_content_change",
        }
        for item in edit_commands
        if item["command_succeeded"] and item["target_named"] and not target_changed
    ]
    stale_or_reversed = [
        item for item in edit_commands if item.get("possible_stale_or_reversed_edit")
    ]
    verification_after_ineffective = _verification_after_ineffective_edit(
        edit_commands, test_telemetry["attempts"], target_changed
    )
    declared_edits = _declared_edit_evidence(steps, target, required_phase)
    for declared in declared_edits:
        declared["edit_effect_observed"] = target_changed
        declared["target_file_changed"] = target_changed
    first_action = normalized_actions[0] if normalized_actions else ""
    actual_action_kind = _action_kind(first_action)
    declared_edit_not_executed = bool(
        declared_edits and not edit_command_attempted and not tracked_change
    )
    edit_proposed = edit_proposed or bool(declared_edits)
    phase_satisfied = _required_phase_satisfied(
        required_phase,
        actual_action_kind,
        target_changed,
        target_displayed,
        git_diff_observed,
    )
    required_phase_violation = bool(required_phase and not phase_satisfied)
    reasoning_action_mismatch = bool(
        declared_edit_not_executed
        or (
            edit_proposed
            and test_telemetry["test_command_attempted"]
            and not edit_action_observed
            and not tracked_change
        )
    )
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
        "source_evidence": traceback_evidence,
        "target_file_changed": target_changed,
        "edit_command_observed": edit_action_observed,
        "edit_command_attempted": edit_command_attempted,
        "edit_command_succeeded": edit_command_succeeded,
        "edit_effect_observed": edit_effect_observed,
        "edit_commands": edit_commands,
        "no_op_edits": no_op_edits,
        "possible_stale_or_reversed_edit": bool(stale_or_reversed),
        "stale_or_reversed_edit_evidence": stale_or_reversed,
        "verification_after_ineffective_edit": verification_after_ineffective,
        "declared_edit_not_executed": declared_edits if declared_edit_not_executed else [],
        "required_phase": required_phase,
        "required_phase_action_violation": (
            {
                "required_phase": required_phase,
                "actual_action_kind": actual_action_kind,
                "phase_satisfied": phase_satisfied,
                "target_file_changed": target_changed,
                "tracked_change_observed": tracked_change,
            }
            if required_phase_violation
            else None
        ),
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
        "repeated_test_without_change": repeated_tests,
        "known_failure_reverified": bool(repeated_tests),
        "possible_reasoning_action_mismatch": reasoning_action_mismatch,
        "reasoning_action_evidence": {
            "edit_proposed": edit_proposed,
            "proposal_is_immediate": bool(declared_edits),
            "proposed_target": declared_edits[0]["declared_target"] if declared_edits else None,
            "proposed_mechanism": (
                declared_edits[0]["declared_mechanism"] if declared_edits else None
            ),
            "actual_action_kind": actual_action_kind,
            "edit_command_observed": edit_action_observed,
            "edit_command_attempted": edit_command_attempted,
            "edit_effect_observed": edit_effect_observed,
            "response_action_conformant": not declared_edit_not_executed,
            "response_excerpt": _reasoning_excerpt(prose) if edit_proposed else None,
            "action_excerpt": first_action[:500] if first_action else None,
        },
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
        "required_next_phase": (
            "edit"
            if repeated_tests or no_op_edits or declared_edit_not_executed
            else None
        ),
        "phase_exit_condition": (
            {"target": target, "requires_nonempty_diff": True}
            if repeated_tests or no_op_edits or declared_edit_not_executed
            else None
        ),
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
    if repeated_tests:
        failure_types.extend(("repeated_test_without_change", "known_failure_reverified"))
    if no_op_edits:
        failure_types.append("no_op_edit")
    if stale_or_reversed:
        failure_types.append("possible_stale_or_reversed_edit")
    if verification_after_ineffective:
        failure_types.append("verification_after_ineffective_edit")
    if declared_edit_not_executed:
        failure_types.append("declared_edit_not_executed")
    if required_phase_violation:
        failure_types.append("required_phase_action_violation")
    if edit_command_attempted and not edit_command_succeeded:
        failure_types.append("malformed_edit")
    if edit_command_attempted and tracked_change and not target_changed:
        failure_types.append("wrong_file_edit")
    if reasoning_action_mismatch:
        failure_types.append("possible_reasoning_action_mismatch")
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
    repeated_tests = diagnosis.get("repeated_test_without_change") or []
    traceback_evidence = diagnosis.get("source_evidence") or []
    no_op_edits = diagnosis.get("no_op_edits") or []
    declared_edits = diagnosis.get("declared_edit_not_executed") or []
    if declared_edits:
        return "\n".join(
            [
                "## CGR corrective evidence",
                "",
                "Previous attempt outcome:",
                "- You described an edit command, but the executed action was not that edit.",
                "- Commands written only in explanation do not modify the repository.",
                "- No edit command reached execution.",
                "- No tracked file changed.",
                "- The same known failing verifier was repeated without a repository change.",
                "- Do not commit, push, modify remotes, or configure Git identity.",
                "",
                "Immediate required phase: execute edit",
                "",
                "1. Do not provide a multi-step plan as the next response.",
                f"2. Your next actual executed action must be one noninteractive command that modifies {source}.",
                "3. Do not run the focused test yet.",
                "4. After the edit executes, display the relevant target region.",
                f"5. Run `git diff -- {source}`.",
                "6. Continue only after the diff is nonempty.",
                "7. Then run the focused test once, inspect the final diff, and submit the worktree patch without committing.",
                "- Your next executed action must perform the required edit phase.",
                "",
            ]
        )
    if no_op_edits and traceback_evidence:
        source_item = traceback_evidence[0]
        return "\n".join(
            [
                "## CGR corrective evidence",
                "",
                "Previous attempt outcome:",
                f"- You attempted to edit {source}, but the target file did not change.",
                "- The sed command searched for text that was not the current source line.",
                "- You then reran the same failing test without a nonempty diff.",
                "- The verifier again failed with RecursionError.",
                "- Do not commit, push, modify remotes, or configure Git identity.",
                "",
                "Current grounded source:",
                f"- `{source_item['content']}`",
                "",
                "Immediate required phase: edit and confirm",
                "",
                f"1. Modify the existing line in {source} using a noninteractive mechanism.",
                "2. Display the relevant target file region after editing.",
                f"3. Run `git diff -- {source}`.",
                "4. Do not run pytest unless this diff is nonempty and the old grounded line is absent.",
                "5. If the diff is empty, the edit did not take effect; correct the edit command first.",
                "6. After a confirmed edit, run the focused test once.",
                "7. Inspect the final diff and submit the worktree patch without committing.",
                "- Your next executed action must perform the required edit phase.",
                "",
                "Required edit condition:",
                "- The edit must modify the existing grounded line.",
                "- Confirm the old line is no longer present.",
                f"- Confirm `git diff -- {source}` is nonempty before testing.",
                "",
            ]
        )
    if repeated_tests and traceback_evidence:
        repeated = repeated_tests[0]
        source_item = traceback_evidence[0]
        return "\n".join(
            [
                "## CGR corrective evidence",
                "",
                "Previous attempt outcome:",
                "- The focused test ran and failed with a RecursionError.",
                f"- The traceback repeatedly pointed to {source_item['path']} line {source_item['line']}.",
                f"- The observed line was `{source_item['content']}`.",
                f"- You ran the same failing test {repeated['count']} times without changing the repository.",
                "- No tracked file changed.",
                "- Do not commit, push, modify remotes, or configure Git identity.",
                "",
                "Immediate required action:",
                "- Do not run the test again yet.",
                f"- Your first action must modify {source} using one available noninteractive editing mechanism.",
                "- Do not use nano or another interactive editor.",
                f"- After the edit, run `git diff -- {source}`.",
                f"- Only after a nonempty diff exists, run the focused test once: `{verifier}`.",
                "- If the focused test passes, inspect the final diff and submit the worktree patch without committing.",
                "- Your next executed action must perform the required edit phase.",
                "",
                "Source evidence:",
                "- The failing recursion keeps `b` in the second argument position.",
                "- Reinspect the recursive argument order so each call progresses toward the base case.",
                "- Do not rerun an unchanged failing verifier; rerun only after a repository change or to gather different evidence.",
                "",
            ]
        )
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
                "- Do not configure Git identity.",
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
        result_observed = bool(result_pattern.search(observation))
        environment_failure = bool(environment_pattern.search(observation)) and not result_observed
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


def _traceback_source_evidence(
    observations: list[tuple[int, str]], target: str
) -> list[dict[str, Any]]:
    if not target:
        return []
    escaped_target = re.escape(target).replace("/", r"[\\/]")
    path_pattern = re.compile(rf"{escaped_target}:(\d+)")
    grouped: dict[tuple[int, str], dict[str, Any]] = {}
    for step, observation in observations:
        lines = observation.splitlines()
        for index, line in enumerate(lines):
            match = path_pattern.search(line)
            if not match:
                continue
            content = next(
                (
                    candidate.strip()
                    for candidate in lines[index + 1 : index + 4]
                    if candidate.strip().startswith("return ")
                ),
                "",
            )
            if not content:
                continue
            key = (int(match.group(1)), content)
            item = grouped.setdefault(
                key,
                {
                    "path": target,
                    "line": int(match.group(1)),
                    "content": content,
                    "source": "focused_test_traceback",
                    "occurrences": 0,
                    "first_step": step,
                    "last_step": step,
                },
            )
            item["occurrences"] += 1
            item["last_step"] = step
    return list(grouped.values())


def _edit_command_telemetry(
    actions: list[tuple[int, str, str]],
    steps: list[dict[str, Any]],
    target: str,
    source_evidence: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grounded_lines = {
        str(item["content"]).strip()
        for item in source_evidence
        if item.get("content")
    }
    telemetry = []
    for step, _raw, command in actions:
        if not _is_edit_action(command):
            continue
        observation = _observation_for_step(steps, step)
        mechanism = "sed" if re.search(r"(?:^|\s)sed\s+-i", command) else "other"
        target_named = bool(target and target in command)
        failed = bool(
            re.search(
                r"command not found|no such file|permission denied|syntax error|traceback \(most recent call last\)|error:",
                observation,
                re.I,
            )
        )
        item: dict[str, Any] = {
            "step": step,
            "command": command,
            "target": target if target_named else None,
            "mechanism": mechanism,
            "target_named": target_named,
            "command_attempted": True,
            "command_succeeded": not failed,
            "edit_effect_observed": False,
        }
        substitution = _sed_substitution(command) if mechanism == "sed" else None
        if substitution is not None:
            search, replacement = substitution
            item["search_text"] = search
            item["replacement_text"] = replacement
            item["search_matches_grounded_source"] = search.strip() in grounded_lines
            item["replacement_matches_grounded_source"] = replacement.strip() in grounded_lines
            item["possible_stale_or_reversed_edit"] = bool(
                grounded_lines
                and search.strip() not in grounded_lines
                and replacement.strip() in grounded_lines
            )
        telemetry.append(item)
    return telemetry


def _declared_edit_evidence(
    steps: list[dict[str, Any]], target: str, required_phase: str | None
) -> list[dict[str, Any]]:
    if not target:
        return []
    target_pattern = re.escape(target).replace("/", r"[\\/]")
    declaration = re.compile(
        rf"(?:execute|run|apply)(?:\s+this)?(?:\s+edit)?\s+now:\s*"
        rf"(?P<command>(?P<mechanism>sed)\s+-i[^\r\n]*{target_pattern})",
        re.I,
    )
    evidence = []
    for step_number, step in enumerate(steps, start=1):
        response = step.get("response")
        action = step.get("action")
        if not isinstance(response, str):
            continue
        match = declaration.search(response)
        if not match:
            continue
        normalized_action = normalize_action(action) if isinstance(action, str) else ""
        evidence.append(
            {
                "step": step_number,
                "required_phase": required_phase or "edit",
                "declared_edit": True,
                "declared_target": target,
                "declared_mechanism": match.group("mechanism").lower(),
                "declared_command": normalize_action(match.group("command")),
                "actual_action_kind": _action_kind(normalized_action),
                "edit_command_attempted": _is_edit_action(normalized_action),
                "edit_effect_observed": False,
                "target_file_changed": False,
            }
        )
    return evidence


def _action_kind(action: str) -> str:
    if _is_edit_action(action):
        return "edit"
    if re.search(r"(?:^|[\s;&|])(?:pytest|[^\s]+\s+-m\s+pytest)(?:[\s;&|]|$)", action):
        return "test"
    if re.search(r"(?:^|\s)git\s+diff(?:\s|$)", action):
        return "diff"
    if re.search(r"(?:^|\s)git\s+(?:commit|push)(?:\s|$)", action):
        return "git_publication"
    if _is_inspection(action):
        return "inspect"
    return "other" if action else "none"


def _required_phase_satisfied(
    required_phase: str | None,
    actual_action_kind: str,
    target_changed: bool,
    target_displayed: bool,
    git_diff_observed: bool,
) -> bool:
    if required_phase == "edit":
        return actual_action_kind == "edit" and target_changed
    if required_phase == "confirm_edit":
        return (
            actual_action_kind in {"inspect", "diff"}
            and target_displayed
            and git_diff_observed
            and target_changed
        )
    return True


def _sed_substitution(command: str) -> tuple[str, str] | None:
    match = re.search(r"sed\s+-i(?:\s+[^\s]+)?\s+(['\"])(s)(.)(.*?)\3(.*?)\3[^'\"]*\1", command)
    if not match:
        return None
    return match.group(4), match.group(5)


def _verification_after_ineffective_edit(
    edit_commands: list[dict[str, Any]],
    test_attempts: list[dict[str, Any]],
    target_changed: bool,
) -> list[dict[str, Any]]:
    if target_changed:
        return []
    successful_edits = [item for item in edit_commands if item["command_succeeded"]]
    if not successful_edits:
        return []
    first_edit = successful_edits[0]
    subsequent = [
        item
        for item in test_attempts
        if item["step"] > first_edit["step"] and item["test_failed"]
    ]
    if not subsequent:
        return []
    return [
        {
            "edit_command": first_edit["command"],
            "edit_step": first_edit["step"],
            "test_steps": [item["step"] for item in subsequent],
            "failure_fingerprint": _observation_fingerprint(
                str(subsequent[0]["observation_evidence"])
            ),
        }
    ]


def _repeated_tests_without_change(
    attempts: list[dict[str, Any]], tracked_change: bool, target_changed: bool
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for attempt in attempts:
        if attempt["focused_test"] and attempt["test_failed"]:
            grouped[str(attempt["normalized_action"])].append(attempt)
    repeated = []
    for command, occurrences in grouped.items():
        if len(occurrences) < 2 or tracked_change or target_changed:
            continue
        fingerprints = Counter(
            _observation_fingerprint(str(item["observation_evidence"]))
            for item in occurrences
        )
        fingerprint, count = fingerprints.most_common(1)[0]
        if count < 2:
            continue
        repeated.append(
            {
                "command": command,
                "count": len(occurrences),
                "first_step": occurrences[0]["step"],
                "last_step": occurrences[-1]["step"],
                "repeated_failure_fingerprint": fingerprint,
                "tracked_change_between_executions": False,
            }
        )
    return repeated


def _is_edit_action(action: str) -> bool:
    return bool(
        re.search(
            r"(?:sed\s+-i|apply_patch|perl\s+-[pi]|(?:cat|printf)\b[^\n]*(?:>|>>)|python\b[^\n]*(?:write_text|open\())",
            action,
            re.I,
        )
    )


def _reasoning_excerpt(prose: str) -> str:
    normalized = " ".join(prose.split())
    return normalized[:500]


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
