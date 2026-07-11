import json
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from cgr.plugins.providers.openai_compatible.chat_config import (
    OpenAICompatibleChatConfig,
)
from cgr.swebench.agent import (
    ActionParsingError,
    ActionValidationError,
    AgentResponseError,
    FirstPartyRepositoryAgent,
    ModelResponse,
    _openai_model_call,
    _parse_action,
    parse_agent_args,
)


class ScriptedModel:
    def __init__(self, responses: Sequence[str | ModelResponse]) -> None:
        self._responses = list(responses)
        self.messages: list[list[dict[str, str]]] = []

    def __call__(
        self, messages: list[dict[str, str]], _: OpenAICompatibleChatConfig
    ) -> str | ModelResponse:
        self.messages.append([dict(message) for message in messages])
        return self._responses.pop(0)


def _config() -> OpenAICompatibleChatConfig:
    return OpenAICompatibleChatConfig(
        api_key="local", model="qwen", base_url="http://localhost"
    )


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=workspace, check=True
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=workspace, check=True)
    (workspace / "app.py").write_text("VALUE = 1\n")
    subprocess.run(["git", "add", "app.py"], cwd=workspace, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=workspace, check=True)
    return workspace


def _agent(
    workspace: Path, model: ScriptedModel, *, steps: int = 4, calls: int = 4
) -> FirstPartyRepositoryAgent:
    return FirstPartyRepositoryAgent(
        workspace,
        "Fix the public issue.",
        "baseline",
        steps,
        calls,
        _config(),
        model,
    )


def test_agent_argument_parsing() -> None:
    args = parse_agent_args(
        [
            "--workspace",
            "repo",
            "--problem-file",
            "issue.txt",
            "--mode",
            "cgr_multi",
            "--max-steps",
            "12",
            "--max-calls",
            "8",
        ]
    )

    assert args.workspace == Path("repo")
    assert args.mode == "cgr_multi"
    assert args.max_steps == 12
    assert args.max_calls == 8


def test_agent_rejects_invalid_model_response(tmp_path: Path) -> None:
    agent = _agent(_workspace(tmp_path), ScriptedModel(["not action json"]), calls=1)

    with pytest.raises(AgentResponseError, match="invalid"):
        agent.run()


def test_action_parser_accepts_plain_and_single_fenced_json() -> None:
    assert _parse_action('  {"action": "finish"}  ') == {"action": "finish"}
    assert _parse_action('```json\n{"action": "finish"}\n```') == {"action": "finish"}
    assert _parse_action('```\n{"action": "finish"}\n```') == {"action": "finish"}


def test_action_parser_rejects_malformed_and_schema_invalid_json() -> None:
    with pytest.raises(ActionParsingError, match="valid action JSON"):
        _parse_action('{"action":')
    with pytest.raises(ActionValidationError, match="action schema"):
        _parse_action('{"action": "write_file", "path": "app.py"}')


def test_action_parser_rejects_arbitrary_prose_and_unknown_actions() -> None:
    with pytest.raises(ActionParsingError):
        _parse_action('Here is the action: {"action": "finish"}')
    with pytest.raises(ActionValidationError, match="unsupported"):
        _parse_action('{"action": "delete_repository"}')


def test_agent_uses_one_correction_retry_and_records_debug_trace(tmp_path: Path) -> None:
    model = ScriptedModel(
        [
            "not json local-secret",
            '```json\n{"action": "write_file", "path": "app.py", "content": "VALUE = 7\\n"}\n```',
            '{"action": "finish"}',
        ]
    )
    agent = _agent(_workspace(tmp_path), model, calls=3)

    result = agent.run()

    assert result.finished is True
    assert result.calls == 3
    assert any(item["event"] == "parsing_failure" for item in result.debug_trace)
    assert {"event": "correction_retry", "outcome": "succeeded"} in result.debug_trace
    assert "local-secret" not in json.dumps(result.debug_trace)
    assert "[REDACTED]" in json.dumps(result.debug_trace)
    assert "Return only one valid JSON action" in model.messages[1][-1]["content"]


def test_invalid_response_does_not_exceed_model_call_budget(tmp_path: Path) -> None:
    agent = _agent(_workspace(tmp_path), ScriptedModel(["not json"]), calls=1)

    with pytest.raises(AgentResponseError, match="budget is exhausted"):
        agent.run()


def test_agent_reports_failed_correction_retry(tmp_path: Path) -> None:
    agent = _agent(_workspace(tmp_path), ScriptedModel(["not json", "still not json"]), calls=2)

    with pytest.raises(AgentResponseError, match="valid action JSON") as raised:
        agent.run()

    assert {"event": "correction_retry", "outcome": "failed"} in raised.value.debug_trace


