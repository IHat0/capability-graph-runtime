"""Couple existing verified screening, reviewed PBPK and modular pharmacology.

Every missing/unsupported computation stays visible. The default without a
reviewed deployment policy is an explicit unsupported result, not a hidden dose
or an implicitly qualified historical analysis.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
from types import SimpleNamespace

from .bioactivity_hypotheses import ACTIVITY_UNITS, ScientificSources, nominate_targets, verify_hypotheses
from .mechanistic_models import load_models, exposure_perturbation, simulate, verify_simulation, runtime_status
from .phase8_scientific_handlers import _NativeRunner
from .prospective_dossier import acquire_dossier, load_library, select_regimen
from .prospective_evidence import ProspectivePolicy, EvidenceDatum, eligibility, verify_source
from .native_sensitivity import design as sensitivity_design, conditional_acquisition, summarize as summarize_sensitivity, native_exposure_endpoints
from .scientific_runtime import ScientificCapabilityFailure, ScientificCapabilityOutcome
from .virtual_organism import PBPKRequest, canonical, digest
from .virtual_organism_handlers import VirtualOrganismHandler
from .safety_pharmacology import load_panel as load_safety_panel, evaluate_panel as evaluate_safety_panel


def same_json_document(expected, payload):
    """Compare content, not serializer whitespace; byte integrity is checked separately."""
    return canonical(expected) == canonical(json.loads(payload))


def load_policy():
    configured = os.environ.get('PULSATE_VIRTUAL_INVESTIGATION_POLICY')
    if not configured: return None, None, None
    path = Path(configured).resolve(strict=True)
    payload = path.read_bytes()
    if len(payload) > 2 * 1024 * 1024: raise ValueError('Virtual investigation policy exceeds its bound.')
    doc = json.loads(payload)
    if doc.get('schema') != 'pulsate.virtual-investigation-policy/v1' or not doc.get('reviewer'):
        raise ValueError('Virtual investigation policy is not versioned/reviewed.')
    ProspectivePolicy.model_validate(doc['prospective_evidence'])
    for field in ('source_library', 'mechanistic_catalogue', 'sponsor_side_dossier', 'general_safety_panel', 'target_activity_models', 'functional_activity_library', 'functional_target_universe', 'functional_prediction_models', 'functional_tissue_evidence'):
        if doc.get(field):
            resolved = (path.parent / doc[field]['path']).resolve(strict=True)
            if not resolved.is_relative_to(path.parent) or digest(resolved.read_bytes()) != doc[field]['sha256']:
                raise ValueError('Virtual investigation configuration path/hash mismatch: ' + field)
    if doc.get('target_activity_models') and not doc.get('general_safety_panel'):
        raise ValueError('Target activity models require their exact frozen general safety panel.')
    if doc.get('functional_prediction_models') and not doc.get('functional_target_universe'):
        raise ValueError('Functional models require their exact frozen Human target universe.')
    if doc.get('functional_tissue_evidence') and not doc.get('functional_target_universe'):
        raise ValueError('Functional tissue evidence requires the exact frozen Human universe.')
    return doc, path.parent, digest(payload)


def acquire_investigation_dossier(identity, policy, library, sources, config, root):
    """Opt-in sponsor data, without weakening public-history admission."""
    if config and config.get('sponsor_side_dossier'):
        from .sponsor_dossier import load_sponsor_dossier, acquire_sponsor_dossier
        doc, sponsor_sources, sponsor_sha = load_sponsor_dossier(root / config['sponsor_side_dossier']['path'])
        sources.update(sponsor_sources)
        return acquire_sponsor_dossier(identity, 'Human', doc, sponsor_sources, policy, file_sha256=sponsor_sha)
    return acquire_dossier(identity, 'Human', policy, library, sources)


def compatible_activity(identity, library, sources, policy):
    activities, excluded = [], []
    for item in library.get('records', []):
        if item.get('inchikey') != identity['inchikey'] or item.get('native_parameter') != 'quantitative_activity': continue
        datum = EvidenceDatum.model_validate(item['datum'])
        verify_source(datum, sources[datum.source_sha256], sources)
        decision = eligibility(datum, policy, identity['inchikey'])
        if not decision['eligible']:
            excluded.append(decision)
            continue
        context = datum.context
        if datum.category != 'quantitative_activity' or datum.unit not in ACTIVITY_UNITS or context.get('kind') not in {'Ki','Kd','IC50','EC50'}:
            raise ValueError('Invalid quantitative activity type or concentration unit.')
        if datum.subject_inchikey != identity['inchikey'] or context.get('species') != item.get('species'):
            raise ValueError('Candidate activity identity/species mismatch.')
        value = float(datum.value) * ACTIVITY_UNITS[datum.unit]
        if not math.isfinite(value) or value <= 0: raise ValueError('Invalid quantitative activity value.')
        activities.append({'inchikey': identity['inchikey'], 'species': item['species'],
            'target_accession': datum.target_accession, 'kind': context['kind'], 'value': value, 'unit': 'umol/l',
            'concentration_basis': context.get('concentration_basis'), 'organ': context.get('organ'),
            'compartment': context.get('compartment'), 'assay_context': context.get('assay_context'),
            'uncertainty': item.get('uncertainty', 'Experimental uncertainty unquantified'),
            'source': datum.source_url, 'source_sha256': datum.source_sha256, 'datum': datum.model_dump(mode='json'),
            'eligibility': decision, 'classification': datum.evidence_type})
    return activities, excluded


def compatible_exposure_snapshots(identity, library, sources, policy):
    """Exact-source observed/qualified unbound endpoints, never invented curves.

    A published estimated free Cmax stays sourced/estimated, not measured tissue
    exposure. Unknown post-dose time stays null. Tissue equivalence, if admitted,
    is an explicit source-bound assumption and still requires model qualification.
    """
    from .functional_pharmacology import _anchor
    snapshots, excluded = [], []
    for item in library.get('records', []):
        if item.get('inchikey') != identity['inchikey'] or item.get('native_parameter') != 'exposure_snapshot':
            continue
        datum = EvidenceDatum.model_validate(item['datum'])
        verify_source(datum, sources[datum.source_sha256], sources)
        decision = eligibility(datum, policy, identity['inchikey'])
        if not decision['eligible']:
            excluded.append(decision)
            continue
        context = datum.context
        if (datum.category != 'early_pk' or datum.evidence_type not in {'measured', 'sourced'}
                or datum.original_source is None or datum.subject_inchikey != identity['inchikey']
                or context.get('species') != item.get('species')
                or context.get('endpoint') != 'unbound_Cmax' or context.get('concentration_basis') != 'unbound'
                or datum.unit not in ACTIVITY_UNITS or type(datum.value) not in (float, int)
                or not math.isfinite(datum.value) or datum.value <= 0
                or not all(context.get(k) for k in ('organ', 'compartment', 'regimen'))
                or not all(item.get(k) for k in ('uncertainty', 'applicability'))):
            raise ValueError('Exposure snapshot needs exact identity/species, primary unbound Cmax, regimen, uncertainty and applicability.')
        organ, compartment = context['organ'], context['compartment']
        translation = item.get('compartment_translation')
        if translation is not None:
            if (translation.get('kind') != 'passive_unbound_equivalence_assumption'
                    or translation.get('source_organ') != organ or translation.get('source_compartment') != compartment
                    or not all(translation.get(k) for k in ('organ', 'compartment', 'rationale', 'limitations', 'source_anchors'))):
                raise ValueError('Unqualified exposure compartment translation.')
            for anchor in translation['source_anchors']: _anchor(anchor, sources)
            organ, compartment = translation['organ'], translation['compartment']
        time = context.get('peak_time_h')
        if time is not None and (type(time) not in (float, int) or not math.isfinite(time) or time < 0):
            raise ValueError('Invalid observed peak timestamp.')
        snapshots.append({'species': item['species'], 'organ': organ, 'compartment': compartment,
            'concentration_basis': 'unbound', 'unit': 'umol/l', 'values_umol_l': [float(datum.value)*ACTIVITY_UNITS[datum.unit]],
            'times_h': [time], 'evidence_kind': 'qualified_exposure_snapshot', 'endpoint': 'Cmax',
            'timecourse_available': False, 'classification': datum.evidence_type,
            'exposure_case_identifier': 'observed-endpoint-' + datum.identifier,
            'path': datum.original_source.source_url, 'datum': datum.model_dump(mode='json'),
            'eligibility': decision, 'regimen': context['regimen'], 'uncertainty': item['uncertainty'],
            'applicability': item['applicability'], 'compartment_translation': translation,
            'limitation': 'Exact published exposure endpoint, not a generated concentration-time curve. Estimated free concentrations and compartment equivalence are not measured tissue concentrations.'})
    return snapshots, excluded


def predict_activity(identity, config, root, panel, panel_sha, *, timestamp=None):
    if not config or not config.get('target_activity_models'):
        return None, [], {}
    from .activity_prediction import predict_panel, quantitative_hypotheses
    prediction, sources = predict_panel(identity, panel, panel_sha,
        root / config['target_activity_models']['path'], timestamp=timestamp)
    return prediction, quantitative_hypotheses(prediction, identity), sources


def predict_functional_activity(identity, config, root, universe, universe_sha, *, timestamp=None):
    if not config or not config.get('functional_prediction_models'):
        return None, [], {}
    from .functional_prediction import predict_universe, quantitative_hypotheses
    prediction, sources = predict_universe(identity, universe, universe_sha,
        root / config['functional_prediction_models']['path'], timestamp=timestamp)
    return prediction, quantitative_hypotheses(prediction, identity), sources


def activity_overlap(series, activity):
    compatible = [s for s in series if s.get('species') == activity['species']
        and s.get('organ') == activity.get('organ') and s.get('compartment') == activity.get('compartment')]
    if (not compatible or activity.get('concentration_basis') != 'unbound'
            or activity['unit'] != 'umol/l' or not activity.get('assay_context')
            or any(s.get('concentration_basis') != 'unbound' or s.get('unit') != 'umol/l' for s in compatible)):
        return {'status': 'insufficient_compatible_evidence', 'target_accession': activity['target_accession'],
            'reason': 'Exact species, tissue/compartment, concentration units, unbound basis and assay context are required; total tissue exposure and nominal binding-assay concentration cannot be substituted.'}
    peaks = [max(s['values_umol_l']) for s in compatible]
    if any(not math.isfinite(v) or v < 0 for v in peaks): raise ValueError('Invalid exposure peak.')
    if 'interval_umol_l' in activity:
        interval = activity['interval_umol_l']
        if (not isinstance(interval, (list, tuple)) or len(interval) != 2
                or any(type(v) not in (float, int) or not math.isfinite(v) or v <= 0 for v in interval)
                or interval[0] > interval[1] or not interval[0] <= activity['value'] <= interval[1]
                or not activity.get('interval_definition')):
            raise ValueError('Quantitative activity interval requires finite positive, ordered bounds and declared semantics.')
        comparisons = []
        for exposure, peak in zip(compatible, peaks, strict=True):
            comparisons.append({'exposure_case_identifier': exposure.get('exposure_case_identifier'),
                'subject_identifier': exposure.get('subject_identifier'), 'native_path': exposure.get('path'),
                'native_population_sha256': exposure.get('native_population_sha256'),
                'series_sha256': digest(canonical(exposure)), 'peak_umol_l': peak,
                'peak_activity_ratio_interval': [peak/interval[1], peak/interval[0]],
                'status': 'modeled_peak_below_activity_range' if peak < interval[0]
                    else 'modeled_peak_exceeds_activity_range' if peak > interval[1] else 'modeled_peak_overlaps_activity_range'})
        statuses = {r['status'] for r in comparisons}
        return {'status': next(iter(statuses)) if len(statuses) == 1 else 'scenario_dependent_exposure_activity',
            'target_accession': activity['target_accession'], 'kind': activity['kind'],
            'activity_interval_umol_l': list(interval), 'interval_definition': activity['interval_definition'],
            'comparisons': comparisons, 'activity_sha256': digest(canonical(activity)), 'uncertainty': activity['uncertainty'],
            'limitation': 'Conditional sampled-peak/activity-range comparison; no nominal case, occupancy, effect direction, physiological consequence or clinical risk inferred. Not a joint probabilistic confidence interval.'}
    return {'status': 'exposure_relevant_molecular_hypothesis' if max(peaks) >= activity['value'] else 'modeled_peak_below_activity_point',
        'target_accession': activity['target_accession'], 'kind': activity['kind'], 'activity_umol_l': activity['value'],
        'unbound_peaks_umol_l': peaks, 'peak_activity_ratios': [p/activity['value'] for p in peaks],
        'uncertainty': activity['uncertainty'], 'series_sha256': [digest(canonical(s)) for s in compatible],
        'activity_sha256': digest(canonical(activity)),
        'limitation': 'Point assay comparison with modeled exposure; not occupancy, proven engagement, effect direction, physiological consequence or clinical risk. Subthreshold modeled peaks do not establish absence of effect.'}


def decision_eligible_response(response):
    """Numerical execution alone never promotes an exploratory hypothesis."""
    receipt = response.get('perturbation', {}).get('transfer_receipt') or {}
    return (receipt.get('qualified') is not False
        and receipt.get('qualification_state') != 'exploratory_only'
        and receipt.get('candidate_decision_authority') is not False)


def assess_candidate(candidate, policy, sources=None):
    """Reviewed computational progression criteria, never a clinical verdict.

    No built-in thresholds or candidate-specific decision. Missing coverage is
    insufficient evidence, not a pass. Reviewed model response thresholds may
    support a concern/rejection under their stated computational scope only.
    """
    default = {'status': 'INSUFFICIENT EVIDENCE', 'reason':
        'No reviewed computational progression criteria are configured. Screening, exposure and functional hypotheses are separate evidence levels; they do not establish safety.',
        'scope': 'Computational research prioritization only, not clinical advancement or Human safety.',
        'criteria_sha256': None, 'matched_criteria': [], 'missing': [], 'clinical_inference': False}
    if not policy: return default
    from .progression_review import reviewed_criteria
    criteria=reviewed_criteria(policy,sources)
    allowed_levels = {'exposure','molecular_hypotheses','cellular_functional','organ_physiology'}
    levels = policy.get('required_levels')
    if not isinstance(levels, list) or not levels or not set(levels).issubset(allowed_levels):
        raise ValueError('Progression criteria must require explicit nonclinical evidence levels.')
    if type(policy.get('allow_advance')) is not bool or type(policy.get('require_candidate_activity')) is not bool:
        raise ValueError('Progression coverage/advancement controls must be explicit.')
    missing = [level for level in levels if not candidate['inference_levels'].get(level)]
    sensitivity=candidate.get('native_sensitivity')
    if sensitivity and (sensitivity.get('status')!='planned' or not sensitivity.get('summary',{}).get('complete_discrete_space')):
        missing.append('Unresolved qualified-input uncertainty or unevaluated interactions')
    if policy['require_candidate_activity'] and not candidate['quantitative_activity']:
        missing.append('Qualified candidate-specific quantitative activity')
    matched = []
    operators = {'>': lambda a,b: a>b, '>=': lambda a,b: a>=b, '<': lambda a,b: a<b, '<=': lambda a,b: a<=b}
    for rule in criteria:
        if (rule.get('status') not in {'CONCERN','REJECT'} or rule.get('operator') not in operators
                or rule.get('metric') not in {'final_difference','maximum_absolute_difference'}
                or not all(rule.get(k) for k in ('model_identifier','output','unit','source','rationale'))
                or type(rule.get('threshold')) not in (float,int) or not math.isfinite(rule['threshold'])):
            raise ValueError('Invalid reviewed functional response criterion.')
        responses = [f['result'] for f in candidate['functional_models'] if f['status']=='computed'
            and f['result']['model']['identifier']==rule['model_identifier']
            and decision_eligible_response(f['result'])]
        if any(f['status']=='refused' and f.get('model_identifier')==rule['model_identifier']
               for f in candidate['functional_models']):
            missing.append('Required functional execution refused: ' + rule['model_identifier'])
        if sensitivity and sensitivity.get('status')=='planned':
            required_cases={case['case_identifier'] for case in sensitivity['design']['cases']}
            covered_cases={f.get('exposure_case_identifier') for f in candidate['functional_models']
                if f['status']=='computed' and f['result']['model']['identifier']==rule['model_identifier']
                and decision_eligible_response(f['result'])}
            if not required_cases <= covered_cases:
                missing.append('Functional criterion has not evaluated every qualified native input case: ' + rule['model_identifier'])
        observed = False
        for response in responses:
            domain=rule['applicability_domain']
            if (response['model'].get('species')!=domain['species'] or response['model'].get('tissue')!=domain['tissue']
                    or response['model'].get('sha256')!=domain['model_sha256']): continue
            if response.get('inference_level')!=rule['evidence_level']: continue
            value = response['response'].get(rule['output'])
            if value is None: continue
            if value['unit'] != rule['unit']: raise ValueError('Progression criterion/model output units mismatch.')
            observed = True
            if operators[rule['operator']](value[rule['metric']], rule['threshold']):
                matched.append({'criterion':rule, 'observed':value[rule['metric']], 'response_sha256':digest(canonical(response))})
        if not observed: missing.append('Computed reviewed model output: ' + rule['model_identifier'] + '/' + rule['output'])
    # Critical gaps remain insufficient even when an evaluated subset crosses a
    # criterion. Preserve that subset's signal, never manufacture full coverage.
    status = ('INSUFFICIENT EVIDENCE' if missing else
        'REJECT' if any(m['criterion']['status']=='REJECT' for m in matched) else 'CONCERN' if matched
        else 'ADVANCE' if not missing and policy['allow_advance'] else 'INSUFFICIENT EVIDENCE')
    reason = ('Critical research evidence or uncertainty coverage is incomplete; retained partial criterion signals do not authorize a candidate conclusion.' if missing else
        'Reviewed model-response criterion met; this is a computational follow-up decision, not an observed drug effect.' if matched else
        'Required computational evidence and reviewed progression criteria met at their declared scope.' if status=='ADVANCE' else
        'Required evidence is missing or the reviewed policy does not authorize progression. Missing/no-effect calculations are not evidence of safety.')
    return dict(default, status=status, reason=reason, scope=policy['scope'], criteria_sha256=digest(canonical(policy)),
        matched_criteria=matched, missing=missing, coverage_statement=policy['coverage_statement'])


def native_experiment(store, identity, acquisition, regimen, invocation):
    """Reuse the validated native handlers; do not substitute a home-made PBPK model."""
    runner = _NativeRunner(store)
    request = PBPKRequest.model_validate(regimen['request'])
    dossier = acquisition['dossier']
    source_payload = canonical(dossier)
    source = runner.write_json(artifact_type='pbpk_source_model', payload=source_payload,
        producer='discovery.virtual_investigate', execution_identifier=invocation.invocation_identifier)
    entry = {'inchikey': identity['inchikey'], 'species': acquisition['species'], 'kind': 'dossier',
        'source': 'Frozen sponsor-side prospective dossier' if acquisition.get('experiment_provenance') else 'Verified date-qualified prospective library', 'sha256': digest(source_payload),
        'limitations': dossier['limitations']}
    model = {'entry':entry, 'document':dossier, 'source_artifact_identifier':source.artifact_identifier,
        'parameters':[dict(p,name=k) for k,p in dossier['parameters'].items()], 'species':acquisition['species']}
    parameterization = {'schema_version':'pulsate.pbpk-parameterization/v1', 'request':request.model_dump(mode='json'),
        'identity':identity, 'models':[model], 'missing':[], 'status':'parameterized', 'adme':None}
    ref = runner.write_json(artifact_type='pbpk_parameterization', payload=canonical(parameterization),
        producer='discovery.virtual_investigate', execution_identifier=invocation.invocation_identifier, parents=(source,))
    references = [source, ref]
    record = SimpleNamespace(artifact_references=tuple(references), verified=True)
    objective = SimpleNamespace(research_requirements=SimpleNamespace(pbpk_request=request), input_references=(),
        skip_functional_integration=True) # Outer investigation performs this once across every case.
    workflow = []
    names = ('physiology.organism_construct', 'pharmacokinetics.pbpk_simulate', 'pharmacokinetics.population_simulate',
        'pharmacokinetics.cross_species_compare', 'pharmacokinetics.exposure_verify', 'discovery.exposure_relevance_assess')
    for name in names:
        handler = VirtualOrganismHandler(store, name)
        child = SimpleNamespace(invocation_identifier=invocation.invocation_identifier + '-' + name.replace('.', '-'))
        outcome = handler.execute(invocation=child, objective=objective, record=record)
        for artifact in (*outcome.output_artifacts, *outcome.evidence_artifacts):
            if artifact.artifact_identifier not in {r.artifact_identifier for r in references}: references.append(artifact)
        record.artifact_references = tuple(references)
        workflow.append({'capability':name, 'output_artifacts':[r.artifact_identifier for r in outcome.output_artifacts]})
    final = next(r for r in references if r.artifact_type == 'virtual_organism_assessment')
    return json.loads(store.read(final)), references, workflow


def compatible_experiments(contract, transfers, activities, series, sources=None, refusals=None):
    """Every distinct qualified perturbation once; never choose a convenient one."""
    experiments, qualification_cache = {}, {}
    for transfer in transfers:
        for activity in activities:
            for exposure in series:
                try:
                    perturbation = exposure_perturbation(contract, transfer, exposure, activity,
                        evidence_eligible=activity['eligibility']['eligible'], evidence_sources=sources,
                        qualification_cache=qualification_cache)
                except ValueError as error:
                    if refusals is not None:
                        refusals.append({'model_identifier':contract.identifier,
                            'transfer_sha256':digest(canonical(transfer)),
                            'activity_sha256':digest(canonical(activity)),
                            'exposure_sha256':digest(canonical(exposure)),
                            'reason':str(error),'status':'refused'})
                    continue
                key = digest(canonical(perturbation.model_dump(mode='json')))
                experiments.setdefault(key, (perturbation, activity, exposure))
                if len(experiments) > 128:
                    raise ValueError('Compatible functional experiments exceed the reviewed execution bound; no subset was silently selected.')
    return experiments


def sensitivity_plan(acquisition, policy):
    if not acquisition['conflicts']: return None
    try:
        return {'status':'planned', 'design':sensitivity_design(acquisition, policy)}
    except ValueError as error:
        return {'status':'refused','reason':str(error),'nominal_withheld':True}


def exposure_evidence(organism, sensitivity):
    """Keep every verified conditional exposure distinct, never select a nominal.

    Caller verification replays each native case first. Case/subject/native-path
    identities remain inside the exposure hash used by a qualified transfer.
    No total tissue concentration is relabeled as unbound tissue using plasma fu.
    """
    if organism is not None and sensitivity is not None:
        raise ValueError('Conflicting-input sensitivity cannot coexist with an arbitrary nominal organism result.')
    scenarios = [(None, organism)] if organism is not None else []
    if sensitivity and sensitivity.get('status')=='planned':
        scenarios = [(e['case_identifier'], e['virtual_organism']) for e in sensitivity.get('executions', [])
                     if e['status']=='computed']
    series=[]
    for case_identifier, result in scenarios:
        for run in result['runs']:
            for item in run['result']['series']:
                basis = 'unbound' if item['compartment'] in {
                    'Plasma Unbound (Peripheral Venous Blood)', 'Interstitial Unbound', 'Intracellular Unbound'} else 'total'
                normalized = dict(item, species=run['species'], unit='umol/l', concentration_basis=basis)
                if case_identifier is not None:
                    normalized['exposure_case_identifier']=case_identifier
                    physiology=run['result'].get('population_physiology')
                    normalized['native_population_sha256']=digest(canonical(physiology)) if physiology is not None else None
                series.append(normalized)
    return series


def verify_native_result(store, by_id, invocation, acquisition, regimen, organism, identifiers):
    """Re-read the exact conditional dossier and replay the native CSV verifier."""
    if regimen['status'] != 'scenario_ready': raise ValueError('Native run has no qualified dossier/regimen.')
    child_refs = tuple(by_id[i] for i in identifiers)
    child_record = SimpleNamespace(artifact_references=child_refs, verified=True)
    objective = SimpleNamespace(research_requirements=SimpleNamespace(
        pbpk_request=PBPKRequest.model_validate(regimen['request'])),input_references=())
    outcome = VirtualOrganismHandler(store,'pharmacokinetics.exposure_verify').execute(
        invocation=invocation, objective=objective, record=child_record)
    check = json.loads(store.read(outcome.output_artifacts[0]))
    if canonical(check) != canonical(organism['verification']): raise ValueError('Native organism verification mismatch.')
    source = next(r for r in child_refs if r.artifact_type=='pbpk_source_model')
    if canonical(acquisition['dossier']) != store.read(source): raise ValueError('Native model did not use its qualified conditional dossier.')
    final = next(r for r in child_refs if r.artifact_type=='virtual_organism_assessment')
    if canonical(organism) != store.read(final): raise ValueError('Nested native result changed.')


def model_refusal(contract):
    return {'status':'refused', 'model_identifier':contract.identifier, 'species':contract.species,
        'reason_code':'no_qualified_transfer',
        'reason':'No exact species/tissue/unbound quantitative activity and reviewed model-parameter transfer combination. An unrelated validation model is not automatically a Human drug-response model.'}


def runtime_refusal(contract, perturbation, status):
    return {'status':'refused', 'model_identifier':contract.identifier, 'species':contract.species,
        'reason_code':status['reason_code'], 'reason':status['reason'], 'runtime':status,
        'perturbation':perturbation.model_dump(mode='json')}


class VirtualInvestigationHandler:
    def __init__(self, store, adapter=None): self.runner, self.adapter = _NativeRunner(store), adapter

    def execute(self, *, invocation, objective, record):
        try: return self._execute(invocation, objective, record)
        except (ValueError, KeyError, OSError, TypeError) as error:
            raise ScientificCapabilityFailure('virtual_investigation_invalid', str(error)[:2000]) from error

    def _execute(self, invocation, objective, record):
        from .scientific_off_targets import _descriptors, _intended_accession
        from rdkit import Chem
        config, root, config_sha = load_policy()
        trace_ref, rows = _descriptors(record, self.runner.store)
        pocket_ref = next(r for r in record.artifact_references if r.artifact_type == 'binding_pocket')
        intended = _intended_accession(record, self.runner.store)
        results, refs = [], [trace_ref, pocket_ref]
        source_id = json.loads(self.runner.store.read(pocket_ref)).get('source_protein_artifact_identifier')
        refs.extend(r for r in record.artifact_references if r.artifact_identifier == source_id)
        policy = ProspectivePolicy.model_validate(config['prospective_evidence']) if config else ProspectivePolicy()
        library, sources, library_sha = ({'records':[]}, {}, None)
        if config and config.get('source_library'):
            library, sources, library_sha = load_library(root / config['source_library']['path'])
        functional_library, functional_sha = None, None
        if config and config.get('functional_activity_library'):
            from .functional_pharmacology import load_library as load_functional_library
            functional_library, functional_sources, functional_sha = load_functional_library(root / config['functional_activity_library']['path'])
            sources.update(functional_sources)
        models = load_models(root / config['mechanistic_catalogue']['path'])[0] if config and config.get('mechanistic_catalogue') else []
        safety_panel, safety_sha = None, None
        if config and config.get('general_safety_panel'):
            safety_panel, safety_sources, safety_sha = load_safety_panel(root / config['general_safety_panel']['path'])
            sources.update(safety_sources)
        functional_universe, functional_universe_sha = None, None
        if config and config.get('functional_target_universe'):
            from .functional_universe import load_universe
            functional_universe, universe_sources, functional_universe_sha = load_universe(root / config['functional_target_universe']['path'])
            sources.update(universe_sources)
        tissue_evidence = None
        if config and config.get('functional_tissue_evidence'):
            from .functional_tissue import load_tissues
            tissue_evidence, tissue_sources, _ = load_tissues(root / config['functional_tissue_evidence']['path'], functional_universe_sha)
            sources.update(tissue_sources)
        for contract,payload in models:
            refs.append(self.runner.write_bytes(artifact_type='mechanistic_cellml_compilation' if contract.format == 'cellml_compiled' else 'mechanistic_sbml_source',payload=payload,
                media_type='application/json' if contract.format == 'cellml_compiled' else 'application/sbml+xml',producer='discovery.virtual_investigate',execution_identifier=invocation.invocation_identifier,
                metadata={'model_identifier':contract.identifier,'source_url':contract.source_url,'model_sha256':contract.sha256}))
        for candidate, descriptor, descriptor_ref in rows:
            identity = {'name':candidate['display_name'], 'smiles':descriptor['canonical_smiles'],
                'inchikey':Chem.MolToInchiKey(Chem.MolFromSmiles(descriptor['canonical_smiles'])), 'pubchem_cid':None}
            refs.append(descriptor_ref)
            bioactivity, source_set = nominate_targets(identity['smiles'], intended, policy) if config else (
                {'status':'not_configured', 'targets':[], 'reason':'No reviewed virtual-investigation source policy configured'}, None)
            if source_set:
                for sha,payload in source_set.payloads.items():
                    refs.append(self.runner.write_json(artifact_type='virtual_investigation_source', payload=payload,
                        producer='discovery.virtual_investigate', execution_identifier=invocation.invocation_identifier,
                        metadata={'source_sha256':sha}))
                bioactivity['verification'] = verify_hypotheses(bioactivity, identity['smiles'], intended, policy, source_set.payloads)
            acquisition = acquire_investigation_dossier(identity, policy, library, sources, config, root)
            regimen = select_regimen(identity, acquisition, config.get('exploratory_regimen') if config else None)
            organism, child_workflow = None, []
            if regimen['status'] == 'scenario_ready':
                organism, child_refs, child_workflow = native_experiment(self.runner.store, identity, acquisition, regimen, invocation)
                refs.extend(child_refs)
            sensitivity = sensitivity_plan(acquisition, config.get('native_sensitivity') if config else None)
            if sensitivity and sensitivity['status']=='planned':
                executions=[]
                for case in sensitivity['design']['cases']:
                    conditional=conditional_acquisition(acquisition,case)
                    conditional_regimen=select_regimen(identity,conditional,config.get('exploratory_regimen'))
                    if conditional_regimen['status']!='scenario_ready':
                        executions.append({'case_identifier':case['case_identifier'],'status':'unsupported',
                            'reason':'Conditional inputs remain insufficient: '+', '.join(conditional_regimen.get('missing',[])),
                            'regimen':conditional_regimen})
                        continue
                    child=SimpleNamespace(invocation_identifier=invocation.invocation_identifier+'-'+case['case_identifier'])
                    # Numerical/tool failures fail the capability, preserving the
                    # attempted source/model artifacts rather than inventing a run.
                    result, conditional_refs, conditional_workflow=native_experiment(
                        self.runner.store,identity,conditional,conditional_regimen,child)
                    refs.extend(conditional_refs)
                    executions.append({'case_identifier':case['case_identifier'],'status':'computed',
                        'regimen':conditional_regimen,'virtual_organism':result,'native_workflow':conditional_workflow,
                        'native_artifact_identifiers':[r.artifact_identifier for r in conditional_refs]})
                sensitivity.update(executions=executions,summary=summarize_sensitivity(sensitivity['design'],executions))
            native_metrics = {'reference': native_exposure_endpoints(organism) if organism else None,
                'conditional': [{'case_identifier': e['case_identifier'], 'metrics': native_exposure_endpoints(e['virtual_organism'])}
                    for e in sensitivity.get('executions', []) if e['status'] == 'computed'] if sensitivity else []}
            activities, excluded_activity = compatible_activity(identity, library, sources, policy)
            if functional_library is not None:
                from .functional_pharmacology import candidate_assays
                functional_activities, functional_excluded = candidate_assays(identity, functional_library, sources, policy)
                activities.extend(functional_activities)
                excluded_activity.extend(functional_excluded)
            prediction, predicted_activities, prediction_sources = predict_activity(identity, config, root, safety_panel, safety_sha)
            sources.update(prediction_sources)
            activities.extend(predicted_activities)
            functional_prediction, functional_predictions, functional_prediction_sources = predict_functional_activity(
                identity, config, root, functional_universe, functional_universe_sha)
            sources.update(functional_prediction_sources)
            activities.extend(functional_predictions)
            from .functional_tissue import annotate_predictions
            functional_tissues = annotate_predictions(functional_prediction, tissue_evidence) if tissue_evidence else None
            safety_coverage = evaluate_safety_panel(safety_panel, safety_sha, bioactivity, activities, intended) if safety_panel else None
            series = exposure_evidence(organism, sensitivity)
            snapshots, excluded_exposure = compatible_exposure_snapshots(identity, library, sources, policy)
            series.extend(snapshots)
            relevance = [activity_overlap(series, a) for a in activities]
            functional = []
            transfer_refusals = []
            for contract,payload in models:
                experiments = compatible_experiments(contract, config.get('transfers', []) if config else [], activities, series, sources,transfer_refusals)
                if not experiments:
                    functional.append(model_refusal(contract))
                    continue
                availability = runtime_status(contract)
                for perturbation, activity, s in experiments.values():
                    if not availability['available']:
                        functional.append(runtime_refusal(contract, perturbation, availability))
                        continue
                    response = simulate(contract, payload, perturbation)
                    response['verification'] = verify_simulation(response, payload)
                    refs.append(self.runner.write_json(artifact_type='mechanistic_response',payload=canonical(response),
                        producer='discovery.virtual_investigate',execution_identifier=invocation.invocation_identifier))
                    functional.append({'status':'computed', 'result':response, 'activity_sha256':digest(canonical(activity)),
                        'exposure_sha256':digest(canonical(s)), 'exposure_case_identifier':s.get('exposure_case_identifier')})
            results.append({'candidate_identifier':candidate['candidate_identifier'], 'identity':identity,
                'bioactivity_hypotheses':bioactivity, 'dossier_acquisition':acquisition, 'regimen':regimen,
                'virtual_organism':organism, 'native_workflow':child_workflow, 'quantitative_activity':activities,
                'native_sensitivity':sensitivity,
                'native_exposure_endpoints': native_metrics,
                'general_safety_panel': safety_coverage,
                'target_activity_prediction': prediction,
                'functional_activity_prediction': functional_prediction,
                'functional_tissue_relevance': functional_tissues,
                'native_artifact_identifiers':[r.artifact_identifier for r in child_refs] if organism else [],
                'excluded_activity':excluded_activity, 'exposure_activity':relevance, 'functional_models':functional,
                'functional_transfer_refusals':transfer_refusals,
                'reviewed_exposure_snapshots':snapshots, 'excluded_exposure':excluded_exposure,
                'inference_levels':{'exposure':bool(series), 'molecular_hypotheses':bool(bioactivity.get('targets') or activities),
                    'cellular_functional':any(f['status']=='computed' and f['result']['inference_level']=='cellular_functional' and decision_eligible_response(f['result']) for f in functional),
                    'organ_physiology':any(f['status']=='computed' and f['result']['inference_level']=='organ_physiology' and decision_eligible_response(f['result']) for f in functional), 'clinical':False},
                })
            assessment = assess_candidate(results[-1], config.get('candidate_assessment') if config else None, sources)
            results[-1].update(candidate_assessment=assessment, candidate_status=assessment['status'], assessment_reason=assessment['reason'])
        for sha,payload in sources.items():
            media = ('application/pdf' if payload.startswith(b'%PDF-') else 'image/png'
                if payload.startswith(b'\x89PNG') else 'application/octet-stream')
            refs.append(self.runner.write_bytes(artifact_type='virtual_investigation_source', payload=payload,media_type=media,
                producer='discovery.virtual_investigate', execution_identifier=invocation.invocation_identifier, metadata={'source_sha256':sha}))
        structural={'status':'not_configured','reason':'No reviewed bioactivity investigation/docking adapter configured.'}
        if config and self.adapter is None:
            raise ValueError('Configured bioactivity investigation requires the existing docking adapter; no structural evaluation silently omitted.')
        if config and self.adapter is not None:
            from .bioactivity_structures import compose_panel, verify_panel
            from .scientific_off_targets import OffTargetScreeningHandler, OffTargetVerificationHandler
            hypotheses={c['candidate_identifier']:c['bioactivity_hypotheses'] for c in results}
            panel, source_refs=compose_panel(record,self.runner.store,invocation,hypotheses,intended)
            panel_ref=self.runner.write_json(artifact_type='bioactivity_structural_panel',payload=canonical(panel),
                producer='discovery.virtual_investigate',execution_identifier=invocation.invocation_identifier,
                parents=(trace_ref,*source_refs))
            child_refs=[*record.artifact_references,panel_ref,*source_refs]
            child_record=SimpleNamespace(artifact_references=tuple(child_refs),verified=True)
            panel_check=verify_panel(panel,child_record,self.runner.store,invocation,hypotheses,intended)
            screened=OffTargetScreeningHandler(self.runner.store,self.adapter,
                panel_type='bioactivity_structural_panel',report_type='bioactivity_structural_screen').execute(
                    invocation=invocation,objective=objective,record=child_record)
            child_refs.extend((*screened.output_artifacts,*screened.evidence_artifacts))
            child_record.artifact_references=tuple(child_refs)
            checked=OffTargetVerificationHandler(self.runner.store,panel_type='bioactivity_structural_panel',
                report_type='bioactivity_structural_screen',verification_type='bioactivity_structural_verification').execute(
                    invocation=invocation,objective=objective,record=child_record)
            refs.extend((panel_ref,*source_refs,*screened.output_artifacts,*screened.evidence_artifacts,
                *checked.output_artifacts,*checked.evidence_artifacts))
            structural={'status':'evaluated','panel':panel,'panel_verification':panel_check,
                'screening':json.loads(self.runner.store.read(screened.output_artifacts[0])),
                'verification':json.loads(self.runner.store.read(checked.output_artifacts[0]))}
        document = {'schema':'pulsate.virtual-investigation/v1', 'policy_sha256':config_sha,
            'prospective_policy':policy.model_dump(mode='json'), 'source_library_sha256':library_sha, 'candidates':results,
            'functional_activity_library_sha256': functional_sha,
            'functional_target_universe_sha256': functional_universe_sha,
            'bioactivity_structural':structural,
            'computation_selection':{'selected_compute':'classical','quantum_selected':False,
                'reason':'PBPK and mechanistic ODEs are classical; no independently justified electronic subproblem was identified.'},
            'verification_scope':'Source eligibility/identity, numerical receipts and evidence-chain integrity; not human safety or biological truth.'}
        # This receipt commits to all child artifacts; later final assembly checks
        # it again. Child native verifiers already replay raw native CSV/models.
        unique = {r.artifact_identifier:r for r in refs}
        document['artifact_sha256'] = {i:r.content_sha256 for i,r in unique.items()}
        ref = self.runner.write_json(artifact_type='virtual_investigation', payload=canonical(document),
            producer='discovery.virtual_investigate', execution_identifier=invocation.invocation_identifier, parents=tuple(unique.values()))
        return ScientificCapabilityOutcome(output_artifacts=(ref,), evidence_artifacts=tuple(unique.values()),
            scientific_summary='Recorded prospective acquisition, quantitative target hypotheses, native exposure and supported/refused mechanistic evidence without clinical inference.')


class VirtualInvestigationVerificationHandler:
    """Fresh source/parameter/model replay before the scientist sees v3 claims."""
    def __init__(self, store): self.runner = _NativeRunner(store)

    def execute(self, *, invocation, objective, record):
        try: return self._execute(invocation, objective, record)
        except (ValueError, KeyError, OSError, TypeError) as error:
            raise ScientificCapabilityFailure('virtual_investigation_verification_failed', str(error)[:2000]) from error

    def _execute(self, invocation, objective, record):
        from .scientific_off_targets import _descriptors, _intended_accession
        config, root, config_sha = load_policy()
        by_id = {r.artifact_identifier:r for r in record.artifact_references}
        ref = next(r for r in by_id.values() if r.artifact_type == 'virtual_investigation')
        report = json.loads(self.runner.store.read(ref))
        if report['policy_sha256'] != config_sha: raise ValueError('Investigation policy changed before verification.')
        sources = {}
        for identifier,sha in report['artifact_sha256'].items():
            r = by_id[identifier]
            payload = self.runner.store.read(r)
            if r.content_sha256 != sha or digest(payload) != sha: raise ValueError('Investigation child artifact hash mismatch.')
            if r.artifact_type == 'virtual_investigation_source': sources[sha] = payload
        policy = ProspectivePolicy.model_validate(report['prospective_policy'])
        expected_policy = ProspectivePolicy.model_validate(config['prospective_evidence']) if config else ProspectivePolicy()
        if policy != expected_policy: raise ValueError('Investigation prospective policy mismatch.')
        library, library_sources, library_sha = ({'records':[]}, {}, None)
        if config and config.get('source_library'):
            library, library_sources, library_sha = load_library(root / config['source_library']['path'])
        if library_sha != report['source_library_sha256']: raise ValueError('Reviewed evidence library changed before verification.')
        functional_library, functional_sha = None, None
        if config and config.get('functional_activity_library'):
            from .functional_pharmacology import load_library as load_functional_library
            functional_library, functional_sources, functional_sha = load_functional_library(root / config['functional_activity_library']['path'])
            if any(sources.get(sha) != raw for sha,raw in functional_sources.items()):
                raise ValueError('Original functional assay sources were omitted or changed.')
            library_sources.update(functional_sources)
        if functional_sha != report.get('functional_activity_library_sha256'):
            raise ValueError('Functional assay library changed before verification.')
        models = load_models(root / config['mechanistic_catalogue']['path'])[0] if config and config.get('mechanistic_catalogue') else []
        safety_panel, safety_sha = None, None
        if config and config.get('general_safety_panel'):
            safety_panel, safety_sources, safety_sha = load_safety_panel(root / config['general_safety_panel']['path'])
            if any(sources.get(sha) != payload for sha, payload in safety_sources.items()):
                raise ValueError('General safety panel source bytes were omitted or changed.')
        functional_universe, functional_universe_sha = None, None
        if config and config.get('functional_target_universe'):
            from .functional_universe import load_universe
            functional_universe, universe_sources, functional_universe_sha = load_universe(root / config['functional_target_universe']['path'])
            if any(sources.get(sha) != payload for sha, payload in universe_sources.items()):
                raise ValueError('Functional universe primary identity/membership sources were omitted or changed.')
        if functional_universe_sha != report.get('functional_target_universe_sha256'):
            raise ValueError('Functional target universe changed before verification.')
        tissue_evidence = None
        if config and config.get('functional_tissue_evidence'):
            from .functional_tissue import load_tissues
            tissue_evidence, tissue_sources, _ = load_tissues(root / config['functional_tissue_evidence']['path'], functional_universe_sha)
            if any(sources.get(sha) != payload for sha,payload in tissue_sources.items()):
                raise ValueError('Human tissue annotation primary sources were omitted or changed.')
        _, rows = _descriptors(record, self.runner.store)
        if [c['candidate_identifier'] for c,_,_ in rows] != [c['candidate_identifier'] for c in report['candidates']]:
            raise ValueError('Investigation omitted/reordered a candidate.')
        pocket_ref = next(r for r in by_id.values() if r.artifact_type == 'binding_pocket')
        intended = _intended_accession(record, self.runner.store)
        structural=report.get('bioactivity_structural')
        if structural and structural['status']=='evaluated':
            from .bioactivity_structures import verify_panel
            from .scientific_off_targets import OffTargetVerificationHandler
            hypotheses={c['candidate_identifier']:c['bioactivity_hypotheses'] for c in report['candidates']}
            check=verify_panel(structural['panel'],record,self.runner.store,invocation,hypotheses,intended)
            if check!=structural['panel_verification']: raise ValueError('Structural hypothesis qualification receipt changed.')
            outcome=OffTargetVerificationHandler(self.runner.store,panel_type='bioactivity_structural_panel',
                report_type='bioactivity_structural_screen',verification_type='bioactivity_structural_verification').execute(
                    invocation=invocation,objective=objective,record=record)
            if canonical(json.loads(self.runner.store.read(outcome.output_artifacts[0])))!=canonical(structural['verification']):
                raise ValueError('Structural hypothesis numerical receipt verification failed replay.')
            for kind,expected in [('bioactivity_structural_panel',structural['panel']),('bioactivity_structural_screen',structural['screening'])]:
                matches=[r for r in by_id.values() if r.artifact_type==kind]
                if len(matches)!=1 or not same_json_document(expected,self.runner.store.read(matches[0])):
                    raise ValueError('Nested structural hypothesis report differs from the native artifact.')
        elif config or structural!={'status':'not_configured','reason':'No reviewed bioactivity investigation/docking adapter configured.'}:
            raise ValueError('Structural hypothesis execution was silently omitted.')
        for candidate, (_, descriptor, _) in zip(report['candidates'], rows, strict=True):
            from rdkit import Chem
            identity = candidate['identity']
            if identity['smiles'] != descriptor['canonical_smiles'] or identity['inchikey'] != Chem.MolToInchiKey(Chem.MolFromSmiles(identity['smiles'])):
                raise ValueError('Investigation candidate graph identity mismatch.')
            bioactivity = candidate['bioactivity_hypotheses']
            if config:
                expected_check = verify_hypotheses({k:v for k,v in bioactivity.items() if k!='verification'}, identity['smiles'], intended, policy, sources)
                if bioactivity['verification'] != expected_check: raise ValueError('Bioactivity verification receipt mismatch.')
            elif bioactivity != {'status':'not_configured','targets':[], 'reason':'No reviewed virtual-investigation source policy configured'}:
                raise ValueError('Unconfigured hypotheses were manufactured.')
            acquisition = acquire_investigation_dossier(identity, policy, library, library_sources, config, root)
            regimen = select_regimen(identity, acquisition, config.get('exploratory_regimen') if config else None)
            if canonical(acquisition) != canonical(candidate['dossier_acquisition']) or canonical(regimen) != canonical(candidate['regimen']):
                raise ValueError('Dossier/source eligibility or dosing failed replay.')
            activities, excluded = compatible_activity(identity, library, library_sources, policy)
            if functional_library is not None:
                from .functional_pharmacology import candidate_assays
                functional_activities, functional_excluded = candidate_assays(identity, functional_library, library_sources, policy)
                activities.extend(functional_activities)
                excluded.extend(functional_excluded)
            prediction = candidate.get('target_activity_prediction')
            expected_prediction, predicted_activities, prediction_sources = predict_activity(identity, config, root,
                safety_panel, safety_sha, timestamp=prediction.get('timestamp') if prediction else None)
            if canonical(expected_prediction) != canonical(prediction):
                raise ValueError('Candidate binding prediction/domain/refusal failed independent numerical replay.')
            if any(sources.get(sha) != payload for sha, payload in prediction_sources.items()):
                raise ValueError('Target activity model, validation, gate or manifest source bytes were omitted or changed.')
            library_sources.update(prediction_sources)
            activities.extend(predicted_activities)
            functional_prediction = candidate.get('functional_activity_prediction')
            expected_functional, functional_predictions, functional_sources = predict_functional_activity(
                identity, config, root, functional_universe, functional_universe_sha,
                timestamp=functional_prediction.get('timestamp') if functional_prediction else None)
            if canonical(expected_functional) != canonical(functional_prediction):
                raise ValueError('Functional action/potency/interval/domain/refusal failed independent numerical replay.')
            if any(sources.get(sha) != payload for sha, payload in functional_sources.items()):
                raise ValueError('Functional model, primary assay, split, calibration or gate sources were omitted or changed.')
            library_sources.update(functional_sources)
            activities.extend(functional_predictions)
            from .functional_tissue import annotate_predictions
            expected_tissues = annotate_predictions(expected_functional, tissue_evidence) if tissue_evidence else None
            if canonical(expected_tissues) != canonical(candidate.get('functional_tissue_relevance')):
                raise ValueError('Human tissue relevance failed source-bound independent replay.')
            expected_coverage = evaluate_safety_panel(safety_panel, safety_sha, bioactivity, activities, intended) if safety_panel else None
            if canonical(expected_coverage) != canonical(candidate.get('general_safety_panel')):
                raise ValueError('General safety panel coverage failed independent replay.')
            if canonical(activities) != canonical(candidate['quantitative_activity']) or canonical(excluded) != canonical(candidate['excluded_activity']):
                raise ValueError('Quantitative activity extraction failed replay.')
            organism = candidate['virtual_organism']
            expected_plan=sensitivity_plan(acquisition,config.get('native_sensitivity') if config else None)
            sensitivity=candidate.get('native_sensitivity')
            if expected_plan and expected_plan['status']=='planned':
                if not sensitivity or sensitivity['status']!='planned' or canonical(expected_plan['design'])!=canonical(sensitivity['design']):
                    raise ValueError('Native sensitivity design failed independent replay.')
                if [e['case_identifier'] for e in sensitivity['executions']] != [c['case_identifier'] for c in expected_plan['design']['cases']]:
                    raise ValueError('Native sensitivity case omitted or reordered.')
                for case,execution in zip(expected_plan['design']['cases'],sensitivity['executions'],strict=True):
                    conditional=conditional_acquisition(acquisition,case)
                    conditional_regimen=select_regimen(identity,conditional,config.get('exploratory_regimen'))
                    if canonical(conditional_regimen)!=canonical(execution['regimen']): raise ValueError('Conditional native regimen changed.')
                    if execution['status']=='computed':
                        verify_native_result(self.runner.store,by_id,invocation,conditional,conditional_regimen,
                            execution['virtual_organism'],execution['native_artifact_identifiers'])
                    elif conditional_regimen['status']=='scenario_ready' or execution != {
                            'case_identifier':case['case_identifier'],'status':'unsupported',
                            'reason':'Conditional inputs remain insufficient: '+', '.join(conditional_regimen.get('missing',[])),
                            'regimen':conditional_regimen}:
                        raise ValueError('Native sensitivity refusal is unsupported.')
                if canonical(summarize_sensitivity(expected_plan['design'],sensitivity['executions']))!=canonical(sensitivity['summary']):
                    raise ValueError('Native sensitivity ranges failed independent replay.')
            elif canonical(expected_plan)!=canonical(sensitivity):
                raise ValueError('Native sensitivity refusal changed.')
            if organism:
                verify_native_result(self.runner.store,by_id,invocation,acquisition,regimen,organism,candidate['native_artifact_identifiers'])
            elif regimen['status']=='scenario_ready': raise ValueError('A required native scenario was silently omitted.')
            series = exposure_evidence(organism, sensitivity)
            snapshots, excluded_exposure = compatible_exposure_snapshots(identity, library, library_sources, policy)
            if canonical(snapshots) != canonical(candidate.get('reviewed_exposure_snapshots', [])) or canonical(excluded_exposure) != canonical(candidate.get('excluded_exposure', [])):
                raise ValueError('Exact-source exposure snapshot failed independent replay.')
            series.extend(snapshots)
            expected_metrics = {'reference': native_exposure_endpoints(organism) if organism else None,
                'conditional': [{'case_identifier': e['case_identifier'], 'metrics': native_exposure_endpoints(e['virtual_organism'])}
                    for e in sensitivity.get('executions', []) if e['status'] == 'computed'] if sensitivity else []}
            if canonical(expected_metrics) != canonical(candidate.get('native_exposure_endpoints')):
                raise ValueError('Native peak, Tmax, AUC or tissue/plasma endpoints failed independent replay.')
            if canonical([activity_overlap(series,a) for a in activities]) != canonical(candidate['exposure_activity']):
                raise ValueError('Exposure/activity comparison failed replay.')
            expected_combinations = {}
            transfer_refusals = []
            for contract,payload in models:
                expected_combinations[contract.identifier] = compatible_experiments(contract, config.get('transfers', []), activities, series, library_sources,transfer_refusals)
            if canonical(transfer_refusals)!=canonical(candidate.get('functional_transfer_refusals',[])):
                raise ValueError('Functional transfer refusal audit failed independent replay.')
            actual_combinations, refusals = {}, set()
            for result in candidate['functional_models']:
                if result['status']=='computed':
                    response = result['result']
                    contract,payload = next((m,p) for m,p in models if m.identifier==response['model']['identifier'])
                    if canonical(contract.model_dump(mode='json')) != canonical(response['model']): raise ValueError('Functional model identity/contract mismatch.')
                    key = digest(canonical(response['perturbation']))
                    if key not in expected_combinations[contract.identifier]: raise ValueError('Mechanistic output lacks reviewed exposure/activity coupling.')
                    actual = actual_combinations.setdefault(contract.identifier,set())
                    if key in actual: raise ValueError('Duplicate mechanistic experiment.')
                    actual.add(key)
                    if result['activity_sha256'] != response['perturbation']['activity_sha256'] or result['exposure_sha256'] != response['perturbation']['exposure_sha256']:
                        raise ValueError('Functional coupling evidence identifiers changed.')
                    exposure = expected_combinations[contract.identifier][key][2]
                    if result.get('exposure_case_identifier') != exposure.get('exposure_case_identifier'):
                        raise ValueError('Functional native sensitivity case identity changed.')
                    expected = verify_simulation({k:v for k,v in response.items() if k!='verification'},payload)
                    if expected != response['verification']: raise ValueError('Mechanistic solver verification mismatch.')
                elif result['status']=='refused':
                    model_id = result['model_identifier']
                    if model_id not in expected_combinations:
                        raise ValueError('Duplicate/unsupported mechanistic refusal.')
                    contract = next(m for m,_ in models if m.identifier==model_id)
                    if expected_combinations[model_id]:
                        key = digest(canonical(result.get('perturbation')))
                        if key not in expected_combinations[model_id]: raise ValueError('Unqualified numerical-runtime refusal.')
                        status = runtime_status(contract)
                        expected = runtime_refusal(contract, expected_combinations[model_id][key][0], status)
                        if status['available'] or canonical(expected) != canonical(result): raise ValueError('Numerical-runtime refusal failed independent replay.')
                        actual = actual_combinations.setdefault(model_id,set())
                        if key in actual: raise ValueError('Duplicate mechanistic experiment/refusal.')
                        actual.add(key)
                    else:
                        if model_id in refusals or canonical(result) != canonical(model_refusal(contract)):
                            raise ValueError('Duplicate/altered mechanistic refusal.')
                        refusals.add(model_id)
                else: raise ValueError('Unknown functional computation status.')
            if any(actual_combinations.get(model_id,set()) != set(experiments) or (not experiments and model_id not in refusals)
                    for model_id,experiments in expected_combinations.items()):
                raise ValueError('Required compatible functional experiment/refusal was omitted.')
            expected_levels = {'exposure':bool(series), 'molecular_hypotheses':bool(bioactivity.get('targets') or activities),
                'cellular_functional':any(f['status']=='computed' and f['result']['inference_level']=='cellular_functional' and decision_eligible_response(f['result']) for f in candidate['functional_models']),
                'organ_physiology':any(f['status']=='computed' and f['result']['inference_level']=='organ_physiology' and decision_eligible_response(f['result']) for f in candidate['functional_models']), 'clinical':False}
            assessment = assess_candidate(dict(candidate, inference_levels=expected_levels), config.get('candidate_assessment') if config else None, library_sources)
            if (candidate['inference_levels'] != expected_levels or candidate['candidate_status'] != assessment['status']
                    or candidate['assessment_reason'] != assessment['reason'] or canonical(candidate['candidate_assessment']) != canonical(assessment)):
                raise ValueError('Inference levels or unsupported advancement recommendation were altered.')
        payload = canonical({'passed':True,'report_sha256':ref.content_sha256, 'artifact_sha256':report['artifact_sha256'], 'scope':report['verification_scope']})
        check_ref = self.runner.write_json(artifact_type='virtual_investigation_verification',payload=payload,
            producer='discovery.virtual_investigation_verify',execution_identifier=invocation.invocation_identifier,parents=(ref,))
        return ScientificCapabilityOutcome(output_artifacts=(check_ref,),verified=True, scientific_summary='Independently replayed prospective source, dosing, native and mechanistic evidence chains.')
