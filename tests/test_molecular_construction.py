"""Reusable construction planning and literal identity safeguards."""
import json
from types import SimpleNamespace
import pytest

from cgr.pulsate_api.scientific_construction import literal_entity, construction_computation_decision
from cgr.pulsate_api.scientific_requirements import ScientificRequirementProposal, ScientificRequirement, validate_requirement_proposal
from cgr.pulsate_api.scientific_objectives import compile_scientific_objective, plan_scientific_objective
from cgr.pulsate_api.scientific_runtime import ScientificCapabilityFailure


@pytest.mark.parametrize('name', ['methanol', 'acetone', 'benzene'])
def test_named_construction_composes_without_entity_routes(name):
    question = f'Build {name}.'
    proposal = ScientificRequirementProposal(proposal_identifier='proposal-test',
        requirements=(ScientificRequirement(operation='construct_molecule',
            requested_output='constructed_molecular_structure', supporting_quote=question),),
        summary='Construct the named molecular structure.', provider_kind='test', model_name='test')
    requirements = validate_requirement_proposal(proposal, available_input_types=())
    objective = compile_scientific_objective(question, research_requirements=requirements)
    plan = plan_scientific_objective(objective)
    names = [s.capability_name for s in plan.steps]
    assert plan.executable and not plan.ibm_job_submission_planned
    assert objective.quantum_execution_target == 'none'
    assert objective.active_space_policy == 'not_requested'
    assert not objective.ibm_submission_authorized
    assert names.index('molecular.identity_construct') < names.index('molecular.identity_verify')
    assert {'electronic.hartree_fock', 'electronic.computation_select', 'molecular.scene_project'} <= set(names)
    assert not any(name.startswith('quantum.') or 'active_space' in name for name in names)
    assert names.index('electronic.hartree_fock') < names.index('molecular.identity_verify')
    verifier = next(s for s in plan.steps if s.capability_name == 'scientific_verification.molecular_construction')
    identity = next(s for s in plan.steps if s.capability_name == 'molecular.identity_verify')
    assert identity.step_identifier in verifier.depends_on


def test_entity_must_be_literal_not_a_model_invention():
    provider = SimpleNamespace(complete=lambda messages: json.dumps({'name': 'invented'}))
    with pytest.raises(ValueError, match='ambiguous'):
        literal_entity('Construct the named material.', provider)


def test_literal_name_is_not_replaced_with_model_structure():
    provider = SimpleNamespace(complete=lambda messages: json.dumps({'name': 'methanol'}))
    assert literal_entity('Construct methanol.', provider) == 'methanol'


def test_missing_provider_is_an_explicit_limitation():
    with pytest.raises(ValueError, match='configured language model'):
        literal_entity('Construct a structure.', None)


def test_model_casing_preserves_the_scientists_literal_name():
    provider = SimpleNamespace(complete=lambda messages: json.dumps({'name': 'Methanol'}))
    assert literal_entity('Construct methanol.', provider) == 'methanol'


def _reference(**changes):
    return dict(converged=True, reference_method='rhf', electron_count=8,
        alpha_electron_count=4, beta_electron_count=4, spatial_orbital_count=6,
        orbital_energies_alpha_hartree=[-2, -1, -.8, -.4, .2, .7],
        orbital_occupations_alpha=[1, 1, 1, 1, 0, 0], **changes)


def test_ordinary_construction_selects_classical_without_using_entity_or_quantum_availability():
    decision = construction_computation_decision(_reference())
    assert decision['selected_compute'] == 'classical'
    assert decision['quantum_execution_target'] == 'none'
    assert not decision['quantum_selected']
    assert 'availability alone' in decision['reason']
    assert decision['homo_lumo_gap_hartree'] == pytest.approx(.6)


@pytest.mark.parametrize('field,value', [('converged', False), ('reference_method', 'uhf'), ('alpha_electron_count', 5)])
def test_invalid_classical_reference_never_manufactures_a_sufficient_result(field, value):
    reference = _reference()
    reference[field] = value
    with pytest.raises(ScientificCapabilityFailure, match='not valid'):
        construction_computation_decision(reference)
