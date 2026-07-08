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
    lowered = failure_diagnostic.lower()
    failed: list[str] = []
    for example in examples:
        call = example.partition(" -> ")[0]
        string_values = [
            match.group("value")
            for match in re.finditer(
                r"(?P<quote>['\"])(?P<value>.*?)(?P=quote)", call
            )
        ]
        if call.lower() in lowered or any(
            value.lower() in lowered for value in string_values
        ):
            failed.append(example)
    return failed
