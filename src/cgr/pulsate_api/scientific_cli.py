"""Terminal client for the authenticated cross-phase scientific API."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from cgr.kernel.contracts import CapabilityVersion
from cgr.science import ArtifactReference, CreationProvenance

from .scientific_acquisition import (
    AcquiredScientificInput,
    acquire_ligand_pubchem,
    acquire_protein_pdb,
    generate_ligand_geometry,
    grounded_identifiers,
)


_VERSION = CapabilityVersion(major=1, minor=0, patch=0)
_INPUT_TYPES = {
    "protein_structure",
    "ligand_structure",
    "molecular_structure",
    "prepared_receptor",
    "prepared_ligand",
}
_MAXIMUM_INPUT_BYTES = 16 * 1024 * 1024


def _media_type(path: Path) -> str:
    return {
        ".pdb": "chemical/x-pdb",
        ".sdf": "chemical/x-mdl-sdfile",
        ".mol": "chemical/x-mdl-molfile",
        ".mol2": "chemical/x-mol2",
        ".pdbqt": "chemical/x-pdbqt",
        ".smi": "chemical/x-daylight-smiles",
        ".smiles": "chemical/x-daylight-smiles",
    }.get(path.suffix.casefold(), "application/octet-stream")


def _read_input(value: str, position: int) -> tuple[dict[str, str], ArtifactReference, bytes]:
    artifact_type, separator, path_value = value.partition("=")
    if not separator or artifact_type not in _INPUT_TYPES or not path_value:
        raise ValueError("Inputs must use TYPE=PATH with a supported scientific input type.")
    path = Path(path_value)
    try:
        metadata = path.stat()
        if not path.is_file() or metadata.st_size > _MAXIMUM_INPUT_BYTES:
            raise ValueError
        payload = path.read_bytes()
    except (OSError, ValueError):
        raise ValueError("A scientific input file is unavailable or exceeds 16 MiB.") from None
    digest = hashlib.sha256(payload).hexdigest()
    artifact_identifier = f"scientific-input-{digest[:32]}"
    reference_identifier = f"input-{position:02d}-{artifact_type.replace('_', '-')}"
    reference = ArtifactReference(
        artifact_identifier=artifact_identifier,
        schema_version=_VERSION,
        artifact_type=artifact_type,
        media_type=_media_type(path),
        content_sha256=digest,
        byte_size=len(payload),
        provenance=CreationProvenance(
            producer="cgr-scientist-cli",
            producer_version=_VERSION,
            execution_identifier=f"scientific-input-upload-{digest[:24]}",
        ),
    )
    semantic = {
        "reference_identifier": reference_identifier,
        "artifact_type": artifact_type,
        "artifact_identifier": artifact_identifier,
    }
    return semantic, reference, payload


def _acquired_input(
    acquired: AcquiredScientificInput,
    position: int,
) -> tuple[dict[str, str], ArtifactReference, bytes]:
    digest = acquired.content_sha256
    artifact_identifier = f"scientific-input-{digest[:32]}"
    reference_identifier = (
        f"input-{position:02d}-{acquired.artifact_type.replace('_', '-')}"
    )
    reference = ArtifactReference(
        artifact_identifier=artifact_identifier,
        schema_version=_VERSION,
        artifact_type=acquired.artifact_type,
        media_type=acquired.media_type,
        content_sha256=digest,
        byte_size=len(acquired.payload),
        metadata=acquired.metadata,
        provenance=CreationProvenance(
            producer="cgr-scientist-cli",
            producer_version=_VERSION,
            execution_identifier=f"scientific-input-acquisition-{digest[:24]}",
            source=acquired.source_kind,
        ),
    )
    semantic = {
        "reference_identifier": reference_identifier,
        "artifact_type": acquired.artifact_type,
        "artifact_identifier": artifact_identifier,
    }
    return semantic, reference, acquired.payload


def _reviewed_reaction_target(arguments: argparse.Namespace) -> dict[str, object] | None:
    names = (
        "protein_chain",
        "protein_residue_sequence",
        "protein_atom_name",
        "nucleophile_formal_charge",
        "ligand_reaction_smarts",
        "total_qm_charge",
        "spin",
    )
    values = {name: getattr(arguments, name) for name in names}
    if all(value is None for value in values.values()):
        return None
    missing = tuple(name.replace("_", "-") for name, value in values.items() if value is None)
    if missing:
        raise ValueError(
            "A reviewed covalent target requires all reaction options; missing: "
            + ", ".join(missing)
        )
    return {
        "reaction_family": "covalent_substitution",
        "protein_chain_label": arguments.protein_chain,
        "protein_residue_sequence": arguments.protein_residue_sequence,
        "protein_residue_name": arguments.protein_residue_name,
        "protein_atom_name": arguments.protein_atom_name,
        "protein_nucleophile_formal_charge": arguments.nucleophile_formal_charge,
        "ligand_reaction_smarts": arguments.ligand_reaction_smarts,
        "selected_total_qm_charge": arguments.total_qm_charge,
        "selected_spin": arguments.spin,
    }


def _post(base_url: str, route: str, token: str, payload: dict[str, object]) -> dict[str, object]:
    request = Request(
        base_url.rstrip("/") + route,
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=120) as response:
            result = json.loads(response.read())
    except HTTPError as error:
        try:
            detail = json.loads(error.read())
        except (ValueError, OSError):
            detail = {"detail": {"code": "http_error", "message": "Request failed."}}
        raise RuntimeError(json.dumps(detail, sort_keys=True)) from None
    except (URLError, TimeoutError, OSError):
        raise RuntimeError("The Pulsate API is unavailable.") from None
    if not isinstance(result, dict):
        raise RuntimeError("The Pulsate API returned an invalid response.")
    return result


def scientist_main(argv: list[str] | None = None) -> int:
    """Upload exact inputs, compile the question, and execute through Pulsate."""

    parser = argparse.ArgumentParser(prog="cgr-scientist")
    parser.add_argument("question", help="A quoted molecular-science question.")
    parser.add_argument(
        "--input",
        action="append",
        default=[],
        metavar="TYPE=PATH",
        help="Exact input structure; repeat for protein and ligand inputs.",
    )
    parser.add_argument("--protein-pdb-id")
    parser.add_argument("--ligand-pubchem-cid", type=int)
    parser.add_argument("--ligand-smiles")
    parser.add_argument("--ligand-inchi")
    parser.add_argument(
        "--approve-acquired-inputs",
        action="store_true",
        help="Execute after reviewing the exact acquired/generated input provenance.",
    )
    parser.add_argument("--protein-chain")
    parser.add_argument("--protein-residue-sequence")
    parser.add_argument("--protein-residue-name")
    parser.add_argument("--protein-atom-name")
    parser.add_argument("--nucleophile-formal-charge", type=int)
    parser.add_argument("--ligand-reaction-smarts")
    parser.add_argument("--total-qm-charge", type=int)
    parser.add_argument("--spin", type=int)
    parser.add_argument(
        "--api-url",
        default=os.environ.get("PULSATE_API_URL", "http://127.0.0.1:8000"),
    )
    parser.add_argument(
        "--token-env",
        default="PULSATE_ACCESS_TOKEN",
        help="Environment variable holding the bearer token.",
    )
    parser.add_argument("--plan-only", action="store_true")
    arguments = parser.parse_args(argv)
    parsed = urlsplit(arguments.api_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        print(json.dumps({"status": "failed", "category": "api_url_invalid"}))
        return 2
    token = os.environ.get(arguments.token_env, "")
    if not token:
        print(json.dumps({"status": "failed", "category": "credential_unavailable"}))
        return 2
    try:
        labelled = grounded_identifiers(arguments.question)

        def resolve(label: str, explicit: object, grounded: object) -> object:
            if explicit is not None and grounded is not None and explicit != grounded:
                raise ValueError(f"The explicit {label} conflicts with the question.")
            return explicit if explicit is not None else grounded

        pdb_id = resolve(
            "PDB identifier",
            arguments.protein_pdb_id.upper() if arguments.protein_pdb_id else None,
            labelled.protein_pdb_id,
        )
        pubchem_cid = resolve(
            "PubChem CID", arguments.ligand_pubchem_cid, labelled.ligand_pubchem_cid
        )
        smiles = resolve("SMILES", arguments.ligand_smiles, labelled.ligand_smiles)
        inchi = resolve("InChI", arguments.ligand_inchi, labelled.ligand_inchi)
        ligand_source_count = sum(
            value is not None for value in (pubchem_cid, smiles, inchi)
        )
        if ligand_source_count > 1:
            raise ValueError("Supply exactly one ligand structure source.")

        acquired: list[AcquiredScientificInput] = []
        if pdb_id is not None:
            acquired.append(acquire_protein_pdb(str(pdb_id)))
        if pubchem_cid is not None:
            acquired.append(acquire_ligand_pubchem(int(pubchem_cid)))
        elif smiles is not None:
            acquired.append(generate_ligand_geometry(smiles=str(smiles)))
        elif inchi is not None:
            acquired.append(generate_ligand_geometry(inchi=str(inchi)))

        file_inputs = tuple(
            _read_input(value, position)
            for position, value in enumerate(arguments.input, start=1)
        )
        duplicate_types = {
            item.artifact_type
            for _, item, _ in file_inputs
        } & {item.artifact_type for item in acquired}
        if duplicate_types:
            raise ValueError(
                "A structure was supplied both as a file and an exact identifier: "
                + ", ".join(sorted(duplicate_types))
            )
        inputs = file_inputs + tuple(
            _acquired_input(item, len(file_inputs) + position)
            for position, item in enumerate(acquired, start=1)
        )
        reaction_target = _reviewed_reaction_target(arguments)
        uploaded_inputs: list[tuple[dict[str, str], ArtifactReference]] = []
        for semantic, reference, payload in inputs:
            uploaded = _post(
                arguments.api_url,
                "/api/v1/scientific/artifacts",
                token,
                {
                    "reference": reference.model_dump(mode="json"),
                    "payload_base64": base64.b64encode(payload).decode("ascii"),
                },
            )
            owned_reference = ArtifactReference.model_validate(uploaded)
            uploaded_inputs.append(
                (
                    {
                        **semantic,
                        "artifact_identifier": owned_reference.artifact_identifier,
                    },
                    owned_reference,
                )
            )
        compiled = _post(
            arguments.api_url,
            "/api/v1/scientific/objectives/compile",
            token,
            {
                "question": arguments.question,
                "input_references": [item[0] for item in uploaded_inputs],
                "artifact_references": [
                    item[1].model_dump(mode="json") for item in uploaded_inputs
                ],
                "covalent_reaction_target": reaction_target,
            },
        )
        result = compiled
        acquired_review_required = bool(acquired) and not arguments.approve_acquired_inputs
        if acquired_review_required:
            result = {
                **compiled,
                "acquired_input_execution_approval_required": True,
                "acquired_input_provenance": [item.metadata for item in acquired],
            }
        if (
            not arguments.plan_only
            and not acquired_review_required
            and compiled.get("status") == "planned"
        ):
            execution_identifier = compiled.get("execution_identifier")
            if not isinstance(execution_identifier, str):
                raise RuntimeError("The compiled execution has no valid identity.")
            result = _post(
                arguments.api_url,
                f"/api/v1/scientific/executions/{execution_identifier}/execute",
                token,
                {},
            )
        print(json.dumps(result, sort_keys=True, indent=2))
        return 0 if result.get("status") in {"planned", "succeeded"} else 1
    except (ValueError, RuntimeError) as error:
        print(
            json.dumps(
                {"status": "failed", "category": "scientific_request_failed", "detail": str(error)},
                sort_keys=True,
            )
        )
        return 1


__all__ = ["scientist_main"]


if __name__ == "__main__":
    raise SystemExit(scientist_main())
