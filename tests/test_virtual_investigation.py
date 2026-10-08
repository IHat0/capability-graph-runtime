"""Generic/synthetic contracts, distinct from preserved real-model validation."""
import json
from datetime import date
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from rdkit import Chem

from cgr.pulsate_api.bioactivity_hypotheses import ScientificSources, normalize_activity, nominate_targets, verify_hypotheses
from cgr.pulsate_api.mechanistic_models import ModelContract, Perturbation, exposure_perturbation, runtime_status
from cgr.pulsate_api.phase8_scientific_handlers import _NativeRunner
from cgr.pulsate_api.prospective_dossier import acquire_dossier, select_regimen
from cgr.pulsate_api.prospective_evidence import ProspectivePolicy
from cgr.pulsate_api.virtual_investigation import activity_overlap, assess_candidate, compatible_experiments, exposure_evidence, model_refusal, runtime_refusal, VirtualInvestigationHandler, VirtualInvestigationVerificationHandler
from cgr.pulsate_api.virtual_organism import canonical, digest


def test_nested_structural_json_checks_content_not_serializer_spacing():
    from cgr.pulsate_api.virtual_investigation import same_json_document
    document = {'status': 'evaluated', 'rows': [{'score': -2.3, 'target': 'fixture'}]}
    assert same_json_document(document, json.dumps(document, indent=2).encode())
    assert same_json_document(document, canonical(document))
    altered = {'status': 'evaluated', 'rows': [{'score': -2.4, 'target': 'fixture'}]}
    assert not same_json_document(document, json.dumps(altered).encode())


def test_interval_activity_comparison_preserves_each_case_and_does_not_select_nominal():
    activity={'species':'Human','organ':'Liver','compartment':'Interstitial Unbound','concentration_basis':'unbound',
        'unit':'umol/l','assay_context':'Synthetic compatible assay only','target_accession':'P00001','kind':'IC50',
        'value':1.,'interval_umol_l':[.5,2.],'interval_definition':'Synthetic calibration interval', 'uncertainty':'Not biological validation'}
    def exposure(case,value):
        return {'species':'Human','organ':'Liver','compartment':'Interstitial Unbound','concentration_basis':'unbound',
            'unit':'umol/l','path':'Native|Zone','subject_identifier':'0','exposure_case_identifier':case,
            'times_h':[0,1,2],'values_umol_l':[0,value,0]}
    result=activity_overlap([exposure('A',.1),exposure('B',1.),exposure('C',3.)],activity)
    assert result['status']=='scenario_dependent_exposure_activity'
    assert [r['exposure_case_identifier'] for r in result['comparisons']]==['A','B','C']
    assert result['comparisons'][0]['peak_activity_ratio_interval']==[.05,.2]
    assert [r['status'] for r in result['comparisons']]==['modeled_peak_below_activity_range',
        'modeled_peak_overlaps_activity_range','modeled_peak_exceeds_activity_range']
    assert activity_overlap([dict(exposure('A',1),concentration_basis='total')],activity)['status']=='insufficient_compatible_evidence'
    for invalid in ([0,1],[2,1],[float('nan'),1],[True,1],[.1,.2]):
        with pytest.raises(ValueError): activity_overlap([exposure('A',1)],dict(activity,interval_umol_l=invalid))


@pytest.mark.parametrize('unit,factor', [('M',1e6),('mM',1e3),('uM',1.),('nM',1e-3),('pM',1e-6)])
def test_activity_units_are_not_docking_scores(unit, factor):
    row = {'standard_type':'Ki','standard_relation':'=','standard_value':'2','standard_units':unit}
    assert normalize_activity(row)['value_umol_l'] == 2*factor
    for changes in ({'standard_type':'Vina'}, {'standard_relation':'<'}, {'standard_units':'kcal/mol'},
                    {'standard_value':'nan'}, {'data_validity_comment':'invalid'}):
        with pytest.raises(ValueError): normalize_activity(dict(row,**changes))


