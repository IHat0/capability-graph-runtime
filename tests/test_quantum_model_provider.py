from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from cgr.quantum_candidate.contracts import CandidateAdjudicationReceipt
from cgr.quantum_candidate.findings import finding
from cgr.quantum_preflight.manifests import load_manifest
from cgr.quantum_repair.contracts import QuantumRepairPolicy, StructuredEdit
from cgr.quantum_repair.cli import tool_check_main
from cgr.quantum_repair.directives import create_directive
from cgr.quantum_repair.model_acceptance import (
    _summarize,
    _verify_smoke_report,
    load_model_acceptance_manifest,
    run_model_acceptance,
)
from cgr.quantum_repair.model_provider.agent import (
    TOOL_DOCKER_ARGS,
    build_official_command,
    child_environment,
    provider_overlay,
    repository_commit,
    tool_network_policy_descriptor,
    verify_pristine_sweagent,
)
from cgr.quantum_repair.model_provider.config import (
    DEFAULT_MODEL,
    REQUIRED_SWEAGENT_COMMIT,
    SWEAgentProviderConfig,
    load_provider_config,
)
from cgr.quantum_repair.model_provider.contracts import (
    ModelEndpointDescriptor,
    ModelRepairPrompt,
    ProviderBudget,
    ProviderInvocationRequest,
    SamplingParameters,
    ToolSandboxHealthArtifact,
    ToolSandboxImageDescriptor,
    ToolControlProxyLifecycleArtifact,
    ToolControlProxyPolicyDescriptor,
    seal_contract,
)
from cgr.quantum_repair.model_provider.control_proxy import ControlProxyEndpoint
from cgr.quantum_repair.model_provider.endpoint import (
    EndpointPolicyError,
    normalize_loopback_base_url,
    verify_model_endpoint,
)
from cgr.quantum_repair.model_provider.extraction import (
    extract_official_patch,
    redact_trajectory,
)
from cgr.quantum_repair.model_provider.process import run_bounded_process
from cgr.quantum_repair.model_provider.prompting import (
    build_model_prompt,
    render_problem_statement,
)
from cgr.quantum_repair.model_provider.recovery import (
    InvocationStateStore,
    recover_attempt_invocations,
)
from cgr.quantum_repair.model_provider.redaction import (
    RedactionError,
    assert_prompt_safe,
    sanitize_text,
)
from cgr.quantum_repair.model_provider.tool_sandbox import (
    OwnedContainerEndpoint,
    OwnedControlNetwork,
    ToolSandboxError,
    classify_bootstrap_failure,
    failed_tool_health,
    infrastructure_install_attempt_observed,
    inspect_tool_image,
    recover_owned_control_network,
    run_offline_tool_preflight,
)
from cgr.quantum_repair.patches import (
    RepairPatchRejected,
    create_patch,
    validate_and_apply_patch,
)
from cgr.quantum_repair.persistence import create_source_manifest, write_evidence
from cgr.quantum_repair.replay import (
    _verify_provider_network_policy,
    _verify_provider_proxy_lifecycle,
    _verify_provider_proxy_policy,
)
from cgr.science import ArtifactPointer, sha256_fingerprint

ROOT = Path(__file__).parents[1]
PUBLIC = ROOT / "benchmark-manifests/quantum-preflight/lih-ground-state-v1.json"
ACCEPTANCE = (
    ROOT / "benchmark-manifests/quantum-repair/lih-sweagent-qwen-acceptance-v1.json"
)
SWE_SOURCE = ROOT / ".sandbox-sweagent-src"
SWE_EXECUTABLE = ROOT / ".sandbox-sweagent-venv/Scripts/sweagent.exe"


def _tool_image_descriptor() -> ToolSandboxImageDescriptor:
    return seal_contract(
        ToolSandboxImageDescriptor,
        {
            "image_repository": "cgr-quantum-sweagent-tool",
            "image_id": "sha256:" + "a" * 64,
            "build_schema_version": "cgr.quantum-sweagent-tool-image-build/1.1.0",
            "build_input_sha256": "b" * 64,
            "offline_bootstrap": True,
            "network_policy": "docker-internal-loopback-control",
            "required_sweagent_commit": REQUIRED_SWEAGENT_COMMIT,
            "swerex_version": "1.4.0",
            "runtime_identity": "c" * 64,
        },
        "descriptor_sha256",
    )


class _FakeControlNetwork:
    name = "cgr-swerex-test-network"
    ownership_nonce = "d" * 32
    identifier_sha256 = "e" * 64
    docker_args = (
        "--network=cgr-swerex-test-network",
        "--label=org.cgr.swerex-control.owner-nonce=" + "d" * 32,
        "--cpus=2",
    )

    endpoint = OwnedContainerEndpoint(
        "owned-tool-container", "sha256:" + "a" * 64, "172.30.0.2"
    )

    def wait_for_owned_container(
        self, expected_image: str, _timeout: float
    ) -> OwnedContainerEndpoint:
        assert expected_image == "sha256:" + "a" * 64
        return self.endpoint

    def verify_direct_control(self, endpoint: OwnedContainerEndpoint) -> bool:
        assert endpoint == self.endpoint
        return True

    def wait_for_direct_control(
        self, endpoint: OwnedContainerEndpoint, _timeout: float
    ) -> bool:
        return self.verify_direct_control(endpoint)

    def cleanup(self) -> tuple[bool, bool]:
        return self.cleanup_containers(), self.cleanup_network()

    def cleanup_containers(self) -> bool:
        return True

    def cleanup_network(self) -> bool:
        return True


def _fake_control_network(_state_path: Path) -> _FakeControlNetwork:
    return _FakeControlNetwork()


class _FakeProxy:
    def __init__(self, _port: int, _endpoint: ControlProxyEndpoint) -> None:
        self.stopped = False

    def start(self) -> None:
        return None

    def assert_healthy(self) -> None:
        if self.stopped:
            raise RuntimeError("proxy stopped")

    def stop(self) -> bool:
        self.stopped = True
        return True


def _fake_proxy(port: int, endpoint: ControlProxyEndpoint) -> _FakeProxy:
    return _FakeProxy(port, endpoint)


class _DockerNetworkRunner:
    def __init__(
        self,
        *,
        internal: bool = True,
        bind_address: str = "127.0.0.1",
        container_bind_address: str | None = None,
        foreign_container: bool = False,
        extra_network: str | None = None,
        container_image: str = "sha256:" + "a" * 64,
        internal_ip: str = "172.30.0.2",
        multiple_containers: bool = False,
    ) -> None:
        self.internal = internal
        self.bind_address = bind_address
        self.container_bind_address = container_bind_address
        self.foreign_container = foreign_container
        self.extra_network = extra_network
        self.container_image = container_image
        self.internal_ip = internal_ip
        self.multiple_containers = multiple_containers
        self.network_exists = False
        self.container_exists = False
        self.name = ""
        self.nonce = ""
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str], **_kwargs: Any) -> Any:
        self.commands.append(command)
        arguments = command[1:]
        if arguments[:2] == ["network", "create"]:
            self.name = arguments[-1]
            self.nonce = next(
                item.rsplit("=", 1)[-1]
                for item in arguments
                if item.startswith("org.cgr.swerex-control.owner-nonce=")
            )
            self.network_exists = True
            return SimpleNamespace(returncode=0, stdout="network-id\n", stderr="")
        if arguments[:2] == ["network", "inspect"]:
            if not self.network_exists:
                return SimpleNamespace(returncode=1, stdout="", stderr="missing")
            containers = {"container-id": {}} if self.container_exists else {}
            if self.container_exists and self.multiple_containers:
                containers["second-container-id"] = {}
            payload = {
                "Id": "network-id",
                "Name": self.name,
                "Driver": "bridge",
                "Internal": self.internal,
                "Options": {
                    "com.docker.network.bridge.host_binding_ipv4": self.bind_address
                },
                "Labels": {
                    "org.cgr.swerex-control.schema": (
                        "cgr.quantum-swerex-control-network/1.0.0"
                    ),
                    "org.cgr.swerex-control.owner-nonce": self.nonce,
                },
                "Containers": containers,
                "IPAM": {"Config": [{"Subnet": "172.30.0.0/24"}]},
            }
            return SimpleNamespace(
                returncode=0, stdout=json.dumps([payload]), stderr=""
            )
        if arguments[:2] == ["container", "inspect"]:
            if not self.container_exists:
                return SimpleNamespace(returncode=1, stdout="", stderr="missing")
            networks = {self.name: {"IPAddress": self.internal_ip}}
            if self.extra_network is not None:
                networks[self.extra_network] = {}
            label = "foreign" if self.foreign_container else self.nonce
            payload = {
                "Id": "container-id",
                "Image": self.container_image,
                "Config": {"Labels": {"org.cgr.swerex-control.owner-nonce": label}},
                "NetworkSettings": {
                    "Networks": networks,
                    "Ports": {
                        "8000/tcp": (
                            [
                                {
                                    "HostIp": self.container_bind_address,
                                    "HostPort": "49152",
                                }
                            ]
                            if self.container_bind_address is not None
                            else None
                        )
                    },
                },
            }
            return SimpleNamespace(
                returncode=0, stdout=json.dumps([payload]), stderr=""
            )
        if arguments[:3] == ["container", "rm", "--force"]:
            self.container_exists = False
            return SimpleNamespace(returncode=0, stdout="container-id\n", stderr="")
        if arguments[:2] == ["network", "rm"]:
            if self.container_exists:
                return SimpleNamespace(returncode=1, stdout="", stderr="in use")
            self.network_exists = False
            return SimpleNamespace(returncode=0, stdout=self.name + "\n", stderr="")
        raise AssertionError(f"Unexpected Docker command: {command}")


