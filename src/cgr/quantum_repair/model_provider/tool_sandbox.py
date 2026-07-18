"""Immutable tool-image validation and exact offline SWE-ReX preflight."""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from cgr.science import sha256_fingerprint

from .agent import TOOL_DOCKER_ARGS
from .config import SWEAgentProviderConfig
from .contracts import (
    ToolSandboxHealthArtifact,
    ToolSandboxImageDescriptor,
    seal_contract,
)

BUILD_SCHEMA = "cgr.quantum-sweagent-tool-image-build/1.0.0"
IMAGE_SCHEMA = "cgr.quantum-sweagent-tool-image/1.0.0"
_INSTALL_ATTEMPT = re.compile(
    r"(?i)(?:python\d*\s+-m\s+pip\s+install|\bpipx?\s+install|"
    r"\bapt(?:-get)?\s+install|\bapk\s+add|\bdnf\s+install|"
    r"\byum\s+install|\bconda\s+install|/simple/pipx/)"
)


class ToolSandboxError(RuntimeError):
    def __init__(
        self, code: str, message: str, *, package_install_attempt: bool = False
    ) -> None:
        super().__init__(message)
        self.code = code
        self.package_install_attempt = package_install_attempt


def inspect_tool_image(
    config: SWEAgentProviderConfig,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> ToolSandboxImageDescriptor:
    """Resolve labels and reject mutable, substituted, or incompatible images."""
    if config.tool_container_image == "sha256:" + "0" * 64:
        raise ToolSandboxError("missing_image_identity", "Tool image was not built.")
    docker = shutil.which("docker")
    if docker is None:
        raise ToolSandboxError("docker_unavailable", "Docker is unavailable.")
    process = runner(
        [docker, "image", "inspect", config.tool_container_image],
        capture_output=True,
        text=True,
        check=False,
    )
    if process.returncode:
        raise ToolSandboxError("tool_image_missing", "Tool image is unavailable.")
    try:
        payload = json.loads(process.stdout)
        image = payload[0]
        observed_id = image["Id"]
        labels = image["Config"]["Labels"] or {}
    except (IndexError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ToolSandboxError(
            "tool_image_inspection_failed", "Tool image metadata is malformed."
        ) from exc
    if observed_id != config.tool_container_image:
        raise ToolSandboxError(
            "tool_image_digest_mismatch", "Local tool image identity was substituted."
        )
    expected = {
        "org.cgr.tool-sandbox.schema": IMAGE_SCHEMA,
        "org.cgr.tool-sandbox.build-input-sha256": config.tool_image_build_input_sha256,
        "org.cgr.tool-sandbox.sweagent-commit": config.required_sweagent_commit,
        "org.cgr.tool-sandbox.swerex-version": config.required_swerex_version,
        "org.cgr.tool-sandbox.offline-bootstrap": "true",
        "org.cgr.tool-sandbox.network-policy": "none",
    }
    if any(labels.get(name) != value for name, value in expected.items()):
        raise ToolSandboxError(
            "tool_image_provenance_mismatch",
            "Tool image labels do not match the reviewed build contract.",
        )
    values = {
        "image_repository": config.tool_container_image_repository,
        "image_id": observed_id,
        "build_schema_version": config.tool_image_build_schema_version,
        "build_input_sha256": config.tool_image_build_input_sha256,
        "offline_bootstrap": True,
        "network_policy": "none",
        "required_sweagent_commit": config.required_sweagent_commit,
        "swerex_version": config.required_swerex_version,
        "runtime_identity": sha256_fingerprint(
            {"image_id": observed_id, "labels": expected}
        ),
    }
    return seal_contract(ToolSandboxImageDescriptor, values, "descriptor_sha256")


def deployment_configuration(config: SWEAgentProviderConfig) -> dict[str, Any]:
    return {
        "type": "docker",
        "image": config.tool_container_image,
        "docker_args": list(TOOL_DOCKER_ARGS),
        "startup_timeout": float(config.tool_startup_timeout_seconds),
        "pull": "never",
        "remove_images": False,
        "python_standalone_dir": None,
        "remove_container": True,
        "container_runtime": "docker",
    }


def run_offline_tool_preflight(
    config: SWEAgentProviderConfig,
    *,
    image: ToolSandboxImageDescriptor | None = None,
    deployment_factory: Callable[[dict[str, Any]], Any] | None = None,
) -> ToolSandboxHealthArtifact:
    """Exercise the real SWE-ReX Docker lifecycle with networking disabled."""
    descriptor = image or inspect_tool_image(config)
    deployment_values = deployment_configuration(config)
    deployment_identity = sha256_fingerprint(deployment_values)
    started = time.monotonic()
    shell = workspace = cleanup = False
    credential_forwarding = docker_socket = model_access = package_attempt = False
    failure: str | None = None
    deployment: Any | None = None
    containers_before = _running_image_containers(descriptor.image_id)
    try:
        if deployment_factory is None:
            from swerex.deployment.config import DockerDeploymentConfig

            deployment = DockerDeploymentConfig.model_validate(
                deployment_values
            ).get_deployment()
        else:
            deployment = deployment_factory(deployment_values)
        observations = asyncio.run(_exercise_deployment(deployment))
        shell = observations["shell"]
        workspace = observations["workspace"]
        credential_forwarding = observations["credential_forwarding"]
        docker_socket = observations["docker_socket"]
        model_access = observations["model_access"]
    except Exception as exc:
        text = str(exc)
        package_attempt = bool(_INSTALL_ATTEMPT.search(text))
        failure = classify_bootstrap_failure(text)
    finally:
        if deployment is not None:
            try:
                asyncio.run(deployment.stop())
                cleanup = not (
                    _running_image_containers(descriptor.image_id) - containers_before
                )
            except Exception:
                cleanup = False
    status = (
        "passed"
        if all(
            (
                shell,
                workspace,
                cleanup,
                not credential_forwarding,
                not docker_socket,
                not model_access,
                not package_attempt,
            )
        )
        else "failed"
    )
    if status == "failed" and failure is None:
        failure = "tool_sandbox_policy_failure"
    values = {
        "tool_image_descriptor_sha256": descriptor.descriptor_sha256,
        "deployment_identity_sha256": deployment_identity,
        "network_mode": "none",
        "startup_result": status,
        "shell_smoke_passed": shell,
        "workspace_write_passed": workspace,
        "cleanup_passed": cleanup,
        "credential_forwarding_observed": credential_forwarding,
        "docker_socket_forwarded": docker_socket,
        "model_endpoint_reachable": model_access,
        "infrastructure_package_install_attempt_observed": package_attempt,
        "runtime_seconds": time.monotonic() - started,
        "failure_classification": failure,
    }
    return seal_contract(ToolSandboxHealthArtifact, values, "health_artifact_sha256")


async def _exercise_deployment(deployment: Any) -> dict[str, bool]:
    from swerex.runtime.abstract import Command, UploadRequest

    await deployment.start()
    runtime = deployment.runtime
    await runtime.execute(
        Command(command="printf cgr-shell-ready", shell=True, check=True)
    )
    with tempfile.TemporaryDirectory(prefix="cgr-tool-preflight-") as temporary:
        source = Path(temporary) / "workspace-file"
        source.write_text("before", encoding="utf-8")
        await runtime.upload(
            UploadRequest(
                source_path=str(source), target_path="/cgr-preflight/workspace-file"
            )
        )
        await runtime.execute(
            Command(
                command=(
                    "printf after >> /cgr-preflight/workspace-file && "
                    'test "$(cat /cgr-preflight/workspace-file)" = beforeafter'
                ),
                shell=True,
                check=True,
            )
        )
    environment = await runtime.execute(Command(command="env", shell=True, check=True))
    environment_text = getattr(environment, "stdout", "") or getattr(
        environment, "output", ""
    )
    prohibited = ("AWS_", "IBM_", "GITHUB_", "SSH_", "CGR_REPAIR_MODEL_API_KEY")
    credential_forwarding = any(item in environment_text for item in prohibited)
    docker_observation = await runtime.execute(
        Command(command="test -S /var/run/docker.sock", shell=True, check=False)
    )
    docker_socket = getattr(docker_observation, "exit_code", 1) == 0
    model_observation = await runtime.execute(
        Command(
            command=(
                'python3 -c "import socket; s=socket.socket(); '
                "s.settimeout(1); raise SystemExit(0 if "
                "s.connect_ex(('127.0.0.1',8000)) == 0 else 1)\""
            ),
            shell=True,
            check=False,
        )
    )
    model_access = getattr(model_observation, "exit_code", 1) == 0
    return {
        "shell": True,
        "workspace": True,
        "credential_forwarding": credential_forwarding,
        "docker_socket": docker_socket,
        "model_access": model_access,
    }


def classify_bootstrap_failure(value: str) -> str:
    lowered = value.lower()
    if _INSTALL_ATTEMPT.search(value) and (
        "name resolution" in lowered
        or "could not find a version" in lowered
        or "/simple/pipx/" in lowered
    ):
        return "offline_dependency_missing"
    if "container process terminated" in lowered:
        return "tool_container_terminated_during_startup"
    return "tool_sandbox_bootstrap_failure"


def infrastructure_install_attempt_observed(value: str) -> bool:
    return bool(_INSTALL_ATTEMPT.search(value))


def _running_image_containers(image_id: str) -> set[str]:
    docker = shutil.which("docker")
    if docker is None:
        return set()
    process = subprocess.run(
        [docker, "ps", "--filter", f"ancestor={image_id}", "--format", "{{.ID}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return set(process.stdout.splitlines()) if process.returncode == 0 else set()


def failed_tool_health(
    config: SWEAgentProviderConfig, error: Exception
) -> ToolSandboxHealthArtifact:
    """Create portable fail-closed health evidence when image/startup fails early."""
    values = {
        "tool_image_descriptor_sha256": "0" * 64,
        "deployment_identity_sha256": sha256_fingerprint(
            deployment_configuration(config)
        ),
        "network_mode": "none",
        "startup_result": "failed",
        "shell_smoke_passed": False,
        "workspace_write_passed": False,
        "cleanup_passed": True,
        "credential_forwarding_observed": False,
        "docker_socket_forwarded": False,
        "model_endpoint_reachable": False,
        "infrastructure_package_install_attempt_observed": getattr(
            error,
            "package_install_attempt",
            infrastructure_install_attempt_observed(str(error)),
        ),
        "runtime_seconds": 0.0,
        "failure_classification": getattr(
            error, "code", "tool_sandbox_bootstrap_failure"
        ),
    }
    return seal_contract(ToolSandboxHealthArtifact, values, "health_artifact_sha256")
