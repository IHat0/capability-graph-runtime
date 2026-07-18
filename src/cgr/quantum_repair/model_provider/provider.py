"""Production whole-invocation pristine SWE-agent repair provider."""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from cgr.science import sha256_fingerprint

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
    tool_control_proxy_policy_descriptor,
    tool_network_policy_descriptor,
    verify_pristine_sweagent,
)
from .config import SWEAgentProviderConfig
from .contracts import (
    InvocationStatus,
    ProviderBudget,
    ProviderInvocationRequest,
    ProviderInvocationResult,
    ToolControlProxyLifecycleArtifact,
    seal_contract,
)
from .endpoint import verify_model_endpoint
from .control_proxy import LoopbackControlProxy, select_loopback_port
from .extraction import extract_official_patch, redact_trajectory
from .process import run_bounded_process
from .prompting import build_model_prompt, render_problem_statement
from .recovery import InvocationStateStore, recover_attempt_invocations
from .telemetry import ProviderTelemetryLog
from .tool_sandbox import (
    ToolSandboxError,
    OwnedControlNetwork,
    classify_bootstrap_failure,
    infrastructure_install_attempt_observed,
    inspect_tool_image,
    recover_stale_control_networks,
)


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
        recover_stale_control_networks(invocation_root)
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
        control_network: OwnedControlNetwork | None = None
        control_proxy: LoopbackControlProxy | None = None
        control_endpoint: Any | None = None
        control_port = 0
        proxy_started = 0.0
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
            tool_image = inspect_tool_image(invocation_config)
            private = directory / "private"
            private.mkdir(exist_ok=True)
            control_network = OwnedControlNetwork.create(
                private / "tool-network-state.json"
            )
            control_port = select_loopback_port()
            overlay = provider_overlay(
                invocation_config,
                control_network_name=control_network.name,
                network_ownership_nonce=control_network.ownership_nonce,
                control_port=control_port,
            )
            agent = verify_pristine_sweagent(
                invocation_config,
                overlay=overlay,
                tool_image_descriptor=tool_image,
            )
            network_policy = tool_network_policy_descriptor(invocation_config)
            proxy_policy = tool_control_proxy_policy_descriptor(invocation_config)
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
            write_evidence(directory / "tool-image-descriptor.json", tool_image)
            write_evidence(directory / "tool-network-policy.json", network_policy)
            write_evidence(directory / "tool-control-proxy-policy.json", proxy_policy)
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
            control_channel_observed = False

            def heartbeat() -> None:
                nonlocal control_channel_observed, control_endpoint
                nonlocal control_proxy, last_heartbeat, proxy_started
                if control_network is None:
                    raise ToolSandboxError(
                        "tool_control_proxy_destination_invalid",
                        "The owned tool network disappeared during startup.",
                    )
                observed = control_network.discover_owned_container(tool_image.image_id)
                if control_proxy is None and observed is not None:
                    try:
                        control_network.verify_direct_control(observed)
                    except ToolSandboxError as exc:
                        if exc.code == "tool_runtime_control_channel_unreachable":
                            observed = None
                        else:
                            raise
                    if observed is None:
                        pass
                    else:
                        control_endpoint = observed
                        control_proxy = LoopbackControlProxy(
                            source_port=control_port,
                            endpoint=observed.proxy_endpoint(control_network),
                        )
                        control_proxy.start()
                        proxy_started = time.monotonic()
                        control_channel_observed = True
                elif control_proxy is not None:
                    if observed != control_endpoint:
                        raise ToolSandboxError(
                            "tool_control_proxy_destination_invalid",
                            "The supervised tool container destination changed.",
                        )
                    control_proxy.assert_healthy()
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
                model_artifacts_exist = any(
                    path.suffix.lower() in {".traj", ".pred"}
                    for path in output_directory.rglob("*")
                    if path.is_file()
                )
                classification = (
                    "agent_execution_failure"
                    if model_artifacts_exist
                    else classify_bootstrap_failure(process.stderr)
                )
                raise ToolSandboxError(
                    classification,
                    "Official SWE-agent deployment failed safely.",
                    package_install_attempt=(
                        not model_artifacts_exist
                        and infrastructure_install_attempt_observed(process.stderr)
                    ),
                )
            if not control_channel_observed:
                raise ToolSandboxError(
                    "tool_runtime_control_channel_unreachable",
                    "The official SWE-ReX control channel was not observed.",
                )
            assert control_proxy is not None
            _release_control_proxy(
                control_proxy,
                directory,
                proxy_policy.descriptor_sha256,
                control_endpoint,
                control_network.identifier_sha256,
                control_port,
                proxy_started,
            )
            control_proxy = None
            _release_control_network(control_network)
            control_network = None
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
            after = verify_pristine_sweagent(
                invocation_config,
                overlay=overlay,
                tool_image_descriptor=tool_image,
            )
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
            failure = exc
            if control_proxy is not None:
                try:
                    _release_control_proxy(
                        control_proxy,
                        directory,
                        tool_control_proxy_policy_descriptor(
                            invocation_config
                        ).descriptor_sha256,
                        control_endpoint,
                        control_network.identifier_sha256
                        if control_network is not None
                        else "0" * 64,
                        control_port,
                        proxy_started,
                        failure_classification=getattr(
                            failure, "code", type(failure).__name__
                        ),
                    )
                    control_proxy = None
                except ToolSandboxError as cleanup_error:
                    failure = cleanup_error
            lifecycle_path = directory / "tool-control-proxy-lifecycle.json"
            if control_network is not None and not lifecycle_path.is_file():
                _write_failed_proxy_lifecycle(
                    directory,
                    invocation_config,
                    control_endpoint,
                    control_network.identifier_sha256,
                    control_port,
                    proxy_started,
                    getattr(failure, "code", type(failure).__name__),
                )
            if control_network is not None:
                try:
                    _release_control_network(control_network)
                except ToolSandboxError as cleanup_error:
                    failure = cleanup_error
            self.elapsed_seconds += time.monotonic() - started
            if self.crash_injector is not None:
                raise
            _persist_failure(store, telemetry, failure, started)
            raise failure