def _receipt(code: str) -> CandidateAdjudicationReceipt:
    values: dict[str, Any] = {
        "candidate_identifier": "model-provider-test",
        "candidate_source_tree_sha256": "a" * 64,
        "input_experiment_sha256": "b" * 64,
        "candidate_image_identifier": "sha256:" + "c" * 64,
        "candidate_dependency_lock_sha256": "d" * 64,
        "sandbox_policy_sha256": "e" * 64,
        "execution_evidence": ArtifactPointer(
            artifact_identifier="candidate_execution", content_sha256="f" * 64
        ),
        "candidate_output_package_sha256": None,
        "candidate_artifacts": (),
        "recomputed_scientific_result_sha256": None,
        "trusted_reference_receipt_sha256": "1" * 64,
        "findings": (finding(code, "Public candidate defect."),),
        "primary_failure_code": code,
        "authorized": False,
        "authorization_policy_sha256": "2" * 64,
    }
    provisional = CandidateAdjudicationReceipt.model_construct(
        **values, receipt_content_sha256="0" * 64
    )
    values["receipt_content_sha256"] = sha256_fingerprint(
        provisional.canonical_identity()
    )
    return CandidateAdjudicationReceipt.model_validate(values)


def _directive(tmp_path: Path, *, config: bool = False) -> tuple[Any, Any, Path]:
    source = tmp_path / "source"
    source.mkdir()
    (source / "main.py").write_text("VALUE = 'bad'\n", encoding="utf-8")
    allowed = ("main.py",)
    if config:
        (source / "repair-config.json").write_text(
            json.dumps(
                {"candidate_identifier": "owned-candidate", "mapper": "parity"},
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        allowed = ("repair-config.json",)
    manifest = create_source_manifest(source, "model-provider-test")
    directive = create_directive(
        task_identifier="model-provider-test",
        repair_run_identifier="repair-run-001",
        attempt_identifier="attempt-000",
        attempt_index=0,
        source_manifest=manifest,
        adjudication=_receipt("candidate_runtime_error"),
        policy=QuantumRepairPolicy(),
        allowed_edit_paths=allowed,
    )
    return directive, manifest, source


class _ModelsServer:
    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        context: int = 65_536,
        redirect: bool = False,
    ) -> None:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if outer.redirect:
                    self.send_response(302)
                    self.send_header("Location", "http://example.com/v1/models")
                    self.end_headers()
                    return
                body = json.dumps(
                    {"data": [{"id": outer.model, "max_model_len": outer.context}]}
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format: str, *_args: Any) -> None:
                return None

        self.model = model
        self.context = context
        self.redirect = redirect
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> str:
        self.thread.start()
        return f"http://127.0.0.1:{self.server.server_port}/v1"

    def __exit__(self, *_: Any) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def _endpoint(base_url: str) -> ModelEndpointDescriptor:
    return verify_model_endpoint(
        base_url=base_url,
        requested_model=DEFAULT_MODEL,
        api_key="provider-secret",
        request_timeout_seconds=2,
        sampling=SamplingParameters(),
        budget=ProviderBudget(),
    )


def test_endpoint_descriptor_is_loopback_self_hashed_and_live() -> None:
    with _ModelsServer() as url:
        endpoint = _endpoint(url)
    assert endpoint.observed_model_identifier == DEFAULT_MODEL
    assert endpoint.observed_context_length == 65_536
    assert endpoint.loopback_only is True
    assert endpoint.descriptor_sha256 == endpoint.fingerprint
    assert "provider-secret" not in endpoint.to_canonical_json()


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/v1",
        "http://192.0.2.1:8000/v1",
        "http://user:secret@127.0.0.1:8000/v1",
        "http://127.0.0.1:8000/v1?api_key=secret",
    ],
)
def test_public_or_secret_bearing_endpoint_is_rejected(url: str) -> None:
    with pytest.raises(EndpointPolicyError):
        normalize_loopback_base_url(url)


def test_wrong_model_redirect_and_unavailable_endpoint_fail_closed() -> None:
    with _ModelsServer(model="wrong/model") as url:
        with pytest.raises(EndpointPolicyError, match="identity"):
            _endpoint(url)
    with _ModelsServer(redirect=True) as url:
        with pytest.raises(EndpointPolicyError, match="redirect"):
            _endpoint(url)
    with pytest.raises(EndpointPolicyError, match="unavailable"):
        _endpoint("http://127.0.0.1:1/v1")


def test_provider_config_references_but_never_persists_api_key(tmp_path: Path) -> None:
    config_path = tmp_path / "provider.json"
    config_path.write_text(
        json.dumps({"api_key_environment_variable": "CGR_REPAIR_MODEL_API_KEY"}),
        encoding="utf-8",
    )
    config = load_provider_config(
        config_path, {"CGR_REPAIR_MODEL_API_KEY": "do-not-persist"}
    )
    assert (
        config.api_key({"CGR_REPAIR_MODEL_API_KEY": "do-not-persist"})
        == "do-not-persist"
    )
    assert "do-not-persist" not in config.model_dump_json()


def test_agent_descriptor_verifies_real_pristine_checkout() -> None:
    config = SWEAgentProviderConfig(
        sweagent_source=SWE_SOURCE, sweagent_executable=str(SWE_EXECUTABLE)
    )
    descriptor = verify_pristine_sweagent(
        config, tool_image_descriptor=_tool_image_descriptor()
    )
    assert descriptor.pristine_source_commit == REQUIRED_SWEAGENT_COMMIT
    assert descriptor.source_tree_clean is True
    assert descriptor.descriptor_sha256 == descriptor.fingerprint
    assert (
        descriptor.tool_image_descriptor_sha256
        == _tool_image_descriptor().descriptor_sha256
    )
    assert "--network=cgr-swerex-invocation-network" in TOOL_DOCKER_ARGS
    assert descriptor.tool_network_policy_descriptor_sha256 is not None


def test_agent_wrong_commit_dirty_tree_and_missing_executable_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "swe"
    (source / "config").mkdir(parents=True)
    (source / "sweagent").mkdir()
    (source / "config/default.yaml").write_text("agent: {}\n", encoding="utf-8")
    (source / "sweagent/__init__.py").write_text("", encoding="utf-8")
    executable = tmp_path / "sweagent"
    executable.write_text("executable", encoding="utf-8")
    config = SWEAgentProviderConfig(
        sweagent_source=source, sweagent_executable=str(executable)
    )
    import cgr.quantum_repair.model_provider.agent as agent_module

    monkeypatch.setattr(agent_module, "_git", lambda _root, *_args: "0" * 40)
    with pytest.raises(ValueError, match="commit"):
        verify_pristine_sweagent(config)
    monkeypatch.setattr(
        agent_module,
        "_git",
        lambda _root, *args: (
            REQUIRED_SWEAGENT_COMMIT if args[0] == "rev-parse" else "?? changed.py\n"
        ),
    )
    with pytest.raises(ValueError, match="dirty"):
        verify_pristine_sweagent(config)
    with pytest.raises(ValueError, match="executable"):
        verify_pristine_sweagent(
            config.model_copy(update={"sweagent_executable": str(tmp_path / "missing")})
        )


