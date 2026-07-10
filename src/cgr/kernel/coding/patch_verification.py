"""Shared verification and deterministic selection for coding-agent patches."""

import ast
import re

from .coding_patch import CodingPatch
from .coding_task import CodingTask
from .python_test_runner import PythonTestRunner, safe_hidden_failure_summary


def verify_patch(
    task: CodingTask, patch: CodingPatch
) -> tuple[bool, list[str]] | None:
    """Run task tests when available, otherwise report no verification contract."""
    if not task.test_files or not task.test_commands:
        return None
    files = apply_patch_to_task_files(task, patch)
    visible = PythonTestRunner().run(
        files,
        task.test_files,
        task.test_commands,
    )
    if not visible[0] or not task.hidden_test_files or not task.hidden_test_commands:
        return visible
    hidden = PythonTestRunner().run(
        files,
        task.hidden_test_files,
        task.hidden_test_commands,
    )
    if hidden[0]:
        return True, [*visible[1], *hidden[1]]
    safe_summary = safe_hidden_failure_summary(hidden[1])
    return False, [
        *visible[1],
        "Hidden scoring also failed. Safe hidden failure summary:\n"
        f"{safe_summary}\nHidden source included: false",
    ]


def select_patch(
    original: CodingPatch,
    original_passed: bool,
    repaired: CodingPatch,
    repaired_passed: bool,
) -> CodingPatch:
    """Prefer verified patches, then fewer and shorter replacement files."""
    if original_passed != repaired_passed:
        return original if original_passed else repaired
    return min((original, repaired), key=_patch_size)


def apply_patch_to_task_files(
    task: CodingTask, patch: CodingPatch
) -> dict[str, str]:
    """Overlay generated replacement files onto the original task repo files."""
    return {**task.files, **patch.files}


def _patch_size(patch: CodingPatch) -> tuple[int, int]:
    return len(patch.files), sum(len(name) + len(text) for name, text in patch.files.items())


def patch_fingerprint(patch: CodingPatch) -> tuple[tuple[str, str], ...]:
    """Return a stable exact-content identity for repetition detection."""
    return tuple(sorted(patch.files.items()))


def check_bool_before_string_normalization(
    files: dict[str, str], task_contract_checklist: list[str]
) -> str | None:
    """Reject parser code that normalizes strings before handling bool inputs."""
    contract = "\n".join(task_contract_checklist).casefold()
    if "bool inputs return themselves" not in contract:
        return None
    for content in files.values():
        for match in re.finditer(
            r"def\s+\w+\s*\(\s*(?P<param>[A-Za-z_]\w*)\b[^)]*\)\s*:",
            content,
        ):
            param = match.group("param")
            body = content[match.end() :]
            normalization_positions = [
                position
                for pattern in (
                    rf"{re.escape(param)}\s*\.\s*strip\s*\(",
                    rf"{re.escape(param)}\s*\.\s*lower\s*\(",
                )
                if (position := _first_match_position(pattern, body)) is not None
            ]
            if not normalization_positions:
                continue
            first_normalization = min(normalization_positions)
            bool_guard_patterns = (
                rf"isinstance\s*\(\s*{re.escape(param)}\s*,\s*bool\s*\)",
                rf"type\s*\(\s*{re.escape(param)}\s*\)\s+is\s+bool",
            )
            bool_guard_positions = [
                position
                for pattern in bool_guard_patterns
                if (position := _first_match_position(pattern, body)) is not None
            ]
            if not bool_guard_positions or min(bool_guard_positions) > first_normalization:
                return (
                    "Rejected candidate before tests; bool inputs must be handled "
                    "before string normalization."
                )
    return None


def check_dict_list_contract_shape(
    files: dict[str, str], task_contract_checklist: list[str]
) -> str | None:
    """Reject obvious scalar assignments when a dict-of-lists contract exists."""
    contract = "\n".join(task_contract_checklist).casefold()
    if not _contract_requires_dict_list_values(contract):
        return None
    for content in files.values():
        for line in content.splitlines():
            compact = line.strip()
            if "setdefault" in compact or ".append(" in compact:
                continue
            if re.search(r"\[[^\]]+\]\s*=\s*\[[^\]]*\]", compact):
                continue
            if re.search(r"\w+\s*\[[^\]]+\]\s*=\s*[A-Za-z_]\w*\b", compact):
                return (
                    "Rejected candidate before tests; contract requires dictionary "
                    "values to be lists for single and repeated keys."
                )
    return None


