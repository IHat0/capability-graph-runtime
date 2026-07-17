"""Public input projection and hostile candidate-output collection."""

from __future__ import annotations

import hashlib
import json
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse

from pydantic import ValidationError

from cgr.quantum_preflight.artifacts import write_json_atomic
from cgr.quantum_preflight.contracts import ManifestEnvelope, QuantumChemistryExperiment
from cgr.science import ArtifactPointer, sha256_fingerprint

from .contracts import (
    PUBLIC_INPUT_SCHEMA,
    CandidateArtifactClaim,
    CandidateFinding,
    CandidateOutputSummary,
    CandidateSandboxPolicy,
)
from .findings import finding


@dataclass(frozen=True)
class CollectedCandidateFile:
    relative_path: str
    content_sha256: str
    byte_size: int
    payload: Any | None

    @property
    def pointer(self) -> ArtifactPointer:
        return ArtifactPointer(
            artifact_identifier=_path_identifier(self.relative_path),
            content_sha256=self.content_sha256,
        )


@dataclass(frozen=True)
class CandidateOutputPackage:
    files: tuple[CollectedCandidateFile, ...]
    package_sha256: str
    total_bytes: int
    findings: tuple[CandidateFinding, ...]

    def by_path(self) -> dict[str, CollectedCandidateFile]:
        return {item.relative_path: item for item in self.files}


def public_experiment_document(
    manifest: ManifestEnvelope,
    *,
    candidate_dependency_lock_sha256: str,
) -> dict[str, Any]:
    """Expose the declared experiment and candidate policy, never trusted answers."""
    return {
        "schema_version": PUBLIC_INPUT_SCHEMA,
        "candidate_output_schema_version": "cgr.quantum-candidate-output/1.0.0",
        "required_workflow_profile": "lih_statevector_vqe",
        "candidate_dependency_lock_sha256": candidate_dependency_lock_sha256,
        "experiment": manifest.experiment.model_dump(mode="json"),
    }


