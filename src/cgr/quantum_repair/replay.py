"""Read-only verification of persisted quantum repair runs."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from cgr.quantum_candidate.contracts import (
    CandidateAdjudicationReceipt,
    CandidateExecutionEvidence,
)
from cgr.quantum_candidate.protocol import source_tree_sha256

from .contracts import (
    PatchValidation,
    ProviderCapability,
    QuantumRepairAttempt,
    QuantumRepairDirective,
    QuantumRepairPatch,
    QuantumRepairPolicy,
    QuantumRepairRunReceipt,
    SourceManifest,
)
from .events import verify_event_log
from .persistence import read_json, verify_source_manifest


def verify_repair_run(run_directory: Path) -> dict[str, Any]:
    receipt = QuantumRepairRunReceipt.model_validate(
        read_json(run_directory / "repair-run-receipt.json")
    )
    run_manifest = read_json(run_directory / "repair-run-manifest.json")
    if run_manifest.get("schema_version") != "cgr.quantum-repair-run-manifest/1.0.0":
        raise ValueError("Repair-run manifest schema is unsupported.")
    policy = QuantumRepairPolicy.model_validate(
        read_json(run_directory / "repair-policy.json")
    )
    capability = ProviderCapability.model_validate(
        run_manifest.get("provider_capability")
    )
    public_input_sha256 = hashlib.sha256(
        (run_directory / "public-experiment.json").read_bytes()
    ).hexdigest()
    if (
        run_manifest.get("repair_run_identifier") != receipt.repair_run_identifier
        or run_manifest.get("public_experiment_sha256")
        != receipt.public_experiment_sha256
        or run_manifest.get("trusted_reference_receipt_sha256")
        != receipt.trusted_reference_receipt_sha256
        or run_manifest.get("public_input_sha256") != public_input_sha256
        or run_manifest.get("policy_sha256") != policy.fingerprint
        or receipt.policy_sha256 != policy.fingerprint
        or run_manifest.get("provider_capability_sha256") != capability.fingerprint
        or receipt.provider_capability_sha256 != capability.fingerprint
    ):
        raise ValueError("Repair-run immutable inputs were substituted.")
    verify_event_log(run_directory / "events.jsonl", receipt.repair_run_identifier)
    original_manifest = SourceManifest.model_validate(
        read_json(run_directory / "source-original-manifest.json")
    )
    verify_source_manifest(run_directory / "source-original", original_manifest)
    if (
        original_manifest.source_manifest_sha256
        != receipt.original_source_manifest_sha256
    ):
        raise ValueError("Repair-run original source was substituted.")
    attempts_root = run_directory / "attempts"
    actual_directories = sorted(
        path.name
        for path in attempts_root.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    )
    expected_directories = [
        f"attempt-{index:03d}" for index in range(len(receipt.attempts))
    ]
    if actual_directories != expected_directories:
        raise ValueError("Repair attempts were inserted, deleted, or reordered.")
    previous_identifier: str | None = None
    for reference, directory_name in zip(receipt.attempts, expected_directories):
        attempt_directory = attempts_root / directory_name
        attempt = QuantumRepairAttempt.model_validate(
            read_json(attempt_directory / "attempt.json")
        )
        if (
            attempt.attempt_content_sha256 != reference.attempt_content_sha256
            or attempt.attempt_identifier != reference.attempt_identifier
            or attempt.parent_attempt_identifier != previous_identifier
        ):
            raise ValueError("Repair attempt linkage or identity is invalid.")
        source_manifest = SourceManifest.model_validate(
            read_json(attempt_directory / "source-manifest.json")
        )
        verify_source_manifest(attempt_directory / "source", source_manifest)
        if (
            source_manifest.source_manifest_sha256
            != attempt.input_source_manifest_sha256
        ):
            raise ValueError("Repair attempt source was substituted.")
        execution = CandidateExecutionEvidence.model_validate(
            read_json(attempt_directory / "candidate-execution" / "execution.json")
        )
        adjudication = CandidateAdjudicationReceipt.model_validate(
            read_json(attempt_directory / "adjudication" / "receipt.json")
        )
        if (
            execution.fingerprint != attempt.candidate_execution_sha256
            or execution.source_tree_sha256
            != source_tree_sha256(attempt_directory / "source")
            or adjudication.receipt_content_sha256
            != attempt.adjudication_receipt_sha256
            or adjudication.authorized != attempt.authorized
            or adjudication.execution_evidence.content_sha256 != execution.fingerprint
            or adjudication.input_experiment_sha256 != receipt.public_experiment_sha256
            or adjudication.trusted_reference_receipt_sha256
            != receipt.trusted_reference_receipt_sha256
        ):
            raise ValueError("Execution or adjudication evidence was cross-linked.")
        if attempt.directive_sha256 is not None:
            directive = QuantumRepairDirective.model_validate(
                read_json(attempt_directory / "repair-directive.json")
            )
            if directive.directive_sha256 != attempt.directive_sha256:
                raise ValueError("Repair directive was substituted.")
            if attempt.patch_sha256 is not None:
                patch = QuantumRepairPatch.model_validate(
                    read_json(attempt_directory / "repair-patch.json")
                )
                if patch.patch_sha256 != attempt.patch_sha256:
                    raise ValueError("Repair patch was substituted.")
                validation = PatchValidation.model_validate(
                    read_json(attempt_directory / "patch-validation.json")
                )
                output_manifest = SourceManifest.model_validate(
                    read_json(attempt_directory / "output-source-manifest.json")
                )
                verify_source_manifest(
                    attempt_directory / "repaired-source", output_manifest
                )
                if (
                    not validation.validated
                    or validation.patch_sha256 != patch.patch_sha256
                    or validation.output_source_manifest_sha256
                    != output_manifest.source_manifest_sha256
                    or output_manifest.source_manifest_sha256
                    != attempt.output_source_manifest_sha256
                ):
                    raise ValueError(
                        "Applied repair source or validation was substituted."
                    )
        elif attempt.patch_sha256 is not None:
            raise ValueError("Repair patch exists without a directive.")
        _verify_provider_invocations(attempt_directory, attempt)
        previous_identifier = attempt.attempt_identifier
    final_reference = receipt.attempts[-1]
    if (
        final_reference.adjudication_receipt_sha256
        != receipt.final_adjudication_receipt_sha256
        or final_reference.authorized != receipt.authorized
    ):
        raise ValueError("Final repair authorization was substituted.")
    if (
        receipt.final_source_manifest_sha256
        != QuantumRepairAttempt.model_validate(
            read_json(attempts_root / expected_directories[-1] / "attempt.json")
        ).input_source_manifest_sha256
    ):
        raise ValueError("Final repair source identity was substituted.")
    final_adjudication = CandidateAdjudicationReceipt.model_validate(
        read_json(
            attempts_root / expected_directories[-1] / "adjudication" / "receipt.json"
        )
    )
    if (
        receipt.final_scientific_outcome_sha256
        != final_adjudication.recomputed_scientific_result_sha256
    ):
        raise ValueError("Final scientific outcome identity was substituted.")
    return {
        "schema_version": "cgr.quantum-repair-replay-summary/1.0.0",
        "repair_run_identifier": receipt.repair_run_identifier,
        "attempts_verified": len(receipt.attempts),
        "authorized": receipt.authorized,
        "terminal_status": receipt.terminal_status,
        "repair_run_content_sha256": receipt.repair_run_content_sha256,
        "replay_verified": True,
    }


def _verify_provider_invocations(
    attempt_directory: Path, attempt: QuantumRepairAttempt
) -> None:
    root = attempt_directory / "provider-invocations"
    if not root.exists():
        return
    from .model_provider.contracts import (
        AgentDescriptor,
        ModelEndpointDescriptor,
        ModelRepairPrompt,
        ProviderInvocationRequest,
        ProviderInvocationResult,
        ProviderTrajectoryManifest,
    )
    from .model_provider.telemetry import verify_provider_telemetry

    directories = sorted(path for path in root.glob("invocation-*") if path.is_dir())
    expected = [f"invocation-{index:03d}" for index in range(len(directories))]
    if [path.name for path in directories] != expected:
        raise ValueError("Provider invocations were inserted, deleted, or reordered.")
    completed = 0
    for directory in directories:
        state = read_json(directory / "invocation-state.json")
        if state.get("schema_version") != "cgr.quantum-repair-provider-state/1.0.0":
            raise ValueError("Provider invocation state schema is unsupported.")
        status = state.get("status")
        request_path = directory / "provider-request.json"
        request = None
        if request_path.is_file():
            request = ProviderInvocationRequest.model_validate(read_json(request_path))
            if (
                request.directive_sha256 != attempt.directive_sha256
                or request.input_source_manifest_sha256
                != attempt.input_source_manifest_sha256
            ):
                raise ValueError("Provider request was cross-linked.")
            endpoint = ModelEndpointDescriptor.model_validate(
                read_json(directory / "model-endpoint.json")
            )
            agent = AgentDescriptor.model_validate(
                read_json(directory / "agent-descriptor.json")
            )
            prompt = ModelRepairPrompt.model_validate(
                read_json(directory / "model-prompt.json")
            )
            if (
                request.model_endpoint_descriptor_sha256 != endpoint.descriptor_sha256
                or request.agent_descriptor_sha256 != agent.descriptor_sha256
                or request.prompt_sha256 != prompt.prompt_sha256
            ):
                raise ValueError("Provider descriptors or prompt were substituted.")
        if status == "completed":
            if request is None:
                raise ValueError("Completed provider invocation has no request.")
            completed += 1
            result = ProviderInvocationResult.model_validate(
                read_json(directory / "provider-result.json")
            )
            trajectory = ProviderTrajectoryManifest.model_validate(
                read_json(directory / "trajectory-manifest.json")
            )
            proposed = QuantumRepairPatch.model_validate(
                read_json(directory / "proposed-patch.json")
            )
            if (
                result.request_sha256 != request.request_content_sha256
                or result.trajectory_identity != trajectory.complete_trajectory_sha256
                or result.proposed_patch_identity != proposed.patch_sha256
                or proposed.patch_sha256 != attempt.patch_sha256
            ):
                raise ValueError("Completed provider evidence was cross-linked.")
        verify_provider_telemetry(directory / "provider-events.jsonl")
    if completed > 1:
        raise ValueError("More than one provider invocation completed for an attempt.")
