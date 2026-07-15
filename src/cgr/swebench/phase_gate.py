"""Generic workflow-phase gate for official SWE-agent shell actions."""

from __future__ import annotations

import ast
import base64
import difflib
import hashlib
import json
import os
import re
import shlex
import stat
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


PHASES = ("inspect", "edit", "confirm_edit", "test", "final_diff", "submit")


class TransactionalCleanupError(RuntimeError):
    """Raised when CGR cannot prove that an incomplete edit was restored."""


@dataclass(frozen=True)
class CandidateAction:
    raw: str
    kind: str
    targets_required_file: bool
    references_required_file: bool = False
    write_targets: tuple[str, ...] = ()
    targets_test_file: bool = False
    targets_unrelated_files: bool = False
    parsed_executable: str | None = None
    parsed_argv: tuple[str, ...] = ()
    redirection_operators: tuple[str, ...] = ()
    heredoc_present: bool = False
    heredoc_delimiter: str | None = None
    command_multiline: bool = False
    physical_line_count: int = 1
    parsed_command_count: int = 0
    quote_closed: bool = True
    continuation_detected: bool = False
    parse_status: str = "parsed"
    parse_failure_category: str | None = None


@dataclass(frozen=True)
class GateDecision:
    allowed: bool
    phase: str
    candidate: CandidateAction
    feedback: str | None = None


@dataclass
class RepositoryEvidence:
    target_diff: str = ""
    tracked_diff: str = ""


@dataclass(frozen=True)
class EditPolicy:
    mode: str = "nonempty_target_change"
    prohibit_test_scaffolding: bool = False
    require_existing_content_change: bool = False

    @classmethod
    def from_mapping(cls, value: Any) -> EditPolicy:
        if not isinstance(value, dict):
            return cls()
        return cls(
            mode=str(value.get("mode", "nonempty_target_change")),
            prohibit_test_scaffolding=bool(value.get("prohibit_test_scaffolding", False)),
            require_existing_content_change=bool(
                value.get("require_existing_content_change", False)
            ),
        )


@dataclass(frozen=True)
class FileSnapshot:
    path: str
    existed: bool
    content: bytes
    mode: int | None

    @property
    def fingerprint(self) -> str:
        marker = b"present\0" if self.existed else b"absent\0"
        mode = str(self.mode).encode() if self.mode is not None else b"none"
        return hashlib.sha256(marker + mode + b"\0" + self.content).hexdigest()


@dataclass(frozen=True)
class DiffEvidence:
    target_changed: bool
    existing_lines_modified: bool
    lines_added: int
    lines_deleted: int
    append_only: bool
    executable_content_changed: bool
    comment_only_change: bool
    whitespace_only_change: bool
    test_scaffolding_added: bool
    syntax_valid: bool


@dataclass(frozen=True)
class EditEvaluation:
    accepted: bool
    evidence: DiffEvidence
    failures: tuple[str, ...]


@dataclass(frozen=True)
class PatchAuthorization:
    authorized: bool
    failures: tuple[str, ...]
    fingerprint: str | None


@dataclass(frozen=True)
class TransactionReconciliation:
    required: bool
    attempted: bool
    succeeded: bool
    verified: bool
    target: str | None = None
    snapshot_path: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class _ParsedShellCommand:
    executable: str | None
    argv: tuple[str, ...]
    redirections: tuple[str, ...]
    output_targets: tuple[str, ...]
    heredoc_delimiter: str | None = None
    heredoc_body: str = ""


@dataclass(frozen=True)
class _ShellActionAnalysis:
    commands: tuple[_ParsedShellCommand, ...]
    write_targets: tuple[str, ...]
    parsed_executable: str | None
    parsed_argv: tuple[str, ...]
    redirection_operators: tuple[str, ...]
    heredoc_delimiter: str | None
    command_multiline: bool
    physical_line_count: int
    quote_closed: bool
    continuation_detected: bool
    parse_status: str
    parse_failure_category: str | None


@dataclass(frozen=True)
class _ShellParseResult:
    commands: tuple[_ParsedShellCommand, ...]
    quote_closed: bool
    continuation_detected: bool
    failure_category: str | None = None


def build_initial_phase_state(
    *, initial_phase: str, target: str, edit_policy: EditPolicy
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "current_phase": initial_phase,
        "initial_phase": initial_phase,
        "target": target,
        "edit_policy": asdict(edit_policy),
        "target_inspected": False,
        "target_confirmed_after_edit": False,
        "accepted_target_edit": False,
        "target_diff_inspected": False,
        "focused_test_executed": False,
        "focused_test_passed": False,
        "final_diff_inspected": False,
        "submission_authorized": False,
        "workflow_complete": False,
        "last_event_index": 0,
        "accepted_patch_fingerprint": None,
        "terminal_reason": None,
        "last_postcondition_failures": [],
        "diagnostic_flags": [],
        "rejected_edit_count": 0,
        "rollback_count": 0,
        "phase_rejection_count": 0,
        "repeated_candidate_count": 0,
        "repeated_kind_count": 0,
        "last_rejected_kind": None,
        "last_rejected_action_fingerprint": None,
        "declared_edit_without_edit_action": False,
        "verifier_failure_summary_available": False,
        "phase_stalled_repeated_action": False,
        "coaching_level": 0,
        "last_edit_execution": None,
        "active_transaction": None,
        "active_transaction_status": None,
        "active_transaction_target": None,
        "active_transaction_snapshot_fingerprint": None,
        "last_transaction": None,
        "last_transaction_failure_kind": None,
        "transactional_cleanup_verified": True,
    }


def patch_fingerprint(value: str) -> str:
    normalized = value.replace("\r\n", "\n").rstrip("\n")
    if normalized:
        normalized += "\n"
    return hashlib.sha256(normalized.encode()).hexdigest()


def authorize_phase_patch(
    state: dict[str, Any] | None, tracked_diff: str
) -> PatchAuthorization:
    failures: list[str] = []
    if not isinstance(state, dict):
        return PatchAuthorization(False, ("phase_state_missing",), None)
    if state.get("active_transaction") is not None:
        failures.append("active_transaction_incomplete")
    if state.get("transactional_cleanup_verified") is False:
        failures.append("transactional_cleanup_unverified")
    requirements = (
        ("accepted_target_edit", "accepted_target_edit_missing"),
        ("target_diff_inspected", "target_diff_not_inspected"),
        ("focused_test_executed", "focused_test_not_executed"),
        ("focused_test_passed", "focused_test_not_passed"),
        ("final_diff_inspected", "final_diff_not_inspected"),
        ("submission_authorized", "submission_not_authorized"),
        ("workflow_complete", "workflow_incomplete"),
    )
    for key, failure in requirements:
        if not state.get(key):
            failures.append(failure)
    fingerprint = patch_fingerprint(tracked_diff) if tracked_diff.strip() else None
    if fingerprint is None:
        failures.append("final_patch_empty")
    accepted = state.get("accepted_patch_fingerprint")
    if not isinstance(accepted, str) or not accepted:
        failures.append("accepted_patch_fingerprint_missing")
    elif fingerprint is not None and accepted != fingerprint:
        failures.append("final_patch_fingerprint_mismatch")
    return PatchAuthorization(
        not failures,
        tuple(dict.fromkeys(failures)),
        fingerprint,
    )


def write_phase_state(path: Path, state: dict[str, Any]) -> None:
    _atomic_write_json(path, state)


def reconcile_incomplete_transaction(
    workspace: Path,
    state: dict[str, Any],
    state_path: Path,
) -> TransactionReconciliation:
    active = state.get("active_transaction")
    transaction = active if isinstance(active, dict) else state.get("last_transaction")
    required = bool(
        isinstance(active, dict)
        or state.get("transactional_cleanup_verified") is False
    )
    if not required or not isinstance(transaction, dict):
        return TransactionReconciliation(required=False, attempted=False, succeeded=True, verified=True)
    target = transaction.get("target")
    snapshot_path_value = transaction.get("snapshot_path")
    try:
        if not isinstance(target, str) or not target:
            raise ValueError("Incomplete transaction does not identify its target.")
        if isinstance(snapshot_path_value, str) and snapshot_path_value:
            snapshot_path = Path(snapshot_path_value).absolute()
            journal_root = state_path.parent.absolute()
            try:
                snapshot_path.relative_to(journal_root)
            except ValueError as exc:
                raise ValueError("Transaction snapshot is outside the attempt artifacts.") from exc
            payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
        else:
            payload = transaction.get("snapshot")
            snapshot_path = None
        snapshot = _snapshot_from_payload(payload)
        if snapshot.path != target:
            raise ValueError("Transaction target does not match its snapshot.")
        _restore_host_target(workspace, snapshot)
        restored = _snapshot_host_target(workspace, target)
        if not _snapshots_equal(snapshot, restored):
            raise RuntimeError("Post-run restoration verification failed.")
        reconciled = dict(transaction)
        reconciled.update(
            {
                "status": "reconciled",
                "transaction_closed": True,
                "rollback_attempted": True,
                "rollback_succeeded": True,
                "rollback_verified": True,
                "restored_fingerprint": restored.fingerprint,
            }
        )
        state["active_transaction"] = None
        state["active_transaction_status"] = "reconciled"
        state["last_transaction"] = reconciled
        state["transactional_cleanup_verified"] = True
        write_phase_state(state_path, state)
        return TransactionReconciliation(
            required=True,
            attempted=True,
            succeeded=True,
            verified=True,
            target=target,
            snapshot_path=str(snapshot_path) if snapshot_path else None,
        )
    except Exception as exc:
        state["active_transaction_status"] = "cleanup_incomplete"
        state["last_transaction_failure_kind"] = "post_run_reconciliation_error"
        state["transactional_cleanup_verified"] = False
        try:
            write_phase_state(state_path, state)
        except OSError:
            pass
        return TransactionReconciliation(
            required=True,
            attempted=True,
            succeeded=False,
            verified=False,
            target=target if isinstance(target, str) else None,
            snapshot_path=snapshot_path_value if isinstance(snapshot_path_value, str) else None,
            error=_redact_sensitive_text(str(exc))[:1000],
        )