def test_official_command_uses_env_key_reference_and_isolated_docker_policy(
    tmp_path: Path,
) -> None:
    config = SWEAgentProviderConfig(
        sweagent_source=SWE_SOURCE, sweagent_executable=str(SWE_EXECUTABLE)
    )
    with _ModelsServer() as url:
        endpoint = _endpoint(url)
    for name in ("workspace", "output"):
        (tmp_path / name).mkdir()
    problem = tmp_path / "problem.md"
    overlay = tmp_path / "overlay.yaml"
    problem.write_text("task", encoding="utf-8")
    overlay.write_text(provider_overlay(config), encoding="utf-8")
    command = build_official_command(
        config=config,
        endpoint=endpoint,
        workspace=tmp_path / "workspace",
        problem_file=problem,
        output_directory=tmp_path / "output",
        overlay_file=overlay,
    )
    assert "provider-secret" not in " ".join(command)
    assert "$CGR_REPAIR_MODEL_API_KEY" in command
    environment = child_environment(config, tmp_path / "home", "provider-secret")
    assert environment["CGR_REPAIR_MODEL_API_KEY"] == "provider-secret"
    assert not any(name.startswith(("AWS_", "IBM_", "GITHUB_")) for name in environment)
    overlay_text = provider_overlay(config)
    assert "--network=cgr-swerex-invocation-network" in overlay_text
    assert "owner-nonce=" in overlay_text
    assert "edit_anthropic" not in overlay_text
    assert "pip install" not in overlay_text


def test_tool_image_requires_immutable_identity() -> None:
    with pytest.raises(ValidationError, match="exact sha256"):
        SWEAgentProviderConfig(tool_container_image="python:3.12")
    with pytest.raises(ValidationError, match="exact sha256"):
        SWEAgentProviderConfig(tool_container_image="cgr-sweagent-tool:latest")


@pytest.mark.parametrize(
    "override",
    [
        {"tool_control_bind_address": "0.0.0.0"},
        {"tool_control_bind_address": "::"},
        {"tool_control_network_type": "bridge"},
        {"tool_control_network_type": "host"},
        {"tool_external_egress_disabled": False},
        {"tool_public_port_exposure": True},
        {"tool_model_endpoint_access": True},
        {"tool_control_proxy_type": "socat"},
        {"tool_control_proxy_bind_address": "0.0.0.0"},
        {"tool_control_proxy_destination_port": 9000},
        {"tool_control_proxy_public_exposure": True},
        {"tool_control_proxy_external_destination": True},
        {
            "tool_image_build_schema_version": (
                "cgr.quantum-sweagent-tool-image-build/1.0.0"
            )
        },
    ],
)
def test_provider_contract_rejects_unsafe_control_network_policy(
    override: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        SWEAgentProviderConfig.model_validate(override)


def test_tool_image_identity_and_build_labels_are_verified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import cgr.quantum_repair.model_provider.tool_sandbox as tool_module

    image_id = "sha256:" + "a" * 64
    config = SWEAgentProviderConfig(
        tool_container_image=image_id,
        tool_image_build_input_sha256="b" * 64,
    )
    labels = {
        "org.cgr.tool-sandbox.schema": "cgr.quantum-sweagent-tool-image/1.1.0",
        "org.cgr.tool-sandbox.build-input-sha256": "b" * 64,
        "org.cgr.tool-sandbox.sweagent-commit": REQUIRED_SWEAGENT_COMMIT,
        "org.cgr.tool-sandbox.swerex-version": "1.4.0",
        "org.cgr.tool-sandbox.offline-bootstrap": "true",
        "org.cgr.tool-sandbox.external-egress-disabled": "true",
        "org.cgr.tool-sandbox.control-network": "docker-internal",
        "org.cgr.tool-sandbox.control-bind-address": "127.0.0.1",
    }
    monkeypatch.setattr(tool_module.shutil, "which", lambda _name: "docker")

    def runner(*_args: Any, **_kwargs: Any) -> Any:
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps([{"Id": image_id, "Config": {"Labels": labels}}]),
        )

    descriptor = inspect_tool_image(config, runner=runner)
    assert descriptor.image_id == image_id
    assert descriptor.network_policy == "docker-internal-loopback-control"

    def mismatch(*_args: Any, **_kwargs: Any) -> Any:
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                [{"Id": "sha256:" + "f" * 64, "Config": {"Labels": labels}}]
            ),
        )

    with pytest.raises(ToolSandboxError, match="substituted"):
        inspect_tool_image(config, runner=mismatch)


def test_owned_internal_network_creation_and_loopback_control_binding(
    tmp_path: Path,
) -> None:
    runner = _DockerNetworkRunner()
    network = OwnedControlNetwork.create(
        tmp_path / "network-state.json", runner=runner, docker="docker"
    )
    create = runner.commands[0]
    assert "--internal" in create
    assert "com.docker.network.bridge.host_binding_ipv4=127.0.0.1" in create
    assert network.docker_args[0] == f"--network={network.name}"
    assert all(
        value not in {"--network=bridge", "--network=host"}
        for value in network.docker_args
    )
    runner.container_exists = True
    endpoint = network.inspect_owned_container("container-id", "sha256:" + "a" * 64)
    assert endpoint.internal_ipv4 == "172.30.0.2"
    container_cleanup, network_cleanup = network.cleanup()
    assert container_cleanup is True and network_cleanup is True
    assert runner.container_exists is False and runner.network_exists is False


@pytest.mark.parametrize(
    ("internal", "bind_address", "classification"),
    [
        (False, "127.0.0.1", "tool_control_network_not_internal"),
        (True, "0.0.0.0", "tool_control_port_publicly_exposed"),
        (True, "::", "tool_control_port_publicly_exposed"),
    ],
)
def test_non_internal_or_public_control_network_is_rejected(
    tmp_path: Path,
    internal: bool,
    bind_address: str,
    classification: str,
) -> None:
    runner = _DockerNetworkRunner(internal=internal, bind_address=bind_address)
    with pytest.raises(ToolSandboxError) as raised:
        OwnedControlNetwork.create(
            tmp_path / "network-state.json", runner=runner, docker="docker"
        )
    assert raised.value.code == classification
    assert runner.network_exists is False


@pytest.mark.parametrize(
    ("bind_address", "extra_network", "classification"),
    [
        ("0.0.0.0", None, "tool_control_port_publicly_exposed"),
        ("::", None, "tool_control_port_publicly_exposed"),
        (None, "bridge", "tool_control_proxy_destination_invalid"),
        (None, "host", "tool_control_proxy_destination_invalid"),
    ],
)
def test_public_binding_default_bridge_and_host_network_are_rejected(
    tmp_path: Path,
    bind_address: str | None,
    extra_network: str | None,
    classification: str,
) -> None:
    runner = _DockerNetworkRunner(
        container_bind_address=bind_address, extra_network=extra_network
    )
    network = OwnedControlNetwork.create(
        tmp_path / "network-state.json", runner=runner, docker="docker"
    )
    runner.container_exists = True
    with pytest.raises(ToolSandboxError) as raised:
        network.inspect_owned_container("container-id", "sha256:" + "a" * 64)
    assert raised.value.code == classification
    network.cleanup()


def test_foreign_container_and_substituted_network_are_never_removed(
    tmp_path: Path,
) -> None:
    runner = _DockerNetworkRunner(foreign_container=True)
    network = OwnedControlNetwork.create(
        tmp_path / "network-state.json", runner=runner, docker="docker"
    )
    runner.container_exists = True
    container_cleanup, network_cleanup = network.cleanup()
    assert container_cleanup is False and network_cleanup is False
    assert runner.container_exists is True and runner.network_exists is True

    runner.container_exists = False
    runner.nonce = "f" * 32
    assert network.cleanup() == (True, False)
    assert runner.network_exists is True


