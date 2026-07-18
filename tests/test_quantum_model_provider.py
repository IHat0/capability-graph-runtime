from __future__ import annotations

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
    ToolSandboxImageDescriptor,
    seal_contract,
)
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
    ToolSandboxError,
    classify_bootstrap_failure,
    infrastructure_install_attempt_observed,
    inspect_tool_image,
    run_offline_tool_preflight,
)
from cgr.quantum_repair.patches import (
    RepairPatchRejected,
    create_patch,
    validate_and_apply_patch,
)
from cgr.quantum_repair.persistence import create_source_manifest
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
            "build_schema_version": "cgr.quantum-sweagent-tool-image-build/1.0.0",
            "build_input_sha256": "b" * 64,
            "offline_bootstrap": True,
            "network_policy": "none",
            "required_sweagent_commit": REQUIRED_SWEAGENT_COMMIT,
            "swerex_version": "1.4.0",
            "runtime_identity": "c" * 64,
        },
        "descriptor_sha256",
    )


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
    assert "--network=none" in TOOL_DOCKER_ARGS


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
    assert "--network=none" in overlay_text
    assert "edit_anthropic" not in overlay_text
    assert "pip install" not in overlay_text


def test_tool_image_requires_immutable_identity() -> None:
    with pytest.raises(ValidationError, match="exact sha256"):
        SWEAgentProviderConfig(tool_container_image="python:3.12")
    with pytest.raises(ValidationError, match="exact sha256"):
        SWEAgentProviderConfig(tool_container_image="cgr-sweagent-tool:latest")


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
        "org.cgr.tool-sandbox.schema": "cgr.quantum-sweagent-tool-image/1.0.0",
        "org.cgr.tool-sandbox.build-input-sha256": "b" * 64,
        "org.cgr.tool-sandbox.sweagent-commit": REQUIRED_SWEAGENT_COMMIT,
        "org.cgr.tool-sandbox.swerex-version": "1.4.0",
        "org.cgr.tool-sandbox.offline-bootstrap": "true",
        "org.cgr.tool-sandbox.network-policy": "none",
    }
    monkeypatch.setattr(tool_module.shutil, "which", lambda _name: "docker")

    def runner(*_args: Any, **_kwargs: Any) -> Any:
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps([{"Id": image_id, "Config": {"Labels": labels}}]),
        )

    descriptor = inspect_tool_image(config, runner=runner)
    assert descriptor.image_id == image_id
    assert descriptor.network_policy == "none"

    def mismatch(*_args: Any, **_kwargs: Any) -> Any:
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                [{"Id": "sha256:" + "f" * 64, "Config": {"Labels": labels}}]
            ),
        )

    with pytest.raises(ToolSandboxError, match="substituted"):
        inspect_tool_image(config, runner=mismatch)


def test_exact_offline_deployment_preflight_and_cleanup() -> None:
    class Runtime:
        async def upload(self, _request: Any) -> None:
            return None

        async def execute(self, command: Any) -> Any:
            if command.command == "env":
                return SimpleNamespace(stdout="PATH=/usr/bin", exit_code=0)
            if "docker.sock" in command.command or "connect_ex" in command.command:
                return SimpleNamespace(stdout="", exit_code=1)
            return SimpleNamespace(stdout="ok", exit_code=0)

    class Deployment:
        def __init__(self) -> None:
            self.runtime = Runtime()
            self.stopped = False

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
            if "--network=none" in values["docker_args"]
            else pytest.fail("network isolation missing")
        ),
    )
    assert health.startup_result == "passed"
    assert health.cleanup_passed is True
    assert deployment.stopped is True
    assert health.credential_forwarding_observed is False
    assert health.docker_socket_forwarded is False
    assert health.model_endpoint_reachable is False


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
    )
    assert health.startup_result == "failed"
    assert health.failure_classification == "offline_dependency_missing"
    assert health.infrastructure_package_install_attempt_observed is True
    assert health.cleanup_passed is True
    assert classify_bootstrap_failure("Container process terminated") == (
        "tool_container_terminated_during_startup"
    )


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
        "schema_version": "cgr.quantum-repair-provider-smoke/1.0.0",
        "provider_smoke_passed": True,
        "tool_image_descriptor_sha256": "a" * 64,
        "endpoint_descriptor_sha256": "b" * 64,
        "agent_descriptor_sha256": "c" * 64,
        "provider_configuration_sha256": sha256_fingerprint(
            config.model_dump(mode="json")
        ),
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
            endpoint_sha256="b" * 64,
            agent_sha256="c" * 64,
        )


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
    assert health.network_mode == "none"
    assert health.infrastructure_package_install_attempt_observed is False
