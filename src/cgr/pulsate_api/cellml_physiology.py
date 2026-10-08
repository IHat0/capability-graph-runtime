"""Execute a source-pinned native CellML compilation, never generated biology.

The compilation is a reviewed deployment artifact. Its restricted mathematical
Python profile is checked before execution; no arbitrary Python plugin is
accepted. Native units, initialization and equations remain unchanged. A paced
single-cell response is not a whole-heart model, clinical QT or arrhythmia.
"""
from __future__ import annotations

import ast
import json
import math
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree as ET

from .virtual_organism import canonical, digest

SCIPY_VERSION = '1.16.3'
COMPILER_VERSION = '0.7.1'
ACCELERATOR_VERSION = '0.67.0'
LLVM_VERSION = '0.49.0'


def runtime_status():
    status = {'engine': 'SciPy/native-libCellML-Python', 'required_version': SCIPY_VERSION,
        'compiler_version': COMPILER_VERSION, 'available': False, 'runtime_version': None,
        'reason_code': 'numerical_dependency_unavailable'}
    try:
        import scipy
    except ImportError:
        return dict(status, reason='Pinned native CellML numerical runtime unavailable; no functional calculation ran.')
    status['runtime_version'] = scipy.__version__
    if scipy.__version__ != SCIPY_VERSION:
        return dict(status, reason_code='numerical_version_unqualified', reason='CellML solver differs from the pinned research runtime.')
    return dict(status, available=True, reason_code=None, reason=None)


def load_compilation(contract, source, root):
    files = {}
    for key in ('generated_python', 'inspection'):
        entry = contract.native_compilation.get(key, {})
        path = (Path(root) / entry['path']).resolve(strict=True)
        if not path.is_relative_to(root) or path.stat().st_size > 4 * 1024 * 1024:
            raise ValueError('CellML compilation path/size exceeds its trusted boundary.')
        raw = path.read_bytes()
        if digest(raw) != entry['sha256']:
            raise ValueError('Native compilation artifact changed: ' + key)
        files[key] = raw.decode('utf-8')
    return canonical(dict(files, cellml_source=source.decode('utf-8')))


def _math_profile(code):
    """Only the native mathematical profile; no I/O, reflection or imports."""
    tree = ast.parse(code)
    allowed = {'Enum': Enum, 'bool': bool, **{name: getattr(math, name) for name in dir(math) if not name.startswith('_')}}
    functions = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if not ((node.module == 'math' and [a.name for a in node.names] == ['*'])
                    or (node.module == 'enum' and [a.name for a in node.names] == ['Enum'])):
                raise ValueError('Native compilation contains a nonmathematical import.')
        if isinstance(node, (ast.Import, ast.With, ast.AsyncWith, ast.Try, ast.Raise,
                ast.Delete, ast.Global, ast.Nonlocal, ast.Lambda, ast.AsyncFunctionDef)):
            raise ValueError('Native compilation is outside the restricted mathematical profile.')
        if isinstance(node, ast.Attribute) and (not isinstance(node.value, ast.Name) or node.value.id != 'VariableType'):
            raise ValueError('Native compilation contains an unqualified attribute access.')
        if isinstance(node, ast.Name) and node.id.startswith('__') and node.id != '__version__':
            raise ValueError('Native compilation contains reflection metadata.')
        if isinstance(node, ast.Call) and (not isinstance(node.func, ast.Name)
                or node.func.id not in set(allowed) | functions):
            raise ValueError('Native compilation contains an unqualified function call.')
    tree.body = [n for n in tree.body if not isinstance(n, ast.ImportFrom)]
    namespace = {'__builtins__': {'__build_class__': __build_class__}, '__name__': 'pulsate_native_math', **allowed}
    exec(compile(tree, '<reviewed-native-cellml-math>', 'exec'), namespace)
    return SimpleNamespace(**namespace), tree


