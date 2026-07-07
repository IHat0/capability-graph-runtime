"""Configuration for OpenAI-compatible chat providers."""

import os

from pydantic import BaseModel, ConfigDict, Field


class OpenAICompatibleChatConfig(BaseModel):
    """Immutable connection configuration for a chat-completions provider."""

    model_config = ConfigDict(frozen=True)

    api_key: str = Field(min_length=1, repr=False)
    model: str = Field(min_length=1)
    base_url: str = Field(min_length=1)
    timeout_seconds: float = Field(default=60.0, gt=0)
    provider_name: str = Field(default="openai_compatible", min_length=1)

    @classmethod
    def from_env(cls, prefix: str = "CGR_MODEL") -> "OpenAICompatibleChatConfig":
        """Load provider configuration from a consistently prefixed environment."""
        values: dict[str, str] = {}
        for field_name, suffix in (
            ("api_key", "API_KEY"),
            ("model", "MODEL"),
            ("base_url", "BASE_URL"),
        ):
            variable = f"{prefix}_{suffix}"
            value = os.getenv(variable)
            if not value:
                raise ValueError(f"{variable} is not set.")
            values[field_name] = value

        timeout = os.getenv(f"{prefix}_TIMEOUT_SECONDS")
        if timeout is not None:
            values["timeout_seconds"] = timeout
        provider_name = os.getenv(f"{prefix}_PROVIDER_NAME")
        if provider_name is not None:
            values["provider_name"] = provider_name
        return cls.model_validate(values)
