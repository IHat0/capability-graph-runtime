"""Synthetic source tests for coordinate mapping and companion provenance."""
import hashlib
from types import SimpleNamespace
import pytest
from cgr.pulsate_api.scientific_target_selection import inspect_receptor
from cgr.pulsate_api.scientific_conversation import ScientificEvidenceProposal
from cgr.pulsate_api.scientific_objectives import ScientificInputReference
from cgr.science import ArtifactReference, CreationProvenance
from cgr.kernel.contracts import CapabilityVersion


def _line(fields):
    line = list(' ' * 80)
    for start, end, value in fields:
        line[start:end] = str(value).ljust(end-start)[:end-start]
    return ''.join(line)


def _source(reference_start=100):
    lines = [_line([(0, 6, 'DBREF '), (12, 13, 'A'), (14, 18, '1'), (20, 24, '20'),
                    (33, 41, 'QTEST'), (55, 60, reference_start), (62, 67, reference_start+19)])]
    for i in range(1, 21):
        lines.append(_line([(0, 6, 'ATOM  '), (12, 16, ' CA '), (17, 20, 'ALA'),
                            (21, 22, 'A'), (22, 26, i), (30, 38, f'{i/10:.3f}'),
                            (38, 46, '0.000'), (46, 54, '0.000'), (76, 78, 'C')]))
    for i in range(12):
        lines.append(_line([(0, 6, 'HETATM'), (12, 16, ' C  '), (17, 20, 'LIG'),
                            (21, 22, 'A'), (22, 26, '50'), (30, 38, f'{i/10:.3f}'),
                            (38, 46, '1.000'), (46, 54, '0.000'), (76, 78, 'C')]))
    return SimpleNamespace(payload='\n'.join(lines).encode())


def test_site_contacts_use_reference_domain_not_author_numbering():
    candidate = {'chains': ['A'], 'domain_start': 100, 'domain_end': 119, 'uniprot_accession': 'QTEST',
                 'site_components': {'LIG': {'match_policy': 'synthetic exact identity'}}}
    assert inspect_receptor(_source(), candidate)['chain'] == 'A'
    with pytest.raises(ValueError, match='no unique ligand-defined site'):
        inspect_receptor(_source(reference_start=300), candidate)


@pytest.mark.parametrize('case,eligible', [('remote', True), ('site', False),
    ('terminal_proxy', True), ('internal_missing', False), ('conflict', False)])
def test_prospective_engineered_construct_policy_retains_scientific_boundaries(case, eligible):
    candidate = {'chains': ['A'], 'domain_start': 100, 'domain_end': 119, 'uniprot_accession': 'QTEST',
                 'site_components': {'LIG': {'match_policy': 'synthetic exact identity'}},
                 'mutation_policy': 'remote_engineered_annotation_only'}
    lines = _source().payload.decode().splitlines()
    seq = 0 if case == 'terminal_proxy' else 19
    if case in ('remote', 'terminal_proxy'):
        moved = 1 if case == 'terminal_proxy' else seq
        lines = [l[:30] + '30.000  ' + l[38:] if l.startswith('ATOM  ') and int(l[22:26]) == moved else l for l in lines]
    if case == 'internal_missing':
        lines = [l for l in lines if not (l.startswith('ATOM  ') and int(l[22:26]) == seq)]
    if case in ('terminal_proxy', 'internal_missing'):
        lines.append(_line([(0, 10, 'REMARK 465'), (15, 18, 'ALA'), (19, 20, 'A'), (21, 26, seq)]))
    lines.append(_line([(0, 6, 'SEQADV'), (16, 17, 'A'), (18, 22, seq),
                        (49, 80, 'CONFLICT' if case == 'conflict' else 'ENGINEERED MUTATION')]))
    source = SimpleNamespace(payload='\n'.join(lines).encode())
    if eligible:
        result = inspect_receptor(source, candidate)
        assert result['mutation_records'] and result['mutation_site_review'][0]['eligible']
        assert result['mutation_site_review'][0]['terminal_boundary_proxy'] == (case == 'terminal_proxy')
        assert result['mutation_site_review'][0]['limitation']
        with pytest.raises(ValueError, match='mutation/conflict'):
            inspect_receptor(source, dict(candidate, mutation_policy='strict_unmutated'))
    else:
        with pytest.raises(ValueError, match='no unique ligand-defined site'):
            inspect_receptor(source, candidate)


def _artifact(identifier, kind, metadata=None, parents=()):
    version = CapabilityVersion(major=1, minor=0, patch=0)
    return ArtifactReference(artifact_identifier=identifier, schema_version=version,
        artifact_type=kind, media_type='application/json', content_sha256=hashlib.sha256(identifier.encode()).hexdigest(),
        byte_size=1, metadata=metadata or {}, parents=parents,
        provenance=CreationProvenance(producer='test', producer_version=version, execution_identifier='test', source='test'))


def test_companion_requires_both_hash_and_exact_parent():
    protein = _artifact('protein', 'protein_structure', {'target_selection_sha256': hashlib.sha256(b'report').hexdigest()})
    report = _artifact('report', 'target_selection_report', parents=(protein.pointer,))
    arguments = dict(proposal_identifier='proposal', source_kind='exact_identifier_acquisition',
        summary='Acquired input and exact linked selection evidence.',
        input_references=(ScientificInputReference(reference_identifier='input', artifact_type='protein_structure',
                                                 artifact_identifier='protein'),))
    ScientificEvidenceProposal(**arguments, artifact_references=(protein, report))
    with pytest.raises(ValueError, match='hash-bind'):
        ScientificEvidenceProposal(**arguments, artifact_references=(protein, report.model_copy(update={'parents': ()})))
    wrong = protein.model_copy(update={'metadata': {'target_selection_sha256': '0'*64}})
    with pytest.raises(ValueError, match='hash-bind'):
        ScientificEvidenceProposal(**arguments, artifact_references=(wrong, report))
