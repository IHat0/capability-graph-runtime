"""Synthetic interface tests only; these fixtures are not sponsor measurements."""
import copy
import json
from datetime import date

import pytest
from rdkit import Chem

from cgr.pulsate_api.native_sensitivity import conditional_acquisition, design
from cgr.pulsate_api.prospective_dossier import select_regimen
from cgr.pulsate_api.prospective_evidence import ProspectivePolicy
from cgr.pulsate_api.sponsor_dossier import SCHEMA, SponsorInput, acquire_sponsor_dossier, load_sponsor_dossier
from cgr.pulsate_api.virtual_investigation import acquire_investigation_dossier, load_policy
from cgr.pulsate_api.virtual_organism import canonical, digest, dossier_requirements, CompoundDossier


def fixture(smiles='CCO'):
    identity = {'name': 'Synthetic contract graph', 'smiles': smiles,
        'inchikey': Chem.MolToInchiKey(Chem.MolFromSmiles(smiles))}
    # Explicit synthetic scenarios, not scientifically validated compound data.
    raw = {'levels': {'logp': [-1., 1.], 'fraction_unbound': [.1, .2],
        'solubility': [1., 2.], 'solubility_ph': [7.4], 'hepatic_clearance': [.1, .2],
        'renal_clearance': [.01, .02], 'reference_weight': [70.],
        'binding_partner': ['Albumin'], 'ionization': [[]]}}
    source = canonical(raw)
    units = {'logp': 'Log Units', 'fraction_unbound': 'fraction', 'solubility': 'mg/l',
        'solubility_ph': 'pH', 'hepatic_clearance': 'ml/min/kg', 'renal_clearance': 'ml/min/kg', 'reference_weight': 'kg'}
    records = []
    for role, levels in raw['levels'].items():
        context = {}
        if role.endswith('clearance'):
            context = {'concentration_basis': 'plasma', 'endpoint': role + '_plasma_clearance'}
        if role == 'solubility':
            context = {'reference_ph': 7.4, 'quantity_kind': 'apparent_solubility_at_reference_ph'}
        records.append({'identifier': 'synthetic-' + role, 'role': role,
            'provenance_class': 'sponsor_side_scenario_assumption', 'values': levels, 'unit': units.get(role),
            'source_sha256': digest(source), 'value_pointer': ['levels', role], 'endpoint_context': context,
            'rationale': 'Synthetic discrete design test, not real pharmaceutical parameter selection',
            'uncertainty': 'Synthetic scenario bounds, not confidence intervals'})
    doc = {'schema': SCHEMA, 'experiment_class': 'sponsor_side_prospective', 'reviewer': 'Synthetic fixture',
        'leakage_review': {'passed': True, 'outcomes_or_known_failure_mechanisms_included': False,
            'historical_private_dataset_claim': False, 'reviewer': 'Synthetic fixture',
            'method': 'Synthetic contract only', 'limitations': 'Not an actual leakage/biological validation'},
        'candidates': [{'inchikey': identity['inchikey'], 'species': 'Human', 'inputs': records}], 'sources': []}
    policy = ProspectivePolicy(cutoff=date(2000, 1, 1), cutoff_source='Synthetic public-history comparator')
    return identity, doc, {digest(source): source}, policy


def scenario_policy():
    return {'provenance': 'Synthetic dosing controls, not historical or therapeutic', 'controls': {
        'species': ['Human'], 'dose': .1, 'dose_unit': 'mg', 'route': 'Intravenous',
        'duration_h': 2., 'population_size': 2}}


def sensitivity_policy():
    return {'schema': 'pulsate.native-input-sensitivity/v1', 'method': 'cartesian_or_level_covering',
        'maximum_runs': 4, 'reviewer': 'Synthetic fixture', 'version': 'test',
        'scientific_justification': 'Synthetic coverage test', 'scope': 'Not biological validation'}


