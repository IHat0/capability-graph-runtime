"""Structured portable event log for repair runs."""

from __future__ import annotations

import os
from pathlib import Path

from cgr.science.canonical import canonical_json

from .contracts import QuantumRepairEvent


class RepairEventLog:
    def __init__(self, path: Path, repair_run_identifier: str) -> None:
        self.path = path
        self.repair_run_identifier = repair_run_identifier
        self.sequence = self._existing_count()

    def append(
        self,
        event_type: str,
        status: str,
        *,
        attempt_identifier: str | None = None,
        content_hashes: tuple[str, ...] = (),
        elapsed_seconds: float = 0.0,
    ) -> QuantumRepairEvent:
        event = QuantumRepairEvent(
            repair_run_identifier=self.repair_run_identifier,
            attempt_identifier=attempt_identifier,
            event_identifier=f"event-{self.sequence:06d}",
            event_sequence=self.sequence,
            event_type=event_type,
            status=status,
            content_hashes=content_hashes,
            elapsed_seconds=elapsed_seconds,
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("ab") as handle:
            handle.write((canonical_json(event) + "\n").encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        self.sequence += 1
        return event

    def _existing_count(self) -> int:
        if not self.path.exists():
            return 0
        lines = self.path.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            event = QuantumRepairEvent.model_validate_json(line)
            if event.event_sequence != index:
                raise ValueError("Repair event sequence is corrupted.")
        return len(lines)


def verify_event_log(
    path: Path, repair_run_identifier: str
) -> tuple[QuantumRepairEvent, ...]:
    if not path.is_file():
        raise ValueError("Repair event log is missing.")
    events = tuple(
        QuantumRepairEvent.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
    )
    if not events or any(
        event.repair_run_identifier != repair_run_identifier
        or event.event_sequence != index
        for index, event in enumerate(events)
    ):
        raise ValueError("Repair event log identity or ordering is invalid.")
    return events
