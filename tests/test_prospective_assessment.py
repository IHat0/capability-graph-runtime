"""General prospective contracts: no acceptance entity or expected outcome fixtures."""
import json
from types import SimpleNamespace

import pytest

from cgr.pulsate_api.scientific_prospective import property_hypotheses, validate_screening_pair
from cgr.pulsate_api.scientific_target_selection import extract_target_scope
from cgr.pulsate_api.scientific_requirements import ScientificRequirement, ScientificRequirementProposal, validate_requirement_proposal
from cgr.pulsate_api.scientific_objectives import compile_scientific_objective, plan_scientific_objective


@pytest.mark.parametrize('compound,target', [('compound-A', 'target-X'), ('compound-B', 'target-Y')])
def test_assessment_composes_existing_screening_plus_separate_assessment(compound, target):
    question = f'Investigate {compound} as a {target} drug candidate.'
    proposal = ScientificRequirementProposal(proposal_identifier='proposal-generic', summary='Prospective assessment.',
        provider_kind='test', model_name='test', requirements=(ScientificRequirement(
            operation='assess_drug_candidate', requested_output='prospective_candidate_assessment', supporting_quote=question),))
    requirements = validate_requirement_proposal(proposal, available_input_types=())
    objective = compile_scientific_objective(question, research_requirements=requirements)
    # Input absence remains a resumable acquisition requirement, not invented evidence.
    assert objective.research_requirements.capability_profile == 'protein_ligand_discovery'
    assert 'prospective_candidate_assessment' in requirements.required_artifact_types
    assert objective.quantum_execution_target == 'none'
    from cgr.pulsate_api.scientific_capability_catalogue import phase8_scientific_capability_catalogue
    composition = phase8_scientific_capability_catalogue().compose(
        task_type='protein_ligand_discovery', required_artifact_types=requirements.required_artifact_types,
        available_artifact_types=('scientist_input', 'protein_structure', 'ligand_structure'))
    names = [s.capability_name for s in composition]
    assert 'discovery.prospective_assess' in names
    assert 'discovery.virtual_investigate' in names
    assert 'discovery.virtual_investigation_verify' in names
    assert names.index('discovery.virtual_investigate') < names.index('discovery.virtual_investigation_verify') < names.index('discovery.prospective_assess')
    assert 'discovery.campaign_iterate' in names
    assert any(n.startswith('scientific_verification.candidate_ranking') for n in names)
    answer = next(s for s in composition if 'scientist_facing_result' in s.produced_artifact_types)
    assert 'prospective_candidate_assessment' in answer.accepted_artifact_types
    assert names.index('discovery.prospective_assess') < names.index(answer.capability_name)


def test_human_scope_is_an_explicit_policy_assumption_not_a_model_fact():
    provider = SimpleNamespace(complete=lambda messages: json.dumps({'name': 'target-X', 'organism': None, 'domain': None}))
    turns = [SimpleNamespace(content='Investigate compound-A as a target-X drug candidate.')]
    assert extract_target_scope(turns, provider) is None
    scope = extract_target_scope(turns, provider, allow_drug_defaults=True)
    assert scope['name'] == 'target-X' and scope['organism'] == 'human' and scope['domain'] is None
    assert 'not supplied by the scientist' in scope['assumptions'][0]


def test_explicit_scope_overrides_assumptions():
    provider = SimpleNamespace(complete=lambda messages: json.dumps({'name': 'target-X', 'organism': 'species-Y', 'domain': 'domain-Z'}))
    scope = extract_target_scope([SimpleNamespace(content='target-X species-Y domain-Z')], provider, allow_drug_defaults=True)
    assert scope['organism'] == 'species-Y' and scope['assumptions'] == []


def test_model_cannot_supply_ungrounded_scope_even_in_drug_mode():
    provider = SimpleNamespace(complete=lambda messages: json.dumps({'name': 'target-X', 'organism': 'invented', 'domain': None}))
    assert extract_target_scope([SimpleNamespace(content='target-X')], provider, allow_drug_defaults=True) is None


def test_general_property_flags_are_hypotheses_never_toxicity_findings():
    properties = dict(molecular_weight=610, logp=6, hbond_donors=2, hbond_acceptors=7)
    flags = property_hypotheses(properties)
    assert {f['metric'] for f in flags} == {'molecular_weight', 'logp'}
    assert all(f['classification'] == 'derived_hypothesis' and 'not a measured liability' in f['limitation'] for f in flags)
    assert property_hypotheses(dict(molecular_weight=200, logp=1, hbond_donors=1, hbond_acceptors=2)) == []


def _pair():
    candidate = {'candidate_identifier': 'candidate-A', 'verification': [{}],
                 'properties_and_calculations': [{'objective_identifier': 'vina_pose_score', 'value': -7.1}]}
    descriptor = {'candidate_identifier': 'candidate-A', 'conformer_generated': True,
        'descriptors': dict(molecular_weight=300, logp=3, qed=.4, hbond_donors=1,
                            hbond_acceptors=4, rotatable_bonds=3, formal_charge=0)}
    docking = {'candidate_identifier': 'candidate-A', 'vina_scores_kcal_per_mol': [-7.1, -6.9],
               'score_semantics': 'autodock_vina_score_not_binding_free_energy'}
    return candidate, descriptor, docking


def test_screening_pair_validates_actual_trace_score():
    assert validate_screening_pair(*_pair())['qed'] == .4


@pytest.mark.parametrize('defect', ['identity', 'nonfinite', 'score', 'semantics', 'verification'])
def test_assessment_never_manufactures_validated_evidence(defect):
    candidate, descriptor, docking = _pair()
    if defect == 'identity': docking['candidate_identifier'] = 'candidate-B'
    if defect == 'nonfinite': descriptor['descriptors']['logp'] = float('nan')
    if defect == 'score': docking['vina_scores_kcal_per_mol'][0] = -8
    if defect == 'semantics': docking['score_semantics'] = 'binding_affinity'
    if defect == 'verification': candidate['verification'] = []
    with pytest.raises(ValueError): validate_screening_pair(candidate, descriptor, docking)
