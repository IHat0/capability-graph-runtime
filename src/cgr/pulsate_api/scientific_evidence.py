"""Bounded, hash-verified handoffs; raw scientific evidence is never rewritten."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping

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


class EvidenceReferenceLookup(Mapping):
    """Owned direct references plus a verified, lazily expanded provenance index.

    No global store lookup is permitted. Manifest/page hashes are checked before
    their references are admitted; each requested payload is hash/size checked.
    Retained inline references may also occur in their own index, but must agree
    exactly. Duplicate declarations or overlapping external indexes are refused.
    """

    def __init__(self, references, store, *, execution_identifier=None,
                 tenant_identifier_sha256=None):
        self.store = store
        self.execution_identifier = execution_identifier
        self.tenant_identifier_sha256 = tenant_identifier_sha256
        self._references = {}
        for reference in references:
            identifier = reference.artifact_identifier
            if identifier in self._references:
                raise ValueError("Duplicate scientific artifact reference: " + identifier)
            self._references[identifier] = reference
        self.manifests = tuple(ref for ref in self._references.values()
            if ref.artifact_type == MANIFEST_TYPE)
        self._expanded = False

    def _expand(self):
        if self._expanded:
            return
        # Build transactionally: a corrupt or ambiguous index must not leave a
        # partially usable cache behind after a failed lookup.
        merged = dict(self._references)
        indexed_identifiers = set()
        for manifest in self.manifests:
            document, indexed = manifest_references(self.store, manifest)
            for field, expected in (("execution_identifier", self.execution_identifier),
                    ("tenant_identifier_sha256", self.tenant_identifier_sha256)):
                if expected is not None and document.get(field) != expected:
                    raise ValueError("Scientific evidence manifest ownership mismatch: " + field)
            pages = tuple(ArtifactReference.model_validate(value)
                for value in document.get("pages", []))
            for reference in (*indexed, *pages):
                identifier = reference.artifact_identifier
                if identifier in indexed_identifiers:
                    raise ValueError("Ambiguous scientific evidence index reference: " + identifier)
                indexed_identifiers.add(identifier)
                if identifier in merged and merged[identifier] != reference:
                    raise ValueError("Conflicting scientific artifact reference: " + identifier)
                merged[identifier] = reference
        self._references = merged
        self._expanded = True

    def __getitem__(self, identifier):
        if identifier not in self._references:
            self._expand()
        try:
            reference = self._references[identifier]
        except KeyError:
            raise KeyError("Scientific artifact is not referenced by this session: " + identifier) from None
        verified_payload(self.store, reference)
        return reference

    def __iter__(self):
        self._expand()
        return iter(self._references)

    def __len__(self):
        self._expand()
        return len(self._references)

    def values(self):
        # Type discovery enumerates verified index metadata, not raw payloads.
        # Payload consumers still use __getitem__/verified_payload (or the
        # immutable store's checked read); do not reread thousands of unrelated
        # sources each time a handler locates a single report by artifact type.
        self._expand()
        return self._references.values()


def resolve_reference(references, store, identifier):
    """Resolve only within an owned record's verified manifest; never global lookup."""
    return EvidenceReferenceLookup(references, store)[identifier]


