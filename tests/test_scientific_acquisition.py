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
    resolve_ligand_pubchem_name_candidates,
    resolve_protein_uniprot_name_candidates,
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


def test_named_ligand_requires_one_unique_public_identifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from cgr.pulsate_api import scientific_acquisition

    calls: list[str] = []

    def fetch(url: str, _accept: str, _maximum_bytes: int) -> bytes:
        calls.append(url)
        if "/cids/JSON" in url:
            return b'{"IdentifierList":{"CID":[4321]}}'
        return b"exact SDF"

    monkeypatch.setattr(
        scientific_acquisition,
        "_validate_ligand_geometry",
        lambda _payload: None,
    )
    acquired = scientific_acquisition.acquire_ligand_pubchem_name(
        "Example Compound",
        fetch=fetch,
    )
    assert acquired.source_identifier == "4321"
    assert "/name/Example%20Compound/cids/JSON" in calls[0]
    assert "/cid/4321/SDF" in calls[1]

    def ambiguous(url: str, _accept: str, _maximum_bytes: int) -> bytes:
        del url
        return b'{"IdentifierList":{"CID":[1,2]}}'

    with pytest.raises(ValueError, match="exactly one"):
        scientific_acquisition.acquire_ligand_pubchem_name(
            "Ambiguous Example",
            fetch=ambiguous,
        )


def test_trusted_ligand_candidates_preserve_ambiguity_and_confidence() -> None:
    def fetch(_url: str, _accept: str, _maximum_bytes: int) -> bytes:
        return b'{"IdentifierList":{"CID":[9,3,9]}}'

    candidates = resolve_ligand_pubchem_name_candidates(
        "Example Compound",
        fetch=fetch,
    )

    assert [item.source_identifier for item in candidates] == ["3", "9"]
    assert all(item.source_kind == "pubchem" for item in candidates)
    assert all(item.confidence == "ambiguous" for item in candidates)


def test_trusted_protein_candidates_require_one_exact_structure_choice() -> None:
    document = {
        "results": [
            {
                "primaryAccession": "P11111",
                "uniProtkbId": "FIRST_HUMAN",
                "uniProtKBCrossReferences": [
                    {"database": "PDB", "id": "1ABC"},
                    {"database": "PDB", "id": "2DEF"},
                ],
            },
            {
                "primaryAccession": "P22222",
                "uniProtkbId": "SECOND_HUMAN",
                "uniProtKBCrossReferences": [
                    {"database": "PDB", "id": "3GHI"},
                ],
            },
        ]
    }

    def fetch(url: str, accept: str, maximum_bytes: int) -> bytes:
        assert "rest.uniprot.org/uniprotkb/search" in url
        assert accept == "application/json"
        assert maximum_bytes == 256 * 1024
        return json.dumps(document).encode("utf-8")

    candidates = resolve_protein_uniprot_name_candidates(
        "Example Protein",
        fetch=fetch,
    )

    assert {
        (item.source_identifier, item.structure_identifier)
        for item in candidates
    } == {("P11111", "1ABC"), ("P11111", "2DEF"), ("P22222", "3GHI")}
    assert all(item.confidence == "ambiguous" for item in candidates)


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
        if route == "/api/v1/research/sessions":
            assert payload["covalent_reaction_target"] is not None
            assert payload["accept_acquired_input_evidence"] is False
            return {
                "status": "planned",
                "session_identifier": "research-session-" + ("b" * 32),
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
        "/api/v1/research/sessions",
    ]


