"""Original-byte functional assays, distinct from nominal binding predictions.

IC50/EC50/Ki/Kd are retained without cross-endpoint pooling. Only a sourced
functional inhibition/blocker IC50 with a sourced Hill slope can currently enter
the reversible current-block adapter. Other semantics stay visible/refused.
Concentration-basis assumptions are explicit; they are never measured free drug.
"""
from __future__ import annotations

import json
import math
import re
import html
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .primary_measurements import locate, same_value
from .prospective_evidence import EvidenceDatum, eligibility, verify_source, allowed_source
from .virtual_organism import canonical, digest


class FunctionalAssay(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    datum: EvidenceDatum
    assay_type: Literal['functional', 'binding']
    action: Literal['blocker', 'inhibitor', 'agonist', 'antagonist', 'activator', 'binding_only', 'unknown']
    endpoint: Literal['IC50', 'EC50', 'Ki', 'Kd']
    relation: Literal['=', '>', '>=', '<', '<='] = '='
    relation_evidence: dict | None = None
    assay_host: str = Field(min_length=1)
    target_species: str = Field(min_length=1)
    tissue: str = Field(min_length=1)
    compartment: str = Field(min_length=1)
    assay_context: str = Field(min_length=1)
    action_evidence: dict
    hill: dict | None = None
    concentration_translation: dict | None = None
    applicability: str = Field(min_length=1)
    uncertainty: str = Field(min_length=1)

    @model_validator(mode='after')
    def distinct_semantics(self):
        if self.datum.category != 'quantitative_activity' or self.datum.evidence_type != 'measured':
            raise ValueError('Functional assay needs a measured quantitative endpoint, not a prediction/narrative.')
        if (self.datum.context.get('kind') != self.endpoint
                or self.datum.context.get('species') != self.target_species
                or not self.datum.target_accession or not self.datum.subject_inchikey):
            raise ValueError('Functional endpoint/target/species/identity contract mismatch.')
        if self.assay_type == 'binding' and self.action not in {'binding_only', 'unknown'}:
            raise ValueError('Binding endpoints do not establish functional direction.')
        if (self.action_evidence.get('action') != self.action
                or self.action_evidence.get('target_accession') != self.datum.target_accession
                or self.action_evidence.get('target_species') != self.target_species):
            raise ValueError('Functional direction must retain its exact target/species semantic review.')
        return self


def _anchor(document, sources):
    raw = sources.get(document.get('source_sha256'))
    if (raw is None or digest(raw) != document['source_sha256'] or len(raw) > 16 * 1024 * 1024
            or not allowed_source(document.get('source_url', '')) or not document.get('review_basis')):
        raise ValueError('Functional semantic/parameter anchor lacks exact primary evidence.')
    value = locate(raw, document.get('source_format', 'json'), document['pointer'],
        pdf_reviews=document.get('pdf_reviews', ()), sources=sources)
    if not same_value(value, document['expected']):
        raise ValueError('Functional semantic/parameter anchor failed original-byte replay.')
    return value


def _contract_dump(assay):
    document=assay.model_dump(mode='json')
    # Existing exact-point source contracts keep their historical content hash.
    if assay.relation=='=' and assay.relation_evidence is None:
        document.pop('relation');document.pop('relation_evidence')
    return document


def normalize_assay(record, identity, policy, sources):
    assay = FunctionalAssay.model_validate(record)
    datum = assay.datum
    if datum.subject_inchikey != identity['inchikey']:
        raise ValueError('Functional measurement belongs to a different molecular identity.')
    verify_source(datum, sources[datum.source_sha256], sources)
    decision = eligibility(datum, policy, identity['inchikey'])
    if not decision['eligible']:
        return None, decision
    _anchor(assay.action_evidence, sources)
    from .bioactivity_hypotheses import ACTIVITY_UNITS
    if assay.relation != '=':
        if datum.unit not in ACTIVITY_UNITS:
            raise ValueError('Censored functional endpoint needs explicit molar units.')
        if isinstance(datum.value,str):
            # Verify the original bytes above; decode only standard source text
            # entities for inequality syntax, not an inferred numerical point.
            match=re.fullmatch(r'(>=|<=|>|<)\s*([+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)',html.unescape(datum.value).strip())
            if not match or match[1]!=assay.relation:
                raise ValueError('Censored relation disagrees with the exact original assay cell.')
            bound=float(match[2])*ACTIVITY_UNITS[datum.unit]
        else:
            if assay.relation_evidence is None or _anchor(assay.relation_evidence,sources)!=assay.relation:
                raise ValueError('A numeric censored bound needs its independently anchored relation.')
            if type(datum.value) not in (float,int):raise ValueError('Invalid censored functional bound.')
            bound=float(datum.value)*ACTIVITY_UNITS[datum.unit]
        if not math.isfinite(bound) or bound<=0 or assay.hill is not None or assay.concentration_translation is not None:
            raise ValueError('Censored activity is not an exact potency, Hill curve or free-concentration conversion.')
        bounded={'status':'censored_measurement','inchikey':identity['inchikey'],'target_accession':datum.target_accession,
            'species':assay.target_species,'kind':assay.endpoint,'relation':assay.relation,
            'activity_bound_umol_l':bound,'unit':'umol/l','functional_direction':assay.action,
            'original_bounded_value':datum.value,
            'concentration_basis':datum.context.get('concentration_basis','assay_nominal'),
            'classification':'measured','source_sha256':datum.source_sha256,
            'functional_assay_sha256':digest(canonical(_contract_dump(assay))),
            'uncertainty':assay.uncertainty,'datum':datum.model_dump(mode='json'),
            'functional_transfer_supported':False,
            'limitation':'Censored assay bound, not an exact potency or a negative safety finding; no Hill slope, exposure/activity ratio or physiological transfer inferred.'}
        return None,dict(decision,status='censored_measurement_retained',
            reason='Original bounded activity retained separately from quantitative point endpoints.',bounded_activity=bounded)
    if datum.unit not in ACTIVITY_UNITS or type(datum.value) not in (int, float) or not math.isfinite(datum.value) or datum.value <= 0:
        raise ValueError('Functional endpoint requires a finite positive molar concentration.')
    value = float(datum.value) * ACTIVITY_UNITS[datum.unit]
    result = {'inchikey': identity['inchikey'], 'species': assay.target_species,
        'target_accession': datum.target_accession, 'kind': assay.endpoint, 'value': value, 'unit': 'umol/l',
        'concentration_basis': datum.context.get('concentration_basis', 'assay_nominal'),
        'organ': assay.tissue, 'compartment': assay.compartment, 'assay_context': assay.assay_context,
        'assay_type': assay.assay_type, 'assay_host': assay.assay_host, 'functional_direction': assay.action,
        'uncertainty': assay.uncertainty, 'applicability': assay.applicability,
        'source': datum.original_source.source_url, 'source_sha256': datum.source_sha256,
        'datum': datum.model_dump(mode='json'), 'classification': 'measured', 'eligibility': decision,
        'functional_assay_sha256': digest(canonical(_contract_dump(assay))),
        'functional_transfer_supported': False}
    if assay.hill is not None:
        hill = _anchor(assay.hill, sources)
        if not same_value(hill, assay.hill.get('value')) or type(assay.hill.get('value')) not in (float, int) or not math.isfinite(assay.hill['value']) or assay.hill['value'] <= 0:
            raise ValueError('Hill slope must be a finite positive sourced parameter, not a guessed default.')
        result['hill_coefficient'] = assay.hill['value']
    translation = assay.concentration_translation
    if translation is not None:
        if (translation.get('kind') != 'protein_free_bath_equivalence_assumption'
                or translation.get('assumed_fraction_unbound') != 1.0
                or not all(translation.get(k) for k in ('rationale', 'limitations', 'applicability', 'source_anchors'))
                or result['concentration_basis'] != 'assay_nominal'):
            raise ValueError('Unqualified assay-to-free concentration translation.')
        for anchor in translation['source_anchors']: _anchor(anchor, sources)
        result.update(concentration_basis='unbound', concentration_translation=translation,
            concentration_basis_classification='assumption', measured_unbound_concentration=False)
    result['functional_transfer_supported'] = (result['assay_type'] == 'functional'
        and result['functional_direction'] in {'blocker', 'inhibitor'} and result['kind'] == 'IC50'
        and result.get('hill_coefficient') is not None and result['concentration_basis'] == 'unbound')
    return result, decision


def load_library(path):
    path = Path(path).resolve(strict=True)
    payload = path.read_bytes()
    if len(payload) > 4 * 1024 * 1024: raise ValueError('Functional library exceeds its bound.')
    doc = json.loads(payload)
    if doc.get('schema') != 'pulsate.functional-pharmacology/v1' or not doc.get('reviewer') or len(doc.get('assays', [])) > 10000:
        raise ValueError('Functional library is not a versioned reviewed assay collection.')
    sources = {}
    for item in doc.get('sources', []):
        source = (path.parent / item['path']).resolve(strict=True)
        if not source.is_relative_to(path.parent) or source.stat().st_size > 16 * 1024 * 1024:
            raise ValueError('Functional primary source is outside the trusted boundary.')
        raw = source.read_bytes()
        if digest(raw) != item['sha256']: raise ValueError('Functional source hash mismatch.')
        sources[item['sha256']] = raw
    return doc, sources, digest(payload)


def candidate_assays(identity, library, sources, policy):
    activities, refusals = [], []
    for record in library.get('assays', []):
        if record['datum']['subject_inchikey'] != identity['inchikey']: continue
        activity, decision = normalize_assay(record, identity, policy, sources)
        if activity is None:
            refusals.append(decision)
        else:
            activities.append(activity)
    return activities, refusals
