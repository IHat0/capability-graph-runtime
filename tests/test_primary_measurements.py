"""Adversarial synthetic extraction contracts, not measured scientific evidence."""
from datetime import date
import copy

import pytest

from cgr.pulsate_api.primary_measurements import locate
from cgr.pulsate_api.prospective_evidence import EvidenceDatum, ProspectivePolicy, adjudicate, verify_adjudication
from cgr.pulsate_api.virtual_organism import canonical, digest


def example(format='text'):
    # Deliberately synthetic original source, accompanied by a modern wrapper.
    text = 'Published 1999. Study X in Human plasma: fraction unbound 25 %. Outcome narrative is NOT an admitted datum.'
    if format == 'html':
        raw = ('<html><p>Published 1999. Study X in Human plasma.</p>'
            '<table><tr><th>Endpoint</th><th>Value</th><th>Unit</th></tr>'
            '<tr><td>fraction unbound</td><td>25</td><td>%</td></tr></table>'
            '<p>Outcome narrative is NOT an admitted datum.</p></html>').encode()
        value = ['table', 0, 1, 1, '25']
        unit = ['table', 0, 1, 2, '%']
        endpoint = ['table', 0, 1, 0, 'fraction unbound']
    else:
        raw = text.encode()
        value = ['span', 'fraction unbound 25 %.', '25']
        unit = ['span', 'fraction unbound 25 %.', '%']
        endpoint = ['span', 'fraction unbound 25 %.', 'fraction unbound']
    wrapper = canonical({'value': .25, 'unit': 'fraction', 'citation': '1999', 'published': '2025'})
    original = dict(source_url='https://pk-db.com/media/data/synthetic.' + format,
        source_record='SYNTHETIC-NOT-SCIENTIFIC-EVIDENCE', source_sha256=digest(raw),
        source_kind='original_primary_record', reviewer='Synthetic test reviewer',
        review_basis='Synthetic exact-source extraction test only',
        publication_date='1999-12-31', date_precision='year', publication_value='1999',
        publication_pointer=['span', 'Published 1999.', '1999'],
        value_pointer=value, unit_pointer=unit, original_value=25, original_unit='%', source_format=format,
        subject_inchikey='SYNTHETIC', semantic_context={'species':'Human','endpoint':'fraction_unbound'},
        context_pointers={'identity':['span','Study X in Human plasma','Study X'],
            'species':['span','Study X in Human plasma','Human'], 'endpoint':endpoint})
    datum = dict(identifier='synthetic-fu', source_url='https://www.ebi.ac.uk/chembl/api/data/synthetic.json',
        source_record='modern-synthetic-wrapper', source_sha256=digest(wrapper), category='binding',
        subject_inchikey='SYNTHETIC', value=.25, unit='fraction', value_pointer=['value'],
        evidence_type='measured', publication_date='2025-12-31', date_precision='year',
        context={'species':'Human', 'endpoint':'fraction_unbound'}, original_source=original,
        wrapper_provenance={'kind':'public_database_wrapper','not_primary':True,
            'source_url':'https://www.ebi.ac.uk/chembl/api/data/synthetic.json',
            'source_sha256':digest(wrapper),'original_source_sha256':digest(raw),
            'record_identifier':'Synthetic wrapper','reviewer':'Synthetic test',
            'purpose':'Synthetic secondary discovery only; original numerical datum replay is required'})
    return datum, {digest(raw):raw, digest(wrapper):wrapper}


def run(datum, sources):
    return adjudicate([EvidenceDatum.model_validate(datum)], sources,
        ProspectivePolicy(cutoff=date(2000,1,1),cutoff_source='Synthetic challenge'), 'SYNTHETIC')


@pytest.mark.parametrize('format',['text','html'])
def test_modern_wrapper_only_admits_exact_original_value_with_surrounding_narrative(format):
    d,s = example(format)
    report = run(d,s)
    assert report['decisions'][0]['eligible']
    assert verify_adjudication(report,s)['passed']
    assert report['datums'][0]['value'] == .25
    d['original_source'] = None
    assert not run(d,s)['decisions'][0]['eligible']


@pytest.mark.parametrize('field,value', [('subject_inchikey','WRONG'),('unit','umol/l'),
    ('context',{'species':'Rat','endpoint':'fraction_unbound'}),
    ('context',{'species':'Human','endpoint':'hepatic_clearance_plasma_clearance'})])
def test_identity_species_endpoint_and_units_cannot_be_changed(field,value):
    d,s=example()
    d[field]=value
    with pytest.raises(ValueError): run(d,s)


def test_table_cell_and_preserved_bytes_tampering_refused():
    d,s=example('html')
    d['original_source']['value_pointer']=['table',0,1,2,'25']
    with pytest.raises(ValueError,match='cell'): run(d,s)
    d,s=example()
    sha=d['original_source']['source_sha256']
    s[sha]=s[sha].replace(b'25',b'35')
    with pytest.raises(ValueError,match='hash'): run(d,s)


@pytest.mark.parametrize('format,raw,pointer,expected',[
    ('csv',b'endpoint,value,unit\nfu,25,%\n',['cell',1,1,'25'],'25'),
    ('tsv',b'endpoint\tvalue\tunit\nfu\t25\t%\n',['cell',1,2,'%'],'%'),
    ('html',b'<table><tr><td>25</td></tr></table>',['table',0,0,0,'25'],'25'),
])
def test_machine_readable_primary_tables(format,raw,pointer,expected):
    assert locate(raw,format,pointer)==expected
    changed=copy.deepcopy(pointer); changed[-1]='wrong'
    with pytest.raises(ValueError,match='cell'): locate(raw,format,changed)


