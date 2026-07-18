"""Immutable evidence contracts for model-backed repair invocations."""

from __future__ import annotations

from typing import Any, Literal, Self, TypeVar

from pydantic import Field, field_validator, model_validator

from cgr.science import CanonicalModel, sha256_fingerprint
from cgr.science.canonical import validate_identifier, validate_sha256

ENDPOINT_SCHEMA = "cgr.quantum-repair-model-endpoint/1.0.0"
AGENT_SCHEMA = "cgr.quantum-repair-agent-descriptor/1.0.0"
AGENT_SCHEMA_V1_1 = "cgr.quantum-repair-agent-descriptor/1.1.0"
AGENT_SCHEMA_V1_2 = "cgr.quantum-repair-agent-descriptor/1.2.0"
AGENT_SCHEMA_V1_3 = "cgr.quantum-repair-agent-descriptor/1.3.0"
TOOL_IMAGE_SCHEMA = "cgr.quantum-sweagent-tool-image/1.1.0"
TOOL_NETWORK_POLICY_SCHEMA = "cgr.quantum-sweagent-tool-network-policy/1.0.0"
TOOL_PROXY_POLICY_SCHEMA = "cgr.quantum-swerex-control-proxy-policy/1.0.0"
TOOL_PROXY_LIFECYCLE_SCHEMA = "cgr.quantum-swerex-control-proxy-lifecycle/1.0.0"
TOOL_HEALTH_SCHEMA = "cgr.quantum-sweagent-tool-health/1.2.0"
BUDGET_SCHEMA = "cgr.quantum-repair-provider-budget/1.0.0"
PROMPT_SCHEMA = "cgr.quantum-repair-model-prompt/1.0.0"
REQUEST_SCHEMA = "cgr.quantum-repair-provider-request/1.0.0"
RESULT_SCHEMA = "cgr.quantum-repair-provider-result/1.0.0"
TRAJECTORY_SCHEMA = "cgr.quantum-repair-provider-trajectory/1.0.0"
TELEMETRY_SCHEMA = "cgr.quantum-repair-provider-event/1.0.0"

InvocationStatus = Literal[
    "created",
    "request_persisted",
    "launching",
    "running",
    "response_persisted",
    "patch_extracted",
    "completed",
    "interrupted",
    "retryable_failure",
    "terminal_failure",
]


class SamplingParameters(CanonicalModel):
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    top_p: float = Field(default=1.0, gt=0.0, le=1.0)
    seed: int = Field(default=8_001, ge=0)

    @model_validator(mode="after")
    def deterministic(self) -> Self:
        if self.temperature != 0.0 or self.top_p != 1.0:
            raise ValueError("Repair-provider sampling must be deterministic in v1.")
        return self