def _diff_evidence(
    before: FileSnapshot, after: FileSnapshot, *, target: str
) -> DiffEvidence:
    changed = (
        before.existed != after.existed
        or before.content != after.content
        or before.mode != after.mode
    )
    before_text = before.content.decode("utf-8", errors="replace")
    after_text = after.content.decode("utf-8", errors="replace")
    before_lines = before_text.splitlines(keepends=True)
    after_lines = after_text.splitlines(keepends=True)
    matcher = difflib.SequenceMatcher(a=before_lines, b=after_lines, autojunk=False)
    added_lines: list[str] = []
    lines_added = 0
    lines_deleted = 0
    existing_lines_modified = False
    insertion_positions: list[int] = []
    for tag, start_before, end_before, start_after, end_after in matcher.get_opcodes():
        if tag in {"replace", "delete"}:
            lines_deleted += end_before - start_before
            existing_lines_modified = True
        if tag in {"replace", "insert"}:
            lines_added += end_after - start_after
            added_lines.extend(after_lines[start_after:end_after])
        if tag == "insert":
            insertion_positions.append(start_before)
    append_only = bool(
        changed
        and before.existed
        and lines_added
        and not lines_deleted
        and insertion_positions
        and all(position == len(before_lines) for position in insertion_positions)
    )
    whitespace_only = bool(
        changed and "".join(before_text.split()) == "".join(after_text.split())
    )
    syntax_valid = True
    executable_content_changed = changed
    comment_only = False
    if target.lower().endswith(".py"):
        before_tree = _normalized_python_tree(before_text) if before.existed else None
        after_tree = _normalized_python_tree(after_text) if after.existed else None
        syntax_valid = after_tree is not None
        if syntax_valid:
            executable_content_changed = before_tree != after_tree
            comment_only = bool(changed and not whitespace_only and before_tree == after_tree)
        else:
            executable_content_changed = False
    return DiffEvidence(
        target_changed=changed,
        existing_lines_modified=existing_lines_modified,
        lines_added=lines_added,
        lines_deleted=lines_deleted,
        append_only=append_only,
        executable_content_changed=executable_content_changed,
        comment_only_change=comment_only,
        whitespace_only_change=whitespace_only,
        test_scaffolding_added=_contains_test_scaffolding("".join(added_lines)),
        syntax_valid=syntax_valid,
    )


