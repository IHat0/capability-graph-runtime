"""Bounded quantitative bioactivity-neighbour evidence, not predicted potency.

Fetch only explicitly projected scientific fields, not compound literature or
clinical narratives. Modern unrelated-ligand evidence may nominate a target;
the candidate's own database activity is excluded from this hypothesis search.
"""
from __future__ import annotations

import json
import math
import re
from datetime import date
from urllib.parse import quote, urlencode

from .prospective_evidence import EvidenceDatum, ProspectivePolicy, adjudicate, allowed_source
from .scientific_acquisition import _fetch_bytes
from .scientific_off_targets import chemical_similarity
from .virtual_organism import canonical, digest


POLICY = {'version': 'pulsate.bioactivity-neighbours/v1', 'remote_similarity_percent': 80,
    'local_morgan_minimum': .6, 'neighbours': 6, 'similarity_records': 24,
    'activities_per_neighbour': 32, 'targets': 8,
    'scope': 'Bounded chemical-neighbour hypotheses; no calibrated candidate potency or proteome completeness'}
CHEMBL = 'https://www.ebi.ac.uk/chembl/api/data/'
ACTIVITY_UNITS = {'M': 1e6, 'mM': 1e3, 'uM': 1., 'µM': 1., 'nM': 1e-3, 'pM': 1e-6}


class ScientificSources:
    def __init__(self, fetch=_fetch_bytes, preserved=None):
        self.fetch, self.payloads, self.audit = fetch, {}, []
        self.preserved = preserved

    def read(self, url):
        if not allowed_source(url):
            raise ValueError('Bioactivity source is outside the primary-source boundary.')
        previous = next((a for a in self.audit if a['url'] == url), None)
        if previous:
            if previous.get('error'):
                raise ValueError('Scientific source unavailable: ' + url)
            return json.loads(self.payloads[previous['sha256']]), previous['sha256']
        if self.preserved is not None:
            if url not in self.preserved:
                raise ValueError('Source required for replay is absent.')
            value = self.preserved[url]
            if isinstance(value, dict) and value.get('error'):
                self.audit.append(dict(value, url=url))
                raise ValueError('Scientific source unavailable: ' + url)
            payload = value
        else:
            try:
                payload = self.fetch(url, 'application/json', 4 * 1024 * 1024)
            except (OSError, ValueError, RuntimeError) as error:
                self.audit.append({'url': url, 'sha256': None, 'error': str(error)[:1000]})
                raise ValueError('Scientific source unavailable: ' + url) from error
        sha = digest(payload)
        document = json.loads(payload)
        self.payloads[sha] = payload
        self.audit.append({'url': url, 'sha256': sha, 'publication_date': None,
            'historical_qualification': 'Unknown unless separately established for the extracted datum'})
        return document, sha

    def chembl(self, endpoint, fields, **filters):
        url = CHEMBL + endpoint + '.json?' + urlencode(dict(filters, only=','.join(fields)))
        document, sha = self.read(url)
        return document, sha, url


def normalize_activity(record):
    if record.get('standard_type') not in {'Ki', 'Kd', 'IC50', 'EC50'} or record.get('standard_relation') != '=':
        raise ValueError('Unsupported/censored activity endpoint; no point potency inferred.')
    unit = record.get('standard_units')
    if unit not in ACTIVITY_UNITS:
        raise ValueError('Unsupported activity concentration unit.')
    if record.get('data_validity_comment') or record.get('potential_duplicate'):
        raise ValueError('Flagged/duplicate activity cannot support nomination.')
    value = float(record['standard_value']) * ACTIVITY_UNITS[unit]
    if not math.isfinite(value) or value <= 0:
        raise ValueError('Nonpositive/nonfinite quantitative activity.')
    return {'kind': record['standard_type'], 'value_umol_l': value,
        'original_value': record['standard_value'], 'original_unit': unit,
        'conversion_factor': ACTIVITY_UNITS[unit], 'classification': 'measured_neighbour_activity_not_candidate_activity',
        'uncertainty': 'Assay error not supplied; no candidate predictive interval established'}


