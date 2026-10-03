"""Prospective evidence eligibility, separate from numerical/scientific validity.

The cutoff is supplied by a reviewed challenge policy, never inferred from a
candidate's history. Exact reviewed original spans/cells only; no literature
narratives or model-generated measurements enter the numerical evidence chain.
"""
from __future__ import annotations

import json
from datetime import date, datetime, time, timezone
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .virtual_organism import canonical, digest
from .primary_measurements import locate, same_value, normalize_value


POLICY_VERSION = "pulsate.prospective-evidence/v1"
FOUNDATIONAL = {"chemical_identity", "molecular_graph", "protein_structure", "intended_target"}
GENERAL = {"physiology", "protein_annotation", "tissue_expression", "pathway_model"}
CANDIDATE = {"physicochemical", "ionization", "solubility", "binding", "clearance", "metabolism",
    "formulation", "preclinical_adme", "early_pk", "development_dose", "quantitative_activity"}
PROHIBITED = {"clinical_outcome", "clinical_safety", "clinical_efficacy",
    "candidate_physiological_observation", "retrospective_toxicity", "failure_explanation",
    "post_outcome_mechanism", "clinical_narrative"}
SOURCE_PATHS = {
    "pubchem.ncbi.nlm.nih.gov": ("/rest/pug/compound/", "/rest/pug/assay/"),
    "www.ebi.ac.uk": ("/chembl/api/data/", "/biomodels/"),
    "rest.uniprot.org": ("/uniprotkb/",),
    "www.proteinatlas.org": ("/api/search_download.php",),
    "files.rcsb.org": ("/download/",),
    "data.rcsb.org": ("/rest/v1/", "/graphql"),
    "docs.open-systems-pharmacology.org": ("/",),
    "raw.githubusercontent.com": ("/Open-Systems-Pharmacology/", "/biomodels/"),
    # Original publication bytes, never wholesale database ADME annotations.
    "pk-db.com": ("/media/data/",),
    "pmc.ncbi.nlm.nih.gov": ("/articles/",),
}


class ProspectivePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: str = POLICY_VERSION
    cutoff: date | None = None
    cutoff_instant: datetime | None = None
    cutoff_source: str | None = None
    allow_modern_general_knowledge: bool = True
    # No unknown-date candidate measurement or regimen can be used silently.
    candidate_unknown_date: str = "exclude"

    @model_validator(mode="after")
    def valid_policy(self):
        if self.version != POLICY_VERSION or self.candidate_unknown_date != "exclude":
            raise ValueError("Unsupported prospective eligibility policy.")
        if (self.cutoff is not None or self.cutoff_instant is not None) and not self.cutoff_source:
            raise ValueError("A historical cutoff requires reviewed provenance.")
        if self.cutoff_instant is not None:
            if self.cutoff_instant.utcoffset() is None:
                raise ValueError("The historical cutoff instant must be timezone-aware.")
            if self.cutoff is not None and self.cutoff != self.cutoff_instant.astimezone(timezone.utc).date():
                raise ValueError("Historical cutoff day and UTC instant disagree.")
        return self

    def boundary(self) -> datetime | None:
        if self.cutoff_instant is not None:
            return self.cutoff_instant.astimezone(timezone.utc)
        return datetime.combine(self.cutoff, time.max, timezone.utc) if self.cutoff else None


class DatedSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    publication_date: date | None = None
    date_precision: str = "unknown"
    publication_instant: datetime | None = None
    release_date: date | None = None
    release_instant: datetime | None = None

    @model_validator(mode="after")
    def valid_dates(self):
        if self.date_precision not in {"instant", "day", "year", "unknown"}:
            raise ValueError("Unknown date precision.")
        if (self.publication_date is None) != (self.date_precision == "unknown"):
            raise ValueError("Date precision must describe the supplied publication date.")
        if self.date_precision == "year" and (self.publication_date.month, self.publication_date.day) != (12, 31):
            raise ValueError("Year-only dating uses conservative year end, not an invented day.")
        if (self.publication_instant is not None) != (self.date_precision == "instant"):
            raise ValueError("Instant precision requires an exact publication timestamp.")
        for instant, day in ((self.publication_instant, self.publication_date), (self.release_instant, self.release_date)):
            if instant is not None and (instant.utcoffset() is None or day != instant.astimezone(timezone.utc).date()):
                raise ValueError("Source timestamps must be timezone-aware and agree with their UTC date.")
        return self


