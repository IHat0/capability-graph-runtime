"""Hash-pinned endpoint QSAR inference. No LLM, name routing or biological defaults.

Native PBPK uses the existing dossier. Predictions without a justified endpoint
translation stay in adme_parameters; they never become arbitrary native inputs.
"""
from __future__ import annotations

import functools
import json
import math
import os
import struct
from datetime import datetime, timezone
from pathlib import Path

from .virtual_organism import CompoundDossier, PBPKParameter, canonical, digest


VERSION = "pulsate.adme-rf/v1"
TRANSLATION_POLICY = "pulsate.native-readiness/2"
FEATURES = {"morgan_radius": 2, "morgan_bits": 512, "descriptors":
    ["MolWt", "MolLogP", "TPSA", "NumHDonors", "NumHAcceptors", "NumRotatableBonds", "RingCount", "FractionCSP3"]}
ENDPOINTS = {
    "aqueous_solubility": ("log10(mol/l)", None, "Aqueous molar solubility; heterogeneous experimental conditions, pH not established"),
    "fraction_unbound": ("log10(fraction)", "species", "Plasma unbound fraction derived from measured bound percentage"),
    "hepatocyte_intrinsic_clearance": ("log10(ul/min/10^6 cells)", "Human", "Apparent human hepatocyte intrinsic clearance; not systemic clearance"),
    "logd_ph74": ("Log Units", None, "Octanol/water distribution coefficient at pH 7.4"),
    "caco2_permeability": ("log10(cm/s)", "Human", "Caco-2 apparent permeability; not native intestinal specific permeability"),
}
PRECEDENCE = {"measured": 5, "sourced": 4, "calculated": 3, "predicted": 3, "estimated": 2, "assumed": 1, "missing": 0}


def molecular_features(smiles):
    from rdkit import Chem, rdBase, DataStructs
    from rdkit.Chem import Descriptors, rdFingerprintGenerator
    mol = Chem.MolFromSmiles(smiles)
    if mol is None or len(Chem.GetMolFrags(mol)) != 1 or mol.GetNumHeavyAtoms() > 150:
        raise ValueError("ADME requires one valid small-molecule graph, not a mixture or macromolecule.")
    if any(a.GetAtomicNum() not in {1, 6, 7, 8, 9, 15, 16, 17, 35, 53} for a in mol.GetAtoms()):
        raise ValueError("ADME descriptor/model element domain does not cover this graph.")
    fp = rdFingerprintGenerator.GetMorganGenerator(radius=2, fpSize=512).GetFingerprint(mol)
    descriptors = [float(getattr(Descriptors, name)(mol)) for name in FEATURES["descriptors"]]
    # sklearn tree inference converts input to float32. Match its numerical boundary.
    values = [float(v) for v in fp] + [struct.unpack("f", struct.pack("f", v))[0] for v in descriptors]
    graph = Chem.MolToSmiles(mol, isomericSmiles=True)
    identity = {"smiles": graph, "inchikey": Chem.MolToInchiKey(mol), "graph_sha256": digest(graph.encode()), "rdkit_version": rdBase.rdkitVersion}
    properties = dict(zip(FEATURES["descriptors"], descriptors, strict=True))
    properties["formal_charge"] = sum(a.GetFormalCharge() for a in mol.GetAtoms())
    return identity, properties, values, fp


def forest_value(model, features, *, model_schema=VERSION):
    if model.get("schema_version") != model_schema or model.get("features") != FEATURES:
        raise ValueError("Incompatible ADME model/feature version.")
    trees = model["trees"]
    if not 1 <= len(trees) <= 256:
        raise ValueError("Unbounded ADME forest.")
    values = []
    for tree in trees:
        count = len(tree["feature"])
        if not 1 <= count <= 8192 or any(len(tree[k]) != count for k in ("left", "right", "threshold", "value")):
            raise ValueError("Invalid ADME tree shape.")
        node = 0
        for _ in range(64):
            if not 0 <= node < count:
                raise ValueError("Invalid ADME node reference.")
            feature = tree["feature"][node]
            if feature == -2:
                value = float(tree["value"][node])
                if not math.isfinite(value):
                    raise ValueError("Nonfinite ADME leaf.")
                values.append(value)
                break
            if not 0 <= feature < len(features) or not math.isfinite(tree["threshold"][node]):
                raise ValueError("Invalid ADME split feature.")
            node = tree["left"][node] if features[feature] <= tree["threshold"][node] else tree["right"][node]
        else:
            raise ValueError("Cyclic/deep ADME tree.")
    return sum(values) / len(values)


