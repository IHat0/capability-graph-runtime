"""Internal research validation is auditable, but is never scientific certification.

Both routes preserve identity, units, primary evidence and applicability checks.
Qualified research needs passing observed-benchmark evidence. Exploratory review
retains a failed observed-accuracy or uncertainty gate, displays the discrepancy,
and grants no candidate-decision authority. Neither is independent certification.
"""
import json
import math
import re

from .primary_measurements import locate, same_value, normalize_value
from .prospective_evidence import allowed_source
from .virtual_organism import canonical, digest


GATES = (
    'model_and_source_provenance', 'inputs_and_units', 'observed_benchmark',
    'supported_domain', 'compatible_concentration_bases', 'bounded_uncertainty',
    'nonclinical_scope', 'critical_failure_refusal',
)


def research_review(document, sources, *, model_sha256, replay_cache=None):
    """Replay an internally validated research package; never accept a label alone."""
    exploratory = document.get('review_status') == 'exploratory_reviewed'
    package = document.get('research_qualification')
    if (document.get('review_status') not in {'research_validated', 'exploratory_reviewed'} or not isinstance(package, dict)
            or digest(canonical(package)) != document.get('research_qualification_sha256')
            or package.get('schema') != 'pulsate.research-qualification/v1'
            or package.get('model_sha256') != model_sha256
            or package.get('independent_scientific_review') is not False
            or package.get('clinical_qualification') is not False
            or package.get('clinical_inference') is not False
            or not all(package.get(k) for k in ('version', 'reviewer', 'review_basis',
                'equations_provenance', 'parameter_provenance', 'unit_conversions',
                'translation_rules', 'applicability_domain', 'uncertainty', 'limitations'))):
        raise ValueError('Research validation requires an explicit, versioned nonclinical audit package; it is not independent certification.')
    if exploratory and (package.get('qualification_state') != 'exploratory_only'
            or package.get('candidate_decision_authority') is not False
            or not package.get('predeclared_protocol_sha256')
            or not package.get('benchmark_discrepancy')):
        raise ValueError('Exploratory review requires frozen criteria, displayed discrepancy and no candidate-decision authority.')
    gates = package.get('gates', {})
    if set(gates) != set(GATES):
        raise ValueError('The complete eight-gate research contract is required.')
    for name, gate in gates.items():
        permitted = {'passed', 'failed'} if exploratory and name in {'observed_benchmark', 'bounded_uncertainty'} else {'passed'}
        if (not isinstance(gate, dict) or gate.get('status') not in permitted
                or not gate.get('basis') or not isinstance(gate.get('evidence_sha256'),list)
                or not 1 <= len(gate['evidence_sha256']) <= 128
                or any(not isinstance(sha,str) or not re.fullmatch(r'[0-9a-f]{64}',sha) for sha in gate['evidence_sha256'])
                or len(set(gate['evidence_sha256'])) != len(gate['evidence_sha256'])):
            raise ValueError('Critical research gate is missing or failed: ' + name)
        for sha in gate['evidence_sha256']:
            raw = (sources or {}).get(sha)
            if raw is None or len(raw) > 16 * 1024 * 1024 or digest(raw) != sha:
                raise ValueError('Research gate evidence is absent or altered: ' + name)
    if exploratory:
        protocol_sha = package['predeclared_protocol_sha256']
        protocol = (sources or {}).get(protocol_sha)
        if protocol is None or digest(protocol) != protocol_sha:
            raise ValueError('Exploratory predeclared qualification protocol is absent or altered.')
    benchmarks = package.get('benchmarks')
    if not isinstance(benchmarks, list) or not 1 <= len(benchmarks) <= 64:
        raise ValueError('Research transfer needs an observed benchmark, not just solver agreement.')
    # Caller-owned cache lives for one composition/verification pass only.
    # Include actual bytes, not merely submitted hash keys. Changed evidence,
    # gates, budgets, context or model necessarily triggers a new replay.
    cache_key = (model_sha256, exploratory, digest(canonical(package)),
        tuple(sorted((sha, digest(raw)) for sha, raw in (sources or {}).items())))
    if replay_cache is not None and cache_key in replay_cache:
        return replay_cache[cache_key]
    if exploratory and any(b.get('comparison_method') != 'native_response_endpoint' for b in benchmarks):
        raise ValueError('Exploratory transfer needs exact observed native-endpoint benchmarks.')
    receipts = [replay_endpoint_benchmark(b, sources, model_sha256, allow_discrepancy=True)
        if exploratory else replay_benchmark(b, sources, model_sha256) for b in benchmarks]
    result = {'scope': 'exploratory_only' if exploratory else 'research_grade_computationally_validated',
        'qualification_state': 'exploratory_only' if exploratory else 'qualified_for_research_signal',
        'candidate_decision_authority': not exploratory,
        'independent_scientific_review': False, 'clinical_qualification': False,
        'clinical_inference': False, 'package_sha256': digest(canonical(package)),
        'benchmarks': receipts, 'gates': gates, 'limitations': package['limitations']}
    if exploratory:
        result.update(benchmark_discrepancy=package['benchmark_discrepancy'],
            predeclared_protocol_sha256=package['predeclared_protocol_sha256'])
    if replay_cache is not None: replay_cache[cache_key] = result
    return result


