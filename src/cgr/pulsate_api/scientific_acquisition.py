"""Grounded, provenance-preserving acquisition of scientist inputs.

Only explicit identifiers are accepted.  The acquisition boundary never sends a
free-form scientific question to a structure service and never chooses among
search results.  Protein coordinates come from one exact RCSB PDB identifier;
ligand coordinates come from one exact PubChem CID or are generated locally
from an explicitly supplied SMILES/InChI representation.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Callable, Literal
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


_MAXIMUM_INPUT_BYTES = 16 * 1024 * 1024
_PDB_IDENTIFIER = re.compile(r"^[0-9][A-Za-z0-9]{3}$")
_PDB_IN_QUESTION = re.compile(
    r"\bPDB(?:\s+ID)?\s*(?::|=|\bis\b)?\s*([0-9][A-Za-z0-9]{3})\b",
    re.IGNORECASE,
)
_PUBCHEM_IN_QUESTION = re.compile(
    r"\bPubChem(?:\s+CID|\s+compound)?\s*(?::|=|\bis\b)?\s*([1-9][0-9]{0,11})\b",
    re.IGNORECASE,
)
_SMILES_IN_QUESTION = re.compile(
    r"\bSMILES\s*(?::|=|\bis\b)\s*([^\s,;]{1,16384})",
    re.IGNORECASE,
)
_INCHI_IN_QUESTION = re.compile(
    r"\bInChI\s*(?::|=|\bis\b)\s*(InChI=[^\s,;]{1,16384})",
    re.IGNORECASE,
)

FetchBytes = Callable[[str, str, int], bytes]


@dataclass(frozen=True, slots=True)
class GroundedScientificIdentifiers:
    """Identifiers literally present in the scientist's question."""

    protein_pdb_id: str | None = None
    ligand_pubchem_cid: int | None = None
    ligand_smiles: str | None = None
    ligand_inchi: str | None = None


@dataclass(frozen=True, slots=True)
class AcquiredScientificInput:
    """Exact bytes plus bounded source evidence for one acquired input."""

    artifact_type: Literal["protein_structure", "ligand_structure"]
    media_type: str
    payload: bytes
    source_kind: str
    source_identifier: str
    source_url: str | None = None
    generation_method: str | None = None

    @property
    def content_sha256(self) -> str:
        return hashlib.sha256(self.payload).hexdigest()

    @property
    def metadata(self) -> dict[str, str]:
        values = {
            "acquisition_source_kind": self.source_kind,
            "acquisition_source_identifier": self.source_identifier,
            "acquisition_content_sha256": self.content_sha256,
        }
        if self.source_url is not None:
            values["acquisition_source_url"] = self.source_url
        if self.generation_method is not None:
            values["acquisition_generation_method"] = self.generation_method
        return values


def grounded_identifiers(question: str) -> GroundedScientificIdentifiers:
    """Extract only identifiers explicitly labelled in the question."""

    def unique(pattern: re.Pattern[str], label: str) -> str | None:
        values = {match.group(1) for match in pattern.finditer(question)}
        if len(values) > 1:
            raise ValueError(f"The question contains multiple explicit {label} values.")
        return next(iter(values), None)

    pdb_id = unique(_PDB_IN_QUESTION, "PDB identifiers")
    cid = unique(_PUBCHEM_IN_QUESTION, "PubChem CIDs")
    smiles = unique(_SMILES_IN_QUESTION, "SMILES values")
    inchi = unique(_INCHI_IN_QUESTION, "InChI values")
    ligand_sources = sum(value is not None for value in (cid, smiles, inchi))
    if ligand_sources > 1:
        raise ValueError(
            "The question contains multiple explicit ligand structure sources."
        )
    return GroundedScientificIdentifiers(
        protein_pdb_id=pdb_id.upper() if pdb_id is not None else None,
        ligand_pubchem_cid=int(cid) if cid is not None else None,
        ligand_smiles=smiles,
        ligand_inchi=inchi,
    )


def _fetch_bytes(url: str, accept: str, maximum_bytes: int) -> bytes:
    request = Request(
        url,
        headers={
            "Accept": accept,
            "User-Agent": "CGR-Pulsate/0.1 scientific-input-acquisition",
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=30) as response:
            resolved = urlsplit(response.geturl())
            requested = urlsplit(url)
            if (
                resolved.scheme != "https"
                or resolved.hostname != requested.hostname
                or resolved.username is not None
                or resolved.password is not None
            ):
                raise ValueError("Scientific input acquisition redirected unsafely.")
            declared = response.headers.get("Content-Length")
            if declared is not None and int(declared) > maximum_bytes:
                raise ValueError("Scientific input acquisition exceeds its size limit.")
            payload = response.read(maximum_bytes + 1)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError):
        raise ValueError("The exact scientific input could not be acquired safely.") from None
    if not payload or len(payload) > maximum_bytes:
        raise ValueError("Scientific input acquisition exceeds its size limit.")
    return payload