def test_real_source_shape_replay_excludes_self_and_salts_and_retains_neighbour_measurement():
    query, neighbour = 'CCOc1ccccc1', 'CCCOc1ccccc1'
    def chemical(smiles, identifier):
        return {'molecule_chembl_id':identifier,'molecule_structures':{'canonical_smiles':smiles,
            'standard_inchi_key':Chem.MolToInchiKey(Chem.MolFromSmiles(smiles))}}
    def fetch(url,accept,bound):
        path = urlsplit(url).path
        assert 'only=' in url or 'fields=' in url
        assert 'description' not in url and 'clinical' not in url
        if '/similarity/' in path:
            return canonical({'molecules':[chemical(query,'CHEMBL1'),chemical(query+'.Cl','CHEMBL2'),chemical(neighbour,'CHEMBL3')]})
        if path.endswith('/activity.json'):
            assert 'molecule_chembl_id=CHEMBL3' in url  # no self/salt activity ever requested
            return canonical({'activities':[{'activity_id':17,'assay_chembl_id':'CHEMBL4','target_chembl_id':'CHEMBL5',
                'document_chembl_id':'CHEMBL6','standard_type':'Ki','standard_relation':'=', 'standard_value':'3','standard_units':'nM'}]})
        if '/assay/' in path: return canonical({'confidence_score':9,'relationship_type':'D','assay_organism':'Homo sapiens'})
        if '/target/' in path: return canonical({'target_type':'SINGLE PROTEIN','organism':'Homo sapiens','target_components':[{'accession':'P00002'}]})
        if '/document/' in path: return canonical({'year':1999,'document_chembl_id':'CHEMBL6'})
        raise OSError('Synthetic unavailable annotation')
    policy = ProspectivePolicy()
    source_set = ScientificSources(fetch=fetch)
    report, _ = nominate_targets(query, None, policy, sources=source_set)
    assert report['targets'][0]['target_accession']=='P00002'
    assert report['targets'][0]['candidate_quantitative_activity'] is None
    assert report['targets'][0]['evidence'][0]['quantitative_neighbour_evidence']['value_umol_l']==.003
    assert report['eligibility']['decisions'][0]['status']=='eligible_modern_general'
    assert verify_hypotheses(report,query,None,policy,source_set.payloads)['passed']
    report['targets'][0]['evidence'][0]['local_similarity'] = 1.
    with pytest.raises(ValueError,match='replay'): verify_hypotheses(report,query,None,policy,source_set.payloads)


def _library(records):
    sources = {}
    result = []
    for i,(role,category,value,unit,context) in enumerate(records):
        payload = canonical({'value':value,'unit':unit,'published':1999})
        sha = digest(payload)
        sources[sha] = payload
        result.append({'inchikey':'KEY-A','species':'Human','native_parameter':role,'endpoint_definition':'Synthetic contract fixture',
            'datum':{'identifier':'d-'+str(i),'source_url':'https://www.ebi.ac.uk/chembl/api/data/activity.json',
                'source_record':str(i),'source_sha256':sha,'publication_date':'1999-12-31','date_precision':'year',
                'category':category,'subject_inchikey':'KEY-A','value':value,'unit':unit,'evidence_type':'measured',
                'value_pointer':['value'],'context':dict(species='Human',**context),
                'original_source':{'source_url':'https://www.ebi.ac.uk/chembl/api/data/activity.json',
                    'source_record':'synthetic-original-'+str(i),'source_sha256':sha,
                    'source_kind':'original_primary_record','reviewer':'Synthetic fixture',
                    'review_basis':'Synthetic primary-data contract, not measured biological evidence',
                    'publication_date':'1999-12-31','date_precision':'year',
                    'publication_pointer':['published'],'publication_value':1999,
                    'value_pointer':['value'],'unit_pointer':['unit']}}})
    return {'records':result},sources


def test_reviewed_dossier_preserves_conflicts_and_excludes_unqualified_historical_inputs():
    smiles = 'CCO'
    key = Chem.MolToInchiKey(Chem.MolFromSmiles(smiles))
    library,sources = _library([('fraction_unbound','binding',.1,'fraction',{}),
        ('fraction_unbound','binding',.2,'fraction',{}), ('binding_partner','binding','Albumin',None,{}), ('ionization','ionization',[],None,{})])
    for record in library['records']:
        record['inchikey'] = key
        record['datum']['subject_inchikey'] = key
    identity = {'name':'synthetic graph','smiles':smiles,'inchikey':key}
    policy = ProspectivePolicy(cutoff=date(2000,1,1),cutoff_source='Synthetic reviewed protocol')
    acquired = acquire_dossier(identity,'Human',policy,library,sources)
    assert acquired['conflicts'][0]['parameter']=='fraction_unbound'
    assert 'fraction_unbound' not in acquired['dossier']['parameters']
    assert acquired['status']=='insufficient_or_conflicting_evidence'
    missing = acquire_dossier(identity,'Human',ProspectivePolicy(),library,sources)
    assert missing['dossier'] is None and len(missing['eligibility']['decisions'])==4
    assert all(not d['eligible'] for d in missing['eligibility']['decisions'])
    assert select_regimen(identity,missing,None)['status']=='requires_regimen'


