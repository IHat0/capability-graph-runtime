"""Hash-pinned, action/readout-specific Human functional prediction.

An accepted conditional assay prediction is not a measured candidate effect.
Nominal assay concentrations never become free Human exposure, Hill slopes,
occupancy or physiological perturbations by implication.
"""
from __future__ import annotations

import json
import math
import statistics
import copy
import threading
from collections import defaultdict,OrderedDict
from datetime import datetime,timezone
from pathlib import Path

from .adme_prediction import molecular_features,forest_value,applicability,FEATURES
from .functional_data import verify_dataset,source_index,denial_connectivities,elemental_inventory,stratum_identifier
from .prediction_validation import _replay
from .virtual_organism import canonical,digest

VERSION='pulsate.functional-activity-rf/v1'
MANIFEST='pulsate.functional-activity-manifest/v1'
KEYS=('target_accession','species','kind','action','readout','concentration_basis','variant')

# Cache successful expensive replay by the complete *verified content* set,
# never by pathname/mtime or candidate identity. Every load still rereads and
# hashes all declared primary/model/protocol bytes before considering a hit.
_REPLAY_CACHE=OrderedDict()
_REPLAY_LOCK=threading.Lock()


def _checked_replay(key,check):
    with _REPLAY_LOCK:
        if key in _REPLAY_CACHE:
            _REPLAY_CACHE.move_to_end(key)
            return copy.deepcopy(_REPLAY_CACHE[key])
    result=check()  # Failures are never cached/admitted.
    with _REPLAY_LOCK:
        _REPLAY_CACHE[key]=copy.deepcopy(result)
        while len(_REPLAY_CACHE)>256:_REPLAY_CACHE.popitem(last=False)
    return result


def model_gate(validation,gate,model):
    if (gate.get('schema')!='pulsate.functional-research-gate/v1' or gate.get('species')!='Human'
            or gate.get('domain')!='in_domain' or model['kind'] not in ('IC50','EC50')
            or model['unit']!='p'+model['kind'] or not all(gate.get(k) for k in ('reviewer','version','rationale','limitations'))):
        raise ValueError('Functional endpoint requires its frozen research-use gate.')
    limits=gate['limits'];expected={'minimum_n','minimum_interval_coverage','maximum_mae','maximum_rmse',
        'minimum_spearman','maximum_median_multiplicative_error'}
    if (set(limits)!=expected or any(type(v) not in (int,float) or not math.isfinite(v) for v in limits.values())
            or type(limits['minimum_n']) is not int or limits['minimum_n']<1
            or not 0<limits['minimum_interval_coverage']<=1 or not -1<=limits['minimum_spearman']<=1
            or limits['maximum_mae']<=0 or limits['maximum_rmse']<=0 or limits['maximum_median_multiplicative_error']<1):
        raise ValueError('Functional gate thresholds invalid.')
    metrics=validation['by_domain']['in_domain'];failures=[]
    for metric,limit,minimum in [('n','minimum_n',True),('interval_coverage','minimum_interval_coverage',True),
            ('mae','maximum_mae',False),('rmse','maximum_rmse',False),('spearman','minimum_spearman',True),
            ('median_multiplicative_error','maximum_median_multiplicative_error',False)]:
        value=metrics.get(metric)
        if value is None or (value<limits[limit] if minimum else value>limits[limit]):failures.append(metric)
    return {'accepted':not failures,'failed_metrics':failures,'metrics':metrics,'gate_sha256':digest(canonical(gate)),
        'scope':'Internal scaffold-held-out research gate, not independent biological/clinical qualification.'}


