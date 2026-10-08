"""Explicit sponsor-side ADME scenarios, never a reconstructed private history.

This opt-in, hash-pinned data contract leaves the public-history contract intact.
Ranges become conditional native experiments; they are not hidden nominal values
or distributions. Scientific curation remains distinct from byte verification.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .prospective_dossier import PARAMETER_CATEGORIES
from .prospective_evidence import EvidenceDatum, ProspectivePolicy, eligibility, verify_source
from .virtual_organism import CompoundDossier, PBPKParameter, PBPKRequest, _PARAM_UNITS, canonical, digest, dossier_requirements

SCHEMA = 'pulsate.sponsor-side-prospective/v1'
ORIGINS = Literal['historically_public_measurement', 'sourced_measurement',
    'sponsor_side_measured_development', 'sourced_foundational_information',
    'predicted_computational_input', 'sponsor_side_scenario_assumption', 'missing']
CONTROL_ROLES = {'binding_partner', 'ionization', 'dosing'}
SCALAR_SCENARIO_CONTROLS = {'solubility_ph', 'reference_weight'}


class SponsorInput(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    identifier: str = Field(min_length=1)
    role: str
    provenance_class: ORIGINS
    values: tuple[object, ...] = ()
    unit: str | None = None
    source_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    value_pointer: tuple[str | int, ...]
    rationale: str = Field(min_length=1)
    uncertainty: str = Field(min_length=1)
    endpoint_context: dict = Field(default_factory=dict)
    datum: EvidenceDatum | None = None
    prediction: PBPKParameter | None = None
    prediction_validation: dict | None = None
    private_measurement_attestation: dict | None = None

    @field_validator('value_pointer', mode='before')
    @classmethod
    def source_pointer(cls, value):
        if not isinstance(value, (tuple, list)) or len(value) > 20 or any(type(v) not in (str, int) for v in value):
            raise ValueError('Sponsor source pointer exceeds its bound or uses an invalid key.')
        return value

    @model_validator(mode='after')
    def declared_semantics(self):
        if self.role not in set(_PARAM_UNITS) | CONTROL_ROLES:
            raise ValueError('Sponsor ADME cannot supply an outcome, mechanism or potency endpoint.')
        if self.role in _PARAM_UNITS and self.unit != _PARAM_UNITS[self.role]:
            raise ValueError('Sponsor input must use the exact native endpoint unit.')
        if self.role in CONTROL_ROLES and self.unit is not None:
            raise ValueError('Structured sponsor controls do not have a scalar native unit.')
        if self.provenance_class == 'missing':
            if self.values or self.datum or self.prediction:
                raise ValueError('Missing sponsor evidence cannot carry invented values.')
            return self
        if not 1 <= len(self.values) <= 8 or len({canonical(v) for v in self.values}) != len(self.values):
            raise ValueError('Sponsor levels must be nonempty, unique and bounded.')
        if self.role in _PARAM_UNITS and any(type(v) not in (int, float) or not math.isfinite(v) for v in self.values):
            raise ValueError('Sponsor native scalar levels must be finite numbers, not booleans.')
        if self.provenance_class == 'sponsor_side_scenario_assumption':
            if self.datum or self.prediction or self.private_measurement_attestation:
                raise ValueError('Scenario assumptions must not masquerade as measurements or predictions.')
            if self.role not in CONTROL_ROLES | SCALAR_SCENARIO_CONTROLS and len(self.values) < 2:
                raise ValueError('An uncertain sponsor parameter requires a scenario range, not a convenient point.')
        if self.provenance_class in {'historically_public_measurement', 'sourced_measurement'}:
            if self.datum is None or self.datum.evidence_type != 'measured' or self.datum.original_source is None:
                raise ValueError('A sourced measurement requires exact original-primary measurement provenance.')
        if self.provenance_class == 'sponsor_side_measured_development':
            attestation = self.private_measurement_attestation or {}
            if (self.datum is None or self.datum.evidence_type != 'measured' or attestation.get('actual_private_measurement') is not True
                    or not all(attestation.get(k) for k in ('sponsor', 'record_identifier', 'reviewer', 'method'))):
                raise ValueError('Private sponsor measurement needs an actual supplied measurement and explicit attestation.')
        if self.provenance_class == 'sourced_foundational_information' and self.role != 'molecular_weight':
            raise ValueError('Foundational identity cannot launder a biological/ADME input.')
        if self.provenance_class == 'predicted_computational_input':
            if self.prediction is None or self.prediction.classification != 'predicted':
                raise ValueError('Prediction requires the calibrated native prediction contract.')
        elif self.prediction is not None or self.prediction_validation is not None:
            raise ValueError('Prediction provenance must retain prediction classification.')
        return self


def load_sponsor_dossier(path):
    path = Path(path).resolve(strict=True)
    payload = path.read_bytes()
    if len(payload) > 4 * 1024 * 1024:
        raise ValueError('Sponsor dossier exceeds its bound.')
    doc = json.loads(payload)
    review = doc.get('leakage_review', {})
    if (doc.get('schema') != SCHEMA or doc.get('experiment_class') != 'sponsor_side_prospective'
            or not doc.get('reviewer') or review.get('passed') is not True
            or review.get('outcomes_or_known_failure_mechanisms_included') is not False
            or review.get('historical_private_dataset_claim') is not False
            or not all(review.get(k) for k in ('reviewer', 'method', 'limitations'))):
        raise ValueError('Sponsor dossier requires explicit experiment identity and leakage review; no historical-private claim.')
    if not 1 <= len(doc.get('candidates', [])) <= 32:
        raise ValueError('Sponsor candidate dossier count is outside its bound.')
    sources = {digest(payload): payload}
    for source in doc.get('sources', []):
        resolved = (path.parent / source['path']).resolve(strict=True)
        if not resolved.is_relative_to(path.parent) or resolved.stat().st_size > 16 * 1024 * 1024:
            raise ValueError('Sponsor source lies outside the trusted directory/bound.')
        raw = resolved.read_bytes()
        if digest(raw) != source['sha256']:
            raise ValueError('Sponsor source hash mismatch.')
        sources[source['sha256']] = raw
    return doc, sources, digest(payload)


def _check_input(item, identity, species, sources, historical_policy):
    raw = sources.get(item.source_sha256)
    if raw is None or digest(raw) != item.source_sha256:
        raise ValueError('Sponsor source bytes are absent or changed.')
    value = json.loads(raw)
    for component in item.value_pointer:
        if isinstance(component, int) and component < 0:
            raise ValueError('Negative sponsor source index.')
        value = value[component]
    if canonical(value) != canonical(list(item.values)):
        raise ValueError('Sponsor levels disagree with their exact frozen source.')
    if item.provenance_class == 'missing':
        return
    if item.datum is not None:
        datum = item.datum
        if (datum.subject_inchikey != identity['inchikey'] or datum.context.get('species') != species
                or datum.category not in PARAMETER_CATEGORIES[item.role]
                or datum.category == 'quantitative_activity' or canonical(datum.value) != canonical(item.values[0])
                or len(item.values) != 1 or datum.unit != item.unit):
            raise ValueError('Sponsor measurement identity, species, endpoint or value mismatch.')
        verify_source(datum, sources[datum.source_sha256], sources)
        for field in ('reference_ph', 'quantity_kind', 'concentration_basis', 'endpoint'):
            if field in item.endpoint_context and datum.context.get(field) != item.endpoint_context[field]:
                raise ValueError('Sponsor endpoint context disagrees with the original measurement context.')
        if item.provenance_class == 'historically_public_measurement' and not eligibility(datum, historical_policy, identity['inchikey'])['historically_qualified']:
            raise ValueError('Public-history measurement did not establish historical qualification.')
    context = item.endpoint_context
    if item.role in {'hepatic_clearance', 'renal_clearance'} and (
            context.get('concentration_basis') != 'plasma' or context.get('endpoint') != item.role + '_plasma_clearance'):
        raise ValueError('Intrinsic/cell/blood clearance cannot substitute for native organ plasma clearance.')
    if item.role == 'solubility' and (context.get('reference_ph') is None
            or context.get('quantity_kind') != 'apparent_solubility_at_reference_ph'):
        raise ValueError('Sponsor solubility must explicitly represent a known reference-pH endpoint.')
    if item.prediction is not None:
        prediction = item.prediction
        validation = item.prediction_validation or {}
        provenance = prediction.prediction
        validation_bytes = sources.get(validation.get('source_sha256'))
        if (provenance.get('applicability', {}).get('status') != 'in_domain'
                or provenance.get('applicability', {}).get('training_graph_seen') is not False
                or not provenance.get('model_sha256') or prediction.unit != item.unit
                or validation_bytes is None or digest(validation_bytes) != validation.get('source_sha256')
                or validation.get('model_sha256') != provenance['model_sha256']
                or validation.get('held_out') is not True or validation.get('accepted_under_predeclared_gate') is not True
                or not validation.get('gate_sha256') or not validation.get('metrics')):
            raise ValueError('Sponsor prediction requires pinned held-out validation, predeclared gates and no training overlap.')
        if canonical(json.loads(validation_bytes)) != canonical({k: v for k, v in validation.items() if k != 'source_sha256'}):
            raise ValueError('Prediction validation assertions disagree with their pinned report.')
        model_bytes = sources.get(provenance['model_sha256'])
        if model_bytes is None or digest(model_bytes) != provenance['model_sha256']:
            raise ValueError('The actual predictive model bytes must be preserved, not only a hash string.')
        from .prediction_validation import verify_adme_validation
        checked_validation = verify_adme_validation(provenance['model_sha256'], provenance, validation, sources)
        from .adme_prediction import molecular_features, prediction as replay_prediction
        graph, properties, features, fingerprint = molecular_features(identity['smiles'])
        model = json.loads(model_bytes)
        if model.get('endpoint') != item.role:
            raise ValueError('Model endpoint cannot be aliased to another native quantity.')
        # Reproduce actual inference/domain/interval, not a submitted value marked
        # predicted. The separately frozen validation report is checked above.
        model = dict(model, validation=checked_validation)
        replayed = replay_prediction(model, provenance['model_sha256'], graph, properties, features,
            fingerprint, species, provenance['prediction_timestamp'], provenance['prediction_request_sha256'])
        if canonical(replayed.model_dump(mode='json')) != canonical(prediction.model_dump(mode='json')):
            raise ValueError('Sponsor prediction failed independent model inference replay.')
        if any(float(v) not in {prediction.interval[0], prediction.value, prediction.interval[1]} for v in item.values):
            raise ValueError('Prediction levels must come from its actual point/interval, not invented samples.')


def acquire_sponsor_dossier(identity, species, doc, sources, historical_policy, *, file_sha256=None):
    from rdkit import Chem
    from rdkit.Chem import Descriptors
    molecule = Chem.MolFromSmiles(identity['smiles'])
    if molecule is None or Chem.MolToInchiKey(molecule) != identity['inchikey']:
        raise ValueError('Sponsor graph identity mismatch.')
    matches = [c for c in doc['candidates'] if c['inchikey'] == identity['inchikey'] and c['species'] == species]
    if len(matches) != 1:
        raise ValueError('A sponsor candidate requires exactly one full-identity/species dossier.')
    inputs = [SponsorInput.model_validate(r) for r in matches[0]['inputs']]
    if len(inputs) > 32 or len({r.role for r in inputs}) != len(inputs) or len({r.identifier for r in inputs}) != len(inputs):
        raise ValueError('Sponsor endpoint/identifier duplicated or unbounded; conflicts require explicit reviewed levels.')
    solubility = next((r for r in inputs if r.role == 'solubility' and r.provenance_class != 'missing'), None)
    ph = next((r for r in inputs if r.role == 'solubility_ph' and r.provenance_class != 'missing'), None)
    if solubility is not None and (ph is None or len(ph.values) != 1
            or solubility.endpoint_context.get('reference_ph') != ph.values[0]):
        raise ValueError('Native solubility reference pH must match the actual frozen solubility-pH control; independent pH sweeps need a qualified solubility model.')
    parameters = {'molecular_weight': PBPKParameter(value=Descriptors.MolWt(molecule), unit='g/mol',
        classification='calculated', source='Exact identity-verified graph', method='RDKit atomic masses').model_dump(mode='json')}
    controls, conflicts, datums, decisions, provenance, missing, receipts = {}, [], [], [], {}, [], []
    for item in inputs:
        _check_input(item, identity, species, sources, historical_policy)
        receipt = item.model_dump(mode='json')
        receipts.append(receipt)
        provenance[item.role] = [item.identifier]
        if item.provenance_class == 'missing':
            missing.append(item.role + ': ' + item.rationale)
            decisions.append({'identifier': item.identifier, 'status': 'missing', 'reason': item.rationale})
            continue
        classification = {'historically_public_measurement': 'measured', 'sourced_measurement': 'measured',
            'sponsor_side_measured_development': 'measured', 'sourced_foundational_information': 'sourced',
            'predicted_computational_input': 'predicted', 'sponsor_side_scenario_assumption': 'assumed'}[item.provenance_class]
        alternatives = []
        for index, value in enumerate(item.values):
            identifier = f'{item.identifier}-level-{index}'
            datums.append({'identifier': identifier, 'source_sha256': item.source_sha256,
                'provenance_class': item.provenance_class, 'original_source': item.datum.original_source.model_dump(mode='json')
                if item.datum and item.datum.original_source else None})
            if item.role in CONTROL_ROLES:
                parameter = value
            elif item.prediction is not None:
                parameter = PBPKParameter.model_validate(dict(item.prediction.model_dump(mode='json'), value=value)).model_dump(mode='json')
            else:
                parameter = PBPKParameter(value=value, unit=item.unit, classification=classification,
                    source=f'{item.provenance_class}; frozen source SHA-256 {item.source_sha256}',
                    method=item.rationale, uncertainty=item.uncertainty,
                    interval=(min(item.values), max(item.values)) if len(item.values) > 1 else None).model_dump(mode='json')
            alternatives.append({'datum': identifier, 'value': parameter})
        if len(alternatives) > 1:
            conflicts.append({'parameter': item.role, 'reason': 'Declared sponsor uncertainty levels; no nominal selected',
                'uncertainty_kind': 'scenario_range' if classification == 'assumed' else 'prediction_interval',
                'alternatives': alternatives})
        elif item.role in CONTROL_ROLES:
            controls[item.role] = alternatives[0]['value']
        else:
            parameters[item.role] = alternatives[0]['value']
        decisions.append({'identifier': item.identifier, 'status': item.provenance_class,
            'reason': item.rationale, 'historical_private_data_claim': False})
    declared = {r.role for r in inputs if r.provenance_class != 'missing'}
    for role in CONTROL_ROLES - {'dosing'} - declared:
        missing.append('Sponsor control missing: ' + role)
    metadata = {'experiment_class': 'sponsor_side_prospective', 'schema': SCHEMA,
        'canonical_document_sha256': digest(canonical(doc)), 'dossier_file_sha256': file_sha256,
        'leakage_review': doc['leakage_review'], 'input_receipts': receipts,
        'historical_private_data_claim': False}
    acquisition = {'schema': SCHEMA, 'identity': identity, 'species': species, 'eligibility': {'datums': datums, 'decisions': decisions},
        'resolved_parameters': parameters, 'resolved_controls': controls, 'parameter_provenance': provenance,
        'conflicts': conflicts, 'missing': missing, 'unsupported': [], 'prospective_dosing': controls.get('dosing'),
        'dossier': None, 'experiment_provenance': metadata,
        'status': 'conditional_sponsor_experiments_required' if conflicts else 'sponsor_dossier_available'}
    # Preserve missing evidence in the audit, but let the native route contract
    # decide necessity. Missing oral permeability must not block an IV study.
    if not conflicts and all(k in controls for k in ('binding_partner', 'ionization')):
        acquisition['dossier'] = sponsor_compound_dossier(acquisition).model_dump(mode='json')
    return acquisition


def sponsor_compound_dossier(acquisition):
    identity = acquisition['identity']
    controls = acquisition['resolved_controls']
    provenance = acquisition['experiment_provenance']
    ionization = next(r for r in provenance['input_receipts'] if r['role'] == 'ionization')
    return CompoundDossier(name=identity['name'], smiles=identity['smiles'], inchikey=identity['inchikey'],
        species=acquisition['species'], parameters=acquisition['resolved_parameters'],
        binding_partner=controls['binding_partner'], ionization=tuple(controls['ionization']),
        ionization_status='scenario' if ionization['provenance_class'] == 'sponsor_side_scenario_assumption' else 'reviewed',
        ionization_source=ionization['identifier'], experiment_provenance=provenance,
        limitations=('Sponsor-side exploratory scenario; not an authentic private historical dataset or a clinical prediction.',))


def sponsor_parameter_audit(acquisition):
    """Parent evidence audit, with ranges visible and no fabricated nominal point."""
    rows = []
    origins = {'historically_public_measurement':'measured','sourced_measurement':'measured',
        'sponsor_side_measured_development':'measured','sourced_foundational_information':'sourced',
        'predicted_computational_input':'predicted','sponsor_side_scenario_assumption':'assumed','missing':'missing'}
    for item in acquisition['experiment_provenance']['input_receipts']:
        if item['role'] not in _PARAM_UNITS:
            continue
        values = item['values']
        rows.append({'name':item['role'],'value':values[0] if len(values)==1 else None,
            'interval':[min(values),max(values)] if len(values)>1 else None,'unit':item['unit'],
            'classification':origins[item['provenance_class']],
            'source':item['provenance_class'] + '; SHA-256 ' + item['source_sha256'],
            'method':item['rationale'],'uncertainty':item['uncertainty']})
    return rows


def select_sponsor_regimen(identity, acquisition, exploratory_policy):
    declared = acquisition.get('prospective_dosing')
    source = 'Frozen sponsor-side development controls'
    if declared is None:
        if not exploratory_policy or not exploratory_policy.get('provenance'):
            return {'status': 'requires_regimen', 'reason': 'No sponsor development dose or explicit exploratory dosing policy.'}
        declared = exploratory_policy['controls']
        source = 'Sponsor-side exploratory dosing policy: ' + exploratory_policy['provenance']
    request = PBPKRequest.model_validate(dict(declared, entity_name=identity['name'], quotes={}))
    missing = list(request.missing())
    if acquisition['dossier']:
        missing.extend(dossier_requirements(CompoundDossier.model_validate(acquisition['dossier']), request.route))
    else:
        missing.extend(acquisition['missing'])
    return {'status': 'scenario_ready' if not missing and not acquisition['conflicts'] else 'insufficient_parameterization',
        'request': request.model_dump(mode='json'), 'source': source, 'missing': missing,
        'classification': 'sponsor_side_prospective', 'historical_dose_claim': False,
        'policy_sha256': digest(canonical(exploratory_policy)) if not acquisition.get('prospective_dosing') else None}
