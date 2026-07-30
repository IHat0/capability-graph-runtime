"""Universal molecular topology and structure-ingestion regressions."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from cgr.kernel.contracts import CapabilityVersion
from cgr.molecular import (
    MolecularTopologyAtom,
    MolecularTopologyBond,
    MolecularTopologyChain,
    MolecularTopologyIndex,
    MolecularTopologyResidue,
    StructureIngestionError,
    ingest_structure_bytes,
)
from cgr.science import ArtifactPointer, ArtifactReference, CreationProvenance

VERSION = CapabilityVersion(major=1, minor=0, patch=0)
PROVENANCE = CreationProvenance(
    producer="molecular-ingestion-tests",
    producer_version=VERSION,
    execution_identifier="execution-ingestion-tests",
    source="test-suite",
)
FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "molecular-ingestion"
REPOSITORY_ROOT = Path(__file__).parents[1]


def _fixture(name: str) -> bytes:
    return (FIXTURE_ROOT / name).read_bytes()


def _source_reference(
    source_bytes: bytes,
    *,
    artifact_type: str = "molecular_structure",
    byte_size: int | None = None,
    content_sha256: str | None = None,
) -> ArtifactReference:
    return ArtifactReference(
        artifact_identifier="artifact-source-structure",
        schema_version=VERSION,
        artifact_type=artifact_type,
        media_type="chemical/x-structure",
        content_sha256=content_sha256 or hashlib.sha256(source_bytes).hexdigest(),
        byte_size=len(source_bytes) if byte_size is None else byte_size,
        storage_location=None,
        provenance=PROVENANCE,
    )


def _ingest(
    source_bytes: bytes,
    *,
    structure_format: str = "xyz",
    coordinate_unit: str = "angstrom",
    source_reference: ArtifactReference | None = None,
):
    return ingest_structure_bytes(
        source_bytes=source_bytes,
        source_reference=source_reference or _source_reference(source_bytes),
        structure_identifier="structure-ingested",
        system_identifier="system-ingested",
        structure_format=structure_format,
        coordinate_unit=coordinate_unit,
        provenance=PROVENANCE,
    )


def _topology(**updates: Any) -> MolecularTopologyIndex:
    atom_a = MolecularTopologyAtom(
        atom_identifier="atom-a",
        atom_index=0,
        element_symbol="c",
        atom_name="C1",
        chain_identifier="chain-a",
        residue_identifier="residue-a",
        source_serial="1",
    )
    atom_b = MolecularTopologyAtom(
        atom_identifier="atom-b",
        atom_index=1,
        element_symbol="n",
        atom_name="N1",
        chain_identifier="chain-a",
        residue_identifier="residue-a",
        formal_charge=1,
        source_serial="2",
    )
    values: dict[str, Any] = {
        "schema_version": VERSION,
        "structure_identifier": "structure-topology",
        "source_structure": ArtifactPointer(
            artifact_identifier="artifact-source",
            content_sha256="a" * 64,
        ),
        "structure_format": "mol",
        "parser_identifier": "parser-test",
        "parser_version": "1.0.0",
        "atoms": (atom_b, atom_a),
        "bonds": (
            MolecularTopologyBond(
                bond_identifier="bond-a",
                atom_identifier_a="atom-b",
                atom_identifier_b="atom-a",
                bond_order=2.0,
                bond_type="double",
            ),
        ),
        "residues": (
            MolecularTopologyResidue(
                residue_identifier="residue-a",
                residue_name="GEN",
                chain_identifier="chain-a",
                atom_identifiers=("atom-b", "atom-a"),
                source_sequence_identifier="1",
            ),
        ),
        "chains": (
            MolecularTopologyChain(
                chain_identifier="chain-a",
                atom_identifiers=("atom-b", "atom-a"),
                residue_identifiers=("residue-a",),
            ),
        ),
        "model_count": 1,
        "frame_count": 1,
        "coordinate_unit": "angstrom",
    }
    values.update(updates)
    return MolecularTopologyIndex(**values)


def test_importing_package_does_not_import_optional_parsers() -> None:
    environment = dict(os.environ)
    source_path = str(REPOSITORY_ROOT / "src")
    environment["PYTHONPATH"] = os.pathsep.join(
        item
        for item in (source_path, environment.get("PYTHONPATH", ""))
        if item
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import cgr.molecular; "
                "assert 'gemmi' not in sys.modules; "
                "assert 'rdkit' not in sys.modules"
            ),
        ],
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_topology_is_canonical_and_deterministic() -> None:
    topology = _topology()
    reordered = _topology(
        atoms=tuple(reversed(topology.atoms)),
        bonds=tuple(reversed(topology.bonds)),
        residues=tuple(reversed(topology.residues)),
        chains=tuple(reversed(topology.chains)),
    )

    assert topology.atoms[0].atom_index == 0
    assert topology.atoms[0].element_symbol == "C"
    assert topology.bonds[0].atom_identifier_a == "atom-a"
    assert topology.bonds[0].atom_identifier_b == "atom-b"
    assert topology.to_canonical_json() == reordered.to_canonical_json()
    assert topology.fingerprint == reordered.fingerprint


@pytest.mark.parametrize(
    "updates",
    [
        {
            "atoms": (
                MolecularTopologyAtom(
                    atom_identifier="atom-a",
                    atom_index=0,
                    element_symbol="C",
                ),
                MolecularTopologyAtom(
                    atom_identifier="atom-b",
                    atom_index=2,
                    element_symbol="N",
                ),
            ),
            "bonds": (),
            "residues": (),
            "chains": (),
        },
        {
            "bonds": (
                MolecularTopologyBond(
                    bond_identifier="bond-missing",
                    atom_identifier_a="atom-a",
                    atom_identifier_b="atom-missing",
                    bond_type="explicit",
                ),
            ),
        },
        {
            "residues": (
                MolecularTopologyResidue(
                    residue_identifier="residue-a",
                    residue_name="GEN",
                    chain_identifier="chain-a",
                    atom_identifiers=("atom-missing",),
                ),
            ),
        },
        {
            "chains": (
                MolecularTopologyChain(
                    chain_identifier="chain-a",
                    atom_identifiers=("atom-missing",),
                    residue_identifiers=("residue-a",),
                ),
            ),
        },
        {
            "residues": (
                MolecularTopologyResidue(
                    residue_identifier="residue-a",
                    residue_name="GEN",
                    chain_identifier="chain-missing",
                    atom_identifiers=("atom-a", "atom-b"),
                ),
            ),
        },
        {
            "chains": (
                MolecularTopologyChain(
                    chain_identifier="chain-a",
                    atom_identifiers=("atom-a", "atom-b"),
                    residue_identifiers=("residue-missing",),
                ),
            ),
        },
        {
            "atoms": (
                MolecularTopologyAtom(
                    atom_identifier="atom-a",
                    atom_index=0,
                    element_symbol="C",
                    residue_identifier="residue-missing",
                ),
            ),
            "bonds": (),
            "residues": (),
            "chains": (),
        },
    ],
)
def test_topology_rejects_noncontiguous_or_unresolved_references(
    updates: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        _topology(**updates)


def test_topology_rejects_duplicate_identifiers_and_self_bonds() -> None:
    topology = _topology()

    with pytest.raises(ValidationError, match="atom identifiers must be unique"):
        _topology(atoms=(topology.atoms[0], topology.atoms[0]))
    with pytest.raises(ValidationError, match="cannot reference one atom twice"):
        MolecularTopologyBond(
            bond_identifier="bond-self",
            atom_identifier_a="atom-a",
            atom_identifier_b="atom-a",
            bond_type="explicit",
        )


def test_topology_rejects_unsupported_schema_version() -> None:
    with pytest.raises(ValidationError, match="exactly 1.0.0"):
        _topology(
            schema_version=CapabilityVersion(major=1, minor=1, patch=0)
        )


@pytest.mark.parametrize(
    ("source_bytes", "reference", "message"),
    [
        (
            b"",
            _source_reference(b""),
            "cannot be empty",
        ),
        (
            _fixture("minimal.xyz"),
            _source_reference(
                _fixture("minimal.xyz"),
                artifact_type="untrusted_input",
            ),
            "artifact type",
        ),
        (
            _fixture("minimal.xyz"),
            _source_reference(
                _fixture("minimal.xyz"),
                content_sha256="0" * 64,
            ),
            "SHA-256",
        ),
        (
            _fixture("minimal.xyz"),
            _source_reference(
                _fixture("minimal.xyz"),
                byte_size=len(_fixture("minimal.xyz")) + 1,
            ),
            "byte size",
        ),
    ],
)
def test_ingestion_rejects_invalid_source_evidence(
    source_bytes: bytes,
    reference: ArtifactReference,
    message: str,
) -> None:
    with pytest.raises(StructureIngestionError, match=message):
        _ingest(source_bytes, source_reference=reference)


@pytest.mark.parametrize("structure_format", ["pdb", "mmcif", "mol", "sdf"])
def test_ingestion_rejects_non_angstrom_units_before_parsing(
    structure_format: str,
) -> None:
    source_bytes = _fixture("minimal.xyz")

    with pytest.raises(StructureIngestionError, match="angstrom"):
        _ingest(
            source_bytes,
            structure_format=structure_format,
            coordinate_unit="bohr",
        )


def test_ingestion_rejects_unsupported_coordinate_unit() -> None:
    with pytest.raises(StructureIngestionError, match="coordinate unit"):
        _ingest(_fixture("minimal.xyz"), coordinate_unit="picometer")


def test_xyz_ingestion_preserves_declared_unit_and_never_guesses_bonds() -> None:
    source_bytes = _fixture("minimal.xyz")
    result = _ingest(source_bytes, coordinate_unit="nanometer")

    assert result.topology.structure_format == "xyz"
    assert result.topology.coordinate_unit == "nanometer"
    assert result.topology.parser_identifier == "pulsate.xyz"
    assert [atom.element_symbol for atom in result.topology.atoms] == ["N", "C"]
    assert result.topology.bonds == ()
    assert result.structure.bond_count == 0
    assert result.structure.atom_count == len(result.topology.atoms) == 2
    assert result.structure.residue_count == len(result.topology.residues) == 0
    assert result.structure.chain_count == len(result.topology.chains) == 0
    assert result.structure.model_count == result.topology.model_count == 1
    assert result.structure.frame_count == result.topology.frame_count == 1


@pytest.mark.parametrize(
    "source_bytes",
    [
        b"",
        b"not-an-integer\ncomment\nC 0 0 0\n",
        b"0\ncomment\n",
        b"2\ncomment\nC 0 0 0\n",
        b"1\ncomment\nC 0 0\n",
        b"1\ncomment\nC nan 0 0\n",
        b"1\ncomment\nC 0 0 0\ntrailing\n",
        b"\xff\xfe",
    ],
)
def test_xyz_parser_fails_closed_for_malformed_input(source_bytes: bytes) -> None:
    with pytest.raises(StructureIngestionError):
        _ingest(source_bytes)


def test_derived_artifact_payload_lineage_and_counts_are_exact() -> None:
    source_bytes = _fixture("minimal.xyz")
    source_reference = _source_reference(source_bytes)
    result = _ingest(source_bytes, source_reference=source_reference)

    assert result.topology_payload == result.topology.to_canonical_json().encode(
        "utf-8"
    )
    assert result.topology_artifact.content_sha256 == hashlib.sha256(
        result.topology_payload
    ).hexdigest()
    assert result.topology_artifact.byte_size == len(result.topology_payload)
    assert result.topology_artifact.artifact_type == "molecular_topology"
    assert (
        result.topology_artifact.media_type
        == "application/vnd.pulsate.molecular-topology+json"
    )
    assert result.topology_artifact.storage_location is None
    assert result.topology_artifact.provenance == PROVENANCE
    assert result.topology_artifact.parents == (source_reference.pointer,)
    assert result.lineage_edge.source == source_reference.pointer
    assert result.lineage_edge.destination == result.topology_artifact.pointer
    assert result.lineage_edge.relationship_type == "parsed_into"
    assert (
        result.lineage_edge.producing_capability
        == "molecular.structure_ingestion"
    )
    assert result.lineage_edge.producing_capability_version == VERSION
    assert result.structure.structure_artifact == source_reference
    assert result.structure.topology_artifact == result.topology_artifact
    assert result.structure.atom_count == len(result.topology.atoms)
    assert result.structure.bond_count == len(result.topology.bonds)


def test_repeated_ingestion_is_byte_for_byte_deterministic() -> None:
    source_bytes = _fixture("minimal.xyz")
    first = _ingest(source_bytes)
    second = _ingest(source_bytes)

    assert first == second
    assert first.topology_payload == second.topology_payload
    assert first.topology.fingerprint == second.topology.fingerprint
    assert first.topology_artifact == second.topology_artifact
    assert first.lineage_edge == second.lineage_edge


def test_canonical_ingestion_output_contains_no_absolute_local_path() -> None:
    result = _ingest(_fixture("minimal.xyz"))
    canonical_output = "\n".join(
        (
            result.topology.to_canonical_json(),
            result.topology_artifact.to_canonical_json(),
            result.structure.to_canonical_json(),
            result.lineage_edge.to_canonical_json(),
        )
    )

    assert str(REPOSITORY_ROOT) not in canonical_output
    assert "C:\\" not in canonical_output


@pytest.mark.parametrize(
    ("module_name", "structure_format", "fixture_name"),
    [
        ("gemmi", "pdb", "minimal.pdb"),
        ("rdkit", "mol", "minimal.mol"),
    ],
)
def test_missing_optional_dependency_is_controlled(
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
    structure_format: str,
    fixture_name: str,
) -> None:
    monkeypatch.setitem(sys.modules, module_name, None)
    source_bytes = _fixture(fixture_name)

    with pytest.raises(
        StructureIngestionError,
        match=f"Missing optional dependency '{module_name}'",
    ):
        _ingest(source_bytes, structure_format=structure_format)


@pytest.mark.parametrize(
    ("fixture_name", "structure_format"),
    [
        ("minimal.pdb", "pdb"),
        ("minimal.cif", "mmcif"),
    ],
)
def test_pdb_and_mmcif_ingestion_through_gemmi(
    fixture_name: str,
    structure_format: str,
) -> None:
    pytest.importorskip("gemmi")
    source_bytes = _fixture(fixture_name)
    result = _ingest(source_bytes, structure_format=structure_format)

    assert result.topology.parser_identifier == "gemmi"
    assert result.topology.structure_format == structure_format
    assert len(result.topology.atoms) == 2
    assert len(result.topology.chains) == 1
    assert len(result.topology.residues) == 1
    assert result.topology.bonds == ()
    assert result.structure.atom_count == 2
    assert result.structure.bond_count == 0
    assert all(atom.atom_name for atom in result.topology.atoms)
    assert all(atom.chain_identifier for atom in result.topology.atoms)
    assert all(atom.residue_identifier for atom in result.topology.atoms)


def test_malformed_gemmi_input_uses_controlled_error() -> None:
    pytest.importorskip("gemmi")
    source_bytes = b"this is not valid PDB structure data\n"

    with pytest.raises(StructureIngestionError, match="pdb"):
        _ingest(source_bytes, structure_format="pdb")


def test_mol_ingestion_preserves_rdkit_bond_order_and_formal_charge() -> None:
    pytest.importorskip("rdkit")
    source_bytes = _fixture("minimal.mol")
    result = _ingest(source_bytes, structure_format="mol")

    assert result.topology.parser_identifier == "rdkit"
    assert len(result.topology.atoms) == 2
    assert len(result.topology.bonds) == 1
    assert result.topology.bonds[0].bond_order == 2.0
    assert [atom.formal_charge for atom in result.topology.atoms] == [0, 1]
    assert result.structure.bond_count == 1


def test_single_record_sdf_ingestion_and_multiple_record_rejection() -> None:
    pytest.importorskip("rdkit")
    mol_bytes = _fixture("minimal.mol")
    single_record = mol_bytes + b"\n$$$$\n"
    single_result = _ingest(single_record, structure_format="sdf")

    assert single_result.topology.structure_format == "sdf"
    assert len(single_result.topology.bonds) == 1
    assert single_result.topology.bonds[0].bond_order == 2.0

    multiple_records = single_record + single_record
    with pytest.raises(StructureIngestionError, match="exactly one"):
        _ingest(multiple_records, structure_format="sdf")


def test_malformed_rdkit_input_uses_controlled_error() -> None:
    pytest.importorskip("rdkit")
    source_bytes = b"not a valid mol block\n"

    with pytest.raises(StructureIngestionError, match="MOL"):
        _ingest(source_bytes, structure_format="mol")
