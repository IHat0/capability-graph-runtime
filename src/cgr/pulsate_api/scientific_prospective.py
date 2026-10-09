"""Prospective assessment of verified screening evidence, never retrospective narrative."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, datetime
from urllib.parse import urlparse

from .phase8_scientific_handlers import _NativeRunner
from .scientific_acquisition import _fetch_bytes
from .scientific_runtime import ScientificCapabilityFailure, ScientificCapabilityOutcome


def property_hypotheses(properties):
    """General oral-small-molecule heuristics; not toxicity or exposure predictions."""
    rules = (
        ('molecular_weight', 500, 'Higher molecular weight can complicate oral exposure.'),
        ('logp', 5, 'High predicted lipophilicity raises solubility and nonspecific-binding questions.'),
        ('hbond_donors', 5, 'Many hydrogen-bond donors can complicate passive permeability.'),
        ('hbond_acceptors', 10, 'Many hydrogen-bond acceptors can complicate passive permeability.'),
    )
    return [{'metric': metric, 'computed_value': properties[metric], 'screen_threshold': threshold,
             'hypothesis': text, 'classification': 'derived_hypothesis',
             'limitation': 'Rule-of-five-style screening heuristic, not a measured liability or clinical conclusion.'}
            for metric, threshold, text in rules if properties[metric] > threshold]


def validate_screening_pair(candidate, descriptor, docking):
    """Never allow an assessment to mix candidates, nonfinite data or invented scores."""
    identifier = candidate['candidate_identifier']
    if descriptor.get('candidate_identifier') != identifier or docking.get('candidate_identifier') != identifier:
        raise ValueError('Candidate evidence identity mismatch.')
    required = ('molecular_weight', 'logp', 'qed', 'hbond_donors', 'hbond_acceptors', 'rotatable_bonds', 'formal_charge')
    properties = descriptor.get('descriptors', {})
    if not all(type(properties.get(k)) in (int, float) and math.isfinite(properties[k]) for k in required):
        raise ValueError('Candidate descriptors are missing or nonfinite.')
    scores = docking.get('vina_scores_kcal_per_mol', [])
    if (not scores or not all(type(v) in (int, float) and math.isfinite(v) for v in scores)
            or docking.get('score_semantics') != 'autodock_vina_score_not_binding_free_energy'):
        raise ValueError('Native docking evidence is invalid.')
    traced = {s['objective_identifier']: s['value'] for s in candidate['properties_and_calculations']}
    if not math.isclose(traced.get('vina_pose_score', math.nan), scores[0], abs_tol=1e-10):
        raise ValueError('Docking score disagrees with the verified trace.')
    if not candidate.get('verification') or not descriptor.get('conformer_generated'):
        raise ValueError('Candidate verification or conformer evidence is missing.')
    return properties


class ProspectiveAssessmentHandler:
    def __init__(self, store):
        self.runner = _NativeRunner(store)

    def execute(self, *, invocation, objective, record):
        from rdkit import Chem, rdBase

        store = self.runner.store
        references = {r.artifact_identifier: r for r in record.artifact_references}
        def typed(kind):
            return next(r for r in references.values() if r.artifact_type == kind)
        trace_ref, pocket_ref, verifier_ref, off_target_ref = (typed(k) for k in
            ('discovery_design_loop_trace', 'binding_pocket', 'scientific_verification_report', 'prospective_off_target_screen'))
        off_targets = json.loads(store.read(off_target_ref))
        off_verifier_ref = typed('prospective_off_target_verification')
        off_verifier = json.loads(store.read(off_verifier_ref))
        if off_verifier.get('passed') is not True or off_verifier.get('screening_report_sha256') != off_target_ref.content_sha256:
            raise ScientificCapabilityFailure('prospective_off_target_invalid', 'Alternative-target assessment requires independent receipt verification.')
        trace, pocket = (json.loads(store.read(r)) for r in (trace_ref, pocket_ref))
        if not record.verified or not trace.get('loop_complete'):
            raise ScientificCapabilityFailure('prospective_unverified', 'Prospective assessment requires blocking screening verification.')
        source_audit, assessment_candidates, parents = [], [], [trace_ref, pocket_ref, verifier_ref, off_target_ref, off_verifier_ref]
        investigation_refs = [r for r in references.values() if r.artifact_type == 'virtual_investigation']
        investigation = None
        if investigation_refs:
            if len(investigation_refs) != 1:
                raise ScientificCapabilityFailure('virtual_investigation_ambiguous', 'Multiple virtual investigations cannot be mixed.')
            investigation_ref = investigation_refs[0]
            check_ref = typed('virtual_investigation_verification')
            check = json.loads(store.read(check_ref))
            if check.get('passed') is not True or check.get('report_sha256') != investigation_ref.content_sha256:
                raise ScientificCapabilityFailure('virtual_investigation_unverified', 'Virtual investigation requires independent verification.')
            investigation = json.loads(store.read(investigation_ref))
            for identifier,sha in investigation['artifact_sha256'].items():
                r = references[identifier]
                if r.content_sha256 != sha or hashlib.sha256(store.read(r)).hexdigest() != sha:
                    raise ScientificCapabilityFailure('virtual_investigation_corrupt', 'Virtual investigation evidence hash mismatch.')
            if [c['candidate_identifier'] for c in investigation['candidates']] != [c['candidate_identifier'] for c in trace['candidates']]:
                raise ScientificCapabilityFailure('virtual_investigation_coverage', 'Virtual investigation must retain all candidates.')
            parents.extend((investigation_ref, check_ref))
            # This optional research assessment is performed only after the
            # final blocking investigation receipt. Never mutate its source.
            from .virtual_investigation import load_policy
            config, _, config_sha = load_policy()
            if config and config.get('functional_concern_criteria'):
                from .functional_concern import assess
                import copy
                if config_sha != investigation['policy_sha256']:
                    raise ScientificCapabilityFailure('functional_criteria_changed', 'Predeclared functional criteria changed after scientific execution.')
                primary_sources = {r.content_sha256: store.read(r) for r in references.values()
                    if r.artifact_type == 'virtual_investigation_source'}
                investigation = copy.deepcopy(investigation)
                for expanded in investigation['candidates']:
                    decision = assess(expanded, config['functional_concern_criteria'], primary_sources, verification_passed=True)
                    expanded['functional_research_assessment'] = decision
                    if decision['status'] == 'CONCERN':
                        expanded.update(candidate_status='CONCERN', assessment_reason=decision['reason'])
        if (off_targets.get('verification', {}).get('passed') is not True or
                sorted(c['candidate_identifier'] for c in off_targets['candidates']) !=
                sorted(c['candidate_identifier'] for c in trace['candidates'])):
            raise ScientificCapabilityFailure('prospective_off_target_invalid', 'Alternative-target report must account for every verified candidate.')
        source_audit.extend(off_targets.get('source_audit', []))
        for r in references.values():
            url = r.metadata.get('acquisition_source_url')
            if url:
                parsed = urlparse(str(url))
                allowed = (parsed.scheme == 'https' and (
                    parsed.hostname == 'files.rcsb.org' and parsed.path.startswith('/download/') or
                    parsed.hostname == 'pubchem.ncbi.nlm.nih.gov' and parsed.path.startswith('/rest/pug/compound/')))
                if not allowed:
                    raise ScientificCapabilityFailure('prospective_source_disallowed', 'A scientific input is outside the foundational source policy.')
                source_audit.append({'url': url, 'record': r.metadata.get('acquisition_source_identifier'),
                    'class': 'molecular identity/structure' if parsed.hostname.startswith('pubchem') else 'experimental protein coordinates',
                    'artifact_identifier': r.artifact_identifier, 'sha256': r.content_sha256})
        for candidate in trace['candidates']:
            evidence_refs = [references[p['artifact_identifier']] for e in candidate['evidence'] for p in e['artifacts']]
            descriptor_ref = next(r for r in evidence_refs if r.artifact_type == 'molecular_candidate_evaluation')
            docking_ref = next(r for r in evidence_refs if r.artifact_type == 'molecular_candidate_docking_evaluation')
            descriptor, docking = (json.loads(store.read(r)) for r in (descriptor_ref, docking_ref))
            try:
                properties = validate_screening_pair(candidate, descriptor, docking)
            except ValueError as error:
                raise ScientificCapabilityFailure('prospective_evidence_invalid', str(error)) from error
            molecule = Chem.MolFromSmiles(descriptor['canonical_smiles'])
            if molecule is None:
                raise ScientificCapabilityFailure('prospective_identity_invalid', 'Candidate graph cannot be reconstructed.')
            identity = {'standard_inchikey': Chem.MolToInchiKey(molecule), 'method': 'RDKit standard InChI from candidate graph',
                        'public_identity_checked': False}
            for source in candidate.get('source_inputs', []):
                r = references[source['artifact_identifier']]
                if r.content_sha256 != source['content_sha256']:
                    raise ScientificCapabilityFailure('prospective_lineage_invalid', 'Candidate source hash mismatch.')
                cid = str(r.metadata.get('acquisition_source_identifier', ''))
                if r.metadata.get('acquisition_source_kind', '').startswith('pubchem') and cid.isdigit():
                    url = f'https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/{cid}/property/InChIKey/JSON'
                    payload = _fetch_bytes(url, 'application/json', 1024 * 1024)
                    rows = json.loads(payload).get('PropertyTable', {}).get('Properties', [])
                    if len(rows) != 1 or rows[0].get('CID') != int(cid) or rows[0].get('InChIKey') != identity['standard_inchikey']:
                        raise ScientificCapabilityFailure('prospective_identity_mismatch', 'Reconstructed candidate identity disagrees with the foundational public identifier.')
                    identity.update(public_identity_checked=True, pubchem_cid=int(cid))
                    source_audit.append({'url': url, 'class': 'identity-only standard InChIKey', 'sha256': hashlib.sha256(payload).hexdigest()})
                parents.append(r)
            parents.extend((descriptor_ref, docking_ref))
            hypotheses = property_hypotheses(properties)
            alternative = next(c for c in off_targets['candidates'] if c['candidate_identifier'] == candidate['candidate_identifier'])
            assessment_candidates.append({'candidate_identifier': candidate['candidate_identifier'],
                'name': candidate['display_name'], 'identity': identity,
                'properties': properties, 'property_method': 'RDKit graph descriptors', 'rdkit_version': rdBase.rdkitVersion,
                'generated_conformer': {k: descriptor.get(k) for k in ('force_field', 'conformer_energy_kcal_per_mol', 'optimization_converged', 'random_seed')},
                'intended_target': {'conclusion': 'screening_pose_supported_not_functional_inhibition',
                    'best_vina_score_kcal_per_mol': docking['vina_scores_kcal_per_mol'][0],
                    'pose_scores_kcal_per_mol': docking['vina_scores_kcal_per_mol'],
                    'limitation': 'A docking pose and score do not establish affinity, intended mechanism, activity, efficacy or safety.'},
                'hypotheses': hypotheses, 'unsupported_risk_dimensions': [
                    'Measured solubility, exposure, metabolism and pharmacokinetics',
                    'Validated toxicity or organ-level safety predictions',
                    'Calibrated alternative-target selectivity and experimentally confirmed perturbation (bounded structural screening is not a safety model)',
                    'Functional intended-target inhibition and efficacy',
                    'Receptor/conformer/protonation ensembles and higher-fidelity binding free energies'],
                'recommendation': 'insufficient_evidence',
                'reason': 'Intended-target docking and descriptors alone cannot establish candidate-level viability. ' +
                    ('Computed property flags warrant targeted follow-up, not a clinical conclusion.' if hypotheses else
                     'Absence of descriptor flags is not evidence of safety.'),
                'alternative_targets': alternative,
                'orthogonal_follow_up': [
                    'A target-specific functional or binding assay is needed for each supported interaction hypothesis; docking does not determine inhibition.',
                    'Receptor/protonation ensembles and an independently justified free-energy method are needed before treating raw cross-target score differences as selectivity.',
                    'No electronic difficulty was established by docking or descriptors; neither triggers quantum computation.'],
                'evidence_artifact_identifiers': [descriptor_ref.artifact_identifier, docking_ref.artifact_identifier, off_target_ref.artifact_identifier]})
            if investigation:
                expanded = next(c for c in investigation['candidates'] if c['candidate_identifier'] == candidate['candidate_identifier'])
                assessment_candidates[-1].update(recommendation=expanded['candidate_status'].lower().replace(' ', '_'),
                    reason=expanded['assessment_reason'], computational_decision_scope=expanded['candidate_assessment']['scope'])
        selection = pocket.get('target_selection_evidence') or {}
        source_audit.extend(dict(s, **{'class': 'foundational identity/sequence/structure metadata'}) for s in selection.get('sources', []))
        document = {'schema': 'pulsate.prospective-assessment/v1', 'created_at': datetime.now(UTC).isoformat(),
            'execution_identifier': record.execution_identifier, 'original_request': objective.original_request,
            'candidates': assessment_candidates, 'target_selection': selection,
            'virtual_investigation': investigation,
            'assumptions': selection.get('assumptions', []), 'source_audit': source_audit,
            'source_policy': selection.get('source_policy', {}),
            'blinding_limitations': [('Projected foundational and general bioactivity/annotation records plus policy-qualified reviewed numerical inputs only. '
                if investigation else 'Foundational retrieval only. ') +
                'Pretrained model weights are not provably outcome-blind; model-generated scientific claims are disallowed.'],
            'verification_scope': 'Computational integrity and lineage only; not biological or clinical correctness.',
            'computation_selection': {'selected_compute': 'classical', 'quantum_selected': False, 'ibm_job_submitted': False,
                'reason': 'Identity, force-field preparation, graph descriptors and rigid-receptor docking are supported classical tasks. No difficult electronic subproblem was specified or established; quantum availability alone is not a reason to invoke it.'},
            'workflow': [n.capability_name for n in record.node_executions if n.status == 'succeeded'],
            'artifact_sha256': {r.artifact_identifier: r.content_sha256 for r in references.values()},
            'authorizes_execution': False}
        ref = self.runner.write_json(artifact_type='prospective_candidate_assessment',
            payload=json.dumps(document, sort_keys=True, separators=(',', ':'), allow_nan=False).encode(),
            producer='discovery.prospective_assess', execution_identifier=invocation.invocation_identifier,
            parents=tuple({r.artifact_identifier: r for r in parents}.values()))
        return ScientificCapabilityOutcome(output_artifacts=(ref,), evidence_artifacts=(ref,), verified=True,
            scientific_summary='Completed a prospective screening assessment with explicitly unsupported risk dimensions.',
            limitations=(document['verification_scope'], *document['blinding_limitations']))
