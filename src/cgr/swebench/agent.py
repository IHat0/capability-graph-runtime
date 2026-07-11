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


MIN_COMPLETION_TOKENS = 256
INITIAL_FILE_LIMIT = 80
TOOL_TEXT_LIMIT = 4_000


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


class ContextBudgetError(AgentResponseError):
    """Raised before a provider request that cannot fit the configured context."""


@dataclass(frozen=True)
class AgentRunResult:
    steps: int
    calls: int
    finished: bool
    stop_reason: str
    final_patch_size: int
    debug_trace: list[dict[str, str]]


@dataclass(frozen=True)
class ActionDefinition:
    """The one canonical schema, prompt, and dispatch contract for an action."""

    required: frozenset[str]
    optional: frozenset[str]
    example: dict[str, Any]


ACTION_DEFINITIONS: dict[str, ActionDefinition] = {
    "list_files": ActionDefinition(frozenset(), frozenset({"limit"}), {"action": "list_files"}),
    "search_text": ActionDefinition(
        frozenset({"pattern"}), frozenset({"limit"}), {"action": "search_text", "pattern": "def add"}
    ),
    "read_file": ActionDefinition(
        frozenset({"path"}), frozenset({"start", "end"}), {"action": "read_file", "path": "src/app.py"}
    ),
    "inspect_symbols": ActionDefinition(
        frozenset({"path"}), frozenset(), {"action": "inspect_symbols", "path": "src/app.py"}
    ),
    "write_file": ActionDefinition(
        frozenset({"path", "content"}), frozenset(), {"action": "write_file", "path": "src/app.py", "content": "VALUE = 1\\n"}
    ),
    "replace_text": ActionDefinition(
        frozenset({"path", "old", "new"}), frozenset(), {"action": "replace_text", "path": "src/app.py", "old": "old", "new": "new"}
    ),
    "apply_patch": ActionDefinition(
        frozenset({"patch"}), frozenset(), {"action": "apply_patch", "patch": "diff --git a/a.py b/a.py\\n..."}
    ),
    "run_tests": ActionDefinition(
        frozenset({"command"}), frozenset({"timeout"}), {"action": "run_tests", "command": ["pytest", "-q"]}
    ),
    "inspect_diff": ActionDefinition(frozenset(), frozenset(), {"action": "inspect_diff"}),
    "revert": ActionDefinition(frozenset(), frozenset(), {"action": "revert"}),
    "finish": ActionDefinition(frozenset(), frozenset(), {"action": "finish"}),
}

ACTION_ALIASES = {
    "edit_file": "replace_text",
    "grep": "search_text",
    "git_diff": "inspect_diff",
    "revert_candidate": "revert",
    "done": "finish",
}

# Kept as a derived compatibility view; ACTION_DEFINITIONS is the source of truth.
ACTION_SCHEMA = {
    name: (set(definition.required), set(definition.optional))
    for name, definition in ACTION_DEFINITIONS.items()
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
                raise AgentResponseError(
                    f"Model call failed: {_redact(str(exc), self._config)}", debug_trace
                ) from exc
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
                            "content": _correction_prompt(),
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
                    raise AgentResponseError(
                        f"Model correction call failed: {_redact(str(retry_call_exc), self._config)}",
                        debug_trace,
                    ) from retry_call_exc
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
        files = self._actions.list_files(limit=INITIAL_FILE_LIMIT + 1)
        files_truncated = len(files) > INITIAL_FILE_LIMIT
        if files_truncated:
            files = files[:INITIAL_FILE_LIMIT]
        problem = _compact_problem_statement(self._problem_statement)
        file_notice = (
            "Initial files (truncated; use list_files to inspect more):"
            if files_truncated
            else "Initial files:"
        )
        messages = [
            {
                "role": "system",
                "content": _system_prompt(),
            },
            {
                "role": "user",
                "content": (
                    f"Mode: {self._mode}\nProblem:\n{problem}\n\n"
                    f"{file_notice}\n{json.dumps(files)}"
                ),
            },
        ]
        _completion_budget(messages, self._config)
        return messages

    def _execute_action(self, action: dict[str, Any]) -> dict[str, Any]:
        try:
            name = action["action"]
            if name not in ACTION_DEFINITIONS:
                raise ValueError(f"Unsupported canonical action: {name}")
            if name == "list_files":
                return {"ok": True, "files": self._actions.list_files(_limit(action))}
            if name == "search_text":
                limit = _limit(action)
                matches = self._actions.search_text(_string(action, "pattern"), limit)
                return {
                    "ok": True,
                    "matches": matches,
                    "truncated": len(matches) >= limit,
                    "notice": "Search output may be truncated; narrow the pattern to inspect more."
                    if len(matches) >= limit
                    else None,
                }
            if name == "read_file":
                start = _positive(action, "start", 1)
                end = _positive(action, "end", 400)
                return {
                    "ok": True,
                    "content": self._actions.read_file(
                        _string(action, "path"), start, end
                    ),
                    "truncated": end - start >= 399,
                    "notice": "Read output is line-bounded; request another range to inspect more."
                    if end - start >= 399
                    else None,
                }
            if name == "inspect_symbols":
                return {"ok": True, "symbols": self._actions.inspect_symbols(_string(action, "path"))}
            if name == "write_file":
                self._actions.write_file(_string(action, "path"), _string(action, "content"))
                return {"ok": True}
            if name == "replace_text":
                self._actions.replace_text(
                    _string(action, "path"), _string(action, "old"), _string(action, "new")
                )
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
                    "stdout": _bounded_tool_text(result.stdout),
                    "stderr": _bounded_tool_text(result.stderr),
                }
            if name == "inspect_diff":
                return {"ok": True, "diff": _bounded_tool_text(self._actions.git_diff())}
            if name == "revert":
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
    max_tokens = _completion_budget(messages, config)
    try:
        response = client.create_chat_completion(
            config, messages, response_format={"type": "json_object"}, max_tokens=max_tokens
        )
    except RuntimeError as exc:
        if not _is_response_format_rejection(exc):
            raise
        response = client.create_chat_completion(config, messages, max_tokens=max_tokens)
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
    action = _normalize_action_alias(action)
    name = action.get("action")
    if not isinstance(name, str):
        raise ActionValidationError("Model action is missing the required action name.")
    definition = ACTION_DEFINITIONS.get(name)
    if definition is None:
        raise ActionValidationError(
            f"Model action {name!r} is unsupported. Valid actions: {_canonical_action_names()}."
        )
    keys = set(action) - {"action"}
    if not definition.required.issubset(keys) or not keys.issubset(
        definition.required | definition.optional
    ):
        raise ActionValidationError(f"Model action {name!r} does not match the action schema.")
    _validate_action_types(action)
    return action


