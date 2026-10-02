"""Artifact-composed Virtual Organism capabilities in the existing CGR runtime."""
from __future__ import annotations

import csv
import io
import json
import math
import os
from pathlib import Path
from urllib.parse import quote

from .phase8_scientific_handlers import _NativeRunner
from .scientific_acquisition import _fetch_bytes
from .scientific_runtime import ScientificCapabilityFailure, ScientificCapabilityOutcome, ScientistCapabilityRegistry
from .virtual_organism import (PBPKRequest, CompoundDossier, QuantitativeActivity, NativePBPKEngine, NativePBPKFailure, canonical, configure_snapshot,
    digest, dossier_requirements, dossier_snapshot, exposure_relevance, metrics, parameter_audit, parse_results, verify_native_model, verify_population)


CAPABILITIES = (
    ("pharmacokinetics.adme_predict", (), ("adme_prediction",)),
    ("pharmacokinetics.adme_verify", ("adme_prediction",), ("adme_verified_dossiers",)),
    ("pharmacokinetics.parameterize", ("adme_verified_dossiers",), ("pbpk_parameterization",)),
    ("physiology.organism_construct", ("pbpk_parameterization",), ("pbpk_model_set", "computation_selection_decision")),
    ("pharmacokinetics.pbpk_simulate", ("pbpk_model_set",), ("pbpk_exposure_result",)),
    ("pharmacokinetics.population_simulate", ("pbpk_exposure_result",), ("pbpk_population_result",)),
    ("pharmacokinetics.cross_species_compare", ("pbpk_exposure_result",), ("pbpk_cross_species_result",)),
    ("pharmacokinetics.exposure_verify", ("pbpk_parameterization", "pbpk_model_set", "pbpk_exposure_result", "pbpk_population_result", "pbpk_cross_species_result"), ("scientific_verification_report",)),
    ("discovery.exposure_relevance_assess", ("scientific_verification_report", "pbpk_exposure_result", "pbpk_population_result", "pbpk_cross_species_result"), ("virtual_organism_assessment",)),
)


def load_catalogue():
    configured = os.environ.get("PULSATE_PBPK_CATALOG_FILE")
    if not configured:
        return [], None
    path = Path(configured).resolve(strict=True)
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("PBPK catalogue is too large.")
    document = json.loads(path.read_bytes())
    if document.get("schema_version") != "pulsate.pbpk-catalogue/v1":
        raise ValueError("Unsupported PBPK catalogue schema.")
    entries = document.get("entries", [])
    if not isinstance(entries, list) or len(entries) > 1000:
        raise ValueError("Invalid PBPK catalogue.")
    return entries, path.parent


def read_model(entry, root):
    path = (root / entry["path"]).resolve(strict=True)
    if not path.is_relative_to(root) or path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("PBPK model path must stay inside its configured catalogue directory.")
    payload = path.read_bytes()
    if digest(payload) != entry["sha256"] or not entry.get("source"):
        raise ValueError("PBPK model hash/provenance mismatch.")
    return json.loads(payload), payload


def supplied_candidate(record, objective, store, request):
    """Use scientist/generated graph evidence before public name resolution.

    No public record is required for a genuinely new molecular graph. Species
    ADME still must be supplied explicitly; another compound is never substituted.
    """
    from rdkit import Chem
    by_id = {r.artifact_identifier: r for r in record.artifact_references}
    dossiers, structures = [], []
    for item in objective.input_references:
        ref = by_id[item.artifact_identifier]
        if item.artifact_type == "pbpk_compound_dossier":
            dossier = CompoundDossier.model_validate_json(store.read(ref))
            molecule = Chem.MolFromSmiles(dossier.smiles)
            if molecule is None or Chem.MolToInchiKey(molecule) != dossier.inchikey:
                raise ValueError("Supplied ADME dossier identity fails graph verification.")
            dossiers.append((dossier, ref))
        elif item.artifact_type in {"molecular_structure", "ligand_structure"}:
            payload = store.read(ref)
            if ref.media_type in {"chemical/x-mdl-sdfile", "chemical/x-mdl-molfile"}:
                molecules = list(Chem.ForwardSDMolSupplier(io.BytesIO(payload), removeHs=False))
            elif ref.media_type in {"chemical/x-daylight-smiles", "text/plain"}:
                molecules = [Chem.MolFromSmiles(payload.decode().strip().split()[0])]
            else:
                raise ValueError("PBPK candidate evidence currently supports SDF, MOL or SMILES molecular graphs.")
            if len(molecules) != 1 or molecules[0] is None:
                raise ValueError("PBPK needs one unambiguous candidate molecular graph.")
            structures.append((molecules[0], ref))
    keys = {d.inchikey for d,_ in dossiers} | {Chem.MolToInchiKey(m) for m,_ in structures}
    if len(keys) > 1:
        raise ValueError("Supplied PBPK graph and ADME dossiers describe different candidates.")
    if not keys:
        return None, dossiers, ()
    if structures:
        molecule = structures[0][0]
    else:
        molecule = Chem.MolFromSmiles(dossiers[0][0].smiles)
    identity = {"name": request.entity_name, "inchikey": next(iter(keys)), "smiles": Chem.MolToSmiles(Chem.RemoveHs(molecule)),
                "pubchem_cid": None, "source_url": None, "identity_method": "Independent RDKit verification of scientist-supplied/generated molecular graph"}
    return identity, dossiers, tuple(r for _,r in (*dossiers, *structures))


