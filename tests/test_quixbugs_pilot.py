from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from cgr import quixbugs_pilot as pilot
from cgr.swebench.phase_gate import PatchAuthorization


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


def test_phase_gate_config_is_absolute_persistent_and_child_readable(tmp_path: Path) -> None:
    attempt = tmp_path / "attempt-001"
    attempt.mkdir()
    config, event_log, state_path = pilot._write_phase_gate_config(
        attempt,
        initial_phase="edit",
        target="src/module.py",
        focused_test="tests/test_module.py",
    )
    environment = os.environ.copy()
    environment["CGR_PHASE_GATE_CONFIG"] = str(config)
    script = (
        "import json, os, pathlib, time; "
        "p=pathlib.Path(os.environ['CGR_PHASE_GATE_CONFIG']); "
        "a=json.loads(p.read_text())['initial_phase']; time.sleep(.05); "
        "b=json.loads(p.read_text())['initial_phase']; print(a + ':' + b)"
    )

    process, timed_out = pilot._run_adapter_process(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        timeout=5,
        environment=environment,
    )

    assert not timed_out
    assert process.returncode == 0
    assert process.stdout.strip() == "edit:edit"
    assert config.is_absolute() and config.is_file()
    assert event_log.is_absolute() and event_log.is_file()
    assert state_path.is_absolute() and state_path.is_file()
    assert pilot._assert_phase_gate_config(config, environment) is not None
    payload = json.loads(config.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["state_path"] == str(state_path)
    assert payload["edit_policy"] == {
        "mode": "modify_existing_source",
        "prohibit_test_scaffolding": True,
        "require_existing_content_change": True,
    }
    assert json.loads(state_path.read_text(encoding="utf-8"))["current_phase"] == "edit"


def test_unauthorized_autosubmission_is_retained_but_phase_incomplete() -> None:
    classification = pilot._phase_aware_classification(
        "resolved",
        gate_enabled=True,
        patch_emitted=True,
        authorization=PatchAuthorization(
            False, ("workflow_incomplete",), "candidate-fingerprint"
        ),
    )

    assert classification == "phase_incomplete"


def test_transactional_deterministic_profiles_reject_then_recover(tmp_path: Path) -> None:
    task, _manifest = pilot._load_task(pilot.DEFAULT_MANIFEST, "quixbugs.gcd")
    failure = pilot._deterministic_actions(
        task, Path(sys.executable), tmp_path, profile="transactional_failure"
    )
    recovery = pilot._deterministic_actions(
        task, Path(sys.executable), tmp_path, profile="transactional_recovery"
    )

    assert len(failure) == 5
    assert "git commit" in failure[0]
    assert failure[1] == "cat python_programs/gcd.py"
    assert "test_gcd.py" in failure[3]
    assert "import unittest" in failure[4]
    assert recovery[:5] == failure
    assert "sed -i" in recovery[5]
    assert "pytest" in recovery[8]
    assert "SWE_AGENT_SUBMISSION" in recovery[-1]
    assert pilot._deterministic_max_calls("transactional_failure") == 6
    assert pilot._deterministic_max_calls("transactional_recovery") == 12


def test_missing_phase_gate_config_fails_closed(tmp_path: Path) -> None:
    missing = (tmp_path / "missing.json").absolute()

    with pytest.raises(pilot.PhaseGateBootstrapError, match="missing, unreadable, or invalid"):
        pilot._assert_phase_gate_config(
            missing, {"CGR_PHASE_GATE_CONFIG": str(missing)}
        )


def test_unreadable_phase_gate_config_fails_closed(tmp_path: Path) -> None:
    unreadable = (tmp_path / "phase-gate-config.json").absolute()
    unreadable.mkdir()

    with pytest.raises(pilot.PhaseGateBootstrapError, match="missing, unreadable, or invalid"):
        pilot._assert_phase_gate_config(
            unreadable, {"CGR_PHASE_GATE_CONFIG": str(unreadable)}
        )


def test_adapter_failure_before_model_contact_is_bootstrap_error(tmp_path: Path) -> None:
    classification = pilot._classify_adapter_failure(
        {"error": "Official SWE-agent exited with code 1."},
        None,
        model_requests=0,
        trajectory=None,
        prediction=None,
        diff="",
        stderr="startup failed",
        phase_config=None,
    )

    assert classification == "adapter_bootstrap_error"


def test_provider_failure_before_recorded_step_remains_model_failure() -> None:
    classification = pilot._classify_adapter_failure(
        {"error": "Provider API call failed"},
        None,
        model_requests=0,
        trajectory=None,
        prediction=None,
        diff="",
        stderr="connection refused",
        phase_config=None,
    )

    assert classification == "model_failure"


def test_phase_gate_startup_failure_is_distinct(tmp_path: Path) -> None:
    config = tmp_path / "phase-gate-config.json"
    classification = pilot._classify_adapter_failure(
        {},
        None,
        model_requests=0,
        trajectory=None,
        prediction=None,
        diff="",
        stderr="Error in sitecustomize while loading CGR_PHASE_GATE_CONFIG",
        phase_config=config,
    )

    assert classification == "phase_gate_bootstrap_error"


def test_normal_model_no_patch_is_not_bootstrap_failure() -> None:
    classification = pilot._classify_adapter_failure(
        {"error": "No non-empty unified patch"},
        "submitted",
        model_requests=2,
        trajectory=Path("attempt.traj"),
        prediction=Path("attempt.pred"),
        diff="",
        stderr="",
        phase_config=None,
    )

    assert classification == "no_patch"


def test_repeated_bootstrap_signature_is_stable_and_detected() -> None:
    seen: set[str] = set()
    first = {
        "classification": "adapter_bootstrap_error",
        "adapter_exit_code": 1,
        "adapter_error": "failure in run-001/attempt-001",
        "model_requests": 0,
        "trajectory_path": None,
        "prediction_path": None,
    }
    second = {**first, "adapter_error": "failure in run-002/attempt-002"}

    first_signature, first_duplicate = pilot._register_bootstrap_failure(seen, first)
    second_signature, second_duplicate = pilot._register_bootstrap_failure(seen, second)

    assert first_signature == second_signature
    assert not first_duplicate
    assert second_duplicate


def test_repeated_bootstrap_stops_outer_run_without_model_correction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []

    def fake_attempt(*args: object, **kwargs: object) -> dict[str, object]:
        calls.append(len(calls) + 1)
        return {
            "artifact_directory": str(tmp_path / f"attempt-{len(calls):03d}"),
            "classification": "adapter_bootstrap_error",
            "infrastructure_status": "failed",
            "top_level_exit_code": 1,
            "adapter_exit_code": 1,
            "adapter_error": "stable startup failure",
            "model_requests": 0,
            "model_requests_source": "trajectory_steps",
            "trajectory_path": None,
            "prediction_path": None,
        }

    monkeypatch.setattr(pilot, "_launch_child_attempt", fake_attempt)
    args = SimpleNamespace(
        result_root=tmp_path,
        task_id="quixbugs.gcd",
        manifest=pilot.DEFAULT_MANIFEST,
        deployment_type="local",
        sweagent_python=Path(sys.executable),
    )

    assert pilot._run_cgr(args, 3) == 1
    result = json.loads((tmp_path / "quixbugs.gcd" / "run-001" / "run-result.json").read_text())
    assert calls == [1, 2]
    assert result["attempts_completed"] == 2
    assert result["diagnoses_generated"] == []
    assert result["corrective_messages_generated"] == []
    assert result["top_level_exit_code"] == 1


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


def test_attempt_timeout_default_is_bounded_and_configurable() -> None:
    assert pilot.DEFAULT_ATTEMPT_TIMEOUT_SECONDS == 600


def test_adapter_timeout_terminates_process_group(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeProcess:
        pid = 123
        returncode = -1

        def __init__(self) -> None:
            self.calls = 0

        def communicate(self, timeout: int | None = None) -> tuple[str, str]:
            self.calls += 1
            if self.calls == 1:
                raise subprocess.TimeoutExpired(["adapter"], timeout)
            return "partial stdout", "partial stderr"

    fake = FakeProcess()
    popen_arguments: dict[str, object] = {}
    terminated: list[object] = []

    def fake_popen(*args: object, **kwargs: object) -> FakeProcess:
        popen_arguments.update(kwargs)
        return fake

    monkeypatch.setattr(pilot.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(pilot, "_terminate_process_group", terminated.append)

    result, timed_out = pilot._run_adapter_process(
        ["adapter"], cwd=pilot.Path.cwd(), timeout=600, environment={}
    )

    assert timed_out is True
    assert result.stdout == "partial stdout"
    assert result.stderr == "partial stderr"
    assert terminated == [fake]
    if pilot.os.name == "nt":
        assert popen_arguments["creationflags"] == subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        assert popen_arguments["start_new_session"] is True
