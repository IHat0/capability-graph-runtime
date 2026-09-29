"""Bounded experimental receptor selection from scientist scope and source evidence.

The model extracts literal scope and matches it to deposited domain annotations.
It never supplies coordinates, accessions, quality metrics or a preferred PDB ID.
"""
from __future__ import annotations

import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from urllib.parse import quote

from .scientific_acquisition import _fetch_bytes, acquire_protein_pdb


def extract_target_scope(turns, provider):
    if provider is None:
        return None
    text = "\n".join(turn.content for turn in turns)
    try:
        messages = [
            {"role": "system", "content": (
                'Extract the protein target scope from the conversation. Return only '
                '{"name":"literal target name", "organism":"literal organism", '
                '"domain":"literal domain or region"}. Each value must be a nonempty EXACT '
                'substring from a scientist turn. Use the latest explicit clarification. '
                'Do not expand abbreviations, infer organism, or invent residue ranges. '
                'Return {} if organism or domain is not supplied.'
            )}, {"role": "user", "content": text},
        ]
        structured = getattr(provider, 'complete_structured', None)
        schema = {'type': 'object', 'properties': {key: {'type': 'string'}
                  for key in ('name', 'organism', 'domain')},
                  'required': ['name', 'organism', 'domain'], 'additionalProperties': False}
        result = json.loads(structured(messages, schema) if structured else provider.complete(messages))
        if set(result) != {"name", "organism", "domain"}:
            return None
        if not all(isinstance(v, str) and 0 < len(v) <= 256 and
                   any(v in turn.content for turn in turns) for v in result.values()):
            return None
        # Preserve a literal ordinal adjacent to a domain phrase even if the
        # extractor omitted it. Conflicting ordinals remain ambiguous.
        pattern = re.compile(re.escape(result['domain']) + r'(?:\s*\d+)?(?!\w)')
        for turn in reversed(turns):
            domain_spans = {m.group() for m in pattern.finditer(turn.content)}
            if domain_spans:
                if len(domain_spans) != 1:
                    return None
                result['domain'] = next(iter(domain_spans))
                break
        # Remove only an independently grounded modifier duplicated in the name.
        for modifier in (result['domain'], result['organism']):
            result['name'] = re.sub(r'(?<!\w)' + re.escape(modifier) + r'(?!\w)', '', result['name']).strip()
        if not result['name'] or not any(result['name'] in turn.content for turn in turns):
            return None
        return result
    except (ValueError, TypeError, RuntimeError):
        return None


def _json_source(url, fetch, evidence):
    if fetch is _fetch_bytes and url.startswith('https://search.rcsb.org/'):
        payload = fetch(url, "application/json", 4 * 1024 * 1024, allow_no_content=True)
    else:
        payload = fetch(url, "application/json", 4 * 1024 * 1024)
    document = json.loads(payload) if payload else {}
    evidence.append({"url": url, "sha256": hashlib.sha256(payload).hexdigest()})
    return document


def _coordinates(line):
    return tuple(float(line[a:b]) for a, b in ((30, 38), (38, 46), (46, 54)))


def _near(a, b, distance=6):
    return sum((x-y)**2 for x, y in zip(a, b)) <= distance**2


