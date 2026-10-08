"""Synthetic native-interface math guards, never Human biological validation."""
import json
import math
import pytest
from cgr.pulsate_api.cellml_physiology import _math_profile, action_potential_duration, inspect
from cgr.pulsate_api.mechanistic_models import ModelContract, Perturbation, simulate, verify_simulation
from cgr.pulsate_api.virtual_organism import canonical, digest


def fixture():
    source = b'<model xmlns="http://www.cellml.org/cellml/1.0#"><units name="per_millisecond"><unit units="second" prefix="milli" exponent="-1"/></units></model>'
    code = '''LIBCELLML_VERSION = "0.7.1"
VOI_INFO = {"units":"millisecond"}
STATE_COUNT = 1
CONSTANT_INFO = [{"component":"test","name":"decay","units":"per_millisecond"},{"component":"stim","name":"start","units":"millisecond"},{"component":"stim","name":"duration","units":"millisecond"},{"component":"stim","name":"period","units":"millisecond"}]
STATE_INFO = [{"component":"test","name":"voltage","units":"millivolt"}]
def create_states_array(): return [1.]
def create_constants_array(): return [.001,10.,1.,50.]
def create_computed_constants_array(): return []
def create_algebraic_variables_array(): return []
def initialise_arrays(states,rates,constants,computed_constants,algebraic_variables): pass
def compute_computed_constants(voi,states,rates,constants,computed_constants,algebraic_variables): pass
def compute_rates(voi,states,rates,constants,computed_constants,algebraic_variables):
    rates[0] = -constants[0]*states[0]
'''
    receipt = canonical({'source_sha256':digest(source),'generated_equations_sha256':digest(code.encode()),
        'runtime':'0.7.1','parser_errors':0,'validator_errors':0,'analyser_errors':0,'analyser_issues':[]})
    contract = ModelContract(identifier='synthetic-native',revision='test-only',name='Synthetic decay, NOT physiology',
        path='test.cellml',sha256=digest(source),source_url='https://example.invalid',license='Test only',
        parameter_provenance='Synthetic mathematical fixture',species='Synthetic',tissue='Synthetic',
        level='cellular_functional',time_unit='ms',format='cellml_compiled',outputs={'test.voltage':'millivolt'},
        perturbable_parameters={'test.decay':'per_millisecond'},limitations=('Synthetic interface, not biological qualification.',),
        native_compilation={'generated_python':{'sha256':digest(code.encode())},'inspection':{'sha256':digest(receipt)}},
        simulation_protocol={'kind':'paced_peak_snapshot','stimulus':{'start':'stim.start','duration':'stim.duration','period':'stim.period'},
            'warmup_cycles':0,'sample_step_ms':.5,'max_step_ms':5.,'solver':'BDF',
            'justification':'Synthetic numerical regression only','observation_period_ms':50.})
    payload = canonical({'cellml_source':source.decode(),'generated_python':code,'inspection':receipt.decode()})
    perturbation = Perturbation(model_identifier=contract.identifier,parameter='test.decay',parameter_unit='per_millisecond',
        times=(0.,50.),fractions_remaining=(.5,.5),source='Synthetic math only',classification='explicit_validation_perturbation')
    return contract,payload,perturbation


def test_exact_native_math_units_grid_and_replay():
    pytest.importorskip('scipy')
    contract,payload,p = fixture()
    report = simulate(contract,payload,p)
    assert len(report['curves'][0]['values']) == 101
    assert report['response']['test.voltage']['baseline_final'] == pytest.approx(math.exp(-.05),abs=2e-8)
    assert report['response']['test.voltage']['perturbed_final'] == pytest.approx(math.exp(-.025),abs=2e-8)
    assert report['action_potential_endpoints'][0]['metrics']['test.voltage.APD90']['status']=='refused'
    assert verify_simulation(report,payload)['passed']
    report['receipt']['protocol']['sample_step_ms']=1.
    with pytest.raises(ValueError,match='replay'): verify_simulation(report,payload)


def test_native_computed_constants_use_the_applied_drug_input():
    contract,payload,p = fixture(); bundle=json.loads(payload)
    code=bundle['generated_python'].replace('return []\ndef create_algebraic', 'return [0.]\ndef create_algebraic')
    code=code.replace('def compute_computed_constants(voi,states,rates,constants,computed_constants,algebraic_variables): pass',
        'def compute_computed_constants(voi,states,rates,constants,computed_constants,algebraic_variables):\n    computed_constants[0] = constants[0]')
    code=code.replace('rates[0] = -constants[0]*states[0]', 'rates[0] = -computed_constants[0]*states[0]')
    receipt=json.loads(bundle['inspection']);receipt['generated_equations_sha256']=digest(code.encode())
    bundle.update(generated_python=code,inspection=canonical(receipt).decode())
    contract=contract.model_copy(update={'native_compilation':{'generated_python':{'sha256':digest(code.encode())},
        'inspection':{'sha256':digest(bundle['inspection'].encode())}}})
    result=simulate(contract,canonical(bundle),p)
    assert result['response']['test.voltage']['perturbed_final']==pytest.approx(math.exp(-.025),abs=2e-8)
    assert verify_simulation(result,canonical(bundle))['passed']


