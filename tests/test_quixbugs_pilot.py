from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from cgr import quixbugs_pilot as pilot


def _git(root: Path, *args: str) -> str:
    process = subprocess.run(
        ["git", "-c", f"safe.directory={root}", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    return process.stdout.strip()


def test_one_task_manifest_is_pinned_and_uses_focused_pytest() -> None:
    task, manifest = pilot._load_task(pilot.DEFAULT_MANIFEST, "quixbugs.gcd")

    assert manifest["source_repository"] == "https://github.com/jkoppel/QuixBugs"
    assert task["pinned_commit"] == "4257f44b0ff1181dedaedee6a447e133219fcebf"
    assert task["source_file"] == "python_programs/gcd.py"
    assert task["test_file"] == "python_testcases/test_gcd.py"
    assert task["verifier_command"][-1] == task["test_file"]
    assert task["agent_verifier_command"] == (
        "PYTHONPATH=.git/cgr-test-runtime {agent_python} -m pytest -q "
        "python_testcases/test_gcd.py"
    )
    assert manifest["dependencies"] == ["pytest==8.3.5"]
    assert len(manifest["tasks"]) == 1


def test_manifest_rejects_paths_outside_repository(tmp_path: Path) -> None:
    manifest = {
        "tasks": [
            {
                "task_id": "unsafe",
                "pinned_commit": "a" * 40,
                "source_file": "../outside.py",
                "test_file": "tests/test_x.py",
                "verifier_command": ["{python}", "-m", "pytest"],
                "agent_verifier_command": "python -m pytest tests/test_x.py",
                "problem_statement": "fix it",
                "timeout_seconds": 10,
            }
        ]
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="unsafe path"):
        pilot._load_task(path, "unsafe")


def test_attempt_clone_is_disposable_and_pinned(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init", "-q")
    _git(source, "config", "user.email", "test@example.com")
    _git(source, "config", "user.name", "Test")
    tracked = source / "program.py"
    tracked.write_text("buggy = True\n", encoding="utf-8")
    _git(source, "add", "program.py")
    _git(source, "commit", "-qm", "initial")
    commit = _git(source, "rev-parse", "HEAD")
    workspace = tmp_path / "workspace"

    pilot._clone_attempt(source, workspace, commit)
    (workspace / "program.py").write_text("buggy = False\n", encoding="utf-8")

    assert _git(workspace, "rev-parse", "HEAD") == commit
    assert _git(source, "status", "--porcelain=v1") == ""
    assert tracked.read_text(encoding="utf-8") == "buggy = True\n"


def test_real_quixbugs_workspace_origin_survives_relocation(tmp_path: Path) -> None:
    official = Path(".quixbugs-src").absolute()
    if not official.is_dir():
        pytest.skip("The pinned QuixBugs integration checkout is not available.")
    task, _ = pilot._load_task(pilot.DEFAULT_MANIFEST, "quixbugs.gcd")
    if _git(official, "rev-parse", "HEAD") != task["pinned_commit"]:
        pytest.skip("The local QuixBugs checkout is not at the pilot commit.")

    canonical = tmp_path / "canonical-source"
    shutil.copytree(official, canonical)
    workspace = tmp_path / "first-location" / "workspace"
    workspace.parent.mkdir()
    pilot._clone_attempt(canonical, workspace, str(task["pinned_commit"]))

    origin = _git(workspace, "remote", "get-url", "origin")
    assert origin == "./.git/cgr-origin.bundle"
    assert str(canonical) not in origin
    assert (workspace / ".git" / "cgr-origin.bundle").is_file()

    relocated = tmp_path / "unrelated-location" / "uploaded-workspace"
    relocated.parent.mkdir()
    shutil.copytree(workspace, relocated)
    canonical.rename(tmp_path / "canonical-source-unavailable")

    for command in (
        ("status",),
        ("fetch",),
        ("checkout", "HEAD"),
        ("clean", "-fdq"),
    ):
        _git(relocated, *command)

    assert _git(relocated, "rev-parse", "HEAD") == task["pinned_commit"]
    assert _git(relocated, "status", "--porcelain=v1") == ""
    assert _git(relocated, "diff", "--binary", "HEAD", "--") == ""


@pytest.mark.parametrize(
    ("adapter_result", "termination", "expected"),
    [
        ({"error": "provider API call failed"}, None, "model_failure"),
        ({"error": "No non-empty unified patch"}, None, "no_patch"),
        ({"error": "stopped"}, "exit_cost", "budget_exhausted"),
        ({"error": "tool failed"}, None, "agent_failure"),
    ],
)
def test_agent_failures_are_classified(
    adapter_result: dict[str, str], termination: str | None, expected: str
) -> None:
    assert pilot._classify_agent_failure(adapter_result, termination) == expected


def test_verifier_command_uses_configured_python() -> None:
    task, _ = pilot._load_task(pilot.DEFAULT_MANIFEST, "quixbugs.gcd")
    python = Path("/venv/bin/python")

    command = pilot._verifier_command(task, python)

    assert command == [
        str(python),
        "-m",
        "pytest",
        "-q",
        "python_testcases/test_gcd.py",
    ]


def test_agent_pytest_runtime_is_portable_and_preflighted(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)

    runtime = pilot._prepare_agent_test_runtime(workspace, Path(sys.executable))
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(workspace / ".git" / "cgr-test-runtime")
    imported = subprocess.run(
        [sys.executable, "-S", "-c", "import pytest; print(pytest.__version__)"],
        cwd=workspace,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert imported.returncode == 0, imported.stderr
    assert runtime["copied_entries"]
    overlay = pilot._quixbugs_overlay()
    assert "CGR_PYTEST_READY=" in overlay
    assert "PYTHONPATH=.git/cgr-test-runtime" in overlay
    assert "command -v python" in overlay
    assert "command -v sed" in overlay
    assert ".sandbox-sweagent-venv" not in overlay


def test_actionable_recovery_requires_grounded_noop_edit() -> None:
    assert pilot._qualifies_for_actionable_recovery(
        {
            "required_next_phase": "edit",
            "no_op_edits": [{"target": "python_programs/gcd.py"}],
            "phase_exit_condition": {"requires_nonempty_diff": True},
        }
    )
    assert not pilot._qualifies_for_actionable_recovery(
        {
            "required_next_phase": "edit",
            "no_op_edits": [],
            "phase_exit_condition": {"requires_nonempty_diff": True},
            "budget_exhausted": True,
        }
    )
    assert pilot._qualifies_for_actionable_recovery(
        {
            "required_next_phase": "edit",
            "declared_edit_not_executed": [{"actual_action_kind": "test"}],
            "phase_exit_condition": {"requires_nonempty_diff": True},
        }
    )
