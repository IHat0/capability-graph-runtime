"""Synthetic source/selection contracts only; live docking evidence is separate."""
import json
from types import SimpleNamespace
from urllib.parse import urlsplit,parse_qs
import pytest
from rdkit import Chem
from cgr.pulsate_api.bioactivity_structures import compose_panel,verify_panel
from cgr.pulsate_api.phase8_scientific_handlers import _NativeRunner
from cgr.pulsate_api.scientific_off_targets import chemical_similarity
from cgr.pulsate_api.virtual_organism import canonical


class Store:
    def __init__(self): self.refs={};self.values={}
    def read(self,r): return self.values[r.artifact_identifier]
    def write(self,r,p): self.refs[r.artifact_identifier]=r;self.values[r.artifact_identifier]=p


def setup(defect=None):
    store=Store(); runner=_NativeRunner(store)
    def write(kind,value):
        return runner.write_json(artifact_type=kind,payload=canonical(value),producer='synthetic-test',execution_identifier='synthetic')
    query,neighbour='CCOc1ccccc1','CCCOc1ccccc1'
    key=Chem.MolToInchiKey(Chem.MolFromSmiles(neighbour))
    descriptor=write('molecular_candidate_evaluation',{'candidate_identifier':'synthetic-candidate','canonical_smiles':query})
    trace=write('discovery_design_loop_trace',{'loop_complete':True,'candidates':[{
        'candidate_identifier':'synthetic-candidate','evidence':[{'artifacts':[{'artifact_identifier':descriptor.artifact_identifier}]}]}]})
    record=SimpleNamespace(verified=True,artifact_references=(trace,descriptor))
    reports={'synthetic-candidate':{'targets':[{'target_accession':'P00002','evidence':[{
        'organism':'Homo sapiens','neighbour_inchikey':key,'neighbour_smiles':neighbour,
        'local_similarity':chemical_similarity(query,neighbour),'datum_identifier':'synthetic-neighbour-datum'}]}]}}
    dbref=list(' '*80)
    for start,value in ((0,'DBREF '),(12,'A'),(14,'   1'),(20,'  10'),(26,'UNP'),(33,'P00002'),(55,'    1'),(62,'   10')):
        dbref[start:start+len(value)]=value
    lines=['EXPDTA    X-RAY DIFFRACTION','REMARK   2 RESOLUTION.    2.00 ANGSTROMS.',''.join(dbref)]
    def atom(kind,serial,residue,sequence,coordinate):
        return f'{kind:6s}{serial:5d}  C1  {residue:3s} A{sequence:4d}    {coordinate:8.3f}{0.:8.3f}{0.:8.3f}  1.00 20.00           C'
    lines += [atom('ATOM',i,'ALA',i,float(i)/10) for i in range(1,11)]
    lines += [atom('HETATM',100+i,'LIG',101,2.+i/10) for i in range(12)]
    if defect=='mutation': lines.append('SEQADV'.ljust(49)+'ENGINEERED MUTATION')
    if defect=='no_site': lines=[l for l in lines if not l.startswith('HETATM')]
    payload=('\n'.join(lines)+'\nEND\n').encode()
    def fetch(url,*args,**kwargs):
        parsed=urlsplit(url)
        if parsed.hostname=='rest.uniprot.org':
            return canonical({'results':[{'primaryAccession':'P00002','organism':{
                'scientificName':'Mus musculus' if defect=='species' else 'Homo sapiens'}}]})
        if parsed.hostname=='data.rcsb.org':
            return canonical({'rcsb_chem_comp_descriptor':{'InChIKey':'WRONG' if defect=='identity' else key}})
        if parsed.hostname=='files.rcsb.org': return payload
        if parsed.hostname=='search.rcsb.org':
            request=json.loads(parse_qs(parsed.query)['json'][0])
            return canonical({'total_count':1,'result_set':[{'identifier':'LIG' if request['return_type']=='mol_definition' else 'SYN1'}]})
        raise AssertionError('Unexpected source URL')
    return store,record,reports,fetch