@pytest.mark.parametrize('role,category,context', [
    ('fraction_unbound','protein_annotation',{}),
    ('hepatic_clearance','clearance',{'concentration_basis':'blood','endpoint':'intrinsic_cell_clearance'}),
    ('solubility','solubility',{'quantity_kind':'heterogeneous_aqueous_solubility'}),
])
def test_general_annotation_or_wrong_endpoint_cannot_launder_native_parameter(role,category,context):
    smiles='CCO'
    key=Chem.MolToInchiKey(Chem.MolFromSmiles(smiles))
    library,sources=_library([(role,category,.1,'fraction',context)])
    library['records'][0]['inchikey']=key
    library['records'][0]['datum']['subject_inchikey']=key
    if category == 'protein_annotation':
        acquired = acquire_dossier({'name':'fixture','smiles':smiles,'inchikey':key},'Human',
            ProspectivePolicy(cutoff=date(2000,1,1),cutoff_source='Synthetic reviewed protocol'),library,sources)
        assert not acquired['eligibility']['decisions'][0]['eligible']
    else:
        with pytest.raises(ValueError):
            acquire_dossier({'name':'fixture','smiles':smiles,'inchikey':key},'Human',
                ProspectivePolicy(cutoff=date(2000,1,1),cutoff_source='Synthetic reviewed protocol'),library,sources)


def model():
    return ModelContract(identifier='synthetic-model',revision='fixture',name='Synthetic interface contract',path='fixture.xml',
        sha256='a'*64,source_url='https://www.ebi.ac.uk/biomodels/fixture',license='test only',parameter_provenance='Synthetic fixture',
        species='Human',tissue='Liver',level='cellular_functional',time_unit='s',outputs={'[X]':'mol/l'},
        perturbable_parameters={'rate':'mol_per_l_s'},limitations=('Not a real scientific model.',),
        biological_parameters={'rate':synthetic_parameter_mapping()})


def synthetic_parameter_mapping():
    return {'model_sha256':'a'*64,'parameter':'rate','target_accession':'P00001','species':'Human','tissue':'Liver',
        'molecular_species':'synthetic-enzyme-active-form','parameter_meaning':'Synthetic catalytic rate test',
        'parameter_unit':'mol_per_l_s','transfer_law':'calibrated_fractional_activity'}


def calibration_sources():
    payload=canonical({'qualification':'Synthetic calibration contract test, not biological evidence'})
    return {digest(payload):payload}


def coupling():
    activity={'kind':'IC50','value':1.,'unit':'umol/l','species':'Human','target_accession':'P00001',
        'concentration_basis':'unbound','organ':'Liver','compartment':'Intracellular Unbound',
        'assay_context':'Synthetic cell contract','uncertainty':'Synthetic interval unquantified'}
    series={'species':'Human','organ':'Liver','compartment':'Intracellular Unbound','concentration_basis':'unbound',
        'unit':'umol/l','times_h':[0.,1.], 'values_umol_l':[0.,1.]}
    equation='fractional_activity = 1 / (1 + (unbound_concentration / functional_IC50) ** hill_coefficient)'
    calibration={'original_source_url':'https://www.ebi.ac.uk/biomodels/synthetic',
        'original_source_sha256':next(iter(calibration_sources())),'record_identifier':'Synthetic only',
        'exact_parameter_mapping':synthetic_parameter_mapping(),'validated_equation':equation,
        'assay_context':'Synthetic cell contract','applicability_basis':'Synthetic test only',
        'reviewer':'Synthetic','reviewer_qualification':'Synthetic test only','review_status':'qualified',
        'supporting_spans':[{'pointer':['qualification'],'expected':'Synthetic calibration contract test, not biological evidence'}]}
    transfer={'schema':'pulsate.reviewed-inhibition-transfer/v2','reviewer':'Synthetic','source':'Synthetic',
        'law':'reversible_fractional_activity','compatible_activity_kinds':['IC50'], 'target_accession':'P00001',
        'model_identifier':'synthetic-model','compartment':'Intracellular Unbound','qualified_assay_context':'Synthetic cell contract',
        'parameter':'rate','parameter_unit':'mol_per_l_s','hill_coefficient':1.,'uncertainty':'Synthetic transfer assumption',
        'version':'test1','reviewer_qualification':'Synthetic test only','review_status':'qualified',
        'molecular_species':'synthetic-enzyme-active-form','parameter_meaning':'Synthetic catalytic rate test',
        'model_sha256':'a'*64,'direction':'inhibition','equation':equation,'assumptions':['Synthetic context only'],
        'calibration_evidence':calibration,'calibration_sha256':digest(canonical(calibration)),
        'applicability_domain':{'species':'Human','tissue':'Liver','compartment':'Intracellular Unbound',
            'concentration_basis':'unbound','concentration_unit':'umol/l','activity_unit':'umol/l',
            'native_parameter_unit':'mol_per_l_s','biological_context':'Synthetic only',
            'exposure_to_assay_equivalence':'Synthetic test assertion, not Human qualification',
            'unbound_concentration_bounds_umol_l':[0,4],'limitations':['Synthetic only']}}
    return activity,series,transfer