@pytest.mark.parametrize(
    "runner",
    [
        _DockerNetworkRunner(container_image="sha256:" + "f" * 64),
        _DockerNetworkRunner(foreign_container=True),
        _DockerNetworkRunner(internal_ip="192.0.2.10"),
    ],
)
def test_proxy_destination_identity_and_owned_subnet_are_enforced(
    tmp_path: Path, runner: _DockerNetworkRunner
) -> None:
    network = OwnedControlNetwork.create(
        tmp_path / "network-state.json", runner=runner, docker="docker"
    )
    runner.container_exists = True
    with pytest.raises(ToolSandboxError) as raised:
        network.discover_owned_container("sha256:" + "a" * 64)
    assert raised.value.code == "tool_control_proxy_destination_invalid"
    runner.container_exists = False
    network.cleanup()


def test_owned_container_discovery_rejects_ambiguity(tmp_path: Path) -> None:
    runner = _DockerNetworkRunner(multiple_containers=True)
    network = OwnedControlNetwork.create(
        tmp_path / "network-state.json", runner=runner, docker="docker"
    )
    runner.container_exists = True
    with pytest.raises(ToolSandboxError) as raised:
        network.discover_owned_container("sha256:" + "a" * 64)
    assert raised.value.code == "tool_control_proxy_destination_invalid"
    runner.container_exists = False
    network.cleanup()


def test_owned_container_discovery_race_succeeds_and_timeout_is_bounded(
    tmp_path: Path,
) -> None:
    runner = _DockerNetworkRunner()
    network = OwnedControlNetwork.create(
        tmp_path / "network-state.json", runner=runner, docker="docker"
    )

    def attach() -> None:
        threading.Event().wait(0.03)
        runner.container_exists = True

    worker = threading.Thread(target=attach)
    worker.start()
    endpoint = network.wait_for_owned_container("sha256:" + "a" * 64, timeout_seconds=1)
    worker.join()
    assert endpoint.container_identity == "container-id"
    runner.container_exists = False
    with pytest.raises(ToolSandboxError) as raised:
        network.wait_for_owned_container(
            "sha256:" + "a" * 64, timeout_seconds=0.02, poll_seconds=0.005
        )
    assert raised.value.code == "tool_runtime_control_channel_unreachable"
    network.cleanup()


def test_replay_verifies_proxy_policy_and_lifecycle_cross_links(tmp_path: Path) -> None:
    policy = seal_contract(
        ToolControlProxyPolicyDescriptor,
        {
            "proxy_type": "provider_owned_tcp",
            "proxy_bind_address": "127.0.0.1",
            "proxy_destination_port": 8000,
            "proxy_public_exposure": False,
            "proxy_external_destination": False,
            "invocation_scoped_ownership": True,
        },
        "descriptor_sha256",
    )
    lifecycle = seal_contract(
        ToolControlProxyLifecycleArtifact,
        {
            "proxy_policy_descriptor_sha256": policy.descriptor_sha256,
            "proxy_bind_identity_sha256": "a" * 64,
            "proxy_bind_address": "127.0.0.1",
            "proxy_source_port": 49152,
            "proxy_destination_container_identity": "owned-container",
            "proxy_destination_image_identity": "sha256:" + "b" * 64,
            "proxy_destination_internal_ip_identity": "c" * 64,
            "proxy_destination_network_identity_sha256": "d" * 64,
            "startup_result": "passed",
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
            "runtime_seconds": 1.0,
            "failure_classification": None,
        },
        "lifecycle_artifact_sha256",
    )
    write_evidence(tmp_path / "tool-control-proxy-policy.json", policy)
    write_evidence(tmp_path / "tool-control-proxy-lifecycle.json", lifecycle)
    observed = _verify_provider_proxy_policy(tmp_path, policy.descriptor_sha256)
    assert observed == policy.descriptor_sha256
    _verify_provider_proxy_lifecycle(tmp_path, observed)
    with pytest.raises(ValueError, match="substituted"):
        _verify_provider_proxy_policy(tmp_path, "f" * 64)
    contradictory = lifecycle.model_dump(mode="json")
    contradictory["failure_classification"] = "tool_runtime_shutdown_failure"
    contradictory.pop("lifecycle_artifact_sha256")
    provisional = ToolControlProxyLifecycleArtifact.model_construct(
        **contradictory, lifecycle_artifact_sha256="0" * 64
    )
    contradictory["lifecycle_artifact_sha256"] = sha256_fingerprint(
        provisional.canonical_identity()
    )
    (tmp_path / "tool-control-proxy-lifecycle.json").write_text(
        json.dumps(contradictory), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="incomplete|cannot pass"):
        _verify_provider_proxy_lifecycle(tmp_path, policy.descriptor_sha256)


def test_tool_health_rejects_failure_pass_and_cleanup_contradictions() -> None:
    failed = failed_tool_health(
        SWEAgentProviderConfig(),
        ToolSandboxError("tool_runtime_shutdown_failure", "sanitized"),
    )

    def validate_changed(**changes: Any) -> None:
        values = failed.model_dump(mode="json")
        values.update(changes)
        values.pop("health_artifact_sha256")
        provisional = ToolSandboxHealthArtifact.model_construct(
            **values, health_artifact_sha256="0" * 64
        )
        values["health_artifact_sha256"] = sha256_fingerprint(
            provisional.canonical_identity()
        )
        ToolSandboxHealthArtifact.model_validate(values)

    with pytest.raises(ValueError, match="cannot pass"):
        validate_changed(startup_result="passed")
    with pytest.raises(ValueError, match="derived consistently"):
        validate_changed(cleanup_passed=False)


def test_tool_check_cli_exits_nonzero_for_every_classified_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import cgr.quantum_repair.model_provider.config as config_module
    import cgr.quantum_repair.model_provider.tool_sandbox as tool_module

    config = SWEAgentProviderConfig()
    health = failed_tool_health(
        config, ToolSandboxError("tool_runtime_shutdown_failure", "sanitized")
    )
    monkeypatch.setattr(config_module, "load_provider_config", lambda _path: config)
    monkeypatch.setattr(
        tool_module, "inspect_tool_image", lambda _config: _tool_image_descriptor()
    )
    monkeypatch.setattr(
        tool_module,
        "run_offline_tool_preflight",
        lambda *_args, **_kwargs: health,
    )
    monkeypatch.setattr(
        tool_module,
        "verify_control_proxy_lifecycle_evidence",
        lambda *_args, **_kwargs: None,
    )
    config_path = tmp_path / "provider-config.json"
    config_path.write_text("{}", encoding="utf-8")
    status = tool_check_main(
        [
            "--provider-config",
            str(config_path),
            "--evidence-root",
            str(tmp_path / "evidence"),
        ]
    )
    assert status != 0


def test_interrupted_owned_network_recovery_removes_only_matching_resources(
    tmp_path: Path,
) -> None:
    runner = _DockerNetworkRunner()
    state_path = tmp_path / "network-state.json"
    network = OwnedControlNetwork.create(state_path, runner=runner, docker="docker")
    runner.container_exists = True
    recover_owned_control_network(state_path, runner=runner, docker="docker")
    assert not state_path.exists()
    assert runner.container_exists is False and runner.network_exists is False
    assert network.ownership_nonce


def test_exact_offline_deployment_preflight_and_cleanup() -> None:
    class Runtime:
        async def upload(self, _request: Any) -> None:
            return None

        async def execute(self, command: Any) -> Any:
            if command.command == "env":
                return SimpleNamespace(stdout="PATH=/usr/bin", exit_code=0)
            if (
                "docker.sock" in command.command
                or "1.1.1.1" in command.command
                or "example.com" in command.command
                or "pypi.org" in command.command
                or "/models" in command.command
            ):
                return SimpleNamespace(stdout="", exit_code=1)
            return SimpleNamespace(stdout="ok", exit_code=0)

    class Deployment:
        def __init__(self) -> None:
            self.runtime = Runtime()
            self.stopped = False
            self.container_name = "owned-tool-container"

        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            self.stopped = True

    deployment = Deployment()
    health = run_offline_tool_preflight(
        SWEAgentProviderConfig(),
        image=_tool_image_descriptor(),
        deployment_factory=lambda values: (
            deployment
            if "--network=cgr-swerex-test-network" in values["docker_args"]
            else pytest.fail("internal control network missing")
        ),
        control_network_factory=_fake_control_network,
        proxy_factory=_fake_proxy,
        control_port_selector=lambda: 49152,
    )
    assert health.startup_result == "passed"
    assert health.cleanup_passed is True
    assert deployment.stopped is True
    assert health.credential_forwarding_observed is False
    assert health.docker_socket_forwarded is False
    assert health.model_endpoint_reachable is False
    assert health.network_internal is True
    assert health.control_bind_address == "127.0.0.1"
    assert health.allocated_control_port == 49152
    assert health.network_cleanup_passed is True


