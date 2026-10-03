"""Verified chemistry-neighbour activity -> conservative existing docking panel.

A structure requires exact target/species identity and an independently measured
active reference ligand. Prefer a verified chemistry neighbour; otherwise an
independent, exact-identity co-crystal reference may define the observed pocket.
Neither a target name nor an arbitrary organic component qualifies a pocket.
"""
from __future__ import annotations

import json
import re
from types import SimpleNamespace
from urllib.parse import quote, urlencode, urlsplit, parse_qs

from .scientific_acquisition import _fetch_bytes
from .scientific_off_targets import _FoundationalSources, _NativeRunner, chemical_similarity
from .scientific_target_selection import inspect_receptor
from .virtual_organism import canonical, digest
from .bioactivity_hypotheses import CHEMBL, normalize_activity
from .prospective_evidence import allowed_source


class _PocketSources(_FoundationalSources):
    """Add only projected, unrelated reference-assay data, never clinical text."""
    def read(self, url, source_class, *, json_result=True):
        if not url.startswith(CHEMBL):
            return super().read(url, source_class, json_result=json_result)
        if url in self.cache:
            return self.cache[url]
        parsed = urlsplit(url)
        fields = set(parse_qs(parsed.query).get('only', [''])[0].split(','))
        allowed_fields = {
            'molecule': {'molecule_chembl_id', 'molecule_structures'},
            'activity': {'activity_id', 'assay_chembl_id', 'document_chembl_id', 'target_chembl_id',
                'molecule_chembl_id', 'standard_type', 'standard_relation', 'standard_value',
                'standard_units', 'data_validity_comment', 'potential_duplicate'},
            'assay': {'assay_chembl_id', 'assay_organism', 'confidence_score', 'assay_type', 'relationship_type'},
            'target': {'target_chembl_id', 'target_type', 'organism', 'target_components'},
            'document': {'document_chembl_id', 'year', 'pubmed_id', 'doi'},
        }
        endpoint = parsed.path[len('/chembl/api/data/'):].split('/')[0].split('.')[0]
        if (not json_result or not allowed_source(url) or endpoint not in allowed_fields
                or not fields or '' in fields or not fields <= allowed_fields[endpoint]
                or not re.fullmatch(r'/chembl/api/data/(molecule|activity|assay|target|document)(/CHEMBL\d+)?\.json', parsed.path)):
            raise ValueError('Pocket-reference request is outside the projected scientific boundary')
        payload = self.fetch(url, 'application/json', 4 * 1024 * 1024)
        value = json.loads(payload)
        metadata = {'source_class': source_class}
        metadata.update({'source_url': url} if len(url) <= 1024 else {'source_url_sha256': digest(url.encode())})
        reference = self.runner.write_bytes(artifact_type='prospective_foundational_source',
            media_type='application/json', payload=payload, producer='discovery.virtual_investigate',
            execution_identifier=self.invocation.invocation_identifier, metadata=metadata)
        self.references.append(reference)
        self.audit.append({'url': url, 'class': source_class, 'sha256': reference.content_sha256,
            'artifact_identifier': reference.artifact_identifier})
        self.cache[url] = value
        return value


def _chembl(sources, endpoint, fields, **filters):
    return sources.read(CHEMBL + endpoint + '.json?' + urlencode(dict(filters, only=','.join(fields))),
        'Projected independent pocket-reference identity or assay; not candidate activity')


