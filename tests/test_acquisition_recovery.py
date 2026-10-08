"""Generic regressions for partial acquisition, scope normalization and retry limits."""
import io
import json
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

from cgr.pulsate_api import scientific_acquisition as acquisition
from cgr.pulsate_api import scientific_target_selection as selection
from cgr.pulsate_api.research_sessions import ResearchSessionController, NamedInputResolutionRequired
from cgr.pulsate_api.scientific_conversation import ScientificNamedInputCandidate, ProviderNeutralScientificEvidenceInterpreter, ScientistEvidenceTurn
from cgr.pulsate_api.scientific_objectives import ScientificInputReference


def test_literal_domain_ordinal_is_preserved():
    text = 'Compare compounds against species-Y target-X domain-Z 2.'
    provider = SimpleNamespace(complete=lambda messages: json.dumps(
        {'name': 'target-X domain-Z 2', 'organism': 'species-Y', 'domain': 'domain-Z'}))
    assert selection.extract_target_scope([SimpleNamespace(content=text)], provider) == {
        'name': 'target-X', 'organism': 'species-Y', 'domain': 'domain-Z 2'}


def test_normalized_target_mentions_are_resolved_once(monkeypatch):
    candidates = tuple(ScientificNamedInputCandidate(name=name, entity_type='protein',
        turn_identifier='turn', supporting_quote=name, provider_kind='test', model_name='test')
        for name in ('target-X', 'target-X domain-Z'))
    monkeypatch.setattr(selection, 'extract_target_scope', lambda *args: {
        'name': 'target-X', 'organism': 'species-Y', 'domain': 'domain-Z'})
    calls = []
    controller = SimpleNamespace(
        evidence_interpreter=SimpleNamespace(propose_named_entities=lambda turns: candidates),
        evidence_resolver=SimpleNamespace(acquire_named_entity=lambda **kwargs: calls.append(kwargs)),
    )
    with pytest.raises(NamedInputResolutionRequired):
        ResearchSessionController._named_collection_proposal(controller, scientist_turns=(),
            input_references=(), artifact_references=(), tenant_identifier_sha256='a'*64)
    assert len(calls) == 1


def test_later_acquired_candidate_does_not_replace_previous_candidate():
    def reference(name):
        return ScientificInputReference(reference_identifier=name, artifact_type='ligand_structure', artifact_identifier=name)
    first, second = reference('first'), reference('second')
    assert ResearchSessionController._accept_proposed_inputs((first,), (second,), merge=True) == (first, second)
    assert ResearchSessionController._accept_proposed_inputs((first,), (first,), merge=True) == (first,)


@pytest.mark.parametrize('status,expected_calls', [(503, 2), (404, 1)])
def test_transport_retries_only_transient_statuses(monkeypatch, status, expected_calls):
    calls = []
    class Response(io.BytesIO):
        headers = {}
        def geturl(self):
            return 'https://source.example/input'
    def open_request(request, timeout):
        calls.append(request)
        if len(calls) == 1:
            raise HTTPError(request.full_url, status, 'test', {}, None)
        return Response(b'evidence')
    monkeypatch.setattr(acquisition, 'urlopen', open_request)
    monkeypatch.setattr(acquisition.time, 'sleep', lambda delay: None)
    if status == 503:
        assert acquisition._fetch_bytes('https://source.example/input', 'text/plain', 100) == b'evidence'
    else:
        with pytest.raises(ValueError):
            acquisition._fetch_bytes('https://source.example/input', 'text/plain', 100)
    assert len(calls) == expected_calls


def test_retry_budget_is_bounded(monkeypatch):
    calls = []
    def fail(request, timeout):
        calls.append(request)
        raise HTTPError(request.full_url, 503, 'test', {}, None)
    monkeypatch.setattr(acquisition, 'urlopen', fail)
    monkeypatch.setattr(acquisition.time, 'sleep', lambda delay: None)
    with pytest.raises(ValueError):
        acquisition._fetch_bytes('https://source.example/input', 'text/plain', 100)
    assert len(calls) == 3


@pytest.mark.parametrize('label,value', [('SMILES','CCO'),('SMILES','C[N+](C)(C)C'),
    ('InChI','InChI=1S/CH4/h1H4'),('PubChem CID','12345')])
def test_labelled_structure_literals_are_not_sent_to_name_resolution(label,value):
    """Synthetic interpreter fixture; no molecule-specific production route."""
    text=f'Compare Compound-X and the ligand with {label}: {value} against Target-X using PDB 1ABC.'
    provider=SimpleNamespace(provider_kind='test',model_name='test',complete=lambda messages:json.dumps({
        'protein_names':['Target-X','1ABC'],'ligand_names':['Compound-X',value]}))
    turns=(ScientistEvidenceTurn(turn_identifier='turn-labelled',content=text),)
    result=ProviderNeutralScientificEvidenceInterpreter(provider).propose_named_entities(turns)
    assert [(c.entity_type,c.name) for c in result]==[('protein','Target-X'),('ligand','Compound-X')]


def test_unlabelled_name_is_not_treated_as_a_graph_by_syntax_guessing():
    provider=SimpleNamespace(provider_kind='test',model_name='test',complete=lambda messages:json.dumps({
        'protein_names':['Target-X'],'ligand_names':['CCO']}))
    result=ProviderNeutralScientificEvidenceInterpreter(provider).propose_named_entities((
        ScientistEvidenceTurn(turn_identifier='turn-unlabelled',content='Compare CCO against Target-X.'),))
    assert [c.name for c in result]==['Target-X','CCO']
