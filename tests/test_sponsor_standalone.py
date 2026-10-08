"""Generic synthetic sponsor routing; native/browser evidence is separate."""
import copy

import pytest
from test_sponsor_dossier import fixture, sensitivity_policy
from cgr.pulsate_api.native_sensitivity import conditional_acquisition, design
from cgr.pulsate_api.sponsor_dossier import acquire_sponsor_dossier, sponsor_parameter_audit
from cgr.pulsate_api.virtual_organism import PBPKRequest, canonical, digest, dossier_requirements, CompoundDossier
from cgr.pulsate_api.virtual_organism_handlers import native_scenarios, parameter_sensitivity_summary, exposure_summary


@pytest.mark.parametrize('smiles', ['CCO', 'CCCO', 'CO'])
def test_sponsor_sources_are_generic_native_scenarios_without_a_nominal(smiles):
    identity, doc, sources, policy = fixture(smiles)
    acquired = acquire_sponsor_dossier(identity, 'Human', doc, sources, policy)
    source = {'entry': {'kind': 'sponsor_dossier'}, 'document': acquired, 'species': 'Human',
        'sensitivity_policy': dict(sensitivity_policy(), maximum_runs=2)}
    request = PBPKRequest(entity_name='Synthetic contract', dose=.1, dose_unit='mg', route='Intravenous', duration_h=2, population_size=2)
    cases = native_scenarios(source, request)
    assert len(cases) == 2 and {c['scenario_kind'] for c in cases} == {'sponsor_input_sensitivity'}
    assert all(c['request']['population_size'] == 2 and c['scenario_policy']['nominal_withheld'] for c in cases)
    assert all(c['parameters'] and c['scenario_policy']['input_provenance']['input_receipts'] for c in cases)
    assert cases == native_scenarios(source, request)
    audit = sponsor_parameter_audit(acquired)
    uncertain = [p for p in audit if p['interval']]
    assert uncertain and all(p['value'] is None and p['classification']=='assumed' for p in uncertain)


def test_missing_route_irrelevant_endpoint_is_preserved_and_never_invented():
    identity, doc, sources, policy = fixture()
    raw = {'levels': {'intestinal_permeability': []}}
    payload = canonical(raw)
    sources[digest(payload)] = payload
    doc['candidates'][0]['inputs'].append({'identifier': 'synthetic-missing-permeability', 'role': 'intestinal_permeability',
        'provenance_class': 'missing', 'values': [], 'unit': 'cm/s', 'source_sha256': digest(payload),
        'value_pointer': ['levels', 'intestinal_permeability'], 'rationale': 'Unknown, not needed for IV', 'uncertainty': 'No prediction'})
    acquired = acquire_sponsor_dossier(identity, 'Human', doc, sources, policy)
    case = design(acquired, dict(sensitivity_policy(), maximum_runs=2))['cases'][0]
    conditional = conditional_acquisition(acquired, case)
    assert conditional['missing'] == acquired['missing']
    dossier = CompoundDossier.model_validate(conditional['dossier'])
    assert not dossier_requirements(dossier, 'Intravenous')
    assert 'intestinal_permeability' in dossier_requirements(dossier, 'Oral')
    assert 'intestinal_permeability' not in dossier.parameters


def test_conditional_population_summary_retains_all_people_not_only_first():
    def series(subject, value):
        return {'subject_identifier': subject, 'path': 'Heart/Interstitial', 'organ': 'Heart',
            'compartment': 'Interstitial Unbound', 'times_h': [0., 1.], 'values_umol_l': [0., value]}
    run = {'species': 'Human', 'scenario_kind': 'sponsor_input_sensitivity', 'scenario_identifier': 'case-A',
        'scenario_policy': {'nominal_withheld': True}, 'file_artifacts': {},
        'result': {'series': [series('0', 1.), series('1', 3.)]}}
    summary = parameter_sensitivity_summary([run])
    curve = summary['scenarios'][0]['population']['series'][0]
    assert curve['subject_count'] == 2 and curve['median_umol_l'] == [0., 2.]
    assert curve['p05_umol_l'] == [0., 1.1] and curve['p95_umol_l'] == [0., 2.9]
    assert run['scenario_kind'] == 'sponsor_input_sensitivity'


def test_conditional_cases_are_not_reported_as_different_species():
    runs = [{'species': 'Human', 'scenario_kind': 'sponsor_input_sensitivity'} for _ in range(2)]
    summary = exposure_summary('exposure_supported', runs)
    assert '1 species (Human)' in summary
    assert '0 reference computation(s) and 2 conditional input scenario(s)' in summary
    assert 'not a safety assessment' in summary


def test_reference_species_and_conditional_runs_are_counted_independently():
    runs = [{'species': 'Human', 'scenario_kind': 'nominal'}, {'species': 'Rat'},
        {'species': 'Human', 'scenario_kind': 'dose_sweep'}]
    assert '2 species (Human, Rat), 2 reference computation(s) and 1 conditional input scenario(s)' in exposure_summary('exposure_supported', runs)
    assert '0 species (none)' in exposure_summary('insufficient_parameterization', [])
