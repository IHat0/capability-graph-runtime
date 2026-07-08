"""Shared prompt construction for coding patch generation."""

import json

from .coding_task import CodingTask
from .python_test_runner import summarize_python_test_failure


def build_patch_prompt(task: CodingTask, extra_instruction: str = "") -> str:
    """Build a strict full-file JSON patch prompt."""
    files = json.dumps(task.files, indent=2)
    extra = f"\n{extra_instruction}\n" if extra_instruction else "\n"
    return (
        "Return only valid JSON. No markdown. No explanation outside JSON. "
        "Solve the coding issue below. JSON shape must be "
        '{"files":{"filename.py":"full file content"}}. '
        "Do not include markdown fences. Do not change file names unless asked. "
        "Preserve the requested function signatures and return types. Make the "
        "smallest change needed to pass tests. Every changed file value must "
        "contain the complete replacement file.\n"
        f"Issue:\n{task.issue}\nOriginal files:\n{files}{extra}"
        "Return only valid JSON in the required files shape. No markdown or "
        "explanation outside JSON. Preserve existing filenames unless asked."
    )


def build_repair_prompt(
    task: CodingTask,
    generated_files: dict[str, str],
    test_messages: list[str],
    critique: str = "",
    previous_repair_files: dict[str, str] | None = None,
    stronger_retry: bool = False,
) -> str:
    """Build a concise repair prompt grounded in concrete verifier feedback."""
    feedback = "\n".join(test_messages) or "No test output was captured."
    diagnostic = summarize_python_test_failure(test_messages)
    critique_section = f"\nCritique:\n{critique}" if critique else ""
    previous_section = (
        "\nPrevious repair files:\n"
        f"{json.dumps(previous_repair_files, indent=2)}"
        if previous_repair_files is not None
        else ""
    )
    retry_instruction = (
        "Your previous repair still failed. Re-read the tests carefully. Identify "
        "the exact semantic mismatch before writing code. "
        if stronger_retry
        else ""
    )
    return (
        "The generated code failed the tests below. Repair the code to pass the "
        f"tests. {retry_instruction}Do not change the public API. Do not add "
        "extra return values. "
        "Preserve public function names, requested signatures, and return types. "
        "Make the smallest code change that passes tests. Do not add explanations "
        "outside JSON. Return only valid JSON with complete replacement files in "
        'this shape: {"files":{"filename.py":"full file content"}}. Do not use '
        "markdown fences and do not change file names unless asked. The tests below "
        "are the source of truth. Infer the required behavior from them. Your job "
        "is to make the generated files pass these tests with the smallest possible "
        "change. Do not use a simpler implementation if it violates the test "
        "assertions.\n"
        f"Original task:\n{task.issue}\nOriginal files:\n"
        f"{json.dumps(task.files, indent=2)}\nCurrent generated files:\n"
        f"{json.dumps(generated_files, indent=2)}{previous_section}\n"
        f"Test files (source of truth):\n{json.dumps(task.test_files, indent=2)}\n"
        f"Diagnostic summary:\n{diagnostic}\nFull test output:\n{feedback}"
        f"{critique_section}\nReturn only valid JSON in the required files shape. "
        "No markdown or explanation outside JSON. Preserve existing filenames, "
        "function names, signatures unless tests require otherwise, and return "
        "types expected by tests."
    )


def build_format_retry_prompt(previous_answer: str) -> str:
    """Request one strict structural conversion without changing code logic."""
    return (
        "The previous answer was not valid JSON in the required format.\n\n"
        "Convert it into this exact JSON shape and return only JSON:\n\n"
        '{\n  "files": {\n    "filename.py": "full file content"\n  }\n}\n\n'
        "Do not use markdown fences. Do not add explanation. Do not change the "
        "code logic unless needed to make the JSON valid.\n\nPrevious answer:\n"
        f"{previous_answer}"
    )
