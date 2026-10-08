"""Predeclared discrete uncertainty design over all highest-precedence sources.

No selected variant is a nominal estimate. Exhaustive conclusions require an
exhaustive successful design. Bounded level coverage leaves interaction gaps
explicit, even when the evaluated subset has an invariant result.
"""
from __future__ import annotations

import copy
import itertools
import math

from .virtual_organism import CompoundDossier, canonical, digest


SCHEMA = 'pulsate.native-input-sensitivity/v1'


def concentration_metrics(series):
    """Sampled peaks and windowed trapezoidal AUC, not extrapolated PK values."""
    values, times = series['values_umol_l'], series['times_h']
    if (len(times) != len(values) or len(times) < 2
            or any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in values + times)
            or any(b <= a for a, b in zip(times, times[1:]))):
        raise ValueError('Invalid native sensitivity concentration series.')
    peak = max(values)
    return {'peak_umol_l': peak, 'sampled_tmax_h': [t for t, v in zip(times, values) if v == peak] if peak > 0 else [],
        'auc_umol_h_l': sum((b-a)*(x+y)/2 for a, b, x, y in zip(times, times[1:], values, values[1:])),
        'window_h': [times[0], times[-1]], 'series_sha256': digest(canonical(series)),
        'limitation': 'Sample-grid Cmax/Tmax and trapezoidal AUC over the recorded window; no AUC infinity or continuous peak inference.'}


def native_exposure_endpoints(organism):
    """Retain species, virtual person, native zone and concentration basis.

    Ratios pair the same subject and sample grid, and use like total/total or
    free/free quantities. They are conditional distribution ratios, not Kp or
    partition coefficients inferred from an incomplete time course.
    """
    rows, ratios, refused = [], [], []
    for run in organism['runs']:
        series = run['result']['series']
        for item in series:
            row = dict(concentration_metrics(item), species=run['species'], organ=item['organ'],
                compartment=item['compartment'], native_path=item.get('path'),
                subject_identifier=item.get('subject_identifier'))
            rows.append(row)
        plasma = [s for s in series if s['organ'] == 'PeripheralVenousBlood' and s['compartment'] in {
            'Plasma (Peripheral Venous Blood)', 'Plasma Unbound (Peripheral Venous Blood)'}]
        for item in series:
            if item['organ'] == 'PeripheralVenousBlood':
                continue
            free = item['compartment'] in {'Interstitial Unbound', 'Intracellular Unbound'}
            # Other native observables may describe blood cells, intracellular or
            # interstitial totals. Do not call those total tissue/plasma ratios.
            if item['compartment'] not in {'Tissue', 'Interstitial Unbound', 'Intracellular Unbound'}:
                continue
            reference_name = 'Plasma Unbound (Peripheral Venous Blood)' if free else 'Plasma (Peripheral Venous Blood)'
            references = [s for s in plasma if s.get('subject_identifier') == item.get('subject_identifier')
                and s['compartment'] == reference_name and s['times_h'] == item['times_h']]
            identity = {'species': run['species'], 'organ': item['organ'], 'compartment': item['compartment'],
                'native_path': item.get('path'), 'subject_identifier': item.get('subject_identifier')}
            if len(references) != 1:
                refused.append(dict(identity, reason='No unique, same-subject/grid plasma reference with compatible concentration basis.'))
                continue
            reference = references[0]
            tissue_metrics, plasma_metrics = concentration_metrics(item), concentration_metrics(reference)
            if plasma_metrics['auc_umol_h_l'] <= 0:
                refused.append(dict(identity, reason='Plasma AUC is zero; no finite tissue/plasma AUC ratio.'))
                continue
            ratios.append(dict(identity, concentration_basis='unbound' if free else 'total',
                auc_tissue_plasma_ratio=tissue_metrics['auc_umol_h_l']/plasma_metrics['auc_umol_h_l'],
                time_ratios=[{'time_h': t, 'ratio': x/y if y > 0 else None,
                    'reason': None if y > 0 else 'Zero plasma concentration; ratio undefined'}
                    for t, x, y in zip(item['times_h'], item['values_umol_l'], reference['values_umol_l'])],
                tissue_series_sha256=tissue_metrics['series_sha256'], plasma_series_sha256=plasma_metrics['series_sha256'],
                window_h=tissue_metrics['window_h']))
    return {'endpoints': rows, 'tissue_plasma_ratios': ratios, 'refused_ratios': refused,
        'interpretation': 'Native conditional concentration ratios; not equilibrium partition coefficients, occupancy or clinical effects.'}