def resolve_candidate(record, objective, runner, request, fetch, capability, invocation):
    """One identity path shared by predictions and existing parameterization."""
    from rdkit import Chem
    identity, supplied, refs = supplied_candidate(record, objective, runner.store, request)
    if identity is not None:
        return identity, supplied, refs
    url = "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/" + quote(request.entity_name, safe="") + "/property/IsomericSMILES,InChIKey,MolecularWeight/JSON"
    payload = fetch(url, "application/json", 128 * 1024)
    rows = json.loads(payload).get("PropertyTable", {}).get("Properties", [])
    if len(rows) != 1:
        raise ValueError("Candidate identity is not unique; provide an exact identity.")
    row = rows[0]
    smiles = row.get("SMILES") or row.get("IsomericSMILES")
    molecule = Chem.MolFromSmiles(smiles)
    if molecule is None or Chem.MolToInchiKey(molecule) != row["InChIKey"]:
        raise ValueError("Public identity fails independent graph check.")
    identity = {"name": request.entity_name, "inchikey": row["InChIKey"], "smiles": smiles, "pubchem_cid": row["CID"], "source_url": url}
    ref = runner.write_bytes(artifact_type="pbpk_identity_source", media_type="application/json", payload=payload,
        producer=capability, execution_identifier=invocation.invocation_identifier, metadata={"source_url": url, "coordinate_source": "none"})
    return identity, supplied, (ref,)


def empirical_quantile(values, fraction):
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    low, high = math.floor(index), math.ceil(index)
    return ordered[low] + (ordered[high] - ordered[low]) * (index - low)


def population_summary(runs):
    result = []
    for run in runs:
        if run.get('scenario_kind', 'nominal') != 'nominal':
            continue
        groups = {}
        for series in run["result"]["series"]:
            groups.setdefault(series["path"], []).append(series)
        for path, items in groups.items():
            times = items[0]["times_h"]
            if any(s["times_h"] != times for s in items):
                raise ValueError("Population time grids differ.")
            result.append({"species": run["species"], "path": path, "organ": items[0]["organ"],
                "compartment": items[0]["compartment"], "times_h": times, "subject_count": len(items),
                "median_umol_l": [empirical_quantile([s["values_umol_l"][i] for s in items], .5) for i in range(len(times))],
                "p05_umol_l": [empirical_quantile([s["values_umol_l"][i] for s in items], .05) for i in range(len(times))],
                "p95_umol_l": [empirical_quantile([s["values_umol_l"][i] for s in items], .95) for i in range(len(times))]})
    return {"series": result, "interval_scope": "Empirical 5th–95th percentile of the bounded simulated subjects; not a clinical confidence interval. Drug-parameter uncertainty is not sampled."}


def cross_species_summary(runs):
    result = []
    for run in runs:
        if run.get('scenario_kind', 'nominal') != 'nominal':
            continue
        plasma = [s for s in run["result"]["series"] if s["compartment"] == "Plasma (Peripheral Venous Blood)"]
        tissue = [s for s in run["result"]["series"] if s["compartment"] == "Tissue"]
        for s in plasma:
            auc = s["metrics"]["auc_0_t_umol_h_l"]
            result.append({"species": run["species"], "subject_identifier": s["subject_identifier"], "plasma_metrics": s["metrics"],
                "tissue_to_plasma_auc_ratios": {t["organ"]: t["metrics"]["auc_0_t_umol_h_l"]/auc for t in tissue if auc > 0 and t["subject_identifier"] == s["subject_identifier"]}})
    return {"subjects": result, "comparison_scope": "Same explicit administration scenario, separately rebuilt species physiology and separately sourced species ADME. Rat results are not human safety predictions."}


