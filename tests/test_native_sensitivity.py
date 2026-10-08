"""Synthetic design/replay tests. Native numerical acceptance is separate."""
import copy

import pytest
from rdkit import Chem

from cgr.pulsate_api.native_sensitivity import SCHEMA, design, conditional_acquisition, summarize, concentration_metrics, native_exposure_endpoints


def inputs(counts=(2,2)):
    key=Chem.MolToInchiKey(Chem.MolFromSmiles('CCO'))
    datums=[]
    conflicts=[]
    for dimension,n in enumerate(counts):
        role=['fraction_unbound','logp','reference_weight'][dimension]
        unit=['fraction','Log Units','kg'][dimension]
        alternatives=[]
        for level in range(n):
            identifier=f'synthetic-{dimension}-{level}'
            datums.append({'identifier':identifier,'source_sha256':'a'*64,'original_source':{'source_sha256':'b'*64}})
            alternatives.append({'datum':identifier,'value':{'value':(level+1)/10,'unit':unit,
                'classification':'measured','source':'Synthetic test only','method':'Synthetic test only'}})
        conflicts.append({'parameter':role,'alternatives':alternatives})
    return {'identity':{'smiles':'CCO','name':'Synthetic graph','inchikey':key},'species':'Human',
        'eligibility':{'datums':datums},'conflicts':conflicts,'resolved_parameters':{},
        'resolved_controls':{'binding_partner':'Albumin','ionization':[]},'parameter_provenance':{'ionization':['synthetic']}}


def policy(bound=8):
    return {'schema':SCHEMA,'method':'cartesian_or_level_covering','maximum_runs':bound,
        'reviewer':'Synthetic fixture','version':'test1','scientific_justification':'Synthetic discrete design coverage test',
        'scope':'Engineering test, not Human scientific qualification'}


def test_exhaustive_discrete_design_never_selects_nominal_or_loses_sources():
    acquired=inputs()
    plan=design(acquired,policy())
    assert plan['exhaustive'] and len(plan['cases'])==4 and plan['nominal_withheld']
    assert plan==design(copy.deepcopy(acquired),copy.deepcopy(policy()))
    for case in plan['cases']:
        conditional=conditional_acquisition(acquired,case)
        assert not conditional['conflicts'] and conditional['sensitivity_condition']==case
        assert conditional['dossier']['parameters']['fraction_unbound']['classification']=='measured'
    assert len(acquired['conflicts'])==2


def test_bounded_design_covers_all_levels_but_does_not_claim_all_interactions():
    acquired=inputs((4,5,3)); p=policy(12)
    plan=design(acquired,p)
    assert not plan['exhaustive'] and len(plan['cases'])<=12 and plan['total_combinations']==60
    for dimension in plan['dimensions']:
        observed={str(case['assignments'][dimension['parameter']]['value']) for case in plan['cases']}
        assert observed=={str(level['value']) for level in dimension['levels']}
    assert plan==design(acquired,p)
    with pytest.raises(ValueError,match='every qualified'): design(acquired,policy(2))


def test_incompatible_units_and_unreviewed_design_fail_closed():
    acquired=inputs()
    acquired['conflicts'][0]['alternatives'][1]['value']['unit']='nM'
    with pytest.raises(ValueError,match='units'): design(acquired,policy())
    with pytest.raises(ValueError,match='reviewed'): design(inputs(),None)


def test_actual_series_schema_ranges_failed_regions_and_coverage_replay():
    plan=design(inputs((2,)),policy())
    executions=[]
    for number,case in enumerate(plan['cases']):
        executions.append({'case_identifier':case['case_identifier'],'status':'computed','virtual_organism':{
            'runs':[{'species':'Human','result':{'series':[{'organ':'Peripheral Venous Blood',
                'compartment':'Plasma Unbound (Peripheral Venous Blood)','subject_identifier':'test',
                'times_h':[0,1,2],'values_umol_l':[0,number+1,0]}]}}]}})
    summary=summarize(plan,executions)
    assert summary['complete_discrete_space'] and summary['nominal_withheld']
    value=next(iter(summary['endpoint_ranges'].values()))
    assert value['peak_range_umol_l']==[1,2] and value['auc_range_umol_h_l']==[1,2]
    executions[-1]={'case_identifier':executions[-1]['case_identifier'],'status':'unsupported','reason':'Synthetic missing native input'}
    assert not summarize(plan,executions)['complete_discrete_space']
    with pytest.raises(ValueError,match='coverage'): summarize(plan,executions[:1])


def test_refused_native_run_and_missing_endpoint_never_complete_the_design():
    plan=design(inputs((2,)),policy())
    executions=[{'case_identifier':c['case_identifier'],'status':'computed','virtual_organism':{
        'runs':[],'refused':[{'reason':'Synthetic unavailable tool'}]}} for c in plan['cases']]
    summary=summarize(plan,executions)
    assert not summary['complete_discrete_space'] and not summary['endpoint_coverage_complete']
    assert len(summary['failed_cases'])==2


