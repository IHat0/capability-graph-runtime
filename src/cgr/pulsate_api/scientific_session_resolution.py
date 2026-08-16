"""Evidence bridge from persisted structures into resumable research sessions."""

from __future__ import annotations

import hashlib
import json
from typing import Protocol

from cgr.kernel.contracts import CapabilityVersion
from cgr.science import ArtifactReference, CreationProvenance

from .scientific_acquisition import (
    AcquiredScientificInput,
    TrustedEntityCandidate,
    acquire_ligand_pubchem,
    acquire_ligand_pubchem_name,
    acquire_protein_pdb,
    generate_ligand_geometry,
    grounded_identifiers,
    resolve_ligand_pubchem_name_candidates,
    resolve_protein_uniprot_name_candidates,
)
from .scientific_conversation import (
    ScientificEntityResolutionCandidate,
    ScientificEvidenceProposal,
    ScientificEvidenceQuote,
    ScientificNamedInputCandidate,
)
from .scientific_objectives import (
    CovalentReactionTarget,
    ScientificInputReference,
)
from .scientific_resolution import ScientificResolutionError
from .scientific_resolver import resolve_automatic_covalent_target

_VERSION = CapabilityVersion(major=1, minor=0, patch=0)


class ScientificPayloadReaderWriter(Protocol):
    """Minimal immutable payload-store surface used by the resolver."""

    def read(self, reference: ArtifactReference) -> bytes: ...

    def write(self, reference: ArtifactReference, payload: bytes) -> None: ...


class ScientificSessionEvidenceResolver(Protocol):
    """Deterministically propose evidence from persisted session artifacts."""

    def capability(self) -> dict[str, object]: ...

    def propose_covalent_evidence(
        self,
        *,
        input_references: tuple[ScientificInputReference, ...],
        artifact_references: tuple[ArtifactReference, ...],
        covalent_reaction_target: CovalentReactionTarget | None,
        tenant_identifier_sha256: str,
    ) -> ScientificEvidenceProposal | None: ...

    def acquire_exact_identifiers(
        self,
        *,
        scientist_text: str,
        input_references: tuple[ScientificInputReference, ...],
        artifact_references: tuple[ArtifactReference, ...],
        tenant_identifier_sha256: str,
    ) -> ScientificEvidenceProposal | None: ...

    def acquire_named_ligand(
        self,
        *,
        candidate: ScientificNamedInputCandidate,
        input_references: tuple[ScientificInputReference, ...],
        tenant_identifier_sha256: str,
    ) -> ScientificEvidenceProposal | None: ...

    def acquire_named_entity(
        self,
        *,
        candidate: ScientificNamedInputCandidate,
        input_references: tuple[ScientificInputReference, ...],
        tenant_identifier_sha256: str,
    ) -> ScientificEvidenceProposal | None: ...

    def acquire_selected_entity_candidate(
        self,
        *,
        candidate: ScientificEntityResolutionCandidate,
        input_references: tuple[ScientificInputReference, ...],
        tenant_identifier_sha256: str,
    ) -> ScientificEvidenceProposal | None: ...

    def unresolved_reason(
        self,
        *,
        input_references: tuple[ScientificInputReference, ...],
        artifact_references: tuple[ArtifactReference, ...],
        covalent_reaction_target: CovalentReactionTarget | None,
    ) -> str | None: ...