def replay_benchmark(benchmark, sources, model_sha256):
    """Fresh solver replay AND comparison with exact preserved observed source data.

    Error limits must be predeclared in deployment configuration. They describe
    this benchmark's error budget, not a universal biological/safety threshold.
    """
    if benchmark.get('comparison_method') == 'native_response_endpoint':
        return replay_endpoint_benchmark(benchmark, sources, model_sha256)
    if (benchmark.get('kind') != 'observed_biological' or not benchmark.get('identifier')
            or not benchmark.get('error_budget_basis')
            or benchmark.get('comparison_method') != 'linear_on_native_time_grid'
            or not benchmark.get('context') or not benchmark.get('limitations')):
        raise ValueError('Benchmark type, context or predeclared comparison budget is missing.')
    budget = benchmark.get('maximum_absolute_error')
    if type(budget) not in (float, int) or not math.isfinite(budget) or budget < 0:
        raise ValueError('Benchmark error budget must be explicitly finite/nonnegative.')
    result_raw = (sources or {}).get(benchmark.get('result_sha256'))
    model_raw = (sources or {}).get(model_sha256)
    if (result_raw is None or model_raw is None or len(result_raw)>16*1024*1024
            or len(model_raw)>16*1024*1024 or digest(model_raw) != model_sha256
            or digest(result_raw) != benchmark['result_sha256']):
        raise ValueError('Benchmark native model/result bytes are missing or altered.')
    report = json.loads(result_raw)
    if report['model']['sha256'] != model_sha256:
        raise ValueError('Benchmark used a different model revision.')
    context = benchmark['context']
    if (not isinstance(context,dict) or context.get('species')!=report['model']['species']
            or context.get('tissue')!=report['model']['tissue']
            or context.get('time_unit')!=report['model']['time_unit']
            or context.get('unit')!=benchmark.get('unit')
            or context.get('endpoint')!=benchmark.get('output')
            or not context.get('experimental_conditions')):
        raise ValueError('Benchmark context differs from the model species/tissue/time/endpoint/unit contract.')
    from .mechanistic_models import verify_simulation
    native = verify_simulation(report, model_raw)
    output = benchmark['output']
    if report['model']['outputs'].get(output) != benchmark.get('unit'):
        raise ValueError('Observed and native benchmark units differ.')
    curve = next(c for c in report['curves'] if c['condition'] == benchmark['condition'])
    column = curve['columns'].index(output)
    points = benchmark.get('points')
    if not isinstance(points, list) or not 2 <= len(points) <= 4096:
        raise ValueError('At least two exact-source benchmark observations are required.')
    comparisons = []
    previous_time = None
    for point in points:
        t, observed = point.get('time'), point.get('value')
        if (type(t) not in (float,int) or type(observed) not in (float,int)
                or not math.isfinite(t) or not math.isfinite(observed)
                or previous_time is not None and t <= previous_time):
            raise ValueError('Benchmark observations require distinct ordered finite times and values.')
        previous_time = t
        source = point['original_source']
        raw = (sources or {}).get(source.get('sha256'))
        if (raw is None or len(raw)>16*1024*1024 or digest(raw) != source['sha256']
                or not allowed_source(source.get('url', ''))
                or source.get('kind') != 'original_primary_record'
                or not source.get('record_identifier') or not source.get('context_pointers')):
            raise ValueError('Observed benchmark has no preserved original-primary/context trace.')
        if (source.get('semantic_context')!=context or not source.get('semantic_review_basis')):
            raise ValueError('Original benchmark endpoint/species/units are not bound to the same reviewed scientific context.')
        for context_name, span in source['context_pointers'].items():
            if context_name not in {'identity', 'species', 'endpoint', 'experimental_context', 'units', 'time_units'}:
                raise ValueError('Unknown benchmark scientific context anchor.')
            actual = locate(raw, source.get('format', 'json'), span['pointer'],
                pdf_reviews=source.get('pdf_reviews', ()), sources=sources)
            if not same_value(actual, span['expected']):
                raise ValueError('Original benchmark context failed replay.')
        if not {'species', 'endpoint', 'experimental_context', 'units'} <= set(source['context_pointers']):
            raise ValueError('Observed benchmark species/endpoint/context/units must be bound to original bytes.')
        conversion_keys = {'original_time', 'original_time_unit', 'original_value', 'original_value_unit'}
        converting = bool(conversion_keys & set(point))
        normalization = None
        if converting:
            if not conversion_keys <= set(point):
                raise ValueError('Original observation normalization requires both values and both exact units.')
            if ('time_units' not in source['context_pointers']
                    or source['context_pointers']['time_units']['expected'] != point['original_time_unit']
                    or source['context_pointers']['units']['expected'] != point['original_value_unit']):
                raise ValueError('Original observation units must independently replay before normalization.')
            for name in ('time', 'value'):
                value = point['original_' + name]
                if type(value) not in (float, int) or not math.isfinite(value):
                    raise ValueError('Original observation values must be finite numerical measurements.')
            normalized_time = normalize_value(point['original_time'], point['original_time_unit'], context['time_unit'])
            normalized_value = normalize_value(point['original_value'], point['original_value_unit'], benchmark['unit'])
            if normalized_time != point['time'] or normalized_value != point['value']:
                raise ValueError('Original observation and native values disagree under the declared physical conversion.')
            normalization = {key: point[key] for key in sorted(conversion_keys)}
            normalization.update(native_time=normalized_time, native_time_unit=context['time_unit'],
                native_value=normalized_value, native_value_unit=benchmark['unit'],
                method='Finite dimension-checked unit registry; no fitted factor or concentration-basis change.')
        for name in ('time', 'value'):
            actual = locate(raw, source.get('format', 'json'), point[name+'_pointer'],
                pdf_reviews=source.get('pdf_reviews', ()), sources=sources)
            if not same_value(actual, point['original_' + name] if converting else point[name]):
                raise ValueError('Original benchmark numerical datum failed replay.')
        t, observed = point['time'], point['value']
        if (type(t) not in (float, int) or type(observed) not in (float, int)
                or not math.isfinite(t) or not math.isfinite(observed)
                or not curve['values'][0][0] <= t <= curve['values'][-1][0]):
            raise ValueError('Observed benchmark lies outside the native time window.')
        rows = curve['values']
        for first, second in zip(rows, rows[1:]):
            if first[0] <= t <= second[0]:
                predicted = first[column] + (second[column]-first[column]) * (t-first[0]) / (second[0]-first[0])
                break
        error = abs(predicted-observed)
        comparisons.append({'time':t, 'observed':observed, 'predicted':predicted,
            'absolute_error':error, 'source_sha256':source['sha256']})
        if normalization is not None:
            comparisons[-1]['normalization'] = normalization
    maximum = max(p['absolute_error'] for p in comparisons)
    if maximum > budget:
        raise ValueError('Observed biological benchmark exceeds its predeclared research error budget.')
    return {'identifier':benchmark['identifier'], 'numerical_replay':native,
        'comparison':comparisons, 'maximum_absolute_error':maximum, 'unit':benchmark['unit'],
        'error_budget':budget, 'error_budget_basis':benchmark['error_budget_basis'],
        'biological_validation_scope':benchmark['context'], 'independent_scientific_review':False}


