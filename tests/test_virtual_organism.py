"""Generic contracts and fail-closed evidence tests; not a numerical oracle."""
import json
from types import SimpleNamespace

import pytest

from cgr.pulsate_api.virtual_organism import (PBPKRequest, PBPKParameter, CompoundDossier,
    interpret_pbpk_request, dossier_requirements, metrics, parse_results, exposure_relevance, verify_native_model)
from cgr.pulsate_api.virtual_organism_handlers import population_summary, cross_species_summary, read_model
from cgr.pulsate_api.scientific_requirements import ScientificRequirementProposal, ScientificRequirement, validate_requirement_proposal
from cgr.pulsate_api.scientific_objectives import compile_scientific_objective, plan_scientific_objective


def request(**kwargs):
    values = dict(entity_name='candidate-A', dose=1, dose_unit='mg/kg', route='Intravenous', duration_h=2,
        quotes={'dose': '1 mg/kg', 'dose_unit': '1 mg/kg', 'route': 'intravenous', 'duration_h': '2 h'})
    return PBPKRequest(**dict(values, **kwargs))


def csv_fixture(values=(0, 1, .5), candidate='candidate-A'):
    return (f'IndividualId,Time [min],Organism|PeripheralVenousBlood|{candidate}|Plasma (Peripheral Venous Blood) [µmol/l]\n'
            + '\n'.join(f'0,{t},{v}' for t,v in zip((0,60,120), values))).encode()


def test_composes_real_pbpk_capabilities_without_molecule_routes():
    question = 'Simulate candidate-A after 1 mg/kg intravenous over 2 h.'
    proposal = ScientificRequirementProposal(proposal_identifier='proposal-pbpk', summary='Exposure', provider_kind='test', model_name='test',
        requirements=(ScientificRequirement(operation='simulate_organism_exposure', requested_output='virtual_organism_exposure', supporting_quote=question),), pbpk_request=request())
    requirements = validate_requirement_proposal(proposal, available_input_types=())
    objective = compile_scientific_objective(question, research_requirements=requirements)
    plan = plan_scientific_objective(objective)
    assert not objective.clarification_required
    assert objective.quantum_execution_target == 'none'
    names = [s.capability_name for s in plan.steps]
    assert names == ['pharmacokinetics.parameterize', 'physiology.organism_construct', 'pharmacokinetics.pbpk_simulate',
        'pharmacokinetics.population_simulate', 'pharmacokinetics.cross_species_compare', 'pharmacokinetics.exposure_verify',
        'discovery.exposure_relevance_assess', 'scientist.result_assemble']


def test_missing_dose_is_resumable_clarification_not_invented_therapeutic_scenario():
    r = PBPKRequest(entity_name='candidate-A')
    assert r.dose is None and len(r.missing()) == 4
    p = ScientificRequirementProposal(proposal_identifier='proposal-pbpk', summary='Exposure', provider_kind='test', model_name='test',
        requirements=(ScientificRequirement(operation='simulate_organism_exposure', requested_output='virtual_organism_exposure', supporting_quote='Simulate candidate-A'),), pbpk_request=r)
    objective = compile_scientific_objective('Simulate candidate-A', research_requirements=validate_requirement_proposal(p, available_input_types=()))
    assert objective.clarification_required and 'dose' in objective.ambiguity_hypotheses[0]


def test_rat_population_is_not_silently_reduced_to_a_single_rat():
    assert any('population scope' in q for q in request(species=('Rat',),population_size=6).missing())
    assert any('population scope' in q for q in request(species=('Human','Rat'),population_size=6).missing())
    assert not request(species=('Human','Rat'),population_size=6,quotes={'population_size':'6 human subjects'}).missing()


@pytest.mark.parametrize('changes', [dict(dose=9), dict(dose_unit='mg'), dict(route='Oral'), dict(species=('Rat',)), dict(population_size=8), dict(infusion_minutes=10), dict(seed=42), dict(proportion_female=30), dict(age_min_years=25)])
def test_rejects_ungrounded_model_controls(changes):
    with pytest.raises(ValueError):
        request(**changes).check_grounding('candidate-A 1 mg/kg intravenous over 2 h')


@pytest.mark.parametrize('bad', [(0,float('nan'),0), (0,-.001,0), (0,float('inf'),0)])
def test_nonfinite_negative_outputs_are_blocked(bad):
    with pytest.raises(ValueError): parse_results(csv_fixture(bad), 'candidate-A', request())


