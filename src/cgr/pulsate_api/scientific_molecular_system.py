"""Grounded molecular systems and controls for composable energy workflows.

This module represents supported formulae, literal internal coordinates, and
explicit electronic controls. It does not route on molecule identity, provide
numerical results, or silently discard the provenance of a default.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# Bounded periodic-table data, not molecule-specific routing.
_ATOMIC_NUMBER = {
    "H": 1, "He": 2, "Li": 3, "Be": 4, "B": 5, "C": 6, "N": 7,
    "O": 8, "F": 9, "Ne": 10, "Na": 11, "Mg": 12, "Al": 13,
    "Si": 14, "P": 15, "S": 16, "Cl": 17, "Ar": 18, "K": 19, "Ca": 20,
}
_ELEMENT_ALIAS = {
    "hydrogen": "H", "hydride": "H", "helium": "He", "lithium": "Li",
    "beryllium": "Be", "boron": "B", "boride": "B", "carbon": "C",
    "carbide": "C", "nitrogen": "N", "nitride": "N", "oxygen": "O",
    "oxide": "O", "fluorine": "F", "fluoride": "F", "neon": "Ne",
    "sodium": "Na", "magnesium": "Mg", "aluminum": "Al",
    "aluminium": "Al", "silicon": "Si", "silicide": "Si",
    "phosphorus": "P", "phosphide": "P", "sulfur": "S", "sulphur": "S",
    "sulfide": "S", "sulphide": "S", "chlorine": "Cl", "chloride": "Cl",
    "argon": "Ar", "potassium": "K", "calcium": "Ca",
}
_ALIASES = tuple(sorted(_ELEMENT_ALIAS, key=lambda item: (-len(item), item)))
_FORMULA_TOKEN = re.compile(r"(?P<symbol>[A-Z][a-z]?)(?P<count>\d*)")
_FORMULA_CANDIDATE = re.compile(
    r"(?<![A-Za-z0-9])(?P<formula>(?:[A-Z][a-z]?\d*){1,16})(?![A-Za-z0-9])"
)
_ANGSTROM_VALUE = re.compile(
    r"(?P<value>\d+(?:\.\d+)?)\s*(?:\u00c5|angstroms?)\b", re.IGNORECASE
)
_BOND_LENGTH = re.compile(
    r"\bbond\s+(?:length|distance)s?(?:\s+(?:of|is|at))?\s*"
    r"(?P<value>\d+(?:\.\d+)?)\s*(?:\u00c5|angstroms?)\b",
    re.IGNORECASE,
)
_BOND_LABEL = re.compile(
    r"(?P<left>[A-Z][a-z]?)\s*[\-\u2013\u2014]\s*"
    r"(?P<right>[A-Z][a-z]?)\s+bond\s+(?:length|distance)s?", re.IGNORECASE
)
_ANGLE = re.compile(
    r"(?P<left>[A-Z][a-z]?)\s*[\-\u2013\u2014]\s*"
    r"(?P<center>[A-Z][a-z]?)\s*[\-\u2013\u2014]\s*"
    r"(?P<right>[A-Z][a-z]?)\s+bond\s+angles?(?:\s+(?:of|is))?\s*"
    r"(?P<value>\d+(?:\.\d+)?)\s*degrees?\b", re.IGNORECASE
)
_ACTIVE_SPACE = re.compile(
    r"(?P<electrons>\d{1,2})[-\s]?electron\s*,?\s*"
    r"(?P<orbitals>\d{1,2})[-\s]?orbital\s+(?:\w+\s+){0,2}?active\s+space",
    re.IGNORECASE,
)
_CHARGE = re.compile(
    r"\b(?:charge|net\s+charge)\s*(?:of|is|=)?\s*(?P<value>[+-]?\d+)\b",
    re.IGNORECASE,
)
_MULTIPLICITY = {"singlet": 1, "doublet": 2, "triplet": 3, "quartet": 4, "quintet": 5}


class MolecularCartesianAtom(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    atom_index: int = Field(ge=0, le=255)
    element_symbol: str
    x_angstrom: float
    y_angstrom: float
    z_angstrom: float

    @field_validator("element_symbol")
    @classmethod
    def supported_element(cls, value: str) -> str:
        if value not in _ATOMIC_NUMBER:
            raise ValueError("The molecular geometry contains an unsupported element.")
        return value

    @field_validator("x_angstrom", "y_angstrom", "z_angstrom")
    @classmethod
    def finite_coordinate(cls, value: float) -> float:
        if not math.isfinite(value) or abs(value) > 1000:
            raise ValueError("Molecular coordinates must be finite and bounded.")
        return value

    @property
    def atomic_number(self) -> int:
        return _ATOMIC_NUMBER[self.element_symbol]


class MolecularGeometryPoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    point_identifier: str = Field(pattern=r"geometry-point-[a-z0-9-]+")
    atoms: tuple[MolecularCartesianAtom, ...] = Field(min_length=2, max_length=64)
    supporting_quote: str = Field(min_length=1, max_length=1024)
    parameter_name: Literal["bond_distance_angstrom"] | None = None
    parameter_value: float | None = Field(default=None, gt=0.1, le=20.0)

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if tuple(atom.atom_index for atom in self.atoms) != tuple(range(len(self.atoms))):
            raise ValueError("Geometry atom indices must be contiguous and ordered.")
        if (self.parameter_name is None) != (self.parameter_value is None):
            raise ValueError("Geometry sweep parameter name and value must be paired.")
        return self


class ScientificControlProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    field_name: str = Field(pattern=r"[a-z][a-z0-9_]{0,63}")
    source: Literal["scientist", "derived", "audited_default"]
    supporting_quote: str | None = Field(default=None, max_length=1024)


class MolecularGroundStateSpecification(BaseModel):
    """General molecular geometry plus explicit, provenance-bearing controls."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    system_label: str = Field(min_length=1, max_length=128)
    molecular_formula: str = Field(min_length=1, max_length=128)
    element_symbols: tuple[str, ...] = Field(min_length=2, max_length=64)
    geometry_points: tuple[MolecularGeometryPoint, ...] = Field(min_length=1, max_length=101)
    molecular_charge: int = Field(ge=-8, le=8)
    spin: int = Field(ge=0, le=8)
    identity_supporting_quote: str = Field(min_length=1, max_length=256)
    geometry_supporting_quote: str = Field(min_length=1, max_length=2048)
    basis_set: str = Field(min_length=1, max_length=64)
    reference_method: Literal["rhf", "uhf", "rohf"]
    active_electron_count: int = Field(gt=0, le=64)
    active_spatial_orbital_count: int = Field(gt=0, le=32)
    requested_frozen_core_orbital_count: int | None = Field(default=None, ge=0, le=32)
    mapper: Literal["jordan_wigner", "parity"]
    ansatz: Literal["uccsd"]
    execution_model: Literal["exact_statevector_expectation"]
    exact_diagonalization_requested: bool
    requested_outputs: tuple[str, ...] = Field(min_length=1, max_length=32)
    control_provenance: tuple[ScientificControlProvenance, ...] = Field(min_length=1)
    assumptions: tuple[str, ...] = ()
    electronic_state_policy: str = "reviewed_electronic_state_v2"
    basis_set_policy: str = "explicit_or_audited_default_v2"
    active_space_policy: str = "explicit_or_frontier_v2"

    @field_validator("element_symbols")
    @classmethod
    def supported_elements(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(symbol not in _ATOMIC_NUMBER for symbol in value):
            raise ValueError("The molecular system contains an unsupported element.")
        return value

    @field_validator("basis_set")
    @classmethod
    def normalized_basis(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9+*(),.\-]{0,63}", normalized):
            raise ValueError("Basis-set identifiers must be bounded names.")
        return normalized

    @field_validator("requested_outputs")
    @classmethod
    def unique_outputs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)) or any(
            not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", item) for item in value
        ):
            raise ValueError("Requested outputs must be unique stable identifiers.")
        return value

    @property
    def atomic_numbers(self) -> tuple[int, ...]:
        return tuple(_ATOMIC_NUMBER[item] for item in self.element_symbols)

    @property
    def electron_count(self) -> int:
        return sum(self.atomic_numbers) - self.molecular_charge

    @property
    def multiplicity(self) -> int:
        return self.spin + 1

    @property
    def is_parameter_sweep(self) -> bool:
        return len(self.geometry_points) > 1

    @property
    def bond_length_angstrom(self) -> float | None:
        point = self.geometry_points[0]
        return point.parameter_value if len(self.geometry_points) == 1 else None

    @model_validator(mode="after")
    def scientifically_consistent(self) -> Self:
        if self.electron_count <= 0 or self.spin > self.electron_count:
            raise ValueError("Charge and spin are incompatible with the molecular identity.")
        if (self.electron_count - self.spin) % 2:
            raise ValueError("Charge and spin imply non-integral alpha/beta populations.")
        if self.active_electron_count > self.electron_count:
            raise ValueError("Active electrons exceed the molecular electron count.")
        if self.active_electron_count > 2 * self.active_spatial_orbital_count:
            raise ValueError("Active electrons exceed active-space capacity.")
        if self.active_electron_count < self.spin or (self.active_electron_count - self.spin) % 2:
            raise ValueError("Active electrons are incompatible with molecular spin.")
        expected = Counter(self.element_symbols)
        for point in self.geometry_points:
            if Counter(atom.element_symbol for atom in point.atoms) != expected:
                raise ValueError("Every geometry point must preserve molecular identity.")
        names = tuple(item.field_name for item in self.control_provenance)
        if len(names) != len(set(names)):
            raise ValueError("Calculation-control provenance fields must be unique.")
        return self


