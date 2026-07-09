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
    return hints


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
