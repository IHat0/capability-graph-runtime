"""Shared covalent QM/MM environment and electronic-state preparation.

The resolver and the Phase-8 execution path use this module so charge/spin
preflight cannot silently diverge from execution semantics.
"""

from __future__ import annotations

import hashlib
import io
import math
from dataclasses import dataclass
from typing import Literal

from cgr.electronic_structure.qm_region import (
    QMRegionPreparationError,
    prepare_qm_region,
)
from cgr.kernel.contracts import CapabilityVersion
from cgr.molecular import (
    MolecularEnvironment,
    MolecularSimulationAtom,
    MolecularSimulationBond,
    MolecularVector3,
)


_VERSION = CapabilityVersion(major=1, minor=0, patch=0)


class CovalentQMMMStateError(ValueError):
    """Controlled failure while preparing covalent QM/MM state evidence."""


@dataclass(frozen=True, slots=True)
class CovalentQMMMSelection:
    """Source indices and reviewed nucleophile charge required by the runtime."""

    protein_nucleophile_source_index: int
    ligand_electrophile_source_index: int
    ligand_leaving_group_source_index: int
    protein_nucleophile_formal_charge: int


@dataclass(frozen=True, slots=True)
class CovalentQMMMEnvironmentResult:
    """Shared environment plus source-index and charge-evidence bookkeeping."""

    environment: MolecularEnvironment
    indices: dict[str, int]
    partial_charges: tuple[float, ...]
    protein_atom_count: int
    protein_charge_explicit: tuple[bool, ...]


@dataclass(frozen=True, slots=True)
class CovalentQMStateAssessment:
    """Electronic state derived with the same QM-region logic as execution."""

    molecular_charge: int
    electron_count: int
    spin_candidates: tuple[int, ...]
    selected_particle_indices: tuple[int, ...]
    confidence: Literal["high", "runtime_assumption"]
    implicit_neutral_protein_source_indices: tuple[int, ...]


def _stable_identifier(prefix: str, *values: object) -> str:
    encoded = "\x1f".join(str(value) for value in values).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(encoded).hexdigest()[:32]}"


def _pdb_charge(raw: str) -> tuple[int, bool]:
    value = raw.strip()
    if not value:
        # This preserves the current Phase-8 runtime behaviour.  Callers are
        # told that the value is assumed rather than explicit.
        return 0, False

    if (
        len(value) == 2
        and value[0].isdigit()
        and value[1] in {"+", "-"}
    ):
        magnitude = int(value[0])
        return (
            magnitude if value[1] == "+" else -magnitude,
            True,
        )

    raise CovalentQMMMStateError(
        "Protein PDB contains an invalid formal-charge field."
    )


def _protein_atoms(
    payload: bytes,
) -> tuple[tuple[dict[str, object], ...], tuple[bool, ...]]:
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise CovalentQMMMStateError(
            "Protein PDB payload must be valid UTF-8 text."
        ) from None

    from rdkit import Chem

    table = Chem.GetPeriodicTable()
    atoms: list[dict[str, object]] = []
    explicit: list[bool] = []

    try:
        for line in text.splitlines():
            if not line.startswith(("ATOM  ", "HETATM")):
                continue

            element = line[76:78].strip() if len(line) >= 78 else ""
            if not element:
                element = "".join(
                    character
                    for character in line[12:16]
                    if character.isalpha()
                )[:2]

            if not element:
                raise CovalentQMMMStateError(
                    "Protein PDB atom has no resolvable element."
                )

            element = element[0].upper() + element[1:].lower()
            formal_charge, charge_explicit = _pdb_charge(
                line[78:80] if len(line) >= 80 else ""
            )

            atomic_number = int(table.GetAtomicNumber(element))
            if atomic_number <= 0:
                raise CovalentQMMMStateError(
                    "Protein PDB contains an unsupported element."
                )

            atoms.append(
                {
                    "serial": int(line[6:11]),
                    "atom_name": line[12:16].strip(),
                    "residue_name": line[17:20].strip() or "UNK",
                    "chain": line[21:22].strip() or "blank",
                    "sequence": line[22:26].strip()
                    or str(len(atoms) + 1),
                    "x": float(line[30:38]),
                    "y": float(line[38:46]),
                    "z": float(line[46:54]),
                    "element": element,
                    "atomic_number": atomic_number,
                    "formal_charge": formal_charge,
                }
            )
            explicit.append(charge_explicit)
    except (IndexError, ValueError):
        raise CovalentQMMMStateError(
            "Protein PDB contains an invalid atomic record."
        ) from None

    if not atoms:
        raise CovalentQMMMStateError(
            "Protein PDB contains no atoms."
        )

    return tuple(atoms), tuple(explicit)


