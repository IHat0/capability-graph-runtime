"""Immutable contracts for hostile candidate evidence and trusted decisions."""

from __future__ import annotations

import math
from typing import Any, Literal, Self

from pydantic import Field, field_validator, model_validator

from cgr.science import ArtifactPointer, CanonicalModel
from cgr.science.canonical import (
    BoundedMetadata,
    validate_bounded_metadata,
    validate_identifier,
    validate_sha256,
)

FINDINGS_SCHEMA = "cgr.quantum-candidate-findings/1.0.0"
OUTPUT_SCHEMA = "cgr.quantum-candidate-output/1.0.0"
RECEIPT_SCHEMA = "cgr.quantum-candidate-adjudication-receipt/1.0.0"
BENCHMARK_SCHEMA = "cgr.quantum-candidate-benchmark/1.0.0"
PUBLIC_INPUT_SCHEMA = "cgr.quantum-candidate-input/1.0.0"

FindingPhase = Literal[
    "candidate_bundle_validation",
    "sandbox_preflight",
    "candidate_execution",
    "candidate_output_collection",
    "candidate_protocol_validation",
    "scientific_identity_validation",
    "hamiltonian_validation",
    "result_validation",
    "evidence_integrity_validation",
    "authorization",
]
FindingCategory = Literal[
    "bundle",
    "execution",
    "resource",
    "protocol",
    "scientific_specification",
    "structure",
    "electronic_problem",
    "active_space",
    "hamiltonian",
    "solver",
    "result",
    "lineage",
    "integrity",
    "security",
    "authorization",
]