def test_exact_neighbour_target_species_and_site_compose_then_replay_without_network():
    store,record,reports,fetch=setup()
    invocation=SimpleNamespace(invocation_identifier='synthetic')
    panel,refs=compose_panel(record,store,invocation,reports,'P00001',fetch=fetch)
    target=panel['candidates'][0]['targets'][0]
    assert target['uniprot_accession']=='P00002' and target['component']=='LIG'
    assert target['classification']=='bioactivity_nominated_structure_supported_hypothesis'
    assert not panel['policy']['candidate_potency_inferred']
    full=SimpleNamespace(verified=True,artifact_references=record.artifact_references+tuple(refs))
    assert verify_panel(panel,full,store,invocation,reports,'P00001')['passed']
    panel['candidates'][0]['targets'][0]['similarity']=1.
    with pytest.raises(ValueError,match='replay'): verify_panel(panel,full,store,invocation,reports,'P00001')


@pytest.mark.parametrize('defect',['species','identity','mutation','no_site'])
def test_ineligible_structures_have_explicit_refusals_not_candidate_effects(defect):
    store,record,reports,fetch=setup(defect)
    invocation=SimpleNamespace(invocation_identifier='synthetic')
    panel,refs=compose_panel(record,store,invocation,reports,'P00001',fetch=fetch)
    candidate=panel['candidates'][0]
    assert candidate['targets']==[] and candidate['status']=='insufficient_structural_evidence'
    assert any(e.get('status')=='refused' for e in candidate['excluded'])
    full=SimpleNamespace(verified=True,artifact_references=record.artifact_references+tuple(refs))
    assert verify_panel(panel,full,store,invocation,reports,'P00001')['passed']


def test_intended_target_not_redocked_as_alternative_and_source_tamper_detected():
    store,record,reports,fetch=setup()
    invocation=SimpleNamespace(invocation_identifier='synthetic')
    panel,refs=compose_panel(record,store,invocation,reports,'P00002',fetch=fetch)
    assert not refs and not panel['candidates'][0]['targets']
    panel,refs=compose_panel(record,store,invocation,reports,'P00001',fetch=fetch)
    full=SimpleNamespace(verified=True,artifact_references=record.artifact_references+tuple(refs))
    store.values[refs[0].artifact_identifier]=b'tampered'
    with pytest.raises(ValueError,match='tampered'): verify_panel(panel,full,store,invocation,reports,'P00001')


def independent_setup(defect=None):
    """A third graph defines a pocket independently of the nominated neighbour."""
    store,record,reports,base=setup()
    reports['synthetic-candidate']['targets'][0]['evidence'][0]['target_chembl_id']='CHEMBL200'
    graph='CCCCCCOc1ccccc1'
    if defect in {'self','parent','self_entry'}:
        graph='CCOc1ccccc1'
    key=Chem.MolToInchiKey(Chem.MolFromSmiles(graph))
    reads=[]
    def fetch(url,*args,**kwargs):
        reads.append(url)
        parsed=urlsplit(url);params=parse_qs(parsed.query)
        if parsed.hostname=='search.rcsb.org':
            request=json.loads(params['json'][0])
            if request['return_type']=='mol_definition':return canonical({'total_count':0,'result_set':[]})
            return canonical({'total_count':1,'result_set':[{'identifier':'SYN1'}]})
        if parsed.hostname=='data.rcsb.org' and parsed.path=='/graphql':
            components=[{'nonpolymer_comp':{'chem_comp':{'id':'LIG'},'rcsb_chem_comp_descriptor':{'SMILES_stereo':graph,'InChIKey':key}}}]
            if defect=='self_entry':
                other='CCCCCCOc1ccccc1'
                components.insert(0,{'nonpolymer_comp':{'chem_comp':{'id':'OTH'},'rcsb_chem_comp_descriptor':{
                    'SMILES_stereo':other,'InChIKey':Chem.MolToInchiKey(Chem.MolFromSmiles(other))}}})
            return canonical({'data':{'entries':[{'rcsb_id':'SYN1','nonpolymer_entities':components}]}})
        if parsed.hostname=='www.ebi.ac.uk':
            endpoint=parsed.path.rsplit('/',1)[1]
            if endpoint=='molecule.json':
                return canonical({'page_meta':{'total_count':1},'molecules':[{'molecule_chembl_id':'CHEMBL100',
                    'molecule_structures':{'canonical_smiles':graph,'standard_inchi_key':'WRONG' if defect=='reference_identity' else key}}]})
            if endpoint=='CHEMBL200.json':
                return canonical({'target_chembl_id':'CHEMBL200','target_type':'SINGLE PROTEIN',
                    'organism':'Homo sapiens','target_components':[{'accession':'P00003' if defect=='reference_target' else 'P00002'}]})
            if endpoint=='activity.json':
                return canonical({'page_meta':{'total_count':1},'activities':[{'activity_id':101,
                    'molecule_chembl_id':'CHEMBL100','target_chembl_id':'CHEMBL200','assay_chembl_id':'CHEMBL300',
                    'document_chembl_id':'CHEMBL400','standard_type':'IC50','standard_relation':'=' if defect!='censored' else '>',
                    'standard_value':'150','standard_units':'nM' if defect!='unit' else 'kcal/mol'}]})
            if endpoint=='CHEMBL300.json':
                return canonical({'assay_chembl_id':'CHEMBL300','assay_organism':'Mus musculus' if defect=='reference_species' else 'Homo sapiens',
                    'confidence_score':8 if defect=='confidence' else 9,'relationship_type':'D','assay_type':'P' if defect=='assay_type' else 'B'})
            if endpoint=='CHEMBL400.json':return canonical({'document_chembl_id':'CHEMBL400','year':2020,'doi':'synthetic'})
            raise AssertionError(url)
        return base(url,*args,**kwargs)
    return store,record,reports,fetch,reads


