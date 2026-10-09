"""Freeze general panel membership before any functional candidate inference."""
import argparse
import json
import shutil
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from cgr.pulsate_api.functional_universe import SCHEMA, vendor_rows, compose_targets, load_universe
from cgr.pulsate_api.safety_pharmacology import load_panel, target_identities
from cgr.pulsate_api.virtual_organism import canonical, digest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--curation', type=Path, required=True)
    parser.add_argument('--vendor-pdf', type=Path, required=True)
    parser.add_argument('--rendered-pages', type=Path, required=True)
    parser.add_argument('--primary-panel', type=Path, required=True)
    parser.add_argument('--protocol', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    curation_bytes = args.curation.read_bytes()
    curation = json.loads(curation_bytes)
    vendor = args.vendor_pdf.read_bytes()
    assert digest(vendor) == curation['source_sha256']
    primary, _, primary_sha = load_panel(args.primary_panel)
    genes = {row[1] for family in curation['families'] for row in family['rows'] if row[1]}
    genes.update(t['gene'] for t in primary['targets'])
    observed = vendor_rows(vendor)
    # Check every original row before any network identity query.
    assert sorted((f['page'], r[0]) for f in curation['families'] for r in f['rows']) == sorted((r['page'], r['label']) for r in observed)
    chunks, urls = [], []
    ordered_genes = sorted(genes)
    for start in range(0, len(ordered_genes), 25):
        batch = ordered_genes[start:start + 25]
        query = '(organism_id:9606) AND (reviewed:true) AND (' + ' OR '.join('gene_exact:' + gene for gene in batch) + ')'
        url = 'https://rest.uniprot.org/uniprotkb/search?' + urlencode({
            'query': query, 'format': 'tsv', 'fields': 'accession,gene_primary,protein_name,organism_id', 'size': 500})
        with urlopen(Request(url, headers={'Accept': 'text/tab-separated-values'}), timeout=60) as response:
            raw = response.read(16 * 1024 * 1024 + 1)
            assert len(raw) <= 16 * 1024 * 1024 and not response.headers.get('Link')
        chunks.append(raw)
        urls.append(url)
    header = chunks[0].splitlines()[0]
    assert all(raw.splitlines()[0] == header for raw in chunks)
    identity = b'\n'.join([header, *sorted({line for raw in chunks for line in raw.splitlines()[1:]})]) + b'\n'
    identities = target_identities(identity, genes)
    targets, unresolved = compose_targets(curation, observed, primary, identities)
    args.output.mkdir(parents=True, exist_ok=False)
    shutil.copytree(args.primary_panel.parent, args.output / 'primary-panel')
    (args.output / 'vendor.pdf').write_bytes(vendor)
    (args.output / 'curation.json').write_bytes(curation_bytes)
    (args.output / 'identities.tsv').write_bytes(identity)
    identity_snapshots = []
    for index, raw in enumerate(chunks):
        name = f'identities-source-{index}.tsv'
        (args.output / name).write_bytes(raw)
        identity_snapshots.append({'path': name, 'sha256': digest(raw), 'url': urls[index]})
    protocol_bytes = args.protocol.read_bytes()
    (args.output / 'protocol.json').write_bytes(protocol_bytes)
    reviews = []
    for page in (3, 4):
        png = (args.rendered_pages / f'page-{page}.png').read_bytes()
        name = f'page-{page}.png'
        (args.output / name).write_bytes(png)
        reviews.append({'page': page, 'source_sha256': digest(vendor), 'image_path': name,
            'image_sha256': digest(png), 'status': 'rendered_page_verified',
            'reviewer': curation['reviewer'], 'review_basis': 'Every original target row, family grouping and assay-format column visually inspected; ambiguous rows retained',
            'render_method': 'pdfplumber/pypdfium2 120 dpi; independent pypdf and pdfplumber text replay',
            'independent_label_aliases': {'Opioid (μ)': 'Opioid (/uni03BC)'} if page == 3 else {},
            'verified_literals': [r['label'] for r in observed if r['page'] == page]})
    doc = {'schema': SCHEMA, 'version': '2026-10-08-v1', 'candidate_informed': False,
        'reviewer': curation['reviewer'], 'scope': 'Bounded general Human secondary functional pharmacology; no candidate-specific target selection',
        'vendor_source': {'path': 'vendor.pdf', 'sha256': digest(vendor), 'url': curation['source_url']},
        'curation': {'path': 'curation.json', 'sha256': digest(curation_bytes)},
        'identity_source': {'path': 'identities.tsv', 'sha256': digest(identity), 'source_snapshots': identity_snapshots,
            'derivation': 'Exact identical-header union of every bounded complete primary response; no missing identities supplied'},
        'primary_panel': {'path': 'primary-panel/' + args.primary_panel.name, 'sha256': primary_sha},
        'protocol': {'path': 'protocol.json', 'sha256': digest(protocol_bytes)},
        'pdf_reviews': reviews, 'targets': targets, 'unresolved_source_rows': unresolved,
        'limitations': curation['limitations'] + ['General target inclusion is not measured or predicted candidate activity.']}
    (args.output / 'universe.json').write_bytes(canonical(doc))
    verified, _, sha = load_universe(args.output / 'universe.json')
    receipt = {'schema': 'pulsate.functional-universe-freeze/v1', 'universe_sha256': sha,
        'protocol_sha256': digest(protocol_bytes), 'candidate_evaluated': False,
        'human_protein_count': len(verified['targets']), 'unresolved_source_rows': len(unresolved),
        'original_vendor_rows': len(observed), 'source_sha256': digest(vendor)}
    (args.output / 'freeze.json').write_bytes(canonical(receipt))
    print(json.dumps(receipt, sort_keys=True))


if __name__ == '__main__':
    main()
