"""Official prediction collection and deterministic structured-patch extraction."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from cgr.swebench.swe_agent_adapter import collect_official_patch

from ..contracts import (
    QuantumRepairDirective,
    QuantumRepairPatch,
    SourceManifest,
    StructuredEdit,
)
from ..patches import create_patch
from ..persistence import copy_source_tree
from .contracts import (
    ProviderBudget,
    ProviderTrajectoryManifest,
    TrajectoryArtifact,
    seal_contract,
)
from .redaction import sanitize_artifact


def extract_official_patch(
    *,
    output_directory: Path,
    source_root: Path,
    source_manifest: SourceManifest,
    directive: QuantumRepairDirective,
    provider_identifier: str,
    provider_version: str,
    budget: ProviderBudget,
    extraction_root: Path,
    patch_identifier: str,
) -> tuple[QuantumRepairPatch, str, Path]:
    unified_diff, prediction_path = collect_official_patch(output_directory)
    normalized = unified_diff.replace("\r\n", "\n")
    raw = normalized.encode("utf-8")
    if not normalized.strip():
        raise ValueError("Official SWE-agent prediction is empty.")
    if len(raw) > budget.maximum_patch_bytes:
        raise ValueError("Official SWE-agent patch exceeds its byte budget.")
    if "GIT binary patch" in normalized or "Binary files " in normalized:
        raise ValueError("Binary model patches are prohibited.")
    paths = _diff_paths(normalized)
    if not paths:
        raise ValueError("Official prediction contains no valid unified diff.")
    workspace = extraction_root / "workspace"
    copy_source_tree(source_root, workspace)
    _git(workspace, "init", "-q")
    _git(workspace, "config", "user.email", "cgr@invalid.local")
    _git(workspace, "config", "user.name", "CGR Provider Extractor")
    _git(workspace, "add", "--all")
    _git(workspace, "commit", "-q", "-m", "source snapshot")
    check = subprocess.run(
        ["git", "apply", "--check", "--whitespace=nowarn", "-"],
        cwd=workspace,
        input=normalized,
        text=True,
        capture_output=True,
        check=False,
    )
    if check.returncode:
        raise ValueError("Official SWE-agent patch is malformed, truncated, or stale.")
    applied = subprocess.run(
        ["git", "apply", "--whitespace=nowarn", "-"],
        cwd=workspace,
        input=normalized,
        text=True,
        capture_output=True,
        check=False,
    )
    if applied.returncode:
        raise ValueError(
            "Official SWE-agent patch could not be applied deterministically."
        )
    edits: list[StructuredEdit] = []
    for path in paths:
        before = source_root / path
        after = workspace / path
        if not before.is_file() or not after.is_file():
            raise ValueError(
                "V1 provider patches may only modify existing regular files."
            )
        try:
            old_text = before.read_text(encoding="utf-8")
            new_text = after.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Provider patches must modify UTF-8 text files.") from exc
        edits.append(
            StructuredEdit(relative_path=path, old_text=old_text, new_text=new_text)
        )
    patch = create_patch(
        patch_identifier=patch_identifier,
        directive=directive,
        source_manifest=source_manifest,
        provider_identifier=provider_identifier,
        provider_version=provider_version,
        provider_type="swe_agent",
        edits=tuple(edits),
        rationale="Apply the source repair proposed through pristine SWE-agent's official prediction artifact.",
        claimed_addressed_findings=(directive.primary_finding_code,),
    )
    return patch, hashlib.sha256(raw).hexdigest(), prediction_path


def redact_trajectory(
    *,
    invocation_identifier: str,
    raw_root: Path,
    portable_root: Path,
    prediction_path: Path,
    secrets: tuple[str, ...],
) -> ProviderTrajectoryManifest:
    artifacts: list[TrajectoryArtifact] = []
    model_calls = 0
    tool_calls = 0
    input_tokens = 0
    output_tokens = 0
    suffixes = {".traj", ".pred", ".json", ".jsonl", ".log", ".patch"}
    for source in sorted(path for path in raw_root.rglob("*") if path.is_file()):
        if source.suffix.lower() not in suffixes:
            continue
        relative = source.relative_to(raw_root).as_posix()
        destination = portable_root / relative
        data = sanitize_artifact(source, destination, secrets)
        artifacts.append(
            TrajectoryArtifact(
                relative_path=relative,
                content_sha256=hashlib.sha256(data).hexdigest(),
                byte_size=len(data),
            )
        )
        try:
            value = json.loads(data)
        except json.JSONDecodeError:
            continue
        usage = _usage(value)
        model_calls += usage[0]
        input_tokens += usage[1]
        output_tokens += usage[2]
        tool_calls += _tool_calls(value)
    if not artifacts:
        raise ValueError("SWE-agent produced no redaction-safe trajectory artifacts.")
    try:
        extraction_source = prediction_path.relative_to(raw_root).as_posix()
    except ValueError as exc:
        raise ValueError(
            "Prediction artifact is outside the official output directory."
        ) from exc
    values = {
        "provider_invocation_identifier": invocation_identifier,
        "artifacts": tuple(artifacts),
        "tool_call_count": tool_calls,
        "model_call_count": model_calls,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "patch_extraction_source": extraction_source,
    }
    return seal_contract(
        ProviderTrajectoryManifest, values, "complete_trajectory_sha256"
    )


def _diff_paths(patch: str) -> tuple[str, ...]:
    paths: list[str] = []
    for line in patch.splitlines():
        if not line.startswith("diff --git "):
            continue
        fields = line.split()
        if (
            len(fields) != 4
            or not fields[2].startswith("a/")
            or not fields[3].startswith("b/")
        ):
            raise ValueError("Unified diff has malformed file headers.")
        before = fields[2][2:]
        after = fields[3][2:]
        if before != after:
            raise ValueError("V1 provider patches cannot rename source files.")
        # StructuredEdit performs the canonical traversal/absolute-path validation.
        StructuredEdit(relative_path=after, old_text="x", new_text="y")
        paths.append(after)
    return tuple(sorted(set(paths)))


def _git(root: Path, *arguments: str) -> None:
    process = subprocess.run(
        ["git", "-C", str(root), *arguments],
        capture_output=True,
        text=True,
        check=False,
    )
    if process.returncode:
        raise ValueError("Could not prepare deterministic patch extraction workspace.")


def _usage(value: Any) -> tuple[int, int, int]:
    calls = inputs = outputs = 0
    if isinstance(value, dict):
        usage = value.get("usage")
        if isinstance(usage, dict):
            prompt = usage.get("prompt_tokens", 0)
            completion = usage.get("completion_tokens", 0)
            if isinstance(prompt, int) and isinstance(completion, int):
                calls += 1
                inputs += max(prompt, 0)
                outputs += max(completion, 0)
        for child in value.values():
            child_usage = _usage(child)
            calls += child_usage[0]
            inputs += child_usage[1]
            outputs += child_usage[2]
    elif isinstance(value, list):
        for child in value:
            child_usage = _usage(child)
            calls += child_usage[0]
            inputs += child_usage[1]
            outputs += child_usage[2]
    return calls, inputs, outputs


def _tool_calls(value: Any) -> int:
    if isinstance(value, dict):
        own = int("action" in value and isinstance(value.get("action"), str))
        return own + sum(_tool_calls(item) for item in value.values())
    if isinstance(value, list):
        return sum(_tool_calls(item) for item in value)
    return 0
