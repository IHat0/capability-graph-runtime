"""Model-backed, reviewable natural-language scientific interpretation."""

from __future__ import annotations

import json
import math
import os
import re
import threading
import unicodedata
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from cgr.quantum_preflight.artifacts import write_json_atomic
from cgr.science import sha256_fingerprint

INTERPRETATION_SCHEMA = "cgr.pulsate-natural-language-interpretation/1.0.0"
APPROVED_EXPERIMENT_SCHEMA = "cgr.pulsate-approved-experiment/1.0.0"
MODEL_DRAFT_SCHEMA = "cgr.pulsate-model-scientific-draft/1.0.0"
MODEL_RESPONSE_MAXIMUM_BYTES = 512 * 1024
MODEL_REQUEST_MAXIMUM_BYTES = 512 * 1024
INTERPRETATION_RECORD_MAXIMUM_BYTES = 2 * 1024 * 1024
_INTERPRETATION_IDENTIFIER = re.compile(r"^interpretation-[0-9a-f]{32}$")
_ELEMENT = re.compile(r"^[A-Z][a-z]?$")
_IMAGE_OR_PATH = re.compile(
    r"^(?:[A-Za-z]:[\\/]|/|\\\\|file://)|(?:^|[\\/])\.\.(?:[\\/]|$)",
    re.IGNORECASE,
)
_CREDENTIAL_ASSIGNMENT = re.compile(
    r"\b(?:api[_-]?key|token|password|credential)\b\s*[:=]", re.IGNORECASE
)
_FORMULA_TOKEN = re.compile(r"([A-Z][a-z]?)([0-9]*)")
_NUMBER_PATTERN = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
_NUMBER_TOKEN = re.compile(rf"(?<![A-Za-z0-9.]){_NUMBER_PATTERN}")
_SUPPORTED_ELEMENT_SYMBOLS = frozenset(
    """
    H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni
    Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe
    Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg
    Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Rf Db Sg
    Bh Hs Mt Ds Rg Cn Nh Fl Mc Lv Ts Og
    """.split()  # noqa: SIM905 - clearer audited periodic table
)
_EXPLICIT_FIELD_PATHS = frozenset(
    {
        "scientific_objective",
        "requested_quantity",
        "molecule.name",
        "molecule.formula",
        "molecule.smiles",
        "molecule.inchi",
        "molecule.atoms",
        "molecule.geometry_description",
        "molecule.bond_lengths",
        "coordinate_unit",
        "charge",
        "multiplicity",
        "basis",
        "electronic_structure_method",
        "active_space",
        "mapper",
        "ansatz",
        "optimizer",
        "tolerance",
        "requested_execution_target",
        "requested_backend",
        "shots",
        "precision",
    }
)
_CONSERVATIVE_ELEMENT_NAME_TOKENS = {
    "H": ("hydrogen", "hydride"),
    "Li": ("lithium",),
}
_COORDINATE_ELEMENT_NAMES = {
    "hydrogen": "H",
    "helium": "He",
    "lithium": "Li",
    "beryllium": "Be",
    "boron": "B",
    "carbon": "C",
    "nitrogen": "N",
    "oxygen": "O",
    "fluorine": "F",
}
_COORDINATE_ELEMENT_PATTERN = "|".join(
    (
        *sorted(_COORDINATE_ELEMENT_NAMES, key=len, reverse=True),
        *sorted(_SUPPORTED_ELEMENT_SYMBOLS, key=len, reverse=True),
    )
)
_ATOM_COORDINATE_SEQUENCE = re.compile(
    rf"""
    (?<![A-Za-z])
    (?P<element>{_COORDINATE_ELEMENT_PATTERN})
    (?![A-Za-z])
    \s*(?:atom\s*)?(?:[:=]\s*)?
    [(\[]\s*
    (?P<x>{_NUMBER_PATTERN})\s*(?:,\s*|\s+)
    (?P<y>{_NUMBER_PATTERN})\s*(?:,\s*|\s+)
    (?P<z>{_NUMBER_PATTERN})\s*
    [)\]]
    """,
    re.IGNORECASE | re.VERBOSE,
)
_OBJECTIVE_GENERIC_TOKENS = frozenset(
    {
        "a",
        "an",
        "and",
        "calculate",
        "compute",
        "determine",
        "electronic",
        "experiment",
        "for",
        "of",
        "prepare",
        "run",
        "study",
        "the",
        "using",
    }
)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class NaturalLanguageUnavailableError(RuntimeError):
    pass


class NaturalLanguageInterpretationError(RuntimeError):
    pass


class InterpretationNotFoundError(LookupError):
    pass


class ApprovalValidationError(ValueError):
    pass


