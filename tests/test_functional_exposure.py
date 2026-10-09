import copy
import pytest
from cgr.pulsate_api.functional_exposure import native_series,read_summary
from cgr.pulsate_api.virtual_organism import canonical,digest

def test_native_curve_basis_and_lineage_are_not_inferred_from_plasma_binding():
    curves=[{'compartment':c,'organ':'Heart','times_h':[0.,1.],
        'values_umol_l':[0.,1.], 'subject_identifier':'subject-a','path':'original/'+c}
        for c in ('Tissue','Interstitial Unbound','Intracellular Unbound')]
    result={'series':curves,'population_physiology':{'sha':'reviewed native'},'receipt':{'sha':'native'}}
    exposure={'runs':[{'scenario_identifier':'conditional-a','species':'Human','result':result}]}
    original=copy.deepcopy(exposure)
    series=native_series(exposure)
    assert exposure==original
    assert [s['concentration_basis'] for s in series]==['total','unbound','unbound']
    assert all(s['unit']=='umol/l' and s['exposure_case_identifier']=='conditional-a' for s in series)
    assert all(s['native_population_sha256']==digest(canonical(result['population_physiology'])) for s in series)
    assert all(s['native_receipt_sha256']==digest(canonical(result['receipt'])) for s in series)
    assert all(s['subject_identifier']=='subject-a' for s in series)


def test_dense_waveforms_do_not_erase_the_answer_or_change_downloadable_scientific_evidence():
    response={'curves':[{'condition':'baseline','columns':['time','V'],
        'values':[[i/20.,-80.12345678901234] for i in range(20001)]}],
        'response':{'V':{'final_difference':0.123456789}},'model':{'sha256':'synthetic'}}
    response['verification']={'passed':True,'report_sha256':digest(canonical(response))}
    document={'functional_exposure':{'functional_models':[{'status':'computed','result':response}]}}
    payload=canonical(document)
    assert len(payload)>100000
    summary=read_summary(payload,'virtual_organism_assessment',100000)
    result=summary['functional_exposure']['functional_models'][0]['result']
    assert result['response']==response['response']
    assert result['verification']==response['verification']
    assert 'curves' not in result
    assert result['waveform_evidence']['curves'][0]['sample_count']==20001
    assert canonical(document)==payload and len(response['curves'][0]['values'])==20001
    with pytest.raises(ValueError,match='read limit'):
        read_summary(payload,'unrelated_artifact',100000)
    response['curves'][0]['values'][0][1]=0.
    with pytest.raises(ValueError,match='exact numerical replay'):
        read_summary(canonical(document),'virtual_organism_assessment',100000)


def test_full_large_evidence_reference_is_verified_before_read_only_projection():
    response={'curves':[{'condition':'baseline','columns':['time','V'],
        'values':[[i/20.,-80.] for i in range(20001)]}],
        'response':{'V':{'final_difference':0.12345678901234567}},'model':{'sha256':'synthetic'}}
    response['verification']={'passed':True,'report_sha256':digest(canonical(response))}
    document={'functional_models':[{'status':'computed','result':response}]}
    payload=canonical(document)
    summary=read_summary(payload,'native_functional_exposure',100000,
        expected_sha256=digest(payload),expected_byte_size=len(payload))
    assert summary['functional_models'][0]['result']['response']==response['response']
    assert canonical(document)==payload
    for changes in ({'expected_sha256':'0'*64}, {'expected_byte_size':len(payload)-1}):
        with pytest.raises(ValueError,match='full payload hash/size'):
            read_summary(payload,'native_functional_exposure',100000,**changes)


def test_large_non_waveform_evidence_still_refuses():
    with pytest.raises(ValueError,match='summary exceeds'):
        read_summary(canonical({'unrelated_values':['x'*100001]}),'virtual_organism_assessment',100000)


def test_large_aligned_exposure_arrays_are_externalized_without_endpoint_or_peak_loss():
    times=[i/1000 for i in range(24001)]
    values=[i/12345 for i in range(24001)]
    values[101]=99.1234567890123
    curve={'times_h':times,'values_umol_l':values,'unit':'umol/l',
        'metrics':{'auc_0_t':12.1234567890123}}
    document={'drug_parameter_uncertainty':{'scenarios':[{'series':[curve]}]},
        'functional_models':[],'raw_padding_curves':[copy.deepcopy(curve) for _ in range(8)]}
    payload=canonical(document)
    assert len(payload)>4*1024*1024
    projected=read_summary(payload,'virtual_organism_assessment',4*1024*1024,
        expected_sha256=digest(payload),expected_byte_size=len(payload))
    small=projected['drug_parameter_uncertainty']['scenarios'][0]['series'][0]
    assert len(canonical(projected))<4*1024*1024
    assert small['times_h'][0]==0 and small['times_h'][-1]==24
    assert max(small['values_umol_l'])==max(values)
    assert small['metrics']==curve['metrics']
    assert small['display_projection']['original_sample_count']==24001
    assert small['display_projection']['externalized_fields']==['times_h','values_umol_l']
    assert canonical(document)==payload


