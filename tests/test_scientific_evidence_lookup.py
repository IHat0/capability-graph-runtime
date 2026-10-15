"""Owned-reference recovery contracts; synthetic evidence, no scientific engines."""
import hashlib
import json

import pytest

from cgr.pulsate_api.phase8_scientific_handlers import _NativeRunner
from cgr.pulsate_api.scientific_evidence import (
    EvidenceReferenceLookup, canonical, resolve_reference,
    recover_reference_handoff,
)
from cgr.pulsate_api.scientific_executions import ScientificNodeExecutionRecord, ScientistFacingResult
from cgr.pulsate_api.scientific_runtime import ScientificCapabilityOutcome
from test_scientific_evidence import record_fixture
from test_scientific_runtime import PayloadStore


@pytest.fixture
def evidence():
    store = PayloadStore()
    runner = _NativeRunner(store)
    raw = runner.write_json(artifact_type="synthetic_source", payload=b'{"value":7}',
        producer="synthetic", execution_identifier="synthetic-execution")

    def manifest(rows=(raw,), **changes):
        document = {"schema": "pulsate.scientific-evidence-manifest/v1",
            "execution_identifier": "synthetic-execution",
            "tenant_identifier_sha256": "a" * 64,
            "artifact_count": len(rows),
            "artifact_references": [ref.model_dump(mode="json") for ref in rows]}
        document.update(changes)
        return runner.write_json(artifact_type="scientific_evidence_manifest",
            payload=canonical(document), producer="synthetic-index",
            execution_identifier="synthetic-execution")

    return store, runner, raw, manifest


def test_direct_reference_does_not_require_or_read_an_unrelated_manifest(evidence):
    store, _, raw, manifest = evidence
    index = manifest()
    del store.payloads[index.artifact_identifier]
    assert resolve_reference((raw, index), store, raw.artifact_identifier) == raw
    assert resolve_reference((raw,), store, raw.artifact_identifier) == raw


def test_external_manifest_and_exact_inline_mirror_resolve(evidence):
    store, _, raw, manifest = evidence
    index = manifest()
    lookup = EvidenceReferenceLookup((index,), store,
        execution_identifier="synthetic-execution", tenant_identifier_sha256="a" * 64)
    assert lookup[raw.artifact_identifier] == raw
    assert lookup.manifests == (index,)
    assert lookup[raw.artifact_identifier] == raw
    mirror = EvidenceReferenceLookup((raw, index), store)
    assert dict(mirror) == {raw.artifact_identifier: raw, index.artifact_identifier: index}


@pytest.mark.parametrize("defect", ["missing_manifest", "manifest_hash", "manifest_size",
    "missing_artifact", "artifact_hash", "artifact_size", "unknown_reference"])
def test_missing_or_mismatched_evidence_refuses_without_global_fallback(evidence, defect):
    store, _, raw, manifest = evidence
    index = manifest()
    identifier = raw.artifact_identifier
    if defect == "missing_manifest":
        del store.payloads[index.artifact_identifier]
    elif defect == "manifest_hash":
        index = index.model_copy(update={"content_sha256": "0" * 64})
    elif defect == "manifest_size":
        index = index.model_copy(update={"byte_size": index.byte_size + 1})
    elif defect == "missing_artifact":
        del store.payloads[raw.artifact_identifier]
    elif defect == "artifact_hash":
        # Same size, different bytes must still fail.
        store.payloads[raw.artifact_identifier] = b'{"value":8}'
    elif defect == "artifact_size":
        index = manifest((raw.model_copy(update={"byte_size": raw.byte_size + 1}),))
    else:
        identifier = "unowned-artifact"
    with pytest.raises((ValueError, KeyError)):
        resolve_reference((index,), store, identifier)


@pytest.mark.parametrize("defect", ["direct_duplicate", "index_duplicate", "conflicting_mirror",
    "overlapping_indexes", "wrong_execution", "wrong_tenant"])
