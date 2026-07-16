"""Scientific foundation contract and integration regressions."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from cgr.kernel.contracts import CapabilityVersion, ExecutionContext, ExecutionStatus
from cgr.science import (
    ApprovalStatus,
    ArtifactLineageEdge,
    ArtifactLineageGraph,
    ArtifactReference,
    AssumptionSource,
    CapabilityDescriptor,
    CapabilityInvocation,
    CapabilityResult,
    CreationProvenance,
    DeterminismClassification,
    ExperimentExecutionPolicy,
    FailureInformation,
    FindingSeverity,
    MolecularAtomReference,
    MolecularMeasurement,
    MolecularRepresentation,
    MolecularResidueReference,
    MolecularScene,
    MolecularSelection,
    MolecularStructure,
    ScientificAssumption,
    ScientificExperiment,
    ScientificVerificationOutcome,
    ScientificVerificationResult,
    VerificationFinding,
    WorkflowDefinition,
    WorkflowPhase,
    WorkflowState,
    WorkflowTerminalStatus,
    WorkflowTransition,
    WorkflowVerificationRequirement,
    transition_workflow,
    validate_workflow_transition,
)

VERSION = CapabilityVersion(major=1, minor=0, patch=0)
PROVENANCE = CreationProvenance(
    producer="cgr.science.fixture",
    producer_version=VERSION,
    execution_identifier="execution.fixture-001",
)


def _digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _artifact(
    identifier: str,
    artifact_type: str,
    content: str,
    *,
    metadata: Mapping[str, str | int | float | bool | None] | None = None,
) -> ArtifactReference:
    return ArtifactReference(
        artifact_identifier=identifier,
        schema_version=VERSION,
        artifact_type=artifact_type,
        media_type="application/json",
        content_sha256=_digest(content),
        byte_size=len(content.encode("utf-8")),
        storage_location=f"artifacts/{identifier}.json",
        metadata=dict(metadata or {}),
        provenance=PROVENANCE,
    )


def _finding(*, blocking: bool = True) -> VerificationFinding:
    return VerificationFinding(
        code="assumption.unresolved",
        severity=FindingSeverity.ERROR,
        message="An explicit assumption is unresolved.",
        location="assumptions/geometry",
        expected="approved",
        observed="pending",
        blocking=blocking,
    )


def _verification(
    subject: ArtifactReference,
    outcome: ScientificVerificationOutcome,
    *,
    findings: tuple[VerificationFinding, ...] = (),
) -> ScientificVerificationResult:
    return ScientificVerificationResult(
        verifier_identifier="science.assumption_verifier",
        verifier_version=VERSION,
        subject=subject.pointer,
        outcome=outcome,
        findings=findings,
        summary="Assumption review completed.",
    )


def _experiment(
    structure: ArtifactReference,
    assumption: ScientificAssumption,
) -> ScientificExperiment:
    return ScientificExperiment(
        experiment_identifier="experiment.generic-001",
        schema_version=VERSION,
        original_objective="Create a verified view of the supplied invented structure.",
        normalized_objective="Verify assumptions and visualize the supplied structure.",
        scientific_domain="molecular_modeling",
        input_artifacts=(structure,),
        assumptions=(assumption,),
        constraints=("Do not infer absent physical properties.",),
        requested_outputs=("molecular_scene", "verification_report"),
        execution_policy=ExperimentExecutionPolicy(
            execution_allowed=True,
            require_all_blocking_assumptions_approved=True,
            permitted_runtimes=("cgr.local",),
        ),
        provenance=PROVENANCE,
    )


def _workflow() -> WorkflowDefinition:
    return WorkflowDefinition(
        workflow_identifier="workflow.scientific-foundation-v1",
        schema_version=VERSION,
        phases=(
            WorkflowPhase(phase_identifier="draft"),
            WorkflowPhase(
                phase_identifier="verified",
                required_artifact_types=("scientific_experiment",),
                required_verifications=(
                    WorkflowVerificationRequirement(
                        verifier_identifier="science.assumption_verifier"
                    ),
                ),
            ),
            WorkflowPhase(
                phase_identifier="visualized",
                required_artifact_types=("molecular_scene",),
            ),
            WorkflowPhase(
                phase_identifier="complete",
                required_artifact_types=("molecular_scene", "verification_report"),
                required_verifications=(
                    WorkflowVerificationRequirement(
                        verifier_identifier="science.assumption_verifier"
                    ),
                ),
            ),
        ),
        transitions=(
            WorkflowTransition(source_phase="draft", destination_phase="verified"),
            WorkflowTransition(source_phase="verified", destination_phase="visualized"),
            WorkflowTransition(source_phase="visualized", destination_phase="complete"),
        ),
        entry_phase="draft",
        terminal_phases=("complete",),
    )


def test_artifact_sha256_validation_and_semantic_identity() -> None:
    valid = _artifact("artifact.invalid", "natural_language_request", "request")
    with pytest.raises(ValidationError, match="64 lowercase"):
        ArtifactReference.model_validate(
            {**valid.model_dump(), "content_sha256": "ABC"}
        )

    first = _artifact(
        "artifact.request",
        "natural_language_request",
        "request",
        metadata={"beta": 2, "alpha": "one"},
    )
    second = _artifact(
        "artifact.request",
        "natural_language_request",
        "request",
        metadata={"alpha": "one", "beta": 2},
    )
    changed = _artifact(
        "artifact.request", "natural_language_request", "different request"
    )

    assert first.to_canonical_json() == second.to_canonical_json()
    assert first.fingerprint == second.fingerprint
    assert first.fingerprint != changed.fingerprint
    assert json.loads(first.to_canonical_json())["metadata"] == {
        "alpha": "one",
        "beta": 2,
    }


@pytest.mark.parametrize("metadata", ({"api_key": "forbidden"}, {"workspace": "/tmp/run"}))
def test_artifact_metadata_rejects_secrets_and_absolute_paths(
    metadata: dict[str, str],
) -> None:
    with pytest.raises(ValidationError):
        _artifact(
            "artifact.safe",
            "natural_language_request",
            "request",
            metadata=metadata,
        )


def test_artifact_fingerprint_is_stable_across_processes() -> None:
    artifact = _artifact(
        "artifact.cross-process",
        "natural_language_request",
        "portable request",
        metadata={"alpha": 1, "beta": "two"},
    )
    payload = json.dumps(artifact.model_dump(mode="json"))
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json,sys; from cgr.science import ArtifactReference; "
            "print(ArtifactReference.model_validate(json.loads(sys.argv[1])).fingerprint)",
            payload,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert process.returncode == 0, process.stderr
    assert process.stdout.strip() == artifact.fingerprint


def test_lineage_validation_duplicate_behavior_and_stable_graph() -> None:
    source = _artifact("artifact.source", "natural_language_request", "source")
    destination = _artifact("artifact.destination", "scientific_experiment", "destination")
    edge = ArtifactLineageEdge(
        source=source.pointer,
        destination=destination.pointer,
        relationship_type="generated_from",
        producing_capability="science.experiment_drafting",
        producing_capability_version=VERSION,
        execution_identifier="execution.fixture-001",
    )
    reverse = ArtifactLineageEdge(
        source=destination.pointer,
        destination=source.pointer,
        relationship_type="supersedes",
        producing_capability="science.experiment_drafting",
        producing_capability_version=VERSION,
    )

    assert ArtifactLineageGraph(edges=(edge, reverse)).fingerprint == ArtifactLineageGraph(
        edges=(reverse, edge)
    ).fingerprint
    with pytest.raises(ValidationError, match="Duplicate"):
        ArtifactLineageGraph(edges=(edge, edge))
    with pytest.raises(ValidationError, match="itself"):
        ArtifactLineageEdge(
            source=source.pointer,
            destination=source.pointer,
            relationship_type="derived_from",
            producing_capability="science.invalid",
            producing_capability_version=VERSION,
        )


def test_experiment_distinguishes_assumption_sources_and_approval() -> None:
    structure = _artifact("structure.generic", "molecular_structure", "coordinates")
    user_assumption = ScientificAssumption(
        assumption_identifier="assumption.user_geometry",
        description="Use the geometry supplied by the user.",
        source=AssumptionSource.USER_PROVIDED,
        approval_status=ApprovalStatus.NOT_REQUIRED,
        supporting_artifact=structure.pointer,
    )
    unresolved = ScientificAssumption(
        assumption_identifier="assumption.model_scope",
        description="The intended model scope is not yet specified.",
        source=AssumptionSource.UNRESOLVED,
        approval_status=ApprovalStatus.PENDING,
        blocks_execution_until_approved=True,
    )
    experiment = ScientificExperiment(
        **{
            **_experiment(structure, unresolved).model_dump(),
            "assumptions": (unresolved, user_assumption),
        }
    )
    reordered = ScientificExperiment(
        **{
            **experiment.model_dump(),
            "assumptions": (user_assumption, unresolved),
        }
    )

    assert experiment.fingerprint == reordered.fingerprint
    assert experiment.execution_ready is False
    assert experiment.blocking_assumptions == (unresolved,)
    approved = ScientificExperiment(
        **{
            **experiment.model_dump(),
            "assumptions": (user_assumption, unresolved.approve()),
        }
    )
    assert approved.execution_ready is True
    assert approved.fingerprint != experiment.fingerprint


def test_capability_invocation_fingerprint_excludes_operational_timestamp() -> None:
    artifact = _artifact("structure.generic", "molecular_structure", "coordinates")
    experiment = _artifact("experiment.generic", "scientific_experiment", "experiment")
    descriptor = CapabilityDescriptor(
        capability_name="science.scene_generation",
        version=VERSION,
        accepted_artifact_types=("molecular_structure",),
        produced_artifact_types=("molecular_scene",),
        required_tools=("python",),
        required_runtime="cgr.local",
        determinism=DeterminismClassification.DETERMINISTIC,
    )
    first = CapabilityInvocation(
        capability=descriptor,
        input_artifacts=(artifact,),
        experiment=experiment.pointer,
        context=ExecutionContext(
            execution_id="execution.same",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            metadata={"mode": "verified"},
        ),
        parameters={"detail": "minimal", "quality": 1},
    )
    second = CapabilityInvocation(
        capability=descriptor,
        input_artifacts=(artifact,),
        experiment=experiment.pointer,
        context=ExecutionContext(
            execution_id="execution.same",
            created_at=datetime(2030, 1, 1, tzinfo=UTC),
            metadata={"mode": "verified"},
        ),
        parameters={"quality": 1, "detail": "minimal"},
    )

    assert first.fingerprint == second.fingerprint
    assert "created_at" not in first.to_canonical_json()


def test_capability_result_rejects_blocking_failure_as_success() -> None:
    artifact = _artifact("experiment.generic", "scientific_experiment", "experiment")
    blocked = _verification(
        artifact,
        ScientificVerificationOutcome.BLOCKED,
        findings=(_finding(),),
    )
    with pytest.raises(ValidationError, match="Blocking verification"):
        CapabilityResult(
            status=ExecutionStatus.SUCCESS,
            verification_results=(blocked,),
        )
    failed = CapabilityResult(
        status=ExecutionStatus.FAILED,
        verification_results=(blocked,),
        failure=FailureInformation(
            code="verification.blocked",
            message="Execution was blocked by verification.",
        ),
    )
    assert json.loads(failed.to_canonical_json())["status"] == "failed"


def test_verification_cannot_pass_with_blocking_finding() -> None:
    artifact = _artifact("experiment.generic", "scientific_experiment", "experiment")
    with pytest.raises(ValidationError, match="blocking finding"):
        _verification(
            artifact,
            ScientificVerificationOutcome.PASSED,
            findings=(_finding(),),
        )


def test_workflow_rejects_unknown_illegal_terminal_and_missing_evidence() -> None:
    definition = _workflow()
    draft = WorkflowState(current_phase="draft")

    assert validate_workflow_transition(definition, draft, "unknown").failures == (
        "destination_phase_unknown",
    )
    illegal = validate_workflow_transition(definition, draft, "visualized")
    assert "illegal_transition" in illegal.failures
    missing = validate_workflow_transition(definition, draft, "verified")
    assert "required_artifact_missing:scientific_experiment" in missing.failures
    assert "required_verification_missing:science.assumption_verifier" in missing.failures
    terminal = WorkflowState(
        current_phase="complete",
        authorization_status=True,
        terminal_status=WorkflowTerminalStatus.COMPLETED,
    )
    blocked = validate_workflow_transition(definition, terminal, "complete")
    assert "terminal_phase_cannot_advance" in blocked.failures


def test_workflow_completion_rejects_failed_blocking_verification() -> None:
    definition = _workflow()
    experiment = _artifact("experiment.generic", "scientific_experiment", "experiment")
    scene = _artifact("scene.generic", "molecular_scene", "scene")
    report = _artifact("verification.generic", "verification_report", "verification")
    state = WorkflowState(
        current_phase="visualized",
        produced_artifacts=(experiment, scene, report),
        verification_results=(
            _verification(
                experiment,
                ScientificVerificationOutcome.FAILED,
                findings=(_finding(),),
            ),
        ),
    )

    validation = validate_workflow_transition(definition, state, "complete")

    assert not validation.allowed
    assert "blocking_verification_failed" in validation.failures


def test_molecular_structure_requires_units_and_preserves_unknown_charge() -> None:
    artifact = _artifact("structure.generic", "molecular_structure", "coordinates")
    with pytest.raises(ValidationError):
        MolecularStructure(
            structure_artifact=artifact,
            structure_role="ligand",
            structure_format="XYZ",
            coordinate_unit="",
            atom_count=3,
            preparation_status="raw",
        )
    structure = MolecularStructure(
        structure_artifact=artifact,
        structure_role="fragment",
        structure_format="XYZ",
        coordinate_unit="angstrom",
        atom_count=3,
        preparation_status="user_supplied",
    )
    assert structure.molecular_charge is None
    assert structure.spin_multiplicity is None


def test_molecular_scene_exact_references_and_fingerprint_changes() -> None:
    structure = _artifact("structure.generic", "molecular_structure", "coordinates-v1")
    changed_structure = _artifact(
        "structure.generic", "molecular_structure", "coordinates-v2"
    )
    selection = MolecularSelection(
        selection_identifier="selection.region-a",
        structure_artifact_identifier=structure.artifact_identifier,
        atom_indices=(0, 1),
    )
    scene = MolecularScene(
        scene_identifier="scene.generic-001",
        schema_version=VERSION,
        structures=(structure,),
        representations=(
            MolecularRepresentation(
                representation_identifier="representation.primary",
                structure_artifact_identifier=structure.artifact_identifier,
                representation_type="ball_and_stick",
            ),
        ),
        selections=(selection,),
        highlighted_residues=(
            MolecularResidueReference(
                structure_artifact_identifier=structure.artifact_identifier,
                residue_identifier="residue.R1",
            ),
        ),
        quantum_region_selection_identifier=selection.selection_identifier,
        measurements=(
            MolecularMeasurement(
                measurement_identifier="measurement.distance-01",
                measurement_type="distance",
                atoms=(
                    MolecularAtomReference(
                        structure_artifact_identifier=structure.artifact_identifier,
                        atom_index=0,
                    ),
                    MolecularAtomReference(
                        structure_artifact_identifier=structure.artifact_identifier,
                        atom_index=1,
                    ),
                ),
                coordinate_unit="angstrom",
                calculated_value=1.25,
            ),
        ),
    )
    changed_scene = MolecularScene(
        **{
            **scene.model_dump(),
            "structures": (changed_structure,),
        }
    )
    alternate_selection = MolecularSelection(
        selection_identifier="selection.region-b",
        structure_artifact_identifier=structure.artifact_identifier,
        atom_indices=(1, 2),
    )
    alternate_scene = MolecularScene(
        **{
            **scene.model_dump(),
            "selections": (selection, alternate_selection),
            "quantum_region_selection_identifier": alternate_selection.selection_identifier,
        }
    )

    assert scene.structures == (structure,)
    assert scene.fingerprint != changed_scene.fingerprint
    assert scene.fingerprint != alternate_scene.fingerprint

    changed_measurement = MolecularMeasurement(
        **{
            **scene.measurements[0].model_dump(),
            "calculated_value": 1.5,
        }
    )
    measured_scene = MolecularScene(
        **{
            **scene.model_dump(),
            "measurements": (changed_measurement,),
        }
    )
    assert scene.fingerprint != measured_scene.fingerprint

    with pytest.raises(ValidationError, match="displayed structure"):
        MolecularScene(
            **{
                **scene.model_dump(),
                "representations": (
                    MolecularRepresentation(
                        representation_identifier="representation.foreign",
                        structure_artifact_identifier="structure.not-displayed",
                        representation_type="surface",
                    ),
                ),
            }
        )

    with pytest.raises(ValidationError, match="exactly two"):
        MolecularMeasurement(
            measurement_identifier="measurement.invalid",
            measurement_type="distance",
            atoms=(
                MolecularAtomReference(
                    structure_artifact_identifier=structure.artifact_identifier,
                    atom_index=0,
                ),
            ),
            coordinate_unit="angstrom",
        )


def test_generic_scientific_workflow_blocks_then_completes_with_lineage() -> None:
    request = _artifact(
        "request.generic-001",
        "natural_language_request",
        "Create a verified view of the supplied invented structure.",
    )
    structure = _artifact(
        "structure.generic-001",
        "molecular_structure",
        "invented Cartesian coordinates with no scientific claim",
    )
    unresolved = ScientificAssumption(
        assumption_identifier="assumption.geometry_approval",
        description="Confirm that the supplied invented geometry may be used.",
        source=AssumptionSource.UNRESOLVED,
        approval_status=ApprovalStatus.PENDING,
        supporting_artifact=structure.pointer,
        blocks_execution_until_approved=True,
    )
    draft_experiment = _experiment(structure, unresolved)
    draft_experiment_artifact = _artifact(
        "experiment.generic-001",
        "scientific_experiment",
        draft_experiment.to_canonical_json(),
    )
    blocked_verification = _verification(
        draft_experiment_artifact,
        ScientificVerificationOutcome.BLOCKED,
        findings=(_finding(),),
    )
    definition = _workflow()
    blocked_state = WorkflowState(
        current_phase="draft",
        produced_artifacts=(request, structure, draft_experiment_artifact),
        verification_results=(blocked_verification,),
    )
    rejected = validate_workflow_transition(definition, blocked_state, "verified")
    assert not rejected.allowed
    assert "required_verification_missing:science.assumption_verifier" in rejected.failures

    approved_experiment = _experiment(structure, unresolved.approve())
    approved_experiment_artifact = _artifact(
        "experiment.generic-001",
        "scientific_experiment",
        approved_experiment.to_canonical_json(),
    )
    passed_verification = _verification(
        approved_experiment_artifact,
        ScientificVerificationOutcome.PASSED,
    )
    verification_artifact = _artifact(
        "verification.generic-001",
        "verification_report",
        passed_verification.to_canonical_json(),
    )
    ready = WorkflowState(
        current_phase="draft",
        produced_artifacts=(
            request,
            structure,
            approved_experiment_artifact,
            verification_artifact,
        ),
        verification_results=(passed_verification,),
    )
    verified = transition_workflow(definition, ready, "verified")

    region = MolecularSelection(
        selection_identifier="selection.quantum-region",
        structure_artifact_identifier=structure.artifact_identifier,
        atom_indices=(0, 1),
    )
    scene = MolecularScene(
        scene_identifier="scene.generic-001",
        schema_version=VERSION,
        structures=(structure,),
        representations=(
            MolecularRepresentation(
                representation_identifier="representation.primary",
                structure_artifact_identifier=structure.artifact_identifier,
                representation_type="ball_and_stick",
            ),
        ),
        selections=(region,),
        quantum_region_selection_identifier=region.selection_identifier,
    )
    scene_artifact = _artifact(
        "scene.generic-001",
        "molecular_scene",
        scene.to_canonical_json(),
    )
    visualizable = WorkflowState(
        **{
            **verified.model_dump(),
            "produced_artifacts": (*verified.produced_artifacts, scene_artifact),
        }
    )
    visualized = transition_workflow(definition, visualizable, "visualized")
    completed = transition_workflow(definition, visualized, "complete")

    edges = (
        ArtifactLineageEdge(
            source=request.pointer,
            destination=approved_experiment_artifact.pointer,
            relationship_type="generated_from",
            producing_capability="science.experiment_drafting",
            producing_capability_version=VERSION,
        ),
        ArtifactLineageEdge(
            source=structure.pointer,
            destination=approved_experiment_artifact.pointer,
            relationship_type="derived_from",
            producing_capability="science.experiment_drafting",
            producing_capability_version=VERSION,
        ),
        ArtifactLineageEdge(
            source=approved_experiment_artifact.pointer,
            destination=verification_artifact.pointer,
            relationship_type="verifies",
            producing_capability="science.assumption_verifier",
            producing_capability_version=VERSION,
        ),
        ArtifactLineageEdge(
            source=structure.pointer,
            destination=scene_artifact.pointer,
            relationship_type="visualizes",
            producing_capability="science.scene_generation",
            producing_capability_version=VERSION,
            verification_evidence=(verification_artifact.pointer,),
        ),
    )
    graph = ArtifactLineageGraph(edges=edges)

    assert approved_experiment.execution_ready
    assert scene.structures[0].content_sha256 == structure.content_sha256
    assert completed.authorization_status is True
    assert completed.terminal_status == WorkflowTerminalStatus.COMPLETED
    assert len(completed.transition_history) == 3
    assert len(graph.edges) == 4
    assert {
        pointer.artifact_identifier
        for edge in graph.edges
        for pointer in (edge.source, edge.destination)
    } == {
        request.artifact_identifier,
        structure.artifact_identifier,
        approved_experiment_artifact.artifact_identifier,
        verification_artifact.artifact_identifier,
        scene_artifact.artifact_identifier,
    }


def test_science_import_is_independent_and_quixbugs_does_not_import_it() -> None:
    root = Path(__file__).resolve().parents[1]
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import cgr.quixbugs_pilot; "
            "print('cgr.science' in sys.modules)",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert process.returncode == 0, process.stderr
    assert process.stdout.strip() == "False"

    imports = subprocess.run(
        [
            sys.executable,
            "-c",
            "import cgr.science; import cgr.kernel.coding; print('ok')",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert imports.returncode == 0, imports.stderr
    assert imports.stdout.strip() == "ok"


def test_no_chemistry_or_quantum_dependency_was_added() -> None:
    root = Path(__file__).resolve().parents[1]
    project = (root / "pyproject.toml").read_text(encoding="utf-8").lower()
    for dependency in ("qiskit", "rdkit", "pyscf", "openmm", "molstar", "three"):
        assert dependency not in project