def test_concentration_time_units_and_actual_exposure_drive_qualified_perturbation():
    activity,series,transfer=coupling()
    p=exposure_perturbation(model(),transfer,series,activity,evidence_eligible=True,evidence_sources=calibration_sources())
    assert p.times==(0.,3600.) and p.fractions_remaining==(1.,.5)
    assert p.exposure_sha256==digest(canonical(series))
    series['values_umol_l']=[0.,3.]
    assert exposure_perturbation(model(),transfer,series,activity,evidence_eligible=True,evidence_sources=calibration_sources()).fractions_remaining==(1.,.25)
    assert activity_overlap([series],activity)['peak_activity_ratios']==[3.]


def test_exploratory_transfer_preserves_actual_mapping_and_has_no_decision_authority(monkeypatch):
    # Mock only the separately tested research-package verifier here. The source,
    # model parameter, concentration law and downstream decision checks are real.
    import cgr.pulsate_api.research_qualification as qualification
    monkeypatch.setattr(qualification,'research_review',lambda *a,**k:{
        'scope':'exploratory_only','qualification_state':'exploratory_only',
        'candidate_decision_authority':False,'benchmark_discrepancy':'Synthetic mismatch only'})
    activity,series,transfer=coupling()
    transfer['review_status']='exploratory_reviewed'
    transfer['calibration_evidence']['review_status']='exploratory_reviewed'
    transfer['calibration_evidence']['exact_parameter_mapping']['transfer_law']='reviewed_fractional_activity'
    transfer['calibration_sha256']=digest(canonical(transfer['calibration_evidence']))
    contract=model().model_copy(update={'biological_parameters':{'rate':
        dict(synthetic_parameter_mapping(),transfer_law='reviewed_fractional_activity')}})
    p=exposure_perturbation(contract,transfer,series,activity,evidence_eligible=True,evidence_sources=calibration_sources())
    assert p.fractions_remaining==(1.,.5)
    assert p.transfer_receipt['qualified'] is False
    assert p.transfer_receipt['qualification_state']=='exploratory_only'
    assert p.transfer_receipt['candidate_decision_authority'] is False
    from cgr.pulsate_api.virtual_investigation import decision_eligible_response
    assert not decision_eligible_response({'perturbation':p.model_dump(mode='json')})
    with pytest.raises(ValueError,match='mapping'):
        exposure_perturbation(model(),transfer,series,activity,evidence_eligible=True,evidence_sources=calibration_sources())


def test_duplicate_reviewed_combinations_are_one_experiment_without_dropping_distinct_inputs():
    activity,series,transfer=coupling()
    activity['eligibility']={'eligible':True}
    experiments=compatible_experiments(model(),[transfer,transfer],[activity,activity],[series,series],calibration_sources())
    assert len(experiments)==1
    changed=dict(series,values_umol_l=[0.,3.])
    experiments=compatible_experiments(model(),[transfer],[activity],[series,changed],calibration_sources())
    assert len(experiments)==2
    assert {p.fractions_remaining for p,_,_ in experiments.values()}=={(1.,.5),(1.,.25)}
    activity['eligibility']['eligible']=False
    assert compatible_experiments(model(),[transfer],[activity],[series])=={}
    refusal=model_refusal(model())
    assert refusal['reason_code']=='no_qualified_transfer' and 'result' not in refusal


