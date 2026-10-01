"""Unrelated/synthetic contracts. Synthetic scores are not scientific acceptance results."""
import hashlib
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from cgr.pulsate_api.phase8_scientific_handlers import _NativeRunner
from cgr.pulsate_api.scientific_off_targets import (
    OffTargetHypothesisHandler, OffTargetScreeningHandler, OffTargetVerificationHandler, _FoundationalSources,
    chemical_similarity, compare_screening, functional_hypotheses, retain_panel,
)


class Store:
    def __init__(self):
        self.payloads = {}

    def read(self, reference):
        return self.payloads[reference.artifact_identifier]

    def write(self, reference, payload):
        self.payloads[reference.artifact_identifier] = payload


def hypothesis(accession, similarity=.9, pdb='AAAA'):
    return dict(uniprot_accession=accession, similarity=similarity, resolution_angstrom=2., pdb_id=pdb, chain='A')


def test_retains_multiple_distinct_alternatives_and_every_exclusion():
    hypotheses = [hypothesis('Q00000'), hypothesis('Q00001'), hypothesis('Q00002'),
                  hypothesis('Q00003'), hypothesis('Q00004'), hypothesis('Q00001', .8, 'BBBB')]
    selected, excluded = retain_panel(hypotheses, 'Q00000')
    assert {h['uniprot_accession'] for h in selected} == {'Q00001', 'Q00002', 'Q00003'}
    assert len(selected) + len(excluded) == len(hypotheses)
    assert {h['exclusion_reason'] for h in excluded} == {
        'intended_target_not_off_target', 'duplicate_target_structural_evidence', 'bounded_panel_overflow'}
    assert retain_panel(hypotheses, 'Q00000', maximum_targets=0)[0] == []


@pytest.mark.parametrize('bound', [-1, 4, 1.5, True])
def test_panel_bounds_are_explicit(bound):
    with pytest.raises(ValueError): retain_panel([], 'Q00000', maximum_targets=bound)


def test_real_local_fingerprints_not_public_relevance_scores():
    assert chemical_similarity('CCO', 'OCC') == 1
    assert chemical_similarity('CCO', 'CCCC') < 1
    assert chemical_similarity('c1ccccc1', 'CCO') < .7
    with pytest.raises(ValueError): chemical_similarity('not-a-graph', 'CCO')


def _annotations():
    return {'uniProtKBCrossReferences': [
        {'database': 'GO', 'id': 'GO:0000001', 'properties': [{'key': 'GoTerm', 'value': 'P:process-X'}, {'key': 'GoEvidenceType', 'value': 'IDA:test'}]},
        {'database': 'GO', 'id': 'GO:0000002', 'properties': [{'key': 'GoTerm', 'value': 'F:function-Y'}]},
        {'database': 'GO', 'id': 'GO:0000003', 'properties': [{'key': 'GoTerm', 'value': 'C:location-Z'}]},
        {'database': 'narrative', 'id': 'untrusted'}]}


def test_functional_annotations_remain_conditional_sourced_hypotheses():
    result = functional_hypotheses(_annotations(), interaction_supported=True)
    assert len(result) == 2
    assert result[0]['annotation_evidence'] == 'IDA:test'
    assert all(r['classification'] == 'functional_consequence_hypothesis' for r in result)
    assert all('not established' in r['limitation'] for r in result)
    assert functional_hypotheses(_annotations(), interaction_supported=False) == []


def test_cross_target_difference_is_not_selectivity_or_toxicity():
    result = compare_screening(-7., {'vina_scores_kcal_per_mol': [-8., -7.5],
        'score_semantics': 'autodock_vina_score_not_binding_free_energy'})
    assert result['raw_score_difference_kcal_per_mol'] == -1.
    assert result['follow_up_warranted'] and result['classification'] == 'computational_interaction_hypothesis'
    assert 'not affinity' in result['limitation']
    assert 'toxicity' not in result and 'selectivity_ratio' not in result


