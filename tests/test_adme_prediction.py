"""Synthetic contracts/adversarial tests, never experimental accuracy evidence."""
import copy
import json
from pathlib import Path

import pytest

from cgr.pulsate_api.adme_prediction import (FEATURES, VERSION, dossier_scenarios, forest_value, merge_parameter, reconcile_dossiers,
    molecular_features, predict_dossiers, verify_prediction)
from cgr.pulsate_api.virtual_organism import PBPKParameter, CompoundDossier, PBPKRequest, canonical, digest, dossier_requirements


def model_fixture(tmp_path, *, endpoint='fraction_unbound', species='Human', value=-1):
    identity, properties, _, fp = molecular_features('CCO')
    model = {'schema_version': VERSION, 'features': FEATURES, 'endpoint': endpoint, 'species': species,
        'unit': 'log10(fraction)', 'training': {'source_url': 'https://example.test/synthetic', 'license': 'synthetic fixture only'},
        'calibration': {'absolute_error_quantile': .4, 'coverage_target': .9},
        'domain': {'training_fingerprints': [fp.ToBitString()], 'training_graph_hashes': [],
            'descriptor_ranges': {k: [-10000, 10000] for k in FEATURES['descriptors']}, 'similarity_threshold': .1},
        'trees': [{'left': [-1], 'right': [-1], 'feature': [-2], 'threshold': [-2.], 'value': [value]}]}
    payload = canonical(model)
    path = tmp_path / f'model-{species}.json'
    path.write_bytes(payload)
    return {'path': path.name, 'sha256': digest(payload), 'endpoint': endpoint, 'species': species}, model


def manifest_fixture(tmp_path, models):
    path = tmp_path / 'manifest.json'
    path.write_bytes(canonical({'schema_version': 'pulsate.adme-model-manifest/v1', 'models': models}))
    return path


def identity():
    graph,_,_,_ = molecular_features('CCO')
    return dict(graph, name='generated-X')


def test_new_graph_without_model_is_partial_not_invented(monkeypatch):
    monkeypatch.delenv('PULSATE_ADME_MODEL_MANIFEST', raising=False)
    result = predict_dossiers(identity(), ('Human', 'Rat'))
    assert result['status'] == 'prediction_unavailable'
    for doc in result['dossiers']:
        d = CompoundDossier.model_validate(doc)
        assert d.parameters['molecular_weight'].classification == 'calculated'
        assert d.ionization_status == 'missing'
        assert d.parameters['hepatic_clearance'].value is None
        assert d.parameters['renal_clearance'].value is None
        assert dossier_requirements(d, 'Intravenous')
    assert verify_prediction(result, identity(), ('Human','Rat'))['passed']


@pytest.mark.parametrize('smiles', ['CCO.O', '[Na+]', 'not-a-graph', '[Fe]'])
def test_invalid_or_unsupported_graph_does_not_get_qsar(smiles):
    with pytest.raises(ValueError): molecular_features(smiles)


def test_species_models_cannot_be_silently_copied(tmp_path):
    entry,_ = model_fixture(tmp_path)
    manifest = manifest_fixture(tmp_path, [entry])
    result = predict_dossiers(identity(), ('Human','Rat'), manifest=manifest)
    human, rat = result['dossiers']
    assert human['parameters']['fraction_unbound']['classification'] == 'predicted'
    assert human['parameters']['fraction_unbound']['value'] == pytest.approx(.1)
    assert rat['parameters']['fraction_unbound']['value'] is None
    assert not rat['adme_parameters']
    assert verify_prediction(result, identity(), ('Human','Rat'), manifest=manifest)['passed']


@pytest.mark.parametrize('field', ['value', 'unit', 'prediction', 'interval'])
def test_model_number_units_provenance_interval_mutations_are_blocked(tmp_path, field):
    entry,_ = model_fixture(tmp_path)
    manifest = manifest_fixture(tmp_path, [entry])
    result = predict_dossiers(identity(), ('Human',), manifest=manifest)
    result['dossiers'][0]['parameters']['fraction_unbound'][field] = 'forged'
    with pytest.raises(ValueError, match='replay mismatch'):
        verify_prediction(result, identity(), ('Human',), manifest=manifest)


def test_model_hash_and_path_trust_are_checked(tmp_path):
    entry,_ = model_fixture(tmp_path)
    manifest = manifest_fixture(tmp_path, [entry])
    (tmp_path / entry['path']).write_bytes(b'{}')
    with pytest.raises(ValueError, match='hash mismatch'):
        predict_dossiers(identity(), ('Human',), manifest=manifest)
    entry['path'] = '../outside.json'
    (tmp_path.parent / 'outside.json').write_bytes(b'{}')
    manifest = manifest_fixture(tmp_path, [entry])
    with pytest.raises(ValueError, match='outside trusted'):
        predict_dossiers(identity(), ('Human',), manifest=manifest)