def verify_training_partitions(model,dataset):
    """Labels for all three splits derive from primary-replayed records."""
    from rdkit import Chem,DataStructs
    from rdkit.Chem.Scaffolds import MurckoScaffold
    import numpy as np
    grouped=defaultdict(list)
    for r in dataset['records']:
        if r['stratum']!=model['training']['stratum']:continue
        try: graph,properties,x,fp=molecular_features(r['smiles'])
        except ValueError:continue
        grouped[graph['smiles']].append((r['p_activity'],graph,properties,x,fp))
    groups={name:[] for name in ('train','calibration','test')}
    for smiles,items in sorted(grouped.items()):
        _,graph,properties,x,fp=items[0]
        scaffold=MurckoScaffold.MurckoScaffoldSmiles(mol=Chem.MolFromSmiles(smiles),includeChirality=False) or smiles
        bucket=int(digest((str(model['policy']['seed'])+':'+scaffold).encode())[:8],16)%100
        name='train' if bucket<70 else 'calibration' if bucket<85 else 'test'
        groups[name].append({'graph':graph,'properties':properties,'x':x,'fp':fp,
            'label':statistics.median(i[0] for i in items),'scaffold':scaffold})
    for name,rows in groups.items():
        sha=digest(canonical([{'graph':r['graph']['graph_sha256'],'label':r['label'],'scaffold':r['scaffold']} for r in rows]))
        if sha!=model['training']['split_hashes'][name] or len(rows)!=model['training']['split_counts'][name]:
            raise ValueError('Functional source labels/graphs do not reproduce all frozen scaffold splits.')
        if len(rows)<model['policy']['minimum_'+name+'_graphs']:raise ValueError('Incomplete functional partition.')
    training,calibration,_=groups.values()
    if (model['domain']['training_graph_hashes']!=[r['graph']['graph_sha256'] for r in training]
            or model['domain']['training_fingerprints']!=[r['fp'].ToBitString() for r in training]):
        raise ValueError('Functional training-overlap/domain fingerprints changed.')
    ranges={n:[min(r['properties'][n] for r in training),max(r['properties'][n] for r in training)] for n in FEATURES['descriptors']}
    nearest=[max(DataStructs.BulkTanimotoSimilarity(r['fp'],[t['fp'] for t in training])) for r in calibration]
    threshold=max(model['policy']['minimum_similarity'],float(np.quantile(nearest,.1)))
    errors=sorted(abs(r['label']-forest_value(model,r['x'],model_schema=VERSION)) for r in calibration)
    order=min(len(errors),math.ceil((len(errors)+1)*.9))
    if (canonical(ranges)!=canonical(model['domain']['descriptor_ranges']) or threshold!=model['domain']['similarity_threshold']
            or model['calibration']['order_statistic']!=order or model['calibration']['n']!=len(calibration)
            or model['calibration']['coverage_target']!=.9
            or not math.isclose(errors[order-1],model['calibration']['absolute_error_quantile'],abs_tol=1e-12)):
        raise ValueError('Functional calibration/domain failed numerical source replay.')


