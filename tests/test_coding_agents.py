import json
from typing import Any

import pytest

from cgr.kernel.coding import (
    CodeTestCase,
    CodingPatch,
    JsonPatchParser,
    select_patch,
)
from cgr.kernel.coding.hard_coding_suite import create_hard_coding_tasks
from cgr.kernel.contracts import ExecutionContext, ExecutionRequest
from cgr.kernel.runtime import KernelRuntime
from cgr.kernel.swe import SWEABRunner, SWETask, create_local_swe_tasks
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


def test_swe_runner_accepts_functionally_correct_text_different_code() -> None:
    class FunctionalClient:
        def create_chat_completion(
            self,
            config: OpenAICompatibleChatConfig,
            messages: list[dict[str, str]],
        ) -> dict[str, Any]:
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "files": {
                                        "math_utils.py": (
                                            "def add(a: float, b: float) -> float:\n"
                                            "    \"\"\"Return the sum.\"\"\"\n"
                                            "    return a + b\n"
                                        )
                                    },
                                    "explanation": "Functionally equivalent.",
                                }
                            )
                        }
                    }
                ]
            }

    runtime = KernelRuntime()
    plugin = OpenAICompatibleChatPlugin(
        config=OpenAICompatibleChatConfig(
            api_key="local", model="functional", base_url="http://localhost"
        ),
        client=FunctionalClient(),
        capability_id="model.code",
        plugin_id="functional",
    )
    runtime.register_plugin(plugin)
    task = SWETask(
        id="functional",
        issue="Fix add.",
        files={"math_utils.py": "def add(a, b):\n    return a - b\n"},
        expected_files={"math_utils.py": "def add(a, b):\n    return a + b\n"},
        test_files={
            "test_task.py": (
                "from math_utils import add\n"
                "assert add(1, 2) == 3\n"
                "assert add(-5, 5) == 0\n"
            )
        },
        test_commands=[
            CodeTestCase(name="functional", command=["python", "test_task.py"])
        ],
    )

    case = SWEABRunner(runtime)._run_case(task, "baseline", plugin.metadata.id)

    assert case.passed is True
    assert case.files != task.expected_files


class FeedbackRepairClient:
    def __init__(self, first_files: dict[str, str], repaired_files: dict[str, str]) -> None:
        self._responses = [first_files, repaired_files]
        self.prompts: list[str] = []

    def create_chat_completion(
        self,
        config: OpenAICompatibleChatConfig,
        messages: list[dict[str, str]],
    ) -> dict[str, Any]:
        self.prompts.append(messages[-1]["content"])
        files = self._responses[min(len(self.prompts) - 1, 1)]
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {"files": files, "explanation": "feedback repair"}
                        )
                    }
                }
            ]
        }


@pytest.mark.parametrize(
    "task_id",
    ["hard.parse_bool", "hard.merge_counts", "hard.validate_password"],
)
def test_single_agent_repairs_observed_bugs_from_test_feedback(task_id: str) -> None:
    task = next(task for task in create_hard_coding_tasks() if task.id == task_id)
    client = FeedbackRepairClient(task.files, task.expected_files)
    runtime = KernelRuntime()
    model = OpenAICompatibleChatPlugin(
        config=OpenAICompatibleChatConfig(
            api_key="local", model="feedback", base_url="http://localhost"
        ),
        client=client,
        capability_id="model.code",
        plugin_id="feedback.model",
    )
    runtime.register_plugin(model)
    agent = SingleModelCodingAgentPlugin(runtime)
    runtime.register_plugin(agent)

    result = runtime.execute(
        agent.metadata.id,
        ExecutionRequest(
            capability=agent.metadata.capabilities[0],
            context=ExecutionContext(),
            payload={
                "issue": task.issue,
                "files": task.files,
                "test_files": task.test_files,
                "test_commands": task.test_commands,
            },
        ),
    )

    assert result.output["files"] == task.expected_files
    assert len(client.prompts) == 2
    repair_prompt = client.prompts[1]
    assert "failed the tests below" in repair_prompt
    assert "exit code" in repair_prompt
    assert "Do not change the public API" in repair_prompt
    assert "Do not add extra return values" in repair_prompt


def test_passing_original_is_not_replaced_by_failing_repair() -> None:
    original = CodingPatch(files={"module.py": "def value():\n    return True\n"})
    repair = CodingPatch(files={"module.py": "def value():\n    return (True, 'ok')\n"})

    assert select_patch(original, True, repair, False) is original
    assert select_patch(original, False, repair, True) is repair
