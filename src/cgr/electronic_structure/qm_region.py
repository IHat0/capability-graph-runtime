"""Deterministic QM-region construction and boundary preparation."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from cgr.kernel.contracts import CapabilityVersion
from cgr.molecular import MolecularEnvironment
from cgr.science import CanonicalModel
from cgr.science.canonical import validate_identifier

from .contracts import ElectronicAtom, ElectronicMolecule


_SCHEMA_VERSION = CapabilityVersion(major=1, minor=0, patch=0)

# Transition-metal ranges used only to decide whether multiple spin
# candidates must be retained. They are not oxidation-state inference.
_TRANSITION_METALS = frozenset(
    (*range(21, 31), *range(39, 49), *range(72, 81))
)


class QMRegionPreparationError(ValueError):
    """Controlled scientific failure during QM-region preparation."""


def _stable_identifier(prefix: str, *values: object) -> str:
    encoded = "\x1f".join(str(value) for value in values).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(encoded).hexdigest()[:32]}"


def _finite(value: float, *, label: str) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{label} must be finite.")
    return float(value)


class ElectronicQMBoundaryLink(CanonicalModel):
    """One QM/MM covalent cut replaced by an explicit link hydrogen."""

    qm_particle_index: int = Field(ge=0)
    mm_particle_index: int = Field(ge=0)
    source_bond_order: int | None = Field(default=None, ge=1, le=3)
    link_atom_index: int = Field(ge=0)
    link_element_symbol: Literal["H"] = "H"
    x_angstrom: float
    y_angstrom: float
    z_angstrom: float
    link_distance_angstrom: float = Field(gt=0, le=2.0)

    @field_validator(
        "x_angstrom",
        "y_angstrom",
        "z_angstrom",
        "link_distance_angstrom",
    )
    @classmethod
    def validate_scalars(cls, value: float) -> float:
        return _finite(value, label="QM boundary-link value")


class ElectronicQMChargeSpinAssessment(CanonicalModel):
    """Charge evidence and bounded spin candidates for one prepared QM region."""

    molecular_charge: int
    charge_source: Literal["selected_atom_formal_charges"]
    electron_count: int = Field(gt=0)
    contains_transition_metal: bool
    spin_candidates: tuple[int, ...] = Field(min_length=1)

    @field_validator("spin_candidates")
    @classmethod
    def validate_spin_candidates(
        cls,
        value: tuple[int, ...],
    ) -> tuple[int, ...]:
        if (
            any(spin < 0 for spin in value)
            or len(value) != len(set(value))
        ):
            raise ValueError(
                "QM-region spin candidates must be unique and nonnegative."
            )
        return tuple(sorted(value))

    @model_validator(mode="after")
    def validate_parity(self) -> Self:
        parity = self.electron_count % 2
        if any(
            spin > self.electron_count or spin % 2 != parity
            for spin in self.spin_candidates
        ):
            raise ValueError(
                "QM-region spin candidates are incompatible with electron count."
            )
        return self


class ElectronicQMRegionPreparation(CanonicalModel):
    """Auditable QM-region membership, capping, charge and spin evidence."""

    schema_version: CapabilityVersion
    preparation_identifier: str
    environment_identifier: str
    source_artifact_identifier: str
    seed_particle_indices: tuple[int, ...] = Field(min_length=1)
    selected_particle_indices: tuple[int, ...] = Field(min_length=1)
    selected_source_atom_indices: tuple[int, ...] = ()
    included_residue_identifiers: tuple[str, ...] = ()
    expansion_bond_depth: int = Field(ge=0, le=8)
    include_seed_residues: bool
    boundary_policy: Literal["hydrogen_link_atom"]
    boundary_links: tuple[ElectronicQMBoundaryLink, ...] = ()
    charge_spin_assessment: ElectronicQMChargeSpinAssessment
    electronic_molecule_identifiers: tuple[str, ...] = Field(min_length=1)

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(
        cls,
        value: CapabilityVersion,
    ) -> CapabilityVersion:
        if value != _SCHEMA_VERSION:
            raise ValueError(
                "QM-region preparation schema version must be exactly 1.0.0."
            )
        return value

    @field_validator(
        "preparation_identifier",
        "environment_identifier",
        "source_artifact_identifier",
    )
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="QM-region identifier")

    @field_validator("included_residue_identifiers")
    @classmethod
    def validate_residue_identifiers(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        normalized = tuple(
            validate_identifier(item, label="QM-region residue identifier")
            for item in value
        )
        if len(normalized) != len(set(normalized)):
            raise ValueError("QM-region residue identifiers must be unique.")
        return tuple(sorted(normalized))

    @field_validator(
        "seed_particle_indices",
        "selected_particle_indices",
        "selected_source_atom_indices",
    )
    @classmethod
    def validate_indices(
        cls,
        value: tuple[int, ...],
    ) -> tuple[int, ...]:
        if any(index < 0 for index in value):
            raise ValueError("QM-region indices cannot be negative.")
        if len(value) != len(set(value)):
            raise ValueError("QM-region indices must be unique.")
        return tuple(sorted(value))

    @field_validator("electronic_molecule_identifiers")
    @classmethod
    def validate_molecule_identifiers(
        cls,
        value: tuple[str, ...],
    ) -> tuple[str, ...]:
        normalized = tuple(
            validate_identifier(item, label="QM-region molecule identifier")
            for item in value
        )
        if len(normalized) != len(set(normalized)):
            raise ValueError(
                "QM-region electronic molecule identifiers must be unique."
            )
        return normalized

    @model_validator(mode="after")
    def validate_membership(self) -> Self:
        selected = set(self.selected_particle_indices)

        if not set(self.seed_particle_indices).issubset(selected):
            raise ValueError(
                "Every QM-region seed must remain in the selected region."
            )

        expected_link_indices = list(
            range(
                len(self.selected_particle_indices),
                len(self.selected_particle_indices)
                + len(self.boundary_links),
            )
        )
        actual_link_indices = [
            link.link_atom_index for link in self.boundary_links
        ]

        if actual_link_indices != expected_link_indices:
            raise ValueError(
                "QM-region link-atom indices must directly follow selected atoms."
            )

        if any(
            link.qm_particle_index not in selected
            or link.mm_particle_index in selected
            for link in self.boundary_links
        ):
            raise ValueError(
                "QM-region boundary links do not match region membership."
            )

        if (
            len(self.electronic_molecule_identifiers)
            != len(self.charge_spin_assessment.spin_candidates)
        ):
            raise ValueError(
                "QM-region molecule candidates must match spin candidates."
            )

        return self


@dataclass(frozen=True, slots=True)
class QMRegionPreparationResult:
    preparation: ElectronicQMRegionPreparation
    molecules: tuple[ElectronicMolecule, ...]


def _alpha_beta_counts(
    electron_count: int,
    spin: int,
) -> tuple[int, int]:
    if (
        spin < 0
        or spin > electron_count
        or (electron_count - spin) % 2
    ):
        raise QMRegionPreparationError(
            "Spin is incompatible with QM-region electron count."
        )

    alpha = (electron_count + spin) // 2
    return alpha, electron_count - alpha


def prepare_qm_region(
    environment: MolecularEnvironment,
    *,
    source_artifact_identifier: str,
    seed_particle_indices: tuple[int, ...],
    expansion_bond_depth: int,
    include_seed_residues: bool,
    link_distance_angstrom: float = 1.09,
    maximum_transition_metal_spin: int = 6,
) -> QMRegionPreparationResult:
    """Build a capped QM region from an existing simulation environment."""

    if not isinstance(environment, MolecularEnvironment):
        raise QMRegionPreparationError(
            "QM-region preparation requires a molecular environment."
        )

    validate_identifier(
        source_artifact_identifier,
        label="QM-region source artifact identifier",
    )

    seeds = tuple(sorted(seed_particle_indices))

    if (
        not seeds
        or len(seeds) != len(set(seeds))
        or any(index < 0 for index in seeds)
    ):
        raise QMRegionPreparationError(
            "QM-region seeds must be unique nonnegative particle indices."
        )

    if not 0 <= expansion_bond_depth <= 8:
        raise QMRegionPreparationError(
            "QM-region bond expansion depth is outside the supported range."
        )

    if (
        not math.isfinite(link_distance_angstrom)
        or not 0.5 <= link_distance_angstrom <= 2.0
    ):
        raise QMRegionPreparationError(
            "QM-region link distance is outside the supported range."
        )

    if (
        maximum_transition_metal_spin < 0
        or maximum_transition_metal_spin > 12
    ):
        raise QMRegionPreparationError(
            "Maximum transition-metal spin is outside the supported range."
        )

    solute_count = environment.source_solute_atom_count

    if any(index >= solute_count for index in seeds):
        raise QMRegionPreparationError(
            "QM-region seeds must resolve to source-solute particles."
        )

    atoms_by_index = {
        atom.particle_index: atom
        for atom in environment.atoms
    }

    for index in seeds:
        atom = atoms_by_index[index]
        if atom.particle_kind != "atom":
            raise QMRegionPreparationError(
                "QM regions cannot contain extra particles."
            )

    selected = set(seeds)

    if include_seed_residues:
        seed_residues = {
            atoms_by_index[index].residue_identifier
            for index in seeds
        }

        selected.update(
            atom.particle_index
            for atom in environment.atoms[:solute_count]
            if atom.residue_identifier in seed_residues
        )

    adjacency: dict[int, set[int]] = {
        index: set()
        for index in range(solute_count)
    }

    for bond in environment.bonds:
        a = bond.atom_index_a
        b = bond.atom_index_b

        if a < solute_count and b < solute_count:
            adjacency[a].add(b)
            adjacency[b].add(a)

    frontier = set(selected)

    for _ in range(expansion_bond_depth):
        expanded: set[int] = set()

        for index in frontier:
            expanded.update(adjacency[index])

        expanded.difference_update(selected)

        if not expanded:
            break

        selected.update(expanded)
        frontier = expanded

    selected_indices = tuple(sorted(selected))

    for index in selected_indices:
        atom = atoms_by_index[index]

        if (
            atom.particle_kind != "atom"
            or atom.atomic_number is None
            or atom.element_symbol is None
        ):
            raise QMRegionPreparationError(
                "Selected QM-region particles must have atomic identities."
            )

    boundary_bonds = []

    for bond in environment.bonds:
        a_selected = bond.atom_index_a in selected
        b_selected = bond.atom_index_b in selected

        if a_selected == b_selected:
            continue

        qm_index = (
            bond.atom_index_a
            if a_selected
            else bond.atom_index_b
        )
        mm_index = (
            bond.atom_index_b
            if a_selected
            else bond.atom_index_a
        )

        if mm_index >= solute_count:
            raise QMRegionPreparationError(
                "A QM region cannot cut a covalent solute/environment bond."
            )

        if bond.order is not None and bond.order != 1:
            raise QMRegionPreparationError(
                "Hydrogen link atoms cannot cap an explicit multiple bond."
            )

        qm_atom = atoms_by_index[qm_index]
        mm_atom = atoms_by_index[mm_index]

        if (
            qm_atom.atomic_number in _TRANSITION_METALS
            or mm_atom.atomic_number in _TRANSITION_METALS
        ):
            raise QMRegionPreparationError(
                "QM/MM boundaries cannot cut a transition-metal coordination bond."
            )

        boundary_bonds.append(
            (qm_index, mm_index, bond.order)
        )

    boundary_bonds.sort(
        key=lambda item: (item[0], item[1])
    )

    boundary_links: list[ElectronicQMBoundaryLink] = []

    for offset, (qm_index, mm_index, bond_order) in enumerate(
        boundary_bonds
    ):
        qm_position = environment.positions[qm_index]
        mm_position = environment.positions[mm_index]

        dx = mm_position.x - qm_position.x
        dy = mm_position.y - qm_position.y
        dz = mm_position.z - qm_position.z

        distance_nm = math.sqrt(
            dx * dx + dy * dy + dz * dz
        )

        if not math.isfinite(distance_nm) or distance_nm <= 1e-12:
            raise QMRegionPreparationError(
                "QM/MM boundary bond has invalid geometry."
            )

        link_distance_nm = link_distance_angstrom / 10.0
        scale = link_distance_nm / distance_nm

        link_x_nm = qm_position.x + scale * dx
        link_y_nm = qm_position.y + scale * dy
        link_z_nm = qm_position.z + scale * dz

        boundary_links.append(
            ElectronicQMBoundaryLink(
                qm_particle_index=qm_index,
                mm_particle_index=mm_index,
                source_bond_order=bond_order,
                link_atom_index=len(selected_indices) + offset,
                x_angstrom=10.0 * link_x_nm,
                y_angstrom=10.0 * link_y_nm,
                z_angstrom=10.0 * link_z_nm,
                link_distance_angstrom=link_distance_angstrom,
            )
        )

    selected_atoms = tuple(
        atoms_by_index[index]
        for index in selected_indices
    )

    formal_charges = tuple(
        atom.formal_charge
        for atom in selected_atoms
    )

    if any(charge is None for charge in formal_charges):
        raise QMRegionPreparationError(
            "QM-region charge cannot be determined because a selected atom "
            "has no formal-charge evidence."
        )

    molecular_charge = sum(
        int(charge)
        for charge in formal_charges
        if charge is not None
    )

    electronic_atoms: list[ElectronicAtom] = []

    for electronic_index, particle_index in enumerate(
        selected_indices
    ):
        source_atom = atoms_by_index[particle_index]
        position = environment.positions[particle_index]

        electronic_atoms.append(
            ElectronicAtom(
                atom_index=electronic_index,
                atomic_number=int(source_atom.atomic_number),
                element_symbol=str(source_atom.element_symbol),
                x_angstrom=10.0 * position.x,
                y_angstrom=10.0 * position.y,
                z_angstrom=10.0 * position.z,
                source_atom_index=source_atom.source_atom_index,
            )
        )

    for link in boundary_links:
        electronic_atoms.append(
            ElectronicAtom(
                atom_index=link.link_atom_index,
                atomic_number=1,
                element_symbol="H",
                x_angstrom=link.x_angstrom,
                y_angstrom=link.y_angstrom,
                z_angstrom=link.z_angstrom,
                source_atom_index=None,
            )
        )

    electron_count = (
        sum(atom.atomic_number for atom in electronic_atoms)
        - molecular_charge
    )

    if electron_count <= 0:
        raise QMRegionPreparationError(
            "Prepared QM region has an invalid electron count."
        )

    contains_transition_metal = any(
        atom.atomic_number in _TRANSITION_METALS
        for atom in selected_atoms
    )

    parity = electron_count % 2

    if contains_transition_metal:
        spin_candidates = tuple(
            spin
            for spin in range(
                parity,
                min(
                    maximum_transition_metal_spin,
                    electron_count,
                )
                + 1,
                2,
            )
        )
    else:
        spin_candidates = (parity,)

    if not spin_candidates:
        raise QMRegionPreparationError(
            "No valid QM-region spin candidate could be constructed."
        )

    preparation_identifier = _stable_identifier(
        "electronic-qm-region",
        environment.environment_identifier,
        *seeds,
        *selected_indices,
        expansion_bond_depth,
        include_seed_residues,
        molecular_charge,
        *spin_candidates,
        *(
            (
                link.qm_particle_index,
                link.mm_particle_index,
                link.link_atom_index,
                link.x_angstrom,
                link.y_angstrom,
                link.z_angstrom,
            )
            for link in boundary_links
        ),
    )

    molecules: list[ElectronicMolecule] = []

    for spin in spin_candidates:
        alpha, beta = _alpha_beta_counts(
            electron_count,
            spin,
        )

        molecule_identifier = _stable_identifier(
            "electronic-molecule",
            preparation_identifier,
            spin,
            *(
                atom.model_dump_json()
                for atom in electronic_atoms
            ),
        )

        molecules.append(
            ElectronicMolecule(
                schema_version=_SCHEMA_VERSION,
                molecule_identifier=molecule_identifier,
                source_artifact_identifier=source_artifact_identifier,
                source_geometry_identifier=(
                    environment.environment_identifier
                ),
                atoms=tuple(electronic_atoms),
                molecular_charge=molecular_charge,
                source_formal_charge=molecular_charge,
                spin=spin,
                electron_count=electron_count,
                alpha_electron_count=alpha,
                beta_electron_count=beta,
            )
        )

    preparation = ElectronicQMRegionPreparation(
        schema_version=_SCHEMA_VERSION,
        preparation_identifier=preparation_identifier,
        environment_identifier=environment.environment_identifier,
        source_artifact_identifier=source_artifact_identifier,
        seed_particle_indices=seeds,
        selected_particle_indices=selected_indices,
        selected_source_atom_indices=tuple(
            sorted(
                atom.source_atom_index
                for atom in selected_atoms
                if atom.source_atom_index is not None
            )
        ),
        included_residue_identifiers=tuple(
            sorted(
                {
                    atom.residue_identifier
                    for atom in selected_atoms
                }
            )
        ),
        expansion_bond_depth=expansion_bond_depth,
        include_seed_residues=include_seed_residues,
        boundary_policy="hydrogen_link_atom",
        boundary_links=tuple(boundary_links),
        charge_spin_assessment=ElectronicQMChargeSpinAssessment(
            molecular_charge=molecular_charge,
            charge_source="selected_atom_formal_charges",
            electron_count=electron_count,
            contains_transition_metal=contains_transition_metal,
            spin_candidates=spin_candidates,
        ),
        electronic_molecule_identifiers=tuple(
            molecule.molecule_identifier
            for molecule in molecules
        ),
    )

    return QMRegionPreparationResult(
        preparation=preparation,
        molecules=tuple(molecules),
    )