def _reference_activity(sources, key, target_id, accession, organism):
    """An exact nonself reference must have a direct quantitative same-target assay."""
    from rdkit import Chem
    molecules = _chembl(sources, 'molecule', ['molecule_chembl_id', 'molecule_structures'],
        molecule_structures__standard_inchi_key=key, limit=2)
    matches = molecules.get('molecules', [])
    if len(matches) != 1 or molecules.get('page_meta', {}).get('total_count', len(matches)) != 1:
        raise ValueError('Independent pocket reference has no unique exact ChEMBL graph identity')
    identity = matches[0]
    graph = identity.get('molecule_structures') or {}
    molecule = Chem.MolFromSmiles(graph.get('canonical_smiles') or '')
    if molecule is None or Chem.MolToInchiKey(molecule) != key or graph.get('standard_inchi_key') != key:
        raise ValueError('Independent pocket reference graph differs between sources')
    chembl_id = identity.get('molecule_chembl_id')
    if not re.fullmatch(r'CHEMBL\d+', chembl_id or ''):
        raise ValueError('Invalid independent reference identifier')
    target = _chembl(sources, 'target/' + target_id,
        ['target_chembl_id', 'target_type', 'organism', 'target_components'])
    if (target.get('target_chembl_id') != target_id or target.get('target_type') != 'SINGLE PROTEIN'
            or target.get('organism') != organism or len(target.get('target_components', [])) != 1
            or target['target_components'][0].get('accession') != accession):
        raise ValueError('Independent reference target/species differs from nominated target')
    rows = _chembl(sources, 'activity', ['activity_id', 'assay_chembl_id', 'document_chembl_id',
        'target_chembl_id', 'molecule_chembl_id', 'standard_type', 'standard_relation', 'standard_value',
        'standard_units', 'data_validity_comment', 'potential_duplicate'],
        molecule_chembl_id=chembl_id, target_chembl_id=target_id,
        standard_type__in='Ki,Kd,IC50,EC50', standard_relation='=', limit=16)
    exclusions = []
    for row in rows.get('activities', [])[:16]:
        try:
            if row.get('molecule_chembl_id') != chembl_id or row.get('target_chembl_id') != target_id:
                raise ValueError('Reference assay filter returned a different molecule/target')
            quantitative = normalize_activity(row)
            quantitative['classification'] = 'measured_pocket_reference_activity_not_candidate_activity'
            assay_id, document_id = row.get('assay_chembl_id'), row.get('document_chembl_id')
            if not all(re.fullmatch(r'CHEMBL\d+', x or '') for x in (assay_id, document_id)):
                raise ValueError('Missing exact reference assay/document identifier')
            assay = _chembl(sources, 'assay/' + assay_id,
                ['assay_chembl_id', 'assay_organism', 'confidence_score', 'assay_type', 'relationship_type'])
            if (assay.get('assay_chembl_id') != assay_id or assay.get('confidence_score') != 9
                    or assay.get('relationship_type') != 'D' or assay.get('assay_organism') != organism
                    or assay.get('assay_type') not in {'B', 'F'}):
                raise ValueError('Reference assay is not direct, exact-species single-protein evidence')
            publication = _chembl(sources, 'document/' + document_id,
                ['document_chembl_id', 'year', 'pubmed_id', 'doi'])
            if publication.get('document_chembl_id') != document_id:
                raise ValueError('Reference assay publication identity differs')
            return {'reference_chembl_id': chembl_id, 'reference_inchikey': key,
                'reference_smiles': graph['canonical_smiles'], 'activity_record': row,
                'quantitative_reference_evidence': quantitative, 'assay': assay, 'target': target,
                'publication': publication, 'excluded_activities': exclusions,
                'activity_review_bound': 16, 'available_activities': rows.get('page_meta', {}).get('total_count'),
                'classification': 'independently_measured_pocket_reference_not_candidate_potency',
                'historical_scope': 'Unrelated reference data; modern/unknown availability disclosed, not candidate evidence'}
        except (ValueError, KeyError, TypeError) as error:
            exclusions.append({'activity_id': row.get('activity_id'), 'reason': str(error)})
    raise ValueError('No eligible exact-target quantitative activity for the independent pocket reference')


