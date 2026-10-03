"""Exact equation authorization; synthetic model tests, not biological evidence."""
import hashlib

import pytest

from cgr.pulsate_api.mechanistic_models import ModelContract, inspect_sbml


def test_assignment_rule_requires_exact_predeclared_formula():
    sbml = pytest.importorskip('libsbml')
    doc = sbml.SBMLDocument(2, 4)
    model = doc.createModel()
    model.setId('synthetic')
    compartment = model.createCompartment()
    compartment.setId('c'); compartment.setSize(1)
    species = model.createSpecies()
    species.setId('X'); species.setCompartment('c'); species.setInitialConcentration(1)
    parameter = model.createParameter()
    parameter.setId('reported'); parameter.setConstant(False)
    rule = model.createAssignmentRule()
    rule.setVariable('reported'); rule.setMath(sbml.parseL3Formula('2 * X'))
    payload = sbml.writeSBMLToString(doc).encode()
    contract = ModelContract(identifier='synthetic',revision='test',name='Synthetic only',path='x.xml',
        sha256=hashlib.sha256(payload).hexdigest(),source_url='https://example.invalid',license='Test',
        parameter_provenance='Test',species='Synthetic',level='cellular_functional',time_unit='s',
        outputs={'[X]':'substance/volume'},perturbable_parameters={},limitations=('Test only',))
    with pytest.raises(ValueError,match='exact equation manifest'):
        inspect_sbml(contract,payload)
    declared=contract.model_copy(update={'assignment_rules':{'reported':'2 * X'}})
    _, executable=inspect_sbml(declared,payload)
    assert '2' in executable.decode()
    with pytest.raises(ValueError,match='exact equation manifest'):
        inspect_sbml(contract.model_copy(update={'assignment_rules':{'reported':'3 * X'}}),payload)


def test_rate_rule_is_not_discarded_as_an_assignment():
    sbml = pytest.importorskip('libsbml')
    doc=sbml.SBMLDocument(2,4);model=doc.createModel();model.setId('synthetic')
    p=model.createParameter();p.setId('rate');p.setConstant(False)
    r=model.createRateRule();r.setVariable('rate');r.setMath(sbml.parseL3Formula('1'))
    payload=sbml.writeSBMLToString(doc).encode()
    contract=ModelContract(identifier='synthetic',revision='test',name='Synthetic only',path='x.xml',
        sha256=hashlib.sha256(payload).hexdigest(),source_url='https://example.invalid',license='Test',
        parameter_provenance='Test',species='Synthetic',level='cellular_functional',time_unit='s',
        outputs={'[X]':'substance/volume'},perturbable_parameters={},limitations=('Test only',),
        assignment_rules={'rate':'1'})
    with pytest.raises(ValueError,match='does not qualify these SBML rules'):
        inspect_sbml(contract,payload)


def test_nondefault_tolerance_requires_scale_justification():
    with pytest.raises(ValueError,match='predeclared unit/scale justification'):
        ModelContract(identifier='synthetic',revision='test',name='Synthetic only',path='x.xml',
            sha256='a'*64,source_url='https://example.invalid',license='Test',parameter_provenance='Test',
            species='Synthetic',level='cellular_functional',time_unit='s',outputs={'[X]':'substance/volume'},
            perturbable_parameters={},limitations=('Test only',),absolute_tolerance=1e-18)
