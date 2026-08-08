"""Phase 8 scientific structural-selection foundation tests."""

from __future__ import annotations

import pytest

from cgr.kernel.contracts import CapabilityVersion
from cgr.molecular.scientific_selection import (
    MolecularScientificSelectionError,
    resolve_atom_selection,
    resolve_bond_selection,
    resolve_residue_selection,
)
from cgr.molecular.topology import (
    MolecularTopologyAtom,
    MolecularTopologyBond,
    MolecularTopologyChain,
    MolecularTopologyIndex,
    MolecularTopologyResidue,
)
from cgr.science.artifacts import ArtifactReference, CreationProvenance


VERSION = CapabilityVersion(major=1, minor=0, patch=0)
PROVENANCE = CreationProvenance(
    producer="phase8-scientific-selection-tests",
    producer_version=VERSION,
)


def _source_artifact() -> ArtifactReference:
    return ArtifactReference(
        artifact_identifier="structure-protein-test",
        schema_version=VERSION,
        artifact_type="molecular_structure",
        media_type="chemical/x-pdb",
        content_sha256="a" * 64,
        byte_size=1,
        provenance=PROVENANCE,
    )


def _topology() -> MolecularTopologyIndex:
    source = _source_artifact()

    atoms = (
        MolecularTopologyAtom(
            atom_identifier="atom-cys145-ca",
            atom_index=0,
            element_symbol="C",
            atom_name="CA",
            chain_identifier="chain-a",
            residue_identifier="residue-cys145",
            source_serial="1",
        ),
        MolecularTopologyAtom(
            atom_identifier="atom-cys145-sg",
            atom_index=1,
            element_symbol="S",
            atom_name="SG",
            chain_identifier="chain-a",
            residue_identifier="residue-cys145",
            source_serial="2",
        ),
        MolecularTopologyAtom(
            atom_identifier="atom-his41-ca",
            atom_index=2,
            element_symbol="C",
            atom_name="CA",
            chain_identifier="chain-a",
            residue_identifier="residue-his41",
            source_serial="3",
        ),
        MolecularTopologyAtom(
            atom_identifier="atom-his41-ne2",
            atom_index=3,
            element_symbol="N",
            atom_name="NE2",
            chain_identifier="chain-a",
            residue_identifier="residue-his41",
            source_serial="4",
        ),
        MolecularTopologyAtom(
            atom_identifier="atom-zinc",
            atom_index=4,
            element_symbol="Zn",
            atom_name="ZN",
            chain_identifier="chain-z",
            residue_identifier="residue-zinc",
            formal_charge=2,
            source_serial="5",
        ),
    )

    bonds = (
        MolecularTopologyBond(
            bond_identifier="bond-cys145-ca-sg",
            atom_identifier_a="atom-cys145-ca",
            atom_identifier_b="atom-cys145-sg",
            bond_order=1.0,
            bond_type="single",
        ),
        MolecularTopologyBond(
            bond_identifier="bond-his41-ca-ne2",
            atom_identifier_a="atom-his41-ca",
            atom_identifier_b="atom-his41-ne2",
            bond_order=1.0,
            bond_type="single",
        ),
    )

    residues = (
        MolecularTopologyResidue(
            residue_identifier="residue-cys145",
            residue_name="CYS",
            chain_identifier="chain-a",
            atom_identifiers=(
                "atom-cys145-ca",
                "atom-cys145-sg",
            ),
            source_sequence_identifier="145",
        ),
        MolecularTopologyResidue(
            residue_identifier="residue-his41",
            residue_name="HIS",
            chain_identifier="chain-a",
            atom_identifiers=(
                "atom-his41-ca",
                "atom-his41-ne2",
            ),
            source_sequence_identifier="41",
        ),
        MolecularTopologyResidue(
            residue_identifier="residue-zinc",
            residue_name="ZN",
            chain_identifier="chain-z",
            atom_identifiers=("atom-zinc",),
            source_sequence_identifier="301",
        ),
    )

    chains = (
        MolecularTopologyChain(
            chain_identifier="chain-a",
            atom_identifiers=(
                "atom-cys145-ca",
                "atom-cys145-sg",
                "atom-his41-ca",
                "atom-his41-ne2",
            ),
            residue_identifiers=(
                "residue-cys145",
                "residue-his41",
            ),
        ),
        MolecularTopologyChain(
            chain_identifier="chain-z",
            atom_identifiers=("atom-zinc",),
            residue_identifiers=("residue-zinc",),
        ),
    )

    return MolecularTopologyIndex(
        schema_version=VERSION,
        structure_identifier="structure-protein-test",
        source_structure=source.pointer,
        structure_format="pdb",
        parser_identifier="phase8-test-parser",
        parser_version="1.0.0",
        atoms=atoms,
        bonds=bonds,
        residues=residues,
        chains=chains,
        model_count=1,
        frame_count=1,
        coordinate_unit="angstrom",
    )


def test_resolves_named_residue_by_sequence_and_chain() -> None:
    topology = _topology()

    result = resolve_residue_selection(
        topology,
        residue_name="Cys",
        source_sequence_identifier="145",
        chain_identifier="chain-a",
    )

    assert result.selection_kind == "residue"
    assert result.residue_identifiers == ("residue-cys145",)
    assert result.atom_identifiers == (
        "atom-cys145-ca",
        "atom-cys145-sg",
    )
    assert result.bond_identifiers == ("bond-cys145-ca-sg",)


def test_resolves_named_atom_inside_resolved_residue() -> None:
    topology = _topology()
    residue = resolve_residue_selection(
        topology,
        residue_name="CYS",
        source_sequence_identifier="145",
        chain_identifier="chain-a",
    )

    result = resolve_atom_selection(
        topology,
        atom_name="SG",
        residue_identifiers=residue.residue_identifiers,
        require_unique=True,
    )

    assert result.atom_identifiers == ("atom-cys145-sg",)
    assert result.residue_identifiers == ("residue-cys145",)


def test_resolves_metal_by_element_identity() -> None:
    result = resolve_atom_selection(
        _topology(),
        element_symbol="zn",
        require_unique=True,
    )

    assert result.atom_identifiers == ("atom-zinc",)
    assert result.residue_identifiers == ("residue-zinc",)


def test_unique_atom_resolution_fails_closed_on_ambiguity() -> None:
    with pytest.raises(
        MolecularScientificSelectionError,
        match="ambiguous",
    ):
        resolve_atom_selection(
            _topology(),
            element_symbol="C",
            require_unique=True,
        )


def test_resolves_bond_independent_of_endpoint_order() -> None:
    first = resolve_bond_selection(
        _topology(),
        atom_identifier_a="atom-cys145-ca",
        atom_identifier_b="atom-cys145-sg",
    )
    second = resolve_bond_selection(
        _topology(),
        atom_identifier_a="atom-cys145-sg",
        atom_identifier_b="atom-cys145-ca",
    )

    assert first == second
    assert first.bond_identifiers == ("bond-cys145-ca-sg",)


def test_missing_residue_fails_closed() -> None:
    with pytest.raises(
        MolecularScientificSelectionError,
        match="No topology residue",
    ):
        resolve_residue_selection(
            _topology(),
            residue_name="ASP",
            source_sequence_identifier="999",
            chain_identifier="chain-a",
        )