class OriginalDatumSource(DatedSource):
    """Exact original measurement, not a modern wrapper's citation/transcription."""
    source_url: str = Field(min_length=1)
    source_record: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_kind: str
    reviewer: str = Field(min_length=1)
    review_basis: str = Field(min_length=1)
    value_pointer: tuple[str | int, ...]
    unit_pointer: tuple[str | int, ...] | None = None
    publication_pointer: tuple[str | int, ...]
    publication_value: object
    release_pointer: tuple[str | int, ...] | None = None
    release_value: object = None
    source_format: str = "json"
    original_value: object = None
    original_unit: str | None = None
    unit_value: str | None = None
    unit_review_basis: str | None = None
    subject_inchikey: str | None = None
    semantic_context: dict = Field(default_factory=dict)
    context_pointers: dict[str, tuple[str | int, ...]] = Field(default_factory=dict)
    pdf_reviews: tuple[dict, ...] = ()

    @model_validator(mode="after")
    def valid_original(self):
        if self.source_kind != "original_primary_record":
            raise ValueError("An original measurement trace cannot be a secondary transcription or curated wrapper.")
        if self.publication_date is None:
            raise ValueError("An original measurement requires dated source evidence.")
        if self.source_format not in {'json', 'text', 'html', 'csv', 'tsv', 'pdf'}:
            raise ValueError('Unsupported original-primary format.')
        if self.source_format != 'json' and (self.original_value is None
                or not self.subject_inchikey
                or not all(self.semantic_context.get(k) for k in ('species', 'endpoint'))
                or not {'identity', 'species', 'endpoint'}.issubset(self.context_pointers)):
            raise ValueError('Original prose/table/PDF requires reviewed exact identity/species/endpoint anchors.')
        if self.unit_value is not None and self.unit_value != self.original_unit and not self.unit_review_basis:
            raise ValueError('Nonliteral original unit interpretation requires explicit scientific review.')
        if any(len(pointer) > 20 for pointer in (self.value_pointer, self.publication_pointer,
                self.unit_pointer or (), self.release_pointer or ())):
            raise ValueError("Original source pointer exceeds its bound.")
        if self.date_precision == "year":
            if str(self.publication_value) != str(self.publication_date.year):
                raise ValueError("Original publication year disagrees with its preserved date value.")
        elif self.date_precision == "day":
            if self.publication_value != self.publication_date.isoformat():
                raise ValueError("Original publication date disagrees with its preserved date value.")
        elif not isinstance(self.publication_value, str) or datetime.fromisoformat(
                self.publication_value.replace("Z", "+00:00")) != self.publication_instant:
            raise ValueError("Original publication instant disagrees with its preserved date value.")
        if self.release_date is not None:
            expected = self.release_instant.isoformat() if self.release_instant else self.release_date.isoformat()
            value = self.release_value.replace("Z", "+00:00") if isinstance(self.release_value, str) else None
            if self.release_pointer is None or value != expected:
                raise ValueError("Original release date requires exact preserved release evidence.")
        elif self.release_pointer is not None or self.release_value is not None:
            raise ValueError("Original release evidence must include a qualified release date.")
        canonical(self.publication_value)
        return self


class EvidenceDatum(DatedSource):
    model_config = ConfigDict(extra="forbid", frozen=True)
    identifier: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    source_record: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    category: str
    subject_inchikey: str | None = None
    target_accession: str | None = None
    value: object
    unit: str | None = None
    evidence_type: str
    # Pointer into the preserved scalar/structured source, not an LLM quote.
    value_pointer: tuple[str | int, ...]
    context: dict = Field(default_factory=dict)
    original_source: OriginalDatumSource | None = None
    wrapper_provenance: dict | None = None

    @model_validator(mode="after")
    def valid_datum(self):
        if self.category not in FOUNDATIONAL | GENERAL | CANDIDATE | PROHIBITED:
            raise ValueError("Unrecognized evidence category.")
        if len(self.value_pointer) > 20:
            raise ValueError("Evidence pointer exceeds its bound.")
        canonical(self.value)  # Reject unserializable and nonfinite numerical evidence.
        return self


