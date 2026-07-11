import json
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from cgr.plugins.providers.openai_compatible.chat_config import (
    OpenAICompatibleChatConfig,
)
from cgr.swebench.agent import (
    ACTION_ALIASES,
    ACTION_DEFINITIONS,
    ActionParsingError,
    ActionValidationError,
    AgentResponseError,
    ContextBudgetError,
    FirstPartyRepositoryAgent,
    ModelResponse,
    _openai_model_call,
    _parse_action,
    _completion_budget,
    _estimate_prompt_tokens,
    _system_prompt,
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
    workspace.mkdir(parents=True)
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
    workspace: Path,
    model: ScriptedModel,
    *,
    mode: str = "baseline",
    steps: int = 10,
    calls: int = 10,
) -> FirstPartyRepositoryAgent:
    return FirstPartyRepositoryAgent(
        workspace,
        "Fix the public issue.",
        mode,
        steps,
        calls,
        _config(),
        model,
    )


def _inspect_verify_finish_actions() -> list[str]:
    return [
        '{"action":"inspect_diff"}',
        '{"action":"run_tests","command":["python","--version"]}',
        '{"action":"finish"}',
    ]


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


def test_every_canonical_action_has_an_accepted_exact_schema() -> None:
    for name, definition in ACTION_DEFINITIONS.items():
        assert _parse_action(json.dumps(definition.example))["action"] == name


def test_every_safe_alias_is_normalized_before_validation() -> None:
    for alias, canonical in ACTION_ALIASES.items():
        action = {**ACTION_DEFINITIONS[canonical].example, "action": alias}

        assert _parse_action(json.dumps(action))["action"] == canonical


def test_action_parser_rejects_missing_action_and_incorrect_fields() -> None:
    with pytest.raises(ActionValidationError, match="missing"):
        _parse_action('{"path": "app.py"}')
    with pytest.raises(ActionValidationError, match="action schema"):
        _parse_action('{"action": "finish", "extra": true}')
    with pytest.raises(ActionValidationError, match="invalid types"):
        _parse_action('{"action": "replace_text", "path": "app.py", "old": 1, "new": "x"}')


def test_system_prompt_and_schema_share_every_canonical_action() -> None:
    prompt = _system_prompt()

    for name in ACTION_DEFINITIONS:
        assert name in prompt
    assert '"action":"read_file"' in prompt
    assert '"action":"replace_text"' in prompt
    assert len(prompt) < 2_000


def test_prompt_and_completion_budget_fit_within_4096_tokens(tmp_path: Path) -> None:
    config = _config().model_copy(update={"max_model_len": 4096, "max_completion_tokens": 512})
    agent = FirstPartyRepositoryAgent(
        _workspace(tmp_path), "Fix the public issue.", "baseline", 4, 4, config, ScriptedModel([])
    )
    messages = agent._initial_messages()
    completion = _completion_budget(messages, config)

    assert _estimate_prompt_tokens(messages) + completion <= 4096
    assert completion == 512


def test_oversized_problem_fails_before_provider_call(tmp_path: Path) -> None:
    config = _config().model_copy(update={"max_model_len": 512, "max_completion_tokens": 256})
    agent = FirstPartyRepositoryAgent(
        _workspace(tmp_path), "requirement\n" * 5_000, "baseline", 4, 4, config, ScriptedModel([])
    )

    with pytest.raises(ContextBudgetError, match="Prompt cannot fit"):
        agent.run()


def test_agent_uses_one_correction_retry_and_records_debug_trace(tmp_path: Path) -> None:
    model = ScriptedModel(
        [
            "not json local-secret",
            '```json\n{"action": "read_file", "path": "app.py"}\n```',
            '{"action":"replace_text","path":"app.py","old":"VALUE = 1","new":"VALUE = 7"}',
            *_inspect_verify_finish_actions(),
        ]
    )
    agent = _agent(_workspace(tmp_path), model, calls=6)

    result = agent.run()

    assert result.finished is True
    assert result.successful_verification_commands[-1]["command"] == ["python", "--version"]
    assert result.files_read == ["app.py"]
    assert result.files_modified == ["app.py"]
    assert result.calls == 6
    assert any(item["event"] == "parsing_failure" for item in result.debug_trace)
    assert {"event": "correction_retry", "outcome": "succeeded"} in result.debug_trace
    assert "local-secret" not in json.dumps(result.debug_trace)
    assert "[REDACTED]" in json.dumps(result.debug_trace)
    assert "Return only one valid JSON action" in model.messages[1][-1]["content"]