def check_duplicate_suffix_format(
    files: dict[str, str], literal_format_hints: list[str]
) -> str | None:
    """Reject direct numeric suffix concatenation when expected uses '-N'."""
    if not any("hyphen-number" in hint for hint in literal_format_hints):
        return None
    direct_suffix_patterns = (
        r"\w+\s*\+=\s*str\s*\(\s*\w+\s*\)",
        r"\w+\s*=\s*\w+\s*\+\s*str\s*\(\s*\w+\s*\)",
        r"f[\"'][^\"']*\{\s*\w+\s*\}\s*\{\s*\w+\s*\}[^\"']*[\"']",
    )
    hyphen_suffix_patterns = (
        r"f[\"'][^\"']*\{\s*\w+\s*\}-\{\s*\w+\s*\}[^\"']*[\"']",
        r"\w+\s*\+\s*[\"']-[\"']\s*\+\s*str\s*\(\s*\w+\s*\)",
    )
    for content in files.values():
        if any(re.search(pattern, content) for pattern in hyphen_suffix_patterns):
            continue
        if any(re.search(pattern, content) for pattern in direct_suffix_patterns):
            return (
                "Rejected candidate before tests; expected duplicate suffix "
                "format is '-N', not direct numeric concatenation."
            )
    return None


def check_none_overwrite_config_merge(
    files: dict[str, str], task_contract_checklist: list[str]
) -> str | None:
    """Reject obvious config merges that assign None values as overrides."""
    contract = "\n".join(task_contract_checklist).casefold()
    if not _none_skip_context(contract):
        return None
    if not _all_sources_recursive_context(contract):
        for content in files.values():
            try:
                tree = ast.parse(content)
            except SyntaxError:
                continue
            for node in tree.body:
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if _function_assigns_value(node) and not _function_skips_none(node):
                    return (
                        "Rejected candidate before tests; None values must not "
                        "override existing non-None config values."
                    )
        return None
    shape = config_recursive_merge_debug_fields(files, task_contract_checklist)
    if shape["config_manual_merge_rejected"]:
        return CONFIG_ALL_SOURCES_REJECTION
    return None


CONFIG_ALL_SOURCES_REJECTION = (
    "Rejected candidate before tests; every config source must pass through the "
    "recursive None-skipping merge helper. Do not duplicate top-level merge logic "
    "in resolve_config."
)

CART_MUTATION_REJECTION = (
    "Rejected candidate before tests; cart total must not mutate input items or "
    "assign derived fields such as line_total."
)
CART_DISCOUNT_REJECTION = (
    "Rejected candidate before tests; discount_amount returns the discount amount. "
    "Subtract it from subtotal before applying tax."
)
CART_PARTIAL_REJECTION = (
    "Rejected candidate before tests; the candidate fixed only one cart "
    "requirement. It must both avoid input mutation and subtract discount_amount "
    "from subtotal."
)
CART_ORDER_REJECTION = (
    "Rejected candidate before tests; subtract the discount amount before applying "
    "tax, and round only the final total."
)


def check_cart_total_contract(
    files: dict[str, str], task_contract_checklist: list[str]
) -> str | None:
    """Reject partial cart repairs before spending an executable test run."""
    shape = cart_total_debug_fields(files, task_contract_checklist)
    if not shape["cart_contract_detected"]:
        return None
    mutation = shape["cart_input_mutation_detected"]
    subtraction = shape["cart_discount_subtraction_detected"]
    if mutation and not subtraction:
        return CART_PARTIAL_REJECTION
    if mutation:
        return CART_MUTATION_REJECTION
    if not subtraction:
        return CART_DISCOUNT_REJECTION
    if not shape["cart_tax_after_discount_detected"]:
        return CART_ORDER_REJECTION
    if not shape["cart_final_only_rounding_detected"]:
        return CART_ORDER_REJECTION
    return None


