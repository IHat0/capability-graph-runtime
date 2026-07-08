import json
from typing import Any

import pytest

from cgr.kernel.coding import (
    CodeTestCase,
    CodingPatch,
    JsonPatchParser,
    PythonTestRunner,
    extract_test_assertion_checklist,
    extract_test_io_examples,
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
        self.prompts: list[str] = []

    def create_chat_completion(
        self,
        config: OpenAICompatibleChatConfig,
        messages: list[dict[str, str]],
    ) -> dict[str, Any]:
        self.calls += 1
        self.prompts.append(messages[-1]["content"])
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
        debug_trace=True,
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
    multi_case = next(
        case
        for case in result.results
        if case.task_id == "local.greeting" and case.mode == "cgr_multi"
    )
    assert multi_case.repair_attempts_count == 0
    assert multi_case.candidates_count == 1
    assert multi_case.selected_candidate_id == "candidate_1"
    assert multi_case.candidate_scores is not None
    assert multi_case.candidate_file_previews is not None


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
        files = self._responses[
            min(len(self.prompts) - 1, len(self._responses) - 1)
        ]
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
    assert "current implementation failed tests" in repair_prompt
    assert "exit code" in repair_prompt
    assert "Do not change the public API" in repair_prompt
    assert "Do not add extra return values" in repair_prompt


def test_passing_original_is_not_replaced_by_failing_repair() -> None:
    original = CodingPatch(files={"module.py": "def value():\n    return True\n"})
    repair = CodingPatch(files={"module.py": "def value():\n    return (True, 'ok')\n"})

    assert select_patch(original, True, repair, False) is original
    assert select_patch(original, False, repair, True) is repair
    assert select_patch(original, False, repair, False) is original


def test_multi_agent_second_semantic_repair_fixes_merge_counts() -> None:
    task = next(
        task
        for task in create_hard_coding_tasks()
        if task.id == "hard.merge_counts"
    )
    overwrite = {
        "counter_utils.py": (
            "def merge_counts(a, b):\n    return {**a, **b}\n"
        )
    }
    draft_client = FeedbackRepairClient(overwrite, overwrite)
    draft_client._responses.append(task.expected_files)
    critic_client = SequencedClient(critique=True)
    runtime = KernelRuntime()
    config = OpenAICompatibleChatConfig(
        api_key="local", model="semantic", base_url="http://localhost"
    )
    draft = OpenAICompatibleChatPlugin(
        config=config,
        client=draft_client,
        capability_id="model.code",
        plugin_id="semantic.draft",
    )
    critic = OpenAICompatibleChatPlugin(
        config=config,
        client=critic_client,
        capability_id="model.reason",
        plugin_id="semantic.critic",
    )
    runtime.register_plugin(draft)
    runtime.register_plugin(critic)
    agent = MultiModelCodingAgentPlugin(runtime)
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
    trace = result.output["_trace"]
    assert trace["repair_attempts_count"] == 3
    assert trace["repair_variant_count"] == 3
    assert trace["candidates_count"] == 4
    assert trace["selected_candidate_id"] == "repair_2"
    assert trace["candidate_scores"] == {
        "candidate_1": 0.0,
        "repair_1": 0.0,
        "repair_2": 1.0,
        "repair_3": 1.0,
    }
    assert len(draft_client.prompts) == 4
    first_repair = draft_client.prompts[1]
    second_repair = draft_client.prompts[2]
    assert "expected {'x': 3, 'y': 3}, got {'x': 2, 'y': 3}" in first_repair
    assert "AssertionError" in first_repair
    assert "tests are the source of truth" in first_repair
    assert "Your previous repair still failed" in second_repair
    assert "Previous repair files" in second_repair
    assert trace["candidate_file_previews"]["repair_2"] == task.expected_files
    assert trace["repeated_candidate_rejections"] == 1
    assert trace["known_failing_candidate_ids"] == [
        "candidate_1",
        "repair_1",
    ]
    assert any(
        "dictionary unpacking" in hint
        for hint in trace["forbidden_pattern_hints"]
    )
    assert "explicit for-loop over the second input" in second_repair
    assert "identify the exact semantic bug" in critic_client.prompts[0]


class DiagnosticAwareMergeClient:
    def __init__(
        self, failing_files: dict[str, str], passing_files: dict[str, str]
    ) -> None:
        self.failing_files = failing_files
        self.passing_files = passing_files
        self.prompts: list[str] = []

    def create_chat_completion(
        self,
        config: OpenAICompatibleChatConfig,
        messages: list[dict[str, str]],
    ) -> dict[str, Any]:
        prompt = messages[-1]["content"]
        self.prompts.append(prompt)
        signals = (
            "expected {'x': 3, 'y': 3}, got {'x': 2, 'y': 3}",
            "overlapping keys must be summed",
            "not overwritten",
        )
        files = (
            self.passing_files
            if any(signal in prompt for signal in signals)
            else self.failing_files
        )
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {"files": files, "explanation": "diagnostic repair"}
                        )
                    }
                }
            ]
        }


