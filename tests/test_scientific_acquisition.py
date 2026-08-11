"""Grounded input acquisition and reaction-path regression coverage."""

from __future__ import annotations

import json

import pytest

from cgr.pulsate_api import scientific_cli
from cgr.pulsate_api.phase8_scientific_handlers import (
    _reaction_path_endpoint_evidence,
)
from cgr.pulsate_api.scientific_acquisition import (
    AcquiredScientificInput,
    acquire_protein_pdb,
    generate_ligand_geometry,
    grounded_identifiers,
)
from cgr.science import ArtifactReference


def test_question_extraction_accepts_only_explicit_grounded_identifiers() -> None:
    identifiers = grounded_identifiers(
        "Use PDB ID: 1abc with PubChem CID: 1234 for this transition state."
    )

    assert identifiers.protein_pdb_id == "1ABC"
    assert identifiers.ligand_pubchem_cid == 1234
    assert grounded_identifiers("Use the familiar protease and inhibitor.").protein_pdb_id is None
    with pytest.raises(ValueError, match="multiple explicit PDB"):
        grounded_identifiers("Compare PDB ID: 1abc and PDB ID: 2xyz.")


def test_exact_pdb_acquisition_is_bounded_and_provenanced() -> None:
    calls: list[tuple[str, str, int]] = []

    def fetch(url: str, accept: str, maximum_bytes: int) -> bytes:
        calls.append((url, accept, maximum_bytes))
        return (
            b"ATOM      1  CA  GLY A   1       0.000   0.000   0.000  "
            b"1.00  0.00           C  \nEND\n"
        )

    acquired = acquire_protein_pdb("1abc", fetch=fetch)

    assert calls[0][0] == "https://files.rcsb.org/download/1ABC.pdb"
    assert acquired.source_identifier == "1ABC"
    assert acquired.metadata["acquisition_content_sha256"] == acquired.content_sha256
    assert "question" not in acquired.metadata


def test_local_smiles_geometry_has_one_deterministic_3d_conformer() -> None:
    pytest.importorskip("rdkit")

    first = generate_ligand_geometry(smiles="CCSC")
    second = generate_ligand_geometry(smiles="CCSC")

    assert first.payload == second.payload
    assert first.source_identifier.startswith("sha256:")
    assert first.generation_method == "rdkit_etkdgv3_mmff94_seed_17"


def test_reaction_path_accepts_opposed_mode_endpoints_below_old_cutoff() -> None:
    numpy = pytest.importorskip("numpy")
    transition = numpy.zeros((2, 3))
    mode = numpy.zeros((2, 3))
    mode[0, 0] = 1.0
    forward = transition.copy()
    reverse = transition.copy()
    forward[0, 0] = 0.033
    reverse[0, 0] = -0.033

    evidence = _reaction_path_endpoint_evidence(
        transition_coordinates=transition,
        forward_endpoint=forward,
        reverse_endpoint=reverse,
        normalized_mode=mode,
        active_indices=(0, 1),
        step_size_bohr=0.08,
    )

    assert evidence["endpoint_separation_bohr"] == pytest.approx(0.066)
    assert evidence["endpoint_separation_bohr"] < 0.08
    assert evidence["opposed_mode_projections"]
    assert evidence["distinct_endpoints"]


def test_cli_acquires_question_identifiers_but_requires_execution_review(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    protein = AcquiredScientificInput(
        artifact_type="protein_structure",
        media_type="chemical/x-pdb",
        payload=b"ATOM protein\n",
        source_kind="rcsb_pdb_entry",
        source_identifier="1ABC",
        source_url="https://files.rcsb.org/download/1ABC.pdb",
    )
    ligand = AcquiredScientificInput(
        artifact_type="ligand_structure",
        media_type="chemical/x-mdl-sdfile",
        payload=b"ligand SDF",
        source_kind="pubchem_compound_3d",
        source_identifier="1234",
        source_url=(
            "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/"
            "1234/SDF?record_type=3d"
        ),
    )
    monkeypatch.setattr(scientific_cli, "acquire_protein_pdb", lambda _value: protein)
    monkeypatch.setattr(scientific_cli, "acquire_ligand_pubchem", lambda _value: ligand)
    monkeypatch.setenv("PULSATE_ACCESS_TOKEN", "bounded-test-token")
    routes: list[str] = []

    def post(
        _base_url: str,
        route: str,
        _token: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        routes.append(route)
        if route == "/api/v1/scientific/artifacts":
            return ArtifactReference.model_validate(payload["reference"]).model_dump(
                mode="json"
            )
        if route == "/api/v1/scientific/objectives/compile":
            assert payload["covalent_reaction_target"] is not None
            return {
                "status": "planned",
                "execution_identifier": "scientific-execution-" + ("b" * 32),
            }
        raise AssertionError("Unreviewed acquired inputs must not execute.")

    monkeypatch.setattr(scientific_cli, "_post", post)
    result = scientific_cli.scientist_main(
        [
            "Find the transition state using PDB ID: 1abc and PubChem CID: 1234.",
            "--protein-chain", "A",
            "--protein-residue-sequence", "145",
            "--protein-residue-name", "CYS",
            "--protein-atom-name", "SG",
            "--nucleophile-formal-charge", "-1",
            "--ligand-reaction-smarts", "[C:1]-[S:2]",
            "--total-qm-charge", "-1",
            "--spin", "0",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert result == 0
    assert output["acquired_input_execution_approval_required"] is True
    assert len(output["acquired_input_provenance"]) == 2
    assert routes == [
        "/api/v1/scientific/artifacts",
        "/api/v1/scientific/artifacts",
        "/api/v1/scientific/objectives/compile",
    ]