@functools.lru_cache(maxsize=24)
def _parsed_model(path_string, expected_sha, modified_ns, size):
    path = Path(path_string)
    payload = path.read_bytes()
    if digest(payload) != expected_sha:
        raise ValueError("ADME model artifact hash mismatch.")
    return json.loads(payload)


def load_models(configured=None):
    configured = configured or os.environ.get("PULSATE_ADME_MODEL_MANIFEST")
    if not configured:
        return [], None
    path = Path(configured).resolve(strict=True)
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("ADME manifest too large.")
    payload = path.read_bytes()
    manifest_sha = digest(payload)
    doc = json.loads(payload)
    if doc.get("schema_version") != "pulsate.adme-model-manifest/v1" or not 1 <= len(doc.get("models", [])) <= 24:
        raise ValueError("Incompatible ADME model manifest.")
    validation = None
    validation_path = path.parent / 'held-out-validation.json'
    if validation_path.exists():
        if validation_path.stat().st_size > 32 * 1024 * 1024:
            raise ValueError('ADME validation report exceeds its bound.')
        validation_payload = validation_path.read_bytes()
        validation = json.loads(validation_payload)
        if validation.get('manifest_sha256') != manifest_sha:
            raise ValueError('ADME held-out validation references another frozen model manifest.')
        validation_sha = digest(validation_payload)
    result, seen = [], set()
    for item in doc["models"]:
        key = item["endpoint"], item["species"]
        if key in seen or key[0] not in ENDPOINTS:
            raise ValueError("Duplicate/unknown ADME endpoint.")
        seen.add(key)
        model_path = (path.parent / item["path"]).resolve(strict=True)
        if not model_path.is_relative_to(path.parent) or model_path.stat().st_size > 64 * 1024 * 1024:
            raise ValueError("ADME model outside trusted directory/bound.")
        stat = model_path.stat()
        model = _parsed_model(str(model_path), item["sha256"], stat.st_mtime_ns, stat.st_size)
        if model['training'].get('rdkit_version'):
            from rdkit import rdBase
            if model['training']['rdkit_version'] != rdBase.rdkitVersion:
                raise ValueError('ADME feature runtime differs from the pinned model training RDKit version.')
        definition = ENDPOINTS[key[0]]
        if model["endpoint"] != key[0] or model["species"] != key[1] or model["unit"] != definition[0]:
            raise ValueError("Model endpoint/unit/species mismatch.")
        if (definition[1] is None and key[1] is not None) or (definition[1] == "Human" and key[1] != "Human") or (definition[1] == "species" and key[1] not in {"Human", "Rat"}):
            raise ValueError("Unsupported endpoint species.")
        if validation:
            evidence = validation['endpoints'][item['path']]
            if evidence['unit'] != model['unit']:
                raise ValueError('ADME validation endpoint units mismatch.')
            model = dict(model, validation={'report_sha256': validation_sha, 'manifest_sha256': manifest_sha,
                'overall': evidence['overall'], 'by_domain': evidence['by_domain'], 'scope': evidence['scope'], 'unit': evidence['unit']})
        result.append((model, item["sha256"]))
    return result, manifest_sha


def applicability(model, properties, fp, identity):
    from rdkit import DataStructs
    domain = model["domain"]
    fps = [DataStructs.CreateFromBitString(bits) for bits in domain["training_fingerprints"]]
    if not fps or len(fps) > 20000 or any(f.GetNumBits() != 512 for f in fps):
        raise ValueError("Invalid model applicability references.")
    nearest = max(DataStructs.BulkTanimotoSimilarity(fp, fps))
    outside = [name for name, bounds in domain["descriptor_ranges"].items() if not bounds[0] <= properties[name] <= bounds[1]]
    threshold = domain["similarity_threshold"]
    if not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError("Invalid domain threshold.")
    status = "out_of_domain" if outside or nearest < threshold else ("weakly_supported" if nearest < min(1, threshold + .1) else "in_domain")
    return {"status": status, "nearest_training_tanimoto": nearest, "threshold": threshold,
        "outside_descriptor_ranges": outside, "training_graph_seen": identity["graph_sha256"] in domain["training_graph_hashes"],
        "method": "Calibration 10th-percentile nearest Morgan Tanimoto plus training descriptor ranges; heuristic, not probability"}


