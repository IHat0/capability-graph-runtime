"""Reviewed structured public evidence -> native PBPK dossier.

The source library is deployment-reviewed configuration, not an LLM-generated
search result. Each measurement is exact-source checked and cutoff adjudicated
before it can enter the existing native parameterization. No outcome literature
retrieval or automatic invention of clearance/ionization is performed here.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from .prospective_evidence import EvidenceDatum, adjudicate, verify_adjudication
from .virtual_organism import CompoundDossier, PBPKParameter, PBPKRequest, canonical, digest, dossier_requirements


VERSION = 'pulsate.reviewed-prospective-dossier/v1'
PRECEDENCE = {'measured': 5, 'sourced': 4, 'predicted': 3, 'assumed': 1}
PARAMETER_CATEGORIES = {
    'molecular_weight': {'chemical_identity'}, 'logp': {'physicochemical'}, 'fraction_unbound': {'binding'},
    'solubility': {'solubility'}, 'solubility_ph': {'solubility'},
    'hepatic_clearance': {'clearance'}, 'renal_clearance': {'clearance'},
    'reference_weight': {'preclinical_adme', 'early_pk'}, 'intestinal_permeability': {'preclinical_adme'},
    'ionization': {'ionization'}, 'binding_partner': {'binding'}, 'dosing': {'development_dose'},
    'quantitative_activity': {'quantitative_activity'},
}


def load_library(path):
    root = Path(path).resolve(strict=True)
    if root.stat().st_size > 4 * 1024 * 1024:
        raise ValueError('Reviewed evidence library exceeds its bound.')
    payload = root.read_bytes()
    doc = json.loads(payload)
    if doc.get('schema') != VERSION or not doc.get('reviewer') or len(doc.get('records', [])) > 10000:
        raise ValueError('Unsupported/unreviewed prospective source library.')
    sources = {}
    for item in doc.get('sources', []):
        source_path = (root.parent / item['path']).resolve(strict=True)
        if not source_path.is_relative_to(root.parent) or source_path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError('Reviewed source path is outside its configured directory/bound.')
        source = source_path.read_bytes()
        if digest(source) != item['sha256']:
            raise ValueError('Reviewed dossier source hash mismatch.')
        sources[item['sha256']] = source
    return doc, sources, digest(payload)


def acquire_dossier(identity, species, policy, library, sources):
    """Preserve all conflicts; a conflicting nominal value is not silently chosen."""
    from rdkit import Chem
    from rdkit.Chem import Descriptors
    molecule = Chem.MolFromSmiles(identity['smiles'])
    if molecule is None or Chem.MolToInchiKey(molecule) != identity['inchikey']:
        raise ValueError('Prospective dossier graph identity mismatch.')
    matched = [r for r in library.get('records', []) if r.get('inchikey') == identity['inchikey'] and r.get('species') == species]
    datums = [EvidenceDatum.model_validate(r['datum']) for r in matched]
    report = adjudicate(datums, sources, policy, identity['inchikey'])
    verify_adjudication(report, sources)
    accepted = {d['identifier'] for d in report['decisions'] if d['eligible']}
    grouped, unsupported = {}, []
    for item, datum in zip(matched, datums, strict=True):
        if datum.identifier not in accepted: continue
        if datum.subject_inchikey != identity['inchikey'] or datum.context.get('species') != species:
            raise ValueError('Dossier measurement identity/species is not exact.')
        role = item['native_parameter']
        if role not in PARAMETER_CATEGORIES or datum.category not in PARAMETER_CATEGORIES[role]:
            raise ValueError('Evidence category cannot supply this native endpoint: ' + role)
        if role in {'hepatic_clearance', 'renal_clearance'} and (
                datum.context.get('concentration_basis') != 'plasma'
                or datum.context.get('endpoint') != role + '_plasma_clearance'):
            raise ValueError('Cell/intrinsic/total-systemic/blood clearance cannot be aliased to an organ plasma-clearance input.')
        if role == 'solubility' and (datum.context.get('reference_ph') is None
                or datum.context.get('quantity_kind') != 'apparent_solubility_at_reference_ph'):
            raise ValueError('Unqualified/unknown-pH solubility cannot supply native reference-pH solubility.')
        if role == 'quantitative_activity':
            continue  # Molecular activity is not a PK-Sim native ADME parameter.
        evidence_class = datum.evidence_type
        if evidence_class not in PRECEDENCE:
            unsupported.append({'datum': datum.identifier, 'reason': 'Unsupported numerical evidence class'})
            continue
        if role not in {'ionization', 'binding_partner', 'dosing'}:
            value = float(datum.value)
            if not math.isfinite(value): raise ValueError('Nonfinite dossier value.')
            if evidence_class == 'predicted':
                unsupported.append({'datum': datum.identifier, 'reason': 'Prediction must use the calibrated ADME contract, not a curated measurement alias'})
                continue
            parameter = PBPKParameter(value=value, unit=datum.unit, classification=evidence_class,
                source=datum.source_url, method=item['endpoint_definition'], uncertainty=item.get('uncertainty'))
            grouped.setdefault(role, []).append((PRECEDENCE[evidence_class], parameter, datum.identifier))
        else:
            grouped.setdefault(role, []).append((PRECEDENCE[evidence_class], datum.value, datum.identifier))
    parameters, conflicts, provenance = {}, [], {}
    controls = {}
    parameters['molecular_weight'] = PBPKParameter(value=Descriptors.MolWt(molecule), unit='g/mol',
        classification='calculated', source='Exact identity-verified molecular graph', method='RDKit atomic masses')
    for role, items in grouped.items():
        maximum = max(i[0] for i in items)
        best = [i for i in items if i[0] == maximum]
        # Same-value measurements with different provenance are not a numerical conflict.
        values = {canonical({'value': i[1].value, 'unit': i[1].unit} if isinstance(i[1], PBPKParameter) else i[1]) for i in best}
        provenance[role] = [i[2] for i in sorted(items, key=lambda i: (-i[0], i[2]))]
        if len(values) > 1:
            conflicts.append({'parameter': role, 'reason': 'Conflicting highest-precedence prospective measurements; all preserved, nominal withheld',
                'alternatives': [dict(datum=i[2], value=i[1].model_dump(mode='json') if isinstance(i[1], PBPKParameter) else i[1]) for i in best]})
            continue
        selected = sorted(best, key=lambda i: i[2])[0][1]
        if isinstance(selected, PBPKParameter): parameters[role] = selected
        else: controls[role] = selected
    dossier = None
    missing = []
    if 'binding_partner' not in grouped or any(c['parameter'] == 'binding_partner' for c in conflicts):
        missing.append('Reviewed plasma binding-partner evidence')
    if 'ionization' not in grouped or any(c['parameter'] == 'ionization' for c in conflicts):
        missing.append('Reviewed ionization/pKa evidence')
    if not missing:
        dossier = CompoundDossier(name=identity['name'], smiles=identity['smiles'],
            inchikey=identity['inchikey'], species=species, parameters=parameters,
            binding_partner=controls['binding_partner'], ionization=tuple(controls['ionization']), ionization_status='reviewed',
            ionization_source='; '.join(provenance['ionization']), parameter_conflicts=tuple(conflicts),
            limitations=('Date-qualified public/reviewed numerical inputs; computational qualification is separate from clinical validation.',))
    return {'schema': VERSION, 'identity': identity, 'species': species, 'eligibility': report,
        'parameter_provenance': provenance, 'dossier': dossier.model_dump(mode='json') if dossier else None,
        'resolved_parameters': {k:v.model_dump(mode='json') for k,v in parameters.items()},
        'resolved_controls': controls,
        'conflicts': conflicts, 'missing': missing, 'unsupported': unsupported,
        'prospective_dosing': controls.get('dosing'),
        'status': 'reviewed_dossier_available' if dossier and not conflicts else 'insufficient_or_conflicting_evidence'}


def select_regimen(identity, acquisition, exploratory_policy):
    """No therapeutic dose is guessed; exploratory controls are frozen deployment policy."""
    evidence = acquisition.get('prospective_dosing')
    if evidence:
        source = 'Date-qualified prospective development regimen'
        controls = dict(evidence)
    else:
        if not exploratory_policy or not exploratory_policy.get('provenance'):
            return {'status': 'requires_regimen', 'reason': 'No permissible development dose or reviewed exploratory scenario policy.'}
        controls = dict(exploratory_policy['controls'])
        source = 'Exploratory computational scenario, not a therapeutic recommendation: ' + exploratory_policy['provenance']
    controls.pop('quotes', None)
    controls['entity_name'] = identity['name']
    controls['quotes'] = {}
    request = PBPKRequest.model_validate(controls)
    if request.missing():
        return {'status': 'requires_regimen', 'reason': ', '.join(request.missing())}
    dossier = acquisition.get('dossier')
    missing = dossier_requirements(CompoundDossier.model_validate(dossier), request.route) if dossier else acquisition['missing']
    return {'status': 'scenario_ready' if not missing and not acquisition['conflicts'] else 'insufficient_parameterization',
        'request': request.model_dump(mode='json'), 'source': source, 'missing': missing,
        'classification': 'prospective_development_regimen' if evidence else 'explicit_exploratory_policy',
        'historical_dose_claim': bool(evidence), 'policy_sha256': digest(canonical(exploratory_policy)) if not evidence else None}