def test_zero_exposure_is_not_a_successful_positive_dose_experiment():
    with pytest.raises(ValueError, match='no plasma exposure'):
        parse_results(csv_fixture((0,0,0)), 'candidate-A', request())


def test_literal_recovery_does_not_promote_model_defaults_to_scientist_evidence():
    r=request().model_dump(mode='json')
    r.update(seed=29)
    r['quotes'].update(route='Intravenous',seed='29',proportion_female='50%')
    provider=SimpleNamespace(complete=lambda messages:json.dumps(r))
    parsed=interpret_pbpk_request('candidate-A 1 mg/kg intravenous over 2 h',provider)
    assert parsed is not None
    assert parsed.quotes['route']=='intravenous'
    assert 'seed' not in parsed.quotes and 'proportion_female' not in parsed.quotes
    r['seed']=42
    assert interpret_pbpk_request('candidate-A 1 mg/kg intravenous over 2 h',provider) is None


def test_identity_population_and_time_coverage_are_verified():
    with pytest.raises(ValueError): parse_results(csv_fixture(), 'candidate-B', request())
    with pytest.raises(ValueError): parse_results(csv_fixture(), 'candidate-A', request(population_size=2))
    with pytest.raises(ValueError): parse_results(csv_fixture(), 'candidate-A', request(duration_h=3))
    parsed = parse_results(csv_fixture(), 'candidate-A', request())
    assert parsed['series'][0]['metrics']['cmax_umol_l'] == 1
    assert parsed['series'][0]['metrics']['auc_0_t_umol_h_l'] == 1.25
    assert parsed['series'][0]['metrics']['half_life_h'] is None


def test_repeated_species_literal_and_missing_iv_formulation_quote():
    r = request(species=('Rat',), formulation='Solution').model_dump(mode='json')
    r['quotes']['species'] = 'Rat'
    text = 'Simulate candidate-A in a rat after 1 mg/kg intravenous over 2 h using the rat dossier.'
    parsed = interpret_pbpk_request(text, SimpleNamespace(complete=lambda messages: json.dumps(r)))
    assert parsed is not None and parsed.species == ('Rat',)
    assert parsed.quotes['species'] == 'rat'
    assert parsed.formulation is None


def test_nondefault_species_without_quote_is_recovered_only_from_literal_species():
    r = request(species=('Rat',)).model_dump(mode='json')
    provider = SimpleNamespace(complete=lambda messages: json.dumps(r))
    assert interpret_pbpk_request('candidate-A rat 1 mg/kg intravenous over 2 h', provider) is not None
    assert interpret_pbpk_request('candidate-A 1 mg/kg intravenous over 2 h', provider) is None


def test_no_docking_score_to_activity_or_total_tissue_to_unbound_shortcut():
    assert exposure_relevance([], {'kind':'Vina','value':-8,'unit':'kcal/mol','source':'docking'})['status'] == 'insufficient_evidence'
    measured = dict(kind='Ki', value=.1, unit='umol/l', source='assay', compartment_path='path-A')
    total = [dict(path='path-A',compartment='Tissue',values_umol_l=[1,2])]
    assert exposure_relevance(total, measured)['status'] == 'insufficient_evidence'
    unbound = [dict(path='path-A',compartment='Interstitial Unbound',values_umol_l=[.05,.1])]
    result = exposure_relevance(unbound, measured)
    assert result['peak_exposure_to_activity_ratios'] == [1]
    assert result['status'] == 'prioritization_hypothesis'


def test_missing_adme_does_not_turn_descriptors_into_clearance():
    dossier = CompoundDossier(name='candidate-A', smiles='CC',inchikey='identity',species='Rat',binding_partner='Albumin',parameters={},ionization=(),ionization_source='explicit neutral-state evidence')
    assert {'hepatic_clearance','renal_clearance','fraction_unbound'} <= set(dossier_requirements(dossier, 'Intravenous'))
    assert 'intestinal_permeability' in dossier_requirements(dossier, 'Oral')
    with pytest.raises(ValueError): PBPKParameter(value=1,unit='fraction',classification='missing',source='not available',method='none')


def test_native_species_and_units_not_merely_input_json():
    xml = b'<Model><StringExtendedProperty name="Species" value="Rat"/><Parameter name="DosePerBodyWeight" value="1e-6"/></Model>'
    assert verify_native_model(xml, request(species=('Rat',)), 'Rat')['species'] == 'Rat'
    with pytest.raises(ValueError): verify_native_model(xml, request(), 'Human')
    with pytest.raises(ValueError): verify_native_model(xml.replace(b'1e-6',b'1'), request(), 'Rat')


