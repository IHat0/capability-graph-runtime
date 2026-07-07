"""Shared prompt construction for coding patch generation."""

import json

from .coding_task import CodingTask


def build_patch_prompt(task: CodingTask, extra_instruction: str = "") -> str:
    """Build a strict full-file JSON patch prompt."""
    files = json.dumps(task.files, indent=2)
    extra = f"\n{extra_instruction}\n" if extra_instruction else "\n"
    return (
        "Solve the coding issue below. Return ONLY valid JSON with this shape: "
        '{"files":{"filename":"full patched content"},'
        '"explanation":"short explanation"}. '
        "Every changed file value must contain the complete patched file.\n"
        f"Issue:\n{task.issue}\nOriginal files:\n{files}{extra}"
    )
