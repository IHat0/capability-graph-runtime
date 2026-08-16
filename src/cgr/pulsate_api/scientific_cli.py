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
from .scientific_resolution import ScientificResolutionError
from .scientific_resolver import resolve_automatic_covalent_target


_VERSION = CapabilityVersion(major=1, minor=0, patch=0)
_INPUT_TYPES = {
    "protein_structure",
    "ligand_structure",
    "molecular_structure",
    "prepared_receptor",
    "prepared_ligand",
}
_MAXIMUM_INPUT_BYTES = 16 * 1024 * 1024


def _requests_covalent_target(question: str) -> bool:
    lowered = " ".join(question.casefold().split())
    return "transition state" in lowered or (
        "covalent" in lowered and "reaction" in lowered
    )


def _file_acquisition(
    item: tuple[dict[str, str], ArtifactReference, bytes],
) -> AcquiredScientificInput:
    semantic, reference, payload = item
    del semantic
    if reference.artifact_type not in {"protein_structure", "ligand_structure"}:
        raise ValueError("Automatic resolution requires a protein or ligand structure.")
    return AcquiredScientificInput(
        artifact_type=reference.artifact_type,
        media_type=reference.media_type,
        payload=payload,
        source_kind="scientist_supplied_file",
        source_identifier=reference.artifact_identifier,
    )


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


def _concise_result(result: dict[str, object]) -> dict[str, object]:
    """Keep conversational state visible without printing the canonical graph."""

    fields = (
        "status",
        "session_identifier",
        "revision",
        "scientist_summary",
        "execution_status",
        "next_questions",
        "intent_proposal",
        "accepted_task_type",
        "requirement_proposal",
        "accepted_research_requirements",
        "evidence_proposal",
        "accepted_partial_covalent_reaction_target",
        "unapproved_input_artifact_identifiers",
        "acquired_input_execution_approval_required",
        "acquired_input_provenance",
        "automatic_evidence_resolution",
        "scene_identifier",
        "scientist_result",
    )
    return {field: result[field] for field in fields if field in result}


