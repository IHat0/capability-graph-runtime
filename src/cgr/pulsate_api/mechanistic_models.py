"""Hash-pinned established SBML models and auditable external perturbations.

No physiological equations are generated here. Candidate coupling requires a
reviewed target/parameter/compartment transfer contract; an assay or docking
score by itself does not license scaling an arbitrary biological parameter.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator, model_serializer

from .virtual_organism import canonical, digest


ENGINE_VERSION = '2.9.2'
SBML_VERSION = '5.21.1'
POLICY_VERSION = 'pulsate.sbml-mechanistic/v1'


def runtime_status(contract=None):
    """Missing optional numerical engines are explicit refusals, never results."""
    if contract is not None and contract.format == 'cellml_compiled':
        from .cellml_physiology import runtime_status as cellml_runtime
        return cellml_runtime()
    import importlib
    status = {'engine':'libRoadRunner', 'required_version':ENGINE_VERSION,
        'sbml_required_version':SBML_VERSION, 'sbml_runtime_version':None,
        'available':False, 'runtime_version':None, 'reason_code':'numerical_dependency_unavailable'}
    try:
        sbml = importlib.import_module('libsbml')
        engine = importlib.import_module('roadrunner')
    except ImportError:
        return dict(status, reason='The pinned SBML numerical dependencies are unavailable; no functional calculation ran.')
    status['runtime_version'] = getattr(engine, '__version__', None)
    status['sbml_runtime_version'] = sbml.getLibSBMLDottedVersion()
    if status['runtime_version'] != ENGINE_VERSION or status['sbml_runtime_version'] != SBML_VERSION:
        return dict(status, reason_code='numerical_version_unqualified',
            reason='The installed mechanistic engine version differs from the reviewed pinned version; no functional calculation ran.')
    return dict(status, available=True, reason_code=None, reason=None)


class ModelContract(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    identifier: str
    revision: str = Field(min_length=1)
    name: str
    path: str
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    source_url: str
    license: str = Field(min_length=1)
    parameter_provenance: str = Field(min_length=1)
    species: str = Field(min_length=1)
    tissue: str | None = None
    level: str  # cellular_functional or organ_physiology; never clinical
    time_unit: str
    outputs: dict[str, str]
    perturbable_parameters: dict[str, str]  # exact SBML identifiers -> SBML unit identifiers
    limitations: tuple[str, ...] = Field(min_length=1)
    biological_parameters: dict[str, dict] = Field(default_factory=dict)
    assignment_rules: dict[str, str] = Field(default_factory=dict)
    relative_tolerance: float = Field(default=1e-9, ge=1e-12, le=1e-6, allow_inf_nan=False)
    absolute_tolerance: float = Field(default=1e-11, ge=1e-24, le=1e-6, allow_inf_nan=False)
    numerical_justification: str | None = None
    format: str = 'sbml'
    native_compilation: dict | None = None
    simulation_protocol: dict | None = None

    @model_serializer(mode='wrap')
    def preserve_sbml_document(self, handler):
        document = handler(self)
        if self.format == 'sbml':
            for key in ('format', 'native_compilation', 'simulation_protocol'):
                document.pop(key, None)
        return document

    @model_validator(mode='after')
    def qualified_scope(self):
        if self.format not in {'sbml', 'cellml_compiled'}:
            raise ValueError('Unsupported native model format.')
        if self.level not in {'cellular_functional', 'organ_physiology'} or self.time_unit not in (
                {'s', 'min', 'h'} if self.format == 'sbml' else {'ms'}):
            raise ValueError('Unsupported mechanistic model scope/time unit.')
        if self.format == 'cellml_compiled' and (not self.native_compilation or not self.simulation_protocol):
            raise ValueError('Native CellML needs pinned compiler evidence and an explicit simulation protocol.')
        if self.format == 'sbml' and (self.native_compilation is not None or self.simulation_protocol is not None):
            raise ValueError('CellML compilation controls cannot alter an SBML contract.')
        if not 1 <= len(self.outputs) <= 32 or len(self.perturbable_parameters) > 32:
            raise ValueError('Model interface exceeds its bound.')
        if (self.relative_tolerance != 1e-9 or self.absolute_tolerance != 1e-11) and not self.numerical_justification:
            raise ValueError('Nondefault numerical tolerances require a predeclared unit/scale justification.')
        return self


class Perturbation(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    model_identifier: str
    parameter: str
    parameter_unit: str
    times: tuple[float, ...]
    fractions_remaining: tuple[float, ...]
    source: str = Field(min_length=1)
    classification: str
    exposure_sha256: str | None = None
    activity_sha256: str | None = None
    transfer_contract_sha256: str | None = None
    transfer_receipt: dict | None = None

    @model_validator(mode='after')
    def bounded(self):
        if (not 2 <= len(self.times) <= 4096 or len(self.times) != len(self.fractions_remaining)
                or self.times[0] != 0 or any(not math.isfinite(t) for t in self.times)
                or any(b <= a for a,b in zip(self.times, self.times[1:]))
                or any(not math.isfinite(f) or not 0 <= f <= 1 for f in self.fractions_remaining)):
            raise ValueError('Perturbation requires an ordered finite time course and bounded fractions.')
        if self.classification not in {'explicit_validation_perturbation', 'reviewed_exposure_activity_transfer'}:
            raise ValueError('Unqualified biological perturbation.')
        if self.classification == 'reviewed_exposure_activity_transfer' and not all(
                value and len(value) == 64 for value in (self.exposure_sha256, self.activity_sha256, self.transfer_contract_sha256)):
            raise ValueError('Exposure coupling requires the complete upstream evidence chain.')
        if self.classification == 'reviewed_exposure_activity_transfer' and not self.transfer_receipt:
            raise ValueError('Exposure coupling requires a semantic qualification receipt.')
        return self


def load_models(path):
    catalogue = Path(path).resolve(strict=True)
    payload = catalogue.read_bytes()
    if len(payload) > 2 * 1024 * 1024: raise ValueError('Mechanistic catalogue exceeds its bound.')
    doc = json.loads(payload)
    if doc.get('schema') != POLICY_VERSION or not doc.get('reviewer'):
        raise ValueError('Mechanistic catalogue is not versioned/reviewed.')
    models = []
    for item in doc.get('models', []):
        if len(models) >= 32: raise ValueError('Mechanistic catalogue exceeds its model bound.')
        contract = ModelContract.model_validate(item)
        model_path = (catalogue.parent / contract.path).resolve(strict=True)
        if not model_path.is_relative_to(catalogue.parent) or model_path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError('Model path exceeds trusted directory/size boundary.')
        sbml = model_path.read_bytes()
        if digest(sbml) != contract.sha256: raise ValueError('Mechanistic model hash mismatch.')
        if contract.format == 'cellml_compiled':
            from .cellml_physiology import load_compilation
            sbml = load_compilation(contract, sbml, catalogue.parent)
        models.append((contract, sbml))
    if len({m.identifier for m,_ in models}) != len(models): raise ValueError('Duplicate model identity.')
    return models, digest(payload)


def inspect_sbml(contract, payload):
    import libsbml
    if libsbml.getLibSBMLDottedVersion() != SBML_VERSION:
        raise ValueError('SBML transformation engine differs from the frozen version.')
    if digest(payload) != contract.sha256: raise ValueError('SBML artifact hash mismatch.')
    if b'<!ENTITY' in payload or b'<!DOCTYPE' in payload:
        raise ValueError('External entity declarations are not allowed in models.')
    doc = libsbml.readSBMLFromString(payload.decode('utf-8'))
    severe = [doc.getError(i).getMessage() for i in range(doc.getNumErrors())
        if doc.getError(i).getSeverity() >= libsbml.LIBSBML_SEV_ERROR]
    model = doc.getModel()
    if severe or model is None: raise ValueError('SBML parse error: ' + '; '.join(severe))
    if model.getNumEvents():
        raise ValueError('This perturbation adapter does not qualify SBML events.')
    # Assignment rules are evaluated by the native engine, never discarded or
    # rewritten. Only a predeclared exact formula manifest can authorize them.
    # Rate/algebraic rules remain outside this adapter's replay contract.
    actual_rules = {}
    for rule in model.getListOfRules():
        if not rule.isAssignment() or rule.getVariable() in actual_rules:
            raise ValueError('This perturbation adapter does not qualify these SBML rules.')
        actual_rules[rule.getVariable()] = libsbml.formulaToL3String(rule.getMath())
    if actual_rules != contract.assignment_rules:
        raise ValueError('SBML assignment rules differ from the predeclared exact equation manifest.')
    properties = libsbml.ConversionProperties()
    properties.addOption('promoteLocalParameters', True)
    if doc.convert(properties) != libsbml.LIBSBML_OPERATION_SUCCESS:
        raise ValueError('Equation-preserving local-parameter promotion failed.')
    model = doc.getModel()
    time_definition = model.getUnitDefinition(model.getTimeUnits() or 'time')
    factor = 1.
    if time_definition:
        if time_definition.getNumUnits() != 1:
            raise ValueError('Unsupported compound SBML time unit.')
        unit = time_definition.getUnit(0)
        if unit.getKind() != libsbml.UNIT_KIND_SECOND or unit.getExponent() != 1:
            raise ValueError('SBML model time unit is not a time dimension.')
        factor = unit.getMultiplier() * 10 ** unit.getScale()
    elif model.getLevel() >= 3 and not model.getTimeUnits():
        raise ValueError('SBML Level 3 model has unspecified time units.')
    if not math.isclose(factor, {'s':1.,'min':60.,'h':3600.}[contract.time_unit]):
        raise ValueError('Declared time unit differs from the SBML model.')
    for name, unit in contract.perturbable_parameters.items():
        parameter = model.getParameter(name)
        if parameter is None or not parameter.getConstant() or (parameter.getUnits() or 'not_declared') != unit:
            raise ValueError('Perturbation parameter identity/unit/constant contract mismatch: ' + name)
        if not math.isfinite(parameter.getValue()) or parameter.getValue() < 0:
            raise ValueError('Unsupported baseline parameter value.')
    for output, declared in contract.outputs.items():
        name = output.strip('[]')
        species = model.getSpecies(name)
        if species is None: raise ValueError('Unknown model output identifier: ' + output)
        actual = species.getSubstanceUnits() or model.getSubstanceUnits() or 'substance'
        if output.startswith('['):
            compartment = model.getCompartment(species.getCompartment())
            actual += '/' + (compartment.getUnits() or model.getVolumeUnits() or 'volume')
        if actual != declared: raise ValueError('SBML output unit contract mismatch: ' + output)
    # The returned native interface outlives this SBMLDocument. SWIG child
    # pointers do not own their parent document; return an owning clone rather
    # than exposing a model whose C++ storage can already have been released.
    return model.clone(), libsbml.writeSBMLToString(doc).encode('utf-8')


def _runner(payload, contract=None):
    import roadrunner
    if roadrunner.__version__ != ENGINE_VERSION:
        raise ValueError('Mechanistic numerical engine differs from the frozen version.')
    rr = roadrunner.RoadRunner(payload.decode('utf-8'))
    rr.setIntegrator('cvode')
    rr.integrator.setValue('relative_tolerance', contract.relative_tolerance if contract else 1e-9)
    absolute = contract.absolute_tolerance if contract else 1e-11
    # A scalar RoadRunner tolerance is scaled by the current state on restart.
    # Repeated piecewise runs can drive that scale to zero as pools deplete.
    # Explicit per-state absolute tolerances preserve the declared error budget
    # at every interval; no species values are clamped or equations modified.
    rr.integrator.setValue('absolute_tolerance', absolute)
    count = len(rr.integrator.getAbsoluteToleranceVector())
    rr.integrator.setValue('absolute_tolerance', [absolute] * count)
    rr.integrator.setValue('maximum_num_steps', 100000)
    return rr


def _math_symbols(node, model, active_functions=()):
    """Exact AST dependencies, not biological qualification by symbol names."""
    if node is None:
        return set()
    names = {node.getName()} if node.isName() else set()
    for index in range(node.getNumChildren()):
        names.update(_math_symbols(node.getChild(index), model, active_functions))
    # Function arguments above and external symbols in a function body both
    # matter. Bound lambda arguments are not external model dependencies.
    function = model.getFunctionDefinition(node.getName()) if node.isFunction() else None
    if function is not None:
        identifier = function.getId()
        if identifier in active_functions:
            raise ValueError('Recursive model functions have no qualified dependency replay.')
        body = _math_symbols(function.getBody(), model, (*active_functions, identifier))
        bound = {function.getArgument(i).getName() for i in range(function.getNumArguments())}
        names.update(body - bound)
    return names


def native_influence_paths(model, symbol, outputs):
    """Conservative native equation reachability; not proof of drug response.

    A disconnected input can never justify an exposure-to-output transfer.
    Reaching an output is necessary, but kinetics, calibration, concentration
    bases and observed validation remain separate mandatory scientific gates.
    """
    edges = {}
    for rule in model.getListOfRules():
        if not rule.isAssignment():
            raise ValueError('Native dependency replay requires qualified assignment rules only.')
        for name in _math_symbols(rule.getMath(), model):
            edges.setdefault(name, set()).add(rule.getVariable())
    for reaction in model.getListOfReactions():
        law = reaction.getKineticLaw()
        if law is None or law.getMath() is None:
            raise ValueError('Reaction has no qualified native kinetic equation.')
        dependencies = _math_symbols(law.getMath(), model)
        dependencies.difference_update(p.getId() for p in law.getListOfParameters())
        net = {}
        for sign, participants in ((-1, reaction.getListOfReactants()), (1, reaction.getListOfProducts())):
            for reference in participants:
                if reference.isSetStoichiometryMath():
                    raise ValueError('Dynamic stoichiometry is outside the native transfer dependency contract.')
                net[reference.getSpecies()] = net.get(reference.getSpecies(), 0) + sign * reference.getStoichiometry()
        affected = {name for name, change in net.items()
            if change != 0 and not model.getSpecies(name).getBoundaryCondition()}
        for name in dependencies:
            edges.setdefault(name, set()).update(affected)
    for output in outputs:
        identifier = output.strip('[]')
        if output.startswith('['):
            species = model.getSpecies(identifier)
            if species is None:
                raise ValueError('Native transfer output species is absent.')
            # Concentration depends on volume too; amount alone does not.
            edges.setdefault(species.getCompartment(), set()).add(identifier)
    if len(edges) > 65536:
        raise ValueError('Native dependency graph exceeds its bound.')
    pending, paths = [symbol], {symbol: [symbol]}
    for current in pending:
        for next_symbol in sorted(edges.get(current, ())):
            if next_symbol not in paths:
                paths[next_symbol] = [*paths[current], next_symbol]
                pending.append(next_symbol)
    return {output: paths[output.strip('[]')] for output in sorted(outputs) if output.strip('[]') in paths}


def native_transfer_interface(contract, model, parameter):
    """Replay the physical unit and actual equation path at execution time."""
    import libsbml
    native = model.getParameter(parameter)
    unit_name = contract.perturbable_parameters.get(parameter)
    if native is None or not unit_name or unit_name == 'not_declared' or native.getUnits() != unit_name:
        raise ValueError('Candidate transfer has no exact declared native parameter unit.')
    definition = model.getUnitDefinition(unit_name)
    if definition is not None:
        components = [{'kind': libsbml.UnitKind_toString(unit.getKind()),
            'exponent': unit.getExponent(), 'scale': unit.getScale(), 'multiplier': unit.getMultiplier()}
            for unit in definition.getListOfUnits()]
    else:
        kind = libsbml.UnitKind_forName(unit_name)
        if kind == libsbml.UNIT_KIND_INVALID:
            raise ValueError('Candidate transfer native unit identifier has no physical definition.')
        components = [{'kind': libsbml.UnitKind_toString(kind), 'exponent': 1, 'scale': 0, 'multiplier': 1.}]
    if not components or any(c['kind'] == 'invalid' or not math.isfinite(c['exponent'])
            or not math.isfinite(c['multiplier']) or c['multiplier'] <= 0 for c in components):
        raise ValueError('Candidate transfer native unit definition is invalid.')
    if not math.isfinite(native.getValue()) or native.getValue() <= 0:
        raise ValueError('Fractional inhibition requires a positive native baseline parameter.')
    paths = native_influence_paths(model, parameter, contract.outputs)
    if not paths:
        raise ValueError('Candidate transfer parameter is disconnected from every declared native output.')
    consistency = native_model_consistency(model)
    return {'schema': 'pulsate.native-transfer-interface/v1', 'model_sha256': contract.sha256,
        'parameter': parameter, 'native_unit': unit_name, 'unit_components': components,
        'baseline_parameter_value': native.getValue(), 'output_dependency_paths': paths,
        'native_consistency': consistency,
        'scope': 'Necessary native unit/equation connectivity check only; not calibration, observed validation or clinical qualification.'}


def native_model_consistency(model):
    """Do not infer missing dimensions or tolerate invalid candidate networks.

    Parsing SBML successfully does not establish component or dimensional
    consistency. Explicit mathematical controls remain controls, but a candidate
    transfer cannot rely on an undeclared species dependency or uncheckable unit.
    No source equations, parameter values or units are repaired here.
    """
    import libsbml
    document = libsbml.SBMLDocument(model.getSBMLNamespaces())
    if document.setModel(model) != libsbml.LIBSBML_OPERATION_SUCCESS:
        raise ValueError('Native candidate model cannot be copied for consistency verification.')
    document.checkConsistency()
    errors = [document.getError(i) for i in range(document.getNumErrors())]
    severe = [error for error in errors if error.getSeverity() >= libsbml.LIBSBML_SEV_ERROR]
    if severe:
        raise ValueError('Native candidate model has invalid SBML component/equation consistency: '
            + '; '.join(error.getShortMessage() for error in severe[:16]))
    unit_messages = [error for error in errors if error.getCategory() == libsbml.LIBSBML_CAT_UNITS_CONSISTENCY]
    if unit_messages:
        raise ValueError('Native candidate model dimensions are inconsistent or cannot be fully checked: '
            + '; '.join(error.getShortMessage() for error in unit_messages[:16]))
    scales = native_equation_scales(model)
    return {'engine': 'libSBML', 'version': libsbml.getLibSBMLDottedVersion(),
        'component_consistency': 'passed', 'unit_consistency': 'passed',
        'native_equation_unit_scales': scales,
        'other_messages': [{'id': error.getErrorId(), 'severity': error.getSeverityAsString(),
            'category': error.getCategoryAsString(), 'message': error.getShortMessage()} for error in errors],
        'unit_inference': False, 'source_model_changes': False,
        'scope': 'Necessary native structural/dimensional validity, not assay calibration or biological validation.'}


def native_equation_scales(model):
    """Check physical scale as well as dimensions; never assume unit conversion.

    A rate annotated per hour is not a native per-second rate merely because
    both dimensions are inverse time. This adapter does not silently transform
    equations or parameter values to repair such a source encoding.
    """
    import libsbml

    def resolve(identifier):
        definition = model.getUnitDefinition(identifier)
        if definition is not None:
            return definition.clone()
        kind = libsbml.UnitKind_forName(identifier)
        if kind == libsbml.UNIT_KIND_INVALID:
            raise ValueError('Native equation unit has no physical definition.')
        definition = libsbml.UnitDefinition(model.getLevel(), model.getVersion())
        unit = definition.createUnit(); unit.setKind(kind); unit.setExponent(1)
        unit.setScale(0); unit.setMultiplier(1)
        return definition

    def signature(definition):
        if definition is None or not definition.getNumUnits():
            raise ValueError('Native equation units cannot be determined.')
        si = libsbml.UnitDefinition.convertToSI(definition)
        dimensions, factor = {}, 1.
        for unit in si.getListOfUnits():
            kind = libsbml.UnitKind_toString(unit.getKind())
            if kind == 'invalid' or unit.getOffset() != 0:
                raise ValueError('Native equation units are not multiplicative physical dimensions.')
            exponent = unit.getExponent()
            factor *= (unit.getMultiplier() * 10. ** unit.getScale()) ** exponent
            if kind != 'dimensionless':
                dimensions[kind] = dimensions.get(kind, 0.) + exponent
        if not math.isfinite(factor) or factor <= 0:
            raise ValueError('Native equation unit scale is invalid.')
        return {'si_dimensions': {k: v for k, v in sorted(dimensions.items()) if v}, 'si_factor': factor}

    substance = model.getExtentUnits() or ('substance' if model.getUnitDefinition('substance') else 'mole')
    time = model.getTimeUnits() or ('time' if model.getUnitDefinition('time') else 'second')
    expected = signature(resolve(substance))
    clock = signature(resolve(time))
    expected['si_factor'] /= clock['si_factor']
    for kind, exponent in clock['si_dimensions'].items():
        expected['si_dimensions'][kind] = expected['si_dimensions'].get(kind, 0.) - exponent
    expected['si_dimensions'] = {k: v for k, v in sorted(expected['si_dimensions'].items()) if v}
    checks = []
    for reaction in model.getListOfReactions():
        law = reaction.getKineticLaw()
        if law is None or law.containsUndeclaredUnits():
            raise ValueError('Native reaction units contain undeclared quantities.')
        actual = signature(law.getDerivedUnitDefinition())
        if (actual['si_dimensions'] != expected['si_dimensions']
                or not math.isclose(actual['si_factor'], expected['si_factor'], rel_tol=1e-12, abs_tol=0.)):
            raise ValueError('Native reaction physical unit scale differs from model extent/time: ' + reaction.getId())
        checks.append({'reaction': reaction.getId(), 'rate': actual, 'expected_extent_per_time': dict(expected)})
    for rule in model.getListOfRules():
        target = model.getElementBySId(rule.getVariable())
        if target is None or rule.containsUndeclaredUnits():
            raise ValueError('Native assignment units contain undeclared quantities.')
        actual, expected_rule = signature(rule.getDerivedUnitDefinition()), signature(target.getDerivedUnitDefinition())
        if (actual['si_dimensions'] != expected_rule['si_dimensions']
                or not math.isclose(actual['si_factor'], expected_rule['si_factor'], rel_tol=1e-12, abs_tol=0.)):
            raise ValueError('Native assignment physical unit scale differs from its target: ' + rule.getVariable())
        checks.append({'assignment': rule.getVariable(), 'expression': actual, 'target': expected_rule})
    return {'checks': checks, 'scale_tolerance': 'Relative 1e-12 for dimension metadata arithmetic only; no biological acceptance tolerance.'}


def simulate(contract, payload, perturbation):
    """Piecewise constant, explicitly sampled perturbation; no hidden exposure interpolation."""
    if contract.format == 'cellml_compiled':
        from .cellml_physiology import simulate as simulate_cellml
        return simulate_cellml(contract, payload, perturbation)
    model, executable = inspect_sbml(contract, payload)
    if perturbation.model_identifier != contract.identifier or contract.perturbable_parameters.get(perturbation.parameter) != perturbation.parameter_unit:
        raise ValueError('Model/parameter/unit perturbation mapping mismatch.')
    interface = None
    if perturbation.classification == 'reviewed_exposure_activity_transfer':
        interface = native_transfer_interface(contract, model, perturbation.parameter)
    import libsbml
    unit_definitions = {definition.getId(): [{'kind': libsbml.UnitKind_toString(unit.getKind()),
        'exponent': unit.getExponent(), 'scale': unit.getScale(), 'multiplier': unit.getMultiplier()}
        for unit in definition.getListOfUnits()] for definition in model.getListOfUnitDefinitions()}
    selections = ['time', *contract.outputs]
    curves = []
    for name, fractions in (('baseline', [1.] * len(perturbation.times)), ('perturbed', perturbation.fractions_remaining)):
        rr = _runner(executable, contract)
        base_value = float(rr[perturbation.parameter])
        rows = [[0., *(float(rr[s]) for s in selections[1:])]]
        for i,(start,end) in enumerate(zip(perturbation.times, perturbation.times[1:])):
            rr[perturbation.parameter] = base_value * fractions[i]
            result = rr.simulate(start, end, 2, selections=selections)
            rows.append([float(v) for v in result[-1]])
        if any(not math.isfinite(v) for row in rows for v in row): raise ValueError('Nonfinite mechanistic output.')
        curves.append({'condition': name, 'columns': selections, 'values': rows})
    response = {}
    for i,(output,unit) in enumerate(contract.outputs.items(), 1):
        baseline = [r[i] for r in curves[0]['values']]
        perturbed = [r[i] for r in curves[1]['values']]
        response[output] = {'unit': unit, 'baseline_final': baseline[-1], 'perturbed_final': perturbed[-1],
            'final_difference': perturbed[-1] - baseline[-1],
            'maximum_absolute_difference': max(abs(a-b) for a,b in zip(baseline, perturbed, strict=True))}
    report = {'schema': POLICY_VERSION, 'model': contract.model_dump(mode='json'),
        'perturbation': perturbation.model_dump(mode='json'), 'curves': curves, 'response': response,
        'receipt': {'engine': 'libRoadRunner', 'engine_version': ENGINE_VERSION, 'integrator': 'CVODE',
            'sbml_engine':'libSBML', 'sbml_engine_version':SBML_VERSION,
            'relative_tolerance': contract.relative_tolerance, 'absolute_tolerance': contract.absolute_tolerance,
            'numerical_justification': contract.numerical_justification, 'sbml_sha256': digest(payload),
            'absolute_tolerance_semantics': 'Explicit fixed per-native-state vector; not dynamically rescaled by depleted concentrations on restart',
            'executable_sbml_sha256': digest(executable),
            'sbml_unit_definitions': unit_definitions,
            'assignment_rule_manifest': actual_assignment_rules(contract),
            'unit_scope': 'Native declared SBML units, expanded into kind/exponent/decimal scale/multiplier; undeclared parameter units remain unqualified.',
            'transformation': 'libSBML promoteLocalParameters; no biological equations added or altered',
            'perturbation_sha256': digest(canonical(perturbation.model_dump(mode='json'))),
            'sampling': 'External perturbation held constant over each supplied interval; endpoints do not imply continuous exact exposure'},
        'inference_level': contract.level, 'clinical_inference': False,
        'limitation': 'Predicted model response under the declared perturbation, not an observed drug effect or evidence of clinical safety.'}
    if interface is not None:
        report['receipt']['native_transfer_interface'] = interface
    return report


def actual_assignment_rules(contract):
    """Already compared against the preserved SBML before execution."""
    return dict(contract.assignment_rules)


def verify_simulation(report, payload):
    contract = ModelContract.model_validate(report['model'])
    perturbation = Perturbation.model_validate(report['perturbation'])
    expected = simulate(contract, payload, perturbation)
    if canonical(expected) != canonical(report): raise ValueError('Mechanistic output/receipt failed independent replay.')
    return {'passed': True, 'report_sha256': digest(canonical(report)),
        'scope': 'Native model hash, declared identifiers/units and fresh numerical replay; not biological validation.'}


def exposure_perturbation(model, transfer, series, activity, *, evidence_eligible, evidence_sources=None, qualification_cache=None):
    """Only reviewed fractional-activity transfer models; no Vina -> potency."""
    if not evidence_eligible: raise ValueError('Quantitative transfer evidence is not prospectively eligible.')
    if ('functional_assay_sha256' in activity or 'functional_prediction_sha256' in activity) and (
            activity.get('functional_transfer_supported') is not True
            or activity.get('assay_type') != 'functional'
            or activity.get('functional_direction') not in {'inhibitor', 'blocker'}
            or activity.get('kind') != 'IC50'):
        raise ValueError('The functional assay does not support a qualified inhibitory transfer; nominal/binding/other endpoint evidence remains separate.')
    if transfer.get('schema') != 'pulsate.reviewed-inhibition-transfer/v2' or not transfer.get('reviewer') or not transfer.get('source'):
        raise ValueError('No reviewed quantitative activity-to-model transfer contract.')
    if transfer.get('parameter_unit') == 'not_declared':
        raise ValueError('Undeclared model parameter units cannot support candidate physiological coupling.')
    if transfer.get('law') != 'reversible_fractional_activity' or activity.get('kind') not in transfer.get('compatible_activity_kinds', []):
        raise ValueError('Activity endpoint does not justify this parameter-transfer law.')
    if (series.get('species') != model.species or activity.get('species') != model.species
            or series.get('organ') != model.tissue or activity.get('target_accession') != transfer.get('target_accession')
            or activity.get('organ') != series.get('organ')
            or activity.get('compartment') != series.get('compartment')
            or transfer.get('model_identifier') != model.identifier
            or model.perturbable_parameters.get(transfer.get('parameter')) != transfer.get('parameter_unit')
            or series.get('concentration_basis') != 'unbound' or activity.get('concentration_basis') != 'unbound'
            or series.get('unit') != 'umol/l' or activity.get('unit') != 'umol/l'
            or series.get('compartment') != transfer.get('compartment')
            or activity.get('assay_context') != transfer.get('qualified_assay_context')):
        raise ValueError('Species, target, tissue, free-concentration basis or assay context is incompatible.')
    receipt = qualify_transfer(model, transfer, series, activity, evidence_sources=evidence_sources,
        qualification_cache=qualification_cache)
    potency, hill = activity.get('value'), transfer.get('hill_coefficient')
    if not all(type(v) in (float,int) and math.isfinite(v) and v > 0 for v in (potency, hill)):
        raise ValueError('Missing positive quantitative potency or qualified Hill coefficient.')
    if not transfer.get('uncertainty') or not activity.get('uncertainty'):
        raise ValueError('Missing quantitative activity/transfer uncertainty disclosure.')
    concentrations = series['values_umol_l']
    if any(not math.isfinite(c) or c < 0 for c in concentrations): raise ValueError('Invalid exposure concentrations.')
    fractions = tuple(1 / (1 + (c / potency) ** hill) for c in concentrations)
    if model.format == 'cellml_compiled':
        if (transfer.get('temporal_policy') != 'sampled_unbound_peak_snapshot'
                or activity.get('functional_direction') not in {'inhibitor', 'blocker'}
                or activity.get('assay_type') != 'functional'
                or activity.get('hill_coefficient') != hill):
            raise ValueError('Native current block requires sourced functional direction/Hill slope and a reviewed exposure-snapshot policy.')
        period = model.simulation_protocol.get('observation_period_ms')
        if type(period) not in (float, int) or not math.isfinite(period) or not 0 < period <= 10000:
            raise ValueError('Missing bounded native observation period for exposure snapshot.')
        peak = max(concentrations)
        fraction = min(fractions)
        receipt = dict(receipt, temporal_policy={'kind': 'sampled_unbound_peak_snapshot',
            'peak_umol_l': peak, 'sampled_peak_times_h': [t for t,c in zip(series['times_h'], concentrations, strict=True) if c == peak],
            'exposure_evidence_kind': series.get('evidence_kind', 'native_pbpk_timecourse'),
            'timecourse_available': series.get('timecourse_available', True),
            'exposure_clock_unit': 'h', 'model_clock_unit': 'ms', 'whole_exposure_sha256': digest(canonical(series)),
            'scope': 'Paced cellular response at sampled peak exposure; not hours-long dynamic physiology or a measured response.'})
        return Perturbation(model_identifier=model.identifier, parameter=transfer['parameter'],
            parameter_unit=transfer['parameter_unit'], times=(0., float(period)), fractions_remaining=(fraction, fraction),
            source=transfer['source'], classification='reviewed_exposure_activity_transfer',
            exposure_sha256=digest(canonical(series)), activity_sha256=digest(canonical(activity)),
            transfer_contract_sha256=digest(canonical(transfer)), transfer_receipt=receipt)
    time_factors = {'s': 3600., 'min': 60., 'h': 1., 'ms': 3600000.}
    return Perturbation(model_identifier=model.identifier, parameter=transfer['parameter'],
        parameter_unit=transfer['parameter_unit'], times=tuple(t * time_factors[model.time_unit] for t in series['times_h']),
        fractions_remaining=fractions, source=transfer['source'], classification='reviewed_exposure_activity_transfer',
        exposure_sha256=digest(canonical(series)), activity_sha256=digest(canonical(activity)),
        transfer_contract_sha256=digest(canonical(transfer)), transfer_receipt=receipt)


def qualify_transfer(model, transfer, series, activity, *, evidence_sources=None, qualification_cache=None):
    """An accession/string match is necessary but never biological qualification.

    This narrow law supports calibrated reversible inhibition of a declared
    fractional-activity parameter only. It does not turn binding Kd/Ki into
    inhibition of any arbitrary rate, enzyme abundance or physiological output.
    """
    required = ('version','reviewer_qualification','review_status','molecular_species',
        'parameter_meaning','direction','equation','assumptions','calibration_evidence',
        'applicability_domain','uncertainty','model_sha256','calibration_sha256')
    if not all(transfer.get(k) for k in required) or transfer['review_status'] not in {'qualified','research_validated','exploratory_reviewed'}:
        raise ValueError('Transfer scientific qualification is missing, pending or rejected.')
    research = None
    if transfer['review_status'] in {'research_validated','exploratory_reviewed'}:
        from .research_qualification import research_review
        research = research_review(transfer,evidence_sources,model_sha256=model.sha256, replay_cache=qualification_cache)
    if transfer['model_sha256'] != model.sha256:
        raise ValueError('Biological transfer was qualified for a different model revision.')
    calibration=transfer['calibration_evidence']
    if (not isinstance(calibration,dict) or digest(canonical(calibration)) != transfer['calibration_sha256']
            or not all(calibration.get(k) for k in ('original_source_url','original_source_sha256',
                'record_identifier','exact_parameter_mapping','validated_equation','assay_context',
                'applicability_basis','reviewer','reviewer_qualification','review_status'))
            or calibration['review_status']!=transfer['review_status']
            or len(calibration['original_source_sha256'])!=64):
        raise ValueError('Transfer requires a preserved calibration contract at the declared review scope.')
    from .prospective_evidence import allowed_source
    from .primary_measurements import locate, same_value
    raw=(evidence_sources or {}).get(calibration['original_source_sha256'])
    if (raw is None or digest(raw)!=calibration['original_source_sha256'] or len(raw)>16*1024*1024
            or not allowed_source(calibration['original_source_url'])
            or not isinstance(calibration.get('supporting_spans'),list) or not calibration['supporting_spans']):
        raise ValueError('Original calibration source bytes/exact qualification spans are missing.')
    for span in calibration['supporting_spans']:
        extracted=locate(raw,calibration.get('source_format','json'),span['pointer'],
            pdf_reviews=calibration.get('pdf_reviews',()),sources=evidence_sources)
        if not same_value(extracted,span['expected']):
            raise ValueError('Calibration qualification span failed original-byte replay.')
    expected_equation='fractional_activity = 1 / (1 + (unbound_concentration / functional_IC50) ** hill_coefficient)'
    if (transfer['equation'] != expected_equation or calibration['validated_equation'] != expected_equation
            or transfer['direction']!='inhibition' or activity.get('kind')!='IC50'
            or transfer.get('compatible_activity_kinds')!=['IC50']):
        raise ValueError('Activity type/equation/direction does not qualify a functional fractional-activity law.')
    annotation=model.biological_parameters.get(transfer.get('parameter'))
    mapping=calibration['exact_parameter_mapping']
    expected={'model_sha256':model.sha256,'parameter':transfer.get('parameter'),
        'target_accession':transfer.get('target_accession'),'species':model.species,'tissue':model.tissue,
        'molecular_species':transfer['molecular_species'],'parameter_meaning':transfer['parameter_meaning'],
        'parameter_unit':transfer.get('parameter_unit'),'transfer_law':
            'reviewed_fractional_activity' if transfer['review_status']=='exploratory_reviewed' else 'calibrated_fractional_activity'}
    if not annotation or any(annotation.get(k)!=v or mapping.get(k)!=v for k,v in expected.items()):
        raise ValueError('No reviewed biological parameter/target/molecular-species mapping in the model contract.')
    domain=transfer['applicability_domain']
    if (not isinstance(domain,dict) or domain.get('species')!=model.species
            or domain.get('tissue')!=model.tissue or domain.get('compartment')!=series.get('compartment')
            or domain.get('concentration_basis')!='unbound' or domain.get('concentration_unit')!='umol/l'
            or not all(domain.get(k) for k in ('biological_context','exposure_to_assay_equivalence','limitations'))
            or activity.get('assay_context')!=calibration['assay_context']):
        raise ValueError('Transfer applicability/assay/exposure equivalence is unqualified.')
    bounds=domain.get('unbound_concentration_bounds_umol_l')
    if (not isinstance(bounds,list) or len(bounds)!=2
            or any(type(v) not in (float,int) or not math.isfinite(v) or v<0 for v in bounds)
            or bounds[1]<=bounds[0]
            or any(not bounds[0]<=c<=bounds[1] for c in series['values_umol_l'])):
        raise ValueError('Exposure lies outside the independently calibrated transfer domain.')
    if domain.get('activity_unit')!='umol/l' or domain.get('native_parameter_unit')!=transfer.get('parameter_unit'):
        raise ValueError('Transfer native/exposure/activity dimensions are not qualified.')
    exploratory = transfer['review_status'] == 'exploratory_reviewed'
    return {'schema':'pulsate.mechanistic-transfer-qualification/v1', 'qualified':not exploratory,
        'qualification_state':'exploratory_only' if exploratory else 'qualified_for_research_signal',
        'candidate_decision_authority':not exploratory,
        'qualification_scope':research or {'scope':'deployment_reviewed_model_hypothesis',
            'independent_scientific_review':False,'clinical_qualification':False},
        'model_parameter_mapping':expected,'equation':expected_equation,'direction':'inhibition',
        'calibration_sha256':transfer['calibration_sha256'],'original_calibration_source_sha256':calibration['original_source_sha256'],
        'reviewer':transfer['reviewer'],'reviewer_qualification':transfer['reviewer_qualification'],
        'applicability_domain':domain,'assumptions':transfer['assumptions'],'uncertainty':transfer['uncertainty'],
        'scope':'Reviewed model hypothesis only. Calibration provenance must be independently replayed before numerical execution; no clinical inference.'}
