"""Exact-coordinate scene projection for scientist-supplied research evidence."""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Callable

from cgr.science import ArtifactReference


class ResearchSceneError(ValueError):
    """Raised when exact persisted evidence cannot be projected safely."""


_ELEMENT = re.compile(r"^[A-Za-z]{1,3}$")
_VISUALIZABLE_ARTIFACT_TYPES = frozenset(
    {
        "ligand_structure",
        "docking_pose_pdbqt",
        "docking_receptor_pdbqt",
        "molecular_structure",
        "molecular_candidate_docking_poses_pdbqt",
        "prepared_molecular_structure",
        "prepared_ligand",
        "prepared_receptor",
        "predicted_protein_structure",
        "protein_structure",
    }
)


def visualizable_research_artifact(reference: ArtifactReference) -> bool:
    """Return whether an exact structure has a supported coordinate encoding."""

    return (
        reference.artifact_type in _VISUALIZABLE_ARTIFACT_TYPES
        and reference.media_type
        in {
            "chemical/x-mdl-molfile",
            "chemical/x-mdl-sdfile",
            "chemical/x-mol2",
            "chemical/x-pdb",
            "chemical/x-pdbqt",
        }
    )


def project_research_scene(
    *,
    session_identifier: str,
    reference: ArtifactReference,
    payload: bytes,
    conformation_index: int = 0,
) -> dict[str, object]:
    """Project only coordinates and bonds explicitly present in exact evidence."""

    if not visualizable_research_artifact(reference):
        raise ResearchSceneError(
            "This research artifact does not contain a supported coordinate scene."
        )
    if hashlib.sha256(payload).hexdigest() != reference.content_sha256:
        raise ResearchSceneError("Research artifact content identity is inconsistent.")
    if not isinstance(conformation_index, int) or not 0 <= conformation_index <= 999:
        raise ResearchSceneError("Research conformation index is invalid.")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        raise ResearchSceneError(
            "The coordinate evidence is not valid UTF-8 text."
        ) from None

    parser: Callable[[str], tuple[list[dict[str, object]], list[dict[str, object]]]]
    if reference.media_type == "chemical/x-pdb":
        parser = _parse_pdb
    elif reference.media_type == "chemical/x-pdbqt":
        parser = lambda value: _parse_pdbqt(  # noqa: E731
            value, conformation_index=conformation_index
        )
    elif reference.media_type in {
        "chemical/x-mdl-molfile",
        "chemical/x-mdl-sdfile",
    }:
        parser = _parse_mol_v2000
    else:
        parser = _parse_mol2
    atoms, bonds = parser(text)
    for atom in atoms:
        atom["source_atom_identifier"] = atom["atom_identifier"]
        atom["structure_artifact_identifier"] = reference.artifact_identifier
    scene_digest = hashlib.sha256(
        (
            f"{session_identifier}\x1f{reference.artifact_identifier}"
            f"\x1f{conformation_index}"
        ).encode("utf-8")
    ).hexdigest()[:32]
    scene: dict[str, object] = {
        "scene_identifier": f"research-scene-{scene_digest}",
        "scene_stage": "scientist_evidence",
        "structure_identifier": reference.artifact_identifier,
        "structure_hash": reference.content_sha256,
        "coordinate_unit": "angstrom",
        "atoms": atoms,
        "provenance": {
            "research_session_identifier": session_identifier,
            "source_artifact_identifier": reference.artifact_identifier,
            "source_content_sha256": reference.content_sha256,
            "coordinate_source": "exact_persisted_evidence",
            "conformation_index": conformation_index,
        },
        "artifact_references": [reference.artifact_identifier],
    }
    # PDBQT has no complete covalent bond table. Omitting the field allows the
    # viewer's bounded geometry adapter to mark any displayed bonds as inferred.
    if reference.media_type != "chemical/x-pdbqt":
        scene["bonds"] = bonds
    return scene


