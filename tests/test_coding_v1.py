import json
from typing import Any

from cgr.apps.cli import main as cli
from cgr.kernel.coding import CodeTestCase, PythonTestRunner
from cgr.kernel.coding.v1_benchmarks import create_coding_v1_tasks
from cgr.kernel.coding.v1_runner import CodingV1Runner
from cgr.kernel.runtime import KernelRuntime
from cgr.kernel.swe import SWEABRunner, SWECaseResult, SWEEvalResult, SWETask
from cgr.plugins.agents import SingleModelCodingAgentPlugin
from cgr.plugins.providers.openai_compatible import (
    OpenAICompatibleChatConfig,
    OpenAICompatibleChatPlugin,
)


def test_coding_v1_catalog_has_unique_visible_and_hidden_tasks() -> None:
    tasks = create_coding_v1_tasks()

    assert len(tasks) >= 25
    assert len({task.id for task in tasks}) == len(tasks)
    assert all(task.visible_test_files for task in tasks)
    assert all(task.hidden_test_files for task in tasks)
    assert all(task.visible_test_commands for task in tasks)
    assert all(task.hidden_test_commands for task in tasks)


def test_coding_v1_reference_solutions_pass_visible_and_hidden_tests() -> None:
    for task in create_coding_v1_tasks():
        passed, messages = PythonTestRunner().run(
            task.expected_files,
            task.scoring_test_files,
            task.scoring_test_commands,
        )
        assert passed, f"{task.id}: {messages}"


class _SequencedPatchClient:
    def __init__(self, responses: list[dict[str, str]]) -> None:
        self.responses = responses
        self.prompts: list[str] = []

    def create_chat_completion(
        self,
        config: OpenAICompatibleChatConfig,
        messages: list[dict[str, str]],
    ) -> dict[str, Any]:
        self.prompts.append(messages[-1]["content"])
        files = self.responses[min(len(self.prompts) - 1, len(self.responses) - 1)]
        return {
            "choices": [{"message": {"content": json.dumps({"files": files})}}]
        }


def _hidden_boundary_task() -> SWETask:
    return SWETask(
        id="v1.hidden_boundary",
        issue="Fix value using the visible behavioral contract.",
        files={"value.py": "def value():\n    return 0\n"},
        expected_files={"value.py": "def value():\n    return 2\n"},
        visible_test_files={
            "visible_tests.py": "from value import value\nassert value() >= 1\n"
        },
        hidden_test_files={
            "hidden_tests.py": (
                "from value import value\n"
                "assert value() == 2, 'HIDDEN_SENTINEL exact edge case'\n"
            )
        },
        visible_test_commands=[
            CodeTestCase(name="visible", command=["python", "visible_tests.py"])
        ],
        hidden_test_commands=[
            CodeTestCase(name="hidden", command=["python", "hidden_tests.py"])
        ],
    )


def test_hidden_tests_score_results_but_source_stays_out_of_repair_prompt() -> None:
    task = _hidden_boundary_task()
    client = _SequencedPatchClient(
        [
            {"value.py": "def value():\n    return 0\n"},
            {"value.py": "def value():\n    return 1\n"},
        ]
    )
    runtime = KernelRuntime()
    model = OpenAICompatibleChatPlugin(
        config=OpenAICompatibleChatConfig(
            api_key="local", model="hidden-test", base_url="http://localhost"
        ),
        client=client,
        capability_id="model.code",
        plugin_id="hidden.model",
    )
    runtime.register_plugin(model)
    agent = SingleModelCodingAgentPlugin(runtime)
    runtime.register_plugin(agent)

    result = SWEABRunner(runtime)._run_case(task, "cgr_single", agent.metadata.id)

    assert result.passed is False
    assert len(client.prompts) == 2
    assert "HIDDEN_SENTINEL" not in client.prompts[1]
    assert "visible behavioral contract" in client.prompts[1]


