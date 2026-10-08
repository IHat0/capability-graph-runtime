"""Target- and endpoint-specific quantitative binding prediction.

No potency is obtained from docking or neighbours. Each frozen forest has one
Human target and one assay quantity. Binding assays use nominal assay units,
not assumed unbound tissue potency or a functional inhibition transfer.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

from .adme_prediction import molecular_features, forest_value, applicability
from .prediction_validation import _replay
from .virtual_organism import canonical, digest

VERSION = 'pulsate.target-activity-rf/v1'
MANIFEST = 'pulsate.target-activity-manifest/v1'


def model_gate(validation, gate, model):
    if (gate.get('schema') != 'pulsate.target-activity-research-gate/v1'
            or gate.get('species')!='Human' or gate.get('domain')!='in_domain'
            or gate.get('kind') != model['kind'] or gate.get('unit') != model['unit']
            or not all(gate.get(k) for k in ('reviewer','version','rationale','limitations'))):
        raise ValueError('Activity prediction lacks an endpoint-specific predeclared research-use gate.')
    metrics=validation['by_domain']['in_domain']
    limits=gate.get('limits',{})
    expected={'minimum_n','minimum_interval_coverage','maximum_mae','maximum_rmse','minimum_spearman'}
    if (set(limits)!=expected or any(type(v) not in (float,int) or not math.isfinite(v) for v in limits.values())
            or type(limits['minimum_n']) is not int or limits['minimum_n']<1
            or not 0<limits['minimum_interval_coverage']<=1 or not -1<=limits['minimum_spearman']<=1
            or limits['maximum_mae']<=0 or limits['maximum_rmse']<=0):
        raise ValueError('Activity research-use gate thresholds are incomplete or invalid.')
    failures=[]
    for key,limit,minimum in [('n','minimum_n',True),('interval_coverage','minimum_interval_coverage',True),
            ('mae','maximum_mae',False),('rmse','maximum_rmse',False),('spearman','minimum_spearman',True)]:
        value=metrics.get(key)
        if value is None or (value<limits[limit] if minimum else value>limits[limit]): failures.append(key)
    return {'accepted':not failures,'failed_metrics':failures,'metrics':metrics,
        'gate_sha256':digest(canonical(gate)),'scope':'Internal scaffold-held-out numerical research-use gate; not independent biological or clinical qualification'}


def load_models(path,panel_sha):
    path=Path(path).resolve(strict=True)
    envelope_bytes=path.read_bytes()
    envelope=json.loads(envelope_bytes)
    if len(envelope_bytes)>2*1024*1024 or envelope.get('schema')!='pulsate.target-activity-deployment/v1':
        raise ValueError('Target activity deployment is not a bounded frozen envelope.')
    sources={digest(envelope_bytes):envelope_bytes}
    def read(entry,bound):
        p=(path.parent/entry['path']).resolve(strict=True)
        if not p.is_relative_to(path.parent) or p.stat().st_size>bound: raise ValueError('Predictive source outside trusted path/bound')
        payload=p.read_bytes()
        if digest(payload)!=entry['sha256']: raise ValueError('Target activity source hash mismatch')
        sources[entry['sha256']]=payload
        return payload
    manifest_bytes=read(envelope['model_manifest'],4*1024*1024)
    manifest=json.loads(manifest_bytes)
    if (manifest.get('schema_version')!=MANIFEST or manifest.get('panel_sha256')!=panel_sha
            or manifest.get('candidate_evaluated') is not False or not 0<=len(manifest.get('models',[]))<=128):
        raise ValueError('Target activity manifest is not a bounded, candidate-independent frozen panel model set.')
    report_bytes=read(envelope['validation_report'],32*1024*1024)
    gate=json.loads(read(manifest['gate'],2*1024*1024))
    seen=set(); models=[]
    for entry in manifest['models']:
        key=entry['target_accession'],entry['kind'],entry['species']
        if key in seen: raise ValueError('Duplicate target/assay/species model')
        seen.add(key)
        payload=read(entry,16*1024*1024); model=json.loads(payload)
        if (model.get('schema_version')!=VERSION or model.get('species')!='Human' or model.get('kind') not in {'Ki','Kd'}
                or model.get('unit')!='p'+model['kind'] or model.get('concentration_basis')!='assay_nominal'
                or model.get('functional_direction') is not None or model.get('assay_type')!='B'):
            raise ValueError('Binding model semantics, units or species are invalid; no mixed/functional endpoint substitution.')
        validation=_replay(payload,manifest_bytes,report_bytes)
        receipt=model_gate(validation,gate,model)
        models.append({'model':model,'sha256':entry['sha256'],'validation':validation,'gate':receipt})
    return manifest,models,sources,digest(manifest_bytes)


def predict_panel(identity,panel,panel_sha,path,*,timestamp=None):
    manifest,models,sources,manifest_sha=load_models(path,panel_sha)
    if any(m['model']['target_accession'] not in {t['target_accession'] for t in panel['targets']} for m in models):
        raise ValueError('Activity model target is outside the frozen general panel.')
    graph,properties,features,fp=molecular_features(identity['smiles'])
    if graph['inchikey']!=identity['inchikey']: raise ValueError('Quantitative activity graph identity mismatch')
    timestamp=timestamp or datetime.now(timezone.utc).isoformat()
    if datetime.fromisoformat(timestamp).utcoffset() is None: raise ValueError('Activity prediction timestamp requires timezone')
    request={'graph':graph,'manifest_sha256':manifest_sha,'panel_sha256':panel_sha,'timestamp':timestamp}
    results=[]
    for target in panel['targets']:
        matching=[m for m in models if m['model']['target_accession']==target['target_accession']]
        if not matching:
            refused=[r for r in manifest.get('unsupported',[]) if r['target_accession']==target['target_accession']]
            reason='No frozen target-specific binding model with sufficient assay data'
            if refused: reason += ': ' + '; '.join(r['reason'] for r in refused)
            results.append(dict(target,status='unsupported',reason=reason,refusals=refused))
            continue
        for item in matching:
            model=item['model']; domain=applicability(model,properties,fp,graph)
            common=dict(target,kind=model['kind'],unit='umol/l',classification='predicted',
                model_sha256=item['sha256'],validation=item['validation'],research_gate=item['gate'],applicability=domain,
                concentration_basis='assay_nominal',assay_domain=model['assay_domain'],functional_direction=None,
                tissue_potency_transfer_supported=False,
                limitation='Nominal binding assay prediction; no functional inhibition/activation, unbound-tissue potency, occupancy or clinical liability inferred.')
            if not item['gate']['accepted'] or domain['status']!='in_domain' or domain['training_graph_seen']:
                reasons=[]
                if not item['gate']['accepted']: reasons.append('Predeclared validation gate failed: '+', '.join(item['gate']['failed_metrics']))
                if domain['status']!='in_domain': reasons.append('Applicability status: '+domain['status'])
                if domain['training_graph_seen']: reasons.append('Query graph overlaps training data; not an independent prediction')
                results.append(dict(common,status='refused',reason='; '.join(reasons)))
                continue
            score=forest_value(model,features,model_schema=VERSION)
            width=model['calibration']['absolute_error_quantile']
            values=[10**(6-v) for v in (score,score+width,score-width)]
            if any(not math.isfinite(v) or v<=0 for v in values): raise ValueError('Predicted activity conversion is outside finite positive concentration range')
            results.append(dict(common,status='predicted',value_umol_l=values[0],interval_umol_l=values[1:],
                model_space_value=score,model_space_interval=[score-width,score+width],model_space_unit=model['unit'],
                interval_definition='90% marginal split-conformal interval in negative-log molar binding space, inverted into concentration; not joint exposure/activity confidence',
                uncertainty='Assay/protocol heterogeneity, marginal calibration and possible domain shift; validate in the actual relevant assay.'))
    report={'schema':'pulsate.target-activity-prediction/v1','request':request,'request_sha256':digest(canonical(request)),
        'manifest_sha256':manifest_sha,'panel_sha256':panel_sha,'timestamp':timestamp,'targets':results,
        'classical':True,'scope':'Quantitative binding hypotheses with explicit domain/refusal and assay semantics, not physiological or clinical predictions'}
    return report,sources


def quantitative_hypotheses(report, identity):
    """Bind accepted predictions to the existing activity evidence contract.

    A binding quantity is numerical candidate evidence, but is deliberately not
    a reviewed unbound/functional transfer into a physiological equation.
    """
    if report['request']['graph']['inchikey'] != identity['inchikey']:
        raise ValueError('Activity report belongs to a different candidate graph.')
    return [{'inchikey': identity['inchikey'], 'species': r['species'],
        'target_accession': r['target_accession'], 'kind': r['kind'],
        'value': r['value_umol_l'], 'interval_umol_l': r['interval_umol_l'],
        'interval_definition': r['interval_definition'], 'unit': r['unit'],
        'concentration_basis': r['concentration_basis'], 'organ': None, 'compartment': None,
        'assay_context': r['assay_domain'], 'functional_direction': None,
        'tissue_potency_transfer_supported': False, 'uncertainty': r['uncertainty'],
        'source': 'Frozen target-specific binding model', 'source_sha256': r['model_sha256'],
        'prediction_sha256': digest(canonical(r)), 'report_sha256': digest(canonical(report)),
        'eligibility': {'eligible': True, 'scope': 'Research binding hypothesis only; not a physiological transfer'},
        'classification': 'predicted', 'limitation': r['limitation']}
        for r in report['targets'] if r['status'] == 'predicted']