@pytest.mark.parametrize('smiles', ['CCO', 'CO', 'CCCO', 'CC(=O)O'])
def test_generic_graph_sponsor_ranges_are_not_measurements_or_a_nominal(smiles):
    identity, doc, sources, policy = fixture(smiles)
    acquired = acquire_sponsor_dossier(identity, 'Human', doc, sources, policy)
    assert acquired['schema'] == SCHEMA and acquired['dossier'] is None
    assert acquired['experiment_provenance']['historical_private_data_claim'] is False
    assert all(d['status'] == 'sponsor_side_scenario_assumption' for d in acquired['eligibility']['decisions'])
    assert select_regimen(identity, acquired, scenario_policy())['status'] == 'insufficient_parameterization'
    plan = design(acquired, sensitivity_policy())
    assert plan['nominal_withheld'] and not plan['exhaustive']
    for case in plan['cases']:
        conditional = conditional_acquisition(acquired, case)
        dossier = CompoundDossier.model_validate(conditional['dossier'])
        assert dossier.ionization_status == 'scenario'
        assert dossier.parameters['fraction_unbound'].classification == 'assumed'
        assert dossier.experiment_provenance['experiment_class'] == 'sponsor_side_prospective'
        assert not dossier_requirements(dossier, 'Intravenous')
        regimen = select_regimen(identity, conditional, scenario_policy())
        assert regimen['status'] == 'scenario_ready' and not regimen['historical_dose_claim']
        assert regimen['classification'] == 'sponsor_side_prospective'


def test_sponsor_source_value_tampering_and_identity_substitution_fail():
    identity, doc, sources, policy = fixture()
    doc['candidates'][0]['inputs'][0]['values'][0] = -2.
    with pytest.raises(ValueError, match='exact frozen source'):
        acquire_sponsor_dossier(identity, 'Human', doc, sources, policy)
    identity, doc, sources, policy = fixture()
    with pytest.raises(ValueError, match='identity'):
        acquire_sponsor_dossier(dict(identity, inchikey='not-the-graph'), 'Human', doc, sources, policy)
    with pytest.raises(ValueError, match='exactly one'):
        acquire_sponsor_dossier(identity, 'Rat', doc, sources, policy)


@pytest.mark.parametrize('changes', [
    {'provenance_class': 'sourced_measurement'}, {'provenance_class': 'sponsor_side_measured_development'},
    {'provenance_class': 'predicted_computational_input'}, {'provenance_class': 'sourced_foundational_information'},
    {'role': 'quantitative_activity'}, {'role': 'clinical_outcome'}, {'values': [.1]},
    {'values': [True, .2]}, {'unit': 'nM'}, {'values': [float('nan'), .2]},
])
def test_sponsor_scenarios_cannot_launder_other_evidence_classes(changes):
    _, doc, _, _ = fixture()
    record = next(r for r in doc['candidates'][0]['inputs'] if r['role'] == 'fraction_unbound')
    with pytest.raises(ValueError):
        SponsorInput.model_validate(dict(record, **changes))


def test_native_endpoint_semantics_are_not_weakened_by_sponsor_mode():
    identity, doc, sources, policy = fixture()
    record = next(r for r in doc['candidates'][0]['inputs'] if r['role'] == 'hepatic_clearance')
    record['endpoint_context']['concentration_basis'] = 'blood'
    with pytest.raises(ValueError, match='plasma clearance'):
        acquire_sponsor_dossier(identity, 'Human', doc, sources, policy)


def test_missing_is_preserved_not_an_execution_value():
    identity, doc, sources, policy = fixture()
    record = next(r for r in doc['candidates'][0]['inputs'] if r['role'] == 'hepatic_clearance')
    record.update(provenance_class='missing', values=[], endpoint_context={})
    raw = json.loads(next(iter(sources.values())))
    raw['levels']['hepatic_clearance'] = []
    payload = canonical(raw)
    old_sha = record['source_sha256']
    for item in doc['candidates'][0]['inputs']:
        item['source_sha256'] = digest(payload)
    sources = {digest(payload): payload}
    acquired = acquire_sponsor_dossier(identity, 'Human', doc, sources, policy)
    assert acquired['missing'] and 'hepatic_clearance' not in acquired['resolved_parameters']
    assert old_sha not in sources


