from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import asyncio
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from cgr.swebench.phase_gate import (
    EditPolicy,
    FileSnapshot,
    PhaseGate,
    RepositoryEvidence,
    _ExecutionExitCapture,
    _normalize_observation_text,
    _probe_repository,
    _restore_snapshot,
    authorize_phase_patch,
    classify_candidate_action,
    evaluate_edit,
    install_sweagent_phase_gate,
    patch_fingerprint,
)


TARGET = "src/module.py"
TEST = "tests/test_module.py"
EC2_SED_ACTION = (
    "sed -i 's/def gcd(a, b):/def gcd(a, b):/; "
    "s/return gcd(a % b, b)/while b != 0:/; s/^/    /' python_programs/gcd.py"
)
EC2_HEREDOC_ACTION = (
    "sed -i '/def gcd(a, b):/,/return a/' python_programs/gcd.py <<EOF\n"
    "while b != 0:\n"
    "a, b = b, a % b\n"
    "return a\n"
    "EOF"
)


@pytest.mark.parametrize(
    ("action", "kind"),
    [
        ("cat src/module.py", "target_confirmation"),
        ("ls tests", "inspection"),
        ("sed -i 's/a/b/' src/module.py", "noninteractive_edit"),
        ("python -c \"open('src/module.py','w').write('x')\"", "noninteractive_edit"),
        ("cat > src/module.py <<'EOF'\nx\nEOF", "noninteractive_edit"),
        ("git diff -- src/module.py", "git_diff"),
        ("python -m pytest -q tests/test_module.py", "focused_pytest"),
        ("pytest -q tests/test_other.py", "unrelated_pytest"),
        ("python -m unittest tests/test_module.py", "focused_unittest"),
        ("python3 -m unittest discover", "unrelated_unittest"),
        ("nano src/module.py", "interactive_editor"),
        ("vim src/module.py", "interactive_editor"),
        ("vi src/module.py", "interactive_editor"),
        ("emacs src/module.py", "interactive_editor"),
        ("git commit -m x", "commit"),
        ("git push origin main", "push"),
        ("submit", "submission"),
        ("frobnicate", "unknown"),
    ],
)
def test_candidate_action_classification(action: str, kind: str) -> None:
    assert classify_candidate_action(action, target=TARGET, focused_test=TEST).kind == kind


@pytest.mark.parametrize(
    ("action", "kind", "targets"),
    [
        ("echo x > src/module.py", "noninteractive_edit", ("src/module.py",)),
        ("printf x >> src/module.py", "noninteractive_edit", ("src/module.py",)),
        ("printf x | tee src/module.py", "noninteractive_edit", ("src/module.py",)),
        ("printf x | tee -a src/module.py", "noninteractive_edit", ("src/module.py",)),
        ("touch tests/test_module.py", "edit_wrong_file", ("tests/test_module.py",)),
        ("echo x > tests/test_module.py", "edit_wrong_file", ("tests/test_module.py",)),
        (
            "echo x > src/one.py\nprintf y >> src/two.py",
            "edit_wrong_file",
            ("src/one.py", "src/two.py"),
        ),
        (
            "echo x > src/module.py\necho y > tests/test_module.py",
            "edit_mixed_targets",
            ("src/module.py", "tests/test_module.py"),
        ),
        (
            "cat <<'EOF' > src/module.py\nx\nEOF",
            "noninteractive_edit",
            ("src/module.py",),
        ),
        (
            "python - <<'PY'\nfrom pathlib import Path\n"
            "Path('src/module.py').write_text('x')\nPY",
            "noninteractive_edit",
            ("src/module.py",),
        ),
        (
            "python -c \"from pathlib import Path; Path('src/module.py').open('w+').write('x')\"",
            "noninteractive_edit",
            ("src/module.py",),
        ),
    ],
)
def test_redirected_write_targets_are_extracted(
    action: str, kind: str, targets: tuple[str, ...]
) -> None:
    candidate = classify_candidate_action(action, target=TARGET, focused_test=TEST)

    assert candidate.kind == kind
    assert candidate.write_targets == targets


@pytest.mark.parametrize(
    ("action", "kind"),
    [
        ("echo x > src/module.py && git commit -am x", "commit"),
        ("touch src/module.py && git push origin main", "push"),
    ],
)
def test_prohibited_git_action_takes_precedence_over_edit(action: str, kind: str) -> None:
    assert classify_candidate_action(action, target=TARGET, focused_test=TEST).kind == kind


def test_python_read_open_is_not_misclassified_as_write() -> None:
    candidate = classify_candidate_action(
        "python -c \"from pathlib import Path; Path('src/module.py').open().read()\"",
        target=TARGET,
        focused_test=TEST,
    )

    assert candidate.write_targets == ()
    assert candidate.kind == "unknown"


