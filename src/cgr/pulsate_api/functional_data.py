"""Conservative functional-assay semantics and identity-before-label firewall.

These are assay rules, not drug-name routes. Unknown direction is refused.
Identity normalization is for denial only: it never changes a model's input
graph or turns a mixture into an admissible small molecule.
"""
from __future__ import annotations

import re
import json
import math
import functools

from .bioactivity_hypotheses import ACTIVITY_UNITS

from .virtual_organism import canonical, digest

SEMANTICS_VERSION = 'pulsate.functional-assay-semantics/v1'


def elemental_inventory(smiles):
    """Heavy elements cannot change under salts, protonation or tautomerism."""
    from collections import Counter
    from rdkit import Chem
    mol=Chem.MolFromSmiles(smiles)
    if mol is None:raise ValueError('Invalid denial graph.')
    return sorted({tuple(sorted(Counter(a.GetAtomicNum() for a in fragment.GetAtoms() if a.GetAtomicNum()>1).items()))
        for fragment in Chem.GetMolFrags(mol,asMols=True,sanitizeFrags=True)})


@functools.lru_cache(maxsize=32768)
def denial_connectivities(smiles):
    from rdkit import Chem
    from rdkit.Chem.MolStandardize import rdMolStandardize as standard
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise ValueError('Exclusion graph cannot be parsed.')
    keys = set()
    for fragment in Chem.GetMolFrags(mol, asMols=True, sanitizeFrags=True):
        parent = standard.FragmentParent(fragment)
        for item in (fragment, parent, standard.Uncharger().uncharge(parent)):
            # Macromolecules cannot enter the declared model domain. Preserve
            # their literal parent identities without pretending a truncated
            # tautomer enumeration is canonical.
            forms=[item]
            if item.GetNumHeavyAtoms()<=150:
                enumerator=standard.TautomerEnumerator()
                result=enumerator.Enumerate(item)
                if result.status!=standard.TautomerEnumeratorStatus.Completed:
                    raise ValueError('Incomplete tautomer normalization; refuse before labels.')
                forms.append(enumerator.PickCanonical(result))
            for form in forms:
                key = Chem.MolToInchiKey(form)
                if not key:
                    raise ValueError('Exclusion connectivity cannot be established.')
                keys.add(key.split('-')[0])
    return sorted(keys)


def graph_allowed(structure, denied, inventories=None):
    from rdkit import Chem
    smiles = structure.get('canonical_smiles')
    if not smiles:
        raise ValueError('No exact public molecular graph.')
    mol = Chem.MolFromSmiles(smiles)
    if mol is None or len(Chem.GetMolFrags(mol)) != 1:
        raise ValueError('Mixture/salt is not a model input; all fragments still checked for denial.')
    if Chem.MolToInchiKey(mol) != structure.get('standard_inchi_key'):
        raise ValueError('Public graph and InChIKey disagree.')
    if mol.GetNumHeavyAtoms() > 150:
        raise ValueError('Macromolecule is outside the declared model feature domain.')
    # Exact parent/tautomer matches necessarily preserve heavy-element counts.
    # This is only a prefilter for denial, never a similarity or activity gate.
    needs_normalization = inventories is None or bool(set(elemental_inventory(smiles)) & set(
        tuple(tuple(pair) for pair in inventory) for inventory in inventories))
    if needs_normalization and set(denial_connectivities(smiles)) & set(denied):
        raise ValueError('Excluded normalized parent/fragment/connectivity/tautomer.')
    return Chem.MolToSmiles(mol, isomericSmiles=True)


