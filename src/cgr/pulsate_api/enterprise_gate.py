"""Canonical evidence aggregation for the Phase 1-3 enterprise gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import stat
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any, Self

from pydantic import Field, field_validator, model_validator

from cgr.science.canonical import CanonicalModel, sha256_fingerprint, validate_identifier, validate_sha256


READINESS_EVIDENCE_SCHEMA_VERSION = "cgr.pulsate-readiness-evidence/1.0.0"
ENTERPRISE_DECISION_SCHEMA_VERSION = "cgr.pulsate-enterprise-decision/1.0.0"


class EvidenceClassification(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"
    PENDING = "PENDING"


@dataclass(frozen=True, slots=True)
class EnterpriseGateProducer:
    """The one approved workflow producer for a mandatory gate."""

    gate_identifier: str
    workflow_job: str
    operation_identity: str
    blocked_exit_statuses: tuple[int, ...] = ()

    @property
    def evidence_filename(self) -> str:
        return f"{self.gate_identifier}.json"


def _producer(
    gate_identifier: str,
    workflow_job: str,
    operation_identity: str,
    *,
    blocked_exit_statuses: tuple[int, ...] = (),
) -> EnterpriseGateProducer:
    return EnterpriseGateProducer(
        gate_identifier=validate_identifier(gate_identifier),
        workflow_job=validate_identifier(workflow_job),
        operation_identity=validate_identifier(operation_identity),
        blocked_exit_statuses=blocked_exit_statuses,
    )


ENTERPRISE_GATE_PRODUCERS = (
    _producer("audit-integrity", "ci.yml:backend-evidence", "pytest-audit-integrity"),
    _producer("authentication", "enterprise-readiness.yml:deployment-and-recovery", "authenticated-container-smoke"),
    _producer("authorization", "ci.yml:backend-evidence", "pytest-authorization"),
    _producer("backup", "enterprise-readiness.yml:deployment-and-recovery", "recovery-backup"),
    _producer("backup-verification", "enterprise-readiness.yml:deployment-and-recovery", "recovery-backup-verification"),
    _producer("catalogue-determinism", "ci.yml:backend-evidence", "pytest-catalogue-determinism"),
    _producer("compose-validation", "enterprise-readiness.yml:deployment-and-recovery", "docker-compose-validation"),
    _producer("container-graceful-shutdown", "enterprise-readiness.yml:deployment-and-recovery", "container-graceful-shutdown"),
    _producer("container-live-probe", "enterprise-readiness.yml:deployment-and-recovery", "container-live-probe"),
    _producer("container-non-root", "enterprise-readiness.yml:deployment-and-recovery", "container-non-root-inspection"),
    _producer("container-ready-probe", "enterprise-readiness.yml:deployment-and-recovery", "container-ready-probe"),
    _producer("container-sbom", "supply-chain-security.yml:container-supply-chain", "syft-1.18.1-container-sbom"),
    _producer("container-startup", "enterprise-readiness.yml:deployment-and-recovery", "container-startup"),
    _producer("container-startup-duration", "enterprise-readiness.yml:deployment-and-recovery", "container-startup-duration", blocked_exit_statuses=(78,)),
    _producer("container-vulnerability-scan", "supply-chain-security.yml:container-supply-chain", "trivy-0.58.2-container-scan"),
    _producer("correlation-identity", "ci.yml:backend-evidence", "pytest-correlation-identity"),
    _producer("cross-phase-scientific-integration", "ci.yml:backend-evidence", "pytest-cross-phase-scientific-integration"),
    _producer("durable-audit", "ci.yml:backend-evidence", "pytest-durable-audit"),
    _producer("durable-grants", "ci.yml:backend-evidence", "pytest-durable-grants"),
    _producer("enumeration-resistance", "ci.yml:backend-evidence", "pytest-enumeration-resistance"),
    _producer("frontend-application-typescript", "ci.yml:frontend-evidence", "typescript-application"),
    _producer("frontend-authentication-states", "ci.yml:frontend-evidence", "vitest-authentication-states"),
    _producer("frontend-build", "ci.yml:frontend-evidence", "vite-production-build"),
    _producer("frontend-eslint", "ci.yml:frontend-evidence", "eslint-frontend"),
    _producer("frontend-node-typescript", "ci.yml:frontend-evidence", "typescript-node"),
    _producer("frontend-planning-workspace", "ci.yml:frontend-evidence", "vitest-planning-workspace"),
    _producer("frontend-vitest", "ci.yml:frontend-evidence", "vitest-complete"),
    _producer("frontend-workflow-workspace", "ci.yml:frontend-evidence", "vitest-workflow-workspace"),
    _producer("image-build", "enterprise-readiness.yml:deployment-and-recovery", "docker-image-build"),
    _producer("license-inventory-node", "supply-chain-security.yml:sbom-and-licenses", "node-license-inventory"),
    _producer("license-inventory-python", "supply-chain-security.yml:sbom-and-licenses", "python-license-inventory"),
    _producer("lifecycle-stability", "enterprise-readiness.yml:deployment-and-recovery", "container-lifecycle-stability"),
    _producer("lock-consistency", "supply-chain-security.yml:dependency-consistency", "dependency-lock-consistency"),
    _producer("memory-process-observations", "enterprise-readiness.yml:deployment-and-recovery", "container-resource-observation"),
    _producer("molecular-artifact-integrity", "ci.yml:backend-evidence", "pytest-molecular-artifact-integrity"),
    _producer("molecular-atomic-publication", "ci.yml:backend-evidence", "pytest-molecular-atomic-publication"),
    _producer("molecular-concurrency", "ci.yml:backend-evidence", "pytest-molecular-concurrency"),
    _producer("molecular-corruption-detection", "ci.yml:backend-evidence", "pytest-molecular-corruption"),
    _producer("molecular-project-integrity", "ci.yml:backend-evidence", "pytest-molecular-project-integrity"),
    _producer("molecular-workspace", "ci.yml:frontend-evidence", "vitest-molecular-workspace"),
    _producer("node-sbom", "supply-chain-security.yml:sbom-and-licenses", "node-sbom"),
    _producer("node-vulnerability-scan", "supply-chain-security.yml:vulnerability-and-static-security", "npm-audit"),
    _producer("performance-acceptance", "enterprise-readiness.yml:deployment-and-recovery", "performance-acceptance-policy", blocked_exit_statuses=(78,)),
    _producer("performance-diagnostics", "enterprise-readiness.yml:deployment-and-recovery", "performance-diagnostics"),
    _producer("planning-api", "ci.yml:backend-evidence", "pytest-planning-api"),
    _producer("planning-determinism", "ci.yml:backend-evidence", "pytest-planning-determinism"),
    _producer("planning-latency", "enterprise-readiness.yml:deployment-and-recovery", "planning-latency-diagnostic", blocked_exit_statuses=(78,)),
    _producer("planning-no-execution-authorization", "ci.yml:backend-evidence", "pytest-planning-no-execution"),
    _producer("planning-read-only", "ci.yml:backend-evidence", "pytest-planning-read-only"),
    _producer("production-jwt-jwks", "ci.yml:backend-evidence", "pytest-production-jwt-jwks"),
    _producer("python-sbom", "supply-chain-security.yml:sbom-and-licenses", "python-sbom"),
    _producer("python-vulnerability-scan", "supply-chain-security.yml:vulnerability-and-static-security", "pip-audit-2.9.0"),
    _producer("readiness-failure", "ci.yml:backend-evidence", "pytest-readiness-failure"),
    _producer("readiness-transition", "enterprise-readiness.yml:deployment-and-recovery", "container-readiness-transition"),
    _producer("recovery-duration", "enterprise-readiness.yml:deployment-and-recovery", "recovery-duration", blocked_exit_statuses=(78,)),
    _producer("recovery-stability", "enterprise-readiness.yml:deployment-and-recovery", "recovery-stability"),
    _producer("request-concurrency", "enterprise-readiness.yml:deployment-and-recovery", "request-concurrency-diagnostic", blocked_exit_statuses=(78,)),
    _producer("resource-feasibility-contracts", "ci.yml:backend-evidence", "pytest-resource-feasibility"),
    _producer("restore", "enterprise-readiness.yml:deployment-and-recovery", "recovery-restore"),
    _producer("restored-readiness", "enterprise-readiness.yml:deployment-and-recovery", "restored-readiness"),
    _producer("rollback-procedure", "enterprise-readiness.yml:deployment-and-recovery", "rollback-procedure"),
    _producer("schema-compatibility", "ci.yml:backend-evidence", "pytest-schema-compatibility"),
    _producer("scientific-canonical-serialization", "ci.yml:backend-evidence", "pytest-scientific-canonical"),
    _producer("scientific-contract-compatibility", "ci.yml:backend-evidence", "pytest-scientific-contracts"),
    _producer("secret-path-redaction", "ci.yml:backend-evidence", "pytest-secret-path-redaction"),
    _producer("secret-scan", "supply-chain-security.yml:vulnerability-and-static-security", "gitleaks-8.24.2-secret-scan"),
    _producer("static-security-scan", "supply-chain-security.yml:vulnerability-and-static-security", "ruff-0.15.20-bandit-1.8.6-trivy-0.58.2"),
    _producer("tenant-isolation", "ci.yml:backend-evidence", "pytest-tenant-isolation"),
    _producer("unassigned-plan", "ci.yml:backend-evidence", "pytest-unassigned-plan"),
    _producer("workflow-api-security", "ci.yml:backend-evidence", "pytest-workflow-api-security"),
    _producer("workflow-graph-backend", "ci.yml:backend-evidence", "pytest-workflow-graph-backend"),
    _producer("workflow-recovery", "ci.yml:backend-evidence", "pytest-workflow-recovery"),
)

REQUIRED_ENTERPRISE_GATES = tuple(
    producer.gate_identifier for producer in ENTERPRISE_GATE_PRODUCERS
)
_PRODUCER_BY_GATE = {
    producer.gate_identifier: producer for producer in ENTERPRISE_GATE_PRODUCERS
}

if REQUIRED_ENTERPRISE_GATES != tuple(sorted(REQUIRED_ENTERPRISE_GATES)) or len(
    REQUIRED_ENTERPRISE_GATES
) != len(set(REQUIRED_ENTERPRISE_GATES)):
    raise RuntimeError("Enterprise gate producer registry is invalid.")


class EvidenceArtifact(CanonicalModel):
    """One actual local artifact downloaded or produced for a gate."""

    relative_path: str
    byte_length: int = Field(ge=0, le=1024 * 1024 * 1024)
    content_sha256: str

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if (
            path.is_absolute()
            or "\\" in value
            or any(part in {"", ".", ".."} for part in path.parts)
            or len(value.encode("utf-8")) > 1024
        ):
            raise ValueError("Evidence artifact path is invalid.")
        return path.as_posix()

    @field_validator("content_sha256")
    @classmethod
    def validate_content_sha256(cls, value: str) -> str:
        return validate_sha256(value)


def readiness_evidence_fingerprint(value: dict[str, Any]) -> str:
    return sha256_fingerprint(
        {key: item for key, item in value.items() if key != "evidence_fingerprint"}
    )


class ReadinessEvidenceRecord(CanonicalModel):
    """Evidence for an operation that actually ran or was actually blocked."""

    schema_version: str
    gate_identifier: str
    producer_identity: str
    operation_identity: str
    command_identity: str
    environment_identity: str
    tool_version: str | None = None
    source_commit: str
    started_at: datetime
    completed_at: datetime | None = None
    exit_status: int | None = None
    classification: EvidenceClassification
    passed_count: int | None = Field(default=None, ge=0, le=10_000_000)
    failed_count: int | None = Field(default=None, ge=0, le=10_000_000)
    skipped_count: int | None = Field(default=None, ge=0, le=10_000_000)
    artifacts: tuple[EvidenceArtifact, ...] = Field(default=(), max_length=128)
    notes: tuple[str, ...] = Field(default=(), max_length=32)
    evidence_fingerprint: str

    @field_validator("schema_version")
    @classmethod
    def validate_schema(cls, value: str) -> str:
        if value != READINESS_EVIDENCE_SCHEMA_VERSION:
            raise ValueError("Readiness evidence schema is unsupported.")
        return value

    @field_validator(
        "gate_identifier",
        "producer_identity",
        "operation_identity",
        "command_identity",
        "environment_identity",
    )
    @classmethod
    def validate_identities(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("tool_version")
    @classmethod
    def validate_tool_version(cls, value: str | None) -> str | None:
        if value is None:
            return None
        rejected = ("/", "\\", "token", "secret", "credential", "bearer")
        if (
            not value.strip()
            or len(value.encode("utf-8")) > 256
            or "\n" in value
            or "\r" in value
            or any(fragment in value.casefold() for fragment in rejected)
        ):
            raise ValueError("Evidence tool version is unsafe.")
        return value

    @field_validator("source_commit")
    @classmethod
    def validate_source_commit(cls, value: str) -> str:
        if len(value) != 40 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError("Evidence source commit is invalid.")
        return value

    @field_validator("started_at", "completed_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Evidence timestamps must be timezone-aware.")
        return value.astimezone(UTC)

    @field_validator("notes")
    @classmethod
    def validate_notes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        rejected_fragments = ("bearer ", "token=", "secret=", ":\\", "/home/")
        if any(
            not item.strip()
            or len(item) > 1024
            or "\n" in item
            or "\r" in item
            or any(fragment in item.casefold() for fragment in rejected_fragments)
            for item in value
        ):
            raise ValueError("Evidence notes are invalid.")
        return value

    @field_validator("evidence_fingerprint")
    @classmethod
    def validate_fingerprint(cls, value: str) -> str:
        return validate_sha256(value)

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        if self.completed_at is not None and self.completed_at < self.started_at:
            raise ValueError("Evidence completion precedes its start.")
        if self.classification == EvidenceClassification.PASS and (
            self.completed_at is None
            or self.exit_status != 0
            or self.failed_count
            or self.skipped_count
        ):
            raise ValueError("PASS evidence must be a completed successful operation.")
        if self.classification == EvidenceClassification.FAIL and (
            self.completed_at is None
            or self.exit_status is None
            or (self.exit_status == 0 and not self.failed_count)
        ):
            raise ValueError("FAIL evidence requires a failed operation or report.")
        if self.classification == EvidenceClassification.BLOCKED and self.exit_status == 0:
            raise ValueError("Blocked evidence cannot claim a successful command.")
        if self.classification == EvidenceClassification.PENDING and self.exit_status is not None:
            raise ValueError("Pending evidence cannot invent an exit status.")
        if (
            self.classification == EvidenceClassification.PENDING
            and self.completed_at is not None
        ):
            raise ValueError("Pending evidence cannot invent a completion time.")
        paths = tuple(item.relative_path for item in self.artifacts)
        if paths != tuple(sorted(paths)) or len(paths) != len(set(paths)):
            raise ValueError("Evidence artifacts must be unique and ordered.")
        expected = readiness_evidence_fingerprint(self.model_dump(mode="json"))
        if self.evidence_fingerprint != expected:
            raise ValueError("Readiness evidence fingerprint is invalid.")
        return self


def enterprise_decision_fingerprint(value: dict[str, Any]) -> str:
    return sha256_fingerprint(
        {key: item for key, item in value.items() if key != "decision_fingerprint"}
    )


class EnterpriseReadinessDecision(CanonicalModel):
    """Deterministic decision across the complete mandatory gate set."""

    schema_version: str
    source_commit: str
    required_gates: tuple[str, ...]
    evidence_records: tuple[ReadinessEvidenceRecord, ...]
    missing_evidence: tuple[str, ...]
    failed_evidence: tuple[str, ...]
    blocked_evidence: tuple[str, ...]
    pending_evidence: tuple[str, ...]
    final_decision: EvidenceClassification
    decision_fingerprint: str

    @field_validator("schema_version")
    @classmethod
    def validate_schema(cls, value: str) -> str:
        if value != ENTERPRISE_DECISION_SCHEMA_VERSION:
            raise ValueError("Enterprise decision schema is unsupported.")
        return value

    @field_validator("source_commit")
    @classmethod
    def validate_source_commit(cls, value: str) -> str:
        if len(value) != 40 or any(
            character not in "0123456789abcdef" for character in value
        ):
            raise ValueError("Enterprise decision source commit is invalid.")
        return value

    @model_validator(mode="after")
    def validate_decision(self) -> Self:
        if self.required_gates != tuple(sorted(self.required_gates)) or len(
            self.required_gates
        ) != len(set(self.required_gates)):
            raise ValueError("Enterprise decision gates are invalid.")
        evidence_gates = tuple(item.gate_identifier for item in self.evidence_records)
        if evidence_gates != tuple(sorted(evidence_gates)) or len(evidence_gates) != len(
            set(evidence_gates)
        ):
            raise ValueError("Enterprise decision evidence is invalid.")
        if any(item.source_commit != self.source_commit for item in self.evidence_records):
            raise ValueError("Enterprise decision evidence is stale.")
        if any(gate not in self.required_gates for gate in evidence_gates):
            raise ValueError("Enterprise decision evidence is unexpected.")
        by_gate = {item.gate_identifier: item for item in self.evidence_records}
        missing = tuple(gate for gate in self.required_gates if gate not in by_gate)
        failed = tuple(
            gate
            for gate, item in sorted(by_gate.items())
            if item.classification == EvidenceClassification.FAIL
        )
        blocked = tuple(
            gate
            for gate, item in sorted(by_gate.items())
            if item.classification == EvidenceClassification.BLOCKED
        )
        pending = tuple(
            gate
            for gate, item in sorted(by_gate.items())
            if item.classification == EvidenceClassification.PENDING
        )
        expected_decision = (
            EvidenceClassification.FAIL
            if failed
            else EvidenceClassification.BLOCKED
            if blocked
            else EvidenceClassification.PENDING
            if missing or pending
            else EvidenceClassification.PASS
        )
        if (
            self.missing_evidence != missing
            or self.failed_evidence != failed
            or self.blocked_evidence != blocked
            or self.pending_evidence != pending
            or self.final_decision != expected_decision
        ):
            raise ValueError("Enterprise decision classification is inconsistent.")
        expected = enterprise_decision_fingerprint(self.model_dump(mode="json"))
        if self.decision_fingerprint != expected:
            raise ValueError("Enterprise decision fingerprint is invalid.")
        return self


def decide_enterprise_readiness(
    *,
    source_commit: str,
    evidence_records: tuple[ReadinessEvidenceRecord, ...],
    required_gates: tuple[str, ...] = REQUIRED_ENTERPRISE_GATES,
) -> EnterpriseReadinessDecision:
    """Fail closed on stale, duplicate, missing, failed, or blocked evidence."""

    if len(source_commit) != 40 or any(
        character not in "0123456789abcdef" for character in source_commit
    ):
        raise ValueError("Enterprise decision source commit is invalid.")
    required = tuple(sorted(validate_identifier(gate) for gate in required_gates))
    if len(required) != len(set(required)):
        raise ValueError("Required enterprise gates must be unique.")
    by_gate: dict[str, ReadinessEvidenceRecord] = {}
    for record in evidence_records:
        if record.source_commit != source_commit or record.gate_identifier not in required:
            raise ValueError("Enterprise evidence is stale or unexpected.")
        expected_producer = _PRODUCER_BY_GATE.get(record.gate_identifier)
        if expected_producer is not None and (
            record.producer_identity != expected_producer.workflow_job
            or record.operation_identity != expected_producer.operation_identity
        ):
            raise ValueError("Enterprise evidence producer is unexpected.")
        if record.gate_identifier in by_gate:
            raise ValueError("Enterprise evidence is contradictory or duplicated.")
        by_gate[record.gate_identifier] = record
    missing = tuple(gate for gate in required if gate not in by_gate)
    failed = tuple(
        gate
        for gate, item in sorted(by_gate.items())
        if item.classification == EvidenceClassification.FAIL
    )
    blocked = tuple(
        gate
        for gate, item in sorted(by_gate.items())
        if item.classification == EvidenceClassification.BLOCKED
    )
    pending = tuple(
        gate
        for gate, item in sorted(by_gate.items())
        if item.classification == EvidenceClassification.PENDING
    )
    if failed:
        decision = EvidenceClassification.FAIL
    elif blocked:
        decision = EvidenceClassification.BLOCKED
    elif missing or pending:
        decision = EvidenceClassification.PENDING
    else:
        decision = EvidenceClassification.PASS
    value: dict[str, Any] = {
        "schema_version": ENTERPRISE_DECISION_SCHEMA_VERSION,
        "source_commit": source_commit,
        "required_gates": required,
        "evidence_records": [
            item.model_dump(mode="json")
            for item in sorted(
                evidence_records, key=lambda item: item.gate_identifier
            )
        ],
        "missing_evidence": missing,
        "failed_evidence": failed,
        "blocked_evidence": blocked,
        "pending_evidence": pending,
        "final_decision": decision.value,
    }
    value["decision_fingerprint"] = enterprise_decision_fingerprint(value)
    return EnterpriseReadinessDecision.model_validate(value)


def load_evidence_records(root: Path, *, source_commit: str) -> tuple[ReadinessEvidenceRecord, ...]:
    """Load canonical records and verify every referenced artifact byte-for-byte."""

    trusted = _safe_root(root)
    records: list[ReadinessEvidenceRecord] = []
    for path in _evidence_record_files(trusted):
        payload = _read_file(path, 4 * 1024 * 1024)
        record = ReadinessEvidenceRecord.model_validate_json(payload)
        if payload != record.to_canonical_json().encode("utf-8") or record.source_commit != source_commit:
            raise ValueError("Readiness evidence is stale or noncanonical.")
        expected_producer = _PRODUCER_BY_GATE.get(record.gate_identifier)
        if expected_producer is not None and (
            record.producer_identity != expected_producer.workflow_job
            or record.operation_identity != expected_producer.operation_identity
        ):
            raise ValueError("Readiness evidence producer is invalid.")
        bundle_root = path.parent.parent if path.parent.name == "records" else trusted
        for artifact in record.artifacts:
            artifact_path = _safe_descendant_file(bundle_root, artifact.relative_path)
            byte_length, content_sha256 = _hash_regular_file(artifact_path)
            if (
                byte_length != artifact.byte_length
                or content_sha256 != artifact.content_sha256
            ):
                raise ValueError("Readiness artifact fingerprint is invalid.")
        records.append(record)
    return tuple(records)


def evidence_run_main(argv: list[str] | None = None) -> int:
    """Execute one mandatory command and write its actual canonical evidence."""

    parser = argparse.ArgumentParser(prog="cgr-pulsate-evidence-run")
    parser.add_argument("--gate", required=True)
    parser.add_argument("--producer", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--evidence-root", required=True, type=Path)
    parser.add_argument("--output", required=True)
    parser.add_argument("--runner-identity-class", default="github-hosted")
    parser.add_argument("--version-executable")
    parser.add_argument("--artifact", action="append", default=[])
    parser.add_argument("--optional-artifact", action="append", default=[])
    parser.add_argument("command", nargs=argparse.REMAINDER)
    arguments = parser.parse_args(argv)

    command = list(arguments.command)
    if command and command[0] == "--":
        command.pop(0)
    try:
        producer = _PRODUCER_BY_GATE[arguments.gate]
        if producer.workflow_job != arguments.producer:
            raise ValueError("Evidence producer is not approved for this gate.")
        if arguments.output != producer.evidence_filename:
            raise ValueError("Evidence output name is not canonical for this gate.")
        if not command:
            raise ValueError("A mandatory evidence command is required.")
        source_commit = _validated_source_commit(arguments.source_commit)
        if _current_source_commit() != source_commit:
            raise ValueError("Evidence source commit does not match the checkout.")
        evidence_root = _safe_root(arguments.evidence_root)
        output = _safe_record_output(evidence_root, arguments.output)
        environment_identity = _safe_environment_identity(
            arguments.runner_identity_class
        )
        command_identity = f"sha256:{sha256_fingerprint(command)}"
        version_executable = arguments.version_executable or command[0]
        if arguments.version_executable is not None and (
            PurePosixPath(version_executable).parts != (version_executable,)
            or "\\" in version_executable
        ):
            raise ValueError("Version executable identity is unsafe.")
        tool_version = _safe_tool_version(version_executable)
        required_artifacts = tuple(
            _validated_artifact_declaration(value) for value in arguments.artifact
        )
        optional_artifacts = tuple(
            _validated_artifact_declaration(value)
            for value in arguments.optional_artifact
        )
        if len(required_artifacts) + len(optional_artifacts) > 128:
            raise ValueError("Too many evidence artifacts were declared.")
        if len(set(required_artifacts + optional_artifacts)) != len(
            required_artifacts + optional_artifacts
        ):
            raise ValueError("Evidence artifact declarations must be unique.")
    except (KeyError, OSError, RuntimeError, ValueError):
        return 2

    started_at = datetime.now(UTC)
    child_exit_status: int | None = None
    classification = EvidenceClassification.BLOCKED
    failed_count: int | None = None
    notes: tuple[str, ...] = ()
    runner_exit_status = 125
    try:
        completed = subprocess.run(command, check=False, shell=False)
        child_exit_status = completed.returncode
        classification = (
            EvidenceClassification.PASS
            if child_exit_status == 0
            else EvidenceClassification.BLOCKED
            if child_exit_status in producer.blocked_exit_statuses
            else EvidenceClassification.FAIL
        )
        failed_count = 1 if classification == EvidenceClassification.FAIL else None
        if classification == EvidenceClassification.BLOCKED:
            notes = ("approved-acceptance-policy-unavailable",)
        runner_exit_status = child_exit_status
    except (FileNotFoundError, PermissionError, OSError):
        notes = ("command-execution-blocked",)
    completed_at = datetime.now(UTC)

    artifacts: tuple[EvidenceArtifact, ...] = ()
    if child_exit_status is not None:
        try:
            if _current_source_commit() != source_commit:
                raise ValueError("Evidence source commit changed during execution.")
            artifacts = _declared_evidence_artifacts(
                evidence_root,
                required=required_artifacts,
                optional=optional_artifacts,
            )
        except ValueError:
            classification = EvidenceClassification.FAIL
            failed_count = 1
            notes = ("evidence-postcondition-invalid",)
            runner_exit_status = child_exit_status if child_exit_status else 1

    value: dict[str, Any] = {
        "schema_version": READINESS_EVIDENCE_SCHEMA_VERSION,
        "gate_identifier": producer.gate_identifier,
        "producer_identity": producer.workflow_job,
        "operation_identity": producer.operation_identity,
        "command_identity": command_identity,
        "environment_identity": environment_identity,
        "tool_version": tool_version,
        "source_commit": source_commit,
        "started_at": started_at.isoformat().replace("+00:00", "Z"),
        "completed_at": completed_at.isoformat().replace("+00:00", "Z"),
        "exit_status": child_exit_status,
        "classification": classification.value,
        "passed_count": None,
        "failed_count": failed_count,
        "skipped_count": None,
        "artifacts": [item.model_dump(mode="json") for item in artifacts],
        "notes": notes,
    }
    value["evidence_fingerprint"] = readiness_evidence_fingerprint(value)
    try:
        record = ReadinessEvidenceRecord.model_validate(value)
        _write_exclusive(output, record.to_canonical_json().encode("utf-8"))
    except (OSError, RuntimeError, ValueError):
        return 2
    return runner_exit_status


def enterprise_gate_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cgr-pulsate-enterprise-gate")
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--evidence-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args(argv)
    try:
        actual_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
        if actual_commit != arguments.source_commit:
            raise ValueError("Source commit does not match the current checkout.")
        records = load_evidence_records(arguments.evidence_root, source_commit=actual_commit)
        decision = decide_enterprise_readiness(
            source_commit=actual_commit,
            evidence_records=records,
        )
        _write_exclusive(arguments.output, decision.to_canonical_json().encode("utf-8"))
        print(decision.to_canonical_json())
        return 0 if decision.final_decision == EvidenceClassification.PASS else 1
    except Exception:
        print(
            json.dumps(
                {
                    "status": "unavailable",
                    "category": "enterprise_evidence_invalid",
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 2


def _validated_source_commit(value: str) -> str:
    if len(value) != 40 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError("Evidence source commit is invalid.")
    return value


def _current_source_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
            shell=False,
        )
        return _validated_source_commit(result.stdout.strip())
    except (OSError, subprocess.SubprocessError, ValueError):
        raise ValueError("Source commit identity is unavailable.") from None


def _safe_environment_identity(runner_identity_class: str) -> str:
    if runner_identity_class not in {"github-hosted", "github-self-hosted", "local"}:
        raise ValueError("Runner identity class is unsupported.")

    def component(value: str) -> str:
        safe = "".join(
            character.casefold() if character.isalnum() else "-"
            for character in value
        ).strip("-")
        return safe or "unknown"

    return validate_identifier(
        "-".join(
            (
                component(platform.system()),
                component(platform.machine()),
                f"python-{sys.version_info.major}-{sys.version_info.minor}-{sys.version_info.micro}",
                runner_identity_class,
            )
        )
    )


def _safe_tool_version(executable: str) -> str | None:
    try:
        result = subprocess.run(
            [executable, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
            shell=False,
        )
        line = (result.stdout or result.stderr).splitlines()[0].strip()
        if not line:
            return None
        return ReadinessEvidenceRecord.validate_tool_version(line)
    except (IndexError, OSError, subprocess.SubprocessError, ValueError):
        return None


def _validated_artifact_declaration(value: str) -> str:
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or "\\" in value
        or any(part in {"", ".", ".."} for part in path.parts)
        or not path.parts
        or path.parts[0] == "records"
        or len(value.encode("utf-8")) > 1024
    ):
        raise ValueError("Evidence artifact declaration is invalid.")
    return path.as_posix()


def _safe_record_output(root: Path, output_name: str) -> Path:
    if PurePosixPath(output_name).parts != (output_name,) or not output_name.endswith(
        ".json"
    ):
        raise ValueError("Evidence output name is invalid.")
    records = _safe_descendant_directory(root, "records")
    target = records / output_name
    try:
        target.lstat()
    except FileNotFoundError:
        return target
    except OSError:
        raise ValueError("Evidence output is unsafe.") from None
    raise ValueError("Evidence output already exists.")


def _safe_descendant_directory(root: Path, relative_path: str) -> Path:
    current = root
    try:
        for part in PurePosixPath(relative_path).parts:
            current = current / part
            metadata = current.lstat()
            junction = getattr(current, "is_junction", None)
            if (
                stat.S_ISLNK(metadata.st_mode)
                or bool(junction and junction())
                or not stat.S_ISDIR(metadata.st_mode)
            ):
                raise OSError
        resolved = current.resolve(strict=True)
        if resolved == root or root not in resolved.parents:
            raise OSError
        return resolved
    except (OSError, RuntimeError):
        raise ValueError("Evidence directory is unsafe.") from None


def _declared_evidence_artifacts(
    root: Path,
    *,
    required: tuple[str, ...],
    optional: tuple[str, ...],
) -> tuple[EvidenceArtifact, ...]:
    artifacts: list[EvidenceArtifact] = []
    for relative_path in required + optional:
        try:
            path = _safe_descendant_file(root, relative_path)
            _validate_report_syntax(path)
            byte_length, content_sha256 = _hash_regular_file(path)
        except ValueError:
            if relative_path in optional:
                continue
            raise
        artifacts.append(
            EvidenceArtifact(
                relative_path=relative_path,
                byte_length=byte_length,
                content_sha256=content_sha256,
            )
        )
    return tuple(sorted(artifacts, key=lambda item: item.relative_path))


def _validate_report_syntax(path: Path) -> None:
    lower_name = path.name.casefold()
    try:
        if lower_name.endswith((".json", ".sarif")):
            payload = _read_file(path, 128 * 1024 * 1024)
            json.loads(payload)
        elif lower_name.endswith((".xml", ".junit")):
            ElementTree.parse(path)
    except (ElementTree.ParseError, json.JSONDecodeError, UnicodeDecodeError, OSError):
        raise ValueError("Mandatory evidence report is malformed.") from None


def _hash_regular_file(path: Path) -> tuple[int, str]:
    try:
        metadata = path.lstat()
        junction = getattr(path, "is_junction", None)
        if (
            stat.S_ISLNK(metadata.st_mode)
            or bool(junction and junction())
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size > 1024 * 1024 * 1024
        ):
            raise OSError
        digest = hashlib.sha256()
        byte_length = 0
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                byte_length += len(chunk)
                if byte_length > 1024 * 1024 * 1024:
                    raise OSError
                digest.update(chunk)
        return byte_length, digest.hexdigest()
    except OSError:
        raise ValueError("Evidence file is unsafe.") from None


def _evidence_record_files(root: Path) -> tuple[Path, ...]:
    records: list[Path] = []
    pending = [root]
    inspected = 0
    try:
        while pending:
            directory = pending.pop()
            for entry in os.scandir(directory):
                inspected += 1
                if inspected > 100_000:
                    raise OSError
                path = Path(entry.path)
                metadata = path.lstat()
                junction = getattr(path, "is_junction", None)
                if stat.S_ISLNK(metadata.st_mode) or bool(junction and junction()):
                    raise OSError
                if stat.S_ISDIR(metadata.st_mode):
                    pending.append(path)
                elif (
                    stat.S_ISREG(metadata.st_mode)
                    and path.suffix == ".json"
                    and (path.parent == root or path.parent.name == "records")
                ):
                    records.append(path)
                elif not stat.S_ISREG(metadata.st_mode):
                    raise OSError
    except OSError:
        raise ValueError("Evidence tree is unsafe.") from None
    return tuple(sorted(records, key=lambda item: item.as_posix()))


def _safe_root(path: Path) -> Path:
    candidate = Path(os.path.abspath(path))
    try:
        metadata = candidate.lstat()
        junction = getattr(candidate, "is_junction", None)
        if (
            stat.S_ISLNK(metadata.st_mode)
            or bool(junction and junction())
            or not stat.S_ISDIR(metadata.st_mode)
        ):
            raise OSError
        return candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        raise ValueError("Evidence root is unsafe.") from None


def _read_file(path: Path, maximum: int) -> bytes:
    try:
        metadata = path.lstat()
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size > maximum
        ):
            raise OSError
        payload = path.read_bytes()
        if len(payload) > maximum:
            raise OSError
        return payload
    except OSError:
        raise ValueError("Evidence file is unsafe.") from None


def _safe_descendant_file(root: Path, relative_path: str) -> Path:
    try:
        current = root
        parts = PurePosixPath(relative_path).parts
        for index, part in enumerate(parts):
            current = current / part
            metadata = current.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                raise OSError
            if index < len(parts) - 1 and not stat.S_ISDIR(metadata.st_mode):
                raise OSError
        resolved = current.resolve(strict=True)
        if resolved == root or root not in resolved.parents:
            raise OSError
        return resolved
    except (OSError, RuntimeError):
        raise ValueError("Evidence artifact path is unsafe.") from None


def _write_exclusive(path: Path, payload: bytes) -> None:
    parent = _safe_root(path.parent)
    target = parent / path.name
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, target, follow_symlinks=False)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


__all__ = [
    "ENTERPRISE_GATE_PRODUCERS",
    "ENTERPRISE_DECISION_SCHEMA_VERSION",
    "READINESS_EVIDENCE_SCHEMA_VERSION",
    "REQUIRED_ENTERPRISE_GATES",
    "EnterpriseReadinessDecision",
    "EnterpriseGateProducer",
    "EvidenceArtifact",
    "EvidenceClassification",
    "ReadinessEvidenceRecord",
    "decide_enterprise_readiness",
    "evidence_run_main",
    "enterprise_decision_fingerprint",
    "enterprise_gate_main",
    "load_evidence_records",
    "readiness_evidence_fingerprint",
]


if __name__ == "__main__":
    raise SystemExit(enterprise_gate_main())