@pytest.mark.parametrize('field', ['cellml_source','generated_python','inspection'])
def test_all_native_source_and_compiler_bytes_are_pinned(field):
    contract,payload,_ = fixture(); bundle = json.loads(payload); bundle[field]+=' '
    with pytest.raises(ValueError): inspect(contract,canonical(bundle))


def test_warnings_are_not_laundered_by_zero_native_error_count():
    contract,payload,_ = fixture(); bundle = json.loads(payload)
    receipt=json.loads(bundle['inspection']); receipt['analyser_issues']=['Incompatible native physical units']
    bundle['inspection']=canonical(receipt).decode()
    compiled=dict(contract.native_compilation,inspection={'sha256':digest(bundle['inspection'].encode())})
    with pytest.raises(ValueError,match='physical-unit warnings'): inspect(contract.model_copy(update={'native_compilation':compiled}),canonical(bundle))


@pytest.mark.parametrize('code', ['import os','from pathlib import Path','open("x")','__import__("os")','x.y()','f = lambda: 0','try:\n pass\nexcept:\n pass'])
def test_native_math_profile_does_not_accept_an_arbitrary_python_plugin(code):
    with pytest.raises(ValueError): _math_profile(code)


def test_dynamic_or_wrong_clock_is_not_claimed_as_a_peak_snapshot():
    contract,payload,p = fixture()
    for updates in ({'times':(0.,25.,50.),'fractions_remaining':(1.,.5,.2)}, {'times':(0.,1.)}):
        with pytest.raises(ValueError): simulate(contract,payload,p.model_copy(update=updates))


def native_code_update(contract, payload, transform):
    bundle = json.loads(payload)
    code = transform(bundle['generated_python'])
    receipt = json.loads(bundle['inspection'])
    receipt['generated_equations_sha256'] = digest(code.encode())
    bundle.update(generated_python=code, inspection=canonical(receipt).decode())
    compiled = {'generated_python': {'sha256': digest(code.encode())},
        'inspection': {'sha256': digest(bundle['inspection'].encode())}}
    return contract.model_copy(update={'native_compilation': compiled}), canonical(bundle)


def test_native_zero_start_stimulus_keeps_the_complete_grid():
    contract, payload, p = fixture()
    contract, payload = native_code_update(contract, payload,
        lambda code: code.replace('return [.001,10.,1.,50.]', 'return [.001,0.,1.,50.]'))
    report = simulate(contract, payload, p)
    assert len(report['curves'][0]['values']) == 101
    assert report['curves'][0]['values'][0][0] == 0.
    assert verify_simulation(report, payload)['passed']


def test_declared_acceleration_never_silently_changes_versions():
    contract, payload, p = fixture()
    protocol = dict(contract.simulation_protocol, acceleration={'engine': 'Numba', 'version': 'wrong'})
    with pytest.raises(ValueError, match='pinned reviewed'):
        simulate(contract.model_copy(update={'simulation_protocol': protocol}), payload, p)


def test_pinned_acceleration_matches_interpreted_native_equations():
    numba = pytest.importorskip('numba')
    if numba.__version__ != '0.67.0': pytest.skip('Requires the declared isolated numerical runtime')
    contract, payload, p = fixture()
    contract, payload = native_code_update(contract, payload, lambda code: code +
        '\ndef compute_variables(voi,states,rates,constants,computed_constants,algebraic_variables): pass\n')
    reference = simulate(contract, payload, p)
    protocol = dict(contract.simulation_protocol,
        acceleration={'engine': 'Numba', 'version': '0.67.0', 'llvmlite_version': '0.49.0'})
    accelerated_contract = contract.model_copy(update={'simulation_protocol': protocol})
    report = simulate(accelerated_contract, payload, p)
    assert report['receipt']['acceleration']['fresh_rate_equivalence_maximum_scaled_error'] == 0.
    assert report['receipt']['acceleration']['fastmath'] is False
    assert report['response'] == reference['response']
    assert verify_simulation(report, payload)['passed']


def test_experimental_overrides_cannot_change_the_drug_parameter_or_units():
    contract,payload,p = fixture()
    for name,unit in [('test.decay','per_millisecond'),('stim.start','second'),('unknown','millisecond')]:
        protocol=dict(contract.simulation_protocol,constant_overrides={name:{'value':1.,'unit':unit,'source':'Synthetic test','justification':'Synthetic guard'}})
        with pytest.raises(ValueError): simulate(contract.model_copy(update={'simulation_protocol':protocol}),payload,p)


def test_apd_requires_a_complete_upstroke_and_repolarization_not_a_truncated_wave():
    measured = action_potential_duration([0.,1.,2.,3.,4.],[-80.,-60.,40.,-40.,-80.])
    assert measured['value'] > 0 and measured['clinical_inference'] is False
    for voltage in ([-80.]*5,[-80.,-60.,40.,0.,-20.]):
        with pytest.raises(ValueError): action_potential_duration([0.,1.,2.,3.,4.],voltage)
