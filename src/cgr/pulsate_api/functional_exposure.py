"""Functional analysis after existing native PBPK verification, not a PBPK model.

Shared by the scientist's direct Virtual Human path. Exact source evidence,
nominal/free refusals and the existing physiological qualification are retained.
"""
from __future__ import annotations

import copy
import json

from .virtual_organism import canonical,digest


def read_summary(payload, artifact_type, maximum_bytes, *, expected_sha256=None, expected_byte_size=None, artifact_reference=None):
    """Bounded display/synthesis projection; full verified waveforms stay downloadable.

    Native physiological responses can legitimately contain many recorded samples.
    They must not silently erase the scientist's answer or exceed the display bound.
    This never changes a persisted result, a solver, a qualification or an endpoint.
    """
    if ((expected_sha256 is not None and digest(payload) != expected_sha256)
            or (expected_byte_size is not None and len(payload) != expected_byte_size)):
        raise ValueError('Scientific evidence full payload hash/size mismatch before projection.')
    physiological = artifact_type in {
        'virtual_organism_assessment', 'prospective_candidate_assessment',
        'native_functional_exposure',
    }
    exposure = artifact_type in {'exposure_prediction', 'native_exposure_prediction'}
    if len(payload) > (32 * 1024 * 1024 if physiological or exposure else maximum_bytes):
        raise ValueError('Scientific evidence exceeds its bounded read limit.')
    document = json.loads(payload)
    if not physiological and not exposure:
        return document
    document = copy.deepcopy(document)
    projected = 0

    def visit(value):
        nonlocal projected
        if not isinstance(value, dict):
            return
        for item in value.get('functional_models', []):
            if item.get('status') != 'computed':
                continue
            result = item.get('result', {})
            if 'curves' not in result:
                continue
            verification = result.get('verification', {})
            original = {k: v for k, v in result.items() if k != 'verification'}
            if (verification.get('passed') is not True
                    or verification.get('report_sha256') != digest(canonical(original))):
                raise ValueError('Physiological waveform projection lacks its exact numerical replay receipt.')
            curves = result['curves']
            if not isinstance(curves, list) or not 1 <= len(curves) <= 8:
                raise ValueError('Physiological waveform projection exceeds its curve bound.')
            receipts = []
            for curve in curves:
                samples = curve.get('values')
                if not isinstance(samples, list) or not 1 <= len(samples) <= 200001:
                    raise ValueError('Physiological waveform projection exceeds its sample bound.')
                indices = display_indices([list(column) for column in zip(*samples)])
                receipts.append({'condition': curve['condition'], 'columns': curve['columns'],
                    'sample_count': len(samples), 'waveform_sha256': digest(canonical(curve)),
                    'display_projection': {'method': 'uniform-index-plus-column-extrema/v1',
                        'values': [samples[i] for i in indices], 'sample_count': len(indices),
                        'scope': 'Display only; numerical endpoints use the full original response.'}})
            result.pop('curves')
            result['waveform_evidence'] = {'full_response_sha256': verification['report_sha256'],
                'curves': receipts,
                'scope': 'Display projection only. Complete recorded samples remain in the original downloadable evidence artifact.'}
            projected += 1
            if projected > 128:
                raise ValueError('Physiological waveform projection exceeds its response bound.')
        visit(value.get('functional_exposure'))
        visit(value.get('virtual_investigation'))
        visit(value.get('virtual_organism'))
        for candidate in value.get('candidates', []):
            visit(candidate)
        for execution in (value.get('native_sensitivity') or {}).get('executions', []):
            visit(execution)

    visit(document)
    # PBPK scenario/population/individual curves are another large representation
    # seam. Use the same retained indices for every aligned series, including all
    # original extrema; no integral or numerical endpoint is recomputed here.
    def project_tables(value, path=''):
        if isinstance(value, list):
            for index, item in enumerate(value):
                project_tables(item, path + '/' + str(index))
        elif isinstance(value, dict):
            times = value.get('times_h')
            if isinstance(times, list) and len(times) > 256:
                names = ['times_h'] + [name for name in ('values_umol_l', 'median_umol_l', 'p05_umol_l', 'p95_umol_l')
                    if isinstance(value.get(name), list) and len(value[name]) == len(times)]
                if len(names) > 1:
                    original = {name: value[name] for name in names}
                    indices = display_indices(list(original.values()))
                    value['display_projection'] = {'method': 'uniform-index-plus-column-extrema/v1',
                        'original_sample_count': len(times), 'display_sample_count': len(indices),
                        'source_table_sha256': digest(canonical(original)), 'source_json_pointer': path,
                        'externalized_fields': names,
                        'scope': 'Full aligned arrays remain in the immutable raw artifact; this projection is for display only.'}
                    for name in names:
                        value[name] = [original[name][i] for i in indices]
            for name, item in list(value.items()):
                if name != 'display_projection':
                    project_tables(item, path + '/' + name.replace('~', '~0').replace('/', '~1'))

    project_tables(document)
    if artifact_reference is not None and (projected or len(payload) > maximum_bytes):
        document['evidence_manifest'] = {
            'artifact_identifier': artifact_reference.artifact_identifier,
            'content_sha256': artifact_reference.content_sha256,
            'byte_size': artifact_reference.byte_size, 'media_type': artifact_reference.media_type,
            'evidence_class': artifact_type,
            'provenance_reference': artifact_reference.provenance.model_dump(mode='json'),
            'raw_download_ref': artifact_reference.artifact_identifier,
            'scope': 'Hash-verified bounded summary; original evidence is external and unchanged.'}
    if len(canonical(document)) > maximum_bytes:
        raise ValueError('Scientific evidence summary exceeds its display/synthesis bound.')
    return document


