"""Explicit optional configuration for the SWE-agent model provider."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .contracts import ProviderBudget, SamplingParameters

REQUIRED_SWEAGENT_COMMIT = "0f3acafacabc0def8cc76b4e48acb4b6cf302cb9"
DEFAULT_MODEL = "Qwen/Qwen2.5-Coder-7B-Instruct"
DEFAULT_BASE_URL = "http://127.0.0.1:8000/v1"


class SWEAgentProviderConfig(BaseModel):
    """Runtime configuration; API-key values are referenced, never stored here."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "cgr.quantum-repair-sweagent-provider-config/1.1.0"
    provider_type: Literal["sweagent-openai-compatible"] = "sweagent-openai-compatible"
    base_url: str = DEFAULT_BASE_URL
    model_identifier: str = DEFAULT_MODEL
    api_key_environment_variable: str = "CGR_REPAIR_MODEL_API_KEY"
    request_timeout_seconds: int = Field(default=120, gt=0, le=300)
    sampling: SamplingParameters = Field(default_factory=SamplingParameters)
    budget: ProviderBudget = Field(default_factory=ProviderBudget)
    sweagent_source: Path = Path(".swe-agent-src")
    sweagent_executable: str = "sweagent"
    required_sweagent_commit: str = REQUIRED_SWEAGENT_COMMIT
    sweagent_version: str = "1.1.0"
    tool_container_image_repository: str = "cgr-quantum-sweagent-tool"
    tool_container_image: str = "sha256:" + "0" * 64
    tool_image_build_schema_version: str = "cgr.quantum-sweagent-tool-image-build/1.0.0"
    tool_image_build_input_sha256: str = "0" * 64
    tool_image_offline_bootstrap: Literal[True] = True
    tool_container_network_policy: Literal["none"] = "none"
    required_swerex_version: Literal["1.4.0"] = "1.4.0"
    tool_startup_timeout_seconds: int = Field(default=180, gt=0, le=600)
    tool_container_pull_policy: Literal["never"] = "never"
    guidance_mode: Literal["baseline", "cgr"] = "cgr"
    source_context_maximum_bytes: int = Field(default=96 * 1024, gt=0, le=512 * 1024)
    heartbeat_seconds: int = Field(default=5, gt=0, le=60)
    lease_seconds: int = Field(default=30, gt=5, le=300)

    @field_validator("schema_version")
    @classmethod
    def valid_schema(cls, value: str) -> str:
        if value != "cgr.quantum-repair-sweagent-provider-config/1.1.0":
            raise ValueError("Unsupported SWE-agent provider configuration schema.")
        return value

    @field_validator("tool_container_image")
    @classmethod
    def immutable_tool_image(cls, value: str) -> str:
        if not value.startswith("sha256:") or len(value) != 71:
            raise ValueError("Tool container image must be an exact sha256 image ID.")
        if any(character not in "0123456789abcdef" for character in value[7:]):
            raise ValueError("Tool container image ID is malformed.")
        return value

    @field_validator("tool_image_build_input_sha256")
    @classmethod
    def build_input_digest(cls, value: str) -> str:
        if len(value) != 64 or any(
            character not in "0123456789abcdef" for character in value
        ):
            raise ValueError("Tool image build-input identity is malformed.")
        return value

    @field_validator("api_key_environment_variable")
    @classmethod
    def safe_environment_name(cls, value: str) -> str:
        if not value.startswith("CGR_REPAIR_") or not value.replace("_", "").isalnum():
            raise ValueError(
                "Provider API-key environment name is outside the CGR namespace."
            )
        return value

    @field_validator("required_sweagent_commit")
    @classmethod
    def pinned_commit(cls, value: str) -> str:
        if value != REQUIRED_SWEAGENT_COMMIT:
            raise ValueError("SWE-agent v1 requires the reviewed pristine commit.")
        return value

    @model_validator(mode="after")
    def bounded_lease(self) -> Self:
        if self.lease_seconds <= self.heartbeat_seconds:
            raise ValueError("Provider lease must exceed its heartbeat interval.")
        return self

    def api_key(self, environment: dict[str, str] | None = None) -> str:
        values = os.environ if environment is None else environment
        value = values.get(self.api_key_environment_variable, "")
        if not value:
            raise ValueError(
                f"{self.api_key_environment_variable} must be set for model access."
            )
        return value


def load_provider_config(
    path: Path | None, environment: dict[str, str] | None = None
) -> SWEAgentProviderConfig:
    """Load JSON configuration, with explicit environment overrides for endpoint fields."""
    values = os.environ if environment is None else environment
    payload: dict[str, object] = {}
    if path is not None:
        payload = json.loads(path.read_text(encoding="utf-8"))
    overrides = {
        "base_url": values.get("CGR_REPAIR_MODEL_BASE_URL"),
        "model_identifier": values.get("CGR_REPAIR_MODEL_ID"),
        "request_timeout_seconds": values.get("CGR_REPAIR_MODEL_TIMEOUT_SECONDS"),
        "sweagent_source": values.get("CGR_SWE_AGENT_SOURCE"),
        "sweagent_executable": values.get("CGR_SWE_AGENT_EXECUTABLE"),
        "tool_container_image": values.get("CGR_SWEAGENT_TOOL_IMAGE"),
        "tool_image_build_input_sha256": values.get(
            "CGR_SWEAGENT_TOOL_BUILD_INPUT_SHA256"
        ),
    }
    for key, value in overrides.items():
        if value not in (None, ""):
            payload[key] = int(value) if key == "request_timeout_seconds" else value
    return SWEAgentProviderConfig.model_validate(payload)
