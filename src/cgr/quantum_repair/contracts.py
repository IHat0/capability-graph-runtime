"""Immutable versioned contracts for quantum candidate repair evidence."""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import Field, field_validator, model_validator
from pydantic_core import to_jsonable_python

from cgr.science import CanonicalModel, sha256_fingerprint
from cgr.science.canonical import validate_identifier, validate_sha256

DIRECTIVE_SCHEMA = "cgr.quantum-repair-directive/1.0.0"
PATCH_SCHEMA = "cgr.quantum-repair-patch/1.0.0"
ATTEMPT_SCHEMA = "cgr.quantum-repair-attempt/1.0.0"
RUN_RECEIPT_SCHEMA = "cgr.quantum-repair-run-receipt/1.0.0"
SOURCE_MANIFEST_SCHEMA = "cgr.quantum-repair-source-manifest/1.0.0"
POLICY_SCHEMA = "cgr.quantum-repair-policy/1.0.0"
EVENT_SCHEMA = "cgr.quantum-repair-event/1.0.0"
BENCHMARK_SCHEMA = "cgr.quantum-repair-benchmark/1.0.0"

RepairDisposition = Literal["repairable", "terminal", "human_review"]
ProviderType = Literal["deterministic", "model", "swe_agent", "human"]
ProviderTrust = Literal["reviewed", "untrusted", "human_reviewed"]
AttemptStatus = Literal[
    "created",
    "source_snapshotted",
    "candidate_executing",
    "adjudicated",
    "directive_created",
    "repair_proposed",
    "patch_validated",
    "patch_applied",
    "reexecution_pending",
    "authorized",
    "terminal_rejection",
    "human_review_required",
    "repair_provider_failed",
    "patch_rejected",
    "attempt_budget_exhausted",
    "time_budget_exhausted",
    "repeated_failure",
    "repair_oscillation",
    "controller_failure",
]
TerminalStatus = Literal[
    "authorized",
    "terminal_rejection",
    "human_review_required",
    "repair_provider_failed",
    "patch_rejected",
    "attempt_budget_exhausted",
    "time_budget_exhausted",
    "repeated_failure",
    "repair_oscillation",
    "controller_failure",
]


def validate_source_path(value: str) -> str:
    """Normalize and validate a portable candidate-source path."""
    normalized = value.replace("\\", "/")
    if (
        not normalized
        or normalized.startswith("/")
        or ":" in normalized
        or ".." in normalized.split("/")
    ):
        raise ValueError("Source paths must be relative and traversal-free.")
    return normalized


def _schema(value: str, expected: str) -> str:
    if value != expected:
        raise ValueError(f"Unsupported schema; expected {expected}.")
    return value


class SourceEntry(CanonicalModel):
    relative_path: str
    content_sha256: str
    byte_size: int = Field(ge=0)
    file_mode: int = Field(ge=0)
    file_type: Literal["regular"] = "regular"
    symlink: bool = False
    executable: bool

    @field_validator("relative_path")
    @classmethod
    def safe_path(cls, value: str) -> str:
        return validate_source_path(value)

    @field_validator("content_sha256")
    @classmethod
    def valid_hash(cls, value: str) -> str:
        return validate_sha256(value)

    @model_validator(mode="after")
    def regular_only(self) -> Self:
        if self.symlink:
            raise ValueError("Source manifests cannot authorize symbolic links.")
        return self


