"""First-party bounded repository agent for the SWE-bench pilot."""

from __future__ import annotations

import argparse
import json
import os
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cgr.plugins.providers.openai_compatible.chat_client import (
    UrllibOpenAICompatibleChatClient,
)
from cgr.plugins.providers.openai_compatible.chat_config import (
    OpenAICompatibleChatConfig,
)
from cgr.plugins.providers.openai_compatible.openai_compatible_chat_plugin import (
    OpenAICompatibleChatPlugin,
)

from .integration import MODES, RepositoryActions, capture_git_patch


@dataclass(frozen=True)
class ModelResponse:
    """Text returned by a model call plus safe transport diagnostics."""

    content: str
    response_format_fallback: bool = False


ModelCall = Callable[
    [list[dict[str, str]], OpenAICompatibleChatConfig], str | ModelResponse
]


class AgentResponseError(RuntimeError):
    """Raised when the model does not produce one valid bounded action."""

    def __init__(self, message: str, debug_trace: list[dict[str, str]] | None = None) -> None:
        super().__init__(message)
        self.debug_trace = debug_trace or []


class ActionParsingError(AgentResponseError):
    """Raised when an action response is not a JSON object."""


class ActionValidationError(AgentResponseError):
    """Raised when a JSON object is not in the bounded action schema."""


@dataclass(frozen=True)
class AgentRunResult:
    steps: int
    calls: int
    finished: bool
    stop_reason: str
    final_patch_size: int
    debug_trace: list[dict[str, str]]


ACTION_SCHEMA: dict[str, tuple[set[str], set[str]]] = {
    "list_files": (set(), {"limit"}),
    "search_text": ({"pattern"}, {"limit"}),
    "read_file": ({"path"}, {"start", "end"}),
    "inspect_symbols": ({"path"}, set()),
    "write_file": ({"path", "content"}, set()),
    "apply_patch": ({"patch"}, set()),
    "run_tests": ({"command"}, {"timeout"}),
    "git_diff": (set(), set()),
    "revert_candidate": (set(), set()),
    "finish": (set(), set()),
}