@pytest.mark.parametrize("fail_after_readiness", [False, True])
def test_proxy_container_network_cleanup_order_is_stable(
    fail_after_readiness: bool,
) -> None:
    events: list[str] = []
    proxy_holder: list[Proxy] = []
    deployment_stop_calls = 0

    class Network(_FakeControlNetwork):
        def cleanup_containers(self) -> bool:
            events.append("container")
            return True

        def cleanup_network(self) -> bool:
            events.append("network")
            return True

    class Proxy(_FakeProxy):
        def stop(self) -> bool:
            events.append("proxy")
            return super().stop()

    class Runtime:
        async def upload(self, _request: Any) -> None:
            return None

        async def execute(self, command: Any) -> Any:
            if fail_after_readiness and command.command == "printf cgr-shell-ready":
                raise RuntimeError("controlled post-readiness failure")
            if command.command == "env":
                return SimpleNamespace(stdout="PATH=/usr/bin", exit_code=0)
            probes = ("docker.sock", "1.1.1.1", "example.com", "pypi.org", "/models")
            return SimpleNamespace(
                stdout="ok",
                exit_code=1 if any(item in command.command for item in probes) else 0,
            )

    class Deployment:
        runtime = Runtime()

        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            nonlocal deployment_stop_calls
            deployment_stop_calls += 1
            proxy_holder[0].assert_healthy()
            events.append("runtime-close")
            proxy_holder[0].assert_healthy()
            events.append("deployment-stop")

    def proxy_factory(port: int, endpoint: ControlProxyEndpoint) -> Proxy:
        proxy = Proxy(port, endpoint)
        proxy_holder.append(proxy)
        return proxy

    health = run_offline_tool_preflight(
        SWEAgentProviderConfig(),
        image=_tool_image_descriptor(),
        deployment_factory=lambda _values: Deployment(),
        control_network_factory=lambda _path: Network(),
        proxy_factory=proxy_factory,
        control_port_selector=lambda: 49152,
    )
    assert events == [
        "runtime-close",
        "deployment-stop",
        "container",
        "proxy",
        "network",
    ]
    assert deployment_stop_calls == 1
    assert health.startup_result == ("failed" if fail_after_readiness else "passed")


def test_runtime_close_refusal_is_fail_closed_after_successful_fallback() -> None:
    events: list[str] = []

    class Network(_FakeControlNetwork):
        def cleanup_containers(self) -> bool:
            events.append("fallback-container")
            return True

        def cleanup_network(self) -> bool:
            events.append("fallback-network")
            return True

    class Proxy(_FakeProxy):
        def stop(self) -> bool:
            events.append("fallback-proxy")
            return super().stop()

    class Runtime:
        async def upload(self, _request: Any) -> None:
            return None

        async def execute(self, command: Any) -> Any:
            if command.command == "env":
                return SimpleNamespace(stdout="PATH=/usr/bin", exit_code=0)
            probes = ("docker.sock", "1.1.1.1", "example.com", "pypi.org", "/models")
            return SimpleNamespace(
                stdout="ok",
                exit_code=1 if any(item in command.command for item in probes) else 0,
            )

    class Deployment:
        runtime = Runtime()
        stop_calls = 0

        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            self.stop_calls += 1
            events.append("official-stop")
            raise ConnectionRefusedError(
                "Cannot connect to host 127.0.0.1:49152 Connection refused"
            )

    deployment = Deployment()
    health = run_offline_tool_preflight(
        SWEAgentProviderConfig(),
        image=_tool_image_descriptor(),
        deployment_factory=lambda _values: deployment,
        control_network_factory=lambda _path: Network(),
        proxy_factory=lambda port, endpoint: Proxy(port, endpoint),
        control_port_selector=lambda: 49152,
    )
    assert events == [
        "official-stop",
        "fallback-container",
        "fallback-proxy",
        "fallback-network",
    ]
    assert deployment.stop_calls == 1
    assert health.failure_classification == "tool_runtime_shutdown_failure"
    assert health.startup_result == "failed"
    assert health.preflight_passed is False
    assert health.official_deployment_stop_passed is False
    assert health.fallback_cleanup_required is True
    assert health.fallback_proxy_cleanup_passed is True
    assert health.fallback_container_cleanup_passed is True
    assert health.fallback_network_cleanup_passed is True
    assert health.cleanup_passed is True


def test_proxy_exit_during_official_stop_is_detected_and_never_passes() -> None:
    proxy_holder: list[_FakeProxy] = []

    class Runtime:
        async def upload(self, _request: Any) -> None:
            return None

        async def execute(self, command: Any) -> Any:
            if command.command == "env":
                return SimpleNamespace(stdout="PATH=/usr/bin", exit_code=0)
            probes = ("docker.sock", "1.1.1.1", "example.com", "pypi.org", "/models")
            return SimpleNamespace(
                stdout="ok",
                exit_code=1 if any(item in command.command for item in probes) else 0,
            )

    class Deployment:
        runtime = Runtime()
        stop_calls = 0

        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            self.stop_calls += 1
            proxy_holder[0].stop()

    def proxy_factory(port: int, endpoint: ControlProxyEndpoint) -> _FakeProxy:
        proxy = _FakeProxy(port, endpoint)
        proxy_holder.append(proxy)
        return proxy

    deployment = Deployment()
    health = run_offline_tool_preflight(
        SWEAgentProviderConfig(),
        image=_tool_image_descriptor(),
        deployment_factory=lambda _values: deployment,
        control_network_factory=_fake_control_network,
        proxy_factory=proxy_factory,
        control_port_selector=lambda: 49152,
    )
    assert deployment.stop_calls == 1
    assert health.failure_classification == "tool_control_proxy_premature_shutdown"
    assert health.preflight_passed is False
    assert health.official_deployment_stop_passed is True
    assert health.proxy_cleanup_passed is True


def test_pinned_deployment_stop_is_idempotent_after_runtime_close() -> None:
    from swerex.deployment.config import DockerDeploymentConfig

    class Runtime:
        close_calls = 0

        async def close(self) -> None:
            self.close_calls += 1

    deployment = DockerDeploymentConfig(
        image="sha256:" + "a" * 64,
        python_standalone_dir=None,
        remove_container=True,
    ).get_deployment()
    runtime = Runtime()
    deployment._runtime = runtime
    asyncio.run(deployment.stop())
    asyncio.run(deployment.stop())
    assert runtime.close_calls == 1


def test_offline_dependency_and_container_startup_failures_are_precise() -> None:
    class FailedDeployment:
        runtime = None
        stopped = False

        async def start(self) -> None:
            raise RuntimeError(
                "Container process terminated: /simple/pipx/ temporary name resolution"
            )

        async def stop(self) -> None:
            self.stopped = True

    deployment = FailedDeployment()
    health = run_offline_tool_preflight(
        SWEAgentProviderConfig(),
        image=_tool_image_descriptor(),
        deployment_factory=lambda _values: deployment,
        control_network_factory=_fake_control_network,
        proxy_factory=_fake_proxy,
        control_port_selector=lambda: 49152,
    )
    assert health.startup_result == "failed"
    assert health.failure_classification == "offline_dependency_missing"
    assert health.infrastructure_package_install_attempt_observed is True
    assert health.cleanup_passed is True
    assert classify_bootstrap_failure("Container process terminated") == (
        "tool_container_terminated_during_startup"
    )
    assert classify_bootstrap_failure("Runtime did not start within timeout") == (
        "tool_runtime_control_channel_unreachable"
    )


