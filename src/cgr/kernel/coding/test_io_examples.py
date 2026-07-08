"""Regex-based extraction of concrete input/output examples from Python tests."""

import re


_ASSERT_IS = re.compile(
    r"^\s*assert_is\(\s*(?P<call>[A-Za-z_]\w*\(.*\))\s*,\s*"
    r"(?P<expected>True|False|None|-?\d+(?:\.\d+)?|(?:'[^']*'|\"[^\"]*\"))\s*,"
)
_DIRECT_ASSERT = re.compile(
    r"^\s*assert\s+(?P<call>[A-Za-z_]\w*\(.*\))\s+"
    r"(?P<operator>is|==)\s+(?P<expected>.+?)\s*$"
)
_VALUE_ERROR = re.compile(
    r"try:\s*\n\s*(?P<call>[A-Za-z_]\w*\([^\n]+\))\s*\n"
    r"\s*except\s+ValueError\s*:",
    re.MULTILINE,
)


def extract_test_io_examples(test_files: dict[str, str]) -> list[str]:
    """Extract common asserted call outcomes without executing test code."""
    examples: list[str] = []

    def add(example: str) -> None:
        concise = " ".join(example.strip().split())
        if concise and concise not in examples:
            examples.append(concise)

    for source in test_files.values():
        for line in source.splitlines():
            match = _ASSERT_IS.match(line) or _DIRECT_ASSERT.match(line)
            if match is not None:
                add(f"{match.group('call')} -> {match.group('expected')}")
        for match in _VALUE_ERROR.finditer(source):
            add(f"{match.group('call')} -> raises ValueError")
    return examples


def infer_failed_test_io_examples(
    examples: list[str], failure_diagnostic: str
) -> list[str]:
    """Find required examples referenced by a concrete failure diagnostic."""
    lowered = _normalize_quotes(failure_diagnostic).casefold()
    failed: list[str] = []
    for example in examples:
        call = example.partition(" -> ")[0]
        string_values = [
            match.group("value")
            for match in re.finditer(
                r"(?P<quote>['\"])(?P<value>.*?)(?P=quote)", call
            )
        ]
        normalized_call = _normalize_quotes(call).casefold()
        if normalized_call in lowered or any(
            re.search(rf"(?<!\w){re.escape(value.casefold())}(?!\w)", lowered)
            for value in string_values
        ):
            failed.append(example)
    return failed


def check_example_literal_coverage(
    files: dict[str, str], test_io_examples: list[str]
) -> list[str]:
    """Reject obvious finite parser tables that omit required string examples."""
    code = "\n".join(files.values())
    lowered = code.casefold()
    normalization = (
        "str(" in code or "isinstance(value, str)" in code or ".lower()" in code
    )
    strips_and_lowers = ".strip()" in code and ".lower()" in code
    if not normalization:
        return []
    finite_membership = bool(
        re.search(r"\bin\s*[\{\[\(]", code)
        or re.search(r"==\s*['\"]", code)
    )
    missing: list[str] = []
    for example in test_io_examples:
        call, _, expected = example.partition(" -> ")
        if expected.startswith("raises "):
            continue
        values = _quoted_values(call)
        if not values:
            continue
        if all(value.casefold() in lowered for value in values):
            continue
        if strips_and_lowers and all(value.strip().casefold() in lowered for value in values):
            continue
        if not finite_membership:
            continue
        missing.append(example)
    return missing


def classify_boolean_string_examples(
    test_io_examples: list[str],
) -> tuple[list[str], list[str]]:
    """Return string inputs asserted to produce True and False."""
    truthy: list[str] = []
    falsy: list[str] = []
    for example in test_io_examples:
        call, separator, expected = example.partition(" -> ")
        if not separator or expected not in {"True", "False"}:
            continue
        target = truthy if expected == "True" else falsy
        for value in _quoted_values(call):
            if value not in target:
                target.append(value)
    return truthy, falsy


def classify_boolean_contract_examples(
    task_contract_checklist: list[str],
) -> tuple[list[str], list[str]]:
    """Extract truthy/falsy parser values from natural-language task contracts."""
    truthy: list[str] = []
    falsy: list[str] = []
    for item in task_contract_checklist:
        lowered = item.casefold()
        if "true values include" in lowered:
            _extend_unique(truthy, _values_after_include(item))
        if "false values include" in lowered:
            _extend_unique(falsy, _values_after_include(item))
    return truthy, falsy


def _extend_unique(target: list[str], values: list[str]) -> None:
    for value in values:
        if value and value not in target:
            target.append(value)


def _values_after_include(text: str) -> list[str]:
    _, _, tail = text.partition("include")
    tail = tail.strip().rstrip(".")
    return [
        value.strip().strip("'\"")
        for value in re.split(r",|\band\b", tail)
        if value.strip()
    ]


def _quoted_values(text: str) -> list[str]:
    return [
        match.group("value")
        for match in re.finditer(
            r"(?P<quote>['\"])(?P<value>.*?)(?P=quote)", text
        )
    ]


def _normalize_quotes(text: str) -> str:
    return text.replace('"', "'").replace("‘", "'").replace("’", "'")