def test_equal_numerical_levels_keep_all_source_provenance():
    acquired=inputs((2,))
    extra=copy.deepcopy(acquired['conflicts'][0]['alternatives'][0])
    extra['datum']='other-provenance'; extra['value']['source']='Another source'
    acquired['eligibility']['datums'].append(dict(acquired['eligibility']['datums'][0],identifier=extra['datum']))
    acquired['conflicts'][0]['alternatives'].append(extra)
    plan=design(acquired,policy())
    assert len(plan['cases'])==2
    assert sum(len(level['sources']) for level in plan['dimensions'][0]['levels'])==3


def test_liver_zonal_native_paths_are_not_merged_into_one_endpoint():
    plan=design(inputs((2,)),policy())
    executions=[]
    for case in plan['cases']:
        series=[{'organ':'Liver','compartment':'Interstitial Unbound','subject_identifier':'0',
            'path':'Organism|Liver|'+zone+'|Graph|Interstitial Unbound','times_h':[0,1],
            'values_umol_l':[0,1]} for zone in ('Periportal','Pericentral')]
        executions.append({'case_identifier':case['case_identifier'],'status':'computed',
            'virtual_organism':{'runs':[{'species':'Human','result':{'series':series}}]}})
    summary=summarize(plan,executions)
    assert summary['complete_discrete_space'] and len(summary['endpoint_ranges'])==2
    executions[0]['virtual_organism']['runs'][0]['result']['series'].append(series[0])
    with pytest.raises(ValueError,match='Duplicate'): summarize(plan,executions)


def test_equal_subject_ordinals_do_not_imply_the_same_virtual_person():
    plan=design(inputs((2,)),policy())
    executions=[]
    for number,case in enumerate(plan['cases']):
        result={'subject_count':1,'population_physiology':{'subjects':[{'identifier':'0','weight_kg':60+number}]},
            'series':[{'path':'Plasma','organ':'Blood','compartment':'Unbound','subject_identifier':'0',
                'times_h':[0,1],'values_umol_l':[0,1]}]}
        executions.append({'case_identifier':case['case_identifier'],'status':'computed',
            'virtual_organism':{'runs':[{'species':'Human','result':result}]}})
    summary=summarize(plan,executions)
    assert len(summary['endpoint_ranges'])==2
    assert not summary['complete_discrete_space']
    assert not summary['population_alignment']['Human']['paired_population_verified']
    executions[1]['virtual_organism']['runs'][0]['result']['population_physiology']=copy.deepcopy(executions[0]['virtual_organism']['runs'][0]['result']['population_physiology'])
    assert summarize(plan,executions)['population_alignment']['Human']['paired_population_verified']
    assert summarize(plan,executions)['complete_discrete_space']
    executions[1]['virtual_organism']['runs'][0]['result']['series'][0]['times_h']=[0,2]
    assert not summarize(plan,executions)['complete_discrete_space']


def test_sampled_metrics_preserve_plateaus_zero_series_and_actual_window():
    s={'times_h':[1,2,3,4], 'values_umol_l':[0,2,2,0]}
    metrics=concentration_metrics(s)
    assert metrics['sampled_tmax_h']==[2,3] and metrics['auc_umol_h_l']==4 and metrics['window_h']==[1,4]
    assert concentration_metrics(dict(s,values_umol_l=[0,0,0,0]))['sampled_tmax_h']==[]
    with pytest.raises(ValueError): concentration_metrics(dict(s,values_umol_l=[0,True,2,0]))


def test_native_ratios_never_mix_subjects_or_free_and_total_concentrations():
    def series(organ,compartment,values,subject='0'):
        return {'organ':organ,'compartment':compartment,'subject_identifier':subject,
            'path':organ+'|'+compartment,'times_h':[0,1,2],'values_umol_l':values}
    rows=[series('PeripheralVenousBlood','Plasma (Peripheral Venous Blood)',[0,2,0]),
        series('PeripheralVenousBlood','Plasma Unbound (Peripheral Venous Blood)',[0,.2,0]),
        series('Liver','Tissue',[0,4,0]), series('Liver','Interstitial Unbound',[0,.4,0]),
        series('Liver','Intracellular Unbound',[0,.4,0],subject='other')]
    result=native_exposure_endpoints({'runs':[{'species':'Human','result':{'series':rows}}]})
    assert [r['auc_tissue_plasma_ratio'] for r in result['tissue_plasma_ratios']]==[2,2]
    assert len(result['refused_ratios'])==1 and result['refused_ratios'][0]['subject_identifier']=='other'
    assert all(r['time_ratios'][0]['ratio'] is None for r in result['tissue_plasma_ratios'])