@pytest.mark.parametrize(
    ("successful_probe", "classification"),
    [
        ("1.1.1.1", "tool_external_egress_detected"),
        ("example.com", "tool_external_egress_detected"),
        ("pypi.org", "tool_external_egress_detected"),
        ("/models", "tool_model_endpoint_access_detected"),
    ],
)
def test_external_egress_and_model_endpoint_access_fail_preflight(
    successful_probe: str, classification: str
) -> None:
    class Runtime:
        async def upload(self, _request: Any) -> None:
            return None

        async def execute(self, command: Any) -> Any:
            if command.command == "env":
                return SimpleNamespace(stdout="PATH=/usr/bin", exit_code=0)
            probes = ("docker.sock", "1.1.1.1", "example.com", "pypi.org", "/models")
            if any(probe in command.command for probe in probes):
                return SimpleNamespace(
                    stdout="", exit_code=0 if successful_probe in command.command else 1
                )
            return SimpleNamespace(stdout="ok", exit_code=0)

    class Deployment:
        runtime = Runtime()
        container_name = "owned-tool-container"

        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            return None

    health = run_offline_tool_preflight(
        SWEAgentProviderConfig(),
        image=_tool_image_descriptor(),
        deployment_factory=lambda _values: Deployment(),
        control_network_factory=_fake_control_network,
        proxy_factory=_fake_proxy,
        control_port_selector=lambda: 49152,
    )
    assert health.startup_result == "failed"
    assert health.failure_classification == classification


@pytest.mark.parametrize(
    "cleanup",
    [
        (False, False),
        (True, False),
    ],
)
def test_cleanup_failures_are_precisely_classified(
    cleanup: tuple[bool, bool],
) -> None:
    class Network(_FakeControlNetwork):
        def cleanup_containers(self) -> bool:
            return cleanup[0]

        def cleanup_network(self) -> bool:
            return cleanup[1]

    class FailedDeployment:
        runtime = None

        async def start(self) -> None:
            raise TimeoutError("runtime did not start within timeout")

        async def stop(self) -> None:
            return None

    health = run_offline_tool_preflight(
        SWEAgentProviderConfig(),
        image=_tool_image_descriptor(),
        deployment_factory=lambda _values: FailedDeployment(),
        control_network_factory=lambda _path: Network(),
        proxy_factory=_fake_proxy,
        control_port_selector=lambda: 49152,
    )
    assert health.failure_classification == "tool_runtime_control_channel_unreachable"
    assert health.container_cleanup_passed is cleanup[0]
    assert health.network_cleanup_passed is cleanup[1]
    assert health.startup_result == "failed"


@pytest.mark.parametrize(
    "command",
    [
        "pip install x",
        "pipx install x",
        "apt-get install x",
        "apt install x",
        "apk add x",
        "dnf install x",
        "yum install x",
        "conda install x",
    ],
)
def test_runtime_infrastructure_install_attempt_detection(command: str) -> None:
    assert infrastructure_install_attempt_observed(command) is True


@pytest.mark.parametrize("mode", ["baseline", "cgr"])
def test_prompt_contract_is_deterministic_sanitized_and_mode_separated(
    tmp_path: Path, mode: str
) -> None:
    directive, manifest, source = _directive(tmp_path)
    public_task = load_manifest(PUBLIC).experiment.model_dump(mode="json")
    prompt = build_model_prompt(
        directive=directive,
        source_root=source,
        source_manifest=manifest,
        public_task=public_task,
        guidance_mode=mode,
        budget=ProviderBudget(),
        context_maximum_bytes=100_000,
        observed_context_length=65_536,
        secrets=("provider-secret",),
    )
    rendered = render_problem_statement(prompt)
    assert prompt.prompt_sha256 == prompt.fingerprint
    assert "provider-secret" not in rendered
    assert "trusted_exact_energy" not in rendered
    if mode == "baseline":
        assert prompt.primary_finding_code is None
        assert "candidate_runtime_error" not in rendered
    else:
        assert prompt.primary_finding_code == "candidate_runtime_error"
    assert_prompt_safe(rendered, ("provider-secret",))


def test_prompt_tampering_leakage_and_context_overflow_fail(tmp_path: Path) -> None:
    directive, manifest, source = _directive(tmp_path)
    public_task = load_manifest(PUBLIC).experiment.model_dump(mode="json")
    with pytest.raises(ValueError, match="context"):
        build_model_prompt(
            directive=directive,
            source_root=source,
            source_manifest=manifest,
            public_task=public_task,
            guidance_mode="cgr",
            budget=ProviderBudget(),
            context_maximum_bytes=1,
            observed_context_length=65_536,
        )
    with pytest.raises(RedactionError):
        assert_prompt_safe("trusted exact energy = -7.862128")
    prompt_values = {
        "prompt_version": "v1",
        "guidance_mode": "baseline",
        "public_task_identity": "a" * 64,
        "public_task": {},
        "source_manifest_sha256": "b" * 64,
        "source_context_policy": "complete",
        "source_context_sha256": "c" * 64,
        "source_files": (),
        "primary_finding_code": "candidate_runtime_error",
        "additional_finding_codes": (),
        "sanitized_guidance": (),
        "required_invariants": (),
        "allowed_paths": (),
        "prohibited_paths": (),
        "maximum_files_changed": 1,
        "maximum_changed_lines": 1,
        "maximum_patch_bytes": 1,
        "attempt_number": 0,
        "remaining_attempt_budget": 1,
        "previous_patch_identities": (),
        "previous_public_failure_categories": (),
        "instructions": ("repair",),
    }
    with pytest.raises(ValidationError, match="Baseline"):
        seal_contract(ModelRepairPrompt, prompt_values, "prompt_sha256")


def test_official_unified_diff_extracts_structured_patch(tmp_path: Path) -> None:
    directive, manifest, source = _directive(tmp_path)
    output = tmp_path / "official"
    output.mkdir()
    (output / "prediction.patch").write_text(
        "diff --git a/main.py b/main.py\n"
        "--- a/main.py\n+++ b/main.py\n@@ -1 +1 @@\n-VALUE = 'bad'\n+VALUE = 'good'\n",
        encoding="utf-8",
    )
    patch, prediction_sha, prediction = extract_official_patch(
        output_directory=output,
        source_root=source,
        source_manifest=manifest,
        directive=directive,
        provider_identifier="sweagent-openai-compatible",
        provider_version="1.0.0",
        budget=ProviderBudget(),
        extraction_root=tmp_path / "extract",
        patch_identifier="model-patch-000",
    )
    assert patch.provider_type == "swe_agent"
    assert patch.edits[0].new_text == "VALUE = 'good'\n"
    assert prediction == output / "prediction.patch"
    assert len(prediction_sha) == 64


@pytest.mark.parametrize(
    "prediction",
    [
        "I fixed the candidate successfully.",
        "diff --git a/../main.py b/../main.py\n--- a/../main.py\n+++ b/../main.py\n",
        "diff --git a/main.py b/main.py\nGIT binary patch\n",
        "diff --git a/main.py b/main.py\n--- a/main.py\n+++ b/main.py\n@@ -9 +9 @@\n-x\n+y\n",
    ],
)
def test_empty_malformed_traversal_binary_and_stale_predictions_fail(
    tmp_path: Path, prediction: str
) -> None:
    directive, manifest, source = _directive(tmp_path)
    output = tmp_path / "official"
    output.mkdir()
    (output / "prediction.patch").write_text(prediction, encoding="utf-8")
    with pytest.raises(ValueError):
        extract_official_patch(
            output_directory=output,
            source_root=source,
            source_manifest=manifest,
            directive=directive,
            provider_identifier="sweagent-openai-compatible",
            provider_version="1.0.0",
            budget=ProviderBudget(),
            extraction_root=tmp_path / "extract",
            patch_identifier="model-patch-000",
        )


def test_model_patch_candidate_identity_is_revalidated(tmp_path: Path) -> None:
    directive, manifest, source = _directive(tmp_path, config=True)
    old = (source / "repair-config.json").read_text(encoding="utf-8")
    new = old.replace("owned-candidate", "valid-control")
    patch = create_patch(
        patch_identifier="model-patch-000",
        directive=directive,
        source_manifest=manifest,
        provider_identifier="sweagent-openai-compatible",
        provider_version="1.0.0",
        provider_type="swe_agent",
        edits=(
            StructuredEdit(
                relative_path="repair-config.json", old_text=old, new_text=new
            ),
        ),
        rationale="Official model prediction.",
        claimed_addressed_findings=(directive.primary_finding_code,),
    )
    with pytest.raises(RepairPatchRejected) as error:
        validate_and_apply_patch(
            source_root=source,
            destination_root=tmp_path / "repaired",
            source_manifest=manifest,
            directive=directive,
            patch=patch,
            policy=QuantumRepairPolicy(),
        )
    assert error.value.code in {"valid_control_shortcut", "candidate_identity_edit"}


