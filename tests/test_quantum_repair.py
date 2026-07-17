from __future__ import annotations

import copy
import hashlib
import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from cgr.quantum_candidate.contracts import (
    CandidateAdjudicationReceipt,
    CandidateExecutionEvidence,
    CandidateSandboxPolicy,
)
from cgr.quantum_candidate.findings import finding
from cgr.quantum_candidate.protocol import CandidateOutputPackage, source_tree_sha256
from cgr.quantum_preflight.manifests import load_manifest
from cgr.quantum_repair.benchmark import load_repair_benchmark
from cgr.quantum_repair.benchmark_provider import (
    ReviewedBenchmarkRepairProvider,
    materialize_benchmark_source,
)
from cgr.quantum_repair.contracts import (
    AttemptReference,
    QuantumRepairAttempt,
    QuantumRepairPolicy,
    QuantumRepairRunReceipt,
    StructuredEdit,
    sealed_values,
)
from cgr.quantum_repair.directives import (
    assert_directive_sanitized,
    create_directive,
)
from cgr.quantum_repair.events import RepairEventLog, verify_event_log
from cgr.quantum_repair.orchestrator import resume_repair_run, run_repair
from cgr.quantum_repair.patches import (
    RepairPatchRejected,
    create_patch,
    validate_and_apply_patch,
)
from cgr.quantum_repair.persistence import (
    RepairPersistenceError,
    atomic_directory,
    create_source_manifest,
    finalize_directory,
    verify_source_manifest,
    write_evidence,
)
from cgr.quantum_repair.replay import verify_repair_run
from cgr.quantum_repair.providers import (
    RepairProvider,
    RepairProviderError,
    invoke_provider,
)
from cgr.quantum_repair.state import AttemptStateMachine
from cgr.science import ArtifactPointer, sha256_fingerprint

ROOT = Path(__file__).parents[1]
PUBLIC = ROOT / "benchmark-manifests/quantum-preflight/lih-ground-state-v1.json"
REPAIR_MANIFEST = (
    ROOT / "benchmark-manifests/quantum-repair/lih-candidate-repair-benchmark-v1.json"
)
DIAGNOSIS_MANIFEST = (
    ROOT / "benchmark-manifests/quantum-candidate/lih-candidate-benchmark-v1.json"
)
FIXTURES = ROOT / "benchmark-fixtures/quantum-repair-v1"
DIAGNOSIS_SUPPORT = (
    ROOT / "benchmark-fixtures/quantum-candidate-v1/_support/standalone_candidate.py"
)
H = "a" * 64


def _rejected_receipt(code: str) -> CandidateAdjudicationReceipt:
    values: dict[str, Any] = {
        "candidate_identifier": "repair-test",
        "candidate_source_tree_sha256": H,
        "input_experiment_sha256": "b" * 64,
        "candidate_image_identifier": "sha256:" + "c" * 64,
        "candidate_dependency_lock_sha256": "d" * 64,
        "sandbox_policy_sha256": "e" * 64,
        "execution_evidence": ArtifactPointer(
            artifact_identifier="candidate_execution", content_sha256="f" * 64
        ),
        "candidate_output_package_sha256": None,
        "candidate_artifacts": (),
        "recomputed_scientific_result_sha256": None,
        "trusted_reference_receipt_sha256": "1" * 64,
        "findings": (finding(code, "Publicly diagnosable candidate defect."),),
        "primary_failure_code": code,
        "authorized": False,
        "authorization_policy_sha256": "2" * 64,
    }
    provisional = CandidateAdjudicationReceipt.model_construct(
        **values, receipt_content_sha256="0" * 64
    )
    values["receipt_content_sha256"] = sha256_fingerprint(
        provisional.canonical_identity()
    )
    return CandidateAdjudicationReceipt.model_validate(values)


