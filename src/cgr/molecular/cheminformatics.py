"""Generic, engine-neutral molecular graph and geometry artifacts."""

from __future__ import annotations

import math
import re
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from cgr.kernel.contracts import CapabilityVersion
from cgr.science import CanonicalModel
from cgr.science.canonical import validate_identifier

_SCHEMA_VERSION = CapabilityVersion(major=1, minor=0, patch=0)
_ELEMENT_SYMBOL = re.compile(r"^[A-Za-z]{1,3}$")


def _validate_schema_version(value: CapabilityVersion) -> CapabilityVersion:
    if value != _SCHEMA_VERSION:
        raise ValueError(
            "Molecular cheminformatics schema version must be exactly 1.0.0."
        )
    return value


def _finite(value: float, *, label: str) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{label} must be finite.")
    return value


class MolecularGraphAtom(CanonicalModel):
    """One engine-neutral atom record in stable source order."""

    atom_index: int = Field(ge=0)
    atomic_number: int = Field(gt=0)
    element_symbol: str
    formal_charge: int = 0
    isotope: int = Field(default=0, ge=0)
    aromatic: bool = False
    chiral_tag: str
    explicit_hydrogen_count: int = Field(default=0, ge=0)
    implicit_hydrogen_count: int = Field(default=0, ge=0)
    atom_map_number: int = Field(gt=0)
    source_atom_index: int | None = Field(default=None, ge=0)

    @field_validator("element_symbol")
    @classmethod
    def normalize_element_symbol(cls, value: str) -> str:
        stripped = value.strip()
        if not _ELEMENT_SYMBOL.fullmatch(stripped):
            raise ValueError("Element symbol must contain one to three letters.")
        return stripped[0].upper() + stripped[1:].lower()

    @field_validator("chiral_tag")
    @classmethod
    def validate_chiral_tag(cls, value: str) -> str:
        return validate_identifier(value, label="molecular chiral tag")


class MolecularGraphBond(CanonicalModel):
    """One engine-neutral bond with canonical endpoint ordering."""

    bond_index: int = Field(ge=0)
    atom_index_a: int = Field(ge=0)
    atom_index_b: int = Field(ge=0)
    bond_order: float = Field(gt=0)
    bond_type: str
    aromatic: bool = False
    conjugated: bool = False
    stereo: str

    @model_validator(mode="before")
    @classmethod
    def canonicalize_endpoints(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        atom_a = value.get("atom_index_a")
        atom_b = value.get("atom_index_b")
        if isinstance(atom_a, int) and isinstance(atom_b, int) and atom_b < atom_a:
            normalized = dict(value)
            normalized["atom_index_a"] = atom_b
            normalized["atom_index_b"] = atom_a
            return normalized
        return value

    @field_validator("bond_order")
    @classmethod
    def validate_bond_order(cls, value: float) -> float:
        return _finite(value, label="Molecular bond order")

    @field_validator("bond_type", "stereo")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="molecular bond identifier")

    @model_validator(mode="after")
    def reject_self_bond(self) -> Self:
        if self.atom_index_a == self.atom_index_b:
            raise ValueError("A molecular graph bond cannot reference one atom twice.")
        return self