class FirstPartyRepositoryAgent:
    """Execute a bounded JSON-action trajectory inside one repository workspace."""

    def __init__(
        self,
        workspace: Path,
        problem_statement: str,
        mode: str,
        max_steps: int,
        max_calls: int,
        config: OpenAICompatibleChatConfig,
        model_call: ModelCall,
    ) -> None:
        if mode not in MODES:
            raise ValueError(f"Unsupported SWE-bench mode: {mode}")
        if max_steps <= 0 or max_calls <= 0:
            raise ValueError("max_steps and max_calls must be positive.")
        self._workspace = workspace.resolve()
        if not self._workspace.is_dir():
            raise ValueError(f"Workspace does not exist: {workspace}")
        self._actions = RepositoryActions(self._workspace)
        self._problem_statement = problem_statement
        self._mode = mode
        self._max_steps = max_steps
        self._max_calls = max_calls
        self._config = config
        self._model_call = model_call

    def run(self) -> AgentRunResult:
        messages = self._initial_messages()
        steps = 0
        calls = 0
        debug_trace: list[dict[str, str]] = []
        while steps < self._max_steps and calls < self._max_calls:
            try:
                response = self._request_model(messages, debug_trace)
            except RuntimeError as exc:
                debug_trace.append({"event": "model_call_failure", "error": _redact(str(exc), self._config)})
                raise AgentResponseError("Model call failed.", debug_trace) from exc
            calls += 1
            _record_raw_response(debug_trace, response, self._config)
            try:
                action = _parse_action(response)
            except AgentResponseError as exc:
                _record_failure(debug_trace, exc)
                if calls >= self._max_calls:
                    raise AgentResponseError(
                        "Model response was invalid and the model-call budget is exhausted.",
                        debug_trace,
                    ) from exc
                messages.extend(
                    [
                        {"role": "assistant", "content": response},
                        {
                            "role": "user",
                            "content": (
                                "Your previous response was invalid. Return only one "
                                "valid JSON action object matching the required schema."
                            ),
                        },
                    ]
                )
                debug_trace.append({"event": "correction_retry", "outcome": "requested"})
                try:
                    retry = self._request_model(messages, debug_trace)
                except RuntimeError as retry_call_exc:
                    debug_trace.append(
                        {
                            "event": "model_call_failure",
                            "error": _redact(str(retry_call_exc), self._config),
                        }
                    )
                    debug_trace.append({"event": "correction_retry", "outcome": "failed"})
                    raise AgentResponseError("Model correction call failed.", debug_trace) from retry_call_exc
                calls += 1
                _record_raw_response(debug_trace, retry, self._config)
                try:
                    action = _parse_action(retry)
                except AgentResponseError as retry_exc:
                    _record_failure(debug_trace, retry_exc)
                    debug_trace.append({"event": "correction_retry", "outcome": "failed"})
                    raise AgentResponseError(str(retry_exc), debug_trace) from retry_exc
                debug_trace.append({"event": "correction_retry", "outcome": "succeeded"})
                response = retry
            if action["action"] == "finish":
                try:
                    patch, _ = capture_git_patch(self._workspace)
                except ValueError as exc:
                    raise AgentResponseError(
                        f"Final workspace does not contain a valid repository diff: {exc}",
                        debug_trace,
                    ) from exc
                return AgentRunResult(steps, calls, True, "finished", len(patch.encode()), debug_trace)
            outcome = self._execute_action(action)
            steps += 1
            messages.extend(
                [
                    {"role": "assistant", "content": response},
                    {"role": "tool", "content": json.dumps(outcome)},
                ]
            )
        reason = "max_steps" if steps >= self._max_steps else "max_calls"
        raise AgentResponseError(
            f"Repository agent stopped because {reason} was exhausted.", debug_trace
        )

    def _request_model(
        self, messages: list[dict[str, str]], debug_trace: list[dict[str, str]]
    ) -> str:
        result = self._model_call(messages, self._config)
        if isinstance(result, ModelResponse):
            if result.response_format_fallback:
                debug_trace.append({"event": "response_format_fallback", "outcome": "used"})
            return result.content
        return result

    def _initial_messages(self) -> list[dict[str, str]]:
        return [
            {
                "role": "system",
                "content": (
                    "You are a bounded repository repair agent. Operate only through "
                    "the JSON actions described below. Never request network access, "
                    "Git history, .git files, or paths outside the workspace. Return "
                    "one JSON object and no Markdown. Action schema: list_files accepts "
                    "optional positive limit; search_text requires pattern and optional "
                    "positive limit; read_file requires path and optional positive start/end; "
                    "inspect_symbols requires path; write_file requires path and content; "
                    "apply_patch requires patch; run_tests requires command as a list of "
                    "strings and optional positive timeout; git_diff, revert_candidate, "
                    "and finish take no fields."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Mode: {self._mode}\nProblem:\n{self._problem_statement}\n\n"
                    f"Initial files:\n{json.dumps(self._actions.list_files(limit=500))}"
                ),
            },
        ]

    def _execute_action(self, action: dict[str, Any]) -> dict[str, Any]:
        try:
            name = action["action"]
            if name == "list_files":
                return {"ok": True, "files": self._actions.list_files(_limit(action))}
            if name == "search_text":
                return {
                    "ok": True,
                    "matches": self._actions.search_text(_string(action, "pattern"), _limit(action)),
                }
            if name == "read_file":
                return {
                    "ok": True,
                    "content": self._actions.read_file(
                        _string(action, "path"), _positive(action, "start", 1), _positive(action, "end", 400)
                    ),
                }
            if name == "inspect_symbols":
                return {"ok": True, "symbols": self._actions.inspect_symbols(_string(action, "path"))}
            if name == "write_file":
                self._actions.write_file(_string(action, "path"), _string(action, "content"))
                return {"ok": True}
            if name == "apply_patch":
                self._actions.apply_patch(_string(action, "patch"))
                return {"ok": True}
            if name == "run_tests":
                command = action.get("command")
                if not isinstance(command, list) or not all(isinstance(item, str) for item in command):
                    raise ValueError("command must be a list of strings")
                _deny_network_command(command)
                result = self._actions.run_safe(command, timeout=_positive(action, "timeout", 600))
                return {
                    "ok": result.returncode == 0,
                    "exit_code": result.returncode,
                    "stdout": result.stdout[-4000:],
                    "stderr": result.stderr[-4000:],
                }
            if name == "git_diff":
                return {"ok": True, "diff": self._actions.git_diff()[-8000:]}
            if name == "revert_candidate":
                self._actions.revert_candidate()
                return {"ok": True}
            raise ValueError(f"Unsupported action: {name}")
        except (OSError, RuntimeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}


def parse_agent_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run CGR's bounded SWE-bench agent.")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--problem-file", type=Path, required=True)
    parser.add_argument("--mode", choices=MODES, required=True)
    parser.add_argument("--max-steps", type=_positive_int, required=True)
    parser.add_argument("--max-calls", type=_positive_int, required=True)
    return parser.parse_args(argv)