def _independent_sites(sources, target, accession, organism, candidate_key, excluded):
    """Bounded projected co-crystal review when the active neighbours lack deposits."""
    from rdkit import Chem
    target_ids = {e.get('target_chembl_id') for e in target['evidence']}
    if len(target_ids) != 1 or not re.fullmatch(r'CHEMBL\d+', next(iter(target_ids)) or ''):
        raise ValueError('No unique exact ChEMBL target identity for reference-assay replay')
    target_id = next(iter(target_ids))
    body = {'query': {'type': 'group', 'logical_operator': 'and', 'nodes': [
        {'type': 'terminal', 'service': 'text', 'parameters': {'attribute':
            'rcsb_polymer_entity_container_identifiers.reference_sequence_identifiers.database_accession',
            'operator': 'exact_match', 'value': accession}},
        {'type': 'terminal', 'service': 'text', 'parameters': {'attribute':
            'rcsb_entry_info.resolution_combined', 'operator': 'less_or_equal', 'value': 3.0}},
        {'type': 'terminal', 'service': 'text', 'parameters': {'attribute':
            'exptl.method', 'operator': 'exact_match', 'value': 'X-RAY DIFFRACTION'}}]},
        'return_type': 'entry', 'request_options': {'paginate': {'start': 0, 'rows': 16},
            'sort': [{'sort_by': 'rcsb_entry_info.resolution_combined', 'direction': 'asc'}]}}
    result = sources.read('https://search.rcsb.org/rcsbsearch/v2/query?json=' + quote(json.dumps(body), safe=''),
        'Bounded exact-target experimental reference review')
    ids = [h['identifier'] for h in result.get('result_set', [])[:16]]
    if any(not re.fullmatch(r'[A-Za-z0-9]{4}', i) for i in ids):
        raise ValueError('Unsupported independent reference coordinate identifier')
    excluded.append({'target_accession': accession, 'reason': 'Independent reference review bound',
        'available_structures': result.get('total_count', 0), 'metadata_review_limit': 16,
        'independent_reference_activity_limit': 4})
    if not ids:
        return {}
    query = '{entries(entry_ids:' + json.dumps(ids) + '){rcsb_id nonpolymer_entities{nonpolymer_comp{chem_comp{id} rcsb_chem_comp_descriptor{SMILES SMILES_stereo InChIKey}}}}}'
    document = sources.read('https://data.rcsb.org/graphql?query=' + quote(query, safe=''),
        'Projected deposited component graphs only; no structure titles or candidate conclusions')
    if document.get('errors'):
        raise ValueError('Independent reference graph projection failed')
    entries = {e['rcsb_id']: e for e in (document.get('data') or {}).get('entries', []) if e}
    reviewed, sites = {}, {}
    for pdb in ids:
        components = {}
        entities = entries.get(pdb, {}).get('nonpolymer_entities') or []
        # Reject the whole self-containing entry before any activity/coordinate
        # retrieval; another unrelated component cannot launder its candidate pose.
        candidate_parent = candidate_key.split('-')[0]
        keys = [((e.get('nonpolymer_comp') or {}).get('rcsb_chem_comp_descriptor') or {}).get('InChIKey') for e in entities]
        if any(isinstance(k, str) and k.split('-')[0] == candidate_parent for k in keys):
            excluded.append({'target_accession': accession, 'pdb_id': pdb,
                'reason': 'Candidate self/parent entry excluded before activity and coordinates'})
            continue
        for entity in entities:
            component = entity.get('nonpolymer_comp') or {}
            graph = component.get('rcsb_chem_comp_descriptor') or {}
            code = (component.get('chem_comp') or {}).get('id')
            molecule = Chem.MolFromSmiles(graph.get('SMILES_stereo') or graph.get('SMILES') or '')
            key = graph.get('InChIKey')
            if (not re.fullmatch(r'[A-Z0-9]{1,5}', code or '') or molecule is None
                    or len(Chem.GetMolFrags(molecule)) != 1 or molecule.GetNumHeavyAtoms() < 12
                    or not any(a.GetSymbol() == 'C' for a in molecule.GetAtoms())
                    or Chem.MolToInchiKey(molecule) != key):
                continue
            # No candidate/parent activity query, and no self-defined pocket.
            if key.split('-')[0] == candidate_key.split('-')[0]:
                excluded.append({'target_accession': accession, 'pdb_id': pdb, 'component': code,
                    'reason': 'Candidate self/parent reference excluded before activity and coordinates'})
                continue
            if key not in reviewed:
                if len(reviewed) >= 4:
                    excluded.append({'target_accession': accession, 'pdb_id': pdb, 'component': code,
                        'reason': 'Independent reference activity review bound; not evidence of inactivity'})
                    continue
                try:
                    reviewed[key] = _reference_activity(sources, key, target_id, accession, organism)
                except (ValueError, KeyError, TypeError, OSError, RuntimeError) as error:
                    reviewed[key] = None
                    excluded.append({'target_accession': accession, 'reference_inchikey': key,
                        'reason': str(error), 'status': 'reference_refused'})
            if reviewed[key]:
                components[code] = {'chemical_component_id': code, 'inchikey': key,
                    'match_policy': 'Exact deposited/reference graph plus independent direct same-target/species assay',
                    'reference_activity': reviewed[key]}
        if components:
            sites[pdb] = components
    return sites


