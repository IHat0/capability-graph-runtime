"""First-party bounded repository agent for the SWE-bench pilot."""

from __future__ import annotations

import argparse
import json
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

from .integration import MODES, RepositoryActions


ModelCall = Callable[[list[dict[str, str]], OpenAICompatibleChatConfig], str]


class AgentResponseError(RuntimeError):
    """Raised when the model does not produce one valid bounded action."""


@dataclass(frozen=True)
class AgentRunResult:
    steps: int
    calls: int
    finished: bool
    stop_reason: str


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
        while steps < self._max_steps and calls < self._max_calls:
            response = self._model_call(messages, self._config)
            calls += 1
            action = _parse_action(response)
            if action["action"] == "finish":
                return AgentRunResult(steps, calls, True, "finished")
            outcome = self._execute_action(action)
            steps += 1
            messages.extend(
                [
                    {"role": "assistant", "content": response},
                    {"role": "tool", "content": json.dumps(outcome)},
                ]
            )
        reason = "max_steps" if steps >= self._max_steps else "max_calls"
        return AgentRunResult(steps, calls, False, reason)

    def _initial_messages(self) -> list[dict[str, str]]:
        return [
            {
                "role": "system",
                "content": (
                    "You are a bounded repository repair agent. Operate only through "
                    "the JSON actions described below. Never request network access, "
                    "Git history, .git files, or paths outside the workspace. Return "
                    "one JSON object and no Markdown. Actions: list_files, search_text, "
                    "read_file, inspect_symbols, write_file, apply_patch, run_tests, "
                    "git_diff, revert_candidate, finish."
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
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    print(
        json.dumps(
            {
                "ok": True,
                "steps": result.steps,
                "calls": result.calls,
                "finished": result.finished,
                "stop_reason": result.stop_reason,
            }
        )
    )
    return 0


def _openai_model_call(
    messages: list[dict[str, str]], config: OpenAICompatibleChatConfig
) -> str:
    response = UrllibOpenAICompatibleChatClient().create_chat_completion(config, messages)
    return OpenAICompatibleChatPlugin._extract_text(response)


def _parse_action(response: str) -> dict[str, Any]:
    try:
        action = json.loads(response)
    except json.JSONDecodeError as exc:
        raise AgentResponseError("Model response was not valid action JSON.") from exc
    if not isinstance(action, dict) or not isinstance(action.get("action"), str):
        raise AgentResponseError("Model response did not contain an action string.")
    return action


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