class SourceManifest(CanonicalModel):
    schema_version: str = SOURCE_MANIFEST_SCHEMA
    source_identifier: str
    entries: tuple[SourceEntry, ...]
    total_bytes: int = Field(ge=0)
    source_manifest_sha256: str

    @field_validator("schema_version")
    @classmethod
    def valid_schema(cls, value: str) -> str:
        return _schema(value, SOURCE_MANIFEST_SCHEMA)

    @field_validator("source_identifier")
    @classmethod
    def valid_identifier(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("source_manifest_sha256")
    @classmethod
    def valid_hash(cls, value: str) -> str:
        return validate_sha256(value)

    def canonical_identity(self) -> Any:
        value = self.model_dump(mode="json")
        value.pop("source_manifest_sha256", None)
        return value

    @model_validator(mode="after")
    def verified(self) -> Self:
        paths = [item.relative_path for item in self.entries]
        if paths != sorted(paths) or len(paths) != len(set(paths)):
            raise ValueError("Source manifest entries must be ordered and unique.")
        if self.total_bytes != sum(item.byte_size for item in self.entries):
            raise ValueError("Source manifest byte total is inconsistent.")
        if self.source_manifest_sha256 != self.fingerprint:
            raise ValueError("Source manifest hash was not recomputed.")
        return self


class QuantumRepairPolicy(CanonicalModel):
    schema_version: str = POLICY_SCHEMA
    maximum_attempts: int = Field(default=3, ge=1, le=5)
    absolute_attempt_cap: int = 5
    maximum_files_changed: int = Field(default=8, ge=1, le=8)
    maximum_patch_bytes: int = Field(default=64 * 1024, ge=1, le=64 * 1024)
    maximum_changed_lines: int = Field(default=300, ge=1, le=300)
    maximum_provider_seconds: int = Field(default=30, ge=1, le=120)
    maximum_total_seconds: int = Field(default=600, ge=1, le=3600)
    allowed_file_types: tuple[str, ...] = (".json", ".py", ".txt")
    prohibited_paths: tuple[str, ...] = (
        ".git",
        ".env",
        "benchmark-manifests",
        "docker",
        "requirements",
        "src/cgr",
        "trusted-reference",
    )

    @field_validator("schema_version")
    @classmethod
    def valid_schema(cls, value: str) -> str:
        return _schema(value, POLICY_SCHEMA)

    @model_validator(mode="after")
    def hard_caps(self) -> Self:
        if (
            self.absolute_attempt_cap != 5
            or self.maximum_attempts > self.absolute_attempt_cap
        ):
            raise ValueError("Repair attempts exceed the absolute hard cap.")
        if tuple(sorted(set(self.allowed_file_types))) != self.allowed_file_types:
            raise ValueError("Allowed file types must be sorted and unique.")
        return self


class ProviderCapability(CanonicalModel):
    provider_identifier: str
    provider_version: str
    provider_type: ProviderType
    supported_finding_codes: tuple[str, ...]
    maximum_patch_bytes: int = Field(gt=0, le=64 * 1024)
    deterministic: bool
    network_required: bool
    tool_requirements: tuple[str, ...] = ()
    trust_classification: ProviderTrust

    @field_validator("provider_identifier", "provider_version")
    @classmethod
    def identifiers(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("supported_finding_codes", "tool_requirements")
    @classmethod
    def ordered_identifiers(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(sorted(set(validate_identifier(item) for item in value)))
        return normalized


class QuantumRepairDirective(CanonicalModel):
    schema_version: str = DIRECTIVE_SCHEMA
    directive_identifier: str
    task_identifier: str
    repair_run_identifier: str
    source_attempt_identifier: str
    source_manifest_sha256: str
    source_adjudication_receipt_sha256: str
    primary_finding_code: str
    additional_finding_codes: tuple[str, ...] = ()
    sanitized_explanations: tuple[str, ...]
    disposition: RepairDisposition
    allowed_edit_paths: tuple[str, ...]
    prohibited_edit_paths: tuple[str, ...]
    maximum_files_changed: int = Field(gt=0, le=8)
    maximum_changed_lines: int = Field(gt=0, le=300)
    maximum_patch_bytes: int = Field(gt=0, le=64 * 1024)
    allowed_file_types: tuple[str, ...]
    required_invariants: tuple[str, ...]
    required_reverification_gates: tuple[str, ...]
    deliberately_withheld: tuple[str, ...]
    attempt_number: int = Field(ge=0, le=4)
    remaining_attempt_budget: int = Field(ge=0, le=4)
    creation_policy_version: str
    directive_sha256: str

    @field_validator("schema_version")
    @classmethod
    def valid_schema(cls, value: str) -> str:
        return _schema(value, DIRECTIVE_SCHEMA)

    @field_validator(
        "directive_identifier",
        "task_identifier",
        "repair_run_identifier",
        "source_attempt_identifier",
        "primary_finding_code",
        "creation_policy_version",
    )
    @classmethod
    def identifiers(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator(
        "source_manifest_sha256",
        "source_adjudication_receipt_sha256",
        "directive_sha256",
    )
    @classmethod
    def hashes(cls, value: str) -> str:
        return validate_sha256(value)

    def canonical_identity(self) -> Any:
        value = self.model_dump(mode="json")
        value.pop("directive_sha256", None)
        return value

    @model_validator(mode="after")
    def verified(self) -> Self:
        if self.directive_sha256 != self.fingerprint:
            raise ValueError("Repair directive hash was not recomputed.")
        if set(self.allowed_edit_paths) & set(self.prohibited_edit_paths):
            raise ValueError("Repair directive path policy overlaps.")
        return self


class StructuredEdit(CanonicalModel):
    relative_path: str
    old_text: str
    new_text: str

    @field_validator("relative_path")
    @classmethod
    def safe_path(cls, value: str) -> str:
        return validate_source_path(value)

    @model_validator(mode="after")
    def changed(self) -> Self:
        if self.old_text == self.new_text:
            raise ValueError("Structured edits cannot be no-ops.")
        return self


class QuantumRepairPatch(CanonicalModel):
    schema_version: str = PATCH_SCHEMA
    patch_identifier: str
    directive_sha256: str
    base_source_manifest_sha256: str
    provider_identifier: str
    provider_version: str
    provider_type: ProviderType
    edits: tuple[StructuredEdit, ...]
    changed_paths: tuple[str, ...]
    added_lines: int = Field(ge=0)
    deleted_lines: int = Field(ge=0)
    rationale: str = Field(min_length=1, max_length=2048)
    claimed_addressed_findings: tuple[str, ...]
    creation_evidence_sha256: str
    validation_status: Literal["proposed", "validated", "rejected"] = "proposed"
    patch_sha256: str

    @field_validator("schema_version")
    @classmethod
    def valid_schema(cls, value: str) -> str:
        return _schema(value, PATCH_SCHEMA)

    @field_validator("patch_identifier", "provider_identifier", "provider_version")
    @classmethod
    def identifiers(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator(
        "directive_sha256",
        "base_source_manifest_sha256",
        "creation_evidence_sha256",
        "patch_sha256",
    )
    @classmethod
    def hashes(cls, value: str) -> str:
        return validate_sha256(value)

    def canonical_identity(self) -> Any:
        value = self.model_dump(mode="json")
        value.pop("patch_sha256", None)
        return value

    @model_validator(mode="after")
    def verified(self) -> Self:
        if not self.edits:
            raise ValueError("Repair patches cannot be empty.")
        paths = tuple(sorted({item.relative_path for item in self.edits}))
        if self.changed_paths != paths:
            raise ValueError("Repair patch changed-path inventory is inconsistent.")
        if self.patch_sha256 != self.fingerprint:
            raise ValueError("Repair patch hash was not recomputed.")
        return self


class PatchValidation(CanonicalModel):
    schema_version: str = "cgr.quantum-repair-patch-validation/1.0.0"
    patch_sha256: str
    base_source_manifest_sha256: str
    validated: bool
    checks: tuple[str, ...]
    rejection_code: str | None = None
    output_source_manifest_sha256: str | None = None
    source_provenance: Literal["fresh-copy-plus-structured-edits"]
    unchanged_file_ratio: float = Field(ge=0.0, le=1.0)
    control_source_match: bool
    candidate_identifier_retained: bool

    @field_validator(
        "patch_sha256", "base_source_manifest_sha256", "output_source_manifest_sha256"
    )
    @classmethod
    def hashes(cls, value: str | None) -> str | None:
        return validate_sha256(value) if value is not None else None


class QuantumRepairAttempt(CanonicalModel):
    schema_version: str = ATTEMPT_SCHEMA
    repair_run_identifier: str
    attempt_identifier: str
    attempt_index: int = Field(ge=0, le=4)
    parent_attempt_identifier: str | None
    input_source_manifest_sha256: str
    directive_sha256: str | None
    patch_sha256: str | None
    output_source_manifest_sha256: str
    candidate_execution_sha256: str
    adjudication_receipt_sha256: str
    authorized: bool
    findings_before: tuple[str, ...]
    findings_after: tuple[str, ...]
    status: AttemptStatus
    failure_reason: str | None = Field(default=None, max_length=2048)
    elapsed_seconds: float = Field(ge=0)
    attempt_content_sha256: str

    @field_validator("schema_version")
    @classmethod
    def valid_schema(cls, value: str) -> str:
        return _schema(value, ATTEMPT_SCHEMA)

    @field_validator(
        "repair_run_identifier", "attempt_identifier", "parent_attempt_identifier"
    )
    @classmethod
    def identifiers(cls, value: str | None) -> str | None:
        return validate_identifier(value) if value is not None else None

    @field_validator(
        "input_source_manifest_sha256",
        "directive_sha256",
        "patch_sha256",
        "output_source_manifest_sha256",
        "candidate_execution_sha256",
        "adjudication_receipt_sha256",
        "attempt_content_sha256",
    )
    @classmethod
    def hashes(cls, value: str | None) -> str | None:
        return validate_sha256(value) if value is not None else None

    def canonical_identity(self) -> Any:
        value = self.model_dump(mode="json")
        value.pop("attempt_content_sha256", None)
        return value

    @model_validator(mode="after")
    def verified(self) -> Self:
        if self.attempt_content_sha256 != self.fingerprint:
            raise ValueError("Repair attempt hash was not recomputed.")
        if self.authorized != (self.status == "authorized"):
            raise ValueError("Attempt authorization must come from authorized status.")
        return self


class AttemptReference(CanonicalModel):
    attempt_identifier: str
    attempt_index: int = Field(ge=0, le=4)
    attempt_content_sha256: str
    source_manifest_sha256: str
    adjudication_receipt_sha256: str
    authorized: bool

    @field_validator("attempt_identifier")
    @classmethod
    def identifier(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator(
        "attempt_content_sha256",
        "source_manifest_sha256",
        "adjudication_receipt_sha256",
    )
    @classmethod
    def hashes(cls, value: str) -> str:
        return validate_sha256(value)


class QuantumRepairRunReceipt(CanonicalModel):
    schema_version: str = RUN_RECEIPT_SCHEMA
    repair_run_identifier: str
    public_experiment_sha256: str
    original_source_manifest_sha256: str
    trusted_reference_receipt_sha256: str
    provider_capability_sha256: str
    policy_sha256: str
    attempts: tuple[AttemptReference, ...]
    attempt_cap: int = Field(ge=1, le=5)
    total_budget_seconds: int = Field(gt=0, le=3600)
    terminal_status: TerminalStatus
    final_source_manifest_sha256: str
    final_adjudication_receipt_sha256: str
    final_scientific_outcome_sha256: str | None
    authorized: bool
    repair_run_content_sha256: str

    @field_validator("schema_version")
    @classmethod
    def valid_schema(cls, value: str) -> str:
        return _schema(value, RUN_RECEIPT_SCHEMA)

    @field_validator("repair_run_identifier")
    @classmethod
    def identifier(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator(
        "public_experiment_sha256",
        "original_source_manifest_sha256",
        "trusted_reference_receipt_sha256",
        "provider_capability_sha256",
        "policy_sha256",
        "final_source_manifest_sha256",
        "final_adjudication_receipt_sha256",
        "final_scientific_outcome_sha256",
        "repair_run_content_sha256",
    )
    @classmethod
    def hashes(cls, value: str | None) -> str | None:
        return validate_sha256(value) if value is not None else None

    def canonical_identity(self) -> Any:
        value = self.model_dump(mode="json")
        value.pop("repair_run_content_sha256", None)
        return value

    @model_validator(mode="after")
    def verified(self) -> Self:
        if self.repair_run_content_sha256 != self.fingerprint:
            raise ValueError("Repair-run receipt hash was not recomputed.")
        indices = [item.attempt_index for item in self.attempts]
        if indices != list(range(len(indices))):
            raise ValueError("Repair attempts must be complete and ordered.")
        if self.authorized != (self.terminal_status == "authorized"):
            raise ValueError("Repair-run authorization must come from terminal status.")
        if self.authorized and self.final_scientific_outcome_sha256 is None:
            raise ValueError(
                "Authorized repair runs require a final scientific outcome."
            )
        if not self.attempts:
            raise ValueError("Repair-run receipts require an adjudicated attempt.")
        if len(self.attempts) > self.attempt_cap:
            raise ValueError("Repair-run receipt exceeds its attempt cap.")
        return self


class QuantumRepairEvent(CanonicalModel):
    schema_version: str = EVENT_SCHEMA
    repair_run_identifier: str
    attempt_identifier: str | None
    event_identifier: str
    event_sequence: int = Field(ge=0)
    event_type: str
    status: str
    content_hashes: tuple[str, ...] = ()
    elapsed_seconds: float = Field(ge=0)

    @field_validator("schema_version")
    @classmethod
    def valid_schema(cls, value: str) -> str:
        return _schema(value, EVENT_SCHEMA)

    @field_validator(
        "repair_run_identifier",
        "attempt_identifier",
        "event_identifier",
        "event_type",
        "status",
    )
    @classmethod
    def identifiers(cls, value: str | None) -> str | None:
        return validate_identifier(value) if value is not None else None

    @field_validator("content_hashes")
    @classmethod
    def hashes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(validate_sha256(item) for item in value)


class QuantumRepairBenchmarkCase(CanonicalModel):
    case_identifier: str
    initial_defects: tuple[str, ...]
    expected_findings: tuple[str, ...]
    expected_attempts: int = Field(ge=1, le=3)
    authorized_without_repair: bool

    @field_validator("case_identifier")
    @classmethod
    def identifier(cls, value: str) -> str:
        return validate_identifier(value)


class QuantumRepairBenchmarkManifest(CanonicalModel):
    schema_version: str = BENCHMARK_SCHEMA
    benchmark_identifier: str
    public_experiment_manifest: str
    diagnosis_benchmark_manifest_sha256: str
    cases: tuple[QuantumRepairBenchmarkCase, ...]

    @field_validator("schema_version")
    @classmethod
    def valid_schema(cls, value: str) -> str:
        return _schema(value, BENCHMARK_SCHEMA)

    @field_validator("benchmark_identifier")
    @classmethod
    def identifier(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("diagnosis_benchmark_manifest_sha256")
    @classmethod
    def valid_hash(cls, value: str) -> str:
        return validate_sha256(value)

    @model_validator(mode="after")
    def cases_complete(self) -> Self:
        identifiers = [item.case_identifier for item in self.cases]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Repair benchmark cases must be unique.")
        return self


def sealed_values(values: dict[str, Any], hash_field: str) -> dict[str, Any]:
    """Return values with a content hash over the identity excluding that hash field."""
    schema_by_hash_field = {
        "source_manifest_sha256": SOURCE_MANIFEST_SCHEMA,
        "directive_sha256": DIRECTIVE_SCHEMA,
        "patch_sha256": PATCH_SCHEMA,
        "attempt_content_sha256": ATTEMPT_SCHEMA,
        "repair_run_content_sha256": RUN_RECEIPT_SCHEMA,
    }
    identity = dict(values)
    if "schema_version" not in identity and hash_field in schema_by_hash_field:
        identity["schema_version"] = schema_by_hash_field[hash_field]
    identity.pop(hash_field, None)
    normalized = to_jsonable_python(identity)
    return {**identity, hash_field: sha256_fingerprint(normalized)}
