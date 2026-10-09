"""Source-replayed, candidate-independent functional screening universe.

Panel membership is not activity. Ambiguous complexes, aliases and isoforms
remain visible rather than being replaced with convenient single proteins.
"""
from __future__ import annotations

import io
import json
from pathlib import Path

from .safety_pharmacology import load_panel, target_identities
from .virtual_organism import canonical, digest

SCHEMA = 'pulsate.functional-target-universe/v1'


def verify_panel_cells(payload, rows, reviews, sources):
    """Membership cells, not measurements: verify both parser text layers.

    PDF column order differs between extractors. Check whole target-name lines
    in pypdf and adjacent cells in pdfplumber, never fabricate a shared prose
    span. One explicitly reviewed missing Greek glyph is a label alias only;
    this routine cannot admit numerical measurements or potency data.
    """
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(payload))
    for page in {row['page'] for row in rows}:
        review = next((r for r in reviews if r['page'] == page), None)
        if (not review or review.get('source_sha256') != digest(payload)
                or review.get('status') != 'rendered_page_verified'
                or not all(review.get(k) for k in ('reviewer', 'review_basis', 'render_method'))):
            raise ValueError('Panel cells lack an exact source-bound rendered review.')
        image = sources.get(review.get('image_sha256'))
        if (not image or digest(image) != review['image_sha256']
                or not image.startswith(b'\x89PNG\r\n\x1a\n')):
            raise ValueError('Panel review image changed or is missing.')
        lines = [' '.join(line.split()) for line in reader.pages[page - 1].extract_text().splitlines()]
        # PDF font emits this literal escape in pypdf; the reviewed rendered
        # cell and pdfplumber independently show mu. No other glyph is guessed.
        aliases = review.get('independent_label_aliases', {})
        for row in (r for r in rows if r['page'] == page):
            label = row['label']
            if label not in review.get('verified_literals', []):
                raise ValueError('An original panel cell was not visually reviewed.')
            independent = aliases.get(label, label)
            if lines.count(independent) != 1:
                raise ValueError('Independent whole panel target cell is missing or ambiguous: ' + label)


def vendor_rows(payload):
    """Read only the two explicitly reviewed target/format columns, not narratives."""
    import pdfplumber
    result = []
    with pdfplumber.open(io.BytesIO(payload)) as pdf:
        for page_number, lower, upper in ((3, 98., 752.), (4, 63., 330.)):
            page = pdf.pages[page_number - 1]
            words = page.extract_words()
            selected = [word for word in words if 104. <= (word['x0'] + word['x1']) / 2 <= 187.
                        and lower <= word['top'] <= upper]
            grouped = []
            for word in sorted(selected, key=lambda item: (item['top'], item['x0'])):
                if not grouped or abs(word['top'] - grouped[-1][0]) > 1.:
                    grouped.append((word['top'], [word]))
                else:
                    grouped[-1][1].append(word)
            for top, row in grouped:
                name = ' '.join(word['text'] for word in sorted(row, key=lambda item: item['x0']))
                if name == 'Target Name':
                    continue
                formats = [word for word in words if 199. <= (word['x0'] + word['x1']) / 2 <= 282.
                           and abs(word['top'] - top) <= 1.]
                assay_format = ' '.join(word['text'] for word in sorted(formats, key=lambda item: item['x0']))
                if not assay_format:
                    raise ValueError('Panel row has no exact adjacent assay-format cell.')
                result.append({'page': page_number, 'label': name, 'assay_format': assay_format})
    return result