def test_real_ec2_sed_action_is_a_single_target_noninteractive_edit() -> None:
    candidate = classify_candidate_action(
        EC2_SED_ACTION,
        target="python_programs/gcd.py",
        focused_test="test_gcd.py",
    )

    assert candidate.kind == "noninteractive_edit"
    assert candidate.write_targets == ("python_programs/gcd.py",)
    assert candidate.parsed_executable == "sed"
    assert candidate.parsed_argv[2].count(";") == 2
    assert "%" in candidate.parsed_argv[2]
    assert candidate.heredoc_present is False


@pytest.mark.parametrize(
    "action",
    [
        r"sed -i 's/value\(x\)/value(y)/; s#old/path#new\\path#' src/module.py",
        "sed -i'' 's/a/b/' src/module.py",
        "sed -i '' 's/a/b/' src/module.py",
        "sed -i.bak 's/a/b/' src/module.py",
        "sed --in-place 's/a/b/' src/module.py",
        "sed --in-place=.bak 's/a/b/' src/module.py",
        "sed -e 's/a/b/' -i src/module.py",
        "sed -n -i -e 's/a/b/' src/module.py",
        "sed -e 's/a/b/' -e 's/c/d/' -i src/module.py",
    ],
)
def test_sed_in_place_variants_extract_only_positional_file_operands(action: str) -> None:
    candidate = classify_candidate_action(action, target=TARGET, focused_test=TEST)

    assert candidate.kind == "noninteractive_edit"
    assert candidate.write_targets == (TARGET,)
    assert candidate.parsed_executable == "sed"


def test_quoted_sed_program_metacharacters_are_not_paths_or_command_separators() -> None:
    action = r"sed -i 's#x/y%z#x\\y%;z#; s/(old)/[new]/' src/module.py"

    candidate = classify_candidate_action(action, target=TARGET, focused_test=TEST)

    assert candidate.kind == "noninteractive_edit"
    assert candidate.write_targets == (TARGET,)
    assert len(candidate.parsed_argv) == 4
    assert ";" in candidate.parsed_argv[2]
    assert "%" in candidate.parsed_argv[2]
    assert "\\" in candidate.parsed_argv[2]


@pytest.mark.parametrize("literal", [r"\;", "';'", '";"'])
def test_escaped_or_quoted_standalone_semicolon_is_an_argument(literal: str) -> None:
    action = f"printf {literal} > src/module.py"

    candidate = classify_candidate_action(action, target=TARGET, focused_test=TEST)

    assert candidate.kind == "noninteractive_edit"
    assert candidate.write_targets == (TARGET,)
    assert candidate.parsed_argv == ("printf", ";")


@pytest.mark.parametrize(
    ("operator", "expected_operator"),
    [
        ("<<EOF", "<<"),
        ("<< EOF", "<<"),
        ("<<'EOF'", "<<"),
        ('<<"EOF"', "<<"),
        ("<<-EOF", "<<-"),
    ],
)
def test_heredoc_syntax_is_metadata_not_a_write_target(
    operator: str, expected_operator: str
) -> None:
    action = (
        f"sed -i 's/old/new/' {TARGET} {operator}\n"
        "tests/not_a_target.py\n"
        "Path('also_not_a_target.py').write_text('x')\n"
        "EOF"
    )

    candidate = classify_candidate_action(action, target=TARGET, focused_test=TEST)

    assert candidate.kind == "noninteractive_edit"
    assert candidate.write_targets == (TARGET,)
    assert candidate.heredoc_present is True
    assert candidate.heredoc_delimiter == "EOF"
    assert expected_operator in candidate.redirection_operators


def test_real_ec2_heredoc_action_is_not_a_mixed_target_edit() -> None:
    candidate = classify_candidate_action(
        EC2_HEREDOC_ACTION,
        target="python_programs/gcd.py",
        focused_test="test_gcd.py",
    )

    assert candidate.kind == "noninteractive_edit"
    assert candidate.write_targets == ("python_programs/gcd.py",)
    assert candidate.heredoc_present is True
    assert candidate.heredoc_delimiter == "EOF"
    assert candidate.redirection_operators == ("<<",)


def test_custom_heredoc_delimiter_is_recorded_without_becoming_a_target() -> None:
    action = (
        "sed -i 's/old/new/' src/module.py <<'CGR_INPUT'\n"
        "arbitrary body text\n"
        "CGR_INPUT"
    )

    candidate = classify_candidate_action(action, target=TARGET, focused_test=TEST)

    assert candidate.kind == "noninteractive_edit"
    assert candidate.write_targets == (TARGET,)
    assert candidate.heredoc_delimiter == "CGR_INPUT"


def test_genuine_multi_file_sed_edit_remains_mixed_target() -> None:
    candidate = classify_candidate_action(
        "sed -i 's/old/new/' src/module.py src/other.py",
        target=TARGET,
        focused_test=TEST,
    )

    assert candidate.kind == "edit_mixed_targets"
    assert candidate.write_targets == (TARGET, "src/other.py")


def test_ambiguous_unclosed_shell_quote_fails_closed() -> None:
    candidate = classify_candidate_action(
        "sed -i 's/old/new/ src/module.py", target=TARGET, focused_test=TEST
    )

    assert candidate.kind == "unknown"
    assert candidate.write_targets == ()


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


