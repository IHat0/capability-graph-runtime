"""Shared prompt construction for coding patch generation."""

import json

from .coding_task import CodingTask
from .python_test_runner import (
    extract_syntax_error_summary,
    summarize_python_test_failure,
)
from .task_contract import extract_task_contract_checklist
from .test_assertion_checklist import extract_test_assertion_checklist
from .test_io_examples import (
    extract_test_io_examples,
    infer_failed_test_io_examples,
)


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
    known_failures: list[tuple[str, dict[str, str], str]] | None = None,
    forbidden_pattern_hints: list[str] | None = None,
    repair_plan: str = "",
    variant_instruction: str = "",
    failed_required_examples: list[str] | None = None,
) -> str:
    """Build a concise repair prompt grounded in concrete verifier feedback."""
    feedback = "\n".join(test_messages) or "No test output was captured."
    diagnostic = summarize_python_test_failure(test_messages)
    syntax_error = extract_syntax_error_summary(test_messages)
    syntax_section = (
        "SYNTAX REPAIR REQUIRED:\nYour previous code does not even parse. First "
        "produce syntactically valid Python. Then satisfy the tests.\n"
        f"{syntax_error}\nDo not preserve the malformed indentation or typo.\n"
        if syntax_error
        else ""
    )
    contract = extract_task_contract_checklist(task.issue)
    contract_text = "\n".join(f"- {item}" for item in contract)
    hidden_summaries = [
        message
        for message in test_messages
        if message.startswith("Hidden scoring also failed.")
    ]
    hidden_section = (
        "\n".join(hidden_summaries) + "\n" if hidden_summaries else ""
    )
    checklist = extract_test_assertion_checklist(task.test_files)
    checklist_text = "\n".join(f"- {item}" for item in checklist)
    io_examples = extract_test_io_examples(task.test_files)
    io_examples_text = "\n".join(f"- {item}" for item in io_examples)
    failed_examples = list(failed_required_examples or [])
    for example in infer_failed_test_io_examples(io_examples, diagnostic):
        if example not in failed_examples:
            failed_examples.append(example)
    failed_examples_section = (
        "FAILED REQUIRED EXAMPLES THAT MUST BE FIXED NOW:\n- "
        + "\n- ".join(failed_examples)
        if failed_examples
        else "FAILED REQUIRED EXAMPLES THAT MUST BE FIXED NOW:\n- None identified."
    )
    self_check = "\n".join(f"[ ] {example}" for example in io_examples)
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
    merge_evidence = (
        diagnostic + "\n" + "\n".join(task.test_files.values())
    ).lower()
    merge_warning = (
        "Do not use a shallow dictionary merge such as update(), {**a, **b}, or "
        "result.update(b) when the tests show overlapping values must be combined. "
        if any(
            marker in merge_evidence
            for marker in ("merge_counts", "overlapping", "summed", "counts")
        )
        else ""
    )
    known_section = ""
    if known_failures:
        entries = []
        for index, (candidate_id, files, summary) in enumerate(known_failures, 1):
            previews = {
                filename: content[:1000] for filename, content in files.items()
            }
            entries.append(
                f"{index}. {candidate_id} failed because:\n{summary}\n"
                "Do not repeat this implementation.\nForbidden file content "
                f"preview:\n{json.dumps(previews, indent=2)}"
            )
        known_section = "\nKnown failing implementations:\n" + "\n".join(entries)
    hints_section = (
        "\nForbidden implementation patterns:\n- "
        + "\n- ".join(forbidden_pattern_hints)
        if forbidden_pattern_hints
        else ""
    )
    plan_section = f"\nRepair plan:\n{repair_plan}" if repair_plan else ""
    return (
        "The current implementation failed tests. Repair against the full "
        "contract below.\n"
        f"{syntax_section}"
        f"{failed_examples_section}\n"
        "Your previous repair failed the examples above. The next answer is "
        "invalid unless it satisfies every failed required example. If a failed "
        "example contains a string literal such as 'off', that literal or an "
        "equivalent normalized handling path must be present in the "
        "implementation.\n"
        f"ALL REQUIRED INPUT/OUTPUT EXAMPLES:\n{io_examples_text}\n"
        "You must implement every required input/output example exactly. Do not "
        "produce code that only fixes the latest failing example. If the examples "
        "show several accepted string values, include all of them in the "
        "implementation.\n"
        f"Test assertion checklist:\n{checklist_text}\n"
        f"Task contract checklist:\n{contract_text}\n"
        "The task contract is source of truth alongside tests. Repair must satisfy "
        "every contract item.\n"
        "Do not stop after fixing only the first traceback. The repaired code "
        "must satisfy every checklist item. Before writing code, infer all "
        "accepted inputs and required outputs from the checklist.\n"
        f"Latest failure diagnostic:\n{diagnostic}\n"
        f"{hidden_section}"
        f"{retry_instruction}Before writing code, infer the semantic mismatch from "
        "the expected/got values. Then output only the corrected JSON. The tests "
        "are the source of truth. Do not repeat the previous implementation if the "
        "diagnostic says it overwrote or lost expected values. "
        f"{merge_warning}{variant_instruction}\n"
        f"{known_section}{hints_section}\n"
        f"Test files (source of truth):\n{json.dumps(task.test_files, indent=2)}\n"
        f"Current generated files:\n{json.dumps(generated_files, indent=2)}"
        f"{previous_section}\nOriginal task:\n{task.issue}\nOriginal files:\n"
        f"{json.dumps(task.files, indent=2)}"
        f"{plan_section}\nFull test output:\n{feedback}"
        f"{critique_section}\nDo not change the public API. Do not add extra "
        "return values. Preserve existing filenames, public function names, signatures "
        "unless tests require otherwise, and return types expected by tests. Make "
        "the smallest code change that passes tests. Return only valid JSON with "
        'this shape: {"files":{"filename.py":"full file content"}}. No markdown. '
        "No explanation outside JSON. Implement the repair plan exactly. Do not "
        "repeat any known failing implementation."
        f"\nBefore finalizing, mentally check each required example:\n{self_check}\n"
        "Your JSON answer must implement all of them."
    )


def build_repair_plan_prompt(
    task: CodingTask,
    failed_files: dict[str, str],
    diagnostic: str,
) -> str:
    """Ask a critic for a semantic plan before requesting replacement code."""
    checklist = extract_test_assertion_checklist(task.test_files)
    checklist_text = "\n".join(f"- {item}" for item in checklist)
    io_examples = extract_test_io_examples(task.test_files)
    io_examples_text = "\n".join(f"- {item}" for item in io_examples)
    contract = extract_task_contract_checklist(task.issue)
    contract_text = "\n".join(f"- {item}" for item in contract)
    return (
        "Given the failing tests and known failing code, identify the exact semantic "
        "bug and the minimal algorithmic fix. Do not write code yet. Be specific. "
        "List every required behavior from the checklist. Then identify what the "
        "current code misses.\n"
        f"Required input/output examples:\n{io_examples_text}\n"
        f"Test assertion checklist:\n{checklist_text}\n"
        f"Task contract checklist:\n{contract_text}\n"
        f"Task:\n{task.issue}\nFailed files:\n"
        f"{json.dumps(failed_files, indent=2)}\nTest source:\n"
        f"{json.dumps(task.test_files, indent=2)}\nDiagnostic:\n{diagnostic}"
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