def scientist_main(argv: list[str] | None = None) -> int:
    """Create or resume one unified, conversational research session."""

    parser = argparse.ArgumentParser(prog="cgr-scientist")
    parser.add_argument(
        "question",
        nargs="?",
        help="A quoted scientific question, or a reply when --session-id is used.",
    )
    parser.add_argument(
        "--session-id",
        help="Resume an existing research session with the supplied reply.",
    )
    parser.add_argument(
        "--accept-intent-proposal",
        action="store_true",
        help="Confirm the model-proposed registered goal for this session reply.",
    )
    parser.add_argument(
        "--accept-requirement-proposal",
        action="store_true",
        help="Confirm the model-proposed general research operations and outputs.",
    )
    parser.add_argument(
        "--accept-evidence-proposal",
        action="store_true",
        help="Confirm the session's quoted or deterministic evidence proposal.",
    )
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
    parser.add_argument(
        "--full-json",
        action="store_true",
        help="Print the complete canonical session payload for diagnostics.",
    )
    arguments = parser.parse_args(argv)
    if arguments.question is None:
        parser.error("a scientific question or session reply is required")
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
            if not isinstance(pubchem_cid, int):
                raise ValueError("The resolved PubChem CID is invalid.")
            acquired.append(acquire_ligand_pubchem(pubchem_cid))
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
        reaction_target = _reviewed_reaction_target(arguments)
        all_exact_inputs: list[AcquiredScientificInput] = [
            _file_acquisition(item) for item in file_inputs
            if item[1].artifact_type in {"protein_structure", "ligand_structure"}
        ] + acquired
        resolution_review: dict[str, object] | None = None
        if (
            _requests_covalent_target(arguments.question)
            and reaction_target is None
            and sum(item.artifact_type == "protein_structure" for item in all_exact_inputs) == 1
            and not any(item.artifact_type == "ligand_structure" for item in all_exact_inputs)
        ):
            protein = next(
                item for item in all_exact_inputs
                if item.artifact_type == "protein_structure"
            )
            try:
                resolution = resolve_automatic_covalent_target(protein)
            except (
                ImportError,
                RuntimeError,
                ScientificResolutionError,
                UnicodeError,
                ValueError,
            ) as error:
                resolution_review = {
                    "status": "unresolved",
                    "reason": str(error),
                }
            else:
                resolution_review = {
                    "status": resolution.status,
                    "confidence": resolution.confidence,
                    "reason": resolution.reason,
                }
                if resolution.status == "resolved":
                    assert resolution.protein_input is not None
                    assert resolution.ligand_input is not None
                    assert resolution.target is not None
                    file_inputs = tuple(
                        item for item in file_inputs
                        if item[1].artifact_type != "protein_structure"
                    )
                    acquired = [
                        item for item in acquired
                        if item.artifact_type != "protein_structure"
                    ]
                    acquired.extend(
                        (resolution.protein_input, resolution.ligand_input)
                    )
                    reaction_target = resolution.target.model_dump(mode="json")
                elif (
                    resolution.status == "confirmation_required"
                    and resolution.target is not None
                    and resolution.protein_input is not None
                    and resolution.ligand_input is not None
                    and arguments.accept_evidence_proposal
                    and arguments.session_id is not None
                ):
                    file_inputs = tuple(
                        item for item in file_inputs
                        if item[1].artifact_type != "protein_structure"
                    )
                    acquired = [
                        item for item in acquired
                        if item.artifact_type != "protein_structure"
                    ]
                    acquired.extend(
                        (resolution.protein_input, resolution.ligand_input)
                    )
                    reaction_target = resolution.target.model_dump(mode="json")
        inputs = file_inputs + tuple(
            _acquired_input(item, len(file_inputs) + position)
            for position, item in enumerate(acquired, start=1)
        )
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
        input_payload = [item[0] for item in uploaded_inputs]
        artifact_payload = [
            item[1].model_dump(mode="json") for item in uploaded_inputs
        ]
        if arguments.session_id:
            reply_payload: dict[str, object] = {
                "message": arguments.question,
                "accept_intent_proposal": arguments.accept_intent_proposal,
                "accept_requirement_proposal": (
                    arguments.accept_requirement_proposal
                ),
                "accept_evidence_proposal": arguments.accept_evidence_proposal,
                "accept_acquired_input_evidence": arguments.approve_acquired_inputs,
            }
            if uploaded_inputs:
                reply_payload.update(
                    {
                        "input_references": input_payload,
                        "artifact_references": artifact_payload,
                    }
                )
            if reaction_target is not None:
                reply_payload["covalent_reaction_target"] = reaction_target
            compiled = _post(
                arguments.api_url,
                f"/api/v1/research/sessions/{arguments.session_id}/reply",
                token,
                reply_payload,
            )
        else:
            if (
                arguments.accept_intent_proposal
                or arguments.accept_requirement_proposal
                or arguments.accept_evidence_proposal
            ):
                raise ValueError(
                    "Proposal acceptance requires --session-id."
                )
            compiled = _post(
                arguments.api_url,
                "/api/v1/research/sessions",
                token,
                {
                    "question": arguments.question,
                    "input_references": input_payload,
                    "artifact_references": artifact_payload,
                    "covalent_reaction_target": reaction_target,
                    "accept_acquired_input_evidence": (
                        arguments.approve_acquired_inputs
                    ),
                },
            )
        result = compiled
        acquired_review_required = (
            bool(acquired) and not arguments.approve_acquired_inputs
        )
        if acquired_review_required:
            result = {
                **compiled,
                "acquired_input_execution_approval_required": True,
                "acquired_input_provenance": [item.metadata for item in acquired],
            }
        if resolution_review is not None:
            result = {
                **result,
                "automatic_evidence_resolution": resolution_review,
            }
        if (
            not arguments.plan_only
            and not acquired_review_required
            and compiled.get("status") == "planned"
        ):
            session_identifier = compiled.get("session_identifier")
            if not isinstance(session_identifier, str):
                raise RuntimeError("The research session has no valid identity.")
            result = _post(
                arguments.api_url,
                f"/api/v1/research/sessions/{session_identifier}/execute",
                token,
                {},
            )
        output = result if arguments.full_json else _concise_result(result)
        print(json.dumps(output, sort_keys=True, indent=2))
        return 0 if result.get("status") in {
            "awaiting_clarification",
            "awaiting_approval",
            "planned",
            "running",
            "completed",
        } else 1
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