def tissue_relevance(accession, sources):
    if not re.fullmatch(r'[A-Z0-9]{6,10}', accession):
        raise ValueError('Invalid protein accession.')
    url = 'https://rest.uniprot.org/uniprotkb/search?' + urlencode({
        'query': 'accession:' + accession, 'format': 'json', 'size': 2,
        'fields': 'accession,organism_name,protein_name,go,xref_ensembl'})
    doc, sha = sources.read(url)
    rows = doc.get('results', [])
    if len(rows) != 1 or rows[0].get('primaryAccession') != accession:
        raise ValueError('Ambiguous protein annotation identity.')
    annotation = rows[0]
    genes = set()
    go = []
    for cross in annotation.get('uniProtKBCrossReferences', []):
        if cross.get('database') == 'Ensembl':
            for prop in cross.get('properties', []):
                if prop.get('key') == 'GeneId':
                    gene = prop['value'].split('.')[0]
                    if re.fullmatch(r'ENSG\d{11}', gene): genes.add(gene)
        elif cross.get('database') == 'GO':
            props = {p['key']: p['value'] for p in cross.get('properties', [])}
            go.append({'identifier': cross['id'], 'term': props.get('GoTerm'), 'evidence': props.get('GoEvidenceType')})
    expression, missing = [], []
    if annotation.get('organism', {}).get('taxonId') == 9606 and len(genes) == 1:
        gene = next(iter(genes))
        hpa_url = 'https://www.proteinatlas.org/api/search_download.php?' + urlencode({
            'search': gene, 'format': 'json', 'columns': 'g,eg,up,rnats,rnatd,rnatsm,prts,prtd,prtsm', 'compress': 'no'})
        hpa, hpa_sha = sources.read(hpa_url)
        for item in hpa if isinstance(hpa, list) else []:
            uniprot = item.get('Uniprot', [])
            uniprot = uniprot.split(';') if isinstance(uniprot, str) else uniprot
            if item.get('Ensembl') == gene and accession in uniprot:
                expression.append({'record': item, 'source_url': hpa_url, 'source_sha256': hpa_sha,
                    'species': 'Human', 'date': None, 'historical_qualification': 'Modern general annotation; availability at historical cutoff unestablished'})
        if not expression: missing.append('No exact gene/protein-matched HPA expression record in this projected response.')
    else:
        missing.append('No unique Human Ensembl identity for HPA lookup.')
    return {'target_accession': accession, 'taxonomy_id': annotation.get('organism', {}).get('taxonId'),
        'go': go, 'expression': expression, 'missing': missing, 'source_url': url, 'source_sha256': sha,
        'classification': 'general_target_relevance_not_candidate_effect',
        'limitation': 'Expression/function annotations do not establish target perturbation, effect direction or clinical consequences. Tissue-specific summaries are not a complete absence/presence atlas.'}