def inspect(contract, payload):
    bundle = json.loads(payload)
    source, code = bundle['cellml_source'].encode(), bundle['generated_python'].encode()
    receipt = json.loads(bundle['inspection'])
    if (digest(source) != contract.sha256
            or digest(code) != contract.native_compilation['generated_python']['sha256']
            or digest(bundle['inspection'].encode()) != contract.native_compilation['inspection']['sha256']
            or receipt.get('source_sha256') != contract.sha256
            or receipt.get('generated_equations_sha256') != digest(code)
            or receipt.get('runtime') != COMPILER_VERSION
            or any(receipt.get(k) != 0 for k in ('parser_errors', 'validator_errors', 'analyser_errors'))):
        raise ValueError('Native source/compiler/unit-analysis provenance failed replay.')
    if receipt.get('analyser_issues'):
        raise ValueError('Unresolved native physical-unit warnings prevent qualified physiological transfer.')
    if b'<!ENTITY' in source or b'<!DOCTYPE' in source:
        raise ValueError('CellML external entity declarations are not allowed.')
    model, tree = _math_profile(code.decode())
    if model.LIBCELLML_VERSION != COMPILER_VERSION or model.VOI_INFO['units'] != 'millisecond' or contract.time_unit != 'ms':
        raise ValueError('Native clock/compiler differs from the reviewed protocol.')
    constants = {v['component'] + '.' + v['name']: (i, v) for i, v in enumerate(model.CONSTANT_INFO)}
    states = {v['component'] + '.' + v['name']: (i, v) for i, v in enumerate(model.STATE_INFO)}
    if len(constants) != len(model.CONSTANT_INFO) or len(states) != len(model.STATE_INFO):
        raise ValueError('Ambiguous native variable interface.')
    for name, unit in contract.perturbable_parameters.items():
        if name not in constants or constants[name][1]['units'] != unit:
            raise ValueError('Exact CellML parameter/native unit mismatch: ' + name)
    for name, unit in contract.outputs.items():
        if name not in states or states[name][1]['units'] != unit:
            raise ValueError('Exact CellML output/native unit mismatch: ' + name)
    return model, tree, source, receipt, constants, states


def _reference(node):
    if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)
            and isinstance(node.slice, ast.Constant) and type(node.slice.value) is int
            and node.value.id in {'constants', 'algebraic_variables', 'rates', 'states', 'computed_constants'}):
        return node.value.id + ':' + str(node.slice.value)
    return None


def native_interface(contract, payload, parameter):
    model, tree, source, receipt, constants, states = inspect(contract, payload)
    index, info = constants[parameter]
    # Compiler-generated array dependencies. Connectivity is a necessary gate,
    # never pharmacological qualification by a suggestive parameter name.
    edges = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            deps = {_reference(n) for n in ast.walk(node.value)} - {None}
            for target in node.targets:
                ref = _reference(target)
                if ref: edges.setdefault(ref, set()).update(deps)
    reached, pending = {'constants:' + str(index)}, True
    while pending:
        before = len(reached)
        reached.update(n for n, deps in edges.items() if reached & deps)
        pending = len(reached) != before
    paths = {name: 'rates:' + str(states[name][0]) for name in contract.outputs
        if 'rates:' + str(states[name][0]) in reached}
    if not paths:
        raise ValueError('Native CellML parameter is disconnected from the declared state rates.')
    root = ET.fromstring(source)
    ns = {'c': 'http://www.cellml.org/cellml/1.0#'}
    units = root.find("c:units[@name='" + info['units'] + "']", ns)
    if units is None:
        raise ValueError('Native parameter has no declared physical unit definition.')
    components = [dict(n.attrib) for n in units]
    return {'schema': 'pulsate.native-cellml-transfer-interface/v1', 'model_sha256': contract.sha256,
        'parameter': parameter, 'native_unit': info['units'], 'unit_components': components,
        'output_dependency_paths': paths, 'compiler_analysis': receipt,
        'scope': 'Native declared units and equation connectivity; not independent biological qualification.'}


def _initialize(model, *, numpy_arrays=False):
    states = model.create_states_array()
    rates = [0.] * model.STATE_COUNT
    constants = model.create_constants_array()
    computed = model.create_computed_constants_array()
    variables = model.create_algebraic_variables_array()
    if numpy_arrays:
        import numpy as np
        # Empty computed/algebraic arrays are legitimate native models. Typed
        # arrays must exist before the first compiled call, not afterwards.
        states, rates, constants, computed, variables = (
            np.asarray(v, dtype=np.float64) for v in (states, rates, constants, computed, variables))
    model.initialise_arrays(states, rates, constants, computed, variables)
    model.compute_computed_constants(0., states, rates, constants, computed, variables)
    return states, rates, constants, computed, variables