def prediction(model, model_sha, identity, properties, features, fp, species, timestamp, request_sha):
    if model["species"] not in {None, species}:
        raise ValueError("Species-specific model cannot be transferred.")
    value = forest_value(model, features)
    width = model["calibration"]["absolute_error_quantile"]
    if not math.isfinite(width) or width <= 0:
        raise ValueError("Missing/invalid endpoint uncertainty calibration.")
    endpoint = model["endpoint"]
    lo, hi = value - width, value + width
    applicability_result = applicability(model, properties, fp, identity)
    provenance = {"model": "Morgan-descriptor random forest", "model_version": VERSION, "model_sha256": model_sha,
        "validation": model.get('validation', {'status': 'not_available', 'reason': 'No pinned held-out numerical validation report configured'}),
        "training": model["training"], "calibration": model["calibration"], "molecular_input_sha256": identity["graph_sha256"],
        "prediction_request_sha256": request_sha, "prediction_timestamp": timestamp, "endpoint": endpoint,
        "endpoint_definition": ENDPOINTS[endpoint][2], "species": model["species"], "applicability": applicability_result,
        "model_space_value": value, "model_space_interval": [lo, hi], "model_space_unit": model["unit"], "translation": None}
    unit = model["unit"]
    if endpoint == "fraction_unbound":
        # Restrict only to the physical range; disclose saturation, never invent zero binding precision.
        upper = min(0, hi)
        provenance["translation"] = {"formula": "fu=10**log10_fu; upper interval clipped to physical fu<=1", "input_unit": unit, "output_unit": "fraction", "upper_clipped": hi > 0}
        value, lo, hi, unit = min(1, 10 ** value), 10 ** min(0, lo), 10 ** upper, "fraction"
    elif endpoint == "aqueous_solubility":
        mw = properties["MolWt"]
        provenance["translation"] = {"formula": "mg/l=10**log10_mol_per_l * molecular_weight_g_per_mol * 1000", "molecular_weight": mw, "input_unit": unit, "output_unit": "mg/l", "reference_ph": None,
            "quantity_kind": "heterogeneous_aqueous_solubility",
            "intrinsic_solubility_established": False, "pbpk_native_eligible": False,
            "reason": "Unit conversion does not establish intrinsic solubility or apparent solubility at a known pH"}
        value, lo, hi = tuple(10 ** v * mw * 1000 for v in (value, lo, hi))
        unit = "mg/l"
    return PBPKParameter(value=value, unit=unit, classification="predicted", source=model["training"]["source_url"],
        method="Endpoint-specific CPU random forest; frozen training/calibration split", interval=(lo, hi), prediction=provenance,
        uncertainty="90% marginal split-conformal interval in model log space; not guaranteed under domain shift or jointly across endpoints")


def missing_parameter(unit, reason):
    return PBPKParameter(unit=unit, classification="missing", source="No suitable verified endpoint evidence", method=reason)


def merge_parameter(existing, proposed):
    if existing is None or PRECEDENCE[proposed.classification] > PRECEDENCE[existing.classification]:
        return proposed, None
    conflict = None
    if existing.value is not None and proposed.value is not None and not math.isclose(existing.value, proposed.value, rel_tol=1e-8, abs_tol=1e-12):
        conflict = {"retained": existing.model_dump(mode="json"), "alternative": proposed.model_dump(mode="json"),
            "reason": "Existing evidence has equal/higher declared precedence; no automatic replacement", "units_compatible": existing.unit == proposed.unit}
    return existing, conflict


