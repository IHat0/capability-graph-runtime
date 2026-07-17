"""Production whole-invocation pristine SWE-agent repair provider."""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..contracts import (
    ProviderCapability,
    QuantumRepairDirective,
    QuantumRepairPatch,
    SourceManifest,
)
from ..persistence import copy_source_tree, read_json, write_evidence
from ..providers import RepairProviderError
from .agent import (
    build_official_command,
    child_environment,
    provider_overlay,
    verify_pristine_sweagent,
)
from .config import SWEAgentProviderConfig
from .contracts import (
    InvocationStatus,
    ProviderBudget,
    ProviderInvocationRequest,
    ProviderInvocationResult,
    seal_contract,
)
from .endpoint import verify_model_endpoint
from .extraction import extract_official_patch, redact_trajectory
from .process import run_bounded_process
from .prompting import build_model_prompt, render_problem_statement
from .recovery import InvocationStateStore, recover_attempt_invocations
from .telemetry import ProviderTelemetryLog


class SWEAgentOpenAICompatibleRepairProvider:
    """Untrusted model provider; returned patches still cross the generic validator."""

    provider_identifier = "sweagent-openai-compatible"
    provider_version = "1.0.0"

    def __init__(
        self,
        *,
        config: SWEAgentProviderConfig,
        public_task: dict[str, Any],
        environment: dict[str, str] | None = None,
        crash_injector: Callable[[InvocationStatus], None] | None = None,
    ) -> None:
        self.config = config
        self.public_task = json.loads(json.dumps(public_task))
        self.environment = environment
        self.crash_injector = crash_injector
        self._capability = ProviderCapability(
            provider_identifier=self.provider_identifier,
            provider_version=self.provider_version,
            provider_type="swe_agent",
            supported_finding_codes=("candidate_repair",),
            maximum_patch_bytes=config.budget.maximum_patch_bytes,
            deterministic=False,
            # Loopback model transport is separately constrained and is not
            # general provider network access.
            network_required=False,
            tool_requirements=("docker", "openai_compatible", "sweagent"),
            trust_classification="untrusted",
        )
        self.invocations = 0
        self.model_calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.tool_calls = 0
        self.tool_output_bytes = 0
        self.elapsed_seconds = 0.0

    @property
    def capability(self) -> ProviderCapability:
        return self._capability

    @property
    def consumption(self) -> dict[str, int | float]:
        return {
            "provider_invocations": self.invocations,
            "model_calls": self.model_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.input_tokens + self.output_tokens,
            "tool_calls": self.tool_calls,
            "tool_output_bytes": self.tool_output_bytes,
            "elapsed_seconds": self.elapsed_seconds,
        }

    def propose_repair(
        self,
        *,
        directive: QuantumRepairDirective,
        source_root: Path,
        source_manifest: SourceManifest,
    ) -> QuantumRepairPatch:
        invocation_root = source_root.parent / "provider-invocations"
        recovered, sequence, _ = recover_attempt_invocations(
            invocation_root,
            directive_sha256=directive.directive_sha256,
            source_manifest_sha256=source_manifest.source_manifest_sha256,
        )
        if recovered is not None:
            return recovered
        errors: list[str] = []
        for retry in range(self.config.budget.maximum_retries + 1):
            invocation_sequence = sequence + retry
            try:
                invocation_config = _remaining_config(self.config, self.consumption)
            except RepairProviderError as exc:
                errors.append(type(exc).__name__)
                break
            try:
                patch = self._invoke_once(
                    directive=directive,
                    source_root=source_root,
                    source_manifest=source_manifest,
                    invocation_root=invocation_root,
                    invocation_sequence=invocation_sequence,
                    invocation_config=invocation_config,
                )
                return patch
            except Exception as exc:
                if self.crash_injector is not None:
                    raise
                errors.append(type(exc).__name__)
                if retry < self.config.budget.maximum_retries:
                    failed_identifier = f"provider-invocation-{invocation_sequence:03d}"
                    ProviderTelemetryLog(
                        invocation_root
                        / f"invocation-{invocation_sequence:03d}"
                        / "provider-events.jsonl",
                        repair_run_identifier=directive.repair_run_identifier,
                        attempt_identifier=directive.source_attempt_identifier,
                        invocation_identifier=failed_identifier,
                    ).append("provider_invocation_retried", "retrying")
        raise RepairProviderError(
            "SWE-agent provider exhausted its bounded retries: " + ",".join(errors)
        )

    def _invoke_once(
        self,
        *,
        directive: QuantumRepairDirective,
        source_root: Path,
        source_manifest: SourceManifest,
        invocation_root: Path,
        invocation_sequence: int,
        invocation_config: SWEAgentProviderConfig,
    ) -> QuantumRepairPatch:
        self.invocations += 1
        identifier = f"provider-invocation-{invocation_sequence:03d}"
        directory = invocation_root / f"invocation-{invocation_sequence:03d}"
        store = InvocationStateStore(
            directory,
            identifier,
            lease_seconds=self.config.lease_seconds,
            crash_injector=None,
        )
        telemetry = ProviderTelemetryLog(
            directory / "provider-events.jsonl",
            repair_run_identifier=directive.repair_run_identifier,
            attempt_identifier=directive.source_attempt_identifier,
            invocation_identifier=identifier,
        )
        telemetry.append("provider_invocation_started", "created")
        store.crash_injector = self.crash_injector
        if self.crash_injector is not None:
            self.crash_injector("created")
        started = time.monotonic()
        api_key = invocation_config.api_key(self.environment)
        secrets = (api_key, str(directory.resolve()), str(source_root.resolve()))
        try:
            endpoint = verify_model_endpoint(
                base_url=invocation_config.base_url,
                requested_model=invocation_config.model_identifier,
                api_key=api_key,
                request_timeout_seconds=invocation_config.request_timeout_seconds,
                sampling=invocation_config.sampling,
                budget=invocation_config.budget,
            )
            telemetry.append(
                "model_endpoint_verified",
                "verified",
                model_identifier=endpoint.observed_model_identifier,
            )
            overlay = provider_overlay(invocation_config)
            agent = verify_pristine_sweagent(invocation_config, overlay)
            prompt = build_model_prompt(
                directive=directive,
                source_root=source_root,
                source_manifest=source_manifest,
                public_task=self.public_task,
                guidance_mode=invocation_config.guidance_mode,
                budget=invocation_config.budget,
                context_maximum_bytes=invocation_config.source_context_maximum_bytes,
                observed_context_length=endpoint.observed_context_length,
                secrets=(api_key,),
            )
            write_evidence(directory / "model-endpoint.json", endpoint)
            write_evidence(directory / "agent-descriptor.json", agent)
            write_evidence(directory / "model-prompt.json", prompt)
            request_values = {
                "provider_invocation_identifier": identifier,
                "invocation_sequence": invocation_sequence,
                "repair_run_identifier": directive.repair_run_identifier,
                "attempt_identifier": directive.source_attempt_identifier,
                "directive_sha256": directive.directive_sha256,
                "input_source_manifest_sha256": source_manifest.source_manifest_sha256,
                "public_task_identity": prompt.public_task_identity,
                "provider_capability_sha256": self.capability.fingerprint,
                "model_endpoint_descriptor_sha256": endpoint.descriptor_sha256,
                "agent_descriptor_sha256": agent.descriptor_sha256,
                "prompt_sha256": prompt.prompt_sha256,
                "budget": invocation_config.budget,
                "allowed_paths": directive.allowed_edit_paths,
            }
            request = seal_contract(
                ProviderInvocationRequest, request_values, "request_content_sha256"
            )
            store.persist_request(request)
            private = directory / "private"
            workspace = private / "agent-workspace"
            copy_source_tree(source_root, workspace)
            _initialize_repository(workspace)
            problem_file = private / "problem.md"
            problem_file.write_text(
                render_problem_statement(prompt), encoding="utf-8", newline="\n"
            )
            overlay_file = private / "provider-overlay.yaml"
            overlay_file.write_text(overlay, encoding="utf-8", newline="\n")
            output_directory = private / "official-output"
            output_directory.mkdir()
            command = build_official_command(
                config=invocation_config,
                endpoint=endpoint,
                workspace=workspace,
                problem_file=problem_file,
                output_directory=output_directory,
                overlay_file=overlay_file,
            )
            store.transition("launching")
            telemetry.append(
                "sweagent_started",
                "launching",
                model_identifier=endpoint.observed_model_identifier,
                agent_descriptor_sha256=agent.descriptor_sha256,
                prompt_sha256=prompt.prompt_sha256,
            )
            store.transition("running")
            last_heartbeat = 0.0

            def heartbeat() -> None:
                nonlocal last_heartbeat
                now = time.monotonic()
                if now - last_heartbeat >= invocation_config.heartbeat_seconds:
                    store.heartbeat()
                    telemetry.append(
                        "provider_heartbeat",
                        "running",
                        elapsed_seconds=now - started,
                    )
                    last_heartbeat = now

            process = run_bounded_process(
                command,
                cwd=workspace,
                environment=child_environment(
                    invocation_config, private / "home", api_key
                ),
                timeout_seconds=invocation_config.budget.maximum_wall_seconds,
                maximum_output_bytes=invocation_config.budget.maximum_tool_output_bytes,
                secrets=secrets,
                heartbeat_seconds=invocation_config.heartbeat_seconds,
                heartbeat=heartbeat,
            )
            (private / "sweagent.stdout.log").write_text(
                process.stdout, encoding="utf-8", newline="\n"
            )
            (private / "sweagent.stderr.log").write_text(
                process.stderr, encoding="utf-8", newline="\n"
            )
            store.transition("response_persisted")
            telemetry.append(
                "sweagent_completed",
                "timeout" if process.timed_out else "completed",
                elapsed_seconds=process.elapsed_seconds,
            )
            if process.timed_out:
                raise TimeoutError("Official SWE-agent exceeded its wall-time budget.")
            if process.exit_code != 0:
                raise RuntimeError("Official SWE-agent exited unsuccessfully.")
            patch, prediction_sha, prediction_path = extract_official_patch(
                output_directory=output_directory,
                source_root=source_root,
                source_manifest=source_manifest,
                directive=directive,
                provider_identifier=self.provider_identifier,
                provider_version=self.provider_version,
                budget=invocation_config.budget,
                extraction_root=private / "extraction",
                patch_identifier=f"model-patch-{invocation_sequence:03d}",
            )
            telemetry.append("prediction_observed", "observed")
            trajectory = redact_trajectory(
                invocation_identifier=identifier,
                raw_root=output_directory,
                portable_root=directory / "portable-trajectory",
                prediction_path=prediction_path,
                secrets=secrets,
            )
            telemetry.append(
                "model_request_completed",
                "completed",
                input_tokens=trajectory.input_tokens,
                output_tokens=trajectory.output_tokens,
            )
            telemetry.append(
                "tool_command_completed",
                "completed",
                tool_call_count=trajectory.tool_call_count,
            )
            _enforce_consumption(
                invocation_config, trajectory, directory / "portable-trajectory"
            )
            write_evidence(directory / "trajectory-manifest.json", trajectory)
            store.persist_patch(patch)
            store.transition("patch_extracted")
            telemetry.append(
                "patch_extraction_completed",
                "completed",
                patch_sha256=patch.patch_sha256,
                input_tokens=trajectory.input_tokens,
                output_tokens=trajectory.output_tokens,
                tool_call_count=trajectory.tool_call_count,
            )
            after = verify_pristine_sweagent(invocation_config, overlay)
            if after.descriptor_sha256 != agent.descriptor_sha256:
                raise ValueError("SWE-agent source identity changed during invocation.")
            completed = time.monotonic()
            result = _result(
                request=request,
                identifier=identifier,
                status="completed",
                started=started,
                completed=completed,
                exit_status=process.exit_code,
                trajectory=trajectory,
                prediction_sha=prediction_sha,
                patch_sha=patch.patch_sha256,
            )
            store.persist_result(result)
            store.transition("completed")
            telemetry.append(
                "provider_invocation_completed",
                "completed",
                model_identifier=endpoint.observed_model_identifier,
                patch_sha256=patch.patch_sha256,
                input_tokens=trajectory.input_tokens,
                output_tokens=trajectory.output_tokens,
                tool_call_count=trajectory.tool_call_count,
                elapsed_seconds=result.elapsed_seconds,
            )
            self.model_calls += trajectory.model_call_count
            self.input_tokens += trajectory.input_tokens
            self.output_tokens += trajectory.output_tokens
            self.tool_calls += trajectory.tool_call_count
            self.tool_output_bytes += result.tool_output_bytes
            self.elapsed_seconds += result.elapsed_seconds
            return patch
        except Exception as exc:
            self.elapsed_seconds += time.monotonic() - started
            if self.crash_injector is not None:
                raise
            _persist_failure(store, telemetry, exc, started)
            raise