class MolecularGraph(CanonicalModel):
    """A deterministic molecule graph without external-engine objects."""

    schema_version: CapabilityVersion
    graph_identifier: str
    representation_source: Literal[
        "smiles",
        "prepared_graph",
        "ph_protonation_state",
        "fragment",
        "scaffold",
    ]
    canonical_smiles: str = Field(min_length=1, max_length=16_384)
    mapped_smiles: str = Field(min_length=1, max_length=32_768)
    atoms: tuple[MolecularGraphAtom, ...] = Field(min_length=1)
    bonds: tuple[MolecularGraphBond, ...] = ()
    sanitized: bool
    explicit_hydrogens: bool
    formal_charge: int
    component_count: int = Field(gt=0)

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(
        cls,
        value: CapabilityVersion,
    ) -> CapabilityVersion:
        return _validate_schema_version(value)

    @field_validator("graph_identifier")
    @classmethod
    def validate_graph_identifier(cls, value: str) -> str:
        return validate_identifier(value, label="molecular graph identifier")

    @field_validator("canonical_smiles", "mapped_smiles")
    @classmethod
    def normalize_smiles(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Molecular SMILES representations cannot be blank.")
        return normalized

    @field_validator("atoms")
    @classmethod
    def order_atoms(
        cls,
        value: tuple[MolecularGraphAtom, ...],
    ) -> tuple[MolecularGraphAtom, ...]:
        indices = [atom.atom_index for atom in value]
        map_numbers = [atom.atom_map_number for atom in value]
        if sorted(indices) != list(range(len(value))):
            raise ValueError(
                "Molecular graph atom indices must be contiguous from zero."
            )
        if len(map_numbers) != len(set(map_numbers)):
            raise ValueError("Molecular graph atom-map numbers must be unique.")
        return tuple(sorted(value, key=lambda atom: atom.atom_index))

    @field_validator("bonds")
    @classmethod
    def order_bonds(
        cls,
        value: tuple[MolecularGraphBond, ...],
    ) -> tuple[MolecularGraphBond, ...]:
        indices = [bond.bond_index for bond in value]
        if sorted(indices) != list(range(len(value))):
            raise ValueError(
                "Molecular graph bond indices must be contiguous from zero."
            )
        endpoints = [(bond.atom_index_a, bond.atom_index_b) for bond in value]
        if len(endpoints) != len(set(endpoints)):
            raise ValueError("Molecular graph bonds must have unique endpoints.")
        return tuple(sorted(value, key=lambda bond: bond.bond_index))

    @model_validator(mode="after")
    def validate_graph(self) -> Self:
        atom_count = len(self.atoms)
        if any(
            bond.atom_index_a >= atom_count or bond.atom_index_b >= atom_count
            for bond in self.bonds
        ):
            raise ValueError("Every molecular graph bond endpoint must resolve.")
        if self.formal_charge != sum(atom.formal_charge for atom in self.atoms):
            raise ValueError(
                "Molecular graph formal charge must equal its atom charges."
            )
        source_indices = tuple(
            atom.source_atom_index
            for atom in self.atoms
            if atom.source_atom_index is not None
        )
        if len(source_indices) != len(set(source_indices)):
            raise ValueError("Molecular graph source atom indices must be unique.")
        if any(index >= len(self.atoms) for index in source_indices):
            raise ValueError("Molecular graph source atom indices must resolve.")
        return self


class MolecularCoordinate(CanonicalModel):
    """One Cartesian atom coordinate in angstrom."""

    atom_index: int = Field(ge=0)
    x: float
    y: float
    z: float

    @field_validator("x", "y", "z")
    @classmethod
    def validate_coordinate(cls, value: float) -> float:
        return _finite(value, label="Molecular coordinate")


class MolecularConformer(CanonicalModel):
    """One generated conformer with stable coordinate ordering."""

    conformer_identifier: str
    conformer_index: int = Field(ge=0)
    coordinates: tuple[MolecularCoordinate, ...] = Field(min_length=1)
    energy: float | None = None
    energy_unit: Literal["kilocalorie_per_mole"] | None = None
    optimization_status: Literal[
        "not_requested",
        "converged",
        "not_converged",
    ]

    @field_validator("conformer_identifier")
    @classmethod
    def validate_conformer_identifier(cls, value: str) -> str:
        return validate_identifier(value, label="molecular conformer identifier")

    @field_validator("energy")
    @classmethod
    def validate_energy(cls, value: float | None) -> float | None:
        if value is None:
            return None
        return _finite(value, label="Molecular conformer energy")

    @field_validator("coordinates")
    @classmethod
    def order_coordinates(
        cls,
        value: tuple[MolecularCoordinate, ...],
    ) -> tuple[MolecularCoordinate, ...]:
        indices = [coordinate.atom_index for coordinate in value]
        if sorted(indices) != list(range(len(value))):
            raise ValueError("Conformer atom indices must be contiguous from zero.")
        return tuple(sorted(value, key=lambda coordinate: coordinate.atom_index))

    @model_validator(mode="after")
    def validate_energy_unit(self) -> Self:
        if (self.energy is None) != (self.energy_unit is None):
            raise ValueError("Conformer energies and units must be declared together.")
        return self


class MolecularConformerSet(CanonicalModel):
    """A deterministic set of three-dimensional conformers."""

    schema_version: CapabilityVersion
    conformer_set_identifier: str
    source_graph: MolecularGraph
    coordinate_unit: Literal["angstrom"] = "angstrom"
    generation_method: str
    force_field: Literal["mmff94s", "uff", "none"]
    random_seed: int = Field(ge=0, le=2_147_483_647)
    requested_conformer_count: int = Field(gt=0)
    conformers: tuple[MolecularConformer, ...] = Field(min_length=1)

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(
        cls,
        value: CapabilityVersion,
    ) -> CapabilityVersion:
        return _validate_schema_version(value)

    @field_validator("conformer_set_identifier", "generation_method")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="molecular conformer identifier")

    @field_validator("conformers")
    @classmethod
    def order_conformers(
        cls,
        value: tuple[MolecularConformer, ...],
    ) -> tuple[MolecularConformer, ...]:
        identifiers = [conformer.conformer_identifier for conformer in value]
        indices = [conformer.conformer_index for conformer in value]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Molecular conformer identifiers must be unique.")
        if sorted(indices) != list(range(len(value))):
            raise ValueError("Conformer indices must be contiguous from zero.")
        return tuple(sorted(value, key=lambda conformer: conformer.conformer_index))

    @model_validator(mode="after")
    def validate_conformer_shapes(self) -> Self:
        atom_count = len(self.source_graph.atoms)
        if any(
            len(conformer.coordinates) != atom_count for conformer in self.conformers
        ):
            raise ValueError("Every conformer must contain one coordinate per atom.")
        if len(self.conformers) > self.requested_conformer_count:
            raise ValueError("Generated conformers cannot exceed the requested count.")
        return self


