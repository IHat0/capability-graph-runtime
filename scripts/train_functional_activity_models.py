"""Fixed predeclared functional forests, frozen before scaffold test scoring."""
from __future__ import annotations

import argparse
import json
import math
import shutil
import statistics
from collections import defaultdict,Counter
from pathlib import Path

import numpy as np
from rdkit import Chem,DataStructs,rdBase
from rdkit.Chem.Scaffolds import MurckoScaffold
from sklearn import __version__ as sklearn_version
from sklearn.ensemble import RandomForestRegressor
from scipy.stats import spearmanr

from cgr.pulsate_api.adme_prediction import FEATURES,molecular_features,forest_value,applicability
from cgr.pulsate_api.functional_data import verify_dataset,source_index
from cgr.pulsate_api.virtual_organism import canonical,digest

VERSION='pulsate.functional-activity-rf/v1'
MANIFEST='pulsate.functional-activity-manifest/v1'


def dataset_rows(dataset,stratum,seed):
    grouped=defaultdict(list);rejected=Counter()
    for record in dataset['records']:
        if record['stratum']!=stratum: continue
        try:
            graph,properties,features,fp=molecular_features(record['smiles'])
            if graph['inchikey']!=record['inchikey']: raise ValueError('Feature identity mismatch.')
            grouped[graph['smiles']].append((record,graph,properties,features,fp))
        except (ValueError,TypeError,KeyError) as error: rejected[str(error)]+=1
    rows=[]
    for smiles,records in sorted(grouped.items()):
        _,graph,properties,features,fp=records[0]
        scaffold=MurckoScaffold.MurckoScaffoldSmiles(mol=Chem.MolFromSmiles(smiles),includeChirality=False) or smiles
        bucket=int(digest((str(seed)+':'+scaffold).encode())[:8],16)%100
        rows.append({'graph':graph,'properties':properties,'x':features,'fp':fp,
            'label':statistics.median(r[0]['p_activity'] for r in records),
            'activity_ids':sorted(r[0]['activity_id'] for r in records),'scaffold':scaffold,
            'split':'train' if bucket<70 else 'calibration' if bucket<85 else 'test'})
    return rows,dict(rejected)


def train(dataset,dataset_sha,stratum,protocol,output):
    policy=protocol['training'];rows,rejected=dataset_rows(dataset,stratum,policy['seed'])
    groups={name:[r for r in rows if r['split']==name] for name in ('train','calibration','test')}
    for name in groups:
        if len(groups[name])<policy['minimum_'+name+'_graphs']:
            raise ValueError('Insufficient independent '+name+' graphs: '+str(len(groups[name])))
    training,calibration,test=groups.values()
    rf=RandomForestRegressor(**{k:policy[k] for k in ('n_estimators','max_depth','min_samples_leaf','max_features')},
        random_state=policy['seed'],n_jobs=2)
    rf.fit(np.array([r['x'] for r in training],dtype=np.float32),[r['label'] for r in training])
    predictions=rf.predict(np.array([r['x'] for r in calibration],dtype=np.float32))
    errors=sorted(abs(r['label']-p) for r,p in zip(calibration,predictions,strict=True))
    order=min(len(errors),math.ceil((len(errors)+1)*.9))
    nearest=[max(DataStructs.BulkTanimotoSimilarity(r['fp'],[t['fp'] for t in training])) for r in calibration]
    key=dataset['strata'][stratum]
    model={'schema_version':VERSION,'features':FEATURES,**key,'unit':'p'+key['kind'],
        'policy':policy,'assay_domain':'Direct Human single-protein functional '+key['readout']+'; heterogeneous protocols explicitly preserved',
        'training':{'dataset_sha256':dataset_sha,'stratum':stratum,'rdkit_version':rdBase.rdkitVersion,
            'sklearn_version':sklearn_version,'rejected':rejected,'excluded_connectivities':dataset['excluded_connectivities'],
            'excluded_identifiers':dataset['excluded_identifiers'],'split_counts':{n:len(g) for n,g in groups.items()},
            'split_hashes':{n:digest(canonical([{'graph':r['graph']['graph_sha256'],'label':r['label'],'scaffold':r['scaffold']} for r in g])) for n,g in groups.items()},
            'split_activity_ids':{n:[r['activity_ids'] for r in g] for n,g in groups.items()}},
        'calibration':{'coverage_target':.9,'n':len(calibration),'order_statistic':order,
            'absolute_error_quantile':float(errors[order-1]),'method':'90% marginal split-conformal in endpoint-specific negative-log molar space'},
        'domain':{'training_fingerprints':[r['fp'].ToBitString() for r in training],
            'training_graph_hashes':[r['graph']['graph_sha256'] for r in training],
            'descriptor_ranges':{name:[min(r['properties'][name] for r in training),max(r['properties'][name] for r in training)] for name in FEATURES['descriptors']},
            'similarity_threshold':max(policy['minimum_similarity'],float(np.quantile(nearest,.1)))},
        'trees':[{'left':t.tree_.children_left.tolist(),'right':t.tree_.children_right.tolist(),
            'feature':t.tree_.feature.tolist(),'threshold':t.tree_.threshold.tolist(),'value':t.tree_.value[:,0,0].tolist()} for t in rf.estimators_]}
    assert np.allclose(predictions,[forest_value(model,r['x'],model_schema=VERSION) for r in calibration],rtol=0,atol=1e-12)
    payload=canonical(model);name=stratum+'.json';(output/name).write_bytes(payload)
    return {'path':name,'sha256':digest(payload),**key},model,test