def test_optional_numerical_dependency_refusal_has_no_fabricated_curve(monkeypatch):
    import importlib
    def missing(name): raise ImportError('Synthetic missing optional engine')
    monkeypatch.setattr(importlib,'import_module',missing)
    status=runtime_status()
    assert not status['available'] and status['reason_code']=='numerical_dependency_unavailable'
    activity,series,transfer=coupling()
    perturbation=exposure_perturbation(model(),transfer,series,activity,evidence_eligible=True,evidence_sources=calibration_sources())
    refused=runtime_refusal(model(),perturbation,status)
    assert refused['status']=='refused' and refused['runtime']==status
    assert not {'curves','response','result'}.intersection(refused)
    assert refused['perturbation']['exposure_sha256']==digest(canonical(series))
    monkeypatch.setattr(importlib,'import_module',lambda name:SimpleNamespace(__version__='unqualified',getLibSBMLDottedVersion=lambda:'5.21.1'))
    assert runtime_status()['reason_code']=='numerical_version_unqualified'
    monkeypatch.setattr(importlib,'import_module',lambda name:SimpleNamespace(__version__='2.9.2',getLibSBMLDottedVersion=lambda:'5.21.1'))
    assert runtime_status()['available']


@pytest.mark.parametrize('defect', ['eligibility','species','unit','total_tissue','target','context','hill','unknown_parameter_unit',
    'activity_organ','activity_compartment','model_parameter'])
def test_insufficient_or_incompatible_mechanism_evidence_is_refused(defect):
    activity,series,transfer=coupling()
    if defect=='species': series['species']='Rat'
    if defect=='unit': activity['unit']='nM'
    if defect=='total_tissue': series['concentration_basis']='total'
    if defect=='target': activity['target_accession']='P00002'
    if defect=='context': activity['assay_context']='Different assay'
    if defect=='hill': transfer['hill_coefficient']=None
    if defect=='unknown_parameter_unit': transfer['parameter_unit']='not_declared'
    if defect=='activity_organ': activity['organ']='Heart'
    if defect=='activity_compartment': activity['compartment']='Plasma Unbound (Peripheral Venous Blood)'
    if defect=='model_parameter': transfer['parameter']='unrelated_rate'
    with pytest.raises(ValueError): exposure_perturbation(model(),transfer,series,activity,
        evidence_eligible=defect!='eligibility',evidence_sources=calibration_sources())


@pytest.mark.parametrize('defect',['old_schema','pending_review','molecular_species','meaning','equation','direction',
    'calibration_hash','missing_calibration_bytes','outside_domain','binding_not_function','model_revision','missing_annotation'])
def test_names_and_activity_points_do_not_qualify_human_transfer(defect):
    activity,series,transfer=coupling(); contract=model(); sources=calibration_sources()
    if defect=='old_schema': transfer['schema']='pulsate.reviewed-inhibition-transfer/v1'
    if defect=='pending_review': transfer['review_status']='pending'
    if defect=='molecular_species': transfer['molecular_species']='wrong-species'
    if defect=='meaning': transfer['parameter_meaning']='unrelated physiological rate'
    if defect=='equation': transfer['equation']='arbitrary fraction'
    if defect=='direction': transfer['direction']='activation'
    if defect=='calibration_hash': transfer['calibration_sha256']='b'*64
    if defect=='missing_calibration_bytes': sources={}
    if defect=='outside_domain': series['values_umol_l']=[0,5]
    if defect=='binding_not_function': activity['kind']='Ki'
    if defect=='model_revision': transfer['model_sha256']='b'*64
    if defect=='missing_annotation': contract=contract.model_copy(update={'biological_parameters':{}})
    with pytest.raises(ValueError): exposure_perturbation(contract,transfer,series,activity,
        evidence_eligible=True,evidence_sources=sources)