def cart_total_debug_fields(
    files: dict[str, str], task_contract_checklist: list[str]
) -> dict[str, bool]:
    """Analyze the combined non-mutation and discount-subtraction cart contract."""
    contract = "\n".join(task_contract_checklist).casefold()
    content = "\n".join(files.values())
    detected = _cart_combined_context(contract, content.casefold())
    result = {
        "cart_contract_detected": detected,
        "cart_input_mutation_detected": False,
        "cart_discount_subtraction_detected": False,
        "cart_tax_after_discount_detected": False,
        "cart_final_only_rounding_detected": False,
        "cart_combined_contract_satisfied": False,
    }
    if not detected:
        return result
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return result
    total_function = next(
        (
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "total"
        ),
        None,
    )
    if total_function is None:
        return result
    item_names = {
        node.target.id
        for node in ast.walk(total_function)
        if isinstance(node, (ast.For, ast.comprehension))
        and isinstance(node.target, ast.Name)
        and isinstance(node.iter, ast.Name)
        and node.iter.id == "items"
    }
    mutation = any(
        isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign))
        and any(
            _subscript_root_name(target) in item_names
            for target in _assignment_targets(node)
        )
        for node in ast.walk(total_function)
    )
    discount_names = {
        target.id
        for node in ast.walk(total_function)
        if isinstance(node, ast.Assign)
        and _is_discount_amount_call(node.value)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    subtraction_nodes = [
        node
        for node in ast.walk(total_function)
        if isinstance(node, ast.BinOp)
        and isinstance(node.op, ast.Sub)
        and _contains_name(node.left, "subtotal")
        and (
            _contains_discount_call(node.right)
            or any(_contains_name(node.right, name) for name in discount_names)
        )
    ]
    discounted_names = {
        target.id
        for node in ast.walk(total_function)
        if isinstance(node, ast.Assign)
        and node.value in subtraction_nodes
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    tax_after_discount = any(
        isinstance(node, ast.BinOp)
        and isinstance(node.op, ast.Mult)
        and (
            any(subtraction in ast.walk(node) for subtraction in subtraction_nodes)
            or any(_contains_name(node, name) for name in discounted_names)
        )
        and _contains_name(node, "tax_rate")
        for node in ast.walk(total_function)
    )
    round_calls = [
        node
        for node in ast.walk(total_function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "round"
    ]
    returned_nodes = {
        id(child)
        for node in ast.walk(total_function)
        if isinstance(node, ast.Return) and node.value is not None
        for child in ast.walk(node.value)
    }
    final_rounding = len(round_calls) == 1 and id(round_calls[0]) in returned_nodes
    result.update(
        {
            "cart_input_mutation_detected": mutation,
            "cart_discount_subtraction_detected": bool(subtraction_nodes),
            "cart_tax_after_discount_detected": tax_after_discount,
            "cart_final_only_rounding_detected": final_rounding,
        }
    )
    result["cart_combined_contract_satisfied"] = bool(
        not mutation and subtraction_nodes and tax_after_discount and final_rounding
    )
    return result


def _assignment_targets(node: ast.AST) -> list[ast.AST]:
    if isinstance(node, ast.Assign):
        return list(node.targets)
    if isinstance(node, (ast.AnnAssign, ast.AugAssign)):
        return [node.target]
    return []


def _subscript_root_name(node: ast.AST) -> str | None:
    current = node
    while isinstance(current, ast.Subscript):
        current = current.value
    return current.id if isinstance(current, ast.Name) else None


def _is_discount_amount_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "discount_amount"
    )


def _contains_discount_call(node: ast.AST) -> bool:
    return any(_is_discount_amount_call(child) for child in ast.walk(node))


def _contains_name(node: ast.AST, name: str) -> bool:
    return any(isinstance(child, ast.Name) and child.id == name for child in ast.walk(node))


def config_recursive_merge_debug_fields(
    files: dict[str, str], task_contract_checklist: list[str]
) -> dict[str, bool]:
    """Describe whether config sources use one guarded recursive merge path."""
    contract = "\n".join(task_contract_checklist).casefold()
    inactive = {
        "all_sources_use_recursive_merge": False,
        "top_level_none_guard_enforced": False,
        "config_manual_merge_rejected": False,
        "config_recursive_helper_detected": False,
    }
    if not _none_skip_context(contract) or not _all_sources_recursive_context(contract):
        return inactive
    for content in files.values():
        try:
            tree = ast.parse(content)
        except SyntaxError:
            continue
        functions = {
            node.name: node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        resolve = functions.get("resolve_config")
        helpers = {
            name: node
            for name, node in functions.items()
            if name != "resolve_config" and "merge" in name.casefold()
        }
        guarded_helpers = {
            name
            for name, node in helpers.items()
            if _function_skips_none(node) and _function_recursively_merges(node, name)
        }
        helper_detected = bool(guarded_helpers)
        all_sources = bool(
            resolve is not None
            and guarded_helpers
            and _resolve_routes_all_sources(resolve, guarded_helpers)
        )
        manual_merge = bool(
            resolve is not None and _resolve_contains_manual_merge(resolve)
        )
        shallow_update = any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "update"
            for node in ast.walk(tree)
        )
        rejected = shallow_update or manual_merge or not all_sources
        return {
            "all_sources_use_recursive_merge": all_sources,
            "top_level_none_guard_enforced": all_sources and helper_detected,
            "config_manual_merge_rejected": rejected,
            "config_recursive_helper_detected": helper_detected,
        }
    return {**inactive, "config_manual_merge_rejected": True}


def _function_skips_none(node: ast.AST) -> bool:
    return any(
        isinstance(item, ast.If)
        and isinstance(item.test, ast.Compare)
        and isinstance(item.test.left, ast.Name)
        and item.test.left.id == "value"
        and any(isinstance(operator, ast.Is) for operator in item.test.ops)
        and any(
            isinstance(comparator, ast.Constant) and comparator.value is None
            for comparator in item.test.comparators
        )
        and any(isinstance(statement, ast.Continue) for statement in item.body)
        for item in ast.walk(node)
    )


def _function_assigns_value(node: ast.AST) -> bool:
    return any(
        isinstance(item, ast.Assign)
        and isinstance(item.value, ast.Name)
        and item.value.id == "value"
        and any(isinstance(target, ast.Subscript) for target in item.targets)
        for item in ast.walk(node)
    )


def _function_recursively_merges(node: ast.AST, helper_name: str) -> bool:
    return any(
        isinstance(item, ast.Call)
        and isinstance(item.func, ast.Name)
        and item.func.id == helper_name
        for item in ast.walk(node)
    )


def _resolve_routes_all_sources(
    resolve: ast.AST, guarded_helpers: set[str]
) -> bool:
    expected_sources = {"defaults", "file_config", "env_config", "overrides"}
    for loop in (node for node in ast.walk(resolve) if isinstance(node, ast.For)):
        if not isinstance(loop.target, ast.Name) or loop.target.id != "source":
            continue
        source_names = {
            element.id
            for element in getattr(loop.iter, "elts", [])
            if isinstance(element, ast.Name)
        }
        if source_names != expected_sources:
            continue
        for item in ast.walk(loop):
            if not isinstance(item, ast.Assign) or len(item.targets) != 1:
                continue
            target = item.targets[0]
            call = item.value
            if (
                isinstance(target, ast.Name)
                and target.id == "result"
                and isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id in guarded_helpers
                and len(call.args) == 2
                and isinstance(call.args[0], ast.Name)
                and call.args[0].id == "result"
                and isinstance(call.args[1], ast.Name)
                and call.args[1].id == "source"
            ):
                return True
    return False


def _resolve_contains_manual_merge(resolve: ast.AST) -> bool:
    return any(
        (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"items", "update"}
        )
        or (
            isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Subscript) for target in node.targets)
        )
        for node in ast.walk(resolve)
    )


