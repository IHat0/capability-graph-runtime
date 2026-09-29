"""No entity-specific policy: exact identities, not organic size, define sites."""
import pytest
from cgr.pulsate_api.scientific_site_identity import requested_site_components
from cgr.pulsate_api.scientific_target_selection import inspect_receptor
from test_target_selection_evidence import _source


def test_large_contacting_component_without_candidate_identity_is_rejected():
    candidate = {'chains': ['A'], 'domain_start': 100, 'domain_end': 119,
                 'uniprot_accession': 'QTEST', 'site_components': {'OTHER': {}}}
    with pytest.raises(ValueError, match='no unique ligand-defined site'):
        inspect_receptor(_source(), candidate)


def test_exact_site_match_is_retained_in_evidence():
    evidence = {'chemical_component_id': 'LIG', 'match_policy': 'full standard InChIKey equality'}
    candidate = {'chains': ['A'], 'domain_start': 100, 'domain_end': 119,
                 'uniprot_accession': 'QTEST', 'site_components': {'LIG': evidence}}
    assert inspect_receptor(_source(), candidate)['site_identity_evidence'] == evidence


@pytest.mark.parametrize('mismatch', [False, True])
def test_full_identity_not_connectivity_prefix(mismatch):
    key = 'ABCDEFGHIJKLMN-ABCDEFGHIJ-N'
    responses = iter([
        {'IdentifierList': {'CID': [123]}},
        {'PropertyTable': {'Properties': [{'CID': 123, 'InChIKey': key}]}},
        {'total_count': 1, 'result_set': [{'identifier': 'XYZ'}]},
        {'rcsb_chem_comp_descriptor': {'InChIKey': key if not mismatch else 'ABCDEFGHIJKLMN-ZBCDEFGHIJ-N'}},
    ])
    if mismatch:
        with pytest.raises(ValueError, match='disagrees'):
            requested_site_components(['candidate'], lambda url: next(responses))
    else:
        assert requested_site_components(['candidate'], lambda url: next(responses))['XYZ']['pubchem_cid'] == 123


def test_no_candidates_requires_site_clarification():
    with pytest.raises(ValueError, match='requires named candidate'):
        requested_site_components([], lambda url: pytest.fail('must not query'))


def test_empty_search_is_not_an_oversized_structure(monkeypatch):
    import io
    from cgr.pulsate_api import scientific_acquisition as acquisition
    class Response(io.BytesIO):
        status = 204
        headers = {}
        def geturl(self):
            return 'https://search.rcsb.org/test'
    monkeypatch.setattr(acquisition, '_open_source_request', lambda request: Response(b''))
    assert acquisition._fetch_bytes('https://search.rcsb.org/test', 'application/json', 100, allow_no_content=True) == b''
    with pytest.raises(ValueError):
        acquisition._fetch_bytes('https://search.rcsb.org/test', 'application/json', 100)


def test_one_unmatched_candidate_does_not_hide_another_exact_site():
    key = 'ABCDEFGHIJKLMN-ABCDEFGHIJ-N'
    identity = {'PropertyTable': {'Properties': [{'CID': 123, 'InChIKey': key}]}}
    responses = iter([
        {'IdentifierList': {'CID': [123]}}, identity, {},
        {'IdentifierList': {'CID': [123]}}, identity,
        {'total_count': 1, 'result_set': [{'identifier': 'XYZ'}]},
        {'rcsb_chem_comp_descriptor': {'InChIKey': key}},
    ])
    result = requested_site_components(['first candidate', 'second candidate'], lambda url: next(responses))
    assert result['XYZ']['candidate_name'] == 'second candidate'
