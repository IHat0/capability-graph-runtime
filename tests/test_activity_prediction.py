"""Synthetic contracts only; these fixtures are not scientific model validation."""
import json

import pytest

from cgr.pulsate_api.activity_prediction import VERSION, MANIFEST, load_models, predict_panel, quantitative_hypotheses
from cgr.pulsate_api.adme_prediction import molecular_features, forest_value, applicability
from cgr.pulsate_api.virtual_organism import canonical, digest
from test_prediction_validation import fixture as adme_fixture


def freeze(tmp_path, *, strict_gate=False):
    sha, _, validation, sources = adme_fixture()
    model = json.loads(sources[sha])
    model.pop('endpoint')
    model.update(schema_version=VERSION, target_accession='P00001', kind='Ki', unit='pKi',
        assay_type='B', assay_domain='Synthetic direct nominal binding assay',
        concentration_basis='assay_nominal', functional_direction=None)
    model['trees'] = [{'feature':[512,-2,-2], 'left':[1,-1,-1], 'right':[2,-1,-1],
        'threshold':[80.,-2.,-2.], 'value':[0.,7.,8.]}]
    original = json.loads(sources[validation['report_sha256']])['endpoints']['model.json']
    # The shared ADME fixture needs only one held-out row; rank replay needs a
    # varied synthetic set in the same declared scaffold partition.
    original['rows']=[]
    for n in range(2,60):
        graph,properties,_,fp=molecular_features('C'*n)
        if int(digest(('1:'+graph['smiles']).encode())[:8],16)%100>=85:
            original['rows'].append({'smiles':graph['smiles'],'inchikey':graph['inchikey'],
                'interval_covered':True,'domain':applicability(model,properties,fp,graph)['status']})
    model['training']['split_counts']['test']=len(original['rows'])
    masses=[molecular_features(r['smiles'])[2][512] for r in original['rows']]
    model['trees'][0]['threshold'][0]=(min(masses)+max(masses))/2
    split=[]
    for row in original['rows']:
        graph, _, features, _ = molecular_features(row['smiles'])
        score = forest_value(model,features,model_schema=VERSION)
        row.update(prediction=score, experimental=score-.2, error=.2)
        split.append({'graph':graph['graph_sha256'],'label':score-.2,'scaffold':graph['smiles']})
    assert len({r['prediction'] for r in original['rows']})>1
    model['training']['split_hashes']['test']=digest(canonical(split))
    model_bytes=canonical(model)
    gate={'schema':'pulsate.target-activity-research-gate/v1','kind':'Ki','species':'Human','unit':'pKi',
        'domain':'in_domain','reviewer':'Synthetic software contract','version':'synthetic',
        'rationale':'Not biological validation','limitations':['Synthetic numerical labels'],
        'limits':{'minimum_n':1,'minimum_interval_coverage':.8,'maximum_mae':.1 if strict_gate else .5,
            'maximum_rmse':.5,'minimum_spearman':.5}}
    manifest={'schema_version':MANIFEST,'panel_sha256':'a'*64,'candidate_evaluated':False,
        'models':[{'path':'model.json','sha256':digest(model_bytes),'target_accession':'P00001','kind':'Ki','species':'Human'}],
        'unsupported':[{'target_accession':'P00002','kind':'Ki','reason':'Synthetic insufficient dataset'}],
        'gate':{'path':'gate.json','sha256':digest(canonical(gate))}}
    manifest_bytes=canonical(manifest)
    metrics={'n':len(original['rows']),'mae':.2,'rmse':.2,'interval_coverage':1.,'spearman':1.}
    report=canonical({'manifest_sha256':digest(manifest_bytes),'endpoints':{'model.json':dict(original,
        overall=metrics,by_domain={'in_domain':metrics},unit='pKi')}})
    for name, payload in [('model.json',model_bytes),('gate.json',canonical(gate)),
            ('model-set.json',manifest_bytes),('report.json',report)]:
        (tmp_path/name).write_bytes(payload)
    envelope={'schema':'pulsate.target-activity-deployment/v1',
        'model_manifest':{'path':'model-set.json','sha256':digest(manifest_bytes)},
        'validation_report':{'path':'report.json','sha256':digest(report)}}
    (tmp_path/'deployment.json').write_bytes(canonical(envelope))
    panel={'targets':[{'target_accession':a,'species':'Human','gene':g,'name':'Synthetic '+g,'source_domains':['Fixture']}
        for a,g in [('P00001','GENEA'),('P00002','GENEB')]]}
    identity=molecular_features('CCC')[0]
    return identity,panel,tmp_path/'deployment.json'