def test_ambiguous_secondary_merged_and_script_values_fail_closed():
    with pytest.raises(ValueError,match='ambiguous'): locate(b'Value 2. Value 2.','text',['span','Value 2.','2'])
    with pytest.raises(ValueError,match='Merged'): locate(b'<table><tr><td colspan="2">2</td></tr></table>',
        'html',['table',0,0,0,'2'])
    with pytest.raises(ValueError,match='absent'): locate(b'<script>Value 2.</script>','html',['span','Value 2.','2'])
    d,s=example(); d['original_source']['source_kind']='secondary_transcription'
    with pytest.raises(ValueError,match='secondary'): run(d,s)


def test_unreviewed_ocr_and_missing_page_evidence_never_admitted():
    payload=b'%PDF-synthetic'
    with pytest.raises(ValueError,match='rendered-page review'):
        locate(payload,'pdf',['pdf',1,'Value 2.','2'])
    review={'page':1,'source_sha256':digest(payload),'status':'rendered_page_verified',
        'reviewer':'Synthetic','review_basis':'Synthetic','render_method':'Synthetic',
        'image_sha256':'0'*64,'verified_literals':['2']}
    with pytest.raises(ValueError,match='image'):
        locate(payload,'pdf',['pdf',1,'Value 2.','2'],pdf_reviews=[review])


@pytest.mark.parametrize('raw,literal', [(b'Value 25 %','2'),(b'Value -2 %','2'),(b'Value 2.5 %','2')])
def test_partial_numerical_literals_are_not_measurements(raw,literal):
    with pytest.raises(ValueError,match='partial'):
        locate(raw,'text',['span',raw.decode(),literal])


@pytest.mark.parametrize('pointer', [['cell',True,0,'x'],['cell',9,0,'x'],['cell',0,'0','x']])
def test_invalid_table_cells_fail_closed(pointer):
    with pytest.raises(ValueError): locate(b'x','csv',pointer)


def test_pdf_whitespace_must_not_manufacture_a_numerical_literal():
    from cgr.pulsate_api.primary_measurements import _span
    with pytest.raises(ValueError,match='whitespace'):
        _span('Value 2 5 %','Value25%','25',pdf=True)


def test_unrelated_merged_table_does_not_poison_an_exact_unmerged_table():
    raw = (b'<table><tr><th colspan="2">Ambiguous table, never admitted</th></tr></table>'
        b'<table><tr><th>Value Mean (SD)</th></tr><tr><td>12.5 (2.4)</td></tr></table>')
    assert locate(raw, 'html', ['table', 1, 1, 0, '12.5 (2.4)']) == '12.5 (2.4)'
    with pytest.raises(ValueError, match='Merged'):
        locate(raw, 'html', ['table', 0, 0, 0, 'Ambiguous table, never admitted'])


def test_exact_scalar_within_a_reviewed_statistical_cell_is_not_guessed():
    raw = b'<table><tr><td>12.5 (2.4)</td></tr></table>'
    assert locate(raw, 'html', ['table_span', 0, 0, 0, '12.5 (2.4)', '12.5']) == '12.5'
    for cell, literal in [('12.5 (9.9)', '12.5'), ('12.5 (2.4)', '12'), ('12.5 (2.4)', '9.9')]:
        with pytest.raises(ValueError):
            locate(raw, 'html', ['table_span', 0, 0, 0, cell, literal])
    repeated = b'<table><tr><td>12.5 (12.5)</td></tr></table>'
    with pytest.raises(ValueError, match='unique'):
        locate(repeated, 'html', ['table_span', 0, 0, 0, '12.5 (12.5)', '12.5'])


def test_statistical_cell_keeps_header_and_unit_as_separate_required_evidence():
    # The locator never decides whether 12.5 is a mean, a deviation or an endpoint.
    # Semantic/header qualification remains in the calling evidence contract.
    raw = b'<table><tr><th>Mean (SD), nM</th></tr><tr><td>12.5 (2.4)</td></tr></table>'
    assert locate(raw, 'html', ['table_span', 0, 0, 0, 'Mean (SD), nM', 'nM']) == 'nM'
    assert locate(raw, 'html', ['table', 0, 0, 0, 'Mean (SD), nM']) == 'Mean (SD), nM'


def test_script_table_is_never_an_original_measurement():
    raw = b'<script><table><tr><td>25</td></tr></table></script>'
    with pytest.raises(ValueError):
        locate(raw, 'html', ['table', 0, 0, 0, '25'])


def test_exact_number_is_not_ambiguous_with_a_substring_of_another_number():
    raw = b'<table><tr><td>1051.9 (9)</td></tr></table>'
    assert locate(raw, 'html', ['table_span', 0, 0, 0, '1051.9 (9)', '9']) == '9'
    with pytest.raises(ValueError, match='partial'):
        locate(b'Value 1e3', 'text', ['span', 'Value 1e3', '3'])
    with pytest.raises(ValueError, match='partial'):
        locate(b'Value 1e-3', 'text', ['span', 'Value 1e-3', '-3'])


def test_layout_break_cannot_join_separate_cell_numbers():
    raw = b'<table><tr><td>12<br>5</td></tr></table>'
    with pytest.raises(ValueError, match='cell mismatch'):
        locate(raw, 'html', ['table_span', 0, 0, 0, '125', '125'])