def evaluate(model,rows):
    observations=[];width=model['calibration']['absolute_error_quantile']
    for row in rows:
        prediction=forest_value(model,row['x'],model_schema=VERSION);error=prediction-row['label']
        domain=applicability(model,row['properties'],row['fp'],row['graph'])
        if domain['training_graph_seen']: raise ValueError('Training overlap in held-out evaluation.')
        observations.append({'smiles':row['graph']['smiles'],'inchikey':row['graph']['inchikey'],
            'experimental':row['label'],'prediction':prediction,'error':error,'interval_covered':abs(error)<=width,
            'domain':domain['status'],'activity_ids':row['activity_ids'],'nearest_training_tanimoto':domain['nearest_training_tanimoto']})
    def metrics(items):
        if not items:return {'n':0}
        errors=[r['error'] for r in items]
        rho=float(spearmanr([r['experimental'] for r in items],[r['prediction'] for r in items]).statistic) if len(items)>2 else None
        return {'n':len(items),'mae':statistics.mean(abs(e) for e in errors),'rmse':math.sqrt(statistics.mean(e*e for e in errors)),
            'spearman':rho if rho is not None and math.isfinite(rho) else None,
            'median_multiplicative_error':10**statistics.median(abs(e) for e in errors),
            'interval_coverage':statistics.mean(r['interval_covered'] for r in items)}
    return {'unit':model['unit'],'overall':metrics(observations),'by_domain':{name:metrics([r for r in observations if r['domain']==name])
        for name in ('in_domain','weakly_supported','out_of_domain')},'rows':observations,
        'scope':'Internal unrelated scaffold-held-out functional assay validation; no independent biological/clinical qualification'}