def _normalize_action_alias(action: dict[str, Any]) -> dict[str, Any]:
    name = action.get("action")
    if not isinstance(name, str):
        return action
    canonical = ACTION_ALIASES.get(name, name)
    return {**action, "action": canonical}


def _extract_json_object(response: str) -> str:
    stripped = response.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, re.DOTALL | re.IGNORECASE)
    return fenced.group(1).strip() if fenced is not None else stripped


def _canonical_action_names() -> str:
    return ", ".join(ACTION_DEFINITIONS)


def _estimate_prompt_tokens(messages: list[dict[str, str]]) -> int:
    """Use a conservative tokenizer-free bound before calling a provider."""
    characters = sum(len(message.get("content", "")) for message in messages)
    return max(1, (characters + 2) // 3) + (4 * len(messages))


def _completion_budget(messages: list[dict[str, str]], config: OpenAICompatibleChatConfig) -> int:
    prompt_tokens = _estimate_prompt_tokens(messages)
    available = config.max_model_len - prompt_tokens
    if available < MIN_COMPLETION_TOKENS:
        raise ContextBudgetError(
            "Prompt cannot fit the configured model context: "
            f"prompt_token_estimate={prompt_tokens}, max_model_len={config.max_model_len}, "
            f"minimum_completion_tokens={MIN_COMPLETION_TOKENS}."
        )
    return min(config.max_completion_tokens, available)


def _compact_problem_statement(problem: str) -> str:
    """Remove only nonsemantic whitespace; never truncate code or requirements."""
    compacted: list[str] = []
    in_fence = False
    blank_count = 0
    for raw_line in problem.splitlines():
        line = raw_line.rstrip()
        if line.strip().startswith("```"):
            in_fence = not in_fence
        if not in_fence and not line:
            blank_count += 1
            if blank_count > 1:
                continue
        else:
            blank_count = 0
        compacted.append(line)
    return "\n".join(compacted).strip()


def _bounded_tool_text(value: str) -> str:
    if len(value) <= TOOL_TEXT_LIMIT:
        return value
    return (
        value[:TOOL_TEXT_LIMIT]
        + f"\n[Output truncated at {TOOL_TEXT_LIMIT} characters; refine the request for more.]"
    )


def _system_prompt() -> str:
    schema = "; ".join(
        f"{name}({','.join(sorted(definition.required)) or '-'}"
        f"; optional={','.join(sorted(definition.optional)) or '-'})"
        for name, definition in ACTION_DEFINITIONS.items()
    )
    return (
        "Bounded repository repair agent. Return one JSON action only. No network, "
        "Git history, .git paths, or workspace escapes. Canonical actions: "
        f"{_canonical_action_names()}. Schemas: {schema}. "
        'Examples: {"action":"read_file","path":"src/app.py"}; '
        '{"action":"replace_text","path":"src/app.py","old":"x","new":"y"}. '
        "Use finish only after leaving a valid non-empty diff."
    )


def _correction_prompt() -> str:
    return (
        "Return only one valid JSON action object matching the required schema. "
        f"Valid canonical action names: {_canonical_action_names()}."
    )


def _validate_action_types(action: dict[str, Any]) -> None:
    name = action["action"]
    strings = {
        "search_text": {"pattern"},
        "read_file": {"path"},
        "inspect_symbols": {"path"},
        "write_file": {"path", "content"},
        "replace_text": {"path", "old", "new"},
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
