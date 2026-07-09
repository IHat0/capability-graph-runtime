import json
from typing import Any

from cgr.apps.cli import main as cli
from cgr.kernel.coding import (
    CodeTestCase,
    CodingPatchNormalizationError,
    CodingPatchNormalizer,
    PythonTestRunner,
    check_dict_list_contract_shape,
    extract_forbidden_patterns_from_failed_code,
    extract_structural_repair_hints,
    extract_task_contract_checklist,
)
from cgr.kernel.coding.repo_v0_benchmarks import (
    RepoCodingTask,
    create_repo_v0_repo_tasks,
    create_repo_v0_tasks,
)
from cgr.kernel.runtime import KernelRuntime
from cgr.kernel.swe import SWEABRunner, SWECaseResult, SWEEvalResult, SWETask
from cgr.kernel.swe.swe_case_result import SWEMode
from cgr.plugins.agents import MultiModelCodingAgentPlugin, SingleModelCodingAgentPlugin
from cgr.plugins.providers.openai_compatible import (
    OpenAICompatibleChatConfig,
    OpenAICompatibleChatPlugin,
)


def test_repo_v0_catalog_has_ten_reference_passing_tasks() -> None:
    tasks = create_repo_v0_tasks()

    assert len(tasks) == 10
    assert len({task.id for task in tasks}) == 10
    for task in tasks:
        assert task.allowed_files_to_edit
        passed, messages = PythonTestRunner().run(
            {**task.files, **task.expected_files},
            task.scoring_test_files,
            task.scoring_test_commands,
        )
        assert passed, f"{task.id}: {messages}"


def test_repo_v0_representation_converts_to_swe_task() -> None:
    repo_task = create_repo_v0_repo_tasks()[0]

    assert isinstance(repo_task, RepoCodingTask)
    swe_task = repo_task.to_swe_task()
    assert swe_task.id == repo_task.task_id
    assert swe_task.files == repo_task.repo_files
    assert swe_task.allowed_files_to_edit == repo_task.allowed_files_to_edit
    assert "allowed file paths" in swe_task.issue


def test_equality_assertion_summary_includes_expression_expected_and_got() -> None:
    files = {
        "src/query_parser.py": (
            "def parse_query(query):\n"
            "    return {'a': '', 'b': '2'}\n"
        )
    }
    tests = {
        "visible_tests.py": (
            "from src.query_parser import parse_query\n"
            "assert parse_query('a=&b=2') == {'a': [''], 'b': ['2']}\n"
        )
    }

    passed, messages = PythonTestRunner().run(
        files,
        tests,
        [CodeTestCase(name="visible", command=["python", "visible_tests.py"])],
    )
    text = "\n".join(messages)

    assert passed is False
    assert "Expression:" in text
    assert "parse_query('a=&b=2')" in text
    assert "Expected:" in text
    assert "{'a': [''], 'b': ['2']}" in text
    assert "Got:" in text
    assert "{'a': '', 'b': '2'}" in text


def test_dict_list_expected_got_mismatch_produces_structural_hint() -> None:
    diagnostic = (
        "Expression:\nparse_query('a=&b=2')\n"
        "Expected:\n{'a': [''], 'b': ['2']}\n"
        "Got:\n{'a': '', 'b': '2'}"
    )

    hints = extract_structural_repair_hints(diagnostic)

    assert (
        "Expected dictionary values are lists. Store every value in a list, "
        "even for keys that occur once."
    ) in hints
    assert "Do not store first occurrence as a scalar. Initialize result[key] = [value]." in hints


def test_repo_query_contract_checklist_mentions_one_item_lists() -> None:
    task = create_repo_v0_tasks()[0]
    checklist = extract_task_contract_checklist(task.issue)

    assert any("each key maps to a list of values" in item for item in checklist)
    assert any("single keys still map to one-item lists" in item for item in checklist)


def test_disallowed_file_edits_are_rejected() -> None:
    task = create_repo_v0_tasks()[0]

    try:
        CodingPatchNormalizer().normalize(
            json.dumps({"files": {"src/url_utils.py": "def decode(v): return v\n"}}),
            set(task.allowed_files_to_edit),
        )
    except CodingPatchNormalizationError as exc:
        assert "unknown filename" in str(exc)
    else:
        raise AssertionError("disallowed edit should be rejected")


