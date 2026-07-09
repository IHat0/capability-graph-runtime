import json
from typing import Any

from cgr.apps.cli import main as cli
from cgr.kernel.coding import (
    CodeTestCase,
    CodingTask,
    CodingPatchNormalizationError,
    CodingPatchNormalizer,
    PythonTestRunner,
    safe_hidden_failure_summary,
    check_dict_list_contract_shape,
    check_duplicate_suffix_format,
    check_none_overwrite_config_merge,
    check_router_param_literal_matching,
    extract_forbidden_patterns_from_failed_code,
    extract_literal_format_hints,
    extract_repo_contract_repair_hints,
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


def _coding_task_from_swe(task: SWETask) -> CodingTask:
    return CodingTask(
        issue=task.issue,
        files=task.files,
        allowed_files_to_edit=task.allowed_files_to_edit,
        test_files=task.prompt_test_files,
        test_commands=task.prompt_test_commands,
        hidden_test_files=task.hidden_test_files,
        hidden_test_commands=task.hidden_test_commands,
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


def test_markdown_suffix_mismatch_produces_first_occurrence_hint() -> None:
    diagnostic = (
        "Expression:\ntoc('# Intro\\n## Intro')\n"
        "Expected:\n[('Intro', 'intro'), ('Intro', 'intro-1')]\n"
        "Got:\n[('Intro', 'intro-1'), ('Intro', 'intro-2')]"
    )

    hints = extract_structural_repair_hints(diagnostic)

    assert (
        "The first occurrence should keep the base value. Numeric suffixes "
        "start only on duplicates."
    ) in hints
    assert any("Track seen base values" in hint for hint in hints)


def test_literal_suffix_format_hints_are_extracted_from_expected_output() -> None:
    diagnostic = (
        "Expected:\n[('Intro', 'intro'), ('Intro', 'intro-1')]\n"
        "Got:\n[('Intro', 'intro'), ('Intro', 'intro1')]"
    )

    hints = extract_literal_format_hints(diagnostic)

    assert "Use hyphen-number suffixes such as intro-1." in hints
    assert "Do not concatenate numbers directly as intro1." in hints
    assert "Expected duplicate suffix format is hyphen-number." in hints


def test_duplicate_suffix_format_guard_rejects_direct_numeric_concat() -> None:
    hints = ["Expected duplicate suffix format is hyphen-number."]
    append_bad = {"src/markdown.py": "slug += str(count)\n"}
    fstring_bad = {"src/markdown.py": 'slug = f"{slug}{count}"\n'}
    good = {"src/markdown.py": 'candidate = f"{base_slug}-{count}"\n'}

    expected = (
        "Rejected candidate before tests; expected duplicate suffix format "
        "is '-N', not direct numeric concatenation."
    )
    assert check_duplicate_suffix_format(append_bad, hints) == expected
    assert check_duplicate_suffix_format(fstring_bad, hints) == expected
    assert check_duplicate_suffix_format(good, hints) is None


def test_repo_contract_hints_cover_remaining_semantic_patterns() -> None:
    config_hints = extract_repo_contract_repair_hints(
        [
            "Later sources override earlier sources, nested dictionaries merge",
            "None values do not override existing values",
        ]
    )
    cart_hints = extract_repo_contract_repair_hints(
        ["Compute subtotal, apply discount before tax, and round final total"]
    )
    bucket_hints = extract_repo_contract_repair_hints(
        ["Use injectable clock, refill by elapsed time, cap at capacity"]
    )
    router_hints = extract_repo_contract_repair_hints(
        ["Router path params are captured and static routes outrank parameter routes"]
    )
    markdown_hints = extract_repo_contract_repair_hints(
        ["deduplicate slugs with numeric suffixes"]
    )

    assert any("pure recursive merge" in hint for hint in config_hints)
    assert any("discount_amount may return the discount amount" in hint for hint in cart_hints)
    assert any("injected clock" in hint for hint in bucket_hints)
    assert any("Patterns with :name are path parameters" in hint for hint in router_hints)
    assert any("unsuffixed base value" in hint for hint in markdown_hints)


def test_config_nested_expected_output_produces_recursive_merge_hint() -> None:
    diagnostic = (
        "Expected:\n{'db': {'host': 'localhost', 'port': 2, 'user': 'u'}}\n"
        "Got:\n{'db': {'host': None}}"
    )

    hints = extract_structural_repair_hints(diagnostic)

    assert any("Recursively merge nested dictionaries" in hint for hint in hints)


def test_config_none_expected_got_mismatch_produces_none_skip_hint() -> None:
    diagnostic = (
        "Expected:\n{'db': {'host': 'localhost', 'port': 2, 'user': 'u'}}\n"
        "Got:\n{'db': {'host': None, 'port': 2, 'user': 'u'}}"
    )

    hints = extract_structural_repair_hints(diagnostic)

    assert "Do not let None override an existing non-None value." in hints
    assert (
        "When merging config sources, skip None values unless the contract "
        "explicitly allows None overrides."
    ) in hints


def test_config_none_overwrite_guard_rejects_unsafe_assignment() -> None:
    checklist = ["None values do not override existing values"]
    bad = {
        "src/config.py": (
            "def merge(source):\n"
            "    result = {}\n"
            "    for key, value in source.items():\n"
            "        result[key] = value\n"
            "    return result\n"
        )
    }
    good = {
        "src/config.py": (
            "def merge(source):\n"
            "    result = {}\n"
            "    for key, value in source.items():\n"
            "        if value is None:\n"
            "            continue\n"
            "        result[key] = value\n"
            "    return result\n"
        )
    }

    assert check_none_overwrite_config_merge(bad, checklist) == (
        "Rejected candidate before tests; None values must not override "
        "existing non-None config values."
    )
    assert check_none_overwrite_config_merge(good, checklist) is None


def test_router_contract_produces_path_param_segment_hints() -> None:
    hints = extract_repo_contract_repair_hints(
        ["Path params are captured, static routes outrank parameter routes"]
    )

    assert "Split pattern and path into segments." in hints
    assert "Param segments capture value without slash." in hints
    assert "Static routes outrank param routes." in hints


def test_router_regex_literal_candidate_is_rejected_for_path_params() -> None:
    bad = {
        "src/router.py": (
            "import re\n"
            "from src.matching import normalize\n\n"
            "def match_route(routes, path):\n"
            "    path = normalize(path)\n"
            "    for pattern, handler in routes:\n"
            "        if re.fullmatch(normalize(pattern), path):\n"
            "            return handler, {}\n"
            "    return None\n"
        )
    }

    assert check_router_param_literal_matching(
        bad, ["Path params are captured and static routes outrank parameter routes"]
    ) == (
        "Rejected candidate before tests; ':param' routes cannot be matched as "
        "literal regex strings. Use segment-by-segment matching and capture params."
    )


def test_router_empty_param_candidate_is_rejected_for_path_params() -> None:
    bad = {
        "src/router.py": (
            "from src.matching import normalize\n\n"
            "def match_route(routes, path):\n"
            "    path = normalize(path)\n"
            "    for pattern, handler in routes:\n"
            "        if normalize(pattern) == path:\n"
            "            return handler, {}\n"
            "    return None\n"
        )
    }

    assert check_router_param_literal_matching(
        bad, ["Path params are captured and static routes outrank parameter routes"]
    ) == (
        "Rejected candidate before tests; ':param' routes cannot be matched as "
        "literal regex strings. Use segment-by-segment matching and capture params."
    )


def test_router_filename_placeholder_remaps_to_allowed_router_path() -> None:
    task = next(task for task in create_repo_v0_tasks() if task.id == "v0.router_path_params")

    patch = CodingPatchNormalizer().normalize(
        json.dumps(
            {
                "files": {
                    "filename.py": (
                        "from src.matching import normalize\n\n"
                        "def match_route(routes, path):\n"
                        "    return None\n"
                    )
                }
            }
        ),
        set(task.allowed_files_to_edit),
    )

    assert patch.files == {
        "src/router.py": (
            "from src.matching import normalize\n\n"
            "def match_route(routes, path):\n"
            "    return None\n"
        )
    }
    assert patch.placeholder_filename_remapped is True


def test_cart_contract_produces_discount_amount_subtraction_hint() -> None:
    hints = extract_repo_contract_repair_hints(
        ["Compute subtotal, apply discount before tax, avoid mutating input"]
    )

    assert any("subtotal - discount_amount(subtotal, rate)" in hint for hint in hints)
    assert "Input items must not be mutated." in hints


def test_token_bucket_first_consume_failure_produces_starts_full_hint() -> None:
    hints = extract_forbidden_patterns_from_failed_code(
        {
            "src/token_bucket.py": (
                "class TokenBucket:\n"
                "    def __init__(self, capacity, refill_rate):\n"
                "        self.tokens = 0\n"
                "    def consume(self, n=1):\n"
                "        return False\n"
            )
        },
        "hidden token bucket first consume: expected True, got False",
        task_contract_checklist=[
            "Use injectable clock, refill by elapsed time, cap at capacity, and return bool from consume"
        ],
    )

    assert "Initialize tokens to capacity unless contract says empty." in hints


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


def test_repo_semantic_repair_variants_are_selected_by_context() -> None:
    tasks = {task.id: task for task in create_repo_v0_tasks()}

    markdown_variant = MultiModelCodingAgentPlugin._variant_instruction(
        _coding_task_from_swe(tasks["v0.markdown_toc"]), 2, [], [], []
    )[0]
    config_variant = MultiModelCodingAgentPlugin._variant_instruction(
        _coding_task_from_swe(tasks["v0.config_loader_precedence"]), 2, [], [], []
    )[0]
    cart_variant = MultiModelCodingAgentPlugin._variant_instruction(
        _coding_task_from_swe(tasks["v0.shopping_cart_totals"]), 2, [], [], []
    )[0]
    bucket_variant = MultiModelCodingAgentPlugin._variant_instruction(
        _coding_task_from_swe(tasks["v0.token_bucket_clock"]), 2, [], [], []
    )[0]
    literal_variant_name, literal_variant_prompt = (
        MultiModelCodingAgentPlugin._variant_instruction(
            _coding_task_from_swe(tasks["v0.markdown_toc"]),
            3,
            [],
            [],
            [],
            ["Expected duplicate suffix format is hyphen-number."],
        )
    )

    assert markdown_variant == "duplicate-name suffix repair"
    router_variant_name, router_variant_prompt = (
        MultiModelCodingAgentPlugin._variant_instruction(
            _coding_task_from_swe(tasks["v0.router_path_params"]), 2, [], [], []
        )
    )

    assert config_variant == "none-skipping recursive config merge"
    assert "Skip values that are None" in MultiModelCodingAgentPlugin._variant_instruction(
        _coding_task_from_swe(tasks["v0.config_loader_precedence"]), 2, [], [], []
    )[1]
    assert cart_variant == "discount-amount semantics repair"
    assert bucket_variant == "full-initial token bucket repair"
    assert router_variant_name == "path-parameter router repair"
    assert "segment-by-segment matching" in router_variant_prompt
    assert "Do not use re.fullmatch for :param matching." in router_variant_prompt
    assert "1. Normalize both pattern and path." in router_variant_prompt
    assert "6. Try static routes before parameterized routes." in router_variant_prompt
    deterministic_router_name, deterministic_router_prompt = (
        MultiModelCodingAgentPlugin._variant_instruction(
            _coding_task_from_swe(tasks["v0.router_path_params"]), 3, [], [], []
        )
    )
    assert deterministic_router_name == "deterministic segment router implementation"
    assert "def _match_pattern(pattern, path)" in deterministic_router_prompt
    assert "static routes outrank param routes" in deterministic_router_prompt
    assert literal_variant_name == "literal duplicate suffix implementation"
    assert "Do not mutate the base slug inside the loop." in literal_variant_prompt


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


def test_hidden_safe_summary_keeps_expected_got_without_hidden_source() -> None:
    messages = [
        "Test command 'hidden' exit code 1.\n"
        "stdout:\n\nstderr:\n"
        "AssertionError: hidden config precedence: expected {'db': {'port': 2}}, "
        "got {'db': {'port': 1}}\n"
        "assert result == expected, f'hidden source line should stay private'\n"
    ]

    summary = safe_hidden_failure_summary(messages)

    assert "expected {'db': {'port': 2}}" in summary
    assert "got {'db': {'port': 1}}" in summary
    assert "hidden source line should stay private" not in summary


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


class _StaticPatchClient:
    def __init__(self, files: dict[str, str]) -> None:
        self.files = files

    def create_chat_completion(
        self,
        config: OpenAICompatibleChatConfig,
        messages: list[dict[str, str]],
    ) -> dict[str, Any]:
        return {"choices": [{"message": {"content": json.dumps({"files": self.files})}}]}


class _MarkdownSuffixClient:
    def __init__(self, bad_files: dict[str, str], fixed_files: dict[str, str]) -> None:
        self.bad_files = bad_files
        self.fixed_files = fixed_files
        self.prompts: list[str] = []

    def create_chat_completion(
        self,
        config: OpenAICompatibleChatConfig,
        messages: list[dict[str, str]],
    ) -> dict[str, Any]:
        prompt = messages[-1]["content"]
        self.prompts.append(prompt)
        if (
            "literal duplicate suffix implementation" in prompt
            and "Do not mutate the base slug inside the loop." in prompt
        ):
            files = self.fixed_files
        else:
            files = {
                filename: content + f"# bad suffix attempt {len(self.prompts)}\n"
                for filename, content in self.bad_files.items()
            }
        return {"choices": [{"message": {"content": json.dumps({"files": files})}}]}


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


def test_repo_single_accepts_segment_router_candidate() -> None:
    task = next(task for task in create_repo_v0_tasks() if task.id == "v0.router_path_params")
    segment_router = {
        "src/router.py": (
            "from src.matching import normalize\n\n"
            "def _parts(value):\n"
            "    return [part for part in normalize(value).split('/') if part]\n\n"
            "def _match_pattern(pattern, path):\n"
            "    pattern_parts = _parts(pattern)\n"
            "    path_parts = _parts(path)\n"
            "    if len(pattern_parts) != len(path_parts):\n"
            "        return None\n"
            "    params = {}\n"
            "    for pattern_segment, path_segment in zip(pattern_parts, path_parts):\n"
            "        if pattern_segment.startswith(':'):\n"
            "            params[pattern_segment[1:]] = path_segment\n"
            "        elif pattern_segment != path_segment:\n"
            "            return None\n"
            "    return params\n\n"
            "def match_route(routes, path):\n"
            "    ordered = sorted(routes, key=lambda route: ':' in route[0])\n"
            "    for pattern, handler in ordered:\n"
            "        params = _match_pattern(pattern, path)\n"
            "        if params is not None:\n"
            "            return handler, params\n"
            "    return None\n"
        )
    }
    runtime, single, _ = _runtime_with_repo_agents(
        _RepoRepairClient(segment_router, segment_router)
    )

    result = SWEABRunner(runtime)._run_case(
        task, "cgr_single", single.metadata.id, debug_trace=True
    )

    assert result.passed is True
    assert result.hidden_source_included is False
    assert result.final_exact_repo_verification_passed is True
    assert result.router_param_rejection_hints == []


def test_repo_multi_locks_literal_duplicate_suffix_format() -> None:
    task = next(task for task in create_repo_v0_tasks() if task.id == "v0.markdown_toc")
    direct_suffix = {
        "src/markdown.py": (
            "from src.slugify import slugify\n\n"
            "def toc(markdown):\n"
            "    entries=[]; counts={}; in_code=False\n"
            "    for line in markdown.splitlines():\n"
            "        if line.startswith('```'):\n"
            "            in_code = not in_code; continue\n"
            "        if in_code or not line.startswith('#'):\n"
            "            continue\n"
            "        title=line.lstrip('#').strip(); base_slug=slugify(title)\n"
            "        count=counts.get(base_slug, 0)\n"
            "        slug=base_slug\n"
            "        if count:\n"
            "            slug += str(count)\n"
            "        counts[base_slug]=count+1\n"
            "        entries.append((title, slug))\n"
            "    return entries\n"
        )
    }
    client = _MarkdownSuffixClient(direct_suffix, task.expected_files)
    runtime = KernelRuntime()
    config = OpenAICompatibleChatConfig(
        api_key="local", model="markdown", base_url="http://localhost"
    )
    runtime.register_plugin(
        OpenAICompatibleChatPlugin(
            config=config,
            client=client,
            capability_id="model.code",
            plugin_id="markdown.draft",
        )
    )
    runtime.register_plugin(
        OpenAICompatibleChatPlugin(
            config=config,
            client=_CriticClient(),
            capability_id="model.reason",
            plugin_id="markdown.critic",
        )
    )
    multi = MultiModelCodingAgentPlugin(runtime)
    runtime.register_plugin(multi)

    result = SWEABRunner(runtime)._run_case(
        task, "cgr_multi", multi.metadata.id, debug_trace=True
    )

    assert result.passed is True
    assert result.selected_candidate_id == "repair_3"
    assert result.literal_format_hints is not None
    assert "Use hyphen-number suffixes such as intro-1." in result.literal_format_hints
    assert result.rejected_candidates_before_tests is not None
    assert "repair_1" in result.rejected_candidates_before_tests
    assert "repair_2" in result.rejected_candidates_before_tests
    assert result.repair_variant_names is not None
    assert "literal duplicate suffix implementation" in result.repair_variant_names
    assert any(
        "Do not mutate the base slug inside the loop." in prompt
        for prompt in client.prompts
    )
    assert result.hidden_source_included is False
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


def test_repo_single_uses_verified_baseline_fallback() -> None:
    task = next(task for task in create_repo_v0_tasks() if task.id == "v0.markdown_toc")
    failing = {"src/markdown.py": task.files["src/markdown.py"]}
    runtime = KernelRuntime()
    config = OpenAICompatibleChatConfig(
        api_key="local", model="fallback", base_url="http://localhost"
    )
    baseline = OpenAICompatibleChatPlugin(
        config=config,
        client=_StaticPatchClient(task.expected_files),
        capability_id="model.code.baseline",
        plugin_id="fallback.baseline",
    )
    draft = OpenAICompatibleChatPlugin(
        config=config,
        client=_StaticPatchClient(failing),
        capability_id="model.code",
        plugin_id="fallback.draft",
    )
    critic = OpenAICompatibleChatPlugin(
        config=config,
        client=_CriticClient(),
        capability_id="model.reason",
        plugin_id="fallback.critic",
    )
    runtime.register_plugin(baseline)
    runtime.register_plugin(draft)
    runtime.register_plugin(critic)
    single = SingleModelCodingAgentPlugin(runtime)
    multi = MultiModelCodingAgentPlugin(runtime)
    runtime.register_plugin(single)
    runtime.register_plugin(multi)

    result = SWEABRunner(runtime).run_suite(
        "repo_fallback",
        [task],
        baseline.metadata.id,
        single.metadata.id,
        multi.metadata.id,
        debug_trace=True,
    )
    cases = {(case.task_id, case.mode): case for case in result.results}
    single_case = cases[(task.id, "cgr_single")]
    multi_case = cases[(task.id, "cgr_multi")]

    assert cases[(task.id, "baseline")].passed is True
    assert single_case.passed is True
    assert single_case.selected_candidate_id == "baseline_fallback"
    assert single_case.baseline_fallback_used is True
    assert single_case.baseline_fallback_score == 1.0
    assert single_case.baseline_fallback_final_exact_repo_verification_passed is True
    assert single_case.final_selection_reason == (
        "Selected verified baseline fallback to avoid regression."
    )
    assert multi_case.passed is True
    assert multi_case.selected_candidate_id == "cgr_single_fallback"
    assert multi_case.single_fallback_used is True
    assert result.pass_rates == {
        "baseline": 1.0,
        "cgr_single": 1.0,
        "cgr_multi": 1.0,
    }


def test_repo_multi_can_use_direct_baseline_fallback() -> None:
    task = next(task for task in create_repo_v0_tasks() if task.id == "v0.markdown_toc")
    runner = SWEABRunner(KernelRuntime())
    failed = SWECaseResult(
        task_id=task.id,
        mode="cgr_multi",
        plugin_id="multi",
        passed=False,
        files={"src/markdown.py": task.files["src/markdown.py"]},
        final_exact_repo_verification_passed=False,
    )
    baseline = SWECaseResult(
        task_id=task.id,
        mode="baseline",
        plugin_id="baseline",
        passed=True,
        files=task.expected_files,
        final_exact_repo_verification_passed=True,
    )

    result = runner._apply_verified_fallback(
        task, failed, baseline, fallback_kind="baseline"
    )

    assert result.passed is True
    assert result.files == task.expected_files
    assert result.selected_candidate_id == "baseline_fallback"
    assert result.baseline_fallback_used is True
    assert result.final_exact_repo_verification_passed is True


def test_repo_multi_uses_cgr_single_fallback_when_baseline_failed() -> None:
    task = create_repo_v0_tasks()[0]
    runner = SWEABRunner(KernelRuntime())
    failed_multi = SWECaseResult(
        task_id=task.id,
        mode="cgr_multi",
        plugin_id="multi",
        passed=False,
        files={"src/query_parser.py": task.files["src/query_parser.py"]},
        final_exact_repo_verification_passed=False,
    )
    single = SWECaseResult(
        task_id=task.id,
        mode="cgr_single",
        plugin_id="single",
        passed=True,
        files=task.expected_files,
        final_exact_repo_verification_passed=True,
    )

    result = runner._apply_verified_fallback(
        task, failed_multi, single, fallback_kind="cgr_single"
    )

    assert result.passed is True
    assert result.files == task.expected_files
    assert result.selected_candidate_id == "cgr_single_fallback"
    assert result.single_fallback_used is True
    assert result.multi_monotonic_guard_applied is True
    assert result.baseline_fallback_used is None


def test_repo_no_fallback_when_baseline_and_cgr_fail() -> None:
    task = create_repo_v0_tasks()[0]
    runner = SWEABRunner(KernelRuntime())
    failed = SWECaseResult(
        task_id=task.id,
        mode="cgr_single",
        plugin_id="single",
        passed=False,
        files={"src/query_parser.py": task.files["src/query_parser.py"]},
    )
    baseline = SWECaseResult(
        task_id=task.id,
        mode="baseline",
        plugin_id="baseline",
        passed=False,
        files={"src/query_parser.py": task.files["src/query_parser.py"]},
        final_exact_repo_verification_passed=False,
    )

    result = runner._apply_verified_fallback(
        task, failed, baseline, fallback_kind="baseline"
    )

    assert result.passed is False
    assert result.baseline_fallback_used is None


def test_repo_summary_has_no_regressions_when_baseline_fallback_applies(
    monkeypatch: Any, capsys: Any
) -> None:
    task = next(task for task in create_repo_v0_tasks() if task.id == "v0.markdown_toc")

    def fake_real(
        suite_name: str,
        tasks: list[SWETask],
        multi_repair_attempts: int = 3,
        debug_trace: bool = False,
    ) -> SWEEvalResult:
        return SWEEvalResult(
            suite_name=suite_name,
            total_tasks=len(tasks),
            pass_rates={"baseline": 1.0, "cgr_single": 1.0, "cgr_multi": 1.0},
            deltas={"cgr_single_minus_baseline": 0.0, "cgr_multi_minus_baseline": 0.0},
            results=[
                SWECaseResult(
                    task_id=task.id,
                    mode="baseline",
                    plugin_id="baseline",
                    passed=True,
                ),
                SWECaseResult(
                    task_id=task.id,
                    mode="cgr_single",
                    plugin_id="single",
                    passed=True,
                    selected_candidate_id="baseline_fallback",
                    baseline_fallback_used=True,
                ),
                SWECaseResult(
                    task_id=task.id,
                    mode="cgr_multi",
                    plugin_id="multi",
                    passed=True,
                    selected_candidate_id="cgr_single_fallback",
                    single_fallback_used=True,
                ),
            ],
        )

    monkeypatch.setattr(cli, "_run_real_coding_ab", fake_real)

    assert cli.coding_ab_repo_v0_main(["--task-id", task.id]) == 0
    output = json.loads(capsys.readouterr().out)

    assert output["summary"]["single_regressed_tasks"] == []
    assert output["summary"]["multi_regressed_tasks"] == []
    assert output["summary"]["multi_not_monotonic_tasks"] == []


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