def replay_endpoint_benchmark(benchmark, sources, model_sha256, *, allow_discrepancy=False):
    """Compare actual observed changes with freshly replayed native endpoints.

    A concentration-response endpoint is not a voltage time-series sample. The
    exact response statistic, units, protocol and perturbation are all pinned;
    this route does not accept solver agreement as observed biological evidence.
    """
    if (benchmark.get('kind') != 'observed_biological' or not benchmark.get('identifier')
            or not benchmark.get('error_budget_basis') or not benchmark.get('limitations')
            or benchmark.get('observed_definition') != 'change_from_control'):
        raise ValueError('Endpoint benchmark type, observed definition or predeclared budget is missing.')
    budget = benchmark.get('maximum_absolute_error')
    if type(budget) not in (float, int) or not math.isfinite(budget) or budget < 0:
        raise ValueError('Endpoint benchmark error budget must be finite/nonnegative.')
    context = benchmark.get('context')
    if (not isinstance(context, dict) or not all(context.get(k) for k in
            ('species', 'tissue', 'time_unit', 'unit', 'endpoint', 'experimental_conditions', 'native_protocol_sha256'))
            or context['unit'] != benchmark.get('unit') or context['endpoint'] != benchmark.get('output')):
        raise ValueError('Endpoint benchmark scientific context is incomplete.')
    points = benchmark.get('points')
    if not isinstance(points, list) or not 2 <= len(points) <= 64:
        raise ValueError('At least two distinct exact-source endpoint conditions are required.')
    def preserved(sha):
        raw = (sources or {}).get(sha)
        if raw is None or len(raw) > 16 * 1024 * 1024 or digest(raw) != sha:
            raise ValueError('Endpoint benchmark native/source bytes are absent or altered.')
        return raw
    model_source = preserved(model_sha256)
    # Compiled native models retain the publication/source hash as identity,
    # while the executable payload also contains pinned code and inspection.
    # Preserve both byte identities; a source hash cannot stand for that bundle.
    model_raw = preserved(benchmark['model_payload_sha256']) if benchmark.get('model_payload_sha256') else model_source
    comparisons, replays, seen = [], [], set()
    from .mechanistic_models import verify_simulation
    for point in points:
        result_sha = point.get('result_sha256')
        report = json.loads(preserved(result_sha))
        model = report['model']
        if model.get('format') == 'cellml_compiled' and not benchmark.get('model_payload_sha256'):
            raise ValueError('Compiled endpoint benchmark requires a preserved native execution bundle as well as model source.')
        perturbation_sha = digest(canonical(report['perturbation']))
        if perturbation_sha in seen:
            raise ValueError('Endpoint conditions must have distinct native perturbations.')
        seen.add(perturbation_sha)
        if (model['sha256'] != model_sha256 or any(model.get(k) != context[k] for k in ('species', 'tissue', 'time_unit'))
                or digest(canonical(report['receipt'].get('protocol'))) != context['native_protocol_sha256']
                or point.get('perturbation_sha256') != perturbation_sha):
            raise ValueError('Endpoint native model/protocol/perturbation context mismatch.')
        endpoint = report['response'].get(benchmark['output'], {})
        predicted = endpoint.get('final_difference')
        observed = point.get('value')
        if (endpoint.get('unit') != benchmark['unit'] or any(type(v) not in (float, int)
                or not math.isfinite(v) for v in (predicted, observed))):
            raise ValueError('Native endpoint is missing, refused, nonfinite or has incompatible units.')
        source = point.get('original_source', {})
        raw = preserved(source.get('sha256'))
        if (not allowed_source(source.get('url', '')) or source.get('kind') != 'original_primary_record'
                or not source.get('record_identifier') or not source.get('semantic_review_basis')
                or source.get('semantic_context') != context or not point.get('subject_identifier')):
            raise ValueError('Endpoint observation has no exact original-primary/context trace.')
        anchors = source.get('context_pointers', {})
        if not {'identity', 'species', 'endpoint', 'experimental_context', 'units'} <= set(anchors):
            raise ValueError('Endpoint observation identity/species/endpoint/context/units must be bound to original bytes.')
        for key, span in anchors.items():
            if key not in {'identity', 'species', 'endpoint', 'experimental_context', 'units', 'time_units'}:
                raise ValueError('Unknown endpoint scientific context anchor.')
            # Authors may publish numerical CSVs separately from the original
            # methods defining their units/protocol. Replay both original byte
            # sources rather than inventing units in a local extraction wrapper.
            anchor_raw = raw
            anchor_format = source.get('format', 'json')
            anchor_reviews = source.get('pdf_reviews', ())
            if span.get('source_sha256'):
                if not allowed_source(span.get('source_url', '')) or not span.get('review_basis'):
                    raise ValueError('Separate endpoint context source lacks original-source review.')
                anchor_raw = preserved(span['source_sha256'])
                anchor_format = span.get('source_format', 'json')
                anchor_reviews = span.get('pdf_reviews', ())
            actual = locate(anchor_raw, anchor_format, span['pointer'],
                pdf_reviews=anchor_reviews, sources=sources)
            if not same_value(actual, span['expected']):
                raise ValueError('Original endpoint context failed replay.')
        if anchors['identity']['expected'] != point['subject_identifier']:
            raise ValueError('Endpoint subject differs from the exact original identity anchor.')
        original_value = point.get('original_value', observed)
        original_unit = point.get('original_value_unit', benchmark['unit'])
        if type(original_value) not in (float, int) or not math.isfinite(original_value):
            raise ValueError('Original endpoint must be finite numerical evidence.')
        actual = locate(raw, source.get('format', 'json'), point['value_pointer'],
            pdf_reviews=source.get('pdf_reviews', ()), sources=sources)
        if (not same_value(actual, original_value) or anchors['units']['expected'] != original_unit
                or normalize_value(original_value, original_unit, benchmark['unit']) != observed):
            raise ValueError('Original endpoint numerical datum/unit conversion failed replay.')
        replay = verify_simulation(report, model_raw)
        if replay.get('passed') is not True:
            raise ValueError('Endpoint benchmark numerical replay failed; exploratory status cannot bypass numerical verification.')
        replays.append(replay)
        comparisons.append({'observed': observed, 'predicted': predicted, 'absolute_error': abs(predicted-observed),
            'unit': benchmark['unit'], 'source_sha256': source['sha256'], 'result_sha256': result_sha,
            'perturbation_sha256': perturbation_sha, 'subject_identifier': point['subject_identifier']})
    maximum = max(p['absolute_error'] for p in comparisons)
    if maximum > budget and not allow_discrepancy:
        raise ValueError('Observed endpoint benchmark exceeds its predeclared research error budget.')
    return {'identifier': benchmark['identifier'], 'numerical_replay': replays, 'comparison': comparisons,
        'maximum_absolute_error': maximum, 'unit': benchmark['unit'], 'error_budget': budget,
        'error_budget_basis': benchmark['error_budget_basis'], 'biological_validation_scope': context,
        'biological_error_budget_passed': maximum <= budget,
        'independent_scientific_review': False, 'clinical_inference': False}