@pytest.mark.parametrize('scores,semantics', [([], 'autodock_vina_score_not_binding_free_energy'),
    ([float('nan')], 'autodock_vina_score_not_binding_free_energy'), ([False], 'autodock_vina_score_not_binding_free_energy'), ([-6.], 'measured_affinity')])
def test_invalid_screening_is_not_reported_as_science(scores, semantics):
    with pytest.raises(ValueError): compare_screening(-7., {'vina_scores_kcal_per_mol': scores, 'score_semantics': semantics})


def fixture_record(store, *, candidate_count=2, intended=True):
    runner = _NativeRunner(store)
    refs, candidates = [], []
    for i in range(candidate_count):
        identifier = f'candidate-{i}'
        descriptor = runner.write_json(artifact_type='molecular_candidate_evaluation', producer='test', execution_identifier='test',
            payload=json.dumps({'candidate_identifier': identifier, 'canonical_smiles': 'c1ccccc1-c1ccccc1'}).encode())
        refs.append(descriptor)
        candidates.append({'candidate_identifier': identifier, 'evidence': [{'artifacts': [{'artifact_identifier': descriptor.artifact_identifier}]}],
            'properties_and_calculations': [{'objective_identifier': 'vina_pose_score', 'value': -7.}]})
    trace = runner.write_json(artifact_type='discovery_design_loop_trace', producer='test', execution_identifier='test',
        payload=json.dumps({'candidates': candidates, 'loop_complete': True}).encode())
    pocket = runner.write_json(artifact_type='binding_pocket', producer='test', execution_identifier='test',
        payload=json.dumps({'target_selection_evidence': {'uniprot_accession': 'Q00000', 'organism': {'taxonId': 9606}} if intended else None}).encode())
    return SimpleNamespace(artifact_references=(*refs, trace, pocket), verified=True)


def _line(fields):
    line = list(' ' * 80)
    for start, end, value in fields: line[start:end] = str(value).ljust(end-start)[:end-start]
    return ''.join(line)


def coordinates(accession):
    lines = ['EXPDTA    X-RAY DIFFRACTION', 'REMARK   2 RESOLUTION.    2.00 ANGSTROMS.',
        _line([(0, 6, 'DBREF '), (12, 13, 'A'), (14, 18, '1'), (20, 24, '20'),
               (26, 32, 'UNP'), (33, 41, accession), (55, 60, '1'), (62, 67, '20')])]
    for i in range(1, 21):
        lines.append(_line([(0, 6, 'ATOM  '), (17, 20, 'ALA'), (21, 22, 'A'), (22, 26, i),
            (30, 38, f'{i/10:.3f}'), (38, 46, '0.000'), (46, 54, '0.000'), (76, 78, 'C')]))
    for i in range(12):
        lines.append(_line([(0, 6, 'HETATM'), (17, 20, 'LIG'), (21, 22, 'A'), (22, 26, 50),
            (30, 38, f'{i/10:.3f}'), (38, 46, '1.000'), (46, 54, '0.000'), (76, 78, 'C')]))
    return '\n'.join(lines).encode()


def structural_fetch(url, *args, **kwargs):
    from rdkit import Chem
    parsed = urlsplit(url)
    if parsed.hostname == 'files.rcsb.org':
        return coordinates({'AAAA': 'Q00001', 'BBBB': 'Q00002', 'CCCC': 'Q00000'}[parsed.path.split('/')[-1][:4]])
    if parsed.path == '/graphql':
        smiles = 'c1ccccc1-c1ccccc1'
        result = {'data': {'chem_comp': {'rcsb_chem_comp_descriptor': {'SMILES': smiles, 'InChIKey': Chem.MolToInchiKey(Chem.MolFromSmiles(smiles))}}}}
    elif parsed.hostname == 'rest.uniprot.org':
        accession = parse_qs(parsed.query)['query'][0].split(':')[1]
        result = {'results': [dict(_annotations(), primaryAccession=accession, organism={'taxonId': 9606})]}
    else:
        query = json.loads(parse_qs(parsed.query)['json'][0])
        if query['return_type'] == 'entry':
            assert query['query']['nodes'][0]['service'] == 'text_chem'
            assert query['query']['nodes'][0]['parameters']['attribute'] == 'rcsb_chem_comp_container_identifiers.comp_id'
        identifiers = ['LIG'] if query['return_type'] == 'mol_definition' else ['AAAA', 'BBBB', 'CCCC']
        result = {'result_set': [{'identifier': i} for i in identifiers], 'total_count': len(identifiers)}
    return json.dumps(result).encode()