def nominate_targets(smiles, intended_accession, policy, *, sources=None):
    from rdkit import Chem
    sources = sources or ScientificSources()
    molecule = Chem.MolFromSmiles(smiles)
    if molecule is None: raise ValueError('Invalid candidate graph.')
    key = Chem.MolToInchiKey(molecule)
    hypotheses, excluded, datums, failures = [], [], [], []
    try:
        doc, _, _ = sources.chembl('similarity/' + quote(smiles, safe='') + '/80',
            ['molecule_chembl_id', 'molecule_structures', 'similarity'], limit=24)
        excluded.append({'reason': 'search_bound', 'available': doc.get('page_meta', {}).get('total_count'), 'reviewed_maximum': 24})
        neighbours = []
        for row in doc.get('molecules', [])[:24]:
            graph = row.get('molecule_structures') or {}
            other = Chem.MolFromSmiles(graph.get('canonical_smiles') or '')
            if other is None or Chem.MolToInchiKey(other) != graph.get('standard_inchi_key'):
                excluded.append({'record': row.get('molecule_chembl_id'), 'reason': 'Neighbour graph identity mismatch'})
                continue
            if len(Chem.GetMolFrags(other)) != 1:
                excluded.append({'record': row.get('molecule_chembl_id'), 'reason': 'Multi-component/salt record is not an unrelated single-graph ligand'})
                continue
            other_key = Chem.MolToInchiKey(other)
            # Exclude candidate connectivity, including same-parent salt/stereo records.
            if other_key.split('-')[0] == key.split('-')[0]:
                excluded.append({'record': row['molecule_chembl_id'], 'reason': 'Candidate self/parent-connectivity record excluded before activity retrieval'})
                continue
            similarity = chemical_similarity(smiles, graph['canonical_smiles'])
            if similarity < POLICY['local_morgan_minimum']: continue
            neighbours.append((similarity, row['molecule_chembl_id'], graph['canonical_smiles'], other_key))
        neighbours.sort(key=lambda n: (-n[0], n[1]))
        excluded.extend({'record': n[1], 'reason': 'Neighbour retrieval budget'} for n in neighbours[6:])
        for similarity, chembl_id, neighbour_smiles, neighbour_key in neighbours[:6]:
            if not re.fullmatch(r'CHEMBL\d+', chembl_id): raise ValueError('Invalid ChEMBL chemical identifier.')
            activities, source_sha, url = sources.chembl('activity',
                ['activity_id', 'assay_chembl_id', 'document_chembl_id', 'target_chembl_id', 'standard_type', 'standard_relation', 'standard_value', 'standard_units', 'data_validity_comment', 'potential_duplicate'],
                molecule_chembl_id=chembl_id, standard_type__in='Ki,Kd,IC50,EC50', standard_relation='=', limit=32)
            excluded.append({'record': chembl_id, 'reason': 'activity_bound', 'available': activities.get('page_meta', {}).get('total_count'), 'reviewed_maximum': 32})
            for index, row in enumerate(activities.get('activities', [])[:32]):
                try:
                    quantitative = normalize_activity(row)
                    assay_id, target_id, document_id = (row[k] for k in ('assay_chembl_id', 'target_chembl_id', 'document_chembl_id'))
                    if not all(re.fullmatch(r'CHEMBL\d+', v or '') for v in (assay_id, target_id, document_id)):
                        raise ValueError('Missing/invalid target, assay or document identity.')
                    assay, _, _ = sources.chembl('assay/' + assay_id, ['assay_chembl_id', 'assay_organism', 'confidence_score', 'assay_type', 'relationship_type'])
                    if assay.get('confidence_score') != 9 or assay.get('relationship_type') != 'D':
                        raise ValueError('Not a direct, high-confidence single-protein assay.')
                    target, _, _ = sources.chembl('target/' + target_id, ['target_chembl_id', 'target_type', 'organism', 'target_components'])
                    components = target.get('target_components', [])
                    if target.get('target_type') != 'SINGLE PROTEIN' or len(components) != 1:
                        raise ValueError('Target is not one molecularly identified protein.')
                    accession = components[0].get('accession')
                    if not re.fullmatch(r'[A-Z0-9]{6,10}', accession or ''):
                        raise ValueError('Target lacks an exact protein accession.')
                    if assay.get('assay_organism') != target.get('organism'):
                        raise ValueError('Assay and target species differ or are unresolved.')
                    publication, _, _ = sources.chembl('document/' + document_id, ['document_chembl_id', 'year', 'pubmed_id', 'doi'])
                    year = publication.get('year')
                    when = date(year, 12, 31) if isinstance(year, int) and 1900 <= year <= 2100 else None
                    datum = EvidenceDatum(identifier='chembl-activity-' + str(row['activity_id']),
                        source_url=url, source_record=str(row['activity_id']), source_sha256=source_sha,
                        publication_date=when, date_precision='year' if when else 'unknown',
                        category='quantitative_activity', subject_inchikey=neighbour_key, target_accession=accession,
                        value=row['standard_value'], unit=row['standard_units'], evidence_type='measured',
                        value_pointer=('activities', index, 'standard_value'), context={'assay': assay, 'document': publication})
                    datums.append(datum)
                    hypotheses.append({'target_accession': accession, 'target_chembl_id': target_id,
                        'organism': target['organism'], 'intended_target': accession == intended_accession if intended_accession else None,
                        'neighbour_chembl_id': chembl_id, 'neighbour_smiles': neighbour_smiles, 'neighbour_inchikey': neighbour_key,
                        'local_similarity': similarity, 'quantitative_neighbour_evidence': quantitative,
                        'datum_identifier': datum.identifier, 'assay_context': assay,
                        'classification': 'chemical_neighbour_target_hypothesis',
                        'limitation': 'Activity is measured for the unrelated neighbour, not this candidate. Similarity has no calibrated potency or effect-direction interpretation.'})
                except (ValueError, KeyError, TypeError) as error:
                    excluded.append({'activity_id': row.get('activity_id'), 'reason': str(error)})
    except (ValueError, KeyError, TypeError, OSError, RuntimeError) as error:
        failures.append({'reason': str(error), 'stage': 'bioactivity_acquisition'})
    eligibility_report = adjudicate(datums, sources.payloads, policy, key)
    accepted = {d['identifier'] for d in eligibility_report['decisions'] if d['eligible']}
    hypotheses = [h for h in hypotheses if h['datum_identifier'] in accepted]
    ordered = sorted({h['target_accession'] for h in hypotheses},
        key=lambda a: (-max(h['local_similarity'] for h in hypotheses if h['target_accession'] == a), a))
    targets = []
    for accession in ordered[:8]:
        matches = [h for h in hypotheses if h['target_accession'] == accession]
        try:
            relevance = tissue_relevance(accession, sources)
        except (ValueError, KeyError, TypeError, OSError, RuntimeError) as error:
            relevance = {'status': 'unavailable', 'reason': str(error)}
        targets.append({'target_accession': accession, 'evidence': matches, 'tissue_relevance': relevance,
            'candidate_quantitative_activity': None, 'status': 'hypothesis_requires_candidate_assay'})
    return {'schema': 'pulsate.bioactivity-target-hypotheses/v1', 'candidate_inchikey': key,
        'policy': POLICY, 'targets': targets, 'excluded': excluded, 'failures': failures,
        'unselected_targets': ordered[8:], 'eligibility': eligibility_report, 'sources': sources.audit,
        'limitation': POLICY['scope']}, sources


def verify_hypotheses(report, smiles, intended_accession, policy, sources):
    """Full extraction/identity/similarity/eligibility replay using preserved bytes."""
    by_url = {item['url']: item if item.get('error') else sources[item['sha256']] for item in report['sources']}
    expected, _ = nominate_targets(smiles, intended_accession, policy, sources=ScientificSources(preserved=by_url))
    if canonical(expected) != canonical(report):
        raise ValueError('Bioactivity hypothesis report failed independent source replay.')
    return {'passed': True, 'report_sha256': digest(canonical(report)),
        'scope': 'Identity, scalar assay extraction, units, local similarity, species and source eligibility; not candidate potency.'}
