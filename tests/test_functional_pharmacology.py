"""Synthetic semantic guards; not observed drug-response acceptance evidence."""
import pytest
from cgr.pulsate_api.functional_pharmacology import normalize_assay, candidate_assays
from cgr.pulsate_api.prospective_evidence import ProspectivePolicy
from cgr.pulsate_api.virtual_organism import canonical, digest


def fixture():
    raw = canonical({'value':120.,'unit':'nM','published':'1999-12-31','action':'blocker','hill':1.3,
        'bath':'Synthetic protein-free assay contract, not a real experiment'})
    sha = digest(raw)
    url = 'https://www.ebi.ac.uk/chembl/api/data/synthetic-functional.json'
    original = dict(source_url=url,source_record='Synthetic primary fixture only',source_sha256=sha,
        publication_date='1999-12-31',date_precision='day',source_kind='original_primary_record',
        reviewer='Synthetic test',review_basis='Contract only',publication_pointer=['published'],
        publication_value='1999-12-31',value_pointer=['value'],unit_pointer=['unit'])
    datum = dict(identifier='synthetic-assay',source_url=url,source_record='Synthetic test',source_sha256=sha,
        value=120.,unit='nM',value_pointer=['value'],category='quantitative_activity',evidence_type='measured',
        subject_inchikey='test-identity',target_accession='test-target',publication_date='1999-12-31',
        date_precision='day',original_source=original,context={'species':'Human','kind':'IC50','concentration_basis':'assay_nominal'})
    def anchor(key, value):
        return {'source_sha256':sha,'source_url':url,'source_format':'json','pointer':[key],
            'expected':value,'review_basis':'Synthetic semantic test, not scientific qualification'}
    record = dict(datum=datum,assay_type='functional',action='blocker',endpoint='IC50',assay_host='Synthetic test host',
        target_species='Human',tissue='Heart',compartment='Interstitial Unbound',assay_context='Synthetic voltage-clamp context',
        action_evidence=dict(anchor('action','blocker'),action='blocker',target_accession='test-target',target_species='Human'),hill=dict(anchor('hill',1.3),value=1.3),
        concentration_translation={'kind':'protein_free_bath_equivalence_assumption','assumed_fraction_unbound':1.,
            'rationale':'Test assumption only','limitations':'Unmeasured free concentration','applicability':'Synthetic bath only',
            'source_anchors':[anchor('bath','Synthetic protein-free assay contract, not a real experiment')]},
        applicability='Synthetic guards only',uncertainty='Not actual activity evidence')
    return record, {'inchikey':'test-identity'}, {sha:raw}, ProspectivePolicy(cutoff='2000-01-01',cutoff_source='Synthetic test')


def test_functional_endpoint_units_and_assumption_are_not_laundered():
    record, identity, sources, policy = fixture()
    activity, _ = normalize_assay(record,identity,policy,sources)
    assert activity['value'] == pytest.approx(.12) and activity['unit'] == 'umol/l'
    assert activity['functional_transfer_supported'] and activity['hill_coefficient'] == 1.3
    assert activity['concentration_basis_classification'] == 'assumption'
    assert activity['measured_unbound_concentration'] is False


@pytest.mark.parametrize('unit,value', [('nmol/l', 120.), ('umol/l', .12)])
def test_canonical_concentration_units_follow_exact_original_conversion(unit, value):
    record, identity, sources, policy = fixture()
    record['datum']['unit'] = unit
    record['datum']['value'] = value
    # Original primary data remain 120 nM. A separate normalization receipt is
    # explicitly non-primary; changing the source datum itself would be refused.
    original_sha = record['datum']['source_sha256']
    receipt = canonical({'value': value, 'unit': unit})
    receipt_sha = digest(receipt)
    sources[receipt_sha] = receipt
    record['datum']['source_sha256'] = receipt_sha
    record['datum']['original_source'].update(original_value=120., original_unit='nM')
    record['datum']['wrapper_provenance'] = {'kind': 'local_extraction_receipt', 'not_primary': True,
        'source_sha256': receipt_sha, 'original_source_sha256': original_sha,
        'discovery_url': record['datum']['source_url'], 'record_identifier': 'Synthetic normalization receipt',
        'reviewer': 'Synthetic unit test', 'purpose': 'Dimension-checked original value conversion'}
    activity, _ = normalize_assay(record, identity, policy, sources)
    assert activity['value'] == pytest.approx(.12)
    assert activity['unit'] == 'umol/l'


