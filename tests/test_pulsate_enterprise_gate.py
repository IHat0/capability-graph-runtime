"""Fail-closed E7 evidence and decision tests."""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cgr.pulsate_api.enterprise_gate import (
    ENTERPRISE_GATE_PRODUCERS,
    REQUIRED_ENTERPRISE_GATES,
    READINESS_EVIDENCE_SCHEMA_VERSION,
    EvidenceArtifact,
    EvidenceClassification,
    ReadinessEvidenceRecord,
    decide_enterprise_readiness,
    evidence_run_main,
    enterprise_gate_main,
    load_evidence_records,
    readiness_evidence_fingerprint,
)


COMMIT = "a" * 40
NOW = datetime(2026, 8, 4, tzinfo=UTC)


def test_required_enterprise_gate_set_is_explicit_unique_and_ordered() -> None:
    assert REQUIRED_ENTERPRISE_GATES == tuple(sorted(REQUIRED_ENTERPRISE_GATES))
    assert len(REQUIRED_ENTERPRISE_GATES) == len(set(REQUIRED_ENTERPRISE_GATES))
    assert {
        "container-startup-duration",
        "lifecycle-stability",
        "memory-process-observations",
        "performance-acceptance",
        "performance-diagnostics",
        "planning-latency",
        "readiness-transition",
        "recovery-duration",
        "recovery-stability",
        "request-concurrency",
    } <= set(REQUIRED_ENTERPRISE_GATES)


def _record(gate: str, classification: EvidenceClassification) -> ReadinessEvidenceRecord:
    registered = {
        item.gate_identifier: item for item in ENTERPRISE_GATE_PRODUCERS
    }.get(gate)
    value = {
        "schema_version": READINESS_EVIDENCE_SCHEMA_VERSION,
        "gate_identifier": gate,
        "producer_identity": (
            registered.workflow_job if registered is not None else "local-test"
        ),
        "operation_identity": (
            registered.operation_identity
            if registered is not None
            else "pytest-focused-gate"
        ),
        "command_identity": "sha256:" + "c" * 64,
        "environment_identity": "local-test-only",
        "tool_version": None,
        "source_commit": COMMIT,
        "started_at": NOW.isoformat().replace("+00:00", "Z"),
        "completed_at": (
            None
            if classification == EvidenceClassification.PENDING
            else NOW.isoformat().replace("+00:00", "Z")
        ),
        "exit_status": 0 if classification == EvidenceClassification.PASS else 1 if classification == EvidenceClassification.FAIL else None,
        "classification": classification.value,
        "passed_count": 1 if classification == EvidenceClassification.PASS else 0,
        "failed_count": 1 if classification == EvidenceClassification.FAIL else 0,
        "skipped_count": 0,
        "artifacts": [],
        "notes": [],
    }
    value["evidence_fingerprint"] = readiness_evidence_fingerprint(value)
    return ReadinessEvidenceRecord.model_validate(value)


def test_decision_passes_only_when_every_required_gate_actually_passed() -> None:
    records = (_record("gate-a", EvidenceClassification.PASS), _record("gate-b", EvidenceClassification.PASS))
    decision = decide_enterprise_readiness(
        source_commit=COMMIT,
        evidence_records=records,
        required_gates=("gate-a", "gate-b"),
    )
    assert decision.final_decision == EvidenceClassification.PASS
    assert not decision.missing_evidence


@pytest.mark.parametrize(
    ("records", "expected"),
    (
        ((), EvidenceClassification.PENDING),
        ((_record("gate-a", EvidenceClassification.PENDING),), EvidenceClassification.PENDING),
        ((_record("gate-a", EvidenceClassification.BLOCKED),), EvidenceClassification.BLOCKED),
        ((_record("gate-a", EvidenceClassification.FAIL),), EvidenceClassification.FAIL),
    ),
)
def test_missing_pending_blocked_and_failed_evidence_never_pass(
    records: tuple[ReadinessEvidenceRecord, ...], expected: EvidenceClassification
) -> None:
    decision = decide_enterprise_readiness(
        source_commit=COMMIT,
        evidence_records=records,
        required_gates=("gate-a",),
    )
    assert decision.final_decision == expected