def test_multi_agent_uses_explicit_expected_got_diagnostic() -> None:
    task = next(
        task
        for task in create_hard_coding_tasks()
        if task.id == "hard.merge_counts"
    )
    failing = {
        "counter_utils.py": "def merge_counts(a, b):\n    return {**a, **b}\n"
    }
    baseline_passed, _ = PythonTestRunner().run(
        failing, task.test_files, task.test_commands
    )
    assert baseline_passed is False

    client = DiagnosticAwareMergeClient(failing, task.expected_files)
    runtime = KernelRuntime()
    config = OpenAICompatibleChatConfig(
        api_key="local", model="diagnostic", base_url="http://localhost"
    )
    draft = OpenAICompatibleChatPlugin(
        config=config,
        client=client,
        capability_id="model.code",
        plugin_id="diagnostic.draft",
    )
    critic = OpenAICompatibleChatPlugin(
        config=config,
        client=SequencedClient(critique=True),
        capability_id="model.reason",
        plugin_id="diagnostic.critic",
    )
    runtime.register_plugin(draft)
    runtime.register_plugin(critic)
    agent = MultiModelCodingAgentPlugin(runtime)
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
    trace = result.output["_trace"]
    assert trace["repair_attempts_count"] >= 1
    assert trace["selected_candidate_id"] == "repair_1"
    assert trace["candidate_file_previews"]["candidate_1"] == failing
    assert trace["candidate_file_previews"]["repair_1"] == task.expected_files
    diagnostic = "expected {'x': 3, 'y': 3}, got {'x': 2, 'y': 3}"
    assert diagnostic in client.prompts[1]
    assert diagnostic in trace["repair_prompt_preview"]
    assert diagnostic in trace["verifier_messages_preview"]


class FullChecklistParseClient:
    def __init__(self, complete_files: dict[str, str]) -> None:
        self.complete_files = complete_files
        self.prompts: list[str] = []

    def create_chat_completion(
        self,
        config: OpenAICompatibleChatConfig,
        messages: list[dict[str, str]],
    ) -> dict[str, Any]:
        prompt = messages[-1]["content"]
        self.prompts.append(prompt)
        if len(self.prompts) == 1:
            files = {
                "parse_utils.py": (
                    "def parse_bool(value):\n"
                    "    normalized = value.lower()\n"
                    "    if normalized == 'true':\n        return True\n"
                    "    if normalized == 'false':\n        return False\n"
                    "    raise ValueError(value)\n"
                )
            }
        elif len(self.prompts) == 2:
            files = {
                "parse_utils.py": (
                    "def parse_bool(value):\n"
                    "    if isinstance(value, bool):\n        return value\n"
                    "    normalized = str(value).strip().lower()\n"
                    "    if normalized == 'true':\n        return True\n"
                    "    if normalized == 'false':\n        return False\n"
                    "    raise ValueError(value)\n"
                )
            }
        elif len(self.prompts) == 3:
            files = {
                "parse_utils.py": (
                    "def parse_bool(value):\n"
                    "    if isinstance(value, bool):\n        return value\n"
                    "    normalized = str(value).strip().lower()\n"
                    "    if normalized in {'true', 'yes', '1'}:\n        return True\n"
                    "    if normalized in {'false', '0'}:\n        return False\n"
                    "    raise ValueError(value)\n"
                )
            }
        else:
            required = (
                "Required input/output examples",
                "YES should parse as True",
                "off should parse as False",
                "1 should parse as True",
                "0 should parse as False",
            )
            files = (
                self.complete_files
                if all(item in prompt for item in required)
                else {}
            )
        return {
            "choices": [
                {"message": {"content": json.dumps({"files": files})}}
            ]
        }


def test_multi_agent_repairs_parse_bool_from_full_test_checklist() -> None:
    task = next(
        task
        for task in create_hard_coding_tasks()
        if task.id == "hard.parse_bool"
    )
    checklist = extract_test_assertion_checklist(task.test_files)
    checklist_text = "\n".join(checklist)
    assert all(value in checklist_text for value in ("YES", "off", "'1'", "'0'"))
    io_examples = extract_test_io_examples(task.test_files)
    assert io_examples == [
        "parse_bool(True) -> True",
        "parse_bool(False) -> False",
        "parse_bool('YES') -> True",
        "parse_bool('off') -> False",
        "parse_bool('1') -> True",
        "parse_bool('0') -> False",
        "parse_bool('maybe') -> raises ValueError",
    ]

    draft_client = FullChecklistParseClient(task.expected_files)
    critic_client = SequencedClient(critique=True)
    runtime = KernelRuntime()
    config = OpenAICompatibleChatConfig(
        api_key="local", model="checklist", base_url="http://localhost"
    )
    runtime.register_plugin(
        OpenAICompatibleChatPlugin(
            config=config,
            client=draft_client,
            capability_id="model.code",
            plugin_id="checklist.draft",
        )
    )
    runtime.register_plugin(
        OpenAICompatibleChatPlugin(
            config=config,
            client=critic_client,
            capability_id="model.reason",
            plugin_id="checklist.critic",
        )
    )
    agent = MultiModelCodingAgentPlugin(runtime)
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

    trace = result.output["_trace"]
    assert result.output["files"] == task.expected_files
    assert trace["selected_candidate_id"] == "repair_3"
    assert trace["test_assertion_checklist"] == checklist
    assert "YES" in trace["latest_failure_preview_by_candidate"]["repair_1"]
    assert "off" in trace["latest_failure_preview_by_candidate"]["repair_2"]
    assert "Required input/output examples" in draft_client.prompts[3]
    assert "parse_bool('off') -> False" in draft_client.prompts[3]
    assert all(value in critic_client.prompts[0] for value in ("YES", "off", "'1'", "'0'"))
    assert "Handle bool inputs before string normalization." in trace[
        "forbidden_pattern_hints"
    ]
    assert set(trace["repair_prompt_previews_by_attempt"]) == {
        "repair_1",
        "repair_2",
        "repair_3",
    }
    assert trace["test_io_examples"] == io_examples
    assert "parse_bool('off') -> False" in trace["failed_required_examples"]
    assert trace["repair_variant_names"][-1] == (
        "test-example-driven implementation"
    )
