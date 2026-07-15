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
    targets = bool(target and target in action)
    if re.search(r"(?:^|\s)git\s+(?:commit|push)(?:\s|$)", normalized):
        kind = "commit" if re.search(r"(?:^|\s)git\s+commit(?:\s|$)", normalized) else "push"
    elif re.search(r"<<SWE_AGENT_SUBMISSION>>|model\.patch|^submit$", action, re.I):
        kind = "submission"
    elif re.search(r"(?:^|\s)git\s+diff(?:\s|$)", normalized):
        kind = "git_diff"
    elif _is_edit(action):
        kind = "edit"
    elif re.search(r"(?:^|[\s;&|])(?:pytest|[^\s]+\s+-m\s+pytest)(?:[\s;&|]|$)", normalized):
        kind = "focused_test" if focused_test and focused_test in action else "unrelated_test"
    elif _is_inspection(action):
        kind = "target_confirmation" if targets else "inspection"
    else:
        kind = "unknown"
    return CandidateAction(action, kind, targets)


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
            if candidate.kind != "edit":
                reason = f"{_kind_label(candidate.kind)} cannot satisfy the edit phase."
            elif not candidate.targets_required_file:
                reason = "The edit does not target the required source file."
        elif self.phase == "confirm_edit":
            if candidate.kind == "edit" and candidate.targets_required_file:
                pass
            elif candidate.kind == "target_confirmation":
                pass
            elif candidate.kind == "git_diff" and candidate.targets_required_file:
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
        feedback = (
            f"ACTION REJECTED BY CGR\n\nRequired phase: {self.phase}\n"
            f"Candidate kind: {candidate.kind}\n\n{reason}\n"
            "The proposed action was not executed. Return one action that satisfies the required phase. "
            "Do not describe multiple future commands."
        )
        decision = GateDecision(False, self.phase, candidate, feedback)
        self.record(decision, executed=False, phase_after=self.phase)
        return decision

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
        elif phase_before in {"edit", "confirm_edit"} and kind == "edit":
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
    from sweagent.agent.agents import DefaultAgent  # type: ignore[import-not-found]

    if getattr(DefaultAgent.handle_action, "_cgr_phase_gate", False):
        return
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
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
        gate.record_execution(decision, observation=result.observation, evidence=evidence)
        return result

    handle_action._cgr_phase_gate = True  # type: ignore[attr-defined]
    DefaultAgent.handle_action = handle_action


def _probe_repository(agent: Any, target: str) -> RepositoryEvidence:
    env = agent._env
    if env is None:
        return RepositoryEvidence()
    quoted = shlex.quote(target)
    target_diff = env.communicate(f"git diff -- {quoted}", check="ignore")
    tracked_diff = env.communicate("git diff --binary HEAD --", check="ignore")
    return RepositoryEvidence(target_diff=target_diff, tracked_diff=tracked_diff)


def _is_edit(action: str) -> bool:
    return bool(
        re.search(
            r"(?:sed\s+-i|apply_patch|perl\s+-[pi]|python[^\n]*(?:write_text|open\([^)]*,\s*['\"]w)|(?:cat|printf)[^\n]*(?:>|>>))",
            action,
            re.I,
        )
    )


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