def test_native_clearance_builder_without_executable_reaction_is_blocked():
    xml=b'<Model><StringExtendedProperty name="Species" value="Rat"/><Parameter name="DosePerBodyWeight" value="1e-6"/><EventGroup containerType="Application" eventGroupType="IntravenousBolus"><Parameter name="Start time" value="0"/></EventGroup><Container name="candidate-A"><Children><Parameter name="Molecular weight" value="1e-7"/></Children></Container><ReactionBuilder name="candidate-A-process"/></Model>'
    snapshot={'Compounds':[{'Name':'candidate-A','Parameters':[{'Name':'Molecular weight','Value':100,'Unit':'g/mol'}]}],'Simulations':[{'Compounds':[{'Name':'candidate-A','Processes':[{'Name':'process','SystemicProcessType':'Hepatic'}]}]}]}
    with pytest.raises(ValueError,match='executable native reaction network'):
        verify_native_model(xml,request(species=('Rat',)),'Rat',snapshot)


def test_new_graph_and_dossier_do_not_require_public_identity_lookup():
    from rdkit import Chem
    from cgr.pulsate_api.virtual_organism_handlers import supplied_candidate
    smiles='CCO'
    key=Chem.MolToInchiKey(Chem.MolFromSmiles(smiles))
    dossier=CompoundDossier(name='unpublished candidate',smiles=smiles,inchikey=key,species='Rat',binding_partner='Albumin',parameters={},ionization=(),ionization_source='explicit neutral-state assumption')
    ref=SimpleNamespace(artifact_identifier='dossier-A')
    store=SimpleNamespace(read=lambda ref:dossier.model_dump_json().encode())
    identity,supplied,refs=supplied_candidate(SimpleNamespace(artifact_references=(ref,)),SimpleNamespace(input_references=(SimpleNamespace(artifact_type='pbpk_compound_dossier',artifact_identifier='dossier-A'),)),store,request(entity_name='unpublished candidate'))
    assert identity['inchikey']==key and identity['pubchem_cid'] is None
    assert len(supplied)==1 and refs==(ref,)
    assert 'hepatic_clearance' in dossier_requirements(supplied[0][0],'Intravenous')


def test_finite_population_physiology_and_literal_age_are_required():
    from cgr.pulsate_api.virtual_organism import verify_population
    payload=b'IndividualId,Gender,Population,Population Name,Organism|Age [year(s)],Organism|Weight [kg],Organism|Height [dm]\n0,MALE,European_ICRP_2002,Population,31,73,17.5\n'
    assert verify_population(payload,request(proportion_female=0))['subjects'][0]['age_years']==31
    with pytest.raises(ValueError):verify_population(payload.replace(b',31,',b',91,'),request(proportion_female=0))
    with pytest.raises(ValueError):verify_population(payload.replace(b',73,',b',NaN,'),request(proportion_female=0))


def test_population_intervals_are_empirical_and_recomputed():
    parsed = parse_results(csv_fixture(), 'candidate-A', request())
    run = {'species':'Human','result':parsed}
    summary = population_summary([run])
    assert summary['series'][0]['median_umol_l'] == [0,1,.5]
    assert 'not a clinical confidence interval' in summary['interval_scope']
    assert cross_species_summary([run])['subjects'][0]['plasma_metrics']['auc_0_t_umol_h_l'] == 1.25


def test_model_catalogue_confines_paths_and_checks_hash(tmp_path):
    path = tmp_path/'model.json'; path.write_text('{}')
    with pytest.raises(ValueError): read_model({'path':'model.json','sha256':'0'*64,'source':'reviewed'}, tmp_path)


def test_unknown_legacy_requirements_remain_readable():
    from cgr.pulsate_api.scientific_requirements import ValidatedResearchRequirements
    old = ValidatedResearchRequirements(operations=('analyze_structure',),requested_outputs=('structure_analysis',),
        required_artifact_types=('molecular_structure_analysis',),capability_profile='structure_analysis',source_proposal_identifier='proposal-old')
    assert old.pbpk_request is None


