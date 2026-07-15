"""Generic workflow-phase gate for official SWE-agent shell actions."""

from __future__ import annotations

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
    ) -> None:
        if phase not in PHASES:
            raise ValueError(f"Unsupported required phase: {phase}")
        self.phase = phase
        self.target = target
        self.focused_test = focused_test
        self.log_path = log_path
        self.target_inspected = False
        self.target_diff_inspected = False
        self.last_failed_test_fingerprint: str | None = None
        self.last_failed_diff_fingerprint: str | None = None

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
        self.record(decision, executed=False, phase_after=self.phase)
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

    def record_execution(
        self,
        decision: GateDecision,
        *,
        observation: str,
        evidence: RepositoryEvidence,
    ) -> None:
        phase_before = self.phase
        kind = decision.candidate.kind
        if phase_before == "inspect" and kind == "target_confirmation":
            self.target_inspected = True
            self.phase = "edit"
        elif phase_before in {"edit", "confirm_edit"} and kind == "edit_target_file":
            self.target_inspected = False
            self.target_diff_inspected = False
            self.phase = "confirm_edit" if evidence.target_diff.strip() else "edit"
        elif phase_before == "confirm_edit" and kind == "target_confirmation":
            self.target_inspected = True
        elif phase_before == "confirm_edit" and kind == "git_diff":
            self.target_diff_inspected = bool(evidence.target_diff.strip())
        if self.phase == "confirm_edit" and self.target_inspected and self.target_diff_inspected:
            self.phase = "test"
        if phase_before == "test" and kind == "focused_test":
            if _test_passed(observation):
                self.last_failed_test_fingerprint = None
                self.last_failed_diff_fingerprint = None
                self.phase = "final_diff"
            else:
                self.last_failed_test_fingerprint = _fingerprint(observation)
                self.last_failed_diff_fingerprint = _fingerprint(evidence.target_diff)
        elif phase_before == "final_diff" and kind == "git_diff" and evidence.tracked_diff.strip():
            self.phase = "submit"
        self.record(
            decision,
            executed=True,
            phase_after=self.phase,
            observation=observation,
            evidence=evidence,
        )

    def record(
        self,
        decision: GateDecision,
        *,
        executed: bool,
        phase_after: str,
        observation: str = "",
        evidence: RepositoryEvidence | None = None,
    ) -> None:
        if self.log_path is None:
            return
        payload = {
            "phase_before": decision.phase,
            "phase_after": phase_after,
            "allowed": decision.allowed,
            "executed": executed,
            "candidate": asdict(decision.candidate),
            "feedback": decision.feedback,
            "observation_preview": observation[:1000],
            "target_diff_nonempty": bool(evidence and evidence.target_diff.strip()),
            "tracked_diff_nonempty": bool(evidence and evidence.tracked_diff.strip()),
        }
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")


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
            )
            self._cgr_phase_gate_state = gate
        evidence = _probe_repository(self, gate.target)
        decision = gate.decide(step.action, evidence)
        if not decision.allowed:
            step.observation = decision.feedback or "Action rejected by CGR."
            step.state = self.tools.get_state(env=self._env)
            return step
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
