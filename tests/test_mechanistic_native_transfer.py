"""Synthetic mathematical guards; no fixture qualifies Human drug response."""
from hashlib import sha256
import math

import pytest

from cgr.pulsate_api.mechanistic_models import (
    ModelContract, Perturbation, inspect_sbml, native_influence_paths,
    native_transfer_interface, simulate, verify_simulation,
)


def fixture(*, disconnected=False, local_shadow=False, value=1.):
    sbml = pytest.importorskip('libsbml')
    doc = sbml.SBMLDocument(3, 2)
    model = doc.createModel(); model.setId('synthetic_equation_guard')
    model.setSubstanceUnits('mole'); model.setExtentUnits('mole')
    model.setVolumeUnits('litre'); model.setTimeUnits('second')
    compartment = model.createCompartment(); compartment.setId('c'); compartment.setSize(1)
    compartment.setUnits('litre'); compartment.setConstant(True); compartment.setSpatialDimensions(3)
    unit = model.createUnitDefinition(); unit.setId('per_second')
    component = unit.createUnit(); component.setKind(sbml.UNIT_KIND_SECOND); component.setExponent(-1)
    component.setScale(0); component.setMultiplier(1)
    for identifier in ('X', 'Y'):
        species = model.createSpecies(); species.setId(identifier); species.setCompartment('c')
        species.setInitialConcentration(1); species.setConstant(False)
        species.setSubstanceUnits('mole'); species.setHasOnlySubstanceUnits(False)
        species.setBoundaryCondition(identifier == 'Y')
    for identifier in ('k', 'unused'):
        p = model.createParameter(); p.setId(identifier); p.setValue(value)
        p.setConstant(True); p.setUnits('per_second')
    function = model.createFunctionDefinition(); function.setId('scale')
    function.setMath(sbml.parseL3Formula('lambda(a, 2 dimensionless * a)'))
    rule = model.createAssignmentRule(); rule.setVariable('Y')
    rule.setMath(sbml.parseL3Formula('2 dimensionless * X'))
    reaction = model.createReaction(); reaction.setId('decay'); reaction.setReversible(False)
    ref = reaction.createReactant(); ref.setSpecies('X'); ref.setStoichiometry(1)
    ref.setConstant(True)
    law = reaction.createKineticLaw()
    law.setMath(sbml.parseL3Formula(('1 dimensionless' if disconnected else 'scale(k)') + ' * X * c'))
    if local_shadow:
        p = law.createLocalParameter(); p.setId('k'); p.setValue(1); p.setUnits('per_second')
    payload = sbml.writeSBMLToString(doc).encode()
    contract = ModelContract(identifier='synthetic', revision='test', name='Synthetic equation guard', path='x.xml',
        sha256=sha256(payload).hexdigest(), source_url='https://example.invalid', license='Test',
        parameter_provenance='Synthetic mathematical fixture', species='Synthetic', tissue='Synthetic',
        level='cellular_functional', time_unit='s', outputs={'[Y]': 'mole/litre'},
        perturbable_parameters={'k': 'per_second'}, assignment_rules={'Y': '2 dimensionless * X'},
        limitations=('Mathematical guard only, no biological validation.',))
    return model.clone(), payload, contract


def reviewed(contract):
    return Perturbation(model_identifier=contract.identifier, parameter='k', parameter_unit='per_second',
        times=(0., .5, 1.), fractions_remaining=(.5, .5, .5), source='Synthetic only',
        classification='reviewed_exposure_activity_transfer', exposure_sha256='a' * 64,
        activity_sha256='b' * 64, transfer_contract_sha256='c' * 64,
        transfer_receipt={'synthetic_mathematical_fixture': True, 'biological_qualification': False})


def test_exact_function_and_assignment_path_and_native_unit_are_replayed():
    pytest.importorskip('roadrunner')
    _, payload, contract = fixture()
    report = simulate(contract, payload, reviewed(contract))
    receipt = report['receipt']['native_transfer_interface']
    assert receipt['output_dependency_paths'] == {'[Y]': ['k', 'X', 'Y']}
    assert receipt['native_consistency']['unit_consistency'] == 'passed'
    assert receipt['unit_components'] == [{'kind': 'second', 'exponent': -1, 'scale': 0, 'multiplier': 1.}]
    # A disclosed mathematical error budget for the restarted native solver,
    # not a biological-validation or progression threshold.
    assert report['curves'][1]['values'][-1][1] == pytest.approx(2 * math.exp(-1), abs=2e-8)
    assert verify_simulation(report, payload)['passed']
    report['receipt']['native_transfer_interface']['unit_components'][0]['exponent'] = 1
    with pytest.raises(ValueError, match='failed independent replay'):
        verify_simulation(report, payload)


