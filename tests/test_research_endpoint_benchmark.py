"""Synthetic endpoint contract adversaries, never Human validation evidence."""
import copy
import pytest
from cgr.pulsate_api.research_qualification import replay_benchmark
from cgr.pulsate_api.virtual_organism import canonical, digest


def fixture(monkeypatch):
    import cgr.pulsate_api.mechanistic_models as native
    calls=[]
    monkeypatch.setattr(native,'verify_simulation',lambda report,raw:calls.append(report) or {'passed':True,'scope':'Mock only'})
    model=b'Synthetic model byte identity, not biology'; sha=digest(model)
    protocol={'kind':'synthetic_unit_test_only'}
    context={'species':'Human','tissue':'Synthetic test tissue','time_unit':'ms','unit':'ms','endpoint':'[X].duration',
        'experimental_conditions':'Synthetic change from control, no biological validity',
        'native_protocol_sha256':digest(canonical(protocol))}
    original=canonical(dict(context,identity='Synthetic subject',points=[1.,2.]))
    sources={sha:model,digest(original):original}
    trace={'kind':'original_primary_record','url':'https://pmc.ncbi.nlm.nih.gov/articles/synthetic-contract-fixture/',
        'sha256':digest(original),'record_identifier':'Synthetic test only','semantic_context':context,
        'semantic_review_basis':'Mock fixture, not original biological observations',
        'context_pointers':{key:{'pointer':[field],'expected':value} for key,field,value in
            [('identity','identity','Synthetic subject'),('species','species',context['species']),
            ('endpoint','endpoint',context['endpoint']),('experimental_context','experimental_conditions',context['experimental_conditions']),
            ('units','unit','ms')]}}
    points=[]
    for i in range(2):
        perturbation={'fractions_remaining':[1.-i*.1,1.-i*.1],'source':'Synthetic only'}
        report=canonical({'model':{'sha256':sha,**context},'receipt':{'protocol':protocol},
            'perturbation':perturbation,'response':{context['endpoint']:{'unit':'ms','final_difference':float(i+1)}}})
        sources[digest(report)]=report
        points.append({'value':float(i+1),'original_source':copy.deepcopy(trace),'value_pointer':['points',i],
            'result_sha256':digest(report),'perturbation_sha256':digest(canonical(perturbation)),
            'subject_identifier':'Synthetic subject'})
    benchmark={'kind':'observed_biological','identifier':'Synthetic endpoint contract only','observed_definition':'change_from_control',
        'comparison_method':'native_response_endpoint','error_budget_basis':'Zero-error synthetic fixture; not a physiological threshold',
        'maximum_absolute_error':0.,'context':context,'limitations':['Mock solver and observations only'],
        'output':context['endpoint'],'unit':'ms','points':points}
    return benchmark,sources,sha,calls


def test_endpoint_replays_each_native_condition_and_exact_observation(monkeypatch):
    b,s,sha,calls=fixture(monkeypatch)
    result=replay_benchmark(b,s,sha)
    assert len(calls)==2 and result['maximum_absolute_error']==0.
    assert not result['independent_scientific_review'] and not result['clinical_inference']


@pytest.mark.parametrize('field,value',[('observed_definition','absolute_duration'),('unit','s'),
    ('kind','numerical_solver_agreement'),('maximum_absolute_error',float('inf')),('maximum_absolute_error',True)])
def test_endpoint_invalid_contract_refuses(monkeypatch,field,value):
    b,s,sha,_=fixture(monkeypatch);b[field]=value
    with pytest.raises(ValueError):replay_benchmark(b,s,sha)


@pytest.mark.parametrize('field',['species','tissue','time_unit','native_protocol_sha256','endpoint'])
def test_endpoint_wrong_native_context_refuses(monkeypatch,field):
    b,s,sha,_=fixture(monkeypatch);b['context']=dict(b['context'],**{field:'wrong'})
    with pytest.raises(ValueError):replay_benchmark(b,s,sha)


@pytest.mark.parametrize('field,value',[('value',9.),('subject_identifier','Another subject'),
    ('result_sha256','b'*64),('perturbation_sha256','b'*64),('original_value',9.),('original_value_unit','umol/l')])
def test_endpoint_tampered_observation_or_native_condition_refuses(monkeypatch,field,value):
    b,s,sha,_=fixture(monkeypatch);b['points'][0][field]=value
    with pytest.raises(ValueError):replay_benchmark(b,s,sha)


def test_duplicate_native_perturbation_is_not_a_second_benchmark(monkeypatch):
    b,s,sha,_=fixture(monkeypatch);b['points'][1]=copy.deepcopy(b['points'][0])
    with pytest.raises(ValueError,match='distinct native'):replay_benchmark(b,s,sha)


def test_missing_original_context_cannot_be_replaced_by_an_extraction_wrapper(monkeypatch):
    b,s,sha,_=fixture(monkeypatch);b['points'][0]['original_source']['kind']='local_extraction'
    with pytest.raises(ValueError,match='original-primary'):replay_benchmark(b,s,sha)


def test_true_discrepancy_refuses_instead_of_raising_the_budget(monkeypatch):
    b,s,sha,_=fixture(monkeypatch)
    import json
    report=json.loads(s[b['points'][0]['result_sha256']]);report['response'][b['output']]['final_difference']=1.25
    raw=canonical(report);s[digest(raw)]=raw;b['points'][0]['result_sha256']=digest(raw)
    with pytest.raises(ValueError,match='exceeds'):replay_benchmark(b,s,sha)


def test_separate_original_methods_source_can_define_csv_endpoint_units(monkeypatch):
    b,s,sha,_=fixture(monkeypatch)
    raw=canonical({'unit':'ms','scope':'Synthetic original-methods contract only'})
    s[digest(raw)]=raw
    for point in b['points']:
        point['original_source']['context_pointers']['units'].update(source_sha256=digest(raw),
            source_url='https://pmc.ncbi.nlm.nih.gov/articles/synthetic-methods/',source_format='json',
            pointer=['unit'],review_basis='Synthetic exact-source methods mapping')
    assert replay_benchmark(b,s,sha)['maximum_absolute_error']==0.
    s[digest(raw)]=b'Tampered units source'
    with pytest.raises(ValueError,match='absent or altered'):replay_benchmark(b,s,sha)


def test_compiled_endpoint_keeps_source_and_execution_bundle_hashes_distinct(monkeypatch):
    """Synthetic byte-identity contract; mocked replay is not native evidence."""
    import json
    b, s, sha, calls = fixture(monkeypatch)
    bundle = b'Synthetic compiled execution bundle, not a biological model'
    s[digest(bundle)] = bundle
    b['model_payload_sha256'] = digest(bundle)
    for point in b['points']:
        report = json.loads(s[point['result_sha256']])
        report['model']['format'] = 'cellml_compiled'
        raw = canonical(report)
        s[digest(raw)] = raw
        point['result_sha256'] = digest(raw)
    import cgr.pulsate_api.mechanistic_models as native
    monkeypatch.setattr(native, 'verify_simulation', lambda report, payload:
        calls.append(payload) or {'passed': payload == bundle, 'scope': 'Synthetic mocked replay only'})
    assert replay_benchmark(b, s, sha)['maximum_absolute_error'] == 0
    assert calls == [bundle, bundle]
    b.pop('model_payload_sha256')
    with pytest.raises(ValueError, match='execution bundle'):
        replay_benchmark(b, s, sha)
    b['model_payload_sha256'] = digest(bundle)
    s[digest(bundle)] = b'Changed native execution bundle'
    with pytest.raises(ValueError, match='absent or altered'):
        replay_benchmark(b, s, sha)
