"""Headless native PBPK boundary; no replacement ODEs or compound-name routing.

Model data are reviewed configuration, selected by full molecular identity and
species. New structures need an ADME dossier, not substitution of another drug's
parameters. This module never interprets a docking score as quantitative activity.
"""
from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import math
import os
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def canonical(document) -> bytes:
    return json.dumps(document, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode()


def literal_numbers(quote: str) -> list[float]:
    numbers = [float(n) for n in re.findall(r"(?<![\w.])\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", quote)]
    words = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty")
    numbers.extend(float(i) for i, word in enumerate(words) if re.search(r"(?<!\w)" + word + r"(?!\w)", quote, re.I))
    return numbers


class PBPKRequest(BaseModel):
    """Literal scientist controls; no inferred therapeutic dose or ADME."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    entity_name: str = Field(min_length=1, max_length=128)
    species: tuple[Literal["Human", "Rat"], ...] = ("Human",)
    dose: float | None = Field(default=None, gt=0, le=1e6, allow_inf_nan=False)
    dose_unit: Literal["mg", "mg/kg"] | None = None
    route: Literal["Intravenous", "Oral"] | None = None
    duration_h: float | None = Field(default=None, gt=0, le=720, allow_inf_nan=False)
    infusion_minutes: float = Field(default=0, ge=0, le=1440, allow_inf_nan=False)
    administration_times_h: tuple[float, ...] = (0.0,)
    formulation: Literal["Solution"] | None = None
    population_size: int = Field(default=1, ge=1, le=32)
    age_min_years: float = Field(default=20, ge=18, le=80)
    age_max_years: float = Field(default=50, ge=18, le=80)
    proportion_female: int = Field(default=50, ge=0, le=100)
    seed: int = Field(default=29, ge=1, le=2147483647)
    quotes: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def domain(self):
        if len(set(self.species)) != len(self.species) or not self.species:
            raise ValueError("Species must be unique and nonempty.")
        if self.age_max_years < self.age_min_years:
            raise ValueError("Population age range is reversed.")
        times = self.administration_times_h
        if not 1 <= len(times) <= 32 or any(not math.isfinite(t) or t < 0 for t in times):
            raise ValueError("Administration times must be finite and nonnegative.")
        if tuple(sorted(set(times))) != times:
            raise ValueError("Administration times must be strictly increasing.")
        if self.duration_h is not None and times[-1] >= self.duration_h:
            raise ValueError("Administration must precede the simulation endpoint.")
        if self.route == "Oral" and self.infusion_minutes:
            raise ValueError("Infusion duration is not an oral administration control.")
        return self

    def missing(self) -> tuple[str, ...]:
        missing = [label for key, label in (("dose", "an explicit research dose"),
                    ("dose_unit", "dose units (mg or mg/kg)"), ("route", "oral or intravenous route"),
                    ("duration_h", "simulation duration")) if getattr(self, key) is None]
        if self.route == "Oral" and self.formulation is None:
            missing.append("formulation (currently an explicitly declared solution is supported)")
        if 'Rat' in self.species and self.population_size > 1:
            scope = self.quotes.get('population_size', '').lower()
            if self.species == ('Rat',) or 'human' not in scope:
                missing.append("population scope: rat population sampling is not qualified; explicitly request a human population with a single reference rat, or a single rat only")
        return tuple(missing)

    def check_grounding(self, text: str):
        if self.entity_name not in text or any(not quote or quote not in text for quote in self.quotes.values()):
            raise ValueError("PBPK controls require literal scientist evidence.")
        for key in ("dose", "dose_unit", "route", "duration_h"):
            value = getattr(self, key)
            if value is not None and key not in self.quotes:
                raise ValueError(f"Missing literal support for {key}.")
        if self.dose_unit and re.findall(r"(?<![a-z])mg(?:/kg)?(?![a-z/])", self.quotes["dose_unit"].lower().replace(" ", "")) != [self.dose_unit]:
            raise ValueError("Dose unit is not literal.")
        if self.route:
            aliases = {"Intravenous": ("intravenous", "iv", "i.v."), "Oral": ("oral", "orally", "per os")}
            if not any(re.search(r"(?<!\w)" + re.escape(a) + r"(?!\w)", self.quotes["route"], re.I) for a in aliases[self.route]):
                raise ValueError("Route is not supported by the literal quote.")
        if self.species != ("Human",) and ("species" not in self.quotes or any(s.lower() not in self.quotes["species"].lower() for s in self.species)):
            raise ValueError("Nondefault species require literal evidence.")
        for key in ("dose", "duration_h", "infusion_minutes", "population_size", "age_min_years", "age_max_years", "proportion_female", "seed"):
            if key in self.quotes:
                numbers = literal_numbers(self.quotes[key])
                if key == 'infusion_minutes' and re.search(r'\bbolus\b', self.quotes[key], re.I):
                    numbers.append(0.0)  # The scientific definition of a bolus, not a guessed duration.
                value = getattr(self, key)
                # Duration may be a literal number of minutes, converted to hours.
                if not any(math.isclose(value, n) or (key == "duration_h" and "min" in self.quotes[key].lower() and math.isclose(value * 60, n)) for n in numbers):
                    raise ValueError(f"Unquoted numeric control {key}.")
        if self.infusion_minutes and "infusion_minutes" not in self.quotes:
            raise ValueError("Infusion duration must be explicit.")
        if self.population_size > 1 and "population_size" not in self.quotes:
            raise ValueError("Population size must be explicit.")
        if self.administration_times_h != (0.0,) and "administration_times_h" not in self.quotes:
            raise ValueError("Repeated administration requires explicit times.")
        for key in ("age_min_years", "age_max_years", "proportion_female", "seed", "formulation"):
            if getattr(self, key) != type(self).model_fields[key].default and key not in self.quotes:
                raise ValueError(f"Nondefault {key} requires explicit scientist evidence.")
        if self.formulation and "solution" not in self.quotes["formulation"].lower():
            raise ValueError("Solution formulation must be explicit.")
        if self.administration_times_h != (0.0,):
            numbers = [float(n) for n in re.findall(r"(?<![\w.])\d+(?:\.\d+)?", self.quotes["administration_times_h"])]
            if not all(any(math.isclose(t, n) for n in numbers) for t in self.administration_times_h):
                raise ValueError("Each repeated dose time must be literal, in hours.")
        return self


def interpret_pbpk_request(text, provider) -> PBPKRequest | None:
    """Provider extracts intent only; a deterministic gate rejects invented controls."""
    try:
        messages = [
            {"role": "system", "content": (
                "You are a literal extraction component, not a scientific answerer. Extract PBPK exposure controls as JSON. "
                "entity_name must be an exact input substring. species is an array of Human/Rat; default Human. "
                "dose, dose_unit (mg or mg/kg), route (Intravenous or Oral), duration_h must be null if absent. "
                "Convert a duration in minutes to hours. Do not choose a dose. "
                "Simulation duration is distinct from infusion duration. An intravenous bolus has infusion_minutes=0. "
                "Do not convert the observation horizon into an infusion. Solution formulation is only extracted when explicitly requested. "
                "Optional infusion_minutes, administration_times_h (explicit list of dose times in hours), formulation (Solution only), "
                "population_size, age_min_years, age_max_years, proportion_female, seed may be supplied ONLY if explicit. "
                "Population sampling is qualified for humans only; population_size is the requested human count. "
                "Do not silently replace a requested rat population by one rat. Quote the species scope with a population count when both species are requested. "
                "quotes must map EACH supplied control to an exact substring supporting it. No ADME or pharmacological values. "
                "Use digits in numeric quotes. Omit unspecified optional fields. proportion_female is an integer percentage, not a fraction. "
                "When not specified the disclosed policy defaults are population_size=1, age_min_years=20, age_max_years=50, "
                "proportion_female=50, seed=29, infusion_minutes=0, administration_times_h=[0]. Do not invent other settings. "
                "The quotes object is mandatory. For example dose and dose_unit need the exact dose phrase; route needs the route word; "
                "duration_h needs the duration phrase. Each explicitly supplied optional field also needs its exact quote." )},
            {"role": "user", "content": text},
        ]
        schema = PBPKRequest.model_json_schema()
        schema['required'] = ['entity_name', 'species', 'dose', 'dose_unit', 'route', 'duration_h', 'quotes']
        schema['properties']['quotes'] = {'type': 'object', 'properties': {key: {'type': 'string'} for key in PBPKRequest.model_fields if key != 'quotes'},
            'required': ['entity_name', 'dose', 'dose_unit', 'route', 'duration_h'], 'additionalProperties': False,
            'description': 'Exact original-input substrings for each extracted value. Empty only when the corresponding optional control is absent/null.'}
        for attempt in range(2):
            complete = getattr(provider, "complete_structured", None)
            raw = complete(messages, schema) if complete else provider.complete(messages)
            try:
                result = PBPKRequest.model_validate_json(raw)
                if result.entity_name not in text:
                    literal = list(re.finditer(re.escape(result.entity_name), text, re.I))
                    if len(literal) == 1:
                        result = result.model_copy(update={'entity_name': literal[0].group()})
                quotes = dict(result.quotes)
                for key, quote in tuple(quotes.items()):
                    if key in PBPKRequest.model_fields and getattr(result, key) is None:
                        del quotes[key]
                        continue
                    if quote in text:
                        continue
                    matches = list(re.finditer(re.escape(quote), text, re.I)) if quote else []
                    spellings = {match.group() for match in matches}
                    if len(spellings) == 1:
                        # Repeated occurrences of the same literal spelling are
                        # not ambiguous evidence. Never choose between distinct
                        # case-sensitive spellings or repair different words.
                        quotes[key] = spellings.pop()
                    elif key not in {"entity_name", "dose", "dose_unit", "route", "duration_h"} and key in PBPKRequest.model_fields and getattr(result, key) == PBPKRequest.model_fields[key].default:
                        # An unquoted policy default is NOT scientist evidence. Strip
                        # the model's invented quote, retaining the disclosed default.
                        del quotes[key]
                if result.dose_unit is not None and 'dose_unit' not in quotes and result.dose_unit in quotes.get('dose', ''):
                    quotes['dose_unit'] = quotes['dose']
                if result.species != ('Human',) and (not quotes.get('species') or quotes['species'] not in text):
                    spans = [list(re.finditer(r'(?<!\w)' + re.escape(s) + r'(?!\w)', text, re.I)) for s in result.species]
                    if all(len(matches) == 1 for matches in spans):
                        quotes['species'] = text[min(m[0].start() for m in spans):max(m[0].end() for m in spans)]
                updates = {"quotes": quotes}
                if result.route == 'Intravenous' and result.formulation is not None and (not quotes.get('formulation') or quotes['formulation'] not in text):
                    # The oral formulation contract is not an IV control. Do not
                    # retain a model-invented formulation as a scientific fact.
                    updates['formulation'] = None
                    quotes.pop('formulation', None)
                bolus = list(re.finditer(r'\bbolus\b', text, re.I))
                if result.route == 'Intravenous' and len(bolus) == 1 and not re.search(r'\binfus\w*\b', text, re.I):
                    # Explicit symbolic administration controls beat an LLM's
                    # conflation of observation horizon and infusion duration.
                    updates['infusion_minutes'] = 0.0
                    quotes['infusion_minutes'] = bolus[0].group()
                result = result.model_copy(update=updates)
                return result.check_grounding(text)
            except (ValueError, TypeError, KeyError) as error:
                if attempt:
                    return None
                messages.append({"role": "user", "content":
                    "Repair the extraction JSON only. Validation rejected it: " + str(error)[:1200] +
                    ". Include literal quotes from the original input. Unspecified optional values must be omitted or use the stated defaults. Never fill missing dose, route or duration."})
    except (ValueError, TypeError, KeyError, RuntimeError):
        return None


class PBPKParameter(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    value: float | None = Field(default=None, allow_inf_nan=False)
    unit: str
    classification: Literal["measured", "sourced", "calculated", "estimated", "assumed", "missing"]
    source: str = Field(min_length=1)
    method: str = Field(min_length=1)
    uncertainty: str | None = None

    @model_validator(mode="after")
    def missing_is_explicit(self):
        if (self.value is None) != (self.classification == "missing"):
            raise ValueError("Missing parameter values require missing classification.")
        return self


class CompoundDossier(BaseModel):
    """Species-specific ADME, usable for any matching molecular graph."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str
    smiles: str
    inchikey: str
    species: Literal["Human", "Rat"]
    binding_partner: Literal["Albumin", "Alpha1AcidGlycoprotein"]
    parameters: dict[str, PBPKParameter]
    ionization: tuple[dict, ...]  # Type/Pka/ValueOrigin; empty means explicitly neutral
    ionization_source: str = Field(min_length=1)
    limitations: tuple[str, ...] = ()


class QuantitativeActivity(BaseModel):
    """An assay is evidence, not a potency estimate converted from docking."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    inchikey: str
    species: Literal["Human", "Rat"]
    target: str = Field(min_length=1)
    kind: Literal["Ki", "Kd", "IC50", "EC50"]
    value: float = Field(gt=0, allow_inf_nan=False)
    unit: Literal["umol/l"]
    source: str = Field(min_length=1)
    assay_context: str = Field(min_length=1)
    compartment_path: str = Field(min_length=1)
    compatibility_limitations: str = Field(min_length=1)


_PARAM_UNITS = {
    "molecular_weight": "g/mol", "logp": "Log Units", "fraction_unbound": "fraction",
    "solubility": "mg/l", "solubility_ph": "pH", "hepatic_clearance": "ml/min/kg",
    "renal_clearance": "ml/min/kg", "reference_weight": "kg", "intestinal_permeability": "cm/s",
}


def dossier_requirements(dossier: CompoundDossier, route: str) -> list[str]:
    required = set(_PARAM_UNITS) - {"intestinal_permeability"}
    if route == "Oral":
        required.add("intestinal_permeability")
    missing = [key for key in sorted(required) if key not in dossier.parameters or dossier.parameters[key].value is None]
    for key, parameter in dossier.parameters.items():
        if key not in _PARAM_UNITS or parameter.unit != _PARAM_UNITS[key]:
            raise ValueError("Unsupported PBPK parameter or unit: " + key)
        if parameter.value is not None and key != "logp" and parameter.value < 0:
            raise ValueError("Negative PBPK parameter: " + key)
    fu = dossier.parameters.get("fraction_unbound")
    if fu and fu.value is not None and not 0 < fu.value <= 1:
        raise ValueError("Unbound fraction must be in (0, 1].")
    ph = dossier.parameters.get("solubility_ph")
    if ph and ph.value is not None and not 0 <= ph.value <= 14:
        raise ValueError("Reference pH is outside the supported aqueous range.")
    for item in dossier.ionization:
        if set(item) - {"Type", "Pka", "ValueOrigin"} or item.get("Type") not in {"Acid", "Base"} or not math.isfinite(item.get("Pka", math.nan)):
            raise ValueError("Unsupported ionization evidence.")
    return missing


def dossier_snapshot(dossier: CompoundDossier, request: PBPKRequest) -> dict:
    """Build native species physiology, never mutate human physiology into rat."""
    missing = dossier_requirements(dossier, request.route)
    if missing:
        raise ValueError("Insufficient parameterization: " + ", ".join(missing))
    from rdkit import Chem
    molecule = Chem.MolFromSmiles(dossier.smiles)
    if molecule is None or Chem.MolToInchiKey(molecule) != dossier.inchikey:
        raise ValueError("Dossier identity mismatch.")
    if len(Chem.GetMolFrags(molecule)) != 1:
        raise ValueError("Mixtures/salts require an explicit active-moiety and dose-basis model, not a single-molecule PBPK substitution.")
    from rdkit.Chem import Descriptors
    if not math.isclose(Descriptors.MolWt(molecule), dossier.parameters["molecular_weight"].value, rel_tol=.01):
        raise ValueError("Molecular weight is inconsistent with graph identity.")
    if dossier.parameters["reference_weight"].value <= 0:
        raise ValueError("Reference body weight must be positive.")
    def param(key, name):
        p = dossier.parameters[key]
        return {"Name": name, "Value": p.value,
                **({"Unit": p.unit} if p.unit not in {"fraction", "pH"} else {}),
                "ValueOrigin": {"Source": "Other", "Method": "Unknown",
                    "Description": p.classification + "; " + p.source + "; " + p.method}}
    alternative = lambda key, name: [{"Name": "Evidence", "Parameters": [param(key, name)]}]
    compound = {"Name": dossier.name, "IsSmallMolecule": True,
        "PlasmaProteinBindingPartner": dossier.binding_partner,
        "Parameters": [param("molecular_weight", "Molecular weight")],
        "Lipophilicity": alternative("logp", "Lipophilicity"),
        "FractionUnbound": [{"Name": "Evidence", "Species": dossier.species,
            "Parameters": [param("fraction_unbound", "Fraction unbound (plasma, reference value)")]}],
        "Solubility": [{"Name": "Evidence", "Parameters": [param("solubility", "Solubility at reference pH"), param("solubility_ph", "Reference pH")]}],
        "PkaTypes": list(dossier.ionization), "Processes": [],
        "CalculationMethods": ["Cellular partition coefficient method - PK-Sim Standard", "Cellular permeability - PK-Sim Standard"]}
    processes = []
    for key, native, kind, display in (("hepatic_clearance", "LiverClearance", "Hepatic", "Total Hepatic Clearance"),
                                        ("renal_clearance", "KidneyClearance", "Renal", "Renal Clearances")):
        # A zero remains an explicit input assumption in the dossier, not a hidden default.
        if dossier.parameters[key].value == 0:
            continue
        compound["Processes"].append({"InternalName": native, "Species": dossier.species, "DataSource": "Evidence",
            "Parameters": [param(key, "Plasma clearance"), param("fraction_unbound", "Fraction unbound (experiment)"), param("reference_weight", "Body weight")]})
        processes.append({"Name": display + "-Evidence", "SystemicProcessType": kind})
    if "intestinal_permeability" in dossier.parameters and dossier.parameters["intestinal_permeability"].value is not None:
        compound["IntestinalPermeability"] = alternative("intestinal_permeability", "Specific intestinal permeability (transcellular)")
    origin = ({"Species": "Human", "Population": "European_ICRP_2002", "Gender": "MALE", "Age": {"Value": 30, "Unit": "year(s)"}}
              if dossier.species == "Human" else {"Species": "Rat", "Population": "Rat", "Gender": "UNKNOWN"})
    origin["Weight"] = {"Value": dossier.parameters["reference_weight"].value, "Unit": "kg"}
    snapshot = {"Version": 120, "Name": "Exposure project", "Individuals": [{"Name": "Individual", "Seed": request.seed, "OriginData": origin}],
        "Compounds": [compound], "Simulations": [{"Name": "Exposure", "Model": "4Comp", "Individual": "Individual",
            "Compounds": [{"Name": dossier.name, "Processes": processes, "Protocol": {"Name": "Administration"}}]}]}
    return configure_snapshot(snapshot, "Exposure", request, dossier.species)


def configure_snapshot(source: dict, simulation_name: str, request: PBPKRequest, species: str) -> dict:
    """Select an exact reviewed model, replace only declared experiment controls."""
    snapshot = copy.deepcopy(source)
    matches = [s for s in snapshot.get("Simulations", []) if s["Name"] == simulation_name]
    if len(matches) != 1 or len(matches[0].get("Compounds", [])) != 1:
        raise ValueError("Exactly one single-candidate simulation must be selected.")
    simulation = matches[0]
    individual = next(i for i in snapshot["Individuals"] if i["Name"] == simulation["Individual"])
    if individual["OriginData"]["Species"] != species:
        raise ValueError("Reference model physiology species mismatch.")
    individual["Seed"] = request.seed
    application_type = "IntravenousBolus" if request.route == "Intravenous" and request.infusion_minutes == 0 else request.route
    native_parameters = lambda time: [
        {"Name": "InputDose", "Value": request.dose, "Unit": request.dose_unit},
        {"Name": "Start time", "Value": time, "Unit": "h"},
        *([{"Name": "Infusion time", "Value": request.infusion_minutes, "Unit": "min"}] if application_type == "Intravenous" else []),
    ]
    protocol = {"Name": "Administration", "TimeUnit": "h", "Schemas": [{"Name": f"Dose {i + 1}",
        "Parameters": [{"Name": "Start time", "Value": time, "Unit": "h"}],
        "SchemaItems": [{"Name": "Administration", "ApplicationType": application_type,
            "Parameters": native_parameters(0), **({"FormulationKey": "Solution"} if request.route == "Oral" else {})}]} for i, time in enumerate(request.administration_times_h)]}
    # Simple protocol for one administration uses the established native contract.
    if len(request.administration_times_h) == 1:
        protocol = {"Name": "Administration", "ApplicationType": application_type, "DosingInterval": "Single",
                    "Parameters": native_parameters(request.administration_times_h[0])}
    properties = simulation["Compounds"][0]
    properties["Protocol"] = {"Name": "Administration", **({"Formulations": [{"Name": "Solution", "Key": "Formulation"}]} if request.route == "Oral" else {})}
    if request.route == "Oral":
        snapshot["Formulations"] = [{"Name": "Solution", "FormulationType": "Formulation_Dissolved"}]
    snapshot["Protocols"] = [protocol]
    # Analysis/observation and application-path overrides belong to the original experiment.
    for key in ("IndividualAnalyses", "PopulationAnalyses", "ObservedData", "HasResults"):
        simulation.pop(key, None)
    simulation["Parameters"] = [p for p in simulation.get("Parameters", []) if not p["Path"].startswith(("Applications|", "Events|"))]
    simulation["OutputSchema"] = [{"Parameters": [
        {"Name": "Start time", "Value": 0, "Unit": "h"},
        {"Name": "End time", "Value": request.duration_h, "Unit": "h"},
        {"Name": "Resolution", "Value": 20, "Unit": "pts/h"}]}]
    snapshot["Simulations"] = [simulation]
    snapshot.pop("ParameterIdentifications", None)
    snapshot.pop("SensitivityAnalyses", None)
    if request.population_size > 1:
        if species != "Human":
            raise ValueError("Rat population sampling is not validated; request an individual rat.")
        snapshot["Populations"] = [{"Name": "Virtual population", "Seed": request.seed, "Settings": {
            "NumberOfIndividuals": request.population_size, "ProportionOfFemales": request.proportion_female,
            "Age": {"Min": request.age_min_years, "Max": request.age_max_years, "Unit": "year(s)"}, "Individual": individual}}]
        simulation.pop("Individual")
        simulation["Population"] = "Virtual population"
    else:
        snapshot.pop("Populations", None)
    return snapshot


def parameter_audit(document, source: str, path="") -> list[dict]:
    """Do not re-label an upstream fitted/unknown value as a measurement."""
    result = []
    if isinstance(document, dict):
        if "Value" in document and isinstance(document["Value"], (int, float)):
            origin = document.get("ValueOrigin", {})
            result.append({"path": path, "name": document.get("Name", document.get("Path", path)),
                "value": document["Value"], "unit": document.get("Unit", "native dimensionless or model-base unit"),
                "classification": "sourced" if origin.get("Source") not in (None, "Unknown") else "assumed",
                "source": source, "method": origin.get("Method", "uncharacterized upstream input"), "origin": origin,
                "uncertainty": None})
        if "Pka" in document:
            result.append({"path": path, "name": "pKa", "value": document["Pka"], "unit": "pKa",
                "classification": "sourced" if document.get("ValueOrigin") else "assumed", "source": source,
                "method": "upstream ionization input", "uncertainty": None})
        for key, value in document.items():
            result.extend(parameter_audit(value, source, path + "/" + key))
    elif isinstance(document, list):
        for i, value in enumerate(document):
            result.extend(parameter_audit(value, source, path + f"/{i}"))
    return result


def metrics(times: list[float], values: list[float]) -> dict:
    if len(times) != len(values) or len(times) < 2:
        raise ValueError("Insufficient PK samples.")
    peak = max(values)
    return {"cmax_umol_l": peak, "tmax_h": times[values.index(peak)],
            "auc_0_t_umol_h_l": sum((b-a)*(x+y)/2 for a,b,x,y in zip(times[:-1],times[1:],values[:-1],values[1:],strict=True)),
            "half_life_h": None, "clearance_l_h": None, "bioavailability": None,
            "metric_scope": "Sampled Cmax/Tmax and trapezoidal AUC within the simulated window; no terminal extrapolation."}


def parse_results(payload: bytes, candidate: str, request: PBPKRequest) -> dict:
    """CSV is exported for both individual and population simulations."""
    reader = csv.DictReader(io.StringIO(payload.decode("utf-8-sig")))
    fields = reader.fieldnames or []
    time_col = next((f for f in fields if f.startswith("Time [")), None)
    if time_col not in ("Time [min]", "Time [h]"):
        raise ValueError("Unsupported native time unit.")
    paths = {}
    for field in fields:
        match = re.fullmatch(r"(Organism\|.+) \[([µμu]mol/[lL])\]", field)
        if not match:
            continue
        path = match[1].split("|")
        if candidate not in path:
            continue
        # Only named observable compartments, not a metabolite or enzyme concentration.
        label = path[-1]
        if label in {"Tissue", "Interstitial Unbound", "Intracellular Unbound"} or (path[1] == "PeripheralVenousBlood" and label in {
            "Plasma (Peripheral Venous Blood)", "Plasma Unbound (Peripheral Venous Blood)", "Whole Blood (Peripheral Venous Blood)"}):
            paths[field] = {"organ": path[1], "compartment": label, "path": match[1], "unit": "umol/l"}
    if not any(p["compartment"] == "Plasma (Peripheral Venous Blood)" for p in paths.values()):
        raise ValueError("Candidate plasma output missing.")
    subjects = {}
    for row in reader:
        subject = str(row["IndividualId"])
        time = float(row[time_col]) / (60 if time_col == "Time [min]" else 1)
        values = {col: float(row[col]) for col in paths}
        if not math.isfinite(time) or any(not math.isfinite(v) or v < 0 for v in values.values()):
            raise ValueError("Nonfinite/negative exposure; no curve accepted.")
        point = subjects.setdefault(subject, {"times_h": [], "values": {col: [] for col in paths}})
        point["times_h"].append(time)
        for col, value in values.items():
            point["values"][col].append(value)
    if len(subjects) != request.population_size:
        raise ValueError("Native population coverage mismatch.")
    if not any(max(subject["values"][col]) > 0 for subject in subjects.values() for col, details in paths.items() if details["compartment"] == "Plasma (Peripheral Venous Blood)"):
        raise ValueError("A positive-dose experiment produced no plasma exposure; administration/absorption must be reviewed, not accepted as a successful exposure prediction.")
    series = []
    for identifier, subject in subjects.items():
        times = subject["times_h"]
        if times[0] != 0 or not math.isclose(times[-1], request.duration_h, abs_tol=1e-5) or any(b <= a for a,b in zip(times[:-1],times[1:])):
            raise ValueError("Output time domain inconsistent with the request.")
        for col, values in subject["values"].items():
            series.append(dict(paths[col], subject_identifier=identifier, times_h=times, values_umol_l=values, metrics=metrics(times, values)))
    return {"series": series, "subject_count": len(subjects), "concentration_unit": "umol/l", "time_unit": "h"}


def verify_native_model(payload: bytes, request: PBPKRequest, species: str, snapshot: dict | None = None) -> dict:
    """Verify exported native physiology/dose, not merely requested input JSON."""
    root = ET.fromstring(payload)
    native_species = {e.get("value") for e in root.iter("StringExtendedProperty") if e.get("name") == "Species"}
    if native_species != {species}:
        raise ValueError("Exported native physiology species mismatch.")
    dose_name = "DosePerBodyWeight" if request.dose_unit == "mg/kg" else "Dose"
    doses = [float(e.get("value")) for e in root.iter("Parameter") if e.get("name") == dose_name and e.get("value") is not None]
    if not doses or not all(math.isclose(v, request.dose * 1e-6, rel_tol=1e-7, abs_tol=1e-14) for v in doses):
        raise ValueError("Exported native dose does not match the requested mass units/value.")
    checks = {"species": species, "native_dose_parameter": dose_name, "native_dose_values_kg_or_kg_per_kg": doses,
              "physiology_source": "Pinned PK-Sim species database; complete native parameterisation retained in PKML."}
    if snapshot is None:
        return checks
    applications = [e for e in root.iter("EventGroup") if e.get("containerType") == "Application"]
    expected_route = "IntravenousBolus" if request.route == "Intravenous" and not request.infusion_minutes else request.route
    if len(applications) != len(request.administration_times_h) or any(e.get("eventGroupType") != expected_route for e in applications):
        raise ValueError("Exported native route/administration count mismatch.")
    starts = []
    for app in applications:
        params = {e.get("name"): float(e.get("value")) for e in app.iter("Parameter") if e.get("value") is not None}
        starts.append(params["Start time"] / 60)
        if request.infusion_minutes and not math.isclose(params.get("Infusion time", math.nan), request.infusion_minutes):
            raise ValueError("Exported native infusion duration mismatch.")
    if sorted(starts) != list(request.administration_times_h):
        raise ValueError("Exported native repeated-dose timing mismatch.")
    candidate = snapshot["Simulations"][0]["Compounds"][0]
    compound = next(c for c in snapshot["Compounds"] if c["Name"] == candidate["Name"])
    native_compounds = [e for e in root.iter('Container') if e.get('name') == candidate['Name']]
    molecular_weights = [float(p.get('value')) for c in native_compounds for p in c.findall('./Children/Parameter') if p.get('name') == 'Molecular weight' and p.get('value') is not None]
    source_weight = next(p for p in compound['Parameters'] if p['Name'] == 'Molecular weight')
    if source_weight.get('Unit') != 'g/mol' or not molecular_weights or any(not math.isfinite(v) or not math.isclose(v, source_weight['Value'] * 1e-9, rel_tol=1e-3) for v in molecular_weights):
        raise ValueError('Executable native candidate molecular mass/unit does not match the reviewed graph parameterization.')
    processes = []
    for selection in candidate.get("Processes", []):
        if "SystemicProcessType" not in selection:
            continue  # Reviewed enzyme models retain their full native reaction network.
        transport = selection["SystemicProcessType"] in {"Renal", "GFR", "Biliary"}
        name = selection["Name"] + "-" + candidate["Name"] if transport else candidate["Name"] + "-" + selection["Name"]
        containers = [e for e in root.iter("Transport" if transport else "Container") if e.get("name") == name]
        reactions = [e for e in root.iter("Transport" if transport else "Reaction") if e.get("name") == name]
        if not containers or not reactions:
            raise ValueError("Declared systemic clearance is absent from the executable native reaction network: " + name)
        # Reviewed native display names are the selection contract; compare each
        # source process's experimental inputs after native base-unit conversion.
        display = {"LiverClearance": "Total Hepatic Clearance", "KidneyClearance": "Renal Clearances", "GlomerularFiltration": "Glomerular Filtration"}
        source = next((p for p in compound.get("Processes", []) if display.get(p.get("InternalName"), p.get("InternalName")) + "-" + p.get("DataSource", "") == selection["Name"]), None)
        if source is None:
            raise ValueError("Selected clearance cannot be traced to its source process.")
        native = {e.get("name"): float(e.get("value")) for c in containers for e in c.iter("Parameter") if e.get("value") is not None}
        for parameter in source.get("Parameters", []):
            unit = parameter.get("Unit")
            conversion = {"ml/min/kg": .001, "kg": 1, "l": 1, "l/min": 1, None: 1, "%": .01}.get(unit)
            # Legacy snapshots reinitialise reference physiology on migration to
            # the pinned database. Permit bounded numerical rounding, not ADME drift.
            tolerance = 1e-4 if parameter["Name"] in {"Body weight", "Volume (kidney)", "Blood flow rate (kidney)"} else 1e-7
            if conversion is None or not math.isfinite(native.get(parameter["Name"], math.nan)) or not math.isclose(native[parameter["Name"]], parameter["Value"] * conversion, rel_tol=tolerance, abs_tol=1e-12):
                raise ValueError("Declared clearance parameter failed native value/unit verification: " + parameter["Name"])
        processes.append({"name": name, "parameters_native_units": {p["Name"]: native[p["Name"]] for p in source["Parameters"]}, "reaction_count": len(reactions)})
    checks.update(route=expected_route, administration_times_h=sorted(starts), systemic_processes=processes, native_molecular_weights_kg_per_umol=molecular_weights)
    return checks


def verify_population(payload: bytes, request: PBPKRequest) -> dict:
    rows = list(csv.DictReader(io.StringIO("\n".join(line for line in payload.decode("utf-8-sig").splitlines() if not line.startswith("#")))))
    if len(rows) != request.population_size or len({r["IndividualId"] for r in rows}) != len(rows):
        raise ValueError("Native population physiology coverage mismatch.")
    subjects = []
    for row in rows:
        numeric = {key: float(value) for key, value in row.items() if key not in {"IndividualId", "Gender", "Population", "Population Name"}}
        if any(not math.isfinite(v) for v in numeric.values()):
            raise ValueError("Nonfinite sampled physiology.")
        age = numeric["Organism|Age [year(s)]"]
        if not request.age_min_years <= age <= request.age_max_years or numeric["Organism|Weight [kg]"] <= 0:
            raise ValueError("Sampled age/body mass outside the declared population.")
        subjects.append({"identifier": row["IndividualId"], "sex": row["Gender"], "age_years": age,
            "weight_kg": numeric["Organism|Weight [kg]"], "height_dm": numeric["Organism|Height [dm]"],
            "physiology": numeric, "population": row["Population"]})
    expected_females = request.population_size * request.proportion_female / 100
    if sum(s["sex"] == "FEMALE" for s in subjects) not in {math.floor(expected_females), math.ceil(expected_females)}:
        raise ValueError("Native population sex allocation mismatch.")
    return {"subject_count": len(subjects), "subjects": subjects, "seed": request.seed,
            "scope": "Native sampled physiology, including body size, organ volumes/flows and model-specific biological variation. Drug evidence uncertainty is not sampled."}


class NativePBPKFailure(ValueError):
    """Retain unsuccessful native evidence; never publish it as an exposure."""
    def __init__(self, message, files):
        super().__init__(message)
        self.files = files


class NativePBPKEngine:
    def __init__(self, executable: Path):
        self.executable = executable.resolve(strict=True)
        self.database = self.executable.parent / "PKSimDB.sqlite"
        if not self.database.is_file():
            raise ValueError("Pinned native physiology database is missing.")

    def run(self, snapshot: dict, request: PBPKRequest, candidate: str) -> tuple[dict, dict[str, bytes]]:
        # Never give the CLI a pre-existing evidence/output directory.
        with tempfile.TemporaryDirectory(prefix="pulsate-pbpk-") as tmp:
            root = Path(tmp)
            inp, out = root/"input", root/"output"
            inp.mkdir(); out.mkdir()
            input_bytes = canonical(snapshot)
            (inp/"model.json").write_bytes(input_bytes)
            version_probe = subprocess.run([str(self.executable), "--version"], capture_output=True, text=True, timeout=20, check=False)
            version = (version_probe.stdout + version_probe.stderr).strip()
            # This CLI returns 1 for the informational --version command.
            if not re.fullmatch(r"PKSim\.CLI 12\.3\.173\+[a-f0-9]+", version):
                raise ValueError("This native adapter requires the validated PK-Sim 12.3.173 build.")
            timed_out = False
            try:
                completed = subprocess.run([str(self.executable), "run", "-i", str(inp), "-o", str(out), "--forAll", "-c", "-j", "-k", "-x"],
                    capture_output=True, text=True, timeout=300, check=False)
                log = completed.stdout + completed.stderr
                exit_code = completed.returncode
            except subprocess.TimeoutExpired as error:
                # Preserve partial evidence before TemporaryDirectory removes it.
                # A timeout is a refused experiment, never an accepted curve.
                timed_out = True
                def decode(value):
                    return value.decode(errors='replace') if isinstance(value, bytes) else (value or '')
                log = decode(error.stdout) + decode(error.stderr)
                exit_code = None
            files = {p.name: p.read_bytes() for p in out.iterdir() if p.is_file()}
            files["solver.log"] = log.encode()
            files["input-snapshot.json"] = input_bytes
            def reject(reason):
                files["failure-receipt.json"] = canonical({"engine": "PK-Sim", "version": version, "status": "refused",
                    "reason": reason, "exit_code": exit_code, "input_sha256": digest(input_bytes),
                    "executable_sha256": digest(self.executable.read_bytes()),
                    "physiology_database_sha256": digest(self.database.read_bytes()),
                    "files": {n: digest(p) for n,p in files.items()}, "quantum_selected": False})
                raise NativePBPKFailure(reason, files)
            results = [(n, p) for n, p in files.items() if n.endswith("-Results.csv")]
            if timed_out:
                reject("Native PBPK exceeded the 300-second isolated execution limit; partial evidence retained, no exposure accepted.")
            if exit_code != 0 or len(results) != 1 or not any(n.endswith(".pkml") for n in files) or not any(n.endswith(".xml") for n in files):
                reject("Native PBPK did not export a complete executable model and result; " + log[-1200:])
            if "fail:" in log or "warn:" in log:
                reject("Native model warning/failure requires review before use; " + log[-1600:])
            try:
                result = parse_results(results[0][1], candidate, request)
                if not {"Liver", "Kidney", "Brain", "Heart", "Fat", "Muscle"} <= {s["organ"] for s in result["series"]}:
                    raise ValueError("Required native tissue observable coverage is incomplete.")
                species = snapshot["Individuals"][0]["OriginData"]["Species"]
                result["native_model_verification"] = verify_native_model(next(p for n,p in files.items() if n.endswith(".pkml")), request, species, snapshot)
                if request.population_size > 1:
                    population_files = [p for n,p in files.items() if n.endswith("-Population.csv")]
                    if len(population_files) != 1:
                        raise ValueError("Native population physiology evidence missing.")
                    result["population_physiology"] = verify_population(population_files[0], request)
            except (ValueError, KeyError) as error:
                reject(str(error))
            result["receipt"] = {"engine": "PK-Sim", "version": version, "executable_sha256": digest(self.executable.read_bytes()),
                "physiology_database_sha256": digest(self.database.read_bytes()), "input_sha256": digest(input_bytes),
                "exit_code": exit_code, "files": {n: digest(p) for n,p in files.items()}, "results_file": results[0][0],
                "selected_compute": "classical", "quantum_selected": False,
                "reason": "Mechanistic PBPK is a classical differential-equation computation. No electronic or quantum subproblem is requested."}
            return result, files


def exposure_relevance(series: list[dict], activity: dict | None) -> dict:
    if not activity or activity.get("kind") not in {"Ki", "Kd", "IC50", "EC50"} or activity.get("unit") != "umol/l" or not activity.get("source"):
        return {"status": "insufficient_evidence", "reason": "No sourced quantitative activity for a compatible compartment and assay. Docking scores are not potency, occupancy or functional engagement."}
    if not math.isfinite(activity.get("value", math.nan)) or activity["value"] <= 0:
        raise ValueError("Invalid quantitative activity.")
    relevant = [s for s in series if s["path"] == activity.get("compartment_path") and "Unbound" in s["compartment"]]
    if not relevant:
        return {"status": "insufficient_evidence", "reason": "Matching unbound compartment exposure is not available; total tissue concentration is not free concentration."}
    return {"status": "prioritization_hypothesis", "activity": activity,
        "peak_exposure_to_activity_ratios": [max(s["values_umol_l"])/activity["value"] for s in relevant],
        "reason": "An exposure/activity comparison prioritizes follow-up only. Assay concentration, cellular context and free concentration compatibility require review; no occupancy, functional effect or toxicity was computed."}