def _result(
    *,
    request: ProviderInvocationRequest,
    identifier: str,
    status: str,
    started: float,
    completed: float,
    exit_status: int | None,
    trajectory: Any | None = None,
    prediction_sha: str | None = None,
    patch_sha: str | None = None,
    error_code: str | None = None,
    error_detail: str | None = None,
) -> ProviderInvocationResult:
    input_tokens = trajectory.input_tokens if trajectory is not None else 0
    output_tokens = trajectory.output_tokens if trajectory is not None else 0
    values = {
        "request_sha256": request.request_content_sha256,
        "provider_invocation_identifier": identifier,
        "terminal_status": status,
        "started_monotonic_seconds": started,
        "completed_monotonic_seconds": completed,
        "elapsed_seconds": completed - started,
        "sweagent_exit_status": exit_status,
        "model_request_count": trajectory.model_call_count
        if trajectory is not None
        else 0,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "tool_call_count": trajectory.tool_call_count if trajectory is not None else 0,
        "tool_output_bytes": (
            sum(item.byte_size for item in trajectory.artifacts)
            if trajectory is not None
            else 0
        ),
        "trajectory_identity": (
            trajectory.complete_trajectory_sha256 if trajectory is not None else None
        ),
        "prediction_identity": prediction_sha,
        "proposed_patch_identity": patch_sha,
        "sanitized_error_code": error_code,
        "sanitized_error_detail": error_detail,
    }
    return seal_contract(ProviderInvocationResult, values, "provider_result_sha256")