def test_stale_and_contradictory_evidence_fail_closed() -> None:
    stale = _record("gate-a", EvidenceClassification.PASS).model_copy(
        update={"source_commit": "b" * 40}
    )
    with pytest.raises(ValueError):
        decide_enterprise_readiness(
            source_commit=COMMIT,
            evidence_records=(stale,),
            required_gates=("gate-a",),
        )
    duplicate = _record("gate-a", EvidenceClassification.PASS)
    with pytest.raises(ValueError):
        decide_enterprise_readiness(
            source_commit=COMMIT,
            evidence_records=(duplicate, duplicate),
            required_gates=("gate-a",),
        )


def test_pass_evidence_cannot_hide_skips_or_nonzero_exit() -> None:
    value = _record("gate-a", EvidenceClassification.PASS).model_dump(mode="json")
    value["skipped_count"] = 1
    value["evidence_fingerprint"] = readiness_evidence_fingerprint(value)
    with pytest.raises(ValueError):
        ReadinessEvidenceRecord.model_validate(value)


def test_artifact_hash_is_verified_from_actual_bytes(tmp_path: Path) -> None:
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    artifact = artifact_root / "actual-report.json"
    artifact.write_bytes(b"{}")
    import hashlib

    value = _record("gate-a", EvidenceClassification.PASS).model_dump(mode="json")
    value["artifacts"] = [
        EvidenceArtifact(
            relative_path="artifacts/actual-report.json",
            byte_length=2,
            content_sha256=hashlib.sha256(b"{}").hexdigest(),
        ).model_dump(mode="json")
    ]
    value["evidence_fingerprint"] = readiness_evidence_fingerprint(value)
    record = ReadinessEvidenceRecord.model_validate(value)
    (tmp_path / "gate-a.json").write_text(record.to_canonical_json(), encoding="utf-8")
    assert load_evidence_records(tmp_path, source_commit=COMMIT) == (record,)
    artifact.write_bytes(b"changed")
    with pytest.raises(ValueError):
        load_evidence_records(tmp_path, source_commit=COMMIT)


def test_artifact_parent_symlink_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "report.json").write_bytes(b"{}")
    linked = tmp_path / "artifacts"
    try:
        linked.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("This platform cannot create test symlinks.")
    import hashlib

    value = _record("gate-a", EvidenceClassification.PASS).model_dump(mode="json")
    value["artifacts"] = [
        {
            "relative_path": "artifacts/report.json",
            "byte_length": 2,
            "content_sha256": hashlib.sha256(b"{}").hexdigest(),
        }
    ]
    value["evidence_fingerprint"] = readiness_evidence_fingerprint(value)
    record = ReadinessEvidenceRecord.model_validate(value)
    (tmp_path / "gate-a.json").write_text(
        record.to_canonical_json(), encoding="utf-8"
    )
    with pytest.raises(ValueError):
        load_evidence_records(tmp_path, source_commit=COMMIT)


def test_enterprise_gate_cli_passes_only_with_complete_actual_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    for gate in REQUIRED_ENTERPRISE_GATES:
        record = _record(gate, EvidenceClassification.PASS)
        (evidence / f"{gate}.json").write_text(
            record.to_canonical_json(), encoding="utf-8"
        )

    class _Result:
        stdout = f"{COMMIT}\n"

    monkeypatch.setattr(
        "cgr.pulsate_api.enterprise_gate.subprocess.run",
        lambda *_args, **_kwargs: _Result(),
    )
    output = tmp_path / "decision.json"
    assert enterprise_gate_main(
        [
            "--source-commit",
            COMMIT,
            "--evidence-root",
            str(evidence),
            "--output",
            str(output),
        ]
    ) == 0
    assert '"final_decision":"PASS"' in output.read_text(encoding="utf-8")


def _runner_arguments(root: Path, *command: str) -> list[str]:
    return [
        "--gate",
        "audit-integrity",
        "--producer",
        "ci.yml:backend-evidence",
        "--source-commit",
        COMMIT,
        "--evidence-root",
        str(root),
        "--output",
        "audit-integrity.json",
        "--runner-identity-class",
        "local",
        "--",
        *command,
    ]


def _prepare_runner_root(tmp_path: Path) -> Path:
    root = tmp_path / "evidence"
    root.mkdir(parents=True)
    (root / "records").mkdir()
    return root