def compose_targets(curation, observed_rows, primary_panel, identities):
    rows = []
    for family in curation['families']:
        for entry in family['rows']:
            rows.append({'page': family['page'], 'label': entry[0], 'gene': entry[1],
                         'family': family['family'], 'variant_limitation': entry[2] if len(entry) > 2 else None})
    if sorted((r['page'], r['label']) for r in rows) != sorted((r['page'], r['label']) for r in observed_rows):
        raise ValueError('Curated membership differs from the complete original vendor target columns.')
    original = {(r['page'], r['label']): r for r in observed_rows}
    targets = {}
    for row in rows:
        gene = row['gene']
        if gene is None:
            continue
        identity = identities[gene]
        accession = identity['Entry']
        target = targets.setdefault(accession, {'gene': gene, 'target_accession': accession,
            'species': 'Human', 'name': identity['Protein names'], 'families': [],
            'source_domains': [], 'membership': []})
        if row['family'] not in target['families']:
            target['families'].append(row['family'])
        target['membership'].append(dict(row, assay_format=original[row['page'], row['label']]['assay_format'],
            source_sha256=curation['source_sha256']))
    for source in primary_panel['targets']:
        target = targets.setdefault(source['target_accession'], {
            'gene': source['gene'], 'target_accession': source['target_accession'],
            'species': 'Human', 'name': source['name'], 'families': ['Primary general panel; family annotation pending'],
            'source_domains': [], 'membership': []})
        if identities[source['gene']]['Entry'] != source['target_accession']:
            raise ValueError('Independent panel Human identity changed.')
        target['source_domains'] = source['source_domains']
        target['membership'].append({'source_sha256': primary_panel['membership_source']['sha256'],
            'source_label': source['gene'], 'domains': source['source_domains']})
    result = sorted(targets.values(), key=lambda item: item['target_accession'])
    if not 1 <= len(result) <= 128:
        raise ValueError('Complete functional universe is outside its predeclared bound; no truncation.')
    return result, [dict(row, status='unsupported_identity', reason=row['variant_limitation'])
                    for row in rows if row['gene'] is None]


def load_universe(path):
    path = Path(path).resolve(strict=True)
    payload = path.read_bytes()
    doc = json.loads(payload)
    if (len(payload) > 2 * 1024 * 1024 or doc.get('schema') != SCHEMA
            or doc.get('candidate_informed') is not False or not doc.get('reviewer')
            or not doc.get('version') or not doc.get('limitations')):
        raise ValueError('Functional universe requires a bounded candidate-independent source review.')
    sources = {digest(payload): payload}

    def read(item, bound=16 * 1024 * 1024):
        resolved = (path.parent / item['path']).resolve(strict=True)
        if not resolved.is_relative_to(path.parent) or resolved.stat().st_size > bound:
            raise ValueError('Functional-universe source outside trusted path/bound.')
        raw = resolved.read_bytes()
        if digest(raw) != item['sha256']:
            raise ValueError('Functional-universe source hash mismatch.')
        sources[item['sha256']] = raw
        return raw

    vendor = read(doc['vendor_source'])
    curation = json.loads(read(doc['curation']))
    if curation['source_sha256'] != digest(vendor) or curation['source_url'] != doc['vendor_source']['url']:
        raise ValueError('Panel curation does not bind the exact original vendor bytes.')
    for review in doc['pdf_reviews']:
        read({'path': review['image_path'], 'sha256': review['image_sha256']})
    observed = vendor_rows(vendor)
    verify_panel_cells(vendor, observed, doc['pdf_reviews'], sources)
    protocol = json.loads(read(doc['protocol']))
    if protocol.get('schema') != 'pulsate.functional-research-protocol/v1':
        raise ValueError('Functional universe lacks its predeclared research protocol.')
    primary_entry = doc['primary_panel']
    read(primary_entry)
    primary, extra, _ = load_panel(path.parent / primary_entry['path'])
    sources.update(extra)
    identity_raw = read(doc['identity_source'])
    chunks = [read(item) for item in doc['identity_source']['source_snapshots']]
    if not chunks or any(raw.splitlines()[0] != chunks[0].splitlines()[0] for raw in chunks):
        raise ValueError('Human identity source headers disagree.')
    union = b'\n'.join([chunks[0].splitlines()[0], *sorted({line for raw in chunks for line in raw.splitlines()[1:]})]) + b'\n'
    if union != identity_raw:
        raise ValueError('Human identity union does not replay complete original responses.')
    genes = {row[1] for family in curation['families'] for row in family['rows'] if row[1]}
    genes.update(t['gene'] for t in primary['targets'])
    identities = target_identities(identity_raw, genes)
    targets, unresolved = compose_targets(curation, observed, primary, identities)
    if canonical(targets) != canonical(doc['targets']) or canonical(unresolved) != canonical(doc['unresolved_source_rows']):
        raise ValueError('Functional universe membership/identities do not replay their primary sources.')
    return doc, sources, digest(payload)
