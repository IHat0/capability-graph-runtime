"""Deterministic repository-path supervision for native SWE-agent actions.

The validator deliberately accepts ordinary shell actions unless they clearly
reference a path outside the live task worktree.  It is not a shell parser:
the goal is to block unsafe, unambiguous repository-path hallucinations before
the official executor receives them.
"""

from __future__ import annotations

import json
import posixpath
import re
import shlex
import sys
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import Any


_EXTERNAL_PATH_RE = re.compile(r"(?<![\w.-])(?:~/(?:[^\s'\"`<>|;&()]+)|/(?:[^\s'\"`<>|;&()]+))")
_RELATIVE_ESCAPE_RE = re.compile(r"(?<![\w.-])(?:\.\.?/(?:[^\s'\"`<>|;&()]+))")
_SYSTEM_PATH_PREFIXES = ("/bin/", "/dev/", "/etc/", "/proc/", "/sys/", "/tmp/", "/usr/")


@dataclass(frozen=True)
class ActionValidation:
    allowed: bool
    feedback: str | None
    invalid_paths: tuple[str, ...]
    suggested_repository_relative_paths: tuple[str, ...]
    metrics: dict[str, Any]

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


def validate_repository_action(
    action: str,
    *,
    repository_root: str,
    repository_files: Iterable[str],
    prior_invalid_paths: Sequence[str] = (),
) -> ActionValidation:
    """Validate clear repository-path references in one proposed shell action."""
    root = _normalise_absolute_root(repository_root)
    files = tuple(sorted({_normalise_relative_path(path) for path in repository_files if path}))
    candidates = _extract_path_candidates(action)
    invalid = tuple(
        candidate
        for candidate in candidates
        if _is_external_repository_path(candidate, root)
    )
    suggestions = tuple(
        suggestion
        for path in invalid
        for suggestion in _unique_suffix_suggestions(path, files)
    )
    suggestions = tuple(dict.fromkeys(suggestions))
    repeated = tuple(path for path in invalid if path in prior_invalid_paths)
    metrics: dict[str, Any] = {
        "cgr_action_rejections": int(bool(invalid)),
        "invalid_repository_paths": list(invalid),
        "repeated_invalid_path_rejections": len(repeated),
        "corrective_root_feedback_sent": int(bool(invalid)),
        "suggested_repository_relative_paths": list(suggestions),
        "recovery_after_cgr_rejection": bool(prior_invalid_paths and not invalid),
        "first_valid_action_after_rejection": bool(prior_invalid_paths and not invalid),
    }
    if not invalid:
        return ActionValidation(True, None, (), (), metrics)
    return ActionValidation(
        False,
        _feedback(root, invalid, suggestions, repeated),
        invalid,
        suggestions,
        metrics,
    )


def _normalise_absolute_root(value: str) -> str:
    if not value.startswith("/"):
        raise ValueError("The active repository root must be an absolute POSIX path.")
    return posixpath.normpath(value)


def _normalise_relative_path(value: str) -> str:
    path = posixpath.normpath(value.lstrip("/"))
    return "" if path == "." else path


def _extract_path_candidates(action: str) -> tuple[str, ...]:
    candidates: list[str] = []
    for match in _EXTERNAL_PATH_RE.finditer(action):
        candidates.append(match.group(0).rstrip(".,:"))
    for match in _RELATIVE_ESCAPE_RE.finditer(action):
        candidates.append(match.group(0).rstrip(".,:"))
    try:
        tokens = shlex.split(action, posix=True)
    except ValueError:
        tokens = []
    for token in tokens:
        if token.startswith(("~/", "/", "../", "./")):
            candidates.append(token.rstrip(".,:"))
    return tuple(dict.fromkeys(candidate for candidate in candidates if candidate))


def _is_external_repository_path(candidate: str, root: str) -> bool:
    if candidate.startswith("~/"):
        return True
    if candidate.startswith("/"):
        if candidate.startswith(_SYSTEM_PATH_PREFIXES):
            return False
        return not _is_within_root(posixpath.normpath(candidate), root)
    if candidate.startswith(("../", "./")):
        return not _is_within_root(posixpath.normpath(posixpath.join(root, candidate)), root)
    return False


def _is_within_root(path: str, root: str) -> bool:
    try:
        return posixpath.commonpath((root, path)) == root
    except ValueError:
        return False


def _unique_suffix_suggestions(external_path: str, repository_files: Sequence[str]) -> tuple[str, ...]:
    if external_path.startswith("~/"):
        parts = PurePosixPath(external_path[2:]).parts
    else:
        parts = PurePosixPath(external_path).parts
    matches = []
    for path in repository_files:
        path_parts = PurePosixPath(path).parts
        overlap = min(len(parts), len(path_parts))
        if overlap and tuple(parts[-overlap:]) == path_parts[-overlap:]:
            matches.append(path)
    return (matches[0],) if len(matches) == 1 else ()


def _feedback(
    root: str,
    invalid_paths: Sequence[str],
    suggestions: Sequence[str],
    repeated: Sequence[str],
) -> str:
    lines = [
        "ACTION REJECTED BY CGR",
        "",
        "The proposed path is outside the active repository:",
        *invalid_paths,
        "",
        "The active repository root is:",
        root,
        "",
        "Use paths relative to that repository root. Inspect the exact source before editing it.",
    ]
    if suggestions:
        lines.extend(("", "A likely repository-relative target is:", *suggestions))
    if repeated:
        lines.extend(("", "You have already attempted this invalid external path. Do not use it again."))
    return "\n".join(lines)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        result = validate_repository_action(
            str(payload["action"]),
            repository_root=str(payload["repository_root"]),
            repository_files=payload.get("repository_files", ()),
            prior_invalid_paths=payload.get("prior_invalid_paths", ()),
        )
    except (TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": f"Invalid CGR action-validation request: {exc}"}))
        return 2
    print(json.dumps(result.to_json(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