def _parse_formula(candidate: str) -> tuple[str, ...] | None:
    position = 0
    symbols: list[str] = []
    for match in _FORMULA_TOKEN.finditer(candidate):
        if match.start() != position:
            return None
        symbol = match.group("symbol")
        if symbol not in _ATOMIC_NUMBER:
            return None
        count = int(match.group("count") or "1")
        if count <= 0 or len(symbols) + count > 64:
            return None
        symbols.extend((symbol,) * count)
        position = match.end()
    return tuple(symbols) if position == len(candidate) and len(symbols) >= 2 else None


def _formula_identity(text: str) -> tuple[str, tuple[str, ...], str] | None:
    for match in _FORMULA_CANDIDATE.finditer(text):
        symbols = _parse_formula(match.group("formula"))
        if symbols is not None:
            return match.group("formula"), symbols, match.group("formula")
    matches: list[tuple[int, int, str]] = []
    for alias in _ALIASES:
        for match in re.finditer(rf"\b{re.escape(alias)}\b", text, re.IGNORECASE):
            if any(match.start() < end and match.end() > start for start, end, _ in matches):
                continue
            matches.append((match.start(), match.end(), _ELEMENT_ALIAS[alias]))
    matches.sort()
    if len(matches) != 2:
        return None
    symbols = (matches[0][2], matches[1][2])
    return "".join(symbols), symbols, text[matches[0][0] : matches[1][1]]