def _persist_failure(
    store: InvocationStateStore,
    telemetry: ProviderTelemetryLog,
    error: Exception,
    started: float,
) -> None:
    status = store.status
    target: InvocationStatus
    if status in {"created", "request_persisted", "launching", "running"}:
        target = "interrupted"
    elif status in {"response_persisted"}:
        target = "retryable_failure"
    elif status == "patch_extracted":
        target = "terminal_failure"
    else:
        return
    completed = time.monotonic()
    request_path = store.directory / "provider-request.json"
    if request_path.is_file():
        request = ProviderInvocationRequest.model_validate(read_json(request_path))
        result = _result(
            request=request,
            identifier=store.invocation_identifier,
            status=target,
            started=started,
            completed=completed,
            exit_status=None,
            error_code=type(error).__name__,
            error_detail=f"Provider invocation failed safely at state {status}.",
        )
        store.persist_result(result)
    store.transition(target)
    telemetry.append(
        "provider_invocation_interrupted",
        target,
        elapsed_seconds=completed - started,
    )


def _initialize_repository(workspace: Path) -> None:
    commands = (
        ("init", "-q"),
        ("config", "user.email", "cgr@invalid.local"),
        ("config", "user.name", "CGR Agent Workspace"),
        ("add", "--all"),
        ("commit", "-q", "-m", "candidate source snapshot"),
    )
    for arguments in commands:
        process = subprocess.run(
            ["git", "-C", str(workspace), *arguments],
            capture_output=True,
            text=True,
            check=False,
        )
        if process.returncode:
            raise ValueError("Could not initialize isolated SWE-agent workspace.")