def test_endpoint_units_calibration_and_every_panel_member_preserved(tmp_path):
    identity,panel,path=freeze(tmp_path)
    report,sources=predict_panel(identity,panel,'a'*64,path,timestamp='2026-01-01T00:00:00Z')
    assert len(report['targets'])==2
    first,second=report['targets']
    assert first['status']=='predicted' and first['kind']=='Ki'
    assert first['value_umol_l']==pytest.approx(.1)
    assert first['interval_umol_l']==pytest.approx([10**-1.5,10**-.5])
    assert first['functional_direction'] is None and not first['tissue_potency_transfer_supported']
    assert first['concentration_basis']=='assay_nominal'
    assert second['status']=='unsupported' and second['refusals']
    assert len(sources)==5 and all(digest(b)==sha for sha,b in sources.items())
    hypotheses=quantitative_hypotheses(report,identity)
    assert len(hypotheses)==1 and hypotheses[0]['classification']=='predicted'
    assert hypotheses[0]['organ'] is None and hypotheses[0]['kind']=='Ki'
    from cgr.pulsate_api.virtual_investigation import activity_overlap
    assert activity_overlap([],hypotheses[0])['status']=='insufficient_compatible_evidence'


def test_failed_gate_retains_refusal_without_a_numerical_candidate_value(tmp_path):
    identity,panel,path=freeze(tmp_path,strict_gate=True)
    report,_=predict_panel(identity,panel,'a'*64,path)
    assert report['targets'][0]['status']=='refused'
    assert report['targets'][0]['research_gate']['failed_metrics']==['mae']
    assert 'value_umol_l' not in report['targets'][0]
    assert quantitative_hypotheses(report,identity)==[]


@pytest.mark.parametrize('defect',['identity','timestamp','panel','training_overlap','outside_domain'])
def test_prediction_cannot_escape_identity_time_panel_or_model_domain(tmp_path,defect):
    identity,panel,path=freeze(tmp_path)
    if defect=='identity': identity['inchikey']='incorrect'
    elif defect=='panel': panel['targets']=panel['targets'][1:]
    elif defect=='training_overlap': identity=molecular_features('CO')[0]
    elif defect=='outside_domain': identity=molecular_features('FC(F)(F)C(F)(F)F')[0]
    if defect in {'identity','panel','timestamp'}:
        with pytest.raises(ValueError): predict_panel(identity,panel,'a'*64,path,timestamp='2026-01-01' if defect=='timestamp' else None)
    else:
        report,_=predict_panel(identity,panel,'a'*64,path)
        assert report['targets'][0]['status']=='refused' and 'value_umol_l' not in report['targets'][0]


@pytest.mark.parametrize('defect',['model_bytes','report_bytes','rehashed_metrics','rehashed_prediction','rehashed_rank'])
def test_source_hashes_and_actual_numerical_replay_both_required(tmp_path,defect):
    _,_,path=freeze(tmp_path)
    envelope=json.loads(path.read_bytes())
    if defect.endswith('_bytes'):
        (tmp_path/('model.json' if defect=='model_bytes' else 'report.json')).write_bytes(b'{}')
    else:
        report=json.loads((tmp_path/'report.json').read_bytes())
        evidence=report['endpoints']['model.json']
        if defect=='rehashed_prediction': evidence['rows'][0]['prediction']=0.
        elif defect=='rehashed_rank': evidence['overall']['spearman']=0.
        else: evidence['by_domain']['in_domain']['mae']=0.
        payload=canonical(report); (tmp_path/'report.json').write_bytes(payload)
        envelope['validation_report']['sha256']=digest(payload)
        path.write_bytes(canonical(envelope))
    with pytest.raises(ValueError): load_models(path,'a'*64)


def test_model_cannot_be_laundered_as_adme(tmp_path):
    freeze(tmp_path)
    from cgr.pulsate_api.prediction_validation import verify_adme_validation
    model=(tmp_path/'model.json').read_bytes(); manifest=(tmp_path/'model-set.json').read_bytes()
    report=(tmp_path/'report.json').read_bytes(); gate=(tmp_path/'gate.json').read_bytes()
    sources={digest(b):b for b in (model,manifest,report,gate)}
    with pytest.raises(ValueError,match='binding-activity model'):
        verify_adme_validation(digest(model),{},dict(report_sha256=digest(report),manifest_sha256=digest(manifest),gate_sha256=digest(gate)),sources)


def test_investigation_prediction_replays_without_network_or_candidate_defaults(tmp_path):
    identity,panel,path=freeze(tmp_path)
    from cgr.pulsate_api.virtual_investigation import predict_activity
    config={'target_activity_models':{'path':path.name}}
    report,activities,sources=predict_activity(identity,config,tmp_path,panel,'a'*64,timestamp='2026-01-01T00:00:00Z')
    second,other,again=predict_activity(identity,config,tmp_path,panel,'a'*64,timestamp=report['timestamp'])
    assert canonical(report)==canonical(second) and canonical(activities)==canonical(other) and sources==again
    assert predict_activity(identity,None,None,None,None)==(None,[],{})