def main():
    p=argparse.ArgumentParser();p.add_argument('--data',type=Path,required=True)
    p.add_argument('--protocol',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();raw=args.data.read_bytes();data=json.loads(raw)
    protocol_raw=args.protocol.read_bytes();protocol=json.loads(protocol_raw)
    if data['schema']!='pulsate.functional-datasets/v1' or data['candidate_activity_requested'] is not False:
        raise ValueError('Functional data are not candidate-excluded.')
    if digest(canonical(protocol))!=data['protocol_sha256']: raise ValueError('Predeclared protocol changed.')
    args.output.mkdir(parents=True,exist_ok=False)
    (args.output/'protocol.json').write_bytes(protocol_raw)
    exclusion_bytes=(args.data.parent/'exclusions.json').read_bytes()
    if digest(exclusion_bytes)!=data['exclusions_sha256']:raise ValueError('Denial identity dossier changed.')
    (args.output/'exclusions.json').write_bytes(exclusion_bytes)
    gate=dict(protocol['gate'],reviewer=protocol['reviewer'],version=protocol['version'])
    gate_raw=canonical(gate);(args.output/'research-gate.json').write_bytes(gate_raw)
    trained=[];unsupported=[];sources=[]
    for entry in data['datasets']:
        path=args.data.parent/entry['path'];payload=path.read_bytes()
        if digest(payload)!=entry['sha256']:raise ValueError('Functional dataset hash changed.')
        dataset=json.loads(payload)
        if dataset['status']!='acquired':
            unsupported.append({'target_accession':entry['target_accession'],'reason':dataset.get('reason','No eligible functional evidence'),
                'rejections':dataset['rejections']});continue
        verify_dataset(dataset,source_index(path.parent))
        copied=False
        for stratum,key in sorted(dataset['strata'].items()):
            try:
                fitted=train(dataset,entry['sha256'],stratum,protocol,args.output);trained.append(fitted)
                if not copied:
                    dest=args.output/'training-sources'/entry['target_accession'];dest.mkdir(parents=True)
                    shutil.copy2(path,dest/'dataset.json')
                    primary_hashes={r[k] for r in dataset['records'] for k in
                        ('graph_source_sha256','activity_source_sha256','assay_source_sha256','target_source_sha256')}
                    primary=[]
                    for sha in sorted(primary_hashes):
                        src=path.parent/(sha+'.json');shutil.copy2(src,dest/src.name)
                        primary.append({'path':str((dest/src.name).relative_to(args.output)).replace('\\','/'),'sha256':sha})
                    sources.append({'path':str((dest/'dataset.json').relative_to(args.output)).replace('\\','/'),
                        'sha256':entry['sha256'],'primary_sources':primary});copied=True
                print(json.dumps({'stratum':stratum,'phase':'model_frozen','sha256':fitted[0]['sha256']}),flush=True)
            except ValueError as error:unsupported.append(dict(key,reason=str(error)))
    manifest={'schema_version':MANIFEST,'universe_sha256':data['universe_sha256'],'data_manifest_sha256':digest(raw),
        'protocol':{'path':'protocol.json','sha256':digest(protocol_raw)},'candidate_evaluated':False,
        'models':[entry for entry,_,_ in trained],'unsupported':unsupported,'training_sources':sources,
        'gate':{'path':'research-gate.json','sha256':digest(gate_raw)},
        'excluded_connectivities':data['excluded_connectivities'],'excluded_identifiers':data['excluded_identifiers']}
    manifest['exclusions']={'path':'exclusions.json','sha256':digest(exclusion_bytes)}
    manifest['excluded_element_inventories']=data['excluded_element_inventories']
    core=canonical(manifest);(args.output/'model-set.json').write_bytes(core)
    print(json.dumps({'all_models_frozen_sha256':digest(core),'model_count':len(trained)}),flush=True)
    report={'manifest_sha256':digest(core),'endpoints':{entry['path']:evaluate(model,rows) for entry,model,rows in trained}}
    report_raw=canonical(report);(args.output/'held-out-validation.json').write_bytes(report_raw)
    envelope={'schema':'pulsate.functional-activity-deployment/v1',
        'model_manifest':{'path':'model-set.json','sha256':digest(core)},
        'validation_report':{'path':'held-out-validation.json','sha256':digest(report_raw)}}
    (args.output/'deployment.json').write_bytes(canonical(envelope))
    from cgr.pulsate_api.functional_prediction import model_gate
    for entry,model,_ in trained:
        print(json.dumps({**{k:entry[k] for k in ('target_accession','kind','action','readout')},
            'gate':model_gate(report['endpoints'][entry['path']],gate,model)}),flush=True)


if __name__=='__main__':main()
