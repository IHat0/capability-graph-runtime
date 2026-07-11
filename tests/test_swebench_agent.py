import json
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from cgr.plugins.providers.openai_compatible.chat_config import (
    OpenAICompatibleChatConfig,
)
from cgr.swebench.agent import (
    AgentResponseError,
    FirstPartyRepositoryAgent,
    parse_agent_args,
)


class ScriptedModel:
    def __init__(self, responses: Sequence[str]) -> None:
        self._responses = list(responses)
        self.messages: list[list[dict[str, str]]] = []

    def __call__(
        self, messages: list[dict[str, str]], _: OpenAICompatibleChatConfig
    ) -> str:
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


def _agent(workspace: Path, model: ScriptedModel, *, steps: int = 4, calls: int = 4) -> FirstPartyRepositoryAgent:
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
    agent = _agent(_workspace(tmp_path), ScriptedModel(["not action json"]))

    with pytest.raises(AgentResponseError, match="valid action JSON"):
        agent.run()


def test_agent_stops_at_max_calls(tmp_path: Path) -> None:
    agent = _agent(
        _workspace(tmp_path), ScriptedModel(['{"action":"list_files"}']), steps=5, calls=1
    )

    result = agent.run()

    assert result.calls == 1
    assert result.steps == 1
    assert result.finished is False
    assert result.stop_reason == "max_calls"


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
    assert (workspace / "app.py").read_text() == "VALUE = 3\n"


def test_agent_denies_git_metadata_patch_target(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    patch = "diff --git a/.git/config b/.git/config\n--- a/.git/config\n+++ b/.git/config\n"
    model = ScriptedModel(
        [json.dumps({"action": "apply_patch", "patch": patch}), '{"action":"finish"}']
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
            '{"action":"finish"}',
        ]
    )

    result = _agent(workspace, model).run()
    outcomes = [json.loads(messages[-1]["content"]) for messages in model.messages[1:4]]

    assert result.finished is True
    assert all(outcome["ok"] is False for outcome in outcomes)
    assert "escapes" in outcomes[0]["error"]
    assert ".git" in outcomes[1]["error"]
    assert "Network" in outcomes[2]["error"]
