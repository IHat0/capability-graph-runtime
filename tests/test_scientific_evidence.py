"""Representation-only tests. No scientific predictor, solver or native engine."""
import hashlib
import json
from datetime import timedelta

import pytest

from cgr.pulsate_api.scientific_evidence import (
    RECORD_LIMIT, canonical, compact_execution_record, resolve_reference,
    reassemble_saved_handoff, manifest_references,
)
from cgr.pulsate_api.scientific_executions import (
    ScientificExecutionRepository, ScientificObjectiveCompileRequest,
    ScientificNodeExecutionRecord, ScientistFacingResult,
)
from cgr.pulsate_api.phase8_scientific_handlers import _NativeRunner
from cgr.pulsate_api.scientific_production import DurableScientificPayloadStore
from cgr.pulsate_api.scientific_runtime import ScientificCapabilityOutcome
from test_scientific_runtime import PayloadStore


def record_fixture(tmp_path):
    repository = ScientificExecutionRepository(tmp_path / 'executions')
    repository.start()
    return repository, repository.create(ScientificObjectiveCompileRequest(question='Compare conformers in solution.'))


def test_3_point_9_mib_record_is_allowed_without_externalization(tmp_path):
    repository, record = record_fixture(tmp_path)
    small = record.model_copy(update={'scientist_summary':'x' * int(3.9 * 1024 * 1024),
        'updated_at':record.updated_at + timedelta(seconds=1)})
    assert len(canonical(small.model_dump(mode='json'))) < RECORD_LIMIT
    assert compact_execution_record(small, None) is small
    repository.replace(small, expected_updated_at=record.updated_at)
    assert repository.get(record.execution_identifier) == small


def test_large_reference_handoff_is_deterministic_external_and_exact(tmp_path):
    _, record = record_fixture(tmp_path)
    store = PayloadStore()
    runner = _NativeRunner(store)
    refs = tuple(runner.write_json(artifact_type='source_evidence', payload=b'{}',
        producer='synthetic', execution_identifier=record.execution_identifier,
        metadata={'fixture':i, 'note':'x' * 1000}) for i in range(3000))
    original = record.model_copy(update={'artifact_references':refs,
        'evidence_artifact_identifiers':tuple(r.artifact_identifier for r in refs)})
    assert len(canonical(original.model_dump(mode='json'))) > RECORD_LIMIT
    before = dict(store.payloads)
    compact = compact_execution_record(original, store)
    repeated = compact_execution_record(original, store)
    assert compact == repeated
    assert len(canonical(compact.model_dump(mode='json'))) < RECORD_LIMIT
    manifest = compact.artifact_references[0]
    document, indexed = manifest_references(store, manifest)
    assert document['artifact_count'] == 3000 and len(document['externalized']['artifact_references']) == 3000
    assert indexed == tuple(sorted(refs,key=lambda r:r.artifact_identifier))
    assert manifest.byte_size < RECORD_LIMIT
    assert all(page['byte_size'] < RECORD_LIMIT for page in document['pages'])
    assert all(store.payloads[k] == v for k,v in before.items())
    assert resolve_reference(compact.artifact_references, store, refs[5].artifact_identifier) == refs[5]
    # Repack an older oversized external index without changing its bytes or
    # invoking answer synthesis/scientific execution again.
    legacy_bytes = canonical({**document, 'schema':'pulsate.scientific-evidence-manifest/v1',
        'artifact_references':[r.model_dump(mode='json') for r in indexed]})
    old_index = runner.write_json(artifact_type='scientific_evidence_manifest',payload=legacy_bytes,
        producer='synthetic-legacy',execution_identifier=record.execution_identifier)
    assert old_index.byte_size > RECORD_LIMIT
    repacked = compact_execution_record(record.model_copy(update={'artifact_references':(old_index,)}),store)
    assert repacked.objective == record.objective and repacked.status == record.status
    assert all(r.byte_size < RECORD_LIMIT for r in repacked.artifact_references if r.artifact_type=='scientific_evidence_manifest')
    assert resolve_reference(repacked.artifact_references,store,old_index.artifact_identifier)==old_index
    assert store.read(old_index)==legacy_bytes
    store.payloads[refs[5].artifact_identifier] = b'corrupt'
    with pytest.raises(ValueError, match='hash/size mismatch'):
        resolve_reference(compact.artifact_references, store, refs[5].artifact_identifier)
    # A corrupt index cannot be bypassed by a convenient global artifact lookup.
    store.payloads[document['pages'][0]['artifact_identifier']] = b'corrupt index'
    with pytest.raises(ValueError, match='hash/size mismatch'):
        resolve_reference(compact.artifact_references, store, refs[-1].artifact_identifier)