def allowed_source(url: str) -> bool:
    parsed = urlsplit(url)
    return (parsed.scheme == "https" and not parsed.username and not parsed.password
        and not parsed.port and not parsed.fragment and parsed.hostname in SOURCE_PATHS
        and any(parsed.path.startswith(prefix) for prefix in SOURCE_PATHS[parsed.hostname]))


def _checked_source(url, sha256, payload):
    if not allowed_source(url):
        raise ValueError("Evidence source is outside the configured primary-source allowlist.")
    if len(payload) > 16 * 1024 * 1024 or digest(payload) != sha256:
        raise ValueError("Prospective source hash/size mismatch.")
    return payload


def _source_document(url, sha256, payload):
    return json.loads(_checked_source(url, sha256, payload))


def _at(document, pointer):
    value = document
    for component in pointer:
        if isinstance(component, int) and component < 0:
            raise ValueError("Negative source indices are not evidence pointers.")
        value = value[component]
    return value


def verify_source(datum: EvidenceDatum, payload: bytes, sources=None):
    value = _at(_source_document(datum.source_url, datum.source_sha256, payload), datum.value_pointer)
    if canonical(value) != canonical(datum.value):
        raise ValueError("Datum is not the exact preserved source value.")
    original = datum.original_source
    if original is not None:
        if original.source_sha256 != datum.source_sha256:
            wrapper = datum.wrapper_provenance
            if (not isinstance(wrapper,dict)
                    or wrapper.get('kind') not in {'public_database_wrapper','local_extraction_receipt'}
                    or wrapper.get('not_primary') is not True
                    or wrapper.get('source_sha256')!=datum.source_sha256
                    or wrapper.get('original_source_sha256')!=original.source_sha256
                    or not all(wrapper.get(k) for k in ('record_identifier','reviewer','purpose'))):
                raise ValueError('A separate original source requires explicit non-primary wrapper/extraction provenance.')
            if (wrapper['kind']=='public_database_wrapper' and wrapper.get('source_url')!=datum.source_url
                    or wrapper['kind']=='local_extraction_receipt' and wrapper.get('discovery_url')!=original.source_url):
                raise ValueError('Wrapper origin/discovery URL disagrees with the original source trace.')
        if sources is None or original.source_sha256 not in sources:
            raise ValueError("Original measurement source bytes are missing; a citation is insufficient.")
        raw = _checked_source(original.source_url, original.source_sha256, sources[original.source_sha256])
        def extracted(pointer):
            return locate(raw, original.source_format, pointer,
                pdf_reviews=original.pdf_reviews, sources=sources)
        expected = original.original_value if original.original_value is not None else datum.value
        original_unit = original.original_unit if original.original_unit is not None else datum.unit
        if not same_value(extracted(original.value_pointer), expected):
            raise ValueError("Datum disagrees with the exact original measurement value.")
        if datum.unit is not None and (original.unit_pointer is None
                or extracted(original.unit_pointer) != (original.unit_value or original_unit)):
            raise ValueError("Datum unit disagrees with exact original measurement evidence.")
        if canonical(normalize_value(expected, original_unit, datum.unit)) != canonical(datum.value):
            raise ValueError('Datum is not the dimension-checked normalized original value.')
        if original.subject_inchikey is not None and original.subject_inchikey != datum.subject_inchikey:
            raise ValueError('Original measurement candidate identity mismatch.')
        if any(datum.context.get(k) != v for k,v in original.semantic_context.items()):
            raise ValueError('Original measurement species/endpoint/context mismatch.')
        for pointer in original.context_pointers.values():
            if not extracted(pointer): raise ValueError('Original semantic anchor is missing.')
        if canonical(extracted(original.publication_pointer)) != canonical(original.publication_value):
            raise ValueError("Original publication date is not exact preserved source evidence.")
        if original.release_pointer is not None and canonical(extracted(original.release_pointer)) != canonical(original.release_value):
            raise ValueError("Original release date is not exact preserved source evidence.")


def _historical(source: DatedSource, policy: ProspectivePolicy) -> bool:
    boundary = policy.boundary()
    if boundary is None or source.publication_date is None:
        return False
    # Latest possible availability at the recorded precision. No invented time.
    latest = [source.publication_instant or datetime.combine(source.publication_date, time.max, timezone.utc)]
    if source.release_date is not None:
        latest.append(source.release_instant or datetime.combine(source.release_date, time.max, timezone.utc))
    return all(instant <= boundary for instant in latest)


