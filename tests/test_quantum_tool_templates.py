from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from cgr.quantum_repair.model_provider.agent import (
    provider_overlay,
    validate_pristine_tool_templates,
    verify_pristine_sweagent,
)
from cgr.quantum_repair.model_provider.config import (
    REQUIRED_SWEAGENT_COMMIT,
    SWEAgentProviderConfig,
    ToolTemplateVariables,
)
from cgr.quantum_repair.model_provider.contracts import (
    ProviderInvocationRequest,
    ProviderBudget,
    ToolSandboxImageDescriptor,
    seal_contract,
)
from cgr.quantum_repair.model_provider.tool_templates import (
    PROVIDER_TOOL_BUNDLES,
    ToolTemplateConfigurationError,
    validate_tool_template_configuration,
)
from cgr.quantum_repair.model_provider.tool_sandbox import classify_bootstrap_failure
from cgr.quantum_repair.persistence import write_evidence
from cgr.quantum_repair.replay import _verify_provider_tool_templates
from cgr.science import sha256_fingerprint

ROOT = Path(__file__).parents[1]
SWE_SOURCE = ROOT / ".sandbox-sweagent-src"
SWE_PYTHON = ROOT / ".sandbox-sweagent-venv/Scripts/python.exe"
SWE_EXECUTABLE = ROOT / ".sandbox-sweagent-venv/Scripts/sweagent.exe"


def _source(tmp_path: Path, docstring: str) -> Path:
    source = tmp_path / "swe"
    for bundle in PROVIDER_TOOL_BUNDLES:
        directory = source / bundle
        directory.mkdir(parents=True)
        value = docstring if bundle == "tools/windowed" else "plain documentation"
        (directory / "config.yaml").write_text(
            "tools:\n  command:\n    docstring: " + json.dumps(value) + "\n",
            encoding="utf-8",
        )
    return source


def _validation(window: int = 100):
    return validate_tool_template_configuration(
        source=SWE_SOURCE,
        pristine_source_commit=REQUIRED_SWEAGENT_COMMIT,
        configured_variables={"WINDOW": window},
    )


def _image() -> ToolSandboxImageDescriptor:
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


def _request(validation_sha256: str) -> ProviderInvocationRequest:
    return seal_contract(
        ProviderInvocationRequest,
        {
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
            "tool_template_validation_sha256": validation_sha256,
            "prompt_sha256": "1" * 64,
            "budget": ProviderBudget(),
            "allowed_paths": ("main.py",),
        },
        "request_content_sha256",
    )


def test_windowed_without_window_fails_before_launch_with_zero_consumption(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path, "moves {WINDOW} lines")
    with pytest.raises(ToolTemplateConfigurationError) as raised:
        validate_tool_template_configuration(
            source=source,
            pristine_source_commit=REQUIRED_SWEAGENT_COMMIT,
            configured_variables={},
        )
    assert raised.value.code == "tool_configuration_template_missing_variable"
    assert raised.value.model_request_count == 0
    assert raised.value.total_tokens == 0
    assert raised.value.trusted_evidence_exposure == 0
    assert (
        classify_bootstrap_failure("KeyError: 'WINDOW'")
        == "tool_configuration_template_missing_variable"
    )


def test_real_overlay_resolves_every_bundle_and_upstream_command_docs() -> None:
    config = SWEAgentProviderConfig(
        sweagent_source=SWE_SOURCE,
        sweagent_executable=str(SWE_EXECUTABLE),
    )
    validation = validate_pristine_tool_templates(config)
    assert validation.configured_bundles == PROVIDER_TOOL_BUNDLES
    assert validation.required_variables == ("WINDOW",)
    assert validation.configured_variables == {"WINDOW": 100}
    assert "env_variables:\n      WINDOW: 100" in provider_overlay(config)
    script = (
        "from sweagent.tools.tools import ToolConfig;"
        "c=ToolConfig.model_validate({"
        "'env_variables':{'WINDOW':100},"
        "'bundles':[{'path':p} for p in " + repr(list(PROVIDER_TOOL_BUNDLES)) + "],"
        "'enable_bash_tool':True,"
        "'parse_function':{'type':'function_calling'}});"
        "assert '100 lines' in c.command_docs;print('command-docs-passed')"
    )
    environment = {
        **os.environ,
        "PYTHONIOENCODING": "utf-8",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "safe.directory",
        "GIT_CONFIG_VALUE_0": SWE_SOURCE.as_posix(),
    }
    result = subprocess.run(
        [str(SWE_PYTHON), "-c", script],
        cwd=SWE_SOURCE,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "command-docs-passed" in result.stdout
    assert "KeyError" not in result.stderr


def test_unknown_missing_malformed_and_unbounded_template_values_fail_closed(
    tmp_path: Path,
) -> None:
    unknown_source = _source(tmp_path / "unknown", "uses {UNREVIEWED}")
    with pytest.raises(ToolTemplateConfigurationError, match="unknown"):
        validate_tool_template_configuration(
            source=unknown_source,
            pristine_source_commit=REQUIRED_SWEAGENT_COMMIT,
            configured_variables={"WINDOW": 100},
        )
    source = _source(tmp_path / "window", "moves {WINDOW} lines")
    for value in ("100", True, 0, -1, 1_001, 10**100):
        with pytest.raises(ToolTemplateConfigurationError, match="bounded"):
            validate_tool_template_configuration(
                source=source,
                pristine_source_commit=REQUIRED_SWEAGENT_COMMIT,
                configured_variables={"WINDOW": value},
            )
    with pytest.raises(ToolTemplateConfigurationError, match="unknown"):
        validate_tool_template_configuration(
            source=source,
            pristine_source_commit=REQUIRED_SWEAGENT_COMMIT,
            configured_variables={"WINDOW": 100, "SECRET": 1},
        )
    with pytest.raises(ValidationError):
        ToolTemplateVariables(WINDOW="100")


def test_window_changes_overlay_agent_request_and_validation_identity() -> None:
    default = SWEAgentProviderConfig(
        sweagent_source=SWE_SOURCE,
        sweagent_executable=str(SWE_EXECUTABLE),
    )
    changed = default.model_copy(
        update={"tool_template_variables": ToolTemplateVariables(WINDOW=80)}
    )
    default_validation = validate_pristine_tool_templates(default)
    changed_validation = validate_pristine_tool_templates(changed)
    assert provider_overlay(default) != provider_overlay(changed)
    assert sha256_fingerprint(default.model_dump(mode="json")) != sha256_fingerprint(
        changed.model_dump(mode="json")
    )
    assert default_validation.validation_sha256 != changed_validation.validation_sha256
    default_agent = verify_pristine_sweagent(default, tool_image_descriptor=_image())
    changed_agent = verify_pristine_sweagent(changed, tool_image_descriptor=_image())
    assert default_agent.configuration_sha256 != changed_agent.configuration_sha256
    assert default_agent.descriptor_sha256 != changed_agent.descriptor_sha256
    assert (
        _request(default_validation.validation_sha256).request_content_sha256
        != _request(changed_validation.validation_sha256).request_content_sha256
    )


def test_replay_rejects_substituted_template_values(tmp_path: Path) -> None:
    original = _validation(100)
    substituted = _validation(80)
    write_evidence(tmp_path / "tool-template-validation.json", substituted)
    with pytest.raises(ValueError, match="substituted"):
        _verify_provider_tool_templates(tmp_path, original.validation_sha256)


def test_pristine_sweagent_checkout_remains_unmodified() -> None:
    result = subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={SWE_SOURCE.as_posix()}",
            "-C",
            str(SWE_SOURCE),
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout == ""
