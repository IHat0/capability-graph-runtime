import json
from typing import Any

import pytest

from cgr.kernel.coding import JsonPatchParser
from cgr.kernel.contracts import ExecutionContext, ExecutionRequest
from cgr.kernel.runtime import KernelRuntime
from cgr.kernel.swe import SWEABRunner, create_local_swe_tasks
from cgr.plugins.agents import (
    MultiModelCodingAgentPlugin,
    SingleModelCodingAgentPlugin,
)
from cgr.plugins.providers.openai_compatible import (
    OpenAICompatibleChatConfig,
    OpenAICompatibleChatPlugin,
)


PATCH_TEXT = json.dumps(
    {
        "files": {"app.py": 'print("hello CGR")\n'},
        "explanation": "Updated greeting.",
    }
)


class SequencedClient:
    def __init__(self, critique: bool = False) -> None:
        self.critique = critique
        self.calls = 0

    def create_chat_completion(
        self,
        config: OpenAICompatibleChatConfig,
        messages: list[dict[str, str]],
    ) -> dict[str, Any]:
        self.calls += 1
        content = "No corrections needed." if self.critique else PATCH_TEXT
        return {"choices": [{"message": {"content": content}}]}


def _runtime_with_agents() -> tuple[KernelRuntime, SequencedClient, SequencedClient]:
    runtime = KernelRuntime()
    draft_client = SequencedClient()
    critic_client = SequencedClient(critique=True)
    draft = OpenAICompatibleChatPlugin(
        config=OpenAICompatibleChatConfig(
            api_key="local", model="draft", base_url="http://localhost"
        ),
        client=draft_client,
        capability_id="model.code",
        plugin_id="draft",
    )
    critic = OpenAICompatibleChatPlugin(
        config=OpenAICompatibleChatConfig(
            api_key="local", model="critic", base_url="http://localhost"
        ),
        client=critic_client,
        capability_id="model.reason",
        plugin_id="critic",
    )
    runtime.register_plugin(draft)
    runtime.register_plugin(critic)
    runtime.register_plugin(SingleModelCodingAgentPlugin(runtime))
    runtime.register_plugin(MultiModelCodingAgentPlugin(runtime))
    return runtime, draft_client, critic_client


def test_json_patch_parser_parses_raw_and_fenced_json() -> None:
    parser = JsonPatchParser()

    assert parser.parse(PATCH_TEXT).files["app.py"] == 'print("hello CGR")\n'
    assert parser.parse(f"```json\n{PATCH_TEXT}\n```").explanation == (
        "Updated greeting."
    )


def test_json_patch_parser_rejects_invalid_text() -> None:
    with pytest.raises(ValueError, match="valid coding patch"):
        JsonPatchParser().parse("not JSON")


def test_single_model_agent_calls_model_and_returns_patch() -> None:
    runtime, draft_client, _ = _runtime_with_agents()
    plugin = runtime.registry.get("agent.single_model_coding")

    result = runtime.execute(
        plugin.metadata.id,
        ExecutionRequest(
            capability=plugin.metadata.capabilities[0],
            context=ExecutionContext(),
            payload={
                "issue": "Change greeting.",
                "files": {"app.py": 'print("hello")\n'},
            },
        ),
    )

    assert result.output["files"] == {"app.py": 'print("hello CGR")\n'}
    assert draft_client.calls == 1


def test_multi_model_agent_performs_draft_critique_repair() -> None:
    runtime, draft_client, critic_client = _runtime_with_agents()
    plugin = runtime.registry.get("agent.multi_model_coding")

    result = runtime.execute(
        plugin.metadata.id,
        ExecutionRequest(
            capability=plugin.metadata.capabilities[0],
            context=ExecutionContext(),
            payload={
                "issue": "Change greeting.",
                "files": {"app.py": 'print("hello")\n'},
            },
        ),
    )

    assert result.output["files"] == {"app.py": 'print("hello CGR")\n'}
    assert draft_client.calls == 2
    assert critic_client.calls == 1


def test_swe_ab_runner_computes_all_mode_pass_rates() -> None:
    runtime, _, _ = _runtime_with_agents()

    result = SWEABRunner(runtime).run_suite(
        "test",
        create_local_swe_tasks(),
        "draft",
        "agent.single_model_coding",
        "agent.multi_model_coding",
    )

    assert result.pass_rates == {
        "baseline": pytest.approx(1 / 3),
        "cgr_single": pytest.approx(1 / 3),
        "cgr_multi": pytest.approx(1 / 3),
    }
    assert result.deltas == {
        "cgr_single_minus_baseline": 0.0,
        "cgr_multi_minus_baseline": 0.0,
    }
    assert len(result.results) == 9


def test_local_swe_suite_contains_three_distinct_tasks() -> None:
    tasks = create_local_swe_tasks()

    assert len(tasks) >= 3
    assert {task.id for task in tasks} >= {
        "local.greeting",
        "local.add",
        "local.is_even",
    }