ROUTER_PARAM_LITERAL_REJECTION = (
    "Rejected candidate before tests; ':param' routes cannot be matched as "
    "literal regex strings. Use segment-by-segment matching and capture params."
)
ROUTER_PARAM_HELPER_REJECTION = (
    "Rejected candidate before tests; router matcher should return params dict "
    "or None, and match_route must accept empty params dict."
)
ROUTER_HIDDEN_SAFE_HINTS = [
    "Visible passed but hidden failed; check static route priority and trailing "
    "slash normalization.",
    "Empty params dict for static routes must be treated as a valid match.",
    "Try static routes before param routes.",
]


def check_router_param_literal_matching(
    files: dict[str, str], task_contract_checklist: list[str]
) -> str | None:
    """Reject path-param routers that treat ':id' as a literal regex segment."""
    contract = "\n".join(task_contract_checklist).casefold()
    if not _router_path_param_context(contract):
        return None
    for content in files.values():
        if _router_helper_return_shape_bug(content) or _router_truthy_match_check(
            content
        ):
            return ROUTER_PARAM_HELPER_REJECTION
        if _router_uses_segment_matching(content) or _router_compiles_param_regex(
            content
        ):
            continue
        if _uses_literal_re_fullmatch(content) or _returns_empty_params_only(content):
            return ROUTER_PARAM_LITERAL_REJECTION
    return None


def _first_match_position(pattern: str, text: str) -> int | None:
    match = re.search(pattern, text)
    return match.start() if match is not None else None