class ProvenancedString(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str | None = Field(default=None, max_length=4096)
    provenance: Literal["explicit", "derived", "assumed", "missing"]

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if (self.value is None) != (self.provenance == "missing"):
            raise ValueError("String value and provenance are inconsistent.")
        if self.value is not None:
            value = self.value.strip()
            if (
                not value
                or _IMAGE_OR_PATH.search(value)
                or _CREDENTIAL_ASSIGNMENT.search(value)
            ):
                raise ValueError("Scientific text is empty or contains unsafe control data.")
            self.value = value
        return self


class ProvenancedInteger(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: int | None = None
    provenance: Literal["explicit", "derived", "assumed", "missing"]

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if (self.value is None) != (self.provenance == "missing"):
            raise ValueError("Integer value and provenance are inconsistent.")
        return self


class ProvenancedPositiveFloat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: float | None = None
    provenance: Literal["explicit", "derived", "assumed", "missing"]

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if (self.value is None) != (self.provenance == "missing"):
            raise ValueError("Numeric value and provenance are inconsistent.")
        if self.value is not None and (
            not math.isfinite(self.value) or self.value <= 0
        ):
            raise ValueError("Scientific numeric values must be positive and finite.")
        return self


class MolecularAtom(BaseModel):
    model_config = ConfigDict(extra="forbid")

    element: str
    coordinates: tuple[float, float, float] | None = None

    @field_validator("element")
    @classmethod
    def valid_element(cls, value: str) -> str:
        if value not in _SUPPORTED_ELEMENT_SYMBOLS:
            raise ValueError("Atom element is not a valid chemical symbol.")
        return value

    @field_validator("coordinates")
    @classmethod
    def finite_coordinates(
        cls, value: tuple[float, float, float] | None
    ) -> tuple[float, float, float] | None:
        if value is not None and not all(math.isfinite(item) for item in value):
            raise ValueError("Atom coordinates must be finite.")
        return value


class ProvenancedAtoms(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: tuple[MolecularAtom, ...] | None = None
    provenance: Literal["explicit", "derived", "assumed", "missing"]

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if (self.value is None) != (self.provenance == "missing"):
            raise ValueError("Atom list and provenance are inconsistent.")
        if self.value is not None and not self.value:
            raise ValueError("An atom list must not be empty.")
        if self.provenance == "assumed":
            raise ValueError("Molecular atoms must not be invented as an assumption.")
        return self


class BondLength(BaseModel):
    model_config = ConfigDict(extra="forbid")

    atom_indices: tuple[int, int]
    value: float
    unit: Literal["angstrom", "bohr"]

    @model_validator(mode="after")
    def valid_bond(self) -> Self:
        first, second = self.atom_indices
        if first < 0 or second < 0 or first == second:
            raise ValueError("Bond atom indices are invalid.")
        if not math.isfinite(self.value) or self.value <= 0:
            raise ValueError("Bond lengths must be positive and finite.")
        return self


class ProvenancedBondLengths(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: tuple[BondLength, ...] | None = None
    provenance: Literal["explicit", "derived", "assumed", "missing"]

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if (self.value is None) != (self.provenance == "missing"):
            raise ValueError("Bond lengths and provenance are inconsistent.")
        if self.value is not None and not self.value:
            raise ValueError("Bond-length evidence must not be empty.")
        if self.provenance == "assumed":
            raise ValueError("Bond lengths must not be invented as assumptions.")
        return self


class MoleculeDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: ProvenancedString
    formula: ProvenancedString
    smiles: ProvenancedString
    inchi: ProvenancedString
    atoms: ProvenancedAtoms
    geometry_description: ProvenancedString
    bond_lengths: ProvenancedBondLengths


class ModelScientificDraft(BaseModel):
    """Strict schema accepted from the language model."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[MODEL_DRAFT_SCHEMA] = MODEL_DRAFT_SCHEMA
    scientific_objective: ProvenancedString
    requested_quantity: ProvenancedString
    molecule: MoleculeDraft
    coordinate_unit: ProvenancedString
    charge: ProvenancedInteger
    multiplicity: ProvenancedInteger
    basis: ProvenancedString
    electronic_structure_method: ProvenancedString
    active_space: ProvenancedString
    mapper: ProvenancedString
    ansatz: ProvenancedString
    optimizer: ProvenancedString
    tolerance: ProvenancedPositiveFloat
    requested_execution_target: ProvenancedString
    requested_backend: ProvenancedString
    shots: ProvenancedInteger
    precision: ProvenancedPositiveFloat
    assumptions: tuple[str, ...] = ()
    missing_required_information: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    explicit_evidence: dict[str, str] = Field(
        default_factory=dict, exclude=True, max_length=len(_EXPLICIT_FIELD_PATHS)
    )

    @field_validator("explicit_evidence")
    @classmethod
    def validate_explicit_evidence(
        cls, value: dict[str, str]
    ) -> dict[str, str]:
        for path, quotation in value.items():
            if path not in _EXPLICIT_FIELD_PATHS:
                raise ValueError("Explicit evidence contains an unsupported field path.")
            if not quotation or len(quotation) > 512:
                raise ValueError("Explicit evidence quotations must be bounded.")
        return value

    @model_validator(mode="after")
    def validate_controlled_values(self) -> Self:
        if self.multiplicity.value is not None and self.multiplicity.value <= 0:
            raise ValueError("Multiplicity must be a positive integer.")
        if self.shots.value is not None and self.shots.value <= 0:
            raise ValueError("Shots must be a positive integer.")
        if (
            self.requested_execution_target.value is not None
            and self.requested_execution_target.value
            not in {"ibm_quantum", "local_simulator"}
        ):
            raise ValueError("Execution target is unsupported.")
        if (
            self.coordinate_unit.value is not None
            and self.coordinate_unit.value not in {"angstrom", "bohr"}
        ):
            raise ValueError("Coordinate unit is unsupported.")
        for value in (*self.assumptions, *self.missing_required_information, *self.warnings):
            if (
                not value
                or len(value) > 1000
                or _IMAGE_OR_PATH.search(value)
                or _CREDENTIAL_ASSIGNMENT.search(value)
            ):
                raise ValueError("Model annotations contain unsafe text.")
        atoms = self.molecule.atoms.value
        bonds = self.molecule.bond_lengths.value
        if atoms is not None and bonds is not None:
            for bond in bonds:
                if max(bond.atom_indices) >= len(atoms):
                    raise ValueError("Bond indices exceed the atom count.")
        if atoms is not None:
            coordinate_count = sum(atom.coordinates is not None for atom in atoms)
            if coordinate_count not in {0, len(atoms)}:
                raise ValueError("Coordinates must be supplied for every atom or none.")
        return self


class ModelProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_kind: Literal["openai_compatible_http", "controlled_test_provider"]
    model_name: str
    prompt_sha256: str
    response_sha256: str
    requested_at: str
    repair_attempted: bool
    request_count_for_interpretation: int = Field(ge=1, le=2)


class InterpretedScientificSpecification(ModelScientificDraft):
    original_question: str = Field(min_length=1, max_length=4096)
    interpretation_status: Literal[
        "ready_for_review", "needs_clarification", "interpretation_failed"
    ]
    execution_support_status: Literal[
        "supported", "requires_compiler_capability", "needs_clarification"
    ]
    model_provenance: ModelProvenance


class InterpretationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[INTERPRETATION_SCHEMA] = INTERPRETATION_SCHEMA
    interpretation_identifier: str
    original_question: str
    specification: InterpretedScientificSpecification
    assumptions: tuple[str, ...]
    missing_required_information: tuple[str, ...]
    warnings: tuple[str, ...]
    interpretation_status: str
    execution_support_status: str
    model_provenance: ModelProvenance
    scientist_approval_possible: bool
    created_at: str


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    specification: InterpretedScientificSpecification
    accepted_assumptions: bool


class ApprovedExperimentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[APPROVED_EXPERIMENT_SCHEMA] = APPROVED_EXPERIMENT_SCHEMA
    experiment_identifier: str
    interpretation_identifier: str
    original_question: str
    specification: InterpretedScientificSpecification
    specification_sha256: str
    requested_execution_target: Literal["ibm_quantum", "local_simulator"]
    status: Literal[
        "ready_for_ibm_submission", "approved_pending_compiler_support"
    ]
    scientist_reviewed_overrides: tuple[str, ...] = ()
    assumptions_accepted: Literal[True] = True
    approved_at: str


class NaturalLanguageModelProvider(Protocol):
    provider_kind: Literal["openai_compatible_http", "controlled_test_provider"]
    model_name: str

    @property
    def request_count(self) -> int: ...

    def complete(self, messages: list[dict[str, str]]) -> str: ...


class OpenAICompatibleModelProvider:
    provider_kind: Literal["openai_compatible_http"] = "openai_compatible_http"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model_name: str,
        timeout_seconds: float,
    ) -> None:
        parsed = urlsplit(base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise ValueError("PULSATE_NL_MODEL_BASE_URL is invalid.")
        if not api_key or len(api_key) > 4096:
            raise ValueError("PULSATE_NL_MODEL_API_KEY is invalid.")
        if not model_name or len(model_name) > 512:
            raise ValueError("PULSATE_NL_MODEL_NAME is invalid.")
        if (
            not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
            or timeout_seconds > 300
        ):
            raise ValueError("PULSATE_NL_MODEL_TIMEOUT_SECONDS is invalid.")
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self.model_name = model_name
        self._timeout_seconds = timeout_seconds
        self._request_count = 0
        self._lock = threading.Lock()

    @classmethod
    def from_environment(cls) -> OpenAICompatibleModelProvider:
        base_url = os.environ.get("PULSATE_NL_MODEL_BASE_URL")
        api_key = os.environ.get("PULSATE_NL_MODEL_API_KEY")
        model_name = os.environ.get("PULSATE_NL_MODEL_NAME")
        if not base_url or not api_key or not model_name:
            raise NaturalLanguageUnavailableError(
                "The natural-language model provider is not configured."
            )
        try:
            timeout = float(
                os.environ.get("PULSATE_NL_MODEL_TIMEOUT_SECONDS", "60")
            )
        except ValueError as exc:
            raise NaturalLanguageUnavailableError(
                "The natural-language model timeout is invalid."
            ) from exc
        try:
            return cls(
                base_url=base_url,
                api_key=api_key,
                model_name=model_name,
                timeout_seconds=timeout,
            )
        except ValueError as exc:
            raise NaturalLanguageUnavailableError(str(exc)) from None

    @property
    def request_count(self) -> int:
        with self._lock:
            return self._request_count

    def complete(self, messages: list[dict[str, str]]) -> str:
        request_payload = json.dumps(
            {
                "model": self.model_name,
                "messages": messages,
                "temperature": 0,
                "response_format": {"type": "json_object"},
            },
            separators=(",", ":"),
        ).encode("utf-8")
        if len(request_payload) > MODEL_REQUEST_MAXIMUM_BYTES:
            raise NaturalLanguageInterpretationError("The model request is oversized.")
        request = urllib.request.Request(
            f"{self._base_url}/chat/completions",
            data=request_payload,
            method="POST",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
        )
        with self._lock:
            self._request_count += 1
        try:
            with urllib.request.urlopen(
                request, timeout=self._timeout_seconds
            ) as response:
                raw = response.read(MODEL_RESPONSE_MAXIMUM_BYTES + 1)
        except (urllib.error.URLError, TimeoutError, OSError):
            raise NaturalLanguageInterpretationError(
                "The configured language model request failed."
            ) from None
        if len(raw) > MODEL_RESPONSE_MAXIMUM_BYTES:
            raise NaturalLanguageInterpretationError(
                "The language model response exceeded the bounded size."
            )
        try:
            envelope = json.loads(raw.decode("utf-8"))
            content = envelope["choices"][0]["message"]["content"]
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError):
            raise NaturalLanguageInterpretationError(
                "The language model returned a malformed response envelope."
            ) from None
        if not isinstance(content, str) or len(content.encode("utf-8")) > MODEL_RESPONSE_MAXIMUM_BYTES:
            raise NaturalLanguageInterpretationError(
                "The language model returned invalid content."
            )
        return content


def _extract_json(content: str) -> dict[str, Any]:
    stripped = content.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, re.DOTALL)
    candidate = fenced.group(1) if fenced else stripped
    if not candidate.startswith("{"):
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("No JSON object was found.")
        candidate = candidate[start : end + 1]
    if len(candidate.encode("utf-8")) > MODEL_RESPONSE_MAXIMUM_BYTES:
        raise ValueError("JSON content is oversized.")
    value = json.loads(candidate)
    if not isinstance(value, dict):
        raise TypeError("Model content is not a JSON object.")
    return value


def _formula_atoms(formula: str) -> tuple[str, ...] | None:
    tokens = list(_FORMULA_TOKEN.finditer(formula))
    if not tokens or "".join(token.group(0) for token in tokens) != formula:
        return None
    atoms: list[str] = []
    for token in tokens:
        element = token.group(1)
        count = int(token.group(2) or "1")
        if element not in _SUPPORTED_ELEMENT_SYMBOLS or count <= 0 or count > 512:
            return None
        atoms.extend([element] * count)
    return tuple(atoms) if atoms else None


def _normalize_evidence_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return " ".join(normalized.split()).casefold()


def _canonical_scientific_text(value: str) -> str:
    normalized = _normalize_evidence_text(value).replace("å", " angstrom ")
    return " ".join(re.sub(r"[-_]+", " ", normalized).split())


def _field_at(draft: ModelScientificDraft, path: str) -> Any:
    if path.startswith("molecule."):
        return getattr(draft.molecule, path.split(".", 1)[1])
    return getattr(draft, path)


def _quotation_is_grounded(quotation: str, question: str) -> bool:
    return _normalize_evidence_text(quotation) in _normalize_evidence_text(question)


def _evidence_has_number(quotation: str, expected: float) -> bool:
    for match in _NUMBER_TOKEN.finditer(unicodedata.normalize("NFKC", quotation)):
        try:
            if math.isclose(float(match.group(0)), expected, rel_tol=1e-12, abs_tol=1e-12):
                return True
        except ValueError:
            continue
    return False


def _matching_number_windows(
    quotation: str, expected: float, *, radius: int = 64
) -> tuple[tuple[str, str], ...]:
    normalized = unicodedata.normalize("NFKC", quotation).casefold()
    windows: list[tuple[str, str]] = []
    for match in _NUMBER_TOKEN.finditer(normalized):
        try:
            matches = math.isclose(
                float(match.group(0)), expected, rel_tol=1e-12, abs_tol=1e-12
            )
        except ValueError:
            matches = False
        if matches:
            windows.append(
                (
                    normalized[max(0, match.start() - radius) : match.start()],
                    normalized[match.end() : match.end() + radius],
                )
            )
    return tuple(windows)


def _has_contextual_number(
    quotation: str,
    expected: float,
    *,
    before: tuple[str, ...],
    after: tuple[str, ...] = (),
) -> bool:
    for prefix, suffix in _matching_number_windows(quotation, expected):
        canonical_prefix = _canonical_scientific_text(prefix)
        canonical_suffix = _canonical_scientific_text(suffix)
        if any(
            re.search(rf"\b{pattern}\s*(?:of|is)?\s*[:=]?\s*$", canonical_prefix)
            for pattern in before
        ) or any(
            re.match(rf"^\s*{pattern}\b", canonical_suffix)
            for pattern in after
        ):
            return True
    return False


def _objective_is_supported(value: str, quotation: str) -> bool:
    canonical_value = _canonical_scientific_text(value)
    canonical_evidence = _canonical_scientific_text(quotation)
    if canonical_value in canonical_evidence:
        return True
    objective_tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", canonical_value)
        if token not in _OBJECTIVE_GENERIC_TOKENS
    }
    evidence_tokens = set(re.findall(r"[a-z0-9]+", canonical_evidence))
    return bool(objective_tokens) and objective_tokens.issubset(evidence_tokens)


def _bond_length_is_supported(bond: BondLength, quotation: str) -> bool:
    distance_terms = ("bond", "bond length", "distance", "separation", "apart")
    unit_terms = {
        "angstrom": ("angstrom", "ångström", "å"),
        "bohr": ("bohr",),
    }[bond.unit]
    for prefix, suffix in _matching_number_windows(quotation, bond.value):
        window = _normalize_evidence_text(prefix[-48:] + " " + suffix[:48])
        canonical_window = _canonical_scientific_text(window)
        if (
            any(unit in window for unit in unit_terms)
            and any(term in canonical_window for term in distance_terms)
        ):
            return True
    return False


def _coordinate_element(value: str) -> str | None:
    if value in _SUPPORTED_ELEMENT_SYMBOLS:
        return value
    lowered = value.casefold()
    if lowered in _COORDINATE_ELEMENT_NAMES:
        return _COORDINATE_ELEMENT_NAMES[lowered]
    for symbol in _SUPPORTED_ELEMENT_SYMBOLS:
        if symbol.casefold() == lowered:
            return symbol
    return None


def _atom_coordinates_are_supported(
    atoms: tuple[MolecularAtom, ...], quotation: str
) -> bool:
    parsed: list[tuple[str, tuple[float, float, float]]] = []
    normalized = unicodedata.normalize("NFKC", quotation)
    for match in _ATOM_COORDINATE_SEQUENCE.finditer(normalized):
        element = _coordinate_element(match.group("element"))
        if element is None:
            return False
        coordinates = tuple(float(match.group(axis)) for axis in ("x", "y", "z"))
        parsed.append((element, coordinates))
    if len(parsed) != len(atoms):
        return False
    for atom, (element, coordinates) in zip(atoms, parsed):
        if atom.element != element or atom.coordinates is None:
            return False
        if any(
            not math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-12)
            for actual, expected in zip(coordinates, atom.coordinates)
        ):
            return False
    return True


def _explicit_value_matches_evidence(
    path: str, field: Any, quotation: str
) -> bool:
    value = field.value
    if value is None:
        return False
    canonical_evidence = _canonical_scientific_text(quotation)
    if path == "scientific_objective":
        return _objective_is_supported(str(value), quotation)
    if path in {
        "requested_quantity",
        "molecule.name",
        "molecule.formula",
        "molecule.smiles",
        "molecule.inchi",
        "molecule.geometry_description",
        "basis",
        "electronic_structure_method",
        "active_space",
        "ansatz",
        "optimizer",
        "requested_backend",
    }:
        return _canonical_scientific_text(str(value)) in canonical_evidence
    if path == "requested_execution_target":
        aliases = {
            "ibm_quantum": ("ibm quantum", "ibm hardware", "quantum hardware"),
            "local_simulator": ("local simulator", "simulator"),
        }
        return any(alias in canonical_evidence for alias in aliases.get(str(value), ()))
    if path == "mapper":
        return _canonical_scientific_text(str(value)) in canonical_evidence
    if path == "coordinate_unit":
        aliases = {
            "angstrom": ("angstrom", "ångström", "å"),
            "bohr": ("bohr",),
        }
        normalized_quote = _normalize_evidence_text(quotation)
        return any(alias in normalized_quote for alias in aliases.get(str(value), ()))
    if path == "charge":
        return _has_contextual_number(
            quotation,
            float(value),
            before=(r"(?:net\s+)?charge",),
            after=(r"(?:net\s+)?charge",),
        )
    if path == "multiplicity":
        multiplicity_aliases = {1: "singlet", 2: "doublet", 3: "triplet"}
        return _has_contextual_number(
            quotation,
            float(value),
            before=("multiplicity",),
            after=("multiplicity",),
        ) or (
            bool(multiplicity_aliases.get(value))
            and re.search(
                rf"\b{multiplicity_aliases[value]}\b", canonical_evidence
            )
            is not None
        )
    if path == "shots":
        return _has_contextual_number(
            quotation,
            float(value),
            before=("shots", "samples", "measurements"),
            after=("shots", "samples", "measurements"),
        )
    if path == "tolerance":
        return _has_contextual_number(
            quotation,
            float(value),
            before=(
                "tolerance",
                r"error\s+threshold",
                r"convergence\s+tolerance",
            ),
            after=(
                "tolerance",
                r"error\s+threshold",
                r"convergence\s+tolerance",
            ),
        )
    if path == "precision":
        return _has_contextual_number(
            quotation,
            float(value),
            before=("precision",),
            after=("precision",),
        )
    if path == "molecule.bond_lengths":
        return all(_bond_length_is_supported(bond, quotation) for bond in value)
    if path == "molecule.atoms":
        if not value or any(atom.coordinates is None for atom in value):
            return False
        return _atom_coordinates_are_supported(value, quotation)
    return False


def _formula_is_derived_from_grounded_name(draft: ModelScientificDraft) -> bool:
    formula = draft.molecule.formula.value
    name = draft.molecule.name.value
    if (
        formula is None
        or name is None
        or draft.molecule.name.provenance != "explicit"
    ):
        return False
    atoms = _formula_atoms(formula)
    if atoms is None:
        return False
    counts = {element: atoms.count(element) for element in set(atoms)}
    normalized_name = _canonical_scientific_text(name)
    for element, count in counts.items():
        tokens = _CONSERVATIVE_ELEMENT_NAME_TOKENS.get(element)
        if tokens is None or not any(token in normalized_name for token in tokens):
            return False
        if count != 1:
            return False
    return True


def _downgrade_unverified_field(path: str, field: Any) -> Any:
    if path in {
        "molecule.atoms",
        "molecule.bond_lengths",
        "molecule.geometry_description",
        "scientific_objective",
        "requested_quantity",
        "molecule.name",
    }:
        field.value = None
        field.provenance = "missing"
    else:
        field.provenance = "assumed"
    return field


def _ground_model_draft(
    draft: ModelScientificDraft, original_question: str
) -> ModelScientificDraft:
    value = draft.model_copy(deep=True)
    evidence = dict(value.explicit_evidence)
    for path in _EXPLICIT_FIELD_PATHS:
        field = _field_at(value, path)
        if field.provenance == "explicit":
            quotation = evidence.get(path)
            if (
                quotation is None
                or not _quotation_is_grounded(quotation, original_question)
                or not _explicit_value_matches_evidence(path, field, quotation)
            ):
                _downgrade_unverified_field(path, field)
    for path in _EXPLICIT_FIELD_PATHS:
        field = _field_at(value, path)
        if field.provenance == "derived":
            trusted = False
            if path == "molecule.formula":
                trusted = _formula_is_derived_from_grounded_name(value)
            elif path == "molecule.atoms":
                formula_is_trusted = (
                    value.molecule.formula.provenance == "explicit"
                    or (
                        value.molecule.formula.provenance == "derived"
                        and _formula_is_derived_from_grounded_name(value)
                    )
                )
                formula_atoms = (
                    _formula_atoms(value.molecule.formula.value)
                    if value.molecule.formula.value is not None
                    and formula_is_trusted
                    else None
                )
                trusted = (
                    formula_atoms is not None
                    and field.value is not None
                    and tuple(atom.element for atom in field.value) == formula_atoms
                    and all(atom.coordinates is None for atom in field.value)
                )
            elif path == "coordinate_unit":
                bonds = value.molecule.bond_lengths.value or ()
                trusted = bool(bonds) and {bond.unit for bond in bonds} == {field.value}
            if not trusted:
                _downgrade_unverified_field(path, field)
    value.assumptions = ()
    value.missing_required_information = ()
    value.warnings = ()
    value.explicit_evidence = {}
    return ModelScientificDraft.model_validate(value.model_dump(mode="json"))


def _assumed_field_paths(draft: ModelScientificDraft) -> tuple[str, ...]:
    paths: list[str] = []
    for name in (
        "scientific_objective",
        "requested_quantity",
        "coordinate_unit",
        "charge",
        "multiplicity",
        "basis",
        "electronic_structure_method",
        "active_space",
        "mapper",
        "ansatz",
        "optimizer",
        "tolerance",
        "requested_execution_target",
        "requested_backend",
        "shots",
        "precision",
    ):
        if getattr(draft, name).provenance == "assumed":
            paths.append(name)
    for name in (
        "name",
        "formula",
        "smiles",
        "inchi",
        "atoms",
        "geometry_description",
        "bond_lengths",
    ):
        if getattr(draft.molecule, name).provenance == "assumed":
            paths.append(f"molecule.{name}")
    return tuple(paths)


def _canonical_assumptions(draft: ModelScientificDraft) -> tuple[str, ...]:
    assumptions: list[str] = []
    for path in _assumed_field_paths(draft):
        field = _field_at(draft, path)
        assumptions.append(
            f"{path}={field.value!r} (scientist approval required)"
        )
    return tuple(assumptions)


def _scientific_conflicts(draft: ModelScientificDraft) -> tuple[str, ...]:
    conflicts: list[str] = []
    formula = draft.molecule.formula.value
    formula_atoms = _formula_atoms(formula) if formula else None
    atoms = draft.molecule.atoms.value
    bonds = draft.molecule.bond_lengths.value
    if (
        atoms is not None
        and formula_atoms is not None
        and sorted(atom.element for atom in atoms) != sorted(formula_atoms)
    ):
        conflicts.append("formula_atom_list_conflict")
    if (
        bonds is not None
        and draft.coordinate_unit.value is not None
        and any(bond.unit != draft.coordinate_unit.value for bond in bonds)
    ):
        conflicts.append("coordinate_unit_conflict")
    if (
        atoms is not None
        and bonds is not None
        and all(atom.coordinates is not None for atom in atoms)
    ):
        for bond in bonds:
            first = atoms[bond.atom_indices[0]].coordinates
            second = atoms[bond.atom_indices[1]].coordinates
            assert first is not None and second is not None
            distance = math.sqrt(
                sum((left - right) ** 2 for left, right in zip(first, second))
            )
            if not math.isclose(distance, bond.value, rel_tol=1e-7, abs_tol=1e-9):
                conflicts.append("cartesian_bond_length_conflict")
                break
    return tuple(dict.fromkeys(conflicts))


def _postprocess_draft(draft: ModelScientificDraft) -> ModelScientificDraft:
    value = draft.model_copy(deep=True)
    if value.requested_execution_target.value is None:
        value.requested_execution_target = ProvenancedString(
            value="ibm_quantum", provenance="assumed"
        )
    formula = value.molecule.formula.value
    formula_atoms = (
        _formula_atoms(formula)
        if formula
        and value.molecule.formula.provenance in {"explicit", "derived"}
        else None
    )
    atoms = value.molecule.atoms.value
    if atoms is None and formula_atoms is not None:
        value.molecule.atoms = ProvenancedAtoms(
            value=tuple(MolecularAtom(element=element) for element in formula_atoms),
            provenance="derived",
        )
        atoms = value.molecule.atoms.value
    bonds = value.molecule.bond_lengths.value
    if (
        atoms is not None
        and len(atoms) == 2
        and all(atom.coordinates is None for atom in atoms)
        and bonds is not None
        and len(bonds) == 1
        and bonds[0].atom_indices == (0, 1)
    ):
        half = bonds[0].value / 2.0
        value.molecule.atoms = ProvenancedAtoms(
            value=(
                MolecularAtom(element=atoms[0].element, coordinates=(-half, 0.0, 0.0)),
                MolecularAtom(element=atoms[1].element, coordinates=(half, 0.0, 0.0)),
            ),
            provenance="derived",
        )
        value.coordinate_unit = ProvenancedString(
            value=bonds[0].unit, provenance="derived"
        )
    elif value.coordinate_unit.provenance == "derived":
        bond_units = {bond.unit for bond in bonds or ()}
        if len(bond_units) == 1 and value.coordinate_unit.value in bond_units:
            pass
        else:
            value.coordinate_unit.provenance = "assumed"
    value.assumptions = _canonical_assumptions(value)
    value.missing_required_information = ()
    value.warnings = _scientific_conflicts(value)
    return ModelScientificDraft.model_validate(value.model_dump(mode="json"))


def _missing_information(draft: ModelScientificDraft) -> tuple[str, ...]:
    missing = set(draft.missing_required_information)
    required: tuple[tuple[str, Any], ...] = (
        ("scientific_objective", draft.scientific_objective.value),
        ("requested_quantity", draft.requested_quantity.value),
        ("molecular_identity", any((
            draft.molecule.name.value,
            draft.molecule.formula.value,
            draft.molecule.smiles.value,
            draft.molecule.inchi.value,
            draft.molecule.atoms.value,
        ))),
        (
            "geometry",
            draft.molecule.atoms.value is not None
            and all(
                atom.coordinates is not None
                for atom in draft.molecule.atoms.value
            ),
        ),
        ("coordinate_unit", draft.coordinate_unit.value),
        ("charge", draft.charge.value),
        ("multiplicity", draft.multiplicity.value),
        ("basis", draft.basis.value),
        ("electronic_structure_method", draft.electronic_structure_method.value),
        ("active_space", draft.active_space.value),
        ("mapper", draft.mapper.value),
        ("ansatz", draft.ansatz.value),
        ("optimizer", draft.optimizer.value),
        ("tolerance", draft.tolerance.value),
        ("requested_execution_target", draft.requested_execution_target.value),
    )
    for name, present in required:
        if present is None or present is False:
            missing.add(name)
    return tuple(sorted(missing))


def _execution_support(draft: ModelScientificDraft, missing: tuple[str, ...]) -> str:
    if missing:
        return "needs_clarification"
    atoms = draft.molecule.atoms.value or ()
    current_elements = {"H", "He", "Li"}
    supported = (
        len(atoms) == 2
        and all(atom.element in current_elements for atom in atoms)
        and draft.basis.value.casefold() == "sto-3g"
        and draft.electronic_structure_method.value.casefold()
        in {"restricted_hartree_fock", "rhf"}
        and draft.mapper.value.casefold().replace("-", "_").replace(" ", "_")
        == "jordan_wigner"
        and draft.ansatz.value.casefold() == "uccsd"
        and draft.optimizer.value.casefold() == "slsqp"
    )
    return "supported" if supported else "requires_compiler_capability"


def _apply_scientist_overrides(
    original: InterpretedScientificSpecification,
    reviewed: InterpretedScientificSpecification,
) -> tuple[ModelScientificDraft, tuple[str, ...]]:
    reviewed_payload = reviewed.model_dump(mode="json")
    draft = ModelScientificDraft.model_validate(
        {
            name: reviewed_payload[name]
            for name in ModelScientificDraft.model_fields
            if name in reviewed_payload
        }
    )
    overrides: list[str] = []
    for path in sorted(_EXPLICIT_FIELD_PATHS):
        original_field = _field_at(original, path)
        reviewed_field = _field_at(draft, path)
        if original_field.value == reviewed_field.value:
            reviewed_field.provenance = original_field.provenance
        else:
            overrides.append(path)
            reviewed_field.provenance = (
                "missing" if reviewed_field.value is None else "explicit"
            )
    draft.assumptions = ()
    draft.missing_required_information = ()
    draft.warnings = ()
    draft.explicit_evidence = {}
    return draft, tuple(overrides)


def _system_prompt() -> str:
    schema = json.dumps(
        ModelScientificDraft.model_json_schema(), separators=(",", ":")
    )
    return (
        "You interpret chemistry questions into a draft only. Return exactly one JSON "
        "object matching the supplied schema and no commentary. Preserve arbitrary "
        "molecule identities. Never invent geometry. Mark each field explicit, derived, "
        "assumed, or missing. For every explicit field, add its allowed field path to "
        "explicit_evidence with an exact quotation from the user's question. Do not "
        "claim atoms or coordinates are explicit unless the quotation contains them. "
        "Proposed defaults must be assumed. IBM Quantum is the "
        "visible assumed target only when no target is explicit. Do not emit credentials, "
        "environment variables, file paths, code, or shell commands. Schema: "
        + schema
    )


class NaturalLanguageInterpretationStore:
    def __init__(
        self,
        root: Path,
        provider: NaturalLanguageModelProvider | None,
        *,
        unavailable_reason: str | None = None,
    ) -> None:
        self.configured_root = Path(root)
        self.root = self.configured_root
        self.provider = provider
        self._unavailable_reason = unavailable_reason
        self._lock = threading.RLock()
        self._started = False

    @classmethod
    def from_environment(cls, root: Path) -> NaturalLanguageInterpretationStore:
        try:
            provider = OpenAICompatibleModelProvider.from_environment()
            return cls(root, provider)
        except NaturalLanguageUnavailableError as exc:
            return cls(root, None, unavailable_reason=str(exc))

    def start(self) -> None:
        with self._lock:
            if self.configured_root.is_symlink():
                raise ValueError("Interpretation root must be a normal directory.")
            self.configured_root.mkdir(parents=True, exist_ok=True)
            if not self.configured_root.is_dir():
                raise ValueError("Interpretation root must be a normal directory.")
            self.root = self.configured_root.resolve(strict=True)
            self._started = True

    def close(self) -> None:
        self._started = False

    def capability(self) -> dict[str, Any]:
        return {
            "available": self.provider is not None,
            "provider_kind": (
                self.provider.provider_kind if self.provider is not None else None
            ),
            "model_name": self.provider.model_name if self.provider is not None else None,
            "model_request_count": (
                self.provider.request_count if self.provider is not None else 0
            ),
            "reason": None if self.provider is not None else self._unavailable_reason,
        }

    def interpret(self, question: str) -> InterpretationResponse:
        if not self._started:
            raise RuntimeError("Interpretation store is not started.")
        normalized = question.strip()
        if not normalized or len(normalized) > 4096:
            raise ApprovalValidationError(
                "Question must contain between 1 and 4096 characters."
            )
        if self.provider is None:
            raise NaturalLanguageUnavailableError(
                self._unavailable_reason
                or "The natural-language model provider is unavailable."
            )
        prompt = _system_prompt()
        content = self.provider.complete(
            [
                {"role": "system", "content": prompt},
                {"role": "user", "content": normalized},
            ]
        )
        repair_attempted = False
        try:
            raw_draft = _extract_json(content)
            draft = ModelScientificDraft.model_validate(raw_draft)
        except (TypeError, ValueError):
            repair_attempted = True
            content = self.provider.complete(
                [
                    {"role": "system", "content": prompt},
                    {
                        "role": "user",
                        "content": (
                            "Repair the following malformed draft into the exact schema. "
                            "Return JSON only:\n" + content[:65536]
                        ),
                    },
                ]
            )
            try:
                raw_draft = _extract_json(content)
                draft = ModelScientificDraft.model_validate(raw_draft)
            except (TypeError, ValueError):
                raise NaturalLanguageInterpretationError(
                    "The language model did not return a valid scientific draft."
                ) from None
        draft = _postprocess_draft(_ground_model_draft(draft, normalized))
        missing = _missing_information(draft)
        conflicts = _scientific_conflicts(draft)
        support = _execution_support(draft, (*missing, *conflicts))
        status = "needs_clarification" if missing or conflicts else "ready_for_review"
        provenance = ModelProvenance(
            provider_kind=self.provider.provider_kind,
            model_name=self.provider.model_name,
            prompt_sha256=sha256_fingerprint({"prompt": prompt}),
            response_sha256=sha256_fingerprint(raw_draft),
            requested_at=_utc_now(),
            repair_attempted=repair_attempted,
            request_count_for_interpretation=2 if repair_attempted else 1,
        )
        specification_payload = draft.model_dump(mode="json")
        specification_payload.update(
            {
                "original_question": normalized,
                "missing_required_information": missing,
                "interpretation_status": status,
                "execution_support_status": support,
                "model_provenance": provenance,
            }
        )
        specification = InterpretedScientificSpecification.model_validate(
            specification_payload
        )
        identifier = f"interpretation-{uuid.uuid4().hex}"
        now = _utc_now()
        response = InterpretationResponse(
            interpretation_identifier=identifier,
            original_question=normalized,
            specification=specification,
            assumptions=specification.assumptions,
            missing_required_information=missing,
            warnings=specification.warnings,
            interpretation_status=status,
            execution_support_status=support,
            model_provenance=provenance,
            scientist_approval_possible=not missing and not conflicts,
            created_at=now,
        )
        directory = self.root / identifier
        directory.mkdir(mode=0o700)
        write_json_atomic(
            directory / "interpretation.json",
            response.model_dump(mode="json"),
            maximum_bytes=INTERPRETATION_RECORD_MAXIMUM_BYTES,
        )
        return response

    def approve(
        self, interpretation_identifier: str, request: ApprovalRequest
    ) -> ApprovedExperimentResponse:
        if not self._started:
            raise RuntimeError("Interpretation store is not started.")
        if _INTERPRETATION_IDENTIFIER.fullmatch(interpretation_identifier) is None:
            raise InterpretationNotFoundError("Interpretation not found.")
        directory = self.root / interpretation_identifier
        if directory.is_symlink() or not directory.is_dir():
            raise InterpretationNotFoundError("Interpretation not found.")
        try:
            if directory.resolve(strict=True).parent != self.root:
                raise InterpretationNotFoundError("Interpretation not found.")
        except OSError:
            raise InterpretationNotFoundError("Interpretation not found.") from None
        interpretation_record = directory / "interpretation.json"
        if (
            interpretation_record.is_symlink()
            or not interpretation_record.is_file()
            or interpretation_record.stat().st_size
            > INTERPRETATION_RECORD_MAXIMUM_BYTES
        ):
            raise InterpretationNotFoundError("Interpretation not found.")
        original = InterpretationResponse.model_validate_json(
            interpretation_record.read_text(encoding="utf-8")
        )
        reviewed = InterpretedScientificSpecification.model_validate(
            request.specification.model_dump(mode="json")
        )
        if reviewed.original_question != original.original_question:
            raise ApprovalValidationError("Original question identity changed.")
        if reviewed.model_provenance != original.model_provenance:
            raise ApprovalValidationError("Model provenance identity changed.")
        reviewed_draft, scientist_overrides = _apply_scientist_overrides(
            original.specification, reviewed
        )
        deterministic = _postprocess_draft(reviewed_draft)
        conflicts = _scientific_conflicts(deterministic)
        if conflicts:
            raise ApprovalValidationError(
                "Scientific contradictions remain unresolved: "
                + ", ".join(conflicts)
            )
        missing = _missing_information(deterministic)
        if missing:
            raise ApprovalValidationError(
                "Required information remains unresolved: " + ", ".join(missing)
            )
        if deterministic.assumptions and not request.accepted_assumptions:
            raise ApprovalValidationError(
                "All proposed assumptions require explicit scientist acceptance."
            )
        support = _execution_support(deterministic, missing)
        reviewed_payload = deterministic.model_dump(mode="json")
        reviewed_payload.update(
            {
                "original_question": original.original_question,
                "missing_required_information": (),
                "interpretation_status": "ready_for_review",
                "execution_support_status": support,
                "model_provenance": original.model_provenance,
            }
        )
        reviewed = InterpretedScientificSpecification.model_validate(reviewed_payload)
        target = reviewed.requested_execution_target.value
        if target not in {"ibm_quantum", "local_simulator"}:
            raise ApprovalValidationError("Execution target is unresolved.")
        specification_payload = reviewed.model_dump(mode="json")
        specification_sha = sha256_fingerprint(specification_payload)
        experiment_identifier = f"experiment-{uuid.uuid4().hex}"
        approved = ApprovedExperimentResponse(
            experiment_identifier=experiment_identifier,
            interpretation_identifier=interpretation_identifier,
            original_question=original.original_question,
            specification=reviewed,
            specification_sha256=specification_sha,
            requested_execution_target=target,
            status=(
                "ready_for_ibm_submission"
                if target == "ibm_quantum" and support == "supported"
                else "approved_pending_compiler_support"
            ),
            scientist_reviewed_overrides=scientist_overrides,
            approved_at=_utc_now(),
        )
        approved_root = self.root / "approved"
        approved_root.mkdir(mode=0o700, exist_ok=True)
        if (
            approved_root.is_symlink()
            or not approved_root.is_dir()
            or approved_root.resolve(strict=True).parent != self.root
        ):
            raise ApprovalValidationError(
                "The approved-experiment store is not controlled."
            )
        approved_directory = approved_root / experiment_identifier
        approved_directory.mkdir(mode=0o700)
        write_json_atomic(
            approved_directory / "experiment.json",
            approved.model_dump(mode="json"),
            maximum_bytes=INTERPRETATION_RECORD_MAXIMUM_BYTES,
        )
        return approved