def test_dict_list_contract_rejects_scalar_first_assignment() -> None:
    task = create_repo_v0_tasks()[0]
    checklist = extract_task_contract_checklist(task.issue)
    bad = {
        "src/query_parser.py": (
            "def parse_query(query):\n"
            "    result = {}\n"
            "    result[key] = value\n"
            "    return result\n"
        )
    }
    good = {
        "src/query_parser.py": (
            "def parse_query(query):\n"
            "    result = {}\n"
            "    result[key] = [value]\n"
            "    return result\n"
        )
    }

    assert check_dict_list_contract_shape(bad, checklist) == (
        "Rejected candidate before tests; contract requires dictionary values "
        "to be lists for single and repeated keys."
    )
    assert check_dict_list_contract_shape(good, checklist) is None
    hints = extract_forbidden_patterns_from_failed_code(
        bad,
        "Expression:\nx\nExpected:\n{'a': ['']}\nGot:\n{'a': ''}",
        task_contract_checklist=checklist,
    )
    assert any("dictionary values are lists" in hint for hint in hints)


def test_malformed_json_candidate_is_rejected() -> None:
    task = create_repo_v0_tasks()[0]

    try:
        CodingPatchNormalizer().normalize("not json!", set(task.allowed_files_to_edit))
    except CodingPatchNormalizationError as exc:
        assert exc.raw_output_preview == "not json!"
    else:
        raise AssertionError("malformed output should be rejected")


def test_syntax_invalid_repo_candidate_fails_exact_verification() -> None:
    task = create_repo_v0_tasks()[0]
    patch = CodingPatchNormalizer().normalize(
        json.dumps({"files": {"src/query_parser.py": "def broken(:\n    pass\n"}}),
        set(task.allowed_files_to_edit),
    )

    passed, messages = SWEABRunner(KernelRuntime())._verify_final_patch(task, patch)

    assert passed is False
    assert "SyntaxError" in "\n".join(messages)
    assert "Final selected candidate failed exact-file verification" in messages[0]


class _RepoRepairClient:
    def __init__(self, first_files: dict[str, str], repaired_files: dict[str, str]) -> None:
        self.responses = [first_files, repaired_files]
        self.prompts: list[str] = []

    def create_chat_completion(
        self,
        config: OpenAICompatibleChatConfig,
        messages: list[dict[str, str]],
    ) -> dict[str, Any]:
        self.prompts.append(messages[-1]["content"])
        files = self.responses[min(len(self.prompts) - 1, len(self.responses) - 1)]
        return {"choices": [{"message": {"content": json.dumps({"files": files})}}]}


class _CriticClient:
    def create_chat_completion(
        self,
        config: OpenAICompatibleChatConfig,
        messages: list[dict[str, str]],
    ) -> dict[str, Any]:
        return {"choices": [{"message": {"content": "Use the test feedback."}}]}


def _runtime_with_repo_agents(
    client: _RepoRepairClient,
) -> tuple[KernelRuntime, SingleModelCodingAgentPlugin, MultiModelCodingAgentPlugin]:
    runtime = KernelRuntime()
    config = OpenAICompatibleChatConfig(
        api_key="local", model="repo", base_url="http://localhost"
    )
    runtime.register_plugin(
        OpenAICompatibleChatPlugin(
            config=config,
            client=client,
            capability_id="model.code",
            plugin_id="repo.draft",
        )
    )
    runtime.register_plugin(
        OpenAICompatibleChatPlugin(
            config=config,
            client=_CriticClient(),
            capability_id="model.reason",
            plugin_id="repo.critic",
        )
    )
    single = SingleModelCodingAgentPlugin(runtime)
    multi = MultiModelCodingAgentPlugin(runtime)
    runtime.register_plugin(single)
    runtime.register_plugin(multi)
    return runtime, single, multi


def test_visible_failure_and_safe_hidden_summary_reach_repair_prompt() -> None:
    task = create_repo_v0_tasks()[0]
    visible_only = {
        "src/query_parser.py": (
            "def parse_query(query):\n"
            "    result = {}\n"
            "    if not query:\n        return result\n"
            "    for part in query.split('&'):\n"
            "        if not part:\n            continue\n"
            "        key, _, value = part.partition('=')\n"
            "        result.setdefault(key, []).append(value)\n"
            "    return result\n"
        )
    }
    client = _RepoRepairClient(
        visible_only,
        task.expected_files,
    )
    runtime, single, _ = _runtime_with_repo_agents(client)

    result = SWEABRunner(runtime)._run_case(
        task, "cgr_single", single.metadata.id, debug_trace=True
    )

    assert result.passed is True
    assert len(client.prompts) == 2
    assert "visible_tests.py" in client.prompts[1]
    assert "a%20b=hello+world" not in client.prompts[1]
    assert "Hidden scoring also failed" in client.prompts[1]
    assert "Allowed files to edit" in client.prompts[1]
    assert result.hidden_source_included is False
    assert result.final_exact_repo_verification_passed is True
    assert result.allowed_files_to_edit == task.allowed_files_to_edit
    assert result.changed_files == sorted(task.expected_files)