def extract_forbidden_patterns_from_failed_code(
    files: dict[str, str],
    failure_summary: str,
    test_assertion_checklist: list[str] | None = None,
    test_io_examples: list[str] | None = None,
    task_contract_checklist: list[str] | None = None,
) -> list[str]:
    """Derive generic implementation warnings from code and verifier evidence."""
    code = "\n".join(files.values())
    summary = failure_summary.lower()
    checklist = "\n".join(test_assertion_checklist or []).lower()
    examples = test_io_examples or []
    contract = "\n".join(task_contract_checklist or []).lower()
    hints: list[str] = []
    if "{**" in code and "expected" in summary and "got" in summary:
        hints.append(
            "Do not use dictionary unpacking merge like {**a, **b}; it overwrites "
            "duplicate keys."
        )
    if ".update(" in code and "expected" in summary and "got" in summary:
        hints.append(
            "Do not use dict.update for conflicting values; it overwrites duplicate "
            "keys."
        )
    if ("return False," in code or "return True," in code) and (
        "bool" in summary or "boolean" in summary
    ):
        hints.append("Do not return tuples when tests expect booleans.")
    normalization_evidence = (
        ("has no attribute" in summary and "lower" in summary)
        or "valueerror: yes" in summary
        or all(value in checklist for value in ("yes", "off", "1", "0"))
    )
    if ".lower()" in code and normalization_evidence:
        hints.extend(
            [
                "Handle bool inputs before string normalization.",
                "Normalize strings with strip().lower() before comparison.",
                "Include all truthy/falsy string values shown in the tests, not only "
                "'true' and 'false'.",
            ]
        )
    truthy_values = _string_inputs_for_result(examples, "True")
    falsy_values = _string_inputs_for_result(examples, "False")
    if truthy_values:
        hints.append(
            "The implementation must include all truthy examples shown: "
            f"{', '.join(truthy_values)}."
        )
    if falsy_values:
        hints.append(
            "The implementation must include all falsy examples shown: "
            f"{', '.join(falsy_values)}."
        )
    if truthy_values and falsy_values:
        hints.append("Do not stop at true/false only.")
    if "positive integer" in contract:
        hints.append("Check isinstance(size, int) as well as positivity.")
    if "raise typeerror" in contract and "raise ValueError" in code:
        hints.append("The required exception type is TypeError, not ValueError.")
    if (
        ("cannot be interpreted as an integer" in summary or "range(" in code)
        and "integer" in contract
    ):
        hints.append("Validate integer type before using range.")
    if ".lower()" in code and "bool" in contract:
        hints.append("Handle bool inputs before string normalization.")
    if "strip" in contract and ".lower()" in code and ".strip()" not in code:
        hints.append("Normalize strings with strip().lower(), not lower() alone.")
    if any(error in summary for error in ("syntaxerror", "indentationerror", "taberror", "nameerror")):
        hints.append("Do not preserve the malformed indentation or typo.")
    if check_dict_list_contract_shape(files, task_contract_checklist or []) is not None:
        hints.append(
            "Expected dictionary values are lists. Store every value in a list, "
            "even for keys that occur once."
        )
        hints.append(
            "Do not store first occurrence as a scalar. Initialize "
            "result[key] = [value]."
        )
    if check_none_overwrite_config_merge(files, task_contract_checklist or []) is not None:
        hints.append("Do not let None override an existing non-None value.")
        hints.append(
            "When merging config sources, skip None values unless the contract "
            "explicitly allows None overrides."
        )
    if "discount_amount" in code and ("discount" in contract or "tax" in contract):
        hints.append(
            "discount_amount may return the discount amount, not the discounted "
            "subtotal. Compute subtotal - discount_amount(subtotal, rate), then "
            "apply tax."
        )
    if (
        re.search(r"self\.tokens\s*=\s*0\b", code)
        and "capacity" in contract
        and "consume" in contract
    ):
        hints.append("Initialize tokens to capacity unless contract says empty.")
    if check_router_param_literal_matching(files, task_contract_checklist or []) is not None:
        hints.append(
            check_router_param_literal_matching(
                files, task_contract_checklist or []
            )
            or ROUTER_PARAM_LITERAL_REJECTION
        )
    if _router_path_param_context(contract) and "hidden scoring also failed" in summary:
        hints.extend(ROUTER_HIDDEN_SAFE_HINTS)
    hints.extend(extract_structural_repair_hints(failure_summary))
    hints.extend(extract_literal_format_hints(failure_summary))
    hints.extend(extract_repo_contract_repair_hints(task_contract_checklist or []))
    hints = _unique(hints)
    return hints