def _normalized_python_tree(source: str) -> str | None:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (
            isinstance(body, list)
            and body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            del body[0]
    return ast.dump(tree, include_attributes=False)


def _contains_test_scaffolding(added: str) -> bool:
    signals = {
        "framework": bool(re.search(r"\b(?:import|from)\s+(?:unittest|pytest)\b", added)),
        "test_definition": bool(re.search(r"^\s*(?:async\s+)?def\s+test_", added, re.M)),
        "test_class": bool(re.search(r"^\s*class\s+Test\w*", added, re.M)),
        "test_case": bool(re.search(r"\bunittest\.TestCase\b", added)),
        "runner": bool(re.search(r"\b(?:unittest\.main|pytest\.main)\s*\(", added)),
        "assertion_api": bool(re.search(r"\bself\.assert\w+\s*\(", added)),
    }
    return signals["test_definition"] or sum(signals.values()) >= 2


def evaluate_edit(
    before: FileSnapshot,
    after: FileSnapshot,
    *,
    target: str,
    policy: EditPolicy,
) -> EditEvaluation:
    evidence = _diff_evidence(before, after, target=target)
    failures: list[str] = []
    if not evidence.target_changed:
        failures.append("no_target_change")
    if policy.mode == "modify_existing_source":
        if not before.existed:
            failures.append("existing_implementation_missing")
        if evidence.append_only:
            failures.append("append_only_nonrepair_edit")
        if policy.require_existing_content_change and not evidence.existing_lines_modified:
            failures.append("existing_implementation_unchanged")
        if policy.prohibit_test_scaffolding and evidence.test_scaffolding_added:
            failures.append("test_scaffolding_in_production_source")
        if evidence.whitespace_only_change:
            failures.append("whitespace_only_change")
        elif evidence.comment_only_change:
            failures.append("comment_only_change")
        if evidence.target_changed and not evidence.executable_content_changed:
            failures.append("executable_content_unchanged")
        if target.lower().endswith(".py") and not evidence.syntax_valid:
            failures.append("invalid_python_syntax")
    return EditEvaluation(not failures, evidence, tuple(dict.fromkeys(failures)))


def classify_candidate_action(
    action: str, *, target: str, focused_test: str
) -> CandidateAction:
    normalized = " ".join(action.replace("\r", "").split())
    analysis = _analyze_shell_action(action)
    write_targets = analysis.write_targets
    normalized_target = _normalize_target(target)
    normalized_test = _normalize_target(focused_test)
    targets = normalized_target in write_targets
    references_target = bool(normalized_target and normalized_target in action.replace("\\", "/"))
    targets_test = normalized_test in write_targets or any(
        _looks_like_test_path(path) for path in write_targets
    )
    unrelated = any(path != normalized_target for path in write_targets)
    test_framework = _test_framework(action)
    git_operation = _git_operation(analysis)
    if git_operation in {"commit", "push"}:
        kind = git_operation
    elif analysis.parse_status == "error" and re.search(
        r"(?:^|\s)git\s+(?:commit|push)(?:\s|$)", normalized
    ):
        kind = (
            "commit"
            if re.search(r"(?:^|\s)git\s+commit(?:\s|$)", normalized)
            else "push"
        )
    elif _analysis_has_interactive_editor(analysis):
        kind = "interactive_editor"
    elif re.search(r"<<SWE_AGENT_SUBMISSION>>|model\.patch|^submit$", action, re.I):
        kind = "submission"
    elif git_operation == "diff":
        kind = "git_diff"
    elif (
        analysis.parse_status == "error"
        and references_target
        and _appears_to_be_supported_edit(action)
    ):
        kind = "shell_parse_error"
    elif write_targets:
        if targets and unrelated:
            kind = "edit_mixed_targets"
        elif targets:
            kind = "noninteractive_edit"
        else:
            kind = "edit_wrong_file"
    elif _has_write_intent(action):
        kind = "unknown"
    elif test_framework:
        scope = "focused" if focused_test and normalized_test in action.replace("\\", "/") else "unrelated"
        kind = f"{scope}_{test_framework}"
    elif _analysis_is_inspection(analysis):
        kind = "target_confirmation" if references_target else "inspection"
    else:
        kind = "unknown"
    return CandidateAction(
        raw=action,
        kind=kind,
        targets_required_file=targets,
        references_required_file=references_target,
        write_targets=write_targets,
        targets_test_file=targets_test,
        targets_unrelated_files=unrelated,
        parsed_executable=analysis.parsed_executable,
        parsed_argv=analysis.parsed_argv,
        redirection_operators=analysis.redirection_operators,
        heredoc_present=analysis.heredoc_delimiter is not None,
        heredoc_delimiter=analysis.heredoc_delimiter,
        command_multiline=analysis.command_multiline,
        physical_line_count=analysis.physical_line_count,
        parsed_command_count=len(analysis.commands),
        quote_closed=analysis.quote_closed,
        continuation_detected=analysis.continuation_detected,
        parse_status=analysis.parse_status,
        parse_failure_category=analysis.parse_failure_category,
    )


def _test_framework(action: str) -> str | None:
    normalized = " ".join(action.replace("\r", "").split())
    module = re.search(
        r"(?:^|[\s;&|])[^\s;&|]*python(?:3(?:\.\d+)?)?(?:\.exe)?\s+-m\s+"
        r"(?P<framework>pytest|unittest)(?:[\s;&|]|$)",
        normalized,
        re.I,
    )
    if module:
        return module.group("framework").lower()
    direct = re.search(
        r"(?:^|[\s;&|])(?P<framework>pytest|unittest)(?:\.exe)?(?:[\s;&|]|$)",
        normalized,
        re.I,
    )
    return direct.group("framework").lower() if direct else None


def _git_operation(analysis: _ShellActionAnalysis) -> str | None:
    operations: list[str] = []
    for command in analysis.commands:
        if command.executable == "git" and len(command.argv) >= 2:
            operation = command.argv[1].lower()
            if operation in {"commit", "push", "diff"}:
                operations.append(operation)
    for prohibited in ("commit", "push"):
        if prohibited in operations:
            return prohibited
    return "diff" if "diff" in operations else None


def _analysis_has_interactive_editor(analysis: _ShellActionAnalysis) -> bool:
    return any(
        command.executable in {"nano", "vim", "vi", "emacs", "pico"}
        for command in analysis.commands
    )


def _analysis_is_inspection(analysis: _ShellActionAnalysis) -> bool:
    return any(
        command.executable in {"cat", "head", "tail", "rg", "grep", "ls"}
        or (
            command.executable == "sed"
            and any(token == "-n" or token.startswith("-n") for token in command.argv[1:])
        )
        for command in analysis.commands
    )


class PhaseGate:
    def __init__(
        self,
        *,
        phase: str,
        target: str,
        focused_test: str,
        log_path: Path | None = None,
        state_path: Path | None = None,
        edit_policy: EditPolicy | None = None,
        snapshot_python: str = "python",
        verifier_failure_evidence: dict[str, Any] | None = None,
    ) -> None:
        if phase not in PHASES:
            raise ValueError(f"Unsupported required phase: {phase}")
        self.phase = phase
        self.target = target
        self.focused_test = focused_test
        self.log_path = log_path
        self.state_path = state_path
        self.edit_policy = edit_policy or EditPolicy()
        self.snapshot_python = snapshot_python
        self.verifier_failure_evidence = (
            verifier_failure_evidence if isinstance(verifier_failure_evidence, dict) else {}
        )
        self.target_inspected = False
        self.target_confirmed_after_edit = False
        self.target_diff_inspected = False
        self.last_failed_test_fingerprint: str | None = None
        self.last_failed_diff_fingerprint: str | None = None
        self._rejected_action_counts: dict[str, int] = {}
        self._rejected_kind_counts: dict[str, int] = {}
        self.state = build_initial_phase_state(
            initial_phase=phase, target=target, edit_policy=self.edit_policy
        )
        self.state["verifier_failure_summary_available"] = bool(
            self.verifier_failure_evidence.get("available")
            and self.verifier_failure_evidence.get("summary")
        )
        self._persist_state()

    def decide(
        self,
        action: str,
        evidence: RepositoryEvidence,
        *,
        model_text: str = "",
    ) -> GateDecision:
        candidate = classify_candidate_action(
            action, target=self.target, focused_test=self.focused_test
        )
        reason: str | None = None
        if candidate.kind in {"commit", "push"}:
            reason = "Committing, pushing, changing remotes, and configuring Git identity are prohibited."
        elif self.phase == "inspect":
            if candidate.kind not in {"inspection", "target_confirmation"}:
                reason = "Inspect the required source or relevant test before continuing."
        elif self.phase == "edit":
            if candidate.kind == "edit_wrong_file":
                reason = "The edit does not modify the required production source file."
            elif candidate.kind == "edit_mixed_targets":
                reason = "The command mixes the required source edit with unrelated or test-file writes."
            elif candidate.kind != "noninteractive_edit":
                reason = f"{_kind_label(candidate.kind)} cannot satisfy the edit phase."
        elif self.phase == "confirm_edit":
            if candidate.kind == "noninteractive_edit":
                pass
            elif candidate.kind == "target_confirmation":
                pass
            elif candidate.kind == "git_diff" and candidate.references_required_file:
                if not evidence.target_diff.strip():
                    reason = "The target-specific diff is empty; the edit has not taken effect."
            else:
                reason = f"{_kind_label(candidate.kind)} cannot satisfy confirm_edit."
        elif self.phase == "test":
            if candidate.kind not in {"focused_pytest", "focused_unittest"}:
                reason = "Run only the configured focused verifier in the test phase."
            elif (
                self.last_failed_test_fingerprint is not None
                and self.last_failed_diff_fingerprint == _fingerprint(evidence.target_diff)
            ):
                reason = "The unchanged known failing verifier cannot be repeated without a repository change."
        elif self.phase == "final_diff":
            if candidate.kind != "git_diff":
                reason = "Inspect the final Git diff before submission."
            elif not evidence.tracked_diff.strip():
                reason = "The repository has no nonempty tracked patch to inspect."
        elif self.phase == "submit":
            if candidate.kind != "submission":
                reason = "Submit the existing worktree patch in the submit phase."
            elif not evidence.tracked_diff.strip():
                reason = "Submission requires a nonempty tracked patch."
        if reason is None:
            return GateDecision(True, self.phase, candidate)
        self._register_rejection(candidate, model_text=model_text)
        feedback = self._rejection_feedback(candidate, reason)
        decision = GateDecision(False, self.phase, candidate, feedback)
        self.record(decision, executed=False, accepted=False, phase_after=self.phase)
        return decision

    def _rejection_feedback(self, candidate: CandidateAction, reason: str) -> str:
        lines = [
            "ACTION REJECTED BY CGR",
            "",
            f"Required phase: {self.phase}",
            f"Required target: {self.target}",
        ]
        if candidate.write_targets:
            label = "Observed write target" if len(candidate.write_targets) == 1 else "Observed write targets"
            lines.append(f"{label}: {', '.join(candidate.write_targets)}")
        lines.extend((f"Candidate kind: {candidate.kind}", ""))
        test_kinds = {
            "focused_pytest",
            "unrelated_pytest",
            "focused_unittest",
            "unrelated_unittest",
        }
        declared = bool(self.state["declared_edit_without_edit_action"])
        level = int(self.state["coaching_level"])
        if self.phase == "edit" and candidate.kind in test_kinds:
            lines.append("The proposed test was not executed.")
            verifier = self._verifier_failure_clause()
            if verifier:
                lines.append(verifier)
            lines.append("Testing cannot satisfy the edit phase while the target is unchanged.")
            if level >= 2:
                lines.append("The repository is unchanged, so the repeated test remains blocked.")
            constraint = self._edit_action_constraint(include_mechanisms=level >= 3)
        elif candidate.kind == "interactive_editor":
            lines.append("The editor was not opened because actions must be noninteractive.")
            constraint = self._edit_action_constraint(include_mechanisms=True)
        elif candidate.kind in {"edit_wrong_file", "edit_mixed_targets"}:
            lines.append(reason)
            constraint = (
                f"Return exactly one noninteractive command that edits {self.target}. "
                "Do not create or modify tests, commit, submit, or run tests."
            )
        elif candidate.kind == "shell_parse_error":
            return "\n".join(
                (
                    "ACTION COULD NOT BE PARSED SAFELY",
                    "",
                    f"Required phase: {self.phase}",
                    f"Required target: {self.target}",
                    "",
                    "The action appears to target the required source using a supported "
                    "noninteractive editor, but its shell quoting could not be parsed safely. "
                    "It was not executed.",
                    "Return one syntactically complete noninteractive edit command.",
                    "Do not describe multiple future commands.",
                )
            )
        elif candidate.kind in {"commit", "push"}:
            lines.append(reason)
            if declared:
                lines.append(
                    "You described a possible source change but did not apply it. "
                    "The executable action did not apply the described change."
                )
                constraint = (
                    self._edit_action_constraint(include_mechanisms=level >= 2)
                    if self.phase == "edit"
                    else "The commit was not executed. First return exactly one inspection "
                    "action that satisfies the current inspect phase."
                )
            else:
                constraint = (
                    f"The {candidate.kind} was not executed and the required phase remains "
                    f"{self.phase}. Return exactly one action that satisfies the current phase."
                )
        else:
            lines.append(reason)
            if self.phase == "edit" and declared:
                lines.append(
                    "You described a source change, but your executable action did not apply it. "
                    "The action was not executed."
                )
                constraint = self._edit_action_constraint(include_mechanisms=level >= 2)
            elif self.phase == "edit":
                constraint = self._edit_action_constraint(include_mechanisms=level >= 3)
            else:
                constraint = (
                    "The proposed action was not executed. Return exactly one action that "
                    "satisfies the required phase."
                )
        lines.extend((constraint, "Do not describe multiple future commands."))
        return "\n".join(lines)

    def _edit_action_constraint(self, *, include_mechanisms: bool) -> str:
        message = (
            "Return exactly one noninteractive shell command that changes the existing "
            f"implementation in {self.target}."
        )
        if include_mechanisms:
            message += (
                " Supported mechanisms include sed -i, a Python file-writing command, or a "
                "heredoc/full-file rewrite."
            )
        return message + " Do not run tests, open an editor, commit, or submit."

    def _verifier_failure_clause(self) -> str:
        summary = self.verifier_failure_evidence.get("summary")
        return str(summary)[:400] if isinstance(summary, str) else ""

    def _register_rejection(self, candidate: CandidateAction, *, model_text: str) -> None:
        fingerprint = _fingerprint(" ".join(candidate.raw.split()))
        self._rejected_action_counts[fingerprint] = (
            self._rejected_action_counts.get(fingerprint, 0) + 1
        )
        self._rejected_kind_counts[candidate.kind] = (
            self._rejected_kind_counts.get(candidate.kind, 0) + 1
        )
        self.state["phase_rejection_count"] += 1
        self.state["repeated_candidate_count"] = self._rejected_action_counts[fingerprint]
        self.state["repeated_kind_count"] = self._rejected_kind_counts[candidate.kind]
        self.state["last_rejected_kind"] = candidate.kind
        self.state["last_rejected_action_fingerprint"] = fingerprint
        declared = candidate.kind not in {
            "noninteractive_edit",
            "edit_mixed_targets",
        } and _declares_source_change(model_text, self.target)
        self.state["declared_edit_without_edit_action"] = declared
        if int(self.state["repeated_candidate_count"]) >= 2:
            self.state["phase_stalled_repeated_action"] = True
        self.state["coaching_level"] = min(
            3,
            max(
                1,
                int(self.state["repeated_candidate_count"]),
                int(self.state["repeated_kind_count"]),
            ),
        )

    def phase_transition_coaching(self) -> str:
        lines = [
            "CGR PHASE TRANSITION",
            "",
            "Current phase: edit",
            f"Required target: {self.target}",
            "",
        ]
        verifier = self._verifier_failure_clause()
        if verifier:
            lines.append(verifier)
        lines.extend(
            (
                "Inspection alone does not satisfy the task. Return exactly one "
                "noninteractive shell action that changes the existing implementation in "
                "the required target.",
                "Allowed mechanisms include sed -i, a Python file-writing command, or a "
                "heredoc/full-file rewrite.",
                "Do not run tests, open an interactive editor, commit, submit, or merely "
                "describe the change.",
            )
        )
        return "\n".join(lines)

    def begin_transaction(
        self, decision: GateDecision, snapshot: FileSnapshot
    ) -> dict[str, Any]:
        transaction_id = (
            f"tx-{int(self.state['last_event_index']) + 1:06d}-"
            f"{_fingerprint(decision.candidate.raw)}"
        )
        snapshot_payload = _snapshot_payload(snapshot)
        snapshot_path: str | None = None
        if self.state_path is not None:
            path = self.state_path.with_name(f"{transaction_id}.snapshot.json")
            _atomic_write_json(path, snapshot_payload)
            snapshot_path = str(path)
        transaction: dict[str, Any] = {
            "transaction_id": transaction_id,
            "target": snapshot.path,
            "candidate_fingerprint": _fingerprint(decision.candidate.raw),
            "candidate_kind": decision.candidate.kind,
            "status": "started",
            "start_event_index": int(self.state["last_event_index"]) + 1,
            "snapshot_path": snapshot_path,
            "snapshot": snapshot_payload if snapshot_path is None else None,
            "pre_action_fingerprint": snapshot.fingerprint,
            "pre_action_mode": snapshot.mode,
            "execution_attempted": False,
            "execution_returned_normally": False,
            "execution_exception_category": None,
            "validation_attempted": False,
            "validation_completed": False,
            "rollback_attempted": False,
            "rollback_succeeded": False,
            "rollback_verified": False,
            "restored_fingerprint": None,
            "failure_kind": None,
            "timeout_owner": None,
            "timeout_operation": None,
            "timeout_seconds": None,
            "timeout_command_preview": None,
            "model_action_completed_before_timeout": False,
            "transaction_closed": False,
        }
        self.state["active_transaction"] = transaction
        self.state["active_transaction_status"] = "started"
        self.state["active_transaction_target"] = snapshot.path
        self.state["active_transaction_snapshot_fingerprint"] = snapshot.fingerprint
        self.state["last_transaction_failure_kind"] = None
        self.state["transactional_cleanup_verified"] = False
        self._persist_state()
        return transaction

    def finish_transaction(
        self,
        transaction: dict[str, Any],
        *,
        status: str,
        failure_kind: str | None,
        cleanup_verified: bool,
        updates: dict[str, Any],
    ) -> dict[str, Any]:
        completed = dict(transaction)
        completed.update(updates)
        completed["status"] = status
        completed["failure_kind"] = failure_kind
        completed["transaction_closed"] = status in {"accepted", "rejected", "reconciled"}
        self.state["active_transaction_status"] = status
        self.state["last_transaction_failure_kind"] = failure_kind
        self.state["transactional_cleanup_verified"] = cleanup_verified
        self.state["last_transaction"] = completed
        self.state["active_transaction"] = (
            None if completed["transaction_closed"] else completed
        )
        self._persist_state()
        return completed

    def update_transaction(
        self, transaction: dict[str, Any], **updates: Any
    ) -> dict[str, Any]:
        transaction.update(updates)
        self.state["active_transaction"] = dict(transaction)
        self.state["active_transaction_status"] = str(transaction.get("status", "started"))
        self._persist_state()
        return transaction

    def transactional_failure_feedback(
        self,
        *,
        failure_kind: str,
        target_changed: bool,
        rollback_verified: bool,
    ) -> str:
        if failure_kind == "cgr_postinspection_timeout":
            cause = "an internal inspection operation timed out"
        elif failure_kind == "model_action_timeout":
            cause = "the model-authored command timed out"
        else:
            cause = "CGR could not complete post-edit validation"
        changed = " changed the target, but" if target_changed else " did not complete because"
        restoration = (
            "The original target was restored and verified."
            if rollback_verified
            else "CGR could not verify restoration, so the transaction remains incomplete."
        )
        return "\n".join(
            (
                "ACTION DID NOT COMPLETE TRANSACTIONAL VALIDATION",
                "",
                "Required phase: edit",
                f"Required target: {self.target}",
                "",
                f"The model-authored command{changed} {cause}.",
                restoration,
                "Return exactly one corrected noninteractive edit action.",
            )
        )

    def transactional_rejection_feedback(
        self,
        evaluation: EditEvaluation,
        *,
        observation: str = "",
        execution_exit_code: int | None = None,
    ) -> str:
        descriptions = {
            "test_scaffolding_in_production_source": (
                "It added test scaffolding to a production source target"
            ),
            "append_only_nonrepair_edit": "It only appended content",
            "existing_implementation_unchanged": (
                "It did not modify the existing implementation"
            ),
            "whitespace_only_change": "It changed only whitespace",
            "comment_only_change": "It changed only comments",
            "no_target_change": "It produced no target change",
            "invalid_python_syntax": "It left invalid Python syntax",
            "command_nonzero_exit": "The command exited unsuccessfully",
        }
        grounded = [descriptions[item] for item in evaluation.failures if item in descriptions]
        reason = " and ".join(grounded[:2]) or "It did not satisfy the configured edit policy"
        evidence = _bounded_execution_evidence(observation, execution_exit_code)
        lines = [
            "ACTION EXECUTED BUT DID NOT PRODUCE AN ACCEPTABLE EDIT",
            "",
            "Required phase: edit",
            f"Required target: {self.target}",
            "",
            "The noninteractive command targeted the required file, but its resulting change "
            "was rejected and rolled back.",
            f"Grounded outcome: {reason}.",
        ]
        if evidence:
            lines.append(evidence)
        lines.extend(
            (
                "The required phase remains edit.",
                f"Return exactly one corrected noninteractive command that edits {self.target}.",
                "Do not add tests, commit, submit, or run tests.",
            )
        )
        return "\n".join(lines)

    def record_execution(
        self,
        decision: GateDecision,
        *,
        observation: str,
        evidence: RepositoryEvidence,
        accepted: bool = True,
        rolled_back: bool = False,
        pre_action_fingerprint: str | None = None,
        post_action_fingerprint: str | None = None,
        postcondition_failures: tuple[str, ...] = (),
        diff_evidence: DiffEvidence | None = None,
        execution_exit_code: int | None = None,
        transaction_details: dict[str, Any] | None = None,
    ) -> None:
        phase_before = self.phase
        kind = decision.candidate.kind
        if phase_before == "inspect" and kind == "target_confirmation":
            self.target_inspected = True
            self.state["target_inspected"] = True
            self.target_confirmed_after_edit = False
            self.state["target_confirmed_after_edit"] = False
            self.phase = "edit"
        elif phase_before in {"edit", "confirm_edit"} and kind == "noninteractive_edit":
            prior_edit_accepted = bool(self.state["accepted_target_edit"])
            self.target_confirmed_after_edit = False
            self.target_diff_inspected = False
            self.state["target_confirmed_after_edit"] = False
            self.state["target_diff_inspected"] = False
            self.state["focused_test_executed"] = False
            self.state["focused_test_passed"] = False
            self.state["final_diff_inspected"] = False
            self.state["submission_authorized"] = False
            self.state["workflow_complete"] = False
            if accepted and evidence.target_diff.strip():
                self.state["accepted_target_edit"] = True
                self.state["accepted_patch_fingerprint"] = patch_fingerprint(
                    evidence.tracked_diff
                )
                self.state["last_postcondition_failures"] = []
                self.phase = "confirm_edit"
            else:
                restored_accepted_edit = bool(
                    phase_before == "confirm_edit"
                    and prior_edit_accepted
                    and evidence.target_diff.strip()
                )
                self.state["accepted_target_edit"] = restored_accepted_edit
                self.state["accepted_patch_fingerprint"] = (
                    patch_fingerprint(evidence.tracked_diff)
                    if restored_accepted_edit
                    else None
                )
                self.state["last_postcondition_failures"] = list(postcondition_failures)
                self.state["rejected_edit_count"] += 1
                if rolled_back:
                    self.state["rollback_count"] += 1
                flags = self.state["diagnostic_flags"]
                for item in (*postcondition_failures, "edit_postcondition_failed"):
                    if item not in flags:
                        flags.append(item)
                if rolled_back and "edit_rolled_back" not in flags:
                    flags.append("edit_rolled_back")
                self.phase = "confirm_edit" if restored_accepted_edit else "edit"
        elif phase_before == "confirm_edit" and kind == "target_confirmation":
            self.target_inspected = True
            self.state["target_inspected"] = True
            self.target_confirmed_after_edit = True
            self.state["target_confirmed_after_edit"] = True
        elif phase_before == "confirm_edit" and kind == "git_diff":
            self.target_diff_inspected = bool(evidence.target_diff.strip())
            self.state["target_diff_inspected"] = self.target_diff_inspected
        if (
            self.phase == "confirm_edit"
            and self.target_confirmed_after_edit
            and self.target_diff_inspected
        ):
            self.phase = "test"
        if phase_before == "test" and kind in {"focused_pytest", "focused_unittest"}:
            self.state["focused_test_executed"] = True
            if _test_passed(observation):
                self.last_failed_test_fingerprint = None
                self.last_failed_diff_fingerprint = None
                self.state["focused_test_passed"] = True
                self.phase = "final_diff"
            else:
                self.state["focused_test_passed"] = False
                self.last_failed_test_fingerprint = _fingerprint(observation)
                self.last_failed_diff_fingerprint = _fingerprint(evidence.target_diff)
        elif phase_before == "final_diff" and kind == "git_diff" and evidence.tracked_diff.strip():
            self.state["final_diff_inspected"] = True
            self.state["accepted_patch_fingerprint"] = patch_fingerprint(
                evidence.tracked_diff
            )
            self.phase = "submit"
        elif phase_before == "submit" and kind == "submission" and accepted:
            self.state["submission_authorized"] = True
            self.state["workflow_complete"] = True
            self.state["accepted_patch_fingerprint"] = patch_fingerprint(
                evidence.tracked_diff
            )
        self.state["current_phase"] = self.phase
        if self.phase != "inspect" and self.target_inspected:
            self.state["target_inspected"] = True
        if self.phase != phase_before:
            self._rejected_action_counts.clear()
            self._rejected_kind_counts.clear()
            self.state["phase_rejection_count"] = 0
            self.state["repeated_candidate_count"] = 0
            self.state["repeated_kind_count"] = 0
            self.state["last_rejected_kind"] = None
            self.state["last_rejected_action_fingerprint"] = None
            self.state["declared_edit_without_edit_action"] = False
            self.state["coaching_level"] = 1 if self.phase == "edit" else 0
        self.record(
            decision,
            executed=True,
            accepted=accepted,
            rolled_back=rolled_back,
            phase_after=self.phase,
            observation=observation,
            evidence=evidence,
            pre_action_fingerprint=pre_action_fingerprint,
            post_action_fingerprint=post_action_fingerprint,
            postcondition_failures=postcondition_failures,
            diff_evidence=diff_evidence,
            execution_exit_code=execution_exit_code,
            transaction_details=transaction_details,
        )

    def record(
        self,
        decision: GateDecision,
        *,
        executed: bool,
        accepted: bool,
        phase_after: str,
        rolled_back: bool = False,
        observation: str = "",
        evidence: RepositoryEvidence | None = None,
        pre_action_fingerprint: str | None = None,
        post_action_fingerprint: str | None = None,
        postcondition_failures: tuple[str, ...] = (),
        diff_evidence: DiffEvidence | None = None,
        execution_exit_code: int | None = None,
        transaction_details: dict[str, Any] | None = None,
    ) -> None:
        self.state["last_event_index"] += 1
        self.state["current_phase"] = phase_after
        candidate_payload = asdict(decision.candidate)
        candidate_payload["raw"] = _redact_sensitive_text(decision.candidate.raw)[:4000]
        payload = {
            "event_index": self.state["last_event_index"],
            "phase_before": decision.phase,
            "phase_after": phase_after,
            "allowed": decision.allowed,
            "executed": executed,
            "accepted": accepted,
            "rolled_back": rolled_back,
            "candidate": candidate_payload,
            "feedback": decision.feedback,
            "observation_preview": _redact_sensitive_text(observation)[:1000],
            "target_diff_nonempty": bool(evidence and evidence.target_diff.strip()),
            "tracked_diff_nonempty": bool(evidence and evidence.tracked_diff.strip()),
            "pre_action_fingerprint": pre_action_fingerprint,
            "post_action_fingerprint": post_action_fingerprint,
            "postcondition_failures": list(postcondition_failures),
            "diff_evidence": asdict(diff_evidence) if diff_evidence else None,
            "parsed_executable": decision.candidate.parsed_executable,
            "parsed_argv": list(decision.candidate.parsed_argv),
            "true_write_targets": list(decision.candidate.write_targets),
            "redirection_operators": list(decision.candidate.redirection_operators),
            "heredoc_present": decision.candidate.heredoc_present,
            "heredoc_delimiter": decision.candidate.heredoc_delimiter,
            "command_multiline": decision.candidate.command_multiline,
            "physical_line_count": decision.candidate.physical_line_count,
            "parsed_command_count": decision.candidate.parsed_command_count,
            "quote_closed": decision.candidate.quote_closed,
            "continuation_detected": decision.candidate.continuation_detected,
            "parse_status": decision.candidate.parse_status,
            "parse_failure_category": decision.candidate.parse_failure_category,
            "target_inspected": bool(self.state["target_inspected"]),
            "target_confirmed_after_edit": bool(
                self.state["target_confirmed_after_edit"]
            ),
            "execution_attempted": bool(
                executed and decision.candidate.kind == "noninteractive_edit"
            ),
            "execution_exit_code": execution_exit_code,
            "target_changed": diff_evidence.target_changed if diff_evidence else False,
            "rollback_status": "rolled_back"
            if rolled_back
            else ("not_required" if executed else "not_executed"),
            "postcondition_outcome": (
                "accepted" if accepted else "rejected"
            )
            if executed and decision.candidate.kind == "noninteractive_edit"
            else None,
            "transaction": _bounded_transaction_details(transaction_details),
            "phase_rejection_count": self.state["phase_rejection_count"],
            "repeated_candidate_count": self.state["repeated_candidate_count"],
            "repeated_kind_count": self.state["repeated_kind_count"],
            "last_rejected_kind": self.state["last_rejected_kind"],
            "last_rejected_action_fingerprint": self.state[
                "last_rejected_action_fingerprint"
            ],
            "declared_edit_without_edit_action": self.state[
                "declared_edit_without_edit_action"
            ],
            "verifier_failure_summary_available": self.state[
                "verifier_failure_summary_available"
            ],
            "phase_stalled_repeated_action": self.state[
                "phase_stalled_repeated_action"
            ],
            "coaching_level": self.state["coaching_level"],
        }
        if decision.candidate.kind == "noninteractive_edit" and executed:
            self.state["last_edit_execution"] = {
                "parsed_executable": decision.candidate.parsed_executable,
                "parsed_argv": list(decision.candidate.parsed_argv),
                "write_targets": list(decision.candidate.write_targets),
                "redirection_operators": list(decision.candidate.redirection_operators),
                "heredoc_present": decision.candidate.heredoc_present,
                "heredoc_delimiter": decision.candidate.heredoc_delimiter,
                "command_multiline": decision.candidate.command_multiline,
                "physical_line_count": decision.candidate.physical_line_count,
                "parsed_command_count": decision.candidate.parsed_command_count,
                "quote_closed": decision.candidate.quote_closed,
                "continuation_detected": decision.candidate.continuation_detected,
                "parse_status": decision.candidate.parse_status,
                "parse_failure_category": decision.candidate.parse_failure_category,
                "execution_attempted": True,
                "execution_exit_code": execution_exit_code,
                "target_changed": diff_evidence.target_changed if diff_evidence else False,
                "rolled_back": rolled_back,
                "postcondition_outcome": "accepted" if accepted else "rejected",
                "postcondition_failures": list(postcondition_failures),
                "transaction_id": transaction_details.get("transaction_id")
                if transaction_details
                else None,
                "execution_returned_normally": bool(
                    transaction_details
                    and transaction_details.get("execution_returned_normally")
                ),
                "validation_attempted": bool(
                    transaction_details and transaction_details.get("validation_attempted")
                ),
                "validation_completed": bool(
                    transaction_details and transaction_details.get("validation_completed")
                ),
                "rollback_attempted": bool(
                    transaction_details and transaction_details.get("rollback_attempted")
                ),
                "rollback_verified": bool(
                    transaction_details and transaction_details.get("rollback_verified")
                ),
                "transaction_closed": bool(
                    transaction_details and transaction_details.get("transaction_closed")
                ),
            }
        if self.log_path is not None:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, sort_keys=True) + "\n")
        self._persist_state()

    def _persist_state(self) -> None:
        if self.state_path is None:
            return
        _atomic_write_json(self.state_path, self.state)


