import copy
import importlib.util
from pathlib import Path

import pytest
from rdkit import Chem

from cgr.pulsate_api.functional_data import (assay_semantics, denial_connectivities, graph_allowed,
    numeric_guard, stratum_key, stratum_identifier)


def assay(description, **extra):
    return dict(confidence_score=9,relationship_type='D',assay_organism='Homo sapiens',
        assay_type='F',description=description,assay_cell_type=None,bao_format=None,**extra)


@pytest.mark.parametrize('description,family,action,readout',[
    ('Inhibition of current by voltage-clamp measurements',['Ion Channel'],'blocker','electrophysiological_current'),
    ('Agonist stimulation of cAMP in cells',['GPCR'],'agonist','camp'),
    ('Antagonist activity measured by calcium flux',['GPCR'],'antagonist','calcium_flux'),
    ('Antagonist inhibition of agonist-induced cAMP response',['GPCR'],'antagonist','camp'),
    ('Antagonism of agonist-stimulated calcium flux',['GPCR'],'antagonist','calcium_flux'),
    ('Inhibitory enzymatic activity measured by substrate hydrolysis',['Phosphodiesterase'],'inhibitor','enzymatic_activity'),
    ('Inhibition of substrate uptake',['Transporter'],'inhibitor','substrate_transport'),
    ('Inhibition of enzymatic activity',['COX'],'inhibitor','enzymatic_activity'),
    ('Inhibitory substrate metabolism',['MAO'],'inhibitor','enzymatic_activity'),
])
def test_direction_is_assay_semantics_not_a_drug_route(description,family,action,readout):
    result=assay_semantics(assay(description),family)
    assert result['action']==action and result['readout']==readout
    assert result['concentration_basis']=='assay_nominal'


@pytest.mark.parametrize('description,family',[
    ('Ligand binding IC50',['GPCR']),('Radioligand displacement',['GPCR']),
    ('Inhibition of cAMP production',['GPCR']), # Could be agonism at a Gi receptor.
    ('Agonist and antagonist cAMP activity',['GPCR']),('Inhibition',['Other Enzyme']),
    ('Agonist activity and antagonism against agonist-induced cAMP',['GPCR']),
    ('Inhibition of mutant enzymatic activity',['Kinase']),
    ('Agonist cAMP and calcium responses',['GPCR']),
])
def test_ambiguous_or_binding_assays_do_not_supply_functional_direction(description,family):
    with pytest.raises(ValueError):assay_semantics(assay(description),family)


def test_semantics_strata_preserve_endpoint_direction_and_readout():
    sem=assay_semantics(assay('Agonist cAMP stimulation'),['GPCR'])
    keys=[stratum_key('P00001','EC50',sem),stratum_key('P00001','IC50',sem),
        stratum_key('P00001','EC50',dict(sem,action='antagonist')),
        stratum_key('P00001','EC50',dict(sem,readout='calcium_flux'))]
    assert len({stratum_identifier(k) for k in keys})==4


def test_parent_salt_and_tautomer_denial_precedes_labels():
    denied=denial_connectivities('CC(=O)C')
    assert set(denied)&set(denial_connectivities('CC(O)=C'))
    assert set(denied)&set(denial_connectivities('CC(=O)C.[Na+]'))
    molecule=Chem.MolFromSmiles('CC(O)=C')
    with pytest.raises(ValueError,match='Excluded'):
        graph_allowed({'canonical_smiles':'CC(O)=C','standard_inchi_key':Chem.MolToInchiKey(molecule)},denied)
    from cgr.pulsate_api.functional_data import elemental_inventory
    with pytest.raises(ValueError,match='Excluded'):
        graph_allowed({'canonical_smiles':'CC(O)=C','standard_inchi_key':Chem.MolToInchiKey(molecule)},denied,elemental_inventory('CC(=O)C'))
    assert elemental_inventory('CC(O)=C')==elemental_inventory('CC(=O)C')


def test_label_query_requires_both_explicit_sets_and_exact_endpoint():
    filters={'molecule_chembl_id__in':'M1','assay_chembl_id__in':'A1','target_chembl_id':'T1','standard_type':'IC50'}
    row={'molecule_chembl_id':'M1','assay_chembl_id':'A1','target_chembl_id':'T1','standard_type':'IC50','standard_relation':'='}
    numeric_guard({'activities':[row]},filters,{'DENIED'})
    for field,value in [('molecule_chembl_id','DENIED'),('assay_chembl_id','OTHER'),('standard_type','Ki'),('target_chembl_id','OTHER')]:
        with pytest.raises(ValueError):numeric_guard({'activities':[dict(row,**{field:value})]},filters,{'DENIED'})
    with pytest.raises(ValueError):numeric_guard({'activities':[row]},dict(filters,assay_chembl_id__in=''),())


def test_primary_panel_rows_and_ambiguous_cells_are_not_silently_removed():
    from cgr.pulsate_api.functional_universe import compose_targets
    curation={'source_sha256':'s','families':[{'family':'GPCR','page':3,'rows':[['a','GENE'],['complex',None,'unspecified subunits']]}]}
    observed=[{'page':3,'label':'a','assay_format':'functional'},{'page':3,'label':'complex','assay_format':'binding'}]
    identities={'GENE':{'Entry':'P00001','Protein names':'name'}}
    targets,unsupported=compose_targets(curation,observed,{'targets':[]},identities)
    assert len(targets)==1 and unsupported[0]['status']=='unsupported_identity'
    with pytest.raises(ValueError):compose_targets(curation,observed[:1],{'targets':[]},identities)


def test_censored_source_audit_never_becomes_an_equal_training_label():
    filters={'molecule_chembl_id__in':'M1','assay_chembl_id__in':'A1','target_chembl_id':'T1',
        'standard_type':'IC50','standard_relation':'>'}
    row={'molecule_chembl_id':'M1','assay_chembl_id':'A1','target_chembl_id':'T1',
        'standard_type':'IC50','standard_relation':'>'}
    with pytest.raises(ValueError):numeric_guard({'activities':[row]},filters,())
    numeric_guard({'activities':[row]},filters,(),allowed_relations=('>',))
    with pytest.raises(ValueError):numeric_guard({'activities':[dict(row,standard_relation='=')]},filters,(),allowed_relations=('>',))