def reconcile_dossiers(primary, secondary):
    """Combine available evidence before prediction; don't hide catalogue measurements."""
    if (primary.inchikey, primary.species) != (secondary.inchikey, secondary.species):
        raise ValueError('Cannot combine dossiers across molecular identity/species.')
    parameters = dict(primary.parameters)
    endpoints = dict(primary.adme_parameters)
    conflicts = [*primary.parameter_conflicts, *secondary.parameter_conflicts]
    for destination, other in ((parameters, secondary.parameters), (endpoints, secondary.adme_parameters)):
        for name, parameter in other.items():
            existing = destination.get(name)
            if existing and existing.unit != parameter.unit:
                raise ValueError('Conflicting dossier endpoint units require review: ' + name)
            selected, conflict = merge_parameter(existing, parameter)
            if selected is parameter and existing is not None and existing.value is not None and parameter.value != existing.value:
                conflict = {'parameter': name, 'retained': parameter.model_dump(mode='json'), 'alternative': existing.model_dump(mode='json'), 'reason': 'Higher evidence precedence retained; other source preserved'}
            destination[name] = selected
            if conflict: conflicts.append(dict(conflict, parameter=name))
    updates = {'parameters': parameters, 'adme_parameters': endpoints, 'parameter_conflicts': tuple(conflicts),
        'limitations': tuple(dict.fromkeys((*primary.limitations, *secondary.limitations)))}
    if primary.ionization_status == 'missing' and secondary.ionization_status == 'reviewed':
        updates.update(ionization=secondary.ionization, ionization_status='reviewed', ionization_source=secondary.ionization_source)
    elif primary.ionization_status == secondary.ionization_status == 'reviewed' and primary.ionization != secondary.ionization:
        raise ValueError('Conflicting reviewed ionization models require scientist clarification.')
    return primary.model_copy(update=updates)


def predict_dossiers(identity, species_list, supplied=(), *, manifest=None, timestamp=None):
    graph, properties, features, fp = molecular_features(identity["smiles"])
    if graph["inchikey"] != identity["inchikey"]:
        raise ValueError("ADME graph identity mismatch.")
    models, manifest_sha = load_models(manifest)
    timestamp = timestamp or datetime.now(timezone.utc).isoformat()
    datetime.fromisoformat(timestamp)  # Reject malformed replay timestamps.
    request = {"identity": graph, "species": list(species_list), "manifest_sha256": manifest_sha, "version": VERSION,
        "translation_policy": TRANSLATION_POLICY}
    request_sha = digest(canonical(request))
    dossiers = []
    for species in species_list:
        matches = [d for d in supplied if d.species == species]
        if len(matches) > 1:
            raise ValueError("Ambiguous supplied species dossier.")
        base = matches[0] if matches else None
        if base and base.inchikey != graph["inchikey"]:
            raise ValueError("Supplied dossier and predicted graph differ.")
        parameters = dict(base.parameters) if base else {}
        endpoints = dict(base.adme_parameters) if base else {}
        conflicts = list(base.parameter_conflicts) if base else []
        calculated = {"molecular_weight": (properties["MolWt"], "g/mol", "RDKit graph atomic-weight sum"),
                      "logp": (properties["MolLogP"], "Log Units", "RDKit Wildman–Crippen calculated logP, not measured logD")}
        for key, (value, unit, method) in calculated.items():
            parameters[key], conflict = merge_parameter(parameters.get(key), PBPKParameter(value=value, unit=unit, classification="calculated", source="RDKit " + graph["rdkit_version"], method=method))
            if conflict: conflicts.append(dict(conflict, parameter=key))
        for model, sha in models:
            if model["species"] not in {None, species}:
                continue
            endpoint = model["endpoint"]
            proposed = prediction(model, sha, graph, properties, features, fp, species, timestamp, request_sha)
            endpoints[endpoint], conflict = merge_parameter(endpoints.get(endpoint), proposed)
            if conflict: conflicts.append(dict(conflict, parameter=endpoint))
            # A separately supplied pH must never retroactively label a prediction
            # trained on mixed/unknown-pH observations. Keep that endpoint display-only.
            key = {"fraction_unbound": "fraction_unbound"}.get(endpoint)
            if key:
                parameters[key], conflict = merge_parameter(parameters.get(key), proposed)
                if conflict: conflicts.append(dict(conflict, parameter=key))
        for key, unit, reason in (
            ("fraction_unbound", "fraction", "No configured species-matched plasma binding predictor"),
            ("solubility", "mg/l", "No intrinsic or known-reference-pH apparent solubility model; mixed-condition aqueous prediction is display-only"),
            ("solubility_ph", "pH", "Experimental reference pH not established by heterogeneous logS data"),
            ("hepatic_clearance", "ml/min/kg", "Intrinsic hepatocyte clearance is not native plasma clearance: incubation binding, validated IVIVE and blood/plasma conversion are required"),
            ("renal_clearance", "ml/min/kg", "No renal secretion/reabsorption or whole-organ clearance model; no elimination route is assumed"),
            ("reference_weight", "kg", "Only required for evidence normalized to an experimental reference weight; native subject weight is not a missing drug parameter"),
            ("intestinal_permeability", "cm/s", "Caco-2 apparent permeability is not PK-Sim specific intestinal permeability")):
            if key not in parameters:
                parameters[key] = missing_parameter(unit, reason)
        dossiers.append(CompoundDossier(name=identity["name"], smiles=graph["smiles"], inchikey=graph["inchikey"], species=species,
            binding_partner=base.binding_partner if base else "Albumin", parameters=parameters, adme_parameters=endpoints,
            parameter_conflicts=tuple(conflicts), ionization=base.ionization if base else (),
            ionization_source=base.ionization_source if base else "Missing reviewed pKa/protonation evidence; neutral formal charge is insufficient",
            ionization_status=base.ionization_status if base else "missing",
            limitations=(*(base.limitations if base else ()), "Predicted endpoints are exploratory, not measurements or clinical validation.",
                "Applicability and marginal intervals do not encompass structural-model error or guarantee joint PK coverage.",
                "Albumin is a native placeholder only while the dossier is insufficient; binding-partner selection requires reviewed evidence.")))
    return {"schema_version": "pulsate.adme-prediction/v1", "request": request, "request_sha256": request_sha,
        "timestamp": timestamp, "descriptors": properties, "dossiers": [d.model_dump(mode="json") for d in dossiers],
        "native_translation_audit": [native_translation_audit(d) for d in dossiers],
        "status": "predicted" if models else "prediction_unavailable", "classical": True,
        "limitations": ["No automatic calibrated pKa, pH-specific solubility or assay-to-native renal/hepatic clearance translation is established. Native RBC partitioning and physiology must be audited in the instantiated PK-Sim model; neither B/P nor subject weight is a silent drug default."]}