class MolecularGeometryFinding(CanonicalModel):
    """One bounded geometry-validation finding."""

    finding_code: str
    severity: Literal["warning", "error"]
    message: str = Field(min_length=1, max_length=1024)
    conformer_index: int = Field(ge=0)
    atom_indices: tuple[int, ...] = ()
    observed_value: float | None = None
    expected_minimum: float | None = None
    expected_maximum: float | None = None
    unit: Literal["angstrom"] | None = None

    @field_validator("finding_code")
    @classmethod
    def validate_finding_code(cls, value: str) -> str:
        return validate_identifier(value, label="geometry finding code")

    @field_validator("atom_indices")
    @classmethod
    def order_atom_indices(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if any(index < 0 for index in value):
            raise ValueError("Geometry finding atom indices cannot be negative.")
        if len(value) != len(set(value)):
            raise ValueError("Geometry finding atom indices must be unique.")
        return tuple(sorted(value))

    @field_validator("observed_value", "expected_minimum", "expected_maximum")
    @classmethod
    def validate_measurement(cls, value: float | None) -> float | None:
        if value is None:
            return None
        return _finite(value, label="Geometry validation measurement")

    @model_validator(mode="after")
    def validate_bounds(self) -> Self:
        if (
            self.expected_minimum is not None
            and self.expected_maximum is not None
            and self.expected_minimum > self.expected_maximum
        ):
            raise ValueError("Geometry finding bounds are invalid.")
        return self


class MolecularGeometryValidation(CanonicalModel):
    """Machine-readable validation of a conformer set."""

    schema_version: CapabilityVersion
    validation_identifier: str
    conformer_set_identifier: str
    valid: bool
    evaluated_conformer_count: int = Field(gt=0)
    atom_count: int = Field(gt=0)
    findings: tuple[MolecularGeometryFinding, ...] = ()

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(
        cls,
        value: CapabilityVersion,
    ) -> CapabilityVersion:
        return _validate_schema_version(value)

    @field_validator("validation_identifier", "conformer_set_identifier")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="geometry validation identifier")

    @field_validator("findings")
    @classmethod
    def order_findings(
        cls,
        value: tuple[MolecularGeometryFinding, ...],
    ) -> tuple[MolecularGeometryFinding, ...]:
        return tuple(
            sorted(
                value,
                key=lambda finding: (
                    finding.conformer_index,
                    finding.severity,
                    finding.finding_code,
                    finding.atom_indices,
                ),
            )
        )

    @model_validator(mode="after")
    def validate_outcome(self) -> Self:
        has_error = any(finding.severity == "error" for finding in self.findings)
        if self.valid == has_error:
            raise ValueError("Geometry validity must agree with error findings.")
        return self