def test_unconfigured_engine_is_verified_partial_not_invented_human(monkeypatch):
    monkeypatch.delenv('PULSATE_VIRTUAL_INVESTIGATION_POLICY',raising=False)
    class Store:
        def __init__(self): self.values={}
        def read(self,r): return self.values[r.artifact_identifier]
        def write(self,r,payload): self.values[r.artifact_identifier]=payload
    store=Store()
    runner=_NativeRunner(store)
    invocation=SimpleNamespace(invocation_identifier='fixture-invocation')
    def write(kind,document):
        return runner.write_json(artifact_type=kind,payload=canonical(document),producer='test',execution_identifier='fixture')
    descriptor=write('molecular_candidate_evaluation',{'candidate_identifier':'candidate-A','canonical_smiles':'CCO'})
    trace=write('discovery_design_loop_trace',{'loop_complete':True,'candidates':[{'candidate_identifier':'candidate-A',
        'display_name':'Synthetic candidate','evidence':[{'artifacts':[{'artifact_identifier':descriptor.artifact_identifier}]}]}]})
    pocket=write('binding_pocket',{})
    record=SimpleNamespace(artifact_references=(descriptor,trace,pocket),verified=True)
    outcome=VirtualInvestigationHandler(store).execute(invocation=invocation,objective=None,record=record)
    record.artifact_references+=(*outcome.output_artifacts,*outcome.evidence_artifacts)
    report=json.loads(store.read(outcome.output_artifacts[0]))
    assert report['candidates'][0]['virtual_organism'] is None
    check=VirtualInvestigationVerificationHandler(store).execute(invocation=invocation,objective=None,record=record)
    assert json.loads(store.read(check.output_artifacts[0]))['passed']
    report['candidates'][0]['inference_levels']['clinical']=True
    store.values[outcome.output_artifacts[0].artifact_identifier]=canonical(report)
    with pytest.raises(Exception,match='Inference levels'):
        VirtualInvestigationVerificationHandler(store).execute(invocation=invocation,objective=None,record=record)


def test_parameter_perturbation_rejects_nonfinite_or_unordered_curves():
    for times,values in (((0.,1.),(1.,float('nan'))), ((0.,0.),(1.,1.)), ((0.,1.),(1.,2.))):
        with pytest.raises(ValueError): Perturbation(model_identifier='test',parameter='k',parameter_unit='unit',
            times=times,fractions_remaining=values,source='Synthetic',classification='explicit_validation_perturbation')


