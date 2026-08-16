"""Deterministic grounding tests for acquired scientific structures."""

from __future__ import annotations

from cgr.pulsate_api.scientific_acquisition import AcquiredScientificInput
from cgr.pulsate_api.scientific_resolution import ground_acquired_protein_pdb


def _atom(
    record: str,
    serial: int,
    atom_name: str,
    residue_name: str,
    chain: str,
    sequence: int,
    x: float,
    y: float,
    z: float,
    element: str,
) -> str:
    return (
        f"{record:<6}{serial:>5} "
        f"{atom_name:^4} "
        f"{residue_name:>3} "
        f"{chain:1}"
        f"{sequence:>4}    "
        f"{x:>8.3f}"
        f"{y:>8.3f}"
        f"{z:>8.3f}"
        f"{1.00:>6.2f}"
        f"{20.00:>6.2f}"
        f"          "
        f"{element:>2}\n"
    )


def test_grounded_pdb_inventory_separates_source_components() -> None:
    payload = (
        _atom("ATOM", 1, "CA", "CYS", "A", 145, 0.0, 0.0, 0.0, "C")
        + _atom("ATOM", 2, "SG", "CYS", "A", 145, 1.8, 0.0, 0.0, "S")
        + _atom("HETATM", 3, "C1", "LIG", "B", 501, 3.0, 0.0, 0.0, "C")
        + _atom("HETATM", 4, "O1", "LIG", "B", 501, 4.2, 0.0, 0.0, "O")
        + _atom("HETATM", 5, "O", "HOH", "W", 1, 8.0, 0.0, 0.0, "O")
        + _atom("HETATM", 6, "ZN", "ZN", "Z", 1, 10.0, 0.0, 0.0, "Zn")
        + "END\n"
    ).encode("utf-8")

    acquired = AcquiredScientificInput(
        artifact_type="protein_structure",
        media_type="chemical/x-pdb",
        payload=payload,
        source_kind="neutral_test_fixture",
        source_identifier="NEUTRAL-PDB-001",
        source_url="https://example.invalid/neutral-pdb-001",
    )

    grounded = ground_acquired_protein_pdb(acquired)

    assert grounded.source_identifier == "NEUTRAL-PDB-001"
    assert grounded.source_content_sha256 == acquired.content_sha256
    assert grounded.topology.parser_identifier == "gemmi"

    assert [
        (
            item.residue_name,
            item.chain_label,
            item.residue_sequence,
            item.atom_names,
        )
        for item in grounded.protein_residues
    ] == [
        ("CYS", "A", "145", ("CA", "SG")),
    ]

    assert [
        (item.residue_name, item.chain_label, item.residue_sequence)
        for item in grounded.secondary_components
    ] == [
        ("LIG", "B", "501"),
    ]

    assert [item.residue_name for item in grounded.solvent_components] == ["HOH"]
    assert [item.residue_name for item in grounded.ion_components] == ["ZN"]

def _link(
    atom_a: str,
    residue_a: str,
    chain_a: str,
    sequence_a: int,
    atom_b: str,
    residue_b: str,
    chain_b: str,
    sequence_b: int,
) -> str:
    columns = [" "] * 80

    def put(start: int, value: str) -> None:
        columns[start : start + len(value)] = value

    put(0, "LINK  ")
    put(12, f"{atom_a:>4}")
    put(17, f"{residue_a:>3}")
    put(21, chain_a)
    put(22, f"{sequence_a:>4}")
    put(42, f"{atom_b:>4}")
    put(47, f"{residue_b:>3}")
    put(51, chain_b)
    put(52, f"{sequence_b:>4}")

    return "".join(columns).rstrip() + "\n"


