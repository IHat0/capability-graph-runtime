"""Immutable tool-image validation and offline SWE-ReX control networking."""

from __future__ import annotations

import asyncio
import json
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cgr.science import sha256_fingerprint

from .agent import (
    TOOL_DOCKER_RESOURCE_ARGS,
    TOOL_NETWORK_OWNERSHIP_LABEL,
    tool_network_policy_descriptor,
)
from .config import SWEAgentProviderConfig
from .contracts import (
    ToolSandboxHealthArtifact,
    ToolSandboxImageDescriptor,
    seal_contract,
)

BUILD_SCHEMA = "cgr.quantum-sweagent-tool-image-build/1.1.0"
IMAGE_SCHEMA = "cgr.quantum-sweagent-tool-image/1.1.0"
NETWORK_SCHEMA = "cgr.quantum-swerex-control-network/1.0.0"
NETWORK_SCHEMA_LABEL = "org.cgr.swerex-control.schema"
HOST_BINDING_OPTION = "com.docker.network.bridge.host_binding_ipv4"
_INSTALL_ATTEMPT = re.compile(
    r"(?i)(?:python\d*\s+-m\s+pip\s+install|\bpipx?\s+install|"
    r"\bapt(?:-get)?\s+install|\bapk\s+add|\bdnf\s+install|"
    r"\byum\s+install|\bconda\s+install|/simple/pipx/)"
)

Runner = Callable[..., subprocess.CompletedProcess[str]]


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
    runner: Runner = subprocess.run,
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
        "org.cgr.tool-sandbox.external-egress-disabled": "true",
        "org.cgr.tool-sandbox.control-network": "docker-internal",
        "org.cgr.tool-sandbox.control-bind-address": "127.0.0.1",
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
        "network_policy": "docker-internal-loopback-control",
        "required_sweagent_commit": config.required_sweagent_commit,
        "swerex_version": config.required_swerex_version,
        "runtime_identity": sha256_fingerprint(
            {"image_id": observed_id, "labels": expected}
        ),
    }
    return seal_contract(ToolSandboxImageDescriptor, values, "descriptor_sha256")