def inspect_receptor(source, candidate):
    """Reject uncertain sites, mutations, and unobserved site residues conservatively."""
    lines = source.payload.decode("utf-8").splitlines()
    if sum(line.startswith("MODEL ") for line in lines) > 1:
        raise ValueError("multiple deposited models")
    mutations = [line.strip() for line in lines if line.startswith("SEQADV") and
                 any(word in line[49:].upper() for word in ("MUTATION", "CONFLICT"))]
    if mutations:
        raise ValueError("deposited sequence mutation/conflict: " + "; ".join(mutations))
    chain_results = []
    for chain in candidate["chains"]:
        atoms = [line for line in lines if line.startswith("ATOM  ") and line[21:22] == chain]
        if not atoms:
            continue
        # Map observed author residue numbers through the deposited DBREF, not
        # by assuming PDB and UniProt numbering are interchangeable.
        domain_atoms = []
        for line in lines:
            if not line.startswith("DBREF ") or line[12:13] != chain:
                continue
            try:
                author_start, author_end = int(line[14:18]), int(line[20:24])
                ref_start, ref_end = int(line[55:60]), int(line[62:67])
            except ValueError:
                continue
            if (line[33:41].strip() != candidate.get('uniprot_accession') or
                    author_end-author_start != ref_end-ref_start):
                continue
            domain_atoms.extend(a for a in atoms if candidate['domain_start'] <=
                                int(a[22:26])-author_start+ref_start <= candidate['domain_end'])
        if not domain_atoms:
            continue
        groups = {}
        for line in lines:
            if line.startswith("HETATM") and line[76:78].strip() not in {"H", "D"}:
                groups.setdefault(line[17:27], []).append(line)
        groups = {key: values for key, values in groups.items() if key[:3].strip() in candidate.get('site_components', {}) and len(values) >= 12 and
                  any(v[76:78].strip() == "C" for v in values) and
                  sum(any(_near(_coordinates(v), _coordinates(a), 4.5) for a in domain_atoms)
                      for v in values) >= 5}
        if len(groups) != 1:
            continue
        key, ligand = next(iter(groups.items()))
        missing = [line.strip() for line in lines if line.startswith(("REMARK 465", "REMARK 470"))
                   and re.match(r"\s*[A-Z]{3}\s+" + re.escape(chain) + r"\s+-?\d+", line[15:])]
        near_missing = []
        for line in missing:
            match = re.search(r"\b[A-Z]{3}\s+" + re.escape(chain) + r"\s+(-?\d+)", line[15:])
            if match and any(abs(int(a[22:26])-int(match[1])) <= 1 and
                             any(_near(_coordinates(a), _coordinates(v)) for v in ligand) for a in atoms):
                near_missing.append(line)
        if near_missing:
            continue
        chain_results.append({"chain": chain, "reference_residue": key,
                              "site_identity_evidence": candidate['site_components'][key[:3].strip()],
                              "missing_records": missing, "binding_site_missing_records": near_missing})
    if not chain_results:
        raise ValueError("no unique ligand-defined site with observed neighboring residues in a scoped chain")
    # Equivalent deposited chain copies: most observed atoms, then chain identifier.
    chain_results.sort(key=lambda c: (-sum(l.startswith("ATOM  ") and l[21:22] == c["chain"] for l in lines), c["chain"]))
    result = chain_results[0]
    result["equivalent_chain_policy"] = "most observed atoms, then lexical chain ID; same scoped UniProt cross-reference"
    result["mutation_records"] = mutations
    return result


