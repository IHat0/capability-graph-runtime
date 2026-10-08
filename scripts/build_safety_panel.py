"""Freeze a general published target enumeration plus exact Human identities.

No candidate input or inference. Sources are technical primary-table and UniProt
identity fields only; the produced panel contains no drug-outcome narrative.
"""
from __future__ import annotations

import argparse
import urllib.parse
import urllib.request
from pathlib import Path

from cgr.pulsate_api.safety_pharmacology import SCHEMA, source_membership, target_identities, load_panel
from cgr.pulsate_api.virtual_organism import canonical, digest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--source-url', required=True)
    parser.add_argument('--table', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--reviewer', required=True)
    parser.add_argument('--version', required=True)
    args = parser.parse_args()
    membership_bytes = args.source.read_bytes()
    membership, discrepancies = source_membership(membership_bytes, args.table)
    query = '(organism_id:9606) AND (reviewed:true) AND (' + ' OR '.join('gene_exact:' + gene for gene in sorted(membership)) + ')'
    url = 'https://rest.uniprot.org/uniprotkb/search?' + urllib.parse.urlencode({
        'query': query, 'format': 'tsv', 'fields': 'accession,gene_primary,protein_name,organism_id', 'size': 500})
    with urllib.request.urlopen(urllib.request.Request(url, headers={'Accept': 'text/tab-separated-values'}), timeout=45) as response:
        identity_bytes = response.read(16 * 1024 * 1024 + 1)
        if len(identity_bytes) > 16 * 1024 * 1024 or response.headers.get('Link'):
            raise ValueError('Unbounded/paginated general target identity query; no partial panel accepted.')
    identities = target_identities(identity_bytes, membership)
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / 'membership.xml').write_bytes(membership_bytes)
    (args.output / 'target-identities.tsv').write_bytes(identity_bytes)
    doc = {'schema': SCHEMA, 'selection_basis': 'primary_general_panel_table', 'candidate_informed_selection': False,
        'reviewer': args.reviewer, 'version': args.version,
        'scope': 'General Human secondary-pharmacology screening coverage; not a clinical safety certificate',
        'membership_source': {'path': 'membership.xml', 'sha256': digest(membership_bytes), 'url': args.source_url, 'table_identifier': args.table},
        'identity_source': {'path': 'target-identities.tsv', 'sha256': digest(identity_bytes), 'url': url},
        'targets': [{'gene': gene, 'target_accession': identities[gene]['Entry'], 'name': identities[gene]['Protein names'],
            'species': 'Human', 'source_domains': membership[gene]} for gene in sorted(membership)],
        'source_count_discrepancies': discrepancies,
        'limitations': ['Exact source enumeration, not an assertion of complete safety-domain coverage.',
            'No missing targets added to reconcile source counts.', 'Panel membership is not candidate activity.',
            'Quantitative models and candidate-to-physiology transfers require separate validation.',
            'Engineering source review is not independent scientific qualification.']}
    (args.output / 'panel.json').write_bytes(canonical(doc))
    verified, _, sha = load_panel(args.output / 'panel.json')
    print({'panel_sha256': sha, 'target_count': len(verified['targets']), 'source_count_discrepancies': discrepancies,
        'candidate_evaluated': False}, flush=True)


if __name__ == '__main__':
    main()