def _diatomic_point(
    symbols: tuple[str, ...], value: float, quote: str, position: int
) -> MolecularGeometryPoint:
    half = value / 2
    return MolecularGeometryPoint(
        point_identifier=f"geometry-point-{position:03d}",
        atoms=(
            MolecularCartesianAtom(atom_index=0, element_symbol=symbols[0], x_angstrom=0, y_angstrom=0, z_angstrom=-half),
            MolecularCartesianAtom(atom_index=1, element_symbol=symbols[1], x_angstrom=0, y_angstrom=0, z_angstrom=half),
        ),
        supporting_quote=quote,
        parameter_name="bond_distance_angstrom",
        parameter_value=value,
    )


def _symmetric_point(
    symbols: tuple[str, ...], central: str, terminal: str,
    distance: float, angle_degrees: float, quote: str,
) -> MolecularGeometryPoint:
    counts = Counter(symbols)
    terminal_count = counts[terminal]
    if counts[central] != 1 or terminal_count not in {2, 3} or sum(counts.values()) != terminal_count + 1:
        raise ValueError("The supplied symmetric internal coordinates do not uniquely cover the formula.")
    theta = math.radians(angle_degrees)
    if not 0 < theta < math.pi:
        raise ValueError("Bond angles must be strictly between zero and 180 degrees.")
    if terminal_count == 2:
        vectors = [(0, 0, distance), (distance * math.sin(theta), 0, distance * math.cos(theta))]
    else:
        cosine_squared = (math.cos(theta) + (1 / 2)) / (3 / 2)
        if not 0 <= cosine_squared <= 1:
            raise ValueError("The pairwise angle is invalid for symmetric AX3 geometry.")
        polar = math.acos(math.sqrt(cosine_squared))
        vectors = [
            (
                distance * math.sin(polar) * math.cos(2 * math.pi * index / terminal_count),
                distance * math.sin(polar) * math.sin(2 * math.pi * index / terminal_count),
                distance * math.cos(polar),
            )
            for index in range(terminal_count)
        ]
    atoms = [MolecularCartesianAtom(atom_index=0, element_symbol=central, x_angstrom=0, y_angstrom=0, z_angstrom=0)]
    atoms.extend(
        MolecularCartesianAtom(
            atom_index=index, element_symbol=terminal,
            x_angstrom=vector[0], y_angstrom=vector[1], z_angstrom=vector[2],
        )
        for index, vector in enumerate(vectors, start=1)
    )
    return MolecularGeometryPoint(point_identifier="geometry-point-001", atoms=tuple(atoms), supporting_quote=quote)


