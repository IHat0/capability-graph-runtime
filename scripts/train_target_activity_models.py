"""Freeze fixed-policy, target-specific binding forests before held-out scoring.

Only candidate-excluded numerical data from the generic acquisition firewall.
No prompt, clinical outcome, candidate score or molecule-specific route is used.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import Counter,defaultdict
from pathlib import Path

import numpy as np
from rdkit import Chem,DataStructs,rdBase
from rdkit.Chem.Scaffolds import MurckoScaffold
from sklearn import __version__ as sklearn_version
from sklearn.ensemble import RandomForestRegressor
from scipy.stats import spearmanr

from cgr.pulsate_api.adme_prediction import FEATURES,molecular_features,forest_value,applicability
from cgr.pulsate_api.activity_prediction import VERSION,MANIFEST,model_gate
from cgr.pulsate_api.virtual_organism import canonical,digest
from cgr.pulsate_api.bioactivity_hypotheses import ACTIVITY_UNITS

POLICY={'seed':20261005,'n_estimators':64,'max_depth':12,'min_samples_leaf':3,'max_features':.7,
    'split':'graph-deduplicated scaffold hash buckets: train 0..69, calibration 70..84, test 85..99',
    'coverage':.90,'minimum_similarity':.4,'calibration_similarity_percentile':.10,
    'duplicate_graph_label':'Median binding quantity in negative-log molar space; heterogeneous protocols explicitly retained'}


def verify_acquired_dataset(dataset, root):
    """Replay labels/identities against exact projected API snapshots, not flags."""
    if (dataset.get('numerical_candidate_activity_requested') is not False
            or dataset.get('species')!='Human' or dataset.get('kind') not in {'Ki','Kd'}):
        raise ValueError('Activity dataset semantics or candidate exclusion receipt invalid')
    cache={}
    def snapshot(sha, key):
        if (sha,key) not in cache:
            payload=(root/(sha+'.json')).read_bytes()
            if len(payload)>8*1024*1024 or digest(payload)!=sha:
                raise ValueError('Activity training source changed or exceeded its bound')
            rows=json.loads(payload)[key]
            field={'activities':'activity_id','assays':'assay_chembl_id','molecules':'molecule_chembl_id','targets':'target_chembl_id'}[key]
            indexed={r[field]:r for r in rows}
            if len(indexed)!=len(rows): raise ValueError('Duplicate primary source identity')
            cache[sha,key]=indexed
        return cache[sha,key]
    for record in dataset['records']:
        target=snapshot(record['target_source_sha256'],'targets')[dataset['target_chembl_id']]
        activity=snapshot(record['activity_source_sha256'],'activities')[record['activity_id']]
        assay=snapshot(record['assay_source_sha256'],'assays')[record['assay_chembl_id']]
        molecule=snapshot(record['source_sha256'],'molecules')[record['molecule_chembl_id']]
        if (target['organism']!='Homo sapiens' or target['target_type']!='SINGLE PROTEIN'
                or len(target['target_components'])!=1 or target['target_components'][0]['accession']!=dataset['target']['target_accession']
                or activity['molecule_chembl_id']!=record['molecule_chembl_id'] or activity['molecule_chembl_id'] in dataset['excluded_identifiers']
                or activity['assay_chembl_id']!=record['assay_chembl_id'] or activity['target_chembl_id']!=dataset['target_chembl_id']
                or activity['standard_type']!=dataset['kind'] or activity['standard_relation']!='=' or activity['assay_type']!='B'
                or activity.get('data_validity_comment') or activity.get('potential_duplicate')
                or assay['confidence_score']!=9 or assay['relationship_type']!='D' or assay['assay_type']!='B'
                or assay['target_chembl_id']!=dataset['target_chembl_id']
                or molecule['molecule_structures']['standard_inchi_key']!=record['inchikey']):
            raise ValueError('Activity training record disagrees with direct exact Human assay/identity sources')
        mol=Chem.MolFromSmiles(molecule['molecule_structures']['canonical_smiles'])
        declared=Chem.MolFromSmiles(record['smiles'])
        if mol is None or declared is None: raise ValueError('Invalid training source chemical graph')
        key=Chem.MolToInchiKey(mol)
        value=float(activity['standard_value'])*ACTIVITY_UNITS[activity['standard_units']]
        if (key!=record['inchikey'] or key.split('-')[0] in dataset['excluded_parent_connectivities']
                or Chem.MolToSmiles(mol,isomericSmiles=True)!=Chem.MolToSmiles(declared,isomericSmiles=True)
                or not math.isfinite(value) or value<=0 or not math.isclose(value,record['value_umol_l'],rel_tol=0,abs_tol=1e-12)):
            raise ValueError('Activity training graph, parent exclusion or concentration label failed source replay')


def dataset_rows(dataset):
    grouped=defaultdict(list); rejected=Counter()
    for record in dataset['records']:
        try:
            graph,properties,features,fp=molecular_features(record['smiles'])
            if graph['inchikey']!=record['inchikey'] or graph['inchikey'].split('-')[0] in dataset['excluded_parent_connectivities']:
                raise ValueError('Identity mismatch or candidate-parent overlap')
            if record['molecule_chembl_id'] in dataset['excluded_identifiers'] or record['kind']!=dataset['kind']:
                raise ValueError('Candidate identifier or mixed assay endpoint')
            score=record['p_activity']
            if type(score) not in (float,int) or not math.isfinite(score) or not math.isclose(score,6-math.log10(record['value_umol_l']),abs_tol=1e-12):
                raise ValueError('Invalid binding concentration conversion')
            grouped[graph['smiles']].append((score,graph,properties,features,fp,record['activity_id']))
        except (ValueError,TypeError,KeyError,OverflowError) as error: rejected[str(error)]+=1
    rows=[]
    for smiles,records in sorted(grouped.items()):
        _,graph,properties,features,fp,_=records[0]
        scaffold=MurckoScaffold.MurckoScaffoldSmiles(mol=Chem.MolFromSmiles(smiles),includeChirality=False) or smiles
        bucket=int(digest((str(POLICY['seed'])+':'+scaffold).encode())[:8],16)%100
        rows.append({'graph':graph,'properties':properties,'x':features,'fp':fp,'label':statistics.median(r[0] for r in records),
            'activity_ids':[r[5] for r in records],'scaffold':scaffold,'split':'train' if bucket<70 else 'calibration' if bucket<85 else 'test'})
    return rows,dict(rejected)


def train(dataset,dataset_sha,output):
    rows,rejected=dataset_rows(dataset)
    groups={name:[r for r in rows if r['split']==name] for name in ('train','calibration','test')}
    if len(groups['train'])<100 or any(len(groups[name])<30 for name in ('calibration','test')):
        raise ValueError('Insufficient independent train/calibration/test graphs under the fixed scaffold split')
    training,calibration,test=groups.values()
    forest=RandomForestRegressor(n_estimators=64,max_depth=12,min_samples_leaf=3,max_features=.7,random_state=POLICY['seed'],n_jobs=2)
    forest.fit(np.array([r['x'] for r in training],dtype=np.float32),[r['label'] for r in training])
    predicted=forest.predict(np.array([r['x'] for r in calibration],dtype=np.float32))
    errors=sorted(abs(r['label']-p) for r,p in zip(calibration,predicted,strict=True))
    order=min(len(errors),math.ceil((len(errors)+1)*POLICY['coverage']))
    nearest=[max(DataStructs.BulkTanimotoSimilarity(r['fp'],[t['fp'] for t in training])) for r in calibration]
    model={'schema_version':VERSION,'features':FEATURES,'target_accession':dataset['target']['target_accession'],
        'kind':dataset['kind'],'unit':'p'+dataset['kind'],'species':'Human','concentration_basis':'assay_nominal',
        'assay_type':'B','assay_domain':dataset['assay_domain'],'functional_direction':None,'policy':POLICY,
        'training':{'dataset_sha256':dataset_sha,'source_url':'https://www.ebi.ac.uk/chembl/api/data/',
            'rdkit_version':rdBase.rdkitVersion,'sklearn_version':sklearn_version,'rejected':rejected,
            'excluded_parent_connectivities':dataset['excluded_parent_connectivities'],'excluded_identifiers':dataset['excluded_identifiers'],
            'split_counts':{n:len(g) for n,g in groups.items()},
            'split_hashes':{n:digest(canonical([{'graph':r['graph']['graph_sha256'],'label':r['label'],'scaffold':r['scaffold']} for r in g])) for n,g in groups.items()}},
        'calibration':{'coverage_target':.9,'n':len(calibration),'order_statistic':order,'absolute_error_quantile':float(errors[order-1]),
            'method':'90% marginal split-conformal absolute residual in endpoint-specific negative-log molar space'},
        'domain':{'training_fingerprints':[r['fp'].ToBitString() for r in training],
            'training_graph_hashes':[r['graph']['graph_sha256'] for r in training],
            'descriptor_ranges':{name:[min(r['properties'][name] for r in training),max(r['properties'][name] for r in training)] for name in FEATURES['descriptors']},
            'similarity_threshold':max(.4,float(np.quantile(nearest,.1)))},
        'trees':[{'left':t.tree_.children_left.tolist(),'right':t.tree_.children_right.tolist(),'feature':t.tree_.feature.tolist(),
            'threshold':t.tree_.threshold.tolist(),'value':t.tree_.value[:,0,0].tolist()} for t in forest.estimators_]}
    assert np.allclose(predicted,[forest_value(model,r['x'],model_schema=VERSION) for r in calibration],rtol=0,atol=1e-12)
    name=dataset['target']['target_accession']+'-'+dataset['kind']+'.json'
    payload=canonical(model)
    with (output/name).open('xb') as stream: stream.write(payload)
    return {'path':name,'sha256':digest(payload),'target_accession':model['target_accession'],'kind':model['kind'],'species':'Human'},model,test


def evaluate(model,rows):
    observations=[]
    width=model['calibration']['absolute_error_quantile']
    for row in rows:
        predicted=forest_value(model,row['x'],model_schema=VERSION)
        error=predicted-row['label']; domain=applicability(model,row['properties'],row['fp'],row['graph'])
        assert not domain['training_graph_seen']
        observations.append({'smiles':row['graph']['smiles'],'inchikey':row['graph']['inchikey'],'experimental':row['label'],
            'prediction':predicted,'error':error,'interval_covered':abs(error)<=width,'domain':domain['status'],
            'activity_ids':row['activity_ids'],'nearest_training_tanimoto':domain['nearest_training_tanimoto']})
    def metrics(items):
        if not items: return {'n':0}
        errors=[r['error'] for r in items]
        rho=float(spearmanr([r['experimental'] for r in items],[r['prediction'] for r in items]).statistic) if len(items)>2 else None
        return {'n':len(items),'mae':statistics.mean(abs(e) for e in errors),'rmse':math.sqrt(statistics.mean(e*e for e in errors)),
            'spearman':rho if rho is not None and math.isfinite(rho) else None,'interval_coverage':statistics.mean(r['interval_covered'] for r in items)}
    return {'unit':model['unit'],'overall':metrics(observations),'by_domain':{name:metrics([r for r in observations if r['domain']==name])
        for name in ('in_domain','weakly_supported','out_of_domain')},'rows':observations,
        'scope':'Internal scaffold-held-out unrelated binding labels; heterogeneous nominal assays, no external biological qualification'}


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--data',type=Path,required=True)
    parser.add_argument('--gate',type=Path,required=True); parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(); data_bytes=args.data.read_bytes(); data=json.loads(data_bytes)
    if (data['schema']!='pulsate.target-activity-datasets/v1' or data['candidate_activity_requested'] is not False
            or data['candidate_evaluated'] is not False): raise ValueError('Training data violated its candidate firewall')
    gate_bytes=args.gate.read_bytes(); gate=json.loads(gate_bytes)
    args.output.mkdir(parents=True,exist_ok=False)
    (args.output/'research-gate.json').write_bytes(gate_bytes)
    trained=[]; unsupported=[]
    for entry in data['datasets']:
        payload=(args.data.parent/entry['path']).read_bytes()
        if digest(payload)!=entry['sha256']: raise ValueError('Activity dataset hash changed')
        dataset=json.loads(payload)
        if dataset['status']!='acquired':
            unsupported.append({'target_accession':entry['target_accession'],'kind':entry['kind'],
                'reason':dataset.get('reason','No eligible binding evidence')}); continue
        try:
            verify_acquired_dataset(dataset,args.data.parent/Path(entry['path']).parent)
            fitted=train(dataset,entry['sha256'],args.output); trained.append(fitted)
            print(json.dumps({'target':entry['target_accession'],'phase':'model_frozen','sha256':fitted[0]['sha256']}),flush=True)
        except ValueError as error:
            unsupported.append({'target_accession':entry['target_accession'],'kind':entry['kind'],'reason':str(error)})
    # The manifest's complete model set is pinned before any test row is scored.
    manifest={'schema_version':MANIFEST,'panel_sha256':data['panel_sha256'],'data_manifest_sha256':digest(data_bytes),
        'candidate_evaluated':False,'models':[entry for entry,_,_ in trained],'unsupported':unsupported,'training_policy':POLICY,
        'gate':{'path':'research-gate.json','sha256':digest(gate_bytes)}}
    # Validation report binds the pre-evaluation model-set receipt. Avoid a
    # circular file hash: its byte hash is pinned by a separate envelope.
    core_bytes=canonical(manifest)
    (args.output/'model-set.json').write_bytes(core_bytes)
    print(json.dumps({'all_models_frozen_sha256':digest(core_bytes),'model_count':len(trained),'candidate_evaluated':False}),flush=True)
    report={'manifest_sha256':digest(core_bytes),'endpoints':{entry['path']:evaluate(model,rows) for entry,model,rows in trained}}
    report_bytes=canonical(report); (args.output/'held-out-validation.json').write_bytes(report_bytes)
    envelope={'schema':'pulsate.target-activity-deployment/v1','model_manifest':{'path':'model-set.json','sha256':digest(core_bytes)},
        'validation_report':{'path':'held-out-validation.json','sha256':digest(report_bytes)}}
    (args.output/'deployment.json').write_bytes(canonical(envelope))
    for entry,model,_ in trained:
        evidence=report['endpoints'][entry['path']]
        receipt=model_gate(evidence,gate,model)
        print(json.dumps({'target':entry['target_accession'],'kind':entry['kind'],'research_gate':receipt,
            'candidate_evaluated':False}),flush=True)


if __name__=='__main__': main()