def load_models(path,universe_sha):
    path=Path(path).resolve(strict=True);raw=path.read_bytes();envelope=json.loads(raw)
    if len(raw)>2*1024*1024 or envelope.get('schema')!='pulsate.functional-activity-deployment/v1':
        raise ValueError('Functional deployment is not a frozen bounded envelope.')
    sources={digest(raw):raw}
    def read(entry,bound):
        file=(path.parent/entry['path']).resolve(strict=True)
        if not file.is_relative_to(path.parent) or file.stat().st_size>bound:raise ValueError('Functional source outside trusted path/bound.')
        payload=file.read_bytes()
        if digest(payload)!=entry['sha256']:raise ValueError('Functional source hash mismatch.')
        sources[entry['sha256']]=payload;return payload
    manifest_raw=read(envelope['model_manifest'],8*1024*1024);manifest=json.loads(manifest_raw)
    if (manifest.get('schema_version')!=MANIFEST or manifest['universe_sha256']!=universe_sha
            or manifest.get('candidate_evaluated') is not False or len(manifest.get('models',[]))>512):
        raise ValueError('Functional models do not bind a frozen candidate-independent universe.')
    protocol=json.loads(read(manifest['protocol'],2*1024*1024))
    exclusions=json.loads(read(manifest['exclusions'],2*1024*1024))
    denied=set();inventories=set()
    if exclusions.get('purpose')!='identity_denial_only':raise ValueError('Functional denial dossier invalid.')
    for identity in exclusions['identities']:
        denied.update(denial_connectivities(identity['smiles']))
        inventories.update(elemental_inventory(identity['smiles']))
    if (sorted(denied)!=manifest['excluded_connectivities']
            or canonical(sorted(inventories))!=canonical(manifest['excluded_element_inventories'])):
        raise ValueError('Functional normalization/exclusion inventories do not replay offline identity graphs.')
    gate=json.loads(read(manifest['gate'],2*1024*1024))
    expected_gate=dict(protocol['gate'],reviewer=protocol['reviewer'],version=protocol['version'])
    if canonical(gate)!=canonical(expected_gate):raise ValueError('Functional gate differs from predeclared protocol.')
    report_raw=read(envelope['validation_report'],128*1024*1024)
    datasets={};dataset_keys={}
    for entry in manifest['training_sources']:
        payload=read(entry,64*1024*1024);dataset=json.loads(payload)
        for source in entry['primary_sources']:read(source,8*1024*1024)
        declared={s['sha256'] for s in entry['primary_sources']}
        referenced={r[field] for r in dataset['records'] for field in
            ('graph_source_sha256','activity_source_sha256','assay_source_sha256','target_source_sha256')}
        if not referenced<=declared:raise ValueError('Functional dataset references undeclared primary bytes.')
        source_key=(entry['sha256'],tuple(sorted(declared)))
        _checked_replay(('dataset',source_key),lambda:verify_dataset(dataset,source_index((path.parent/entry['path']).parent)))
        if (dataset['universe_sha256']!=universe_sha
                or dataset['excluded_connectivities']!=manifest['excluded_connectivities']
                or dataset['excluded_identifiers']!=manifest['excluded_identifiers']
                or canonical(dataset['excluded_element_inventories'])!=canonical(manifest['excluded_element_inventories'])):
            raise ValueError('Functional training exclusion/universe receipt changed.')
        datasets[entry['sha256']]=dataset
        dataset_keys[entry['sha256']]=source_key
    models=[];seen=set()
    for entry in manifest['models']:
        key=tuple(entry[k] for k in KEYS)
        if key in seen:raise ValueError('Duplicate functional target/action/endpoint/readout stratum.')
        seen.add(key);payload=read(entry,32*1024*1024);model=json.loads(payload)
        if (model.get('schema_version')!=VERSION or model.get('species')!='Human'
                or model.get('kind') not in ('IC50','EC50') or model.get('unit')!='p'+model['kind']
                or model.get('action') not in ('blocker','inhibitor','agonist','antagonist')
                or model.get('concentration_basis')!='assay_nominal' or model.get('variant')!='no_variant_declared'
                or canonical(model['policy'])!=canonical(protocol['training'])
                or model['training']['stratum']!=stratum_identifier({k:model[k] for k in KEYS})):
            raise ValueError('Functional model action/assay/units/variant/protocol semantics invalid.')
        dataset=datasets[model['training']['dataset_sha256']]
        key=(entry['sha256'],dataset_keys[model['training']['dataset_sha256']],digest(manifest_raw),digest(report_raw))
        def verify_model():
            verify_training_partitions(model,dataset)
            validation=_replay(payload,manifest_raw,report_raw)
            return {'validation':validation,'gate':model_gate(validation,gate,model)}
        checked=_checked_replay(('model',key),verify_model)
        models.append({'model':model,'sha256':entry['sha256'],**checked})
    return manifest,models,sources,digest(manifest_raw)


