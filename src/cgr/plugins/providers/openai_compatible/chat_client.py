"""OpenAI-compatible chat client protocol and stdlib implementation."""

import json
from typing import Any, Protocol, runtime_checkable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .chat_config import OpenAICompatibleChatConfig


class OpenAICompatibleHTTPError(RuntimeError):
    """Provider HTTP failure with safe request-budget diagnostics."""

    def __init__(
        self, status: int, body: str, prompt_tokens: int, max_tokens: int
    ) -> None:
        self.status = status
        self.body = body
        self.prompt_tokens = prompt_tokens
        self.max_tokens = max_tokens
        super().__init__(
            "OpenAI-compatible chat request failed: "
            f"HTTP {status}; provider_body={body}; "
            f"prompt_token_estimate={prompt_tokens}; max_tokens={max_tokens}."
        )


@runtime_checkable
class OpenAICompatibleChatClient(Protocol):
    """Client contract used by the provider-neutral chat plugin."""

    def create_chat_completion(
        self,
        config: OpenAICompatibleChatConfig,
        messages: list[dict[str, str]],
        response_format: dict[str, str] | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]: ...


class UrllibOpenAICompatibleChatClient:
    """Chat-completions client implemented with Python's standard library."""

    def create_chat_completion(
        self,
        config: OpenAICompatibleChatConfig,
        messages: list[dict[str, str]],
        response_format: dict[str, str] | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": config.model,
            "messages": messages,
            "temperature": 0,
            "top_p": 1,
        }
        if response_format is not None:
            payload["response_format"] = response_format
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
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
            raise OpenAICompatibleHTTPError(
                exc.code,
                error_body.replace(config.api_key, "[REDACTED]"),
                _estimate_prompt_tokens(messages),
                max_tokens or 0,
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


def _estimate_prompt_tokens(messages: list[dict[str, str]]) -> int:
    """Conservative tokenizer-free estimate for provider error diagnostics."""
    characters = sum(len(message.get("content", "")) for message in messages)
    return max(1, (characters + 2) // 3) + (4 * len(messages))
