"""Single-case real-provider smoke gate before comparative acceptance."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from cgr.quantum_candidate.trusted import load_verified_trusted_reference
from cgr.quantum_preflight.artifacts import write_json_atomic
from cgr.quantum_preflight.manifests import load_manifest
from cgr.science import sha256_fingerprint

from .benchmark import load_repair_benchmark
from .benchmark_provider import materialize_benchmark_source
from .contracts import QuantumRepairPolicy
from .model_acceptance import load_model_acceptance_manifest
from .model_provider.agent import (
    repository_commit,
    tool_control_proxy_policy_descriptor,
    tool_network_policy_descriptor,
    verify_pristine_sweagent,
)
from .model_provider.config import SWEAgentProviderConfig
from .model_provider.endpoint import verify_model_endpoint
from .model_provider.provider import SWEAgentOpenAICompatibleRepairProvider
from .model_provider.tool_sandbox import (
    inspect_tool_image,
    run_offline_tool_preflight,
    verify_control_proxy_lifecycle_evidence,
)
from .orchestrator import run_repair
from .persistence import read_json, write_evidence


def run_provider_smoke(
    *,
    acceptance_manifest_path: Path,
    provider_config: SWEAgentProviderConfig,
    trusted_reference_directory: Path,
    result_root: Path,
    candidate_image_identifier: str,
    candidate_lock_path: Path,
    fixture_root: Path,
    diagnosis_support_path: Path,
) -> dict[str, Any]:
    acceptance = load_model_acceptance_manifest(acceptance_manifest_path)
    repair_path = acceptance_manifest_path.parent / acceptance.repair_benchmark_manifest
    if hashlib.sha256(repair_path.read_bytes()).hexdigest() != (
        acceptance.repair_benchmark_manifest_sha256
    ):
        raise ValueError("Repair benchmark manifest changed before provider smoke.")
    repair = load_repair_benchmark(repair_path)
    case = next(item for item in repair.cases if item.case_identifier == "syntax-error")
    public_manifest = load_manifest(
        (repair_path.parent / repair.public_experiment_manifest).resolve()
    )
    trusted = load_verified_trusted_reference(
        trusted_reference_directory, public_manifest.experiment
    )
    directory = _next_smoke_directory(result_root)
    directory.mkdir(parents=True)
    tool_image = inspect_tool_image(provider_config)
    health = run_offline_tool_preflight(
        provider_config,
        image=tool_image,
        lifecycle_root=directory / "private-preflight",
    )
    verify_control_proxy_lifecycle_evidence(
        directory / "private-preflight" / "tool-control-proxy-lifecycle.json",
        health.tool_control_proxy_lifecycle_artifact_sha256,
    )
    write_evidence(directory / "tool-image-descriptor.json", tool_image)
    write_evidence(
        directory / "tool-network-policy.json",
        tool_network_policy_descriptor(provider_config),
    )
    write_evidence(
        directory / "tool-control-proxy-policy.json",
        tool_control_proxy_policy_descriptor(provider_config),
    )
    write_evidence(directory / "provider-preflight.json", health)
    if health.startup_result != "passed":
        return _write_report(
            directory,
            {
                "provider_smoke_passed": False,
                "failure_classification": health.failure_classification,
                "model_request_count": 0,
                "total_model_tokens": 0,
            },
        )
    endpoint = verify_model_endpoint(
        base_url=provider_config.base_url,
        requested_model=provider_config.model_identifier,
        api_key=provider_config.api_key(),
        request_timeout_seconds=provider_config.request_timeout_seconds,
        sampling=provider_config.sampling,
        budget=provider_config.budget,
    )
    agent = verify_pristine_sweagent(provider_config, tool_image_descriptor=tool_image)
    write_evidence(directory / "model-endpoint.json", endpoint)
    write_evidence(directory / "agent-descriptor.json", agent)
    source = directory / "source-initial"
    materialize_benchmark_source(
        template_root=fixture_root / "_template",
        support_root=fixture_root / "_support",
        diagnosis_support=diagnosis_support_path,
        destination=source,
        candidate_identifier=case.case_identifier,
        defects=case.initial_defects,
    )
    provider = SWEAgentOpenAICompatibleRepairProvider(
        config=provider_config.model_copy(update={"guidance_mode": "cgr"}),
        public_task=public_manifest.experiment.model_dump(mode="json"),
    )
    result = run_repair(
        task_identifier=case.case_identifier,
        candidate_source=source,
        public_manifest=public_manifest,
        trusted=trusted,
        result_root=directory,
        candidate_image_identifier=candidate_image_identifier,
        candidate_lock_path=candidate_lock_path,
        provider=provider,
        repair_policy=QuantumRepairPolicy(
            maximum_attempts=2,
            maximum_provider_seconds=min(
                3600, provider_config.budget.maximum_wall_seconds + 30
            ),
            maximum_total_seconds=min(
                3600, provider_config.budget.maximum_wall_seconds + 600
            ),
        ),
    )
    run_directory = Path(result["repair_run_directory"])
    provider_results = [
        read_json(path) for path in sorted(run_directory.rglob("provider-result.json"))
    ]
    official_artifacts = any(run_directory.rglob("trajectory-manifest.json"))
    consumption = provider.consumption
    model_requests = int(consumption["model_calls"])
    total_tokens = int(consumption["total_tokens"])
    precise_failure = next(
        (
            item.get("sanitized_error_code")
            for item in provider_results
            if item.get("sanitized_error_code")
        ),
        None,
    )
    passed = bool(
        model_requests > 0
        and total_tokens > 0
        and (official_artifacts or precise_failure)
        and result["replay_verified"]
        and health.control_network_type == "docker_internal"
        and health.network_internal
        and health.control_bind_address == "127.0.0.1"
        and not health.public_port_exposure_observed
        and not health.docker_published_host_port_observed
        and health.direct_internal_control_reachable
        and health.proxy_readiness_passed
        and health.proxy_cleanup_passed
        and not health.direct_external_ip_reachable
        and not health.external_hostname_reachable
        and not health.pypi_reachable
        and not health.credential_forwarding_observed
        and not health.docker_socket_forwarded
        and not health.model_endpoint_reachable
    )
    return _write_report(
        directory,
        {
            "provider_smoke_passed": passed,
            "failure_classification": None if passed else precise_failure,
            "repair_run_directory": run_directory.relative_to(directory).as_posix(),
            "authorized": result["authorized"],
            "terminal_status": result["terminal_status"],
            "replay_verified": result["replay_verified"],
            "model_request_count": model_requests,
            "total_model_tokens": total_tokens,
            "official_artifacts_observed": official_artifacts,
            "tool_image_descriptor_sha256": tool_image.descriptor_sha256,
            "tool_network_policy_descriptor_sha256": (
                health.tool_network_policy_descriptor_sha256
            ),
            "tool_control_proxy_policy_descriptor_sha256": (
                health.tool_control_proxy_policy_descriptor_sha256
            ),
            "tool_control_proxy_lifecycle_artifact_sha256": (
                health.tool_control_proxy_lifecycle_artifact_sha256
            ),
            "endpoint_descriptor_sha256": endpoint.descriptor_sha256,
            "agent_descriptor_sha256": agent.descriptor_sha256,
            "provider_configuration_sha256": sha256_fingerprint(
                provider_config.model_dump(mode="json")
            ),
            "cgr_commit": repository_commit(Path(__file__).parents[3]),
            "tool_control_network_type": health.control_network_type,
            "trusted_evidence_exposure": 0,
            "candidate_model_endpoint_access": 0,
        },
    )


def _write_report(directory: Path, values: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "schema_version": "cgr.quantum-repair-provider-smoke/1.2.0",
        **values,
    }
    payload["report_sha256"] = sha256_fingerprint(payload)
    path = directory / "provider-smoke-report.json"
    write_json_atomic(path, payload, maximum_bytes=2 * 1024 * 1024)
    return {**payload, "report_path": str(path)}


def _next_smoke_directory(result_root: Path) -> Path:
    root = result_root / "quantum-provider-smoke"
    root.mkdir(parents=True, exist_ok=True)
    for index in range(1, 1_000_000):
        candidate = root / f"smoke-{index:03d}"
        if not candidate.exists():
            return candidate
    raise ValueError("No provider smoke identifier remains available.")
