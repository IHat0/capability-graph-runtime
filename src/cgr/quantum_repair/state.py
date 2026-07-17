"""Explicit persisted repair-attempt state machine."""

from __future__ import annotations

from pathlib import Path

from cgr.quantum_preflight.artifacts import write_json_atomic

from .contracts import AttemptStatus
from .persistence import read_json

_TRANSITIONS: dict[AttemptStatus, frozenset[AttemptStatus]] = {
    "created": frozenset({"source_snapshotted", "controller_failure"}),
    "source_snapshotted": frozenset({"candidate_executing", "controller_failure"}),
    "candidate_executing": frozenset({"adjudicated", "controller_failure"}),
    "adjudicated": frozenset(
        {
            "authorized",
            "directive_created",
            "terminal_rejection",
            "attempt_budget_exhausted",
            "time_budget_exhausted",
            "controller_failure",
        }
    ),
    "directive_created": frozenset(
        {"repair_proposed", "repair_provider_failed", "human_review_required"}
    ),
    "repair_proposed": frozenset({"patch_validated", "patch_rejected"}),
    "patch_validated": frozenset({"patch_applied", "patch_rejected"}),
    "patch_applied": frozenset({"reexecution_pending", "repair_oscillation"}),
    "reexecution_pending": frozenset(),
    "authorized": frozenset(),
    "terminal_rejection": frozenset(),
    "human_review_required": frozenset(),
    "repair_provider_failed": frozenset(),
    "patch_rejected": frozenset(),
    "attempt_budget_exhausted": frozenset(),
    "time_budget_exhausted": frozenset(),
    "repeated_failure": frozenset(),
    "repair_oscillation": frozenset(),
    "controller_failure": frozenset(),
}


class AttemptStateMachine:
    def __init__(self, path: Path, attempt_identifier: str) -> None:
        self.path = path
        self.attempt_identifier = attempt_identifier
        if path.exists():
            value = read_json(path)
            if value.get("attempt_identifier") != attempt_identifier:
                raise ValueError("Attempt state belongs to another attempt.")
            self.status: AttemptStatus = value["status"]
        else:
            self.status = "created"
            self._persist()

    def transition(self, target: AttemptStatus) -> None:
        if target not in _TRANSITIONS[self.status]:
            raise ValueError(
                f"Illegal repair state transition: {self.status} -> {target}"
            )
        self.status = target
        self._persist()

    def _persist(self) -> None:
        write_json_atomic(
            self.path,
            {
                "schema_version": "cgr.quantum-repair-attempt-state/1.0.0",
                "attempt_identifier": self.attempt_identifier,
                "status": self.status,
            },
            maximum_bytes=4096,
        )