def _accelerate(model, protocol):
    """Optional pinned compilation of the already checked native math only.

    No fast-math, disk cache, new equations, altered tolerances or fallback.
    Compare fresh interpreted and compiled rates before using the accelerator.
    This is numerical verification, never biological model qualification.
    """
    option = protocol.get('acceleration')
    if option is None:
        return model, {'engine': 'interpreted_native_math'}
    if option != {'engine': 'Numba', 'version': ACCELERATOR_VERSION, 'llvmlite_version': LLVM_VERSION}:
        raise ValueError('Native math acceleration must use the pinned reviewed versions.')
    import numpy as np
    import numba
    import llvmlite
    if numba.__version__ != ACCELERATOR_VERSION or llvmlite.__version__ != LLVM_VERSION:
        raise ValueError('Native numerical acceleration dependencies differ from the pinned contract.')
    settings = {'cache': False, 'fastmath': False, 'error_model': 'python'}
    namespace = model.compute_rates.__globals__
    # The restricted profile has already rejected all nonmathematical calls.
    # Helpers must be compiled in the same namespace as their callers.
    for name, function in list(namespace.items()):
        if name.endswith('_func') and callable(function):
            namespace[name] = numba.njit(**settings)(function)
    functions = {name: numba.njit(**settings)(getattr(model, name))
        for name in ('compute_computed_constants', 'compute_rates', 'compute_variables')}
    original = _initialize(model)
    maximum = 0.
    for time, voltage_shift in ((0., 0.), (7.3, 2.), (18.7, -2.)):
        arrays = [np.asarray(v, dtype=np.float64).copy() for v in original]
        arrays[0][0] += voltage_shift
        reference = [v.copy() for v in arrays]
        for name in ('compute_computed_constants', 'compute_rates', 'compute_variables'):
            getattr(model, name)(time, *reference)
            functions[name](time, *arrays)
        for expected, actual in zip(reference, arrays, strict=True):
            if not np.isfinite(expected).all() or not np.isfinite(actual).all():
                raise ValueError('Native acceleration check produced nonfinite quantities.')
            error = float(np.max(np.abs(expected-actual) / np.maximum(1., np.abs(expected)), initial=0.))
            maximum = max(maximum, error)
            if error > 1e-11:
                raise ValueError('Compiled native math differs from fresh interpreted evaluation.')
    accelerated = SimpleNamespace(**vars(model), **{})
    for name, function in functions.items(): setattr(accelerated, name, function)
    return accelerated, {'engine': 'Numba', 'version': numba.__version__,
        'llvmlite_version': llvmlite.__version__, 'fastmath': False, 'cache': False,
        'fresh_rate_equivalence_maximum_scaled_error': maximum,
        'scope': 'Numerical acceleration of the same restricted native math; not biological qualification.'}


def action_potential_duration(times, voltage, *, fraction=0.9):
    """Time from 10% upstroke to the final 90% repolarization crossing.

    Missing upstroke/repolarization is a refusal, not an arbitrarily truncated
    duration. The endpoint is cellular APD, never clinical QT.
    """
    if not len(times) == len(voltage) >= 3 or any(not math.isfinite(v) for v in (*times, *voltage)):
        raise ValueError('APD needs a finite matching native waveform.')
    peak = max(range(len(voltage)), key=voltage.__getitem__)
    rest, maximum = voltage[0], voltage[peak]
    if maximum - rest < 40 or peak == 0:
        raise ValueError('No qualified action-potential upstroke.')
    up = rest + 0.1 * (maximum - rest)
    down = rest + (1 - fraction) * (maximum - rest)
    def crossing(a, b, level):
        return times[a] + (times[b]-times[a]) * (level-voltage[a]) / (voltage[b]-voltage[a])
    start = next((crossing(i, i+1, up) for i in range(peak) if voltage[i] < up <= voltage[i+1]), None)
    ends = [crossing(i, i+1, down) for i in range(peak, len(times)-1) if voltage[i] > down >= voltage[i+1]]
    if start is None or not ends or voltage[-1] > down:
        raise ValueError('No completed 90% repolarization in the native observation window.')
    return {'value': ends[-1] - start, 'unit': 'ms', 'upstroke_time_ms': start,
        'repolarization_time_ms': ends[-1], 'method': 'Linear crossing on native sampled waveform; 10% upstroke to 90% repolarization',
        'clinical_inference': False}


