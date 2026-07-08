"""Deterministic requirement extraction from coding task descriptions."""

import re


def extract_task_contract_checklist(description: str) -> list[str]:
    """Split a concise task description into stable contract requirements."""
    normalized = " ".join(description.strip().split())
    if not normalized:
        return []
    clauses = re.split(r"\s*;\s*|(?<=[.!?])\s+", normalized)
    requirements: list[str] = []
    for clause in clauses:
        item = clause.strip().rstrip(".")
        if item and item not in requirements:
            requirements.append(item)
    return requirements