def test_evidence_precedence_preserves_measured_value_and_conflict(tmp_path):
    entry,_ = model_fixture(tmp_path)
    manifest = manifest_fixture(tmp_path, [entry])
    measured = PBPKParameter(value=.7, unit='fraction', classification='measured', source='Synthetic assay', method='Fixture only')
    d = CompoundDossier(name='generated-X', smiles='CCO', inchikey=identity()['inchikey'], species='Human', binding_partner='Albumin',
        parameters={'fraction_unbound': measured}, ionization=(), ionization_source='Synthetic review')
    result = predict_dossiers(identity(), ('Human',), (d,), manifest=manifest)
    out = result['dossiers'][0]
    assert out['parameters']['fraction_unbound']['value'] == .7
    assert out['parameters']['fraction_unbound']['classification'] == 'measured'
    assert out['adme_parameters']['fraction_unbound']['classification'] == 'predicted'
    assert out['parameter_conflicts'][0]['parameter'] == 'fraction_unbound'


def test_reviewed_measurement_is_not_hidden_by_incomplete_supplied_dossier():
    missing = PBPKParameter(unit='fraction',classification='missing',source='Missing',method='Unknown')
    measured = PBPKParameter(value=.6,unit='fraction',classification='measured',source='Synthetic assay',method='Fixture')
    kwargs = dict(name='X',smiles='CCO',inchikey=identity()['inchikey'],species='Human',binding_partner='Albumin',ionization=(),ionization_source='Synthetic review')
    first = CompoundDossier(**kwargs,parameters={'fraction_unbound':missing})
    reviewed = CompoundDossier(**kwargs,parameters={'fraction_unbound':measured})
    assert reconcile_dossiers(first,reviewed).parameters['fraction_unbound'] == measured
    conflicting = first.model_copy(update={'parameters':{'fraction_unbound':measured.model_copy(update={'value':.3})}})
    result = reconcile_dossiers(conflicting,reviewed)
    assert result.parameters['fraction_unbound'].value == .3
    assert result.parameter_conflicts[0]['alternative']['value'] == .6


def test_negative_literal_dose_cannot_be_promoted_to_positive_control():
    request = PBPKRequest(entity_name='generated-X',dose=1,dose_unit='mg/kg',route='Intravenous',duration_h=2,
        quotes={'dose':'-1 mg/kg','dose_unit':'-1 mg/kg','route':'intravenous','duration_h':'2 h'})
    with pytest.raises(ValueError): request.check_grounding('generated-X -1 mg/kg intravenous for 2 h')
    from types import SimpleNamespace
    from cgr.pulsate_api.virtual_organism import interpret_pbpk_request
    assert interpret_pbpk_request('generated-X -1 mg/kg intravenous for 2 h',SimpleNamespace(complete=lambda messages:request.model_dump_json())) is None


def test_missing_critical_translation_remains_missing_with_qsar(tmp_path):
    entry,_ = model_fixture(tmp_path)
    out = predict_dossiers(identity(), ('Human',), manifest=manifest_fixture(tmp_path,[entry]))['dossiers'][0]
    assert out['parameters']['fraction_unbound']['interval'][0] > 0
    assert out['parameters']['hepatic_clearance']['value'] is None
    assert out['parameters']['intestinal_permeability']['value'] is None
    assert out['parameters']['solubility_ph']['value'] is None
    assert out['ionization_status'] == 'missing'


def test_unknown_ph_prediction_cannot_be_relabelled_by_separate_supplied_ph(tmp_path):
    entry, model = model_fixture(tmp_path, endpoint='aqueous_solubility', species=None, value=-2)
    model['unit'] = 'log10(mol/l)'
    payload = canonical(model)
    (tmp_path / entry['path']).write_bytes(payload)
    entry['sha256'] = digest(payload)
    supplied = CompoundDossier(name='generated-X', smiles='CCO', inchikey=identity()['inchikey'], species='Human',
        binding_partner='Albumin', parameters={'solubility_ph': PBPKParameter(value=7.4, unit='pH', classification='sourced',
            source='Synthetic pH fixture, not the training assay', method='Fixture')}, ionization=(), ionization_source='Synthetic review')
    out = predict_dossiers(identity(), ('Human',), (supplied,), manifest=manifest_fixture(tmp_path, [entry]))
    dossier = CompoundDossier.model_validate(out['dossiers'][0])
    assert dossier.adme_parameters['aqueous_solubility'].value > 0
    assert dossier.adme_parameters['aqueous_solubility'].prediction['translation']['reference_ph'] is None
    assert dossier.parameters['solubility'].value is None
    assert 'solubility' in dossier_requirements(dossier, 'Intravenous')
    assert out['native_translation_audit'][0]['solubility']['status'] == 'unresolved'