def test_declared_relationship_precedes_geometric_inference() -> None:
    from cgr.pulsate_api.scientific_relationships import (
        declared_pdb_relationships,
    )

    payload = (
        _link("SG", "CYS", "A", 145, "C1", "LIG", "B", 501)
        + _atom("ATOM", 1, "CA", "CYS", "A", 145, 0.0, 0.0, 0.0, "C")
        + _atom("ATOM", 2, "SG", "CYS", "A", 145, 1.8, 0.0, 0.0, "S")
        + _atom("HETATM", 3, "C1", "LIG", "B", 501, 3.0, 0.0, 0.0, "C")
        + _atom("HETATM", 4, "O1", "LIG", "B", 501, 4.2, 0.0, 0.0, "O")
        + _atom("HETATM", 5, "O", "HOH", "W", 1, 8.0, 0.0, 0.0, "O")
        + "END\n"
    ).encode("utf-8")

    acquired = AcquiredScientificInput(
        artifact_type="protein_structure",
        media_type="chemical/x-pdb",
        payload=payload,
        source_kind="neutral_test_fixture",
        source_identifier="NEUTRAL-PDB-LINK-001",
        source_url="https://example.invalid/neutral-pdb-link-001",
    )

    evidence = declared_pdb_relationships(acquired)

    assert len(evidence.protein_secondary_connections) == 1

    connection = evidence.protein_secondary_connections[0]
    endpoints = {
        (
            connection.endpoint_a.entity_type,
            connection.endpoint_a.residue_name,
            connection.endpoint_a.chain_label,
            connection.endpoint_a.residue_sequence,
            connection.endpoint_a.atom_name,
        ),
        (
            connection.endpoint_b.entity_type,
            connection.endpoint_b.residue_name,
            connection.endpoint_b.chain_label,
            connection.endpoint_b.residue_sequence,
            connection.endpoint_b.atom_name,
        ),
    }

    assert endpoints == {
        ("polymer", "CYS", "A", "145", "SG"),
        ("nonpolymer", "LIG", "B", "501", "C1"),
    }

def test_unique_declared_connection_resolves_covalent_anchor() -> None:
    from cgr.pulsate_api.scientific_resolver import (
        resolve_declared_covalent_anchor,
    )

    payload = (
        _link("SG", "CYS", "A", 145, "C1", "LIG", "B", 501)
        + _atom("ATOM", 1, "CA", "CYS", "A", 145, 0.0, 0.0, 0.0, "C")
        + _atom("ATOM", 2, "SG", "CYS", "A", 145, 1.8, 0.0, 0.0, "S")
        + _atom("HETATM", 3, "C1", "LIG", "B", 501, 3.0, 0.0, 0.0, "C")
        + _atom("HETATM", 4, "O1", "LIG", "B", 501, 4.2, 0.0, 0.0, "O")
        + "END\n"
    ).encode("utf-8")

    acquired = AcquiredScientificInput(
        artifact_type="protein_structure",
        media_type="chemical/x-pdb",
        payload=payload,
        source_kind="neutral_test_fixture",
        source_identifier="NEUTRAL-PDB-ANCHOR-001",
        source_url="https://example.invalid/neutral-pdb-anchor-001",
    )

    resolution = resolve_declared_covalent_anchor(acquired)

    assert resolution.status == "resolved"
    assert resolution.confidence == "high"
    assert resolution.reason is None
    assert resolution.selected is not None

    assert (
        resolution.selected.protein_endpoint.residue_name,
        resolution.selected.protein_endpoint.chain_label,
        resolution.selected.protein_endpoint.residue_sequence,
        resolution.selected.protein_endpoint.atom_name,
    ) == ("CYS", "A", "145", "SG")

    assert (
        resolution.selected.secondary_endpoint.residue_name,
        resolution.selected.secondary_endpoint.chain_label,
        resolution.selected.secondary_endpoint.residue_sequence,
        resolution.selected.secondary_endpoint.atom_name,
    ) == ("LIG", "B", "501", "C1")

    assert resolution.selected.evidence_kind == "pdb_declared_connection"


