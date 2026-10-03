"""Internal-review gate adversaries; these synthetic examples are not clinical validation."""
import pytest

from cgr.pulsate_api.research_qualification import GATES, research_review, replay_benchmark
from cgr.pulsate_api.virtual_organism import canonical,digest


def package():
    raw=canonical({'source':'Synthetic contract fixture, not biological evidence'})
    sources={digest(raw):raw}
    data={'schema':'pulsate.research-qualification/v1','version':'fixture','reviewer':'Internal synthetic fixture',
        'review_basis':'Synthetic software validation only','model_sha256':'a'*64,
        'independent_scientific_review':False,'clinical_qualification':False,'clinical_inference':False,
        'equations_provenance':'Test','parameter_provenance':'Test','unit_conversions':{'test':1},
        'translation_rules':'Test','applicability_domain':'Test','uncertainty':'Test','limitations':['Test only'],
        'gates':{name:{'status':'passed','basis':'Test only','evidence_sha256':[digest(raw)]} for name in GATES},
        'benchmarks':[]}
    return {'review_status':'research_validated','research_qualification':data,
        'research_qualification_sha256':digest(canonical(data))},sources


def test_research_label_cannot_replace_observed_benchmark():
    doc,sources=package()
    with pytest.raises(ValueError,match='observed benchmark'):
        research_review(doc,sources,model_sha256='a'*64)


@pytest.mark.parametrize('gate',GATES)
def test_any_missing_research_gate_refuses_transfer(gate):
    doc,sources=package();doc['research_qualification']['gates'][gate]['status']='missing'
    doc['research_qualification_sha256']=digest(canonical(doc['research_qualification']))
    with pytest.raises(ValueError,match='Critical research gate'):
        research_review(doc,sources,model_sha256='a'*64)


@pytest.mark.parametrize('claim',['independent_scientific_review','clinical_qualification','clinical_inference'])
def test_internal_review_cannot_claim_independent_or_clinical_qualification(claim):
    doc,sources=package();doc['research_qualification'][claim]=True
    doc['research_qualification_sha256']=digest(canonical(doc['research_qualification']))
    with pytest.raises(ValueError,match='not independent certification'):
        research_review(doc,sources,model_sha256='a'*64)


def test_research_package_tamper_and_wrong_revision_are_refused():
    doc,sources=package();doc['research_qualification']['unit_conversions']={'test':2}
    with pytest.raises(ValueError):research_review(doc,sources,model_sha256='a'*64)
    doc,sources=package()
    with pytest.raises(ValueError):research_review(doc,sources,model_sha256='b'*64)
    with pytest.raises(ValueError,match='absent or altered'):
        research_review(doc,{},model_sha256='a'*64)


def test_solver_agreement_is_not_an_observed_biological_benchmark():
    with pytest.raises(ValueError,match='Benchmark type'):
        replay_benchmark({'kind':'numerical_solver_agreement'}, {}, 'a'*64)


def benchmark_fixture(monkeypatch):
    # Synthetic exact-source replay fixture only. Its mocked solver is never
    # counted as biological or real Human numerical acceptance evidence.
    import cgr.pulsate_api.mechanistic_models as mechanistic
    monkeypatch.setattr(mechanistic,'verify_simulation',lambda report,raw:{'passed':True,'scope':'Synthetic test only'})
    model=b'Synthetic SBML byte-identity fixture, not a physiological model'
    model_sha=digest(model)
    context={'species':'Human','tissue':'Synthetic test tissue','time_unit':'s','unit':'test_unit',
        'endpoint':'[X]','experimental_conditions':'Synthetic contract values, not an actual experiment'}
    original=canonical(dict(context,points=[{'time':0,'value':1.},{'time':1,'value':2.}]))
    source_sha=digest(original)
    report=canonical({'model':dict(context,sha256=model_sha,outputs={'[X]':'test_unit'}),
        'curves':[{'condition':'baseline','columns':['time','[X]'],'values':[[0,1.],[1,2.]]}]})
    sources={model_sha:model,source_sha:original,digest(report):report}
    trace={'kind':'original_primary_record','url':'https://pmc.ncbi.nlm.nih.gov/articles/synthetic-contract-fixture/',
        'sha256':source_sha,'record_identifier':'Synthetic test, not a real paper','semantic_context':context,
        'semantic_review_basis':'Synthetic replay test only',
        'context_pointers':{key:{'pointer':[field],'expected':context[field]}
            for key,field in [('species','species'),('endpoint','endpoint'),('experimental_context','experimental_conditions'),('units','unit')]}}
    benchmark={'kind':'observed_biological','identifier':'Synthetic contract test ONLY',
        'error_budget_basis':'Predeclared zero-error fixture, not a biological threshold',
        'maximum_absolute_error':0.,'comparison_method':'linear_on_native_time_grid',
        'context':context,'limitations':['Synthetic values and mock solver, not clinical qualification'],
        'result_sha256':digest(report),'output':'[X]','unit':'test_unit','condition':'baseline',
        'points':[{'time':i,'value':i+1.,'original_source':trace,
            'time_pointer':['points',i,'time'],'value_pointer':['points',i,'value']} for i in range(2)]}
    return benchmark,sources,model_sha