def project_research_complex_scene(
    *,
    session_identifier: str,
    evidence: tuple[tuple[ArtifactReference, bytes], ...],
    conformation_indices: tuple[int, ...] | None = None,
) -> dict[str, object]:
    """Combine up to four exact coordinate artifacts without merging identities."""

    if not 1 <= len(evidence) <= 4:
        raise ResearchSceneError(
            "A research comparison scene requires between one and four structures."
        )
    if conformation_indices is None:
        conformation_indices = (0,) * len(evidence)
    if len(conformation_indices) != len(evidence):
        raise ResearchSceneError(
            "Every comparison structure requires one conformation index."
        )
    projected = [
        project_research_scene(
            session_identifier=session_identifier,
            reference=reference,
            payload=payload,
            conformation_index=conformation_index,
        )
        for (reference, payload), conformation_index in zip(
            evidence, conformation_indices, strict=True
        )
    ]
    atoms: list[dict[str, object]] = []
    bonds: list[dict[str, object]] = []
    artifact_references: list[str] = []
    for structure_position, ((reference, _payload), scene) in enumerate(
        zip(evidence, projected, strict=True),
        start=1,
    ):
        prefix = f"structure-{structure_position}-"
        identifier_map: dict[str, str] = {}
        for raw_atom in scene["atoms"]:
            atom = dict(raw_atom)
            source_identifier = str(atom["atom_identifier"])
            atom_identifier = f"{prefix}{source_identifier}"
            identifier_map[source_identifier] = atom_identifier
            atom["atom_identifier"] = atom_identifier
            atom["source_atom_identifier"] = source_identifier
            atom["structure_artifact_identifier"] = reference.artifact_identifier
            atoms.append(atom)
        for raw_bond in scene.get("bonds", []):
            bond = dict(raw_bond)
            source_bond_identifier = str(bond["bond_identifier"])
            bond["bond_identifier"] = f"{prefix}{source_bond_identifier}"
            bond["atom_identifiers"] = [
                identifier_map[str(item)] for item in bond["atom_identifiers"]
            ]
            bonds.append(bond)
        artifact_references.append(reference.artifact_identifier)
    digest = hashlib.sha256(
        (
            session_identifier
            + "\x1f"
            + "\x1f".join(artifact_references)
            + "\x1f"
            + "\x1f".join(str(item) for item in conformation_indices)
        ).encode("utf-8")
    ).hexdigest()[:32]
    return {
        "scene_identifier": f"research-complex-scene-{digest}",
        "scene_stage": "research_comparison",
        "structure_identifier": f"research-complex-{digest}",
        "coordinate_unit": "angstrom",
        "atoms": atoms,
        "bonds": bonds,
        "provenance": {
            "research_session_identifier": session_identifier,
            "coordinate_source": "exact_persisted_evidence",
            "structure_artifact_identifiers": artifact_references,
            "conformation_indices": list(conformation_indices),
        },
        "artifact_references": artifact_references,
    }