def _enforce_consumption(
    config: SWEAgentProviderConfig, trajectory: Any, root: Path
) -> None:
    if trajectory.model_call_count > config.budget.maximum_model_calls:
        raise ValueError("Model-call budget was exceeded.")
    if trajectory.input_tokens > config.budget.maximum_input_tokens:
        raise ValueError("Input-token budget was exceeded.")
    if trajectory.output_tokens > config.budget.maximum_output_tokens:
        raise ValueError("Output-token budget was exceeded.")
    if (
        trajectory.input_tokens + trajectory.output_tokens
        > config.budget.maximum_total_tokens
    ):
        raise ValueError("Total-token budget was exceeded.")
    if trajectory.tool_call_count > config.budget.maximum_tool_commands:
        raise ValueError("Tool-command budget was exceeded.")
    output_bytes = sum(
        path.stat().st_size for path in root.rglob("*") if path.is_file()
    )
    if output_bytes > config.budget.maximum_tool_output_bytes:
        raise ValueError("Redacted trajectory output exceeded its byte budget.")


def _remaining_config(
    config: SWEAgentProviderConfig, consumption: dict[str, int | float]
) -> SWEAgentProviderConfig:
    """Reserve one invocation from the provider's repair-run-wide budget."""
    budget = config.budget
    remaining = {
        "maximum_model_calls": budget.maximum_model_calls
        - int(consumption["model_calls"]),
        "maximum_input_tokens": budget.maximum_input_tokens
        - int(consumption["input_tokens"]),
        "maximum_output_tokens": budget.maximum_output_tokens
        - int(consumption["output_tokens"]),
        "maximum_total_tokens": budget.maximum_total_tokens
        - int(consumption["total_tokens"]),
        "maximum_wall_seconds": int(
            budget.maximum_wall_seconds - float(consumption["elapsed_seconds"])
        ),
        "maximum_tool_commands": budget.maximum_tool_commands
        - int(consumption["tool_calls"]),
        "maximum_tool_output_bytes": budget.maximum_tool_output_bytes
        - int(consumption["tool_output_bytes"]),
    }
    if any(value <= 0 for value in remaining.values()):
        raise RepairProviderError("Provider repair-run budget is exhausted.")
    values = budget.model_dump(mode="json")
    values.update(remaining)
    return config.model_copy(update={"budget": ProviderBudget.model_validate(values)})
