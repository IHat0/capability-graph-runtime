"""Generic additive/site ambiguity regression. Synthetic geometry, not docking proof."""
from types import SimpleNamespace
import pytest
from cgr.pulsate_api import phase8_scientific_handlers as handlers
from cgr.pulsate_api.scientific_runtime import ScientificCapabilityFailure


def fixture(monkeypatch, second_large=False):
    def atom(kind,serial,residue,sequence,x):
        return f'{kind:6s}{serial:5d}  C1  {residue:3s} A{sequence:4d}    {x:8.3f}{0.:8.3f}{0.:8.3f}  1.00 20.00           C'
    lines=[atom('ATOM',i,'ALA',i,0.) for i in range(1,121)]
    lines += [atom('HETATM',200+i,'LIG',201,2.) for i in range(12)]
    lines += [atom('HETATM',300+i,'BUF',202,2.) for i in range(12 if second_large else 4)]
    protein=SimpleNamespace(artifact_identifier='protein',artifact_type='protein_structure',metadata={})
    record=SimpleNamespace(objective=SimpleNamespace(input_references=(protein,)),artifact_references=(protein,))
    store=SimpleNamespace(read=lambda ref: ('\n'.join(lines)+'\nEND\n').encode())
    monkeypatch.setattr(handlers,'_discovery_ligands',lambda *args:{'query':{'display_name':'Scientist candidate, not the deposited reference'}})
    return record,store


def test_unique_large_contact_defined_site_not_blocked_by_small_organic_additive(monkeypatch):
    record,store=fixture(monkeypatch)
    receptor,pocket=handlers._discovery_target(record,store)
    assert pocket['reference_residue'].startswith('LIG')
    assert not pocket['reference_site_selection_review']['candidate_identity_inferred']
    assert len(pocket['reference_site_selection_review']['components'])==2
    assert b'HETATM' not in receptor


def test_two_meaningful_sites_still_require_clarification(monkeypatch):
    record,store=fixture(monkeypatch,True)
    with pytest.raises(ScientificCapabilityFailure,match='one identifiable'):
        handlers._discovery_target(record,store)


def test_literal_chain_selects_only_deposited_coordinates_not_an_arbitrary_copy(monkeypatch):
    record,store=fixture(monkeypatch)
    raw=store.read(record.artifact_references[0]).decode()
    copies='\n'.join(line[:21]+'B'+line[22:30]+f'{float(line[30:38])+50:8.3f}'+line[38:]
        if line.startswith(('ATOM  ','HETATM')) else line for line in raw.splitlines())
    store.read=lambda ref:(raw+copies).encode()
    record.objective.original_request='Investigate the supplied candidate using chain B.'
    receptor,pocket=handlers._discovery_target(record,store)
    assert pocket['scientist_chain_scope']['chain']=='B'
    assert pocket['scientist_chain_scope']['supporting_quote']=='chain B'
    assert all(line[21]=='B' for line in receptor.decode().splitlines() if line.startswith('ATOM  '))
    assert pocket['reference_residue'][4]=='B'


def test_bound_nonpolymer_author_chain_may_differ_from_requested_protein_chain(monkeypatch):
    record,store=fixture(monkeypatch)
    raw=store.read(record.artifact_references[0]).decode()
    store.read=lambda ref:('\n'.join(line[:21]+'B'+line[22:] if line.startswith('HETATM') else line for line in raw.splitlines())+'\n').encode()
    record.objective.original_request='Use chain A.'
    receptor,pocket=handlers._discovery_target(record,store)
    assert pocket['scientist_chain_scope']['chain']=='A'
    assert pocket['reference_residue'][4]=='B'


@pytest.mark.parametrize('scientist_request,code',[
    ('Use chain Z.','receptor_chain_missing'),
    ('Use chain a.','receptor_chain_missing'),
    ('Compare chain A and chain B.','receptor_chain_ambiguous'),
    ('Use chain A or B.','receptor_chain_ambiguous'),
])
def test_chain_scope_never_guesses_or_collapses_conflicts(monkeypatch,scientist_request,code):
    record,store=fixture(monkeypatch);record.objective.original_request=scientist_request
    with pytest.raises(ScientificCapabilityFailure) as failure: handlers._discovery_target(record,store)
    assert failure.value.code==code