def test_producer_registry_is_the_authoritative_complete_gate_set() -> None:
    gates = tuple(item.gate_identifier for item in ENTERPRISE_GATE_PRODUCERS)
    assert gates == REQUIRED_ENTERPRISE_GATES
    assert len(gates) == 71
    assert len({item.workflow_job for item in ENTERPRISE_GATE_PRODUCERS}) == 7


def test_evidence_runner_records_actual_zero_exit_and_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _prepare_runner_root(tmp_path)
    artifact = root / "artifacts" / "audit-integrity" / "report.json"
    artifact.parent.mkdir(parents=True)
    monkeypatch.setattr(
        "cgr.pulsate_api.enterprise_gate._current_source_commit", lambda: COMMIT
    )
    arguments = _runner_arguments(
        root,
        sys.executable,
        "-c",
        f"from pathlib import Path; Path({str(artifact)!r}).write_text('{{}}', encoding='utf-8')",
    )
    arguments[arguments.index("--") : arguments.index("--")] = [
        "--artifact",
        "artifacts/audit-integrity/report.json",
    ]
    assert evidence_run_main(arguments) == 0
    payload = json.loads((root / "records" / "audit-integrity.json").read_text())
    assert payload["classification"] == "PASS"
    assert payload["exit_status"] == 0
    assert payload["started_at"] <= payload["completed_at"]
    assert payload["artifacts"] == [
        {
            "byte_length": 2,
            "content_sha256": hashlib.sha256(b"{}").hexdigest(),
            "relative_path": "artifacts/audit-integrity/report.json",
        }
    ]


def test_evidence_runner_records_and_returns_actual_nonzero_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _prepare_runner_root(tmp_path)
    monkeypatch.setattr(
        "cgr.pulsate_api.enterprise_gate._current_source_commit", lambda: COMMIT
    )
    assert evidence_run_main(
        _runner_arguments(root, sys.executable, "-c", "raise SystemExit(7)")
    ) == 7
    payload = json.loads((root / "records" / "audit-integrity.json").read_text())
    assert payload["classification"] == "FAIL"
    assert payload["exit_status"] == 7


def test_missing_executable_is_blocked_and_never_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _prepare_runner_root(tmp_path)
    monkeypatch.setattr(
        "cgr.pulsate_api.enterprise_gate._current_source_commit", lambda: COMMIT
    )
    status = evidence_run_main(
        _runner_arguments(root, "definitely-not-an-installed-pulsate-command")
    )
    assert status != 0
    payload = json.loads((root / "records" / "audit-integrity.json").read_text())
    assert payload["classification"] == "BLOCKED"
    assert payload["exit_status"] is None


def test_no_command_or_wrong_producer_cannot_create_pass_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _prepare_runner_root(tmp_path)
    monkeypatch.setattr(
        "cgr.pulsate_api.enterprise_gate._current_source_commit", lambda: COMMIT
    )
    no_command = _runner_arguments(root)
    assert evidence_run_main(no_command) != 0
    wrong_producer = _runner_arguments(root, sys.executable, "-c", "pass")
    wrong_producer[wrong_producer.index("ci.yml:backend-evidence")] = (
        "ci.yml:frontend-evidence"
    )
    assert evidence_run_main(wrong_producer) != 0
    assert not tuple((root / "records").iterdir())


def test_missing_or_malformed_mandatory_artifact_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "cgr.pulsate_api.enterprise_gate._current_source_commit", lambda: COMMIT
    )
    for malformed in (False, True):
        root = _prepare_runner_root(tmp_path / str(malformed))
        artifact = root / "artifacts" / "audit-integrity" / "report.json"
        artifact.parent.mkdir(parents=True)
        command = (
            [sys.executable, "-c", f"from pathlib import Path; Path({str(artifact)!r}).write_text('not-json')"]
            if malformed
            else [sys.executable, "-c", "pass"]
        )
        arguments = _runner_arguments(root, *command)
        arguments[arguments.index("--") : arguments.index("--")] = [
            "--artifact",
            "artifacts/audit-integrity/report.json",
        ]
        assert evidence_run_main(arguments) != 0
        payload = json.loads((root / "records" / "audit-integrity.json").read_text())
        assert payload["classification"] == "FAIL"
        assert payload["exit_status"] == 0


