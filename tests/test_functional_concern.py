import pytest

from cgr.pulsate_api.functional_concern import assess,GATES
from cgr.pulsate_api.virtual_organism import canonical,digest


def fixture():
    raw=b'{"mechanism":"Synthetic general undesirable mechanism","tissue":"Heart"}'
    sha=digest(raw)
    def anchor(field,expected):return {'source_url':'https://www.ebi.ac.uk/chembl/api/data/assay/test.json',
        'source_sha256':sha,'source_format':'json','pointer':[field],'expected':expected,'review_basis':'Synthetic contract test only'}
    criteria={'schema':'pulsate.functional-concern-criteria/v1','candidate_informed':False,'reject_available':False,
        'reviewer':'Synthetic internal test','version':'test','scope':'No scientific validation claim','limitations':['Synthetic'],
        'rules':[{'identifier':'general-test','target_accession':'P00001','action':'blocker','kind':'IC50',
            'organ':'Heart','compartment':'Interstitial Unbound','undesirable_mechanism':'Synthetic mechanism',
            'source_anchors':[anchor('mechanism','Synthetic general undesirable mechanism')],
            'tissue_anchors':[anchor('tissue','Heart')],'limitations':['Synthetic']}]}
    activity={'target_accession':'P00001','species':'Human','functional_direction':'blocker','kind':'IC50',
        'organ':'Heart','compartment':'Interstitial Unbound','concentration_basis':'unbound','eligibility':{'eligible':True}}
    candidate={'quantitative_activity':[activity],'exposure_activity':[{'activity_sha256':digest(canonical(activity)),
        'comparisons':[{'native_population_sha256':'p','subject_identifier':'s','native_path':'test',
            'peak_activity_ratio_interval':[1.1,10.]}]}],'functional_models':[{'status':'computed',
        'result':{'perturbation':{'transfer_receipt':{'qualification_state':'exploratory_only','candidate_decision_authority':False}}}}]}
    return candidate,criteria,{sha:raw}


def test_no_rule_is_invented_and_reject_is_unavailable():
    result=assess({},None,{})
    assert result['status']=='INSUFFICIENT EVIDENCE' and result['reject_available'] is False


def test_source_replayed_concern_is_only_after_blocking_verification():
    candidate,criteria,sources=fixture()
    result=assess(candidate,criteria,sources)
    assert result['status']=='INSUFFICIENT EVIDENCE' and 'blocking_verification' in result['rule_results'][0]['missing_gates']
    assert assess(candidate,criteria,sources,verification_passed=True)['status']=='CONCERN'


def test_exploratory_physiology_cannot_replace_missing_functional_uncertainty():
    candidate,criteria,sources=fixture()
    candidate['exposure_activity'][0]['comparisons'][0]['peak_activity_ratio_interval']=[.01,10.]
    result=assess(candidate,criteria,sources,verification_passed=True)
    assert result['status']=='INSUFFICIENT EVIDENCE'
    assert result['rule_results'][0]['gates']['robust_to_uncertainty'] is False


def test_nominal_concentration_or_partial_population_never_passes():
    candidate,criteria,sources=fixture()
    candidate['quantitative_activity'][0]['concentration_basis']='assay_nominal'
    assert assess(candidate,criteria,sources,verification_passed=True)['status']=='INSUFFICIENT EVIDENCE'
    candidate,criteria,sources=fixture();candidate['native_sensitivity']={'summary':{'complete_discrete_space':False}}
    assert assess(candidate,criteria,sources,verification_passed=True)['status']=='INSUFFICIENT EVIDENCE'


def test_rehashed_review_does_not_replace_original_mechanism_bytes():
    candidate,criteria,sources=fixture()
    criteria['rules'][0]['source_anchors'][0]['expected']='invented'
    with pytest.raises(ValueError):assess(candidate,criteria,sources,verification_passed=True)