def select_experimental_target(scope, provider, *, fetch=_fetch_bytes, ligand_names=()):
    sources = []
    query = '(' + scope['name'] + ') AND (organism_name:"' + scope['organism'] + '") AND (reviewed:true)'
    url = 'https://rest.uniprot.org/uniprotkb/search?query=' + quote(query, safe='') + '&format=json&size=25'
    document = _json_source(url, fetch, sources)
    records = document.get('results', [])
    exact = [r for r in records if any(scope['name'].casefold() == n.get('value', '').casefold()
             for gene in r.get('genes', []) for n in [gene.get('geneName', {}), *gene.get('synonyms', [])])]
    if exact:
        records = exact
    if len(records) != 1:
        raise ValueError("The supplied organism and target do not identify one reviewed UniProt record.")
    record = records[0]
    domains = [f for f in record.get('features', []) if f.get('type') == 'Domain' and
               all(f.get('location', {}).get(k, {}).get('modifier') == 'EXACT' for k in ('start', 'end'))]
    selected = json.loads(provider.complete([
        {"role": "system", "content": (
            'Normalize a protein domain name to one supplied UniProt annotation. '
            'Recognize common domain abbreviations and preserve domain ordinal numbers. '
            'Return only JSON with index (zero-based integer), or null when genuinely ambiguous. '
            'Bromo annotations denote bromodomains; BD is a domain-name abbreviation, not a residue ID.'
        )},
        {"role": "user", "content": json.dumps({'requested_domain': scope['domain'],
            'annotations': [d['description'] for d in domains]})},
    ]))
    index = selected.get('index')
    matches = [domains[index]] if type(index) is int and 0 <= index < len(domains) else []
    if len(matches) != 1:
        raise ValueError("The requested domain could not be matched uniquely to a UniProt domain annotation.")
    domain = matches[0]
    requested_ordinals = re.findall(r'\d+', scope['domain'])
    if requested_ordinals and requested_ordinals != re.findall(r'\d+', domain['description']):
        raise ValueError("The matched domain ordinal conflicts with the scientist's scope.")
    start, end = (domain['location'][k]['value'] for k in ('start', 'end'))
    candidates = []
    for cross in record.get('uniProtKBCrossReferences', []):
        if cross.get('database') != 'PDB':
            continue
        properties = {p['key']: p['value'] for p in cross.get('properties', [])}
        if properties.get('Method') != 'X-ray':
            continue
        try:
            resolution = float(properties.get('Resolution', '').split()[0])
        except (ValueError, IndexError):
            continue
        chains = []
        for entry in properties.get('Chains', '').split(','):
            m = re.fullmatch(r'\s*([A-Za-z0-9/]+)=(\d+)-(\d+)\s*', entry)
            if m and int(m[2]) <= start and int(m[3]) >= end:
                chains.extend(m[1].split('/'))
        if chains and 0 < resolution <= 3.0:
            candidates.append({'pdb_id': cross['id'], 'resolution_angstrom': resolution,
                               'domain_start': start, 'domain_end': end,
                               'uniprot_accession': record['primaryAccession'],
                               'chains': chains, 'uniprot_chain_ranges': properties['Chains']})
    from .scientific_site_identity import requested_site_components, entries_with_components
    fetch_json = lambda url: _json_source(url, fetch, sources)
    components = requested_site_components(ligand_names, fetch_json)
    matched_entries = entries_with_components(components, fetch_json)
    candidates = [dict(c, site_components=components) for c in candidates if c['pdb_id'] in matched_entries]
    candidates.sort(key=lambda c: (c['resolution_angstrom'], c['pdb_id']))
    if not candidates:
        raise ValueError("No candidate-matched X-ray cross-reference at 3 Angstrom or better covers the complete annotated domain; specify another binding-site or receptor policy.")
    # Bounded review is disclosed, never presented as an exhaustive archive optimum.
    def review(candidate):
        try:
            source = acquire_protein_pdb(candidate['pdb_id'], fetch=fetch)
            site = inspect_receptor(source, candidate)
            return {**candidate, **site, 'status': 'eligible'}, source
        except (ValueError, OSError, RuntimeError) as error:
            return {**candidate, 'status': 'excluded', 'reason': str(error)}, None
    with ThreadPoolExecutor(max_workers=4) as pool:
        reviewed = list(pool.map(review, candidates[:16]))
    eligible = [(r, s) for r, s in reviewed if s is not None]
    if not eligible:
        raise ValueError("None of the 16 highest-resolution scoped records has a defensible unmutated ligand-defined site; specify another receptor policy.")
    eligible.sort(key=lambda pair: (len(pair[0]['missing_records']), pair[0]['resolution_angstrom'], pair[0]['pdb_id']))
    chosen, source = eligible[0]
    evidence = {'scope': scope, 'uniprot_accession': record['primaryAccession'],
                'organism': record['organism'], 'domain': domain, 'sources': sources,
                'candidate_count': len(candidates), 'reviewed': [r for r, _ in reviewed], 'selected': chosen,
                'policy': 'Reviewed target and explicit organism; full annotated domain coverage; require a deposited site component with a full InChIKey matching a requested candidate; X-ray <=3 A; inspect at most 16 highest-resolution identity-matched records; reject deposited mutation/conflict and ambiguous ligand sites or missing site-neighbor residues; prefer fewer unobserved records then resolution; ID breaks metric ties.',
                'limitations': [
                    'Site selection requires an exact full InChIKey match to a requested candidate. This conservative co-crystal policy can exclude other suitable sites and can favor a candidate-compatible receptor conformation.',
                    'Bounded selection, not a claim of globally optimal receptor or binding affinity.',
                    'Mutation screening uses deposited SEQADV mutation/conflict annotations, not independent sequence revalidation.',
                    'Missing-site screening uses deposited REMARK 465/470 and spatial proximity of observed sequence neighbors; unannotated disorder cannot be excluded.',
                    'One deposited ligand-defined conformation and one chain are used; receptor flexibility, protonation uncertainty and crystal-condition effects can change rankings.',
                ]}
    return replace(source, selection_evidence=json.dumps(evidence, sort_keys=True, separators=(',', ':')))