class RepairDirective(CanonicalModel):
    action: str
    target: Literal["candidate source", "candidate output protocol", "sandbox policy"]
    required_evidence_after_edit: tuple[str, ...]

    @field_validator("action")
    @classmethod
    def valid_action(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("required_evidence_after_edit")
    @classmethod
    def ordered_evidence(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted(set(validate_identifier(item) for item in value)))


class CandidateFinding(CanonicalModel):
    schema_version: str = FINDINGS_SCHEMA
    code: str
    category: FindingCategory
    phase: FindingPhase
    severity: Literal["info", "warning", "error"] = "error"
    blocking: bool = True
    subject_artifact: str | None = None
    expected: str | int | float | bool | None = None
    observed: str | int | float | bool | None = None
    evidence: tuple[ArtifactPointer, ...] = ()
    explanation: str = Field(min_length=1, max_length=2048)
    repair_directive: RepairDirective
    retryable: bool
    scientific_reconstruction_required: bool
    source_edit_required: bool

    @field_validator("schema_version")
    @classmethod
    def valid_schema(cls, value: str) -> str:
        if value != FINDINGS_SCHEMA:
            raise ValueError("Unsupported candidate finding schema.")
        return value

    @field_validator("code", "subject_artifact")
    @classmethod
    def valid_identifiers(cls, value: str | None) -> str | None:
        return validate_identifier(value) if value is not None else None


class CandidateArtifactClaim(CanonicalModel):
    role: str
    path: str = Field(min_length=1, max_length=512)
    content_sha256: str | None = None

    @field_validator("role")
    @classmethod
    def valid_role(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("content_sha256")
    @classmethod
    def valid_hash(cls, value: str | None) -> str | None:
        return validate_sha256(value) if value is not None else None


class CandidateLineageClaim(CanonicalModel):
    source_role: str
    destination_role: str

    @field_validator("source_role", "destination_role")
    @classmethod
    def valid_roles(cls, value: str) -> str:
        return validate_identifier(value)


class CandidateOutputSummary(CanonicalModel):
    schema_version: str
    candidate_identifier: str
    input_manifest_sha256: str
    execution_completed: bool
    claimed_workflow: str
    artifacts: tuple[CandidateArtifactClaim, ...]
    lineage: tuple[CandidateLineageClaim, ...] = ()
    claimed_molecular_specification: dict[str, Any]
    claimed_active_space: dict[str, Any]
    claimed_mapper: str
    claimed_solver: str
    claimed_energies: dict[str, float | None]
    claimed_converged: bool
    claimed_scientific_result_sha256: str | None = None
    authorized: bool | None = None
    diagnostics: BoundedMetadata = Field(default_factory=dict)

    @field_validator("schema_version")
    @classmethod
    def valid_schema(cls, value: str) -> str:
        if value != OUTPUT_SCHEMA:
            raise ValueError("Unsupported candidate output schema.")
        return value

    @field_validator(
        "candidate_identifier", "claimed_workflow", "claimed_mapper", "claimed_solver"
    )
    @classmethod
    def valid_identifiers(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("input_manifest_sha256", "claimed_scientific_result_sha256")
    @classmethod
    def valid_hashes(cls, value: str | None) -> str | None:
        return validate_sha256(value) if value is not None else None

    @field_validator("artifacts")
    @classmethod
    def unique_roles(
        cls, value: tuple[CandidateArtifactClaim, ...]
    ) -> tuple[CandidateArtifactClaim, ...]:
        roles = [item.role for item in value]
        if len(roles) != len(set(roles)):
            raise ValueError("Candidate artifact roles must be unique.")
        return tuple(sorted(value, key=lambda item: item.role))

    @field_validator("lineage")
    @classmethod
    def ordered_lineage(
        cls, value: tuple[CandidateLineageClaim, ...]
    ) -> tuple[CandidateLineageClaim, ...]:
        return tuple(
            sorted(
                set(value), key=lambda item: (item.source_role, item.destination_role)
            )
        )

    @field_validator("claimed_energies")
    @classmethod
    def finite_energies(cls, value: dict[str, float | None]) -> dict[str, float | None]:
        if any(item is not None and not math.isfinite(item) for item in value.values()):
            raise ValueError("Candidate energy claims must be finite.")
        return dict(sorted(value.items()))

    @field_validator("diagnostics")
    @classmethod
    def valid_diagnostics(cls, value: BoundedMetadata) -> BoundedMetadata:
        return validate_bounded_metadata(value)


class CandidateMount(CanonicalModel):
    container_path: str
    access: Literal["read_only", "writable"]
    purpose: str

    @field_validator("container_path")
    @classmethod
    def container_path_only(cls, value: str) -> str:
        if value not in {"/input/experiment.json", "/candidate", "/output"}:
            raise ValueError("Candidate mount manifest contains an unexpected path.")
        return value

    @field_validator("purpose")
    @classmethod
    def valid_purpose(cls, value: str) -> str:
        return validate_identifier(value)


class CandidateSandboxPolicy(CanonicalModel):
    schema_version: str = "cgr.quantum-candidate-sandbox-policy/1.0.0"
    network_disabled: bool = True
    read_only_root: bool = True
    cpu_limit: float = 2.0
    memory_mib: int = 4096
    process_limit: int = 128
    candidate_uid: int = 10002
    tmpfs: str = "/tmp:rw,nosuid,nodev,noexec,size=512m,mode=1777"
    wall_clock_seconds: int = 90
    maximum_stdout_bytes: int = 2 * 1024 * 1024
    maximum_stderr_bytes: int = 2 * 1024 * 1024
    maximum_output_bytes: int = 32 * 1024 * 1024
    maximum_files: int = 64
    maximum_file_bytes: int = 8 * 1024 * 1024
    mounts: tuple[CandidateMount, ...] = (
        CandidateMount(
            container_path="/input/experiment.json",
            access="read_only",
            purpose="public_experiment",
        ),
        CandidateMount(
            container_path="/candidate", access="read_only", purpose="candidate_source"
        ),
        CandidateMount(
            container_path="/output", access="writable", purpose="candidate_output"
        ),
    )

    @field_validator("schema_version")
    @classmethod
    def valid_schema(cls, value: str) -> str:
        if value != "cgr.quantum-candidate-sandbox-policy/1.0.0":
            raise ValueError("Unsupported candidate sandbox policy schema.")
        return value

    @model_validator(mode="after")
    def hardened(self) -> Self:
        if (
            not self.network_disabled
            or not self.read_only_root
            or self.candidate_uid != 10002
        ):
            raise ValueError(
                "Candidate sandbox must be network-disabled, read-only, and non-root."
            )
        if (
            self.cpu_limit != 2.0
            or self.memory_mib != 4096
            or self.process_limit != 128
        ):
            raise ValueError(
                "Candidate sandbox resources must match the reviewed v1 profile."
            )
        if self.wall_clock_seconds > 90:
            raise ValueError("Candidate sandbox bounds exceed the v1 policy.")
        bounded_values = (
            (self.wall_clock_seconds, 90),
            (self.maximum_stdout_bytes, 2 * 1024 * 1024),
            (self.maximum_stderr_bytes, 2 * 1024 * 1024),
            (self.maximum_output_bytes, 32 * 1024 * 1024),
            (self.maximum_files, 64),
            (self.maximum_file_bytes, 8 * 1024 * 1024),
        )
        if any(value <= 0 or value > maximum for value, maximum in bounded_values):
            raise ValueError(
                "Candidate sandbox quotas must be positive and may not expand v1 limits."
            )
        expected_mounts = {
            "/input/experiment.json": "read_only",
            "/candidate": "read_only",
            "/output": "writable",
        }
        if (
            len(self.mounts) != 3
            or {item.container_path: item.access for item in self.mounts}
            != expected_mounts
        ):
            raise ValueError("Candidate sandbox mount set is incomplete or expanded.")
        if self.tmpfs != "/tmp:rw,nosuid,nodev,noexec,size=512m,mode=1777":
            raise ValueError(
                "Candidate temporary storage must match the reviewed v1 profile."
            )
        return self


class CandidateExecutionEvidence(CanonicalModel):
    schema_version: str = "cgr.quantum-candidate-execution/1.0.0"
    candidate_identifier: str
    source_tree_sha256: str
    input_manifest_sha256: str
    image_identifier: str
    sandbox_policy_sha256: str
    mount_manifest: tuple[CandidateMount, ...]
    execution_category: Literal[
        "completed",
        "syntax_error",
        "import_error",
        "runtime_error",
        "timeout",
        "output_violation",
    ]
    exit_code: int | None
    timed_out: bool
    elapsed_seconds: float = Field(ge=0)
    stdout_sha256: str
    stderr_sha256: str
    stdout_bytes: int = Field(ge=0)
    stderr_bytes: int = Field(ge=0)
    output_bytes: int = Field(ge=0)
    output_files: int = Field(ge=0)
    network_disabled: bool
    trusted_evidence_exposed: bool
    forbidden_cgr_import_attempted: bool = False
    network_access_attempted: bool = False
    output_policy_violated: bool = False

    @field_validator("schema_version")
    @classmethod
    def valid_schema(cls, value: str) -> str:
        if value != "cgr.quantum-candidate-execution/1.0.0":
            raise ValueError("Unsupported candidate execution schema.")
        return value

    @field_validator("image_identifier")
    @classmethod
    def immutable_image_identifier(cls, value: str) -> str:
        if not value.startswith("sha256:") or len(value) != 71:
            raise ValueError(
                "Candidate image evidence requires a full immutable image ID."
            )
        validate_sha256(value.removeprefix("sha256:"))
        return value

    @field_validator(
        "source_tree_sha256",
        "input_manifest_sha256",
        "sandbox_policy_sha256",
        "stdout_sha256",
        "stderr_sha256",
    )
    @classmethod
    def valid_hashes(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("candidate_identifier")
    @classmethod
    def valid_candidate(cls, value: str) -> str:
        return validate_identifier(value)


class CandidateAdjudicationReceipt(CanonicalModel):
    schema_version: str = RECEIPT_SCHEMA
    candidate_identifier: str
    candidate_source_tree_sha256: str
    input_experiment_sha256: str
    candidate_image_identifier: str
    candidate_dependency_lock_sha256: str
    sandbox_policy_sha256: str
    execution_evidence: ArtifactPointer
    candidate_output_package_sha256: str | None
    candidate_artifacts: tuple[ArtifactPointer, ...]
    recomputed_scientific_result_sha256: str | None
    trusted_reference_receipt_sha256: str
    findings: tuple[CandidateFinding, ...]
    primary_failure_code: str | None
    authorized: bool
    authorization_policy_sha256: str
    receipt_content_sha256: str

    @field_validator("schema_version")
    @classmethod
    def valid_schema(cls, value: str) -> str:
        if value != RECEIPT_SCHEMA:
            raise ValueError("Unsupported candidate adjudication receipt schema.")
        return value

    @field_validator(
        "candidate_source_tree_sha256",
        "input_experiment_sha256",
        "candidate_dependency_lock_sha256",
        "sandbox_policy_sha256",
        "candidate_output_package_sha256",
        "recomputed_scientific_result_sha256",
        "trusted_reference_receipt_sha256",
        "authorization_policy_sha256",
        "receipt_content_sha256",
    )
    @classmethod
    def valid_hashes(cls, value: str | None) -> str | None:
        return validate_sha256(value) if value is not None else None

    @field_validator("candidate_identifier", "primary_failure_code")
    @classmethod
    def valid_identifiers(cls, value: str | None) -> str | None:
        return validate_identifier(value) if value is not None else None

    def canonical_identity(self) -> Any:
        value = self.model_dump(mode="json")
        value.pop("receipt_content_sha256", None)
        return value

    @model_validator(mode="after")
    def fail_closed(self) -> Self:
        expected_authorized = not any(item.blocking for item in self.findings)
        if self.authorized != expected_authorized:
            raise ValueError(
                "Candidate authorization disagrees with blocking findings."
            )
        if self.receipt_content_sha256 != self.fingerprint:
            raise ValueError("Candidate receipt content identity was not recomputed.")
        if (self.authorized and self.primary_failure_code is not None) or (
            not self.authorized and self.primary_failure_code is None
        ):
            raise ValueError(
                "Rejected candidates require exactly one primary failure code."
            )
        return self


class CandidateBenchmarkCase(CanonicalModel):
    case_identifier: str
    candidate_directory: str
    authorization_expected: bool
    expected_execution_category: Literal[
        "completed",
        "syntax_error",
        "import_error",
        "runtime_error",
        "timeout",
        "output_violation",
    ]
    expected_primary_finding: str | None = None
    required_additional_findings: tuple[str, ...] = ()
    forbidden_findings: tuple[str, ...] = ()
    maximum_runtime_seconds: int = Field(gt=0, le=90)
    output_protocol_expected: bool
    scientific_adjudication_expected: bool
    purpose: str = Field(min_length=1, max_length=512)

    @field_validator(
        "case_identifier", "expected_execution_category", "expected_primary_finding"
    )
    @classmethod
    def valid_identifiers(cls, value: str | None) -> str | None:
        return validate_identifier(value) if value is not None else None

    @field_validator("candidate_directory")
    @classmethod
    def safe_relative_directory(cls, value: str) -> str:
        if value.startswith(("/", "\\")) or ".." in value.replace("\\", "/").split("/"):
            raise ValueError(
                "Candidate directories must be relative and traversal-free."
            )
        return value


class CandidateBenchmarkManifest(CanonicalModel):
    schema_version: str
    benchmark_identifier: str
    public_experiment_manifest: str
    cases: tuple[CandidateBenchmarkCase, ...]

    @field_validator("schema_version")
    @classmethod
    def valid_schema(cls, value: str) -> str:
        if value != BENCHMARK_SCHEMA:
            raise ValueError("Unsupported quantum-candidate benchmark schema.")
        return value

    @field_validator("benchmark_identifier")
    @classmethod
    def valid_identifier(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("public_experiment_manifest")
    @classmethod
    def safe_manifest_path(cls, value: str) -> str:
        normalized = value.replace("\\", "/")
        allowed_sibling = (
            normalized.startswith("../quantum-preflight/")
            and normalized.count("..") == 1
        )
        if (
            value.startswith(("/", "\\"))
            or ":" in value
            or (".." in normalized.split("/") and not allowed_sibling)
        ):
            raise ValueError("Public experiment manifest must be a safe relative path.")
        return normalized

    @field_validator("cases")
    @classmethod
    def ordered_unique_cases(
        cls, value: tuple[CandidateBenchmarkCase, ...]
    ) -> tuple[CandidateBenchmarkCase, ...]:
        identifiers = [item.case_identifier for item in value]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Benchmark case identifiers must be unique.")
        return tuple(sorted(value, key=lambda item: item.case_identifier))
