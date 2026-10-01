"""Bounded structural binding-space hypotheses and real alternative-target screening.

No LLM nominates targets. Retrieval is limited to chemical graphs, experimental
coordinates, sequence identity/organism and controlled GO annotations. This is
not a clinical safety model or an exhaustive proteome screen.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
from types import SimpleNamespace
from urllib.parse import quote, urlsplit

from .phase8_scientific_handlers import (
    _DiscoveryArtifactBridge, _NativeRunner, DiscoveryProteinPreparationHandler,
)
from .scientific_acquisition import _fetch_bytes
from .scientific_runtime import ScientificCapabilityFailure, ScientificCapabilityOutcome
from .scientific_target_selection import inspect_receptor


def chemical_similarity(query_smiles, reference_smiles):
    """Recompute Morgan similarity locally; never reuse search relevance as similarity."""
    from rdkit import Chem, DataStructs
    from rdkit.Chem import rdFingerprintGenerator
    molecules = [Chem.MolFromSmiles(s) for s in (query_smiles, reference_smiles)]
    if any(m is None for m in molecules):
        raise ValueError('A similarity input has no valid chemical graph.')
    generator = rdFingerprintGenerator.GetMorganGenerator(radius=2, fpSize=2048, includeChirality=True)
    return float(DataStructs.TanimotoSimilarity(*(generator.GetFingerprint(m) for m in molecules)))


def retain_panel(hypotheses, intended_accession, *, maximum_targets=3):
    """Choose distinct targets deterministically; retain every exclusion and provenance."""
    if type(maximum_targets) is not int or not 0 <= maximum_targets <= 3:
        raise ValueError('Alternative-target bound must be between zero and three.')
    ordered = sorted(hypotheses, key=lambda h: (-h['similarity'], h['resolution_angstrom'], h['uniprot_accession'], h['pdb_id'], h['chain']))
    selected, excluded, seen = [], [], set()
    for h in ordered:
        if not math.isfinite(h['similarity']) or not 0 <= h['similarity'] <= 1:
            raise ValueError('Invalid local similarity evidence.')
        reason = ('intended_target_not_off_target' if h['uniprot_accession'] == intended_accession else
                  'duplicate_target_structural_evidence' if h['uniprot_accession'] in seen else
                  'bounded_panel_overflow' if len(selected) >= maximum_targets else None)
        if reason:
            excluded.append(dict(h, exclusion_reason=reason))
        else:
            selected.append(h)
            seen.add(h['uniprot_accession'])
    return selected, excluded


def functional_hypotheses(annotation, *, interaction_supported):
    """Controlled terms describe the protein, not an effect of the candidate."""
    if not interaction_supported:
        return []
    result = []
    for cross in annotation.get('uniProtKBCrossReferences', []):
        if cross.get('database') != 'GO' or not re.fullmatch(r'GO:\d{7}', cross.get('id', '')):
            continue
        props = {p['key']: p['value'] for p in cross.get('properties', [])}
        term = props.get('GoTerm', '')
        if not term.startswith(('P:', 'F:')):
            continue
        result.append({'go_identifier': cross['id'], 'annotation': term, 'annotation_evidence': props.get('GoEvidenceType'),
            'classification': 'functional_consequence_hypothesis',
            'hypothesis': 'If experimental target perturbation is confirmed, test consequences relevant to this sourced protein annotation.',
            'limitation': 'Direction, inhibition, tissue exposure and physiological or toxic effects are not established by docking or this annotation.'})
    return result


def compare_screening(intended_score, alternative):
    """Raw cross-receptor differences are exploratory, never selectivity ratios."""
    if type(intended_score) not in (int, float) or not math.isfinite(intended_score):
        raise ValueError('Invalid intended-target score.')
    scores = alternative.get('vina_scores_kcal_per_mol', [])
    if (alternative.get('score_semantics') != 'autodock_vina_score_not_binding_free_energy'
            or not scores or not all(type(s) in (int, float) and math.isfinite(s) for s in scores)):
        raise ValueError('Invalid alternative-target screening evidence.')
    delta = scores[0] - intended_score
    return {'intended_score_kcal_per_mol': intended_score, 'alternative_score_kcal_per_mol': scores[0],
        'raw_score_difference_kcal_per_mol': delta, 'classification': 'computational_interaction_hypothesis',
        'follow_up_warranted': delta <= 0,
        'limitation': 'Different receptors/boxes are not calibrated against each other. This raw Vina difference is not affinity, a selectivity ratio, statistical significance or proof of off-target activity/toxicity.'}


class _FoundationalSources:
    def __init__(self, runner, invocation, fetch):
        self.runner, self.invocation, self.fetch = runner, invocation, fetch
        self.references, self.audit, self.cache = [], [], {}

    def read(self, url, source_class, *, json_result=True):
        if url in self.cache:
            return self.cache[url]
        parsed = urlsplit(url)
        allowed = {'search.rcsb.org': '/rcsbsearch/v2/query', 'data.rcsb.org': '/graphql',
                   'files.rcsb.org': '/download/', 'rest.uniprot.org': '/uniprotkb/search'}
        if (parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port
                or parsed.hostname not in allowed or not parsed.path.startswith(allowed[parsed.hostname])):
            raise ValueError('Off-target source is outside the foundational retrieval allowlist.')
        payload = self.fetch(url, 'application/json' if json_result else 'chemical/x-pdb',
            8 * 1024 * 1024, **({'allow_no_content': True} if 'search.rcsb.org' in url else {}))
        value = json.loads(payload) if payload and json_result else ({} if json_result else payload)
        ref = self.runner.write_bytes(artifact_type='prospective_foundational_source',
            media_type='application/json' if json_result else 'chemical/x-pdb', payload=payload,
            producer='discovery.off_target_hypothesize', execution_identifier=self.invocation.invocation_identifier,
            metadata={'source_url': url, 'source_class': source_class})
        self.references.append(ref)
        self.audit.append({'url': url, 'class': source_class, 'sha256': ref.content_sha256, 'artifact_identifier': ref.artifact_identifier})
        self.cache[url] = value
        return value

    def search(self, query, return_type, rows, source_class):
        body = {'query': query, 'return_type': return_type, 'request_options': {'paginate': {'start': 0, 'rows': rows}}}
        return self.read('https://search.rcsb.org/rcsbsearch/v2/query?json=' + quote(json.dumps(body), safe=''), source_class)


def _descriptors(record, store):
    trace_ref = next(r for r in record.artifact_references if r.artifact_type == 'discovery_design_loop_trace')
    trace = json.loads(store.read(trace_ref))
    if not record.verified or not trace.get('loop_complete'):
        raise ScientificCapabilityFailure('off_target_unverified', 'Alternative-target investigation requires verified intended-target screening.')
    refs = {r.artifact_identifier: r for r in record.artifact_references}
    rows = []
    for candidate in trace['candidates']:
        matches = [refs[a['artifact_identifier']] for e in candidate['evidence'] for a in e['artifacts']
                   if refs[a['artifact_identifier']].artifact_type == 'molecular_candidate_evaluation']
        if len(matches) != 1:
            raise ValueError('Each candidate requires exactly one descriptor graph.')
        descriptor = json.loads(store.read(matches[0]))
        if descriptor['candidate_identifier'] != candidate['candidate_identifier']:
            raise ValueError('Candidate graph identity mismatch.')
        rows.append((candidate, descriptor, matches[0]))
    return trace_ref, rows


class OffTargetHypothesisHandler:
    def __init__(self, store, *, fetch=_fetch_bytes):
        self.runner, self.fetch = _NativeRunner(store), fetch

    def execute(self, *, invocation, objective, record):
        from rdkit import Chem
        store = self.runner.store
        trace_ref, rows = _descriptors(record, store)
        pocket_ref = next(r for r in record.artifact_references if r.artifact_type == 'binding_pocket')
        selection = json.loads(store.read(pocket_ref)).get('target_selection_evidence') or {}
        intended = selection.get('uniprot_accession')
        organism = selection.get('organism', {}).get('taxonId')
        sources = _FoundationalSources(self.runner, invocation, self.fetch)
        candidates, failures = [], []
        start = time.monotonic()
        # Uniform per-candidate budget. Never select a panel for only the first candidate.
        bound = min(3, 12 // len(rows)) if rows else 0
        for candidate, descriptor, _ in rows:
            hypotheses, excluded = [], []
            identifier = candidate['candidate_identifier']
            def read_available(url, source_class, *, json_result=True):
                try:
                    return sources.read(url, source_class, json_result=json_result)
                except (ValueError, OSError, RuntimeError) as error:
                    excluded.append({'url': url, 'reason': str(error), 'status': 'source_unavailable'})
                    return None
            try:
                if not intended or not organism:
                    raise ValueError('A sourced intended-target accession and organism are required to distinguish alternative targets.')
                if bound == 0:
                    raise ValueError('Candidate count exceeds the 12-pair alternative-screening budget; no candidate was silently prioritized over another.')
                if time.monotonic() - start > min(180, objective.budget.maximum_wall_time_seconds / 3):
                    raise ValueError('Bounded foundational retrieval time budget reached.')
                smiles = descriptor['canonical_smiles']
                query = {'type': 'terminal', 'service': 'chemical', 'parameters': {
                    'value': smiles, 'type': 'descriptor', 'descriptor_type': 'SMILES', 'match_type': 'fingerprint-similarity'}}
                result = sources.search(query, 'mol_definition', 8, 'chemical graph similarity search (not target activity)')
                excluded.append({'reason': 'component_search_bound', 'available': result.get('total_count', 0), 'review_limit': 8})
                entry_budget = 16
                for hit in result.get('result_set', [])[:8]:
                    code = hit['identifier']
                    if not re.fullmatch(r'[A-Z0-9]{1,5}', code):
                        excluded.append({'component': code, 'reason': 'unsupported component identifier'})
                        continue
                    # GraphQL selects descriptors only: no compound narrative or DrugBank links.
                    graph_query = '{chem_comp(comp_id:"' + code + '"){rcsb_chem_comp_descriptor{SMILES SMILES_stereo InChIKey}}}'
                    chemical = read_available('https://data.rcsb.org/graphql?query=' + quote(graph_query, safe=''), 'deposited chemical graph only')
                    if chemical is None:
                        continue
                    graph = ((chemical.get('data') or {}).get('chem_comp') or {}).get('rcsb_chem_comp_descriptor') or {}
                    reference_smiles = graph.get('SMILES_stereo') or graph.get('SMILES')
                    similarity = chemical_similarity(smiles, reference_smiles or '')
                    if similarity < .7:
                        excluded.append({'component': code, 'similarity': similarity, 'reason': 'below local Morgan threshold 0.7'})
                        continue
                    mol = Chem.MolFromSmiles(reference_smiles)
                    if graph.get('InChIKey') != Chem.MolToInchiKey(mol):
                        excluded.append({'component': code, 'reason': 'source graph/identity mismatch'})
                        continue
                    entry_query = {'type': 'group', 'logical_operator': 'and', 'nodes': [
                        {'type': 'terminal', 'service': 'text_chem', 'parameters': {'attribute': 'rcsb_chem_comp_container_identifiers.comp_id', 'operator': 'in', 'value': [code]}},
                        {'type': 'terminal', 'service': 'text', 'parameters': {'attribute': 'rcsb_entry_info.resolution_combined', 'operator': 'less_or_equal', 'value': 3.0}}]}
                    entries = sources.search(entry_query, 'entry', max(1, entry_budget), 'component-containing experimental structural records')
                    excluded.append({'component': code, 'available_entries': entries.get('total_count', 0), 'review_limit': entry_budget, 'reason': 'entry_search_bound'})
                    for hit_entry in entries.get('result_set', [])[:entry_budget]:
                        if time.monotonic() - start > min(180, objective.budget.maximum_wall_time_seconds / 3):
                            raise ValueError('Bounded foundational retrieval time budget reached.')
                        entry_budget -= 1
                        pdb_id = hit_entry['identifier']
                        if not re.fullmatch(r'[A-Za-z0-9]{4}', pdb_id):
                            excluded.append({'pdb_id': pdb_id, 'reason': 'unsupported legacy-coordinate identifier'})
                            continue
                        url = 'https://files.rcsb.org/download/' + pdb_id + '.pdb'
                        payload = read_available(url, 'experimental structural coordinates', json_result=False)
                        if payload is None:
                            continue
                        lines = payload.decode().splitlines()
                        resolution_match = re.search(r'REMARK   2 RESOLUTION\.\s+(\d+\.\d+) ANGSTROMS', payload.decode())
                        if not resolution_match or not any('X-RAY' in l for l in lines if l.startswith('EXPDTA')):
                            excluded.append({'pdb_id': pdb_id, 'reason': 'not a supported experimental X-ray model'})
                            continue
                        resolution = float(resolution_match[1])
                        if resolution > 3:
                            continue
                        for dbref in (l for l in lines if l.startswith('DBREF ') and l[26:32].strip() == 'UNP'):
                            accession, chain = dbref[33:41].strip(), dbref[12:13]
                            if accession == intended:
                                continue
                            try:
                                if not re.fullmatch(r'[A-Z0-9]{6,10}', accession):
                                    raise ValueError('Invalid deposited UniProt accession.')
                                metadata = {'pdb_id': pdb_id, 'chain': chain, 'chains': [chain], 'uniprot_accession': accession,
                                    'domain_start': int(dbref[55:60]), 'domain_end': int(dbref[62:67]),
                                    'site_components': {code: {'chemical_component_id': code, 'inchikey': graph['InChIKey'],
                                        'match_policy': 'source graph identity; candidate locally computed chemical similarity'}},
                                    'mutation_policy': 'strict_unmutated', 'similarity': similarity,
                                    'reference_smiles': reference_smiles, 'component': code, 'resolution_angstrom': resolution,
                                    'structure_source_url': url}
                                site = inspect_receptor(SimpleNamespace(payload=payload), metadata)
                                # Unambiguous UniProt identity and controlled annotations, no comments/publications.
                                annotation_url = 'https://rest.uniprot.org/uniprotkb/search?query=' + quote('accession:' + accession, safe='') + '&format=json&size=2&fields=accession,organism_name,protein_name,go'
                                annotation = sources.read(annotation_url, 'protein identity/organism and controlled GO terms')
                                records = annotation.get('results', [])
                                if len(records) != 1 or records[0].get('primaryAccession') != accession or records[0].get('organism', {}).get('taxonId') != organism:
                                    raise ValueError('Alternative target identity/organism does not match scoped panel.')
                                hypotheses.append(dict(metadata, **site, annotation=records[0], annotation_source_url=annotation_url,
                                    classification='structure_supported_target_hypothesis',
                                    limitation='A deposited similar ligand contact is not measured activity of the candidate. Structural archive coverage and similarity bias the panel.'))
                            except (ValueError, KeyError, IndexError) as error:
                                excluded.append({'pdb_id': pdb_id, 'chain': chain, 'uniprot_accession': accession, 'reason': str(error)})
                        if entry_budget <= 0:
                            break
                    if entry_budget <= 0:
                        break
                panel, rejected = retain_panel(hypotheses, intended, maximum_targets=bound)
                excluded.extend(rejected)
            except (ValueError, KeyError, TypeError, RuntimeError, OSError) as error:
                panel, rejected = retain_panel(hypotheses, intended, maximum_targets=bound)
                excluded.extend(rejected)
                failures.append({'candidate_identifier': identifier, 'reason': str(error)})
            candidates.append({'candidate_identifier': identifier, 'intended_accession': intended, 'targets': panel,
                'excluded': excluded, 'status': 'panel_ready' if panel else 'insufficient_structural_evidence'})
        document = {'schema': 'pulsate.prospective-target-panel/v1', 'candidates': candidates, 'failures': failures,
            'source_audit': sources.audit, 'policy': {'components_per_candidate': 8, 'entries_per_candidate': 16,
                'targets_per_candidate': bound, 'maximum_screening_pairs': 12, 'similarity_threshold': .7,
                'similarity_method': 'RDKit chiral Morgan radius 2, 2048 bits; local Tanimoto',
                'target_basis': 'similar ligand heavy-atom contacts in unmutated same-organism X-ray structures <=3A'},
            'limitation': 'Bounded archive-derived hypothesis panel, not proteome-wide selectivity/safety. Missing hits do not demonstrate absence of risk.'}
        parent_refs = (trace_ref, pocket_ref, *(row[2] for row in rows), *sources.references)
        ref = self.runner.write_json(artifact_type='prospective_target_panel', payload=json.dumps(document, sort_keys=True, allow_nan=False).encode(),
            producer='discovery.off_target_hypothesize', execution_identifier=invocation.invocation_identifier, parents=parent_refs)
        return ScientificCapabilityOutcome(output_artifacts=(ref,), evidence_artifacts=(*sources.references, ref),
            scientific_summary='Recorded a bounded, source-derived alternative-target panel and all candidate coverage gaps.')


class OffTargetScreeningHandler:
    def __init__(self, store, adapter):
        self.runner, self.adapter = _NativeRunner(store), adapter

    def execute(self, *, invocation, objective, record):
        from cgr.discovery import DiscoveryCandidate, VinaMolecularDockingEvaluator
        store = self.runner.store
        trace_ref, descriptors = _descriptors(record, store)
        refs = {r.artifact_identifier: r for r in record.artifact_references}
        panel_ref = next(r for r in refs.values() if r.artifact_type == 'prospective_target_panel')
        panel = json.loads(store.read(panel_ref))
        if sorted(r['candidate_identifier'] for r in panel['candidates']) != sorted(c['candidate_identifier'] for c, _, _ in descriptors):
            raise ScientificCapabilityFailure('off_target_coverage_invalid', 'Target panel must account for every candidate.')
        evidence, results = [], []
        deadline = time.monotonic() + min(300, objective.budget.maximum_wall_time_seconds / 2)
        for candidate, descriptor, descriptor_ref in descriptors:
            targets = next(c['targets'] for c in panel['candidates'] if c['candidate_identifier'] == candidate['candidate_identifier'])
            screened = []
            intended_score = next(s['value'] for s in candidate['properties_and_calculations'] if s['objective_identifier'] == 'vina_pose_score')
            for target in targets:
                try:
                    if time.monotonic() > deadline:
                        raise ValueError('Alternative-target screening time budget reached.')
                    source = next(s for s in panel['source_audit'] if s['url'] == target['structure_source_url'])
                    reference = refs[source['artifact_identifier']]
                    payload = store.read(reference)
                    if hashlib.sha256(payload).hexdigest() != source['sha256']:
                        raise ValueError('Alternative receptor source hash mismatch.')
                    lines = payload.decode().splitlines()
                    site = [l for l in lines if l.startswith('HETATM') and l[17:27] == target['reference_residue'] and l[76:78].strip() not in ('H', 'D')]
                    xyz = [tuple(float(l[a:b]) for a, b in ((30, 38), (38, 46), (46, 54))) for l in site]
                    center = tuple(sum(v[i] for v in xyz) / len(xyz) for i in range(3))
                    size = tuple(max(12., max(v[i] for v in xyz) - min(v[i] for v in xyz) + 12.) for i in range(3))
                    # Never silently discard an experimentally deposited metal cofactor.
                    if any(l.startswith('HETATM') and l[76:78].strip().upper() in ('ZN', 'FE', 'CU', 'MN', 'CO', 'NI', 'MG', 'CA') for l in lines):
                        raise ValueError('Protein-only alternative docking cannot discard a deposited metal cofactor.')
                    chain = [l for l in lines if l.startswith('ATOM  ') and l[21:22] == target['chain']]
                    if any(l[16:17].strip() for l in chain):
                        raise ValueError('Alternative receptor requires a reviewed alternate-conformation choice.')
                    structure = self.runner.write_bytes(artifact_type='molecular_structure', media_type='chemical/x-pdb',
                        payload=('\n'.join(chain) + '\nTER\nEND\n').encode(), producer='discovery.off_target_screen',
                        execution_identifier=invocation.invocation_identifier, parents=(reference, panel_ref))
                    preparation = DiscoveryProteinPreparationHandler(store).execute(invocation=invocation, objective=objective,
                        record=SimpleNamespace(artifact_references=(structure,)))
                    evidence.extend((structure, *preparation.output_artifacts))
                    prepared = next(r for r in preparation.output_artifacts if r.artifact_type == 'prepared_molecular_structure')
                    meeko_input = self.runner.write_bytes(artifact_type='molecular_structure', media_type='chemical/x-pdb',
                        payload=store.read(prepared), producer='discovery.off_target_receptor_input',
                        execution_identifier=invocation.invocation_identifier, parents=(prepared,))
                    evidence.append(meeko_input)
                    receptor, = self.runner.invoke(self.adapter, 'molecular.docking_receptor_prepare', inputs=(meeko_input,),
                        parameters={**{f'center_{axis}_angstrom': center[i] for i, axis in enumerate('xyz')},
                                    **{f'size_{axis}_angstrom': size[i] for i, axis in enumerate('xyz')}},
                        execution_identifier=invocation.invocation_identifier + '-' + target['uniprot_accession'], objective=objective)
                    evidence.append(receptor)
                    bridge = _DiscoveryArtifactBridge(store, producer='discovery.off_target_screen',
                        execution_identifier=invocation.invocation_identifier + '-' + target['uniprot_accession'],
                        parents=(receptor, descriptor_ref, panel_ref))
                    receptor_pointer = bridge.register(receptor)
                    representation = bridge.put('molecular_candidate_smiles', json.dumps({'canonical_smiles': descriptor['canonical_smiles']}).encode())
                    molecule = DiscoveryCandidate(candidate_identifier=candidate['candidate_identifier'], candidate_type='small_molecule',
                        generation=0, generated_by='prospective_off_target_screen', representation_artifacts=(representation,))
                    evaluator = VinaMolecularDockingEvaluator(bridge, receptor_pdbqt=receptor_pointer, objective_identifier='vina_pose_score',
                        box_center_angstrom=center, box_size_angstrom=size, exhaustiveness=1, pose_count=3, random_seed=20260808)
                    calculation = evaluator.evaluate(molecule)
                    artifacts = [bridge.references[(a.artifact_identifier, a.content_sha256)] for e in calculation.evidence_records for a in e.artifacts]
                    evidence.extend((*bridge.references.values(), *artifacts))
                    docking_ref = next(r for r in artifacts if r.artifact_type == 'molecular_candidate_docking_evaluation')
                    docking = json.loads(store.read(docking_ref))
                    if docking['candidate_identifier'] != candidate['candidate_identifier'] or docking['receptor_sha256'] != receptor.content_sha256:
                        raise ValueError('Alternative-target docking identity/hash mismatch.')
                    comparison = compare_screening(intended_score, docking)
                    if not docking['preparation_optimization_converged']:
                        raise ValueError('Alternative ligand preparation did not converge.')
                    screened.append({'target': target, 'status': 'computed', 'docking': docking, 'comparison': comparison,
                        'functional_hypotheses': functional_hypotheses(target['annotation'], interaction_supported=True),
                        'evidence_artifact_identifiers': [r.artifact_identifier for r in (receptor, docking_ref, descriptor_ref)],
                        'limitation': 'Single seed, rigid receptor, low-exhaustiveness exploratory screen. GO annotations describe the protein; no functional perturbation, tissue exposure or toxicity was computed.'})
                except (ScientificCapabilityFailure, ValueError, KeyError, ImportError, OSError) as error:
                    screened.append({'target': target, 'status': 'unsupported', 'reason': str(error)})
            results.append({'candidate_identifier': candidate['candidate_identifier'], 'targets': screened,
                'panel_exclusions': next(c['excluded'] for c in panel['candidates'] if c['candidate_identifier'] == candidate['candidate_identifier']),
                'retrieval_failures': [f for f in panel['failures'] if f['candidate_identifier'] == candidate['candidate_identifier']],
                'status': 'computed' if any(t['status'] == 'computed' for t in screened) else 'insufficient_evidence'})
        document = {'schema': 'pulsate.prospective-off-target-screen/v1', 'candidates': results, 'source_audit': panel['source_audit'],
            'policy': panel['policy'], 'limitations': [panel['limitation'], 'No absence-of-risk inference from missing or unsuccessful screens.',
                'Raw cross-target docking scores are not calibrated selectivity or binding free energies.',
                'No validated toxicity model, ADME measurements, tissue expression inference, MD or quantum calculation was invoked.'],
            'verification': {'passed': True, 'scope': 'Successful native outputs have candidate/receptor hash identity, finite scores and converged ligand preparation; unsuccessful targets remain explicitly unsupported.'}}
        parents = tuple({r.artifact_identifier: r for r in (panel_ref, trace_ref, *evidence)}.values())
        report = self.runner.write_json(artifact_type='prospective_off_target_screen', payload=json.dumps(document, sort_keys=True, allow_nan=False).encode(),
            producer='discovery.off_target_screen', execution_identifier=invocation.invocation_identifier, parents=parents)
        return ScientificCapabilityOutcome(output_artifacts=(report,), evidence_artifacts=(*parents, report),
            scientific_summary='Completed available alternative-target docking with explicit candidate/target coverage and uncalibrated comparison limits.')


class OffTargetVerificationHandler:
    """Independent re-read of persisted native receipts; no scientific safety certification."""
    def __init__(self, store):
        self.runner = _NativeRunner(store)

    def execute(self, *, invocation, objective, record):
        del objective
        store = self.runner.store
        refs = {r.artifact_identifier: r for r in record.artifact_references}
        report_ref = next(r for r in refs.values() if r.artifact_type == 'prospective_off_target_screen')
        report = json.loads(store.read(report_ref))
        panel_ref = next(r for r in refs.values() if r.artifact_type == 'prospective_target_panel')
        panel = json.loads(store.read(panel_ref))
        _, descriptor_rows = _descriptors(record, store)
        intended_scores = {c['candidate_identifier']: next(s['value'] for s in c['properties_and_calculations']
            if s['objective_identifier'] == 'vina_pose_score') for c, _, _ in descriptor_rows}
        for ref in (report_ref, panel_ref):
            if hashlib.sha256(store.read(ref)).hexdigest() != ref.content_sha256:
                raise ScientificCapabilityFailure('off_target_verification_failed', 'Panel or screening report hash mismatch.')
        if sorted(c['candidate_identifier'] for c in report['candidates']) != sorted(c['candidate_identifier'] for c in panel['candidates']):
            raise ScientificCapabilityFailure('off_target_verification_failed', 'Screening dropped or duplicated a candidate.')
        checked = []
        for candidate in report['candidates']:
            nominated = next(c['targets'] for c in panel['candidates'] if c['candidate_identifier'] == candidate['candidate_identifier'])
            if sorted(t['target']['uniprot_accession'] for t in candidate['targets']) != sorted(t['uniprot_accession'] for t in nominated):
                raise ScientificCapabilityFailure('off_target_verification_failed', 'Screening dropped or invented an alternative target.')
            for target in candidate['targets']:
                if target['target'] not in nominated:
                    raise ScientificCapabilityFailure('off_target_verification_failed', 'Alternative target annotation/site evidence differs from its nomination.')
                if target['status'] != 'computed':
                    if not target.get('reason'):
                        raise ScientificCapabilityFailure('off_target_verification_failed', 'Unsupported targets need a recorded reason.')
                    continue
                evidence = [refs[identifier] for identifier in target['evidence_artifact_identifiers']]
                receptor = next(r for r in evidence if r.artifact_type == 'docking_receptor_pdbqt')
                native_ref = next(r for r in evidence if r.artifact_type == 'molecular_candidate_docking_evaluation')
                for ref in evidence:
                    if hashlib.sha256(store.read(ref)).hexdigest() != ref.content_sha256:
                        raise ScientificCapabilityFailure('off_target_verification_failed', 'Alternative-target evidence hash mismatch.')
                native = json.loads(store.read(native_ref))
                if (native != target['docking'] or native['candidate_identifier'] != candidate['candidate_identifier']
                        or native['receptor_sha256'] != receptor.content_sha256 or native['preparation_optimization_converged'] is not True
                        or compare_screening(intended_scores[candidate['candidate_identifier']], native) != target['comparison']):
                    raise ScientificCapabilityFailure('off_target_verification_failed', 'Alternative-target native evidence/comparison mismatch.')
                if functional_hypotheses(target['target']['annotation'], interaction_supported=True) != target['functional_hypotheses']:
                    raise ScientificCapabilityFailure('off_target_verification_failed', 'Functional hypotheses must match sourced controlled annotations.')
                checked.append({'candidate_identifier': candidate['candidate_identifier'], 'uniprot_accession': target['target']['uniprot_accession'],
                    'native_artifact_sha256': native_ref.content_sha256})
        verification = {'schema': 'pulsate.prospective-off-target-verification/v1', 'passed': True,
            'verified_pairs': checked, 'screening_report_sha256': report_ref.content_sha256,
            'scope': 'Independent persisted receipt identity, hash, coverage and comparison checks only. Not physiological activity, selectivity or safety verification.'}
        ref = self.runner.write_json(artifact_type='prospective_off_target_verification', payload=json.dumps(verification, sort_keys=True).encode(),
            producer='discovery.off_target_verify', execution_identifier=invocation.invocation_identifier, parents=(report_ref, panel_ref))
        return ScientificCapabilityOutcome(output_artifacts=(ref,), evidence_artifacts=(ref,), verified=True,
            scientific_summary='Independently checked persisted alternative-target numerical receipts and complete coverage.', limitations=(verification['scope'],))