def assay_semantics(assay, families):
    if (assay.get('confidence_score') != 9 or assay.get('relationship_type') != 'D'
            or assay.get('assay_organism') not in (None, 'Homo sapiens')
            or assay.get('assay_type') not in ('B', 'F')):
        raise ValueError('Not a direct confidence-9 Human functional assay.')
    text = (assay.get('description') or '').lower()
    if not text or re.search(r'\b(binding|displacement|affinity|radioligand|competition|occupancy)\b', text):
        raise ValueError('Binding/ambiguous assay is not a functional endpoint.')
    if re.search(r'\b(mutant|mutated|mutation|variant)\b|\b[A-Z]\d{2,4}[A-Z]\b', assay.get('description') or ''):
        raise ValueError('Variant-specific assay requires a separately represented target variant.')
    inhibition = bool(re.search(r'\b(inhibition|inhibitory|inhibitor|blocked|blocking|blockade|block)\b', text))
    activation = bool(re.search(r'\b(activation|agonist|agonistic|agonism|stimulation)\b', text))
    antagonism = bool(re.search(r'\b(antagonist|antagonistic|antagonism)\b', text))
    if antagonism:
        # A challenge agonist does not make the tested compound an agonist.
        # Only an explicitly described challenge is removed; mixed candidate
        # agonism/antagonism and inhibition-alone remain ambiguous/refused.
        candidate_text = re.sub(r'\bagonist[- ](?:induced|stimulated|evoked)\b', '', text)
        activation = bool(re.search(r'\b(activation|agonist|agonistic|agonism|stimulation)\b', candidate_text))
    family = ' '.join(families).lower()
    if 'ion channel' in family and inhibition and not (activation or antagonism) and re.search(
            r'current|patch.clamp|voltage.clamp|electrophysiolog', text):
        action, readout = 'blocker', 'electrophysiological_current'
    elif 'gpcr' in family and (activation != antagonism):
        matches = [name for name, pattern in (
            ('camp', r'\bcamp\b|cyclic.amp'), ('calcium_flux', r'calcium|ca2\+'),
            ('gtp_signaling', r'gtp|gtpase'), ('beta_arrestin', r'arrestin'),
            ('functional_reporter', r'luciferase|reporter')) if re.search(pattern, text)]
        if len(matches) != 1:
            raise ValueError('GPCR direction/readout is not one unambiguous functional stratum.')
        action, readout = ('agonist' if activation else 'antagonist'), matches[0]
    elif 'transporter' in family and inhibition and not activation and re.search(r'uptake|transport', text):
        action, readout = 'inhibitor', 'substrate_transport'
    elif any(term in family for term in ('enzyme', 'phosphodiesterase', 'cytochrome', 'kinase', 'monoamine oxidase', 'cyclooxygenase', 'protease')) or any(f.casefold() in ('cox','mao') for f in families):
        if not inhibition or activation or antagonism or not re.search(
                r'activity|enzym|turnover|substrate|hydrolys|cataly|phosphorylat|metaboli', text):
            raise ValueError('Enzyme functional inhibition is not explicitly established.')
        action, readout = 'inhibitor', 'enzymatic_activity'
    elif 'nuclear' in family and activation != antagonism and re.search(r'transactiv|luciferase|reporter', text):
        action, readout = ('agonist' if activation else 'antagonist'), 'transcriptional_reporter'
    else:
        raise ValueError('Functional direction/readout unsupported or ambiguous.')
    return {'version': SEMANTICS_VERSION, 'action': action, 'readout': readout,
        'concentration_basis': 'assay_nominal', 'variant': 'no_variant_declared',
        'assay_host': assay.get('assay_cell_type'), 'description': assay['description'],
        'bao_format': assay.get('bao_format'),
        'limitations': 'Assay protocols retained; nominal concentration, unknown free fraction/pH/temperature/substrate conditions; no implicit physiological transfer.'}


def numeric_guard(document, filters, denied_ids, *, allowed_relations=('=',)):
    molecules = set(filters.get('molecule_chembl_id__in', '').split(',')) - {''}
    assays = set(filters.get('assay_chembl_id__in', '').split(',')) - {''}
    relation=filters.get('standard_relation','=')
    if (not molecules or not assays or molecules & set(denied_ids)
            or relation not in allowed_relations or relation not in ('=','>','>=','<','<=')):
        raise ValueError('Labels require explicit admitted molecule AND functional assay sets.')
    for row in document['activities']:
        if (row['molecule_chembl_id'] not in molecules or row['molecule_chembl_id'] in denied_ids
                or row['assay_chembl_id'] not in assays or row['target_chembl_id'] != filters['target_chembl_id']
                or row['standard_type'] != filters['standard_type'] or row['standard_relation'] != relation):
            raise ValueError('Numerical response escaped the pre-label identity/assay firewall; not admitted or persisted.')