def test_cli_uses_a_resolved_automatic_target_without_placeholder_options(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from cgr.pulsate_api.scientific_objectives import CovalentReactionTarget
    from cgr.pulsate_api.scientific_resolver import (
        AutomaticCovalentTargetResolution,
    )

    source = AcquiredScientificInput(
        artifact_type="protein_structure",
        media_type="chemical/x-pdb",
        payload=b"ATOM source\n",
        source_kind="rcsb_pdb_entry",
        source_identifier="1ABC",
    )
    polymer = AcquiredScientificInput(
        artifact_type="protein_structure",
        media_type="chemical/x-pdb",
        payload=b"ATOM polymer\n",
        source_kind="grounded_polymer_only_pdb",
        source_identifier="1ABC",
    )
    ligand = AcquiredScientificInput(
        artifact_type="ligand_structure",
        media_type="chemical/x-mdl-sdfile",
        payload=b"resolved ligand",
        source_kind="rcsb_pdb_chemical_component_ideal",
        source_identifier="LIG",
    )
    target = CovalentReactionTarget(
        protein_chain_label="A",
        protein_residue_sequence="145",
        protein_residue_name="CYS",
        protein_atom_name="SG",
        protein_nucleophile_formal_charge=-1,
        ligand_reaction_smarts="[C:1]-[S:2]",
        selected_total_qm_charge=-1,
        selected_spin=1,
    )
    monkeypatch.setattr(scientific_cli, "acquire_protein_pdb", lambda _value: source)
    monkeypatch.setattr(
        scientific_cli,
        "resolve_automatic_covalent_target",
        lambda _value: AutomaticCovalentTargetResolution(
            status="resolved",
            target=target,
            protein_input=polymer,
            ligand_input=ligand,
            state=None,
            confidence="high",
            reason=None,
        ),
    )
    monkeypatch.setenv("PULSATE_ACCESS_TOKEN", "bounded-test-token")
    requests: list[tuple[str, dict[str, object]]] = []

    def post(
        _base_url: str,
        route: str,
        _token: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        requests.append((route, payload))
        if route == "/api/v1/scientific/artifacts":
            return ArtifactReference.model_validate(payload["reference"]).model_dump(
                mode="json"
            )
        return {
            "status": "planned",
            "session_identifier": "research-session-" + ("c" * 32),
        }

    monkeypatch.setattr(scientific_cli, "_post", post)
    status = scientific_cli.scientist_main(
        [
            "Find the covalent reaction transition state using PDB ID: 1abc.",
            "--plan-only",
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert status == 0
    assert output["automatic_evidence_resolution"]["status"] == "resolved"
    assert [route for route, _payload in requests] == [
        "/api/v1/scientific/artifacts",
        "/api/v1/scientific/artifacts",
        "/api/v1/research/sessions",
    ]
    created = requests[-1][1]
    assert len(created["input_references"]) == 2
    assert created["covalent_reaction_target"] == target.model_dump(mode="json")


def test_acquire_pdb_chemical_component_uses_exact_ccd_identity() -> None:
    from cgr.pulsate_api.scientific_acquisition import (
        acquire_pdb_chemical_component,
        generate_ligand_geometry,
    )

    generated = generate_ligand_geometry(
        smiles="CCO",
        random_seed=23,
    )
    fixture_payload = generated.payload + b"$$$$\n"

    requests: list[tuple[str, str, int]] = []

    def fetch(
        url: str,
        accept: str,
        maximum_bytes: int,
    ) -> bytes:
        requests.append((url, accept, maximum_bytes))
        return fixture_payload

    acquired = acquire_pdb_chemical_component(
        "abc",
        fetch=fetch,
    )

    assert acquired.artifact_type == "ligand_structure"
    assert acquired.media_type == "chemical/x-mdl-sdfile"
    assert acquired.source_kind == "rcsb_pdb_chemical_component_ideal"
    assert acquired.source_identifier == "ABC"
    assert acquired.payload == fixture_payload

    assert len(requests) == 1
    assert requests[0][0] == (
        "https://files.rcsb.org/ligands/download/ABC_ideal.sdf"
    )
    assert requests[0][1] == "chemical/x-mdl-sdfile"

    try:
        acquire_pdb_chemical_component(
            "../ABC",
            fetch=fetch,
        )
    except ValueError:
        pass
    else:
        raise AssertionError(
            "Invalid CCD identifiers must fail before acquisition."
        )

    assert len(requests) == 1


def test_pdb_chemical_component_supports_extended_five_character_ids() -> None:
    from cgr.pulsate_api.scientific_acquisition import (
        acquire_pdb_chemical_component,
        generate_ligand_geometry,
    )

    generated = generate_ligand_geometry(
        smiles="CCO",
        random_seed=29,
    )
    fixture_payload = generated.payload + b"$$$$\n"

    requests: list[str] = []

    def fetch(
        url: str,
        accept: str,
        maximum_bytes: int,
    ) -> bytes:
        del accept, maximum_bytes
        requests.append(url)
        return fixture_payload

    acquired = acquire_pdb_chemical_component(
        "A1B2C",
        fetch=fetch,
    )

    assert acquired.source_identifier == "A1B2C"
    assert requests == [
        "https://files.rcsb.org/ligands/download/A1B2C_ideal.sdf"
    ]

    try:
        acquire_pdb_chemical_component(
            "ABCD",
            fetch=fetch,
        )
    except ValueError:
        pass
    else:
        raise AssertionError(
            "Four-character CCD identifiers must not be accepted."
        )

    assert len(requests) == 1
