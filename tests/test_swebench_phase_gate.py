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

from cgr.swebench import phase_gate as phase_gate_module
from cgr.swebench.phase_gate import (
    EditPolicy,
    FileSnapshot,
    PhaseGate,
    RepositoryEvidence,
    TransactionalCleanupError,
    _ExecutionExitCapture,
    _normalize_observation_text,
    _probe_repository,
    _parse_shell_commands,
    _restore_snapshot,
    _snapshot_host_target,
    _target_evidence_from_snapshots,
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
ATTEMPT_008_MULTILINE_SED = (
    "sed -i '/def gcd(a, b):/a\n"
    "while b != 0:\n"
    "a, b = b, a % b' python_programs/gcd.py"
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


def test_leading_comment_is_not_an_executable_command() -> None:
    candidate = classify_candidate_action(
        "  # inspect the accepted edit\n\ngit diff -- src/module.py",
        target=TARGET,
        focused_test=TEST,
    )

    assert candidate.kind == "git_diff"
    assert candidate.parsed_command_count == 1
    assert candidate.parsed_executable == "git"
    assert candidate.parsed_argv == ("git", "diff", "--", TARGET)
    assert candidate.raw.startswith("  # inspect")


@pytest.mark.parametrize("quote", ("'", '"'))
def test_hash_inside_quotes_is_preserved(quote: str) -> None:
    action = f"sed -i {quote}s/# old/# new/{quote} {TARGET}"
    candidate = classify_candidate_action(action, target=TARGET, focused_test=TEST)

    assert candidate.kind == "noninteractive_edit"
    assert candidate.parsed_argv[2] == "s/# old/# new/"


def test_hash_in_heredoc_body_is_not_removed_as_a_comment() -> None:
    parsed = _parse_shell_commands(f"cat > {TARGET} <<'EOF'\n# retained\nEOF")

    assert len(parsed.commands) == 1
    assert parsed.commands[0].heredoc_body == "# retained"


def test_comment_only_action_fails_closed() -> None:
    candidate = classify_candidate_action(
        "  # no executable action", target=TARGET, focused_test=TEST
    )

    assert candidate.kind == "unknown"
    assert candidate.parsed_command_count == 0
    assert candidate.parsed_executable is None


@pytest.mark.parametrize(
    ("action", "assignments", "argv"),
    [
        (
            "PYTHONPATH=.git/cgr-test-runtime python -m pytest -q tests/test_module.py",
            ("PYTHONPATH=.git/cgr-test-runtime",),
            ("python", "-m", "pytest", "-q", "tests/test_module.py"),
        ),
        (
            "PYTHONPATH=.git/cgr-test-runtime MODE=focused python -m pytest -q tests/test_module.py",
            ("PYTHONPATH=.git/cgr-test-runtime", "MODE=focused"),
            ("python", "-m", "pytest", "-q", "tests/test_module.py"),
        ),
    ],
)
def test_leading_environment_assignments_preserve_executable_and_test_scope(
    action: str, assignments: tuple[str, ...], argv: tuple[str, ...]
) -> None:
    candidate = classify_candidate_action(action, target=TARGET, focused_test=TEST)

    assert candidate.kind == "focused_pytest"
    assert candidate.environment_assignments == assignments
    assert candidate.parsed_executable == "python"
    assert candidate.parsed_argv == argv


@pytest.mark.parametrize(
    "action",
    [
        "git diff -- src/module.py > gcd.patch",
        "git diff -- src/module.py >> gcd.patch",
        "git diff -- src/module.py 1> gcd.patch",
        "git diff -- src/module.py | tee gcd.patch",
        "git diff -- src/module.py | cat > gcd.patch",
        "git diff -- src/module.py; touch gcd.patch",
        "# save the diff\ngit diff -- src/module.py > gcd.patch",
        "   # save the diff\ngit diff -- src/module.py > gcd.patch",
    ],
)
def test_redirected_target_diff_is_classified_write_capable(action: str) -> None:
    candidate = classify_candidate_action(action, target=TARGET, focused_test=TEST)

    assert candidate.kind == "write_capable_inspection"
    assert candidate.write_capable is True
    assert "gcd.patch" in candidate.write_targets


def test_arbitrary_downstream_pipeline_is_write_capable_even_without_redirection() -> None:
    candidate = classify_candidate_action(
        "git diff -- src/module.py | python -c 'print(1)'",
        target=TARGET,
        focused_test=TEST,
    )

    assert candidate.kind == "write_capable_inspection"
    assert candidate.write_capable is True


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
        ("git diff -- src/module.py && git commit -am x", "commit"),
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


def test_exact_attempt_008_multiline_sed_is_one_parsed_edit_command() -> None:
    candidate = classify_candidate_action(
        ATTEMPT_008_MULTILINE_SED,
        target="python_programs/gcd.py",
        focused_test="test_gcd.py",
    )

    assert candidate.kind == "noninteractive_edit"
    assert candidate.parsed_executable == "sed"
    assert candidate.parsed_argv == (
        "sed",
        "-i",
        "/def gcd(a, b):/a\nwhile b != 0:\na, b = b, a % b",
        "python_programs/gcd.py",
    )
    assert candidate.write_targets == ("python_programs/gcd.py",)
    assert candidate.targets_required_file is True
    assert candidate.targets_unrelated_files is False
    assert candidate.command_multiline is True
    assert candidate.physical_line_count == 3
    assert candidate.parsed_command_count == 1
    assert candidate.quote_closed is True
    assert candidate.parse_status == "parsed"
    assert candidate.parse_failure_category is None


@pytest.mark.parametrize(
    ("action", "expected_program", "continued"),
    [
        (
            "sed -i 's/old/new/\ns/value/item/' src/module.py",
            "s/old/new/\ns/value/item/",
            False,
        ),
        (
            'sed -i "s/old/new/\ns/value/item/" src/module.py',
            "s/old/new/\ns/value/item/",
            False,
        ),
        (
            "sed -i 's/old/new/\\\ns/value/item/' src/module.py",
            "s/old/new/\\\ns/value/item/",
            False,
        ),
        (
            'sed -i "s/old/new/\\\ns/value/item/" src/module.py',
            "s/old/new/s/value/item/",
            True,
        ),
        (
            "sed -i \\\n's/old/new/' \\\nsrc/module.py",
            "s/old/new/",
            True,
        ),
    ],
)
def test_multiline_quote_and_continuation_tokenization(
    action: str, expected_program: str, continued: bool
) -> None:
    candidate = classify_candidate_action(action, target=TARGET, focused_test=TEST)

    assert candidate.kind == "noninteractive_edit"
    assert candidate.parsed_argv[2] == expected_program
    assert candidate.write_targets == (TARGET,)
    assert candidate.quote_closed is True
    assert candidate.continuation_detected is continued


def test_multiline_quoted_control_operators_do_not_split_commands() -> None:
    action = (
        "sed -i 's/old/new/;\ns/a|b/c&d/ && literal\n"
        "git commit; git diff; vim' src/module.py"
    )

    candidate = classify_candidate_action(action, target=TARGET, focused_test=TEST)

    assert candidate.kind == "noninteractive_edit"
    assert candidate.parsed_command_count == 1
    assert ";\n" in candidate.parsed_argv[2]
    assert "|" in candidate.parsed_argv[2]
    assert "&&" in candidate.parsed_argv[2]
    assert "git commit" in candidate.parsed_argv[2]


def test_valid_multiline_python_c_write_is_one_edit_command() -> None:
    action = (
        'python -c "from pathlib import Path\n'
        "Path('src/module.py').write_text('value = 2\\n')\""
    )

    candidate = classify_candidate_action(action, target=TARGET, focused_test=TEST)

    assert candidate.kind == "noninteractive_edit"
    assert candidate.parsed_executable == "python"
    assert candidate.parsed_command_count == 1
    assert "\n" in candidate.parsed_argv[2]
    assert candidate.write_targets == (TARGET,)


def test_genuine_multiline_commands_remain_separately_detectable() -> None:
    candidate = classify_candidate_action(
        "printf x > src/module.py\nprintf y > src/other.py",
        target=TARGET,
        focused_test=TEST,
    )

    assert candidate.kind == "edit_mixed_targets"
    assert candidate.parsed_command_count == 2
    assert candidate.write_targets == (TARGET, "src/other.py")


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

    assert candidate.kind == "shell_parse_error"
    assert candidate.write_targets == ()
    assert candidate.parse_status == "error"
    assert candidate.parse_failure_category == "unterminated_quote"
    assert candidate.quote_closed is False


def test_shell_parse_error_feedback_is_accurate_and_bounded(tmp_path: Path) -> None:
    log_path = tmp_path / "events.jsonl"
    gate = PhaseGate(
        phase="edit", target=TARGET, focused_test=TEST, log_path=log_path
    )

    decision = gate.decide(
        "sed -i 's/old/new/ src/module.py",
        RepositoryEvidence(),
        model_text="I will change the required source now.",
    )

    assert decision.allowed is False
    assert decision.candidate.kind == "shell_parse_error"
    assert "ACTION COULD NOT BE PARSED SAFELY" in str(decision.feedback)
    assert "shell quoting could not be parsed safely" in str(decision.feedback)
    assert "did not apply" not in str(decision.feedback)
    event = json.loads(log_path.read_text(encoding="utf-8").splitlines()[-1])
    assert event["executed"] is False
    assert event["parse_status"] == "error"
    assert event["parse_failure_category"] == "unterminated_quote"
    assert event["quote_closed"] is False


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
    patch = RepositoryEvidence(
        target_diff="model patch",
        tracked_diff="model patch",
        status_porcelain=f" M {TARGET}",
    )

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
    before = _snapshot("def transform(value):\n    return 'old expression'\n")
    after = _snapshot("def transform(value):\n    return 'new expression'\n")
    gate.record_execution(
        edit,
        observation="",
        evidence=patch,
        pre_action_fingerprint=before.fingerprint,
        post_action_fingerprint=after.fingerprint,
    )

    confirmation = gate.decide(f"cat {TARGET}", patch)
    gate.record_execution(
        confirmation,
        observation="updated source",
        evidence=patch,
        current_target_snapshot=after,
    )
    focused = gate.decide(f"python -m pytest -q {TEST}", patch)
    gate.record_execution(focused, observation="1 passed in 0.01s", evidence=patch)
    final_diff = gate.decide("git diff -- HEAD", patch)
    gate.record_execution(final_diff, observation="model patch", evidence=patch)
    _mark_accepted_transaction(gate)
    submission = gate.decide("submit", patch, current_target_snapshot=after)
    gate.record_execution(
        submission,
        observation="submitted",
        evidence=patch,
        submitted_patch=patch.tracked_diff,
    )

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
    patch = RepositoryEvidence(
        target_diff="generic patch",
        tracked_diff="generic patch",
        status_porcelain=f" M {TARGET}",
    )

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
        pre_action_fingerprint=_snapshot(
            "def transform(value):\n    return value.strip()\n"
        ).fingerprint,
        post_action_fingerprint=_snapshot(
            "def transform(value):\n    return value.upper()\n"
        ).fingerprint,
        diff_evidence=evaluation.evidence,
        execution_exit_code=0,
    )
    changed = _snapshot("def transform(value):\n    return value.upper()\n")
    confirmation = gate.decide(f"cat {TARGET}", patch)
    gate.record_execution(
        confirmation,
        observation="generic changed source",
        evidence=patch,
        current_target_snapshot=changed,
    )
    focused = gate.decide(f"python -m pytest -q {TEST}", patch)
    gate.record_execution(focused, observation="1 passed in 0.01s", evidence=patch)
    final_diff = gate.decide("git diff -- HEAD", patch)
    gate.record_execution(final_diff, observation="generic patch", evidence=patch)
    _mark_accepted_transaction(gate)
    submission = gate.decide("submit", patch, current_target_snapshot=changed)
    gate.record_execution(
        submission,
        observation="submitted",
        evidence=patch,
        submitted_patch=patch.tracked_diff,
    )

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


def test_confirm_edit_requires_one_integrity_bound_read_only_inspection() -> None:
    evidence = RepositoryEvidence(
        target_diff="diff --git a/src/module.py b/src/module.py",
        tracked_diff="diff --git a/src/module.py b/src/module.py",
    )
    gate = PhaseGate(phase="confirm_edit", target=TARGET, focused_test=TEST)
    before = _snapshot("def value():\n    return 1\n")
    after = _snapshot("def value():\n    return 2\n")
    gate.state.update(
        {
            "accepted_target_edit": True,
            "accepted_target_fingerprint": after.fingerprint,
            "pre_edit_target_fingerprint": before.fingerprint,
            "accepted_patch_fingerprint": patch_fingerprint(evidence.tracked_diff),
        }
    )

    rejected_test = gate.decide("pytest -q tests/test_module.py", evidence)
    assert not rejected_test.allowed

    inspection = gate.decide("cat src/module.py", evidence)
    assert inspection.allowed
    gate.record_execution(
        inspection,
        observation="changed",
        evidence=evidence,
        current_target_snapshot=after,
    )
    assert gate.phase == "test"


def _confirmation_gate(
    *, action_phase: str = "confirm_edit",
) -> tuple[PhaseGate, RepositoryEvidence, FileSnapshot, FileSnapshot]:
    evidence = RepositoryEvidence(target_diff="canonical patch", tracked_diff="canonical patch")
    before = _snapshot("def value():\n    return 1\n")
    after = _snapshot("def value():\n    return 2\n")
    gate = PhaseGate(phase=action_phase, target=TARGET, focused_test=TEST)
    gate.state.update(
        {
            "accepted_target_edit": True,
            "accepted_target_fingerprint": after.fingerprint,
            "pre_edit_target_fingerprint": before.fingerprint,
            "accepted_patch_fingerprint": patch_fingerprint(evidence.tracked_diff),
        }
    )
    return gate, evidence, before, after


def _mark_accepted_transaction(gate: PhaseGate) -> None:
    gate.state["last_transaction"] = {
        "status": "accepted",
        "transaction_closed": True,
        "failure_kind": None,
    }
    gate.state["last_transaction_failure_kind"] = None
    gate.state["transactional_cleanup_verified"] = True


def _submission_gate(
    *, log_path: Path | None = None, submission_command: str = "submit"
) -> tuple[PhaseGate, RepositoryEvidence, FileSnapshot, FileSnapshot]:
    before = _snapshot("def value():\n    return 1\n")
    after = _snapshot("def value():\n    return 2\n")
    evidence = RepositoryEvidence(
        target_diff="canonical patch",
        tracked_diff="canonical patch",
        status_porcelain=f" M {TARGET}",
    )
    fingerprint = patch_fingerprint(evidence.tracked_diff)
    gate = PhaseGate(
        phase="submit",
        target=TARGET,
        focused_test=TEST,
        submission_command=submission_command,
        log_path=log_path,
    )
    gate.state.update(
        {
            "accepted_target_edit": True,
            "accepted_target_fingerprint": after.fingerprint,
            "pre_edit_target_fingerprint": before.fingerprint,
            "target_confirmed_after_edit": True,
            "confirmation_matches_accepted_edit": True,
            "confirmation_diff_nonempty": True,
            "target_diff_inspected": True,
            "focused_test_executed": True,
            "focused_test_passed": True,
            "final_diff_inspected": True,
            "accepted_patch_fingerprint": fingerprint,
            "confirmed_patch_fingerprint": fingerprint,
            "final_patch_fingerprint": fingerprint,
        }
    )
    _mark_accepted_transaction(gate)
    return gate, evidence, before, after


@pytest.mark.parametrize(
    ("action", "kind"),
    [
        (f"git diff -- {TARGET}", "git_diff"),
        (f"cat {TARGET}", "target_confirmation"),
        (f"sed -n '1,80p' {TARGET}", "target_confirmation"),
    ],
)
def test_one_read_only_target_inspection_confirms_exact_accepted_bytes(
    action: str, kind: str
) -> None:
    gate, evidence, _before, after = _confirmation_gate()
    decision = gate.decide(action, evidence)

    feedback = gate.record_execution(
        decision,
        observation="current target",
        evidence=evidence,
        current_target_snapshot=after,
    )

    assert decision.allowed and decision.candidate.kind == kind
    assert feedback is None
    assert gate.phase == "test"
    assert gate.state["target_confirmed_after_edit"] is True
    assert gate.state["confirmation_action_kind"] == kind
    assert gate.state["confirmation_target_fingerprint"] == after.fingerprint
    assert gate.state["confirmation_matches_accepted_edit"] is True
    assert gate.state["confirmation_diff_nonempty"] is True
    assert gate.state["confirmed_patch_fingerprint"] == gate.state[
        "accepted_patch_fingerprint"
    ]


@pytest.mark.parametrize("current", ("stale", "reverted"))
def test_stale_or_reverted_target_cannot_confirm(current: str) -> None:
    gate, evidence, before, after = _confirmation_gate()
    snapshot = (
        before
        if current == "reverted"
        else _snapshot("def value():\n    return 3\n")
    )
    decision = gate.decide(f"git diff -- {TARGET}", evidence)

    feedback = gate.record_execution(
        decision,
        observation="stale target",
        evidence=evidence,
        current_target_snapshot=snapshot,
    )

    assert after.fingerprint != snapshot.fingerprint
    assert gate.phase == "edit"
    assert gate.state["target_confirmed_after_edit"] is False
    assert gate.state["confirmation_matches_accepted_edit"] is False
    assert gate.state["accepted_target_edit"] is False
    assert "TARGET INTEGRITY CONFIRMATION FAILED" in str(feedback)


@pytest.mark.parametrize("phase", ("inspect", "confirm_edit", "final_diff"))
@pytest.mark.parametrize(
    "action",
    (
        f"git diff -- {TARGET} > patchfile",
        f"cat {TARGET} | tee patchfile",
        "rm patchfile",
        "git apply change.patch",
    ),
)
def test_inspection_phases_reject_write_capability_before_execution(
    phase: str, action: str
) -> None:
    gate = PhaseGate(phase=phase, target=TARGET, focused_test=TEST)
    decision = gate.decide(action, RepositoryEvidence(target_diff="patch", tracked_diff="patch"))

    assert decision.allowed is False
    assert decision.candidate.write_capable is True
    assert gate.phase == phase
    assert "must be read-only" in str(decision.feedback)


def test_final_diff_read_only_target_diff_preserves_lineage() -> None:
    gate, evidence, _before, _after = _confirmation_gate(action_phase="final_diff")
    fingerprint = patch_fingerprint(evidence.tracked_diff)
    gate.state["confirmed_patch_fingerprint"] = fingerprint
    decision = gate.decide(f"git diff -- {TARGET}", evidence)

    feedback = gate.record_execution(decision, observation="patch", evidence=evidence)

    assert feedback is None
    assert gate.phase == "submit"
    assert gate.state["final_diff_inspected"] is True
    assert gate.state["final_patch_fingerprint"] == fingerprint


def test_phase_transition_guidance_is_configuration_derived() -> None:
    command = "PYTHONPATH=.git/cgr-test-runtime python -m pytest -q tests/test_module.py"
    gate = PhaseGate(
        phase="test",
        target=TARGET,
        focused_test=TEST,
        focused_test_command=command,
    )

    test_guidance = gate.phase_transition_guidance()
    assert "Current phase: test" in test_guidance
    assert command in test_guidance
    gate.phase = "final_diff"
    assert f"git diff -- {TARGET}" in gate.phase_transition_guidance()
    gate.phase = "submit"
    submit_guidance = gate.phase_transition_guidance()
    assert "Return exactly this single action:\nsubmit" in submit_guidance


def test_redirected_confirmation_diff_is_logged_unexecuted_and_creates_nothing(
    tmp_path: Path,
) -> None:
    target = tmp_path / TARGET
    target.parent.mkdir(parents=True)
    accepted = b"def value():\n    return 2\n"
    target.write_bytes(accepted)
    log_path = tmp_path / "events.jsonl"
    gate, evidence, _before, _after = _confirmation_gate()
    gate.log_path = log_path
    accepted_patch_fingerprint = gate.state["accepted_patch_fingerprint"]

    decision = gate.decide(
        f"# save evidence\ngit diff -- {TARGET} > gcd.patch", evidence
    )

    assert decision.allowed is False
    assert decision.candidate.kind == "write_capable_inspection"
    assert decision.candidate.parsed_command_count == 1
    assert gate.phase == "confirm_edit"
    assert gate.state["target_confirmed_after_edit"] is False
    assert gate.state["accepted_patch_fingerprint"] == accepted_patch_fingerprint
    assert target.read_bytes() == accepted
    assert not (tmp_path / "gcd.patch").exists()
    event = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert event["executed"] is False
    assert event["candidate"]["write_capable"] is True
    assert "must be read-only" in str(decision.feedback)


def test_test_pass_final_diff_and_submission_transitions() -> None:
    evidence = RepositoryEvidence(
        target_diff="patch", tracked_diff="patch", status_porcelain=f" M {TARGET}"
    )
    gate = PhaseGate(phase="test", target=TARGET, focused_test=TEST)
    before = _snapshot("def value():\n    return 1\n")
    after = _snapshot("def value():\n    return 2\n")
    fingerprint = patch_fingerprint(evidence.tracked_diff)
    gate.state["accepted_patch_fingerprint"] = fingerprint
    gate.state["confirmed_patch_fingerprint"] = fingerprint
    gate.state["accepted_target_edit"] = True
    gate.state["target_confirmed_after_edit"] = True
    gate.state["confirmation_matches_accepted_edit"] = True
    gate.state["accepted_target_fingerprint"] = after.fingerprint
    gate.state["pre_edit_target_fingerprint"] = before.fingerprint
    _mark_accepted_transaction(gate)

    test = gate.decide("pytest -q tests/test_module.py", evidence)
    assert test.allowed
    gate.record_execution(test, observation="1 passed in 0.01s", evidence=evidence)
    assert gate.phase == "final_diff"

    diff = gate.decide("git diff -- src/module.py", evidence)
    assert diff.allowed
    gate.record_execution(diff, observation="patch", evidence=evidence)
    assert gate.phase == "submit"
    assert gate.decide(
        "submit", evidence, current_target_snapshot=after
    ).allowed


def test_submission_without_patch_and_unknown_action_are_rejected() -> None:
    submit = PhaseGate(phase="submit", target=TARGET, focused_test=TEST)
    unknown = PhaseGate(phase="edit", target=TARGET, focused_test=TEST)

    assert not submit.decide("submit", RepositoryEvidence()).allowed
    assert not unknown.decide("frobnicate", RepositoryEvidence()).allowed


@pytest.mark.parametrize(
    ("action", "expected_kind"),
    [
        ("submit", "submission"),
        ("# Submit the verified patch\n\nsubmit", "submission"),
        ("submit --force", "unknown"),
        ("submit extra", "unknown"),
        ("submit; git status", "unknown"),
        ("submit && git diff", "git_diff"),
        ("submit > patchfile", "edit_wrong_file"),
        ("submit | tee patchfile", "edit_wrong_file"),
        ('"submit"', "unknown"),
        ("echo 'submit'", "unknown"),
    ],
)
def test_submission_action_classification_is_exact(
    action: str, expected_kind: str
) -> None:
    candidate = classify_candidate_action(
        action, target=TARGET, focused_test=TEST, submission_command="submit"
    )

    assert candidate.kind == expected_kind
    if expected_kind == "submission":
        assert candidate.parsed_command_count == 1
        assert candidate.parsed_executable == "submit"


def test_submission_command_and_guidance_are_configuration_derived() -> None:
    gate, evidence, _before, after = _submission_gate(submission_command="finish")

    decision = gate.decide("finish", evidence, current_target_snapshot=after)

    assert decision.allowed
    assert decision.candidate.kind == "submission"
    assert "Return exactly this single action:\nfinish" in gate.phase_transition_guidance()
    assert classify_candidate_action(
        "submit", target=TARGET, focused_test=TEST, submission_command="finish"
    ).kind == "unknown"


@pytest.mark.parametrize(
    "action",
    [
        f"git add {TARGET}",
        "git commit -m fix",
        "git format-patch HEAD^",
        f"git diff -- {TARGET}",
        f"git diff -- {TARGET} > patchfile",
        "submit > patchfile",
        "submit | tee patchfile",
        "submit; git status",
        "submit --force",
        "submit && git diff",
    ],
)
def test_submit_phase_rejections_name_the_only_permitted_action(action: str) -> None:
    gate, evidence, _before, after = _submission_gate()

    decision = gate.decide(action, evidence, current_target_snapshot=after)

    assert not decision.allowed
    assert gate.phase == "submit"
    assert gate.state["submission_authorized"] is False
    assert gate.state["workflow_complete"] is False
    assert "Required phase: submit" in str(decision.feedback)
    assert "Return exactly this single action:\nsubmit" in str(decision.feedback)
    assert "current inspect phase" not in str(decision.feedback)


@pytest.mark.parametrize(
    ("mutation", "failure"),
    [
        ("focused_test_executed", "focused_test_not_executed"),
        ("focused_test_passed", "focused_test_not_passed"),
        ("final_diff_inspected", "final_diff_not_inspected"),
        ("target_confirmed_after_edit", "target_confirmation_missing"),
    ],
)
def test_submission_preconditions_reject_incomplete_workflow(
    mutation: str, failure: str
) -> None:
    gate, evidence, _before, after = _submission_gate()
    gate.state[mutation] = False

    decision = gate.decide("submit", evidence, current_target_snapshot=after)

    assert not decision.allowed
    assert failure in gate.state["last_submission_failures"]
    assert "Return exactly this single action:\nsubmit" in str(decision.feedback)


@pytest.mark.parametrize("phase", ("inspect", "edit", "confirm_edit", "test", "final_diff"))
def test_premature_submission_never_executes_or_authorizes(phase: str) -> None:
    gate = PhaseGate(phase=phase, target=TARGET, focused_test=TEST)

    decision = gate.decide("submit", RepositoryEvidence())

    assert not decision.allowed
    assert decision.candidate.kind == "submission"
    assert gate.state["explicit_submission_action_seen"] is False
    assert gate.state["submission_authorized"] is False
    assert gate.state["workflow_complete"] is False


@pytest.mark.parametrize(
    ("state_update", "failure"),
    [
        ({"last_transaction": None}, "accepted_transaction_missing"),
        (
            {
                "last_transaction": {
                    "status": "started",
                    "transaction_closed": False,
                    "failure_kind": None,
                }
            },
            "accepted_transaction_not_closed",
        ),
        ({"transactional_cleanup_verified": False}, "transactional_cleanup_unverified"),
        ({"last_transaction_failure_kind": "timeout"}, "transaction_failure_unresolved"),
    ],
)
def test_submission_requires_closed_clean_transaction(
    state_update: dict[str, object], failure: str
) -> None:
    gate, evidence, _before, after = _submission_gate()
    gate.state.update(state_update)

    decision = gate.decide("submit", evidence, current_target_snapshot=after)

    assert not decision.allowed
    assert failure in gate.state["last_submission_failures"]


def test_submission_rejects_changed_target_bytes() -> None:
    gate, evidence, _before, _after = _submission_gate()
    changed = _snapshot("def value():\n    return 3\n")

    decision = gate.decide("submit", evidence, current_target_snapshot=changed)

    assert not decision.allowed
    assert "current_target_fingerprint_mismatch" in gate.state[
        "last_submission_failures"
    ]


@pytest.mark.parametrize(
    "status",
    (f" M {TARGET}\n?? patchfile", f" M {TARGET}\n M src/unrelated.py"),
)
def test_submission_rejects_unrelated_tracked_or_untracked_files(status: str) -> None:
    gate, evidence, _before, after = _submission_gate()
    evidence.status_porcelain = status

    decision = gate.decide("submit", evidence, current_target_snapshot=after)

    assert not decision.allowed
    assert "unrelated_workspace_changes" in gate.state["last_submission_failures"]


def test_submission_rejects_canonical_fingerprint_mismatch() -> None:
    gate, evidence, _before, after = _submission_gate()
    mismatched = RepositoryEvidence(
        target_diff=evidence.target_diff,
        tracked_diff=evidence.tracked_diff + "\n+unrelated\n",
        status_porcelain=evidence.status_porcelain,
    )

    decision = gate.decide("submit", mismatched, current_target_snapshot=after)

    assert not decision.allowed
    assert "canonical_patch_fingerprint_mismatch" in gate.state[
        "last_submission_failures"
    ]


def test_explicit_submission_records_matching_fingerprint_and_event(
    tmp_path: Path,
) -> None:
    log_path = tmp_path / "submission-events.jsonl"
    gate, evidence, _before, after = _submission_gate(log_path=log_path)
    decision = gate.decide("submit", evidence, current_target_snapshot=after)

    feedback = gate.record_execution(
        decision,
        observation=evidence.tracked_diff,
        evidence=evidence,
        accepted=True,
        submitted_patch=evidence.tracked_diff,
    )

    assert feedback is None
    assert gate.state["explicit_submission_action_seen"] is True
    assert gate.state["submission_authorized"] is True
    assert gate.state["workflow_complete"] is True
    assert gate.state["completion_status"] == "completed_explicit_submission"
    assert gate.state["submitted_patch_fingerprint"] == gate.state[
        "final_patch_fingerprint"
    ]
    assert authorize_phase_patch(gate.state, evidence.tracked_diff).authorized
    event = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert event["candidate"]["raw"] == "submit"
    assert event["explicit_submission_action_seen"] is True
    assert event["submitted_patch_fingerprint"] == gate.state[
        "submitted_patch_fingerprint"
    ]


def test_official_submission_patch_mismatch_remains_unauthorized() -> None:
    gate, evidence, _before, after = _submission_gate()
    decision = gate.decide("submit", evidence, current_target_snapshot=after)

    feedback = gate.record_execution(
        decision,
        observation="different patch",
        evidence=evidence,
        accepted=True,
        submitted_patch="different patch",
    )

    assert gate.state["explicit_submission_action_seen"] is True
    assert gate.state["submission_authorized"] is False
    assert gate.state["workflow_complete"] is False
    assert "submitted_patch_fingerprint_mismatch" in str(feedback)


@pytest.mark.parametrize("terminal_reason", ("exit_cost", "attempt_timeout"))
def test_autosubmission_without_explicit_model_action_remains_unauthorized(
    terminal_reason: str,
) -> None:
    gate, evidence, _before, _after = _submission_gate()
    gate.state["terminal_reason"] = terminal_reason

    authorization = authorize_phase_patch(gate.state, evidence.tracked_diff)

    assert gate.state["explicit_submission_action_seen"] is False
    assert gate.state["submission_authorized"] is False
    assert gate.state["workflow_complete"] is False
    assert not authorization.authorized
    assert "explicit_submission_action_missing" in authorization.failures


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
            if "status --porcelain" in command:
                return SimpleNamespace(output=f" M {TARGET}")
            return {"output": "target patch"}

    evidence = _probe_repository(SimpleNamespace(_env=FakeEnvironment()), TARGET)

    assert evidence == RepositoryEvidence(
        target_diff="target patch",
        tracked_diff="tracked patch",
        status_porcelain=f" M {TARGET}",
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


def test_transaction_journal_is_durable_before_execution_with_exact_snapshot(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "phase-state.json"
    gate = PhaseGate(
        phase="edit", target=TARGET, focused_test=TEST, state_path=state_path
    )
    decision = gate.decide(
        "sed -i 's/old/new/' src/module.py", RepositoryEvidence()
    )
    before = FileSnapshot(TARGET, True, b"exact\x00bytes\r\n", 0o600)

    transaction = gate.begin_transaction(decision, before)

    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    assert persisted["active_transaction_status"] == "started"
    assert persisted["transactional_cleanup_verified"] is False
    assert persisted["active_transaction"]["transaction_id"] == transaction["transaction_id"]
    snapshot_path = Path(transaction["snapshot_path"])
    snapshot_payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert snapshot_payload["fingerprint"] == before.fingerprint
    assert snapshot_payload["mode"] == 0o600
    assert snapshot_payload["content_base64"] == "ZXhhY3QAYnl0ZXMNCg=="


def test_host_snapshot_comparison_detects_change_noop_and_builds_bounded_diff(
    tmp_path: Path,
) -> None:
    target = tmp_path / TARGET
    target.parent.mkdir(parents=True)
    target.write_text("def value():\n    return 'old'\n", encoding="utf-8")
    before = _snapshot_host_target(tmp_path, TARGET)
    target.write_text("def value():\n    return 'new'\n", encoding="utf-8")
    after = _snapshot_host_target(tmp_path, TARGET)

    changed = _target_evidence_from_snapshots(before, after, limit=200)
    noop = _target_evidence_from_snapshots(after, after, limit=200)

    assert changed.target_diff.startswith(f"--- a/{TARGET}")
    assert "+    return 'new'" in changed.target_diff
    assert len(changed.target_diff) <= 240
    assert noop == RepositoryEvidence()


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


def _install_host_transaction_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    agent_type: type,
    *,
    initial_phase: str = "edit",
    target: str = TARGET,
    focused_test: str = TEST,
    focused_test_command: str | None = None,
) -> tuple[Path, Path]:
    sweagent = ModuleType("sweagent")
    agent = ModuleType("sweagent.agent")
    agents = ModuleType("sweagent.agent.agents")
    agents.DefaultAgent = agent_type  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sweagent", sweagent)
    monkeypatch.setitem(sys.modules, "sweagent.agent", agent)
    monkeypatch.setitem(sys.modules, "sweagent.agent.agents", agents)
    gate_root = tmp_path / ".git" / "cgr-phase-gate"
    gate_root.mkdir(parents=True, exist_ok=True)
    state_path = gate_root / "state.json"
    event_path = gate_root / "events.jsonl"
    config = gate_root / "phase-gate.json"
    config.write_text(
        json.dumps(
            {
                "initial_phase": initial_phase,
                "target": target,
                "focused_test": focused_test,
                "focused_test_command": focused_test_command or focused_test,
                "log_path": str(event_path),
                "state_path": str(state_path),
                "snapshot_python": sys.executable,
                "host_workspace_root": str(tmp_path),
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
    return state_path, event_path


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
            "target_confirmed_after_edit": True,
            "target_diff_inspected": True,
            "focused_test_executed": True,
            "focused_test_passed": True,
            "final_diff_inspected": True,
            "submission_authorized": True,
            "workflow_complete": True,
            "explicit_submission_action_seen": True,
        }
    )
    patch = "diff --git a/src/module.py b/src/module.py\n+changed\n"
    gate.state["accepted_patch_fingerprint"] = patch_fingerprint(patch)
    gate.state["confirmed_patch_fingerprint"] = patch_fingerprint(patch)
    gate.state["final_patch_fingerprint"] = patch_fingerprint(patch)
    gate.state["submitted_patch_fingerprint"] = patch_fingerprint(patch)
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
    assert state["active_transaction"] is None
    assert state["active_transaction_status"] == "rejected"
    assert state["transactional_cleanup_verified"] is True
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
    assert state["active_transaction"] is None
    assert state["active_transaction_status"] == "accepted"
    assert state["transactional_cleanup_verified"] is True
    assert state["last_edit_execution"]["execution_exit_code"] == 0
    assert state["last_edit_execution"]["postcondition_outcome"] == "accepted"
    event = json.loads(event_path.read_text(encoding="utf-8").splitlines()[-1])
    assert event["execution_attempted"] is True
    assert event["target_changed"] is True
    assert event["rollback_status"] == "not_required"


def test_exact_run_015_shape_completes_full_authorized_workflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _initialize_git_target(
        tmp_path, TARGET, b"def transform(value):\n    return value.strip()\n"
    )

    class FakeAgent:
        def __init__(self) -> None:
            self._env = _LocalPhaseEnvironment(tmp_path)
            self.tools = SimpleNamespace(
                get_state=lambda env: {"working_dir": str(tmp_path)}
            )

        def handle_action(self, step: SimpleNamespace) -> SimpleNamespace:
            if "sed -i" in step.action:
                target.write_text(
                    "def transform(value):\n    return value.upper()\n",
                    encoding="utf-8",
                )
                step.observation = "edit complete"
                step.execution_exit_code = 0
            elif "pytest" in step.action:
                step.observation = "1 passed in 0.01s"
            elif step.action.strip() == "submit":
                submitted = self._env.communicate(
                    "git diff --binary HEAD --", check="ignore"
                )
                step.observation = submitted
                step.submission = submitted
                step.exit_status = "submitted"
                step.done = True
            elif "cat " in step.action:
                step.observation = target.read_text(encoding="utf-8")
            else:
                step.observation = self._env.communicate(
                    f"git diff -- {TARGET}", check="ignore"
                )
            return step

    state_path, event_path = _install_host_transaction_gate(
        tmp_path,
        monkeypatch,
        FakeAgent,
        initial_phase="edit",
        focused_test_command=(
            "PYTHONPATH=.git/cgr-test-runtime python -m pytest -q tests/test_module.py"
        ),
    )
    fake = FakeAgent()

    rejected = fake.handle_action(
        SimpleNamespace(
            action=f"python -m pytest -q {TEST}",
            observation="",
            state=None,
            done=False,
            thought="",
            output="",
        )
    )
    assert "ACTION REJECTED BY CGR" in rejected.observation
    actions = (
        f"# Correct the implementation\nsed -i 's/value.strip()/value.upper()/' {TARGET}",
        f"# Verify the accepted edit\ngit diff -- {TARGET}",
        f"# Run the focused verifier\nPYTHONPATH=.git/cgr-test-runtime python -m pytest -q {TEST}",
        f"# Inspect the final canonical patch\ngit diff -- {TARGET}",
        "submit",
    )
    results = []
    for action in actions:
        results.append(
            fake.handle_action(
                SimpleNamespace(
                    action=action,
                    observation="",
                    state=None,
                    done=False,
                    thought="",
                    output="",
                )
            )
        )

    patch = fake._env.communicate("git diff --binary HEAD --", check="ignore")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["last_transaction"]["status"] == "accepted"
    assert state["last_transaction"]["transaction_closed"] is True
    assert state["transactional_cleanup_verified"] is True
    assert state["workflow_complete"] is True
    assert state["submission_authorized"] is True
    assert state["explicit_submission_action_seen"] is True
    assert state["completion_status"] == "completed_explicit_submission"
    assert state["target_confirmed_after_edit"] is True
    assert state["confirmation_matches_accepted_edit"] is True
    assert state["confirmation_diff_nonempty"] is True
    authorization = authorize_phase_patch(state, patch)
    assert authorization.authorized is True
    assert authorization.fingerprint == state["accepted_patch_fingerprint"]
    events = [json.loads(line) for line in event_path.read_text().splitlines()]
    assert [event["phase_before"] for event in events] == [
        "edit",
        "edit",
        "confirm_edit",
        "test",
        "final_diff",
        "submit",
    ]
    executed = [event for event in events if event["executed"]]
    assert executed[0]["parsed_executable"] == "sed"
    assert executed[0]["parsed_command_count"] == 1
    assert executed[1]["parsed_executable"] == "git"
    assert executed[1]["parsed_command_count"] == 1
    assert executed[2]["parsed_executable"] == "python"
    assert executed[2]["environment_assignments"] == [
        "PYTHONPATH=.git/cgr-test-runtime"
    ]
    fingerprints = {
        state["accepted_patch_fingerprint"],
        state["confirmed_patch_fingerprint"],
        state["final_patch_fingerprint"],
        state["submitted_patch_fingerprint"],
    }
    assert len(fingerprints) == 1
    assert not (tmp_path / "gcd.patch").exists()
    assert "Current phase: confirm_edit" in results[0].observation
    assert "Current phase: test" in results[1].observation
    assert "PYTHONPATH=.git/cgr-test-runtime" in results[1].observation
    assert "Current phase: final_diff" in results[2].observation
    assert "Current phase: submit" in results[3].observation
    assert "Return exactly this single action:\nsubmit" in results[3].observation
    outer = subprocess.run(
        [
            sys.executable,
            "-c",
            "from src.module import transform; assert transform(' a ') == ' A '",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert outer.returncode == 0, outer.stderr
    assert len(actions) + 1 == 6
    assert len(actions) + 1 <= 8
    classification = (
        "resolved"
        if authorization.authorized and outer.returncode == 0
        else "phase_incomplete"
    )
    assert classification == "resolved"


def test_attempt_007_internal_postinspection_timeout_is_contained_and_restored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class CommandTimeoutError(RuntimeError):
        pass

    target_name = "python_programs/gcd.py"
    prior = b"def placeholder(value):\n    return value\n"
    target = _initialize_git_target(tmp_path, target_name, prior)
    if os.name != "nt":
        os.chmod(target, 0o600)
    prior_mode = target.stat().st_mode & 0o777
    original_calls: list[str] = []
    journal_seen_before_edit: list[bool] = []

    class FakeAgent:
        def __init__(self) -> None:
            self._env = _LocalPhaseEnvironment(tmp_path)
            self.tools = SimpleNamespace(get_state=lambda env: {"working_dir": str(tmp_path)})
            self._n_consecutive_timeouts = 0

        def handle_action(self, step: SimpleNamespace) -> SimpleNamespace:
            original_calls.append(step.action)
            if step.action.startswith("sed "):
                state = json.loads(state_path.read_text(encoding="utf-8"))
                journal_seen_before_edit.append(
                    state["active_transaction_status"] == "started"
                )
                target.write_text("def broken(:\n", encoding="utf-8")
                step.observation = "model edit returned"
                step.execution_exit_code = 0
            else:
                step.observation = prior.decode()
            step.done = False
            return step

    state_path, event_path = _install_host_transaction_gate(
        tmp_path,
        monkeypatch,
        FakeAgent,
        initial_phase="inspect",
        target=target_name,
        focused_test="test_target.py",
    )

    def timeout_after_snapshot(*_args: object, **_kwargs: object) -> RepositoryEvidence:
        raise CommandTimeoutError("legacy git diff equivalent timed out")

    monkeypatch.setattr(
        phase_gate_module, "_target_evidence_from_snapshots", timeout_after_snapshot
    )
    fake = FakeAgent()
    rejected_commit = fake.handle_action(
        SimpleNamespace(
            action="git commit -am fix",
            observation="",
            state=None,
            done=False,
            thought="",
            output="",
        )
    )
    assert "ACTION REJECTED BY CGR" in rejected_commit.observation
    fake.handle_action(
        SimpleNamespace(
            action=f"cat {target_name}",
            observation="",
            state=None,
            done=False,
            thought="",
            output="",
        )
    )

    result = fake.handle_action(
        SimpleNamespace(
            action=EC2_SED_ACTION,
            observation="",
            state=None,
            done=False,
            thought="",
            output="",
        )
    )

    assert journal_seen_before_edit == [True]
    assert original_calls == [f"cat {target_name}", EC2_SED_ACTION]
    assert fake._n_consecutive_timeouts == 0
    assert result.done is False
    assert "internal inspection operation timed out" in result.observation
    assert "restored and verified" in result.observation
    assert target.read_bytes() == prior
    assert target.stat().st_mode & 0o777 == prior_mode
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["current_phase"] == "edit"
    assert state["active_transaction"] is None
    assert state["active_transaction_status"] == "rejected"
    assert state["last_transaction_failure_kind"] == "cgr_postinspection_timeout"
    assert state["transactional_cleanup_verified"] is True
    assert state["last_transaction"]["timeout_owner"] == "cgr_internal_inspection"
    assert state["last_transaction"]["model_action_completed_before_timeout"] is True
    assert state["last_transaction"]["rollback_verified"] is True
    events = [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines()]
    edit_event = events[-1]
    assert edit_event["execution_attempted"] is True
    assert edit_event["transaction"]["validation_completed"] is False
    assert edit_event["transaction"]["rollback_verified"] is True
    assert edit_event["transaction"]["transaction_closed"] is True
    assert not authorize_phase_patch(state, "").authorized


def test_attempt_008_multiline_edit_executes_and_rolls_back_normally(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target_name = "python_programs/gcd.py"
    prior = b"def placeholder(value):\n    return value\n"
    target = _initialize_git_target(tmp_path, target_name, prior)
    if os.name != "nt":
        os.chmod(target, 0o600)
    prior_mode = target.stat().st_mode & 0o777
    executed: list[str] = []

    class FakeAgent:
        def __init__(self) -> None:
            self._env = _LocalPhaseEnvironment(tmp_path)
            self.tools = SimpleNamespace(
                get_state=lambda env: {"working_dir": str(tmp_path)}
            )

        def handle_action(self, step: SimpleNamespace) -> SimpleNamespace:
            executed.append(step.action)
            if step.action.startswith("sed "):
                target.write_text("def broken(:\n", encoding="utf-8")
                step.observation = "sed completed"
                step.execution_exit_code = 0
            else:
                step.observation = target.read_text(encoding="utf-8")
            step.done = False
            return step

    state_path, event_path = _install_host_transaction_gate(
        tmp_path,
        monkeypatch,
        FakeAgent,
        initial_phase="inspect",
        target=target_name,
        focused_test="test_target.py",
    )
    fake = FakeAgent()
    commit = fake.handle_action(
        SimpleNamespace(
            action="git commit -am fix",
            observation="",
            state=None,
            done=False,
            thought="",
            output="",
        )
    )
    assert "ACTION REJECTED BY CGR" in commit.observation
    fake.handle_action(
        SimpleNamespace(
            action=f"cat {target_name}",
            observation="",
            state=None,
            done=False,
            thought="",
            output="",
        )
    )

    first = fake.handle_action(
        SimpleNamespace(
            action=f"sed -i 's/return value/return (/' {target_name}",
            observation="",
            state=None,
            done=False,
            thought="",
            output="",
        )
    )
    second = fake.handle_action(
        SimpleNamespace(
            action=ATTEMPT_008_MULTILINE_SED,
            observation="",
            state=None,
            done=False,
            thought="",
            output="",
        )
    )

    assert "invalid Python syntax" in first.observation
    assert "invalid Python syntax" in second.observation
    assert executed[-1] == ATTEMPT_008_MULTILINE_SED
    assert target.read_bytes() == prior
    assert target.stat().st_mode & 0o777 == prior_mode
    assert subprocess.run(
        ["git", "diff", "--quiet", "HEAD", "--"], cwd=tmp_path, check=False
    ).returncode == 0
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["current_phase"] == "edit"
    assert state["target_inspected"] is True
    assert state["target_confirmed_after_edit"] is False
    assert state["rollback_count"] == 2
    assert state["last_transaction"]["status"] == "rejected"
    assert state["last_transaction"]["rollback_verified"] is True
    assert state["transactional_cleanup_verified"] is True
    events = [json.loads(line) for line in event_path.read_text().splitlines()]
    multiline_event = events[-1]
    assert multiline_event["candidate"]["kind"] == "noninteractive_edit"
    assert multiline_event["executed"] is True
    assert multiline_event["command_multiline"] is True
    assert multiline_event["physical_line_count"] == 3
    assert multiline_event["parsed_command_count"] == 1
    assert multiline_event["parse_status"] == "parsed"
    assert multiline_event["parsed_executable"] == "sed"
    assert multiline_event["true_write_targets"] == [target_name]
    assert multiline_event["target_inspected"] is True
    assert multiline_event["target_confirmed_after_edit"] is False
    assert multiline_event["transaction"]["rollback_verified"] is True
    assert not authorize_phase_patch(state, "").authorized


def test_valid_generic_multiline_sed_is_accepted_transactionally(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bash = _bash_executable()
    if bash is None:
        pytest.skip("Bash is required to execute the multiline sed action.")
    target = _initialize_git_target(
        tmp_path, TARGET, b"def transform(value):\n    return value.strip()\n"
    )

    class FakeAgent:
        def __init__(self) -> None:
            self._env = _LocalPhaseEnvironment(tmp_path)
            self.tools = SimpleNamespace(
                get_state=lambda env: {"working_dir": str(tmp_path)}
            )

        def handle_action(self, step: SimpleNamespace) -> SimpleNamespace:
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

    state_path, event_path = _install_host_transaction_gate(
        tmp_path, monkeypatch, FakeAgent, initial_phase="inspect"
    )
    fake = FakeAgent()
    fake.handle_action(
        SimpleNamespace(
            action=f"cat {TARGET}",
            observation="",
            state=None,
            done=False,
            thought="",
            output="",
        )
    )
    action = (
        "sed -i 's/value.strip()/value.upper()/\n"
        "s/def transform/def transform/' src/module.py"
    )

    result = fake.handle_action(
        SimpleNamespace(
            action=action,
            observation="",
            state=None,
            done=False,
            thought="",
            output="",
        )
    )

    assert result.execution_exit_code == 0
    assert "return value.upper()" in target.read_text(encoding="utf-8")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["current_phase"] == "confirm_edit"
    assert state["target_inspected"] is True
    assert state["target_confirmed_after_edit"] is False
    assert state["last_transaction"]["status"] == "accepted"
    assert state["last_transaction"]["transaction_closed"] is True
    event = json.loads(event_path.read_text().splitlines()[-1])
    assert event["accepted"] is True
    assert event["command_multiline"] is True
    assert event["parsed_argv"][2].count("\n") == 1


def test_model_action_timeout_is_distinguished_and_recovers_after_quiescence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class CommandTimeoutError(RuntimeError):
        pass

    prior = b"def transform(value):\n    return value\n"
    target = _initialize_git_target(tmp_path, TARGET, prior)

    class Environment(_LocalPhaseEnvironment):
        def __init__(self, root: Path) -> None:
            super().__init__(root)
            self.interrupts = 0

        def interrupt_session(self) -> None:
            self.interrupts += 1

    class FakeAgent:
        def __init__(self) -> None:
            self._env = Environment(tmp_path)
            self.tools = SimpleNamespace(
                get_state=lambda env: {"working_dir": str(tmp_path)},
                config=SimpleNamespace(execution_timeout=25),
            )

        def handle_action(self, step: SimpleNamespace) -> SimpleNamespace:
            target.write_text("def transform(value):\n    return changed\n", encoding="utf-8")
            raise CommandTimeoutError("model command timed out")

    state_path, _event_path = _install_host_transaction_gate(
        tmp_path, monkeypatch, FakeAgent
    )
    fake = FakeAgent()

    result = fake.handle_action(
        SimpleNamespace(
            action="sed -i 's/value/changed/' src/module.py",
            observation="",
            state=None,
            done=False,
            thought="",
            output="",
        )
    )

    assert fake._env.interrupts == 1
    assert target.read_bytes() == prior
    assert "model-authored command timed out" in result.observation
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["last_transaction_failure_kind"] == "model_action_timeout"
    assert state["last_transaction"]["timeout_owner"] == "model_action"
    assert state["last_transaction"]["timeout_seconds"] == 25
    assert state["last_transaction"]["rollback_verified"] is True


def test_rollback_timeout_leaves_incomplete_journal_and_blocks_authorization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class RollbackTimeoutError(RuntimeError):
        pass

    target = _initialize_git_target(
        tmp_path, TARGET, b"def transform(value):\n    return value\n"
    )

    class FakeAgent:
        def __init__(self) -> None:
            self._env = _LocalPhaseEnvironment(tmp_path)
            self.tools = SimpleNamespace(get_state=lambda env: {"working_dir": str(tmp_path)})

        def handle_action(self, step: SimpleNamespace) -> SimpleNamespace:
            target.write_text("def broken(:\n", encoding="utf-8")
            step.observation = "edit complete"
            step.execution_exit_code = 0
            step.done = False
            return step

    state_path, event_path = _install_host_transaction_gate(
        tmp_path, monkeypatch, FakeAgent
    )

    def rollback_timeout(*_args: object, **_kwargs: object) -> None:
        raise RollbackTimeoutError("rollback timed out")

    monkeypatch.setattr(phase_gate_module, "_restore_target", rollback_timeout)
    fake = FakeAgent()

    with pytest.raises(TransactionalCleanupError):
        fake.handle_action(
            SimpleNamespace(
                action="sed -i 's/value/broken/' src/module.py",
                observation="",
                state=None,
                done=False,
                thought="",
                output="",
            )
        )

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["active_transaction"] is not None
    assert state["active_transaction_status"] == "cleanup_incomplete"
    assert state["transactional_cleanup_verified"] is False
    assert state["last_transaction_failure_kind"] == "cgr_rollback_error"
    assert state["last_transaction"]["timeout_owner"] == "cgr_rollback"
    assert not authorize_phase_patch(state, "patch").authorized
    event = json.loads(event_path.read_text(encoding="utf-8").splitlines()[-1])
    assert event["transaction"]["transaction_closed"] is False
    assert event["transaction"]["rollback_verified"] is False


def test_rollback_verification_failure_remains_incomplete_and_unauthorized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _initialize_git_target(
        tmp_path, TARGET, b"def transform(value):\n    return value\n"
    )

    class FakeAgent:
        def __init__(self) -> None:
            self._env = _LocalPhaseEnvironment(tmp_path)
            self.tools = SimpleNamespace(get_state=lambda env: {"working_dir": str(tmp_path)})

        def handle_action(self, step: SimpleNamespace) -> SimpleNamespace:
            target.write_text("def broken(:\n", encoding="utf-8")
            step.observation = "edit complete"
            step.execution_exit_code = 0
            step.done = False
            return step

    state_path, event_path = _install_host_transaction_gate(
        tmp_path, monkeypatch, FakeAgent
    )
    real_snapshot = phase_gate_module._snapshot_target
    calls = 0

    def mismatched_verification_snapshot(*args: object, **kwargs: object) -> FileSnapshot:
        nonlocal calls
        calls += 1
        snapshot = real_snapshot(*args, **kwargs)
        if calls == 3:
            return FileSnapshot(snapshot.path, True, b"unverified", snapshot.mode)
        return snapshot

    monkeypatch.setattr(
        phase_gate_module, "_snapshot_target", mismatched_verification_snapshot
    )

    with pytest.raises(TransactionalCleanupError):
        FakeAgent().handle_action(
            SimpleNamespace(
                action="sed -i 's/value/broken/' src/module.py",
                observation="",
                state=None,
                done=False,
                thought="",
                output="",
            )
        )

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["last_transaction_failure_kind"] == "cgr_rollback_verification_error"
    assert state["transactional_cleanup_verified"] is False
    assert not authorize_phase_patch(state, "patch").authorized
    event = json.loads(event_path.read_text(encoding="utf-8").splitlines()[-1])
    assert event["transaction"]["rollback_attempted"] is True
    assert event["transaction"]["rollback_succeeded"] is True
    assert event["transaction"]["rollback_verified"] is False