class _ExecutionExitCapture:
    def __init__(self, environment: Any) -> None:
        self.environment = environment
        self.exit_code: int | None = None
        self._runtime: Any = None
        self._original: Any = None
        self._installed = False

    def __enter__(self) -> _ExecutionExitCapture:
        deployment = getattr(self.environment, "deployment", None)
        runtime = getattr(deployment, "runtime", None)
        original = getattr(runtime, "run_in_session", None)
        if runtime is None or not callable(original):
            return self
        self._runtime = runtime
        self._original = original

        async def observed(action: Any) -> Any:
            response = await original(action)
            if self.exit_code is None and isinstance(getattr(action, "command", None), str):
                value = getattr(response, "exit_code", None)
                if isinstance(value, int):
                    self.exit_code = value
            return response

        try:
            setattr(runtime, "run_in_session", observed)
        except (AttributeError, TypeError):
            return self
        self._installed = True
        return self

    def __exit__(self, *_args: Any) -> None:
        if self._installed:
            setattr(self._runtime, "run_in_session", self._original)


def _handle_transactional_edit(
    agent: Any,
    step: Any,
    gate: PhaseGate,
    decision: GateDecision,
    original: Any,
    host_workspace_root: Path | None,
) -> Any:
    before = _snapshot_target(
        agent, gate.target, gate.snapshot_python, host_workspace_root
    )
    transaction = gate.begin_transaction(decision, before)
    result = step
    after: FileSnapshot | None = None
    evidence = RepositoryEvidence()
    evaluation: EditEvaluation | None = None
    execution_exit_code: int | None = None
    failure_kind: str | None = None
    failure_exception: Exception | None = None
    gate.update_transaction(transaction, execution_attempted=True)
    try:
        try:
            with _ExecutionExitCapture(agent._env) as execution:
                result = original(agent, step)
        except Exception as exc:
            failure_exception = exc
            failure_kind = (
                "model_action_timeout" if _is_timeout_exception(exc) else "model_action_error"
            )
            transaction["execution_exception_category"] = type(exc).__name__
            if failure_kind == "model_action_timeout":
                transaction.update(
                    _timeout_details(
                        owner="model_action",
                        operation="model_authored_edit",
                        seconds=_execution_timeout_seconds(agent),
                        command_preview=decision.candidate.raw,
                        model_action_completed=False,
                    )
                )
                if not _ensure_command_quiescence(agent):
                    return _transaction_cleanup_failure(
                        agent,
                        step,
                        gate,
                        decision,
                        transaction,
                        before,
                        evidence,
                        failure_kind="model_action_quiescence_error",
                        execution_exit_code=None,
                    )
        else:
            transaction["execution_returned_normally"] = True
            execution_exit_code = execution.exit_code
            if execution_exit_code is None:
                value = getattr(result, "execution_exit_code", None)
                execution_exit_code = value if isinstance(value, int) else None
        gate.update_transaction(transaction)

        try:
            after = _snapshot_target(
                agent, gate.target, gate.snapshot_python, host_workspace_root
            )
            evidence = _target_evidence_from_snapshots(before, after)
        except Exception as exc:
            failure_exception = failure_exception or exc
            failure_kind = (
                "cgr_postinspection_timeout"
                if _is_timeout_exception(exc)
                else "cgr_postcondition_error"
            )
            if failure_kind == "cgr_postinspection_timeout":
                transaction.update(
                    _timeout_details(
                        owner="cgr_internal_inspection",
                        operation="post_edit_target_snapshot_and_diff",
                        seconds=10,
                        command_preview=f"inspect {gate.target}",
                        model_action_completed=bool(
                            transaction["execution_returned_normally"]
                        ),
                    )
                )

        if failure_kind is None:
            transaction["validation_attempted"] = True
            gate.update_transaction(transaction)
            try:
                assert after is not None
                evaluation = evaluate_edit(
                    before,
                    after,
                    target=gate.target,
                    policy=gate.edit_policy,
                )
                transaction["validation_completed"] = True
            except Exception as exc:
                failure_exception = exc
                failure_kind = "cgr_postcondition_error"
            if execution_exit_code not in {None, 0} and evaluation is not None:
                evaluation = EditEvaluation(
                    False,
                    evaluation.evidence,
                    tuple(
                        dict.fromkeys((*evaluation.failures, "command_nonzero_exit"))
                    ),
                )
            if evaluation is not None and evaluation.accepted:
                assert after is not None
                completed = gate.finish_transaction(
                    transaction,
                    status="accepted",
                    failure_kind=None,
                    cleanup_verified=True,
                    updates={
                        "validation_attempted": True,
                        "validation_completed": True,
                    },
                )
                gate.record_execution(
                    decision,
                    observation=_normalize_observation_text(result.observation),
                    evidence=evidence,
                    accepted=True,
                    pre_action_fingerprint=before.fingerprint,
                    post_action_fingerprint=after.fingerprint,
                    diff_evidence=evaluation.evidence,
                    execution_exit_code=execution_exit_code,
                    transaction_details=completed,
                )
                return result
            if failure_kind is None:
                failure_kind = "postcondition_rejected"

        return _reject_and_restore_transaction(
            agent,
            result,
            gate,
            decision,
            transaction,
            before,
            after,
            evidence,
            evaluation,
            failure_kind=failure_kind or "cgr_postcondition_error",
            failure_exception=failure_exception,
            execution_exit_code=execution_exit_code,
            host_workspace_root=host_workspace_root,
        )
    except TransactionalCleanupError:
        raise
    except Exception as exc:
        return _reject_and_restore_transaction(
            agent,
            result,
            gate,
            decision,
            transaction,
            before,
            after,
            evidence,
            evaluation,
            failure_kind="cgr_postcondition_error",
            failure_exception=exc,
            execution_exit_code=execution_exit_code,
            host_workspace_root=host_workspace_root,
        )


