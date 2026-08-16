"""Provider-neutral, non-authoritative scientist conversation helpers."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Literal, Protocol, Self, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from cgr.science import ArtifactReference
from cgr.science.canonical import validate_identifier

from .natural_language import NaturalLanguageModelProvider
from .scientific_objectives import (
    CovalentReactionTarget,
    ScientificInputReference,
    ScientificTask,
)


class ClarificationPrompt(BaseModel):
    """One question tied to a deterministic unresolved requirement."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    requirement_identifier: str
    question: str = Field(min_length=1, max_length=1024)

    @field_validator("requirement_identifier")
    @classmethod
    def identifier(cls, value: str) -> str:
        return validate_identifier(value, label="clarification requirement")

    @field_validator("question")
    @classmethod
    def normalized_question(cls, value: str) -> str:
        value = " ".join(value.strip().split())
        if not value:
            raise ValueError("Clarification questions cannot be blank.")
        return value


class ScientificIntentProposal(BaseModel):
    """A non-executable model proposal grounded in an exact scientist quote."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_type: ScientificTask
    supporting_quote: str = Field(min_length=1, max_length=1024)
    provider_kind: str
    model_name: str = Field(min_length=1, max_length=512)

    @field_validator("provider_kind")
    @classmethod
    def provider_identifier(cls, value: str) -> str:
        return validate_identifier(value, label="language model provider")


ScientificEvidenceField = Literal[
    "protein_name",
    "ligand_name",
    "protein_chain_label",
    "protein_residue_sequence",
    "protein_residue_name",
    "protein_atom_name",
    "protein_nucleophile_formal_charge",
    "ligand_reaction_smarts",
    "selected_total_qm_charge",
    "selected_spin",
]


class PartialCovalentReactionTarget(BaseModel):
    """Scientist-quoted target fields that are not yet a complete target."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    reaction_family: Literal["covalent_substitution"] = "covalent_substitution"
    protein_chain_label: str | None = None
    protein_residue_sequence: str | None = None
    protein_residue_name: str | None = None
    protein_atom_name: str | None = None
    protein_nucleophile_formal_charge: int | None = Field(
        default=None, ge=-8, le=8
    )
    ligand_reaction_smarts: str | None = Field(
        default=None, min_length=1, max_length=2048
    )
    selected_total_qm_charge: int | None = Field(default=None, ge=-32, le=32)
    selected_spin: int | None = Field(default=None, ge=0, le=12)

    @field_validator(
        "protein_chain_label",
        "protein_residue_sequence",
        "protein_residue_name",
        "protein_atom_name",
    )
    @classmethod
    def bounded_identity(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized or len(normalized) > 32:
            raise ValueError("Reaction atom identities must be bounded nonblank text.")
        return normalized

    @field_validator("ligand_reaction_smarts")
    @classmethod
    def mapped_reaction_smarts(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        maps = tuple(int(item) for item in re.findall(r":(\d+)\]", normalized))
        if sorted(maps) != [1, 2]:
            raise ValueError(
                "Ligand reaction SMARTS must contain exactly atom maps :1 and :2."
            )
        return normalized

    @model_validator(mode="after")
    def nonempty(self) -> Self:
        if not self.supplied_fields:
            raise ValueError("Partial reaction evidence cannot be empty.")
        return self

    @property
    def supplied_fields(self) -> tuple[ScientificEvidenceField, ...]:
        values = self.model_dump(exclude={"reaction_family"})
        return tuple(
            field_name
            for field_name, value in values.items()
            if value is not None
        )  # type: ignore[return-value]

    @property
    def missing_required_fields(self) -> tuple[ScientificEvidenceField, ...]:
        required: tuple[ScientificEvidenceField, ...] = (
            "protein_chain_label",
            "protein_residue_sequence",
            "protein_atom_name",
            "protein_nucleophile_formal_charge",
            "ligand_reaction_smarts",
            "selected_total_qm_charge",
            "selected_spin",
        )
        return tuple(
            field_name
            for field_name in required
            if getattr(self, field_name) is None
        )

    def complete_target(self) -> CovalentReactionTarget | None:
        if self.missing_required_fields:
            return None
        return CovalentReactionTarget.model_validate(
            self.model_dump(exclude_none=True)
        )


class ScientistEvidenceTurn(BaseModel):
    """One scientist turn that an evidence proposal may quote exactly."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    turn_identifier: str
    content: str = Field(min_length=1, max_length=8192)

    @field_validator("turn_identifier")
    @classmethod
    def identifier(cls, value: str) -> str:
        return validate_identifier(value, label="scientist evidence turn")


class ScientificEvidenceQuote(BaseModel):
    """Literal scientist evidence for one proposed structured field."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    field_name: ScientificEvidenceField
    turn_identifier: str
    supporting_quote: str = Field(min_length=1, max_length=2048)

    @field_validator("turn_identifier")
    @classmethod
    def identifier(cls, value: str) -> str:
        return validate_identifier(value, label="scientific evidence turn")


class ScientificNamedInputCandidate(BaseModel):
    """One literal scientist-supplied name proposed for exact source lookup."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=256)
    turn_identifier: str
    supporting_quote: str = Field(min_length=1, max_length=2048)
    provider_kind: str
    model_name: str = Field(min_length=1, max_length=512)
    entity_type: Literal["protein", "ligand"] = "ligand"

    @field_validator("turn_identifier", "provider_kind")
    @classmethod
    def identifiers(cls, value: str) -> str:
        return validate_identifier(value, label="named input evidence")

    @field_validator("name", "supporting_quote")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return " ".join(value.strip().split())


ScientificEvidenceSource = Literal[
    "conversation_extraction",
    "deterministic_structure_resolution",
    "exact_identifier_acquisition",
    "named_identifier_acquisition",
    "entity_resolution_candidates",
]


class ScientificEntityResolutionCandidate(BaseModel):
    """One trusted-source match that requires an explicit scientist choice."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    entity_type: Literal["protein", "ligand"]
    source_kind: Literal["uniprot", "pubchem"]
    source_identifier: str
    display_label: str = Field(min_length=1, max_length=512)
    structure_identifier: str | None = None
    confidence: Literal["high", "ambiguous"]

    @field_validator("source_identifier", "structure_identifier")
    @classmethod
    def bounded_source_identity(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized or len(normalized) > 128:
            raise ValueError("Trusted-source identities must be bounded.")
        return normalized


class ScientificEvidenceProposal(BaseModel):
    """Reviewable evidence that cannot affect execution until it is accepted."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    proposal_identifier: str
    source_kind: ScientificEvidenceSource
    summary: str = Field(min_length=1, max_length=1024)
    input_references: tuple[ScientificInputReference, ...] = Field(
        default=(), max_length=64
    )
    artifact_references: tuple[ArtifactReference, ...] = Field(
        default=(), max_length=64
    )
    covalent_reaction_target: CovalentReactionTarget | None = None
    partial_covalent_reaction_target: PartialCovalentReactionTarget | None = None
    supporting_quotes: tuple[ScientificEvidenceQuote, ...] = Field(
        default=(), max_length=16
    )
    provider_kind: str | None = None
    model_name: str | None = Field(default=None, max_length=512)
    reason: str | None = Field(default=None, max_length=512)
    entity_candidates: tuple[ScientificEntityResolutionCandidate, ...] = Field(
        default=(), max_length=8
    )

    @field_validator("proposal_identifier", "provider_kind")
    @classmethod
    def identifiers(cls, value: str | None) -> str | None:
        return (
            validate_identifier(value, label="scientific evidence proposal")
            if value is not None
            else None
        )

    @field_validator("summary")
    @classmethod
    def normalized_summary(cls, value: str) -> str:
        return " ".join(value.strip().split())

    @model_validator(mode="after")
    def complete(self) -> Self:
        declared = {
            item.artifact_identifier for item in self.input_references
        }
        persisted = {
            item.artifact_identifier for item in self.artifact_references
        }
        if declared != persisted:
            raise ValueError(
                "Proposed semantic inputs must bind exact proposed artifacts."
            )
        if (
            not self.input_references
            and self.covalent_reaction_target is None
            and self.partial_covalent_reaction_target is None
            and not self.entity_candidates
        ):
            raise ValueError("An evidence proposal cannot be empty.")
        if self.source_kind == "conversation_extraction":
            if (
                self.covalent_reaction_target is None
                and self.partial_covalent_reaction_target is None
            ) or (
                not self.supporting_quotes
                or self.provider_kind is None
                or self.model_name is None
            ):
                raise ValueError(
                    "Conversation evidence requires a quoted model proposal."
                )
        elif self.source_kind == "named_identifier_acquisition":
            if (
                not self.input_references
                or not self.supporting_quotes
                or self.provider_kind is None
                or self.model_name is None
            ):
                raise ValueError(
                    "Named acquisition requires quoted model evidence and an input."
                )
        elif self.source_kind == "entity_resolution_candidates":
            if (
                not self.entity_candidates
                or not self.supporting_quotes
                or self.provider_kind is None
                or self.model_name is None
            ):
                raise ValueError(
                    "Ambiguous entity resolution requires candidates and quoted evidence."
                )
        elif self.supporting_quotes or self.provider_kind is not None:
            raise ValueError(
                "Deterministic evidence cannot claim language-model provenance."
            )
        return self


@runtime_checkable
class ScientificIntentInterpreter(Protocol):
    """Replaceable model boundary for non-executable goal classification."""

    def capability(self) -> dict[str, object]: ...

    def propose(self, question: str) -> ScientificIntentProposal | None: ...


@runtime_checkable
class ScientificEvidenceInterpreter(Protocol):
    """Replaceable model boundary for quoted, non-authoritative evidence."""

    def capability(self) -> dict[str, object]: ...

    def propose_covalent_target(
        self,
        turns: tuple[ScientistEvidenceTurn, ...],
    ) -> ScientificEvidenceProposal | None: ...

    def propose_named_ligand(
        self,
        turns: tuple[ScientistEvidenceTurn, ...],
    ) -> ScientificNamedInputCandidate | None: ...

    def propose_named_protein(
        self,
        turns: tuple[ScientistEvidenceTurn, ...],
    ) -> ScientificNamedInputCandidate | None: ...


@runtime_checkable
class ScientificQuestionWriter(Protocol):
    """Replaceable language boundary that may phrase, but never answer, questions."""

    def capability(self) -> dict[str, object]: ...

    def write(
        self,
        requirements: tuple[tuple[str, str], ...],
    ) -> tuple[ClarificationPrompt, ...]: ...


_EXACT_QUESTIONS = {
    "Exactly one protein structure must be resolved.": (
        "Which exact protein structure should this research session use? You can "
        "provide a structure artifact or an exact source identifier."
    ),
    "Exactly one ligand structure must be resolved.": (
        "Which exact ligand structure should this research session use? You can "
        "provide a structure artifact or an exact source identifier."
    ),
    "Bond scan point count and distance endpoints are required.": (
        "What point count, starting distance, and ending distance do you want for "
        "the scan?"
    ),
    "A solvent identity is required for solvated comparison.": (
        "Which solvent should be used for the comparison?"
    ),
    (
        "The protein reaction atom, mapped ligand reaction SMARTS, charge, and "
        "spin must be explicitly reviewed."
    ): (
        "Please provide or confirm the reaction-site identity, mapped reaction "
        "pattern, total charge, and spin for this session."
    ),
}


class DeterministicScientificQuestionWriter:
    """Always-available writer that does not call a language model."""

    def capability(self) -> dict[str, object]:
        return {
            "available": True,
            "provider_kind": "deterministic_fallback",
            "model_name": None,
        }

    def write(
        self,
        requirements: tuple[tuple[str, str], ...],
    ) -> tuple[ClarificationPrompt, ...]:
        return tuple(
            ClarificationPrompt(
                requirement_identifier=identifier,
                question=_EXACT_QUESTIONS.get(
                    reason,
                    "I still need clarification about this part of your request: "
                    + reason,
                ),
            )
            for identifier, reason in requirements
        )


class ProviderNeutralScientificQuestionWriter:
    """Use any configured model provider with a deterministic safe fallback.

    The model receives only unresolved requirement text and may only return a
    question for each exact requirement identity.  Its response never updates
    objective facts, parameters, approvals, artifacts, or execution state.
    """

    def __init__(
        self,
        provider: NaturalLanguageModelProvider | None,
        *,
        fallback: ScientificQuestionWriter | None = None,
    ) -> None:
        self.provider = provider
        self.fallback = fallback or DeterministicScientificQuestionWriter()

    def capability(self) -> dict[str, object]:
        if self.provider is None:
            return self.fallback.capability()
        return {
            "available": True,
            "provider_kind": self.provider.provider_kind,
            "model_name": self.provider.model_name,
            "fallback_available": True,
        }

    def write(
        self,
        requirements: tuple[tuple[str, str], ...],
    ) -> tuple[ClarificationPrompt, ...]:
        if not requirements or self.provider is None:
            return self.fallback.write(requirements)
        payload = {
            "requirements": [
                {"requirement_identifier": identifier, "reason": reason}
                for identifier, reason in requirements
            ]
        }
        try:
            content = self.provider.complete(
                [
                    {
                        "role": "system",
                        "content": (
                            "Return one JSON object with a questions array. For every "
                            "supplied requirement, return its exact "
                            "requirement_identifier and one concise scientist-facing "
                            "question. Ask only; do not "
                            "answer, suggest a value, infer a fact, add a requirement, "
                            "authorize work, or include commentary."
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(payload, separators=(",", ":")),
                    },
                ]
            )
            parsed = json.loads(content)
            raw_questions = parsed["questions"]
            questions = tuple(
                ClarificationPrompt.model_validate(item) for item in raw_questions
            )
            expected = tuple(identifier for identifier, _reason in requirements)
            observed = tuple(item.requirement_identifier for item in questions)
            if len(observed) != len(set(observed)) or set(observed) != set(expected):
                raise ValueError("The model changed clarification identities.")
            by_identifier = {
                item.requirement_identifier: item for item in questions
            }
            return tuple(by_identifier[item] for item in expected)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, RuntimeError):
            return self.fallback.write(requirements)


class ProviderNeutralScientificIntentInterpreter:
    """Propose one registered task using exact literal textual evidence."""

    def __init__(self, provider: NaturalLanguageModelProvider | None) -> None:
        self.provider = provider

    def capability(self) -> dict[str, object]:
        return {
            "available": self.provider is not None,
            "provider_kind": (
                self.provider.provider_kind if self.provider is not None else None
            ),
            "model_name": (
                self.provider.model_name if self.provider is not None else None
            ),
            "requires_scientist_confirmation": False,
            "auto_acceptance_policy": (
                "literal_quote_and_successful_deterministic_compilation"
            ),
        }

    def propose(self, question: str) -> ScientificIntentProposal | None:
        if self.provider is None:
            return None
        try:
            content = self.provider.complete(
                [
                    {
                        "role": "system",
                        "content": (
                            "Classify the scientist's research goal only when it maps "
                            "to exactly one registered task. Return exactly one JSON "
                            "object with task_type and supporting_quote. task_type "
                            "must "
                            "be one of covalent_transition_state, "
                            "metal_active_site_quantum, bond_dissociation_scan, "
                            "solvated_conformer_comparison, or "
                            "protein_ligand_discovery. supporting_quote must be one "
                            "literal, non-empty substring copied from the scientist's "
                            "question. Do not extract or suggest values, structures, "
                            "parameters, methods, assumptions, approvals, or answers. "
                            "If the goal is not uniquely classifiable, return {}."
                        ),
                    },
                    {"role": "user", "content": question},
                ]
            )
            parsed = json.loads(content)
            if set(parsed) != {"task_type", "supporting_quote"}:
                return None
            quote = parsed["supporting_quote"]
            if not isinstance(quote, str) or quote not in question:
                return None
            return ScientificIntentProposal(
                task_type=parsed["task_type"],
                supporting_quote=quote,
                provider_kind=self.provider.provider_kind,
                model_name=self.provider.model_name,
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, RuntimeError):
            return None


_FIELD_VALUE_PATTERNS: dict[ScientificEvidenceField, tuple[re.Pattern[str], ...]] = {
    "ligand_name": (
        re.compile(
            r"\b(?:ligand|compound|inhibitor|drug)(?:\s+name)?\s*"
            r"(?::|=|\bis\b)?\s*([A-Za-z0-9][A-Za-z0-9 ._+'/()-]{0,255})",
            re.IGNORECASE,
        ),
    ),
    "protein_chain_label": (
        re.compile(
            r"\b(?:protein\s+)?chain(?:\s+(?:label|id))?\s*"
            r"(?::|=|\bis\b)?\s*([A-Za-z0-9]{1,4})\b",
            re.IGNORECASE,
        ),
    ),
    "protein_residue_sequence": (
        re.compile(
            r"\bresidue(?:\s+(?:sequence|number|position|seq))?\s*"
            r"(?::|=|\bis\b)?\s*(-?[0-9]+[A-Za-z]?)\b",
            re.IGNORECASE,
        ),
    ),
    "protein_residue_name": (
        re.compile(
            r"\bresidue\s+name\s*(?::|=|\bis\b)?\s*"
            r"([A-Za-z0-9]{1,5})\b",
            re.IGNORECASE,
        ),
    ),
    "protein_atom_name": (
        re.compile(
            r"\b(?:protein\s+)?atom(?:\s+name)?\s*"
            r"(?::|=|\bis\b)?\s*([A-Za-z0-9*'+-]{1,8})\b",
            re.IGNORECASE,
        ),
    ),
    "protein_nucleophile_formal_charge": (
        re.compile(
            r"\b(?:protein\s+)?(?:nucleophile\s+)?formal\s+charge\s*"
            r"(?::|=|\bis\b)?\s*(-?[0-9]+)\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\bprotein\s+nucleophile\s+charge\s*"
            r"(?::|=|\bis\b)?\s*(-?[0-9]+)\b",
            re.IGNORECASE,
        ),
    ),
    "ligand_reaction_smarts": (
        re.compile(
            r"\b(?:ligand\s+)?(?:reaction\s+)?SMARTS\s*"
            r"(?::|=|\bis\b)?\s*([^\s]+)",
            re.IGNORECASE,
        ),
    ),
    "selected_total_qm_charge": (
        re.compile(
            r"\b(?:selected\s+)?total\s+(?:QM\s+)?charge\s*"
            r"(?::|=|\bis\b)?\s*(-?[0-9]+)\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\bQM\s+total\s+charge\s*"
            r"(?::|=|\bis\b)?\s*(-?[0-9]+)\b",
            re.IGNORECASE,
        ),
    ),
    "selected_spin": (
        re.compile(
            r"\b(?:selected\s+)?spin(?:\s+multiplicity)?\s*"
            r"(?::|=|\bis\b)?\s*([0-9]+)\b",
            re.IGNORECASE,
        ),
    ),
}


def _quoted_value_is_grounded(
    field_name: ScientificEvidenceField,
    value: str | int,
    quote: str,
) -> bool:
    """Reject a proposed value unless its field and value occur in the quote."""

    expected = str(value).casefold()
    for pattern in _FIELD_VALUE_PATTERNS[field_name]:
        for match in pattern.finditer(quote):
            observed = match.group(1)
            if field_name == "ligand_reaction_smarts":
                observed = observed.rstrip(".,")
            if observed.casefold() == expected:
                return True
    return False


def _deterministic_partial_target(
    turns: tuple[ScientistEvidenceTurn, ...],
) -> ScientificEvidenceProposal | None:
    """Extract unique explicitly labelled fields without relying on model output."""

    field_names: tuple[ScientificEvidenceField, ...] = (
        "protein_chain_label",
        "protein_residue_sequence",
        "protein_residue_name",
        "protein_atom_name",
        "protein_nucleophile_formal_charge",
        "ligand_reaction_smarts",
        "selected_total_qm_charge",
        "selected_spin",
    )
    extracted: dict[str, str | int] = {}
    quotes: list[ScientificEvidenceQuote] = []
    for field_name in field_names:
        matches: list[tuple[str, str, str]] = []
        for turn in turns:
            for pattern in _FIELD_VALUE_PATTERNS[field_name]:
                for match in pattern.finditer(turn.content):
                    observed = match.group(1)
                    if field_name == "ligand_reaction_smarts":
                        observed = observed.rstrip(".,")
                    matches.append(
                        (observed, turn.turn_identifier, turn.content)
                    )
        unique_values = {item[0].casefold() for item in matches}
        if len(unique_values) != 1:
            continue
        observed, turn_identifier, quote = matches[-1]
        value: str | int = (
            int(observed)
            if field_name in {
                "protein_nucleophile_formal_charge",
                "selected_total_qm_charge",
                "selected_spin",
            }
            else observed
        )
        if not _quoted_value_is_grounded(field_name, value, quote):
            continue
        candidate = {"reaction_family": "covalent_substitution", **extracted}
        candidate[field_name] = value
        try:
            PartialCovalentReactionTarget.model_validate(candidate)
        except ValueError:
            # One invalid labelled value must not discard other grounded fields or
            # escape the non-authoritative extraction boundary.
            continue
        extracted[field_name] = value
        quotes.append(
            ScientificEvidenceQuote(
                field_name=field_name,
                turn_identifier=turn_identifier,
                supporting_quote=quote,
            )
        )
    if not extracted:
        return None
    try:
        partial = PartialCovalentReactionTarget.model_validate(
            {"reaction_family": "covalent_substitution", **extracted}
        )
    except ValueError:
        return None
    complete = partial.complete_target()
    canonical = json.dumps(
        {
            "target": partial.model_dump(mode="json"),
            "evidence": [item.model_dump(mode="json") for item in quotes],
            "provider": "deterministic_label_parser",
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return ScientificEvidenceProposal(
        proposal_identifier=(
            "evidence-proposal-"
            + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]
        ),
        source_kind="conversation_extraction",
        summary=(
            "Pulsate extracted "
            + ("a complete reaction target" if complete else "partial reaction evidence")
            + " from uniquely labelled scientist text. Review and confirm it before use."
        ),
        covalent_reaction_target=complete,
        partial_covalent_reaction_target=(None if complete else partial),
        supporting_quotes=tuple(quotes),
        provider_kind="deterministic_label_parser",
        model_name="built-in",
    )


class ProviderNeutralScientificEvidenceInterpreter:
    """Extract reviewable structured evidence from exact scientist quotes.

    The provider proposes values but cannot commit them. Every value is checked
    against a literal quote and the complete proposal is held behind explicit
    scientist confirmation by the research-session controller.
    """

    def __init__(self, provider: NaturalLanguageModelProvider | None) -> None:
        self.provider = provider

    def capability(self) -> dict[str, object]:
        return {
            "available": self.provider is not None,
            "provider_kind": (
                self.provider.provider_kind if self.provider is not None else None
            ),
            "model_name": (
                self.provider.model_name if self.provider is not None else None
            ),
            "requires_exact_quotes": True,
            "requires_scientist_confirmation": True,
            "supports_partial_evidence": True,
            "supports_named_input_candidates": True,
        }

    def propose_covalent_target(
        self,
        turns: tuple[ScientistEvidenceTurn, ...],
    ) -> ScientificEvidenceProposal | None:
        if self.provider is None or not turns:
            return _deterministic_partial_target(turns)
        deterministic = _deterministic_partial_target(turns)
        if deterministic is not None:
            return deterministic
        payload = {
            "scientist_turns": [item.model_dump(mode="json") for item in turns]
        }
        try:
            content = self.provider.complete(
                [
                    {
                        "role": "system",
                        "content": (
                            "Extract a complete covalent reaction target when every "
                            "required value is explicit, or extract any explicit partial "
                            "subset. Return "
                            "exactly one JSON object with keys reaction_target and "
                            "evidence, or return {}. reaction_target must contain only: "
                            "reaction_family (exactly covalent_substitution), "
                            "protein_chain_label, protein_residue_sequence, optional "
                            "protein_residue_name (string or null), protein_atom_name, "
                            "protein_nucleophile_formal_charge (integer), "
                            "ligand_reaction_smarts, selected_total_qm_charge (integer), "
                            "and selected_spin (integer). Omit every unknown field; never "
                            "emit null. evidence must contain one item "
                            "for every non-null target field except reaction_family. Each "
                            "item must contain only field_name, turn_identifier, and "
                            "supporting_quote. supporting_quote must be a literal substring "
                            "copied from that scientist turn and must itself state both the "
                            "field and value. Never infer, calculate, repair, translate, or "
                            "supply a missing value. Never use a Pulsate question as "
                            "evidence. Do not include commentary."
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(payload, separators=(",", ":")),
                    },
                ]
            )
            parsed = json.loads(content)
            if set(parsed) != {"reaction_target", "evidence"}:
                return None
            partial_target = PartialCovalentReactionTarget.model_validate(
                parsed["reaction_target"]
            )
            evidence = tuple(
                ScientificEvidenceQuote.model_validate(item)
                for item in parsed["evidence"]
            )
            expected_fields = {
                field_name
                for field_name, value in partial_target.model_dump().items()
                if field_name != "reaction_family" and value is not None
            }
            observed_fields = [item.field_name for item in evidence]
            if (
                len(observed_fields) != len(set(observed_fields))
                or set(observed_fields) != expected_fields
            ):
                return None
            turn_by_identifier = {
                item.turn_identifier: item.content for item in turns
            }
            target_values = partial_target.model_dump()
            for item in evidence:
                turn = turn_by_identifier.get(item.turn_identifier)
                value = target_values[item.field_name]
                if (
                    turn is None
                    or item.supporting_quote not in turn
                    or not isinstance(value, (str, int))
                    or not _quoted_value_is_grounded(
                        item.field_name, value, item.supporting_quote
                    )
                ):
                    return None
            canonical = json.dumps(
                {
                    "target": partial_target.model_dump(mode="json"),
                    "evidence": [item.model_dump(mode="json") for item in evidence],
                    "provider": self.provider.provider_kind,
                    "model": self.provider.model_name,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            complete_target = partial_target.complete_target()
            return ScientificEvidenceProposal(
                proposal_identifier=(
                    "evidence-proposal-"
                    + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]
                ),
                source_kind="conversation_extraction",
                summary=(
                    "Pulsate extracted "
                    + ("a complete reaction target" if complete_target else "partial reaction evidence")
                    + " from exact scientist quotes. Review and confirm it before use."
                ),
                covalent_reaction_target=complete_target,
                partial_covalent_reaction_target=(
                    None if complete_target is not None else partial_target
                ),
                supporting_quotes=evidence,
                provider_kind=self.provider.provider_kind,
                model_name=self.provider.model_name,
            )
        except (
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            RuntimeError,
        ):
            return None

    def propose_named_ligand(
        self,
        turns: tuple[ScientistEvidenceTurn, ...],
    ) -> ScientificNamedInputCandidate | None:
        """Propose one literal named ligand for deterministic source resolution."""

        if self.provider is None or not turns:
            return None
        payload = {
            "scientist_turns": [item.model_dump(mode="json") for item in turns]
        }
        try:
            content = self.provider.complete(
                [
                    {
                        "role": "system",
                        "content": (
                            "Identify exactly one named ligand, compound, inhibitor, "
                            "or drug only when the scientist literally names it. Return "
                            "exactly one JSON object with name, turn_identifier, and "
                            "supporting_quote, or return {}. The name and quote must be "
                            "literal substrings of that scientist turn. Do not provide "
                            "an identifier, structure, synonym, correction, inference, "
                            "or commentary. If more than one candidate is named, return {}."
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(payload, separators=(",", ":")),
                    },
                ]
            )
            parsed = json.loads(content)
            if set(parsed) != {"name", "turn_identifier", "supporting_quote"}:
                return None
            candidate = ScientificNamedInputCandidate(
                **parsed,
                provider_kind=self.provider.provider_kind,
                model_name=self.provider.model_name,
            )
            turn_by_identifier = {
                item.turn_identifier: item.content for item in turns
            }
            turn = turn_by_identifier.get(candidate.turn_identifier)
            if (
                turn is None
                or candidate.supporting_quote not in turn
                or candidate.name not in candidate.supporting_quote
            ):
                return None
            return candidate
        except (
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            RuntimeError,
        ):
            return None

    def propose_named_protein(
        self,
        turns: tuple[ScientistEvidenceTurn, ...],
    ) -> ScientificNamedInputCandidate | None:
        """Propose one literal protein name for deterministic trusted-source lookup."""

        if self.provider is None or not turns:
            return None
        payload = {
            "scientist_turns": [item.model_dump(mode="json") for item in turns]
        }
        try:
            content = self.provider.complete(
                [
                    {
                        "role": "system",
                        "content": (
                            "Identify exactly one named protein, receptor, enzyme, or "
                            "target only when the scientist literally names it. Return "
                            "exactly one JSON object with name, turn_identifier, and "
                            "supporting_quote, or return {}. The name and quote must be "
                            "literal substrings of that scientist turn. Do not provide an "
                            "identifier, organism, structure, synonym, correction, inference, "
                            "or commentary. If more than one candidate is named, return {}."
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(payload, separators=(",", ":")),
                    },
                ]
            )
            parsed = json.loads(content)
            if set(parsed) != {"name", "turn_identifier", "supporting_quote"}:
                return None
            candidate = ScientificNamedInputCandidate(
                **parsed,
                provider_kind=self.provider.provider_kind,
                model_name=self.provider.model_name,
                entity_type="protein",
            )
            turn_by_identifier = {
                item.turn_identifier: item.content for item in turns
            }
            turn = turn_by_identifier.get(candidate.turn_identifier)
            if (
                turn is None
                or candidate.supporting_quote not in turn
                or candidate.name not in candidate.supporting_quote
            ):
                return None
            return candidate
        except (
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            RuntimeError,
        ):
            return None


__all__ = [
    "ClarificationPrompt",
    "DeterministicScientificQuestionWriter",
    "PartialCovalentReactionTarget",
    "ProviderNeutralScientificEvidenceInterpreter",
    "ProviderNeutralScientificIntentInterpreter",
    "ProviderNeutralScientificQuestionWriter",
    "ScientificEvidenceField",
    "ScientificEvidenceInterpreter",
    "ScientificEvidenceProposal",
    "ScientificEvidenceQuote",
    "ScientificIntentInterpreter",
    "ScientificIntentProposal",
    "ScientificNamedInputCandidate",
    "ScientificEntityResolutionCandidate",
    "ScientificQuestionWriter",
    "ScientistEvidenceTurn",
]