def predict_universe(identity,universe,universe_sha,path,*,timestamp=None):
    manifest,models,sources,manifest_sha=load_models(path,universe_sha)
    if any(m['model']['target_accession'] not in {t['target_accession'] for t in universe['targets']} for m in models):
        raise ValueError('Functional model is outside the frozen target universe.')
    timestamp=timestamp or datetime.now(timezone.utc).isoformat()
    if datetime.fromisoformat(timestamp).utcoffset() is None:raise ValueError('Functional prediction timestamp needs timezone.')
    request={'inchikey':identity['inchikey'],'universe_sha256':universe_sha,'manifest_sha256':manifest_sha,'timestamp':timestamp}
    results=[]
    try:
        graph,properties,x,fp=molecular_features(identity['smiles'])
        if graph['inchikey']!=identity['inchikey']:raise ValueError('Functional query identity/graph mismatch.')
        request['graph']=graph
    except ValueError as error:
        graph=None;graph_error=str(error)
    for target in universe['targets']:
        matching=[m for m in models if m['model']['target_accession']==target['target_accession']]
        if not matching:
            refused=[r for r in manifest['unsupported'] if r['target_accession']==target['target_accession']]
            results.append(dict(target,status='unsupported',reason='No frozen functional stratum with enough independent data.',refusals=refused));continue
        for item in matching:
            model=item['model'];domain=applicability(model,properties,fp,graph) if graph else {'status':'out_of_domain','reason':graph_error}
            common=dict(target,**{k:model[k] for k in KEYS if k not in target},classification='predicted',unit='umol/l',
                functional_direction=model['action'],model_sha256=item['sha256'],validation=item['validation'],
                research_gate=item['gate'],applicability=domain,assay_domain=model['assay_domain'],
                functional_transfer_supported=False,measured_unbound_concentration=False,
                limitation='Conditional nominal functional assay prediction, not measured activity, unbound-tissue potency or toxicity proof.')
            reasons=[]
            if not item['gate']['accepted']:reasons.append('Predeclared gate failed: '+', '.join(item['gate']['failed_metrics']))
            if domain['status']!='in_domain':reasons.append('Applicability: '+domain['status'])
            if domain.get('training_graph_seen'):reasons.append('Exact training overlap; not an independent prediction.')
            if reasons:results.append(dict(common,status='refused',reason='; '.join(reasons)));continue
            score=forest_value(model,x,model_schema=VERSION);width=model['calibration']['absolute_error_quantile']
            values=[10**(6-v) for v in (score,score+width,score-width)]
            if any(not math.isfinite(v) or v<=0 for v in values):raise ValueError('Nonfinite functional potency inversion.')
            results.append(dict(common,status='predicted',value_umol_l=values[0],interval_umol_l=values[1:],
                model_space_value=score,model_space_unit=model['unit'],model_space_interval=[score-width,score+width],
                interval_definition='90% marginal split-conformal functional assay interval, not conditional-domain or joint exposure/activity confidence.',
                uncertainty='Protocol heterogeneity, direction-specific model conditionality, nominal/free incompatibility and domain shift.'))
    report={'schema':'pulsate.functional-activity-prediction/v1','request':request,'request_sha256':digest(canonical(request)),
        'manifest_sha256':manifest_sha,'universe_sha256':universe_sha,'timestamp':timestamp,'targets':results,
        'unresolved_source_rows':universe['unresolved_source_rows'],
        'coverage':{'supported_model_targets':len({m['model']['target_accession'] for m in models if m['gate']['accepted']}),
            'unsupported_model_targets':len(universe['targets'])-len({m['model']['target_accession'] for m in models if m['gate']['accepted']}),
            'accepted_candidate_targets':len({r['target_accession'] for r in results if r['status']=='predicted'}),
            'universe_targets':len(universe['targets'])},'classical':True,
        'scope':'Research functional mechanism hypotheses; predicted activity is not measured, and mechanism is not toxicity.'}
    return report,sources


def quantitative_hypotheses(report,identity):
    if report['request']['inchikey']!=identity['inchikey']:raise ValueError('Functional report belongs to another candidate.')
    return [{'inchikey':identity['inchikey'],'species':'Human','target_accession':r['target_accession'],
        'kind':r['kind'],'value':r['value_umol_l'],'interval_umol_l':r['interval_umol_l'],
        'interval_definition':r['interval_definition'],'unit':'umol/l','concentration_basis':r['concentration_basis'],
        'organ':None,'compartment':None,'assay_context':r['assay_domain'],'functional_direction':r['action'],
        'functional_transfer_supported':False,'uncertainty':r['uncertainty'],'classification':'predicted',
        'functional_prediction_sha256':digest(canonical(r)),'source_sha256':r['model_sha256'],
        'report_sha256':digest(canonical(report)),'source':'Frozen action-specific Human functional model',
        'eligibility':{'eligible':True,'scope':'Conditional nominal functional hypothesis only; no qualified physiology.'},
        'limitation':r['limitation']} for r in report['targets'] if r['status']=='predicted']