def _reject_and_restore_transaction(
    agent: Any,
    result: Any,
    gate: PhaseGate,
    decision: GateDecision,
    transaction: dict[str, Any],
    before: FileSnapshot,
    after: FileSnapshot | None,
    evidence: RepositoryEvidence,
    evaluation: EditEvaluation | None,
    *,
    failure_kind: str,
    failure_exception: Exception | None,
    execution_exit_code: int | None,
    host_workspace_root: Path | None,
) -> Any:
    transaction["failure_kind"] = failure_kind
    transaction["rollback_attempted"] = True
    gate.update_transaction(transaction)
    try:
        _restore_target(agent, before, gate.snapshot_python, host_workspace_root)
        transaction["rollback_succeeded"] = True
    except Exception as exc:
        if _is_timeout_exception(exc):
            transaction.update(
                _timeout_details(
                    owner="cgr_rollback",
                    operation="restore_target_snapshot",
                    seconds=10,
                    command_preview=f"restore {gate.target}",
                    model_action_completed=bool(
                        transaction.get("execution_returned_normally")
                    ),
                )
            )
        return _transaction_cleanup_failure(
            agent,
            result,
            gate,
            decision,
            transaction,
            before,
            evidence,
            failure_kind="cgr_rollback_error",
            execution_exit_code=execution_exit_code,
            failure_exception=exc,
        )
    try:
        restored = _snapshot_target(
            agent, before.path, gate.snapshot_python, host_workspace_root
        )
        if not _snapshots_equal(before, restored):
            raise RuntimeError("Restored snapshot did not match original bytes and mode.")
        transaction["rollback_verified"] = True
        transaction["restored_fingerprint"] = restored.fingerprint
    except Exception as exc:
        if _is_timeout_exception(exc):
            transaction.update(
                _timeout_details(
                    owner="cgr_rollback",
                    operation="verify_restored_snapshot",
                    seconds=10,
                    command_preview=f"verify restoration {gate.target}",
                    model_action_completed=bool(
                        transaction.get("execution_returned_normally")
                    ),
                )
            )
        return _transaction_cleanup_failure(
            agent,
            result,
            gate,
            decision,
            transaction,
            before,
            evidence,
            failure_kind="cgr_rollback_verification_error",
            execution_exit_code=execution_exit_code,
            failure_exception=exc,
        )
    if failure_exception is not None:
        transaction["execution_exception_category"] = type(failure_exception).__name__
    completed = gate.finish_transaction(
        transaction,
        status="rejected",
        failure_kind=failure_kind,
        cleanup_verified=True,
        updates=transaction,
    )
    diff_evidence = (
        evaluation.evidence
        if evaluation is not None
        else (_diff_evidence(before, after, target=gate.target) if after else None)
    )
    if failure_kind == "postcondition_rejected" and evaluation is not None:
        feedback = gate.transactional_rejection_feedback(
            evaluation,
            observation=_normalize_observation_text(getattr(result, "observation", "")),
            execution_exit_code=execution_exit_code,
        )
        failures = evaluation.failures
    else:
        feedback = gate.transactional_failure_feedback(
            failure_kind=failure_kind,
            target_changed=bool(diff_evidence and diff_evidence.target_changed),
            rollback_verified=True,
        )
        failures = (failure_kind,)
    result.observation = feedback
    result.state = agent.tools.get_state(env=agent._env)
    gate.record_execution(
        decision,
        observation=feedback,
        evidence=RepositoryEvidence(),
        accepted=False,
        rolled_back=True,
        pre_action_fingerprint=before.fingerprint,
        post_action_fingerprint=after.fingerprint if after else None,
        postcondition_failures=failures,
        diff_evidence=diff_evidence,
        execution_exit_code=execution_exit_code,
        transaction_details=completed,
    )
    return result