def _basis(text: str) -> tuple[str, str | None, str]:
    match = re.search(
        r"\b(?P<value>[A-Za-z0-9][A-Za-z0-9+*(),.\-]{0,31})\s+basis(?:\s+set)?\b",
        text, re.IGNORECASE,
    )
    return (match.group("value").lower(), match.group(0), "scientist") if match else ("sto-3g", None, "audited_default")


def _reference(text: str) -> tuple[str, str | None, str]:
    patterns = (
        (r"\b(?:restricted\s+hartree[\-\u2013\u2014 ]fock|RHF)\b", "rhf"),
        (r"\b(?:unrestricted\s+hartree[\-\u2013\u2014 ]fock|UHF)\b", "uhf"),
        (r"\b(?:restricted\s+open[\-\u2013\u2014 ]shell\s+hartree[\-\u2013\u2014 ]fock|ROHF)\b", "rohf"),
    )
    for pattern, value in patterns:
        if match := re.search(pattern, text, re.IGNORECASE):
            return value, match.group(0), "scientist"
    return "rhf", None, "audited_default"


def _outputs(text: str, sweep: bool) -> tuple[str, ...]:
    lowered = text.casefold()
    outputs = ["variational_energy", "scientific_verification", "provenance"]
    checks = (
        ("reference_energy", "hartree" in lowered or bool(re.search(r"\brhf\b", text, re.IGNORECASE))),
        ("active_space_exact_energy", "exact" in lowered or "diagonaliz" in lowered),
        ("variational_exact_difference", "error" in lowered or "compare" in lowered),
        ("inactive_space_energy_contributions", any(word in lowered for word in ("contribution", "frozen", "inactive"))),
        ("qubit_counts", "qubit count" in lowered),
        ("convergence_information", "convergence" in lowered),
        ("assumptions_and_derived_parameters", "assumption" in lowered or "derived parameter" in lowered),
        ("execution_receipt", "receipt" in lowered or "auditable record" in lowered),
    )
    outputs.extend(name for name, applies in checks if applies)
    if sweep:
        outputs.extend(("parameter_sweep_points", "multi_point_execution_receipt"))
    return tuple(dict.fromkeys(outputs))


