"""Pristine SWE-agent identity and official CLI construction."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from cgr.science import sha256_fingerprint

from .config import SWEAgentProviderConfig
from .contracts import (
    AgentDescriptor,
    ModelEndpointDescriptor,
    ToolSandboxImageDescriptor,
    ToolControlProxyPolicyDescriptor,
    ToolNetworkPolicyDescriptor,
    seal_contract,
)

TOOL_NETWORK_NAME_PLACEHOLDER = "cgr-swerex-invocation-network"
TOOL_NETWORK_NONCE_PLACEHOLDER = "0" * 32
TOOL_NETWORK_OWNERSHIP_LABEL = "org.cgr.swerex-control.owner-nonce"
TOOL_DOCKER_RESOURCE_ARGS = (
    "--cpus=2",
    "--memory=2g",
    "--pids-limit=128",
    "--cap-drop=ALL",
    "--security-opt=no-new-privileges",
)
TOOL_DOCKER_ARGS = (
    f"--network={TOOL_NETWORK_NAME_PLACEHOLDER}",
    f"--label={TOOL_NETWORK_OWNERSHIP_LABEL}={TOOL_NETWORK_NONCE_PLACEHOLDER}",
    *TOOL_DOCKER_RESOURCE_ARGS,
)


def provider_overlay(
    config: SWEAgentProviderConfig,
    *,
    control_network_name: str = TOOL_NETWORK_NAME_PLACEHOLDER,
    network_ownership_nonce: str = TOOL_NETWORK_NONCE_PLACEHOLDER,
    control_port: int = 49152,
) -> str:
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,62}", control_network_name):
        raise ValueError("Tool control network name is unsafe.")
    if not re.fullmatch(r"[0-9a-f]{32}", network_ownership_nonce):
        raise ValueError("Tool control network ownership nonce is malformed.")
    if not 1 <= control_port <= 65535:
        raise ValueError("SWE-ReX control port is invalid.")
    arguments = (
        f"--network={control_network_name}",
        f"--label={TOOL_NETWORK_OWNERSHIP_LABEL}={network_ownership_nonce}",
        *TOOL_DOCKER_RESOURCE_ARGS,
    )
    docker_args = "\n".join(f"      - {json.dumps(item)}" for item in arguments)
    return f"""env:
  deployment:
    type: docker
    image: {json.dumps(config.tool_container_image)}
    port: {control_port}
    pull: never
    startup_timeout: {config.tool_startup_timeout_seconds}
    python_standalone_dir: null
    remove_container: true
    docker_args:
{docker_args}
  post_startup_commands:
    - git config core.fileMode false
    - git diff --quiet --ignore-submodules --
    - git diff --cached --quiet --ignore-submodules --
agent:
  history_processors: []
  templates:
    system_template: |-
      You are a software engineer operating through pristine SWE-agent in an isolated candidate repository.
      Inspect the supplied files, make only the bounded source repair requested, inspect the diff, and submit through the official submit command.
      Never claim authorization, access network services, or seek trusted reference data.
    instance_template: |-
      {{{{problem_statement}}}}
    next_step_template: |-
      OBSERVATION:
      {{{{observation}}}}
    next_step_no_output_template: |-
      Your command completed without output. Continue the bounded source repair.
  tools:
    bundles:
      - path: tools/registry
      - path: tools/search
      - path: tools/windowed
      - path: tools/review_on_submit_m
    enable_bash_tool: true
    parse_function:
      type: function_calling
