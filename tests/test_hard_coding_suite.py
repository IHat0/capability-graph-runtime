import json
from typing import Any

from cgr.kernel.coding import PythonTestRunner
from cgr.kernel.coding.hard_coding_suite import create_hard_coding_tasks
from cgr.kernel.runtime import KernelRuntime
from cgr.kernel.swe import SWEABRunner, SWETask
from cgr.plugins.agents import (
    MultiModelCodingAgentPlugin,
    SingleModelCodingAgentPlugin,
)
from cgr.plugins.providers.openai_compatible import (
    OpenAICompatibleChatConfig,
    OpenAICompatibleChatPlugin,
)


def test_hard_suite_has_unique_executable_tasks() -> None:
    tasks = create_hard_coding_tasks()

    assert len(tasks) >= 8
    assert len({task.id for task in tasks}) == len(tasks)
    assert all(task.test_files and task.test_commands for task in tasks)


def test_hard_suite_reference_solutions_pass_executable_tests() -> None:
    for task in create_hard_coding_tasks():
        passed, _ = PythonTestRunner().run(
            task.expected_files,
            task.test_files,
            task.test_commands,
        )
        assert passed, task.id


def test_hard_suite_broken_initial_files_fail_several_tasks() -> None:
    failures = 0
    for task in create_hard_coding_tasks():
        passed, _ = PythonTestRunner().run(
            task.files,
            task.test_files,
            task.test_commands,
        )
        failures += not passed

    assert failures >= 6


class StagedHardSuiteClient:
    def __init__(
        self,
        tasks: list[SWETask],
        solve_limit: int = 0,
        critic: bool = False,
    ) -> None:
        self._tasks = tasks
        self._solve_limit = solve_limit
        self._critic = critic

    def create_chat_completion(
        self,
        config: OpenAICompatibleChatConfig,
        messages: list[dict[str, str]],
    ) -> dict[str, Any]:
        prompt = messages[-1]["content"]
        if self._critic:
            content = "APPLY_REFERENCE_FIX: correct every unmet requirement."
        elif prompt.startswith("Critique the proposed coding patch"):
            content = "Self critique completed."
        else:
            task = self._find_task(prompt)
            task_index = self._tasks.index(task)
            solved = task_index < self._solve_limit
            if "APPLY_REFERENCE_FIX" in prompt:
                solved = True
            files = task.expected_files if solved else task.files
            content = json.dumps(
                {"files": files, "explanation": "Staged fake provider output."}
            )
        return {"choices": [{"message": {"content": content}}]}

    def _find_task(self, prompt: str) -> SWETask:
        for task in self._tasks:
            if task.issue in prompt:
                return task
        raise AssertionError("Hard-suite task was not present in model prompt.")


def test_fake_provider_hard_suite_detects_boosted_improvement() -> None:
    tasks = create_hard_coding_tasks()
    runtime = KernelRuntime()
    config = OpenAICompatibleChatConfig(
        api_key="local", model="staged", base_url="http://localhost"
    )
    baseline = OpenAICompatibleChatPlugin(
        config=config,
        client=StagedHardSuiteClient(tasks, solve_limit=2),
        capability_id="model.code.baseline",
        plugin_id="staged.baseline",
    )
    single_model = OpenAICompatibleChatPlugin(
        config=config,
        client=StagedHardSuiteClient(tasks, solve_limit=5),
        capability_id="model.code.single",
        plugin_id="staged.single",
    )
    multi_model = OpenAICompatibleChatPlugin(
        config=config,
        client=StagedHardSuiteClient(tasks, solve_limit=5),
        capability_id="model.code.multi",
        plugin_id="staged.multi",
    )
    critic = OpenAICompatibleChatPlugin(
        config=config,
        client=StagedHardSuiteClient(tasks, critic=True),
        capability_id="model.reason",
        plugin_id="staged.critic",
    )
    runtime.register_plugin(baseline)
    runtime.register_plugin(single_model)
    runtime.register_plugin(multi_model)
    runtime.register_plugin(critic)
    single = SingleModelCodingAgentPlugin(
        runtime, model_capability_id="model.code.single"
    )
    multi = MultiModelCodingAgentPlugin(
        runtime, draft_capability_id="model.code.multi"
    )
    runtime.register_plugin(single)
    runtime.register_plugin(multi)

    result = SWEABRunner(runtime).run_suite(
        "hard_fake",
        tasks,
        baseline.metadata.id,
        single.metadata.id,
        multi.metadata.id,
    )
    serialized = json.loads(json.dumps(result.model_dump(mode="json")))

    assert serialized["total_tasks"] == 8
    assert result.pass_rates["baseline"] < result.pass_rates["cgr_single"]
    assert result.pass_rates["cgr_single"] <= result.pass_rates["cgr_multi"]
    assert result.pass_rates["baseline"] < result.pass_rates["cgr_multi"]
    assert result.pass_rates["cgr_multi"] == 1.0