def extract_structural_repair_hints(failure_summary: str) -> list[str]:
    """Derive generic shape hints from expected/got assertion diagnostics."""
    expected, got = _extract_expected_got_values(failure_summary)
    if expected is None or got is None:
        return []
    hints: list[str] = []
    if (
        isinstance(expected, dict)
        and isinstance(got, dict)
        and expected
        and all(isinstance(value, list) for value in expected.values())
        and any(not isinstance(got.get(key), list) for key in expected)
    ):
        hints.append(
            "Expected dictionary values are lists. Store every value in a list, "
            "even for keys that occur once."
        )
        hints.append(
            "Do not store first occurrence as a scalar. Initialize "
            "result[key] = [value]."
        )
    if (
        isinstance(expected, dict)
        and any(isinstance(value, list) and len(value) > 1 for value in expected.values())
    ):
        hints.append("Repeated keys should append to the existing list.")
    if _duplicate_suffix_mismatch(expected, got):
        hints.extend(
            [
                "The first occurrence should keep the base value. Numeric suffixes "
                "start only on duplicates.",
                "Track seen base values. If a base value has not appeared, use the "
                "base value. If it has appeared n times, use f\"{base}-{n}\". "
                "Increment the count after choosing the output value.",
            ]
        )
    hints.extend(_literal_suffix_hints(expected))
    if _nested_dict_expected(expected) and _nested_dict_mismatch(expected, got):
        hints.append(
            "Do not replace nested dictionaries wholesale. Recursively merge nested "
            "dictionaries preserving earlier nested keys unless overridden."
        )
    if _none_overwrote_non_none(expected, got):
        hints.append("Do not let None override an existing non-None value.")
        hints.append(
            "When merging config sources, skip None values unless the contract "
            "explicitly allows None overrides."
        )
        hints.append("A None value overwrote an existing non-None config value.")
        hints.append(
            "Skip None values before assignment, including inside nested "
            "dictionaries."
        )
        hints.extend(_nested_none_overwrite_hints(expected, got))
    if isinstance(expected, (int, float)) and isinstance(got, (int, float)):
        hints.append(f"Expected final value {expected!r}, but got {got!r}.")
    return _unique(hints)


def extract_literal_format_hints(failure_summary: str) -> list[str]:
    """Infer exact literal formatting rules from expected assertion values."""
    expected, _ = _extract_expected_got_values(failure_summary)
    return _literal_suffix_hints(expected)


def extract_repo_contract_repair_hints(
    task_contract_checklist: list[str],
) -> list[str]:
    """Derive repo-style semantic repair hints from task contract wording."""
    contract = "\n".join(task_contract_checklist).lower()
    hints: list[str] = []
    if _duplicate_suffix_context(contract):
        hints.extend(
            [
                "For duplicate names or slugs, use the unsuffixed base value for "
                "the first occurrence and add -1, -2, etc. only for later "
                "duplicates.",
                "Track seen base slugs; choose the output slug before incrementing "
                "the duplicate counter.",
            ]
        )
    if _recursive_merge_context(contract):
        hints.extend(
            [
                "Implement a pure recursive merge. Copy dictionaries instead of "
                "mutating inputs. Apply sources in precedence order.",
                "Later sources override earlier sources, nested dictionaries should "
                "be recursively merged, and None values should not override existing "
                "values unless explicitly allowed.",
            ]
        )
    if _formula_order_context(contract):
        hints.extend(
            [
                "Compute subtotal without mutating items. Apply discount before "
                "tax. Apply tax after discount. Round the final result only.",
                "discount_amount may return the discount amount, not the "
                "discounted subtotal. Compute subtotal - "
                "discount_amount(subtotal, rate), then apply tax.",
                "Input items must not be mutated.",
            ]
        )
    if _stateful_clock_context(contract):
        hints.extend(
            [
                "Initialize tokens to capacity unless contract says empty.",
                "Use the injected clock as the only time source.",
                "Track last refill timestamp.",
                "Before every consume, refill by elapsed time multiplied by rate.",
                "Cap tokens at capacity.",
                "If enough tokens, subtract and return True.",
                "If insufficient, return False without subtracting.",
            ]
        )
    if _router_path_param_context(contract):
        hints.extend(
            [
                "Patterns with :name are path parameters.",
                "Split pattern and path into segments.",
                "Static segments must match exactly.",
                "Param segments capture value without slash.",
                "Return handler and params dict.",
                "Static routes outrank param routes.",
                "Normalize trailing slashes consistently.",
            ]
        )
    return _unique(hints)


def _extract_expected_got_values(text: str) -> tuple[object | None, object | None]:
    block = re.search(
        r"Expected:\s*\n(?P<expected>.+?)\s*\nGot:\s*\n(?P<got>.+?)(?:\n|$)",
        text,
        re.DOTALL,
    )
    if block is not None:
        return _literal(block.group("expected")), _literal(block.group("got"))
    inline = re.search(
        r"expected\s+(?P<expected>\{.*?\}),\s+got\s+(?P<got>\{.*?\})",
        text,
        re.IGNORECASE,
    )
    if inline is not None:
        expected_text, got_text = _balanced_inline_expected_got(text, inline.start())
        if expected_text is not None and got_text is not None:
            return _literal(expected_text), _literal(got_text)
        return _literal(inline.group("expected")), _literal(inline.group("got"))
    return None, None