def agent_main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point used by `CGR_SWEBENCH_AGENT_COMMAND`."""
    try:
        args = parse_agent_args(argv)
        problem_statement = args.problem_file.read_text(encoding="utf-8")
        config = OpenAICompatibleChatConfig.from_env("CGR_DRAFT")
        result = FirstPartyRepositoryAgent(
            args.workspace,
            problem_statement,
            args.mode,
            args.max_steps,
            args.max_calls,
            config,
            _openai_model_call,
        ).run()
    except (AgentResponseError, OSError, RuntimeError, ValueError) as exc:
        payload: dict[str, Any] = {"ok": False, "error": str(exc)}
        if os.getenv("CGR_SWEBENCH_DEBUG_TRACE") == "1":
            payload["debug_trace"] = getattr(exc, "debug_trace", [])
        print(json.dumps(payload))
        return 1
    print(
        json.dumps(
            {
                "ok": True,
                "steps": result.steps,
                "calls": result.calls,
                "finished": result.finished,
                "stop_reason": result.stop_reason,
                "final_patch_size": result.final_patch_size,
                "debug_trace": result.debug_trace
                if os.getenv("CGR_SWEBENCH_DEBUG_TRACE") == "1"
                else None,
            }
        )
    )
    return 0


def _openai_model_call(
    messages: list[dict[str, str]], config: OpenAICompatibleChatConfig
) -> ModelResponse:
    client = UrllibOpenAICompatibleChatClient()
    try:
        response = client.create_chat_completion(
            config, messages, response_format={"type": "json_object"}
        )
    except RuntimeError as exc:
        if not _is_response_format_rejection(exc):
            raise
        response = client.create_chat_completion(config, messages)
        return ModelResponse(
            OpenAICompatibleChatPlugin._extract_text(response), response_format_fallback=True
        )
    return ModelResponse(OpenAICompatibleChatPlugin._extract_text(response))


def _is_response_format_rejection(error: RuntimeError) -> bool:
    detail = str(error).casefold()
    return "response_format" in detail and any(
        marker in detail
        for marker in ("unsupported", "not support", "unknown", "invalid", "400")
    )


def _parse_action(response: str) -> dict[str, Any]:
    normalized = _extract_json_object(response)
    try:
        action = json.loads(normalized)
    except json.JSONDecodeError as exc:
        raise ActionParsingError("Model response was not valid action JSON.") from exc
    if not isinstance(action, dict):
        raise ActionValidationError("Model action must be a JSON object.")
    name = action.get("action")
    if not isinstance(name, str) or name not in ACTION_SCHEMA:
        raise ActionValidationError("Model action is missing or unsupported.")
    required, optional = ACTION_SCHEMA[name]
    keys = set(action) - {"action"}
    if not required.issubset(keys) or not keys.issubset(required | optional):
        raise ActionValidationError("Model action does not match the action schema.")
    _validate_action_types(action)
    return action


def _extract_json_object(response: str) -> str:
    stripped = response.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, re.DOTALL | re.IGNORECASE)
    return fenced.group(1).strip() if fenced is not None else stripped


def _validate_action_types(action: dict[str, Any]) -> None:
    name = action["action"]
    strings = {
        "search_text": {"pattern"},
        "read_file": {"path"},
        "inspect_symbols": {"path"},
        "write_file": {"path", "content"},
        "apply_patch": {"patch"},
    }
    if name in strings and any(not isinstance(action[key], str) for key in strings[name]):
        raise ActionValidationError("Model action string fields have invalid types.")
    if name == "run_tests":
        command = action["command"]
        if not isinstance(command, list) or not all(isinstance(item, str) for item in command):
            raise ActionValidationError("run_tests command must be a list of strings.")
    for key in {"limit", "start", "end", "timeout"} & set(action):
        if not isinstance(action[key], int) or isinstance(action[key], bool) or action[key] <= 0:
            raise ActionValidationError(f"Model action field {key} must be positive.")


def _record_raw_response(
    debug_trace: list[dict[str, str]], response: str, config: OpenAICompatibleChatConfig
) -> None:
    debug_trace.append(
        {"event": "raw_model_response", "response": _redact(response, config)}
    )


def _record_failure(debug_trace: list[dict[str, str]], error: AgentResponseError) -> None:
    event = "parsing_failure" if isinstance(error, ActionParsingError) else "validation_failure"
    debug_trace.append({"event": event, "error": str(error)})


def _redact(value: str, config: OpenAICompatibleChatConfig) -> str:
    return value.replace(config.api_key, "[REDACTED]")


def _string(action: dict[str, Any], key: str) -> str:
    value = action.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _positive(action: dict[str, Any], key: str, default: int) -> int:
    value = action.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{key} must be a positive integer")
    return value


def _limit(action: dict[str, Any]) -> int:
    return min(_positive(action, "limit", 200), 2000)


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def _deny_network_command(command: Sequence[str]) -> None:
    joined = " ".join(command).casefold()
    if any(marker in joined for marker in ("http://", "https://", "curl", "wget", "git ", "pip ")):
        raise ValueError("Network and Git commands are forbidden in repository actions.")


if __name__ == "__main__":
    raise SystemExit(agent_main())