def recover_reference_handoff(record, *, store, verifier, assessor, assembler,
                              expected_record_sha256):
    """One-shot recovery of completed evidence, without the scientific runtime.

    Only the failed reference-verification stage and its two pending consumers
    may execute. Previously succeeded nodes, objectives, graph and raw evidence
    remain unchanged. The caller persists the result with optimistic concurrency.
    """
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace
    from .phase8_scientific_handlers import _NativeRunner
    from .scientific_executions import ScientificExecutionRecord

    stages = ("discovery.virtual_investigation_verify", "discovery.prospective_assess",
        "scientist.result_assemble")
    remaining = tuple(node for node in record.node_executions if node.status != "succeeded")
    if (record.status != "failed" or record.scientist_result is not None
            or tuple(node.capability_name for node in remaining) != stages
            or remaining[0].status != "failed"
            or remaining[0].error_code != "virtual_investigation_verification_failed"
            or any(node.status != "pending" for node in remaining[1:])):
        raise ValueError("Record is not eligible for evidence-reference recovery.")
    original = canonical(record.model_dump(mode="json"))
    if hashlib.sha256(original).hexdigest() != expected_record_sha256:
        raise ValueError("Recovery record differs from the reviewed failed snapshot.")
    lookup = EvidenceReferenceLookup(record.artifact_references, store,
        execution_identifier=record.execution_identifier,
        tenant_identifier_sha256=record.tenant_identifier_sha256)
    # Validate every original owned artifact before any recovery output is saved.
    for identifier in lookup:
        lookup[identifier]
    # A terminal failure clears the aggregate verified flag. Restore only the
    # already-passed intended-screening prerequisite from its owned receipt;
    # the remaining investigation verifier still has to pass independently.
    prerequisites = [node for node in record.node_executions
        if node.capability_name == "scientific_verification.candidate_ranking" and node.status == "succeeded"]
    if len(prerequisites) != 1:
        raise ValueError("Recovery lacks the original succeeded screening verification.")
    checks = [lookup[identifier] for identifier in prerequisites[0].output_artifact_identifiers
        if lookup[identifier].artifact_type == "scientific_verification_report"]
    if len(checks) != 1:
        raise ValueError("Recovery screening-verification receipt is missing or ambiguous.")
    previous_verification = checks[0]
    check = json.loads(verified_payload(store, previous_verification))
    if check.get("overall_outcome") != "passed":
        raise ValueError("Original screening verification did not pass.")
    runner = _NativeRunner(store)
    prior = runner.write_json(artifact_type="scientific_handoff_prior_record",
        payload=original, producer="pulsate-reference-handoff-recovery",
        execution_identifier=record.execution_identifier)
    now = max(datetime.now(UTC), record.updated_at + timedelta(microseconds=1))
    staging = ScientificExecutionRecord.model_validate({**record.model_dump(mode="json"),
        "status": "running", "updated_at": now, "verified": True,
        "artifact_references": (*record.artifact_references, prior)})
    recovered_outputs = []
    for node, handler in zip(remaining, (verifier, assessor, assembler), strict=True):
        invocation = SimpleNamespace(invocation_identifier="reference-recovery-" +
            hashlib.sha256((record.execution_identifier + node.step_identifier + expected_record_sha256).encode()).hexdigest()[:32])
        outcome = handler.execute(invocation=invocation, objective=staging.objective, record=staging)
        if outcome.verified is not True:
            raise ValueError("Recovery stage did not verify its saved evidence: " + node.capability_name)
        refs = {ref.artifact_identifier: ref for ref in staging.artifact_references}
        for ref in (*outcome.output_artifacts, *outcome.evidence_artifacts):
            verified_payload(store, ref)
            if ref.artifact_identifier in refs and refs[ref.artifact_identifier] != ref:
                raise ValueError("Recovery artifact identity conflict.")
            refs[ref.artifact_identifier] = ref
        outputs = tuple(ref.artifact_identifier for ref in outcome.output_artifacts)
        result = outcome.scientist_result
        if node.capability_name == stages[-1]:
            if result is None or result.verification_status != "passed":
                raise ValueError("Recovery did not produce a verified scientist-facing answer.")
            answer_bytes = canonical(result.model_dump(mode="json"))
            if len(answer_bytes) > RECORD_LIMIT:
                raise ValueError("Recovered answer exceeds its unchanged synthesis bound.")
            answer = runner.write_json(artifact_type="scientist_answer", payload=answer_bytes,
                producer="pulsate-scientist-result-assembler", execution_identifier=invocation.invocation_identifier,
                parents=tuple(refs[identifier] for identifier in recovered_outputs))
            refs[answer.artifact_identifier] = answer
            outputs = (answer.artifact_identifier,)
        recovered_outputs.extend(outputs)
        updated_node = node.model_copy(update={"status": "succeeded", "error_code": None,
            "error_message": None, "attempt_count": node.attempt_count + 1,
            "output_artifact_identifiers": outputs,
            "evidence_artifact_identifiers": tuple(ref.artifact_identifier for ref in outcome.evidence_artifacts)})
        staging = ScientificExecutionRecord.model_validate({**staging.model_dump(mode="json"),
            "artifact_references": tuple(refs.values()), "verified": True,
            "pending_scientific_summaries": (),
            "verified_scientific_summaries": tuple(dict.fromkeys((*staging.verified_scientific_summaries,
                *staging.pending_scientific_summaries,
                *((outcome.scientific_summary,) if outcome.scientific_summary else ())))),
            "scientist_summary": outcome.scientific_summary or staging.scientist_summary,
            "scientist_result": result or staging.scientist_result,
            "evidence_artifact_identifiers": tuple(dict.fromkeys((*staging.evidence_artifact_identifiers,
                *(ref.artifact_identifier for ref in outcome.evidence_artifacts)))),
            "limitations": tuple(dict.fromkeys((*staging.limitations, *outcome.limitations))),
            "node_executions": tuple(updated_node if item.step_identifier == node.step_identifier else item
                for item in staging.node_executions)})
        staging = compact_execution_record(staging, store)
    receipt = runner.write_json(artifact_type="scientific_handoff_recovery_receipt",
        payload=canonical({"schema": "pulsate.evidence-reference-recovery/v1",
            "prior_record": prior.model_dump(mode="json"),
            "restored_prerequisite_verification": previous_verification.model_dump(mode="json"),
            "original_workflow_snapshot_fingerprint": record.workflow_snapshot_fingerprint,
            "recovered_stages": list(stages), "recovered_output_artifact_identifiers": recovered_outputs,
            "scientific_execution_stages_repeated": [], "new_scientific_sessions": 0,
            "scope": "Existing evidence verification, frozen assessment and answer assembly only. Original failed workflow snapshot retained."}),
        producer="pulsate-reference-handoff-recovery", execution_identifier=record.execution_identifier,
        parents=(prior, previous_verification, *lookup.manifests))
    final = ScientificExecutionRecord.model_validate({**staging.model_dump(mode="json"),
        "status": "succeeded", "result_artifact_identifiers": tuple(recovered_outputs[1:]),
        "checkpoint_identifiers": (*staging.checkpoint_identifiers, receipt.artifact_identifier),
        "artifact_references": (*staging.artifact_references, receipt)})
    return compact_execution_record(final, store), receipt


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