INVOCATION = SimpleNamespace(invocation_identifier='test-off-target')
OBJECTIVE = SimpleNamespace(budget=SimpleNamespace(maximum_wall_time_seconds=3600))


def test_end_to_end_panel_derivation_preserves_two_candidates_two_alternatives_and_hashes():
    store = Store()
    record = fixture_record(store)
    outcome = OffTargetHypothesisHandler(store, fetch=structural_fetch).execute(invocation=INVOCATION, objective=OBJECTIVE, record=record)
    document = json.loads(store.read(outcome.output_artifacts[0]))
    assert len(document['candidates']) == 2
    for candidate in document['candidates']:
        assert {t['uniprot_accession'] for t in candidate['targets']} == {'Q00001', 'Q00002'}
        assert all(t['similarity'] == 1 and t['classification'] == 'structure_supported_target_hypothesis' for t in candidate['targets'])
    assert not document['failures']
    for source in document['source_audit']:
        assert hashlib.sha256(store.payloads[source['artifact_identifier']]).hexdigest() == source['sha256']
    assert all('comments' not in source['url'] for source in document['source_audit'])


@pytest.mark.parametrize('case', ['missing_scope', 'no_hits', 'missing_dependency'])
def test_missing_evidence_or_engine_retains_every_candidate_without_fabrication(case):
    store = Store()
    record = fixture_record(store, intended=case != 'missing_scope')
    fetch = (lambda *a, **k: b'{}') if case == 'no_hits' else structural_fetch
    panel = OffTargetHypothesisHandler(store, fetch=fetch).execute(invocation=INVOCATION, objective=OBJECTIVE, record=record)
    record.artifact_references = (*record.artifact_references, *panel.evidence_artifacts)
    outcome = OffTargetScreeningHandler(store, adapter=None).execute(invocation=INVOCATION, objective=OBJECTIVE, record=record)
    result = json.loads(store.read(outcome.output_artifacts[0]))
    assert len(result['candidates']) == 2
    assert all(c['status'] == 'insufficient_evidence' for c in result['candidates'])
    assert all(t['status'] == 'unsupported' and t['reason'] for c in result['candidates'] for t in c['targets'])
    assert all('docking' not in t for c in result['candidates'] for t in c['targets'])


def test_source_allowlist_rejects_narrative_and_arbitrary_hosts_without_request():
    sources = _FoundationalSources(_NativeRunner(Store()), INVOCATION, lambda *a, **k: pytest.fail('must not fetch'))
    for url in ('https://example.org/history', 'http://files.rcsb.org/download/AAAA.pdb',
                'https://rest.uniprot.org/uniprotkb/Q00001.json', 'https://data.rcsb.org/news/outcome'):
        with pytest.raises(ValueError): sources.read(url, 'narrative')


def test_composition_requires_off_target_report_before_assessment_and_answer():
    from cgr.pulsate_api.scientific_capability_catalogue import phase8_scientific_capability_catalogue
    composed = phase8_scientific_capability_catalogue().compose(task_type='protein_ligand_discovery',
        required_artifact_types=('prospective_candidate_assessment', 'scientist_facing_result'),
        available_artifact_types=('scientist_input', 'protein_structure', 'ligand_structure'))
    names = [s.capability_name for s in composed]
    assert names.index('discovery.off_target_hypothesize') < names.index('discovery.off_target_screen') < names.index('discovery.prospective_assess') < names.index('scientist.result_assemble')
    assert names.index('discovery.off_target_screen') < names.index('discovery.off_target_verify') < names.index('discovery.prospective_assess')


