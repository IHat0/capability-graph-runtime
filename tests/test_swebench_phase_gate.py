from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from cgr.swebench.phase_gate import (
    PhaseGate,
    RepositoryEvidence,
    classify_candidate_action,
    install_sweagent_phase_gate,
)


TARGET = "src/module.py"
TEST = "tests/test_module.py"


@pytest.mark.parametrize(
    ("action", "kind"),
    [
        ("cat src/module.py", "target_confirmation"),
        ("ls tests", "inspection"),
        ("sed -i 's/a/b/' src/module.py", "edit"),
        ("python -c \"open('src/module.py','w').write('x')\"", "edit"),
        ("cat > src/module.py <<'EOF'\nx\nEOF", "edit"),
        ("git diff -- src/module.py", "git_diff"),
        ("python -m pytest -q tests/test_module.py", "focused_test"),
        ("pytest -q tests/test_other.py", "unrelated_test"),
        ("git commit -m x", "commit"),
        ("git push origin main", "push"),
        ("submit", "submission"),
        ("frobnicate", "unknown"),
    ],
)
def test_candidate_action_classification(action: str, kind: str) -> None:
    assert classify_candidate_action(action, target=TARGET, focused_test=TEST).kind == kind


@pytest.mark.parametrize(
    "action",
    [
        "sed -i 's/a/b/' src/module.py",
        "python -c \"open('src/module.py','w').write('x')\"",
        "cat > src/module.py <<'EOF'\nx\nEOF",
    ],
)
def test_effective_supported_edit_advances_to_confirm_edit(action: str) -> None:
    gate = PhaseGate(phase="edit", target=TARGET, focused_test=TEST)
    before = RepositoryEvidence()
    decision = gate.decide(action, before)

    assert decision.allowed
    gate.record_execution(
        decision,
        observation="",
        evidence=RepositoryEvidence(target_diff="diff --git a/src/module.py b/src/module.py"),
    )
    assert gate.phase == "confirm_edit"


def test_test_is_rejected_during_edit_without_execution() -> None:
    gate = PhaseGate(phase="edit", target=TARGET, focused_test=TEST)
    decision = gate.decide(
        "python -m pytest -q tests/test_module.py", RepositoryEvidence()
    )

    assert not decision.allowed
    assert decision.feedback is not None
    assert "was not executed" in decision.feedback
    assert "Required phase: edit" in decision.feedback


def test_inspection_is_allowed_during_inspect() -> None:
    gate = PhaseGate(phase="inspect", target=TARGET, focused_test=TEST)

    assert gate.decide("ls src && cat src/module.py", RepositoryEvidence()).allowed