def test_repo_multi_uses_data_shape_repair_variant() -> None:
    task = create_repo_v0_tasks()[0]
    scalar_first = {
        "src/query_parser.py": (
            "from src.url_utils import decode\n\n"
            "def parse_query(query):\n"
            "    result = {}\n"
            "    for part in query.split('&'):\n"
            "        if not part:\n            continue\n"
            "        key, _, value = part.partition('=')\n"
            "        key = decode(key); value = decode(value)\n"
            "        if key in result:\n"
            "            if isinstance(result[key], list):\n"
            "                result[key].append(value)\n"
            "            else:\n"
            "                result[key] = [result[key], value]\n"
            "        else:\n"
            "            result[key] = value\n"
            "    return result\n"
        )
    }
    client = _RepoRepairClient(scalar_first, task.expected_files)
    client.responses = [scalar_first, scalar_first, task.expected_files]
    runtime, _, multi = _runtime_with_repo_agents(client)

    result = SWEABRunner(runtime)._run_case(
        task, "cgr_multi", multi.metadata.id, debug_trace=True
    )

    assert result.passed is True
    assert result.selected_candidate_id == "repair_2"
    assert result.repair_variant_names is not None
    assert "data-shape contract repair" in result.repair_variant_names
    assert result.forbidden_pattern_hints is not None
    assert any("dictionary values are lists" in hint for hint in result.forbidden_pattern_hints)
    assert result.final_exact_repo_verification_passed is True


def test_repo_multi_monotonic_fallback_works() -> None:
    task = create_repo_v0_tasks()[0]
    failing = {"src/query_parser.py": task.files["src/query_parser.py"]}
    client = _RepoRepairClient(failing, failing)
    client.responses = [failing] * 4 + [task.expected_files]
    runtime, _, multi = _runtime_with_repo_agents(client)

    result = SWEABRunner(runtime)._run_case(
        task, "cgr_multi", multi.metadata.id, debug_trace=True
    )

    assert result.passed is True
    assert result.single_fallback_used is True
    assert result.multi_monotonic_guard_applied is True
    assert result.final_exact_repo_verification_passed is True


def _fake_evaluation(tasks: list[SWETask], debug: bool) -> SWEEvalResult:
    modes: tuple[SWEMode, ...] = ("baseline", "cgr_single", "cgr_multi")
    results = [
        SWECaseResult(
            task_id=task.id,
            mode=mode,
            plugin_id=f"fake.{mode}",
            passed=mode != "baseline",
            elapsed_seconds=0.01,
        )
        for task in tasks
        for mode in modes
    ]
    rates: dict[str, float] = {
        mode: sum(result.passed for result in results if result.mode == mode)
        / len(tasks)
        if tasks
        else 0.0
        for mode in modes
    }
    return SWEEvalResult(
        suite_name="coding_repo_v0",
        total_tasks=len(tasks),
        pass_rates=rates,
        deltas={
            "cgr_single_minus_baseline": rates["cgr_single"] - rates["baseline"],
            "cgr_multi_minus_baseline": rates["cgr_multi"] - rates["baseline"],
        },
        results=results,
    )


def test_repo_v0_cli_reference_check(capsys: Any) -> None:
    assert cli.coding_ab_repo_v0_main(["--reference-check"]) == 0
    output = json.loads(capsys.readouterr().out)

    assert output["suite_name"] == "coding_repo_v0_reference"
    assert output["total_tasks"] == 10
    assert output["passed_tasks"] == 10


def test_repo_v0_cli_filters_and_runs_aggregate(
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

    assert cli.coding_ab_repo_v0_main(["--runs", "2", "--max-tasks", "3"]) == 0
    output = json.loads(capsys.readouterr().out)

    assert output["suite_name"] == "coding_repo_v0"
    assert output["total_tasks"] == 3
    assert output["stability"]["runs"] == 2
    assert len(calls) == 2
    assert all(len(call) == 3 for call in calls)


def test_repo_v0_cli_task_id_selects_one(monkeypatch: Any, capsys: Any) -> None:
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

    assert cli.coding_ab_repo_v0_main(
        ["--task-id", "v0.query_parser_repeated_keys", "--debug-trace"]
    ) == 0
    output = json.loads(capsys.readouterr().out)

    assert selected == ["v0.query_parser_repeated_keys"]
    assert output["total_tasks"] == 1