def test_graph_without_normalized_endpoint_does_not_request_arbitrary_subject_weight():
    out = predict_dossiers(identity(), ('Human',))
    dossier = CompoundDossier.model_validate(out['dossiers'][0])
    assert 'reference_weight' not in dossier_requirements(dossier, 'Intravenous')
    cl = PBPKParameter(value=1, unit='ml/min/kg', classification='measured', source='Synthetic experiment', method='Fixture')
    dossier = dossier.model_copy(update={'parameters': dict(dossier.parameters, hepatic_clearance=cl)})
    assert 'reference_weight' in dossier_requirements(dossier, 'Intravenous')


@pytest.mark.parametrize('parameter,endpoint', [('hepatic_clearance','hepatocyte_intrinsic_clearance'), ('renal_clearance','glomerular_filtration'), ('solubility','aqueous_solubility')])
def test_valid_units_and_domain_do_not_make_the_wrong_endpoint_native(parameter, endpoint):
    value = PBPKParameter(value=1, unit='mg/l' if parameter == 'solubility' else 'ml/min/kg', classification='predicted',
        source='Synthetic endpoint fixture', method='Not a scientific translation', interval=(.5,2),
        prediction={'endpoint':endpoint, 'applicability':{'status':'in_domain'}, 'translation':None})
    dossier = CompoundDossier(name='X',smiles='CCO',inchikey=identity()['inchikey'],species='Human',binding_partner='Albumin',
        parameters={parameter:value},ionization=(),ionization_source='Synthetic review')
    assert any('does not establish' in reason for reason in dossier_requirements(dossier,'Intravenous'))


@pytest.mark.parametrize('pka', [-1000, 1000, float('inf'), float('nan'), '7', True])
def test_unsupported_ionization_values_are_blocked(pka):
    dossier = CompoundDossier(name='X',smiles='CCO',inchikey=identity()['inchikey'],species='Human',binding_partner='Albumin',
        parameters={},ionization=({'Type':'Base','Pka':pka},),ionization_source='Synthetic review')
    with pytest.raises(ValueError, match='Unsupported ionization'):
        dossier_requirements(dossier,'Intravenous')


@pytest.mark.parametrize('mutation', ['unit_1000', 'species', 'fu', 'native_audit'])
def test_translation_audit_and_physical_inputs_are_replay_verified(tmp_path, mutation):
    entry,_ = model_fixture(tmp_path)
    manifest = manifest_fixture(tmp_path,[entry])
    out = predict_dossiers(identity(), ('Human',), manifest=manifest)
    if mutation == 'unit_1000': out['dossiers'][0]['parameters']['fraction_unbound']['value'] *= 1000
    elif mutation == 'species': out['dossiers'][0]['species'] = 'Rat'
    elif mutation == 'fu': out['dossiers'][0]['parameters']['fraction_unbound']['value'] = 1.1
    else: out['native_translation_audit'][0]['hepatic']['value'] = 1
    with pytest.raises(ValueError,match='replay mismatch'):
        verify_prediction(out,identity(),('Human',),manifest=manifest)


def test_total_clearance_is_not_added_to_hepatic_and_renal_components():
    value = PBPKParameter(value=1,unit='ml/min/kg',classification='measured',source='Synthetic',method='Fixture')
    dossier = CompoundDossier(name='X',smiles='CCO',inchikey=identity()['inchikey'],species='Human',binding_partner='Albumin',
        parameters={'total_clearance':value,'hepatic_clearance':value,'renal_clearance':value},ionization=(),ionization_source='Synthetic')
    with pytest.raises(ValueError,match='Unsupported PBPK parameter'):
        dossier_requirements(dossier,'Intravenous')