def stratum_key(target, endpoint, semantics):
    return {'target_accession': target, 'species': 'Human', 'kind': endpoint,
        **{key: semantics[key] for key in ('action', 'readout', 'concentration_basis', 'variant')}}


def stratum_identifier(key):
    return digest(canonical(key))[:24]


def verify_dataset(dataset, snapshot):
    """Replay original identity, direct target, assay semantics and exact label.

    snapshot(sha, collection) must return hash-checked original projected rows.
    No training receipt or curator-created measurement replaces primary bytes.
    """
    from rdkit import Chem
    if (dataset.get('schema')!='pulsate.functional-target-data/v1'
            or dataset.get('candidate_activity_requested') is not False):
        raise ValueError('Functional dataset lacks its exclusion/semantic boundary.')
    seen=set()
    for record in dataset['records']:
        identifier=record['activity_id']
        if identifier in seen: raise ValueError('Duplicate functional activity identity.')
        seen.add(identifier)
        molecule=snapshot(record['graph_source_sha256'],'molecules')[record['molecule_chembl_id']]
        activity=snapshot(record['activity_source_sha256'],'activities')[identifier]
        assay=snapshot(record['assay_source_sha256'],'assays')[record['assay_chembl_id']]
        target=snapshot(record['target_source_sha256'],'targets')[dataset['target_chembl_id']]
        if (target['organism']!='Homo sapiens' or target['target_type']!='SINGLE PROTEIN'
                or len(target['target_components'])!=1 or target['target_components'][0]['accession']!=dataset['target']['target_accession']
                or activity['molecule_chembl_id']!=record['molecule_chembl_id']
                or record['molecule_chembl_id'] in dataset['excluded_identifiers']
                or activity['assay_chembl_id']!=record['assay_chembl_id']
                or activity['target_chembl_id']!=dataset['target_chembl_id']
                or assay['target_chembl_id']!=dataset['target_chembl_id']
                or activity['standard_type']!=record['kind'] or record['kind'] not in ('IC50','EC50')
                or activity['standard_relation']!='=' or activity.get('data_validity_comment') or activity.get('potential_duplicate')):
            raise ValueError('Functional record disagrees with direct original Human assay/target identity.')
        structure=molecule['molecule_structures']
        smiles=graph_allowed(structure,dataset['excluded_connectivities'],dataset.get('excluded_element_inventories'))
        semantics=assay_semantics(assay,dataset['target']['families'])
        key=stratum_key(dataset['target']['target_accession'],record['kind'],semantics)
        value=float(activity['standard_value'])*ACTIVITY_UNITS[activity['standard_units']]
        if (smiles!=record['smiles'] or Chem.MolToInchiKey(Chem.MolFromSmiles(smiles))!=record['inchikey']
                or canonical(semantics)!=canonical(record['semantics'])
                or stratum_identifier(key)!=record['stratum'] or canonical(key)!=canonical(dataset['strata'][record['stratum']])
                or not math.isfinite(value) or value<=0 or value!=record['value_umol_l']
                or not math.isclose(6-math.log10(value),record['p_activity'],abs_tol=1e-12)):
            raise ValueError('Functional graph, source action/readout, stratum or numerical label failed replay.')


def source_index(root):
    """Hash-check each preserved primary response once; index exact IDs."""
    cache={}
    fields={'molecules':'molecule_chembl_id','assays':'assay_chembl_id','activities':'activity_id','targets':'target_chembl_id'}
    def snapshot(sha,collection):
        if (sha,collection) not in cache:
            raw=(root/(sha+'.json')).read_bytes()
            if len(raw)>8*1024*1024 or digest(raw)!=sha: raise ValueError('Functional primary response changed.')
            rows=json.loads(raw)[collection];field=fields[collection]
            indexed={r[field]:r for r in rows}
            if len(indexed)!=len(rows): raise ValueError('Duplicate primary source record identity.')
            cache[sha,collection]=indexed
        return cache[sha,collection]
    return snapshot