def test_trajectory_redaction_removes_keys_headers_and_host_paths(
    tmp_path: Path,
) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    secret = "top-secret-key"
    payload = {
        "usage": {"prompt_tokens": 10, "completion_tokens": 4},
        "action": "read_file",
        "message": f"Authorization: Bearer {secret} C:\\Users\\person\\repo /home/user/repo",
    }
    prediction = raw / "run.pred"
    prediction.write_text(json.dumps({"patch": "diff --git a/a b/a"}), encoding="utf-8")
    (raw / "run.traj").write_text(json.dumps(payload), encoding="utf-8")
    manifest = redact_trajectory(
        invocation_identifier="provider-invocation-000",
        raw_root=raw,
        portable_root=tmp_path / "portable",
        prediction_path=prediction,
        secrets=(secret,),
    )
    combined = "".join(
        path.read_text(encoding="utf-8")
        for path in (tmp_path / "portable").rglob("*")
        if path.is_file()
    )
    assert secret not in combined
    assert "C:\\Users" not in combined and "/home/user" not in combined
    assert manifest.input_tokens == 10 and manifest.output_tokens == 4
    assert manifest.tool_call_count == 1


@pytest.mark.parametrize(
    "target",
    [
        "created",
        "request_persisted",
        "launching",
        "running",
        "response_persisted",
        "patch_extracted",
    ],
)
def test_crash_boundaries_resume_with_new_invocation(
    tmp_path: Path, target: str
) -> None:
    class InjectedCrash(RuntimeError):
        pass

    def crash(status: str) -> None:
        if status == target:
            raise InjectedCrash(status)

    directory = tmp_path / "invocations/invocation-000"
    store: InvocationStateStore | None = None
    try:
        store = InvocationStateStore(
            directory,
            "provider-invocation-000",
            lease_seconds=30,
            crash_injector=crash,
        )
        sequence = [
            "request_persisted",
            "launching",
            "running",
            "response_persisted",
            "patch_extracted",
        ]
        for status in sequence:
            store.transition(status)  # type: ignore[arg-type]
    except InjectedCrash:
        pass
    state_path = directory / "invocation-state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["status"] == target
    state["lease_expires_unix_seconds"] = 0.0
    state_path.write_text(json.dumps(state), encoding="utf-8")
    patch, next_sequence, interrupted = recover_attempt_invocations(
        tmp_path / "invocations",
        directive_sha256="a" * 64,
        source_manifest_sha256="b" * 64,
    )
    assert patch is None
    assert next_sequence == 1
    assert interrupted == ("invocation-000",)


def test_active_lease_and_illegal_transition_prevent_duplicate_invocation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "invocations"
    store = InvocationStateStore(
        root / "invocation-000", "provider-invocation-000", lease_seconds=30
    )
    store.transition("request_persisted")
    with pytest.raises(ValueError, match="active lease"):
        recover_attempt_invocations(
            root,
            directive_sha256="a" * 64,
            source_manifest_sha256="b" * 64,
        )
    with pytest.raises(ValueError, match="Illegal"):
        store.transition("completed")


def test_process_timeout_and_output_redaction(tmp_path: Path) -> None:
    heartbeats = 0

    def heartbeat() -> None:
        nonlocal heartbeats
        heartbeats += 1

    result = run_bounded_process(
        [sys.executable, "-c", "import time; print('secret-value'); time.sleep(2)"],
        cwd=tmp_path,
        environment={},
        timeout_seconds=1,
        maximum_output_bytes=1024,
        secrets=("secret-value",),
        heartbeat_seconds=1,
        heartbeat=heartbeat,
    )
    assert result.timed_out is True
    assert "secret-value" not in result.stdout
    assert heartbeats > 0
    with pytest.raises(ValueError, match="output exceeded"):
        run_bounded_process(
            [sys.executable, "-c", "print('x' * 100000)"],
            cwd=tmp_path,
            environment={},
            timeout_seconds=5,
            maximum_output_bytes=1024,
            secrets=(),
            heartbeat_seconds=1,
            heartbeat=lambda: None,
        )
    with pytest.raises(ToolSandboxError) as raised:
        run_bounded_process(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            cwd=tmp_path,
            environment={},
            timeout_seconds=10,
            maximum_output_bytes=1024,
            secrets=(),
            heartbeat_seconds=1,
            heartbeat=lambda: (_ for _ in ()).throw(
                ToolSandboxError("tool_control_port_publicly_exposed", "unsafe binding")
            ),
        )
    assert raised.value.code == "tool_control_port_publicly_exposed"


def test_telemetry_can_append_retry_without_reordering(tmp_path: Path) -> None:
    from cgr.quantum_repair.model_provider.telemetry import (
        ProviderTelemetryLog,
        verify_provider_telemetry,
    )

    path = tmp_path / "events.jsonl"
    values = {
        "repair_run_identifier": "repair-run-001",
        "attempt_identifier": "attempt-000",
        "invocation_identifier": "provider-invocation-000",
    }
    ProviderTelemetryLog(path, **values).append(
        "provider_invocation_started", "created"
    )
    ProviderTelemetryLog(path, **values).append(
        "provider_invocation_retried", "retrying"
    )
    events = verify_provider_telemetry(path)
    assert [event.sequence for event in events] == [0, 1]
    assert events[-1].event_type == "provider_invocation_retried"


def test_budget_hard_caps_unknown_schemas_and_request_tampering() -> None:
    with pytest.raises(ValidationError):
        ProviderBudget(maximum_files_changed=9)
    with pytest.raises(ValidationError):
        ProviderBudget(maximum_input_tokens=50_000, maximum_total_tokens=60_000)
    with pytest.raises(ValidationError):
        ModelEndpointDescriptor.model_validate(
            {
                "schema_version": "legacy/0",
                "endpoint_type": "openai-compatible",
            }
        )
    values = {
        "provider_invocation_identifier": "provider-invocation-000",
        "invocation_sequence": 0,
        "repair_run_identifier": "repair-run-001",
        "attempt_identifier": "attempt-000",
        "directive_sha256": "a" * 64,
        "input_source_manifest_sha256": "b" * 64,
        "public_task_identity": "c" * 64,
        "provider_capability_sha256": "d" * 64,
        "model_endpoint_descriptor_sha256": "e" * 64,
        "agent_descriptor_sha256": "f" * 64,
        "prompt_sha256": "1" * 64,
        "budget": ProviderBudget(),
        "allowed_paths": ("main.py",),
    }
    request = seal_contract(ProviderInvocationRequest, values, "request_content_sha256")
    payload = request.model_dump(mode="json")
    payload["attempt_identifier"] = "attempt-001"
    with pytest.raises(ValidationError, match="recomputed"):
        ProviderInvocationRequest.model_validate(payload)


def test_provider_budget_is_shared_across_repair_invocations() -> None:
    from cgr.quantum_repair.model_provider.provider import _remaining_config
    from cgr.quantum_repair.providers import RepairProviderError

    config = SWEAgentProviderConfig()
    consumption: dict[str, int | float] = {
        "provider_invocations": 1,
        "model_calls": 2,
        "input_tokens": 1_000,
        "output_tokens": 500,
        "total_tokens": 1_500,
        "tool_calls": 3,
        "tool_output_bytes": 100,
        "elapsed_seconds": 10.2,
    }
    remaining = _remaining_config(config, consumption).budget
    assert remaining.maximum_model_calls == 10
    assert remaining.maximum_total_tokens == 58_500
    assert remaining.maximum_wall_seconds == 889
    exhausted = {**consumption, "model_calls": config.budget.maximum_model_calls}
    with pytest.raises(RepairProviderError, match="budget is exhausted"):
        _remaining_config(config, exhausted)


def test_acceptance_manifest_is_reviewed_twelve_case_parity_set() -> None:
    manifest = load_model_acceptance_manifest(ACCEPTANCE)
    assert len(manifest.cases) == 12
    assert manifest.modes == ("baseline", "cgr")
    assert len(manifest.repeatability_cases) == 6
    assert manifest.repeatability_runs == 2
    assert manifest.minimum_cgr_broken_authorized == 8
    assert manifest.minimum_absolute_improvement == 2