def write_public_experiment(
    path: Path,
    manifest: ManifestEnvelope,
    *,
    candidate_dependency_lock_sha256: str,
) -> str:
    document = public_experiment_document(
        manifest,
        candidate_dependency_lock_sha256=candidate_dependency_lock_sha256,
    )
    write_json_atomic(path, document, maximum_bytes=2 * 1024 * 1024)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def collect_candidate_output(
    output_directory: Path,
    policy: CandidateSandboxPolicy,
) -> CandidateOutputPackage:
    findings: list[CandidateFinding] = []
    collected: list[CollectedCandidateFile] = []
    total_bytes = 0
    if not output_directory.is_dir():
        return CandidateOutputPackage(
            files=(),
            package_sha256=sha256_fingerprint([]),
            total_bytes=0,
            findings=(
                finding(
                    "candidate_output_missing",
                    "Candidate output directory was not produced.",
                ),
            ),
        )
    root = output_directory.resolve()
    for path in sorted(output_directory.rglob("*"), key=lambda item: item.as_posix()):
        try:
            relative = path.relative_to(output_directory).as_posix()
        except ValueError:
            findings.append(
                finding(
                    "candidate_output_path_violation",
                    "Candidate output cannot be relativized to the output root.",
                )
            )
            continue
        try:
            mode = path.lstat().st_mode
        except OSError:
            findings.append(
                finding(
                    "candidate_output_path_violation",
                    "Candidate output metadata could not be inspected safely.",
                    subject_artifact=relative,
                )
            )
            continue
        if stat.S_ISLNK(mode):
            findings.append(
                finding(
                    "candidate_output_path_violation",
                    "Symbolic links are prohibited in candidate output.",
                    subject_artifact=relative,
                )
            )
            continue
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            findings.append(
                finding(
                    "candidate_output_path_violation",
                    "Device files, sockets, and FIFOs are prohibited in candidate output.",
                    subject_artifact=relative,
                )
            )
            continue
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            findings.append(
                finding(
                    "candidate_output_path_violation",
                    "Candidate output path cannot be resolved safely.",
                    subject_artifact=relative,
                )
            )
            continue
        if resolved != root and root not in resolved.parents:
            findings.append(
                finding(
                    "candidate_output_path_violation",
                    "Candidate artifact resolves outside the output package.",
                    subject_artifact=relative,
                )
            )
            continue
        size = path.stat().st_size
        total_bytes += size
        if size > policy.maximum_file_bytes:
            findings.append(
                finding(
                    "candidate_output_path_violation",
                    "Candidate artifact exceeds the individual-file quota.",
                    subject_artifact=relative,
                    expected=policy.maximum_file_bytes,
                    observed=size,
                )
            )
            continue
        data = path.read_bytes()
        payload: Any | None = None
        if path.suffix.lower() == ".json":
            try:
                payload = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                payload = None
        collected.append(
            CollectedCandidateFile(
                relative_path=relative,
                content_sha256=hashlib.sha256(data).hexdigest(),
                byte_size=size,
                payload=payload,
            )
        )
    if len(collected) > policy.maximum_files:
        findings.append(
            finding(
                "candidate_output_path_violation",
                "Candidate output exceeds the file-count quota.",
                expected=policy.maximum_files,
                observed=len(collected),
            )
        )
    if total_bytes > policy.maximum_output_bytes:
        findings.append(
            finding(
                "candidate_output_path_violation",
                "Candidate output exceeds the total-size quota.",
                expected=policy.maximum_output_bytes,
                observed=total_bytes,
            )
        )
    package_identity = [
        {
            "path": item.relative_path,
            "content_sha256": item.content_sha256,
            "byte_size": item.byte_size,
        }
        for item in collected
    ]
    return CandidateOutputPackage(
        files=tuple(collected),
        package_sha256=sha256_fingerprint(package_identity),
        total_bytes=total_bytes,
        findings=tuple(findings),
    )


def load_candidate_summary(
    package: CandidateOutputPackage,
) -> tuple[CandidateOutputSummary | None, list[CandidateFinding]]:
    files = package.by_path()
    summary_file = files.get("candidate-summary.json")
    if summary_file is None:
        return None, [
            finding(
                "candidate_output_missing",
                "Required candidate-summary.json is absent.",
                subject_artifact="candidate-summary.json",
            )
        ]
    if not isinstance(summary_file.payload, dict):
        return None, [
            finding(
                "candidate_protocol_invalid",
                "Candidate summary is not valid JSON object evidence.",
                subject_artifact="candidate-summary.json",
            )
        ]
    try:
        summary = CandidateOutputSummary.model_validate(summary_file.payload)
    except ValidationError as exc:
        return None, [
            finding(
                "candidate_protocol_invalid",
                f"Candidate summary violates the v1 protocol: {exc.errors()[0]['msg']}",
                subject_artifact="candidate-summary.json",
            )
        ]
    return summary, []


def validate_artifact_claim_path(claim: CandidateArtifactClaim) -> bool:
    value = claim.path.strip().replace("\\", "/")
    parsed = urlparse(value)
    path = PurePosixPath(value)
    return bool(
        value
        and not parsed.scheme
        and not value.startswith(("/", "//"))
        and ".." not in path.parts
        and path.as_posix() == value
    )


def source_tree_sha256(directory: Path) -> str:
    records: list[dict[str, str]] = []
    for path in sorted(directory.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_symlink() or not path.is_file():
            continue
        relative = path.relative_to(directory).as_posix()
        records.append(
            {
                "path": relative,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    return sha256_fingerprint(records)


def experiment_fingerprint(experiment: QuantumChemistryExperiment) -> str:
    return experiment.fingerprint


def _path_identifier(path: str) -> str:
    digest = hashlib.sha256(path.encode("utf-8")).hexdigest()[:24]
    return f"candidate-output-{digest}"
