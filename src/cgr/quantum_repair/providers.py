"""Generic repair-provider boundary and bounded invocation."""

from __future__ import annotations

import queue
import threading
from pathlib import Path
from typing import Protocol, runtime_checkable

from .contracts import (
    ProviderCapability,
    QuantumRepairDirective,
    QuantumRepairPatch,
    SourceManifest,
)


@runtime_checkable
class RepairProvider(Protocol):
    @property
    def capability(self) -> ProviderCapability: ...

    def propose_repair(
        self,
        *,
        directive: QuantumRepairDirective,
        source_root: Path,
        source_manifest: SourceManifest,
    ) -> QuantumRepairPatch: ...


class RepairProviderError(RuntimeError):
    pass


def invoke_provider(
    provider: RepairProvider,
    *,
    directive: QuantumRepairDirective,
    source_root: Path,
    source_manifest: SourceManifest,
    timeout_seconds: int,
) -> QuantumRepairPatch:
    if provider.capability.network_required:
        raise RepairProviderError(
            "Network-requiring repair providers are prohibited in v1."
        )
    outcomes: queue.Queue[QuantumRepairPatch | BaseException] = queue.Queue(maxsize=1)

    def invoke() -> None:
        try:
            outcomes.put(
                provider.propose_repair(
                    directive=directive,
                    source_root=source_root,
                    source_manifest=source_manifest,
                )
            )
        except BaseException as exc:
            outcomes.put(exc)

    thread = threading.Thread(
        target=invoke, daemon=True, name="quantum-repair-provider"
    )
    thread.start()
    thread.join(timeout_seconds)
    if thread.is_alive():
        raise RepairProviderError("Repair provider exceeded its bounded timeout.")
    outcome = outcomes.get_nowait()
    if isinstance(outcome, BaseException):
        raise RepairProviderError(
            f"Repair provider failed: {type(outcome).__name__}"
        ) from outcome
    return outcome
