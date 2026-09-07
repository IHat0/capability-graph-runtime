"""Concrete Phase 8 molecular components on the Phase 7 discovery contracts."""

from __future__ import annotations

import hashlib

import pytest

from cgr.discovery import (
    CandidateAssessment,
    CandidateGenerationRequest,
    MolecularCandidateEvidenceVerifier,
    RDKitDeNovoMolecularCandidateGenerator,
    RDKitMolecularCandidateGenerator,
    RDKitMolecularDescriptorEvaluator,
    RDKitMolecularValidityChecker,
    molecular_discovery_registries,
)
from cgr.science import ArtifactPointer


class MemoryCandidateStore:
    def __init__(self) -> None:
        self.payloads: dict[tuple[str, str], bytes] = {}

    def put(self, artifact_type: str, payload: bytes) -> ArtifactPointer:
        digest = hashlib.sha256(payload).hexdigest()
        identifier = f"{artifact_type}-{digest[:32]}"
        self.payloads[(identifier, digest)] = payload
        return ArtifactPointer(
            artifact_identifier=identifier,
            content_sha256=digest,
        )

    def read(self, pointer: ArtifactPointer) -> bytes:
        return self.payloads[(pointer.artifact_identifier, pointer.content_sha256)]


def _generation_request(
    *,
    generation: int,
    parents: tuple = (),
) -> CandidateGenerationRequest:
    return CandidateGenerationRequest(
        request_identifier=f"generation.request-{generation}",
        campaign_identifier="campaign.molecular-components",
        campaign_sha256="c" * 64,
        generation=generation,
        parent_candidates=parents,
        objective_identifiers=("objective.qed",),
        max_candidates=6,
    )


def test_supplied_candidate_comparison_preserves_sources_and_budget():
    from types import SimpleNamespace
    from cgr.pulsate_api.phase8_scientific_handlers import (
        _discovery_ligands, _discovery_generates, DiscoveryCampaignInitializeHandler,
        DiscoveryDesignLoopTraceHandler,
    )
    pytest.importorskip("rdkit")
    refs = tuple(SimpleNamespace(
        artifact_identifier=f"input-{i}", artifact_type="ligand_structure",
        media_type="chemical/x-daylight-smiles", content_sha256=hashlib.sha256(data).hexdigest(),
        metadata={"display_name": f"candidate-{i}"}, payload=data,
    ) for i, data in enumerate((b"CCO\nCCN", b"OCC", b"c1ccccc1")))
    record = SimpleNamespace(artifact_references=refs, objective=SimpleNamespace(input_references=refs))
    supplied = _discovery_ligands(record, SimpleNamespace(read=lambda ref: ref.payload))
    assert set(supplied) == {"CCO", "CCN", "c1ccccc1"}
    assert len(supplied["CCO"]["sources"]) == 2
    assert supplied["CCN"]["sources"][0]["record_number"] == 2
    generator = RDKitMolecularCandidateGenerator(
        MemoryCandidateStore(), seed_smiles=tuple(supplied), seed_metadata=supplied,
    )
    result = generator.propose(_generation_request(generation=0))
    assert len(result.candidates) == 3
    assert all(candidate.metadata["sources"] for candidate in result.candidates)
    assert all(candidate.transformation is None for candidate in result.candidates)
    objective = SimpleNamespace(
        research_requirements=SimpleNamespace(operations=("verify_and_rank_results",)),
        objective_identifier="objective.comparison",
        budget=SimpleNamespace(maximum_candidates=64, maximum_wall_time_seconds=600),
    )
    assert not _discovery_generates(objective)
    campaign = DiscoveryCampaignInitializeHandler.campaign(
        objective, generator_identifier=generator.generator_identifier, candidate_count=7,
    )
    assert campaign.budget.max_generations == 1
    assert campaign.budget.max_candidates_total == 7
    assert campaign.budget.max_candidates_per_generation == 7
    assert campaign.budget.max_expensive_evaluations == 7
    assert "design_next_generation" not in DiscoveryDesignLoopTraceHandler.stages(False)
    objective.research_requirements.operations = ("generate_candidates",)
    generated = DiscoveryCampaignInitializeHandler.campaign(
        objective, generator_identifier=generator.generator_identifier, candidate_count=4,
    )
    assert generated.budget.max_generations == 2
    assert generated.budget.max_candidates_total > 4


def test_invalid_candidate_after_valid_record_is_not_dropped():
    from types import SimpleNamespace
    from cgr.pulsate_api.phase8_scientific_handlers import _discovery_ligands
    from cgr.pulsate_api.scientific_runtime import ScientificCapabilityFailure
    pytest.importorskip("rdkit")
    ref = SimpleNamespace(artifact_identifier="input", artifact_type="ligand_structure",
                          media_type="chemical/x-daylight-smiles")
    record = SimpleNamespace(artifact_references=(ref,),
                             objective=SimpleNamespace(input_references=(ref,)))
    with pytest.raises(ScientificCapabilityFailure, match="invalid molecular record"):
        _discovery_ligands(record, SimpleNamespace(read=lambda ref: b"CCO\nINVALID"))