def simulate(contract, payload, perturbation):
    import numpy as np
    import scipy
    from scipy.integrate import solve_ivp
    if not runtime_status()['available']:
        raise ValueError(runtime_status()['reason'])
    model, _, _, _, constants_by_name, states_by_name = inspect(contract, payload)
    if (perturbation.model_identifier != contract.identifier
            or contract.perturbable_parameters.get(perturbation.parameter) != perturbation.parameter_unit):
        raise ValueError('CellML parameter perturbation differs from the native contract.')
    protocol = contract.simulation_protocol
    required = {'kind', 'stimulus', 'warmup_cycles', 'sample_step_ms', 'max_step_ms', 'solver', 'justification'}
    if not required <= set(protocol) or protocol['kind'] != 'paced_peak_snapshot' or protocol['solver'] not in {'BDF', 'Radau'}:
        raise ValueError('Missing reviewed native pacing/snapshot protocol.')
    if not type(protocol['warmup_cycles']) is int or not 0 <= protocol['warmup_cycles'] <= 1500:
        raise ValueError('Native pacing warmup exceeds its bound.')
    if any(type(protocol[k]) not in (float, int) or not 0 < protocol[k] <= 10 for k in ('sample_step_ms', 'max_step_ms')):
        raise ValueError('Native sampling/step limits are invalid.')
    if len(set(perturbation.fractions_remaining)) != 1:
        raise ValueError('This protocol qualifies a fixed exposure snapshot, not an hours-long dynamic drug perturbation.')
    model, acceleration = _accelerate(model, protocol)
    curves, endpoints, initializations = [], [], []
    for condition, fraction in (('baseline', 1.), ('perturbed', perturbation.fractions_remaining[0])):
        y, rates, constants, computed, algebraic = _initialize(model,
            numpy_arrays=acceleration['engine'] == 'Numba')
        if acceleration['engine'] != 'interpreted_native_math':
            y, rates, constants, computed, algebraic = [np.asarray(v, dtype=np.float64) for v in (y, rates, constants, computed, algebraic)]
        overrides = protocol.get('constant_overrides', {})
        if not isinstance(overrides, dict) or len(overrides) > 16:
            raise ValueError('Native experimental controls exceed their reviewed bound.')
        for name, control in overrides.items():
            if (name == perturbation.parameter or name not in constants_by_name
                    or not isinstance(control, dict) or not control.get('source') or not control.get('justification')
                    or control.get('unit') != constants_by_name[name][1]['units']
                    or type(control.get('value')) not in (float, int) or not math.isfinite(control['value'])):
                raise ValueError('Native experimental control identity/unit/provenance mismatch: ' + name)
            constants[constants_by_name[name][0]] = control['value']
        control_index = constants_by_name[perturbation.parameter][0]
        baseline_parameter = constants[control_index]
        if not math.isfinite(baseline_parameter) or baseline_parameter <= 0:
            raise ValueError('Fractional current block requires a positive native parameter.')
        constants[control_index] = baseline_parameter * fraction
        # Derived native constants may depend on the perturbed conductance.
        # Recompute after all inputs, not before applying the drug fraction.
        model.compute_computed_constants(0., y, rates, constants, computed, algebraic)
        stimulus = {k: constants[constants_by_name[v][0]] for k, v in protocol['stimulus'].items()}
        if set(stimulus) != {'start', 'duration', 'period'} or not 0 <= stimulus['start'] < stimulus['period'] or not 0 < stimulus['duration'] < stimulus['period'] - stimulus['start']:
            raise ValueError('Native stimulus interface is incompatible with the pacing protocol.')
        period, start, duration = stimulus['period'], stimulus['start'], stimulus['duration']
        if perturbation.times != (0., period):
            raise ValueError('Exposure snapshot observation clock must equal one exact native pacing period.')
        grid = np.arange(0., period, protocol['sample_step_ms'])
        grid = np.append(grid, period)
        state_values = None
        nfev = 0
        last_difference = None
        for cycle in range(protocol['warmup_cycles'] + 1):
            before = np.array(y)
            collected = []
            for begin, end in zip((0., start, start+duration), (start, start+duration, period)):
                if begin == end:
                    continue  # Native models may pace at exactly time zero.
                def rhs(t, state):
                    clock = float(np.clip(t, np.nextafter(begin, end), np.nextafter(end, begin)))
                    model.compute_rates(clock, state, rates, constants, computed, algebraic)
                    if not np.isfinite(rates).all(): raise ValueError('Nonfinite native state rates.')
                    return np.asarray(rates).copy()
                run = solve_ivp(rhs, (begin, end), y, method=protocol['solver'],
                    rtol=contract.relative_tolerance, atol=contract.absolute_tolerance,
                    max_step=protocol['max_step_ms'], dense_output=cycle == protocol['warmup_cycles'])
                if not run.success: raise ValueError('Native CellML integration failed: ' + run.message)
                y = run.y[:, -1].copy()
                nfev += run.nfev
                if cycle == protocol['warmup_cycles']:
                    selected = grid[(grid >= begin) & (grid <= end)]
                    # Every supplied grid point once, including a non-grid pulse edge.
                    selected = selected if not collected else selected[selected > begin]
                    collected.append((selected, run.sol(selected)))
            last_difference = float(np.max(np.abs(np.array(y)-before) / np.maximum(1., np.abs(before))))
            if collected:
                sampled_times = np.concatenate([c[0] for c in collected])
                state_values = np.concatenate([c[1] for c in collected], axis=1)
        if state_values is None or not np.array_equal(sampled_times, grid) or not np.isfinite(state_values).all():
            raise ValueError('Native recorded waveform omitted samples or contains nonfinite values.')
        columns = ['time', *contract.outputs]
        rows = [[float(t), *(float(state_values[states_by_name[name][0], i]) for name in contract.outputs)]
            for i, t in enumerate(sampled_times)]
        curves.append({'condition': condition, 'columns': columns, 'values': rows})
        metrics = {}
        for name, unit in contract.outputs.items():
            if unit == 'millivolt':
                try: metrics[name + '.APD90'] = action_potential_duration(sampled_times.tolist(), state_values[states_by_name[name][0]].tolist())
                except ValueError as error: metrics[name + '.APD90'] = {'status': 'refused', 'reason': str(error), 'unit': 'ms'}
        endpoints.append({'condition': condition, 'metrics': metrics})
        initializations.append({'condition': condition, 'native_initialization': True,
            'baseline_parameter_value': baseline_parameter, 'fraction_remaining': fraction,
            'experimental_constant_overrides': overrides,
            'warmup_cycles': protocol['warmup_cycles'], 'normalized_last_cycle_state_difference': last_difference,
            'function_evaluations': nfev})
    response = {}
    for i, (output, unit) in enumerate(contract.outputs.items(), 1):
        baseline = [r[i] for r in curves[0]['values']]
        changed = [r[i] for r in curves[1]['values']]
        response[output] = {'unit': unit, 'baseline_final': baseline[-1], 'perturbed_final': changed[-1],
            'final_difference': changed[-1]-baseline[-1], 'maximum_absolute_difference': max(abs(a-b) for a,b in zip(baseline, changed, strict=True))}
    for output, base in endpoints[0]['metrics'].items():
        changed = endpoints[1]['metrics'][output]
        if 'value' in base and 'value' in changed:
            difference = changed['value']-base['value']
            response[output] = {'unit': 'ms', 'baseline_final': base['value'], 'perturbed_final': changed['value'],
                'final_difference': difference, 'maximum_absolute_difference': abs(difference)}
    return {'schema': 'pulsate.native-cellml-response/v1', 'model': contract.model_dump(mode='json'),
        'perturbation': perturbation.model_dump(mode='json'), 'curves': curves, 'response': response,
        'action_potential_endpoints': endpoints,
        'receipt': {'engine': 'SciPy', 'engine_version': scipy.__version__, 'integrator': protocol['solver'],
            'acceleration': acceleration,
            'native_source_sha256': contract.sha256, 'generated_python_sha256': contract.native_compilation['generated_python']['sha256'],
            'compilation_freshly_reperformed': False, 'native_equations_rewritten': False,
            'native_transfer_interface': native_interface(contract, payload, perturbation.parameter),
            'protocol': protocol, 'initializations': initializations, 'time_unit': 'ms',
            'relative_tolerance': contract.relative_tolerance, 'absolute_tolerance': contract.absolute_tolerance},
        'inference_level': contract.level, 'clinical_inference': False,
        'limitation': 'Single-cell paced response at a specified exposure snapshot; not a dynamic whole-organ, clinical QT, arrhythmia or safety prediction. Warmup does not by itself establish physiological steady state; last-cycle discrepancy is reported.'}