def test_multiple_declared_connections_are_ambiguous_not_guessed() -> None:
    from cgr.pulsate_api.scientific_resolver import (
        resolve_declared_covalent_anchor,
    )

    payload = (
        _link("SG", "CYS", "A", 145, "C1", "LIG", "B", 501)
        + _link("SG", "CYS", "A", 145, "C2", "LIG", "B", 501)
        + _atom("ATOM", 1, "CA", "CYS", "A", 145, 0.0, 0.0, 0.0, "C")
        + _atom("ATOM", 2, "SG", "CYS", "A", 145, 1.8, 0.0, 0.0, "S")
        + _atom("HETATM", 3, "C1", "LIG", "B", 501, 3.0, 0.0, 0.0, "C")
        + _atom("HETATM", 4, "C2", "LIG", "B", 501, 3.5, 0.0, 0.0, "C")
        + "END\n"
    ).encode("utf-8")

    acquired = AcquiredScientificInput(
        artifact_type="protein_structure",
        media_type="chemical/x-pdb",
        payload=payload,
        source_kind="neutral_test_fixture",
        source_identifier="NEUTRAL-PDB-ANCHOR-AMBIGUOUS",
        source_url="https://example.invalid/neutral-pdb-anchor-ambiguous",
    )

    resolution = resolve_declared_covalent_anchor(acquired)

    assert resolution.status == "ambiguous"
    assert resolution.selected is None
    assert resolution.confidence is None
    assert (
        resolution.reason
        == "multiple_explicit_protein_secondary_connections"
    )
    assert len(resolution.candidates) == 2


def _ligand_acquisition(smiles: str, identifier: str) -> AcquiredScientificInput:
    from rdkit import Chem

    molecule = Chem.MolFromSmiles(smiles)
    assert molecule is not None

    payload = Chem.MolToMolBlock(molecule).encode("utf-8")

    return AcquiredScientificInput(
        artifact_type="ligand_structure",
        media_type="chemical/x-mdl-molfile",
        payload=payload,
        source_kind="neutral_test_fixture",
        source_identifier=identifier,
        source_url=f"https://example.invalid/{identifier.lower()}",
    )


def test_supported_ligand_substitution_resolves_uniquely() -> None:
    from cgr.pulsate_api.scientific_resolver import (
        resolve_supported_ligand_reaction,
    )

    acquired = _ligand_acquisition(
        "CCSC",
        "NEUTRAL-LIGAND-001",
    )

    resolution = resolve_supported_ligand_reaction(acquired)

    assert resolution.status == "resolved"
    assert resolution.confidence == "high"
    assert resolution.reason is None
    assert resolution.selected is not None

    assert (
        resolution.selected.pattern_identifier
        == "methyl-thioether-substitution-v1"
    )
    assert (
        resolution.selected.reaction_smarts
        == "[C;H3:1]-[S:2]-[C;H2]"
    )
    assert resolution.selected.electrophile_element == "C"
    assert resolution.selected.leaving_group_element == "S"


def test_multiple_supported_ligand_pairs_are_ambiguous() -> None:
    from cgr.pulsate_api.scientific_resolver import (
        resolve_supported_ligand_reaction,
    )

    acquired = _ligand_acquisition(
        "CSCSC",
        "NEUTRAL-LIGAND-AMBIGUOUS",
    )

    resolution = resolve_supported_ligand_reaction(acquired)

    assert resolution.status == "ambiguous"
    assert resolution.selected is None
    assert resolution.confidence is None
    assert (
        resolution.reason
        == "multiple_supported_covalent_substitution_patterns"
    )
    assert len(resolution.candidates) == 2


def test_unsupported_ligand_reaction_is_not_guessed() -> None:
    from cgr.pulsate_api.scientific_resolver import (
        resolve_supported_ligand_reaction,
    )

    acquired = _ligand_acquisition(
        "CCO",
        "NEUTRAL-LIGAND-UNSUPPORTED",
    )

    resolution = resolve_supported_ligand_reaction(acquired)

    assert resolution.status == "unresolved"
    assert resolution.selected is None
    assert resolution.confidence is None
    assert (
        resolution.reason
        == "no_supported_covalent_substitution_pattern"
    )
    assert resolution.candidates == ()


