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
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


PHASES = ("inspect", "edit", "confirm_edit", "test", "final_diff", "submit")


@dataclass(frozen=True)
class CandidateAction:
    raw: str
    kind: str
    targets_required_file: bool
    references_required_file: bool = False
    write_targets: tuple[str, ...] = ()
    targets_test_file: bool = False
    targets_unrelated_files: bool = False


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
    write_targets = _extract_write_targets(action)
    normalized_target = _normalize_target(target)
    normalized_test = _normalize_target(focused_test)
    targets = normalized_target in write_targets
    references_target = bool(normalized_target and normalized_target in action.replace("\\", "/"))
    targets_test = normalized_test in write_targets or any(
        _looks_like_test_path(path) for path in write_targets
    )
    unrelated = any(path != normalized_target for path in write_targets)
    if re.search(r"(?:^|\s)git\s+(?:commit|push)(?:\s|$)", normalized):
        kind = "commit" if re.search(r"(?:^|\s)git\s+commit(?:\s|$)", normalized) else "push"
    elif re.search(r"<<SWE_AGENT_SUBMISSION>>|model\.patch|^submit$", action, re.I):
        kind = "submission"
    elif re.search(r"(?:^|\s)git\s+diff(?:\s|$)", normalized):
        kind = "git_diff"
    elif write_targets:
        if targets and unrelated:
            kind = "edit_mixed_targets"
        elif targets:
            kind = "edit_target_file"
        else:
            kind = "edit_wrong_file"
    elif _has_write_intent(action):
        kind = "unknown"
    elif re.search(r"(?:^|[\s;&|])(?:pytest|[^\s]+\s+-m\s+pytest)(?:[\s;&|]|$)", normalized):
        kind = "focused_test" if focused_test and focused_test in action else "unrelated_test"
    elif _is_inspection(action):
        kind = "target_confirmation" if references_target else "inspection"
    else:
        kind = "unknown"
    return CandidateAction(
        action,
        kind,
        targets,
        references_target,
        write_targets,
        targets_test,
        unrelated,
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
        self.target_inspected = False
        self.target_diff_inspected = False
        self.last_failed_test_fingerprint: str | None = None
        self.last_failed_diff_fingerprint: str | None = None
        self.state = build_initial_phase_state(
            initial_phase=phase, target=target, edit_policy=self.edit_policy
        )
        self._persist_state()

    def decide(self, action: str, evidence: RepositoryEvidence) -> GateDecision:
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
            elif candidate.kind != "edit_target_file":
                reason = f"{_kind_label(candidate.kind)} cannot satisfy the edit phase."
        elif self.phase == "confirm_edit":
            if candidate.kind == "edit_target_file":
                pass
            elif candidate.kind == "target_confirmation":
                pass
            elif candidate.kind == "git_diff" and candidate.references_required_file:
                if not evidence.target_diff.strip():
                    reason = "The target-specific diff is empty; the edit has not taken effect."
            else:
                reason = f"{_kind_label(candidate.kind)} cannot satisfy confirm_edit."
        elif self.phase == "test":
            if candidate.kind != "focused_test":
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
        lines.extend((f"Candidate kind: {candidate.kind}", "", reason))
        if candidate.kind in {"edit_wrong_file", "edit_mixed_targets"}:
            constraint = (
                f"Return exactly one noninteractive command that edits {self.target}. "
                "Do not create or modify tests, commit, submit, or run tests."
            )
        elif candidate.kind in {"commit", "push"}:
            constraint = (
                f"The {candidate.kind} was not executed and the required phase remains {self.phase}. "
                "Return exactly one action that satisfies the current phase."
            )
        else:
            constraint = (
                "The proposed action was not executed. Return exactly one action that satisfies "
                "the required phase."
            )
        lines.extend((constraint, "Do not describe multiple future commands."))
        return "\n".join(lines)

    def transactional_rejection_feedback(self, evaluation: EditEvaluation) -> str:
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
        }
        grounded = [descriptions[item] for item in evaluation.failures if item in descriptions]
        reason = " and ".join(grounded[:2]) or "It did not satisfy the configured edit policy"
        return (
            "ACTION REJECTED BY CGR\n\n"
            f"Required phase: edit\nRequired target: {self.target}\n"
            "Candidate kind: edit_target_file\n\n"
            "The command executed, but its resulting change was rejected and rolled back. "
            f"{reason}. The required phase remains edit.\n"
            f"Return exactly one noninteractive command that changes the existing production "
            f"implementation in {self.target}. Do not add tests, commit, submit, or run tests."
        )

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
    ) -> None:
        phase_before = self.phase
        kind = decision.candidate.kind
        if phase_before == "inspect" and kind == "target_confirmation":
            self.target_inspected = True
            self.state["target_inspected"] = True
            self.phase = "edit"
        elif phase_before in {"edit", "confirm_edit"} and kind == "edit_target_file":
            prior_edit_accepted = bool(self.state["accepted_target_edit"])
            self.target_inspected = False
            self.target_diff_inspected = False
            self.state["target_inspected"] = False
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
        elif phase_before == "confirm_edit" and kind == "git_diff":
            self.target_diff_inspected = bool(evidence.target_diff.strip())
            self.state["target_diff_inspected"] = self.target_diff_inspected
        if self.phase == "confirm_edit" and self.target_inspected and self.target_diff_inspected:
            self.phase = "test"
        if phase_before == "test" and kind == "focused_test":
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
    ) -> None:
        self.state["last_event_index"] += 1
        self.state["current_phase"] = phase_after
        payload = {
            "event_index": self.state["last_event_index"],
            "phase_before": decision.phase,
            "phase_after": phase_after,
            "allowed": decision.allowed,
            "executed": executed,
            "accepted": accepted,
            "rolled_back": rolled_back,
            "candidate": asdict(decision.candidate),
            "feedback": decision.feedback,
            "observation_preview": observation[:1000],
            "target_diff_nonempty": bool(evidence and evidence.target_diff.strip()),
            "tracked_diff_nonempty": bool(evidence and evidence.tracked_diff.strip()),
            "pre_action_fingerprint": pre_action_fingerprint,
            "post_action_fingerprint": post_action_fingerprint,
            "postcondition_failures": list(postcondition_failures),
            "diff_evidence": asdict(diff_evidence) if diff_evidence else None,
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
            )
            self._cgr_phase_gate_state = gate
        evidence = _probe_repository(self, gate.target)
        decision = gate.decide(step.action, evidence)
        if not decision.allowed:
            step.observation = decision.feedback or "Action rejected by CGR."
            step.state = self.tools.get_state(env=self._env)
            return step
        if decision.candidate.kind == "edit_target_file":
            before = _snapshot_file(self, gate.target, gate.snapshot_python)
            try:
                result = original(self, step)
            except Exception:
                _restore_snapshot(self, before, gate.snapshot_python)
                raise
            after = _snapshot_file(self, gate.target, gate.snapshot_python)
            post_evidence = _probe_repository(self, gate.target)
            evaluation = evaluate_edit(
                before,
                after,
                target=gate.target,
                policy=gate.edit_policy,
            )
            if not evaluation.accepted:
                _restore_snapshot(self, before, gate.snapshot_python)
                restored_evidence = _probe_repository(self, gate.target)
                feedback = gate.transactional_rejection_feedback(evaluation)
                result.observation = feedback
                result.state = self.tools.get_state(env=self._env)
                gate.record_execution(
                    decision,
                    observation=feedback,
                    evidence=restored_evidence,
                    accepted=False,
                    rolled_back=True,
                    pre_action_fingerprint=before.fingerprint,
                    post_action_fingerprint=after.fingerprint,
                    postcondition_failures=evaluation.failures,
                    diff_evidence=evaluation.evidence,
                )
                return result
            gate.record_execution(
                decision,
                observation=_normalize_observation_text(result.observation),
                evidence=post_evidence,
                accepted=True,
                pre_action_fingerprint=before.fingerprint,
                post_action_fingerprint=after.fingerprint,
                diff_evidence=evaluation.evidence,
            )
            return result
        result = original(self, step)
        if not result.done or decision.candidate.kind != "submission":
            evidence = _probe_repository(self, gate.target)
        gate.record_execution(
            decision,
            observation=_normalize_observation_text(result.observation),
            evidence=evidence,
        )
        return result

    handle_action._cgr_phase_gate = True  # type: ignore[attr-defined]
    DefaultAgent.handle_action = handle_action