def parameter_sensitivity_summary(runs, request=None):
    items = []
    for run in runs:
        kind = run.get('scenario_kind', 'nominal')
        if kind == 'nominal':
            continue
        items.append({'species': run['species'], 'scenario_identifier': run['scenario_identifier'], 'scenario_kind': kind,
            'scenario_policy': run['scenario_policy'], 'series': run['result']['series'], 'file_artifacts': run['file_artifacts']})
    dose_analysis = []
    if request:
        for species in request.species:
            points = []
            for run in runs:
                if run['species'] != species or run.get('scenario_kind', 'nominal') not in {'nominal','dose_sweep'} or run['result']['subject_count'] != 1:
                    continue
                dose = request.dose if run.get('scenario_kind', 'nominal') == 'nominal' else run['scenario_policy']['dose']
                plasma = next(s for s in run['result']['series'] if s['compartment'] == 'Plasma (Peripheral Venous Blood)')
                tissues = [s for s in run['result']['series'] if s['compartment'] == 'Tissue']
                points.append({'dose': dose, 'dose_unit': request.dose_unit, 'scenario_identifier': run['scenario_identifier'],
                    'cmax_per_dose': plasma['metrics']['cmax_umol_l'] / dose,
                    'auc_per_dose': plasma['metrics']['auc_0_t_umol_h_l'] / dose,
                    'tissue_auc_ranking': [s['path'] for s in sorted(tissues,key=lambda s:s['metrics']['auc_0_t_umol_h_l'],reverse=True)]})
            if len(points) > 1:
                dose_analysis.append({'species':species, 'points':points,
                    'dose_normalized_cmax_max_min_ratio':max(p['cmax_per_dose'] for p in points)/min(p['cmax_per_dose'] for p in points),
                    'dose_normalized_auc_max_min_ratio':max(p['auc_per_dose'] for p in points)/min(p['auc_per_dose'] for p in points),
                    'tissue_order_changes': any(p['tissue_auc_ranking'] != points[0]['tissue_auc_ranking'] for p in points[1:]),
                    'scope':'Compare dose-normalized exposure under fixed nominal inputs; approximate equality supports proportionality only within this model and tested dose range. Not clinical-dose selection.'})
    return {'scenarios': items, 'dose_analysis': dose_analysis, 'scope': 'Drug-parameter interval sensitivity and explicitly requested dose scenarios, separate from physiological population variability. These are NOT joint PK prediction/confidence intervals; endpoint covariance, blood binding, ionization and structural-model uncertainty are not modeled.'}


def native_scenarios(source, request):
    from .adme_prediction import dossier_scenarios
    entry, species = source['entry'], source['species']
    local = request.model_copy(update={'population_size': request.population_size if species == 'Human' else 1})
    dossier = CompoundDossier.model_validate(source['document']) if entry['kind'] == 'dossier' else None
    result = []
    for identifier, kind, varied, scenario_request, receipt in dossier_scenarios(dossier, local):
        snapshot = dossier_snapshot(varied, scenario_request) if varied else configure_snapshot(source['document'], entry['simulation'], scenario_request, species)
        result.append({'scenario_identifier': identifier, 'scenario_kind': kind, 'scenario_policy': receipt,
            'snapshot': snapshot, 'request': scenario_request.model_dump(mode='json')})
    return result