def _atom_with_charge(
    record: str,
    serial: int,
    atom_name: str,
    residue_name: str,
    chain: str,
    sequence: int,
    x: float,
    y: float,
    z: float,
    element: str,
    charge: str,
) -> str:
    line = _atom(
        record,
        serial,
        atom_name,
        residue_name,
        chain,
        sequence,
        x,
        y,
        z,
        element,
    ).rstrip("\n").ljust(80)

    return line[:78] + f"{charge:>2}" + "\n"


def test_explicit_nucleophile_charge_is_resolved_from_pdb_evidence() -> None:
    from cgr.pulsate_api.scientific_resolver import (
        resolve_declared_covalent_anchor,
        resolve_explicit_protein_nucleophile_charge,
    )

    payload = (
        _link("SG", "CYS", "A", 145, "C1", "LIG", "B", 501)
        + _atom("ATOM", 1, "CA", "CYS", "A", 145, 0.0, 0.0, 0.0, "C")
        + _atom_with_charge(
            "ATOM", 2, "SG", "CYS", "A", 145,
            1.8, 0.0, 0.0, "S", "1-"
        )
        + _atom("HETATM", 3, "C1", "LIG", "B", 501, 3.0, 0.0, 0.0, "C")
        + _atom("HETATM", 4, "O1", "LIG", "B", 501, 4.2, 0.0, 0.0, "O")
        + "END\n"
    ).encode("utf-8")

    acquired = AcquiredScientificInput(
        artifact_type="protein_structure",
        media_type="chemical/x-pdb",
        payload=payload,
        source_kind="neutral_test_fixture",
        source_identifier="NEUTRAL-PDB-CHARGE-001",
        source_url="https://example.invalid/neutral-pdb-charge-001",
    )

    anchor = resolve_declared_covalent_anchor(acquired)

    assert anchor.status == "resolved"
    assert anchor.selected is not None

    charge = resolve_explicit_protein_nucleophile_charge(
        acquired,
        anchor.selected,
    )

    assert charge.status == "resolved"
    assert charge.formal_charge == -1
    assert charge.confidence == "high"
    assert charge.evidence_kind == "pdb_atom_formal_charge"
    assert charge.reason is None


def test_missing_pdb_charge_remains_unresolved_not_zero() -> None:
    from cgr.pulsate_api.scientific_resolver import (
        resolve_declared_covalent_anchor,
        resolve_explicit_protein_nucleophile_charge,
    )

    payload = (
        _link("SG", "CYS", "A", 145, "C1", "LIG", "B", 501)
        + _atom("ATOM", 1, "CA", "CYS", "A", 145, 0.0, 0.0, 0.0, "C")
        + _atom("ATOM", 2, "SG", "CYS", "A", 145, 1.8, 0.0, 0.0, "S")
        + _atom("HETATM", 3, "C1", "LIG", "B", 501, 3.0, 0.0, 0.0, "C")
        + _atom("HETATM", 4, "O1", "LIG", "B", 501, 4.2, 0.0, 0.0, "O")
        + "END\n"
    ).encode("utf-8")

    acquired = AcquiredScientificInput(
        artifact_type="protein_structure",
        media_type="chemical/x-pdb",
        payload=payload,
        source_kind="neutral_test_fixture",
        source_identifier="NEUTRAL-PDB-CHARGE-MISSING",
        source_url="https://example.invalid/neutral-pdb-charge-missing",
    )

    anchor = resolve_declared_covalent_anchor(acquired)

    assert anchor.status == "resolved"
    assert anchor.selected is not None

    charge = resolve_explicit_protein_nucleophile_charge(
        acquired,
        anchor.selected,
    )

    assert charge.status == "unresolved"
    assert charge.formal_charge is None
    assert charge.confidence is None
    assert charge.evidence_kind is None
    assert (
        charge.reason
        == "protein_nucleophile_formal_charge_not_explicit"
    )