def _ligand_molecule(payload: bytes, media_type: str):
    try:
        from rdkit import Chem
    except ImportError:
        raise CovalentQMMMStateError(
            "RDKit is required for covalent QM/MM state preparation."
        ) from None

    media = media_type.casefold()

    try:
        if "sdf" in media:
            supplier = Chem.ForwardSDMolSupplier(
                io.BytesIO(payload),
                sanitize=True,
                removeHs=False,
                strictParsing=True,
            )
            molecules = tuple(
                molecule
                for molecule in supplier
                if molecule is not None
            )
            if len(molecules) != 1:
                raise CovalentQMMMStateError(
                    "Ligand SDF must contain exactly one valid molecule."
                )
            molecule = molecules[0]
        elif "mol" in media:
            text = payload.decode("utf-8", errors="strict")
            molecule = Chem.MolFromMolBlock(
                text,
                sanitize=True,
                removeHs=False,
                strictParsing=True,
            )
            if molecule is None:
                raise CovalentQMMMStateError(
                    "Ligand MOL payload is invalid."
                )
        else:
            raise CovalentQMMMStateError(
                "Covalent QM/MM state preparation requires MOL or SDF ligand input."
            )
    except UnicodeDecodeError:
        raise CovalentQMMMStateError(
            "Ligand structure payload must be valid UTF-8 text."
        ) from None

    if molecule.GetNumConformers() != 1:
        raise CovalentQMMMStateError(
            "Covalent QM/MM state preparation requires one ligand geometry."
        )

    return molecule


def build_covalent_qmmm_environment(
    *,
    protein_payload: bytes,
    protein_content_sha256: str,
    ligand_payload: bytes,
    ligand_media_type: str,
    ligand_content_sha256: str,
    selection: CovalentQMMMSelection,
) -> CovalentQMMMEnvironmentResult:
    """Build the environment used by covalent Phase-8 execution."""

    from rdkit.Chem import GetPeriodicTable

    protein_atoms, protein_charge_explicit = _protein_atoms(
        protein_payload
    )
    ligand = _ligand_molecule(
        ligand_payload,
        ligand_media_type,
    )

    protein_count = len(protein_atoms)

    if not 0 <= selection.protein_nucleophile_source_index < protein_count:
        raise CovalentQMMMStateError(
            "Protein nucleophile source index is invalid."
        )

    if not (
        0
        <= selection.ligand_electrophile_source_index
        < ligand.GetNumAtoms()
    ):
        raise CovalentQMMMStateError(
            "Ligand electrophile source index is invalid."
        )

    if not (
        0
        <= selection.ligand_leaving_group_source_index
        < ligand.GetNumAtoms()
    ):
        raise CovalentQMMMStateError(
            "Ligand leaving-group source index is invalid."
        )

    conformer = ligand.GetConformer()
    offset = protein_count

    atom_rows: list[dict[str, object]] = [
        dict(item)
        for item in protein_atoms
    ]

    for atom in ligand.GetAtoms():
        point = conformer.GetAtomPosition(atom.GetIdx())
        atom_rows.append(
            {
                "atomic_number": atom.GetAtomicNum(),
                "element": atom.GetSymbol(),
                "atom_name": f"L{atom.GetIdx() + 1}",
                "residue_name": "LIG",
                "sequence": "2",
                "chain": "L",
                "formal_charge": atom.GetFormalCharge(),
                "x": point.x,
                "y": point.y,
                "z": point.z,
            }
        )

    bonds: set[tuple[int, int, int | None]] = set()

    for left in range(protein_count):
        for right in range(left + 1, protein_count):
            distance = math.dist(
                tuple(
                    float(protein_atoms[left][axis])
                    for axis in ("x", "y", "z")
                ),
                tuple(
                    float(protein_atoms[right][axis])
                    for axis in ("x", "y", "z")
                ),
            )

            radii = {
                "H": 0.37,
                "C": 0.77,
                "N": 0.75,
                "O": 0.73,
                "S": 1.02,
            }
            cutoff = 1.25 * (
                radii.get(
                    str(protein_atoms[left]["element"]),
                    0.8,
                )
                + radii.get(
                    str(protein_atoms[right]["element"]),
                    0.8,
                )
            )

            if distance <= cutoff:
                bonds.add((left, right, 1))

    for bond in ligand.GetBonds():
        bonds.add(
            (
                offset + int(bond.GetBeginAtomIdx()),
                offset + int(bond.GetEndAtomIdx()),
                int(round(bond.GetBondTypeAsDouble())),
            )
        )

    residue_values = tuple(
        dict.fromkeys(
            (
                str(row["chain"]),
                str(row["sequence"]),
                str(row["residue_name"]),
            )
            for row in atom_rows
        )
    )
    chain_values = tuple(
        dict.fromkeys(
            str(row["chain"])
            for row in atom_rows
        )
    )

    residue_index = {
        value: index
        for index, value in enumerate(residue_values)
    }
    chain_index = {
        value: index
        for index, value in enumerate(chain_values)
    }

    nucleophile_index = (
        selection.protein_nucleophile_source_index
    )
    electrophile_index = (
        offset + selection.ligand_electrophile_source_index
    )
    leaving_index = (
        offset + selection.ligand_leaving_group_source_index
    )

    charges = [0.0] * len(atom_rows)
    charges[nucleophile_index] = float(
        selection.protein_nucleophile_formal_charge
    )

    # Preserve current Phase-8 MM bookkeeping exactly.
    for index, row in enumerate(
        atom_rows[:protein_count]
    ):
        name = str(row["atom_name"]).upper()
        if name == "CA":
            charges[index] = 0.1
        elif name == "C":
            charges[index] = 0.5
        elif name == "O":
            charges[index] = -0.8

    environment_total_charge = sum(
        int(row["formal_charge"])
        for row in atom_rows
    )
    environment_total_charge += (
        int(selection.protein_nucleophile_formal_charge)
        - int(atom_rows[nucleophile_index]["formal_charge"])
    )

    correction = (
        float(environment_total_charge)
        - sum(charges)
    )
    charges[-1] += correction

    digest = hashlib.sha256(
        (
            protein_content_sha256
            + ligand_content_sha256
        ).encode()
    ).hexdigest()

    environment = MolecularEnvironment(
        schema_version=_VERSION,
        environment_identifier=_stable_identifier(
            "covalent-ts-environment",
            digest,
        ),
        environment_type="vacuum",
        source_conformer_set_identifier=_stable_identifier(
            "covalent-ts-coordinates",
            digest,
        ),
        source_conformer_index=0,
        force_field_selection_identifier=(
            "compact-active-site-openmm-force-field"
        ),
        atoms=tuple(
            MolecularSimulationAtom(
                particle_index=index,
                particle_kind="atom",
                atomic_number=int(row["atomic_number"]),
                element_symbol=str(row["element"]),
                atom_name=str(row["atom_name"]),
                residue_index=residue_index[
                    (
                        str(row["chain"]),
                        str(row["sequence"]),
                        str(row["residue_name"]),
                    )
                ],
                residue_name=str(row["residue_name"]),
                residue_identifier=(
                    f"residue-{str(row['chain']).lower()}-"
                    f"{str(row['sequence']).lower()}-"
                    f"{str(row['residue_name']).lower()}"
                ),
                chain_index=chain_index[
                    str(row["chain"])
                ],
                chain_identifier=(
                    f"chain-{str(row['chain']).lower()}"
                ),
                source_atom_index=index,
                formal_charge=(
                    int(
                        selection.protein_nucleophile_formal_charge
                    )
                    if index == nucleophile_index
                    else int(row["formal_charge"])
                ),
            )
            for index, row in enumerate(atom_rows)
        ),
        bonds=tuple(
            MolecularSimulationBond(
                atom_index_a=min(a, b),
                atom_index_b=max(a, b),
                order=order,
            )
            for a, b, order in sorted(bonds)
        ),
        positions=tuple(
            MolecularVector3(
                x=float(row["x"]) / 10.0,
                y=float(row["y"]) / 10.0,
                z=float(row["z"]) / 10.0,
            )
            for row in atom_rows
        ),
        source_solute_atom_count=len(atom_rows),
    )

    table = GetPeriodicTable()
    for row in atom_rows:
        if (
            table.GetAtomicWeight(
                int(row["atomic_number"])
            )
            <= 0
        ):
            raise CovalentQMMMStateError(
                "The active site has an unsupported element."
            )

    return CovalentQMMMEnvironmentResult(
        environment=environment,
        indices={
            "nucleophile": nucleophile_index,
            "electrophile": electrophile_index,
            "leaving_group": leaving_index,
        },
        partial_charges=tuple(charges),
        protein_atom_count=protein_count,
        protein_charge_explicit=protein_charge_explicit,
    )


