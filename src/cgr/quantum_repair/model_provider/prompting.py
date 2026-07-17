"""Deterministic, sanitized prompt construction for baseline and CGR modes."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from cgr.science import sha256_fingerprint

from ..contracts import QuantumRepairDirective, SourceManifest
from .contracts import (
    ModelRepairPrompt,
    PromptSourceFile,
    ProviderBudget,
    seal_contract,
)
from .redaction import assert_prompt_safe


def build_model_prompt(
    *,
    directive: QuantumRepairDirective,
    source_root: Path,
    source_manifest: SourceManifest,
    public_task: dict[str, Any],
    guidance_mode: str,
    budget: ProviderBudget,
    context_maximum_bytes: int,
    observed_context_length: int,
    previous_patch_identities: tuple[str, ...] = (),
    previous_public_failure_categories: tuple[str, ...] = (),
    secrets: tuple[str, ...] = (),
) -> ModelRepairPrompt:
    files = _select_source_context(
        source_root,
        source_manifest,
        directive.allowed_edit_paths,
        context_maximum_bytes,
    )
    context_identity = tuple(
        {
            "relative_path": item.relative_path,
            "content_sha256": item.content_sha256,
        }
        for item in files
    )
    cgr_mode = guidance_mode == "cgr"
    values: dict[str, Any] = {
        "prompt_version": "quantum-sweagent-repair-prompt-v1",
        "guidance_mode": guidance_mode,
        "public_task_identity": sha256_fingerprint(public_task),
        "public_task": public_task,
        "source_manifest_sha256": source_manifest.source_manifest_sha256,
        "source_context_policy": "complete-allowed-files-or-explicit-overflow-v1",
        "source_context_sha256": sha256_fingerprint(context_identity),
        "source_files": files,
        "primary_finding_code": directive.primary_finding_code if cgr_mode else None,
        "additional_finding_codes": (
            directive.additional_finding_codes if cgr_mode else ()
        ),
        "sanitized_guidance": (directive.sanitized_explanations if cgr_mode else ()),
        # The comparator baseline must not receive invariants derived from CGR's
        # diagnosis.  These two public invariants apply to every candidate repair.
        "required_invariants": (
            directive.required_invariants
            if cgr_mode
            else ("candidate_reexecuted", "public_task_preserved")
        ),
        "allowed_paths": directive.allowed_edit_paths,
        "prohibited_paths": directive.prohibited_edit_paths,
        "maximum_files_changed": min(
            directive.maximum_files_changed, budget.maximum_files_changed
        ),
        "maximum_changed_lines": min(
            directive.maximum_changed_lines, budget.maximum_changed_lines
        ),
        "maximum_patch_bytes": min(
            directive.maximum_patch_bytes, budget.maximum_patch_bytes
        ),
        "attempt_number": directive.attempt_number,
        "remaining_attempt_budget": directive.remaining_attempt_budget,
        "previous_patch_identities": previous_patch_identities,
        "previous_public_failure_categories": (
            previous_public_failure_categories if cgr_mode else ()
        ),
        "instructions": (
            "Repair the actual candidate source defect; do not claim authorization.",
            "Use only public task values and candidate-owned computations.",
            "Do not copy a valid-control fixture or change a scenario selector to valid-control.",
            "Do not edit the candidate identifier, CGR, manifests, Dockerfiles, or dependency locks.",
            "Submit the smallest real source patch through SWE-agent's official submit mechanism.",
        ),
    }
    prompt = seal_contract(ModelRepairPrompt, values, "prompt_sha256")
    rendered = render_problem_statement(prompt)
    assert_prompt_safe(rendered, secrets)
    estimated_input_tokens = _estimated_tokens(rendered)
    if estimated_input_tokens > budget.maximum_input_tokens:
        raise ValueError("Provider prompt exceeds its input-token budget.")
    if estimated_input_tokens + budget.maximum_output_tokens > observed_context_length:
        raise ValueError(
            "Provider prompt and output reservation exceed live model context."
        )
    return prompt


def render_problem_statement(prompt: ModelRepairPrompt) -> str:
    """Render the canonical prompt as deterministic text consumed by SWE-agent."""
    heading = (
        "CGR-guided quantum candidate repair request."
        if prompt.guidance_mode == "cgr"
        else "Quantum candidate source repair request."
    )
    return (
        heading + "\n"
        "The JSON document below is the complete provider-visible task. "
        "It contains only public task material.\n" + prompt.to_canonical_json() + "\n"
    )


def _select_source_context(
    source_root: Path,
    source_manifest: SourceManifest,
    allowed_paths: tuple[str, ...],
    maximum_bytes: int,
) -> tuple[PromptSourceFile, ...]:
    entries = {item.relative_path: item for item in source_manifest.entries}
    selected: list[PromptSourceFile] = []
    total = 0
    for relative_path in sorted(set(allowed_paths)):
        entry = entries.get(relative_path)
        if entry is None:
            continue
        path = source_root / relative_path
        try:
            raw = path.read_bytes()
            content = raw.decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ValueError("Provider source context must be UTF-8 text.") from exc
        if hashlib.sha256(raw).hexdigest() != entry.content_sha256:
            raise ValueError("Provider source context changed after manifest creation.")
        total += len(raw)
        if total > maximum_bytes:
            raise ValueError(
                "Allowed source context exceeds deterministic context policy."
            )
        selected.append(
            PromptSourceFile(
                relative_path=relative_path,
                content_sha256=entry.content_sha256,
                content=content,
            )
        )
    if not selected:
        raise ValueError("Repair directive exposes no readable allowed source files.")
    return tuple(selected)


def _estimated_tokens(value: str) -> int:
    # Conservative deterministic bound used before the live provider request.
    return max((len(value.encode("utf-8")) + 2) // 3, 1)
