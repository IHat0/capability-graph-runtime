"""Synthetic inference/held-out gate replay, never scientific certification."""
import copy

import pytest
from rdkit import rdBase

from cgr.pulsate_api.adme_prediction import VERSION, FEATURES, molecular_features, forest_value, applicability
from cgr.pulsate_api.prediction_validation import verify_adme_validation
from cgr.pulsate_api.virtual_organism import canonical, digest


def fixture():
    graphs=[]
    for n in range(2,20):
        smiles='C'*n
        if int(digest(('1:'+smiles).encode())[:8],16)%100>=85:
            graphs.append(smiles)
    assert graphs
    train,_,_,fp=molecular_features('CO')
    split=[]
    model={'schema_version':VERSION,'features':FEATURES,'endpoint':'fraction_unbound','species':'Human','unit':'log10(fraction)',
        'policy':{'seed':1},'training':{'rdkit_version':rdBase.rdkitVersion,'source_url':'Synthetic fixture',
            'split_counts':{'test':len(graphs)},'split_hashes':{}},
        'trees':[{'feature':[-2],'left':[-1],'right':[-1],'threshold':[-2.],'value':[-.8]}],
        'calibration':{'absolute_error_quantile':.5}, 'domain':{'training_graph_hashes':[train['graph_sha256']],
            'training_fingerprints':[fp.ToBitString()], 'descriptor_ranges':{n:[-1000.,10000.] for n in FEATURES['descriptors']},
            'similarity_threshold':0.}}
    rows=[]
    for smiles in graphs:
        graph,props,x,fp=molecular_features(smiles)
        pred=forest_value(model,x)
        domain=applicability(model,props,fp,graph)
        rows.append({'smiles':smiles,'inchikey':graph['inchikey'],'experimental':-1.,'prediction':pred,
            'error':pred+1.,'interval_covered':True,'domain':domain['status']})
        split.append({'graph':graph['graph_sha256'],'label':-1.,'scaffold':smiles})
    model['training']['split_hashes']['test']=digest(canonical(split))
    payload=canonical(model); sha=digest(payload)
    manifest=canonical({'schema_version':'pulsate.adme-model-manifest/v1',
        'models':[{'path':'model.json','endpoint':'fraction_unbound','species':'Human','sha256':sha}]})
    metrics={'n':len(rows),'mae':.2,'rmse':.2,'interval_coverage':1.}
    evidence={'rows':rows,'overall':metrics,'by_domain':{'in_domain':metrics},'unit':'log10(fraction)','scope':'Synthetic gate fixture'}
    assert all(r['domain']=='in_domain' for r in rows)
    report=canonical({'manifest_sha256':digest(manifest),'endpoints':{'model.json':evidence}})
    metadata={'report_sha256':digest(report),'manifest_sha256':digest(manifest),'overall':metrics,
        'by_domain':{'in_domain':metrics},'scope':evidence['scope'],'unit':model['unit']}
    gate=canonical({'schema':'pulsate.sponsor-prediction-gate/v1','endpoint':'fraction_unbound','species':'Human',
        'unit':model['unit'],'domain':'in_domain','reviewer':'Synthetic fixture','version':'synthetic',
        'rationale':'Software replay only','limitations':['Not biological validation'],
        'limits':{'minimum_n':1,'minimum_interval_coverage':.8,'maximum_mae':.5,'maximum_rmse':.5}})
    sources={digest(v):v for v in (payload,manifest,report,gate)}
    validation={'report_sha256':digest(report),'manifest_sha256':digest(manifest),'gate_sha256':digest(gate),'metrics':metrics}
    return sha,{'validation':metadata},validation,sources


def test_validation_gate_is_independently_replayed_from_model_rows_and_sources():
    sha,provenance,validation,sources=fixture()
    assert verify_adme_validation(sha,provenance,validation,sources)==provenance['validation']


@pytest.mark.parametrize('mutation',['metrics','metadata','missing_model','strict_gate','altered_prediction','training_overlap'])
def test_hash_pinned_pass_flags_cannot_replace_actual_validation(mutation):
    sha,provenance,validation,sources=fixture()
    if mutation=='metrics': validation['metrics']=dict(validation['metrics'],rmse=0.)
    elif mutation=='metadata': provenance['validation']['scope']='Invented external certification'
    elif mutation=='missing_model': sources.pop(sha)
    elif mutation=='strict_gate':
        import json
        gate=json.loads(sources[validation['gate_sha256']]); gate['limits']['maximum_rmse']=.01
        payload=canonical(gate); sources[digest(payload)]=payload; validation['gate_sha256']=digest(payload)
    else:
        import json
        report=json.loads(sources[validation['report_sha256']])
        row=report['endpoints']['model.json']['rows'][0]
        if mutation=='altered_prediction': row['prediction']=0.
        else: row['smiles']='CO'
        payload=canonical(report); sources[digest(payload)]=payload; validation['report_sha256']=digest(payload)
    with pytest.raises(ValueError): verify_adme_validation(sha,provenance,validation,sources)
