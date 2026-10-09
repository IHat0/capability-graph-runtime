"""Bounded, hash-verified handoffs; raw scientific evidence is never rewritten."""
from __future__ import annotations

import hashlib
import json
from collections import Counter

from cgr.science import ArtifactReference

MANIFEST_TYPE = "scientific_evidence_manifest"
RECORD_LIMIT = 4 * 1024 * 1024


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def verified_payload(store, reference):
    payload = store.read(reference)
    if len(payload) != reference.byte_size or hashlib.sha256(payload).hexdigest() != reference.content_sha256:
        raise ValueError("Referenced scientific artifact hash/size mismatch: " + reference.artifact_identifier)
    return payload


def manifest_references(store, manifest):
    """Replay an immutable reference index, including bounded v2 index pages."""
    document = json.loads(verified_payload(store, manifest))
    schema = document.get('schema')
    if schema == 'pulsate.scientific-evidence-manifest/v1':
        rows = document['artifact_references']
    elif schema == 'pulsate.scientific-evidence-manifest/v2':
        rows = []
        pages = document.get('pages', [])
        if not 1 <= len(pages) <= 256:
            raise ValueError('Scientific evidence index has invalid page bounds.')
        for ordinal, value in enumerate(pages):
            reference = ArtifactReference.model_validate(value)
            if reference.artifact_type != 'scientific_evidence_index_page' or reference.byte_size >= RECORD_LIMIT:
                raise ValueError('Scientific evidence index page exceeds its bound.')
            page = json.loads(verified_payload(store, reference))
            if (page.get('schema') != 'pulsate.scientific-evidence-index-page/v1'
                    or page.get('page_ordinal') != ordinal
                    or page.get('execution_identifier') != document['execution_identifier']
                    or page.get('tenant_identifier_sha256') != document['tenant_identifier_sha256']):
                raise ValueError('Scientific evidence index page identity/order mismatch.')
            rows.extend(page['artifact_references'])
    else:
        raise ValueError('Scientific evidence manifest schema is invalid.')
    references = tuple(ArtifactReference.model_validate(row) for row in rows)
    if (document.get('artifact_count', len(references)) != len(references)
            or len({ref.artifact_identifier for ref in references}) != len(references)):
        raise ValueError('Scientific evidence index count/identity mismatch.')
    return document, references