def test_agent_corrects_unsupported_action_with_canonical_names(tmp_path: Path) -> None:
    model = ScriptedModel(
        [
            '{"action": "change_file", "path": "app.py"}',
            '{"action":"read_file","path":"app.py"}',
            '{"action":"replace_text","path":"app.py","old":"VALUE = 1","new":"VALUE = 9"}',
            *_inspect_verify_finish_actions(),
        ]
    )

    result = _agent(_workspace(tmp_path), model, calls=6).run()

    assert result.finished is True
    correction = model.messages[1][-1]["content"]
    assert "change_file" in result.debug_trace[1]["error"]
    assert "replace_text" in correction
    assert "inspect_diff" in correction


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
        max_tokens: int | None = None,
    ) -> dict[str, object]:
        received["response_format"] = response_format
        received["max_tokens"] = max_tokens
        return {"choices": [{"message": {"content": '{"action":"finish"}'}}]}

    monkeypatch.setattr(
        "cgr.swebench.agent.UrllibOpenAICompatibleChatClient.create_chat_completion",
        fake_completion,
    )

    response = _openai_model_call([], _config())

    assert response == ModelResponse('{"action":"finish"}')
    assert received["response_format"] == {"type": "json_object"}
    assert received["max_tokens"] == 512


def test_openai_model_call_falls_back_when_provider_rejects_response_format(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    formats: list[dict[str, str] | None] = []

    def fake_completion(
        self: object,
        config: OpenAICompatibleChatConfig,
        messages: list[dict[str, str]],
        response_format: dict[str, str] | None = None,
        max_tokens: int | None = None,
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
                '{"action":"read_file","path":"app.py"}',
                response_format_fallback=True,
            ),
            '{"action":"replace_text","path":"app.py","old":"VALUE = 1","new":"VALUE = 8"}',
            '{"action":"inspect_diff"}',
            '{"action":"run_tests","command":["python","--version"]}',
            '{"action":"finish"}',
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
        [
            '{"action":"read_file","path":"app.py"}',
            json.dumps({"action": "apply_patch", "patch": patch}),
            *_inspect_verify_finish_actions(),
        ]
    )

    result = _agent(workspace, model).run()

    assert result.finished is True
    assert result.final_patch_size > 0
    assert (workspace / "app.py").read_text() == "VALUE = 2\n"


