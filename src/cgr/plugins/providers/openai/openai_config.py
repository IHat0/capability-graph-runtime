"""Configuration for the OpenAI-compatible Responses API provider."""

import os

from pydantic import BaseModel, ConfigDict, Field


class OpenAIProviderConfig(BaseModel):
    """Immutable OpenAI provider configuration."""

    model_config = ConfigDict(frozen=True)

    api_key: str = Field(min_length=1, repr=False)
    model: str = Field(default="gpt-4.1-mini", min_length=1)
    base_url: str = Field(default="https://api.openai.com/v1", min_length=1)
    timeout_seconds: float = Field(default=60.0, gt=0)

    @classmethod
    def from_env(cls) -> "OpenAIProviderConfig":
        """Create configuration from OpenAI environment variables."""
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY is not set.")
        return cls(
            api_key=api_key,
            model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
            base_url=os.getenv(
                "OPENAI_BASE_URL",
                "https://api.openai.com/v1",
            ),
            timeout_seconds=float(os.getenv("OPENAI_TIMEOUT_SECONDS", "60.0")),
        )