@pytest.mark.parametrize('changes', [
    {'functional_transfer_supported':False}, {'assay_type':'binding'},
    {'functional_direction':'unknown'}, {'kind':'Ki'},
])
def test_functional_unsupported_semantics_refuse_before_any_model_qualification(changes):
    from cgr.pulsate_api.mechanistic_models import exposure_perturbation
    record,identity,sources,policy=fixture()
    activity,_=normalize_assay(record,identity,policy,sources)
    activity.update(changes)
    with pytest.raises(ValueError,match='functional assay does not support'):
        exposure_perturbation(None,{}, {},activity,evidence_eligible=True,evidence_sources=sources)


@pytest.mark.parametrize('endpoint', ['Ki','Kd','EC50'])
def test_non_inhibition_endpoints_are_retained_not_aliased(endpoint):
    record,identity,sources,policy = fixture()
    record['endpoint'] = endpoint; record['datum']['context']['kind'] = endpoint
    activity,_ = normalize_assay(record,identity,policy,sources)
    assert activity['kind'] == endpoint and not activity['functional_transfer_supported']


@pytest.mark.parametrize('action', ['agonist','antagonist','activator','unknown'])
def test_direction_does_not_license_reversible_current_block(action):
    record,identity,sources,policy = fixture()
    record['action'] = action
    record['action_evidence']['action'] = action
    activity,_ = normalize_assay(record,identity,policy,sources)
    assert not activity['functional_transfer_supported']


def test_binding_is_not_function_and_unsourced_hill_is_never_defaulted():
    record,identity,sources,policy = fixture()
    record['assay_type']='binding'; record['action']='binding_only'
    record['action_evidence']['action']='binding_only'
    activity,_ = normalize_assay(record,identity,policy,sources)
    assert not activity['functional_transfer_supported']
    record,identity,sources,policy = fixture(); record['hill']=None
    activity,_ = normalize_assay(record,identity,policy,sources)
    assert 'hill_coefficient' not in activity and not activity['functional_transfer_supported']


@pytest.mark.parametrize('change', ['identity','target','species','endpoint','value','unit','hill','action_source','bath_source','hash'])
def test_missing_or_changed_semantics_fail_closed(change):
    record,identity,sources,policy = fixture()
    if change == 'identity': identity['inchikey']='other'
    elif change == 'target': record['datum']['target_accession']=None
    elif change == 'species': record['target_species']='Rat'
    elif change == 'endpoint': record['endpoint']='Kd'
    elif change == 'value': record['datum']['value']=121.
    elif change == 'unit': record['datum']['unit']='mg/l'
    elif change == 'hill': record['hill']['value']=1.
    elif change == 'action_source': record['action_evidence']['expected']='inhibitor'
    elif change == 'bath_source': record['concentration_translation']['source_anchors'][0]['expected']='albumin'
    elif change == 'hash': sources[next(iter(sources))]=b'{}'
    with pytest.raises((ValueError,KeyError)): normalize_assay(record,identity,policy,sources)


def test_identity_and_prospective_eligibility_are_both_required():
    record,identity,sources,policy = fixture()
    activities,refused = candidate_assays(identity,{'assays':[record]},sources,
        ProspectivePolicy(cutoff='1998-01-01',cutoff_source='Earlier synthetic cutoff'))
    assert not activities and len(refused)==1
    assert candidate_assays({'inchikey':'other'},{'assays':[record]},sources,policy)==([],[])