def test_openai_model_call_requests_json_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received: dict[str, object] = {}

    def fake_completion(
        self: object,
        config: OpenAICompatibleChatConfig,
        messages: list[dict[str, str]],
        response_format: dict[str, str] | None = None,
    ) -> dict[str, object]:
        received["response_format"] = response_format
        return {"choices": [{"message": {"content": '{"action":"finish"}'}}]}

    monkeypatch.setattr(
        "cgr.swebench.agent.UrllibOpenAICompatibleChatClient.create_chat_completion",
        fake_completion,
    )

    response = _openai_model_call([], _config())

    assert response == ModelResponse('{"action":"finish"}')
    assert received["response_format"] == {"type": "json_object"}


def test_openai_model_call_falls_back_when_provider_rejects_response_format(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    formats: list[dict[str, str] | None] = []

    def fake_completion(
        self: object,
        config: OpenAICompatibleChatConfig,
        messages: list[dict[str, str]],
        response_format: dict[str, str] | None = None,
    ) -> dict[str, object]:
        formats.append(response_format)
        if response_format is not None:
            raise RuntimeError("OpenAI-compatible chat request failed: 400 unsupported response_format")
        return {"choices": [{"message": {"content": '{"action":"finish"}'}}]}

    monkeypatch.setattr(
        "cgr.swebench.agent.UrllibOpenAICompatibleChatClient.create_chat_completion",
        fake_completion,
    )

    response = _openai_model_call([], _config())

    assert response.content == '{"action":"finish"}'
    assert response.response_format_fallback is True
    assert formats == [{"type": "json_object"}, None]


def test_agent_records_response_format_fallback(tmp_path: Path) -> None:
    model = ScriptedModel(
        [
            ModelResponse(
                '{"action": "write_file", "path": "app.py", "content": "VALUE = 8\\n"}',
                response_format_fallback=True,
            ),
            '{"action": "finish"}',
        ]
    )

    result = _agent(_workspace(tmp_path), model).run()

    assert {"event": "response_format_fallback", "outcome": "used"} in result.debug_trace


def test_agent_exits_nonzero_when_call_budget_is_exhausted(tmp_path: Path) -> None:
    agent = _agent(
        _workspace(tmp_path), ScriptedModel(['{"action":"list_files"}']), steps=5, calls=1
    )

    with pytest.raises(AgentResponseError, match="max_calls"):
        agent.run()


def test_agent_applies_unified_patch_inside_workspace(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    patch = (
        "diff --git a/app.py b/app.py\n"
        "index d9e9d1f..f0c545d 100644\n"
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1 +1 @@\n"
        "-VALUE = 1\n"
        "+VALUE = 2\n"
    )
    model = ScriptedModel(
        [json.dumps({"action": "apply_patch", "patch": patch}), '{"action":"finish"}']
    )

    result = _agent(workspace, model).run()

    assert result.finished is True
    assert result.final_patch_size > 0
    assert (workspace / "app.py").read_text() == "VALUE = 2\n"


def test_agent_leaves_successful_text_edit_in_workspace(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    model = ScriptedModel(
        [
            json.dumps({"action": "write_file", "path": "app.py", "content": "VALUE = 3\n"}),
            '{"action":"finish"}',
        ]
    )

    result = _agent(workspace, model).run()

    assert result.finished is True
    assert result.final_patch_size > 0
    assert (workspace / "app.py").read_text() == "VALUE = 3\n"


def test_agent_denies_git_metadata_patch_target(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    patch = "diff --git a/.git/config b/.git/config\n--- a/.git/config\n+++ b/.git/config\n"
    model = ScriptedModel(
        [
            json.dumps({"action": "apply_patch", "patch": patch}),
            json.dumps({"action": "write_file", "path": "app.py", "content": "VALUE = 4\n"}),
            '{"action":"finish"}',
        ]
    )

    result = _agent(workspace, model).run()
    outcome = json.loads(model.messages[1][-1]["content"])

    assert result.finished is True
    assert outcome["ok"] is False
    assert ".git" in outcome["error"]


def test_agent_denies_path_traversal_git_metadata_and_network_actions(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    model = ScriptedModel(
        [
            json.dumps({"action": "read_file", "path": "../outside.txt"}),
            json.dumps({"action": "read_file", "path": ".git/config"}),
            json.dumps({"action": "run_tests", "command": ["pytest", "https://bad"]}),
            json.dumps({"action": "write_file", "path": "app.py", "content": "VALUE = 5\n"}),
            '{"action":"finish"}',
        ]
    )

    result = _agent(workspace, model, steps=5, calls=5).run()
    outcomes = [json.loads(messages[-1]["content"]) for messages in model.messages[1:4]]

    assert result.finished is True
    assert all(outcome["ok"] is False for outcome in outcomes)
    assert "escapes" in outcomes[0]["error"]
    assert ".git" in outcomes[1]["error"]
    assert "Network" in outcomes[2]["error"]


def test_agent_rejects_finish_without_a_candidate_diff(tmp_path: Path) -> None:
    agent = _agent(_workspace(tmp_path), ScriptedModel(['{"action":"finish"}']))

    with pytest.raises(AgentResponseError, match="does not contain a valid repository diff"):
        agent.run()