def verification_fixture(defect=None):
    """Fabricated contract receipts ONLY, never used as real scientific results."""
    store = Store()
    runner = _NativeRunner(store)
    record = fixture_record(store, candidate_count=1)
    target = dict(hypothesis('Q00001'), annotation=_annotations())
    def write(kind, document):
        return runner.write_json(artifact_type=kind, payload=json.dumps(document).encode(), producer='test', execution_identifier='test')
    receptor = write('docking_receptor_pdbqt', {'test': 'receptor'})
    native = {'candidate_identifier': 'candidate-0', 'receptor_sha256': receptor.content_sha256,
              'preparation_optimization_converged': True, 'vina_scores_kcal_per_mol': [-8.],
              'score_semantics': 'autodock_vina_score_not_binding_free_energy'}
    native_ref = write('molecular_candidate_docking_evaluation', native)
    comparison = compare_screening(-7., native)
    computed = dict(target=target, status='computed', docking=dict(native), comparison=dict(comparison),
        functional_hypotheses=functional_hypotheses(_annotations(), interaction_supported=True),
        evidence_artifact_identifiers=[receptor.artifact_identifier, native_ref.artifact_identifier])
    panel = write('prospective_target_panel', {'candidates': [{'candidate_identifier': 'candidate-0', 'targets': [target]}]})
    if defect == 'score': computed['docking']['vina_scores_kcal_per_mol'] = [-9.]
    if defect == 'intended': computed['comparison'] = compare_screening(-6., native)
    if defect == 'annotation': computed['functional_hypotheses'][0]['annotation'] = 'invented process'
    if defect == 'unsupported': computed = dict(target=target, status='unsupported')
    if defect == 'coverage': computed = None
    report = write('prospective_off_target_screen', {'candidates': [{'candidate_identifier': 'candidate-0', 'targets': [computed] if computed else []}]})
    if defect == 'hash': store.payloads[native_ref.artifact_identifier] = b'{}'
    record.artifact_references = (*record.artifact_references, receptor, native_ref, panel, report)
    return store, record


def test_independent_verifier_rechecks_native_receipts_and_panel():
    store, record = verification_fixture()
    result = OffTargetVerificationHandler(store).execute(invocation=INVOCATION, objective=OBJECTIVE, record=record)
    assert result.verified
    report = json.loads(store.read(result.output_artifacts[0]))
    assert len(report['verified_pairs']) == 1 and 'Not physiological' in report['scope']


@pytest.mark.parametrize('defect', ['score', 'intended', 'annotation', 'unsupported', 'coverage', 'hash'])
def test_independent_verifier_blocks_unfaithful_assessment(defect):
    from cgr.pulsate_api.scientific_runtime import ScientificCapabilityFailure
    store, record = verification_fixture(defect)
    with pytest.raises(ScientificCapabilityFailure):
        OffTargetVerificationHandler(store).execute(invocation=INVOCATION, objective=OBJECTIVE, record=record)


def test_candidate_budget_never_silently_prioritizes_first_candidates():
    store = Store()
    record = fixture_record(store, candidate_count=13)
    result = OffTargetHypothesisHandler(store, fetch=lambda *a, **k: pytest.fail('over-budget panel must not fetch')).execute(
        invocation=INVOCATION, objective=OBJECTIVE, record=record)
    panel = json.loads(store.read(result.output_artifacts[0]))
    assert len(panel['candidates']) == 13 and len(panel['failures']) == 13
    assert all(not c['targets'] for c in panel['candidates'])