def test_disconnected_parameter_is_refused_before_the_solver(monkeypatch):
    _, payload, contract = fixture(disconnected=True)
    monkeypatch.setattr('cgr.pulsate_api.mechanistic_models._runner',
        lambda *args: pytest.fail('A disconnected candidate transfer must not run.'))
    with pytest.raises(ValueError, match='disconnected'):
        simulate(contract, payload, reviewed(contract))


def test_locally_shadowed_parameter_is_not_a_global_dependency():
    native, payload, contract = fixture(local_shadow=True)
    assert native_influence_paths(native, 'k', contract.outputs) == {}
    promoted, _ = inspect_sbml(contract, payload)
    with pytest.raises(ValueError, match='disconnected'):
        native_transfer_interface(contract, promoted, 'k')


def test_native_unit_requires_a_physical_definition_not_matching_strings():
    native, _, contract = fixture()
    native.getParameter('k').setUnits('invented_unit_name')
    contract = contract.model_copy(update={'perturbable_parameters': {'k': 'invented_unit_name'}})
    with pytest.raises(ValueError, match='no physical definition'):
        native_transfer_interface(contract, native, 'k')


def test_zero_native_baseline_cannot_be_interpreted_as_drug_inhibition():
    native, _, contract = fixture(value=0)
    with pytest.raises(ValueError, match='positive native baseline'):
        native_transfer_interface(contract, native, 'k')


def test_unchanged_catalyst_is_not_an_affected_species():
    sbml = pytest.importorskip('libsbml')
    native, _, _ = fixture()
    r = native.getReaction(0)
    p = r.createProduct(); p.setSpecies('X'); p.setStoichiometry(1)
    p.setConstant(True)
    # Net stoichiometry is zero. Appearance in the rate law is not consumption.
    assert native_influence_paths(native, 'k', {'[X]': 'substance/volume'}) == {}


def test_explicit_controls_remain_numerical_controls_without_transfer_receipt():
    pytest.importorskip('roadrunner')
    _, payload, contract = fixture(disconnected=True)
    control = Perturbation(model_identifier=contract.identifier, parameter='k', parameter_unit='per_second',
        times=(0., .5, 1.), fractions_remaining=(.5, .5, .5), source='Synthetic numerical control only',
        classification='explicit_validation_perturbation')
    report = simulate(contract, payload, control)
    assert 'native_transfer_interface' not in report['receipt']
    assert report['response']['[Y]']['maximum_absolute_difference'] == 0


def test_undeclared_reaction_modifier_refuses_candidate_transfer():
    sbml = pytest.importorskip('libsbml')
    native, _, contract = fixture()
    native.getReaction(0).getKineticLaw().setMath(sbml.parseL3Formula('k * X * Y * c'))
    with pytest.raises(ValueError, match='component/equation consistency'):
        native_transfer_interface(contract, native, 'k')


def test_inconsistent_reaction_dimensions_refuse_candidate_transfer():
    sbml = pytest.importorskip('libsbml')
    native, _, contract = fixture()
    native.getReaction(0).getKineticLaw().setMath(sbml.parseL3Formula('k * X'))
    with pytest.raises(ValueError, match='dimensions|component/equation consistency'):
        native_transfer_interface(contract, native, 'k')


def test_same_dimensions_with_per_hour_scale_do_not_become_per_second():
    native, _, contract = fixture()
    native.getUnitDefinition('per_second').getUnit(0).setMultiplier(3600)
    with pytest.raises(ValueError, match='physical unit scale|dimensions'):
        native_transfer_interface(contract, native, 'k')


def test_coherent_native_hour_clock_and_hour_rate_are_not_rejected():
    sbml = pytest.importorskip('libsbml')
    native, _, contract = fixture()
    definition = native.createUnitDefinition(); definition.setId('hour')
    unit = definition.createUnit(); unit.setKind(sbml.UNIT_KIND_SECOND); unit.setExponent(1)
    unit.setMultiplier(3600); unit.setScale(0)
    native.setTimeUnits('hour')
    native.getUnitDefinition('per_second').getUnit(0).setMultiplier(3600)
    contract = contract.model_copy(update={'time_unit': 'h'})
    receipt = native_transfer_interface(contract, native, 'k')
    assert receipt['native_consistency']['unit_consistency'] == 'passed'
    assert receipt['native_consistency']['native_equation_unit_scales']['checks'][0]['rate']['si_factor'] == pytest.approx(1 / 3600)