def test_benchmark_requires_source_replay_and_fresh_numerical_check(monkeypatch):
    benchmark,sources,sha=benchmark_fixture(monkeypatch)
    receipt=replay_benchmark(benchmark,sources,sha)
    assert receipt['maximum_absolute_error']==0.
    assert len(receipt['comparison'])==2 and not receipt['independent_scientific_review']
    benchmark['points'][1]['value']=3.
    with pytest.raises(ValueError,match='numerical datum'):replay_benchmark(benchmark,sources,sha)


def test_duplicate_observation_cannot_count_as_two_independent_points(monkeypatch):
    benchmark,sources,sha=benchmark_fixture(monkeypatch)
    benchmark['points'][1]=benchmark['points'][0]
    with pytest.raises(ValueError,match='distinct ordered'):replay_benchmark(benchmark,sources,sha)


@pytest.mark.parametrize('context_field',['species','tissue','time_unit','endpoint','unit'])
def test_observed_benchmark_requires_identical_scientific_context(monkeypatch,context_field):
    benchmark,sources,sha=benchmark_fixture(monkeypatch)
    benchmark['context']=dict(benchmark['context'],**{context_field:'Wrong context'})
    with pytest.raises(ValueError,match='context'):replay_benchmark(benchmark,sources,sha)


@pytest.mark.parametrize('evidence',[[], 'a'*64, ['not-a-hash'], ['a'*64,'a'*64], [['unhashable-pointer']]])
def test_research_gate_needs_a_bounded_unambiguous_hash_manifest(evidence):
    doc,sources=package()
    doc['research_qualification']['gates'][GATES[0]]['evidence_sha256']=evidence
    doc['research_qualification_sha256']=digest(canonical(doc['research_qualification']))
    with pytest.raises(ValueError,match='Critical research gate'):research_review(doc,sources,model_sha256='a'*64)


def converted_benchmark_fixture(monkeypatch):
    benchmark, sources, sha = benchmark_fixture(monkeypatch)
    context = dict(benchmark['context'], unit='umol/l')
    report = canonical({'model': dict(context, sha256=sha, outputs={'[X]': 'umol/l'}),
        'curves': [{'condition': 'baseline', 'columns': ['time', '[X]'], 'values': [[0, 1.], [60, 2.]]}]})
    original = canonical(dict(context, original_time_unit='min', original_value_unit='nM',
        points=[{'time': 0, 'value': 1000.}, {'time': 1, 'value': 2000.}]))
    sources[digest(original)] = original; sources[digest(report)] = report
    benchmark.update(context=context, unit='umol/l', result_sha256=digest(report))
    for i, point in enumerate(benchmark['points']):
        source = point['original_source']
        source.update(sha256=digest(original), semantic_context=context)
        source['context_pointers']['units'] = {'pointer': ['original_value_unit'], 'expected': 'nM'}
        source['context_pointers']['time_units'] = {'pointer': ['original_time_unit'], 'expected': 'min'}
        point.update(time=60 * i, value=i + 1., original_time=i, original_time_unit='min',
            original_value=1000. * (i + 1), original_value_unit='nM')
    return benchmark, sources, sha


def test_original_benchmark_units_are_replayed_before_native_conversion(monkeypatch):
    benchmark, sources, sha = converted_benchmark_fixture(monkeypatch)
    receipt = replay_benchmark(benchmark, sources, sha)
    assert receipt['maximum_absolute_error'] == 0
    assert receipt['comparison'][1]['time'] == 60
    assert receipt['comparison'][1]['normalization']['original_value'] == 2000
    assert receipt['comparison'][1]['normalization']['original_value_unit'] == 'nM'


@pytest.mark.parametrize('field,value', [('original_time', 2), ('original_value', 2100),
    ('original_time_unit', 'h'), ('original_value_unit', 'M'), ('value', 3.)])
def test_original_benchmark_conversion_cannot_hide_tampering(monkeypatch, field, value):
    benchmark, sources, sha = converted_benchmark_fixture(monkeypatch)
    benchmark['points'][1][field] = value
    with pytest.raises(ValueError):
        replay_benchmark(benchmark, sources, sha)


def test_mass_concentration_cannot_be_silently_relabelled_as_molar(monkeypatch):
    benchmark, sources, sha = converted_benchmark_fixture(monkeypatch)
    benchmark['points'][1]['original_value_unit'] = 'mg/l'
    with pytest.raises(ValueError):
        replay_benchmark(benchmark, sources, sha)


def test_partial_normalization_contract_and_missing_original_units_are_refused(monkeypatch):
    benchmark, sources, sha = converted_benchmark_fixture(monkeypatch)
    del benchmark['points'][0]['original_value_unit']
    with pytest.raises(ValueError):
        replay_benchmark(benchmark, sources, sha)
    benchmark, sources, sha = converted_benchmark_fixture(monkeypatch)
    del benchmark['points'][0]['original_source']['context_pointers']['time_units']
    with pytest.raises(ValueError):
        replay_benchmark(benchmark, sources, sha)


def test_original_units_cannot_manufacture_a_scale_or_swap_time_and_amount():
    from cgr.pulsate_api.primary_measurements import normalize_value
    assert normalize_value(1000., 'nM', 'mole/litre') == pytest.approx(1e-6)
    assert normalize_value(1., 'min', 's') == 60
    for original, native in [('nM', 's'), ('h', 'umol/l'), ('mg/l', 'mole/litre'),
            ('unknown_native_substance', 'mole/litre')]:
        with pytest.raises(ValueError):
            normalize_value(1., original, native)