def display_indices(columns, maximum_samples=128):
    """Deterministic display sampling with exact first/last and column extrema."""
    count = len(columns[0])
    indices = {round(i * (count - 1) / (min(count, maximum_samples) - 1))
        for i in range(min(count, maximum_samples))} if count > 1 else {0}
    for column in columns:
        numeric = [(value, index) for index, value in enumerate(column)
            if isinstance(value, (int, float)) and not isinstance(value, bool)]
        if numeric:
            indices.add(min(numeric)[1]); indices.add(max(numeric)[1])
    return sorted(indices)


def native_series(exposure):
    result=[]
    for run in exposure['runs']:
        for item in run['result']['series']:
            basis='unbound' if item['compartment'] in ('Plasma Unbound (Peripheral Venous Blood)',
                'Interstitial Unbound','Intracellular Unbound') else 'total'
            result.append(dict(item,species=run['species'],unit='umol/l',concentration_basis=basis,
                exposure_case_identifier=run['scenario_identifier'],
                native_population_sha256=digest(canonical(run['result'].get('population_physiology'))),
                native_receipt_sha256=digest(canonical(run['result']['receipt']))))
    return result


def investigate(identity,exposure,config,root):
    from .functional_universe import load_universe
    from .functional_prediction import predict_universe,quantitative_hypotheses
    from .functional_pharmacology import load_library,candidate_assays
    from .functional_tissue import load_tissues,annotate_predictions
    from .mechanistic_models import load_models,simulate,verify_simulation,runtime_status
    from .prospective_evidence import ProspectivePolicy
    from .virtual_investigation import activity_overlap,compatible_experiments,model_refusal,runtime_refusal
    universe,sources,universe_sha=load_universe(root/config['functional_target_universe']['path'])
    prediction,prediction_sources=predict_universe(identity,universe,universe_sha,root/config['functional_prediction_models']['path'])
    sources.update(prediction_sources)
    # Fresh predictor replay and exact source comparison before admission.
    replay,replayed_sources=predict_universe(identity,universe,universe_sha,
        root/config['functional_prediction_models']['path'],timestamp=prediction['timestamp'])
    if canonical(prediction)!=canonical(replay) or prediction_sources!=replayed_sources:
        raise ValueError('Native exposure functional prediction failed independent replay.')
    activities=quantitative_hypotheses(prediction,identity);excluded=[]
    if config.get('functional_activity_library'):
        library,primary,_=load_library(root/config['functional_activity_library']['path']);sources.update(primary)
        measured,excluded=candidate_assays(identity,library,primary,ProspectivePolicy.model_validate(config['prospective_evidence']))
        activities.extend(measured)
    tissues=None
    if config.get('functional_tissue_evidence'):
        tissue_doc,tissue_sources,_=load_tissues(root/config['functional_tissue_evidence']['path'],universe_sha)
        sources.update(tissue_sources);tissues=annotate_predictions(prediction,tissue_doc)
    series=native_series(exposure)
    overlaps=[activity_overlap(series,a) for a in activities]
    functional=[];refusals=[];model_artifacts=[]
    if config.get('mechanistic_catalogue'):
        for contract,payload in load_models(root/config['mechanistic_catalogue']['path'])[0]:
            model_artifacts.append((contract,payload))
            experiments=compatible_experiments(contract,config.get('transfers',[]),activities,series,sources,refusals)
            if not experiments:functional.append(model_refusal(contract));continue
            availability=runtime_status(contract)
            for perturbation,activity,s in experiments.values():
                if not availability['available']:
                    functional.append(runtime_refusal(contract,perturbation,availability));continue
                response=simulate(contract,payload,perturbation)
                response['verification']=verify_simulation(response,payload)
                functional.append({'status':'computed','result':response,
                    'activity_sha256':digest(canonical(activity)),'exposure_sha256':digest(canonical(s)),
                    'exposure_case_identifier':s['exposure_case_identifier']})
    return {'schema':'pulsate.native-functional-exposure/v1','identity':identity,
        'native_exposure_sha256':digest(canonical(exposure)),
        'functional_activity_prediction':prediction,'functional_tissue_relevance':tissues,
        'quantitative_activity':activities,'excluded_activity':excluded,'exposure_activity':overlaps,
        'functional_models':functional,'functional_transfer_refusals':refusals,
        'verification':{'passed':True,'scope':'Previously verified native CSV/receipts plus fresh original-assay/model/calibration replay and native physiological numerical replay; not biological/clinical validity.'},
        'computation_selection':{'selected_compute':'classical','quantum_selected':False,
            'reason':'Functional QSAR, PBPK and native physiological ODEs do not require quantum computation.'},
        'scope':'Predicted activity is not measured activity; mechanism hypothesis is not toxicity proof. Existing exploratory physiology has no independent candidate-decision authority.'},sources,model_artifacts
