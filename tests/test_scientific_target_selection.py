"""Scope must be grounded and receptor choice must follow source evidence, not names."""
import json
from types import SimpleNamespace

import pytest

from cgr.pulsate_api.scientific_target_selection import extract_target_scope, inspect_receptor
from cgr.pulsate_api.scientific_requirements import ProviderNeutralScientificRequirementInterpreter


def test_scope_is_preserved_across_scientist_turns():
    turns = [SimpleNamespace(content='Compare compounds against target-X.'),
             SimpleNamespace(content='Use species-Y and domain-Z.')]
    provider = SimpleNamespace(complete=lambda messages: json.dumps(
        {'name': 'target-X', 'organism': 'species-Y', 'domain': 'domain-Z'}))
    assert extract_target_scope(turns, provider)['domain'] == 'domain-Z'


@pytest.mark.parametrize('field,value', [('organism', 'invented'), ('domain', ''), ('name', 42)])
def test_scope_never_invents_missing_fields(field, value):
    document = {'name': 'target-X', 'organism': 'species-Y', 'domain': 'domain-Z'}
    document[field] = value
    provider = SimpleNamespace(complete=lambda messages: json.dumps(document))
    assert extract_target_scope([SimpleNamespace(content='target-X species-Y domain-Z')], provider) is None


def test_invalid_model_operation_is_retried_not_silently_dropped():
    outputs = iter([
        {'requirements': [{'operation': 'invented_operation'}]},
        {'goal_quotes': ['Compare the compounds']},
        {'requirements': [{'operation': 'dock_candidates', 'requested_output': 'docking_evidence',
                           'supporting_quote': 'Compare the compounds'}]},
        {'candidate_selection': None},
    ])
    provider = SimpleNamespace(provider_kind='test', model_name='test',
        complete=lambda messages: json.dumps(next(outputs)))
    result = ProviderNeutralScientificRequirementInterpreter(provider).propose('Compare the compounds against the target.')
    assert result.requirements[0].operation == 'dock_candidates'


def test_model_cannot_expand_operation_enum_on_retry():
    provider = SimpleNamespace(provider_kind='test', model_name='test',
        complete=lambda messages: json.dumps({'requirements': [{'operation': 'invented_operation'}]}))
    assert ProviderNeutralScientificRequirementInterpreter(provider).propose('Compare compounds.') is None


def test_receptor_without_unique_deposited_site_is_rejected():
    with pytest.raises(ValueError, match='no unique ligand-defined site'):
        inspect_receptor(SimpleNamespace(payload=b'END\n'), {'chains': ['A']})


def test_deposited_mutation_is_not_silently_accepted():
    line = 'SEQADV'.ljust(49) + 'ENGINEERED MUTATION'
    with pytest.raises(ValueError, match='mutation/conflict'):
        inspect_receptor(SimpleNamespace(payload=line.encode()), {'chains': ['A']})