def test_ensemble_preserves_all_levels_and_dossier_provenance():
    identity, doc, sources, policy = fixture()
    acquired = acquire_sponsor_dossier(identity, 'Human', doc, sources, policy)
    exhaustive = dict(sensitivity_policy(), maximum_runs=32)
    plan = design(acquired, exhaustive)
    assert plan['exhaustive'] and len(plan['cases']) == 32
    for case in plan['cases']:
        conditional = conditional_acquisition(acquired, case)
        assert conditional['dossier']['experiment_provenance'] == acquired['experiment_provenance']


@pytest.mark.parametrize('field', ['outcomes_or_known_failure_mechanisms_included', 'historical_private_dataset_claim'])
def test_leakage_or_fake_private_history_rejected(tmp_path, field):
    _, doc, _, _ = fixture()
    doc['leakage_review'][field] = True
    path = tmp_path / 'dossier.json'
    path.write_bytes(canonical(doc))
    with pytest.raises(ValueError, match='leakage review'):
        load_sponsor_dossier(path)


def test_runtime_hash_pin_and_file_source_replay(tmp_path, monkeypatch):
    identity, doc, sources, policy = fixture()
    source_payload = next(iter(sources.values()))
    (tmp_path / 'levels.json').write_bytes(source_payload)
    doc['sources'] = [{'path': 'levels.json', 'sha256': digest(source_payload)}]
    payload = canonical(doc)
    (tmp_path / 'dossier.json').write_bytes(payload)
    config = {'schema': 'pulsate.virtual-investigation-policy/v1', 'reviewer': 'Synthetic fixture',
        'prospective_evidence': policy.model_dump(mode='json'),
        'sponsor_side_dossier': {'path': 'dossier.json', 'sha256': digest(payload)}}
    (tmp_path / 'policy.json').write_bytes(canonical(config))
    monkeypatch.setenv('PULSATE_VIRTUAL_INVESTIGATION_POLICY', str(tmp_path / 'policy.json'))
    actual, root, _ = load_policy()
    preserved = {}
    result = acquire_investigation_dossier(identity, policy, {'records': []}, preserved, actual, root)
    assert result['experiment_provenance']['dossier_file_sha256'] == digest(payload)
    assert source_payload in preserved.values() and payload in preserved.values()
    (tmp_path / 'levels.json').write_bytes(b'{}')
    with pytest.raises(ValueError, match='hash mismatch'):
        acquire_investigation_dossier(identity, policy, {'records': []}, {}, actual, root)


def test_without_sponsor_configuration_public_history_remains_strict():
    identity, _, _, policy = fixture()
    result = acquire_investigation_dossier(identity, policy, {'records': []}, {}, None, None)
    assert result['schema'] != SCHEMA and result['dossier'] is None


def test_solubility_ph_cannot_be_changed_independently_of_its_endpoint():
    identity, doc, sources, policy = fixture()
    record = next(r for r in doc['candidates'][0]['inputs'] if r['role'] == 'solubility')
    record['endpoint_context']['reference_ph'] = 6.
    with pytest.raises(ValueError, match='reference pH'):
        acquire_sponsor_dossier(identity, 'Human', doc, sources, policy)


@pytest.mark.parametrize('pointer', [['levels'] * 21, [True]])
def test_sponsor_source_pointers_are_bounded_and_do_not_use_boolean_indices(pointer):
    _, doc, _, _ = fixture()
    with pytest.raises(ValueError, match='source pointer'):
        SponsorInput.model_validate(dict(doc['candidates'][0]['inputs'][0], value_pointer=pointer))