def _transaction_cleanup_failure(
    agent: Any,
    result: Any,
    gate: PhaseGate,
    decision: GateDecision,
    transaction: dict[str, Any],
    before: FileSnapshot,
    evidence: RepositoryEvidence,
    *,
    failure_kind: str,
    execution_exit_code: int | None,
    failure_exception: Exception | None = None,
) -> Any:
    transaction["failure_kind"] = failure_kind
    if failure_exception is not None:
        transaction["execution_exception_category"] = type(failure_exception).__name__
    incomplete = gate.finish_transaction(
        transaction,
        status="cleanup_incomplete",
        failure_kind=failure_kind,
        cleanup_verified=False,
        updates=transaction,
    )
    feedback = gate.transactional_failure_feedback(
        failure_kind=failure_kind,
        target_changed=bool(evidence.target_diff),
        rollback_verified=False,
    )
    result.observation = feedback
    try:
        result.state = agent.tools.get_state(env=agent._env)
    except Exception:
        result.state = {}
    gate.record_execution(
        decision,
        observation=feedback,
        evidence=evidence,
        accepted=False,
        rolled_back=False,
        pre_action_fingerprint=before.fingerprint,
        postcondition_failures=(failure_kind,),
        execution_exit_code=execution_exit_code,
        transaction_details=incomplete,
    )
    raise TransactionalCleanupError(
        f"Transactional cleanup could not be verified: {failure_kind}."
    )


def _is_timeout_exception(exc: BaseException) -> bool:
    return isinstance(exc, (TimeoutError, subprocess.TimeoutExpired)) or "timeout" in type(
        exc
    ).__name__.lower()


def _execution_timeout_seconds(agent: Any) -> int | float | None:
    tools = getattr(agent, "tools", None)
    config = getattr(tools, "config", None)
    value = getattr(config, "execution_timeout", None)
    return value if isinstance(value, (int, float)) else None


def _timeout_details(
    *,
    owner: str,
    operation: str,
    seconds: int | float | None,
    command_preview: str,
    model_action_completed: bool,
) -> dict[str, Any]:
    return {
        "timeout_owner": owner,
        "timeout_operation": operation,
        "timeout_seconds": seconds,
        "timeout_command_preview": _redact_sensitive_text(command_preview)[:500],
        "model_action_completed_before_timeout": model_action_completed,
    }