def test_molecular_generator_produces_real_seed_and_transformed_lineage() -> None:
    pytest.importorskip("rdkit")
    store = MemoryCandidateStore()
    generator = RDKitMolecularCandidateGenerator(
        store,
        seed_smiles=("CCO", "c1ccccc1O"),
        allowed_substituents=("C", "F"),
    )

    seeds = generator.propose(_generation_request(generation=0))
    assert len(seeds.candidates) == 2
    assert all(candidate.generation == 0 for candidate in seeds.candidates)
    assert all(candidate.candidate_type == "small_molecule" for candidate in seeds.candidates)

    children = generator.propose(
        _generation_request(generation=1, parents=(seeds.candidates[0],))
    )
    assert children.candidates
    assert all(candidate.generation == 1 for candidate in children.candidates)
    assert all(candidate.transformation is not None for candidate in children.candidates)
    assert all(
        candidate.parent_candidate_identifiers
        == (seeds.candidates[0].candidate_identifier,)
        for candidate in children.candidates
    )


def test_de_novo_generator_constructs_candidates_without_scientist_seed() -> None:
    pytest.importorskip("rdkit")
    store = MemoryCandidateStore()
    generator = RDKitDeNovoMolecularCandidateGenerator(
        store,
        construction_fragments=("C1CCCCC1", "c1ccccc1"),
        allowed_extensions=("C", "N", "O", "F"),
        assembly_depth=2,
        construction_policy_identifier="policy.test_de_novo_fragments_v1",
    )

    initial = generator.propose(_generation_request(generation=0))

    assert initial.candidates
    assert all(candidate.generation == 0 for candidate in initial.candidates)
    assert all(not candidate.parent_candidate_identifiers for candidate in initial.candidates)
    assert all(candidate.transformation is None for candidate in initial.candidates)
    assert all(
        candidate.metadata["generation_kind"] == "de_novo_fragment_assembly"
        and candidate.metadata["construction_policy_identifier"]
        == "policy.test_de_novo_fragments_v1"
        for candidate in initial.candidates
    )
    assert all(
        RDKitMolecularValidityChecker(store).check(candidate).valid
        for candidate in initial.candidates
    )

    children = generator.propose(
        _generation_request(generation=1, parents=(initial.candidates[0],))
    )
    assert children.candidates
    assert all(candidate.transformation is not None for candidate in children.candidates)
    assert all(
        candidate.transformation.transformation_kind == "de_novo_graph_extension"
        for candidate in children.candidates
        if candidate.transformation is not None
    )
    assert all(
        candidate.parent_candidate_identifiers
        == (initial.candidates[0].candidate_identifier,)
        for candidate in children.candidates
    )


def test_molecular_validity_evaluation_and_verification_require_evidence() -> None:
    pytest.importorskip("rdkit")
    store = MemoryCandidateStore()
    generator = RDKitMolecularCandidateGenerator(store, seed_smiles=("CCO",))
    candidate = generator.propose(_generation_request(generation=0)).candidates[0]

    validity = RDKitMolecularValidityChecker(store).check(candidate)
    assert validity.valid
    assert all(finding.passed for finding in validity.findings if finding.hard)

    evaluator = RDKitMolecularDescriptorEvaluator(
        store,
        objective_metrics={"objective.qed": "qed"},
        random_seed=17,
    )
    evaluation = evaluator.evaluate(candidate)
    assert evaluation.evidence_records
    assert evaluation.objective_scores[0].objective_identifier == "objective.qed"
    assert 0.0 <= evaluation.objective_scores[0].normalized_value <= 1.0
    assert evaluation.constraint_results[0].passed

    assessment = CandidateAssessment(
        assessment_identifier="assessment.real-molecular-evidence",
        candidate_identifier=candidate.candidate_identifier,
        candidate_sha256=candidate.fingerprint,
        evidence_records=evaluation.evidence_records,
        constraint_results=evaluation.constraint_results,
        objective_scores=evaluation.objective_scores,
    )
    report = MolecularCandidateEvidenceVerifier().verify(
        candidate,
        assessment,
        "candidate_ranking",
    )
    assert report.overall_outcome.value == "passed"
    assert report.execution_integrity_passed
    assert report.scientific_quality_passed


def test_molecular_registry_factory_wires_existing_phase7_registries() -> None:
    store = MemoryCandidateStore()
    generators, checkers, evaluators, verifiers = molecular_discovery_registries(
        store,
        seed_smiles=("CCO",),
        objective_metrics={"objective.qed": "qed"},
    )

    assert generators.identifiers() == ("generator.rdkit_scaffold_transform",)
    assert checkers.identifiers() == ("checker.rdkit_druglike_validity",)
    assert evaluators.identifiers() == ("evaluator.rdkit_3d_descriptors",)
    assert verifiers.identifiers() == ("verifier.molecular_candidate_evidence",)