def test_native_timeout_retains_partial_evidence_without_accepting_a_curve(tmp_path, monkeypatch):
    import subprocess
    from pathlib import Path
    from cgr.pulsate_api.virtual_organism import NativePBPKEngine, NativePBPKFailure, digest
    executable = tmp_path / 'PKSim.CLI.exe'
    executable.write_bytes(b'test executable')
    (tmp_path / 'PKSimDB.sqlite').write_bytes(b'test database')
    def run(arguments, **kwargs):
        if '--version' in arguments:
            return SimpleNamespace(stdout='', stderr='PKSim.CLI 12.3.173+' + 'a' * 40, returncode=1)
        (Path(arguments[arguments.index('-o') + 1]) / 'partial.xml').write_bytes(b'partial native evidence')
        raise subprocess.TimeoutExpired(arguments, 300, output=b'partial log', stderr=b' timeout')
    monkeypatch.setattr(subprocess, 'run', run)
    with pytest.raises(NativePBPKFailure) as captured:
        NativePBPKEngine(executable).run({'test': 'snapshot'}, request(), 'candidate-A')
    evidence = captured.value.files
    receipt = json.loads(evidence['failure-receipt.json'])
    assert receipt['status'] == 'refused' and receipt['exit_code'] is None
    assert receipt['quantum_selected'] is False
    assert evidence['partial.xml'] == b'partial native evidence'
    assert all(digest(evidence[name]) == sha for name, sha in receipt['files'].items())


def test_activity_evidence_is_identity_and_hash_checked_before_assessment():
    from rdkit import Chem
    from cgr.pulsate_api.virtual_organism import QuantitativeActivity, digest
    from cgr.pulsate_api.virtual_organism_handlers import VirtualOrganismHandler
    smiles = 'CCO'
    key = Chem.MolToInchiKey(Chem.MolFromSmiles(smiles))
    activity = QuantitativeActivity(inchikey=key, species='Human', target='test receptor', kind='Ki', value=1,
        unit='umol/l', source='Synthetic unit-test assay fixture; not acceptance evidence.', assay_context='test',
        compartment_path='missing-compartment', compatibility_limitations='No matching exposure.')
    payload = activity.model_dump_json().encode()
    ref = SimpleNamespace(artifact_identifier='activity-A', content_sha256=digest(payload))
    exposure = {'runs': [], 'missing': [{'species': 'Human', 'reason': 'No dossier'}]}
    documents = {'pbpk_parameterization': {'identity': {'smiles': smiles, 'inchikey': key}},
        'pbpk_model_set': {'identity': {'smiles': smiles, 'inchikey': key}, 'models': []},
        'pbpk_exposure_result': exposure, 'pbpk_population_result': population_summary([]),
        'pbpk_cross_species_result': cross_species_summary([])}
    handler = VirtualOrganismHandler.__new__(VirtualOrganismHandler)
    handler.capability = 'pharmacokinetics.exposure_verify'
    handler.runner = SimpleNamespace(store=SimpleNamespace(read=lambda reference: payload))
    handler.read = lambda record, kind: (documents[kind], SimpleNamespace(artifact_identifier=kind))
    written = []
    handler.write = lambda document, *args: written.append(document) or None
    objective = SimpleNamespace(research_requirements=SimpleNamespace(pbpk_request=request()),
        input_references=(SimpleNamespace(artifact_type='quantitative_activity_evidence', artifact_identifier='activity-A'),))
    record = SimpleNamespace(artifact_references=(ref,))
    # Avoid artifact contract construction here; the scientific gate executes
    # before its successful outcome is serialized by the runtime.
    from unittest.mock import patch
    with patch('cgr.pulsate_api.virtual_organism_handlers.ScientificCapabilityOutcome', side_effect=lambda **kwargs: kwargs):
        handler._execute(None, objective, record)
    assert written[0]['activity_comparisons'][0]['status'] == 'insufficient_evidence'
    payload = activity.model_copy(update={'inchikey': 'another identity'}).model_dump_json().encode()
    ref.content_sha256 = digest(payload)
    with pytest.raises(ValueError, match='another candidate'):
        handler._execute(None, objective, record)
    ref.content_sha256 = '0' * 64
    with pytest.raises(ValueError, match='integrity'):
        handler._execute(None, objective, record)


def test_no_held_out_entity_in_new_production_modules():
    from pathlib import Path
    prohibited = ['torce' + 'trapib', '46093ac68e560efd5b6caf6b7d958a05', '18d556f04191e07c9148e05529d1505d', '2837ca98dc688f7af5858b6ba8785a13c67ce460022ad4d0d3862d22915cd140']
    for module in ('virtual_organism.py','virtual_organism_handlers.py'):
        source = (Path(__file__).parents[1]/'src/cgr/pulsate_api'/module).read_text().lower()
        assert all(word not in source for word in prohibited)
        assert all(word not in source for word in ('caffeine','acetanilide','theophylline'))