def test_preflight_failure_aborts_acceptance_before_cases(tmp_path: Path) -> None:
    result = run_model_acceptance(
        acceptance_manifest_path=ACCEPTANCE,
        provider_config=SWEAgentProviderConfig(),
        trusted_reference_directory=tmp_path / "missing-trusted-reference",
        result_root=tmp_path / "results",
        candidate_image_identifier="sha256:" + "d" * 64,
        candidate_lock_path=ROOT / "requirements/quantum-preflight.lock",
        fixture_root=ROOT / "benchmark-fixtures/quantum-repair-v1",
        diagnosis_support_path=(
            ROOT
            / "benchmark-fixtures/quantum-candidate-v1/_support/standalone_candidate.py"
        ),
    )
    assert result["model_provider_acceptance_completed"] is False
    assert result["provider_preflight_failures"] == 1
    assert result["cases_started"] == 0
    assert result["total_model_tokens"] == 0


def test_smoke_gate_requires_positive_real_model_usage(tmp_path: Path) -> None:
    config = SWEAgentProviderConfig()
    values = {
        "schema_version": "cgr.quantum-repair-provider-smoke/1.2.0",
        "provider_smoke_passed": True,
        "tool_image_descriptor_sha256": "a" * 64,
        "tool_network_policy_descriptor_sha256": "d" * 64,
        "tool_control_proxy_policy_descriptor_sha256": "e" * 64,
        "endpoint_descriptor_sha256": "b" * 64,
        "agent_descriptor_sha256": "c" * 64,
        "provider_configuration_sha256": sha256_fingerprint(
            config.model_dump(mode="json")
        ),
        "cgr_commit": repository_commit(ROOT),
        "model_request_count": 0,
        "total_model_tokens": 0,
    }
    values["report_sha256"] = sha256_fingerprint(values)
    path = tmp_path / "smoke.json"
    path.write_text(json.dumps(values), encoding="utf-8")
    with pytest.raises(ValueError, match="did not reach"):
        _verify_smoke_report(
            path,
            provider_config=config,
            tool_image_sha256="a" * 64,
            network_policy_sha256="d" * 64,
            proxy_policy_sha256="e" * 64,
            endpoint_sha256="b" * 64,
            agent_sha256="c" * 64,
        )


def test_smoke_gate_rejects_network_policy_mismatch(tmp_path: Path) -> None:
    config = SWEAgentProviderConfig()
    values = {
        "schema_version": "cgr.quantum-repair-provider-smoke/1.2.0",
        "provider_smoke_passed": True,
        "tool_image_descriptor_sha256": "a" * 64,
        "tool_network_policy_descriptor_sha256": "e" * 64,
        "tool_control_proxy_policy_descriptor_sha256": "f" * 64,
        "endpoint_descriptor_sha256": "b" * 64,
        "agent_descriptor_sha256": "c" * 64,
        "provider_configuration_sha256": sha256_fingerprint(
            config.model_dump(mode="json")
        ),
        "cgr_commit": repository_commit(ROOT),
        "model_request_count": 1,
        "total_model_tokens": 2,
    }
    values["report_sha256"] = sha256_fingerprint(values)
    path = tmp_path / "smoke.json"
    path.write_text(json.dumps(values), encoding="utf-8")
    with pytest.raises(ValueError, match="does not match"):
        _verify_smoke_report(
            path,
            provider_config=config,
            tool_image_sha256="a" * 64,
            network_policy_sha256="d" * 64,
            proxy_policy_sha256="f" * 64,
            endpoint_sha256="b" * 64,
            agent_sha256="c" * 64,
        )


def test_replay_requires_exact_network_policy_evidence(tmp_path: Path) -> None:
    policy = tool_network_policy_descriptor(SWEAgentProviderConfig())
    path = tmp_path / "tool-network-policy.json"
    path.write_text(json.dumps(policy.model_dump(mode="json")), encoding="utf-8")
    assert (
        _verify_provider_network_policy(tmp_path, policy.descriptor_sha256)
        == policy.descriptor_sha256
    )
    with pytest.raises(ValueError, match="substituted"):
        _verify_provider_network_policy(tmp_path, "f" * 64)
    path.unlink()
    with pytest.raises(ValueError, match="missing"):
        _verify_provider_network_policy(tmp_path, policy.descriptor_sha256)


def test_verification_scripts_require_suitable_python_and_no_false_success() -> None:
    for name in (
        "check-quantum-sweagent-provider.sh",
        "check-quantum-sweagent-tool-image.sh",
        "run-quantum-sweagent-qwen-provider-smoke.sh",
        "run-quantum-sweagent-qwen-acceptance.sh",
        "verify-quantum-sweagent-qwen-acceptance.sh",
    ):
        source = (ROOT / "scripts" / name).read_text(encoding="utf-8")
        assert "CGR_PYTHON:?" in source
        assert "import pydantic, cgr" in source
    verifier = (ROOT / "scripts/verify-quantum-sweagent-qwen-acceptance.sh").read_text(
        encoding="utf-8"
    )
    assert "set -euo pipefail" in verifier
    assert "verified_acceptance_summary" in verifier
    bash = shutil.which("bash")
    if bash is None and Path("C:/Program Files/Git/bin/bash.exe").is_file():
        bash = "C:/Program Files/Git/bin/bash.exe"
    if bash is None:
        pytest.skip("Bash is unavailable for verifier environment test.")
    environment = dict(os.environ)
    environment.pop("CGR_PYTHON", None)
    process = subprocess.run(
        [
            bash,
            str(ROOT / "scripts/verify-quantum-sweagent-qwen-acceptance.sh"),
            str(ROOT / "missing-acceptance"),
        ],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    assert process.returncode != 0
    assert "CGR_PYTHON" in process.stderr
    assert "verified_acceptance_summary" not in process.stdout


def test_acceptance_repeatability_contradiction_fails_gate() -> None:
    manifest = load_model_acceptance_manifest(ACCEPTANCE)
    runs: list[dict[str, Any]] = []
    for mode in manifest.modes:
        for case in manifest.cases:
            repetitions = (
                manifest.repeatability_runs
                if case in manifest.repeatability_cases
                else 1
            )
            for repetition in range(repetitions):
                authorized = case == "valid-control" or mode == "cgr"
                if mode == "cgr" and case == "syntax-error" and repetition == 1:
                    authorized = False
                runs.append(
                    {
                        "mode": mode,
                        "case_identifier": case,
                        "repetition": repetition,
                        "completed": True,
                        "authorized": authorized,
                        "provider_budget_sha256": "a" * 64,
                        "safety_failure": False,
                    }
                )
    summary = _summarize(manifest, runs)
    assert summary["repeatability_failures"] == 1
    assert summary["model_provider_acceptance_passed"] is False


def test_sanitizer_never_logs_api_keys_or_authorization_headers() -> None:
    value = sanitize_text(
        "api_key=abc Authorization: Bearer abc password=abc", ("abc",)
    )
    assert "abc" not in value
    assert value.count("[REDACTED]") >= 3


def test_provider_contains_no_benchmark_specific_repair_logic() -> None:
    provider_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "src/cgr/quantum_repair/model_provider").glob("*.py")
    )
    for case_identifier in (
        "syntax-then-structure",
        "wrong-bond-distance",
        "wrong-active-space",
        "wrong-mapper",
        "electronic-energy-as-total",
        "forged-content-hash",
    ):
        assert case_identifier not in provider_source
    assert "ReviewedBenchmarkRepairProvider" not in provider_source


@pytest.mark.quantum_container
def test_real_offline_tool_deployment_when_docker_is_available() -> None:
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("Docker is genuinely unavailable.")
    daemon = subprocess.run(
        [docker, "info"], capture_output=True, text=True, check=False
    )
    if daemon.returncode != 0:
        pytest.skip("Docker daemon is genuinely unavailable.")
    config_path = os.environ.get("CGR_OFFLINE_PROVIDER_CONFIG")
    if not config_path:
        pytest.fail(
            "Docker is available; set CGR_OFFLINE_PROVIDER_CONFIG to the generated immutable configuration."
        )
    config = load_provider_config(Path(config_path))
    health = run_offline_tool_preflight(config)
    assert health.startup_result == "passed"
    assert health.control_network_type == "docker_internal"
    assert health.network_internal is True
    assert health.control_bind_address == "127.0.0.1"
    assert health.public_port_exposure_observed is False
    assert health.direct_external_ip_reachable is False
    assert health.external_hostname_reachable is False
    assert health.pypi_reachable is False
    assert health.infrastructure_package_install_attempt_observed is False