@dataclass
class OwnedControlNetwork:
    """Invocation-owned Docker network surrounding official SWE-ReX lifecycle."""

    docker: str
    name: str
    network_id: str
    ownership_nonce: str
    state_path: Path
    runner: Runner = subprocess.run

    @classmethod
    def create(
        cls,
        state_path: Path,
        *,
        runner: Runner = subprocess.run,
        docker: str | None = None,
    ) -> OwnedControlNetwork:
        executable = docker or shutil.which("docker")
        if executable is None:
            raise ToolSandboxError("docker_unavailable", "Docker is unavailable.")
        recover_owned_control_network(state_path, runner=runner, docker=executable)
        nonce = uuid.uuid4().hex
        name = f"cgr-swerex-{nonce[:20]}"
        process = _docker(
            runner,
            executable,
            "network",
            "create",
            "--driver=bridge",
            "--internal",
            "--opt",
            f"{HOST_BINDING_OPTION}=127.0.0.1",
            "--label",
            f"{NETWORK_SCHEMA_LABEL}={NETWORK_SCHEMA}",
            "--label",
            f"{TOOL_NETWORK_OWNERSHIP_LABEL}={nonce}",
            name,
        )
        if process.returncode:
            raise ToolSandboxError(
                "tool_control_network_creation_failure",
                "Could not create the invocation tool control network.",
            )
        network_id = process.stdout.strip()
        network = cls(executable, name, network_id, nonce, state_path, runner)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps(
                {
                    "schema_version": NETWORK_SCHEMA,
                    "network_name": name,
                    "network_id": network_id,
                    "ownership_nonce": nonce,
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        try:
            network.inspect_owned()
        except Exception:
            if network._remove_network_if_owned():
                state_path.unlink(missing_ok=True)
            raise
        return network

    @property
    def identifier_sha256(self) -> str:
        return sha256_fingerprint(
            {
                "network_id": self.network_id,
                "network_name": self.name,
                "ownership_nonce": self.ownership_nonce,
            }
        )

    @property
    def docker_args(self) -> tuple[str, ...]:
        return (
            f"--network={self.name}",
            f"--label={TOOL_NETWORK_OWNERSHIP_LABEL}={self.ownership_nonce}",
            *TOOL_DOCKER_RESOURCE_ARGS,
        )

    def inspect_owned(self) -> dict[str, Any]:
        payload = self._inspect_network()
        labels = payload.get("Labels") or {}
        options = payload.get("Options") or {}
        if (
            payload.get("Id") != self.network_id
            or payload.get("Name") != self.name
            or labels.get(NETWORK_SCHEMA_LABEL) != NETWORK_SCHEMA
            or labels.get(TOOL_NETWORK_OWNERSHIP_LABEL) != self.ownership_nonce
        ):
            raise ToolSandboxError(
                "tool_control_network_creation_failure",
                "Tool control network ownership could not be established.",
            )
        if payload.get("Driver") != "bridge" or payload.get("Internal") is not True:
            raise ToolSandboxError(
                "tool_control_network_not_internal",
                "Tool control network is not an internal bridge.",
            )
        if options.get(HOST_BINDING_OPTION) != "127.0.0.1":
            raise ToolSandboxError(
                "tool_control_port_publicly_exposed",
                "Tool control network does not default to loopback binding.",
            )
        return payload

    def inspect_running_container(self, container_name: str | None) -> int:
        if not container_name:
            raise ToolSandboxError(
                "tool_runtime_control_channel_unreachable",
                "SWE-ReX did not expose a tool container identity.",
            )
        process = _docker(
            self.runner, self.docker, "container", "inspect", container_name
        )
        if process.returncode:
            raise ToolSandboxError(
                "tool_runtime_control_channel_unreachable",
                "The SWE-ReX tool container could not be inspected.",
            )
        try:
            payload = json.loads(process.stdout)[0]
            labels = payload["Config"].get("Labels") or {}
            networks = payload["NetworkSettings"]["Networks"]
            bindings = payload["NetworkSettings"]["Ports"]["8000/tcp"]
        except (IndexError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ToolSandboxError(
                "tool_runtime_control_channel_unreachable",
                "The SWE-ReX tool container network metadata is malformed.",
            ) from exc
        if (
            set(networks) != {self.name}
            or labels.get(TOOL_NETWORK_OWNERSHIP_LABEL) != self.ownership_nonce
        ):
            raise ToolSandboxError(
                "tool_control_network_not_internal",
                "The tool container joined an unexpected Docker network.",
            )
        if not isinstance(bindings, list) or len(bindings) != 1:
            raise ToolSandboxError(
                "tool_control_port_publicly_exposed",
                "The SWE-ReX control port binding is ambiguous.",
            )
        binding = bindings[0]
        if binding.get("HostIp") != "127.0.0.1":
            raise ToolSandboxError(
                "tool_control_port_publicly_exposed",
                "The SWE-ReX control port is not bound only to loopback.",
            )
        try:
            port = int(binding["HostPort"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ToolSandboxError(
                "tool_runtime_control_channel_unreachable",
                "The SWE-ReX control port is unavailable.",
            ) from exc
        if not 1 <= port <= 65535:
            raise ToolSandboxError(
                "tool_runtime_control_channel_unreachable",
                "The SWE-ReX control port is invalid.",
            )
        return port

    def inspect_active_container(self) -> int | None:
        containers = self.inspect_owned().get("Containers") or {}
        if len(containers) > 1:
            raise ToolSandboxError(
                "tool_control_network_not_internal",
                "Multiple containers joined the invocation tool control network.",
            )
        if not containers:
            return None
        return self.inspect_running_container(next(iter(containers)))

    def cleanup(self) -> tuple[bool, bool]:
        container_cleanup = True
        try:
            payload = self.inspect_owned()
        except ToolSandboxError:
            payload = None
        if payload is not None:
            containers = payload.get("Containers") or {}
            for container_id in containers:
                if not self._container_is_owned(container_id):
                    container_cleanup = False
                    continue
                removed = _docker(
                    self.runner, self.docker, "container", "rm", "--force", container_id
                )
                container_cleanup = container_cleanup and removed.returncode == 0
        network_cleanup = (
            self._remove_network_if_owned() if container_cleanup else False
        )
        if container_cleanup and network_cleanup:
            self.state_path.unlink(missing_ok=True)
        return container_cleanup, network_cleanup

    def _inspect_network(self) -> dict[str, Any]:
        process = _docker(self.runner, self.docker, "network", "inspect", self.name)
        if process.returncode:
            raise ToolSandboxError(
                "tool_control_network_creation_failure",
                "Tool control network is unavailable.",
            )
        try:
            return json.loads(process.stdout)[0]
        except (IndexError, TypeError, json.JSONDecodeError) as exc:
            raise ToolSandboxError(
                "tool_control_network_creation_failure",
                "Tool control network metadata is malformed.",
            ) from exc

    def _container_is_owned(self, container_id: str) -> bool:
        process = _docker(
            self.runner, self.docker, "container", "inspect", container_id
        )
        if process.returncode:
            return True
        try:
            labels = json.loads(process.stdout)[0]["Config"].get("Labels") or {}
        except (IndexError, KeyError, TypeError, json.JSONDecodeError):
            return False
        return labels.get(TOOL_NETWORK_OWNERSHIP_LABEL) == self.ownership_nonce

    def _remove_network_if_owned(self) -> bool:
        process = _docker(self.runner, self.docker, "network", "inspect", self.name)
        if process.returncode:
            return True
        try:
            payload = json.loads(process.stdout)[0]
            labels = payload.get("Labels") or {}
        except (IndexError, TypeError, json.JSONDecodeError):
            return False
        if (
            payload.get("Id") != self.network_id
            or labels.get(NETWORK_SCHEMA_LABEL) != NETWORK_SCHEMA
            or labels.get(TOOL_NETWORK_OWNERSHIP_LABEL) != self.ownership_nonce
        ):
            return False
        removed = _docker(self.runner, self.docker, "network", "rm", self.name)
        if removed.returncode:
            return False
        return (
            _docker(
                self.runner, self.docker, "network", "inspect", self.name
            ).returncode
            != 0
        )


def recover_owned_control_network(
    state_path: Path,
    *,
    runner: Runner = subprocess.run,
    docker: str | None = None,
) -> None:
    """Recover only a network whose persisted identity and Docker labels agree."""
    if not state_path.is_file():
        return
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        network = OwnedControlNetwork(
            docker=docker or shutil.which("docker") or "docker",
            name=state["network_name"],
            network_id=state["network_id"],
            ownership_nonce=state["ownership_nonce"],
            state_path=state_path,
            runner=runner,
        )
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ToolSandboxError(
            "tool_network_cleanup_failure",
            "Persisted tool network recovery state is malformed.",
        ) from exc
    container_cleanup, network_cleanup = network.cleanup()
    if not container_cleanup:
        raise ToolSandboxError(
            "tool_container_cleanup_failure",
            "An interrupted owned tool container could not be removed.",
        )
    if not network_cleanup:
        raise ToolSandboxError(
            "tool_network_cleanup_failure",
            "An interrupted owned tool network could not be removed.",
        )


def recover_stale_control_networks(root: Path) -> None:
    for state_path in sorted(root.glob("invocation-*/private/tool-network-state.json")):
        recover_owned_control_network(state_path)


def deployment_configuration(
    config: SWEAgentProviderConfig, network: OwnedControlNetwork | Any | None = None
) -> dict[str, Any]:
    docker_args = (
        list(network.docker_args)
        if network is not None
        else [
            "--network=<invocation-owned-internal-network>",
            f"--label={TOOL_NETWORK_OWNERSHIP_LABEL}=<ownership-nonce>",
            *TOOL_DOCKER_RESOURCE_ARGS,
        ]
    )
    return {
        "type": "docker",
        "image": config.tool_container_image,
        "docker_args": docker_args,
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
    control_network_factory: Callable[[Path], Any] | None = None,
    lifecycle_root: Path | None = None,
) -> ToolSandboxHealthArtifact:
    """Exercise official SWE-ReX through an owned internal control network."""
    descriptor = image or inspect_tool_image(config)
    policy = tool_network_policy_descriptor(config)
    deployment_identity = sha256_fingerprint(deployment_configuration(config))
    started = time.monotonic()
    observations = {
        "shell": False,
        "workspace": False,
        "credential_forwarding": False,
        "docker_socket": False,
        "model_access": False,
        "direct_external_ip": False,
        "external_hostname": False,
        "pypi": False,
        "allocated_port": 0,
    }
    package_attempt = False
    failure: str | None = None
    public_exposure = False
    deployment: Any | None = None
    network: Any | None = None
    container_cleanup = network_cleanup = False
    temporary: tempfile.TemporaryDirectory[str] | None = None
    if lifecycle_root is None:
        temporary = tempfile.TemporaryDirectory(prefix="cgr-swerex-preflight-")
        lifecycle_root = Path(temporary.name)
    state_path = lifecycle_root / "tool-network-state.json"
    try:
        network = (
            control_network_factory(state_path)
            if control_network_factory is not None
            else OwnedControlNetwork.create(state_path)
        )
        deployment_values = deployment_configuration(config, network)
        if deployment_factory is None:
            from swerex.deployment.config import DockerDeploymentConfig

            deployment = DockerDeploymentConfig.model_validate(
                deployment_values
            ).get_deployment()
        else:
            deployment = deployment_factory(deployment_values)
        observations.update(
            asyncio.run(_exercise_deployment(deployment, network, config))
        )
    except Exception as exc:
        text = str(exc)
        package_attempt = bool(_INSTALL_ATTEMPT.search(text))
        failure = (
            "tool_runtime_control_channel_unreachable"
            if isinstance(exc, TimeoutError)
            else getattr(exc, "code", classify_bootstrap_failure(text))
        )
        public_exposure = (
            getattr(exc, "code", "") == "tool_control_port_publicly_exposed"
        )
    finally:
        if deployment is not None:
            try:
                asyncio.run(deployment.stop())
            except Exception:
                container_cleanup = False
                failure = "tool_container_cleanup_failure"
        if network is not None:
            try:
                container_cleanup, network_cleanup = network.cleanup()
            except Exception:
                container_cleanup = network_cleanup = False
            if not container_cleanup:
                failure = "tool_container_cleanup_failure"
            elif not network_cleanup:
                failure = "tool_network_cleanup_failure"
        if temporary is not None:
            temporary.cleanup()
    cleanup = container_cleanup and network_cleanup
    if observations["direct_external_ip"]:
        failure = "tool_external_egress_detected"
    elif observations["external_hostname"] or observations["pypi"]:
        failure = "tool_external_egress_detected"
    elif observations["model_access"]:
        failure = "tool_model_endpoint_access_detected"
    status = (
        "passed"
        if all(
            (
                observations["shell"],
                observations["workspace"],
                cleanup,
                not observations["credential_forwarding"],
                not observations["docker_socket"],
                not observations["model_access"],
                not observations["direct_external_ip"],
                not observations["external_hostname"],
                not observations["pypi"],
                not package_attempt,
                observations["allocated_port"] > 0,
            )
        )
        else "failed"
    )
    if status == "failed" and failure is None:
        failure = "tool_sandbox_policy_failure"
    values = {
        "tool_image_descriptor_sha256": descriptor.descriptor_sha256,
        "tool_network_policy_descriptor_sha256": policy.descriptor_sha256,
        "deployment_identity_sha256": deployment_identity,
        "control_network_type": "docker_internal",
        "network_identifier_sha256": (
            network.identifier_sha256 if network is not None else "0" * 64
        ),
        "network_ownership_nonce": (
            network.ownership_nonce if network is not None else "0" * 32
        ),
        "network_internal": network is not None,
        "control_bind_address": "127.0.0.1",
        "allocated_control_port": observations["allocated_port"],
        "public_port_exposure_observed": public_exposure,
        "direct_external_ip_reachable": observations["direct_external_ip"],
        "external_hostname_reachable": observations["external_hostname"],
        "pypi_reachable": observations["pypi"],
        "startup_result": status,
        "shell_smoke_passed": observations["shell"],
        "workspace_write_passed": observations["workspace"],
        "cleanup_passed": cleanup,
        "container_cleanup_passed": container_cleanup,
        "network_cleanup_passed": network_cleanup,
        "credential_forwarding_observed": observations["credential_forwarding"],
        "docker_socket_forwarded": observations["docker_socket"],
        "model_endpoint_reachable": observations["model_access"],
        "infrastructure_package_install_attempt_observed": package_attempt,
        "runtime_seconds": time.monotonic() - started,
        "failure_classification": failure,
    }
    return seal_contract(ToolSandboxHealthArtifact, values, "health_artifact_sha256")


async def _exercise_deployment(
    deployment: Any, network: Any, config: SWEAgentProviderConfig
) -> dict[str, bool | int]:
    from swerex.runtime.abstract import Command, UploadRequest

    await deployment.start()
    allocated_port = network.inspect_running_container(deployment.container_name)
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
    docker_socket = await _probe_succeeds(
        runtime, "test -S /var/run/docker.sock", Command
    )
    direct_external_ip = await _probe_succeeds(
        runtime,
        "python3 -c "
        + shlex.quote(
            "import socket; s=socket.create_connection(('1.1.1.1',443),2); s.close()"
        ),
        Command,
    )
    external_hostname = await _probe_succeeds(
        runtime,
        "python3 -c "
        + shlex.quote(
            "import socket; s=socket.create_connection(('example.com',443),2); s.close()"
        ),
        Command,
    )
    pypi = await _probe_succeeds(
        runtime,
        "python3 -c "
        + shlex.quote(
            "import requests; r=requests.get('https://pypi.org/simple/pipx/',timeout=3); "
            "raise SystemExit(0 if r.status_code < 500 else 1)"
        ),
        Command,
    )
    model_probe = (
        "import requests; "
        f"r=requests.get({config.base_url.rstrip('/')!r}+'/models',timeout=2); "
        "data=r.json().get('data',[]); "
        f"raise SystemExit(0 if any(x.get('id') == {config.model_identifier!r} for x in data) else 1)"
    )
    model_access = await _probe_succeeds(
        runtime, "python3 -c " + shlex.quote(model_probe), Command
    )
    return {
        "shell": True,
        "workspace": True,
        "credential_forwarding": credential_forwarding,
        "docker_socket": docker_socket,
        "model_access": model_access,
        "direct_external_ip": direct_external_ip,
        "external_hostname": external_hostname,
        "pypi": pypi,
        "allocated_port": allocated_port,
    }


async def _probe_succeeds(runtime: Any, command: str, command_type: Any) -> bool:
    observation = await runtime.execute(
        command_type(command=command, shell=True, check=False)
    )
    return getattr(observation, "exit_code", 1) == 0


def classify_bootstrap_failure(value: str) -> str:
    lowered = value.lower()
    if _INSTALL_ATTEMPT.search(value) and (
        "name resolution" in lowered
        or "could not find a version" in lowered
        or "/simple/pipx/" in lowered
    ):
        return "offline_dependency_missing"
    if (
        "did not start within timeout" in lowered
        or "timed out" in lowered
        or "timeouterror" in lowered
    ):
        return "tool_runtime_control_channel_unreachable"
    if "container process terminated" in lowered:
        return "tool_container_terminated_during_startup"
    return "tool_sandbox_bootstrap_failure"


def infrastructure_install_attempt_observed(value: str) -> bool:
    return bool(_INSTALL_ATTEMPT.search(value))


def failed_tool_health(
    config: SWEAgentProviderConfig, error: Exception
) -> ToolSandboxHealthArtifact:
    """Create portable fail-closed health evidence when startup fails early."""
    policy = tool_network_policy_descriptor(config)
    values = {
        "tool_image_descriptor_sha256": "0" * 64,
        "tool_network_policy_descriptor_sha256": policy.descriptor_sha256,
        "deployment_identity_sha256": sha256_fingerprint(
            deployment_configuration(config)
        ),
        "control_network_type": "docker_internal",
        "network_identifier_sha256": "0" * 64,
        "network_ownership_nonce": "0" * 32,
        "network_internal": False,
        "control_bind_address": "127.0.0.1",
        "allocated_control_port": 0,
        "public_port_exposure_observed": getattr(error, "code", "")
        == "tool_control_port_publicly_exposed",
        "direct_external_ip_reachable": False,
        "external_hostname_reachable": False,
        "pypi_reachable": False,
        "startup_result": "failed",
        "shell_smoke_passed": False,
        "workspace_write_passed": False,
        "cleanup_passed": True,
        "container_cleanup_passed": True,
        "network_cleanup_passed": True,
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


def _docker(
    runner: Runner, executable: str, *arguments: str
) -> subprocess.CompletedProcess[str]:
    return runner(
        [executable, *arguments],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