def compose_panel(record, store, invocation, reports, intended, *, fetch=_fetch_bytes):
    from .scientific_off_targets import _descriptors
    trace, rows=_descriptors(record,store)
    failures=[]
    def audited_fetch(url,*args,**kwargs):
        try: return fetch(url,*args,**kwargs)
        except (ValueError,OSError,RuntimeError) as error:
            failures.append({'url':url,'reason':str(error)})
            raise RuntimeError(str(error)) from error
    sources=_PocketSources(_NativeRunner(store),invocation,audited_fetch)
    candidates=[]
    budget=min(3,12//max(1,len(rows)))
    for candidate,descriptor,_ in rows:
        report=reports[candidate['candidate_identifier']]
        selected=[]; excluded=[]; entries_remaining=16
        for target in report['targets']:
            accession=target['target_accession']
            if accession==intended:
                excluded.append({'target_accession':accession,'reason':'Intended target already evaluated, not an alternative target'})
                continue
            if len(selected)>=budget:
                excluded.append({'target_accession':accession,'reason':'Declared candidate/target execution budget; not an absence-of-effect result'})
                continue
            structures=[]
            try:
                if not re.fullmatch(r'[A-Z0-9]{6,10}',accession): raise ValueError('Invalid exact target accession')
                species={e.get('organism') for e in target['evidence']}
                if len(species)!=1 or None in species: raise ValueError('Unresolved exact assay organism')
                annotation_url='https://rest.uniprot.org/uniprotkb/search?query='+quote('accession:'+accession,safe='')+'&format=json&size=2&fields=accession,organism_name,protein_name,go'
                records=sources.read(annotation_url,'Exact target/species and controlled annotations').get('results',[])
                if len(records)!=1 or records[0].get('primaryAccession')!=accession or records[0].get('organism',{}).get('scientificName')!=next(iter(species)):
                    raise ValueError('Structural target identity/species does not match neighbour assay evidence')
                components={}
                evidence_by_component={}
                # All evidence has already passed independent source replay. Never
                # retrieve candidate self activity or substitute a parent/salt.
                for evidence in sorted(target['evidence'],key=lambda e:(-e['local_similarity'],e['neighbour_inchikey'])):
                    key=evidence['neighbour_inchikey']
                    query={'type':'terminal','service':'text_chem','parameters':{
                        'attribute':'rcsb_chem_comp_descriptor.InChIKey','operator':'exact_match','value':key}}
                    hits=sources.search(query,'mol_definition',16,'Exact full neighbour identity in deposited components')
                    if hits.get('total_count',0)>16: raise ValueError('Exact component identity review exceeds bound')
                    for hit in hits.get('result_set',[]):
                        code=hit['identifier']
                        if not re.fullmatch(r'[A-Z0-9]{1,5}',code): raise ValueError('Invalid deposited component identifier')
                        comp=sources.read('https://data.rcsb.org/rest/v1/core/chemcomp/'+code,'Original deposited component graph identity')
                        if comp.get('rcsb_chem_comp_descriptor',{}).get('InChIKey')!=key:
                            raise ValueError('Component identity differs from the active neighbour')
                        components[code]={'chemical_component_id':code,'inchikey':key,
                            'match_policy':'Exact full active-neighbour InChIKey equality, not candidate activity'}
                        evidence_by_component[code]=evidence
                if entries_remaining<=0: raise ValueError('Declared structure review budget exhausted')
                independent = {}
                if components:
                    query={'type':'group','logical_operator':'and','nodes':[
                        {'type':'terminal','service':'text','parameters':{'attribute':'rcsb_polymer_entity_container_identifiers.reference_sequence_identifiers.database_accession','operator':'exact_match','value':accession}},
                        {'type':'terminal','service':'text_chem','parameters':{'attribute':'rcsb_chem_comp_container_identifiers.comp_id','operator':'in','value':sorted(components)}},
                        {'type':'terminal','service':'text','parameters':{'attribute':'rcsb_entry_info.resolution_combined','operator':'less_or_equal','value':3.0}},
                        {'type':'terminal','service':'text','parameters':{'attribute':'exptl.method','operator':'exact_match','value':'X-RAY DIFFRACTION'}}]}
                    hits=sources.search(query,'entry',entries_remaining,'Target-specific experimental active-neighbour co-crystal structures')
                else:
                    from rdkit import Chem
                    candidate_key = Chem.MolToInchiKey(Chem.MolFromSmiles(descriptor['canonical_smiles']))
                    independent = _independent_sites(sources, target, accession, next(iter(species)), candidate_key, excluded)
                    hits = {'total_count': len(independent), 'result_set': [{'identifier': pdb} for pdb in independent]}
                    if not independent:
                        raise ValueError('Neither an exact active-neighbour deposit nor an independently assay-qualified co-crystal pocket is available within the declared bounds')
                excluded.append({'target_accession':accession,'reason':'Bounded archive review','available':hits.get('total_count',0),'review_limit':entries_remaining})
                for hit in sorted(hits.get('result_set',[]),key=lambda h:h['identifier']):
                    if entries_remaining <= 0:
                        excluded.append({'target_accession':accession,'reason':'Declared coordinate review budget exhausted; not absence of a suitable structure'})
                        break
                    entries_remaining-=1
                    pdb=hit['identifier']
                    if not re.fullmatch(r'[A-Za-z0-9]{4}',pdb):
                        excluded.append({'target_accession':accession,'pdb_id':pdb,'reason':'Unsupported coordinate identifier'})
                        continue
                    url='https://files.rcsb.org/download/'+pdb+'.pdb'
                    payload=sources.read(url,'Original experimental structure',json_result=False)
                    text=payload.decode(); lines=text.splitlines()
                    resolution=re.search(r'REMARK   2 RESOLUTION\.\s+(\d+\.\d+) ANGSTROMS',text)
                    if not resolution or float(resolution[1])>3 or not any('X-RAY' in l for l in lines if l.startswith('EXPDTA')):
                        excluded.append({'target_accession':accession,'pdb_id':pdb,'reason':'Unsupported experimental method/resolution'})
                        continue
                    for line in lines:
                        if not line.startswith('DBREF ') or line[26:32].strip()!='UNP' or line[33:41].strip()!=accession: continue
                        try:
                            meta={'pdb_id':pdb,'chain':line[12:13],'chains':[line[12:13]],'uniprot_accession':accession,
                                'domain_start':int(line[55:60]),'domain_end':int(line[62:67]),'site_components':independent.get(pdb, components),
                                'mutation_policy':'strict_unmutated','resolution_angstrom':float(resolution[1]),'structure_source_url':url}
                            site=inspect_receptor(SimpleNamespace(payload=payload),meta)
                            code = site['reference_residue'][:3].strip()
                            reference = independent.get(pdb, {}).get(code, {}).get('reference_activity')
                            evidence = target['evidence'][0] if reference else evidence_by_component[code]
                            reference_smiles = reference['reference_smiles'] if reference else evidence['neighbour_smiles']
                            similarity=chemical_similarity(descriptor['canonical_smiles'],reference_smiles)
                            if not reference and similarity!=evidence['local_similarity']: raise ValueError('Neighbour similarity changed before structural composition')
                            structures.append(dict(meta,**site,annotation=records[0],annotation_source_url=annotation_url,
                                reference_smiles=reference_smiles,similarity=similarity,
                                component=code,bioactivity_datum_identifier=evidence['datum_identifier'],
                                independent_pocket_reference=reference,
                                classification='bioactivity_nominated_structure_supported_hypothesis',
                                limitation='Neighbour activity nominates the target; a separately identified, assay-qualified deposited reference defines the observed pocket. Candidate potency and function remain unknown.'))
                        except (ValueError,KeyError,IndexError) as error:
                            excluded.append({'target_accession':accession,'pdb_id':pdb,'chain':line[12:13],'reason':str(error)})
                if not structures: raise ValueError('No unmutated exact-target structure with one observed independently active-reference-defined site')
                structures.sort(key=lambda s:(s['resolution_angstrom'],-s['similarity'],s['pdb_id'],s['chain']))
                selected.append(structures[0])
                excluded.extend(dict(s,reason='Other eligible structure; ordered by resolution, neighbour similarity, accession and chain') for s in structures[1:])
            except (ValueError,KeyError,TypeError,OSError,RuntimeError) as error:
                excluded.append({'target_accession':accession,'reason':str(error),'status':'refused'})
        candidates.append({'candidate_identifier':candidate['candidate_identifier'],'intended_accession':intended,
            'targets':selected,'excluded':excluded,'status':'panel_ready' if selected else 'insufficient_structural_evidence'})
    panel={'schema':'pulsate.prospective-target-panel/v1','candidates':candidates,'failures':[],
        'source_audit':sources.audit,'source_failures':failures,'bioactivity_report_sha256':{k:digest(canonical(v)) for k,v in reports.items()},
        'policy':{'targets_per_candidate':budget,'maximum_screening_pairs':12,'entries_per_candidate':16,
            'target_basis':'Exact nominee assay target/species; exact active neighbour or independently measured same-target reference defines the co-crystal pocket; strict unmutated X-ray <=3A',
            'independent_reference_metadata_entries_per_target':16,'independent_reference_activity_queries_per_target':4,
            'independent_reference_activity_rows':16,'self_parent_reference_activity_excluded':True,
            'selection_order':'Resolution then independently computed neighbour similarity then lexical accession/chain',
            'candidate_potency_inferred':False},
        'limitation':'Bounded structurally supported hypotheses, not proteome-wide coverage, potency, selectivity or safety.'}
    return panel,sources.references


def verify_panel(panel, record, store, invocation, reports, intended):
    by_id={r.artifact_identifier:r for r in record.artifact_references}
    preserved={}
    for item in panel['source_audit']:
        payload=store.read(by_id[item['artifact_identifier']])
        if digest(payload)!=item['sha256']: raise ValueError('Bioactivity structural source tampered')
        preserved[item['url']]=payload
    def fetch(url,*args,**kwargs):
        failure=next((f for f in panel.get('source_failures',[]) if f['url']==url),None)
        if failure: raise RuntimeError(failure['reason'])
        if url not in preserved: raise ValueError('Required structural source missing from preserved evidence')
        return preserved[url]
    expected,_=compose_panel(record,store,invocation,reports,intended,fetch=fetch)
    # Replayed source artifacts get new identities; compare original-byte audits.
    for doc in (expected,):
        doc['source_audit']=[{k:v for k,v in s.items() if k!='artifact_identifier'} for s in doc['source_audit']]
    original=dict(panel,source_audit=[{k:v for k,v in s.items() if k!='artifact_identifier'} for s in panel['source_audit']])
    if canonical(expected)!=canonical(original): raise ValueError('Bioactivity structural panel failed source/selection replay')
    return {'passed':True,'panel_sha256':digest(canonical(panel)),
        'scope':'Exact hypothesis, target/species, deposited ligand, mutation/site and deterministic structure-selection replay; not candidate activity.'}