def test_edit_phase_test_feedback_uses_grounded_verifier_evidence() -> None:
    gate = PhaseGate(
        phase="edit",
        target=TARGET,
        focused_test=TEST,
        verifier_failure_evidence={
            "available": True,
            "category": "recursion_error",
            "summary": "The configured verifier failed with RecursionError.",
        },
    )

    decision = gate.decide("python -m unittest test_module.py", RepositoryEvidence())

    assert not decision.allowed
    assert decision.candidate.kind == "unrelated_unittest"
    assert "was not executed" in str(decision.feedback)
    assert "RecursionError" in str(decision.feedback)
    assert "Testing cannot satisfy the edit phase" in str(decision.feedback)


def test_interactive_editor_is_classified_and_rejected_before_execution() -> None:
    gate = PhaseGate(phase="edit", target=TARGET, focused_test=TEST)

    decision = gate.decide(f"nano {TARGET}", RepositoryEvidence())

    assert not decision.allowed
    assert decision.candidate.kind == "interactive_editor"
    assert "editor was not opened" in str(decision.feedback)
    assert "sed -i" in str(decision.feedback)


def test_repeated_test_coaching_escalates_but_remains_bounded(tmp_path: Path) -> None:
    log_path = tmp_path / "events.jsonl"
    gate = PhaseGate(
        phase="edit",
        target=TARGET,
        focused_test=TEST,
        log_path=log_path,
        verifier_failure_evidence={
            "available": True,
            "summary": "The configured verifier failed with an assertion failure.",
        },
    )
    action = "python -m unittest test_module.py"

    feedback = [str(gate.decide(action, RepositoryEvidence()).feedback) for _ in range(3)]

    assert "repository is unchanged" not in feedback[0]
    assert "repository is unchanged" in feedback[1]
    assert "Supported mechanisms include" in feedback[2]
    assert max(map(len, feedback)) < 1500
    assert gate.state["phase_rejection_count"] == 3
    assert gate.state["repeated_candidate_count"] == 3
    assert gate.state["repeated_kind_count"] == 3
    assert gate.state["phase_stalled_repeated_action"] is True
    assert gate.state["coaching_level"] == 3
    events = [json.loads(line) for line in log_path.read_text().splitlines()]
    assert [event["coaching_level"] for event in events] == [1, 2, 3]
    assert len({event["last_rejected_action_fingerprint"] for event in events}) == 1


def test_declared_edit_without_action_is_telemetry_only() -> None:
    gate = PhaseGate(phase="edit", target=TARGET, focused_test=TEST)
    proposed = "I will modify the function implementation to return a different expression."

    decision = gate.decide(
        "git commit -am fix", RepositoryEvidence(), model_text=proposed
    )

    assert not decision.allowed
    assert gate.state["declared_edit_without_edit_action"] is True
    assert "did not apply" in str(decision.feedback)
    assert "different expression" not in str(decision.feedback)
    assert proposed not in str(decision.feedback)


def test_phase_entry_coaching_is_grounded_and_does_not_reveal_repair() -> None:
    gate = PhaseGate(
        phase="inspect",
        target=TARGET,
        focused_test=TEST,
        verifier_failure_evidence={
            "available": True,
            "summary": "The configured verifier failed with an assertion failure.",
        },
    )
    decision = gate.decide(f"cat {TARGET}", RepositoryEvidence())
    gate.record_execution(decision, observation="return a - b", evidence=RepositoryEvidence())

    coaching = gate.phase_transition_coaching()

    assert gate.phase == "edit"
    assert "Current phase: edit" in coaching
    assert "assertion failure" in coaching
    assert "noninteractive shell action" in coaching
    assert "return a + b" not in coaching


def test_real_trajectory_shape_recovers_to_model_authored_complete_workflow() -> None:
    gate = PhaseGate(
        phase="inspect",
        target=TARGET,
        focused_test=TEST,
        verifier_failure_evidence={
            "available": True,
            "summary": "The configured verifier failed with RecursionError.",
        },
    )
    empty = RepositoryEvidence()
    patch = RepositoryEvidence(target_diff="model patch", tracked_diff="model patch")

    commit = gate.decide(
        "git commit -am fix",
        empty,
        model_text="I propose modifying the function implementation.",
    )
    assert not commit.allowed
    assert gate.state["declared_edit_without_edit_action"] is True

    inspection = gate.decide(f"cat {TARGET}", empty)
    gate.record_execution(inspection, observation="current source", evidence=empty)
    assert gate.phase == "edit"

    first_test = gate.decide(
        "python -m unittest test_module.py",
        empty,
        model_text="The current implementation appears correct.",
    )
    editor = gate.decide(f"nano {TARGET}", empty)
    repeated_test = gate.decide("python -m unittest test_module.py", empty)
    assert first_test.candidate.kind == "unrelated_unittest"
    assert editor.candidate.kind == "interactive_editor"
    assert repeated_test.candidate.kind == "unrelated_unittest"
    assert "repository is unchanged" in str(repeated_test.feedback)

    model_edit_action = "sed -i 's/old expression/new expression/' src/module.py"
    edit = gate.decide(model_edit_action, empty)
    assert edit.allowed and edit.candidate.kind == "noninteractive_edit"
    gate.record_execution(edit, observation="", evidence=patch)

    confirmation = gate.decide(f"cat {TARGET}", patch)
    gate.record_execution(confirmation, observation="updated source", evidence=patch)
    target_diff = gate.decide(f"git diff -- {TARGET}", patch)
    gate.record_execution(target_diff, observation="model patch", evidence=patch)
    focused = gate.decide(f"python -m pytest -q {TEST}", patch)
    gate.record_execution(focused, observation="1 passed in 0.01s", evidence=patch)
    final_diff = gate.decide("git diff -- HEAD", patch)
    gate.record_execution(final_diff, observation="model patch", evidence=patch)
    submission = gate.decide("submit", patch)
    gate.record_execution(submission, observation="submitted", evidence=patch)

    assert gate.state["workflow_complete"] is True
    assert gate.state["submission_authorized"] is True
    assert gate.state["accepted_target_edit"] is True
    assert model_edit_action == edit.candidate.raw


