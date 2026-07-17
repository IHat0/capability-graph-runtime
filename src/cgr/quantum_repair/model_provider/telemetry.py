"""Structured non-sensitive telemetry for whole provider invocations."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .contracts import ProviderTelemetryEvent


class ProviderTelemetryLog:
    def __init__(
        self,
        path: Path,
        *,
        repair_run_identifier: str,
        attempt_identifier: str,
        invocation_identifier: str,
    ) -> None:
        self.path = path
        self.repair_run_identifier = repair_run_identifier
        self.attempt_identifier = attempt_identifier
        self.invocation_identifier = invocation_identifier
        self.sequence = 0
        if path.is_file():
            self.sequence = len(verify_provider_telemetry(path))

    def append(
        self, event_type: str, status: str, **values: Any
    ) -> ProviderTelemetryEvent:
        event = ProviderTelemetryEvent(
            repair_run_identifier=self.repair_run_identifier,
            attempt_identifier=self.attempt_identifier,
            provider_invocation_identifier=self.invocation_identifier,
            sequence=self.sequence,
            event_type=event_type,
            status=status,
            **values,
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(event.model_dump(mode="json"), sort_keys=True))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        self.sequence += 1
        return event


def verify_provider_telemetry(path: Path) -> tuple[ProviderTelemetryEvent, ...]:
    events = tuple(
        ProviderTelemetryEvent.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
    )
    if not events or any(item.sequence != index for index, item in enumerate(events)):
        raise ValueError("Provider telemetry sequence is incomplete or reordered.")
    identity = (
        events[0].repair_run_identifier,
        events[0].attempt_identifier,
        events[0].provider_invocation_identifier,
    )
    if any(
        (
            item.repair_run_identifier,
            item.attempt_identifier,
            item.provider_invocation_identifier,
        )
        != identity
        for item in events
    ):
        raise ValueError("Provider telemetry identities were cross-linked.")
    return events