class ModelEndpointDescriptor(CanonicalModel):
    schema_version: str = ENDPOINT_SCHEMA
    endpoint_type: Literal["openai-compatible"] = "openai-compatible"
    base_url_identity: str
    requested_model_identifier: str
    observed_model_identifier: str
    observed_context_length: int = Field(gt=0)
    api_compatibility_version: str
    tls_policy: Literal["loopback-http", "verified-https"]
    loopback_only: bool
    sampling: SamplingParameters
    request_timeout_seconds: int = Field(gt=0, le=300)
    maximum_total_tokens: int = Field(gt=0)
    descriptor_sha256: str

    @field_validator("schema_version")
    @classmethod
    def valid_schema(cls, value: str) -> str:
        if value != ENDPOINT_SCHEMA:
            raise ValueError("Unsupported model endpoint descriptor schema.")
        return value

    @field_validator(
        "requested_model_identifier",
        "observed_model_identifier",
        "api_compatibility_version",
    )
    @classmethod
    def identifiers(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("descriptor_sha256")
    @classmethod
    def digest(cls, value: str) -> str:
        return validate_sha256(value)

    def canonical_identity(self) -> Any:
        value = self.model_dump(mode="json")
        value.pop("descriptor_sha256", None)
        return value

    @model_validator(mode="after")
    def verified(self) -> Self:
        if not self.loopback_only:
            raise ValueError("V1 model endpoints must be loopback-only.")
        if self.descriptor_sha256 != self.fingerprint:
            raise ValueError("Model endpoint descriptor hash was not recomputed.")
        return self


class AgentDescriptor(CanonicalModel):
    schema_version: str = AGENT_SCHEMA_V1_3
    agent_type: Literal["pristine-sweagent"] = "pristine-sweagent"
    pristine_source_commit: str
    source_tree_clean: bool
    configuration_sha256: str
    tool_environment_sha256: str
    agent_version: str
    patch_output_mechanism: Literal["official-trajectory-prediction"]
    executable_identity_sha256: str
    tool_image_descriptor_sha256: str | None = None
    tool_network_policy_descriptor_sha256: str | None = None
    tool_control_proxy_policy_descriptor_sha256: str | None = None
    descriptor_sha256: str

    @field_validator("schema_version")
    @classmethod
    def valid_schema(cls, value: str) -> str:
        if value not in {
            AGENT_SCHEMA,
            AGENT_SCHEMA_V1_1,
            AGENT_SCHEMA_V1_2,
            AGENT_SCHEMA_V1_3,
        }:
            raise ValueError("Unsupported agent descriptor schema.")
        return value

    @field_validator("pristine_source_commit")
    @classmethod
    def commit(cls, value: str) -> str:
        if len(value) != 40 or any(item not in "0123456789abcdef" for item in value):
            raise ValueError("Agent source identity must be a complete Git commit.")
        return value

    @field_validator(
        "configuration_sha256",
        "tool_environment_sha256",
        "executable_identity_sha256",
        "tool_image_descriptor_sha256",
        "tool_network_policy_descriptor_sha256",
        "tool_control_proxy_policy_descriptor_sha256",
        "descriptor_sha256",
    )
    @classmethod
    def digests(cls, value: str | None) -> str | None:
        return validate_sha256(value) if value is not None else None

    def canonical_identity(self) -> Any:
        value = self.model_dump(mode="json")
        value.pop("descriptor_sha256", None)
        return value

    @model_validator(mode="after")
    def verified(self) -> Self:
        if not self.source_tree_clean:
            raise ValueError("SWE-agent source must be pristine.")
        if (
            self.schema_version
            in {AGENT_SCHEMA_V1_1, AGENT_SCHEMA_V1_2, AGENT_SCHEMA_V1_3}
            and self.tool_image_descriptor_sha256 is None
        ):
            raise ValueError("Agent descriptor requires an immutable tool image.")
        if (
            self.schema_version in {AGENT_SCHEMA_V1_2, AGENT_SCHEMA_V1_3}
            and self.tool_network_policy_descriptor_sha256 is None
        ):
            raise ValueError("Agent descriptor requires a tool network policy.")
        if (
            self.schema_version == AGENT_SCHEMA_V1_3
            and self.tool_control_proxy_policy_descriptor_sha256 is None
        ):
            raise ValueError("Agent descriptor requires a control proxy policy.")
        if self.descriptor_sha256 != self.fingerprint:
            raise ValueError("Agent descriptor hash was not recomputed.")
        return self


class ToolSandboxImageDescriptor(CanonicalModel):
    schema_version: str = TOOL_IMAGE_SCHEMA
    image_repository: str
    image_id: str
    build_schema_version: str
    build_input_sha256: str
    offline_bootstrap: Literal[True] = True
    network_policy: Literal["docker-internal-loopback-control"] = (
        "docker-internal-loopback-control"
    )
    required_sweagent_commit: str
    swerex_version: str
    runtime_identity: str
    descriptor_sha256: str

    @field_validator("schema_version")
    @classmethod
    def valid_schema(cls, value: str) -> str:
        if value != TOOL_IMAGE_SCHEMA:
            raise ValueError("Unsupported tool image descriptor schema.")
        return value

    @field_validator("image_id")
    @classmethod
    def immutable_image(cls, value: str) -> str:
        if not value.startswith("sha256:") or len(value) != 71:
            raise ValueError("Tool image must use an exact sha256 image ID.")
        validate_sha256(value.removeprefix("sha256:"))
        return value

    @field_validator("build_input_sha256", "runtime_identity", "descriptor_sha256")
    @classmethod
    def digests(cls, value: str) -> str:
        return validate_sha256(value)

    def canonical_identity(self) -> Any:
        value = self.model_dump(mode="json")
        value.pop("descriptor_sha256", None)
        return value

    @model_validator(mode="after")
    def verified(self) -> Self:
        if self.descriptor_sha256 != self.fingerprint:
            raise ValueError("Tool image descriptor hash was not recomputed.")
        return self


class ToolNetworkPolicyDescriptor(CanonicalModel):
    schema_version: str = TOOL_NETWORK_POLICY_SCHEMA
    external_egress_disabled: Literal[True] = True
    control_network_type: Literal["docker_internal"] = "docker_internal"
    control_network_driver: Literal["bridge"] = "bridge"
    control_bind_address: Literal["127.0.0.1"] = "127.0.0.1"
    control_container_port: Literal[8000] = 8000
    public_port_exposure: Literal[False] = False
    model_endpoint_access: Literal[False] = False
    invocation_scoped_ownership: Literal[True] = True
    descriptor_sha256: str

    @field_validator("schema_version")
    @classmethod
    def valid_schema(cls, value: str) -> str:
        if value != TOOL_NETWORK_POLICY_SCHEMA:
            raise ValueError("Unsupported tool network policy schema.")
        return value

    @field_validator("descriptor_sha256")
    @classmethod
    def digest(cls, value: str) -> str:
        return validate_sha256(value)

    def canonical_identity(self) -> Any:
        value = self.model_dump(mode="json")
        value.pop("descriptor_sha256", None)
        return value

    @model_validator(mode="after")
    def verified(self) -> Self:
        if self.descriptor_sha256 != self.fingerprint:
            raise ValueError("Tool network policy hash was not recomputed.")
        return self


class ToolControlProxyPolicyDescriptor(CanonicalModel):
    schema_version: str = TOOL_PROXY_POLICY_SCHEMA
    proxy_type: Literal["provider_owned_tcp"] = "provider_owned_tcp"
    proxy_bind_address: Literal["127.0.0.1"] = "127.0.0.1"
    proxy_destination_port: Literal[8000] = 8000
    proxy_public_exposure: Literal[False] = False
    proxy_external_destination: Literal[False] = False
    invocation_scoped_ownership: Literal[True] = True
    descriptor_sha256: str

    @field_validator("schema_version")
    @classmethod
    def valid_schema(cls, value: str) -> str:
        if value != TOOL_PROXY_POLICY_SCHEMA:
            raise ValueError("Unsupported tool control proxy policy schema.")
        return value

    @field_validator("descriptor_sha256")
    @classmethod
    def digest(cls, value: str) -> str:
        return validate_sha256(value)

    def canonical_identity(self) -> Any:
        value = self.model_dump(mode="json")
        value.pop("descriptor_sha256", None)
        return value

    @model_validator(mode="after")
    def verified(self) -> Self:
        if self.descriptor_sha256 != self.fingerprint:
            raise ValueError("Tool control proxy policy hash was not recomputed.")
        return self


class ToolControlProxyLifecycleArtifact(CanonicalModel):
    schema_version: str = TOOL_PROXY_LIFECYCLE_SCHEMA
    proxy_policy_descriptor_sha256: str
    proxy_type: Literal["provider_owned_tcp"] = "provider_owned_tcp"
    proxy_bind_identity_sha256: str
    proxy_bind_address: Literal["127.0.0.1"] = "127.0.0.1"
    proxy_source_port: int = Field(ge=0, le=65535)
    proxy_destination_container_identity: str
    proxy_destination_image_identity: str
    proxy_destination_internal_ip_identity: str
    proxy_destination_network_identity_sha256: str
    proxy_destination_port: Literal[8000] = 8000
    proxy_public_exposure: Literal[False] = False
    proxy_external_destination: Literal[False] = False
    startup_result: Literal["passed", "failed"]
    readiness_result: Literal["passed", "failed", "not_reached"]
    cleanup_passed: bool
    runtime_seconds: float = Field(ge=0)
    failure_classification: str | None
    lifecycle_artifact_sha256: str

    @field_validator(
        "proxy_policy_descriptor_sha256",
        "proxy_bind_identity_sha256",
        "proxy_destination_internal_ip_identity",
        "proxy_destination_network_identity_sha256",
        "lifecycle_artifact_sha256",
    )
    @classmethod
    def digests(cls, value: str) -> str:
        return validate_sha256(value)

    def canonical_identity(self) -> Any:
        value = self.model_dump(mode="json")
        value.pop("lifecycle_artifact_sha256", None)
        return value

    @model_validator(mode="after")
    def verified(self) -> Self:
        if self.startup_result == "passed" and (
            self.proxy_source_port == 0
            or self.readiness_result != "passed"
            or not self.cleanup_passed
        ):
            raise ValueError("Passing proxy lifecycle evidence is incomplete.")
        if self.lifecycle_artifact_sha256 != self.fingerprint:
            raise ValueError("Tool control proxy lifecycle hash was not recomputed.")
        return self


class ToolSandboxHealthArtifact(CanonicalModel):
    schema_version: str = TOOL_HEALTH_SCHEMA
    tool_image_descriptor_sha256: str
    tool_network_policy_descriptor_sha256: str
    tool_control_proxy_policy_descriptor_sha256: str
    tool_control_proxy_lifecycle_artifact_sha256: str
    deployment_identity_sha256: str
    control_network_type: Literal["docker_internal"]
    network_identifier_sha256: str
    network_ownership_nonce: str
    network_internal: bool
    control_bind_address: Literal["127.0.0.1"]
    allocated_control_port: int = Field(ge=0, le=65535)
    public_port_exposure_observed: bool
    docker_published_host_port_observed: bool
    direct_internal_control_reachable: bool
    proxy_readiness_passed: bool
    proxy_cleanup_passed: bool
    direct_external_ip_reachable: bool
    external_hostname_reachable: bool
    pypi_reachable: bool
    startup_result: Literal["passed", "failed"]
    shell_smoke_passed: bool
    workspace_write_passed: bool
    cleanup_passed: bool
    container_cleanup_passed: bool
    network_cleanup_passed: bool
    credential_forwarding_observed: bool
    docker_socket_forwarded: bool
    model_endpoint_reachable: bool
    infrastructure_package_install_attempt_observed: bool
    runtime_seconds: float = Field(ge=0)
    failure_classification: str | None
    health_artifact_sha256: str

    @field_validator("schema_version")
    @classmethod
    def valid_schema(cls, value: str) -> str:
        if value != TOOL_HEALTH_SCHEMA:
            raise ValueError("Unsupported tool sandbox health schema.")
        return value

    @field_validator(
        "tool_image_descriptor_sha256",
        "tool_network_policy_descriptor_sha256",
        "tool_control_proxy_policy_descriptor_sha256",
        "tool_control_proxy_lifecycle_artifact_sha256",
        "deployment_identity_sha256",
        "network_identifier_sha256",
        "health_artifact_sha256",
    )
    @classmethod
    def digests(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("network_ownership_nonce")
    @classmethod
    def ownership_nonce(cls, value: str) -> str:
        if len(value) != 32 or any(item not in "0123456789abcdef" for item in value):
            raise ValueError("Tool network ownership nonce is malformed.")
        return value

    def canonical_identity(self) -> Any:
        value = self.model_dump(mode="json")
        value.pop("health_artifact_sha256", None)
        return value

    @model_validator(mode="after")
    def verified(self) -> Self:
        if self.startup_result == "passed" and (
            not self.shell_smoke_passed
            or not self.workspace_write_passed
            or not self.cleanup_passed
            or not self.container_cleanup_passed
            or not self.network_cleanup_passed
            or not self.network_internal
            or self.allocated_control_port == 0
            or self.public_port_exposure_observed
            or self.docker_published_host_port_observed
            or not self.direct_internal_control_reachable
            or not self.proxy_readiness_passed
            or not self.proxy_cleanup_passed
            or self.direct_external_ip_reachable
            or self.external_hostname_reachable
            or self.pypi_reachable
            or self.credential_forwarding_observed
            or self.docker_socket_forwarded
            or self.model_endpoint_reachable
            or self.infrastructure_package_install_attempt_observed
        ):
            raise ValueError("Passing tool health evidence violates isolation policy.")
        if self.health_artifact_sha256 != self.fingerprint:
            raise ValueError("Tool health artifact hash was not recomputed.")
        return self


class ProviderBudget(CanonicalModel):
    schema_version: str = BUDGET_SCHEMA
    maximum_model_calls: int = Field(default=12, gt=0, le=64)
    maximum_input_tokens: int = Field(default=48_000, gt=0)
    maximum_output_tokens: int = Field(default=12_000, gt=0)
    maximum_total_tokens: int = Field(default=60_000, gt=0)
    maximum_wall_seconds: int = Field(default=900, gt=0, le=3600)
    maximum_tool_commands: int = Field(default=40, gt=0, le=200)
    maximum_tool_output_bytes: int = Field(default=2 * 1024 * 1024, gt=0)
    maximum_files_read: int = Field(default=32, gt=0, le=128)
    maximum_files_changed: int = Field(default=8, gt=0, le=8)
    maximum_patch_bytes: int = Field(default=64 * 1024, gt=0, le=64 * 1024)
    maximum_changed_lines: int = Field(default=300, gt=0, le=300)
    maximum_retries: int = Field(default=1, ge=0, le=3)

    @field_validator("schema_version")
    @classmethod
    def valid_schema(cls, value: str) -> str:
        if value != BUDGET_SCHEMA:
            raise ValueError("Unsupported provider budget schema.")
        return value

    @model_validator(mode="after")
    def totals(self) -> Self:
        if (
            self.maximum_input_tokens + self.maximum_output_tokens
            > self.maximum_total_tokens
        ):
            raise ValueError("Provider token sub-budgets exceed the total budget.")
        return self


class PromptSourceFile(CanonicalModel):
    relative_path: str
    content_sha256: str
    content: str

    @field_validator("content_sha256")
    @classmethod
    def digest(cls, value: str) -> str:
        return validate_sha256(value)


class ModelRepairPrompt(CanonicalModel):
    schema_version: str = PROMPT_SCHEMA
    prompt_version: str
    guidance_mode: Literal["baseline", "cgr"]
    public_task_identity: str
    public_task: dict[str, Any]
    source_manifest_sha256: str
    source_context_policy: str
    source_context_sha256: str
    source_files: tuple[PromptSourceFile, ...]
    primary_finding_code: str | None
    additional_finding_codes: tuple[str, ...]
    sanitized_guidance: tuple[str, ...]
    required_invariants: tuple[str, ...]
    allowed_paths: tuple[str, ...]
    prohibited_paths: tuple[str, ...]
    maximum_files_changed: int
    maximum_changed_lines: int
    maximum_patch_bytes: int
    attempt_number: int
    remaining_attempt_budget: int
    previous_patch_identities: tuple[str, ...]
    previous_public_failure_categories: tuple[str, ...]
    instructions: tuple[str, ...]
    prompt_sha256: str

    @field_validator("schema_version")
    @classmethod
    def valid_schema(cls, value: str) -> str:
        if value != PROMPT_SCHEMA:
            raise ValueError("Unsupported model repair prompt schema.")
        return value

    @field_validator("source_manifest_sha256", "source_context_sha256", "prompt_sha256")
    @classmethod
    def digests(cls, value: str) -> str:
        return validate_sha256(value)

    def canonical_identity(self) -> Any:
        value = self.model_dump(mode="json")
        value.pop("prompt_sha256", None)
        return value

    @model_validator(mode="after")
    def verified(self) -> Self:
        if self.guidance_mode == "baseline" and (
            self.primary_finding_code is not None
            or self.additional_finding_codes
            or self.sanitized_guidance
        ):
            raise ValueError("Baseline prompts cannot contain CGR diagnosis guidance.")
        if self.guidance_mode == "cgr" and self.primary_finding_code is None:
            raise ValueError("CGR prompts require a sanitized primary finding.")
        if self.prompt_sha256 != self.fingerprint:
            raise ValueError("Model prompt hash was not recomputed.")
        return self


class ProviderInvocationRequest(CanonicalModel):
    schema_version: str = REQUEST_SCHEMA
    provider_invocation_identifier: str
    invocation_sequence: int = Field(ge=0)
    repair_run_identifier: str
    attempt_identifier: str
    directive_sha256: str
    input_source_manifest_sha256: str
    public_task_identity: str
    provider_capability_sha256: str
    model_endpoint_descriptor_sha256: str
    agent_descriptor_sha256: str
    prompt_sha256: str
    budget: ProviderBudget
    allowed_paths: tuple[str, ...]
    request_content_sha256: str

    @field_validator("schema_version")
    @classmethod
    def valid_schema(cls, value: str) -> str:
        if value != REQUEST_SCHEMA:
            raise ValueError("Unsupported provider request schema.")
        return value

    @field_validator(
        "provider_invocation_identifier",
        "repair_run_identifier",
        "attempt_identifier",
    )
    @classmethod
    def identifiers(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator(
        "directive_sha256",
        "input_source_manifest_sha256",
        "provider_capability_sha256",
        "model_endpoint_descriptor_sha256",
        "agent_descriptor_sha256",
        "prompt_sha256",
        "request_content_sha256",
    )
    @classmethod
    def digests(cls, value: str) -> str:
        return validate_sha256(value)

    def canonical_identity(self) -> Any:
        value = self.model_dump(mode="json")
        value.pop("request_content_sha256", None)
        return value

    @model_validator(mode="after")
    def verified(self) -> Self:
        if self.request_content_sha256 != self.fingerprint:
            raise ValueError("Provider request hash was not recomputed.")
        return self


class TrajectoryArtifact(CanonicalModel):
    relative_path: str
    content_sha256: str
    byte_size: int = Field(ge=0)
    redaction_status: Literal["passed"] = "passed"

    @field_validator("content_sha256")
    @classmethod
    def digest(cls, value: str) -> str:
        return validate_sha256(value)


class ProviderTrajectoryManifest(CanonicalModel):
    schema_version: str = TRAJECTORY_SCHEMA
    provider_invocation_identifier: str
    artifacts: tuple[TrajectoryArtifact, ...]
    tool_call_count: int = Field(ge=0)
    model_call_count: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    patch_extraction_source: str
    complete_trajectory_sha256: str

    @field_validator("schema_version")
    @classmethod
    def valid_schema(cls, value: str) -> str:
        if value != TRAJECTORY_SCHEMA:
            raise ValueError("Unsupported trajectory manifest schema.")
        return value

    @field_validator("complete_trajectory_sha256")
    @classmethod
    def digest(cls, value: str) -> str:
        return validate_sha256(value)

    def canonical_identity(self) -> Any:
        value = self.model_dump(mode="json")
        value.pop("complete_trajectory_sha256", None)
        return value

    @model_validator(mode="after")
    def verified(self) -> Self:
        if self.complete_trajectory_sha256 != self.fingerprint:
            raise ValueError("Trajectory manifest hash was not recomputed.")
        return self


class ProviderInvocationResult(CanonicalModel):
    schema_version: str = RESULT_SCHEMA
    request_sha256: str
    provider_invocation_identifier: str
    terminal_status: Literal[
        "completed",
        "interrupted",
        "retryable_failure",
        "terminal_failure",
        "budget_exhausted",
    ]
    started_monotonic_seconds: float = Field(ge=0)
    completed_monotonic_seconds: float = Field(ge=0)
    elapsed_seconds: float = Field(ge=0)
    sweagent_exit_status: int | None
    model_request_count: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    tool_call_count: int = Field(ge=0)
    tool_output_bytes: int = Field(ge=0)
    infrastructure_package_install_attempt_observed: bool = False
    trajectory_identity: str | None
    prediction_identity: str | None
    proposed_patch_identity: str | None
    sanitized_error_code: str | None
    sanitized_error_detail: str | None
    provider_result_sha256: str

    @field_validator("schema_version")
    @classmethod
    def valid_schema(cls, value: str) -> str:
        if value != RESULT_SCHEMA:
            raise ValueError("Unsupported provider result schema.")
        return value

    @field_validator(
        "request_sha256",
        "trajectory_identity",
        "prediction_identity",
        "proposed_patch_identity",
        "provider_result_sha256",
    )
    @classmethod
    def digests(cls, value: str | None) -> str | None:
        return validate_sha256(value) if value is not None else None

    def canonical_identity(self) -> Any:
        value = self.model_dump(mode="json")
        value.pop("provider_result_sha256", None)
        return value

    @model_validator(mode="after")
    def verified(self) -> Self:
        if self.total_tokens != self.input_tokens + self.output_tokens:
            raise ValueError("Provider result token accounting is inconsistent.")
        if self.completed_monotonic_seconds < self.started_monotonic_seconds:
            raise ValueError("Provider completion precedes its start evidence.")
        if self.provider_result_sha256 != self.fingerprint:
            raise ValueError("Provider result hash was not recomputed.")
        return self


class ProviderTelemetryEvent(CanonicalModel):
    schema_version: str = TELEMETRY_SCHEMA
    repair_run_identifier: str
    attempt_identifier: str
    provider_invocation_identifier: str
    sequence: int = Field(ge=0)
    event_type: str
    status: str
    model_identifier: str | None = None
    agent_descriptor_sha256: str | None = None
    prompt_sha256: str | None = None
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    tool_call_count: int = Field(default=0, ge=0)
    patch_sha256: str | None = None
    elapsed_seconds: float = Field(default=0.0, ge=0)


T = TypeVar("T", bound=CanonicalModel)


def seal_contract(model_type: type[T], values: dict[str, Any], hash_field: str) -> T:
    """Construct a self-hashed immutable provider evidence contract."""
    provisional_values = {**values, hash_field: "0" * 64}
    provisional = model_type.model_construct(**provisional_values)  # type: ignore[arg-type]
    values = {
        **values,
        hash_field: sha256_fingerprint(provisional.canonical_identity()),
    }
    return model_type.model_validate(values)