def design(acquisition, policy):
    conflicts = acquisition['conflicts']
    if not conflicts: return None
    if (not policy or policy.get('schema') != SCHEMA
            or policy.get('method') != 'cartesian_or_level_covering'
            or not all(policy.get(k) for k in ('reviewer','version','scientific_justification','scope'))
            or type(policy.get('maximum_runs')) is not int or not 1 <= policy['maximum_runs'] <= 256):
        raise ValueError('Conflicting inputs require a bounded reviewed native sensitivity policy.')
    # Equal source values retain all provenance but are one numerical level.
    dimensions = []
    datums = {d['identifier']:d for d in acquisition['eligibility']['datums']}
    for conflict in sorted(conflicts, key=lambda c:c['parameter']):
        grouped = {}
        for alternative in conflict['alternatives']:
            value = alternative['value']
            key = canonical({'value':value['value'],'unit':value['unit']} if isinstance(value,dict) and 'unit' in value else value)
            grouped.setdefault(key, {'value':alternative['value'],'sources':[]})['sources'].append({
                'datum':alternative['datum'], 'datum_sha256':digest(canonical(datums[alternative['datum']])),
                'source_sha256':datums[alternative['datum']]['source_sha256'],
                'original_source_sha256':(datums[alternative['datum']].get('original_source') or {}).get('source_sha256')})
        levels = [grouped[k] for k in sorted(grouped)]
        if len(levels) > 64: raise ValueError('Qualified sensitivity dimension exceeds level bound; no values discarded.')
        units = {v['value']['unit'] for v in levels if isinstance(v['value'],dict) and 'unit' in v['value']}
        if len(units) > 1: raise ValueError('Conflicting input alternatives require compatible normalized native units.')
        dimensions.append({'parameter':conflict['parameter'], 'levels':levels})
    if len(dimensions) > 12: raise ValueError('Native sensitivity exceeds dimension bound; no nominal substituted.')
    counts = [len(d['levels']) for d in dimensions]
    total = math.prod(counts)
    bound = policy['maximum_runs']
    if total <= bound:
        selections = list(itertools.product(*(range(n) for n in counts)))
        exhaustive = True
    else:
        if max(counts) > bound:
            raise ValueError('Sensitivity budget cannot cover every qualified alternative; review a larger budget.')
        # First cover every level in every dimension. Then cover mixed-radix
        # strata across the complete product, independent of any model response.
        selected = {tuple(i % n for n in counts) for i in range(max(counts))}
        def unrank(rank):
            row=[]
            for n in reversed(counts): row.append(rank % n); rank //= n
            return tuple(reversed(row))
        for i in range(bound):
            row=unrank(i * (total-1) // max(1,bound-1))
            if len(selected) < bound: selected.add(row)
        selections=sorted(selected)
        exhaustive=False
    cases=[]
    for selection in selections:
        assignments={d['parameter']:d['levels'][i] for d,i in zip(dimensions,selection,strict=True)}
        cases.append({'case_identifier':'sensitivity-'+digest(canonical(assignments))[:24],
            'assignments':assignments})
    return {'schema':SCHEMA,'policy':policy,'policy_sha256':digest(canonical(policy)),
        'dimensions':dimensions,'total_combinations':total,'exhaustive':exhaustive,
        'cases':cases,'nominal_withheld':True,
        'limitation':'Discrete highest-precedence alternatives, not a probability distribution or continuous confidence interval. '
            + ('All discrete combinations covered.' if exhaustive else 'All levels covered, but unevaluated interactions remain unresolved.')}


def conditional_acquisition(acquisition, case):
    """Create an explicitly conditional native input, retaining the parent conflicts."""
    parameters=copy.deepcopy(acquisition['resolved_parameters'])
    controls=copy.deepcopy(acquisition['resolved_controls'])
    for role, level in case['assignments'].items():
        if role in {'binding_partner','ionization','dosing'}: controls[role]=level['value']
        else: parameters[role]=level['value']
    if not all(k in controls for k in ('binding_partner','ionization')):
        raise ValueError('Sensitivity cannot manufacture missing binding or ionization controls.')
    identity=acquisition['identity']
    dossier=CompoundDossier(name=identity['name'],smiles=identity['smiles'],inchikey=identity['inchikey'],
        species=acquisition['species'],parameters=parameters,binding_partner=controls['binding_partner'],
        ionization=tuple(controls['ionization']),ionization_status='reviewed',
        ionization_source='; '.join(acquisition['parameter_provenance']['ionization']),
        limitations=('Conditional sensitivity variant, not a nominal result. All parent conflicts remain preserved.',))
    if acquisition.get('schema') == 'pulsate.sponsor-side-prospective/v1':
        from .sponsor_dossier import sponsor_compound_dossier
        dossier = sponsor_compound_dossier(dict(acquisition, resolved_parameters=parameters, resolved_controls=controls))
    return dict(acquisition,dossier=dossier.model_dump(mode='json'),conflicts=[],
        resolved_parameters=parameters,resolved_controls=controls,
        missing=list(acquisition['missing']) if acquisition.get('schema') == 'pulsate.sponsor-side-prospective/v1' else [],
        prospective_dosing=controls.get('dosing'),sensitivity_condition=case)


def summarize(plan, executions):
    if [r['case_identifier'] for r in executions] != [r['case_identifier'] for r in plan['cases']]:
        raise ValueError('Native sensitivity case coverage/order mismatch.')
    endpoints={}
    failures=[]
    populations={}
    for execution in executions:
        if execution['status'] != 'computed':
            failures.append({'case_identifier':execution['case_identifier'],'reason':execution['reason']})
            continue
        organism=execution['virtual_organism']
        if not organism['runs'] or organism.get('refused'):
            failures.append({'case_identifier':execution['case_identifier'],
                'reason':'Native execution missing or partially refused', 'refused':organism.get('refused',[])})
        for run in organism['runs']:
            physiology=run['result'].get('population_physiology')
            subjects={}
            if physiology is not None:
                rows=physiology.get('subjects',[])
                subjects={row['identifier']:digest(canonical(row)) for row in rows}
                if len(subjects)!=len(rows) or len(rows)!=run['result'].get('subject_count'):
                    raise ValueError('Native sensitivity population identities are duplicate or incomplete.')
            populations.setdefault(run['species'],[]).append({
                'case_identifier':execution['case_identifier'],
                'population_sha256':digest(canonical(physiology)) if physiology is not None else None})
            for series in run['result']['series']:
                metrics=concentration_metrics(series)
                times=series['times_h']
                # Full native paths distinguish, for example, periportal and
                # pericentral liver. Their concentrations cannot be pooled.
                subject=series.get('subject_identifier')
                if physiology is not None and subject not in subjects:
                    raise ValueError('Native concentration series is not bound to a preserved population subject.')
                key=canonical([run['species'],series.get('path'),series['organ'],series['compartment'],subject,
                    subjects.get(subject),[times[0],times[-1]]]).decode()
                if any(o['case_identifier']==execution['case_identifier'] for o in endpoints.get(key,[])):
                    raise ValueError('Duplicate native sensitivity observable identity; no compartments merged.')
                endpoints.setdefault(key,[]).append({'case_identifier':execution['case_identifier'],
                    **metrics})
    ranges={k:{'observations':v,'peak_range_umol_l':[min(x['peak_umol_l'] for x in v),max(x['peak_umol_l'] for x in v)],
        'auc_range_umol_h_l':[min(x['auc_umol_h_l'] for x in v),max(x['auc_umol_h_l'] for x in v)],
        'sampled_tmax_range_h':[min(t for x in v for t in x['sampled_tmax_h']), max(t for x in v for t in x['sampled_tmax_h'])]
            if any(x['sampled_tmax_h'] for x in v) else None} for k,v in endpoints.items()}
    complete_endpoints=bool(ranges) and all(len(r['observations'])==len(plan['cases']) for r in ranges.values())
    return {'endpoint_ranges':ranges,'failed_cases':failures,'nominal_withheld':True,
        'population_alignment':{species:{'cases':rows,'paired_population_verified':
            len(rows)==len(plan['cases']) and all(r['population_sha256'] is not None for r in rows)
            and len({r['population_sha256'] for r in rows})==1} for species,rows in populations.items()},
        'endpoint_coverage_complete':complete_endpoints,
        'complete_discrete_space':plan['exhaustive'] and not failures and complete_endpoints,
        'qualitative_conclusion':'No biological progression conclusion inferred from concentration ranges. '
            'Applicable reviewed endpoint criteria must evaluate every variant before an invariance claim.'}
