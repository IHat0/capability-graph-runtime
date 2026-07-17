"""Deterministic capture and classification of scientific runtime warnings."""

from __future__ import annotations

import importlib.metadata
import re
import warnings
from collections import Counter
from contextlib import contextmanager
from typing import Iterator, Literal, Self

from pydantic import Field, field_validator, model_validator

from cgr.science import CanonicalModel
from cgr.science.canonical import validate_identifier

_SPACE = re.compile(r"\s+")
_ADDRESS = re.compile(r"0x[0-9a-fA-F]+")
_WINDOWS_PATH = re.compile(r"[A-Za-z]:[\\/][^\s:]+")
_POSIX_PATH = re.compile(r"/(?:[^\s/:]+/)+[^\s:]+")


class CompatibilityWarning(CanonicalModel):
    code: str
    category: str
    origin_module: str
    normalized_message: str = Field(min_length=1, max_length=2048)
    count: int = Field(gt=0)
    severity: Literal["info", "warning", "error"]
    blocking: bool
    suggested_action: str
    dependency_name: str
    dependency_version: str
    first_observed_phase: str

    @field_validator(
        "code",
        "category",
        "origin_module",
        "suggested_action",
        "dependency_name",
        "dependency_version",
        "first_observed_phase",
    )
    @classmethod
    def valid_identifiers(cls, value: str) -> str:
        return validate_identifier(value)


class CompatibilityWarningEvidence(CanonicalModel):
    schema_version: str = "cgr.compatibility-warnings/1.0.0"
    warnings: tuple[CompatibilityWarning, ...] = ()
    status: Literal["clean", "warnings", "blocking"]

    @field_validator("schema_version")
    @classmethod
    def valid_schema(cls, value: str) -> str:
        if value != "cgr.compatibility-warnings/1.0.0":
            raise ValueError("Unsupported compatibility-warning schema.")
        return value

    @field_validator("warnings")
    @classmethod
    def order_warnings(
        cls, value: tuple[CompatibilityWarning, ...]
    ) -> tuple[CompatibilityWarning, ...]:
        return tuple(sorted(value, key=lambda item: item.fingerprint))

    @model_validator(mode="after")
    def status_matches_findings(self) -> Self:
        expected = (
            "blocking"
            if any(item.blocking for item in self.warnings)
            else "warnings" if self.warnings else "clean"
        )
        if self.status != expected:
            raise ValueError("Compatibility status does not match warning evidence.")
        return self


class CapturedWarning:
    def __init__(self, warning: warnings.WarningMessage, phase: str) -> None:
        self.warning = warning
        self.phase = phase


@contextmanager
def capture_warnings(phase: str) -> Iterator[list[CapturedWarning]]:
    validate_identifier(phase, label="warning phase")
    captured: list[CapturedWarning] = []
    with warnings.catch_warnings(record=True) as records:
        warnings.simplefilter("always")
        yield captured
    captured.extend(CapturedWarning(item, phase) for item in records)


def normalize_warning_message(message: str) -> str:
    normalized = _ADDRESS.sub("<address>", str(message))
    normalized = _WINDOWS_PATH.sub("<path>", normalized)
    normalized = _POSIX_PATH.sub("<path>", normalized)
    return _SPACE.sub(" ", normalized).strip()


def warning_evidence(
    captured: list[CapturedWarning],
    *,
    blocking_codes: frozenset[str] = frozenset(),
) -> CompatibilityWarningEvidence:
    classified = [_classify(item) for item in captured]
    counts: Counter[tuple[str, ...]] = Counter(
        (
            item["code"],
            item["category"],
            item["origin_module"],
            item["normalized_message"],
            item["suggested_action"],
            item["dependency_name"],
            item["dependency_version"],
            item["first_observed_phase"],
        )
        for item in classified
    )
    records = tuple(
        CompatibilityWarning(
            code=key[0],
            category=key[1],
            origin_module=key[2],
            normalized_message=key[3],
            count=count,
            severity="error" if key[0] in blocking_codes else "warning",
            blocking=key[0] in blocking_codes,
            suggested_action=key[4],
            dependency_name=key[5],
            dependency_version=key[6],
            first_observed_phase=key[7],
        )
        for key, count in counts.items()
    )
    status: Literal["clean", "warnings", "blocking"] = (
        "blocking" if any(item.blocking for item in records) else "warnings" if records else "clean"
    )
    return CompatibilityWarningEvidence(warnings=records, status=status)


def _classify(captured: CapturedWarning) -> dict[str, str]:
    item = captured.warning
    message = normalize_warning_message(str(item.message))
    category = item.category.__name__
    module = _origin_module(item.filename)
    lowered = message.lower()
    dependency = "unknown"
    action = "review_dependency_runtime_warning"
    code = "dependency_runtime_warning"
    if "blueprintcircuit" in lowered and "deprecat" in lowered:
        code = "qiskit_blueprint_circuit_deprecated"
        dependency = "qiskit"
        action = "migrate_from_blueprint_circuit_before_removal"
    elif "nlocal" in lowered and "deprecat" in lowered:
        code = "qiskit_nlocal_deprecated"
        dependency = "qiskit"
        action = "migrate_to_function_based_nlocal_construction"
    elif "sparseefficiencywarning" in category.lower() or "sparse efficiency" in lowered:
        code = "scipy_sparse_efficiency_warning"
        dependency = "scipy"
        action = "review_sparse_matrix_construction"
    elif issubclass(item.category, DeprecationWarning):
        code = "dependency_deprecation_warning"
        dependency = _dependency_from_module(module)
        action = "track_upstream_deprecation"
    version = _package_version(dependency)
    return {
        "code": code,
        "category": _identifier(category),
        "origin_module": module,
        "normalized_message": message,
        "suggested_action": action,
        "dependency_name": dependency,
        "dependency_version": version,
        "first_observed_phase": captured.phase,
    }


def _origin_module(filename: str) -> str:
    normalized = filename.replace("\\", "/").lower()
    for candidate in ("qiskit_nature", "qiskit_algorithms", "qiskit", "scipy", "pyscf"):
        if f"/{candidate}/" in normalized:
            return candidate
    return "unknown"


def _dependency_from_module(module: str) -> str:
    return module.replace("_", "-") if module != "unknown" else "unknown"


def _package_version(package: str) -> str:
    if package == "unknown":
        return "unknown"
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _identifier(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._:/-]+", "_", value).strip("_")
    return normalized or "unknown"