def _release_control_network(network: OwnedControlNetwork) -> None:
    container_cleanup, network_cleanup = network.cleanup()
    if not container_cleanup:
        raise ToolSandboxError(
            "tool_container_cleanup_failure",
            "The invocation tool container could not be removed.",
        )
    if not network_cleanup:
        raise ToolSandboxError(
            "tool_network_cleanup_failure",
            "The invocation tool control network could not be removed.",
        )


def _release_control_proxy(
    proxy: LoopbackControlProxy,
    directory: Path,
    policy_sha256: str,
    endpoint: Any,
    network_identity_sha256: str,
    source_port: int,
    started: float,
    *,
    failure_classification: str | None = None,
) -> None:
    try:
        cleanup = proxy.stop()
    except Exception as exc:
        raise ToolSandboxError(
            "tool_control_proxy_cleanup_failure",
            "The invocation control proxy could not be removed.",
        ) from exc
    if not cleanup or endpoint is None:
        raise ToolSandboxError(
            "tool_control_proxy_cleanup_failure",
            "The invocation control proxy cleanup evidence is incomplete.",
        )
    values = {
        "proxy_policy_descriptor_sha256": policy_sha256,
        "proxy_bind_identity_sha256": sha256_fingerprint(
            {"address": "127.0.0.1", "port": source_port}
        ),
        "proxy_bind_address": "127.0.0.1",
        "proxy_source_port": source_port,
        "proxy_destination_container_identity": endpoint.container_identity,
        "proxy_destination_image_identity": endpoint.image_identity,
        "proxy_destination_internal_ip_identity": sha256_fingerprint(
            {"internal_ipv4": endpoint.internal_ipv4}
        ),
        "proxy_destination_network_identity_sha256": network_identity_sha256,
        "startup_result": "failed" if failure_classification else "passed",
        "readiness_result": "passed",
        "cleanup_passed": True,
        "proxy_cleanup_passed": True,
        "container_cleanup_passed": True,
        "network_cleanup_passed": True,
        "official_deployment_stop_passed": True,
        "fallback_cleanup_required": False,
        "fallback_proxy_cleanup_passed": False,
        "fallback_container_cleanup_passed": False,
        "fallback_network_cleanup_passed": False,
        "runtime_seconds": max(0.0, time.monotonic() - started),
        "failure_classification": failure_classification,
    }
    lifecycle = seal_contract(
        ToolControlProxyLifecycleArtifact,
        values,
        "lifecycle_artifact_sha256",
    )
    write_evidence(directory / "tool-control-proxy-lifecycle.json", lifecycle)


def _write_failed_proxy_lifecycle(
    directory: Path,
    config: SWEAgentProviderConfig,
    endpoint: Any | None,
    network_identity_sha256: str,
    source_port: int,
    started: float,
    failure_classification: str,
) -> None:
    policy = tool_control_proxy_policy_descriptor(config)
    values = {
        "proxy_policy_descriptor_sha256": policy.descriptor_sha256,
        "proxy_bind_identity_sha256": sha256_fingerprint(
            {"address": "127.0.0.1", "port": source_port}
        ),
        "proxy_bind_address": "127.0.0.1",
        "proxy_source_port": source_port,
        "proxy_destination_container_identity": (
            endpoint.container_identity if endpoint is not None else "unavailable"
        ),
        "proxy_destination_image_identity": (
            endpoint.image_identity
            if endpoint is not None
            else config.tool_container_image
        ),
        "proxy_destination_internal_ip_identity": sha256_fingerprint(
            {
                "internal_ipv4": (
                    endpoint.internal_ipv4 if endpoint is not None else "unavailable"
                )
            }
        ),
        "proxy_destination_network_identity_sha256": network_identity_sha256,
        "startup_result": "failed",
        "readiness_result": "not_reached",
        "cleanup_passed": False,
        "proxy_cleanup_passed": True,
        "container_cleanup_passed": False,
        "network_cleanup_passed": False,
        "official_deployment_stop_passed": False,
        "fallback_cleanup_required": True,
        "fallback_proxy_cleanup_passed": True,
        "fallback_container_cleanup_passed": False,
        "fallback_network_cleanup_passed": False,
        "runtime_seconds": max(0.0, time.monotonic() - started) if started else 0.0,
        "failure_classification": failure_classification,
    }
    lifecycle = seal_contract(
        ToolControlProxyLifecycleArtifact,
        values,
        "lifecycle_artifact_sha256",
    )
    write_evidence(directory / "tool-control-proxy-lifecycle.json", lifecycle)


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
    package_install_attempt: bool = False,
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
        "infrastructure_package_install_attempt_observed": package_install_attempt,
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
            error_code=getattr(error, "code", type(error).__name__),
            error_detail=f"Provider invocation failed safely at state {status}.",
            package_install_attempt=getattr(
                error,
                "package_install_attempt",
                infrastructure_install_attempt_observed(str(error)),
            ),
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