def test_progression_states_require_reviewed_criteria_and_never_establish_clinical_effect():
    # Synthetic decision-contract fixture, not a biological threshold/validation.
    candidate={'inference_levels':{'exposure':True,'cellular_functional':True},'quantitative_activity':[{}],
        'functional_models':[{'status':'computed','result':{'model':{'identifier':'fixture','species':'Human',
        'tissue':'Synthetic tissue','sha256':'a'*64},'inference_level':'cellular_functional','response':{
            'output':{'unit':'fixture-unit','maximum_absolute_difference':2.,'final_difference':-2.}}}}]}
    assert assess_candidate(candidate,None)['status']=='INSUFFICIENT EVIDENCE'
    raw=canonical({'review':'Synthetic criterion engineering fixture, NOT scientific qualification'})
    sources={digest(raw):raw}
    policy={'schema':'pulsate.computational-progression-policy/v2','reviewer':'Synthetic validation',
        'review_status':'qualified','reviewer_qualification':'Synthetic contract validator, not real expert review',
        'version':'synthetic-test-only','scope_kind':'nonclinical_computational','clinical_inference':False,
        'source':'Synthetic decision contract','scope':'Computational prioritization, not clinical advancement',
        'coverage_statement':'Synthetic covered endpoints only','required_levels':['exposure','cellular_functional'],
        'require_candidate_activity':True,'allow_advance':True,'functional_criteria':[{
            'model_identifier':'fixture','output':'output','metric':'maximum_absolute_difference',
            'operator':'>=','threshold':1.,'unit':'fixture-unit','status':'CONCERN',
            'source':'Synthetic test-only criterion','rationale':'Synthetic response trigger',
            'version':'synthetic-test-only','reviewer':'Synthetic validation','review_status':'qualified',
            'reviewer_qualification':'Synthetic test, not real review','endpoint':'Synthetic response',
            'evidence_level':'cellular_functional','scientific_justification':'Test-only criterion replay',
            'uncertainty_handling':'all_qualified_runs_no_unresolved_domain',
            'applicability_domain':{'model_identifier':'fixture','model_sha256':'a'*64,'species':'Human',
                'tissue':'Synthetic tissue','limitations':['Synthetic context only']},
            'provenance':{'source_sha256':digest(raw),'source_url':'https://pk-db.com/media/data/synthetic.json',
                'source_format':'json','record_identifier':'SYNTHETIC','compound_specific':False,
                'supporting_spans':[{'pointer':['review'],'expected':json.loads(raw)['review']}]}}]}
    assert assess_candidate(candidate,policy,sources)['status']=='CONCERN'
    candidate['inference_levels']['exposure']=False
    partial=assess_candidate(candidate,policy,sources)
    assert partial['status']=='INSUFFICIENT EVIDENCE' and partial['matched_criteria']
    candidate['inference_levels']['exposure']=True
    policy['functional_criteria'][0]['status']='REJECT'
    assert assess_candidate(candidate,policy,sources)['status']=='REJECT'
    candidate['functional_models'].append({'status':'refused','model_identifier':'fixture'})
    assert assess_candidate(candidate,policy,sources)['status']=='INSUFFICIENT EVIDENCE'
    candidate['functional_models'].pop()
    candidate['native_sensitivity']={'status':'planned','summary':{'complete_discrete_space':True},
        'design':{'cases':[{'case_identifier':'case-1'},{'case_identifier':'case-2'}]}}
    candidate['functional_models'][0]['exposure_case_identifier']='case-1'
    assert assess_candidate(candidate,policy,sources)['status']=='INSUFFICIENT EVIDENCE'
    import copy
    other=copy.deepcopy(candidate['functional_models'][0]);other['exposure_case_identifier']='case-2'
    candidate['functional_models'].append(other)
    assert assess_candidate(candidate,policy,sources)['status']=='REJECT'
    candidate['native_sensitivity']['summary']['complete_discrete_space']=False
    assert assess_candidate(candidate,policy,sources)['status']=='INSUFFICIENT EVIDENCE'
    candidate['functional_models'].pop();candidate.pop('native_sensitivity')
    policy['functional_criteria'][0]['threshold']=3.
    decision=assess_candidate(candidate,policy,sources)
    assert decision['status']=='ADVANCE' and not decision['clinical_inference']
    # A large numerically computed exploratory effect is not a decision signal,
    # even if a caller erroneously labels the inference level available.
    candidate['functional_models'][0]['result']['perturbation'] = {'transfer_receipt': {
        'qualified':False,'qualification_state':'exploratory_only','candidate_decision_authority':False}}
    for status, threshold in [('CONCERN',1.),('REJECT',1.),('CONCERN',3.)]:
        policy['functional_criteria'][0].update(status=status,threshold=threshold)
        exploratory = assess_candidate(candidate,policy,sources)
        assert exploratory['status']=='INSUFFICIENT EVIDENCE' and not exploratory['matched_criteria']
    candidate['functional_models'][0]['result'].pop('perturbation')
    candidate['functional_models']=[]
    assert assess_candidate(candidate,policy,sources)['status']=='INSUFFICIENT EVIDENCE'
    policy['functional_criteria'][0]['unit']='wrong-unit'
    candidate['functional_models']=[{'status':'computed','result':{'model':{'identifier':'fixture',
        'species':'Human','tissue':'Synthetic tissue','sha256':'a'*64},'inference_level':'cellular_functional','response':{
        'output':{'unit':'fixture-unit','maximum_absolute_difference':2.,'final_difference':-2.}}}}]
    with pytest.raises(ValueError,match='units mismatch'): assess_candidate(candidate,policy,sources)
    with pytest.raises(ValueError,match='provenance'): assess_candidate(candidate,policy,{})
    policy['functional_criteria']=[]
    with pytest.raises(ValueError,match='explicit applicable'): assess_candidate(candidate,policy,sources)


def test_all_conditional_native_exposures_keep_case_population_and_subject_identity():
    activity,base,transfer=coupling()
    activity['eligibility']={'eligible':True}
    raw=dict(base,subject_identifier='subject-0',path='subject-0/Liver/Intracellular Unbound')
    def organism():
        return {'runs':[{'species':'Human','result':{'series':[raw],
            'population_physiology':{'subjects':[{'identifier':'subject-0','weight':70}]}}}]}
    sensitivity={'status':'planned','executions':[
        {'case_identifier':'case-1','status':'computed','virtual_organism':organism()},
        {'case_identifier':'case-2','status':'computed','virtual_organism':organism()},
        {'case_identifier':'case-3','status':'unsupported','reason':'Synthetic insufficient control'},
    ]}
    series=exposure_evidence(None,sensitivity)
    assert len(series)==2
    assert {s['exposure_case_identifier'] for s in series}=={'case-1','case-2'}
    assert len({digest(canonical(s)) for s in series})==2
    assert series[0]['native_population_sha256']==series[1]['native_population_sha256']
    assert all(s['subject_identifier']=='subject-0' and s['concentration_basis']=='unbound' for s in series)
    experiments=compatible_experiments(model(),[transfer],[activity],series,calibration_sources())
    assert len(experiments)==2  # equal curves do not erase distinct input cases
    assert {entry[2]['exposure_case_identifier'] for entry in experiments.values()}=={'case-1','case-2'}
    assert exposure_evidence(None,{'status':'refused'})==[]
    with pytest.raises(ValueError,match='arbitrary nominal'):
        exposure_evidence(organism(),sensitivity)


