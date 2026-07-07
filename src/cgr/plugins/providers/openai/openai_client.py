"""OpenAI-compatible Responses API client abstraction and stdlib client."""

import json
from typing import Any, Protocol, runtime_checkable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .openai_config import OpenAIProviderConfig


@runtime_checkable
class OpenAIResponsesClient(Protocol):
    """Client contract used by the OpenAI Responses model plugin."""

    def create_response(
        self,
        config: OpenAIProviderConfig,
        input_messages: list[dict[str, str]],
    ) -> dict[str, Any]:
        """Create one response from provider input messages."""
        ...


class UrllibOpenAIResponsesClient:
    """Responses API client implemented with Python's standard library."""

    def create_response(
        self,
        config: OpenAIProviderConfig,
        input_messages: list[dict[str, str]],
    ) -> dict[str, Any]:
        """POST a response request and return the decoded JSON object."""
        body = json.dumps(
            {"model": config.model, "input": input_messages}
        ).encode("utf-8")
        request = Request(
            f"{config.base_url.rstrip('/')}/responses",
            data=body,
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=config.timeout_seconds) as response:
                response_body = response.read()
        except HTTPError as exc:
            try:
                error_body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                error_body = ""
            raise RuntimeError(
                f"OpenAI Responses API request failed: {exc.code} {error_body}"
            ) from exc
        except URLError as exc:
            raise RuntimeError(
                f"OpenAI Responses API request failed: {exc.reason}"
            ) from exc

        try:
            parsed = json.loads(response_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                "OpenAI Responses API returned invalid JSON."
            ) from exc
        if not isinstance(parsed, dict):
            raise RuntimeError("OpenAI Responses API returned invalid JSON.")
        return parsed