def test_independent_reference_defines_pocket_without_becoming_neighbour_or_candidate_activity():
    store,record,reports,fetch,reads=independent_setup()
    invocation=SimpleNamespace(invocation_identifier='synthetic')
    panel,refs=compose_panel(record,store,invocation,reports,'P00001',fetch=fetch)
    selected=panel['candidates'][0]['targets'][0]
    reference=selected['independent_pocket_reference']
    assert reference['quantitative_reference_evidence']['value_umol_l']==pytest.approx(.15)
    assert reference['reference_inchikey']!=reports['synthetic-candidate']['targets'][0]['evidence'][0]['neighbour_inchikey']
    assert selected['bioactivity_datum_identifier']=='synthetic-neighbour-datum'
    assert reference['classification']=='independently_measured_pocket_reference_not_candidate_potency'
    full=SimpleNamespace(verified=True,artifact_references=record.artifact_references+tuple(refs))
    assert verify_panel(panel,full,store,invocation,reports,'P00001')['passed']
    source=next(r for r in refs if 'activity.json' in r.metadata.get('source_url',''))
    store.values[source.artifact_identifier]=b'tampered'
    with pytest.raises(ValueError,match='tampered'):verify_panel(panel,full,store,invocation,reports,'P00001')


@pytest.mark.parametrize('defect',['reference_identity','reference_target','reference_species','confidence','censored','unit','assay_type','self','parent','self_entry'])
def test_independent_reference_requires_semantic_activity_and_excludes_self_before_retrieval(defect):
    store,record,reports,fetch,reads=independent_setup(defect)
    panel,_=compose_panel(record,store,SimpleNamespace(invocation_identifier='synthetic'),reports,'P00001',fetch=fetch)
    assert panel['candidates'][0]['targets']==[]
    if defect in {'self','parent','self_entry'}:
        assert not any('activity.json' in url or 'files.rcsb.org' in url for url in reads)


def test_reference_source_projection_cannot_fetch_descriptive_or_clinical_fields():
    from cgr.pulsate_api.bioactivity_structures import _PocketSources
    store=Store()
    def forbidden(*args,**kwargs):raise AssertionError('Unsafe source reached network')
    sources=_PocketSources(_NativeRunner(store),SimpleNamespace(invocation_identifier='synthetic'),forbidden)
    with pytest.raises(ValueError,match='boundary'):
        sources.read('https://www.ebi.ac.uk/chembl/api/data/molecule.json?only=molecule_chembl_id,pref_name','unsafe')
