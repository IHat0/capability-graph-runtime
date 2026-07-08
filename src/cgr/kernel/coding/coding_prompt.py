"""Shared prompt construction for coding patch generation."""

import json

from .coding_task import CodingTask


def build_patch_prompt(task: CodingTask, extra_instruction: str = "") -> str:
    """Build a strict full-file JSON patch prompt."""
    files = json.dumps(task.files, indent=2)
    extra = f"\n{extra_instruction}\n" if extra_instruction else "\n"
    return (
        "Solve the coding issue below. Return only valid JSON. JSON shape must be "
        '{"files":{"filename.py":"full file content"}}. '
        "Do not include markdown fences. Do not change file names unless asked. "
        "Preserve the requested function signatures and return types. Make the "
        "smallest change needed to pass tests. Every changed file value must "
        "contain the complete replacement file.\n"
        f"Issue:\n{task.issue}\nOriginal files:\n{files}{extra}"
    )


def build_repair_prompt(
    task: CodingTask,
    generated_files: dict[str, str],
    test_messages: list[str],
    critique: str = "",
) -> str:
    """Build a concise repair prompt grounded in concrete verifier feedback."""
    feedback = "\n".join(test_messages) or "No test output was captured."
    critique_section = f"\nCritique:\n{critique}" if critique else ""
    return (
        "The generated code failed the tests below. Repair the code to pass the "
        "tests. Do not change the public API. Do not add extra return values. "
        "Preserve public function names, requested signatures, and return types. "
        "Make the smallest code change that passes tests. Do not add explanations "
        "outside JSON. Return only valid JSON with complete replacement files in "
        'this shape: {"files":{"filename.py":"full file content"}}. Do not use '
        "markdown fences and do not change file names unless asked.\n"
        f"Original task:\n{task.issue}\nOriginal files:\n"
        f"{json.dumps(task.files, indent=2)}\nCurrent generated files:\n"
        f"{json.dumps(generated_files, indent=2)}\nTest failures:\n{feedback}"
        f"{critique_section}"
    )