def test_covalent_qm_state_uses_runtime_charge_spin_logic() -> None:
    from cgr.pulsate_api.scientific_acquisition import (
        generate_ligand_geometry,
    )
    from cgr.pulsate_api.scientific_qmmm_state import (
        CovalentQMMMSelection,
        assess_covalent_qm_state,
        build_covalent_qmmm_environment,
    )
    from cgr.pulsate_api.scientific_resolver import (
        resolve_supported_ligand_reaction,
    )

    protein_payload = (
        _atom(
            "ATOM", 1, "CA", "CYS", "A", 145,
            -3.2, 0.0, 0.0, "C"
        )
        + _atom(
            "ATOM", 2, "CB", "CYS", "A", 145,
            -1.7, 0.0, 0.0, "C"
        )
        + _atom_with_charge(
            "ATOM", 3, "SG", "CYS", "A", 145,
            0.0, 0.0, 0.0, "S", "1-"
        )
        + "END\n"
    ).encode("utf-8")

    ligand = generate_ligand_geometry(smiles="CCSC")
    ligand_resolution = resolve_supported_ligand_reaction(
        ligand
    )

    assert ligand_resolution.status == "resolved"
    assert ligand_resolution.selected is not None

    environment = build_covalent_qmmm_environment(
        protein_payload=protein_payload,
        protein_content_sha256=__import__(
            "hashlib"
        ).sha256(protein_payload).hexdigest(),
        ligand_payload=ligand.payload,
        ligand_media_type=ligand.media_type,
        ligand_content_sha256=ligand.content_sha256,
        selection=CovalentQMMMSelection(
            protein_nucleophile_source_index=2,
            ligand_electrophile_source_index=(
                ligand_resolution.selected.electrophile_atom_index
            ),
            ligand_leaving_group_source_index=(
                ligand_resolution.selected.leaving_group_atom_index
            ),
            protein_nucleophile_formal_charge=-1,
        ),
    )

    state = assess_covalent_qm_state(environment)

    assert state.molecular_charge == -1
    assert state.spin_candidates == (0,)
    assert state.electron_count % 2 == 0

    # CA/CB lack explicit PDB formal-charge fields.  Current runtime
    # treats relevant blank fields as neutral, and preflight exposes that
    # assumption rather than presenting it as high-confidence chemistry.
    assert state.confidence == "runtime_assumption"
    assert state.implicit_neutral_protein_source_indices


