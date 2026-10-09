"""Operator-approved, representation-only recovery of already-computed evidence.

Never calls a scientific capability or resumes a workflow. Requires an explicit
reviewed output hash; preserves the original failed session as an immutable
artifact and updates the same durable session with optimistic concurrency.
"""
from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cgr.science import ArtifactReference
from cgr.pulsate_api.natural_language import OpenAICompatibleModelProvider
from cgr.pulsate_api.phase8_scientific_handlers import _NativeRunner
from cgr.pulsate_api.research_sessions import ResearchSessionRepository, ResearchSession
from cgr.pulsate_api.scientific_evidence import canonical, verified_payload, reassemble_saved_handoff, compact_execution_record
from cgr.pulsate_api.scientific_executions import ScientificExecutionRepository
from cgr.pulsate_api.scientific_production import DurableScientificPayloadStore
from cgr.pulsate_api.scientific_runtime import ScientistResultAssembler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', required=True, type=Path)
    parser.add_argument('--session', required=True)
    parser.add_argument('--output-artifact')
    parser.add_argument('--reviewed-output-sha256')
    parser.add_argument('--apply-reassembly', action='store_true')
    parser.add_argument('--repack-index-only', action='store_true',
        help='Repack an existing completed record reference index; never synthesize or compute again.')
    args = parser.parse_args()
    root = args.runtime.resolve(strict=True)
    sessions = ResearchSessionRepository(root / 'sessions'); sessions.start()
    executions = ScientificExecutionRepository(root / 'executions'); executions.start()
    store = DurableScientificPayloadStore(root / 'scientific-artifacts'); store.start()
    session = sessions.get(args.session)
    if args.repack_index_only:
        if args.apply_reassembly or session.status != 'completed' or session.compilation is None:
            raise ValueError('Index-only repacking requires an already-completed session, not scientific reassembly.')
        record = executions.get(session.compilation.execution_identifier)
        if record.status != 'succeeded' or record.scientist_result.verification_status != 'passed':
            raise ValueError('Index-only repacking requires the existing verified final answer.')
        compact = compact_execution_record(record, store)
        if compact == record:
            print(json.dumps({'operation':'index already bounded; no change','science_execution_count':0}))
            return
        runner = _NativeRunner(store)
        prior = runner.write_json(artifact_type='scientific_handoff_prior_record',
            payload=canonical(record.model_dump(mode='json')),producer='pulsate-index-repack',
            execution_identifier=record.execution_identifier)
        receipt = runner.write_json(artifact_type='scientific_index_repack_receipt',
            payload=canonical({'schema':'pulsate.scientific-index-repack/v1',
                'science_execution_count':0,'answer_synthesis_count':0,
                'prior_record':prior.model_dump(mode='json'),
                'answer_unchanged':True,'operation':'Immutable reference index pagination only.'}),
            producer='pulsate-index-repack',execution_identifier=record.execution_identifier)
        compact = compact.model_copy(update={'artifact_references':(*compact.artifact_references,prior,receipt),
            'checkpoint_identifiers':(*compact.checkpoint_identifiers,receipt.artifact_identifier),
            'updated_at':max(datetime.now(UTC),record.updated_at+timedelta(microseconds=1))})
        assert compact.scientist_result == record.scientist_result == session.scientist_result
        assert compact.result_artifact_identifiers == record.result_artifact_identifiers
        assert [(n.status,n.attempt_count) for n in compact.node_executions] == [(n.status,n.attempt_count) for n in record.node_executions]
        compact = compact_execution_record(compact,store)
        executions.replace(compact,expected_updated_at=record.updated_at)
        restored = session.model_copy(update={'execution_steps':compact.node_executions,
            'revision':session.revision+1,'updated_at':max(datetime.now(UTC),session.updated_at+timedelta(microseconds=1))})
        sessions.replace(restored,expected_revision=session.revision)
        print(json.dumps({'operation':'index-only repack','session':args.session,
            'status':restored.status,'science_execution_count':0,'answer_synthesis_count':0,
            'record_bytes':len(canonical(compact.model_dump(mode='json')))+1,
            'manifest_bytes':[r.byte_size for r in compact.artifact_references if r.artifact_type=='scientific_evidence_manifest']}))
        return
    if not args.output_artifact or not args.reviewed_output_sha256:
        parser.error('Evidence reassembly requires --output-artifact and --reviewed-output-sha256.')
    if session.status != 'failed' or session.compilation is None:
        raise ValueError('Only the reviewed terminal failed session may be recovered; no rerun is allowed.')
    record = executions.get(session.compilation.execution_identifier)
    references = {}
    for path in (root / 'scientific-artifacts' / 'objects').rglob('reference.json'):
        reference = ArtifactReference.model_validate_json(path.read_bytes())
        references[reference.artifact_identifier] = reference
    output = references[args.output_artifact]
    if output.content_sha256 != args.reviewed_output_sha256:
        raise ValueError('Operator-reviewed output hash does not match saved evidence.')
    # Resolve the actual provenance closure, not arbitrary unrelated artifacts.
    needed = set(item.artifact_identifier for item in record.artifact_references)
    queue = [output.artifact_identifier, *needed]
    while queue:
        identifier = queue.pop()
        reference = references[identifier]
        needed.add(identifier)
        for parent in reference.parents:
            linked = references[parent.artifact_identifier]
            if linked.content_sha256 != parent.content_sha256:
                raise ValueError('Recovery provenance parent hash mismatch.')
            if parent.artifact_identifier not in needed:
                needed.add(parent.artifact_identifier); queue.append(parent.artifact_identifier)
    bound = tuple(references[key] for key in sorted(needed))
    for reference in bound: verified_payload(store, reference)
    verification = next(ref for ref in record.artifact_references if ref.artifact_type=='scientific_verification_report')
    print(json.dumps({'operation':'read/reassembly only','session':args.session,
        'existing_artifact_hashes_and_sizes_verified':len(bound), 'science_execution_count':0,
        'reviewed_output_sha256':output.content_sha256, 'apply':args.apply_reassembly}), flush=True)
    if not args.apply_reassembly:
        return
    provider = OpenAICompatibleModelProvider.from_environment()
    if provider is None:
        raise ValueError('The existing real answer-synthesis model must be configured; no engineering-written answer is substituted.')
    runner = _NativeRunner(store)
    prior_session = runner.write_json(artifact_type='scientific_handoff_prior_session',
        payload=canonical(session.model_dump(mode='json')), producer='pulsate-handoff-recovery',
        execution_identifier=record.execution_identifier)
    recovered, receipt = reassemble_saved_handoff(record,(*bound, prior_session),store=store,
        assembler=ScientistResultAssembler(store,provider), output_reference=output,
        verification_reference=verification,expected_output_sha256=args.reviewed_output_sha256)
    recovered = executions.replace(recovered,expected_updated_at=record.updated_at)
    restored = ResearchSession.model_validate({**session.model_dump(mode='json'),
        'status':'completed','execution_status':'succeeded', 'execution_steps':recovered.node_executions,
        'scientist_result':recovered.scientist_result,'scientist_summary':recovered.scientist_summary,
        'scene_identifier':recovered.scene_identifier,'revision':session.revision+1,
        'updated_at':max(datetime.now(UTC),session.updated_at+timedelta(microseconds=1))})
    sessions.replace(restored,expected_revision=session.revision)
    reopened = sessions.get(args.session)
    assert reopened.scientist_result == recovered.scientist_result
    print(json.dumps({'session':args.session,'status':reopened.status,
        'execution_record_bytes':len(canonical(recovered.model_dump(mode='json')))+1,
        'final_answer_bytes':len(canonical(recovered.scientist_result.model_dump(mode='json'))),
        'original_artifacts_verified':len(bound),'prior_session_sha256':prior_session.content_sha256,
        'recovery_receipt':receipt.model_dump(mode='json'),'science_execution_count':0}), flush=True)


if __name__ == '__main__': main()
