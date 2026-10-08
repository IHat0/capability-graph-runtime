"""An independently configured general screening panel, not a toxicity oracle.

Every enumerated target remains visible, including untested targets. Source
membership is replayed; a candidate, neighbour or desired result cannot add a
target to the panel. Potency/effects are never inferred from panel membership.
"""
from __future__ import annotations

import csv
import io
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from .virtual_organism import canonical, digest

SCHEMA = 'pulsate.general-safety-panel/v1'


def source_membership(payload, table_identifier):
    # ElementTree does not retrieve external DTDs. Permit an inert JATS external
    # declaration, but never declarations/internal subsets that expand entities.
    if (len(payload) > 16 * 1024 * 1024 or b'<!ENTITY' in payload.upper()
            or re.search(br'<!DOCTYPE[^>]*\[', payload, re.I)):
        raise ValueError('Safety panel source exceeds its XML bound or declares external entities.')
    root = ET.fromstring(payload)
    tables = [t for t in root.findall('.//table-wrap') if t.get('id') == table_identifier]
    if len(tables) != 1:
        raise ValueError('Safety panel requires one exact primary-source table.')
    members, discrepancies = {}, []
    for row in tables[0].findall('./table/tbody/tr'):
        cells = [''.join(c.itertext()).strip() for c in row.findall('td')]
        if len(cells) != 2:
            raise ValueError('Unsupported general panel table shape.')
        label, names = cells
        genes = [v.strip() for v in names.split(',')]
        if len(set(genes)) != len(genes) or any(not re.fullmatch(r'[A-Z][A-Z0-9]+', g) for g in genes):
            raise ValueError('Safety panel genes are ambiguous or duplicated.')
        stated = re.search(r'\((\d+)\)', label)
        if stated and int(stated.group(1)) != len(genes):
            discrepancies.append({'source_label': label, 'stated_count': int(stated.group(1)), 'enumerated_count': len(genes)})
        for gene in genes:
            members.setdefault(gene, []).append(label)
    if not 1 <= len(members) <= 128:
        raise ValueError('General safety panel target count is outside its bound.')
    return members, discrepancies


def target_identities(payload, genes):
    rows = list(csv.DictReader(io.StringIO(payload.decode('utf-8-sig')), delimiter='\t'))
    selected = {}
    for gene in genes:
        matches = [r for r in rows if gene in r.get('Gene Names (primary)', '').split()
            and r.get('Organism (ID)') == '9606']
        if len(matches) != 1 or not matches[0].get('Entry') or not matches[0].get('Protein names'):
            raise ValueError('General safety target lacks one exact reviewed Human identity: ' + gene)
        selected[gene] = matches[0]
    if len({r['Entry'] for r in selected.values()}) != len(selected):
        raise ValueError('Multiple panel symbols resolve to the same target; explicit source correction is required.')
    return selected


def load_panel(path):
    path = Path(path).resolve(strict=True)
    payload = path.read_bytes()
    doc = json.loads(payload)
    if (len(payload) > 2 * 1024 * 1024 or doc.get('schema') != SCHEMA
            or doc.get('selection_basis') != 'primary_general_panel_table'
            or doc.get('candidate_informed_selection') is not False
            or not all(doc.get(k) for k in ('reviewer', 'version', 'scope', 'limitations'))):
        raise ValueError('Safety panel must be an explicit general, non-candidate-informed reviewed configuration.')
    sources = {digest(payload): payload}
    for name in ('membership_source', 'identity_source'):
        item = doc[name]
        source_path = (path.parent / item['path']).resolve(strict=True)
        if not source_path.is_relative_to(path.parent) or source_path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError('Safety panel source is outside its trusted path/bound.')
        raw = source_path.read_bytes()
        if digest(raw) != item['sha256']:
            raise ValueError('Safety panel source hash mismatch.')
        sources[item['sha256']] = raw
    membership, discrepancies = source_membership(sources[doc['membership_source']['sha256']], doc['membership_source']['table_identifier'])
    identities = target_identities(sources[doc['identity_source']['sha256']], membership)
    expected = [{'gene': gene, 'target_accession': identities[gene]['Entry'],
        'name': identities[gene]['Protein names'], 'species': 'Human', 'source_domains': membership[gene]} for gene in sorted(membership)]
    if canonical(expected) != canonical(doc['targets']) or canonical(discrepancies) != canonical(doc['source_count_discrepancies']):
        raise ValueError('General safety panel membership/identity differs from its primary source.')
    return doc, sources, digest(payload)


def evaluate_panel(panel, panel_sha, hypotheses, activities, intended_accession):
    neighbours = {t['target_accession']: t for t in hypotheses.get('targets', [])}
    coverage = []
    for target in panel['targets']:
        accession = target['target_accession']
        candidate_activity = [a for a in activities if a['target_accession'] == accession and a['species'] == 'Human']
        neighbour = neighbours.get(accession)
        coverage.append(dict(target, intended_target=accession == intended_accession if intended_accession else None,
            status='candidate_quantitative_evidence' if candidate_activity else 'neighbour_nominated_hypothesis' if neighbour else 'not_evaluated',
            candidate_activity_sha256=[digest(canonical(a)) for a in candidate_activity],
            neighbour_hypothesis_sha256=digest(canonical(neighbour)) if neighbour else None,
            limitation='Panel inclusion is a predeclared screening question, not candidate binding, potency, target modulation or toxicity.'))
    return {'panel_sha256': panel_sha, 'version': panel['version'], 'scope': panel['scope'],
        'targets': coverage, 'source_count_discrepancies': panel['source_count_discrepancies'],
        'candidate_informed_selection': False, 'limitations': panel['limitations'],
        'coverage_statement': 'Every predeclared panel member retained. Not evaluated is not a negative activity result or evidence of safety.'}