def test_declared_partner_acquisition_preserves_ccd_identity_end_to_end() -> None:
    from cgr.pulsate_api.scientific_acquisition import (
        generate_ligand_geometry,
    )
    from cgr.pulsate_api.scientific_resolver import (
        acquire_declared_covalent_partner,
        resolve_supported_ligand_reaction,
    )

    protein_payload = (
        _link("SG", "CYS", "A", 145, "C1", "LIG", "B", 501)
        + _atom(
            "ATOM", 1, "CA", "CYS", "A", 145,
            0.0, 0.0, 0.0, "C"
        )
        + _atom(
            "ATOM", 2, "SG", "CYS", "A", 145,
            1.8, 0.0, 0.0, "S"
        )
        + _atom(
            "HETATM", 3, "C1", "LIG", "B", 501,
            3.0, 0.0, 0.0, "C"
        )
        + _atom(
            "HETATM", 4, "S1", "LIG", "B", 501,
            4.8, 0.0, 0.0, "S"
        )
        + "END\n"
    ).encode("utf-8")

    protein = AcquiredScientificInput(
        artifact_type="protein_structure",
        media_type="chemical/x-pdb",
        payload=protein_payload,
        source_kind="neutral_test_fixture",
        source_identifier="NEUTRAL-PDB-PARTNER-001",
        source_url="https://example.invalid/neutral-pdb-partner-001",
    )

    generated = generate_ligand_geometry(
        smiles="CCSC",
        random_seed=31,
    )
    ccd_payload = generated.payload + b"$$$$\n"

    requests: list[str] = []

    def fetch(
        url: str,
        accept: str,
        maximum_bytes: int,
    ) -> bytes:
        del accept, maximum_bytes
        requests.append(url)
        return ccd_payload

    partner = acquire_declared_covalent_partner(
        protein,
        fetch=fetch,
    )

    assert partner.status == "resolved"
    assert partner.confidence == "high"
    assert partner.reason is None
    assert partner.ccd_identifier == "LIG"
    assert partner.anchor is not None
    assert partner.acquired_ligand is not None

    assert partner.acquired_ligand.source_identifier == "LIG"
    assert (
        partner.acquired_ligand.source_kind
        == "rcsb_pdb_chemical_component_ideal"
    )

    assert requests == [
        "https://files.rcsb.org/ligands/download/LIG_ideal.sdf"
    ]

    reaction = resolve_supported_ligand_reaction(
        partner.acquired_ligand
    )

    assert reaction.status == "resolved"
    assert reaction.selected is not None
    assert (
        reaction.selected.pattern_identifier
        == "methyl-thioether-substitution-v1"
    )


def test_polymer_only_pdb_excludes_secondary_solvent_and_ions() -> None:
    from cgr.pulsate_api.scientific_resolution import (
        ground_acquired_protein_pdb,
        polymer_only_protein_pdb,
    )

    payload = (
        _atom(
            "ATOM", 1, "CA", "CYS", "A", 145,
            0.0, 0.0, 0.0, "C"
        )
        + _atom(
            "ATOM", 2, "SG", "CYS", "A", 145,
            1.8, 0.0, 0.0, "S"
        )
        + _atom(
            "HETATM", 3, "C1", "LIG", "B", 501,
            3.0, 0.0, 0.0, "C"
        )
        + _atom(
            "HETATM", 4, "S1", "LIG", "B", 501,
            4.8, 0.0, 0.0, "S"
        )
        + _atom(
            "HETATM", 5, "O", "HOH", "W", 1,
            8.0, 0.0, 0.0, "O"
        )
        + _atom(
            "HETATM", 6, "ZN", "ZN", "Z", 1,
            10.0, 0.0, 0.0, "ZN"
        )
        + "END\n"
    ).encode("utf-8")

    acquired = AcquiredScientificInput(
        artifact_type="protein_structure",
        media_type="chemical/x-pdb",
        payload=payload,
        source_kind="neutral_test_fixture",
        source_identifier="NEUTRAL-PDB-POLYMER-ONLY",
        source_url=(
            "https://example.invalid/"
            "neutral-pdb-polymer-only"
        ),
    )

    derived = polymer_only_protein_pdb(acquired)

    text = derived.payload.decode("utf-8")

    assert " CYS " in text
    assert " LIG " not in text
    assert " HOH " not in text
    assert " ZN " not in text

    inventory = ground_acquired_protein_pdb(derived)

    assert len(inventory.protein_residues) == 1
    assert inventory.secondary_components == ()
    assert inventory.solvent_components == ()
    assert inventory.ion_components == ()

    assert derived.source_identifier == acquired.source_identifier
    assert derived.source_kind == "grounded_polymer_only_pdb"
    assert (
        derived.generation_method
        == "gemmi_entity_classification_polymer_only_v1"
    )