def test_answer_preserves_every_verified_scenario_protocol_and_exact_endpoint():
    """Synthetic assembly values, not a scientific acceptance benchmark."""
    from types import SimpleNamespace
    from cgr.pulsate_api.scientific_runtime import ScientistResultAssembler
    activity={'target_accession':'P00001','functional_direction':'blocker','kind':'IC50',
        'value':.123,'classification':'measured','assay_context':'synthetic-protocol',
        'concentration_basis':'unbound','uncertainty':'Synthetic translation assumption',
        'functional_assay_sha256':'a'*64}
    models=[]
    for i in range(8):
        models.append({'status':'computed','exposure_case_identifier':f'synthetic-case-{i}',
            'activity_sha256':digest(canonical(activity)), 'result':{
                'model':{'identifier':'synthetic-cell','name':'Synthetic single cell'},
                'perturbation':{'parameter':'synthetic.conductance','transfer_receipt':{
                    'qualification_state':'exploratory_only','candidate_decision_authority':False}},
                'response':{'V.APD90':{'baseline_final':200.1234567890123,
                    'perturbed_final':201.1234567890123+i,'final_difference':1.+i,'unit':'ms'}},
                'limitation':'Synthetic single cell, not clinical QTc.'}})
    assessment={'candidate':{'name':'Synthetic','pubchem_cid':None,'inchikey':'synthetic-identity'},
        'request':{'dose':1.,'dose_unit':'mg','route':'Intravenous','administration_times_h':[0.],'duration_h':24.},
        'status':'exposure_supported','comparison':{'subjects':[]},'missing':[],
        'verification':{'verification_scope':'Synthetic integrity only'},'evidence_quality':{},
        'computation_selection':{'reason':'Synthetic classical fixture'},'assumptions':[],'limitations':[],
        'exposure_relevance':{'reason':'Not a clinical finding'},
        'functional_exposure':{'functional_activity_prediction':{'coverage':{
            'supported_model_targets':1,'unsupported_model_targets':1,'accepted_candidate_targets':0},'targets':[]},
            'verification':{'scope':'Synthetic numerical replay'},'quantitative_activity':[activity],
            'exposure_activity':[{'target_accession':'P00001','status':'refused','reason':'Incompatible assay basis'}],
            'functional_models':models,'functional_transfer_refusals':[{'reason':'No exact mapping'}],
            'scope':'Research only'}}
    assembler=ScientistResultAssembler()
    assembler._read_json_evidence=lambda record,kind:(assessment,'verified-fixture') if kind=='virtual_organism_assessment' else None
    objective=SimpleNamespace(research_requirements=None,task_type='composed_research',assumptions=())
    record=SimpleNamespace(evidence_artifact_identifiers=(),verified_scientific_summaries=(),verified=True,limitations=())
    statements=assembler._allowed_statements(objective,record,(),())
    text=' '.join(s['text'] for s in statements)
    for i in range(8):
        assert f'synthetic-case-{i}' in text and f'delta {1.+i!r} ms' in text
    assert 'baseline 200.1234567890123' in text
    assert 'synthetic-protocol' in text and 'evidence classification measured' in text
    assert 'simulated through 24.0 h' in text
    assert 'Exposure/activity comparison refused' in text
    assert 'no independent candidate-decision authority' in text
    assert 'not a confidence interval, population interval, clinical QTc or safety prediction' in text
    assert 'CLINICAL INFERENCE: not established' in text
    assert all(s['mandatory'] for s in statements if 'Model output' in s['text'])


def test_corrupt_existing_physiology_cannot_silently_become_an_exposure_only_answer():
    from types import SimpleNamespace
    from cgr.pulsate_api.scientific_runtime import ScientistResultAssembler,ScientificCapabilityFailure
    reference=SimpleNamespace(artifact_type='virtual_organism_assessment',artifact_identifier='synthetic',
        content_sha256='0'*64,byte_size=2)
    assembler=ScientistResultAssembler(payload_reader=SimpleNamespace(read=lambda ref:b'{}'))
    with pytest.raises(ScientificCapabilityFailure,match='bounded integrity'):
        assembler._read_json_evidence(SimpleNamespace(artifact_references=[reference]),'virtual_organism_assessment')