def _receipt_for_execution(
    execution: CandidateExecutionEvidence,
    *,
    experiment_sha256: str,
    trusted_sha256: str,
    dependency_sha256: str,
    code: str | None,
) -> CandidateAdjudicationReceipt:
    findings = () if code is None else (finding(code, "Synthetic boundary finding."),)
    values: dict[str, Any] = {
        "candidate_identifier": execution.candidate_identifier,
        "candidate_source_tree_sha256": execution.source_tree_sha256,
        "input_experiment_sha256": experiment_sha256,
        "candidate_image_identifier": execution.image_identifier,
        "candidate_dependency_lock_sha256": dependency_sha256,
        "sandbox_policy_sha256": execution.sandbox_policy_sha256,
        "execution_evidence": ArtifactPointer(
            artifact_identifier="candidate_execution",
            content_sha256=execution.fingerprint,
        ),
        "candidate_output_package_sha256": sha256_fingerprint([]),
        "candidate_artifacts": (),
        "recomputed_scientific_result_sha256": None if code else "9" * 64,
        "trusted_reference_receipt_sha256": trusted_sha256,
        "findings": findings,
        "primary_failure_code": code,
        "authorized": code is None,
        "authorization_policy_sha256": "2" * 64,
    }
    provisional = CandidateAdjudicationReceipt.model_construct(
        **values, receipt_content_sha256="0" * 64
    )
    values["receipt_content_sha256"] = sha256_fingerprint(
        provisional.canonical_identity()
    )
    return CandidateAdjudicationReceipt.model_validate(values)


def _source(tmp_path: Path, name: str = "source", text: str = "bad\n") -> Path:
    source = tmp_path / name
    source.mkdir()
    (source / "main.py").write_text(text, encoding="utf-8")
    return source


def _directive(
    tmp_path: Path, code: str = "candidate_runtime_error"
) -> tuple[Any, Any]:
    source = _source(tmp_path)
    manifest = create_source_manifest(source, "repair-test")
    directive = create_directive(
        task_identifier="repair-test",
        repair_run_identifier="repair-run-001",
        attempt_identifier="attempt-000",
        attempt_index=0,
        source_manifest=manifest,
        adjudication=_rejected_receipt(code),
        policy=QuantumRepairPolicy(),
        allowed_edit_paths=("main.py",),
    )
    return directive, manifest


def _patch(directive: Any, manifest: Any, edit: StructuredEdit) -> Any:
    return create_patch(
        patch_identifier="patch-000",
        directive=directive,
        source_manifest=manifest,
        provider_identifier="test-provider",
        provider_version="1.0.0",
        provider_type="deterministic",
        edits=(edit,),
        rationale="Correct the diagnosed candidate-owned defect.",
        claimed_addressed_findings=(directive.primary_finding_code,),
    )


def test_repair_manifest_is_separate_complete_and_frozen() -> None:
    benchmark = load_repair_benchmark(REPAIR_MANIFEST)
    assert len(benchmark.cases) == 30
    assert len([case for case in benchmark.cases if case.expected_attempts == 3]) == 3
    assert hashlib.sha256(DIAGNOSIS_MANIFEST.read_bytes()).hexdigest() == (
        benchmark.diagnosis_benchmark_manifest_sha256
    )


@pytest.mark.parametrize(
    "case_identifier",
    [
        case.case_identifier
        for case in load_repair_benchmark(REPAIR_MANIFEST).cases
        if not case.authorized_without_repair
    ],
)
def test_reviewed_provider_repairs_all_declared_defects_without_control_copy(
    tmp_path: Path, case_identifier: str
) -> None:
    benchmark = load_repair_benchmark(REPAIR_MANIFEST)
    case = next(
        item for item in benchmark.cases if item.case_identifier == case_identifier
    )
    experiment = load_manifest(PUBLIC).experiment
    source = tmp_path / "source-000"
    materialize_benchmark_source(
        template_root=FIXTURES / "_template",
        support_root=FIXTURES / "_support",
        diagnosis_support=DIAGNOSIS_SUPPORT,
        destination=source,
        candidate_identifier=case.case_identifier,
        defects=case.initial_defects,
    )
    provider = ReviewedBenchmarkRepairProvider(experiment.model_dump(mode="json"))
    prior_hashes: set[str] = set()
    prior_states: set[str] = set()
    current = source
    for index, code in enumerate(case.expected_findings):
        manifest = create_source_manifest(current, case.case_identifier)
        prior_states.add(manifest.source_manifest_sha256)
        directive = create_directive(
            task_identifier=case.case_identifier,
            repair_run_identifier="repair-run-001",
            attempt_identifier=f"attempt-{index:03d}",
            attempt_index=index,
            source_manifest=manifest,
            adjudication=_rejected_receipt(code),
            policy=QuantumRepairPolicy(),
            allowed_edit_paths=("main.py", "repair-config.json"),
        )
        patch = provider.propose_repair(
            directive=directive, source_root=current, source_manifest=manifest
        )
        assert "valid-control" not in patch.to_canonical_json()
        destination = tmp_path / f"source-{index + 1:03d}"
        _, output = validate_and_apply_patch(
            source_root=current,
            destination_root=destination,
            source_manifest=manifest,
            directive=directive,
            patch=patch,
            policy=QuantumRepairPolicy(),
            prior_patch_hashes=prior_hashes,
            prior_source_hashes=prior_states,
        )
        prior_hashes.add(patch.patch_sha256)
        prior_states.add(output.source_manifest_sha256)
        current = destination
    config = json.loads((current / "repair-config.json").read_text(encoding="utf-8"))
    assert config["candidate_identifier"] == case.case_identifier
    assert provider.invocations == len(case.expected_findings)