def _ensure_command_quiescence(agent: Any) -> bool:
    environment = getattr(agent, "_env", None)
    interrupt = getattr(environment, "interrupt_session", None)
    if not callable(interrupt):
        return False
    try:
        interrupt()
    except Exception:
        close = getattr(environment, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
        return False
    return True


def install_sweagent_phase_gate() -> None:
    config_path = os.getenv("CGR_PHASE_GATE_CONFIG")
    if not config_path:
        return
    path = Path(config_path)
    if not path.is_absolute() or not path.is_file():
        raise RuntimeError("CGR_PHASE_GATE_CONFIG must name an absolute readable file.")
    from sweagent.agent.agents import DefaultAgent  # type: ignore[import-not-found]

    if getattr(DefaultAgent.handle_action, "_cgr_phase_gate", False):
        return
    config = json.loads(path.read_text(encoding="utf-8"))
    required = {"initial_phase", "target", "focused_test", "log_path"}
    if not isinstance(config, dict) or not required.issubset(config):
        raise RuntimeError("CGR phase-gate configuration is incomplete.")
    original = DefaultAgent.handle_action
    host_workspace_root = (
        Path(str(config["host_workspace_root"]))
        if config.get("host_workspace_root")
        else None
    )

    def handle_action(self: Any, step: Any) -> Any:
        gate = getattr(self, "_cgr_phase_gate_state", None)
        if gate is None:
            gate = PhaseGate(
                phase=str(config["initial_phase"]),
                target=str(config["target"]),
                focused_test=str(config["focused_test"]),
                log_path=Path(str(config["log_path"])),
                state_path=Path(str(config["state_path"]))
                if config.get("state_path")
                else None,
                edit_policy=EditPolicy.from_mapping(config.get("edit_policy")),
                snapshot_python=str(config.get("snapshot_python", "python")),
                verifier_failure_evidence=config.get("verifier_failure_evidence"),
            )
            self._cgr_phase_gate_state = gate
        evidence = _probe_repository(self, gate.target)
        model_text = _normalize_observation_text(
            getattr(step, "thought", "") or getattr(step, "output", "")
        )
        decision = gate.decide(step.action, evidence, model_text=model_text)
        if not decision.allowed:
            step.observation = decision.feedback or "Action rejected by CGR."
            step.state = self.tools.get_state(env=self._env)
            return step
        if decision.candidate.kind == "noninteractive_edit":
            return _handle_transactional_edit(
                self,
                step,
                gate,
                decision,
                original,
                host_workspace_root,
            )
        result = original(self, step)
        if not result.done or decision.candidate.kind != "submission":
            evidence = _probe_repository(self, gate.target)
        gate.record_execution(
            decision,
            observation=_normalize_observation_text(result.observation),
            evidence=evidence,
        )
        if decision.phase == "inspect" and gate.phase == "edit":
            source_observation = _normalize_observation_text(result.observation)
            result.observation = (
                source_observation.rstrip()
                + "\n\n"
                + gate.phase_transition_coaching()
            )
        return result

    handle_action._cgr_phase_gate = True  # type: ignore[attr-defined]
    DefaultAgent.handle_action = handle_action


def _snapshot_file(
    agent: Any, target: str, python_command: str = "python", *, timeout: int = 10
) -> FileSnapshot:
    path_literal = json.dumps(target)
    script = (
        "import base64,json,os,pathlib,stat;"
        f"p=pathlib.Path({path_literal});"
        "e=p.exists();"
        "d=base64.b64encode(p.read_bytes()).decode() if e else '';"
        "m=stat.S_IMODE(p.stat().st_mode) if e else None;"
        "print(json.dumps({'existed':e,'content':d,'mode':m}))"
    )
    output = _normalize_observation_text(
        _bounded_communicate(
            agent._env,
            shlex.quote(python_command) + " -c " + shlex.quote(script),
            check="raise",
            timeout=timeout,
        )
    )
    payload = _last_json_mapping(output)
    return FileSnapshot(
        path=target,
        existed=bool(payload["existed"]),
        content=base64.b64decode(str(payload["content"])),
        mode=int(payload["mode"]) if payload.get("mode") is not None else None,
    )


def _restore_snapshot(
    agent: Any,
    snapshot: FileSnapshot,
    python_command: str = "python",
    *,
    timeout: int = 10,
) -> None:
    path_literal = json.dumps(snapshot.path)
    content_literal = json.dumps(base64.b64encode(snapshot.content).decode())
    if snapshot.existed:
        script = (
            "import base64,os,pathlib;"
            f"p=pathlib.Path({path_literal});p.parent.mkdir(parents=True,exist_ok=True);"
            f"p.write_bytes(base64.b64decode({content_literal}));"
        )
        if snapshot.mode is not None:
            script += f"os.chmod(p,{snapshot.mode});"
    else:
        script = (
            "import pathlib;"
            f"p=pathlib.Path({path_literal});"
            "p.unlink() if p.exists() or p.is_symlink() else None;"
        )
    _bounded_communicate(
        agent._env,
        shlex.quote(python_command) + " -c " + shlex.quote(script),
        check="raise",
        timeout=timeout,
    )


def _snapshot_target(
    agent: Any,
    target: str,
    python_command: str,
    host_workspace_root: Path | None,
) -> FileSnapshot:
    if host_workspace_root is not None:
        return _snapshot_host_target(host_workspace_root, target)
    return _snapshot_file(agent, target, python_command)


def _snapshot_host_target(workspace: Path, target: str) -> FileSnapshot:
    path = _confined_target_path(workspace, target)
    existed = path.is_file()
    return FileSnapshot(
        path=target,
        existed=existed,
        content=path.read_bytes() if existed else b"",
        mode=stat.S_IMODE(path.stat().st_mode) if existed else None,
    )


def _restore_target(
    agent: Any,
    snapshot: FileSnapshot,
    python_command: str,
    host_workspace_root: Path | None,
) -> None:
    if host_workspace_root is not None:
        _restore_host_target(host_workspace_root, snapshot)
        return
    _restore_snapshot(agent, snapshot, python_command)


def _restore_host_target(workspace: Path, snapshot: FileSnapshot) -> None:
    path = _confined_target_path(workspace, snapshot.path)
    if snapshot.existed:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(snapshot.content)
        if snapshot.mode is not None:
            os.chmod(path, snapshot.mode)
    elif path.exists() or path.is_symlink():
        path.unlink()


def _target_evidence_from_snapshots(
    before: FileSnapshot, after: FileSnapshot, *, limit: int = 12000
) -> RepositoryEvidence:
    if _snapshots_equal(before, after):
        return RepositoryEvidence()
    before_text = before.content.decode("utf-8", errors="replace")
    after_text = after.content.decode("utf-8", errors="replace")
    diff = "".join(
        difflib.unified_diff(
            before_text.splitlines(keepends=True),
            after_text.splitlines(keepends=True),
            fromfile=f"a/{before.path}",
            tofile=f"b/{after.path}",
        )
    )
    if len(diff) > limit:
        diff = diff[:limit] + "\n... CGR target diff truncated ...\n"
    return RepositoryEvidence(target_diff=diff, tracked_diff=diff)


def _snapshots_equal(left: FileSnapshot, right: FileSnapshot) -> bool:
    return (
        left.existed == right.existed
        and left.content == right.content
        and left.mode == right.mode
    )


def _bounded_communicate(
    environment: Any, command: str, *, check: str, timeout: int
) -> Any:
    try:
        return environment.communicate(
            input=command,
            check=check,
            timeout=timeout,
        )
    except TypeError as exc:
        if "unexpected keyword argument" not in str(exc):
            raise
        return environment.communicate(command, check=check)


def _probe_repository(agent: Any, target: str) -> RepositoryEvidence:
    env = agent._env
    if env is None:
        return RepositoryEvidence()
    quoted = shlex.quote(target)
    target_diff = _normalize_observation_text(
        env.communicate(f"git diff -- {quoted}", check="ignore")
    )
    tracked_diff = _normalize_observation_text(
        env.communicate("git diff --binary HEAD --", check="ignore")
    )
    return RepositoryEvidence(target_diff=target_diff, tracked_diff=tracked_diff)


def _extract_write_targets(action: str) -> tuple[str, ...]:
    return _analyze_shell_action(action).write_targets


def _analyze_shell_action(action: str) -> _ShellActionAnalysis:
    parsed = _parse_shell_commands(action)
    commands = parsed.commands if parsed.failure_category is None else ()
    targets: list[str] = []
    selected: _ParsedShellCommand | None = None

    def add(value: str, command: _ParsedShellCommand) -> None:
        nonlocal selected
        normalized = _normalize_target(value)
        if normalized and normalized not in targets and not _ambiguous_target(normalized):
            targets.append(normalized)
            selected = selected or command

    for command in commands:
        for target in command.output_targets:
            add(target, command)
        argv = command.argv
        executable = command.executable
        if executable == "touch":
            for token in argv[1:]:
                if not token.startswith("-"):
                    add(token, command)
        elif executable == "tee":
            for token in argv[1:]:
                if not token.startswith("-"):
                    add(token, command)
        elif executable == "sed":
            for target in _sed_in_place_targets(argv):
                add(target, command)
        elif executable == "perl" and any(
            "i" in token[1:] for token in argv[1:] if token.startswith("-")
        ):
            if len(argv) >= 3:
                add(argv[-1], command)
        elif executable and re.fullmatch(r"python(?:3(?:\.\d+)*)?(?:\.exe)?", executable):
            python_source = "\n".join((" ".join(argv[1:]), command.heredoc_body))
            _add_python_write_targets(python_source, lambda value: add(value, command))
        elif executable == "apply_patch":
            for match in re.finditer(
                r"^\+\+\+\s+(?:[ab]/)?([^\s]+)", command.heredoc_body, re.M
            ):
                if match.group(1) != "/dev/null":
                    add(match.group(1), command)

    redirections = tuple(
        dict.fromkeys(operator for command in commands for operator in command.redirections)
    )
    heredoc = next(
        (command.heredoc_delimiter for command in commands if command.heredoc_delimiter),
        None,
    )
    selected = selected or (commands[0] if commands else None)
    return _ShellActionAnalysis(
        commands=commands,
        write_targets=tuple(targets),
        parsed_executable=selected.executable if selected else None,
        parsed_argv=_bounded_argv(selected.argv) if selected else (),
        redirection_operators=redirections,
        heredoc_delimiter=heredoc,
        command_multiline="\n" in action.replace("\r", ""),
        physical_line_count=action.replace("\r\n", "\n").replace("\r", "\n").count("\n")
        + 1,
        quote_closed=parsed.quote_closed,
        continuation_detected=parsed.continuation_detected,
        parse_status="error" if parsed.failure_category else "parsed",
        parse_failure_category=parsed.failure_category,
    )


def _parse_shell_commands(action: str) -> _ShellParseResult:
    lines = action.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    commands: list[_ParsedShellCommand] = []
    continuation_detected = False
    index = 0
    while index < len(lines):
        unit: list[str] = []
        quote: str | None = None
        while index < len(lines):
            line, quote, continued = _consume_shell_physical_line(lines[index], quote)
            unit.append(line)
            continuation_detected = continuation_detected or continued
            index += 1
            if quote is None and not continued:
                break
            if index < len(lines) and quote is not None and not continued:
                unit.append("\n")
        if quote is not None:
            return _ShellParseResult(
                commands=tuple(commands),
                quote_closed=False,
                continuation_detected=continuation_detected,
                failure_category="unterminated_quote",
            )
        logical_command = "".join(unit)
        if not logical_command.strip():
            continue
        parsed: list[_ParsedShellCommand] = []
        for segment in _split_shell_control_segments(logical_command):
            try:
                tokens = _shell_tokens(segment)
            except ValueError:
                return _ShellParseResult(
                    commands=tuple(commands),
                    quote_closed=True,
                    continuation_detected=continuation_detected,
                    failure_category="tokenization_error",
                )
            command = _parse_shell_segment(tokens)
            if command is not None:
                parsed.append(command)
        commands.extend(parsed)
        heredoc_index = next(
            (
                offset
                for offset, command in enumerate(parsed)
                if command.heredoc_delimiter is not None
            ),
            None,
        )
        if heredoc_index is not None:
            command = parsed[heredoc_index]
            assert command.heredoc_delimiter is not None
            body: list[str] = []
            strip_tabs = "<<-" in command.redirections
            delimiter_found = False
            while index < len(lines):
                candidate = lines[index].lstrip("\t") if strip_tabs else lines[index]
                if candidate == command.heredoc_delimiter:
                    delimiter_found = True
                    index += 1
                    break
                body.append(lines[index])
                index += 1
            if not delimiter_found:
                return _ShellParseResult(
                    commands=tuple(commands),
                    quote_closed=True,
                    continuation_detected=continuation_detected,
                    failure_category="unterminated_heredoc",
                )
            absolute_index = len(commands) - len(parsed) + heredoc_index
            commands[absolute_index] = _ParsedShellCommand(
                executable=command.executable,
                argv=command.argv,
                redirections=command.redirections,
                output_targets=command.output_targets,
                heredoc_delimiter=command.heredoc_delimiter,
                heredoc_body="\n".join(body),
            )
    return _ShellParseResult(
        commands=tuple(commands),
        quote_closed=True,
        continuation_detected=continuation_detected,
    )


def _consume_shell_physical_line(
    line: str, quote: str | None
) -> tuple[str, str | None, bool]:
    output: list[str] = []
    index = 0
    continuation = False
    while index < len(line):
        character = line[index]
        if quote == "'":
            output.append(character)
            if character == "'":
                quote = None
            index += 1
            continue
        if character == "\\":
            if index + 1 < len(line):
                output.extend((character, line[index + 1]))
                index += 2
                continue
            continuation = True
            index += 1
            continue
        output.append(character)
        if quote == '"':
            if character == '"':
                quote = None
        elif character in {"'", '"'}:
            quote = character
        index += 1
    return "".join(output), quote, continuation


def _shell_tokens(line: str) -> list[str]:
    lexer = shlex.shlex(line, posix=True, punctuation_chars="<>")
    lexer.whitespace_split = True
    lexer.commenters = ""
    return list(lexer)


def _split_shell_control_segments(line: str) -> list[str]:
    segments: list[str] = []
    current: list[str] = []
    quote: str | None = None
    escaped = False
    index = 0
    while index < len(line):
        character = line[index]
        if escaped:
            current.append(character)
            escaped = False
            index += 1
            continue
        if character == "\\" and quote != "'":
            current.append(character)
            escaped = True
            index += 1
            continue
        if quote is not None:
            current.append(character)
            if character == quote:
                quote = None
            index += 1
            continue
        if character in {"'", '"'}:
            quote = character
            current.append(character)
            index += 1
            continue
        if character in {";", "|", "&"}:
            previous = line[index - 1] if index else ""
            following = line[index + 1] if index + 1 < len(line) else ""
            if previous in {"<", ">"} or (character == "&" and following == ">"):
                current.append(character)
                index += 1
                continue
            width = 2 if following == character and character in {"|", "&"} else 1
            segment = "".join(current).strip()
            if segment:
                segments.append(segment)
            current = []
            index += width
            continue
        current.append(character)
        index += 1
    segment = "".join(current).strip()
    if segment:
        segments.append(segment)
    return segments


def _parse_shell_segment(tokens: list[str]) -> _ParsedShellCommand | None:
    argv: list[str] = []
    redirections: list[str] = []
    output_targets: list[str] = []
    heredoc_delimiter: str | None = None
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.isdigit() and index + 1 < len(tokens) and _is_redirection(tokens[index + 1]):
            index += 1
            continue
        if _is_redirection(token):
            operator = token
            destination = tokens[index + 1] if index + 1 < len(tokens) else ""
            if operator == "<<" and destination.startswith("-"):
                operator = "<<-"
                destination = destination[1:]
            elif operator in {"<", ">"} and destination.startswith("&"):
                operator += "&"
                destination = destination[1:]
            elif operator == ">" and destination.startswith("|"):
                operator = ">|"
                destination = destination[1:]
            redirections.append(operator)
            if operator in {">", ">>", ">|"} and destination:
                output_targets.append(destination)
            elif operator in {"<<", "<<-"} and destination:
                heredoc_delimiter = destination
            index += 2 if destination else 1
            continue
        argv.append(token)
        index += 1
    if not argv and not redirections:
        return None
    executable = Path(argv[0]).name.lower() if argv else None
    return _ParsedShellCommand(
        executable=executable,
        argv=tuple(argv),
        redirections=tuple(redirections),
        output_targets=tuple(output_targets),
        heredoc_delimiter=heredoc_delimiter,
    )


def _is_redirection(token: str) -> bool:
    return token in {"<", ">", ">>", ">|", "<<", "<<<", "<&", ">&", "<>"}


def _sed_in_place_targets(argv: tuple[str, ...]) -> tuple[str, ...]:
    in_place = False
    expression_supplied = False
    operands: list[str] = []
    options = True
    index = 1
    while index < len(argv):
        token = argv[index]
        if options and token == "--":
            options = False
            index += 1
            continue
        if options and token in {"-e", "--expression", "-f", "--file"}:
            expression_supplied = True
            index += 2
            continue
        if options and (
            token.startswith("--expression=") or token.startswith("--file=")
        ):
            expression_supplied = True
            index += 1
            continue
        if options and token.startswith("-e") and token != "-e":
            expression_supplied = True
            index += 1
            continue
        if options and token.startswith("-f") and token != "-f":
            expression_supplied = True
            index += 1
            continue
        if options and (
            token == "--in-place"
            or token.startswith("--in-place=")
            or (token.startswith("-") and not token.startswith("--") and "i" in token[1:])
        ):
            in_place = True
            if token == "-i" and index + 1 < len(argv) and argv[index + 1] == "":
                index += 2
            else:
                index += 1
            continue
        if options and token.startswith("-"):
            index += 1
            continue
        operands.append(token)
        index += 1
    if not in_place:
        return ()
    if not expression_supplied and operands:
        operands.pop(0)
    return tuple(operands)


def _add_python_write_targets(source: str, add: Any) -> None:
    patterns = (
        r"\bopen\(\s*(['\"])(?P<path>.+?)\1\s*,\s*(['\"])[wax][bt+]*\3",
        r"\bPath\(\s*(['\"])(?P<path>.+?)\1\s*\)\s*\.\s*(?:write_text|write_bytes)\b",
        r"\bPath\(\s*(['\"])(?P<path>.+?)\1\s*\)\s*\.\s*open"
        r"\(\s*(['\"])[wax][bt+]*\3",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, source, re.I):
            add(match.group("path"))


def _bounded_argv(argv: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(_redact_sensitive_text(token)[:500] for token in argv[:64])


def _has_write_intent(action: str) -> bool:
    return bool(
        re.search(
            r"(?:sed\s+-i|apply_patch|perl\s+-[pi]|\b(?:touch|tee)\b|(?:>|>>)|"
            r"python[^\n]*(?:write_text|write_bytes|open\([^)]*,\s*['\"][wax+]))",
            action,
            re.I,
        )
    )


def _appears_to_be_supported_edit(action: str) -> bool:
    return _has_write_intent(action) and bool(
        re.search(
            r"(?:^|[;&|\n])\s*(?:command\s+)?(?:sed|perl|python(?:3(?:\.\d+)*)?"
            r"(?:\.exe)?|touch|tee|cat|apply_patch)\b",
            action,
            re.I,
        )
    )


def _normalize_target(value: str) -> str:
    normalized = value.strip().strip("'\"").replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.rstrip(",)")


def _ambiguous_target(value: str) -> bool:
    return value in {"", "/dev/null", "&1", "&2", "-"} or bool(
        re.search(r"[$*?{}()]", value)
    )


def _looks_like_test_path(value: str) -> bool:
    lowered = value.lower()
    name = Path(lowered).name
    return name.startswith("test_") or "/tests/" in f"/{lowered}/" or "testcases/" in lowered


def _normalize_observation_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, dict):
        for key in ("output", "stdout", "observation"):
            if key in value:
                return _normalize_observation_text(value[key])
    for attribute in ("output", "stdout", "observation"):
        if hasattr(value, attribute):
            return _normalize_observation_text(getattr(value, attribute))
    return str(value)


def _bounded_execution_evidence(observation: str, exit_code: int | None) -> str:
    normalized = " ".join(_normalize_observation_text(observation).split())
    normalized = _redact_sensitive_text(normalized)
    parts: list[str] = []
    if exit_code is not None:
        parts.append(f"Execution exit code: {exit_code}.")
    if normalized:
        parts.append(f"Bounded shell output: {normalized[:400]}")
    return " ".join(parts)


def _redact_sensitive_text(value: str) -> str:
    redacted = re.sub(
        r"(?i)(authorization\s*:\s*)(?:bearer\s+)?\S+",
        r"\1<redacted>",
        value,
    )
    redacted = re.sub(r"(?i)\bbearer\s+\S+", "Bearer <redacted>", redacted)
    return re.sub(
        r"(?i)(api[_-]?key|token)(\s*[:=]\s*)(\S+)",
        r"\1\2<redacted>",
        redacted,
    )


def _last_json_mapping(value: str) -> dict[str, Any]:
    for line in reversed(value.splitlines()):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise RuntimeError("SWE-agent file snapshot did not return a JSON object.")


def _snapshot_payload(snapshot: FileSnapshot) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "path": snapshot.path,
        "existed": snapshot.existed,
        "content_base64": base64.b64encode(snapshot.content).decode("ascii"),
        "mode": snapshot.mode,
        "fingerprint": snapshot.fingerprint,
    }