def test_agent_allows_write_file_for_new_file(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    model = ScriptedModel(
        [
            json.dumps({"action": "write_file", "path": "new.py", "content": "VALUE = 3\n"}),
            *_inspect_verify_finish_actions(),
        ]
    )

    result = _agent(workspace, model).run()

    assert result.finished is True
    assert result.final_patch_size > 0
    assert result.successful_verification_commands[-1]["command"] == ["python", "--version"]
    assert (workspace / "new.py").read_text() == "VALUE = 3\n"


def test_agent_replace_text_action_edits_inside_workspace(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    model = ScriptedModel(
        [
            '{"action":"read_file","path":"app.py"}',
            json.dumps(
                {
                    "action": "replace_text",
                    "path": "app.py",
                    "old": "VALUE = 1",
                    "new": "VALUE = 6",
                }
            ),
            *_inspect_verify_finish_actions(),
        ]
    )

    result = _agent(workspace, model).run()

    assert result.finished is True
    assert (workspace / "app.py").read_text() == "VALUE = 6\n"


def test_rejected_finish_after_tests_is_returned_as_tool_result_and_recovers(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    model = ScriptedModel(
        [
            '{"action":"read_file","path":"app.py"}',
            '{"action":"replace_text","path":"app.py","old":"VALUE = 1","new":"VALUE = 2"}',
            '{"action":"run_tests","command":["python","--version"]}',
            '{"action":"finish"}',
            '{"action":"inspect_diff"}',
            '{"action":"finish"}',
        ]
    )

    result = _agent(workspace, model).run()
    finish_rejections = [
        json.loads(message[-1]["content"])
        for message in model.messages
        if message[-1]["role"] == "tool"
        and json.loads(message[-1]["content"]).get("action") == "finish"
    ]

    assert result.finished is True
    assert result.final_patch_size > 0
    assert finish_rejections[0]["ok"] is False
    assert finish_rejections[0]["required_next_actions"] == ["inspect_diff"]


def test_rejected_finish_before_tests_is_returned_as_tool_result_and_recovers(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    model = ScriptedModel(
        [
            '{"action":"read_file","path":"app.py"}',
            '{"action":"replace_text","path":"app.py","old":"VALUE = 1","new":"VALUE = 2"}',
            '{"action":"finish"}',
            '{"action":"run_tests","command":["python","--version"]}',
            '{"action":"inspect_diff"}',
            '{"action":"finish"}',
        ]
    )

    result = _agent(workspace, model).run()
    rejection = json.loads(model.messages[3][-1]["content"])

    assert result.finished is True
    assert result.final_patch_size > 0
    assert rejection["action"] == "finish"
    assert rejection["required_next_actions"] == ["inspect_diff", "run_tests"]


def test_cgr_single_enters_repair_phase_after_rejected_primary_finish(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    model = ScriptedModel(
        [
            '{"action":"read_file","path":"app.py"}',
            '{"action":"replace_text","path":"app.py","old":"VALUE = 1","new":"VALUE = 2"}',
            '{"action":"finish"}',
            '{"action":"run_tests","command":["python","--version"]}',
            '{"action":"inspect_diff"}',
            '{"action":"finish"}',
        ]
    )

    result = _agent(workspace, model, mode="cgr_single").run()

    assert result.finished is True
    assert result.repair_phase_entered is True
    assert result.orchestration_path == "cgr_single_repair"
    assert any(
        message["role"] == "user" and "CGR single repair phase" in message["content"]
        for message in model.messages[3]
    )
    assert {"event": "repair_phase_entered", "mode": "cgr_single", "finish_rejections": "1"} in result.debug_trace


def test_baseline_and_cgr_single_have_distinct_rejected_finish_paths(
    tmp_path: Path,
) -> None:
    baseline = _agent(
        _workspace(tmp_path / "baseline"),
        ScriptedModel(
            [
                '{"action":"read_file","path":"app.py"}',
                '{"action":"replace_text","path":"app.py","old":"VALUE = 1","new":"VALUE = 2"}',
                '{"action":"finish"}',
                '{"action":"run_tests","command":["python","--version"]}',
                '{"action":"inspect_diff"}',
                '{"action":"finish"}',
            ]
        ),
        mode="baseline",
    ).run()
    single = _agent(
        _workspace(tmp_path / "single"),
        ScriptedModel(
            [
                '{"action":"read_file","path":"app.py"}',
                '{"action":"replace_text","path":"app.py","old":"VALUE = 1","new":"VALUE = 2"}',
                '{"action":"finish"}',
                '{"action":"run_tests","command":["python","--version"]}',
                '{"action":"inspect_diff"}',
                '{"action":"finish"}',
            ]
        ),
        mode="cgr_single",
    ).run()

    assert baseline.orchestration_path == "baseline"
    assert baseline.repair_phase_entered is False
    assert single.orchestration_path == "cgr_single_repair"
    assert single.repair_phase_entered is True


def test_dispatcher_covers_every_non_finish_canonical_action(tmp_path: Path) -> None:
    class RecordingActions:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def list_files(self, limit: int) -> list[str]:
            self.calls.append("list_files")
            return []

        def search_text(self, pattern: str, limit: int) -> list[str]:
            self.calls.append("search_text")
            return []

        def read_file(self, path: str, start: int, end: int) -> str:
            self.calls.append("read_file")
            return ""

        def inspect_symbols(self, path: str) -> list[dict[str, str]]:
            self.calls.append("inspect_symbols")
            return []

        def write_file(self, path: str, content: str) -> None:
            self.calls.append("write_file")

        def replace_text(self, path: str, old: str, new: str) -> None:
            self.calls.append("replace_text")

        def apply_patch(self, patch: str) -> None:
            self.calls.append("apply_patch")

        def run_safe(self, command: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
            self.calls.append("run_tests")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        def git_diff(self) -> str:
            self.calls.append("inspect_diff")
            return ""

        def revert_candidate(self) -> None:
            self.calls.append("revert")

    agent = _agent(_workspace(tmp_path), ScriptedModel([]))
    actions = RecordingActions()
    agent._actions = actions  # type: ignore[assignment]

    for name, definition in ACTION_DEFINITIONS.items():
        if name != "finish":
            assert agent._execute_action(dict(definition.example))["ok"] is True

    assert set(actions.calls) == set(ACTION_DEFINITIONS) - {"finish"}


def test_agent_denies_git_metadata_patch_target(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    patch = "diff --git a/.git/config b/.git/config\n--- a/.git/config\n+++ b/.git/config\n"
    model = ScriptedModel(
        [
            json.dumps({"action": "apply_patch", "patch": patch}),
            '{"action":"read_file","path":"app.py"}',
            '{"action":"replace_text","path":"app.py","old":"VALUE = 1","new":"VALUE = 4"}',
            *_inspect_verify_finish_actions(),
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
            '{"action":"read_file","path":"app.py"}',
            '{"action":"replace_text","path":"app.py","old":"VALUE = 1","new":"VALUE = 5"}',
            *_inspect_verify_finish_actions(),
        ]
    )

    result = _agent(workspace, model, steps=8, calls=8).run()
    outcomes = [json.loads(messages[-1]["content"]) for messages in model.messages[1:4]]

    assert result.finished is True
    assert all(outcome["ok"] is False for outcome in outcomes)
    assert "escapes" in outcomes[0]["error"]
    assert ".git" in outcomes[1]["error"]
    assert "Network" in outcomes[2]["error"]


def test_agent_rejects_finish_without_a_candidate_diff(tmp_path: Path) -> None:
    agent = _agent(_workspace(tmp_path), ScriptedModel(['{"action":"finish"}']), calls=1)

    with pytest.raises(AgentResponseError, match="Inspect the final diff"):
        agent.run()


def _source_workspace(tmp_path: Path, content: str) -> Path:
    workspace = _workspace(tmp_path)
    (workspace / "app.py").write_text(content)
    subprocess.run(["git", "add", "app.py"], cwd=workspace, check=True)
    subprocess.run(["git", "commit", "-qm", "source"], cwd=workspace, check=True)
    return workspace


def test_catastrophic_existing_file_rewrite_is_rejected(tmp_path: Path) -> None:
    original = "".join(f"# line {index}\n" for index in range(300))
    workspace = _source_workspace(tmp_path, original)
    replacement = "".join(f"# replacement {index}\n" for index in range(15))
    model = ScriptedModel(
        [
            '{"action":"read_file","path":"app.py"}',
            json.dumps({"action": "replace_text", "path": "app.py", "old": original, "new": replacement}),
            *_inspect_verify_finish_actions(),
        ]
    )

    with pytest.raises(AgentResponseError, match="Catastrophic rewrite rejected"):
        _agent(workspace, model, calls=5).run()


def test_exact_destructive_write_invalid_tests_finish_sequence_is_rejected(
    tmp_path: Path,
) -> None:
    original = "".join(f"# keep {index}\n" for index in range(385))
    workspace = _source_workspace(tmp_path, original)
    replacement = "".join(f"# replacement {index}\n" for index in range(16))
    model = ScriptedModel(
        [
            json.dumps({"action": "write_file", "path": "app.py", "content": replacement}),
            '{"action":"run_tests","command":"pytest"}',
            '{"action":"finish"}',
        ]
    )

    with pytest.raises(AgentResponseError, match="Inspect the final diff") as raised:
        _agent(workspace, model, calls=3).run()

    assert (workspace / "app.py").read_text() == original
    assert raised.value.diagnostics["local_tests_invoked"] == []
    assert raised.value.diagnostics["local_verification_passed"] is False


def test_removing_unrelated_public_symbol_is_rejected(tmp_path: Path) -> None:
    original = (
        "def retained():\n    return 1\n\n"
        "def unrelated_public():\n    return 2\n\n"
        + "".join(f"# context {index}\n" for index in range(100))
    )
    workspace = _source_workspace(tmp_path, original)
    model = ScriptedModel(
        [
            '{"action":"read_file","path":"app.py"}',
            json.dumps(
                {
                    "action": "replace_text",
                    "path": "app.py",
                    "old": "def unrelated_public():\n    return 2\n\n",
                    "new": "",
                }
            ),
            *_inspect_verify_finish_actions(),
        ]
    )

    with pytest.raises(AgentResponseError, match="unrelated_public"):
        _agent(workspace, model, calls=5).run()


def test_overwriting_existing_file_with_write_file_is_rejected(tmp_path: Path) -> None:
    agent = _agent(_workspace(tmp_path), ScriptedModel([]))

    outcome = agent._execute_action(
        {"action": "write_file", "path": "app.py", "content": "VALUE = 9\n"}
    )

    assert outcome["ok"] is False
    assert "cannot overwrite" in outcome["error"]


def test_finish_requires_diff_inspection_and_successful_verification(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    no_diff_model = ScriptedModel(
        [
            '{"action":"read_file","path":"app.py"}',
            '{"action":"replace_text","path":"app.py","old":"VALUE = 1","new":"VALUE = 2"}',
            '{"action":"run_tests","command":["python","--version"]}',
            '{"action":"finish"}',
        ]
    )
    with pytest.raises(AgentResponseError, match="Inspect the final diff"):
        _agent(workspace, no_diff_model, calls=4).run()

    workspace = _workspace(tmp_path / "second")
    no_verify_model = ScriptedModel(
        [
            '{"action":"read_file","path":"app.py"}',
            '{"action":"replace_text","path":"app.py","old":"VALUE = 1","new":"VALUE = 2"}',
            '{"action":"inspect_diff"}',
            '{"action":"finish"}',
        ]
    )
    with pytest.raises(AgentResponseError, match="verification"):
        _agent(workspace, no_verify_model, calls=4).run()


def test_failed_local_test_prevents_finish(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    model = ScriptedModel(
        [
            '{"action":"read_file","path":"app.py"}',
            '{"action":"replace_text","path":"app.py","old":"VALUE = 1","new":"VALUE = 2"}',
            '{"action":"run_tests","command":["python","--version","--bad-option"]}',
            '{"action":"inspect_diff"}',
            '{"action":"finish"}',
        ]
    )

    with pytest.raises(AgentResponseError, match="verification"):
        _agent(workspace, model, calls=5).run()


def test_failed_local_test_requires_another_edit_before_successful_rerun(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    model = ScriptedModel(
        [
            '{"action":"read_file","path":"app.py"}',
            '{"action":"replace_text","path":"app.py","old":"VALUE = 1","new":"VALUE = 2"}',
            '{"action":"run_tests","command":["python","--version","--bad-option"]}',
            '{"action":"run_tests","command":["python","--version"]}',
            '{"action":"inspect_diff"}',
            '{"action":"finish"}',
        ]
    )

    with pytest.raises(AgentResponseError, match="verification"):
        _agent(workspace, model, calls=6).run()


def test_edit_after_successful_test_requires_testing_again(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    model = ScriptedModel(
        [
            '{"action":"read_file","path":"app.py"}',
            '{"action":"replace_text","path":"app.py","old":"VALUE = 1","new":"VALUE = 2"}',
            '{"action":"run_tests","command":["python","--version"]}',
            '{"action":"replace_text","path":"app.py","old":"VALUE = 2","new":"VALUE = 3"}',
            '{"action":"inspect_diff"}',
            '{"action":"finish"}',
        ]
    )

    with pytest.raises(AgentResponseError, match="verification"):
        _agent(workspace, model, calls=6).run()


def test_edit_after_inspect_diff_requires_inspection_again(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    model = ScriptedModel(
        [
            '{"action":"read_file","path":"app.py"}',
            '{"action":"replace_text","path":"app.py","old":"VALUE = 1","new":"VALUE = 2"}',
            '{"action":"inspect_diff"}',
            '{"action":"replace_text","path":"app.py","old":"VALUE = 2","new":"VALUE = 3"}',
            '{"action":"run_tests","command":["python","--version"]}',
            '{"action":"finish"}',
        ]
    )

    with pytest.raises(AgentResponseError, match="Inspect the final diff"):
        _agent(workspace, model, calls=6).run()


def test_379_line_deletion_is_rejected(tmp_path: Path) -> None:
    original = "".join(f"# original {index}\n" for index in range(385))
    workspace = _source_workspace(tmp_path, original)
    replacement = "".join(f"# tiny {index}\n" for index in range(16))
    model = ScriptedModel(
        [
            '{"action":"read_file","path":"app.py"}',
            json.dumps({"action": "replace_text", "path": "app.py", "old": original, "new": replacement}),
            *_inspect_verify_finish_actions(),
        ]
    )

    with pytest.raises(AgentResponseError, match="lines_deleted"):
        _agent(workspace, model, calls=5).run()


def test_focused_small_edit_with_passing_verification_and_diff_is_accepted(
    tmp_path: Path,
) -> None:
    workspace = _source_workspace(
        tmp_path, "def add(a, b):\n    return a - b\n\ndef helper():\n    return 1\n"
    )
    model = ScriptedModel(
        [
            '{"action":"read_file","path":"app.py"}',
            json.dumps(
                {
                    "action": "replace_text",
                    "path": "app.py",
                    "old": "return a - b",
                    "new": "return a + b",
                }
            ),
            '{"action":"run_tests","command":["python","-m","compileall","app.py"]}',
            '{"action":"inspect_diff"}',
            '{"action":"finish"}',
        ]
    )

    result = _agent(workspace, model).run()

    assert result.finished is True
    assert result.successful_verification_commands[-1]["command"] == [
        "python",
        "-m",
        "compileall",
        "app.py",
    ]