def test_experimental_weight_is_not_virtual_subject_weight():
    from cgr.pulsate_api.virtual_organism import dossier_snapshot
    _, properties, _, _ = molecular_features('CCO')
    points = {'molecular_weight': (properties['MolWt'],'g/mol'), 'logp': (properties['MolLogP'],'Log Units'),
        'fraction_unbound':(.5,'fraction'), 'solubility':(100,'mg/l'), 'solubility_ph':(7,'pH'),
        'hepatic_clearance':(1,'ml/min/kg'), 'renal_clearance':(.1,'ml/min/kg'), 'reference_weight':(123,'kg')}
    parameters = {name:PBPKParameter(value=value,unit=unit,classification='sourced',source='Synthetic test, not physiology or compound evidence',method='Fixture')
        for name,(value,unit) in points.items()}
    d = CompoundDossier(name='X',smiles='CCO',inchikey=identity()['inchikey'],species='Human',binding_partner='Albumin',
        parameters=parameters,ionization=(),ionization_source='Synthetic reviewed neutral assumption')
    r = PBPKRequest(entity_name='X',dose=1,dose_unit='mg/kg',route='Intravenous',duration_h=2)
    native = dossier_snapshot(d,r)
    assert 'Weight' not in native['Individuals'][0]['OriginData']
    for process in native['Compounds'][0]['Processes']:
        assert next(p for p in process['Parameters'] if p['Name']=='Body weight')['Value'] == 123


@pytest.mark.parametrize('unit', ['ul/min/10^6 cells', 'L/h', 'log10(ul/min/10^6 cells)'])
def test_cell_normalized_or_litre_hour_rate_is_not_native_ml_min_kg(unit):
    value = PBPKParameter(value=1,unit=unit,classification='sourced',source='Synthetic assay',method='Fixture')
    d = CompoundDossier(name='X',smiles='CCO',inchikey=identity()['inchikey'],species='Human',binding_partner='Albumin',
        parameters={'hepatic_clearance':value},ionization=(),ionization_source='Synthetic review')
    with pytest.raises(ValueError,match='Unsupported PBPK parameter or unit'):
        dossier_requirements(d,'Intravenous')


@pytest.mark.parametrize('mutation', ['clint_1000','blood_instead_of_plasma','filtration_instead_of_total'])
def test_clearance_receipt_substitutions_fail_independent_replay(tmp_path,mutation):
    entry,model = model_fixture(tmp_path,endpoint='hepatocyte_intrinsic_clearance',value=1.2)
    model['unit']='log10(ul/min/10^6 cells)'
    payload=canonical(model)
    (tmp_path/entry['path']).write_bytes(payload)
    entry['sha256']=digest(payload)
    manifest=manifest_fixture(tmp_path,[entry])
    out=predict_dossiers(identity(),('Human',),manifest=manifest)
    if mutation=='clint_1000': out['dossiers'][0]['adme_parameters']['hepatocyte_intrinsic_clearance']['value'] += 3
    elif mutation=='blood_instead_of_plasma': out['native_translation_audit'][0]['hepatic']['output_clearance_basis']='blood'
    else: out['native_translation_audit'][0]['renal']['total_clearance_established']=True
    with pytest.raises(ValueError,match='replay mismatch'):
        verify_prediction(out,identity(),('Human',),manifest=manifest)


def test_out_of_domain_prediction_blocks_native_dossier(tmp_path):
    entry,model = model_fixture(tmp_path)
    model['domain']['descriptor_ranges']['MolWt'] = [300,1000]
    payload = canonical(model)
    (tmp_path / entry['path']).write_bytes(payload)
    entry['sha256'] = digest(payload)
    out = predict_dossiers(identity(), ('Human',), manifest=manifest_fixture(tmp_path,[entry]))['dossiers'][0]
    assert out['parameters']['fraction_unbound']['prediction']['applicability']['status'] == 'out_of_domain'
    assert any('not in-domain' in s for s in dossier_requirements(CompoundDossier.model_validate(out), 'Intravenous'))


def test_native_sensitivity_preserves_interval_model_and_separates_population(tmp_path):
    entry,_ = model_fixture(tmp_path)
    out = predict_dossiers(identity(), ('Human',), manifest=manifest_fixture(tmp_path,[entry]))['dossiers'][0]
    d = CompoundDossier.model_validate(out)
    r = PBPKRequest(entity_name='generated-X', dose=1, dose_unit='mg/kg', route='Intravenous', duration_h=2, population_size=4)
    scenarios = dossier_scenarios(d,r)
    assert [s[0] for s in scenarios] == ['nominal','fraction_unbound-low','fraction_unbound-high']
    assert scenarios[0][3].population_size == 4 and scenarios[1][3].population_size == 1
    assert scenarios[1][2].parameters['fraction_unbound'].value == d.parameters['fraction_unbound'].interval[0]
    assert scenarios[1][4]['model_sha256'] == entry['sha256']