def _coordinate(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError:
        raise ResearchSceneError("The coordinate evidence contains invalid numbers.") from None
    if not math.isfinite(parsed):
        raise ResearchSceneError("The coordinate evidence contains invalid numbers.")
    return parsed


def _element(value: str) -> str:
    value = value.strip()
    if _ELEMENT.fullmatch(value) is None:
        raise ResearchSceneError(
            "An explicit element symbol is required for every displayed atom."
        )
    return value[0].upper() + value[1:].lower()


def _parse_pdb(
    text: str,
    *,
    pdbqt: bool = False,
    conformation_index: int = 0,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    atoms: list[dict[str, object]] = []
    serial_to_identifier: dict[str, str] = {}
    connections: set[tuple[str, str]] = set()
    lines = text.splitlines()
    has_models = any(line[:6].strip().upper() == "MODEL" for line in lines)
    current_model = -1
    active_model = not has_models and conformation_index == 0
    for line in lines:
        record = line[:6].strip().upper()
        if record == "MODEL":
            current_model += 1
            active_model = current_model == conformation_index
            continue
        if record == "ENDMDL":
            if active_model and atoms:
                break
            active_model = False
            continue
        if not active_model:
            continue
        if record in {"ATOM", "HETATM"}:
            if len(line) < 54:
                raise ResearchSceneError(
                    "PDB atoms require fixed-width coordinates."
                )
            serial = line[6:11].strip()
            if not serial or serial in serial_to_identifier:
                raise ResearchSceneError("PDB atom serial identities must be unique.")
            atom_identifier = f"atom-{len(atoms) + 1:06d}-{serial}"
            serial_to_identifier[serial] = atom_identifier
            atom: dict[str, object] = {
                "atom_identifier": atom_identifier,
                "element": _pdb_element(line, pdbqt=pdbqt),
                "coordinates": [
                    _coordinate(line[30:38]),
                    _coordinate(line[38:46]),
                    _coordinate(line[46:54]),
                ],
                "atom_name": line[12:16].strip(),
                "residue_name": line[17:20].strip(),
                "chain_identifier": line[21:22].strip() or None,
                "residue_sequence": line[22:27].strip() or None,
            }
            if pdbqt:
                fields = line.split()
                if len(fields) >= 2:
                    try:
                        atom["partial_charge"] = _coordinate(fields[-2])
                    except ResearchSceneError:
                        pass
            atoms.append(atom)
        elif record == "CONECT":
            fields = line[6:].split()
            if len(fields) < 2:
                continue
            for destination in fields[1:]:
                if destination != fields[0]:
                    connections.add(tuple(sorted((fields[0], destination))))
    if not atoms:
        raise ResearchSceneError("The coordinate evidence contains no displayable atoms.")
    bonds: list[dict[str, object]] = []
    for position, (left, right) in enumerate(sorted(connections), start=1):
        if left not in serial_to_identifier or right not in serial_to_identifier:
            raise ResearchSceneError("PDB bond evidence references an unknown atom.")
        bonds.append(
            {
                "bond_identifier": f"bond-{position:06d}",
                "atom_identifiers": [
                    serial_to_identifier[left],
                    serial_to_identifier[right],
                ],
            }
        )
    return atoms, bonds


def _pdbqt_element(atom_type: str) -> str:
    atom_type = atom_type.strip()
    known = {
        "A": "C",
        "C": "C",
        "Cl": "Cl",
        "F": "F",
        "HD": "H",
        "I": "I",
        "Mg": "Mg",
        "Mn": "Mn",
        "N": "N",
        "NA": "N",
        "NS": "N",
        "OA": "O",
        "OS": "O",
        "P": "P",
        "S": "S",
        "SA": "S",
        "Si": "Si",
        "Zn": "Zn",
    }
    if atom_type in known:
        return known[atom_type]
    letters = "".join(character for character in atom_type if character.isalpha())
    if not letters:
        raise ResearchSceneError("A PDBQT atom type cannot be resolved to an element.")
    return _element(letters[:2] if len(letters) > 1 and letters[1].islower() else letters[:1])


def _pdb_element(line: str, *, pdbqt: bool) -> str:
    declared = line[76:78].strip() if len(line) >= 78 else ""
    if declared and not pdbqt:
        return _element(declared)
    if pdbqt:
        fields = line.split()
        if fields:
            return _pdbqt_element(fields[-1])
    atom_name = line[12:16].strip().lstrip("0123456789")
    return _element(atom_name[:1])


def _parse_pdbqt(
    text: str,
    *,
    conformation_index: int = 0,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    return _parse_pdb(
        text,
        pdbqt=True,
        conformation_index=conformation_index,
    )


def _parse_mol_v2000(
    text: str,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    block = text.split("$$$$", 1)[0]
    lines = block.splitlines()
    if len(lines) < 4 or "V2000" not in lines[3]:
        raise ResearchSceneError("Only explicit MDL V2000 coordinate evidence is supported.")
    try:
        atom_count = int(lines[3][0:3])
        bond_count = int(lines[3][3:6])
    except ValueError:
        raise ResearchSceneError("The MDL counts line is invalid.") from None
    if atom_count < 1 or bond_count < 0 or len(lines) < 4 + atom_count + bond_count:
        raise ResearchSceneError("The MDL coordinate evidence is incomplete.")
    atoms: list[dict[str, object]] = []
    for position, line in enumerate(lines[4 : 4 + atom_count], start=1):
        if len(line) < 34:
            raise ResearchSceneError("An MDL atom record is incomplete.")
        atoms.append(
            {
                "atom_identifier": f"atom-{position:06d}",
                "element": _element(line[31:34]),
                "coordinates": [
                    _coordinate(line[0:10]),
                    _coordinate(line[10:20]),
                    _coordinate(line[20:30]),
                ],
            }
        )
    bonds: list[dict[str, object]] = []
    for position, line in enumerate(
        lines[4 + atom_count : 4 + atom_count + bond_count], start=1
    ):
        try:
            left = int(line[0:3])
            right = int(line[3:6])
            order = int(line[6:9])
        except ValueError:
            raise ResearchSceneError("An MDL bond record is invalid.") from None
        if not 1 <= left <= atom_count or not 1 <= right <= atom_count or left == right:
            raise ResearchSceneError("An MDL bond references an invalid atom.")
        if order not in {1, 2, 3, 4}:
            raise ResearchSceneError("An MDL bond order is unsupported.")
        bonds.append(
            {
                "bond_identifier": f"bond-{position:06d}",
                "atom_identifiers": [
                    f"atom-{left:06d}",
                    f"atom-{right:06d}",
                ],
                "order": order,
            }
        )
    return atoms, bonds


def _parse_mol2(text: str) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        if line.startswith("@<TRIPOS>"):
            current = line[len("@<TRIPOS>") :].strip().upper()
            sections.setdefault(current, [])
        elif current is not None and line.strip():
            sections[current].append(line)
    atom_lines = sections.get("ATOM", [])
    if not atom_lines:
        raise ResearchSceneError("The MOL2 evidence contains no explicit atoms.")
    atoms: list[dict[str, object]] = []
    source_to_identifier: dict[str, str] = {}
    for position, line in enumerate(atom_lines, start=1):
        fields = line.split()
        if len(fields) < 6 or fields[0] in source_to_identifier:
            raise ResearchSceneError("A MOL2 atom record is invalid.")
        atom_identifier = f"atom-{position:06d}-{fields[0]}"
        source_to_identifier[fields[0]] = atom_identifier
        atoms.append(
            {
                "atom_identifier": atom_identifier,
                "element": _element(fields[5].split(".", 1)[0]),
                "coordinates": [
                    _coordinate(fields[2]),
                    _coordinate(fields[3]),
                    _coordinate(fields[4]),
                ],
            }
        )
    order_values = {"1": 1, "2": 2, "3": 3, "ar": 4}
    bonds: list[dict[str, object]] = []
    for position, line in enumerate(sections.get("BOND", []), start=1):
        fields = line.split()
        if len(fields) < 4 or fields[1] == fields[2]:
            raise ResearchSceneError("A MOL2 bond record is invalid.")
        left = source_to_identifier.get(fields[1])
        right = source_to_identifier.get(fields[2])
        order = order_values.get(fields[3].casefold())
        if left is None or right is None or order is None:
            raise ResearchSceneError("A MOL2 bond record is unsupported or invalid.")
        bonds.append(
            {
                "bond_identifier": f"bond-{position:06d}",
                "atom_identifiers": [left, right],
                "order": order,
            }
        )
    return atoms, bonds


__all__ = [
    "ResearchSceneError",
    "project_research_scene",
    "project_research_complex_scene",
    "visualizable_research_artifact",
]