def _snapshot_from_payload(value: Any) -> FileSnapshot:
    if not isinstance(value, dict):
        raise ValueError("Transaction snapshot payload is missing.")
    path = value.get("path")
    content = value.get("content_base64")
    if not isinstance(path, str) or not isinstance(content, str):
        raise ValueError("Transaction snapshot payload is invalid.")
    snapshot = FileSnapshot(
        path=path,
        existed=bool(value.get("existed")),
        content=base64.b64decode(content, validate=True),
        mode=int(value["mode"]) if value.get("mode") is not None else None,
    )
    if value.get("fingerprint") != snapshot.fingerprint:
        raise ValueError("Transaction snapshot fingerprint does not match its content.")
    return snapshot


def _confined_target_path(workspace: Path, target: str) -> Path:
    root = workspace.absolute()
    candidate = (root / _normalize_target(target)).resolve(strict=False)
    try:
        candidate.relative_to(root.resolve(strict=True))
    except ValueError as exc:
        raise ValueError("Transaction target escapes the workspace.") from exc
    return candidate


def _bounded_transaction_details(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    allowed = {
        "transaction_id",
        "target",
        "candidate_fingerprint",
        "candidate_kind",
        "status",
        "start_event_index",
        "snapshot_path",
        "pre_action_fingerprint",
        "pre_action_mode",
        "execution_attempted",
        "execution_returned_normally",
        "execution_exception_category",
        "validation_attempted",
        "validation_completed",
        "rollback_attempted",
        "rollback_succeeded",
        "rollback_verified",
        "restored_fingerprint",
        "failure_kind",
        "timeout_owner",
        "timeout_operation",
        "timeout_seconds",
        "timeout_command_preview",
        "model_action_completed_before_timeout",
        "transaction_closed",
    }
    bounded = {key: value.get(key) for key in sorted(allowed)}
    preview = bounded.get("timeout_command_preview")
    if isinstance(preview, str):
        bounded["timeout_command_preview"] = _redact_sensitive_text(preview)[:500]
    return bounded


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _kind_label(kind: str) -> str:
    return {
        "focused_pytest": "Testing with pytest",
        "unrelated_pytest": "An unrelated pytest invocation",
        "focused_unittest": "Testing with unittest",
        "unrelated_unittest": "An unrelated unittest invocation",
        "interactive_editor": "An interactive editor",
        "commit": "A commit",
        "push": "A push",
        "submission": "Submission",
    }.get(kind, f"Action kind {kind!r}")


def _declares_source_change(model_text: str, target: str) -> bool:
    if not model_text.strip():
        return False
    lowered = model_text.lower()
    target_name = Path(target).name.lower()
    intent = bool(
        re.search(
            r"\b(?:change|edit|implement|modify|replace|rewrite|update|fix)\w*\b",
            lowered,
        )
    )
    source_context = target_name in lowered or bool(
        re.search(r"\b(?:source|implementation|function|method|class|code)\b", lowered)
    )
    return intent and source_context


def _test_passed(observation: str) -> bool:
    return bool(re.search(r"\b\d+\s+passed\b", observation, re.I)) and not bool(
        re.search(r"\b\d+\s+failed\b|^FAILED\s", observation, re.I | re.M)
    )


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:16]
