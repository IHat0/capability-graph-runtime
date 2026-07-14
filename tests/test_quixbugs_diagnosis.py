from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from cgr.quixbugs_diagnosis import (
    build_corrective_message,
    diagnose_attempt,
    normalize_action,
)
from cgr.quixbugs_pilot import (
    DEFAULT_MANIFEST,
    _load_task,
    _select_attempt,
    _trajectory_step_count,
)


FAILED_ACTION = (
    "git add python_programs/gcd.py test_gcd.py 2>&1\n"
    'git commit -m "Fix gcd function to return the greatest common divisor" 2>&1\n'
)
FAILED_OBSERVATION = """fatal: pathspec 'test_gcd.py' did not match any files
Author identity unknown
fatal: unable to auto-detect email address
"""


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=workspace, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=workspace, check=True)
    source = workspace / "python_programs" / "gcd.py"
    source.parent.mkdir()
    source.write_text("def gcd(a, b):\n    return a\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=workspace, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=workspace, check=True)
    return workspace


def test_action_normalization_preserves_quotes_and_command_order() -> None:
    action = "git   add  'a  b.py'  x.py  \r\ngit  commit -m \"a  message\"  \n"

    assert normalize_action(action) == (
        "git add 'a  b.py' x.py\ngit commit -m \"a  message\""
    )


def test_diagnosis_extracts_repetition_errors_paths_and_activity(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    trajectory = tmp_path / "failed.traj"
    trajectory.write_text(
        json.dumps(
            {
                "trajectory": [
                    {"action": FAILED_ACTION, "observation": FAILED_OBSERVATION},
                    {"action": FAILED_ACTION, "observation": FAILED_OBSERVATION},
                ]
            }
        ),
        encoding="utf-8",
    )
    result = {
        "classification": "budget_exhausted",
        "termination_reason": "exit_cost",
        "patch_status": "no_patch",
        "patch_size": 0,
        "submitted_patch_path": str(tmp_path / "empty.patch"),
    }

    diagnosis = diagnose_attempt(trajectory, workspace, result)

    assert diagnosis["failure_types"] == [
        "repeated_failed_action",
        "missing_path_reference",
        "no_repository_change",
        "no_tests_run",
        "unnecessary_git_commit",
        "budget_exhausted",
    ]
    assert diagnosis["repeated_actions"][0]["count"] == 2
    assert {item["category"] for item in diagnosis["repeated_errors"]} == {
        "missing_path",
        "git_identity",
    }
    assert diagnosis["missing_paths"] == [
        {
            "path": "test_gcd.py",
            "workspace_exists": False,
            "evidence": "fatal: pathspec 'test_gcd.py' did not match any files",
        }
    ]
    assert diagnosis["commit_attempted"] is True
    assert diagnosis["patch_submitted"] is False


def test_corrective_message_is_grounded_and_does_not_solve_task(tmp_path: Path) -> None:
    task, _ = _load_task(DEFAULT_MANIFEST, "quixbugs.gcd")
    diagnosis = diagnose_attempt(
        _write_trajectory(tmp_path),
        _workspace(tmp_path),
        {
            "classification": "budget_exhausted",
            "termination_reason": "exit_cost",
            "patch_status": "no_patch",
            "patch_size": 0,
        },
    )

    message = build_corrective_message(diagnosis, task)

    assert "test_gcd.py does not exist" in message
    assert "python_programs/gcd.py" in message
    assert "python -m pytest -q python_testcases/test_gcd.py" in message
    assert "A Git commit is not required" in message
    assert "return gcd(b, a % b)" not in message


def test_error_fingerprints_cover_supported_failure_classes(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    observation = """tool: command not found
file.py: Permission denied
1 failed in 0.02s
SyntaxError: invalid syntax
"""
    trajectory = tmp_path / "errors.traj"
    trajectory.write_text(
        json.dumps(
            {
                "trajectory": [
                    {"action": "broken", "observation": observation},
                    {"action": "broken", "observation": observation},
                ]
            }
        ),
        encoding="utf-8",
    )

    diagnosis = diagnose_attempt(trajectory, workspace, {"patch_size": 0})

    assert {item["category"] for item in diagnosis["repeated_errors"]} == {
        "command_not_found",
        "permission_failure",
        "failed_test",
        "syntax_failure",
    }
    assert all(item["count"] == 2 for item in diagnosis["repeated_errors"])


def test_repository_activity_uses_actions_and_real_git_diff(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    (workspace / "python_programs" / "gcd.py").write_text(
        "def gcd(a, b):\n    return b\n", encoding="utf-8"
    )
    trajectory = tmp_path / "active.traj"
    trajectory.write_text(
        json.dumps(
            {
                "trajectory": [
                    {"action": "cat python_programs/gcd.py", "observation": "source"},
                    {"action": "sed -i 's/a/b/' python_programs/gcd.py", "observation": ""},
                    {"action": "python -m pytest -q tests/test_gcd.py", "observation": "1 passed in 0.01s"},
                    {"action": "git diff", "observation": "diff"},
                ]
            }
        ),
        encoding="utf-8",
    )

    diagnosis = diagnose_attempt(
        trajectory,
        workspace,
        {"patch_status": "patch", "patch_size": 10, "classification": "resolved"},
    )

    assert diagnosis["repository_inspection_observed"] is True
    assert diagnosis["tracked_change_observed"] is True
    assert diagnosis["git_diff_observed"] is True
    assert diagnosis["tests_run"] == ["python -m pytest -q tests/test_gcd.py"]
    assert diagnosis["patch_submitted"] is True


def test_second_failure_diagnosis_is_grounded_in_inspection_and_pushes(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    subprocess.run(
        ["git", "remote", "add", "origin", "./.git/cgr-origin.bundle"],
        cwd=workspace,
        check=True,
    )
    task, _ = _load_task(DEFAULT_MANIFEST, "quixbugs.gcd")
    source_observation = """def gcd(a, b):
    if b == 0:
        return a
    else:
        return gcd(a % b, b)
"""
    push_error = "error: failed to push some refs to './.git/cgr-origin.bundle'"
    steps = [
        {
            "action": "sed -n '1,100p' python_programs/gcd.py",
            "observation": source_observation,
            "response": "Inspect the source.",
        },
        {
            "action": 'git commit -m "Fix gcd function"\ngit push origin main',
            "observation": push_error,
            "response": "The provided gcd function appears to be implementing the algorithm correctly.",
        },
        {"action": "git push origin main", "observation": push_error},
        {"action": "git push origin main", "observation": push_error},
    ]
    trajectory = tmp_path / "second-failure.traj"
    trajectory.write_text(json.dumps({"trajectory": steps}), encoding="utf-8")
    result = {
        "classification": "budget_exhausted",
        "termination_reason": "exit_cost",
        "patch_status": "no_patch",
        "patch_size": 0,
        "pre_agent_verifier_exit_code": 1,
    }

    diagnosis = diagnose_attempt(trajectory, workspace, result, task)
    correction = build_corrective_message(diagnosis, task)

    assert diagnosis["required_actions"] == {
        "inspect_target": True,
        "edit_target": False,
        "run_focused_test": False,
        "inspect_diff": False,
        "submit_patch": False,
    }
    assert diagnosis["possible_incorrect_source_assessment"] is True
    assert diagnosis["configured_origin"]["type"] == "portable_bundle"
    assert diagnosis["push_actions"][1]["count"] == 2
    assert diagnosis["push_actions"][1]["repeated_error"]["count"] == 2
    assert {item["category"] for item in diagnosis["repeated_errors"]} == {
        "git_push_failure"
    }
    assert "`gcd(a % b, b)`" in correction
    assert "Do not commit, push, or modify Git remotes" in correction
    assert "Do not configure Git identity" in correction
    assert "return gcd(b, a % b)" not in correction


def test_missing_pytest_is_environment_failure_not_test_execution(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    task, _ = _load_task(DEFAULT_MANIFEST, "quixbugs.gcd")
    trajectory = tmp_path / "missing-pytest.traj"
    trajectory.write_text(
        json.dumps(
            {
                "trajectory": [
                    {
                        "action": "python -m pytest -q python_testcases/test_gcd.py",
                        "observation": "/usr/local/bin/python: No module named pytest",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    diagnosis = diagnose_attempt(trajectory, workspace, {"patch_size": 0}, task)

    assert diagnosis["test_command_observed"] is True
    assert diagnosis["test_command_attempted"] is True
    assert diagnosis["test_process_started"] is False
    assert diagnosis["test_result_observed"] is False
    assert diagnosis["test_environment_failure"] is True
    assert diagnosis["tests_executed"] is False
    assert diagnosis["required_actions"]["run_focused_test"] is False


def test_pytest_cache_warning_does_not_hide_completed_failure(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    task, _ = _load_task(DEFAULT_MANIFEST, "quixbugs.gcd")
    trajectory = tmp_path / "pytest-cache-warning.traj"
    trajectory.write_text(
        json.dumps(
            {
                "trajectory": [
                    {
                        "action": "python -m pytest -q python_testcases/test_gcd.py",
                        "observation": (
                            "FAILED python_testcases/test_gcd.py::test_gcd - RecursionError\n"
                            "PytestCacheWarning: cache could not write path: Permission denied\n"
                            "5 failed, 1 passed, 1 warning in 0.14s"
                        ),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    diagnosis = diagnose_attempt(trajectory, workspace, {"patch_size": 0}, task)

    assert diagnosis["test_process_started"] is True
    assert diagnosis["test_result_observed"] is True
    assert diagnosis["test_environment_failure"] is False
    assert diagnosis["test_failed"] is True
    assert diagnosis["tests_executed"] is True


def test_unavailable_nano_is_recorded_as_failed_edit_attempt(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    trajectory = tmp_path / "nano.traj"
    trajectory.write_text(
        json.dumps(
            {
                "trajectory": [
                    {
                        "action": "nano python_programs/gcd.py",
                        "observation": "bash: nano: command not found",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    diagnosis = diagnose_attempt(trajectory, workspace, {"patch_size": 0})

    assert diagnosis["unavailable_tools"][0]["tool"] == "nano"
    assert diagnosis["editing_attempt_failed"] is True


def test_displayed_target_routes_to_specialized_message_without_prose_or_push(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    task, _ = _load_task(DEFAULT_MANIFEST, "quixbugs.gcd")
    trajectory = tmp_path / "displayed.traj"
    trajectory.write_text(
        json.dumps(
            {
                "trajectory": [
                    {
                        "action": "cat python_programs/gcd.py",
                        "observation": "def gcd(a, b):\n    return gcd(a % b, b)",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    result = {
        "classification": "budget_exhausted",
        "pre_agent_verifier_exit_code": 1,
        "patch_size": 0,
    }

    correction = build_corrective_message(
        diagnose_attempt(trajectory, workspace, result, task), task
    )

    assert "You inspected python_programs/gcd.py" in correction
    assert "`gcd(a % b, b)`" in correction
    assert "Do not use nano or other interactive editors" in correction
    assert "PYTHONPATH=.git/cgr-test-runtime" in correction


def test_traceback_repetition_routes_to_edit_first_correction(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    task, _ = _load_task(DEFAULT_MANIFEST, "quixbugs.gcd")
    command = "python -m pytest -q python_testcases/test_gcd.py"
    observation = """python_programs/gcd.py:5: in gcd
    return gcd(a % b, b)
E   RecursionError: maximum recursion depth exceeded
1 failed in 0.03s
"""
    response = (
        "The source should be updated to an iterative implementation: "
        "def gcd(a, b): while b != 0: a, b = b, a % b; return a."
    )
    trajectory = tmp_path / "repeated-tests.traj"
    trajectory.write_text(
        json.dumps(
            {
                "trajectory": [
                    {"action": command, "observation": observation, "response": response}
                ]
                * 8
            }
        ),
        encoding="utf-8",
    )
    result = {
        "classification": "budget_exhausted",
        "pre_agent_verifier_exit_code": 1,
        "patch_size": 0,
    }

    diagnosis = diagnose_attempt(trajectory, workspace, result, task)
    correction = build_corrective_message(diagnosis, task)

    assert diagnosis["source_evidence"] == [
        {
            "path": "python_programs/gcd.py",
            "line": 5,
            "content": "return gcd(a % b, b)",
            "source": "focused_test_traceback",
            "occurrences": 8,
            "first_step": 1,
            "last_step": 8,
        }
    ]
    assert diagnosis["repeated_test_without_change"][0]["count"] == 8
    assert diagnosis["known_failure_reverified"] is True
    assert diagnosis["possible_reasoning_action_mismatch"] is True
    assert diagnosis["required_next_phase"] == "edit"
    assert "Do not run the test again yet" in correction
    assert "first action must modify python_programs/gcd.py" in correction
    assert "`return gcd(a % b, b)`" in correction
    assert "Do not commit, push, modify remotes" in correction
    assert "def gcd(a, b):" not in correction


def test_successful_noop_edit_is_distinct_from_edit_effect(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    task, _ = _load_task(DEFAULT_MANIFEST, "quixbugs.gcd")
    test_command = "python -m pytest -q python_testcases/test_gcd.py"
    failure = """python_programs/gcd.py:5: in gcd
    return gcd(a % b, b)
E   RecursionError: maximum recursion depth exceeded
5 failed, 1 passed in 0.04s
"""
    edit = (
        "sed -i 's/return gcd(b, a % b)/return gcd(a % b, b)/' "
        "python_programs/gcd.py"
    )
    trajectory = tmp_path / "noop-edit.traj"
    trajectory.write_text(
        json.dumps(
            {
                "trajectory": [
                    {"action": edit, "observation": ""},
                    {"action": test_command, "observation": failure},
                    {"action": test_command, "observation": failure},
                ]
            }
        ),
        encoding="utf-8",
    )

    diagnosis = diagnose_attempt(
        trajectory,
        workspace,
        {"classification": "budget_exhausted", "patch_size": 0},
        task,
    )
    correction = build_corrective_message(diagnosis, task)

    assert diagnosis["edit_command_observed"] is True
    assert diagnosis["edit_command_attempted"] is True
    assert diagnosis["edit_command_succeeded"] is True
    assert diagnosis["edit_effect_observed"] is False
    assert diagnosis["target_file_changed"] is False
    assert diagnosis["declared_edit_not_executed"] == []
    assert diagnosis["no_op_edits"][0]["mechanism"] == "sed"
    assert diagnosis["possible_stale_or_reversed_edit"] is True
    assert diagnosis["verification_after_ineffective_edit"][0]["test_steps"] == [2, 3]
    assert diagnosis["required_next_phase"] == "edit"
    assert diagnosis["phase_exit_condition"] == {
        "target": "python_programs/gcd.py",
        "requires_nonempty_diff": True,
    }
    assert "no_op_edit" in diagnosis["failure_types"]
    assert "possible_stale_or_reversed_edit" in diagnosis["failure_types"]
    assert "verification_after_ineffective_edit" in diagnosis["failure_types"]
    assert "attempted to edit python_programs/gcd.py, but the target file did not change" in correction
    assert "Current grounded source:" in correction
    assert "`return gcd(a % b, b)`" in correction
    assert "Do not run pytest unless this diff is nonempty" in correction
    assert "Do not commit, push, modify remotes" in correction
    assert "def gcd(a, b):" not in correction


@pytest.mark.parametrize(
    "action",
    [
        "sed -i 's/old/new/' python_programs/gcd.py",
        (
            'python -c "from pathlib import Path; '
            "Path('python_programs/gcd.py').write_text('changed')\""
        ),
        "cat > python_programs/gcd.py <<'EOF'\nchanged\nEOF",
    ],
)
def test_successful_target_edit_records_effect(tmp_path: Path, action: str) -> None:
    workspace = _workspace(tmp_path)
    task, _ = _load_task(DEFAULT_MANIFEST, "quixbugs.gcd")
    source = workspace / "python_programs" / "gcd.py"
    source.write_text("def gcd(a, b):\n    return gcd(b, a % b)\n", encoding="utf-8")
    trajectory = tmp_path / "effective-edit.traj"
    trajectory.write_text(
        json.dumps(
            {
                "trajectory": [
                    {
                        "action": action,
                        "observation": "",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    diagnosis = diagnose_attempt(
        trajectory, workspace, {"patch_size": 1}, task, required_phase="edit"
    )

    assert diagnosis["edit_command_succeeded"] is True
    assert diagnosis["edit_effect_observed"] is True
    assert diagnosis["target_file_changed"] is True
    assert diagnosis["edit_commands"][0]["edit_effect_observed"] is True
    assert diagnosis["no_op_edits"] == []
    assert diagnosis["required_phase_action_violation"] is None


def test_immediate_declared_edit_but_test_executed_is_phase_violation(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    task, _ = _load_task(DEFAULT_MANIFEST, "quixbugs.gcd")
    test_command = "python -m pytest -q python_testcases/test_gcd.py"
    response = (
        "Execute this edit now: sed -i 's/return gcd(a % b, b)/"
        "return gcd(b, a % b)/' python_programs/gcd.py"
    )
    failure = """python_programs/gcd.py:5: in gcd
    return gcd(a % b, b)
E   RecursionError: maximum recursion depth exceeded
5 failed, 1 passed in 0.04s
"""
    trajectory = tmp_path / "declared-not-executed.traj"
    trajectory.write_text(
        json.dumps(
            {
                "trajectory": [
                    {"action": test_command, "observation": failure, "response": response},
                    {"action": test_command, "observation": failure, "response": response},
                ]
            }
        ),
        encoding="utf-8",
    )

    diagnosis = diagnose_attempt(
        trajectory,
        workspace,
        {"classification": "budget_exhausted", "patch_size": 0},
        task,
        required_phase="edit",
    )
    correction = build_corrective_message(diagnosis, task)

    declared = diagnosis["declared_edit_not_executed"][0]
    assert declared["declared_target"] == "python_programs/gcd.py"
    assert declared["declared_mechanism"] == "sed"
    assert declared["actual_action_kind"] == "test"
    assert diagnosis["edit_command_attempted"] is False
    assert diagnosis["edit_effect_observed"] is False
    assert diagnosis["target_file_changed"] is False
    assert diagnosis["no_op_edits"] == []
    assert diagnosis["required_phase_action_violation"] == {
        "required_phase": "edit",
        "actual_action_kind": "test",
        "phase_satisfied": False,
        "target_file_changed": False,
        "tracked_change_observed": False,
    }
    evidence = diagnosis["reasoning_action_evidence"]
    assert evidence["edit_proposed"] is True
    assert evidence["proposal_is_immediate"] is True
    assert evidence["response_action_conformant"] is False
    assert "declared_edit_not_executed" in diagnosis["failure_types"]
    assert "required_phase_action_violation" in diagnosis["failure_types"]
    assert "Commands written only in explanation do not modify" in correction
    assert "next actual executed action must be one noninteractive command" in correction
    assert "return gcd(b, a % b)" not in correction


def test_hypothetical_edit_discussion_is_not_declared_execution(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    task, _ = _load_task(DEFAULT_MANIFEST, "quixbugs.gcd")
    trajectory = tmp_path / "hypothetical.traj"
    trajectory.write_text(
        json.dumps(
            {
                "trajectory": [
                    {
                        "action": "python -m pytest -q python_testcases/test_gcd.py",
                        "observation": "1 failed in 0.01s",
                        "response": (
                            "We could use sed to edit python_programs/gcd.py after "
                            "inspecting the implementation."
                        ),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    diagnosis = diagnose_attempt(trajectory, workspace, {"patch_size": 0}, task)

    assert diagnosis["declared_edit_not_executed"] == []
    assert "declared_edit_not_executed" not in diagnosis["failure_types"]


def test_matching_declared_and_executed_edit_is_conformant(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    task, _ = _load_task(DEFAULT_MANIFEST, "quixbugs.gcd")
    command = (
        "sed -i 's/return gcd(a % b, b)/return gcd(b, a % b)/' "
        "python_programs/gcd.py"
    )
    (workspace / "python_programs" / "gcd.py").write_text(
        "def gcd(a, b):\n    return gcd(b, a % b)\n", encoding="utf-8"
    )
    trajectory = tmp_path / "matching-edit.traj"
    trajectory.write_text(
        json.dumps(
            {
                "trajectory": [
                    {
                        "action": command,
                        "observation": "",
                        "response": f"Execute this edit now: {command}",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    diagnosis = diagnose_attempt(
        trajectory, workspace, {"patch_size": 1}, task, required_phase="edit"
    )

    assert diagnosis["declared_edit_not_executed"] == []
    assert diagnosis["reasoning_action_evidence"]["response_action_conformant"] is True
    assert diagnosis["edit_command_attempted"] is True
    assert diagnosis["edit_effect_observed"] is True
    assert diagnosis["required_phase_action_violation"] is None


def test_malformed_executed_edit_is_not_noop(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    task, _ = _load_task(DEFAULT_MANIFEST, "quixbugs.gcd")
    trajectory = tmp_path / "malformed-edit.traj"
    trajectory.write_text(
        json.dumps(
            {
                "trajectory": [
                    {
                        "action": "sed -i 's/old/new/' python_programs/gcd.py",
                        "observation": "sed: cannot read file: No such file or directory",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    diagnosis = diagnose_attempt(
        trajectory, workspace, {"patch_size": 0}, task, required_phase="edit"
    )

    assert diagnosis["edit_command_attempted"] is True
    assert diagnosis["edit_command_succeeded"] is False
    assert diagnosis["no_op_edits"] == []
    assert "malformed_edit" in diagnosis["failure_types"]


def test_test_action_does_not_satisfy_confirm_edit_phase(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    task, _ = _load_task(DEFAULT_MANIFEST, "quixbugs.gcd")
    trajectory = tmp_path / "confirm-edit-test.traj"
    trajectory.write_text(
        json.dumps(
            {
                "trajectory": [
                    {
                        "action": "python -m pytest -q python_testcases/test_gcd.py",
                        "observation": "1 failed in 0.01s",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    diagnosis = diagnose_attempt(
        trajectory, workspace, {"patch_size": 0}, task, required_phase="confirm_edit"
    )

    assert diagnosis["required_phase_action_violation"]["actual_action_kind"] == "test"
    assert diagnosis["required_phase_action_violation"]["phase_satisfied"] is False


def test_selector_does_not_credit_test_environment_failure() -> None:
    failures = [
        {"classification": "budget_exhausted", "patch_size": 0},
        {"classification": "tests_failed", "patch_size": 0},
    ]
    diagnoses = [
        {
            "test_command_attempted": True,
            "test_environment_failure": True,
            "test_passed": False,
            "test_failed": False,
            "inspected_source_paths": ["python_programs/gcd.py"],
        },
        {
            "test_command_attempted": True,
            "test_environment_failure": False,
            "test_passed": False,
            "test_failed": True,
            "inspected_source_paths": [],
        },
    ]

    assert _select_attempt(failures, diagnoses) == 1


def test_selector_prefers_verified_then_nonempty_patch() -> None:
    unresolved = {"classification": "budget_exhausted", "patch_size": 0}
    patched = {"classification": "tests_failed", "patch_size": 12, "verifier_exit_code": 1}
    resolved = {"classification": "resolved", "patch_size": 8, "verifier_exit_code": 0}

    assert _select_attempt([unresolved, patched]) == 1
    assert _select_attempt([resolved, patched]) == 0


def test_selector_uses_inspection_then_later_attempt_for_equal_failures() -> None:
    failures = [
        {"classification": "budget_exhausted", "patch_size": 0},
        {"classification": "budget_exhausted", "patch_size": 0},
        {"classification": "budget_exhausted", "patch_size": 0},
    ]
    diagnoses = [
        {"inspected_source_paths": []},
        {"inspected_source_paths": ["python_programs/gcd.py"]},
        {"inspected_source_paths": ["python_programs/gcd.py"]},
    ]

    assert _select_attempt(failures, diagnoses) == 2


def test_model_request_count_can_be_derived_from_trajectory(tmp_path: Path) -> None:
    trajectory = tmp_path / "count.traj"
    trajectory.write_text(
        json.dumps({"trajectory": [{"action": "one"}, {"action": "two"}]}),
        encoding="utf-8",
    )

    assert _trajectory_step_count(trajectory) == 2


def _write_trajectory(tmp_path: Path) -> Path:
    path = tmp_path / "failed.traj"
    path.write_text(
        json.dumps({"trajectory": [{"action": FAILED_ACTION, "observation": FAILED_OBSERVATION}] * 2}),
        encoding="utf-8",
    )
    return path
