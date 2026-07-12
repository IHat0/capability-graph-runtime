from __future__ import annotations

import json
import subprocess
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