def native_translation_audit(dossier):
    """Auditable endpoint/native boundary, not a fabricated conversion receipt.

    No equation runs without its scientific inputs. PK-Sim's existing mechanisms
    are distinguished from the predictor/assay evidence that would parameterize
    them. This document is covered by deterministic prediction replay.
    """
    def endpoint(name):
        p = dossier.adme_parameters.get(name)
        return p.model_dump(mode="json") if p else None

    clint = endpoint('hepatocyte_intrinsic_clearance')
    reviewed_cl = dossier.parameters.get('hepatic_clearance')
    reviewed_renal = dossier.parameters.get('renal_clearance')
    weight = dossier.parameters.get('reference_weight')
    return {
        'species': dossier.species,
        'scope': 'Endpoint-to-native readiness audit; unresolved entries are not executed numerical translations',
        'ionization': {'status': dossier.ionization_status, 'source': dossier.ionization_source,
            'sites': list(dossier.ionization), 'predicted_sites': [],
            'reason': 'No calibrated site-specific pKa predictor is configured; a neutral formal charge does not establish aqueous speciation'},
        'solubility': {'endpoint': endpoint('aqueous_solubility'),
            'status': 'source_parameter' if dossier.parameters['solubility'].value is not None else 'unresolved',
            'reference_ph': dossier.parameters['solubility_ph'].value,
            'method': 'No Henderson-Hasselbalch calculation without intrinsic solubility, validated ionization and supported solid-state/speciation assumptions',
            'reason': 'Heterogeneous aqueous solubility is neither established intrinsic solubility nor established apparent solubility at a reference pH'},
        'blood_plasma': {'status': 'native_mechanism_not_yet_instantiated',
            'method': 'PK-Sim Standard RBC-composition partitioning with actual native hematocrit, logP, plasma fu and protein partitioning',
            'required_inputs': ['native hematocrit', 'native RBC water/lipid/protein fractions', 'logP', 'species-matched plasma fu', 'native protein partition coefficient'],
            'value': None, 'reason': 'No B/P=1 substitution; actual native model and its partitioning assumptions must be independently audited'},
        'hepatic': {'status': 'declared_native_input' if reviewed_cl and reviewed_cl.value is not None else 'unresolved',
            'endpoint': clint, 'output_clearance_basis': 'plasma',
            'candidate_method': 'Well-stirred linear equilibrium IVIVE; not selected without sufficient evidence',
            'equations': ['CLint_u_ml_min = CLint_apparent_ul_min_per_million * hepatocellularity_million_g * liver_mass_g / (1000 * fu_inc)',
                'fu_blood = fu_plasma / blood_plasma_ratio',
                'CL_blood_ml_min = Q_blood_ml_min * fu_blood * CLint_u_ml_min / (Q_blood_ml_min + fu_blood * CLint_u_ml_min)',
                'CL_plasma_ml_min = CL_blood_ml_min * blood_plasma_ratio'],
            'required_inputs': ['species-matched validated CLint', 'assay incubation binding fu_inc', 'hepatocellularity', 'native subject liver mass', 'native hepatic plus portal blood flow', 'plasma fu', 'native blood/plasma partitioning'],
            'value': reviewed_cl.value if reviewed_cl else None, 'unit': 'ml/min/kg',
            'reason': 'Apparent hepatocyte CLint has no established incubation binding or validated whole-organ translation; in-domain similarity alone is not model-quality validation'},
        'renal': {'status': 'declared_native_input' if reviewed_renal and reviewed_renal.value is not None else 'unresolved',
            'candidate_component': 'glomerular_filtration', 'component_equation': 'CL_filtration_plasma_ml_min = fu_plasma * native_GFR_ml_min',
            'total_clearance_established': bool(reviewed_renal and reviewed_renal.value is not None and reviewed_renal.classification in {'measured', 'sourced'}),
            'unresolved_mechanisms': ['active secretion', 'active reabsorption', 'passive reabsorption'],
            'value': reviewed_renal.value if reviewed_renal else None, 'unit': 'ml/min/kg',
            'reason': 'Filtration is a partial mechanism, not total renal clearance; PK-Sim supports a GFR process but the partial-only model is not qualified for this graph'},
        'reference_weight': {'status': 'experimental_normalization' if weight and weight.value is not None else 'conditional_not_subject_input',
            'value_kg': weight.value if weight else None,
            'method': 'Keep reference weight only for experimental ml/min/kg clearance normalization; never assign it to virtual subject OriginData.Weight',
            'reason': 'Actual subject/population weight, liver size, organ flows and GFR come from native PK-Sim physiology'},
    }


