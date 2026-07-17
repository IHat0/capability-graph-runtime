"""Strict provider-independent structured patch validation and application."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from cgr.science.canonical import canonical_json

from .contracts import (
    PatchValidation,
    ProviderType,
    QuantumRepairDirective,
    QuantumRepairPatch,
    QuantumRepairPolicy,
    SourceManifest,
    StructuredEdit,
    sealed_values,
)
from .persistence import copy_source_tree, create_source_manifest


class RepairPatchRejected(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def create_patch(
    *,
    patch_identifier: str,
    directive: QuantumRepairDirective,
    source_manifest: SourceManifest,
    provider_identifier: str,
    provider_version: str,
    provider_type: ProviderType,
    edits: tuple[StructuredEdit, ...],
    rationale: str,
    claimed_addressed_findings: tuple[str, ...],
) -> QuantumRepairPatch:
    added = sum(_changed_line_count(edit.new_text) for edit in edits)
    deleted = sum(_changed_line_count(edit.old_text) for edit in edits)
    creation_identity = {
        "provider_identifier": provider_identifier,
        "provider_version": provider_version,
        "directive_sha256": directive.directive_sha256,
    }
    values: dict[str, Any] = {
        "patch_identifier": patch_identifier,
        "directive_sha256": directive.directive_sha256,
        "base_source_manifest_sha256": source_manifest.source_manifest_sha256,
        "provider_identifier": provider_identifier,
        "provider_version": provider_version,
        "provider_type": provider_type,
        "edits": edits,
        "changed_paths": tuple(sorted({item.relative_path for item in edits})),
        "added_lines": added,
        "deleted_lines": deleted,
        "rationale": rationale,
        "claimed_addressed_findings": tuple(sorted(set(claimed_addressed_findings))),
        "creation_evidence_sha256": hashlib.sha256(
            canonical_json(creation_identity).encode("utf-8")
        ).hexdigest(),
        "validation_status": "proposed",
    }
    return QuantumRepairPatch.model_validate(sealed_values(values, "patch_sha256"))


def validate_and_apply_patch(
    *,
    source_root: Path,
    destination_root: Path,
    source_manifest: SourceManifest,
    directive: QuantumRepairDirective,
    patch: QuantumRepairPatch,
    policy: QuantumRepairPolicy,
    prior_patch_hashes: set[str] | None = None,
    prior_source_hashes: set[str] | None = None,
    prohibited_source_hashes: set[str] | None = None,
) -> tuple[PatchValidation, SourceManifest]:
    prior_patch_hashes = prior_patch_hashes or set()
    prior_source_hashes = prior_source_hashes or set()
    prohibited_source_hashes = prohibited_source_hashes or set()
    _require(patch.directive_sha256 == directive.directive_sha256, "directive_mismatch")
    _require(
        patch.base_source_manifest_sha256 == source_manifest.source_manifest_sha256,
        "stale_base_source",
    )
    _require(patch.patch_sha256 not in prior_patch_hashes, "repeated_patch")
    _require(
        directive.primary_finding_code in patch.claimed_addressed_findings,
        "wrong_finding",
    )
    _require(len(patch.changed_paths) <= directive.maximum_files_changed, "file_quota")
    _require(len(patch.changed_paths) <= policy.maximum_files_changed, "file_quota")
    _require(
        patch.added_lines + patch.deleted_lines <= directive.maximum_changed_lines,
        "line_quota",
    )
    _require(
        patch.added_lines + patch.deleted_lines <= policy.maximum_changed_lines,
        "line_quota",
    )
    patch_bytes = len(patch.to_canonical_json().encode("utf-8"))
    _require(patch_bytes <= directive.maximum_patch_bytes, "patch_size")
    _require(patch_bytes <= policy.maximum_patch_bytes, "patch_size")
    allowed_paths = set(directive.allowed_edit_paths)
    for edit in patch.edits:
        path = edit.relative_path
        _require(path in allowed_paths, "path_out_of_scope")
        _require(Path(path).suffix in directive.allowed_file_types, "file_type")
        _require(Path(path).suffix in policy.allowed_file_types, "file_type")
        _require(
            not any(
                path == prefix or path.startswith(prefix + "/")
                for prefix in policy.prohibited_paths
            ),
            "prohibited_path",
        )
        _require("\x00" not in edit.old_text + edit.new_text, "binary_patch")
        lowered = edit.new_text.lower()
        _require("valid-control" not in lowered, "valid_control_shortcut")
        _require(
            'main("valid")' not in lowered and "main('valid')" not in lowered,
            "mode_shortcut",
        )
        _require(
            not any(
                token in lowered
                for token in (
                    "aws_access_key",
                    "credential",
                    "requests.get",
                    "socket.create_connection",
                    "urllib.request",
                )
            ),
            "prohibited_capability",
        )
    copy_source_tree(source_root, destination_root)
    try:
        for edit in patch.edits:
            target = destination_root / edit.relative_path
            _require(
                target.is_file() and not target.is_symlink(), "missing_edit_target"
            )
            current = target.read_text(encoding="utf-8")
            _require(current.count(edit.old_text) == 1, "malformed_edit")
            target.write_text(
                current.replace(edit.old_text, edit.new_text, 1), encoding="utf-8"
            )
        output_manifest = create_source_manifest(
            destination_root, source_manifest.source_identifier
        )
        candidate_identifier_retained = _candidate_identifier_retained(
            source_root, destination_root
        )
        _require(candidate_identifier_retained, "candidate_identity_edit")
        _require(
            output_manifest.source_manifest_sha256
            != source_manifest.source_manifest_sha256,
            "no_op_patch",
        )
        _require(
            output_manifest.source_manifest_sha256 not in prior_source_hashes,
            "repair_oscillation",
        )
        _require(
            output_manifest.source_manifest_sha256 not in prohibited_source_hashes,
            "valid_control_copy",
        )
    except Exception:
        _remove_failed_destination(destination_root)
        raise
    validation = PatchValidation(
        patch_sha256=patch.patch_sha256,
        base_source_manifest_sha256=source_manifest.source_manifest_sha256,
        validated=True,
        checks=(
            "base_identity",
            "directive_identity",
            "edit_scope",
            "patch_quotas",
            "shortcut_protection",
            "fresh_source_identity",
        ),
        output_source_manifest_sha256=output_manifest.source_manifest_sha256,
        source_provenance="fresh-copy-plus-structured-edits",
        unchanged_file_ratio=_unchanged_file_ratio(source_manifest, output_manifest),
        control_source_match=False,
        candidate_identifier_retained=candidate_identifier_retained,
    )
    return validation, output_manifest


def validate_claimed_patch_hash(payload: dict[str, Any]) -> QuantumRepairPatch:
    return QuantumRepairPatch.model_validate(payload)


def _changed_line_count(text: str) -> int:
    return len(text.splitlines()) or (1 if text else 0)


def _unchanged_file_ratio(before: SourceManifest, after: SourceManifest) -> float:
    before_hashes = {
        entry.relative_path: entry.content_sha256 for entry in before.entries
    }
    after_hashes = {
        entry.relative_path: entry.content_sha256 for entry in after.entries
    }
    paths = set(before_hashes) | set(after_hashes)
    if not paths:
        return 1.0
    unchanged = sum(
        1 for path in paths if before_hashes.get(path) == after_hashes.get(path)
    )
    return unchanged / len(paths)


def _candidate_identifier_retained(before: Path, after: Path) -> bool:
    path = Path("repair-config.json")
    if not (before / path).is_file() and not (after / path).is_file():
        return True
    try:
        import json

        before_value = json.loads((before / path).read_text(encoding="utf-8"))
        after_value = json.loads((after / path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return False
    identifier = before_value.get("candidate_identifier")
    return (
        isinstance(identifier, str)
        and after_value.get("candidate_identifier") == identifier
    )


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise RepairPatchRejected(code, f"Repair patch rejected: {code}.")


def _remove_failed_destination(path: Path) -> None:
    if not path.exists():
        return
    for child in sorted(
        path.rglob("*"), key=lambda item: len(item.parts), reverse=True
    ):
        if child.is_file() or child.is_symlink():
            child.unlink()
        elif child.is_dir():
            child.rmdir()
    path.rmdir()