def _fake_evaluation(tasks: list[SWETask], debug: bool) -> SWEEvalResult:
    results: list[SWECaseResult] = []
    for index, task in enumerate(tasks):
        for mode in ("baseline", "cgr_single", "cgr_multi"):
            passed = mode != "baseline" or index == 0
            results.append(
                SWECaseResult(
                    task_id=task.id,
                    mode=mode,
                    plugin_id=f"fake.{mode}",
                    passed=passed,
                    elapsed_seconds=0.01,
                )
            )
    total = len(tasks)
    rates = {
        mode: (
            sum(result.passed for result in results if result.mode == mode) / total
            if total
            else 0.0
        )
        for mode in ("baseline", "cgr_single", "cgr_multi")
    }
    return SWEEvalResult(
        suite_name="coding_v1",
        total_tasks=total,
        pass_rates=rates,
        deltas={
            "cgr_single_minus_baseline": rates["cgr_single"] - rates["baseline"],
            "cgr_multi_minus_baseline": rates["cgr_multi"] - rates["baseline"],
        },
        results=results,
    )


def test_v1_runner_builds_summary_stability_and_efficiency() -> None:
    tasks = create_coding_v1_tasks()[:3]
    report = CodingV1Runner(_fake_evaluation).run(tasks, runs=2)

    assert report["suite_name"] == "coding_v1"
    assert report["total_tasks"] == 3
    assert set(report) >= {
        "pass_rates",
        "deltas",
        "results",
        "summary",
        "stability",
        "efficiency",
    }
    assert report["summary"]["baseline_passed"] == 1
    assert report["summary"]["single_improved_tasks"] == sorted(
        task.id for task in tasks[1:]
    )
    assert report["summary"]["multi_regressed_tasks"] == []
    assert report["summary"]["multi_not_monotonic_tasks"] == []
    assert report["stability"]["runs"] == 2
    assert report["stability"]["mode_pass_rate_min"] == report["stability"][
        "mode_pass_rate_max"
    ]
    assert report["stability"]["per_task"][tasks[0].id]["baseline"] == 1.0
    assert isinstance(report["efficiency"]["suite_elapsed_seconds"], float)
    assert report["efficiency"]["usage"]["baseline"]["total_tokens"] is None


def test_coding_v1_cli_filters_tasks_and_emits_report(
    monkeypatch: Any, capsys: Any
) -> None:
    calls: list[list[str]] = []

    def fake_real(
        suite_name: str,
        tasks: list[SWETask],
        multi_repair_attempts: int = 3,
        debug_trace: bool = False,
    ) -> SWEEvalResult:
        calls.append([task.id for task in tasks])
        return _fake_evaluation(tasks, debug_trace)

    monkeypatch.setattr(cli, "_run_real_coding_ab", fake_real)

    assert cli.coding_ab_v1_main(["--runs", "2", "--max-tasks", "2"]) == 0
    output = json.loads(capsys.readouterr().out)

    assert output["total_tasks"] == 2
    assert output["stability"]["runs"] == 2
    assert len(calls) == 2
    assert all(len(call) == 2 for call in calls)


def test_coding_v1_cli_selects_one_task(monkeypatch: Any, capsys: Any) -> None:
    selected: list[str] = []

    def fake_real(
        suite_name: str,
        tasks: list[SWETask],
        multi_repair_attempts: int = 3,
        debug_trace: bool = False,
    ) -> SWEEvalResult:
        selected.extend(task.id for task in tasks)
        return _fake_evaluation(tasks, debug_trace)

    monkeypatch.setattr(cli, "_run_real_coding_ab", fake_real)

    assert cli.coding_ab_v1_main(
        ["--task-id", "v1.parse_bool_extended", "--debug-trace"]
    ) == 0
    output = json.loads(capsys.readouterr().out)

    assert selected == ["v1.parse_bool_extended"]
    assert output["total_tasks"] == 1