def test_native_total_exposure_is_never_relabelled_as_unbound_tissue():
    _,base,_=coupling()
    raw=dict(base,compartment='Tissue')
    result={'runs':[{'species':'Human','result':{'series':[raw]}}]}
    assert exposure_evidence(result,None)[0]['concentration_basis']=='total'
    assert 'exposure_case_identifier' not in exposure_evidence(result,None)[0]


def test_explicit_deposited_receptor_mapping_is_unique_and_chain_scoped():
    from cgr.pulsate_api.scientific_off_targets import _intended_accession
    def dbref(chain,accession):
        text=list(' '*80)
        for start,value in ((0,'DBREF '),(12,chain),(26,'UNP'),(33,accession)): text[start:start+len(value)]=value
        return ''.join(text)
    source=SimpleNamespace(artifact_identifier='protein',artifact_type='protein_structure',media_type='chemical/x-pdb')
    prepared=SimpleNamespace(artifact_identifier='prepared',artifact_type='prepared_molecular_structure',media_type='chemical/x-pdb')
    pocket=SimpleNamespace(artifact_identifier='pocket',artifact_type='binding_pocket')
    values={'protein':(dbref('A','P00001')+'\n'+dbref('B','P00002')).encode(),
        'prepared':b'ATOM                 A\n','pocket':canonical({'source_protein_artifact_identifier':'protein'})}
    store=SimpleNamespace(read=lambda r: values[r.artifact_identifier])
    record=SimpleNamespace(artifact_references=(source,prepared,pocket))
    assert _intended_accession(record,store)=='P00001'
    values['prepared']=b''
    assert _intended_accession(record,store) is None


def test_native_bridge_reuses_all_existing_handlers_and_keeps_tool_refusal_not_a_curve(monkeypatch):
    from cgr.pulsate_api import virtual_organism_handlers as handlers
    from cgr.pulsate_api.virtual_investigation import native_experiment
    from cgr.pulsate_api.virtual_organism import NativePBPKFailure
    class Store:
        def __init__(self): self.values={}
        def read(self,r): return self.values[r.artifact_identifier]
        def write(self,r,payload): self.values[r.artifact_identifier]=payload
    def refused(snapshot,request,name):
        # Synthetic integration failure, never numerical/scientific acceptance.
        raise NativePBPKFailure('Synthetic unavailable native tool', {'failure-receipt.json':canonical({
            'status':'refused','input_sha256':digest(canonical(snapshot)), 'reason':'Synthetic unavailable native tool','files':{}})})
    monkeypatch.setattr(handlers,'NativePBPKEngine',lambda executable:SimpleNamespace(run=refused))
    monkeypatch.setenv('PULSATE_PBPK_EXECUTABLE','synthetic-tool-not-used')
    smiles='CCO'
    identity={'name':'Synthetic integration fixture','smiles':smiles,'inchikey':Chem.MolToInchiKey(Chem.MolFromSmiles(smiles))}
    values={'molecular_weight':(46.069,'g/mol'),'logp':(1.,'Log Units'),'fraction_unbound':(.2,'fraction'),
        'solubility':(1000.,'mg/l'),'solubility_ph':(7.4,'pH'),'hepatic_clearance':(1.,'ml/min/kg'),
        'renal_clearance':(.1,'ml/min/kg'),'reference_weight':(70.,'kg')}
    dossier=dict(identity,species='Human',binding_partner='Albumin',ionization=[],ionization_source='Synthetic neutral assumption',
        parameters={k:dict(value=v,unit=u,classification='assumed',source='Synthetic integration contract',method='Not a real measurement') for k,(v,u) in values.items()},
        limitations=['Synthetic values, not biological validation'])
    acquisition={'species':'Human','dossier':dossier}
    regimen={'request':{'entity_name':identity['name'],'dose':1.,'dose_unit':'mg','route':'Intravenous','duration_h':2.}}
    result,refs,workflow=native_experiment(Store(),identity,acquisition,regimen,SimpleNamespace(invocation_identifier='synthetic'))
    assert len(workflow)==6 and result['verification']['passed']
    assert result['runs']==[] and result['status']=='insufficient_parameterization'
    assert result['refused'][0]['reason']=='Synthetic unavailable native tool'
    assert any(r.artifact_type=='pbpk_native_evidence' for r in refs)