def _balanced_inline_expected_got(
    text: str, start: int = 0
) -> tuple[str | None, str | None]:
    expected_marker = re.search(r"expected\s+", text[start:], re.IGNORECASE)
    if expected_marker is None:
        return None, None
    expected_start = start + expected_marker.end()
    expected_text, expected_end = _balanced_literal_at(text, expected_start)
    if expected_text is None or expected_end is None:
        return None, None
    got_marker = re.search(r",\s+got\s+", text[expected_end:], re.IGNORECASE)
    if got_marker is None:
        return None, None
    got_start = expected_end + got_marker.end()
    got_text, _ = _balanced_literal_at(text, got_start)
    return expected_text, got_text


def _balanced_literal_at(text: str, start: int) -> tuple[str | None, int | None]:
    while start < len(text) and text[start].isspace():
        start += 1
    if start >= len(text) or text[start] not in "{[":
        return None, None
    opener = text[start]
    closer = "}" if opener == "{" else "]"
    depth = 0
    in_string = False
    escaped = False
    quote = ""
    for index in range(start, len(text)):
        current = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif current == "\\":
                escaped = True
            elif current == quote:
                in_string = False
            continue
        if current in ("'", '"'):
            in_string = True
            quote = current
        elif current == opener:
            depth += 1
        elif current == closer:
            depth -= 1
            if depth == 0:
                return text[start : index + 1], index + 1
    return None, None


def _literal(value: str) -> object | None:
    try:
        return ast.literal_eval(value.strip())
    except (SyntaxError, ValueError):
        return None


def _contract_requires_dict_list_values(contract: str) -> bool:
    return (
        ("dictionary" in contract or "dict" in contract or "key maps" in contract)
        and (
            "list of values" in contract
            or "one-item lists" in contract
            or "maps to a list" in contract
            or "values are lists" in contract
        )
    )


def _duplicate_suffix_mismatch(expected: object, got: object) -> bool:
    if not isinstance(expected, list) or not isinstance(got, list):
        return False
    if not expected or not got:
        return False
    expected_values = _second_string_values(expected)
    got_values = _second_string_values(got)
    if not expected_values or not got_values:
        return False
    first_expected = expected_values[0]
    first_got = got_values[0]
    if re.search(r"-\d+$", first_expected):
        return False
    return _base_with_numeric_suffix(first_got) == first_expected


def _second_string_values(values: list[object]) -> list[str]:
    result: list[str] = []
    for value in values:
        if (
            isinstance(value, (tuple, list))
            and len(value) >= 2
            and isinstance(value[1], str)
        ):
            result.append(value[1])
    return result


def _all_string_values(value: object) -> list[str]:
    values: list[str] = []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        for key, child in value.items():
            values.extend(_all_string_values(key))
            values.extend(_all_string_values(child))
        return values
    if isinstance(value, (list, tuple, set)):
        for child in value:
            values.extend(_all_string_values(child))
    return values


def _literal_suffix_hints(expected: object) -> list[str]:
    values = _all_string_values(expected)
    value_set = set(values)
    examples: list[str] = []
    for value in values:
        match = re.match(r"(?P<base>.+)-(?P<number>[1-9]\d*)$", value)
        if match is None:
            continue
        base = match.group("base")
        if base in value_set:
            examples.append(value)
    if not examples:
        return []
    example = examples[0]
    direct = example.replace("-", "", 1)
    return [
        f"Use hyphen-number suffixes such as {example}.",
        f"Do not concatenate numbers directly as {direct}.",
        "Expected duplicate suffix format is hyphen-number.",
    ]


def _base_with_numeric_suffix(value: str) -> str | None:
    match = re.match(r"(?P<base>.+)-\d+$", value)
    return match.group("base") if match is not None else None


def _nested_dict_expected(value: object) -> bool:
    return isinstance(value, dict) and any(
        isinstance(child, dict) for child in value.values()
    )


def _nested_dict_mismatch(expected: object, got: object) -> bool:
    if not isinstance(expected, dict):
        return False
    return not isinstance(got, dict) or expected != got


def _none_overwrote_non_none(expected: object, got: object) -> bool:
    if isinstance(expected, dict) and isinstance(got, dict):
        for key, expected_value in expected.items():
            if key not in got:
                continue
            got_value = got[key]
            if expected_value is not None and got_value is None:
                return True
            if _none_overwrote_non_none(expected_value, got_value):
                return True
    return False