def test_ambiguous_and_wrong_owner_indexes_refuse(evidence, defect):
    store, _, raw, manifest = evidence
    index = manifest()
    refs = (index,)
    if defect == "direct_duplicate":
        refs = (raw, raw)
    elif defect == "index_duplicate":
        refs = (manifest((raw, raw)),)
    elif defect == "conflicting_mirror":
        refs = (raw, manifest((raw.model_copy(update={"media_type": "text/plain"}),)))
    elif defect == "overlapping_indexes":
        refs = (index, manifest(description="a different declared index with the same member"))
    elif defect == "wrong_execution":
        refs = (manifest(execution_identifier="other-execution"),)
    else:
        refs = (manifest(tenant_identifier_sha256="b" * 64),)
    with pytest.raises(ValueError):
        lookup = EvidenceReferenceLookup(refs, store, execution_identifier="synthetic-execution",
            tenant_identifier_sha256="a" * 64)
        dict(lookup)


def test_v2_page_provenance_and_hash_are_not_bypassed(evidence):
    store, runner, raw, _ = evidence
    page = runner.write_json(artifact_type="scientific_evidence_index_page",
        payload=canonical({"schema": "pulsate.scientific-evidence-index-page/v1",
            "execution_identifier": "synthetic-execution", "tenant_identifier_sha256": "a" * 64,
            "page_ordinal": 0, "artifact_references": [raw.model_dump(mode="json")]}),
        producer="synthetic-index", execution_identifier="synthetic-execution")
    index = runner.write_json(artifact_type="scientific_evidence_manifest",
        payload=canonical({"schema": "pulsate.scientific-evidence-manifest/v2",
            "execution_identifier": "synthetic-execution", "tenant_identifier_sha256": "a" * 64,
            "artifact_count": 1, "pages": [page.model_dump(mode="json")]}),
        producer="synthetic-index", execution_identifier="synthetic-execution")
    assert resolve_reference((index,), store, raw.artifact_identifier) == raw
    assert resolve_reference((index,), store, page.artifact_identifier) == page
    store.payloads[page.artifact_identifier] = b"corrupt page"
    with pytest.raises(ValueError, match="hash/size mismatch"):
        resolve_reference((index,), store, raw.artifact_identifier)


def test_index_is_read_once_but_payload_integrity_is_checked_on_every_use(evidence):
    store, _, raw, manifest = evidence
    index = manifest()
    reads = []
    original = store.read

    def read(ref):
        reads.append(ref.artifact_identifier)
        return original(ref)

    store.read = read
    lookup = EvidenceReferenceLookup((index,), store)
    assert lookup[raw.artifact_identifier] == raw
    assert lookup[raw.artifact_identifier] == raw
    assert reads.count(index.artifact_identifier) == 1
    assert reads.count(raw.artifact_identifier) == 2
    store.payloads[raw.artifact_identifier] = b'{"value":8}'
    with pytest.raises(ValueError, match="hash/size mismatch"):
        lookup[raw.artifact_identifier]