def compact_execution_record(record, store, maximum_bytes=RECORD_LIMIT):
    """Externalize large reference collections, not scientific outputs or endpoints.

    Small records are byte-for-byte unchanged. The immutable manifest retains the
    complete original references and all per-node evidence associations. Required
    inputs/outputs and small evidence classes remain inline for existing adapters.
    """
    legacy = [ref for ref in record.artifact_references
        if ref.artifact_type == MANIFEST_TYPE and ref.byte_size >= maximum_bytes]
    if len(canonical(record.model_dump(mode="json"))) + 1 <= maximum_bytes and not legacy:
        return record
    if store is None:
        raise ValueError("Oversized scientific handoff requires an immutable evidence store.")
    from .phase8_scientific_handlers import _NativeRunner

    if legacy:
        # Representation-only migration of an older large external index. Keep
        # its immutable bytes as historical evidence; never reassemble an answer
        # or invoke a scientific handler while repacking references.
        merged = {ref.artifact_identifier: ref for ref in record.artifact_references}
        evidence = list(record.evidence_artifact_identifiers)
        node_evidence = {node.step_identifier: list(node.evidence_artifact_identifiers) for node in record.node_executions}
        for manifest in legacy:
            document, original = manifest_references(store, manifest)
            for ref in original:
                if ref.artifact_identifier in merged and merged[ref.artifact_identifier] != ref:
                    raise ValueError('Scientific evidence index contains conflicting artifact identities.')
                merged[ref.artifact_identifier] = ref
            evidence.extend(document.get('evidence_artifact_identifiers', []))
            for node in document.get('node_evidence', []):
                if node['step_identifier'] not in node_evidence:
                    raise ValueError('Scientific evidence index has an unknown execution step.')
                node_evidence[node['step_identifier']].extend(node['evidence_artifact_identifiers'])
        record = record.model_copy(update={'artifact_references': tuple(merged.values()),
            'evidence_artifact_identifiers': tuple(dict.fromkeys(evidence)),
            'node_executions': tuple(node.model_copy(update={
                'evidence_artifact_identifiers': tuple(dict.fromkeys(node_evidence[node.step_identifier]))})
                for node in record.node_executions)})

    references = sorted(record.artifact_references, key=lambda item: item.artifact_identifier)
    for reference in references:
        verified_payload(store, reference)
    required = {item.artifact_identifier for item in record.objective.input_references}
    required.update(record.result_artifact_identifiers)
    for node in record.node_executions:
        required.update(node.output_artifact_identifiers)
    counts = Counter(item.artifact_type for item in references)
    retained = [item for item in references if item.artifact_identifier in required
        or (counts[item.artifact_type] <= 64 and item not in legacy)]
    omitted = [item.artifact_identifier for item in references if item not in retained]
    if not omitted:
        raise ValueError("Scientific handoff exceeds its bound without an externalizable evidence collection.")
    runner = _NativeRunner(store)
    page_header = {'schema': 'pulsate.scientific-evidence-index-page/v1',
        'execution_identifier': record.execution_identifier, 'tenant_identifier_sha256': record.tenant_identifier_sha256}
    pages, rows, row_bytes = [], [], 0

    def persist_page():
        if not rows:
            return
        page = dict(page_header, page_ordinal=len(pages), artifact_references=rows)
        payload = canonical(page)
        if len(payload) >= min(maximum_bytes, RECORD_LIMIT):
            raise ValueError('Scientific evidence index page exceeds its unchanged storage bound.')
        pages.append(runner.write_json(artifact_type='scientific_evidence_index_page', payload=payload,
            producer='pulsate-bounded-evidence-handoff', execution_identifier=record.execution_identifier,
            metadata={'artifact_count': len(rows), 'page_ordinal': len(pages),
                'tenant_identifier_sha256': record.tenant_identifier_sha256,
                'evidence_class': 'immutable_provenance_index_page'}))

    for reference in references:
        row = reference.model_dump(mode='json')
        size = len(canonical(row)) + 1
        if rows and row_bytes + size > min(3 * 1024 * 1024, maximum_bytes - 1024):
            persist_page()
            rows, row_bytes = [], 0
        rows.append(row)
        row_bytes += size
    persist_page()
    document = {
        "schema": "pulsate.scientific-evidence-manifest/v2",
        "execution_identifier": record.execution_identifier,
        "tenant_identifier_sha256": record.tenant_identifier_sha256,
        "artifact_count": len(references),
        "pages": [page.model_dump(mode='json') for page in pages],
        "evidence_artifact_identifiers": list(record.evidence_artifact_identifiers),
        "node_evidence": [{"step_identifier": node.step_identifier,
            "evidence_artifact_identifiers": list(node.evidence_artifact_identifiers)} for node in record.node_executions],
        "externalized": {"artifact_references": omitted,
            "policy": "Full references and associations retained here; every original payload was hash/size verified. No raw artifact was modified."},
    }
    manifest_bytes = canonical(document)
    if len(manifest_bytes) >= min(maximum_bytes, RECORD_LIMIT):
        raise ValueError('Scientific evidence root manifest exceeds its unchanged storage bound.')
    manifest = runner.write_json(
        artifact_type=MANIFEST_TYPE, payload=manifest_bytes,
        producer="pulsate-bounded-evidence-handoff", execution_identifier=record.execution_identifier,
        metadata={"artifact_count": len(references), "externalized_reference_count": len(omitted),
            "tenant_identifier_sha256": record.tenant_identifier_sha256,
            "evidence_class": "immutable_provenance_manifest"})
    retained.append(manifest)
    identifiers = {item.artifact_identifier for item in retained}

    def bounded_ids(values):
        selected = [value for value in values if value in identifiers]
        if any(value not in identifiers for value in values):
            selected.append(manifest.artifact_identifier)
        return tuple(dict.fromkeys(selected))

    compact = record.model_copy(update={
        "artifact_references": tuple(retained),
        "evidence_artifact_identifiers": bounded_ids(record.evidence_artifact_identifiers),
        "node_executions": tuple(node.model_copy(update={
            "evidence_artifact_identifiers": bounded_ids(node.evidence_artifact_identifiers)}) for node in record.node_executions),
    })
    if len(canonical(compact.model_dump(mode="json"))) + 1 > maximum_bytes:
        raise ValueError("Scientific evidence manifest did not bring the handoff below its storage bound.")
    return type(record).model_validate(compact.model_dump(mode="json"))


