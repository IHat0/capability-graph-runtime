"""Shared verification and deterministic selection for coding-agent patches."""

import re

from .coding_patch import CodingPatch
from .coding_task import CodingTask
from .python_test_runner import PythonTestRunner


def verify_patch(
    task: CodingTask, patch: CodingPatch
) -> tuple[bool, list[str]] | None:
    """Run task tests when available, otherwise report no verification contract."""
    if not task.test_files or not task.test_commands:
        return None
    return PythonTestRunner().run(
        patch.files,
        task.test_files,
        task.test_commands,
    )


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


def _patch_size(patch: CodingPatch) -> tuple[int, int]:
    return len(patch.files), sum(len(name) + len(text) for name, text in patch.files.items())


def patch_fingerprint(patch: CodingPatch) -> tuple[tuple[str, str], ...]:
    """Return a stable exact-content identity for repetition detection."""
    return tuple(sorted(patch.files.items()))


def extract_forbidden_patterns_from_failed_code(
    files: dict[str, str],
    failure_summary: str,
    test_assertion_checklist: list[str] | None = None,
    test_io_examples: list[str] | None = None,
) -> list[str]:
    """Derive generic implementation warnings from code and verifier evidence."""
    code = "\n".join(files.values())
    summary = failure_summary.lower()
    checklist = "\n".join(test_assertion_checklist or []).lower()
    examples = test_io_examples or []
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
    return hints


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
