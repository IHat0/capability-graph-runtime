"""Bounded provider-neutral repair loop using fresh hostile candidate attempts."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Callable

from cgr.quantum_candidate.adjudication import adjudicate_candidate
from cgr.quantum_candidate.contracts import (
    CandidateAdjudicationReceipt,
    CandidateExecutionEvidence,
    CandidateSandboxPolicy,
)
from cgr.quantum_candidate.protocol import (
    CandidateOutputPackage,
    write_public_experiment,
)
from cgr.quantum_candidate.sandbox import execute_candidate
from cgr.quantum_candidate.trusted import TrustedReferenceView
from cgr.quantum_preflight.contracts import ManifestEnvelope

from .contracts import (
    AttemptReference,
    QuantumRepairAttempt,
    QuantumRepairPolicy,
    QuantumRepairRunReceipt,
    TerminalStatus,
    sealed_values,
)
from .directives import create_directive
from .events import RepairEventLog
from .patches import RepairPatchRejected, validate_and_apply_patch
from .persistence import (
    atomic_directory,
    copy_source_tree,
    create_source_manifest,
    finalize_directory,
    write_evidence,
)
from .providers import RepairProvider, RepairProviderError, invoke_provider
from .replay import verify_repair_run
from .state import AttemptStateMachine

ExecuteFunction = Callable[
    ..., tuple[CandidateExecutionEvidence, CandidateOutputPackage]
]
AdjudicateFunction = Callable[..., CandidateAdjudicationReceipt]
SandboxPolicyFactory = Callable[[int], CandidateSandboxPolicy]


def run_repair(
    *,
    task_identifier: str,
    candidate_source: Path,
    public_manifest: ManifestEnvelope,
    trusted: TrustedReferenceView,
    result_root: Path,
    candidate_image_identifier: str,
    candidate_lock_path: Path,
    provider: RepairProvider,
    repair_policy: QuantumRepairPolicy | None = None,
    sandbox_policy: CandidateSandboxPolicy | None = None,
    sandbox_policy_factory: SandboxPolicyFactory | None = None,
    allowed_edit_paths: tuple[str, ...] = ("main.py", "repair-config.json"),
    prohibited_source_hashes: set[str] | None = None,
    execute: ExecuteFunction = execute_candidate,
    adjudicate: AdjudicateFunction = adjudicate_candidate,
) -> dict[str, Any]:
    policy = repair_policy or QuantumRepairPolicy()
    candidate_policy = sandbox_policy or CandidateSandboxPolicy()
    capability = provider.capability
    if capability.maximum_patch_bytes > policy.maximum_patch_bytes:
        raise ValueError("Repair provider capability exceeds the run patch policy.")
    if capability.network_required:
        raise ValueError("Network-requiring repair providers cannot run in v1.")
    run_directory = _next_run_directory(result_root)
    run_identifier = run_directory.name
    run_directory.mkdir(parents=True)
    attempts_root = run_directory / "attempts"
    attempts_root.mkdir()
    event_log = RepairEventLog(run_directory / "events.jsonl", run_identifier)
    started = time.monotonic()
    original_source = run_directory / "source-original"
    copy_source_tree(candidate_source, original_source)
    original_manifest = create_source_manifest(original_source, task_identifier)
    write_evidence(run_directory / "source-original-manifest.json", original_manifest)
    lock_sha256 = hashlib.sha256(candidate_lock_path.read_bytes()).hexdigest()
    public_input = run_directory / "public-experiment.json"
    public_input_sha256 = write_public_experiment(
        public_input,
        public_manifest,
        candidate_dependency_lock_sha256=lock_sha256,
    )
    write_evidence(run_directory / "repair-policy.json", policy)
    write_evidence(
        run_directory / "repair-run-manifest.json",
        {
            "schema_version": "cgr.quantum-repair-run-manifest/1.0.0",
            "repair_run_identifier": run_identifier,
            "task_identifier": task_identifier,
            "public_experiment_sha256": public_manifest.experiment.fingerprint,
            "public_input_sha256": public_input_sha256,
            "original_source_manifest_sha256": original_manifest.source_manifest_sha256,
            "trusted_reference_receipt_sha256": trusted.receipt_content_sha256,
            "candidate_image_identifier": candidate_image_identifier,
            "candidate_dependency_lock_sha256": lock_sha256,
            "provider_capability": capability.model_dump(mode="json"),
            "provider_capability_sha256": capability.fingerprint,
            "policy_sha256": policy.fingerprint,
        },
    )
    event_log.append(
        "repair_run_started",
        "created",
        content_hashes=(original_manifest.source_manifest_sha256, policy.fingerprint),
    )
    attempts: list[QuantumRepairAttempt] = []
    current_source = original_source
    current_manifest = original_manifest
    prior_source_hashes = {original_manifest.source_manifest_sha256}
    prior_patch_hashes: set[str] = set()
    previous_findings: tuple[str, ...] = ()
    final_adjudication: CandidateAdjudicationReceipt | None = None
    terminal_status: TerminalStatus = "controller_failure"
    for attempt_index in range(policy.maximum_attempts):
        elapsed = time.monotonic() - started
        if elapsed > policy.maximum_total_seconds:
            terminal_status = "time_budget_exhausted"
            event_log.append(
                "repair_run_exhausted", terminal_status, elapsed_seconds=elapsed
            )
            break
        attempt_identifier = f"attempt-{attempt_index:03d}"
        temporary, final = atomic_directory(attempts_root, attempt_identifier)
        state = AttemptStateMachine(temporary / "state.json", attempt_identifier)
        event_log.append(
            "attempt_started", "created", attempt_identifier=attempt_identifier
        )
        attempt_source = temporary / "source"
        copy_source_tree(current_source, attempt_source)
        input_manifest = create_source_manifest(attempt_source, task_identifier)
        if (
            input_manifest.source_manifest_sha256
            != current_manifest.source_manifest_sha256
        ):
            raise ValueError(
                "Fresh attempt source reconstruction changed source identity."
            )
        write_evidence(temporary / "source-manifest.json", input_manifest)
        state.transition("source_snapshotted")
        event_log.append(
            "source_snapshot_created",
            "source_snapshotted",
            attempt_identifier=attempt_identifier,
            content_hashes=(input_manifest.source_manifest_sha256,),
        )
        state.transition("candidate_executing")
        event_log.append(
            "candidate_execution_started",
            "candidate_executing",
            attempt_identifier=attempt_identifier,
        )
        execution_directory = temporary / "candidate-execution"
        attempt_candidate_policy = (
            sandbox_policy_factory(attempt_index)
            if sandbox_policy_factory is not None
            else candidate_policy
        )
        execution, package = execute(
            candidate_identifier=task_identifier,
            image_identifier=candidate_image_identifier,
            input_manifest=public_input,
            input_manifest_sha256=public_input_sha256,
            candidate_directory=attempt_source,
            output_directory=execution_directory / "output",
            evidence_directory=execution_directory,
            policy=attempt_candidate_policy,
        )
        event_log.append(
            "candidate_execution_completed",
            execution.execution_category,
            attempt_identifier=attempt_identifier,
            content_hashes=(execution.fingerprint,),
            elapsed_seconds=execution.elapsed_seconds,
        )
        adjudication = adjudicate(
            experiment=public_manifest.experiment,
            execution=execution,
            package=package,
            trusted=trusted,
            candidate_dependency_lock_sha256=lock_sha256,
        )
        final_adjudication = adjudication
        adjudication_directory = temporary / "adjudication"
        adjudication_directory.mkdir()
        write_evidence(adjudication_directory / "receipt.json", adjudication)
        state.transition("adjudicated")
        observed_findings = tuple(item.code for item in adjudication.findings)
        event_log.append(
            "adjudication_completed",
            "authorized" if adjudication.authorized else "rejected",
            attempt_identifier=attempt_identifier,
            content_hashes=(adjudication.receipt_content_sha256,),
        )
        directive_sha256: str | None = None
        patch_sha256: str | None = None
        output_manifest = input_manifest
        failure_reason: str | None = None
        if adjudication.authorized:
            state.transition("authorized")
            terminal_status = "authorized"
            event_log.append(
                "attempt_authorized",
                "authorized",
                attempt_identifier=attempt_identifier,
            )
        elif attempt_index + 1 >= policy.maximum_attempts:
            state.transition("attempt_budget_exhausted")
            terminal_status = "attempt_budget_exhausted"
            failure_reason = "Absolute repair-attempt budget was exhausted."
            event_log.append(
                "repair_run_exhausted",
                terminal_status,
                attempt_identifier=attempt_identifier,
            )
        else:
            directive = create_directive(
                task_identifier=task_identifier,
                repair_run_identifier=run_identifier,
                attempt_identifier=attempt_identifier,
                attempt_index=attempt_index,
                source_manifest=input_manifest,
                adjudication=adjudication,
                policy=policy,
                allowed_edit_paths=allowed_edit_paths,
            )
            directive_sha256 = directive.directive_sha256
            write_evidence(temporary / "repair-directive.json", directive)
            state.transition("directive_created")
            event_log.append(
                "repair_directive_created",
                "repairable",
                attempt_identifier=attempt_identifier,
                content_hashes=(directive.directive_sha256,),
            )
            event_log.append(
                "repair_provider_started",
                "started",
                attempt_identifier=attempt_identifier,
            )
            try:
                patch = invoke_provider(
                    provider,
                    directive=directive,
                    source_root=attempt_source,
                    source_manifest=input_manifest,
                    timeout_seconds=policy.maximum_provider_seconds,
                )
                state.transition("repair_proposed")
                event_log.append(
                    "repair_patch_proposed",
                    "proposed",
                    attempt_identifier=attempt_identifier,
                    content_hashes=(patch.patch_sha256,),
                )
                repaired_source = temporary / "repaired-source"
                validation, output_manifest = validate_and_apply_patch(
                    source_root=attempt_source,
                    destination_root=repaired_source,
                    source_manifest=input_manifest,
                    directive=directive,
                    patch=patch,
                    policy=policy,
                    prior_patch_hashes=prior_patch_hashes,
                    prior_source_hashes=prior_source_hashes,
                    prohibited_source_hashes=prohibited_source_hashes,
                )
                state.transition("patch_validated")
                write_evidence(temporary / "repair-patch.json", patch)
                write_evidence(temporary / "patch-validation.json", validation)
                write_evidence(
                    temporary / "output-source-manifest.json", output_manifest
                )
                patch_sha256 = patch.patch_sha256
                state.transition("patch_applied")
                state.transition("reexecution_pending")
                prior_patch_hashes.add(patch.patch_sha256)
                prior_source_hashes.add(output_manifest.source_manifest_sha256)
                event_log.append(
                    "repair_patch_applied",
                    "reexecution_pending",
                    attempt_identifier=attempt_identifier,
                    content_hashes=(
                        patch.patch_sha256,
                        output_manifest.source_manifest_sha256,
                    ),
                )
            except RepairProviderError as exc:
                state.transition("repair_provider_failed")
                terminal_status = "repair_provider_failed"
                failure_reason = str(exc)
                event_log.append(
                    "attempt_rejected",
                    terminal_status,
                    attempt_identifier=attempt_identifier,
                )
            except RepairPatchRejected as exc:
                if state.status == "repair_proposed":
                    state.transition("patch_rejected")
                terminal_status = "patch_rejected"
                failure_reason = exc.code
                event_log.append(
                    "repair_patch_rejected",
                    exc.code,
                    attempt_identifier=attempt_identifier,
                )
        attempt_values: dict[str, Any] = {
            "repair_run_identifier": run_identifier,
            "attempt_identifier": attempt_identifier,
            "attempt_index": attempt_index,
            "parent_attempt_identifier": (
                attempts[-1].attempt_identifier if attempts else None
            ),
            "input_source_manifest_sha256": input_manifest.source_manifest_sha256,
            "directive_sha256": directive_sha256,
            "patch_sha256": patch_sha256,
            "output_source_manifest_sha256": output_manifest.source_manifest_sha256,
            "candidate_execution_sha256": execution.fingerprint,
            "adjudication_receipt_sha256": adjudication.receipt_content_sha256,
            "authorized": adjudication.authorized,
            "findings_before": previous_findings,
            "findings_after": observed_findings,
            "status": state.status,
            "failure_reason": failure_reason,
            "elapsed_seconds": time.monotonic() - started,
        }
        attempt = QuantumRepairAttempt.model_validate(
            sealed_values(attempt_values, "attempt_content_sha256")
        )
        write_evidence(temporary / "attempt.json", attempt)
        finalize_directory(temporary, final)
        attempts.append(attempt)
        previous_findings = observed_findings
        if adjudication.authorized or state.status not in {"reexecution_pending"}:
            break
        current_source = final / "repaired-source"
        current_manifest = output_manifest
    if final_adjudication is None or not attempts:
        raise ValueError("Repair run ended without a complete adjudicated attempt.")
    references = tuple(
        AttemptReference(
            attempt_identifier=item.attempt_identifier,
            attempt_index=item.attempt_index,
            attempt_content_sha256=item.attempt_content_sha256,
            source_manifest_sha256=item.input_source_manifest_sha256,
            adjudication_receipt_sha256=item.adjudication_receipt_sha256,
            authorized=item.authorized,
        )
        for item in attempts
    )
    # The final identity must name the source that produced the final adjudication,
    # never a patched state that a time-limit prevented us from executing.
    final_source_sha = attempts[-1].input_source_manifest_sha256
    receipt_values: dict[str, Any] = {
        "repair_run_identifier": run_identifier,
        "public_experiment_sha256": public_manifest.experiment.fingerprint,
        "original_source_manifest_sha256": original_manifest.source_manifest_sha256,
        "trusted_reference_receipt_sha256": trusted.receipt_content_sha256,
        "provider_capability_sha256": capability.fingerprint,
        "policy_sha256": policy.fingerprint,
        "attempts": references,
        "attempt_cap": policy.maximum_attempts,
        "total_budget_seconds": policy.maximum_total_seconds,
        "terminal_status": terminal_status,
        "final_source_manifest_sha256": final_source_sha,
        "final_adjudication_receipt_sha256": final_adjudication.receipt_content_sha256,
        "final_scientific_outcome_sha256": (
            final_adjudication.recomputed_scientific_result_sha256
            if final_adjudication.authorized
            else None
        ),
        "authorized": final_adjudication.authorized and terminal_status == "authorized",
    }
    receipt = QuantumRepairRunReceipt.model_validate(
        sealed_values(receipt_values, "repair_run_content_sha256")
    )
    write_evidence(run_directory / "repair-run-receipt.json", receipt)
    summary = {
        "schema_version": "cgr.quantum-repair-run-summary/1.0.0",
        "repair_run_identifier": run_identifier,
        "attempts": len(attempts),
        "terminal_status": receipt.terminal_status,
        "authorized": receipt.authorized,
        "repair_run_content_sha256": receipt.repair_run_content_sha256,
        "network_enabled_executions": sum(
            1
            for index in range(len(attempts))
            if not CandidateExecutionEvidence.model_validate(
                json.loads(
                    (
                        attempts_root
                        / f"attempt-{index:03d}"
                        / "candidate-execution"
                        / "execution.json"
                    ).read_text()
                )
            ).network_disabled
        ),
        "trusted_evidence_exposure_attempts": sum(
            1
            for index in range(len(attempts))
            if CandidateExecutionEvidence.model_validate(
                json.loads(
                    (
                        attempts_root
                        / f"attempt-{index:03d}"
                        / "candidate-execution"
                        / "execution.json"
                    ).read_text(encoding="utf-8")
                )
            ).trusted_evidence_exposed
        ),
    }
    write_evidence(run_directory / "repair-run-summary.json", summary)
    write_evidence(
        run_directory / "repair-run-report.json",
        {
            "summary": summary,
            "attempts": [item.model_dump(mode="json") for item in attempts],
        },
    )
    event_log.append(
        "repair_run_completed",
        receipt.terminal_status,
        content_hashes=(receipt.repair_run_content_sha256,),
        elapsed_seconds=time.monotonic() - started,
    )
    replay = verify_repair_run(run_directory)
    return {
        **summary,
        "replay_verified": replay["replay_verified"],
        "repair_run_directory": str(run_directory),
    }


def resume_repair_run(run_directory: Path) -> dict[str, Any]:
    """Idempotently verify and return a completed run; expose partial state safely."""
    if (run_directory / "repair-run-receipt.json").is_file():
        replay = verify_repair_run(run_directory)
        summary = json.loads(
            (run_directory / "repair-run-summary.json").read_text(encoding="utf-8")
        )
        if (
            summary.get("repair_run_content_sha256")
            != replay["repair_run_content_sha256"]
            or summary.get("authorized") != replay["authorized"]
            or summary.get("terminal_status") != replay["terminal_status"]
        ):
            raise ValueError("Completed repair summary was substituted.")
        return {**summary, "replay_verified": True, "resumed": True}
    partials = sorted((run_directory / "attempts").glob(".*.tmp-*"))
    completed = sorted((run_directory / "attempts").glob("attempt-*"))
    return {
        "schema_version": "cgr.quantum-repair-recovery-status/1.0.0",
        "repair_run_identifier": run_directory.name,
        "safe_to_resume": not partials,
        "complete_attempts": len(completed),
        "corrupted_partial_attempts": len(partials),
        "authorized": False,
    }


def _next_run_directory(result_root: Path) -> Path:
    base = result_root / "quantum-candidate-repair"
    base.mkdir(parents=True, exist_ok=True)
    for index in range(1, 1_000_000):
        candidate = base / f"repair-run-{index:03d}"
        if not candidate.exists():
            return candidate
    raise ValueError("No repair-run identifier remains available.")