def assess_covalent_qm_state(
    result: CovalentQMMMEnvironmentResult,
) -> CovalentQMStateAssessment:
    """Run the same QM-region state derivation used by Phase-8 execution."""

    try:
        prepared = prepare_qm_region(
            result.environment,
            source_artifact_identifier=_stable_identifier(
                "covalent-state-source",
                result.environment.environment_identifier,
            ),
            seed_particle_indices=(
                result.indices["nucleophile"],
                result.indices["electrophile"],
            ),
            expansion_bond_depth=1,
            include_seed_residues=False,
            maximum_transition_metal_spin=0,
        )
    except QMRegionPreparationError as error:
        raise CovalentQMMMStateError(str(error)) from None

    assessment = prepared.preparation.charge_spin_assessment

    selected_protein_indices = tuple(
        index
        for index in prepared.preparation.selected_particle_indices
        if index < result.protein_atom_count
    )

    implicit_neutral = tuple(
        index
        for index in selected_protein_indices
        if (
            index != result.indices["nucleophile"]
            and not result.protein_charge_explicit[index]
        )
    )

    return CovalentQMStateAssessment(
        molecular_charge=assessment.molecular_charge,
        electron_count=assessment.electron_count,
        spin_candidates=assessment.spin_candidates,
        selected_particle_indices=(
            prepared.preparation.selected_particle_indices
        ),
        confidence=(
            "runtime_assumption"
            if implicit_neutral
            else "high"
        ),
        implicit_neutral_protein_source_indices=(
            implicit_neutral
        ),
    )


__all__ = [
    "CovalentQMMMEnvironmentResult",
    "CovalentQMMMSelection",
    "CovalentQMMMStateError",
    "CovalentQMStateAssessment",
    "assess_covalent_qm_state",
    "build_covalent_qmmm_environment",
]
