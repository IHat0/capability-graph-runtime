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
        hints.append(
            "Compute subtotal without mutating items. Apply discount before tax. "
            "Apply tax after discount. Round the final result only."
        )
    if _stateful_clock_context(contract):
        hints.append(
            "Use the injected clock as the only time source. Track last refill time. "
            "Refill by elapsed time multiplied by rate, cap tokens at capacity, and "
            "run refill before consume."
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
        return _literal(inline.group("expected")), _literal(inline.group("got"))
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


def _formula_order_context(contract: str) -> bool:
    return (
        ("discount" in contract and "tax" in contract)
        or ("subtotal" in contract and "round" in contract)
    )


def _stateful_clock_context(contract: str) -> bool:
    return (
        ("clock" in contract or "time" in contract)
        and ("refill" in contract or "capacity" in contract or "consume" in contract)
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
