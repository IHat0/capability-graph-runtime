"""Durable invocation state, leases, retries, and idempotent recovery."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from cgr.quantum_preflight.artifacts import write_json_atomic

from ..contracts import QuantumRepairPatch
from ..persistence import read_json, write_evidence
from .contracts import (
    InvocationStatus,
    ProviderInvocationRequest,
    ProviderInvocationResult,
)

_TRANSITIONS: dict[InvocationStatus, frozenset[InvocationStatus]] = {
    "created": frozenset({"request_persisted", "interrupted"}),
    "request_persisted": frozenset({"launching", "interrupted"}),
    "launching": frozenset({"running", "interrupted", "retryable_failure"}),
    "running": frozenset({"response_persisted", "interrupted", "retryable_failure"}),
    "response_persisted": frozenset(
        {"patch_extracted", "interrupted", "retryable_failure", "terminal_failure"}
    ),
    "patch_extracted": frozenset({"completed", "interrupted", "terminal_failure"}),
    "completed": frozenset(),
    "interrupted": frozenset(),
    "retryable_failure": frozenset(),
    "terminal_failure": frozenset(),
}


class InvocationStateStore:
    def __init__(
        self,
        directory: Path,
        invocation_identifier: str,
        *,
        lease_seconds: int,
        crash_injector: Callable[[InvocationStatus], None] | None = None,
    ) -> None:
        self.directory = directory
        self.invocation_identifier = invocation_identifier
        self.lease_seconds = lease_seconds
        self.crash_injector = crash_injector
        self.path = directory / "invocation-state.json"
        self.directory.mkdir(parents=True, exist_ok=False)
        self.status: InvocationStatus = "created"
        self._persist()

    def transition(self, target: InvocationStatus) -> None:
        if target not in _TRANSITIONS[self.status]:
            raise ValueError(
                f"Illegal provider invocation transition: {self.status} -> {target}"
            )
        self.status = target
        self._persist()

    def heartbeat(self) -> None:
        self._persist(inject=False)

    def persist_request(self, request: ProviderInvocationRequest) -> None:
        write_evidence(self.directory / "provider-request.json", request)
        self.transition("request_persisted")

    def persist_result(self, result: ProviderInvocationResult) -> None:
        write_evidence(self.directory / "provider-result.json", result)

    def persist_patch(self, patch: QuantumRepairPatch) -> None:
        write_evidence(self.directory / "proposed-patch.json", patch)

    def _persist(self, *, inject: bool = True) -> None:
        now = time.time()
        write_json_atomic(
            self.path,
            {
                "schema_version": "cgr.quantum-repair-provider-state/1.0.0",
                "provider_invocation_identifier": self.invocation_identifier,
                "status": self.status,
                "heartbeat_unix_seconds": now,
                "lease_expires_unix_seconds": now + self.lease_seconds,
            },
            maximum_bytes=4096,
        )
        if inject and self.crash_injector is not None:
            self.crash_injector(self.status)


def recover_attempt_invocations(
    root: Path,
    *,
    directive_sha256: str,
    source_manifest_sha256: str,
) -> tuple[QuantumRepairPatch | None, int, tuple[str, ...]]:
    """Return one completed patch or mark expired partial invocations interrupted."""
    root.mkdir(parents=True, exist_ok=True)
    completed: list[QuantumRepairPatch] = []
    interrupted: list[str] = []
    directories = sorted(path for path in root.glob("invocation-*") if path.is_dir())
    for directory in directories:
        state_path = directory / "invocation-state.json"
        if not state_path.is_file():
            raise ValueError("Provider invocation is missing durable state.")
        state = read_json(state_path)
        status = state.get("status")
        request_path = directory / "provider-request.json"
        if status == "completed":
            request = ProviderInvocationRequest.model_validate(read_json(request_path))
            result = ProviderInvocationResult.model_validate(
                read_json(directory / "provider-result.json")
            )
            patch = QuantumRepairPatch.model_validate(
                read_json(directory / "proposed-patch.json")
            )
            if (
                request.directive_sha256 != directive_sha256
                or request.input_source_manifest_sha256 != source_manifest_sha256
                or result.request_sha256 != request.request_content_sha256
                or result.proposed_patch_identity != patch.patch_sha256
            ):
                raise ValueError("Completed provider invocation was cross-linked.")
            completed.append(patch)
        elif status in {"interrupted", "retryable_failure", "terminal_failure"}:
            interrupted.append(directory.name)
        else:
            expires = float(state.get("lease_expires_unix_seconds", 0.0))
            if expires > time.time():
                raise ValueError(
                    "Provider invocation has an active lease; duplicate launch refused."
                )
            state["status"] = "interrupted"
            write_json_atomic(state_path, state, maximum_bytes=4096)
            interrupted.append(directory.name)
    if len(completed) > 1:
        raise ValueError("Duplicate completed provider invocations were detected.")
    return (completed[0] if completed else None, len(directories), tuple(interrupted))
