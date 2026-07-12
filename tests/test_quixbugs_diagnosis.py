from __future__ import annotations

import json
import subprocess
from pathlib import Path

from cgr.quixbugs_diagnosis import (
    build_corrective_message,
    diagnose_attempt,
    normalize_action,
)
from cgr.quixbugs_pilot import DEFAULT_MANIFEST, _load_task, _select_attempt


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
                    {"action": "python -m pytest -q tests/test_gcd.py", "observation": "passed"},
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


def test_selector_prefers_verified_then_nonempty_patch() -> None:
    unresolved = {"classification": "budget_exhausted", "patch_size": 0}
    patched = {"classification": "tests_failed", "patch_size": 12, "verifier_exit_code": 1}
    resolved = {"classification": "resolved", "patch_size": 8, "verifier_exit_code": 0}

    assert _select_attempt([unresolved, patched]) == 1
    assert _select_attempt([resolved, patched]) == 0


def _write_trajectory(tmp_path: Path) -> Path:
    path = tmp_path / "failed.traj"
    path.write_text(
        json.dumps({"trajectory": [{"action": FAILED_ACTION, "observation": FAILED_OBSERVATION}] * 2}),
        encoding="utf-8",
    )
    return path
