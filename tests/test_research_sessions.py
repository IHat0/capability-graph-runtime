"""Unified, resumable scientist-session contracts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from cgr.kernel.contracts import CapabilityVersion
from cgr.pulsate_api.app import _load_preset, create_app
from cgr.pulsate_api.experiments import ExperimentStore
from cgr.pulsate_api.natural_language import NaturalLanguageInterpretationStore
from cgr.pulsate_api.research_sessions import (
    ResearchSessionController,
    ResearchSessionCreateRequest,
    ResearchSessionReplyRequest,
    ResearchSessionRepository,
)
from cgr.pulsate_api.runs import RunCoordinator
from cgr.pulsate_api.scientific_conversation import (
    DeterministicScientificQuestionWriter,
    ProviderNeutralScientificEvidenceInterpreter,
    ProviderNeutralScientificIntentInterpreter,
    ProviderNeutralScientificQuestionWriter,
    ScientificEntityResolutionCandidate,
    ScientificEvidenceProposal,
    ScientificEvidenceQuote,
    ScientificNamedInputCandidate,
)
from cgr.pulsate_api.scientific_executions import ScientificExecutionRepository
from cgr.pulsate_api.scientific_objectives import ScientificInputReference
from cgr.pulsate_api.scientific_requirements import (
    ProviderNeutralScientificRequirementInterpreter,
)
from cgr.pulsate_api.security import (
    InMemoryAuditSink,
    InMemoryGrantProvider,
    SecurityServices,
    StaticAuthenticator,
)
from cgr.pulsate_api.security_contracts import (
    AuthenticatedPrincipal,
    ProtectedAction,
    ResourceGrant,
)
from cgr.pulsate_api.workflows import WorkflowService
from cgr.science import ArtifactReference, CreationProvenance
from cgr.workflow_graph import CapabilityAdapterRegistry


def _input() -> ScientificInputReference:
    return ScientificInputReference(
        reference_identifier="input-01-molecular-structure",
        artifact_type="molecular_structure",
        artifact_identifier="artifact-molecular-structure",
    )


def _controller(root: Path) -> ResearchSessionController:
    execution_repository = ScientificExecutionRepository(root / "executions")
    session_repository = ResearchSessionRepository(root / "sessions")
    execution_repository.start()
    session_repository.start()
    return ResearchSessionController(
        repository=session_repository,
        execution_repository=execution_repository,
        question_writer=DeterministicScientificQuestionWriter(),
    )


class _NoExecution:
    def execute(self, *_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("Unified research-session tests cannot use preset runs.")


def test_missing_value_is_asked_then_same_session_resumes(tmp_path: Path) -> None:
    controller = _controller(tmp_path)
    tenant = hashlib.sha256(b"tenant-alpha").hexdigest()

    payload_digest = hashlib.sha256(b"test structure").hexdigest()
    artifact = ArtifactReference(
        artifact_identifier=_input().artifact_identifier,
        schema_version=CapabilityVersion(major=1, minor=0, patch=0),
        artifact_type="molecular_structure",
        media_type="application/octet-stream",
        content_sha256=payload_digest,
        byte_size=len(b"test structure"),
        provenance=CreationProvenance(
            producer="research-session-tests",
            producer_version=CapabilityVersion(major=1, minor=0, patch=0),
        ),
    )
    first = controller.create(
        ResearchSessionCreateRequest(
            question="Which conformer is preferred: axial or equatorial?",
            input_references=(_input(),),
            artifact_references=(artifact,),
        ),
        tenant_identifier_sha256=tenant,
    )

    assert first.status == "awaiting_clarification"
    assert first.compilation is not None
    assert first.compilation.compatibility_objective.solvent is None
    assert first.next_questions[0].question == (
        "Which solvent should be used for the comparison?"
    )
    assert first.compilation.canonical_plan.unresolved_requirement_identifiers

    resumed = controller.reply(
        first.session_identifier,
        ResearchSessionReplyRequest(message="Use water."),
        tenant_identifier_sha256=tenant,
    )

    assert resumed.session_identifier == first.session_identifier
    assert resumed.revision == 2
    assert resumed.status == "planned"
    assert not resumed.next_questions
    assert resumed.compilation is not None
    assert resumed.compilation.compatibility_objective.solvent == "water"
    assert resumed.compilation.canonical_objective.objective_type == (
        "solvated_conformer_comparison"
    )
    assert resumed.compilation.canonical_plan.selected_assignments
    assert resumed.compilation.canonical_graph.nodes


def test_unsupported_wording_becomes_a_follow_up_not_a_terminal_error(
    tmp_path: Path,
) -> None:
    controller = _controller(tmp_path)
    tenant = hashlib.sha256(b"tenant-alpha").hexdigest()

    session = controller.create(
        ResearchSessionCreateRequest(question="Please investigate this system."),
        tenant_identifier_sha256=tenant,
    )

    assert session.status == "awaiting_clarification"
    assert session.compilation is None
    assert session.next_questions[0].requirement_identifier == (
        "requirement-research-goal"
    )


def test_unsupported_follow_up_does_not_repeat_the_same_prompt(
    tmp_path: Path,
) -> None:
    controller = _controller(tmp_path)
    tenant = hashlib.sha256(b"tenant-routing-loop").hexdigest()
    first = controller.create(
        ResearchSessionCreateRequest(question="Please investigate this system."),
        tenant_identifier_sha256=tenant,
    )

    second = controller.reply(
        first.session_identifier,
        ResearchSessionReplyRequest(
            message=(
                "I do not have additional evidence yet. Explain the exact "
                "acceptable input formats."
            )
        ),
        tenant_identifier_sha256=tenant,
    )

    assert second.revision == 2
    assert second.status == "awaiting_clarification"
    assert second.next_questions != first.next_questions
    assert second.next_questions[0].question.startswith(
        "I could not validate the previous reply"
    )


def test_authenticated_research_api_resumes_and_is_tenant_isolated(
    tmp_path: Path,
) -> None:
    principals = {
        token: AuthenticatedPrincipal(
            subject_identifier=f"scientist-{tenant}",
            tenant_identifier=tenant,
            authentication_method="test-bearer",
            scopes=tuple(action.value for action in ProtectedAction),
            audit_identifier=f"scientist-{tenant}",
        )
        for token, tenant in (
            ("alpha-token", "tenant-alpha"),
            ("beta-token", "tenant-beta"),
        )
    }
    grants = tuple(
        ResourceGrant(
            tenant_identifier=principal.tenant_identifier,
            subject_identifier=principal.subject_identifier,
            resource_type="scientific-execution",
            resource_identifier="*",
            allowed_actions=tuple(ProtectedAction),
        )
        for principal in principals.values()
    )
    application = create_app(
        coordinator=RunCoordinator(
            run_root=tmp_path / "runs",
            manifest_resolver=_load_preset,
            executor=_NoExecution(),
            enabled=False,
        ),
        experiment_store=ExperimentStore(tmp_path / "experiments"),
        natural_language_store=NaturalLanguageInterpretationStore(
            tmp_path / "interpretations",
            None,
            unavailable_reason="deterministic session test",
        ),
        workflow_service=WorkflowService(
            root=tmp_path / "workflows",
            capability_adapter=CapabilityAdapterRegistry(),
            maximum_parallelism=1,
        ),
        scientific_execution_repository=ScientificExecutionRepository(
            tmp_path / "executions"
        ),
        security_services=SecurityServices(
            authenticator=StaticAuthenticator(principals),
            grant_provider=InMemoryGrantProvider(grants, allow_wildcards=True),
            audit_sink=InMemoryAuditSink(),
        ),
    )

    with TestClient(application) as client:
        alpha = {"Authorization": "Bearer alpha-token"}
        beta = {"Authorization": "Bearer beta-token"}
        created = client.post(
            "/api/v1/research/sessions",
            headers=alpha,
            json={
                "question": "Which conformer is preferred: axial or equatorial?"
            },
        )
        assert created.status_code == 201, created.text
        assert created.json()["status"] == "awaiting_clarification"
        session_identifier = created.json()["session_identifier"]

        replied = client.post(
            f"/api/v1/research/sessions/{session_identifier}/reply",
            headers=alpha,
            json={"message": "Use water."},
        )
        assert replied.status_code == 200, replied.text
        assert replied.json()["revision"] == 2
        assert client.get(
            f"/api/v1/research/sessions/{session_identifier}",
            headers=beta,
        ).status_code == 404


def test_question_writer_cannot_commit_or_invent_scientific_values() -> None:
    class Provider:
        provider_kind = "controlled_test_provider"
        model_name = "replaceable-model"
        request_count = 0

        def complete(self, _messages: list[dict[str, str]]) -> str:
            return json.dumps(
                {
                    "questions": [
                        {
                            "requirement_identifier": "requirement-solvent",
                            "question": "Use water.",
                            "solvent": "water",
                        }
                    ]
                }
            )

    writer = ProviderNeutralScientificQuestionWriter(
        Provider()  # type: ignore[arg-type]
    )
    questions = writer.write(
        (("requirement-solvent", "A solvent identity is required."),)
    )

    # Extra model fields fail closed, so the deterministic writer asks instead.
    assert questions[0].question.startswith("I still need clarification")
    assert questions[0].model_dump() == {
        "requirement_identifier": "requirement-solvent",
        "question": questions[0].question,
    }


def test_model_intent_is_grounded_and_compiles_without_redundant_confirmation(
    tmp_path: Path,
) -> None:
    class Provider:
        provider_kind = "controlled_test_provider"
        model_name = "replaceable-model"
        request_count = 0

        def complete(self, messages: list[dict[str, str]]) -> str:
            if "Classify" in messages[0]["content"]:
                return json.dumps(
                    {
                        "task_type": "protein_ligand_discovery",
                        "supporting_quote": "find candidates",
                    }
                )
            identifiers = [
                item["requirement_identifier"]
                for item in json.loads(messages[1]["content"])["requirements"]
            ]
            return json.dumps(
                {
                    "questions": [
                        {
                            "requirement_identifier": identifier,
                            "question": "Please confirm or clarify this requirement.",
                        }
                        for identifier in identifiers
                    ]
                }
            )

    execution_repository = ScientificExecutionRepository(tmp_path / "executions")
    session_repository = ResearchSessionRepository(tmp_path / "sessions")
    execution_repository.start()
    session_repository.start()
    provider = Provider()
    controller = ResearchSessionController(
        repository=session_repository,
        execution_repository=execution_repository,
        question_writer=ProviderNeutralScientificQuestionWriter(
            provider  # type: ignore[arg-type]
        ),
        intent_interpreter=ProviderNeutralScientificIntentInterpreter(
            provider  # type: ignore[arg-type]
        ),
    )
    tenant = hashlib.sha256(b"tenant-alpha").hexdigest()

    proposed = controller.create(
        ResearchSessionCreateRequest(
            question="Please find candidates for this target."
        ),
        tenant_identifier_sha256=tenant,
    )
    assert proposed.status == "awaiting_clarification"
    assert proposed.compilation is not None
    assert proposed.intent_proposal is None
    assert proposed.compilation.compatibility_objective.task_type == (
        "protein_ligand_discovery"
    )
    assert proposed.next_questions
    assert len(proposed.next_questions) == 1

    resumed = controller.reply(
        proposed.session_identifier,
        ResearchSessionReplyRequest(
            message="I do not have the remaining exact evidence yet."
        ),
        tenant_identifier_sha256=tenant,
    )
    assert resumed.compilation is not None


def test_general_requirement_proposal_is_validated_before_composition(
    tmp_path: Path,
) -> None:
    class Provider:
        provider_kind = "controlled_test_provider"
        model_name = "replaceable-model"

        def complete(self, _messages: list[dict[str, str]]) -> str:
            return json.dumps(
                {
                    "requirements": [
                        {
                            "operation": "generate_candidates",
                            "requested_output": "generated_candidates",
                            "supporting_quote": "Generate candidates",
                        },
                        {
                            "operation": "dock_candidates",
                            "requested_output": "docking_evidence",
                            "supporting_quote": "dock candidates",
                        },
                        {
                            "operation": "verify_and_rank_results",
                            "requested_output": "verified_candidate_ranking",
                            "supporting_quote": "verify and rank results",
                        },
                    ]
                }
            )

    execution_repository = ScientificExecutionRepository(tmp_path / "executions")
    session_repository = ResearchSessionRepository(tmp_path / "sessions")
    execution_repository.start()
    session_repository.start()
    provider = Provider()
    controller = ResearchSessionController(
        repository=session_repository,
        execution_repository=execution_repository,
        question_writer=DeterministicScientificQuestionWriter(),
        requirement_interpreter=ProviderNeutralScientificRequirementInterpreter(
            provider  # type: ignore[arg-type]
        ),
    )
    tenant = hashlib.sha256(b"tenant-general-requirements").hexdigest()
    question = "Generate candidates, dock candidates, and verify and rank results."

    proposed = controller.create(
        ResearchSessionCreateRequest(question=question),
        tenant_identifier_sha256=tenant,
    )

    assert proposed.requirement_proposal is None
    assert proposed.accepted_research_requirements is not None
    assert proposed.compilation is not None
    assert proposed.compilation.compatibility_objective.task_type == (
        "composed_research"
    )
    assert "discovery.design_loop_bind" in {
        item.capability_name
        for item in proposed.compilation.compatibility_plan.steps
    }
    assert proposed.status == "awaiting_clarification"


def test_complete_protein_design_question_plans_in_one_turn(
    tmp_path: Path,
) -> None:
    class Provider:
        provider_kind = "controlled_test_provider"
        model_name = "replaceable-model"

        def complete(self, _messages: list[dict[str, str]]) -> str:
            return json.dumps(
                {
                    "requirements": [
                        {
                            "operation": "generate_protein_candidates",
                            "requested_output": "generated_protein_candidates",
                            "supporting_quote": "Generate a de novo protein",
                        }
                    ]
                }
            )

    execution_repository = ScientificExecutionRepository(tmp_path / "executions")
    session_repository = ResearchSessionRepository(tmp_path / "sessions")
    execution_repository.start()
    session_repository.start()
    provider = Provider()
    controller = ResearchSessionController(
        repository=session_repository,
        execution_repository=execution_repository,
        question_writer=DeterministicScientificQuestionWriter(),
        requirement_interpreter=ProviderNeutralScientificRequirementInterpreter(
            provider  # type: ignore[arg-type]
        ),
    )

    session = controller.create(
        ResearchSessionCreateRequest(
            question="Generate a de novo protein with exactly 48 residues."
        ),
        tenant_identifier_sha256=hashlib.sha256(
            b"tenant-one-turn-protein"
        ).hexdigest(),
    )

    assert session.status == "planned"
    assert not session.next_questions
    assert session.requirement_proposal is None
    assert session.accepted_research_requirements is not None
    assert session.compilation is not None
    assert session.compilation.compatibility_objective.semantic_target.target_kind == (
        "designed_protein"
    )


def test_unique_high_confidence_named_structure_is_acquired_in_one_turn(
    tmp_path: Path,
) -> None:
    class EvidenceInterpreter:
        def propose_named_protein(self, turns):
            turn = turns[-1]
            return ScientificNamedInputCandidate(
                entity_type="protein",
                name="ExampleTarget",
                turn_identifier=turn.turn_identifier,
                supporting_quote="protein ExampleTarget",
                provider_kind="controlled_test_provider",
                model_name="replaceable-model",
            )

        def propose_named_ligand(self, _turns):
            return None

        def propose_covalent_target(self, _turns):
            return None

    class Resolver:
        def acquire_exact_identifiers(self, **_kwargs):
            return None

        def acquire_named_entity(
            self,
            *,
            candidate,
            input_references,
            tenant_identifier_sha256,
        ):
            del input_references, tenant_identifier_sha256
            payload = b"trusted structure"
            digest = hashlib.sha256(payload).hexdigest()
            reference = ArtifactReference(
                artifact_identifier="scientific-input-example-target",
                schema_version=CapabilityVersion(major=1, minor=0, patch=0),
                artifact_type="protein_structure",
                media_type="chemical/x-pdb",
                content_sha256=digest,
                byte_size=len(payload),
                provenance=CreationProvenance(producer="controlled-resolver"),
                metadata={
                    "evidence_resolution_confidence": "high",
                    "acquisition_source_kind": "trusted_test_source",
                    "acquisition_source_identifier": "EXAMPLE-1",
                },
            )
            semantic = ScientificInputReference(
                reference_identifier="input-example-target",
                artifact_type="protein_structure",
                artifact_identifier=reference.artifact_identifier,
            )
            return ScientificEvidenceProposal(
                proposal_identifier="evidence-proposal-example-target",
                source_kind="named_identifier_acquisition",
                summary="One unique trusted match was resolved.",
                input_references=(semantic,),
                artifact_references=(reference,),
                supporting_quotes=(
                    ScientificEvidenceQuote(
                        field_name="protein_name",
                        turn_identifier=candidate.turn_identifier,
                        supporting_quote=candidate.supporting_quote,
                    ),
                ),
                provider_kind=candidate.provider_kind,
                model_name=candidate.model_name,
            )

        def unresolved_reason(self, **_kwargs):
            return None

        def propose_covalent_evidence(self, **_kwargs):
            return None

    execution_repository = ScientificExecutionRepository(tmp_path / "executions")
    session_repository = ResearchSessionRepository(tmp_path / "sessions")
    execution_repository.start()
    session_repository.start()
    controller = ResearchSessionController(
        repository=session_repository,
        execution_repository=execution_repository,
        question_writer=DeterministicScientificQuestionWriter(),
        evidence_interpreter=EvidenceInterpreter(),  # type: ignore[arg-type]
        evidence_resolver=Resolver(),  # type: ignore[arg-type]
    )

    session = controller.create(
        ResearchSessionCreateRequest(
            question=(
                "Discover and optimize a drug candidate for protein ExampleTarget."
            )
        ),
        tenant_identifier_sha256=hashlib.sha256(
            b"tenant-one-turn-acquisition"
        ).hexdigest(),
    )

    assert session.status == "planned"
    assert not session.next_questions
    assert session.evidence_proposal is None
    assert len(session.accepted_evidence) == 1
    assert session.input_references[0].artifact_type == "protein_structure"
    assert session.unapproved_input_artifact_identifiers == ()


def _covalent_inputs() -> tuple[
    tuple[ScientificInputReference, ...], tuple[ArtifactReference, ...]
]:
    version = CapabilityVersion(major=1, minor=0, patch=0)
    values: list[ScientificInputReference] = []
    artifacts: list[ArtifactReference] = []
    for position, artifact_type in enumerate(
        ("protein_structure", "ligand_structure"), start=1
    ):
        payload = f"synthetic {artifact_type}".encode()
        digest = hashlib.sha256(payload).hexdigest()
        identifier = f"artifact-{artifact_type.replace('_', '-')}"
        values.append(
            ScientificInputReference(
                reference_identifier=f"input-{position:02d}-{artifact_type.replace('_', '-')}",
                artifact_type=artifact_type,
                artifact_identifier=identifier,
            )
        )
        artifacts.append(
            ArtifactReference(
                artifact_identifier=identifier,
                schema_version=version,
                artifact_type=artifact_type,
                media_type="application/octet-stream",
                content_sha256=digest,
                byte_size=len(payload),
                provenance=CreationProvenance(
                    producer="research-session-tests",
                    producer_version=version,
                ),
            )
        )
    return tuple(values), tuple(artifacts)


def test_natural_language_reaction_evidence_is_quoted_confirmed_and_persisted(
    tmp_path: Path,
) -> None:
    class Provider:
        provider_kind = "controlled_test_provider"
        model_name = "replaceable-model"

        def complete(self, messages: list[dict[str, str]]) -> str:
            instruction = messages[0]["content"]
            if "Extract a complete covalent reaction target" in instruction:
                turn = json.loads(messages[1]["content"])["scientist_turns"][-1]
                quote = turn["content"]
                target = {
                    "reaction_family": "covalent_substitution",
                    "protein_chain_label": "A",
                    "protein_residue_sequence": "145",
                    "protein_residue_name": "CYS",
                    "protein_atom_name": "SG",
                    "protein_nucleophile_formal_charge": -1,
                    "ligand_reaction_smarts": "[C:1]-[S:2]",
                    "selected_total_qm_charge": -1,
                    "selected_spin": 1,
                }
                return json.dumps(
                    {
                        "reaction_target": target,
                        "evidence": [
                            {
                                "field_name": field,
                                "turn_identifier": turn["turn_identifier"],
                                "supporting_quote": quote,
                            }
                            for field in target
                            if field != "reaction_family"
                        ],
                    }
                )
            identifiers = [
                item["requirement_identifier"]
                for item in json.loads(messages[1]["content"])["requirements"]
            ]
            return json.dumps(
                {
                    "questions": [
                        {
                            "requirement_identifier": identifier,
                            "question": "Please provide the exact missing evidence.",
                        }
                        for identifier in identifiers
                    ]
                }
            )

    execution_repository = ScientificExecutionRepository(tmp_path / "executions")
    session_repository = ResearchSessionRepository(tmp_path / "sessions")
    execution_repository.start()
    session_repository.start()
    provider = Provider()
    controller = ResearchSessionController(
        repository=session_repository,
        execution_repository=execution_repository,
        question_writer=ProviderNeutralScientificQuestionWriter(
            provider  # type: ignore[arg-type]
        ),
        evidence_interpreter=ProviderNeutralScientificEvidenceInterpreter(
            provider  # type: ignore[arg-type]
        ),
    )
    inputs, artifacts = _covalent_inputs()
    tenant = hashlib.sha256(b"tenant-evidence").hexdigest()
    first = controller.create(
        ResearchSessionCreateRequest(
            question="Find the covalent reaction transition state.",
            input_references=inputs,
            artifact_references=artifacts,
        ),
        tenant_identifier_sha256=tenant,
    )
    reply = (
        "Use chain A, residue sequence 145, residue name CYS, atom SG, "
        "nucleophile formal charge -1, ligand SMARTS [C:1]-[S:2], total QM "
        "charge -1, and spin 1."
    )
    proposed = controller.reply(
        first.session_identifier,
        ResearchSessionReplyRequest(message=reply),
        tenant_identifier_sha256=tenant,
    )

    assert proposed.status == "awaiting_clarification"
    assert proposed.evidence_proposal is not None
    assert proposed.evidence_proposal.source_kind == "conversation_extraction"
    assert len(proposed.evidence_proposal.supporting_quotes) == 8
    assert any(
        item.requirement_identifier == "requirement-evidence-proposal"
        for item in proposed.next_questions
    )
    assert proposed.covalent_reaction_target is None

    confirmed = controller.reply(
        first.session_identifier,
        ResearchSessionReplyRequest(
            message="I confirm the quoted evidence.",
            accept_evidence_proposal=True,
        ),
        tenant_identifier_sha256=tenant,
    )

    assert confirmed.status == "planned"
    assert confirmed.evidence_proposal is None
    assert len(confirmed.accepted_evidence) == 1
    assert confirmed.covalent_reaction_target is not None
    assert confirmed.compilation is not None
    assert not confirmed.compilation.compatibility_objective.ambiguity_hypotheses


def test_partial_reaction_evidence_is_confirmed_accumulated_and_completed(
    tmp_path: Path,
) -> None:
    class Provider:
        provider_kind = "controlled_test_provider"
        model_name = "replaceable-model"

        def complete(self, messages: list[dict[str, str]]) -> str:
            instruction = messages[0]["content"]
            if "Extract a complete covalent reaction target" in instruction:
                turns = json.loads(messages[1]["content"])["scientist_turns"]
                first = next(
                    (item for item in turns if "chain A" in item["content"]),
                    None,
                )
                second = next(
                    (item for item in turns if "ligand SMARTS" in item["content"]),
                    None,
                )
                if second is not None:
                    fields = {
                        "protein_nucleophile_formal_charge": -1,
                        "ligand_reaction_smarts": "[C:1]-[S:2]",
                        "selected_total_qm_charge": -1,
                        "selected_spin": 1,
                    }
                    selected = second
                elif first is not None:
                    fields = {
                        "protein_chain_label": "A",
                        "protein_residue_sequence": "145",
                        "protein_residue_name": "CYS",
                        "protein_atom_name": "SG",
                    }
                    selected = first
                else:
                    return "{}"
                return json.dumps(
                    {
                        "reaction_target": {
                            "reaction_family": "covalent_substitution",
                            **fields,
                        },
                        "evidence": [
                            {
                                "field_name": field,
                                "turn_identifier": selected["turn_identifier"],
                                "supporting_quote": selected["content"],
                            }
                            for field in fields
                        ],
                    }
                )
            if "Identify exactly one named ligand" in instruction:
                return "{}"
            identifiers = [
                item["requirement_identifier"]
                for item in json.loads(messages[1]["content"])["requirements"]
            ]
            return json.dumps(
                {
                    "questions": [
                        {
                            "requirement_identifier": identifier,
                            "question": "Please provide the exact missing evidence.",
                        }
                        for identifier in identifiers
                    ]
                }
            )

    execution_repository = ScientificExecutionRepository(tmp_path / "executions")
    session_repository = ResearchSessionRepository(tmp_path / "sessions")
    execution_repository.start()
    session_repository.start()
    provider = Provider()
    controller = ResearchSessionController(
        repository=session_repository,
        execution_repository=execution_repository,
        question_writer=ProviderNeutralScientificQuestionWriter(
            provider  # type: ignore[arg-type]
        ),
        evidence_interpreter=ProviderNeutralScientificEvidenceInterpreter(
            provider  # type: ignore[arg-type]
        ),
    )
    inputs, artifacts = _covalent_inputs()
    tenant = hashlib.sha256(b"tenant-partial-evidence").hexdigest()
    created = controller.create(
        ResearchSessionCreateRequest(
            question="Find the covalent reaction transition state.",
            input_references=inputs,
            artifact_references=artifacts,
        ),
        tenant_identifier_sha256=tenant,
    )

    first_reply = controller.reply(
        created.session_identifier,
        ResearchSessionReplyRequest(
            message=(
                "Use chain A, residue sequence 145, residue name CYS, atom SG."
            )
        ),
        tenant_identifier_sha256=tenant,
    )
    assert first_reply.evidence_proposal is not None
    assert first_reply.evidence_proposal.covalent_reaction_target is None
    assert (
        first_reply.evidence_proposal.partial_covalent_reaction_target
        is not None
    )

    first_confirmed = controller.reply(
        created.session_identifier,
        ResearchSessionReplyRequest(
            message="I confirm the quoted partial evidence.",
            accept_evidence_proposal=True,
        ),
        tenant_identifier_sha256=tenant,
    )
    accepted_partial = (
        first_confirmed.accepted_partial_covalent_reaction_target
    )
    assert accepted_partial is not None
    assert accepted_partial.protein_chain_label == "A"
    assert first_confirmed.covalent_reaction_target is None
    narrowed = " ".join(item.question for item in first_confirmed.next_questions)
    assert "mapped ligand reaction SMARTS" in narrowed
    assert "protein chain label" not in narrowed

    second_reply = controller.reply(
        created.session_identifier,
        ResearchSessionReplyRequest(
            message=(
                "Use nucleophile formal charge -1, ligand SMARTS [C:1]-[S:2], "
                "total QM charge -1, and spin 1."
            )
        ),
        tenant_identifier_sha256=tenant,
    )
    assert second_reply.evidence_proposal is not None
    assert (
        second_reply.evidence_proposal.partial_covalent_reaction_target
        is not None
    )

    completed = controller.reply(
        created.session_identifier,
        ResearchSessionReplyRequest(
            message="I confirm the remaining quoted evidence.",
            accept_evidence_proposal=True,
        ),
        tenant_identifier_sha256=tenant,
    )
    assert completed.status == "planned"
    assert completed.covalent_reaction_target is not None
    assert completed.evidence_proposal is None
    assert len(completed.accepted_evidence) == 2


def test_unaccepted_historical_reply_is_retried_without_retyping(
    tmp_path: Path,
) -> None:
    class Provider:
        provider_kind = "controlled_test_provider"
        model_name = "replaceable-model"

        def complete(self, messages: list[dict[str, str]]) -> str:
            instruction = messages[0]["content"]
            if "Extract a complete covalent reaction target" in instruction:
                turns = json.loads(messages[1]["content"])["scientist_turns"]
                historical = next(
                    (item for item in turns if "chain A" in item["content"]),
                    None,
                )
                if historical is None:
                    return "{}"
                fields = {
                    "protein_chain_label": "A",
                    "protein_residue_sequence": "145",
                    "protein_residue_name": "CYS",
                    "protein_atom_name": "SG",
                }
                return json.dumps(
                    {
                        "reaction_target": {
                            "reaction_family": "covalent_substitution",
                            **fields,
                        },
                        "evidence": [
                            {
                                "field_name": field,
                                "turn_identifier": historical["turn_identifier"],
                                "supporting_quote": historical["content"],
                            }
                            for field in fields
                        ],
                    }
                )
            if "Identify exactly one named ligand" in instruction:
                return "{}"
            identifiers = [
                item["requirement_identifier"]
                for item in json.loads(messages[1]["content"])["requirements"]
            ]
            return json.dumps(
                {
                    "questions": [
                        {
                            "requirement_identifier": identifier,
                            "question": "Please provide the exact missing evidence.",
                        }
                        for identifier in identifiers
                    ]
                }
            )

    execution_repository = ScientificExecutionRepository(tmp_path / "executions")
    session_repository = ResearchSessionRepository(tmp_path / "sessions")
    execution_repository.start()
    session_repository.start()
    provider = Provider()
    controller = ResearchSessionController(
        repository=session_repository,
        execution_repository=execution_repository,
        question_writer=ProviderNeutralScientificQuestionWriter(
            provider  # type: ignore[arg-type]
        ),
        evidence_interpreter=ProviderNeutralScientificEvidenceInterpreter(
            provider  # type: ignore[arg-type]
        ),
    )
    inputs, artifacts = _covalent_inputs()
    tenant = hashlib.sha256(b"tenant-historical-evidence").hexdigest()
    created = controller.create(
        ResearchSessionCreateRequest(
            question="Find the covalent reaction transition state.",
            input_references=inputs,
            artifact_references=artifacts,
        ),
        tenant_identifier_sha256=tenant,
    )
    original_interpreter = controller.evidence_interpreter
    controller.evidence_interpreter = None
    missed = controller.reply(
        created.session_identifier,
        ResearchSessionReplyRequest(
            message=(
                "Use chain A, residue sequence 145, residue name CYS, atom SG."
            )
        ),
        tenant_identifier_sha256=tenant,
    )
    assert missed.evidence_proposal is None
    controller.evidence_interpreter = original_interpreter

    retried = controller.reply(
        created.session_identifier,
        ResearchSessionReplyRequest(
            message="Retry the unresolved evidence already provided in this session."
        ),
        tenant_identifier_sha256=tenant,
    )
    assert retried.revision == 3
    assert retried.evidence_proposal is not None
    quoted_turns = {
        item.turn_identifier
        for item in retried.evidence_proposal.supporting_quotes
    }
    historical_turn = next(
        item
        for item in missed.conversation
        if item.role == "scientist" and "chain A" in item.content
    )
    assert historical_turn.turn_identifier in quoted_turns


def test_ungrounded_model_evidence_fails_closed() -> None:
    class Provider:
        provider_kind = "controlled_test_provider"
        model_name = "replaceable-model"

        def complete(self, _messages: list[dict[str, str]]) -> str:
            return json.dumps(
                {
                    "reaction_target": {
                        "reaction_family": "covalent_substitution",
                        "protein_chain_label": "Z",
                        "protein_residue_sequence": "999",
                        "protein_atom_name": "XX",
                        "protein_nucleophile_formal_charge": 7,
                        "ligand_reaction_smarts": "[C:1]-[F:2]",
                        "selected_total_qm_charge": 7,
                        "selected_spin": 7,
                    },
                    "evidence": [],
                }
            )

    interpreter = ProviderNeutralScientificEvidenceInterpreter(
        Provider()  # type: ignore[arg-type]
    )
    from cgr.pulsate_api.scientific_conversation import ScientistEvidenceTurn

    assert interpreter.propose_covalent_target(
        (
            ScientistEvidenceTurn(
                turn_identifier="turn-safe-0001",
                content="Please continue with the information already supplied.",
            ),
        )
    ) is None


def test_deterministic_parser_overrides_model_numeric_cross_match() -> None:
    class Provider:
        provider_kind = "controlled_test_provider"
        model_name = "replaceable-model"

        def complete(self, messages: list[dict[str, str]]) -> str:
            turn = json.loads(messages[1]["content"])["scientist_turns"][-1]
            quote = turn["content"]
            target = {
                "reaction_family": "covalent_substitution",
                "protein_chain_label": "A",
                # The quote says residue 145. The proposed 1 occurs only as spin.
                "protein_residue_sequence": "1",
                "protein_residue_name": "CYS",
                "protein_atom_name": "SG",
                "protein_nucleophile_formal_charge": -1,
                "ligand_reaction_smarts": "[C:1]-[S:2]",
                "selected_total_qm_charge": -1,
                "selected_spin": 1,
            }
            return json.dumps(
                {
                    "reaction_target": target,
                    "evidence": [
                        {
                            "field_name": field,
                            "turn_identifier": turn["turn_identifier"],
                            "supporting_quote": quote,
                        }
                        for field in target
                        if field != "reaction_family"
                    ],
                }
            )

    from cgr.pulsate_api.scientific_conversation import ScientistEvidenceTurn

    interpreter = ProviderNeutralScientificEvidenceInterpreter(
        Provider()  # type: ignore[arg-type]
    )
    proposal = interpreter.propose_covalent_target(
        (
            ScientistEvidenceTurn(
                turn_identifier="turn-safe-0002",
                content=(
                    "Use chain A, residue sequence 145, residue name CYS, atom SG, "
                    "nucleophile formal charge -1, ligand SMARTS [C:1]-[S:2], "
                    "total QM charge -1, and spin 1."
                ),
            ),
        )
    )
    assert proposal is not None
    assert proposal.provider_kind == "deterministic_label_parser"
    assert proposal.covalent_reaction_target is not None
    assert proposal.covalent_reaction_target.protein_residue_sequence == "145"


def test_deterministic_parser_ignores_invalid_labelled_fields() -> None:
    """One invalid value cannot abort extraction of other grounded evidence."""

    from cgr.pulsate_api.scientific_conversation import ScientistEvidenceTurn

    interpreter = ProviderNeutralScientificEvidenceInterpreter(None)
    proposal = interpreter.propose_covalent_target(
        (
            ScientistEvidenceTurn(
                turn_identifier="turn-safe-0003",
                content=(
                    "Use chain A, residue sequence 145, atom SG, formal charge 99, "
                    "and spin 99."
                ),
            ),
        )
    )

    assert proposal is not None
    assert proposal.partial_covalent_reaction_target is not None
    partial = proposal.partial_covalent_reaction_target
    assert partial.protein_chain_label == "A"
    assert partial.protein_residue_sequence == "145"
    assert partial.protein_atom_name == "SG"
    assert partial.protein_nucleophile_formal_charge is None
    assert partial.selected_spin is None


def test_invalid_evidence_provider_proposal_falls_back_to_clarification(
    tmp_path: Path,
) -> None:
    """A broken non-authoritative interpreter cannot abort a session reply."""

    class BrokenEvidenceInterpreter:
        def capability(self) -> dict[str, object]:
            return {"available": True}

        def propose_named_ligand(self, _turns: object) -> None:
            return None

        def propose_covalent_target(self, _turns: object) -> None:
            raise ValueError("invalid model-derived field")

    execution_repository = ScientificExecutionRepository(tmp_path / "executions")
    session_repository = ResearchSessionRepository(tmp_path / "sessions")
    execution_repository.start()
    session_repository.start()
    controller = ResearchSessionController(
        repository=session_repository,
        execution_repository=execution_repository,
        question_writer=DeterministicScientificQuestionWriter(),
        evidence_interpreter=BrokenEvidenceInterpreter(),  # type: ignore[arg-type]
    )
    inputs, artifacts = _covalent_inputs()
    tenant = hashlib.sha256(b"tenant-invalid-evidence-fallback").hexdigest()
    created = controller.create(
        ResearchSessionCreateRequest(
            question="Find the covalent reaction transition state.",
            input_references=inputs,
            artifact_references=artifacts,
        ),
        tenant_identifier_sha256=tenant,
    )

    replied = controller.reply(
        created.session_identifier,
        ResearchSessionReplyRequest(message="Please re-evaluate my prior reply."),
        tenant_identifier_sha256=tenant,
    )

    assert replied.revision == 2
    assert replied.status == "awaiting_clarification"
    assert replied.evidence_proposal is None
    assert replied.next_questions


def test_repeated_unresolved_requirement_becomes_diagnostic(
    tmp_path: Path,
) -> None:
    controller = _controller(tmp_path)
    tenant = hashlib.sha256(b"tenant-loop-guard").hexdigest()
    inputs, artifacts = _covalent_inputs()
    ligand_input = tuple(
        item for item in inputs if item.artifact_type != "ligand_structure"
    )
    ligand_artifact_ids = {item.artifact_identifier for item in ligand_input}
    protein_artifacts = tuple(
        item for item in artifacts
        if item.artifact_identifier in ligand_artifact_ids
    )
    first = controller.create(
        ResearchSessionCreateRequest(
            question="Find the covalent reaction transition state.",
            input_references=ligand_input,
            artifact_references=protein_artifacts,
        ),
        tenant_identifier_sha256=tenant,
    )
    second = controller.reply(
        first.session_identifier,
        ResearchSessionReplyRequest(message="Use the same ligand as before."),
        tenant_identifier_sha256=tenant,
    )

    ligand_question = next(
        item.question for item in second.next_questions
        if "ligand" in item.question.casefold()
    )
    assert ligand_question.startswith("I could not bind the previous reply")


def test_acquired_input_approval_is_persisted_and_cannot_be_bypassed(
    tmp_path: Path,
) -> None:
    controller = _controller(tmp_path)
    tenant = hashlib.sha256(b"tenant-acquired-approval").hexdigest()
    payload = b"exact acquired input"
    digest = hashlib.sha256(payload).hexdigest()
    semantic = ScientificInputReference(
        reference_identifier="input-01-molecular-structure",
        artifact_type="molecular_structure",
        artifact_identifier="artifact-acquired-input",
    )
    artifact = ArtifactReference(
        artifact_identifier=semantic.artifact_identifier,
        schema_version=CapabilityVersion(major=1, minor=0, patch=0),
        artifact_type=semantic.artifact_type,
        media_type="application/octet-stream",
        content_sha256=digest,
        byte_size=len(payload),
        metadata={
            "acquisition_source_kind": "exact_test_source",
            "acquisition_source_identifier": "TEST-INPUT",
        },
        provenance=CreationProvenance(
            producer="research-session-tests",
            producer_version=CapabilityVersion(major=1, minor=0, patch=0),
        ),
    )
    created = controller.create(
        ResearchSessionCreateRequest(
            question="Which conformer is preferred in water?",
            input_references=(semantic,),
            artifact_references=(artifact,),
        ),
        tenant_identifier_sha256=tenant,
    )

    assert created.status == "awaiting_approval"
    assert created.unapproved_input_artifact_identifiers == (
        artifact.artifact_identifier,
    )

    approved = controller.reply(
        created.session_identifier,
        ResearchSessionReplyRequest(
            message="I approve the exact acquired input provenance.",
            accept_acquired_input_evidence=True,
        ),
        tenant_identifier_sha256=tenant,
    )

    assert approved.status == "planned"
    assert approved.unapproved_input_artifact_identifiers == ()


def test_ambiguous_entity_reply_selects_exactly_one_listed_candidate() -> None:
    candidates = (
        ScientificEntityResolutionCandidate(
            entity_type="protein",
            source_kind="uniprot",
            source_identifier="P11111",
            display_label="FIRST (P11111; PDB 1ABC)",
            structure_identifier="1ABC",
            confidence="ambiguous",
        ),
        ScientificEntityResolutionCandidate(
            entity_type="protein",
            source_kind="uniprot",
            source_identifier="P11111",
            display_label="FIRST (P11111; PDB 2DEF)",
            structure_identifier="2DEF",
            confidence="ambiguous",
        ),
        ScientificEntityResolutionCandidate(
            entity_type="protein",
            source_kind="uniprot",
            source_identifier="P22222",
            display_label="SECOND (P22222; PDB 3GHI)",
            structure_identifier="3GHI",
            confidence="ambiguous",
        ),
    )

    selected = ResearchSessionController._selected_entity_candidate(
        "Use P11111 with structure 2DEF.",
        candidates,
    )

    assert selected == candidates[1]
    assert ResearchSessionController._selected_entity_candidate(
        "Use P11111.",
        candidates,
    ) is None
    assert ResearchSessionController._selected_entity_candidate(
        "Either 1ABC or 2DEF is fine.",
        candidates,
    ) is None
