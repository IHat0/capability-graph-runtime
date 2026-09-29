"""Exact public compound identity evidence for deposited docking-site selection.

A large organic crystal component is not necessarily a relevant site marker.
This deliberately conservative policy requires a deposited component matching
one of the requested compounds, including the complete standard InChIKey.
"""
import json
import re
from urllib.parse import quote


def requested_site_components(names, fetch_json):
    if not names:
        raise ValueError('Automatic receptor selection requires named candidate compounds or an explicit binding-site choice.')
    components = {}
    for name in dict.fromkeys(names):
        base = 'https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/'
        result = fetch_json(base + 'name/' + quote(name, safe='') + '/cids/JSON')
        identifiers = result.get('IdentifierList', {}).get('CID', [])
        if len(identifiers) != 1 or type(identifiers[0]) is not int:
            raise ValueError('A candidate name has ambiguous public identity; confirm its exact compound identifier.')
        cid = identifiers[0]
        properties = fetch_json(base + f'cid/{cid}/property/InChIKey/JSON')
        rows = properties.get('PropertyTable', {}).get('Properties', [])
        if len(rows) != 1 or rows[0].get('CID') != cid:
            raise ValueError('Public compound identity could not be verified.')
        key = rows[0].get('InChIKey', '')
        if not re.fullmatch(r'[A-Z]{14}-[A-Z]{10}-[A-Z]', key):
            raise ValueError('Public compound identity lacks a valid full InChIKey.')
        query = {'query': {'type': 'terminal', 'service': 'text_chem', 'parameters': {
            'attribute': 'rcsb_chem_comp_descriptor.InChIKey', 'operator': 'exact_match', 'value': key}},
            'return_type': 'mol_definition', 'request_options': {'paginate': {'start': 0, 'rows': 100}}}
        result = fetch_json('https://search.rcsb.org/rcsbsearch/v2/query?json=' + quote(json.dumps(query), safe=''))
        if result.get('total_count', 0) > 100:
            raise ValueError('Too many deposited component matches for bounded identity review.')
        for row in result.get('result_set', []):
            code = row.get('identifier', '')
            if not re.fullmatch(r'[A-Z0-9]{1,5}', code):
                raise ValueError('Invalid public chemical-component identifier.')
            document = fetch_json('https://data.rcsb.org/rest/v1/core/chemcomp/' + code)
            if document.get('rcsb_chem_comp_descriptor', {}).get('InChIKey') != key:
                raise ValueError('Chemical-component identity disagrees with the search result.')
            components[code] = {'candidate_name': name, 'pubchem_cid': cid, 'inchikey': key,
                                'chemical_component_id': code, 'match_policy': 'full standard InChIKey equality'}
    if not components:
        raise ValueError('No deposited component matches the requested compounds exactly; specify a binding-site reference or receptor policy.')
    return components


def entries_with_components(components, fetch_json):
    query = {'query': {'type': 'terminal', 'service': 'text_chem', 'parameters': {
        'attribute': 'rcsb_chem_comp_container_identifiers.comp_id',
        'operator': 'in', 'value': sorted(components)}}, 'return_type': 'entry',
        'request_options': {'paginate': {'start': 0, 'rows': 10000}}}
    result = fetch_json('https://search.rcsb.org/rcsbsearch/v2/query?json=' + quote(json.dumps(query), safe=''))
    if result.get('total_count', 0) > 10000:
        raise ValueError('Deposited component search exceeds the bounded receptor review limit.')
    return {row['identifier'] for row in result.get('result_set', [])}