def acquire_protein_pdb(
    pdb_identifier: str,
    *,
    fetch: FetchBytes = _fetch_bytes,
) -> AcquiredScientificInput:
    """Download one exact legacy PDB coordinate entry from RCSB PDB."""

    normalized = pdb_identifier.strip().upper()
    if _PDB_IDENTIFIER.fullmatch(normalized) is None:
        raise ValueError("Protein acquisition requires one exact four-character PDB ID.")
    url = f"https://files.rcsb.org/download/{normalized}.pdb"
    payload = fetch(url, "chemical/x-pdb,text/plain", _MAXIMUM_INPUT_BYTES)
    text = payload.decode("utf-8", errors="strict")
    if not any(line.startswith(("ATOM  ", "HETATM")) for line in text.splitlines()):
        raise ValueError("The acquired PDB entry contains no atomic coordinates.")
    return AcquiredScientificInput(
        artifact_type="protein_structure",
        media_type="chemical/x-pdb",
        payload=payload,
        source_kind="rcsb_pdb_entry",
        source_identifier=normalized,
        source_url=url,
    )


def acquire_ligand_pubchem(
    pubchem_cid: int,
    *,
    fetch: FetchBytes = _fetch_bytes,
) -> AcquiredScientificInput:
    """Download one exact PubChem 3D compound record as SDF."""

    if not isinstance(pubchem_cid, int) or not 1 <= pubchem_cid <= 999_999_999_999:
        raise ValueError("Ligand acquisition requires one positive PubChem CID.")
    url = (
        "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/"
        f"{pubchem_cid}/SDF?record_type=3d"
    )
    payload = fetch(url, "chemical/x-mdl-sdfile", _MAXIMUM_INPUT_BYTES)
    _validate_ligand_geometry(payload)
    return AcquiredScientificInput(
        artifact_type="ligand_structure",
        media_type="chemical/x-mdl-sdfile",
        payload=payload,
        source_kind="pubchem_compound_3d",
        source_identifier=str(pubchem_cid),
        source_url=url,
    )


def generate_ligand_geometry(
    *,
    smiles: str | None = None,
    inchi: str | None = None,
    random_seed: int = 17,
) -> AcquiredScientificInput:
    """Generate one deterministic reviewed ligand geometry with pinned RDKit."""

    if (smiles is None) == (inchi is None):
        raise ValueError("Supply exactly one explicit ligand SMILES or InChI value.")
    from rdkit import Chem
    from rdkit.Chem import AllChem

    if smiles is not None:
        representation = smiles.strip()
        if not representation or len(representation) > 16_384:
            raise ValueError("The explicit ligand SMILES value is invalid.")
        molecule = Chem.MolFromSmiles(representation, sanitize=True)
        source_kind = "explicit_smiles"
    else:
        representation = (inchi or "").strip()
        if not representation.startswith("InChI=") or len(representation) > 16_384:
            raise ValueError("The explicit ligand InChI value is invalid.")
        molecule = Chem.MolFromInchi(representation, sanitize=True)
        source_kind = "explicit_inchi"
    if molecule is None or molecule.GetNumAtoms() <= 0:
        raise ValueError("The explicit ligand representation is invalid.")
    molecule = Chem.AddHs(molecule)
    parameters = AllChem.ETKDGv3()
    parameters.randomSeed = random_seed
    parameters.useRandomCoords = False
    if AllChem.EmbedMolecule(molecule, parameters) != 0:
        raise ValueError("RDKit could not generate a ligand geometry.")
    method = "rdkit_etkdgv3_uff"
    if AllChem.MMFFHasAllMoleculeParams(molecule):
        status = AllChem.MMFFOptimizeMolecule(molecule, maxIters=1000)
        method = "rdkit_etkdgv3_mmff94"
    else:
        status = AllChem.UFFOptimizeMolecule(molecule, maxIters=1000)
    if status < 0:
        raise ValueError("RDKit ligand geometry optimization failed.")
    payload = Chem.MolToMolBlock(molecule).encode("utf-8")
    _validate_ligand_geometry(payload)
    source_digest = hashlib.sha256(representation.encode("utf-8")).hexdigest()
    return AcquiredScientificInput(
        artifact_type="ligand_structure",
        media_type="chemical/x-mdl-molfile",
        payload=payload,
        source_kind=source_kind,
        source_identifier=f"sha256:{source_digest}",
        generation_method=f"{method}_seed_{random_seed}",
    )


def _validate_ligand_geometry(payload: bytes) -> None:
    from rdkit import Chem

    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("The acquired ligand record is not valid UTF-8.") from None
    # Preserve the three-line molfile header: an empty title line is valid and
    # stripping it shifts the counts line into the wrong record position.
    block = text.split("$$$$", 1)[0].rstrip()
    molecule = Chem.MolFromMolBlock(block, removeHs=False, sanitize=True)
    if molecule is None or molecule.GetNumAtoms() <= 0:
        raise ValueError("The acquired ligand record is invalid.")
    if molecule.GetNumConformers() != 1 or not molecule.GetConformer().Is3D():
        raise ValueError("The acquired ligand record has no exact 3D geometry.")


__all__ = [
    "AcquiredScientificInput",
    "GroundedScientificIdentifiers",
    "acquire_ligand_pubchem",
    "acquire_protein_pdb",
    "generate_ligand_geometry",
    "grounded_identifiers",
]