def resolve_molecular_ground_state_specification(
    scientist_text: str,
) -> tuple[MolecularGroundStateSpecification | None, tuple[str, ...]]:
    """Resolve a bounded formula, geometry, and calculation-control contract."""

    text = " ".join(scientist_text.strip().split())
    identity = _formula_identity(text)
    if identity is None:
        return None, ("One supported molecular formula or an unambiguous named two-atom identity is required.",)
    formula, symbols, identity_quote = identity
    reasons: list[str] = []
    unit_values = tuple((float(match.group("value")), match.group(0)) for match in _ANGSTROM_VALUE.finditer(text))
    geometries: tuple[MolecularGeometryPoint, ...] = ()
    geometry_quote = ""
    try:
        if len(symbols) == 2 and len(unit_values) > 1:
            geometries = tuple(_diatomic_point(symbols, value, quote, index) for index, (value, quote) in enumerate(unit_values, 1))
            geometry_quote = ", ".join(quote for _, quote in unit_values)
        elif len(symbols) == 2:
            bond_length = _BOND_LENGTH.search(text)
            selected = (float(bond_length.group("value")), bond_length.group(0)) if bond_length else (unit_values[0] if len(unit_values) == 1 else None)
            if selected:
                geometries = (_diatomic_point(symbols, selected[0], selected[1], 1),)
                geometry_quote = selected[1]
        else:
            distance, bond, angle = _BOND_LENGTH.search(text), _BOND_LABEL.search(text), _ANGLE.search(text)
            if distance and bond and angle:
                left, right, center = bond.group("left").title(), bond.group("right").title(), angle.group("center").title()
                terminal = right if center == left else left if center == right else ""
                if not terminal:
                    raise ValueError("Bond and angle labels do not identify one shared central atom.")
                geometry_quote = text[bond.start() : angle.end()]
                geometries = (_symmetric_point(
                    symbols, center, terminal, float(distance.group("value")),
                    float(angle.group("value")), geometry_quote,
                ),)
    except ValueError as exc:
        reasons.append(str(exc))
    if not geometries:
        reasons.append(
            "An exact diatomic bond length, Cartesian coordinates, a complete Z-matrix, or unambiguous supported symmetric internal coordinates are required."
        )
        return None, tuple(dict.fromkeys(reasons))

    charge_match = _CHARGE.search(text)
    neutral_match = re.search(r"\bneutral\b", text, re.IGNORECASE)
    charge = int(charge_match.group("value")) if charge_match else 0
    charge_quote = charge_match.group(0) if charge_match else neutral_match.group(0) if neutral_match else None
    charge_source = "scientist" if charge_quote else "audited_default"
    multiplicity = next(
        ((value, match) for word, value in _MULTIPLICITY.items() if (match := re.search(rf"\b{word}\b", text, re.IGNORECASE))),
        None,
    )
    spin = multiplicity[0] - 1 if multiplicity else 0
    spin_quote = multiplicity[1].group(0) if multiplicity else None
    spin_source = "scientist" if multiplicity else "derived"
    basis, basis_quote, basis_source = _basis(text)
    reference, reference_quote, reference_source = _reference(text)
    active = _ACTIVE_SPACE.search(text)
    active_electrons, active_orbitals = (
        (int(active.group("electrons")), int(active.group("orbitals"))) if active else (2, 2)
    )
    active_source, active_quote = ("scientist", active.group(0)) if active else ("audited_default", None)
    frozen = re.search(r"\bfreeze\s+(?P<quote>[^.]{1,160}?core\s+orbitals?)\b", text, re.IGNORECASE)
    frozen_quote = frozen.group(0) if frozen else None
    frozen_count = None
    if frozen_quote:
        count = re.search(r"\b(?P<count>\d+)\s+core\s+orbitals?\b", frozen_quote, re.IGNORECASE)
        frozen_count = int(count.group("count")) if count else 1
    jordan = re.search(r"\bjordan[\-\u2013\u2014 ]wigner\b", text, re.IGNORECASE)
    parity = re.search(r"\bparity(?:\s+(?:transformation|mapper|mapping))?\b", text, re.IGNORECASE)
    mapper, mapper_quote, mapper_source = (
        ("parity", parity.group(0), "scientist") if parity else
        ("jordan_wigner", jordan.group(0), "scientist") if jordan else
        ("jordan_wigner", None, "audited_default")
    )
    ansatz = re.search(r"\bUCCSD\b", text, re.IGNORECASE)
    ansatz_quote, ansatz_source = (ansatz.group(0), "scientist") if ansatz else (None, "audited_default")
    statevector = re.search(r"\bstatevector\b", text, re.IGNORECASE)
    assumptions = tuple(
        statement for statement, applies in (
            ("Molecular charge defaults to neutral under audited policy; it was not scientist-supplied.", charge_source != "scientist"),
            ("A singlet state is derived from the closed-shell ground-state/RHF request and remains auditable.", spin_source != "scientist"),
            ("STO-3G is an audited minimal-basis default because no basis was supplied.", basis_source != "scientist"),
            ("Restricted Hartree-Fock is the audited closed-shell reference default.", reference_source != "scientist"),
            ("Frontier CAS(2e,2o) is an audited bounded active-space policy, not a scientist-supplied value.", active_source != "scientist"),
            ("Jordan-Wigner mapping without qubit reduction is the audited mapper default.", mapper_source != "scientist"),
            ("UCCSD with a Hartree-Fock initial state is the audited variational ansatz default.", ansatz_source != "scientist"),
        ) if applies
    )
    provenance = (
        ScientificControlProvenance(field_name="molecular_charge", source=charge_source, supporting_quote=charge_quote),
        ScientificControlProvenance(field_name="spin", source=spin_source, supporting_quote=spin_quote),
        ScientificControlProvenance(field_name="basis_set", source=basis_source, supporting_quote=basis_quote),
        ScientificControlProvenance(field_name="reference_method", source=reference_source, supporting_quote=reference_quote),
        ScientificControlProvenance(field_name="active_space", source=active_source, supporting_quote=active_quote),
        ScientificControlProvenance(field_name="frozen_core_orbitals", source="scientist" if frozen_quote else "derived", supporting_quote=frozen_quote),
        ScientificControlProvenance(field_name="mapper", source=mapper_source, supporting_quote=mapper_quote),
        ScientificControlProvenance(field_name="ansatz", source=ansatz_source, supporting_quote=ansatz_quote),
        ScientificControlProvenance(field_name="execution_model", source="scientist" if statevector else "audited_default", supporting_quote=statevector.group(0) if statevector else None),
    )
    try:
        specification = MolecularGroundStateSpecification(
            system_label=identity_quote, molecular_formula=formula,
            element_symbols=symbols, geometry_points=geometries,
            molecular_charge=charge, spin=spin,
            identity_supporting_quote=identity_quote,
            geometry_supporting_quote=geometry_quote,
            basis_set=basis, reference_method=reference,
            active_electron_count=active_electrons,
            active_spatial_orbital_count=active_orbitals,
            requested_frozen_core_orbital_count=frozen_count,
            mapper=mapper, ansatz="uccsd",
            execution_model="exact_statevector_expectation",
            exact_diagonalization_requested=bool(re.search(r"\bexact\b|\bdiagonaliz", text, re.IGNORECASE)),
            requested_outputs=_outputs(text, len(geometries) > 1),
            control_provenance=provenance, assumptions=assumptions,
        )
    except ValueError as exc:
        return None, tuple(dict.fromkeys((*reasons, str(exc))))
    return specification, tuple(dict.fromkeys(reasons))


def resolve_diatomic_ground_state_specification(
    scientist_text: str,
) -> tuple[MolecularGroundStateSpecification | None, tuple[str, ...]]:
    """Compatibility name retained for version-1 callers."""
    return resolve_molecular_ground_state_specification(scientist_text)


__all__ = [
    "MolecularCartesianAtom", "MolecularGeometryPoint",
    "MolecularGroundStateSpecification", "ScientificControlProvenance",
    "resolve_diatomic_ground_state_specification",
    "resolve_molecular_ground_state_specification",
]