def test_real_trajectory_shape_without_edit_fails_closed_at_budget_boundary() -> None:
    gate = PhaseGate(
        phase="inspect",
        target=TARGET,
        focused_test=TEST,
        verifier_failure_evidence={
            "available": True,
            "summary": "The configured verifier failed with an assertion failure.",
        },
    )
    empty = RepositoryEvidence()
    inspection = gate.decide(f"cat {TARGET}", empty)
    gate.record_execution(inspection, observation="current source", evidence=empty)
    for action in (
        "python -m unittest test_module.py",
        f"nano {TARGET}",
        "python -m unittest test_module.py",
    ):
        assert not gate.decide(action, empty).allowed

    assert gate.phase == "edit"
    assert gate.state["accepted_target_edit"] is False
    assert gate.state["workflow_complete"] is False
    assert gate.state["phase_stalled_repeated_action"] is True


def test_exact_attempt_006_shell_actions_progress_through_transactional_evaluation(
    tmp_path: Path,
) -> None:
    target = "python_programs/gcd.py"
    log_path = tmp_path / "events.jsonl"
    gate = PhaseGate(
        phase="inspect", target=target, focused_test="test_gcd.py", log_path=log_path
    )
    empty = RepositoryEvidence()

    commit = gate.decide("git commit -am fix", empty)
    assert not commit.allowed
    inspection = gate.decide(f"cat {target}", empty)
    gate.record_execution(inspection, observation="model-inspected source", evidence=empty)
    assert gate.phase == "edit"

    unchanged = evaluate_edit(
        FileSnapshot(target, True, b"def placeholder():\n    return 1\n", 0o644),
        FileSnapshot(target, True, b"def placeholder():\n    return 1\n", 0o644),
        target=target,
        policy=EditPolicy(),
    )
    for action, exit_code in ((EC2_SED_ACTION, 1), (EC2_HEREDOC_ACTION, 2)):
        decision = gate.decide(action, empty)
        assert decision.allowed
        assert decision.candidate.kind == "noninteractive_edit"
        assert decision.candidate.write_targets == (target,)
        gate.record_execution(
            decision,
            observation=f"shell exited {exit_code}",
            evidence=empty,
            accepted=False,
            rolled_back=True,
            postcondition_failures=("no_target_change", "command_nonzero_exit"),
            diff_evidence=unchanged.evidence,
            execution_exit_code=exit_code,
        )

    assert gate.phase == "edit"
    assert gate.state["accepted_target_edit"] is False
    assert gate.state["workflow_complete"] is False
    events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    edit_events = [event for event in events if event["execution_attempted"]]
    assert len(edit_events) == 2
    assert [event["execution_exit_code"] for event in edit_events] == [1, 2]
    assert all(event["true_write_targets"] == [target] for event in edit_events)
    assert edit_events[1]["heredoc_delimiter"] == "EOF"


