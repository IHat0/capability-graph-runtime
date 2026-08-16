"""Evidence-backed scientific resolution over grounded molecular records.

This module turns explicit structural evidence into deterministic scientific
anchors. It never invents missing chemistry: unresolved and ambiguous cases
remain blocked for later fallback or scientist confirmation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .scientific_acquisition import AcquiredScientificInput
from .scientific_relationships import (
    PdbConnectionEndpoint,
    declared_pdb_relationships,
)


ResolutionStatus = Literal["resolved", "unresolved", "ambiguous"]
ResolutionConfidence = Literal["high"]


@dataclass(frozen=True, slots=True)
class CovalentAnchorCandidate:
    """One evidence-backed protein-to-secondary structural anchor."""

    protein_endpoint: PdbConnectionEndpoint
    secondary_endpoint: PdbConnectionEndpoint
    connection_type: str
    evidence_kind: Literal["pdb_declared_connection"] = (
        "pdb_declared_connection"
    )


@dataclass(frozen=True, slots=True)
class CovalentAnchorResolution:
    """Result of resolving a covalent anchor from declared source evidence."""

    status: ResolutionStatus
    selected: CovalentAnchorCandidate | None
    candidates: tuple[CovalentAnchorCandidate, ...]
    confidence: ResolutionConfidence | None
    reason: str | None


def _endpoint_key(endpoint: PdbConnectionEndpoint) -> tuple[str, ...]:
    return (
        endpoint.entity_type,
        endpoint.residue_name,
        endpoint.chain_label,
        endpoint.residue_sequence,
        endpoint.insertion_code or "",
        endpoint.atom_name,
        endpoint.source_serial,
    )


def _candidate_key(
    candidate: CovalentAnchorCandidate,
) -> tuple[tuple[str, ...], tuple[str, ...], str]:
    return (
        _endpoint_key(candidate.protein_endpoint),
        _endpoint_key(candidate.secondary_endpoint),
        candidate.connection_type,
    )


def _orient_candidate(
    endpoint_a: PdbConnectionEndpoint,
    endpoint_b: PdbConnectionEndpoint,
    *,
    connection_type: str,
) -> CovalentAnchorCandidate | None:
    secondary_types = {"nonpolymer", "branched"}

    if (
        endpoint_a.entity_type == "polymer"
        and endpoint_b.entity_type in secondary_types
    ):
        protein = endpoint_a
        secondary = endpoint_b
    elif (
        endpoint_b.entity_type == "polymer"
        and endpoint_a.entity_type in secondary_types
    ):
        protein = endpoint_b
        secondary = endpoint_a
    else:
        return None

    return CovalentAnchorCandidate(
        protein_endpoint=protein,
        secondary_endpoint=secondary,
        connection_type=connection_type,
    )


def resolve_declared_covalent_anchor(
    acquired: AcquiredScientificInput,
) -> CovalentAnchorResolution:
    """Resolve one unique protein-secondary anchor from explicit PDB evidence."""

    evidence = declared_pdb_relationships(acquired)

    unique: dict[
        tuple[tuple[str, ...], tuple[str, ...], str],
        CovalentAnchorCandidate,
    ] = {}

    for connection in evidence.protein_secondary_connections:
        candidate = _orient_candidate(
            connection.endpoint_a,
            connection.endpoint_b,
            connection_type=connection.connection_type,
        )
        if candidate is None:
            continue
        unique[_candidate_key(candidate)] = candidate

    candidates = tuple(
        sorted(
            unique.values(),
            key=_candidate_key,
        )
    )

    if not candidates:
        return CovalentAnchorResolution(
            status="unresolved",
            selected=None,
            candidates=(),
            confidence=None,
            reason="no_explicit_protein_secondary_connection",
        )

    if len(candidates) > 1:
        return CovalentAnchorResolution(
            status="ambiguous",
            selected=None,
            candidates=candidates,
            confidence=None,
            reason="multiple_explicit_protein_secondary_connections",
        )

    return CovalentAnchorResolution(
        status="resolved",
        selected=candidates[0],
        candidates=candidates,
        confidence="high",
        reason=None,
    )


__all__ = [
    "CovalentAnchorCandidate",
    "CovalentAnchorResolution",
    "resolve_declared_covalent_anchor",
]


@dataclass(frozen=True, slots=True)
class LigandReactionCandidate:
    """One ligand reaction pair supported by an audited transition template."""

    pattern_identifier: str
    reaction_family: Literal["covalent_substitution"]
    reaction_smarts: str
    electrophile_atom_index: int
    leaving_group_atom_index: int
    electrophile_element: str
    leaving_group_element: str
    evidence_kind: Literal["audited_supported_transition_pattern"] = (
        "audited_supported_transition_pattern"
    )


@dataclass(frozen=True, slots=True)
class LigandReactionResolution:
    """Resolution of one supported ligand reaction pair."""

    status: ResolutionStatus
    selected: LigandReactionCandidate | None
    candidates: tuple[LigandReactionCandidate, ...]
    confidence: ResolutionConfidence | None
    reason: str | None


_SUPPORTED_COVALENT_PATTERNS = (
    (
        "methyl-thioether-substitution-v1",
        "[C;H3:1]-[S:2]-[C;H2]",
    ),
)


def _ligand_molecule(acquired: AcquiredScientificInput):
    if acquired.artifact_type != "ligand_structure":
        raise ValueError(
            "Ligand reaction resolution requires a ligand_structure acquisition."
        )

    media_type = acquired.media_type.casefold()
    if "mol" not in media_type and "sdf" not in media_type:
        raise ValueError(
            "Ligand reaction resolution requires MOL or SDF structure input."
        )

    try:
        from rdkit import Chem
    except ImportError:
        raise ValueError(
            "RDKit is required for ligand reaction resolution."
        ) from None

    try:
        text = acquired.payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise ValueError(
            "Acquired ligand payload must be valid UTF-8 text."
        ) from None

    if "sdf" in media_type:
        import io

        supplier = Chem.ForwardSDMolSupplier(
            io.BytesIO(acquired.payload),
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
            raise ValueError(
                "Ligand SDF resolution requires exactly one valid molecule."
            )
        molecule = molecules[0]
    else:
        molecule = Chem.MolFromMolBlock(
            text,
            sanitize=True,
            removeHs=False,
            strictParsing=True,
        )
        if molecule is None:
            raise ValueError(
                "Ligand MOL input could not be parsed."
            )

    return molecule


def resolve_supported_ligand_reaction(
    acquired: AcquiredScientificInput,
) -> LigandReactionResolution:
    """Resolve one audited covalent-substitution pair from the ligand graph."""

    from rdkit import Chem

    molecule = _ligand_molecule(acquired)
    candidates: list[LigandReactionCandidate] = []

    for pattern_identifier, smarts in _SUPPORTED_COVALENT_PATTERNS:
        query = Chem.MolFromSmarts(smarts)
        if query is None:
            raise RuntimeError(
                f"Audited reaction pattern {pattern_identifier} is invalid."
            )

        mapped_query_atoms = {
            int(atom.GetAtomMapNum()): int(atom.GetIdx())
            for atom in query.GetAtoms()
            if int(atom.GetAtomMapNum()) > 0
        }
        if set(mapped_query_atoms) != {1, 2}:
            raise RuntimeError(
                f"Audited reaction pattern {pattern_identifier} "
                "must map electrophile :1 and leaving group :2."
            )

        matches = molecule.GetSubstructMatches(
            query,
            uniquify=True,
            maxMatches=64,
        )

        for match in matches:
            electrophile = int(match[mapped_query_atoms[1]])
            leaving_group = int(match[mapped_query_atoms[2]])

            bond = molecule.GetBondBetweenAtoms(
                electrophile,
                leaving_group,
            )
            if bond is None:
                continue

            candidates.append(
                LigandReactionCandidate(
                    pattern_identifier=pattern_identifier,
                    reaction_family="covalent_substitution",
                    reaction_smarts=smarts,
                    electrophile_atom_index=electrophile,
                    leaving_group_atom_index=leaving_group,
                    electrophile_element=(
                        molecule.GetAtomWithIdx(electrophile).GetSymbol()
                    ),
                    leaving_group_element=(
                        molecule.GetAtomWithIdx(leaving_group).GetSymbol()
                    ),
                )
            )

    unique = {
        (
            candidate.pattern_identifier,
            candidate.electrophile_atom_index,
            candidate.leaving_group_atom_index,
        ): candidate
        for candidate in candidates
    }

    ordered = tuple(
        sorted(
            unique.values(),
            key=lambda item: (
                item.pattern_identifier,
                item.electrophile_atom_index,
                item.leaving_group_atom_index,
            ),
        )
    )

    if not ordered:
        return LigandReactionResolution(
            status="unresolved",
            selected=None,
            candidates=(),
            confidence=None,
            reason="no_supported_covalent_substitution_pattern",
        )

    if len(ordered) > 1:
        return LigandReactionResolution(
            status="ambiguous",
            selected=None,
            candidates=ordered,
            confidence=None,
            reason="multiple_supported_covalent_substitution_patterns",
        )

    return LigandReactionResolution(
        status="resolved",
        selected=ordered[0],
        candidates=ordered,
        confidence="high",
        reason=None,
    )


__all__ += [
    "LigandReactionCandidate",
    "LigandReactionResolution",
    "resolve_supported_ligand_reaction",
]


@dataclass(frozen=True, slots=True)
class ProteinNucleophileChargeResolution:
    """Explicit source evidence for the selected protein nucleophile charge."""

    status: Literal["resolved", "unresolved"]
    formal_charge: int | None
    confidence: ResolutionConfidence | None
    evidence_kind: Literal["pdb_atom_formal_charge"] | None
    source_serial: str
    reason: str | None


def _pdb_formal_charge(raw: str) -> int | None:
    value = raw.strip()
    if not value:
        return None

    if (
        len(value) == 2
        and value[0].isdigit()
        and value[1] in {"+", "-"}
    ):
        magnitude = int(value[0])
        return magnitude if value[1] == "+" else -magnitude

    raise ValueError(
        "The selected PDB atom contains an invalid explicit formal-charge field."
    )


def resolve_explicit_protein_nucleophile_charge(
    acquired: AcquiredScientificInput,
    candidate: CovalentAnchorCandidate,
) -> ProteinNucleophileChargeResolution:
    """Resolve nucleophile charge only when the source PDB states it explicitly."""

    if acquired.artifact_type != "protein_structure":
        raise ValueError(
            "Protein nucleophile charge resolution requires protein_structure input."
        )
    if acquired.media_type != "chemical/x-pdb":
        raise ValueError(
            "Protein nucleophile charge resolution requires chemical/x-pdb input."
        )

    try:
        text = acquired.payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise ValueError(
            "Acquired PDB payload must be valid UTF-8 text."
        ) from None

    source_serial = candidate.protein_endpoint.source_serial
    matches: list[int | None] = []

    for line in text.splitlines():
        if not line.startswith(("ATOM  ", "HETATM")):
            continue

        serial = line[6:11].strip() if len(line) >= 11 else ""
        if serial != source_serial:
            continue

        raw_charge = line[78:80] if len(line) >= 80 else ""
        matches.append(_pdb_formal_charge(raw_charge))

    if len(matches) != 1:
        raise ValueError(
            "The selected protein atom does not resolve exactly once in the source PDB."
        )

    formal_charge = matches[0]

    if formal_charge is None:
        return ProteinNucleophileChargeResolution(
            status="unresolved",
            formal_charge=None,
            confidence=None,
            evidence_kind=None,
            source_serial=source_serial,
            reason="protein_nucleophile_formal_charge_not_explicit",
        )

    return ProteinNucleophileChargeResolution(
        status="resolved",
        formal_charge=formal_charge,
        confidence="high",
        evidence_kind="pdb_atom_formal_charge",
        source_serial=source_serial,
        reason=None,
    )


__all__ += [
    "ProteinNucleophileChargeResolution",
    "resolve_explicit_protein_nucleophile_charge",
]


from collections.abc import Callable

from .scientific_acquisition import acquire_pdb_chemical_component


PartnerFetch = Callable[[str, str, int], bytes]


@dataclass(frozen=True, slots=True)
class DeclaredCovalentPartnerAcquisition:
    """Exact CCD ligand acquired from one PDB-declared covalent partner."""

    status: ResolutionStatus
    ccd_identifier: str | None
    acquired_ligand: AcquiredScientificInput | None
    anchor: CovalentAnchorCandidate | None
    confidence: ResolutionConfidence | None
    reason: str | None


def acquire_declared_covalent_partner(
    protein: AcquiredScientificInput,
    *,
    fetch: PartnerFetch | None = None,
) -> DeclaredCovalentPartnerAcquisition:
    """Acquire the exact CCD structure named by a unique PDB-declared partner."""

    anchor_resolution = resolve_declared_covalent_anchor(protein)

    if anchor_resolution.status == "unresolved":
        return DeclaredCovalentPartnerAcquisition(
            status="unresolved",
            ccd_identifier=None,
            acquired_ligand=None,
            anchor=None,
            confidence=None,
            reason=anchor_resolution.reason,
        )

    if anchor_resolution.status == "ambiguous":
        return DeclaredCovalentPartnerAcquisition(
            status="ambiguous",
            ccd_identifier=None,
            acquired_ligand=None,
            anchor=None,
            confidence=None,
            reason=anchor_resolution.reason,
        )

    anchor = anchor_resolution.selected
    if anchor is None:
        raise ScientificResolutionError(
            "Resolved covalent-anchor state contains no selected anchor."
        )

    ccd_identifier = (
        anchor.secondary_endpoint.residue_name.strip().upper()
    )
    if not ccd_identifier:
        raise ScientificResolutionError(
            "The declared secondary component has no CCD identity."
        )

    try:
        if fetch is None:
            acquired = acquire_pdb_chemical_component(
                ccd_identifier
            )
        else:
            acquired = acquire_pdb_chemical_component(
                ccd_identifier,
                fetch=fetch,
            )
    except ValueError:
        raise ScientificResolutionError(
            "The exact PDB-declared chemical component could not be acquired."
        ) from None

    if (
        acquired.source_kind
        != "rcsb_pdb_chemical_component_ideal"
        or acquired.source_identifier != ccd_identifier
    ):
        raise ScientificResolutionError(
            "Acquired CCD ligand identity does not match the PDB-declared partner."
        )

    return DeclaredCovalentPartnerAcquisition(
        status="resolved",
        ccd_identifier=ccd_identifier,
        acquired_ligand=acquired,
        anchor=anchor,
        confidence="high",
        reason=None,
    )


__all__ += [
    "DeclaredCovalentPartnerAcquisition",
    "acquire_declared_covalent_partner",
]


from .scientific_resolution import polymer_only_protein_pdb
from .scientific_objectives import CovalentReactionTarget
from .scientific_qmmm_state import (
    CovalentQMMMSelection,
    CovalentQMMMStateError,
    CovalentQMStateAssessment,
    assess_covalent_qm_state,
    build_covalent_qmmm_environment,
)


AutomaticCovalentTargetStatus = Literal[
    "resolved",
    "confirmation_required",
    "unresolved",
    "ambiguous",
]


@dataclass(frozen=True, slots=True)
class AutomaticCovalentTargetResolution:
    """Evidence-backed automatic covalent reaction-target resolution."""

    status: AutomaticCovalentTargetStatus
    target: CovalentReactionTarget | None
    protein_input: AcquiredScientificInput | None
    ligand_input: AcquiredScientificInput | None
    state: CovalentQMStateAssessment | None
    confidence: Literal["high", "runtime_assumption"] | None
    reason: str | None


def _polymer_source_index(
    protein: AcquiredScientificInput,
    *,
    source_serial: str,
) -> int:
    try:
        text = protein.payload.decode(
            "utf-8",
            errors="strict",
        )
    except UnicodeDecodeError:
        raise ScientificResolutionError(
            "Polymer-only PDB must be valid UTF-8 text."
        ) from None

    matches: list[int] = []
    atom_index = 0

    for line in text.splitlines():
        if not line.startswith(("ATOM  ", "HETATM")):
            continue

        serial = (
            line[6:11].strip()
            if len(line) >= 11
            else ""
        )

        if serial == source_serial:
            matches.append(atom_index)

        atom_index += 1

    if len(matches) != 1:
        raise ScientificResolutionError(
            "Selected protein reaction atom does not resolve exactly once "
            "in the polymer-only structure."
        )

    return matches[0]


def resolve_automatic_covalent_target(
    protein: AcquiredScientificInput,
    *,
    fetch: PartnerFetch | None = None,
) -> AutomaticCovalentTargetResolution:
    """Resolve the complete audited covalent target without guessing."""

    partner = acquire_declared_covalent_partner(
        protein,
        fetch=fetch,
    )

    if partner.status != "resolved":
        return AutomaticCovalentTargetResolution(
            status=partner.status,
            target=None,
            protein_input=None,
            ligand_input=None,
            state=None,
            confidence=None,
            reason=partner.reason,
        )

    if (
        partner.anchor is None
        or partner.acquired_ligand is None
    ):
        raise ScientificResolutionError(
            "Resolved declared partner is incomplete."
        )

    reaction = resolve_supported_ligand_reaction(
        partner.acquired_ligand
    )

    if reaction.status != "resolved":
        return AutomaticCovalentTargetResolution(
            status=reaction.status,
            target=None,
            protein_input=None,
            ligand_input=partner.acquired_ligand,
            state=None,
            confidence=None,
            reason=reaction.reason,
        )

    if reaction.selected is None:
        raise ScientificResolutionError(
            "Resolved ligand reaction contains no selected candidate."
        )

    charge = resolve_explicit_protein_nucleophile_charge(
        protein,
        partner.anchor,
    )

    if charge.status != "resolved":
        return AutomaticCovalentTargetResolution(
            status="confirmation_required",
            target=None,
            protein_input=None,
            ligand_input=partner.acquired_ligand,
            state=None,
            confidence=None,
            reason=charge.reason,
        )

    if charge.formal_charge is None:
        raise ScientificResolutionError(
            "Resolved nucleophile charge has no formal-charge value."
        )

    polymer = polymer_only_protein_pdb(protein)

    protein_index = _polymer_source_index(
        polymer,
        source_serial=(
            partner.anchor.protein_endpoint.source_serial
        ),
    )

    try:
        environment = build_covalent_qmmm_environment(
            protein_payload=polymer.payload,
            protein_content_sha256=polymer.content_sha256,
            ligand_payload=partner.acquired_ligand.payload,
            ligand_media_type=partner.acquired_ligand.media_type,
            ligand_content_sha256=(
                partner.acquired_ligand.content_sha256
            ),
            selection=CovalentQMMMSelection(
                protein_nucleophile_source_index=(
                    protein_index
                ),
                ligand_electrophile_source_index=(
                    reaction.selected.electrophile_atom_index
                ),
                ligand_leaving_group_source_index=(
                    reaction.selected.leaving_group_atom_index
                ),
                protein_nucleophile_formal_charge=(
                    charge.formal_charge
                ),
            ),
        )

        state = assess_covalent_qm_state(environment)

    except CovalentQMMMStateError:
        return AutomaticCovalentTargetResolution(
            status="unresolved",
            target=None,
            protein_input=polymer,
            ligand_input=partner.acquired_ligand,
            state=None,
            confidence=None,
            reason="covalent_qm_state_preflight_failed",
        )

    if len(state.spin_candidates) != 1:
        return AutomaticCovalentTargetResolution(
            status="ambiguous",
            target=None,
            protein_input=polymer,
            ligand_input=partner.acquired_ligand,
            state=state,
            confidence=None,
            reason="multiple_qm_spin_candidates",
        )

    target = CovalentReactionTarget(
        protein_chain_label=(
            partner.anchor.protein_endpoint.chain_label
        ),
        protein_residue_sequence=(
            partner.anchor.protein_endpoint.residue_sequence
        ),
        protein_residue_name=(
            partner.anchor.protein_endpoint.residue_name
        ),
        protein_atom_name=(
            partner.anchor.protein_endpoint.atom_name
        ),
        protein_nucleophile_formal_charge=(
            charge.formal_charge
        ),
        ligand_reaction_smarts=(
            reaction.selected.reaction_smarts
        ),
        selected_total_qm_charge=(
            state.molecular_charge
        ),
        selected_spin=state.spin_candidates[0],
    )

    if state.confidence == "runtime_assumption":
        return AutomaticCovalentTargetResolution(
            status="confirmation_required",
            target=target,
            protein_input=polymer,
            ligand_input=partner.acquired_ligand,
            state=state,
            confidence="runtime_assumption",
            reason=(
                "qm_state_uses_implicit_neutral_protein_charges"
            ),
        )

    return AutomaticCovalentTargetResolution(
        status="resolved",
        target=target,
        protein_input=polymer,
        ligand_input=partner.acquired_ligand,
        state=state,
        confidence="high",
        reason=None,
    )


__all__ += [
    "AutomaticCovalentTargetResolution",
    "resolve_automatic_covalent_target",
]