def test_runner_rejects_unknown_duplicate_traversal_and_symlink_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "cgr.pulsate_api.enterprise_gate._current_source_commit", lambda: COMMIT
    )
    root = _prepare_runner_root(tmp_path)
    valid = _runner_arguments(root, sys.executable, "-c", "pass")
    unknown = valid.copy()
    unknown[unknown.index("audit-integrity")] = "unknown-gate"
    assert evidence_run_main(unknown) != 0
    assert evidence_run_main(valid) == 0
    assert evidence_run_main(valid) != 0
    traversal = valid.copy()
    traversal[traversal.index("audit-integrity.json")] = "../escape.json"
    assert evidence_run_main(traversal) != 0
    assert not (tmp_path / "escape.json").exists()

    linked_root = tmp_path / "linked"
    linked_root.mkdir()
    outside = tmp_path / "outside-records"
    outside.mkdir()
    try:
        (linked_root / "records").symlink_to(outside, target_is_directory=True)
    except OSError:
        return
    assert evidence_run_main(
        _runner_arguments(linked_root, sys.executable, "-c", "pass")
    ) != 0
    assert not tuple(outside.iterdir())


def test_runner_record_does_not_copy_command_secrets_or_private_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _prepare_runner_root(tmp_path)
    monkeypatch.setattr(
        "cgr.pulsate_api.enterprise_gate._current_source_commit", lambda: COMMIT
    )
    secret = "test-only-token-value"
    private_path = "/home/private/evidence"
    assert evidence_run_main(
        _runner_arguments(
            root,
            sys.executable,
            "-c",
            "raise SystemExit(3)",
            secret,
            private_path,
        )
    ) == 3
    payload = (root / "records" / "audit-integrity.json").read_text()
    assert secret not in payload
    assert private_path not in payload


def test_every_registered_gate_has_exactly_one_workflow_evidence_producer() -> None:
    root = Path(__file__).resolve().parents[1]
    workflows = {
        "ci.yml": (root / ".github/workflows/ci.yml").read_text(encoding="utf-8"),
        "supply-chain-security.yml": (
            root / ".github/workflows/supply-chain-security.yml"
        ).read_text(encoding="utf-8"),
        "enterprise-readiness.yml": (
            root / ".github/workflows/enterprise-readiness.yml"
        ).read_text(encoding="utf-8"),
    }
    assert len(ENTERPRISE_GATE_PRODUCERS) == len(
        {item.gate_identifier for item in ENTERPRISE_GATE_PRODUCERS}
    )
    for producer in ENTERPRISE_GATE_PRODUCERS:
        workflow_name, job_identifier = producer.workflow_job.split(":", 1)
        workflow = workflows[workflow_name]
        assert f"  {job_identifier}:" in workflow
        assert producer.gate_identifier in workflow
        assert "cgr-pulsate-evidence-run" in workflow
    for workflow in workflows.values():
        assert "continue-on-error" not in workflow
        assert "|| true" not in workflow


def test_final_aggregator_uses_only_same_run_evidence_and_preserves_failure() -> None:
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github/workflows/enterprise-readiness.yml").read_text(
        encoding="utf-8"
    )
    assert "uses: ./.github/workflows/ci.yml" in workflow
    assert "uses: ./.github/workflows/supply-chain-security.yml" in workflow
    assert "pattern: enterprise-evidence-*" in workflow
    assert "if: always()" in workflow
    assert "cgr-pulsate-enterprise-gate" in workflow
    assert "hard-coded PASS" not in workflow
    assert "continue-on-error" not in workflow


def test_container_reports_are_bound_to_the_current_built_image() -> None:
    root = Path(__file__).resolve().parents[1]
    supply = (root / ".github/workflows/supply-chain-security.yml").read_text(
        encoding="utf-8"
    )
    enterprise = (root / ".github/workflows/enterprise-readiness.yml").read_text(
        encoding="utf-8"
    )
    assert '"cgr-pulsate:${GITHUB_SHA}"' in supply
    assert "docker image inspect" in supply
    assert "image-id.txt" in supply
    assert "container-sbom/report.json" in supply
    assert "container-vulnerability-scan/report.json" in supply
    assert "org.opencontainers.image.revision" in enterprise
    assert "image-inspect.json" in enterprise
