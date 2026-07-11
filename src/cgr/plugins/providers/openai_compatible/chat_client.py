"""OpenAI-compatible chat client protocol and stdlib implementation."""

import json
from typing import Any, Protocol, runtime_checkable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .chat_config import OpenAICompatibleChatConfig


@runtime_checkable
class OpenAICompatibleChatClient(Protocol):
    """Client contract used by the provider-neutral chat plugin."""

    def create_chat_completion(
        self,
        config: OpenAICompatibleChatConfig,
        messages: list[dict[str, str]],
        response_format: dict[str, str] | None = None,
    ) -> dict[str, Any]: ...


class UrllibOpenAICompatibleChatClient:
    """Chat-completions client implemented with Python's standard library."""

    def create_chat_completion(
        self,
        config: OpenAICompatibleChatConfig,
        messages: list[dict[str, str]],
        response_format: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": config.model,
            "messages": messages,
            "temperature": 0,
            "top_p": 1,
        }
        if response_format is not None:
            payload["response_format"] = response_format
        body = json.dumps(payload).encode("utf-8")
        request = Request(
            f"{config.base_url.rstrip('/')}/chat/completions",
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
                "OpenAI-compatible chat request failed: "
                f"{exc.code} {error_body}"
            ) from exc
        except URLError as exc:
            raise RuntimeError(
                f"OpenAI-compatible chat request failed: {exc.reason}"
            ) from exc

        try:
            parsed = json.loads(response_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                "OpenAI-compatible chat API returned invalid JSON."
            ) from exc
        if not isinstance(parsed, dict):
            raise RuntimeError("OpenAI-compatible chat API returned invalid JSON.")
        return parsed