def eligibility(datum: EvidenceDatum, policy: ProspectivePolicy, candidate_inchikey: str) -> dict:
    """No clinical interpretation and no candidate-specific switches."""
    historical = False
    reason = ""
    status = "excluded"
    if datum.category in PROHIBITED:
        reason = "Outcome-bearing/retrospective evidence is prohibited regardless of date."
    elif not allowed_source(datum.source_url):
        reason = "Source is outside the primary-source allowlist."
    elif datum.evidence_type in {"llm_estimate", "narrative", "docking_potency"}:
        reason = "Narratives, language-model estimates and docking-derived potency are not numerical evidence."
    else:
        direct = datum.subject_inchikey == candidate_inchikey
        historical = _historical(datum.original_source or datum, policy)
        if datum.category in FOUNDATIONAL:
            status = "eligible_foundational"
            reason = "Identity/structure record; historical availability is a separate qualification."
        elif datum.category in GENERAL and datum.subject_inchikey is not None:
            historical = False
            reason = "Compound-specific observations cannot be laundered as general physiology or annotation."
        elif datum.category == "quantitative_activity" and datum.subject_inchikey is None:
            historical = False
            reason = "Anonymous activity cannot establish an unrelated-compound source or a candidate measurement."
        elif datum.category in GENERAL or (datum.category == "quantitative_activity" and not direct):
            if historical:
                status, reason = "eligible_historical", "Source predates the reviewed cutoff."
            elif policy.allow_modern_general_knowledge:
                status, reason = "eligible_modern_general", "General/unrelated evidence permitted, but not claimed historically available."
            else:
                reason = "General knowledge is not historically qualified under this policy."
        elif not direct:
            reason = "Candidate dossier evidence must match the exact molecular identity."
        elif datum.original_source is None:
            historical = False
            reason = "Candidate datum has no exact dated original measurement trace; curated metadata alone is insufficient."
        elif not allowed_source(datum.original_source.source_url):
            historical = False
            reason = "Original measurement is outside the configured primary-source allowlist."
        elif historical:
            status, reason = "eligible_historical", "Exact original candidate measurement is available before the reviewed cutoff; modern wrapper content is not admitted wholesale."
        elif policy.boundary() is None:
            reason = "No reviewed historical cutoff; candidate-specific data are quarantined."
        elif datum.original_source.publication_date is None:
            reason = "Candidate-specific publication date is unknown; excluded, not silently historical."
        else:
            reason = "Original candidate measurement is after the cutoff or its date precision cannot establish availability by the cutoff."
    return {"identifier": datum.identifier, "eligible": status != "excluded", "status": status,
        "historically_qualified": historical, "reason": reason, "policy_version": policy.version,
        "policy_sha256": digest(canonical(policy.model_dump(mode="json"))),
        "datum_sha256": digest(canonical(datum.model_dump(mode="json"))), "source_sha256": datum.source_sha256,
        "original_source_sha256": datum.original_source.source_sha256 if datum.original_source else None,
        "date_qualification_basis": "original_measurement" if datum.original_source else "projected_record_metadata"}


def adjudicate(datums, sources, policy, candidate_inchikey):
    decisions = []
    identifiers = set()
    for datum in datums:
        if datum.identifier in identifiers:
            raise ValueError("Duplicate prospective datum identity.")
        identifiers.add(datum.identifier)
        verify_source(datum, sources[datum.source_sha256], sources)
        decisions.append(eligibility(datum, policy, candidate_inchikey))
    return {"policy": policy.model_dump(mode="json"), "candidate_inchikey": candidate_inchikey,
        "datums": [d.model_dump(mode="json") for d in datums], "decisions": decisions,
        "scope": "Policy eligibility and exact original-byte span/cell/date replay. Source-kind, identity, endpoint, units and dating review are recorded curation, not automatically established by a hash. PDF/OCR requires preserved page-review evidence and dual-parser agreement. Not validation of measurement or biological truth."}


def verify_adjudication(report, sources):
    expected = adjudicate([EvidenceDatum.model_validate(d) for d in report["datums"]], sources,
        ProspectivePolicy.model_validate(report["policy"]), report["candidate_inchikey"])
    if canonical(expected) != canonical(report):
        raise ValueError("Prospective eligibility report failed independent replay.")
    return {"passed": True, "report_sha256": digest(canonical(report)), "scope": expected["scope"]}