def resolve_reference(references, store, identifier):
    """Resolve only within an owned record's verified manifest; never global lookup."""
    for reference in references:
        if reference.artifact_identifier == identifier:
            verified_payload(store, reference)
            return reference
    for manifest in references:
        if manifest.artifact_type != MANIFEST_TYPE:
            continue
        document, indexed = manifest_references(store, manifest)
        for reference in indexed:
            if reference.artifact_identifier == identifier:
                verified_payload(store, reference)
                return reference
        # Index pages are themselves immutable, hash-verifiable downloads.
        for value in document.get('pages', []):
            reference = ArtifactReference.model_validate(value)
            if reference.artifact_identifier == identifier:
                verified_payload(store, reference)
                return reference
    raise KeyError("Scientific artifact is not referenced by this session.")


def reassemble_saved_handoff(record, references, *, store, assembler, output_reference,
                            verification_reference, expected_output_sha256):
    """Explicit read/reassembly recovery, with no orchestrator or scientific adapter.

    Only a terminal record with one unpersisted penultimate handoff and a pending
    answer is eligible. The operator supplies the reviewed output hash. Original
    record and all associations are retained as immutable recovery evidence.
    """
    from datetime import UTC, datetime, timedelta
    from .functional_exposure import read_summary
    from .phase8_scientific_handlers import _NativeRunner
    from .scientific_executions import ScientificExecutionRecord

    nodes = record.node_executions
    if (record.status != 'failed' or len(nodes) < 2
            or any(node.status != 'succeeded' for node in nodes[:-2])
            or nodes[-2].status != 'running' or nodes[-1].status != 'pending'
            or nodes[-1].capability_name != 'scientist.result_assemble'):
        raise ValueError('Record is not eligible for completed-evidence handoff recovery.')
    if output_reference.content_sha256 != expected_output_sha256:
        raise ValueError('Recovery output differs from the reviewed immutable artifact hash.')
    merged = {ref.artifact_identifier: ref for ref in record.artifact_references}
    for ref in references:
        if ref.artifact_identifier in merged and ref != merged[ref.artifact_identifier]:
            raise ValueError('Recovery artifact identity conflict.')
        merged[ref.artifact_identifier] = ref
        verified_payload(store, ref)
    if output_reference not in merged.values() or verification_reference not in record.artifact_references:
        raise ValueError('Recovery output or original blocking verification is not bound.')
    if verification_reference.artifact_identifier not in nodes[-3].output_artifact_identifiers:
        raise ValueError('Recovery requires the original succeeded blocking verification step.')
    report = json.loads(verified_payload(store, verification_reference))
    if report.get('passed') is not True:
        raise ValueError('Saved blocking verification did not pass.')
    summary = read_summary(verified_payload(store, output_reference), output_reference.artifact_type,
        RECORD_LIMIT, expected_sha256=output_reference.content_sha256,
        expected_byte_size=output_reference.byte_size, artifact_reference=output_reference)
    runner = _NativeRunner(store)
    previous = runner.write_json(artifact_type='scientific_handoff_prior_record',
        payload=canonical(record.model_dump(mode='json')), producer='pulsate-handoff-recovery',
        execution_identifier=record.execution_identifier)
    projection = runner.write_json(artifact_type='scientific_evidence_projection', payload=canonical(summary),
        producer='pulsate-bounded-evidence-projection', execution_identifier=record.execution_identifier,
        parents=(output_reference,), metadata={'source_sha256': output_reference.content_sha256,
            'scope': 'Display/synthesis only; no scientific recomputation.'})
    merged.update({previous.artifact_identifier: previous, projection.artifact_identifier: projection})
    handoff_ids = tuple(ref.artifact_identifier for ref in references
        if ref.artifact_identifier != output_reference.artifact_identifier)
    now = max(datetime.now(UTC), record.updated_at + timedelta(microseconds=1))
    staging = ScientificExecutionRecord.model_validate({**record.model_dump(mode='json'),
        'updated_at': now, 'status': 'running', 'verified': True,
        'artifact_references': list(merged.values()),
        'evidence_artifact_identifiers': list(dict.fromkeys((*record.evidence_artifact_identifiers, *handoff_ids))),
        'node_executions': [*nodes[:-2], nodes[-2].model_copy(update={'status':'succeeded',
            'output_artifact_identifiers': (output_reference.artifact_identifier,),
            'evidence_artifact_identifiers': handoff_ids}), nodes[-1]],
        'verified_scientific_summaries': record.pending_scientific_summaries,
        'pending_scientific_summaries': (),
        'scientist_summary': 'Saved verified scientific evidence recovered by representation-only handoff reassembly.',
    })
    staging = compact_execution_record(staging, store)
    # Deliberately call only the existing evidence-grounded answer assembler. No
    # runtime.execute/resume, capability registry, predictor or solver is reachable.
    outcome = assembler.execute(invocation=None, objective=staging.objective, record=staging)
    if outcome.scientist_result is None or outcome.scientist_result.verification_status != 'passed':
        raise ValueError('Saved evidence did not yield a verified scientist-facing answer.')
    answer_bytes = canonical(outcome.scientist_result.model_dump(mode='json'))
    if len(answer_bytes) > RECORD_LIMIT:
        raise ValueError('Recovered answer exceeds its unchanged synthesis bound.')
    answer = runner.write_json(artifact_type='scientist_answer', payload=answer_bytes,
        producer='pulsate-scientist-result-assembler', execution_identifier=record.execution_identifier,
        parents=(output_reference, projection))
    receipt = runner.write_json(artifact_type='scientific_handoff_recovery_receipt',
        payload=canonical({'schema':'pulsate.scientific-handoff-recovery/v1',
            'execution_identifier':record.execution_identifier, 'prior_record':previous.model_dump(mode='json'),
            'original_workflow_snapshot_fingerprint':record.workflow_snapshot_fingerprint,
            'science_execution_count':0, 'operation':'hash verification, bounded projection and answer reassembly only',
            'output_reference':output_reference.model_dump(mode='json'), 'answer':answer.model_dump(mode='json'),
            'projection':projection.model_dump(mode='json'),
            'history':'Original failed graph remains preserved; recovered step state describes evidence handoff, not a repeated calculation.'}),
        producer='pulsate-handoff-recovery', execution_identifier=record.execution_identifier)
    final = ScientificExecutionRecord.model_validate({**staging.model_dump(mode='json'),
        'status':'succeeded', 'scientist_result':outcome.scientist_result,
        'scientist_summary':outcome.scientific_summary,
        'artifact_references': (*staging.artifact_references, answer, receipt),
        'result_artifact_identifiers': (output_reference.artifact_identifier, answer.artifact_identifier),
        'checkpoint_identifiers': (*staging.checkpoint_identifiers, receipt.artifact_identifier),
        'node_executions': (*staging.node_executions[:-1], nodes[-1].model_copy(update={
            'status':'succeeded', 'attempt_count':1, 'output_artifact_identifiers':(answer.artifact_identifier,)})),
    })
    return compact_execution_record(final, store), receipt