def _automatic_target_fixture(
    *,
    explicit_ca_charge: bool,
) -> tuple[AcquiredScientificInput, bytes]:
    from cgr.pulsate_api.scientific_acquisition import (
        generate_ligand_geometry,
    )

    ca = (
        _atom_with_charge(
            "ATOM", 1, "CA", "CYS", "A", 145,
            -1.8, 0.0, 0.0, "C", "0+"
        )
        if explicit_ca_charge
        else _atom(
            "ATOM", 1, "CA", "CYS", "A", 145,
            -1.8, 0.0, 0.0, "C"
        )
    )

    protein_payload = (
        _link(
            "SG", "CYS", "A", 145,
            "C1", "LIG", "B", 501
        )
        + ca
        + _atom_with_charge(
            "ATOM", 2, "SG", "CYS", "A", 145,
            0.0, 0.0, 0.0, "S", "1-"
        )
        + _atom(
            "HETATM", 3, "C1", "LIG", "B", 501,
            2.0, 0.0, 0.0, "C"
        )
        + _atom(
            "HETATM", 4, "S1", "LIG", "B", 501,
            3.8, 0.0, 0.0, "S"
        )
        + "END\n"
    ).encode("utf-8")

    protein = AcquiredScientificInput(
        artifact_type="protein_structure",
        media_type="chemical/x-pdb",
        payload=protein_payload,
        source_kind="neutral_test_fixture",
        source_identifier="NEUTRAL-AUTOMATIC-TARGET",
        source_url=(
            "https://example.invalid/"
            "neutral-automatic-target"
        ),
    )

    ligand = generate_ligand_geometry(
        smiles="CCSC",
        random_seed=37,
    )

    return protein, ligand.payload + b"$$$$\n"


def test_automatic_covalent_target_resolves_from_explicit_evidence() -> None:
    from cgr.pulsate_api.scientific_resolver import (
        resolve_automatic_covalent_target,
    )

    protein, ccd_payload = _automatic_target_fixture(
        explicit_ca_charge=True,
    )

    requests: list[str] = []

    def fetch(
        url: str,
        accept: str,
        maximum_bytes: int,
    ) -> bytes:
        del accept, maximum_bytes
        requests.append(url)
        return ccd_payload

    resolution = resolve_automatic_covalent_target(
        protein,
        fetch=fetch,
    )

    assert resolution.status == "resolved"
    assert resolution.confidence == "high"
    assert resolution.reason is None
    assert resolution.target is not None
    assert resolution.protein_input is not None
    assert resolution.ligand_input is not None
    assert resolution.state is not None

    target = resolution.target

    assert target.protein_chain_label == "A"
    assert target.protein_residue_sequence == "145"
    assert target.protein_residue_name == "CYS"
    assert target.protein_atom_name == "SG"
    assert target.protein_nucleophile_formal_charge == -1
    assert (
        target.ligand_reaction_smarts
        == "[C;H3:1]-[S:2]-[C;H2]"
    )
    assert target.selected_total_qm_charge == -1
    assert target.selected_spin == 1
    assert resolution.state.spin_candidates == (1,)
    assert resolution.state.electron_count % 2 == 1

    assert b"LIG" not in resolution.protein_input.payload
    assert resolution.ligand_input.source_identifier == "LIG"

    assert requests == [
        "https://files.rcsb.org/ligands/download/LIG_ideal.sdf"
    ]


def test_automatic_target_requires_confirmation_for_implicit_neutral_charge() -> None:
    from cgr.pulsate_api.scientific_resolver import (
        resolve_automatic_covalent_target,
    )

    protein, ccd_payload = _automatic_target_fixture(
        explicit_ca_charge=False,
    )

    def fetch(
        url: str,
        accept: str,
        maximum_bytes: int,
    ) -> bytes:
        del url, accept, maximum_bytes
        return ccd_payload

    resolution = resolve_automatic_covalent_target(
        protein,
        fetch=fetch,
    )

    assert resolution.status == "confirmation_required"
    assert resolution.target is not None
    assert resolution.state is not None
    assert resolution.confidence == "runtime_assumption"

    assert (
        resolution.reason
        == "qm_state_uses_implicit_neutral_protein_charges"
    )

    assert (
        resolution.state.implicit_neutral_protein_source_indices
    )
