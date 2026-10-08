"""Synthetic primary-table/identity contracts, not biological validation."""
import copy

import pytest

from cgr.pulsate_api.safety_pharmacology import SCHEMA, source_membership, load_panel, evaluate_panel
from cgr.pulsate_api.virtual_organism import canonical, digest


def freeze(tmp_path):
    xml = b'<article><table-wrap id="TabA"><table><tbody><tr><td>Domain (3)</td><td>GENEA, GENEB</td></tr></tbody></table></table-wrap></article>'
    tsv = b'Entry\tGene Names (primary)\tProtein names\tOrganism (ID)\nP00001\tGENEA\tSynthetic A\t9606\nP00002\tGENEB\tSynthetic B\t9606\n'
    (tmp_path/'members.xml').write_bytes(xml)
    (tmp_path/'identities.tsv').write_bytes(tsv)
    doc = {'schema': SCHEMA, 'selection_basis': 'primary_general_panel_table', 'candidate_informed_selection': False,
        'reviewer': 'Synthetic fixture', 'version': 'synthetic', 'scope': 'Contract only, not real target coverage',
        'limitations': ['Synthetic identities, not biological evidence'],
        'membership_source': {'path': 'members.xml', 'sha256': digest(xml), 'table_identifier': 'TabA'},
        'identity_source': {'path': 'identities.tsv', 'sha256': digest(tsv)},
        'source_count_discrepancies': [{'source_label': 'Domain (3)', 'stated_count': 3, 'enumerated_count': 2}],
        'targets': [{'gene': g, 'target_accession': a, 'name': n, 'species': 'Human', 'source_domains': ['Domain (3)']}
            for g,a,n in [('GENEA','P00001','Synthetic A'),('GENEB','P00002','Synthetic B')]]}
    (tmp_path/'panel.json').write_bytes(canonical(doc))
    return doc


def test_exact_source_membership_retains_discrepancy_without_inventing_target(tmp_path):
    freeze(tmp_path)
    panel, sources, sha = load_panel(tmp_path/'panel.json')
    report = evaluate_panel(panel, sha, {'targets': []}, [], None)
    assert len(report['targets']) == 2 and all(t['status']=='not_evaluated' for t in report['targets'])
    assert report['source_count_discrepancies'][0]['stated_count']==3
    assert digest((tmp_path/'panel.json').read_bytes())==sha and len(sources)==3


@pytest.mark.parametrize('mutation', ['add_target','swap_identity','hide_discrepancy','candidate_informed'])
def test_panel_cannot_be_tuned_to_candidate_or_launder_identity(tmp_path,mutation):
    doc = freeze(tmp_path)
    if mutation=='add_target': doc['targets'].append(copy.deepcopy(doc['targets'][0]))
    elif mutation=='swap_identity': doc['targets'][0]['target_accession']='P99999'
    elif mutation=='hide_discrepancy': doc['source_count_discrepancies']=[]
    else: doc['candidate_informed_selection']=True
    (tmp_path/'panel.json').write_bytes(canonical(doc))
    with pytest.raises(ValueError): load_panel(tmp_path/'panel.json')


def test_neighbour_evidence_never_becomes_candidate_potency_and_untested_retained(tmp_path):
    freeze(tmp_path)
    panel,_,sha=load_panel(tmp_path/'panel.json')
    neighbours={'targets':[{'target_accession':'P00001','candidate_quantitative_activity':None}]}
    report=evaluate_panel(panel,sha,neighbours,[], 'P00002')
    assert report['targets'][0]['status']=='neighbour_nominated_hypothesis'
    assert report['targets'][0]['candidate_activity_sha256']==[]
    assert report['targets'][1]['status']=='not_evaluated' and report['targets'][1]['intended_target']


def test_external_entities_not_parsed_and_changed_sources_fail(tmp_path):
    with pytest.raises(ValueError): source_membership(b'<!DOCTYPE article [<!ENTITY x "bad">]><article/>','A')
    freeze(tmp_path)
    (tmp_path/'identities.tsv').write_bytes(b'changed')
    with pytest.raises(ValueError, match='hash mismatch'): load_panel(tmp_path/'panel.json')


def test_external_jats_declaration_is_not_retrieved(tmp_path):
    freeze(tmp_path)
    payload=b'<!DOCTYPE article SYSTEM "file:///must-not-be-read.dtd">'+(tmp_path/'members.xml').read_bytes()
    assert source_membership(payload,'TabA')[0]=={'GENEA':['Domain (3)'],'GENEB':['Domain (3)']}
