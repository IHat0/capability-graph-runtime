"""Fail-closed validation for pristine SWE-agent command-doc templates."""

from __future__ import annotations

import hashlib
import json
import re
import string
from collections.abc import Mapping
from pathlib import Path

from .contracts import ToolTemplateValidationArtifact, seal_contract

PROVIDER_TOOL_BUNDLES = (
    "tools/registry",
    "tools/search",
    "tools/windowed",
    "tools/review_on_submit_m",
)
_BUNDLE_PATTERN = re.compile(r"tools/[a-z0-9_]+")
_DOCSTRING_PATTERN = re.compile(r"^\s+docstring:\s*(.+?)\s*$")
_VARIABLE_NAME_PATTERN = re.compile(r"[A-Z][A-Z0-9_]*")
_WINDOW_MINIMUM = 1
_WINDOW_MAXIMUM = 1_000


class ToolTemplateConfigurationError(ValueError):
    """Sanitized provider bootstrap error raised before SWE-agent launch."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.model_request_count = 0
        self.total_tokens = 0
        self.trusted_evidence_exposure = 0


def validate_tool_template_configuration(
    *,
    source: Path,
    pristine_source_commit: str,
    configured_variables: Mapping[str, object],
    bundles: tuple[str, ...] = PROVIDER_TOOL_BUNDLES,
) -> ToolTemplateValidationArtifact:
    """Resolve pinned bundle docstrings without formatting untrusted values."""
    source = source.resolve(strict=True)
    tools_root = (source / "tools").resolve(strict=True)
    bundle_hashes: dict[str, str] = {}
    required: set[str] = set()
    for bundle in bundles:
        if not _BUNDLE_PATTERN.fullmatch(bundle):
            raise ToolTemplateConfigurationError(
                "sweagent_tool_template_configuration_failure",
                "Configured SWE-agent tool bundle identity is unsafe.",
            )
        config_path = (source / bundle / "config.yaml").resolve(strict=True)
        if config_path.parent.parent != tools_root:
            raise ToolTemplateConfigurationError(
                "sweagent_tool_template_configuration_failure",
                "Configured SWE-agent tool bundle escaped the pinned tools root.",
            )
        content = config_path.read_bytes()
        bundle_hashes[bundle] = hashlib.sha256(content).hexdigest()
        for docstring in _command_docstrings(content.decode("utf-8")):
            for _, field_name, format_spec, conversion in string.Formatter().parse(
                docstring
            ):
                if field_name is None:
                    continue
                if (
                    not _VARIABLE_NAME_PATTERN.fullmatch(field_name)
                    or format_spec
                    or conversion
                ):
                    raise ToolTemplateConfigurationError(
                        "sweagent_tool_template_configuration_failure",
                        "A bundled command docstring uses an unsafe template field.",
                    )
                required.add(field_name)
    allowed = {"WINDOW"}
    unknown_required = required - allowed
    if unknown_required or set(configured_variables) - allowed:
        raise ToolTemplateConfigurationError(
            "sweagent_tool_template_configuration_failure",
            "A bundled command docstring requires an unknown template variable.",
        )
    missing = required - set(configured_variables)
    if missing:
        raise ToolTemplateConfigurationError(
            "tool_configuration_template_missing_variable",
            "A bundled command docstring is missing a required template variable.",
        )
    sanitized: dict[str, int] = {}
    if "WINDOW" in configured_variables:
        window = configured_variables["WINDOW"]
        if (
            type(window) is not int
            or window < _WINDOW_MINIMUM
            or window > _WINDOW_MAXIMUM
        ):
            raise ToolTemplateConfigurationError(
                "sweagent_tool_template_configuration_failure",
                "The WINDOW template value must be a bounded positive integer.",
            )
        sanitized["WINDOW"] = window
    return seal_contract(
        ToolTemplateValidationArtifact,
        {
            "pristine_source_commit": pristine_source_commit,
            "configured_bundles": bundles,
            "bundle_configuration_sha256": bundle_hashes,
            "required_variables": tuple(sorted(required)),
            "configured_variables": sanitized,
            "validation_result": "passed",
            "failure_classification": None,
            "model_request_count": 0,
            "total_tokens": 0,
            "trusted_evidence_exposure": 0,
        },
        "validation_sha256",
    )


def _command_docstrings(source: str) -> tuple[str, ...]:
    values: list[str] = []
    for line in source.splitlines():
        match = _DOCSTRING_PATTERN.match(line)
        if match is None:
            continue
        scalar = match.group(1)
        if scalar.startswith('"'):
            try:
                value = json.loads(scalar)
            except json.JSONDecodeError as exc:
                raise ToolTemplateConfigurationError(
                    "sweagent_tool_template_configuration_failure",
                    "A bundled command docstring scalar is malformed.",
                ) from exc
        elif scalar.startswith("'") and scalar.endswith("'"):
            value = scalar[1:-1].replace("''", "'")
        elif scalar not in {"|", ">", "|-", ">-"}:
            value = scalar.split(" #", 1)[0].rstrip()
        else:
            raise ToolTemplateConfigurationError(
                "sweagent_tool_template_configuration_failure",
                "A bundled command docstring uses an unsupported scalar form.",
            )
        if not isinstance(value, str):
            raise ToolTemplateConfigurationError(
                "sweagent_tool_template_configuration_failure",
                "A bundled command docstring is not text.",
            )
        values.append(value)
    return tuple(values)