def _snapshot_file(
    agent: Any, target: str, python_command: str = "python"
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
        agent._env.communicate(
            shlex.quote(python_command) + " -c " + shlex.quote(script), check="raise"
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
    agent: Any, snapshot: FileSnapshot, python_command: str = "python"
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
    agent._env.communicate(
        shlex.quote(python_command) + " -c " + shlex.quote(script), check="raise"
    )


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
    targets: list[str] = []

    def add(value: str) -> None:
        normalized = _normalize_target(value)
        if normalized and normalized not in targets and not _ambiguous_target(normalized):
            targets.append(normalized)

    for line in action.replace("\r", "").splitlines():
        for segment in re.split(r"\s*(?:&&|\|\||\||;)\s*", line):
            try:
                tokens = shlex.split(segment, posix=True)
            except ValueError:
                continue
            if not tokens:
                continue
            for index, token in enumerate(tokens):
                if token in {">", ">>"} and index + 1 < len(tokens):
                    add(tokens[index + 1])
                elif token.startswith(">>") and len(token) > 2:
                    add(token[2:])
                elif token.startswith(">") and len(token) > 1 and not token.startswith(">&"):
                    add(token[1:])
            command = Path(tokens[0]).name
            if command == "touch":
                for token in tokens[1:]:
                    if not token.startswith("-"):
                        add(token)
            elif command == "tee":
                for token in tokens[1:]:
                    if not token.startswith("-"):
                        add(token)
            elif command == "sed" and any(token.startswith("-i") for token in tokens[1:]):
                _add_sed_targets(tokens, add)
            elif command == "perl" and any("i" in token[1:] for token in tokens[1:] if token.startswith("-")):
                if len(tokens) >= 3:
                    add(tokens[-1])

    for match in re.finditer(
        r"\bopen\(\s*(['\"])(?P<path>.+?)\1\s*,\s*(['\"])[wax][bt+]*\3",
        action,
        re.I,
    ):
        add(match.group("path"))
    for match in re.finditer(
        r"\bPath\(\s*(['\"])(?P<path>.+?)\1\s*\)\s*\.\s*(?:write_text|write_bytes)\b",
        action,
        re.I,
    ):
        add(match.group("path"))
    for match in re.finditer(
        r"\bPath\(\s*(['\"])(?P<path>.+?)\1\s*\)\s*\.\s*open"
        r"\(\s*(['\"])[wax][bt+]*\3",
        action,
        re.I,
    ):
        add(match.group("path"))
    for match in re.finditer(r"^\+\+\+\s+(?:[ab]/)?([^\s]+)", action, re.M):
        if match.group(1) != "/dev/null":
            add(match.group(1))
    return tuple(targets)


def _add_sed_targets(tokens: list[str], add: Any) -> None:
    expression_seen = False
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            for value in tokens[index + 1 :]:
                add(value)
            return
        if token in {"-e", "--expression"} and index + 1 < len(tokens):
            expression_seen = True
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        if not expression_seen:
            expression_seen = True
        else:
            add(token)
        index += 1


def _has_write_intent(action: str) -> bool:
    return bool(
        re.search(
            r"(?:sed\s+-i|apply_patch|perl\s+-[pi]|\b(?:touch|tee)\b|(?:>|>>)|"
            r"python[^\n]*(?:write_text|write_bytes|open\([^)]*,\s*['\"][wax+]))",
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


def _last_json_mapping(value: str) -> dict[str, Any]:
    for line in reversed(value.splitlines()):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise RuntimeError("SWE-agent file snapshot did not return a JSON object.")


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


def _is_inspection(action: str) -> bool:
    return bool(re.search(r"(?:^|[\s;&|])(?:cat|sed\s+-n|head|tail|rg|grep|ls)(?:[\s;&|]|$)", action))


def _kind_label(kind: str) -> str:
    return {
        "focused_test": "Testing",
        "unrelated_test": "An unrelated test",
        "commit": "A commit",
        "push": "A push",
        "submission": "Submission",
    }.get(kind, f"Action kind {kind!r}")


def _test_passed(observation: str) -> bool:
    return bool(re.search(r"\b\d+\s+passed\b", observation, re.I)) and not bool(
        re.search(r"\b\d+\s+failed\b|^FAILED\s", observation, re.I | re.M)
    )


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:16]