def test_generic_model_authored_sed_completes_authorized_workflow(
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "events.jsonl"
    gate = PhaseGate(
        phase="inspect", target=TARGET, focused_test=TEST, log_path=log_path
    )
    empty = RepositoryEvidence()
    patch = RepositoryEvidence(target_diff="generic patch", tracked_diff="generic patch")

    inspection = gate.decide(f"cat {TARGET}", empty)
    gate.record_execution(inspection, observation="generic source", evidence=empty)
    action = "sed -i 's/value.strip()/value.upper()/' src/module.py"
    edit = gate.decide(action, empty)
    evaluation = evaluate_edit(
        _snapshot("def transform(value):\n    return value.strip()\n"),
        _snapshot("def transform(value):\n    return value.upper()\n"),
        target=TARGET,
        policy=EditPolicy(
            mode="modify_existing_source", require_existing_content_change=True
        ),
    )
    assert evaluation.accepted
    gate.record_execution(
        edit,
        observation="",
        evidence=patch,
        diff_evidence=evaluation.evidence,
        execution_exit_code=0,
    )
    confirmation = gate.decide(f"cat {TARGET}", patch)
    gate.record_execution(confirmation, observation="generic changed source", evidence=patch)
    target_diff = gate.decide(f"git diff -- {TARGET}", patch)
    gate.record_execution(target_diff, observation="generic patch", evidence=patch)
    focused = gate.decide(f"python -m pytest -q {TEST}", patch)
    gate.record_execution(focused, observation="1 passed in 0.01s", evidence=patch)
    final_diff = gate.decide("git diff -- HEAD", patch)
    gate.record_execution(final_diff, observation="generic patch", evidence=patch)
    submission = gate.decide("submit", patch)
    gate.record_execution(submission, observation="submitted", evidence=patch)

    assert gate.state["workflow_complete"] is True
    assert gate.state["submission_authorized"] is True
    assert authorize_phase_patch(gate.state, "generic patch").authorized
    events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    edit_event = next(event for event in events if event["execution_attempted"])
    assert edit_event["parsed_executable"] == "sed"
    assert edit_event["parsed_argv"] == shlex.split(action)
    assert edit_event["postcondition_outcome"] == "accepted"


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
    results = []
    for action in ("pytest -q tests/test_module.py", f"nano {TARGET}"):
        step = SimpleNamespace(action=action, observation="", state=None)
        results.append(FakeAgent().handle_action(step))

    assert executor_calls == []
    assert all("ACTION REJECTED BY CGR" in result.observation for result in results)
    assert all(result.state == {"working_dir": "/repo"} for result in results)
    assert "editor was not opened" in results[1].observation


def test_wrapper_preserves_source_observation_and_appends_phase_coaching(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executor_calls: list[str] = []

    class FakeAgent:
        def __init__(self) -> None:
            self._env = SimpleNamespace(communicate=lambda command, check: "")
            self.tools = SimpleNamespace(get_state=lambda env: {"working_dir": "/repo"})

        def handle_action(self, step: SimpleNamespace) -> SimpleNamespace:
            executor_calls.append(step.action)
            step.observation = "def value():\n    return 1"
            step.done = False
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
                "initial_phase": "inspect",
                "target": TARGET,
                "focused_test": TEST,
                "log_path": str(tmp_path / "events.jsonl"),
                "verifier_failure_evidence": {
                    "available": True,
                    "summary": "The configured verifier failed with RecursionError.",
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CGR_PHASE_GATE_CONFIG", str(config))
    install_sweagent_phase_gate()
    step = SimpleNamespace(
        action=f"cat {TARGET}", observation="", state=None, thought="", output=""
    )

    result = FakeAgent().handle_action(step)

    assert executor_calls == [f"cat {TARGET}"]
    assert result.observation.startswith("def value():\n    return 1")
    assert "CGR PHASE TRANSITION" in result.observation
    assert "RecursionError" in result.observation


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
    assert decision.candidate.kind == "edit_wrong_file"
    assert "Required target: src/module.py" in str(decision.feedback)
    assert "Observed write target: src/other.py" in str(decision.feedback)
    assert "does not modify the required production source" in str(decision.feedback)
    assert "s/a/b" not in str(decision.feedback)


def test_mixed_target_edit_is_rejected_with_grounded_feedback() -> None:
    gate = PhaseGate(phase="edit", target=TARGET, focused_test=TEST)
    action = "echo x > src/module.py\necho y > tests/test_module.py"

    decision = gate.decide(action, RepositoryEvidence())

    assert not decision.allowed
    assert decision.candidate.kind == "edit_mixed_targets"
    assert "src/module.py, tests/test_module.py" in str(decision.feedback)
    assert "mixes the required source edit" in str(decision.feedback)


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


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("raw output", "raw output"),
        ({"output": "mapping output"}, "mapping output"),
        (SimpleNamespace(output="object output"), "object output"),
        (SimpleNamespace(stdout="stdout output"), "stdout output"),
    ],
)
def test_observation_text_normalization(value: object, expected: str) -> None:
    assert _normalize_observation_text(value) == expected


def test_repository_probe_normalizes_swerex_observation_objects() -> None:
    class FakeEnvironment:
        def communicate(self, command: str, check: str) -> object:
            assert check == "ignore"
            if "--binary" in command:
                return SimpleNamespace(output="tracked patch")
            return {"output": "target patch"}

    evidence = _probe_repository(SimpleNamespace(_env=FakeEnvironment()), TARGET)

    assert evidence == RepositoryEvidence(
        target_diff="target patch", tracked_diff="tracked patch"
    )


def test_execution_exit_capture_observes_runtime_result_without_changing_action() -> None:
    observed_actions: list[object] = []

    class FakeRuntime:
        async def run_in_session(self, action: object) -> object:
            observed_actions.append(action)
            return SimpleNamespace(exit_code=7, output="failure")

    runtime = FakeRuntime()
    environment = SimpleNamespace(deployment=SimpleNamespace(runtime=runtime))
    action = SimpleNamespace(command="sed -i 'malformed' src/module.py")

    with _ExecutionExitCapture(environment) as capture:
        response = asyncio.run(runtime.run_in_session(action))

    assert response.exit_code == 7
    assert capture.exit_code == 7
    assert observed_actions == [action]
    assert runtime.run_in_session.__func__ is FakeRuntime.run_in_session


def test_action_event_telemetry_is_bounded_and_redacts_secrets(tmp_path: Path) -> None:
    log_path = tmp_path / "events.jsonl"
    gate = PhaseGate(phase="edit", target=TARGET, focused_test=TEST, log_path=log_path)
    action = "echo x > src/module.py && echo API_KEY=do-not-store"
    decision = gate.decide(action, RepositoryEvidence())

    gate.record_execution(
        decision,
        observation="Authorization: Bearer secret-value",
        evidence=RepositoryEvidence(target_diff="patch", tracked_diff="patch"),
        execution_exit_code=0,
    )

    event_text = log_path.read_text(encoding="utf-8")
    assert "do-not-store" not in event_text
    assert "secret-value" not in event_text
    assert "<redacted>" in event_text


def _snapshot(content: str, *, existed: bool = True, mode: int = 0o644) -> FileSnapshot:
    return FileSnapshot(TARGET, existed, content.encode(), mode if existed else None)


def _initialize_git_target(tmp_path: Path, target_name: str, content: bytes) -> Path:
    target = tmp_path / target_name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", target_name], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tmp_path, check=True)
    return target


def _bash_executable() -> str | None:
    located = shutil.which("bash")
    if located is not None:
        return located
    if os.name == "nt":
        candidate = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git/bin/bash.exe"
        if candidate.is_file():
            return str(candidate)
    return None


class _LocalPhaseEnvironment:
    def __init__(self, root: Path) -> None:
        self.root = root

    def communicate(self, command: str, check: str) -> str:
        process = subprocess.run(
            shlex.split(command, posix=True),
            cwd=self.root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if check == "raise" and process.returncode:
            raise RuntimeError(process.stderr)
        return process.stdout + process.stderr


@pytest.mark.parametrize(
    ("after", "failure"),
    [
        (
            "def add(a, b):\n    return a - b\n\nimport unittest\n"
            "class TestAdd(unittest.TestCase):\n    def test_add(self):\n"
            "        self.assertEqual(add(2, 3), 5)\n",
            "test_scaffolding_in_production_source",
        ),
        (
            "def add(a, b):\n    return a - b\n\nHELPER = 1\n",
            "append_only_nonrepair_edit",
        ),
        (
            "# explanation\ndef add(a, b):\n    return a - b\n",
            "existing_implementation_unchanged",
        ),
    ],
)
def test_modify_existing_policy_rejects_nonrepair_edits(after: str, failure: str) -> None:
    result = evaluate_edit(
        _snapshot("def add(a, b):\n    return a - b\n"),
        _snapshot(after),
        target=TARGET,
        policy=EditPolicy(
            mode="modify_existing_source",
            prohibit_test_scaffolding=True,
            require_existing_content_change=True,
        ),
    )

    assert not result.accepted
    assert failure in result.failures


def test_focused_existing_implementation_edit_is_accepted() -> None:
    result = evaluate_edit(
        _snapshot("def add(a, b):\n    return a - b\n"),
        _snapshot("def add(a, b):\n    return a + b\n"),
        target=TARGET,
        policy=EditPolicy(
            mode="modify_existing_source",
            prohibit_test_scaffolding=True,
            require_existing_content_change=True,
        ),
    )

    assert result.accepted
    assert result.evidence.existing_lines_modified
    assert result.evidence.executable_content_changed


def test_rejected_followup_edit_preserves_prior_accepted_candidate() -> None:
    evidence = RepositoryEvidence(target_diff="focused patch", tracked_diff="focused patch")
    gate = PhaseGate(phase="edit", target=TARGET, focused_test=TEST)
    first = gate.decide("sed -i 's/a/b/' src/module.py", RepositoryEvidence())
    gate.record_execution(first, observation="", evidence=evidence, accepted=True)
    assert gate.phase == "confirm_edit"

    followup = gate.decide("printf scaffold >> src/module.py", evidence)
    gate.record_execution(
        followup,
        observation="rejected and restored",
        evidence=evidence,
        accepted=False,
        rolled_back=True,
        postcondition_failures=("append_only_nonrepair_edit",),
    )

    assert gate.phase == "confirm_edit"
    assert gate.state["accepted_target_edit"] is True
    assert gate.state["accepted_patch_fingerprint"] == patch_fingerprint("focused patch")


def test_snapshot_restore_preserves_exact_prior_bytes_and_mode(tmp_path: Path) -> None:
    target = tmp_path / "module.py"
    prior = b"def value():\r\n    return 1\r\n"
    target.write_bytes(b"destructive replacement\n")

    class LocalEnvironment:
        def communicate(self, command: str, check: str) -> str:
            argv = shlex.split(command, posix=True)
            process = subprocess.run(
                [sys.executable, *argv[1:]],
                cwd=tmp_path,
                capture_output=True,
                text=True,
                check=False,
            )
            if check == "raise" and process.returncode:
                raise RuntimeError(process.stderr)
            return process.stdout

    snapshot = FileSnapshot("module.py", True, prior, 0o600)
    _restore_snapshot(SimpleNamespace(_env=LocalEnvironment()), snapshot)

    assert target.read_bytes() == prior
    if os.name != "nt":
        assert target.stat().st_mode & 0o777 == 0o600


def test_phase_state_and_patch_fingerprint_fail_closed(tmp_path: Path) -> None:
    state_path = tmp_path / "phase-gate-state.json"
    gate = PhaseGate(
        phase="test",
        target=TARGET,
        focused_test=TEST,
        state_path=state_path,
    )
    gate.state.update(
        {
            "accepted_target_edit": True,
            "target_diff_inspected": True,
            "focused_test_executed": True,
            "focused_test_passed": True,
            "final_diff_inspected": True,
            "submission_authorized": True,
            "workflow_complete": True,
        }
    )
    patch = "diff --git a/src/module.py b/src/module.py\n+changed\n"
    gate.state["accepted_patch_fingerprint"] = patch_fingerprint(patch)
    gate._persist_state()

    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    assert authorize_phase_patch(persisted, patch).authorized
    changed = authorize_phase_patch(persisted, patch + "+later\n")
    assert not changed.authorized
    assert "final_patch_fingerprint_mismatch" in changed.failures


def test_installed_gate_rolls_back_rejected_edit_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / TARGET
    target.parent.mkdir(parents=True)
    prior = b"def add(a, b):\r\n    return a - b\r\n"
    target.write_bytes(prior)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", TARGET], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tmp_path, check=True)
    original_calls: list[str] = []

    class LocalEnvironment:
        def communicate(self, command: str, check: str) -> str:
            process = subprocess.run(
                shlex.split(command, posix=True),
                cwd=tmp_path,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            if check == "raise" and process.returncode:
                raise RuntimeError(process.stderr)
            return process.stdout

    class FakeAgent:
        def __init__(self) -> None:
            self._env = LocalEnvironment()
            self.tools = SimpleNamespace(get_state=lambda env: {"working_dir": str(tmp_path)})

        def handle_action(self, step: SimpleNamespace) -> SimpleNamespace:
            original_calls.append(step.action)
            target.write_bytes(
                target.read_bytes()
                + b"\nimport unittest\nclass TestInjected(unittest.TestCase):\n"
                + b"    def test_added(self):\n        self.assertTrue(True)\n"
            )
            step.observation = "edit command completed"
            step.execution_exit_code = 0
            step.done = False
            return step

    sweagent = ModuleType("sweagent")
    agent = ModuleType("sweagent.agent")
    agents = ModuleType("sweagent.agent.agents")
    agents.DefaultAgent = FakeAgent  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sweagent", sweagent)
    monkeypatch.setitem(sys.modules, "sweagent.agent", agent)
    monkeypatch.setitem(sys.modules, "sweagent.agent.agents", agents)
    state_path = tmp_path / "state.json"
    event_path = tmp_path / "events.jsonl"
    config = tmp_path / "phase-gate.json"
    config.write_text(
        json.dumps(
            {
                "initial_phase": "edit",
                "target": TARGET,
                "focused_test": TEST,
                "log_path": str(event_path),
                "state_path": str(state_path),
                "snapshot_python": sys.executable,
                "edit_policy": {
                    "mode": "modify_existing_source",
                    "prohibit_test_scaffolding": True,
                    "require_existing_content_change": True,
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CGR_PHASE_GATE_CONFIG", str(config))
    install_sweagent_phase_gate()

    step = SimpleNamespace(
        action=f"printf scaffold >> {TARGET}", observation="", state=None, done=False
    )
    result = FakeAgent().handle_action(step)

    assert original_calls == [step.action]
    assert target.read_bytes() == prior
    assert "rejected and rolled back" in result.observation
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["current_phase"] == "edit"
    assert state["rollback_count"] == 1
    assert "test_scaffolding_in_production_source" in state["last_postcondition_failures"]
    event = json.loads(event_path.read_text(encoding="utf-8").splitlines()[-1])
    assert event["executed"] is True
    assert event["accepted"] is False
    assert event["rolled_back"] is True
    assert event["execution_exit_code"] == 0
    assert event["target_changed"] is True
    assert event["postcondition_outcome"] == "rejected"
    assert event["parsed_executable"] == "printf"
    assert event["true_write_targets"] == [TARGET]


def test_nonzero_single_target_edit_rolls_back_changed_bytes_and_stays_in_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prior = b"def transform(value):\n    return value.strip()\n"
    target = _initialize_git_target(tmp_path, TARGET, prior)
    original_calls: list[str] = []

    class FakeAgent:
        def __init__(self) -> None:
            self._env = _LocalPhaseEnvironment(tmp_path)
            self.tools = SimpleNamespace(get_state=lambda env: {"working_dir": str(tmp_path)})

        def handle_action(self, step: SimpleNamespace) -> SimpleNamespace:
            original_calls.append(step.action)
            target.write_text(
                "def transform(value):\n    return value.upper()\n", encoding="utf-8"
            )
            step.observation = "sed: simulated write followed by failure"
            step.execution_exit_code = 2
            step.done = False
            return step

    sweagent = ModuleType("sweagent")
    agent = ModuleType("sweagent.agent")
    agents = ModuleType("sweagent.agent.agents")
    agents.DefaultAgent = FakeAgent  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sweagent", sweagent)
    monkeypatch.setitem(sys.modules, "sweagent.agent", agent)
    monkeypatch.setitem(sys.modules, "sweagent.agent.agents", agents)
    state_path = tmp_path / "state.json"
    event_path = tmp_path / "events.jsonl"
    config = tmp_path / "phase-gate.json"
    config.write_text(
        json.dumps(
            {
                "initial_phase": "edit",
                "target": TARGET,
                "focused_test": TEST,
                "log_path": str(event_path),
                "state_path": str(state_path),
                "snapshot_python": sys.executable,
                "edit_policy": {
                    "mode": "modify_existing_source",
                    "require_existing_content_change": True,
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CGR_PHASE_GATE_CONFIG", str(config))
    install_sweagent_phase_gate()
    action = "sed -i 's/value.strip()/value.upper()/' src/module.py"

    result = FakeAgent().handle_action(
        SimpleNamespace(action=action, observation="", state=None, done=False)
    )

    assert original_calls == [action]
    assert target.read_bytes() == prior
    assert "ACTION EXECUTED BUT DID NOT PRODUCE AN ACCEPTABLE EDIT" in result.observation
    assert "Execution exit code: 2" in result.observation
    assert "simulated write followed by failure" in result.observation
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["current_phase"] == "edit"
    assert state["accepted_target_edit"] is False
    assert state["last_edit_execution"]["execution_exit_code"] == 2
    assert state["last_edit_execution"]["target_changed"] is True
    assert state["last_edit_execution"]["rolled_back"] is True
    event = json.loads(event_path.read_text(encoding="utf-8").splitlines()[-1])
    assert event["execution_attempted"] is True
    assert event["execution_exit_code"] == 2
    assert "command_nonzero_exit" in event["postcondition_failures"]


def test_valid_generic_sed_executes_transactionally(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bash = _bash_executable()
    if bash is None:
        pytest.skip("Bash is required to execute the model-authored sed action.")
    target = _initialize_git_target(
        tmp_path, TARGET, b"def transform(value):\n    return value.strip()\n"
    )
    original_calls: list[str] = []

    class FakeAgent:
        def __init__(self) -> None:
            self._env = _LocalPhaseEnvironment(tmp_path)
            self.tools = SimpleNamespace(get_state=lambda env: {"working_dir": str(tmp_path)})

        def handle_action(self, step: SimpleNamespace) -> SimpleNamespace:
            original_calls.append(step.action)
            process = subprocess.run(
                [bash, "-lc", step.action],
                cwd=tmp_path,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            step.observation = process.stdout + process.stderr
            step.execution_exit_code = process.returncode
            step.done = False
            return step

    sweagent = ModuleType("sweagent")
    agent = ModuleType("sweagent.agent")
    agents = ModuleType("sweagent.agent.agents")
    agents.DefaultAgent = FakeAgent  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sweagent", sweagent)
    monkeypatch.setitem(sys.modules, "sweagent.agent", agent)
    monkeypatch.setitem(sys.modules, "sweagent.agent.agents", agents)
    state_path = tmp_path / "state.json"
    event_path = tmp_path / "events.jsonl"
    config = tmp_path / "phase-gate.json"
    config.write_text(
        json.dumps(
            {
                "initial_phase": "edit",
                "target": TARGET,
                "focused_test": TEST,
                "log_path": str(event_path),
                "state_path": str(state_path),
                "snapshot_python": sys.executable,
                "edit_policy": {
                    "mode": "modify_existing_source",
                    "require_existing_content_change": True,
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CGR_PHASE_GATE_CONFIG", str(config))
    install_sweagent_phase_gate()
    action = "sed -i 's/value.strip()/value.upper()/' src/module.py"

    FakeAgent().handle_action(
        SimpleNamespace(action=action, observation="", state=None, done=False)
    )

    assert original_calls == [action]
    assert "return value.upper()" in target.read_text(encoding="utf-8")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["current_phase"] == "confirm_edit"
    assert state["accepted_target_edit"] is True
    assert state["last_edit_execution"]["execution_exit_code"] == 0
    assert state["last_edit_execution"]["postcondition_outcome"] == "accepted"
    event = json.loads(event_path.read_text(encoding="utf-8").splitlines()[-1])
    assert event["execution_attempted"] is True
    assert event["target_changed"] is True
    assert event["rollback_status"] == "not_required"