@pytest.mark.parametrize("defect", [None, "wrong_record_hash", "science_pending", "corrupt_source", "unverified", "prior_check_failed"])
def test_recovery_only_calls_remaining_evidence_stages_preserving_prior_nodes(tmp_path, monkeypatch, defect):
    _, original = record_fixture(tmp_path)
    store = PayloadStore()
    runner = _NativeRunner(store)
    raw = runner.write_json(artifact_type="synthetic_source", payload=b'{"endpoint":7.25}',
        producer="synthetic", execution_identifier=original.execution_identifier)
    index = runner.write_json(artifact_type="scientific_evidence_manifest",
        payload=canonical({"schema": "pulsate.scientific-evidence-manifest/v1",
            "execution_identifier": original.execution_identifier,
            "artifact_references": [raw.model_dump(mode="json")]}),
        producer="synthetic-index", execution_identifier=original.execution_identifier)
    prior_check = runner.write_json(artifact_type="scientific_verification_report",
        payload=canonical({"overall_outcome": "failed" if defect == "prior_check_failed" else "passed"}),
        producer="synthetic-verifier", execution_identifier=original.execution_identifier)
    stages = (("scientific_verification.candidate_ranking", "succeeded"),
        ("discovery.virtual_investigation_verify", "failed"),
        ("synthetic.other_completed_branch", "succeeded"),
        ("discovery.prospective_assess", "pending"), ("scientist.result_assemble", "pending"))
    nodes = tuple(ScientificNodeExecutionRecord(step_identifier=f"step-{i}", capability_name=name,
        status=status, attempt_count=0 if status == "pending" else 1,
        error_code="virtual_investigation_verification_failed" if status == "failed" else None,
        error_message="Missing external reference" if status == "failed" else None,
        output_artifact_identifiers=(prior_check.artifact_identifier,) if i == 0 else ())
        for i, (name, status) in enumerate(stages))
    record = original.model_copy(update={"status": "failed", "artifact_references": (index, prior_check),
        "node_executions": nodes})
    calls = []

    def forbidden(*args, **kwargs):
        raise AssertionError("Scientific execution forbidden during reference recovery")

    monkeypatch.setattr("cgr.pulsate_api.scientific_runtime.ScientificObjectiveRuntime.execute", forbidden)
    monkeypatch.setattr("cgr.pulsate_api.virtual_investigation.VirtualInvestigationHandler.execute", forbidden)

    class Stage:
        def __init__(self, name):
            self.name = name

        def execute(self, *, invocation, objective, record):
            calls.append(self.name)
            assert store.read(raw) == b'{"endpoint":7.25}'
            output = runner.write_json(artifact_type=self.name, payload=b'{"passed":true}',
                producer="synthetic", execution_identifier=invocation.invocation_identifier)
            result = None
            if self.name == "answer":
                result = ScientistFacingResult(original_request=objective.original_request,
                    resolved_interpretation="Synthetic recovery test", structures_and_entities=(),
                    methods=(), assumptions=(), scientific_result="Synthetic test answer from saved evidence.",
                    verification_status="passed", uncertainty=(), replanning_history=(), important_limitations=(),
                    evidence_artifact_identifiers=(raw.artifact_identifier,), scene_identifiers=())
            return ScientificCapabilityOutcome(output_artifacts=(output,), scientist_result=result,
                verified=False if defect == "unverified" else True)

    if defect == "science_pending":
        record = record.model_copy(update={"node_executions": (nodes[0].model_copy(update={"status": "pending"}), *nodes[1:])})
    if defect == "corrupt_source":
        store.payloads[raw.artifact_identifier] = b"corrupt"
    expected = hashlib.sha256(canonical(record.model_dump(mode="json"))).hexdigest()
    if defect == "wrong_record_hash":
        expected = "0" * 64
    kwargs = dict(store=store, verifier=Stage("verify"), assessor=Stage("assess"),
        assembler=Stage("answer"), expected_record_sha256=expected)
    if defect is not None:
        with pytest.raises(ValueError):
            recover_reference_handoff(record, **kwargs)
        assert calls == (["verify"] if defect == "unverified" else [])
        return
    recovered, receipt = recover_reference_handoff(record, **kwargs)
    assert calls == ["verify", "assess", "answer"]
    assert recovered.status == "succeeded" and recovered.verified
    assert recovered.objective == record.objective and recovered.canonical_graph == record.canonical_graph
    assert recovered.node_executions[0] == nodes[0] and recovered.node_executions[2] == nodes[2]
    assert store.read(raw) == b'{"endpoint":7.25}'
    assert json.loads(store.read(receipt))["scientific_execution_stages_repeated"] == []
    with pytest.raises(ValueError, match="eligible"):
        recover_reference_handoff(recovered, **kwargs)