def test_explicit_bounded_dose_sweep_is_grounded_not_therapeutic():
    r = PBPKRequest(entity_name='generated-X', dose=.1,dose_unit='mg/kg',route='Intravenous',duration_h=2,dose_sweep=(.1,1,3),
        quotes={'dose': '.1 mg/kg', 'dose_unit': '.1 mg/kg','route':'intravenous','duration_h':'2 h','dose_sweep':'.1, 1 and 3 mg/kg'})
    r.check_grounding('generated-X .1 mg/kg intravenous 2 h with .1, 1 and 3 mg/kg')
    assert len(dossier_scenarios(None,r)) == 3
    with pytest.raises(ValueError): r.model_copy(update={'dose_sweep':(.1,1,8)}).check_grounding('generated-X .1 mg/kg intravenous 2 h with .1, 1 and 3 mg/kg')
    with pytest.raises(ValueError): PBPKRequest(entity_name='generated-X',dose=1,dose_sweep=(1,2,3,4,5))


def test_model_cannot_silently_discard_explicit_dose_sweep():
    from types import SimpleNamespace
    from cgr.pulsate_api.virtual_organism import interpret_pbpk_request
    request = PBPKRequest(entity_name='generated-X',dose=.1,dose_unit='mg/kg',route='Intravenous',duration_h=2,
        quotes={'dose':'0.1 mg/kg','dose_unit':'0.1 mg/kg','route':'intravenous','duration_h':'2 h'})
    provider = SimpleNamespace(complete=lambda messages: request.model_dump_json())
    result = interpret_pbpk_request('generated-X 0.1 mg/kg intravenous for 2 h; dose sweep of 0.1, 1 and 3 mg/kg',provider)
    assert result.dose_sweep == (.1,1,3)
    assert result.dose == .1
    assert '0.1, 1 and 3 mg/kg' in result.quotes['dose_sweep']


def test_explicit_observation_and_population_scope_survive_lossy_extraction():
    from types import SimpleNamespace
    from cgr.pulsate_api.virtual_organism import interpret_pbpk_request
    request = PBPKRequest(entity_name='candidate-X',species=('Human','Rat'),dose=3,dose_unit='mg/kg',route='Intravenous',duration_h=10,infusion_minutes=10,
        population_size=4,quotes={'dose':'3 mg/kg','dose_unit':'mg/kg','route':'Intravenous','duration_h':'10 h','infusion_minutes':'10 min','population_size':'4','species':'both species'})
    provider = SimpleNamespace(complete=lambda messages:request.model_dump_json())
    text = 'candidate-X in a population of 4 human adults and a single reference Rat after 3 mg/kg intravenous infusion over 10 minutes, with a 16 h observation period.'
    result = interpret_pbpk_request(text,provider)
    assert result is not None
    assert result.duration_h == 16 and result.infusion_minutes == 10 and result.population_size == 4
    assert not result.missing()


def test_unbounded_or_cyclic_model_is_not_executed():
    model = {'schema_version':VERSION,'features':FEATURES,'trees':[{'left':[0],'right':[0],'feature':[0],'threshold':[.5],'value':[0]}]}
    with pytest.raises(ValueError, match='Cyclic'): forest_value(model,[0]*520)


def test_predicted_label_requires_numerical_provenance():
    with pytest.raises(ValueError): PBPKParameter(value=1,unit='fraction',classification='predicted',source='LLM',method='guess')


def test_adme_and_pbpk_requirements_compose_not_target_screening():
    from cgr.pulsate_api.scientific_requirements import ScientificRequirementProposal, ScientificRequirement, validate_requirement_proposal
    from cgr.pulsate_api.scientific_objectives import compile_scientific_objective, plan_scientific_objective
    text = 'Predict ADME for generated-X and simulate 1 mg/kg intravenous over 2 h.'
    request = PBPKRequest(entity_name='generated-X',dose=1,dose_unit='mg/kg',route='Intravenous',duration_h=2,
        quotes={'dose':'1 mg/kg','dose_unit':'1 mg/kg','route':'intravenous','duration_h':'2 h'})
    proposal = ScientificRequirementProposal(proposal_identifier='proposal-adme',summary='ADME and exposure',provider_kind='test',model_name='test',pbpk_request=request,
        requirements=(ScientificRequirement(operation='predict_adme',requested_output='adme_parameterization',supporting_quote='Predict ADME'),
                      ScientificRequirement(operation='simulate_organism_exposure',requested_output='virtual_organism_exposure',supporting_quote='simulate 1 mg/kg intravenous over 2 h')))
    requirements = validate_requirement_proposal(proposal,available_input_types=())
    plan = plan_scientific_objective(compile_scientific_objective(text,research_requirements=requirements))
    assert plan.steps[0].capability_name == 'pharmacokinetics.adme_predict'
    assert not any('dock' in s.capability_name for s in plan.steps)