def verify_prediction(document, identity, species, supplied=(), *, manifest=None):
    replay = predict_dossiers(identity, species, supplied, manifest=manifest, timestamp=document["timestamp"])
    if canonical(replay) != canonical(document):
        raise ValueError("ADME model/input/units/domain/translation/provenance replay mismatch.")
    return {"passed": True, "request_sha256": document["request_sha256"], "model_manifest_sha256": document["request"]["manifest_sha256"],
        "scope": "Graph identity, pinned numerical model artifacts, endpoint/species/units, reproducible inference, domain, intervals, exact unit translations and evidence precedence. Not clinical validity.",
        "prediction_source": "deterministic CPU model inference; no language-model-generated numerical parameters"}


def dossier_scenarios(dossier, request):
    """Bounded endpoint interval sensitivity; not a joint uncertainty distribution.

    Vary one numerical native input at a time, fixed reference physiology. Keep
    nominal population variation separate. Additional doses are nominal-only.
    """
    scenarios = [("nominal", "nominal", dossier, request, None)]
    if dossier is not None:
        for name, p in sorted(dossier.parameters.items()):
            if p.classification != 'predicted' or not p.interval or (name == 'solubility' and request.route == 'Intravenous'):
                continue
            if p.prediction['applicability']['status'] != 'in_domain':
                raise ValueError('Out-of-domain prediction cannot seed a native sensitivity scenario.')
            for index, direction in enumerate(('low', 'high')):
                point = p.interval[index]
                if name == 'fraction_unbound' and not 0 < point <= 1:
                    raise ValueError('Unbound interval is outside physical bounds.')
                parameters = dict(dossier.parameters)
                parameters[name] = p.model_copy(update={'value': point})
                varied = dossier.model_copy(update={'parameters': parameters})
                local = request.model_copy(update={'population_size': 1})
                scenarios.append((name + '-' + direction, 'drug_parameter_sensitivity', varied, local,
                    {'parameter': name, 'bound': direction, 'value': point, 'nominal': p.value, 'interval': list(p.interval),
                     'model_sha256': p.prediction['model_sha256'], 'policy': 'One-at-a-time marginal interval boundary; fixed physiology; no joint probability or confidence level'}))
    for index, dose in enumerate(request.dose_sweep[1:], start=2):
        scenarios.append(('dose-' + str(index), 'dose_sweep', dossier, request.model_copy(update={'dose': dose, 'population_size': 1, 'dose_sweep': ()}),
            {'dose': dose, 'unit': request.dose_unit, 'policy': 'Explicit exploratory dose; nominal ADME and fixed reference physiology, not a therapeutic dose'}))
    if len(scenarios) > 16:
        raise ValueError('Native scenario budget exceeded.')
    return scenarios
