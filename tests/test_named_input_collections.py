"""General named-input collection tests; no acceptance target or compound names."""
import json
from types import SimpleNamespace

import pytest

from cgr.pulsate_api.scientific_conversation import (
    ProviderNeutralScientificEvidenceInterpreter, ScientistEvidenceTurn,
)
from cgr.pulsate_api.research_sessions import ResearchSessionController, NamedInputResolutionRequired


def _entities(document):
    provider = SimpleNamespace(provider_kind='test', model_name='test',
        complete=lambda messages: json.dumps(document))
    turns = (ScientistEvidenceTurn(turn_identifier='turn-names',
        content='Compare compound-X, compound-Y and compound-Z against target-T.'),)
    return ProviderNeutralScientificEvidenceInterpreter(provider).propose_named_entities(turns)


def test_all_named_candidates_and_target_roles_are_retained():
    result = _entities({'protein_names': ['target-T'],
                       'ligand_names': ['compound-X', 'compound-Y', 'compound-Z']})
    assert [(item.entity_type, item.name) for item in result] == [
        ('protein', 'target-T'), ('ligand', 'compound-X'),
        ('ligand', 'compound-Y'), ('ligand', 'compound-Z')]
    assert all(item.turn_identifier == 'turn-names' for item in result)


@pytest.mark.parametrize('document', [
    {'protein_names': ['target-T'], 'ligand_names': ['invented']},
    {'protein_names': ['target-T'], 'ligand_names': ['target-T']},
    {'protein_names': [], 'ligand_names': 'compound-X'},
])
def test_ungrounded_or_conflicting_roles_fail_closed(document):
    assert _entities(document) == ()


def test_unresolved_member_is_not_dropped_when_another_ligand_already_exists():
    candidates = _entities({'protein_names': [], 'ligand_names': ['compound-X', 'compound-Y']})
    controller = SimpleNamespace(
        evidence_interpreter=SimpleNamespace(propose_named_entities=lambda turns: candidates),
        evidence_resolver=SimpleNamespace(acquire_named_entity=lambda **kwargs: None),
    )
    existing = SimpleNamespace(artifact_type='ligand_structure', metadata={'display_name': 'compound-X'})
    with pytest.raises(NamedInputResolutionRequired, match='compound-Y'):
        ResearchSessionController._named_collection_proposal(controller,
            scientist_turns=(), input_references=(), artifact_references=(existing,),
            tenant_identifier_sha256='a' * 64)