def test_valid_control_requires_no_provider_invocation(tmp_path: Path) -> None:
    benchmark = load_repair_benchmark(REPAIR_MANIFEST)
    control = benchmark.cases[0]
    provider = ReviewedBenchmarkRepairProvider(
        load_manifest(PUBLIC).experiment.model_dump(mode="json")
    )
    materialize_benchmark_source(
        template_root=FIXTURES / "_template",
        support_root=FIXTURES / "_support",
        diagnosis_support=DIAGNOSIS_SUPPORT,
        destination=tmp_path / "control",
        candidate_identifier=control.case_identifier,
        defects=control.initial_defects,
    )
    assert control.expected_findings == ()
    assert provider.invocations == 0


def test_directive_is_canonical_bounded_and_sanitized(tmp_path: Path) -> None:
    directive, _ = _directive(tmp_path, "candidate_structure_mismatch")
    assert directive.directive_sha256 == directive.fingerprint
    assert directive.remaining_attempt_budget == 2
    assert directive.disposition == "repairable"
    assert directive.allowed_edit_paths == ("main.py",)
    assert "trusted_exact_energy" in directive.deliberately_withheld
    assert_directive_sanitized(directive)


def test_directive_allows_public_manifest_values_but_rejects_trusted_answer(
    tmp_path: Path,
) -> None:
    directive, _ = _directive(tmp_path)
    values = directive.model_dump(mode="python", exclude={"directive_sha256"})
    values["sanitized_explanations"] = ("Use public bond distance 1.6.",)
    public_directive = type(directive).model_validate(
        sealed_values(values, "directive_sha256")
    )
    assert_directive_sanitized(public_directive)
    values["sanitized_explanations"] = ("trusted exact energy -7.862128",)
    leaking = type(directive).model_validate(sealed_values(values, "directive_sha256"))
    with pytest.raises(ValueError, match="leakage"):
        assert_directive_sanitized(leaking)


def test_unknown_and_tampered_directive_schemas_fail(tmp_path: Path) -> None:
    directive, _ = _directive(tmp_path)
    payload = directive.model_dump(mode="json")
    payload["schema_version"] = "legacy/0"
    with pytest.raises(ValidationError):
        type(directive).model_validate(payload)
    payload = directive.model_dump(mode="json")
    payload["attempt_number"] = 1
    with pytest.raises(ValidationError, match="recomputed"):
        type(directive).model_validate(payload)


def test_valid_structured_patch_applies_to_fresh_workspace(tmp_path: Path) -> None:
    directive, manifest = _directive(tmp_path)
    source = tmp_path / "source"
    patch = _patch(
        directive,
        manifest,
        StructuredEdit(relative_path="main.py", old_text="bad", new_text="good"),
    )
    validation, output = validate_and_apply_patch(
        source_root=source,
        destination_root=tmp_path / "repaired",
        source_manifest=manifest,
        directive=directive,
        patch=patch,
        policy=QuantumRepairPolicy(),
    )
    assert validation.validated
    assert validation.source_provenance == "fresh-copy-plus-structured-edits"
    assert validation.control_source_match is False
    assert validation.candidate_identifier_retained is True
    assert 0.0 <= validation.unchanged_file_ratio <= 1.0
    assert output.source_manifest_sha256 != manifest.source_manifest_sha256
    assert (source / "main.py").read_text(encoding="utf-8") == "bad\n"