class PersistedStructureEvidenceResolver:
    """Reuse the audited structural resolver and persist its exact outputs."""

    def __init__(self, payload_store: ScientificPayloadReaderWriter | None) -> None:
        self.payload_store = payload_store

    def capability(self) -> dict[str, object]:
        return {
            "available": self.payload_store is not None,
            "resolver_kind": "pdb_declared_evidence",
            "requires_scientist_confirmation": "only_when_ambiguous_or_derived",
            "auto_accepts_unique_high_confidence_acquisitions": True,
            "supports_exact_named_ligand_resolution": True,
            "supports_trusted_entity_candidates": True,
            "trusted_sources": ("pubchem", "uniprot", "rcsb_pdb"),
        }

    def unresolved_reason(
        self,
        *,
        input_references: tuple[ScientificInputReference, ...],
        artifact_references: tuple[ArtifactReference, ...],
        covalent_reaction_target: CovalentReactionTarget | None,
    ) -> str | None:
        """Explain why automatic structural resolution could not proceed."""

        if self.payload_store is None or covalent_reaction_target is not None:
            return None
        protein_inputs = tuple(
            item
            for item in input_references
            if item.artifact_type == "protein_structure"
        )
        if len(protein_inputs) != 1 or any(
            item.artifact_type == "ligand_structure"
            for item in input_references
        ):
            return None
        source_reference = next(
            (
                item
                for item in artifact_references
                if item.artifact_identifier
                == protein_inputs[0].artifact_identifier
            ),
            None,
        )
        if source_reference is None or source_reference.media_type != "chemical/x-pdb":
            return None
        try:
            acquired = self._acquired_protein(source_reference)
            resolution = resolve_automatic_covalent_target(acquired)
        except (
            ImportError,
            RuntimeError,
            ScientificResolutionError,
            UnicodeError,
            ValueError,
        ):
            return "automatic_structure_evidence_could_not_be_resolved"
        return resolution.reason or (
            None if resolution.status in {"resolved", "confirmation_required"}
            else "automatic_structure_evidence_could_not_be_resolved"
        )

    def acquire_exact_identifiers(
        self,
        *,
        scientist_text: str,
        input_references: tuple[ScientificInputReference, ...],
        artifact_references: tuple[ArtifactReference, ...],
        tenant_identifier_sha256: str,
    ) -> ScientificEvidenceProposal | None:
        """Acquire only source identities explicitly labelled by the scientist."""

        del artifact_references
        if self.payload_store is None:
            return None
        try:
            grounded = grounded_identifiers(scientist_text)
        except ValueError:
            return None
        existing_types = {item.artifact_type for item in input_references}
        acquired: list[AcquiredScientificInput] = []
        try:
            if (
                grounded.protein_pdb_id is not None
                and "protein_structure" not in existing_types
            ):
                acquired.append(acquire_protein_pdb(grounded.protein_pdb_id))
            if "ligand_structure" not in existing_types:
                if grounded.ligand_pubchem_cid is not None:
                    acquired.append(
                        acquire_ligand_pubchem(grounded.ligand_pubchem_cid)
                    )
                elif grounded.ligand_smiles is not None:
                    acquired.append(
                        generate_ligand_geometry(smiles=grounded.ligand_smiles)
                    )
                elif grounded.ligand_inchi is not None:
                    acquired.append(
                        generate_ligand_geometry(inchi=grounded.ligand_inchi)
                    )
        except (ImportError, RuntimeError, UnicodeError, ValueError):
            return None
        if not acquired:
            return None
        references: list[ArtifactReference] = []
        inputs: list[ScientificInputReference] = []
        for item in acquired:
            reference = self._persist(
                item,
                source_reference=None,
                tenant_identifier_sha256=tenant_identifier_sha256,
                resolution_status="exact_identifier_acquired",
                resolution_confidence="high",
            )
            references.append(reference)
            inputs.append(
                ScientificInputReference(
                    reference_identifier=(
                        "input-acquired-"
                        + item.artifact_type.replace("_structure", "")
                        + "-"
                        + item.content_sha256[:16]
                    ),
                    artifact_type=item.artifact_type,
                    artifact_identifier=reference.artifact_identifier,
                )
            )
        identity = json.dumps(
            [item.model_dump(mode="json") for item in inputs],
            sort_keys=True,
            separators=(",", ":"),
        )
        return ScientificEvidenceProposal(
            proposal_identifier=(
                "evidence-proposal-"
                + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
            ),
            source_kind="exact_identifier_acquisition",
            summary=(
                "Pulsate acquired exact structure evidence from the source "
                "identifier explicitly supplied in your reply. Review and confirm "
                "the acquired input before it is used."
            ),
            input_references=tuple(inputs),
            artifact_references=tuple(references),
        )

    def acquire_named_ligand(
        self,
        *,
        candidate: ScientificNamedInputCandidate,
        input_references: tuple[ScientificInputReference, ...],
        tenant_identifier_sha256: str,
    ) -> ScientificEvidenceProposal | None:
        """Resolve one literal name only when it maps to one exact public CID."""

        if self.payload_store is None or any(
            item.artifact_type == "ligand_structure"
            for item in input_references
        ):
            return None
        try:
            acquired = acquire_ligand_pubchem_name(candidate.name)
        except (ImportError, RuntimeError, UnicodeError, ValueError):
            return None
        reference = self._persist(
            acquired,
            source_reference=None,
            tenant_identifier_sha256=tenant_identifier_sha256,
            resolution_status="named_identifier_acquired",
            resolution_confidence="high",
        )
        semantic = ScientificInputReference(
            reference_identifier=(
                "input-acquired-ligand-" + acquired.content_sha256[:16]
            ),
            artifact_type="ligand_structure",
            artifact_identifier=reference.artifact_identifier,
        )
        identity = json.dumps(
            {
                "input": semantic.model_dump(mode="json"),
                "turn_identifier": candidate.turn_identifier,
                "quote": candidate.supporting_quote,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return ScientificEvidenceProposal(
            proposal_identifier=(
                "evidence-proposal-"
                + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
            ),
            source_kind="named_identifier_acquisition",
            summary=(
                "Pulsate resolved the literal name in your reply to one exact "
                "public source identifier. Review the quoted name and acquired "
                "input before it is used."
            ),
            input_references=(semantic,),
            artifact_references=(reference,),
            supporting_quotes=(
                ScientificEvidenceQuote(
                    field_name="ligand_name",
                    turn_identifier=candidate.turn_identifier,
                    supporting_quote=candidate.supporting_quote,
                ),
            ),
            provider_kind=candidate.provider_kind,
            model_name=candidate.model_name,
        )

    def acquire_named_entity(
        self,
        *,
        candidate: ScientificNamedInputCandidate,
        input_references: tuple[ScientificInputReference, ...],
        tenant_identifier_sha256: str,
    ) -> ScientificEvidenceProposal | None:
        """Acquire one unique trusted match or return bounded review candidates."""

        if self.payload_store is None or any(
            item.artifact_type == f"{candidate.entity_type}_structure"
            for item in input_references
        ):
            return None
        try:
            matches: tuple[TrustedEntityCandidate, ...]
            if candidate.entity_type == "protein":
                matches = resolve_protein_uniprot_name_candidates(candidate.name)
            else:
                matches = resolve_ligand_pubchem_name_candidates(candidate.name)
        except (RuntimeError, UnicodeError, ValueError):
            return None
        if len(matches) == 1:
            try:
                if candidate.entity_type == "protein":
                    structure_identifier = matches[0].structure_identifier
                    if structure_identifier is None:
                        return None
                    source = acquire_protein_pdb(structure_identifier)
                    acquired = AcquiredScientificInput(
                        artifact_type=source.artifact_type,
                        media_type=source.media_type,
                        payload=source.payload,
                        source_kind="uniprot_pdb_cross_reference",
                        source_identifier=(
                            matches[0].source_identifier
                            + ":"
                            + structure_identifier
                        ),
                        source_url=source.source_url,
                    )
                else:
                    acquired = acquire_ligand_pubchem(
                        int(matches[0].source_identifier)
                    )
            except (ImportError, RuntimeError, UnicodeError, ValueError):
                return None
            reference = self._persist(
                acquired,
                source_reference=None,
                tenant_identifier_sha256=tenant_identifier_sha256,
                resolution_status="trusted_named_entity_acquired",
                resolution_confidence="high",
            )
            semantic = ScientificInputReference(
                reference_identifier=(
                    f"input-acquired-{candidate.entity_type}-"
                    + acquired.content_sha256[:16]
                ),
                artifact_type=acquired.artifact_type,
                artifact_identifier=reference.artifact_identifier,
            )
            return ScientificEvidenceProposal(
                proposal_identifier=(
                    "evidence-proposal-"
                    + hashlib.sha256(
                        (
                            semantic.reference_identifier
                            + candidate.turn_identifier
                        ).encode("utf-8")
                    ).hexdigest()[:32]
                ),
                source_kind="named_identifier_acquisition",
                summary=(
                    "Pulsate resolved the literal entity name to one trusted-source "
                    "structure. Review the identity, provenance, and confidence."
                ),
                input_references=(semantic,),
                artifact_references=(reference,),
                supporting_quotes=(
                    ScientificEvidenceQuote(
                        field_name=(
                            "protein_name"
                            if candidate.entity_type == "protein"
                            else "ligand_name"
                        ),
                        turn_identifier=candidate.turn_identifier,
                        supporting_quote=candidate.supporting_quote,
                    ),
                ),
                provider_kind=candidate.provider_kind,
                model_name=candidate.model_name,
            )
        if len(matches) > 1:
            identity = json.dumps(
                [
                    {
                        "entity_type": item.entity_type,
                        "source_kind": item.source_kind,
                        "source_identifier": item.source_identifier,
                        "display_label": item.display_label,
                        "structure_identifier": item.structure_identifier,
                        "confidence": item.confidence,
                    }
                    for item in matches
                ],
                sort_keys=True,
                separators=(",", ":"),
            )
            return ScientificEvidenceProposal(
                proposal_identifier=(
                    "evidence-proposal-"
                    + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
                ),
                source_kind="entity_resolution_candidates",
                summary=(
                    "The literal entity name has multiple meaningful trusted-source "
                    "matches. Choose one exact source or provide a more specific name."
                ),
                entity_candidates=tuple(
                    ScientificEntityResolutionCandidate(
                        entity_type=item.entity_type,
                        source_kind=item.source_kind,
                        source_identifier=item.source_identifier,
                        display_label=item.display_label,
                        structure_identifier=item.structure_identifier,
                        confidence=item.confidence,
                    )
                    for item in matches
                ),
                supporting_quotes=(
                    ScientificEvidenceQuote(
                        field_name=(
                            "protein_name"
                            if candidate.entity_type == "protein"
                            else "ligand_name"
                        ),
                        turn_identifier=candidate.turn_identifier,
                        supporting_quote=candidate.supporting_quote,
                    ),
                ),
                provider_kind=candidate.provider_kind,
                model_name=candidate.model_name,
            )
        return None

    def acquire_selected_entity_candidate(
        self,
        *,
        candidate: ScientificEntityResolutionCandidate,
        input_references: tuple[ScientificInputReference, ...],
        tenant_identifier_sha256: str,
    ) -> ScientificEvidenceProposal | None:
        """Acquire exactly one scientist-selected member of a prior candidate set."""

        artifact_type = f"{candidate.entity_type}_structure"
        if self.payload_store is None or any(
            item.artifact_type == artifact_type for item in input_references
        ):
            return None
        try:
            if candidate.entity_type == "protein":
                if (
                    candidate.source_kind != "uniprot"
                    or candidate.structure_identifier is None
                ):
                    return None
                source = acquire_protein_pdb(candidate.structure_identifier)
                acquired = AcquiredScientificInput(
                    artifact_type=source.artifact_type,
                    media_type=source.media_type,
                    payload=source.payload,
                    source_kind="uniprot_pdb_cross_reference",
                    source_identifier=(
                        candidate.source_identifier
                        + ":"
                        + candidate.structure_identifier
                    ),
                    source_url=source.source_url,
                )
            else:
                if candidate.source_kind != "pubchem":
                    return None
                acquired = acquire_ligand_pubchem(
                    int(candidate.source_identifier)
                )
        except (ImportError, RuntimeError, UnicodeError, ValueError):
            return None
        reference = self._persist(
            acquired,
            source_reference=None,
            tenant_identifier_sha256=tenant_identifier_sha256,
            resolution_status="scientist_selected_trusted_entity_acquired",
            resolution_confidence="high",
        )
        semantic = ScientificInputReference(
            reference_identifier=(
                f"input-selected-{candidate.entity_type}-"
                + acquired.content_sha256[:16]
            ),
            artifact_type=acquired.artifact_type,
            artifact_identifier=reference.artifact_identifier,
        )
        identity = json.dumps(
            {
                "candidate": candidate.model_dump(mode="json"),
                "input": semantic.model_dump(mode="json"),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return ScientificEvidenceProposal(
            proposal_identifier=(
                "evidence-proposal-"
                + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
            ),
            source_kind="exact_identifier_acquisition",
            summary=(
                "Pulsate acquired the exact trusted-source candidate selected "
                "from the previous match list. Review its persisted provenance "
                "before use."
            ),
            input_references=(semantic,),
            artifact_references=(reference,),
        )

    def propose_covalent_evidence(
        self,
        *,
        input_references: tuple[ScientificInputReference, ...],
        artifact_references: tuple[ArtifactReference, ...],
        covalent_reaction_target: CovalentReactionTarget | None,
        tenant_identifier_sha256: str,
    ) -> ScientificEvidenceProposal | None:
        if self.payload_store is None or covalent_reaction_target is not None:
            return None
        protein_inputs = tuple(
            item
            for item in input_references
            if item.artifact_type == "protein_structure"
        )
        ligand_inputs = tuple(
            item
            for item in input_references
            if item.artifact_type == "ligand_structure"
        )
        if len(protein_inputs) != 1 or ligand_inputs:
            return None
        source_reference = next(
            (
                item
                for item in artifact_references
                if item.artifact_identifier
                == protein_inputs[0].artifact_identifier
            ),
            None,
        )
        if source_reference is None or source_reference.media_type != "chemical/x-pdb":
            return None
        try:
            acquired = self._acquired_protein(source_reference)
            resolution = resolve_automatic_covalent_target(acquired)
        except (
            ImportError,
            RuntimeError,
            ScientificResolutionError,
            UnicodeError,
            ValueError,
        ):
            return None
        if (
            resolution.status not in {"resolved", "confirmation_required"}
            or resolution.target is None
            or resolution.protein_input is None
            or resolution.ligand_input is None
        ):
            return None

        proposed_inputs: list[ScientificInputReference] = []
        proposed_artifacts: list[ArtifactReference] = []
        for acquired_input in (
            resolution.protein_input,
            resolution.ligand_input,
        ):
            reference = self._persist(
                acquired_input,
                source_reference=source_reference,
                tenant_identifier_sha256=tenant_identifier_sha256,
                resolution_status=resolution.status,
                resolution_confidence=resolution.confidence,
            )
            proposed_artifacts.append(reference)
            proposed_inputs.append(
                ScientificInputReference(
                    reference_identifier=(
                        "input-resolved-"
                        + acquired_input.artifact_type.replace("_structure", "")
                        + "-"
                        + acquired_input.content_sha256[:16]
                    ),
                    artifact_type=acquired_input.artifact_type,
                    artifact_identifier=reference.artifact_identifier,
                )
            )
        identity = json.dumps(
            {
                "inputs": [item.model_dump(mode="json") for item in proposed_inputs],
                "target": resolution.target.model_dump(mode="json"),
                "reason": resolution.reason,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return ScientificEvidenceProposal(
            proposal_identifier=(
                "evidence-proposal-"
                + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
            ),
            source_kind="deterministic_structure_resolution",
            summary=(
                "Pulsate resolved a protein-only structure, its exact declared "
                "partner, and a complete reaction target from persisted source "
                "evidence. Review and confirm these deterministic results."
            ),
            input_references=tuple(proposed_inputs),
            artifact_references=tuple(proposed_artifacts),
            covalent_reaction_target=resolution.target,
            reason=resolution.reason,
        )

    def _persist(
        self,
        acquired: AcquiredScientificInput,
        *,
        source_reference: ArtifactReference | None,
        tenant_identifier_sha256: str,
        resolution_status: str,
        resolution_confidence: str | None,
    ) -> ArtifactReference:
        assert self.payload_store is not None
        digest = acquired.content_sha256
        metadata: dict[str, str] = {
            **acquired.metadata,
            "tenant_identifier_sha256": tenant_identifier_sha256,
            "evidence_resolution_status": resolution_status,
        }
        if resolution_confidence is not None:
            metadata["evidence_resolution_confidence"] = resolution_confidence
        artifact_identity = json.dumps(
            {
                "artifact_type": acquired.artifact_type,
                "content_sha256": digest,
                "metadata": metadata,
                "parent": (
                    source_reference.pointer.model_dump(mode="json")
                    if source_reference is not None
                    else None
                ),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        reference = ArtifactReference(
            artifact_identifier=(
                "scientific-input-"
                + tenant_identifier_sha256[:16]
                + "-"
                + hashlib.sha256(
                    artifact_identity.encode("utf-8")
                ).hexdigest()[:32]
            ),
            schema_version=_VERSION,
            artifact_type=acquired.artifact_type,
            media_type=acquired.media_type,
            content_sha256=digest,
            byte_size=len(acquired.payload),
            metadata=metadata,
            provenance=CreationProvenance(
                producer="pulsate-scientific-evidence-resolver",
                producer_version=_VERSION,
                execution_identifier=(
                    "scientific-evidence-resolution-" + digest[:24]
                ),
                source=acquired.source_kind,
            ),
            parents=(
                (source_reference.pointer,)
                if source_reference is not None
                else ()
            ),
        )
        self.payload_store.write(reference, acquired.payload)
        return reference

    def _acquired_protein(
        self,
        source_reference: ArtifactReference,
    ) -> AcquiredScientificInput:
        assert self.payload_store is not None
        metadata = source_reference.metadata
        source_identifier = metadata.get("acquisition_source_identifier")
        source_kind = metadata.get("acquisition_source_kind")
        source_url = metadata.get("acquisition_source_url")
        generation_method = metadata.get("acquisition_generation_method")
        return AcquiredScientificInput(
            artifact_type="protein_structure",
            media_type=source_reference.media_type,
            payload=self.payload_store.read(source_reference),
            source_kind=(
                source_kind
                if isinstance(source_kind, str)
                else "scientist_supplied_file"
            ),
            source_identifier=(
                source_identifier
                if isinstance(source_identifier, str)
                else source_reference.artifact_identifier
            ),
            source_url=source_url if isinstance(source_url, str) else None,
            generation_method=(
                generation_method
                if isinstance(generation_method, str)
                else None
            ),
        )


__all__ = [
    "PersistedStructureEvidenceResolver",
    "ScientificSessionEvidenceResolver",
]
