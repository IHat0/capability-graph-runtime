import copy

import pytest

from cgr.pulsate_api import functional_prediction as prediction
from cgr.pulsate_api.virtual_investigation import activity_overlap,predict_functional_activity


def gate():
    return {'schema':'pulsate.functional-research-gate/v1','species':'Human','domain':'in_domain',
        'reviewer':'Internal test','version':'test','rationale':'Synthetic numerical boundary test','limitations':['Not scientific evidence'],
        'limits':{'minimum_n':30,'minimum_interval_coverage':.8,'maximum_mae':.6,'maximum_rmse':.9,
            'minimum_spearman':.5,'maximum_median_multiplicative_error':4.}}


def test_every_frozen_gate_must_pass():
    model={'kind':'IC50','unit':'pIC50'}
    metrics={'n':30,'interval_coverage':.8,'mae':.6,'rmse':.9,'spearman':.5,'median_multiplicative_error':4.}
    assert prediction.model_gate({'by_domain':{'in_domain':metrics}},gate(),model)['accepted']
    for key,value in {'n':29,'interval_coverage':.79,'mae':.61,'rmse':.91,'spearman':.49,'median_multiplicative_error':4.01}.items():
        result=prediction.model_gate({'by_domain':{'in_domain':dict(metrics,**{key:value})}},gate(),model)
        assert not result['accepted'] and key in result['failed_metrics']
    with pytest.raises(ValueError):prediction.model_gate({'by_domain':{'in_domain':metrics}},gate(),dict(model,kind='Ki'))


def test_replay_cache_requires_identical_verified_content_and_cannot_cache_failure():
    prediction._REPLAY_CACHE.clear();seen=[]
    def verify():seen.append('checked');return {'valid':True,'nested':[1]}
    key=('synthetic-dataset-sha',('original-source-sha',))
    first=prediction._checked_replay(key,verify);first['nested'].append(2)
    assert prediction._checked_replay(key,verify)['nested']==[1]
    assert len(seen)==1
    prediction._checked_replay(('changed-dataset-sha',key[1]),verify)
    prediction._checked_replay((key[0],('changed-primary-source-sha',)),verify)
    assert len(seen)==3
    def failed():raise ValueError('Source mismatch')
    for _ in range(2):
        with pytest.raises(ValueError):prediction._checked_replay(('failed',),failed)
    assert ('failed',) not in prediction._REPLAY_CACHE


def test_nominal_functional_prediction_cannot_be_laundered_into_unbound_overlap():
    activity={'species':'Human','organ':'Heart','compartment':'Interstitial Unbound','target_accession':'P00001',
        'concentration_basis':'assay_nominal','unit':'umol/l','assay_context':'Synthetic assay','value':.1,
        'functional_direction':'blocker','functional_transfer_supported':False,'functional_prediction_sha256':'test'}
    series=[{'species':'Human','organ':'Heart','compartment':'Interstitial Unbound','concentration_basis':'unbound','unit':'umol/l',
        'times_h':[0.,1.],'values_umol_l':[0.,.2]}]
    assert activity_overlap(series,activity)['status']=='insufficient_compatible_evidence'


def test_interval_overlap_preserves_subject_native_lineage_and_uncertainty():
    activity={'species':'Human','organ':'Heart','compartment':'Interstitial Unbound','target_accession':'P00001',
        'kind':'IC50','concentration_basis':'unbound','unit':'umol/l','assay_context':'Synthetic free assay',
        'value':.1,'interval_umol_l':[.05,.4],'interval_definition':'Test interval, not clinical',
        'uncertainty':'Unquantified assay transfer','functional_direction':'blocker'}
    series=[{'species':'Human','organ':'Heart','compartment':'Interstitial Unbound','concentration_basis':'unbound','unit':'umol/l',
        'subject_identifier':'s','exposure_case_identifier':'c','native_population_sha256':'sha',
        'path':'native-test','times_h':[0.,1.],'values_umol_l':[0.,.2]}]
    result=activity_overlap(series,activity)
    assert result['status']=='modeled_peak_overlaps_activity_range'
    assert result['comparisons'][0]['peak_activity_ratio_interval']==[.5,4.]
    assert result['comparisons'][0]['subject_identifier']=='s'


def test_legacy_policy_does_not_enable_new_models_implicitly():
    assert predict_functional_activity({},None,None,None,None)==(None,[],{})