@pytest.mark.parametrize(
    ("relative_path", "new_text", "code"),
    [
        ("other.py", "good", "path_out_of_scope"),
        ("main.py", "good\x00", "binary_patch"),
        ("main.py", 'main("valid-control")', "valid_control_shortcut"),
        ("main.py", "requests.get('https://example.test')", "prohibited_capability"),
    ],
)
def test_malicious_patch_content_fails_closed(
    tmp_path: Path, relative_path: str, new_text: str, code: str
) -> None:
    directive, manifest = _directive(tmp_path)
    patch = _patch(
        directive,
        manifest,
        StructuredEdit(relative_path=relative_path, old_text="bad", new_text=new_text),
    )
    with pytest.raises(RepairPatchRejected) as error:
        validate_and_apply_patch(
            source_root=tmp_path / "source",
            destination_root=tmp_path / "repaired",
            source_manifest=manifest,
            directive=directive,
            patch=patch,
            policy=QuantumRepairPolicy(),
        )
    assert error.value.code == code


def test_noop_traversal_and_forged_patch_hashes_fail(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        StructuredEdit(relative_path="main.py", old_text="same", new_text="same")
    with pytest.raises(ValidationError):
        StructuredEdit(relative_path="../main.py", old_text="bad", new_text="good")
    directive, manifest = _directive(tmp_path)
    patch = _patch(
        directive,
        manifest,
        StructuredEdit(relative_path="main.py", old_text="bad", new_text="good"),
    )
    payload = patch.model_dump(mode="json")
    payload["patch_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="recomputed"):
        type(patch).model_validate(payload)


@pytest.mark.parametrize(
    ("guard", "code"),
    [
        ("patch", "repeated_patch"),
        ("source", "repair_oscillation"),
        ("control", "valid_control_copy"),
    ],
)
def test_repeat_oscillation_and_control_copy_guards(
    tmp_path: Path, guard: str, code: str
) -> None:
    directive, manifest = _directive(tmp_path)
    patch = _patch(
        directive,
        manifest,
        StructuredEdit(relative_path="main.py", old_text="bad", new_text="good"),
    )
    _, output = validate_and_apply_patch(
        source_root=tmp_path / "source",
        destination_root=tmp_path / "first",
        source_manifest=manifest,
        directive=directive,
        patch=patch,
        policy=QuantumRepairPolicy(),
    )
    kwargs: dict[str, Any] = {}
    if guard == "patch":
        kwargs["prior_patch_hashes"] = {patch.patch_sha256}
    elif guard == "source":
        kwargs["prior_source_hashes"] = {output.source_manifest_sha256}
    else:
        kwargs["prohibited_source_hashes"] = {output.source_manifest_sha256}
    with pytest.raises(RepairPatchRejected) as error:
        validate_and_apply_patch(
            source_root=tmp_path / "source",
            destination_root=tmp_path / "second",
            source_manifest=manifest,
            directive=directive,
            patch=patch,
            policy=QuantumRepairPolicy(),
            **kwargs,
        )
    assert error.value.code == code


def test_stale_base_and_wrong_finding_fail(tmp_path: Path) -> None:
    directive, manifest = _directive(tmp_path)
    other = _source(tmp_path, "other", "different\n")
    stale_manifest = create_source_manifest(other, "repair-test")
    stale = _patch(
        directive,
        stale_manifest,
        StructuredEdit(relative_path="main.py", old_text="bad", new_text="good"),
    )
    with pytest.raises(RepairPatchRejected, match="stale_base_source"):
        validate_and_apply_patch(
            source_root=tmp_path / "source",
            destination_root=tmp_path / "stale",
            source_manifest=manifest,
            directive=directive,
            patch=stale,
            policy=QuantumRepairPolicy(),
        )
    values = stale.model_dump(mode="python", exclude={"patch_sha256"})
    values["base_source_manifest_sha256"] = manifest.source_manifest_sha256
    values["claimed_addressed_findings"] = ("candidate_import_error",)
    wrong = type(stale).model_validate(sealed_values(values, "patch_sha256"))
    with pytest.raises(RepairPatchRejected, match="wrong_finding"):
        validate_and_apply_patch(
            source_root=tmp_path / "source",
            destination_root=tmp_path / "wrong",
            source_manifest=manifest,
            directive=directive,
            patch=wrong,
            policy=QuantumRepairPolicy(),
        )


def test_source_manifest_rejects_symlink_and_detects_substitution(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)
    manifest = create_source_manifest(source, "repair-test")
    (source / "main.py").write_text("substituted\n", encoding="utf-8")
    with pytest.raises(RepairPersistenceError, match="differs"):
        verify_source_manifest(source, manifest)
    try:
        (source / "escape.py").symlink_to(tmp_path / "outside.py")
    except OSError:
        pytest.skip("This Windows account cannot create symbolic links.")
    with pytest.raises(RepairPersistenceError, match="Symbolic"):
        create_source_manifest(source, "repair-test")


def test_state_machine_atomic_finalization_and_partial_recovery(tmp_path: Path) -> None:
    temporary, final = atomic_directory(tmp_path / "attempts", "attempt-000")
    state = AttemptStateMachine(temporary / "state.json", "attempt-000")
    state.transition("source_snapshotted")
    with pytest.raises(ValueError, match="Illegal"):
        state.transition("authorized")
    finalize_directory(temporary, final)
    assert (final / "state.json").is_file()
    partial, _ = atomic_directory(tmp_path / "run" / "attempts", "attempt-001")
    write_evidence(partial / "state.json", {"status": "created"})
    recovery = resume_repair_run(tmp_path / "run")
    assert recovery["safe_to_resume"] is False
    assert recovery["corrupted_partial_attempts"] == 1


def test_event_log_is_ordered_and_tamper_evident(tmp_path: Path) -> None:
    log = RepairEventLog(tmp_path / "events.jsonl", "repair-run-001")
    log.append("repair_run_started", "created")
    log.append("attempt_started", "created", attempt_identifier="attempt-000")
    assert len(verify_event_log(tmp_path / "events.jsonl", "repair-run-001")) == 2
    lines = (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    payload = json.loads(lines[1])
    payload["event_sequence"] = 8
    lines[1] = json.dumps(payload)
    (tmp_path / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="ordering"):
        verify_event_log(tmp_path / "events.jsonl", "repair-run-001")


def test_provider_protocol_exception_and_timeout(tmp_path: Path) -> None:
    directive, manifest = _directive(tmp_path)
    reviewed = ReviewedBenchmarkRepairProvider(
        load_manifest(PUBLIC).experiment.model_dump(mode="json")
    )
    assert isinstance(reviewed, RepairProvider)

    class Broken:
        capability = reviewed.capability

        def propose_repair(self, **_: Any) -> Any:
            raise RuntimeError("secret provider detail")

    with pytest.raises(RepairProviderError, match="RuntimeError"):
        invoke_provider(
            Broken(),
            directive=directive,
            source_root=tmp_path / "source",
            source_manifest=manifest,
            timeout_seconds=1,
        )

    class Slow:
        capability = reviewed.capability

        def propose_repair(self, **_: Any) -> Any:
            time.sleep(1.2)
            raise AssertionError

    with pytest.raises(RepairProviderError, match="timeout"):
        invoke_provider(
            Slow(),
            directive=directive,
            source_root=tmp_path / "source",
            source_manifest=manifest,
            timeout_seconds=1,
        )


def test_attempt_and_run_receipt_hashes_and_fail_closed_authorization() -> None:
    attempt_values: dict[str, Any] = {
        "repair_run_identifier": "repair-run-001",
        "attempt_identifier": "attempt-000",
        "attempt_index": 0,
        "parent_attempt_identifier": None,
        "input_source_manifest_sha256": H,
        "directive_sha256": None,
        "patch_sha256": None,
        "output_source_manifest_sha256": H,
        "candidate_execution_sha256": "b" * 64,
        "adjudication_receipt_sha256": "c" * 64,
        "authorized": True,
        "findings_before": (),
        "findings_after": (),
        "status": "authorized",
        "failure_reason": None,
        "elapsed_seconds": 1.0,
    }
    attempt = QuantumRepairAttempt.model_validate(
        sealed_values(attempt_values, "attempt_content_sha256")
    )
    reference = AttemptReference(
        attempt_identifier=attempt.attempt_identifier,
        attempt_index=0,
        attempt_content_sha256=attempt.attempt_content_sha256,
        source_manifest_sha256=H,
        adjudication_receipt_sha256="c" * 64,
        authorized=True,
    )
    receipt_values: dict[str, Any] = {
        "repair_run_identifier": "repair-run-001",
        "public_experiment_sha256": "d" * 64,
        "original_source_manifest_sha256": H,
        "trusted_reference_receipt_sha256": "e" * 64,
        "provider_capability_sha256": "f" * 64,
        "policy_sha256": "1" * 64,
        "attempts": (reference,),
        "attempt_cap": 3,
        "total_budget_seconds": 600,
        "terminal_status": "authorized",
        "final_source_manifest_sha256": H,
        "final_adjudication_receipt_sha256": "c" * 64,
        "final_scientific_outcome_sha256": "2" * 64,
        "authorized": True,
    }
    receipt = QuantumRepairRunReceipt.model_validate(
        sealed_values(receipt_values, "repair_run_content_sha256")
    )
    assert attempt.attempt_content_sha256 == attempt.fingerprint
    assert receipt.repair_run_content_sha256 == receipt.fingerprint
    legacy = copy.deepcopy(receipt_values)
    legacy["attempts"] = ()
    legacy["authorized"] = False
    legacy["terminal_status"] = "controller_failure"
    legacy["final_scientific_outcome_sha256"] = None
    with pytest.raises(ValidationError, match="adjudicated attempt"):
        QuantumRepairRunReceipt.model_validate(
            sealed_values(legacy, "repair_run_content_sha256")
        )


def test_orchestrator_reexecutes_before_authorization_and_replay_detects_cross_link(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path, text='raise RuntimeError("bad")\n')
    manifest = load_manifest(PUBLIC)
    trusted_sha256 = "3" * 64
    trusted = SimpleNamespace(receipt_content_sha256=trusted_sha256)
    provider = ReviewedBenchmarkRepairProvider(
        manifest.experiment.model_dump(mode="json")
    )
    calls = 0

    def execute(
        **kwargs: Any,
    ) -> tuple[CandidateExecutionEvidence, CandidateOutputPackage]:
        policy: CandidateSandboxPolicy = kwargs["policy"]
        candidate_directory: Path = kwargs["candidate_directory"]
        evidence_directory: Path = kwargs["evidence_directory"]
        evidence_directory.mkdir()
        (evidence_directory / "output").mkdir()
        execution = CandidateExecutionEvidence(
            candidate_identifier=kwargs["candidate_identifier"],
            source_tree_sha256=source_tree_sha256(candidate_directory),
            input_manifest_sha256=kwargs["input_manifest_sha256"],
            image_identifier=kwargs["image_identifier"],
            sandbox_policy_sha256=policy.fingerprint,
            mount_manifest=policy.mounts,
            execution_category="completed",
            exit_code=0,
            timed_out=False,
            elapsed_seconds=0.01,
            stdout_sha256=hashlib.sha256(b"").hexdigest(),
            stderr_sha256=hashlib.sha256(b"").hexdigest(),
            stdout_bytes=0,
            stderr_bytes=0,
            output_bytes=0,
            output_files=0,
            network_disabled=True,
            trusted_evidence_exposed=False,
        )
        write_evidence(evidence_directory / "execution.json", execution)
        package = CandidateOutputPackage(
            files=(),
            package_sha256=sha256_fingerprint([]),
            total_bytes=0,
            findings=(),
        )
        return execution, package

    def adjudicate(**kwargs: Any) -> CandidateAdjudicationReceipt:
        nonlocal calls
        code = "candidate_runtime_error" if calls == 0 else None
        calls += 1
        return _receipt_for_execution(
            kwargs["execution"],
            experiment_sha256=manifest.experiment.fingerprint,
            trusted_sha256=trusted_sha256,
            dependency_sha256=kwargs["candidate_dependency_lock_sha256"],
            code=code,
        )

    result = run_repair(
        task_identifier="repair-test",
        candidate_source=source,
        public_manifest=manifest,
        trusted=trusted,
        result_root=tmp_path / "results",
        candidate_image_identifier="sha256:" + "c" * 64,
        candidate_lock_path=ROOT / "requirements/quantum-preflight.lock",
        provider=provider,
        allowed_edit_paths=("main.py",),
        execute=execute,
        adjudicate=adjudicate,
    )
    run_directory = Path(result["repair_run_directory"])
    assert result["authorized"] is True
    assert result["attempts"] == 2
    assert provider.invocations == 1
    assert verify_repair_run(run_directory)["replay_verified"] is True
    execution_path = (
        run_directory / "attempts/attempt-001/candidate-execution/execution.json"
    )
    payload = json.loads(execution_path.read_text(encoding="utf-8"))
    payload["source_tree_sha256"] = "0" * 64
    write_evidence(execution_path, payload)
    with pytest.raises(ValueError, match="cross-linked"):
        verify_repair_run(run_directory)