class VirtualOrganismHandler:
    def __init__(self, store, capability, provider=None, fetch=_fetch_bytes):
        self.runner = _NativeRunner(store)
        self.capability, self.provider, self.fetch = capability, provider, fetch

    def read(self, record, kind):
        matches = [r for r in record.artifact_references if r.artifact_type == kind]
        if len(matches) != 1:
            raise ValueError("Missing or ambiguous PBPK prerequisite: " + kind)
        return json.loads(self.runner.store.read(matches[0])), matches[0]

    def write(self, document, kind, invocation, parents=()):
        return self.runner.write_json(artifact_type=kind, payload=canonical(document), producer=self.capability,
            execution_identifier=invocation.invocation_identifier, parents=tuple(parents))

    def execute(self, *, invocation, objective, record):
        try:
            return self._execute(invocation, objective, record)
        except (ValueError, KeyError, StopIteration, OSError) as error:
            raise ScientificCapabilityFailure("virtual_organism_evidence_invalid", str(error)[:2000]) from error

    def _execute(self, invocation, objective, record):
        request = objective.research_requirements.pbpk_request
        if request is None or request.missing():
            raise ValueError("Explicit dosing/time controls are required before a PBPK experiment.")
        name = self.capability
        if name == "pharmacokinetics.adme_predict":
            from .adme_prediction import predict_dossiers, reconcile_dossiers
            identity, supplied, identity_refs = resolve_candidate(record, objective, self.runner, request, self.fetch, name, invocation)
            bases = [d for d,_ in supplied]
            entries, root = load_catalogue()
            source_refs = list(identity_refs)
            for species in request.species:
                matches = [e for e in entries if e['inchikey'] == identity['inchikey'] and e['species'] == species and e['kind'] == 'dossier']
                if len(matches) == 1:
                    document, payload = read_model(matches[0], root)
                    reviewed = CompoundDossier.model_validate(document)
                    indices = [i for i,d in enumerate(bases) if d.species == species]
                    if len(indices) > 1:
                        raise ValueError('Ambiguous supplied species dossier.')
                    if indices:
                        bases[indices[0]] = reconcile_dossiers(bases[indices[0]], reviewed)
                    else:
                        bases.append(reviewed)
                    source_refs.append(self.runner.write_json(artifact_type='adme_reviewed_source', payload=payload, producer=name,
                        execution_identifier=invocation.invocation_identifier, parents=identity_refs, metadata={'source': matches[0]['source']}))
            result = predict_dossiers(identity, request.species, bases)
            ref = self.write({'identity': identity, 'base_dossiers': [d.model_dump(mode='json') for d in bases], 'prediction': result}, 'adme_prediction', invocation, source_refs)
            return ScientificCapabilityOutcome(output_artifacts=(ref,), evidence_artifacts=tuple(source_refs))
        if name == "pharmacokinetics.adme_verify":
            from .adme_prediction import verify_prediction, reconcile_dossiers
            document, parent = self.read(record, 'adme_prediction')
            bases = [CompoundDossier.model_validate(d) for d in document['base_dossiers']]
            # Independently reconstruct scientific inputs, not just self-consistent JSON.
            supplied_identity, original, _ = supplied_candidate(record, objective, self.runner.store, request)
            if supplied_identity is not None and supplied_identity != document['identity']:
                raise ValueError('ADME identity differs from original graph evidence.')
            expected_bases = [d for d,_ in original]
            entries, root = load_catalogue()
            for species in request.species:
                matches = [e for e in entries if e['inchikey'] == document['identity']['inchikey'] and e['species'] == species and e['kind'] == 'dossier']
                if len(matches) == 1:
                    source, _ = read_model(matches[0], root)
                    reviewed = CompoundDossier.model_validate(source)
                    indices = [i for i,d in enumerate(expected_bases) if d.species == species]
                    if len(indices) > 1:
                        raise ValueError('Ambiguous supplied species dossier.')
                    if indices:
                        expected_bases[indices[0]] = reconcile_dossiers(expected_bases[indices[0]], reviewed)
                    else:
                        expected_bases.append(reviewed)
            if canonical([d.model_dump(mode='json') for d in expected_bases]) != canonical(document['base_dossiers']):
                raise ValueError('ADME base dossier is not the supplied/reviewed source evidence.')
            if digest(self.runner.store.read(parent)) != parent.content_sha256:
                raise ValueError('ADME prediction artifact integrity mismatch.')
            report = verify_prediction(document['prediction'], document['identity'], request.species, bases)
            ref = self.write(dict(document, verification=report), 'adme_verified_dossiers', invocation, (parent,))
            return ScientificCapabilityOutcome(output_artifacts=(ref,), evidence_artifacts=(ref,), verified=True)
        if name == "pharmacokinetics.parameterize":
            from rdkit import Chem
            identity, supplied, identity_refs = supplied_candidate(record, objective, self.runner.store, request)
            if identity is None:
                url = "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/" + quote(request.entity_name, safe="") + "/property/IsomericSMILES,InChIKey,MolecularWeight/JSON"
                payload = self.fetch(url, "application/json", 128 * 1024)
                rows = json.loads(payload).get("PropertyTable", {}).get("Properties", [])
                if len(rows) != 1:
                    raise ValueError("Candidate identity is not unique; provide an exact identity.")
                row = rows[0]
                smiles = row.get("SMILES") or row.get("IsomericSMILES")
                molecule = Chem.MolFromSmiles(smiles)
                if molecule is None or Chem.MolToInchiKey(molecule) != row["InChIKey"]:
                    raise ValueError("Public identity fails independent graph check.")
                identity = {"name": request.entity_name, "inchikey": row["InChIKey"], "smiles": smiles, "pubchem_cid": row["CID"], "source_url": url}
                source_ref = self.runner.write_bytes(artifact_type="pbpk_identity_source", media_type="application/json", payload=payload,
                    producer=name, execution_identifier=invocation.invocation_identifier, metadata={"source_url": url, "coordinate_source": "none"})
                identity_refs = (source_ref,)
            # Numeric prediction is a separate, mandatory replay-verified capability.
            adme_refs = [r for r in record.artifact_references if r.artifact_type == 'adme_verified_dossiers']
            adme = None
            if adme_refs:
                adme, adme_ref = self.read(record, 'adme_verified_dossiers')
                if not adme['verification']['passed'] or adme['identity']['inchikey'] != identity['inchikey']:
                    raise ValueError('Unverified or mismatched ADME dossier.')
                supplied_species = {d.species for d,_ in supplied}
                supplied = [(CompoundDossier.model_validate(d), adme_ref) for d in adme['prediction']['dossiers']]
                identity_refs = (*identity_refs, adme_ref)
            else:
                supplied_species = {d.species for d,_ in supplied}
            entries, root = load_catalogue()
            models, missing, model_refs = [], [], []
            for species in request.species:
                supplied_matches = [(d,r) for d,r in supplied if d.species == species]
                selected = [e for e in entries if e["inchikey"] == identity["inchikey"] and e["species"] == species]
                if species not in supplied_species and len(selected) == 1 and selected[0]['kind'] == 'snapshot':
                    # A reviewed complete native model wins over speculative endpoints.
                    supplied_matches = []
                if supplied_matches:
                    if len(supplied_matches) != 1:
                        raise ValueError("Multiple supplied ADME dossiers for one species require clarification.")
                    dossier, dossier_ref = supplied_matches[0]
                    document = dossier.model_dump(mode="json")
                    source_payload = canonical(document)
                    entry = {"inchikey": dossier.inchikey, "species": species, "kind": "dossier", "source": "Scientist-supplied dossier " + dossier_ref.artifact_identifier,
                             "sha256": digest(source_payload), "limitations": list(dossier.limitations)}
                    selected = [entry]
                if len(selected) != 1:
                    missing.append({"species": species, "reason": "No unique reviewed identity-matched species ADME/model dossier. Measured binding, clearance and route-specific absorption evidence are needed; molecular descriptors alone do not supply them."})
                    continue
                entry = selected[0]
                if not supplied_matches:
                    document, source_payload = read_model(entry, root)
                if entry["kind"] == "dossier":
                    dossier = CompoundDossier.model_validate(document)
                    if dossier.inchikey != identity["inchikey"] or dossier.species != species:
                        raise ValueError("Dossier candidate/species mismatch.")
                    absent = dossier_requirements(dossier, request.route)
                    if absent:
                        missing.append({"species": species, "reason": "Insufficient parameterization: " + ", ".join(absent)})
                        continue
                    audit = [dict(p.model_dump(), name=k) for k,p in dossier.parameters.items()]
                    audit.extend({"name": "pKa " + p["Type"], "value": p["Pka"], "unit": "pKa", "classification": "sourced", "source": dossier.ionization_source,
                                  "method": "Declared ionization evidence; not estimated from docking", "uncertainty": None} for p in dossier.ionization)
                elif entry["kind"] == "snapshot":
                    # Full graph identity in the reviewed manifest is independently recomputed.
                    reference_molecule = Chem.MolFromSmiles(entry["smiles"])
                    if reference_molecule is None or Chem.MolToInchiKey(reference_molecule) != identity["inchikey"]:
                        raise ValueError("Reference model identity mismatch.")
                    audit = parameter_audit(document["Compounds"], entry["source"])
                else:
                    raise ValueError("Unsupported model dossier kind.")
                model_ref = self.runner.write_json(artifact_type="pbpk_source_model", payload=source_payload,
                    producer=name, execution_identifier=invocation.invocation_identifier, parents=identity_refs,
                    metadata={"source": entry["source"], "species": species, "inchikey": identity["inchikey"]})
                model_refs.append(model_ref)
                models.append({"entry": entry, "document": document, "source_artifact_identifier": model_ref.artifact_identifier,
                    "parameters": audit, "species": species})
            document = {"schema_version": "pulsate.pbpk-parameterization/v1", "request": request.model_dump(mode="json"),
                "identity": identity,
                "adme": adme,
                "models": models, "missing": missing, "status": "parameterized" if len(models) == len(request.species) else "insufficient_parameterization"}
            ref = self.write(document, "pbpk_parameterization", invocation, (*identity_refs, *model_refs))
            return ScientificCapabilityOutcome(output_artifacts=(ref,), evidence_artifacts=(*identity_refs, *model_refs),
                limitations=("Molecular identity/descriptor evidence is not an ADME measurement.",))
        if name == "physiology.organism_construct":
            parameterization, parent = self.read(record, "pbpk_parameterization")
            models = []
            for item in parameterization["models"]:
                entry, species = item["entry"], item["species"]
                scenarios = native_scenarios(item, request)
                snapshot = scenarios[0]['snapshot']
                local_request = PBPKRequest.model_validate(scenarios[0]['request'])
                origin = snapshot['Individuals'][0]['OriginData']
                policy = [{"name": "Reference physiology", "value": None, "unit": "native physiology building block", "classification": "assumed",
                    "source": "Pinned PK-Sim species database; exact OriginData preserved in input snapshot and executable PKML",
                    "method": "Reference individual: " + json.dumps(origin, sort_keys=True) + "; population replaces this reference with separately recorded sampled subjects when requested.", "uncertainty": None},
                    {"name": "Dosing and sampling policy", "value": None, "unit": "scenario", "classification": "assumed",
                     "source": "Scientist literal controls and disclosed bounded sampling defaults", "method": json.dumps(local_request.model_dump(mode='json'), sort_keys=True), "uncertainty": None}]
                for scenario in scenarios:
                    models.append({**scenario, "species": species,
                        "candidate_native_name": scenario['snapshot']["Simulations"][0]["Compounds"][0]["Name"],
                        "parameters": item["parameters"], "reference_policy": policy,
                        "source": entry["source"], "limitations": entry.get("limitations", [])})
            decision = {"selected_compute": "classical", "quantum_selected": False, "reason": "PBPK uses classical mechanistic differential equations; no electronic subproblem is requested."}
            ref = self.write({"identity": parameterization["identity"], "models": models, "missing": parameterization["missing"], "status": parameterization["status"], "adme": parameterization.get('adme')}, "pbpk_model_set", invocation, (parent,))
            selected = self.write(decision, "computation_selection_decision", invocation, (parent,))
            return ScientificCapabilityOutcome(output_artifacts=(ref, selected))
        if name == "pharmacokinetics.pbpk_simulate":
            model_set, parent = self.read(record, "pbpk_model_set")
            runs, artifacts, refused = [], [], []
            if model_set["models"]:
                executable = os.environ.get("PULSATE_PBPK_EXECUTABLE")
                if not executable:
                    raise ValueError("Native PBPK executable is not configured; no exposure is computed.")
                engine = NativePBPKEngine(Path(executable))
                for item in model_set["models"]:
                    failure = None
                    try:
                        result, files = engine.run(item["snapshot"], PBPKRequest.model_validate(item["request"]), item["candidate_native_name"])
                    except NativePBPKFailure as error:
                        failure, files = str(error), error.files
                    file_refs = {}
                    for file_name, payload in files.items():
                        artifact = self.runner.write_bytes(artifact_type="pbpk_native_evidence", payload=payload,
                            media_type="text/csv" if file_name.endswith(".csv") else "application/octet-stream",
                            producer=name, execution_identifier=invocation.invocation_identifier, parents=(parent,),
                            metadata={"species": item["species"], "scenario_identifier": item['scenario_identifier'], "file_name": file_name})
                        artifacts.append(artifact); file_refs[file_name] = artifact.artifact_identifier
                    if failure:
                        refused.append({"species": item["species"], "scenario_identifier": item['scenario_identifier'], "reason": failure, "file_artifacts": file_refs})
                    else:
                        runs.append({"species": item["species"], "scenario_identifier": item['scenario_identifier'], 'scenario_kind': item['scenario_kind'],
                            'scenario_policy': item['scenario_policy'], "result": result, "file_artifacts": file_refs})
            ref = self.write({"runs": runs, "refused": refused, "missing": [*model_set["missing"], *({"species": r["species"], "reason": "Native calculation refused: " + r["reason"]} for r in refused)], "status": "insufficient_parameterization" if refused else model_set["status"]}, "pbpk_exposure_result", invocation, (parent, *artifacts))
            return ScientificCapabilityOutcome(output_artifacts=(ref,), evidence_artifacts=tuple(artifacts))
        if name in {"pharmacokinetics.population_simulate", "pharmacokinetics.cross_species_compare"}:
            exposure, parent = self.read(record, "pbpk_exposure_result")
            document = population_summary(exposure["runs"]) if name.endswith("population_simulate") else cross_species_summary(exposure["runs"])
            kind = "pbpk_population_result" if name.endswith("population_simulate") else "pbpk_cross_species_result"
            ref = self.write(document, kind, invocation, (parent,))
            return ScientificCapabilityOutcome(output_artifacts=(ref,))
        if name == "pharmacokinetics.exposure_verify":
            from rdkit import Chem
            parameterization, parameter_ref = self.read(record, "pbpk_parameterization")
            model_set, model_ref = self.read(record, "pbpk_model_set")
            exposure, exposure_ref = self.read(record, "pbpk_exposure_result")
            population, pop_ref = self.read(record, "pbpk_population_result")
            comparison, comparison_ref = self.read(record, "pbpk_cross_species_result")
            by_id = {r.artifact_identifier: r for r in record.artifact_references}
            molecular_graph = Chem.MolFromSmiles(parameterization['identity']['smiles'])
            if molecular_graph is None or Chem.MolToInchiKey(molecular_graph) != parameterization['identity']['inchikey'] or model_set['identity'] != parameterization['identity']:
                raise ValueError("PBPK candidate identity failed independent verification.")
            # Reconstruct ALL required scenarios from the verified source dossiers
            # and original controls, not merely the subset a builder happened to emit.
            required_models = [(source['species'], scenario['scenario_identifier'])
                for source in parameterization['models'] for scenario in native_scenarios(source, request)]
            built_models = [(model['species'], model['scenario_identifier']) for model in model_set['models']]
            if len(set(built_models)) != len(built_models) or sorted(required_models) != sorted(built_models):
                raise ValueError('Native scenario construction omitted/duplicated a required dose or parameter-uncertainty point.')
            for model in model_set['models']:
                source = next(m for m in parameterization['models'] if m['species'] == model['species'])
                source_bytes = self.runner.store.read(by_id[source['source_artifact_identifier']])
                if digest(source_bytes) != source['entry']['sha256']:
                    raise ValueError("Reviewed source model evidence hash mismatch.")
                local_request = PBPKRequest.model_validate(model['request'])
                scenarios = native_scenarios(source, request)
                expected_scenario = next(s for s in scenarios if s['scenario_identifier'] == model['scenario_identifier'])
                expected = expected_scenario['snapshot']
                if any(model[key] != expected_scenario[key] for key in ('request', 'scenario_kind', 'scenario_policy')):
                    raise ValueError('Drug uncertainty/dose scenario receipt mismatch.')
                if canonical(expected) != canonical(model['snapshot']):
                    raise ValueError("Executable experiment is not the declared parameterization and scenario.")
            expected_ids = [(m['species'], m['scenario_identifier']) for m in model_set['models']]
            actual_ids = [(r['species'], r['scenario_identifier']) for r in (*exposure['runs'], *exposure.get('refused', []))]
            if len(set(actual_ids)) != len(actual_ids) or sorted(actual_ids) != sorted(expected_ids):
                raise ValueError("Requested model/computed species coverage mismatch.")
            for refusal in exposure.get("refused", []):
                evidence = {name: self.runner.store.read(by_id[identifier]) for name,identifier in refusal["file_artifacts"].items()}
                receipt = json.loads(evidence['failure-receipt.json'])
                model = next(m for m in model_set['models'] if m['species'] == refusal['species'] and m['scenario_identifier'] == refusal['scenario_identifier'])
                if receipt['status'] != 'refused' or receipt['input_sha256'] != digest(canonical(model['snapshot'])) or receipt['reason'] != refusal['reason']:
                    raise ValueError("Failed native experiment receipt mismatch.")
                if any(digest(evidence[name]) != sha for name,sha in receipt['files'].items()):
                    raise ValueError("Failed native experiment artifact integrity mismatch.")
            for run in exposure["runs"]:
                model = next(m for m in model_set["models"] if m["species"] == run["species"] and m['scenario_identifier'] == run['scenario_identifier'])
                if run['scenario_kind'] != model['scenario_kind'] or run['scenario_policy'] != model['scenario_policy']:
                    raise ValueError('Computed drug-uncertainty policy mismatch.')
                receipt = run["result"]["receipt"]
                for file_name, expected_hash in receipt["files"].items():
                    ref = by_id[run["file_artifacts"][file_name]]
                    payload = self.runner.store.read(ref)
                    if digest(payload) != expected_hash or ref.content_sha256 != expected_hash:
                        raise ValueError("Native artifact integrity failure.")
                raw = self.runner.store.read(by_id[run["file_artifacts"][receipt["results_file"]]])
                recomputed = parse_results(raw, model["candidate_native_name"], PBPKRequest.model_validate(model["request"]))
                if canonical(recomputed["series"]) != canonical(run["result"]["series"]):
                    raise ValueError("PK metrics/curves do not match raw solver evidence.")
                if digest(canonical(model["snapshot"])) != receipt["input_sha256"] or receipt["quantum_selected"]:
                    raise ValueError("Model input/computation receipt mismatch.")
                native_model_name = next(n for n in run["file_artifacts"] if n.endswith(".pkml"))
                local_request = PBPKRequest.model_validate(model["request"])
                native_check = verify_native_model(self.runner.store.read(by_id[run["file_artifacts"][native_model_name]]), local_request, model["species"], model["snapshot"])
                if native_check != run["result"]["native_model_verification"]:
                    raise ValueError("Exported native physiology/dosing verification mismatch.")
                if local_request.population_size > 1:
                    population_file = next(n for n in run["file_artifacts"] if n.endswith("-Population.csv"))
                    physiology = verify_population(self.runner.store.read(by_id[run["file_artifacts"][population_file]]), local_request)
                    if physiology != run["result"]["population_physiology"]:
                        raise ValueError("Sampled population physiology verification mismatch.")
            if population != population_summary(exposure["runs"]) or comparison != cross_species_summary(exposure["runs"]):
                raise ValueError("Derived population/species metrics mismatch.")
            activity_results, activity_refs = [], []
            for item in objective.input_references:
                if item.artifact_type != 'quantitative_activity_evidence':
                    continue
                activity_ref = by_id[item.artifact_identifier]
                activity_bytes = self.runner.store.read(activity_ref)
                if digest(activity_bytes) != activity_ref.content_sha256:
                    raise ValueError('Quantitative activity evidence integrity mismatch.')
                activity = QuantitativeActivity.model_validate_json(activity_bytes)
                if activity.inchikey != model_set['identity']['inchikey']:
                    raise ValueError('Quantitative activity belongs to another candidate.')
                # The curves have just been independently reconstructed from
                # native CSV. Match species and an exact unbound compartment;
                # persist these checked ratios for the downstream explanation.
                species_series = [s for r in exposure['runs'] if r['species'] == activity.species and r.get('scenario_kind', 'nominal') == 'nominal' for s in r['result']['series']]
                activity_results.append(exposure_relevance(species_series, activity.model_dump(mode='json')))
                activity_refs.append(activity_ref)
            report = {"passed": True, "verification_scope": "Computational integrity, identity-matched species model, request controls, candidate coverage, native file hashes, time/unit/finite nonnegative output and independently recomputed metrics. Not biological validity or safety.",
                "computed_species": [r["species"] for r in exposure["runs"]], "missing": exposure["missing"], "clinical_safety_established": False,
                "activity_comparisons": activity_results}
            ref = self.write(report, "scientific_verification_report", invocation, (parameter_ref, model_ref, exposure_ref, pop_ref, comparison_ref, *activity_refs))
            return ScientificCapabilityOutcome(output_artifacts=(ref,), evidence_artifacts=(ref,), verified=True)
        if name == "discovery.exposure_relevance_assess":
            exposure, exposure_ref = self.read(record, "pbpk_exposure_result")
            models, model_ref = self.read(record, "pbpk_model_set")
            population, pop_ref = self.read(record, "pbpk_population_result")
            comparison, comparison_ref = self.read(record, "pbpk_cross_species_result")
            verification, check_ref = self.read(record, "scientific_verification_report")
            assumptions = ["Healthy adult European ICRP physiology is the initial human model; not a specific patient.",
                "Species are built separately. Rat database physiology does not distinguish sex or age-dependent maturation.",
                "Rat population sampling is not qualified in this adapter; each rat computation represents one independently built reference individual.",
                "Population age/sex sampling and organ/body variation use PK-Sim's database and a fixed seed. Drug-parameter interval sensitivity is separate, bounded and one-at-a-time; no joint probability, custom disease or genotype is claimed.",
                "Total tissue concentrations are not intracellular free concentrations or proof of engagement.",
                "Ordinary PBPK remains classical. Quantum infrastructure is unchanged and no IBM job was submitted."]
            assumptions.extend(m['species'] + ': ' + policy['method'] for m in models['models'] if m['scenario_kind'] == 'nominal' for policy in m.get('reference_policy', []))
            parameters = [dict(p, species=m["species"]) for m in models["models"] if m['scenario_kind'] == 'nominal' for p in m["parameters"]]
            quality = {label: sum(p["classification"] == label for p in parameters) for label in ("measured", "sourced", "calculated", "predicted", "estimated", "assumed", "missing")}
            quality["missing_species_dossiers"] = len(exposure["missing"])
            activity_results = verification.get('activity_comparisons', [])
            assessment = {"schema_version": "pulsate.virtual-organism/v1", "candidate": models["identity"], "request": request.model_dump(mode="json"),
                "status": "exposure_supported" if exposure["runs"] and not exposure["missing"] else "insufficient_parameterization",
                "runs": [r for r in exposure["runs"] if r['scenario_kind'] == 'nominal'], "refused": exposure.get("refused", []), "population": population, "comparison": comparison, "missing": exposure["missing"], "parameters": parameters,
                "drug_parameter_uncertainty": parameter_sensitivity_summary(exposure['runs'], request),
                "evidence_quality": quality, "activity_comparisons": activity_results,
                "adme_parameterization": models.get('adme'),
                "exposure_relevance": {"status": "prioritization_hypothesis" if any(a["status"] == "prioritization_hypothesis" for a in activity_results) else "insufficient_evidence", "reason": "Sourced quantitative assays require matching unbound species-compartment exposure. Computed ratios, when available, prioritize follow-up; they do not establish occupancy, function or safety."} if activity_results else exposure_relevance([], None),
                "assumptions": assumptions, "verification": verification,
                "scenario_policy": [{"species": m['species'], 'scenario_identifier': m['scenario_identifier'], "records": m.get('scenario_policy')} for m in models['models']],
                "limitations": ["A converged PBPK solver does not establish biological truth, efficacy, adverse effects or human safety.",
                    "Blood-cell binding and partition/permeability predictions use declared PK-Sim methods; tissues without measured partition data are model estimates.",
                    "No terminal half-life, systemic clearance or bioavailability is inferred from a finite sampled curve.",
                    *[limitation for m in models["models"] for limitation in m["limitations"]]],
                "computation_selection": {"selected_compute": "classical", "quantum_selected": False, "reason": "PBPK is classical mechanistic ODE simulation, not an electronic structure problem."}}
            ref = self.write(assessment, "virtual_organism_assessment", invocation, (exposure_ref, model_ref, pop_ref, comparison_ref, check_ref))
            summary = f"Virtual Organism: {assessment['status'].replace('_', ' ')}; {len(exposure['runs'])} species computation(s). This is exposure evidence, not a safety assessment."
            return ScientificCapabilityOutcome(output_artifacts=(ref,), scientific_summary=summary, limitations=tuple(assessment["limitations"]))
        raise ValueError("Unknown virtual organism capability.")


def virtual_organism_registry(store):
    return ScientistCapabilityRegistry({name: VirtualOrganismHandler(store, name) for name,_,_ in CAPABILITIES})