class MolecularFragment(CanonicalModel):
    """One disconnected molecular fragment in source-atom order."""

    fragment_identifier: str
    fragment_index: int = Field(ge=0)
    source_atom_indices: tuple[int, ...] = Field(min_length=1)
    canonical_smiles: str = Field(min_length=1, max_length=16_384)
    formal_charge: int
    heavy_atom_count: int = Field(ge=0)

    @field_validator("fragment_identifier")
    @classmethod
    def validate_fragment_identifier(cls, value: str) -> str:
        return validate_identifier(value, label="molecular fragment identifier")

    @field_validator("source_atom_indices")
    @classmethod
    def order_source_atom_indices(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if any(index < 0 for index in value):
            raise ValueError("Fragment source atom indices cannot be negative.")
        if len(value) != len(set(value)):
            raise ValueError("Fragment source atom indices must be unique.")
        return tuple(sorted(value))

    @field_validator("canonical_smiles")
    @classmethod
    def normalize_smiles(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Fragment SMILES cannot be blank.")
        return normalized


class MolecularFragmentSet(CanonicalModel):
    """Deterministic disconnected-component fragmentation result."""

    schema_version: CapabilityVersion
    fragment_set_identifier: str
    source_graph_identifier: str
    fragments: tuple[MolecularFragment, ...] = Field(min_length=1)

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(
        cls,
        value: CapabilityVersion,
    ) -> CapabilityVersion:
        return _validate_schema_version(value)

    @field_validator("fragment_set_identifier", "source_graph_identifier")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="molecular fragment identifier")

    @field_validator("fragments")
    @classmethod
    def order_fragments(
        cls,
        value: tuple[MolecularFragment, ...],
    ) -> tuple[MolecularFragment, ...]:
        identifiers = [fragment.fragment_identifier for fragment in value]
        indices = [fragment.fragment_index for fragment in value]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Molecular fragment identifiers must be unique.")
        if sorted(indices) != list(range(len(value))):
            raise ValueError("Fragment indices must be contiguous from zero.")
        source_indices = tuple(
            atom_index
            for fragment in value
            for atom_index in fragment.source_atom_indices
        )
        if len(source_indices) != len(set(source_indices)):
            raise ValueError("Molecular fragments cannot overlap source atoms.")
        return tuple(sorted(value, key=lambda fragment: fragment.fragment_index))


class MolecularScaffold(CanonicalModel):
    """Bemis-Murcko scaffold projected back to source atoms."""

    schema_version: CapabilityVersion
    scaffold_identifier: str
    source_graph_identifier: str
    canonical_smiles: str | None = Field(default=None, max_length=16_384)
    source_atom_indices: tuple[int, ...] = ()
    heavy_atom_count: int = Field(ge=0)
    empty: bool

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(
        cls,
        value: CapabilityVersion,
    ) -> CapabilityVersion:
        return _validate_schema_version(value)

    @field_validator("scaffold_identifier", "source_graph_identifier")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="molecular scaffold identifier")

    @field_validator("canonical_smiles")
    @classmethod
    def normalize_smiles(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("source_atom_indices")
    @classmethod
    def order_source_atom_indices(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if any(index < 0 for index in value):
            raise ValueError("Scaffold source atom indices cannot be negative.")
        if len(value) != len(set(value)):
            raise ValueError("Scaffold source atom indices must be unique.")
        return tuple(sorted(value))

    @model_validator(mode="after")
    def validate_empty_scaffold(self) -> Self:
        if self.empty:
            if self.canonical_smiles is not None or self.source_atom_indices:
                raise ValueError("Empty scaffolds cannot contain atoms or SMILES.")
            if self.heavy_atom_count != 0:
                raise ValueError("Empty scaffolds cannot contain heavy atoms.")
        elif self.canonical_smiles is None or not self.source_atom_indices:
            raise ValueError("Nonempty scaffolds require SMILES and source atoms.")
        return self