def test_wrapper_returns_rejection_without_calling_executor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executor_calls: list[str] = []

    class FakeAgent:
        def __init__(self) -> None:
            self._env = SimpleNamespace(communicate=lambda command, check: "")
            self.tools = SimpleNamespace(get_state=lambda env: {"working_dir": "/repo"})

        def handle_action(self, step: SimpleNamespace) -> SimpleNamespace:
            executor_calls.append(step.action)
            return step

    sweagent = ModuleType("sweagent")
    agent = ModuleType("sweagent.agent")
    agents = ModuleType("sweagent.agent.agents")
    agents.DefaultAgent = FakeAgent  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sweagent", sweagent)
    monkeypatch.setitem(sys.modules, "sweagent.agent", agent)
    monkeypatch.setitem(sys.modules, "sweagent.agent.agents", agents)
    config = tmp_path / "phase-gate.json"
    config.write_text(
        json.dumps(
            {
                "initial_phase": "edit",
                "target": TARGET,
                "focused_test": TEST,
                "log_path": str(tmp_path / "events.jsonl"),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CGR_PHASE_GATE_CONFIG", str(config))

    install_sweagent_phase_gate()
    step = SimpleNamespace(
        action="pytest -q tests/test_module.py", observation="", state=None
    )
    result = FakeAgent().handle_action(step)

    assert executor_calls == []
    assert "ACTION REJECTED BY CGR" in result.observation
    assert result.state == {"working_dir": "/repo"}


def test_noop_and_malformed_edits_remain_in_edit() -> None:
    for observation in ("", "sed: malformed expression"):
        gate = PhaseGate(phase="edit", target=TARGET, focused_test=TEST)
        decision = gate.decide("sed -i 's/a/b/' src/module.py", RepositoryEvidence())
        gate.record_execution(decision, observation=observation, evidence=RepositoryEvidence())
        assert gate.phase == "edit"


def test_wrong_file_edit_does_not_satisfy_target_edit() -> None:
    gate = PhaseGate(phase="edit", target=TARGET, focused_test=TEST)
    decision = gate.decide("sed -i 's/a/b/' src/other.py", RepositoryEvidence())

    assert not decision.allowed
    assert "does not target" in str(decision.feedback)


def test_confirm_edit_requires_inspection_and_nonempty_target_diff() -> None:
    evidence = RepositoryEvidence(
        target_diff="diff --git a/src/module.py b/src/module.py",
        tracked_diff="diff --git a/src/module.py b/src/module.py",
    )
    gate = PhaseGate(phase="confirm_edit", target=TARGET, focused_test=TEST)

    rejected_test = gate.decide("pytest -q tests/test_module.py", evidence)
    assert not rejected_test.allowed

    inspection = gate.decide("cat src/module.py", evidence)
    assert inspection.allowed
    gate.record_execution(inspection, observation="changed", evidence=evidence)
    assert gate.phase == "confirm_edit"

    diff = gate.decide("git diff -- src/module.py", evidence)
    assert diff.allowed
    gate.record_execution(diff, observation=evidence.target_diff, evidence=evidence)
    assert gate.phase == "test"


def test_test_pass_final_diff_and_submission_transitions() -> None:
    evidence = RepositoryEvidence(target_diff="patch", tracked_diff="patch")
    gate = PhaseGate(phase="test", target=TARGET, focused_test=TEST)

    test = gate.decide("pytest -q tests/test_module.py", evidence)
    assert test.allowed
    gate.record_execution(test, observation="1 passed in 0.01s", evidence=evidence)
    assert gate.phase == "final_diff"

    diff = gate.decide("git diff -- src/module.py", evidence)
    assert diff.allowed
    gate.record_execution(diff, observation="patch", evidence=evidence)
    assert gate.phase == "submit"
    assert gate.decide("submit", evidence).allowed


def test_submission_without_patch_and_unknown_action_are_rejected() -> None:
    submit = PhaseGate(phase="submit", target=TARGET, focused_test=TEST)
    unknown = PhaseGate(phase="edit", target=TARGET, focused_test=TEST)

    assert not submit.decide("submit", RepositoryEvidence()).allowed
    assert not unknown.decide("frobnicate", RepositoryEvidence()).allowed


def test_repeated_known_failure_without_change_is_rejected() -> None:
    evidence = RepositoryEvidence(target_diff="same patch", tracked_diff="same patch")
    gate = PhaseGate(phase="test", target=TARGET, focused_test=TEST)
    action = "pytest -q tests/test_module.py"
    first = gate.decide(action, evidence)
    gate.record_execution(first, observation="1 failed in 0.01s", evidence=evidence)

    repeated = gate.decide(action, evidence)
    assert not repeated.allowed
    assert "unchanged known failing verifier" in str(repeated.feedback)


def test_commit_and_push_are_rejected_in_every_phase() -> None:
    for phase in ("inspect", "edit", "confirm_edit", "test", "final_diff", "submit"):
        gate = PhaseGate(phase=phase, target=TARGET, focused_test=TEST)
        assert not gate.decide("git commit -am x", RepositoryEvidence()).allowed
        assert not gate.decide("git push", RepositoryEvidence()).allowed