def test_manifest_raw_evidence_resolves_after_durable_reopening_and_missing_refuses(tmp_path):
    store = DurableScientificPayloadStore(tmp_path / 'artifacts'); store.start()
    runner = _NativeRunner(store)
    raw = runner.write_bytes(artifact_type='raw_scientific_evidence', media_type='application/octet-stream', payload=b'x' * (RECORD_LIMIT + 1),
        producer='synthetic', execution_identifier='synthetic-execution')
    manifest = runner.write_json(artifact_type='scientific_evidence_manifest', payload=canonical({
        'schema':'pulsate.scientific-evidence-manifest/v1', 'artifact_references':[raw.model_dump(mode='json')]}),
        producer='synthetic', execution_identifier='synthetic-execution')
    store.close(); reopened = DurableScientificPayloadStore(tmp_path / 'artifacts'); reopened.start()
    assert resolve_reference((manifest,), reopened, raw.artifact_identifier) == raw
    assert hashlib.sha256(reopened.read(raw)).hexdigest() == raw.content_sha256
    with pytest.raises(KeyError): resolve_reference((manifest,), reopened, 'missing-reference')
    # A missing physical file is a controlled refusal, not fabricated evidence.
    for path in (tmp_path / 'artifacts').rglob('payload.bin'):
        if path.read_bytes().startswith(b'x'):
            path.unlink()
    with pytest.raises((KeyError, RuntimeError, ValueError)):
        resolve_reference((manifest,), reopened, raw.artifact_identifier)


def test_read_only_reassembly_never_calls_scientific_engines(tmp_path, monkeypatch):
    _, record = record_fixture(tmp_path)
    store = PayloadStore(); runner = _NativeRunner(store)
    check = runner.write_json(artifact_type='scientific_verification_report', payload=b'{"passed":true}',
        producer='synthetic-verifier', execution_identifier=record.execution_identifier)
    raw = runner.write_json(artifact_type='virtual_organism_assessment', payload=b'{"functional_models":[]}',
        producer='synthetic-completed-engine', execution_identifier=record.execution_identifier)
    nodes = tuple(ScientificNodeExecutionRecord(step_identifier=f'step-{i}', capability_name=name,
        status=status, attempt_count=0 if status=='pending' else 1,
        output_artifact_identifiers=(check.artifact_identifier,) if i==0 else ())
        for i,(name,status) in enumerate((('evidence.verify','succeeded'),('evidence.assess','running'),('scientist.result_assemble','pending'))))
    failed = record.model_copy(update={'status':'failed','artifact_references':(check,), 'node_executions':nodes})
    def forbidden(*args, **kwargs): raise AssertionError('Scientific execution must never occur during reassembly')
    monkeypatch.setattr('cgr.pulsate_api.functional_exposure.investigate', forbidden)
    monkeypatch.setattr('cgr.pulsate_api.scientific_runtime.ScientificObjectiveRuntime.execute', forbidden)
    class Assembler:
        def execute(self, **kwargs):
            result = ScientistFacingResult(original_request='synthetic fixture',resolved_interpretation='synthetic fixture',
                structures_and_entities=(),methods=(),assumptions=(),scientific_result='Synthetic verified answer.',
                verification_status='passed',uncertainty=(),replanning_history=(),important_limitations=(),
                evidence_artifact_identifiers=(raw.artifact_identifier,),scene_identifiers=())
            return ScientificCapabilityOutcome(scientist_result=result, scientific_summary=result.scientific_result)
    recovered, receipt = reassemble_saved_handoff(failed,(raw,),store=store,assembler=Assembler(),
        output_reference=raw,verification_reference=check,expected_output_sha256=raw.content_sha256)
    assert recovered.status=='succeeded' and recovered.verified
    assert json.loads(store.read(receipt))['science_execution_count'] == 0
    assert store.read(raw)==b'{"functional_models":[]}'
    with pytest.raises(ValueError,match='eligible'):
        reassemble_saved_handoff(recovered,(raw,),store=store,assembler=Assembler(),
            output_reference=raw,verification_reference=check,expected_output_sha256=raw.content_sha256)