"""


def tool_network_policy_descriptor(
    config: SWEAgentProviderConfig,
) -> ToolNetworkPolicyDescriptor:
    values = {
        "external_egress_disabled": config.tool_external_egress_disabled,
        "control_network_type": config.tool_control_network_type,
        "control_network_driver": config.tool_control_network_driver,
        "control_bind_address": config.tool_control_bind_address,
        "control_container_port": config.tool_control_container_port,
        "public_port_exposure": config.tool_public_port_exposure,
        "model_endpoint_access": config.tool_model_endpoint_access,
        "invocation_scoped_ownership": True,
    }
    return seal_contract(ToolNetworkPolicyDescriptor, values, "descriptor_sha256")


def tool_control_proxy_policy_descriptor(
    config: SWEAgentProviderConfig,
) -> ToolControlProxyPolicyDescriptor:
    values = {
        "proxy_type": config.tool_control_proxy_type,
        "proxy_bind_address": config.tool_control_proxy_bind_address,
        "proxy_destination_port": config.tool_control_proxy_destination_port,
        "proxy_public_exposure": config.tool_control_proxy_public_exposure,
        "proxy_external_destination": config.tool_control_proxy_external_destination,
        "invocation_scoped_ownership": True,
    }
    return seal_contract(ToolControlProxyPolicyDescriptor, values, "descriptor_sha256")


def verify_pristine_sweagent(
    config: SWEAgentProviderConfig,
    overlay: str | None = None,
    tool_image_descriptor: ToolSandboxImageDescriptor | None = None,
) -> AgentDescriptor:
    source = config.sweagent_source.resolve(strict=True)
    executable = resolve_executable(config.sweagent_executable)
    if (
        not (source / "config/default.yaml").is_file()
        or not (source / "sweagent/__init__.py").is_file()
    ):
        raise ValueError("Configured SWE-agent source is incomplete.")
    commit = _git(source, "rev-parse", "HEAD").strip()
    status = _git(source, "status", "--porcelain=v1", "--untracked-files=all")
    if commit != config.required_sweagent_commit:
        raise ValueError("Pristine SWE-agent commit does not match the reviewed pin.")
    if status.strip():
        raise ValueError(
            "Pristine SWE-agent working tree is dirty or has untracked files."
        )
    overlay_value = provider_overlay(config) if overlay is None else overlay
    configuration_sha = hashlib.sha256(
        (source / "config/default.yaml").read_bytes() + overlay_value.encode("utf-8")
    ).hexdigest()
    executable_sha = hashlib.sha256(executable.read_bytes()).hexdigest()
    from .tool_sandbox import inspect_tool_image

    image = tool_image_descriptor or inspect_tool_image(config)
    network_policy = tool_network_policy_descriptor(config)
    proxy_policy = tool_control_proxy_policy_descriptor(config)
    tool_identity = {
        "image": config.tool_container_image,
        "pull": config.tool_container_pull_policy,
        "docker_args": TOOL_DOCKER_ARGS,
        "credential_forwarding": False,
        "docker_socket_mounted": False,
        "host_home_mounted": False,
        "tool_image_descriptor_sha256": image.descriptor_sha256,
        "tool_network_policy_descriptor_sha256": network_policy.descriptor_sha256,
        "tool_control_proxy_policy_descriptor_sha256": proxy_policy.descriptor_sha256,
    }
    values = {
        "pristine_source_commit": commit,
        "source_tree_clean": True,
        "configuration_sha256": configuration_sha,
        "tool_environment_sha256": sha256_fingerprint(tool_identity),
        "agent_version": config.sweagent_version,
        "patch_output_mechanism": "official-trajectory-prediction",
        "executable_identity_sha256": executable_sha,
        "tool_image_descriptor_sha256": image.descriptor_sha256,
        "tool_network_policy_descriptor_sha256": network_policy.descriptor_sha256,
        "tool_control_proxy_policy_descriptor_sha256": proxy_policy.descriptor_sha256,
    }
    return seal_contract(AgentDescriptor, values, "descriptor_sha256")


def build_official_command(
    *,
    config: SWEAgentProviderConfig,
    endpoint: ModelEndpointDescriptor,
    workspace: Path,
    problem_file: Path,
    output_directory: Path,
    overlay_file: Path,
) -> list[str]:
    executable = resolve_executable(config.sweagent_executable)
    source = config.sweagent_source.resolve(strict=True)
    maximum_output = config.budget.maximum_output_tokens
    maximum_input = min(
        config.budget.maximum_input_tokens,
        endpoint.observed_context_length - maximum_output,
    )
    if maximum_input <= 0:
        raise ValueError("Live model context cannot fit the reserved output budget.")
    return [
        str(executable),
        "run",
        "--config",
        str((source / "config/default.yaml").resolve(strict=True)),
        "--config",
        str(overlay_file.resolve(strict=True)),
        "--output_dir",
        str(output_directory),
        "--problem_statement.path",
        str(problem_file.resolve(strict=True)),
        "--agent.model.name",
        f"openai/{endpoint.requested_model_identifier}",
        "--agent.model.api_base",
        endpoint.base_url_identity,
        "--agent.model.api_key",
        f"${config.api_key_environment_variable}",
        "--agent.model.temperature",
        str(config.sampling.temperature),
        "--agent.model.top_p",
        str(config.sampling.top_p),
        "--agent.model.completion_kwargs",
        json.dumps({"seed": config.sampling.seed}, separators=(",", ":")),
        "--agent.model.per_instance_cost_limit",
        "0",
        "--agent.model.total_cost_limit",
        "0",
        "--agent.model.per_instance_call_limit",
        str(config.budget.maximum_model_calls),
        "--agent.model.max_input_tokens",
        str(maximum_input),
        "--agent.model.max_output_tokens",
        str(maximum_output),
        "--env.repo.path",
        str(workspace.resolve(strict=True)),
    ]


def child_environment(
    config: SWEAgentProviderConfig, private_home: Path, api_key: str
) -> dict[str, str]:
    """Build a minimal host orchestration environment with no cloud credentials."""
    private_home.mkdir(parents=True, exist_ok=True)
    allowed_names = (
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "TMP",
        "TEMP",
    )
    environment = {
        name: os.environ[name] for name in allowed_names if name in os.environ
    }
    environment.update(
        {
            "HOME": str(private_home),
            "USERPROFILE": str(private_home),
            "SWE_AGENT_CONFIG_ROOT": str(config.sweagent_source.resolve(strict=True)),
            "NO_PROXY": "127.0.0.1,localhost,::1",
            "no_proxy": "127.0.0.1,localhost,::1",
            config.api_key_environment_variable: api_key,
        }
    )
    return environment


def resolve_executable(value: str) -> Path:
    discovered = shutil.which(value)
    if discovered is None:
        candidate = Path(value)
        if not candidate.is_file():
            raise ValueError("Official SWE-agent executable is unavailable.")
        discovered = str(candidate)
    return Path(discovered).resolve(strict=True)


def repository_commit(root: Path) -> str:
    commit = _git(root.resolve(strict=True), "rev-parse", "HEAD").strip()
    if len(commit) != 40 or any(item not in "0123456789abcdef" for item in commit):
        raise ValueError("CGR repository commit identity is malformed.")
    return commit


def _git(root: Path, *arguments: str) -> str:
    command = [
        "git",
        "-c",
        f"safe.directory={root.as_posix()}",
        "-C",
        str(root),
        *arguments,
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode:
        raise ValueError("Could not verify pristine SWE-agent source identity.")
    return result.stdout
