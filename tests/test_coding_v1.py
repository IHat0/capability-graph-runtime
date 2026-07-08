import json
from typing import Any

from cgr.apps.cli import main as cli
from cgr.kernel.coding import (
    CodeTestCase,
    CodingTask,
    PythonTestRunner,
    build_repair_prompt,
    extract_forbidden_patterns_from_failed_code,
    extract_syntax_error_summary,
    extract_task_contract_checklist,
)
from cgr.kernel.coding.v1_benchmarks import create_coding_v1_tasks
from cgr.kernel.coding.v1_runner import CodingV1Runner
from cgr.kernel.runtime import KernelRuntime
from cgr.kernel.swe import SWEABRunner, SWECaseResult, SWEEvalResult, SWETask
from cgr.plugins.agents import (
    MultiModelCodingAgentPlugin,
    SingleModelCodingAgentPlugin,
)
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


class _CriticClient:
    def create_chat_completion(
        self,
        config: OpenAICompatibleChatConfig,
        messages: list[dict[str, str]],
    ) -> dict[str, Any]:
        return {
            "choices": [
                {
                    "message": {
                        "content": "Repair every visible and safe hidden requirement."
                    }
                }
            ]
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
                "# SECRET_HIDDEN_SOURCE\n"
                "from value import value\n"
                "actual=value()\n"
                "assert actual == 2, f'expected 2, got {actual}'\n"
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
            {"value.py": "def value():\n    return 1\n"},
            {"value.py": "def value():\n    return 2\n"},
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

    result = SWEABRunner(runtime)._run_case(
        task, "cgr_single", agent.metadata.id, debug_trace=True
    )

    assert result.passed is True, result.model_dump()
    assert len(client.prompts) == 2
    assert "SECRET_HIDDEN_SOURCE" not in client.prompts[1]
    assert "expected 2, got 1" in client.prompts[1]
    assert "Hidden scoring also failed" in client.prompts[1]
    assert result.hidden_source_included is False
    assert result.hidden_failure_summary_safe is not None
    assert result.task_contract_checklist


def test_syntax_error_summary_and_repair_prompt_are_syntax_first() -> None:
    task = CodingTask(
        issue="Implement parse_bool; bool inputs return themselves.",
        files={"parse_utils.py": "def parse_bool(value):\n    return False\n"},
        test_files={"visible_tests.py": "from parse_utils import parse_bool\n"},
        test_commands=[
            CodeTestCase(name="visible", command=["python", "visible_tests.py"])
        ],
    )
    malformed = {
        "parse_utils.py": (
            "def parse_bool(value):\n    if value:\n        return True\n"
            "else:\n    return False\n"
        )
    }
    passed, messages = PythonTestRunner().run(
        malformed, task.test_files, task.test_commands
    )
    prompt = build_repair_prompt(task, malformed, messages)

    assert passed is False
    assert "SyntaxError" in (extract_syntax_error_summary(messages) or "")
    assert "Your previous code does not even parse" in prompt
    assert "Do not preserve the malformed indentation or typo" in prompt


def test_v1_task_contract_extraction_and_generic_hints() -> None:
    tasks = {task.id: task for task in create_coding_v1_tasks()}
    parse_contract = extract_task_contract_checklist(
        tasks["v1.parse_bool_extended"].issue
    )
    merge_contract = extract_task_contract_checklist(
        tasks["v1.merge_counts_nested"].issue
    )
    chunk_contract = extract_task_contract_checklist(
        tasks["v1.chunk_list_strict"].issue
    )

    assert any("bool inputs return themselves" in item for item in parse_contract)
    assert any("strings are stripped" in item for item in parse_contract)
    assert any("raise TypeError" in item for item in merge_contract)
    assert any("positive integer" in item for item in chunk_contract)

    parse_hints = extract_forbidden_patterns_from_failed_code(
        {"parse_utils.py": "value = value.lower()\n"},
        "AttributeError: bool has no attribute lower",
        task_contract_checklist=parse_contract,
    )
    merge_hints = extract_forbidden_patterns_from_failed_code(
        {"count_utils.py": "raise ValueError('bad')\n"},
        "overlap failed",
        task_contract_checklist=merge_contract,
    )
    chunk_hints = extract_forbidden_patterns_from_failed_code(
        {"chunk_utils.py": "return list(range(size))\n"},
        "TypeError: 'float' object cannot be interpreted as an integer",
        task_contract_checklist=chunk_contract,
    )

    assert "Handle bool inputs before string normalization." in parse_hints
    assert "Normalize strings with strip().lower(), not lower() alone." in parse_hints
    assert "The required exception type is TypeError, not ValueError." in merge_hints
    assert "Check isinstance(size, int) as well as positivity." in chunk_hints
    assert "Validate integer type before using range." in chunk_hints


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


def test_v1_multi_uses_monotonic_single_fallback() -> None:
    task = create_coding_v1_tasks()[0]
    client = _SequencedPatchClient([task.files] * 5 + [task.expected_files])
    runtime = KernelRuntime()
    config = OpenAICompatibleChatConfig(
        api_key="local", model="v1-fallback", base_url="http://localhost"
    )
    runtime.register_plugin(
        OpenAICompatibleChatPlugin(
            config=config,
            client=client,
            capability_id="model.code",
            plugin_id="v1.draft",
        )
    )
    runtime.register_plugin(
        OpenAICompatibleChatPlugin(
            config=config,
            client=_CriticClient(),
            capability_id="model.reason",
            plugin_id="v1.critic",
        )
    )
    multi = MultiModelCodingAgentPlugin(runtime)
    runtime.register_plugin(multi)

    result = SWEABRunner(runtime)._run_case(
        task, "cgr_multi", multi.metadata.id, debug_trace=True
    )

    assert result.passed is True, result.model_dump()
    assert result.single_fallback_used is True
    assert result.single_fallback_score == 1.0
    assert result.multi_monotonic_guard_applied is True
    assert result.final_selection_reason is not None
    assert "monotonic guard" in result.final_selection_reason


def test_coding_v1_reference_check_needs_no_provider(capsys: Any) -> None:
    assert cli.coding_ab_v1_main(["--reference-check", "--max-tasks", "2"]) == 0
    output = json.loads(capsys.readouterr().out)

    assert output["suite_name"] == "coding_v1_reference"
    assert output["total_tasks"] == 2
    assert output["passed_tasks"] == 2