def _nested_none_overwrite_hints(
    expected: object, got: object, prefix: tuple[str, ...] = ()
) -> list[str]:
    if not isinstance(expected, dict) or not isinstance(got, dict):
        return []
    hints: list[str] = []
    for key, expected_value in expected.items():
        if key not in got:
            continue
        path = (*prefix, str(key))
        got_value = got[key]
        if expected_value is not None and got_value is None:
            hints.append(
                f"Nested key {'.'.join(path)} expected {expected_value!r} "
                "but got None."
            )
        hints.extend(_nested_none_overwrite_hints(expected_value, got_value, path))
    return hints


def _duplicate_suffix_context(contract: str) -> bool:
    return (
        ("duplicate" in contract or "deduplicate" in contract)
        and ("slug" in contract or "suffix" in contract or "name" in contract)
    )


def _recursive_merge_context(contract: str) -> bool:
    return (
        ("config" in contract or "precedence" in contract or "merge" in contract)
        and ("nested" in contract or "dict" in contract or "dictionary" in contract)
    ) or (
        "none values do not override" in contract
        or "later sources override earlier sources" in contract
    )


def _none_skip_context(contract: str) -> bool:
    return (
        "none values do not override" in contract
        or "none does not override" in contract
        or ("config" in contract and "none" in contract and "override" in contract)
    )


def _all_sources_recursive_context(contract: str) -> bool:
    return (
        "config" in contract
        or "precedence" in contract
        or "later sources override" in contract
    ) and (
        "nested" in contract
        or "recursive" in contract
        or "none values do not override" in contract
    )


def _shallow_config_update(content: str) -> bool:
    return bool(
        re.search(r"\bresult\s*\[\s*key\s*\]\s*\.\s*update\s*\(\s*value\s*\)", content)
        or re.search(r"\.update\s*\(\s*value\s*\)", content)
    )


def _uses_recursive_merge_helper(content: str) -> bool:
    return bool(
        re.search(r"def\s+_?\w*merge\w*\s*\(", content)
        and re.search(r"_?\w*merge\w*\s*\(\s*result\s*\[\s*key\s*\]\s*,\s*value\s*\)", content)
    )


def _formula_order_context(contract: str) -> bool:
    return (
        ("discount" in contract and "tax" in contract)
        or ("subtotal" in contract and "round" in contract)
    )


def _cart_combined_context(contract: str, code: str) -> bool:
    return (
        ("cart" in contract or "shopping" in contract or "checkout" in contract)
        and "discount" in contract
        and "tax" in contract
        and ("mutat" in contract or "unchanged" in contract)
        and "discount_amount" in code
    )


def _stateful_clock_context(contract: str) -> bool:
    return (
        ("clock" in contract or "time" in contract)
        and ("refill" in contract or "capacity" in contract or "consume" in contract)
    )


def _router_path_param_context(contract: str) -> bool:
    return (
        ("path params" in contract or "path parameters" in contract or ":id" in contract)
        and ("route" in contract or "router" in contract)
    )


def _uses_literal_re_fullmatch(content: str) -> bool:
    return bool(
        re.search(
            r"re\.fullmatch\s*\(\s*(?:normalize\s*\(\s*pattern\s*\)|pattern)\s*,\s*path\s*\)",
            content,
        )
    )


def _router_uses_segment_matching(content: str) -> bool:
    return bool(
        re.search(r"\.split\s*\(\s*['\"]/['\"]\s*\)", content)
        and re.search(r"\.startswith\s*\(\s*['\"]:", content)
    )


def _router_compiles_param_regex(content: str) -> bool:
    return "?P<" in content or bool(
        re.search(r"re\.sub\s*\([^)]*:", content, re.DOTALL)
        and re.search(r"re\.fullmatch\s*\(", content)
    )


def _returns_empty_params_only(content: str) -> bool:
    return "return handler, {}" in content and "params" not in content


def _router_helper_return_shape_bug(content: str) -> bool:
    return bool(re.search(r"return\s+['\"]static['\"]\s*,\s*params\b", content))


def _router_truthy_match_check(content: str) -> bool:
    return bool(
        re.search(
            r"(?P<name>[A-Za-z_]\w*)\s*=\s*_match_pattern\s*\([^\n]*\)\s*\n\s*if\s+(?P=name)\s*:",
            content,
        )
    )


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def _string_inputs_for_result(examples: list[str], result: str) -> list[str]:
    values: list[str] = []
    for example in examples:
        call, separator, expected = example.partition(" -> ")
        if not separator or expected != result:
            continue
        for match in re.finditer(r"(?P<quote>['\"])(?P<value>.*?)(?P=quote)", call):
            value = match.group("value")
            if value and value not in values:
                values.append(value)
    return values
