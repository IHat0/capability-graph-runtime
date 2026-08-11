"""Cross-phase evidence from enterprise ingress through scientific execution."""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from cgr.kernel.contracts import CapabilityVersion
from cgr.pulsate_api.app import _load_preset, create_app
from cgr.pulsate_api.deployment import validate_application_data_layout
from cgr.pulsate_api.experiments import ExperimentStore
from cgr.pulsate_api.natural_language import NaturalLanguageInterpretationStore
from cgr.pulsate_api.runs import RunCoordinator
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
from cgr.pulsate_api.scientific_executions import (
    ScientificExecutionRepository,
    ScientificObjectiveCompileRequest,
)
from cgr.pulsate_api.scientific_objectives import (
    CovalentReactionTarget,
    ScientificExecutionBudget,
    ScientificInputReference,
    compile_scientific_objective,
    plan_scientific_objective,
)
from cgr.pulsate_api.scientific_production import (
    DurableScientificPayloadStore,
    create_scientific_production_composition,
)
from cgr.pulsate_api.scientific_runtime import (
    ScientificCapabilityOutcome,
    ScientificObjectiveRuntime,
    ScientistCapabilityRegistry,
    scientific_plan_graph,
)
from cgr.pulsate_api import scientific_cli
from cgr.pulsate_api.workflows import WorkflowService
from cgr.science import ArtifactReference, CreationProvenance
from cgr.workflow_graph import CapabilityAdapterRegistry, FailureAction


def _covalent_target() -> CovalentReactionTarget:
    return CovalentReactionTarget(
        protein_chain_label="A",
        protein_residue_sequence="1",
        protein_residue_name="CYS",
        protein_atom_name="SG",
        protein_nucleophile_formal_charge=-1,
        ligand_reaction_smarts="[C:1]-[S:2]",
        selected_total_qm_charge=-1,
        selected_spin=0,
    )


class _NoExecution:
    def execute(self, *_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("Cross-phase science must not use the preset runner.")


class _PersistedEvidenceHandler:
    def __init__(self, store: DurableScientificPayloadStore) -> None:
        self.store = store

    def execute(self, *, invocation, objective, record) -> ScientificCapabilityOutcome:
        del objective, record
        payload = invocation.capability_identity.encode("utf-8")
        digest = hashlib.sha256(payload + invocation.node_identifier.encode()).hexdigest()
        reference = ArtifactReference(
            artifact_identifier=f"cross-phase-evidence-{digest[:32]}",
            schema_version=CapabilityVersion(major=1, minor=0, patch=0),
            artifact_type="scientific_execution_evidence",
            media_type="application/json",
            content_sha256=hashlib.sha256(payload).hexdigest(),
            byte_size=len(payload),
            provenance=CreationProvenance(
                producer=invocation.capability_identity,
                producer_version=CapabilityVersion(major=1, minor=0, patch=0),
                execution_identifier=invocation.invocation_identifier,
            ),
        )
        self.store.write(reference, payload)
        verification = invocation.capability_identity.startswith(
            "scientific_verification."
        )
        return ScientificCapabilityOutcome(
            output_artifacts=(reference,),
            evidence_artifacts=((reference,) if verification else ()),
            verified=(True if verification else None),
            scientific_summary=(
                "Cross-phase scientific execution completed with persisted evidence."
                if verification
                else None
            ),
            scene_identifier=(
                "cross-phase-scene"
                if invocation.capability_identity == "molecular.scene_project"
                else None
            ),
        )


def _input_reference(artifact_type: str, position: int) -> ScientificInputReference:
    return ScientificInputReference(
        reference_identifier=f"input-{position:02d}-{artifact_type.replace('_', '-')}",
        artifact_type=artifact_type,  # type: ignore[arg-type]
        artifact_identifier=f"artifact-{position:02d}-{artifact_type.replace('_', '-')}",
    )


def test_production_composition_routes_every_phase8_plan_and_declares_recovery(
    tmp_path: Path,
) -> None:
    root = tmp_path / "data"
    root.mkdir()
    validate_application_data_layout(root)
    repository = ScientificExecutionRepository(root / "scientific-executions")
    repository.start()
    composition = create_scientific_production_composition(
        application_data_root=root,
        execution_repository=repository,
    )
    composition.start()
    try:
        cases = (
            (
                "Which conformer is preferred in water: axial or equatorial?",
                (_input_reference("molecular_structure", 1),),
            ),
            (
                "Run a 15-point bond dissociation scan from 1.2 to 2.8 angstroms.",
                (_input_reference("molecular_structure", 1),),
            ),
            (
                "Analyze the metal active site with a local quantum simulator.",
                (_input_reference("protein_structure", 1),),
            ),
            (
                "Find the covalent transition state for this protein ligand reaction.",
                (
                    _input_reference("protein_structure", 1),
                    _input_reference("ligand_structure", 2),
                ),
            ),
            (
                "Discover a drug candidate for this protein binding pocket.",
                (_input_reference("protein_structure", 1),),
            ),
        )
        for question, inputs in cases:
            objective = compile_scientific_objective(
                question,
                input_references=inputs,
                covalent_reaction_target=(
                    _covalent_target()
                    if "covalent transition state" in question
                    else None
                ),
            )
            plan = plan_scientific_objective(objective)
            assert plan.executable
            assert all(
                composition.runtime.capability_registry.get(step.capability_name)
                is not None
                for step in plan.steps
                if not step.authorization_required
            )

        objective = compile_scientific_objective(
            cases[0][0],
            input_references=cases[0][1],
            budget=ScientificExecutionBudget(maximum_replans=2),
        )
        record = repository.create(
            ScientificObjectiveCompileRequest(
                question=objective.original_request,
                input_references=cases[0][1],
                budget=objective.budget,
            )
        )
        graph = scientific_plan_graph(record)
        assert all(node.retry_policy is not None for node in graph.nodes)
        assert all(node.retry_policy.max_attempts == 3 for node in graph.nodes)  # type: ignore[union-attr]
        assert all(
            node.failure_recovery_policy is not None
            and node.failure_recovery_policy.on_failure is FailureAction.RETRY
            for node in graph.nodes
        )
    finally:
        composition.close()
        repository.close()


def test_authenticated_api_persists_input_and_runs_phase1_to_phase8_graph(
    tmp_path: Path,
) -> None:
    execution_repository = ScientificExecutionRepository(tmp_path / "scientific-executions")
    payload_store = DurableScientificPayloadStore(tmp_path / "scientific-artifacts")
    semantic_input = _input_reference("molecular_structure", 1)
    objective = compile_scientific_objective(
        "Which conformer is preferred in water: axial or equatorial?",
        input_references=(semantic_input,),
    )
    plan = plan_scientific_objective(objective)
    handler = _PersistedEvidenceHandler(payload_store)
    registry = ScientistCapabilityRegistry(
        {
            step.capability_name: handler
            for step in plan.steps
            if step.capability_name != "scientist.result_assemble"
        }
    )
    runtime = ScientificObjectiveRuntime(
        root=tmp_path / "scientific-workflows",
        execution_repository=execution_repository,
        capability_registry=registry,
    )
    coordinator = RunCoordinator(
        run_root=tmp_path / "runs",
        manifest_resolver=_load_preset,
        executor=_NoExecution(),
        enabled=False,
    )
    principals = {
        token: AuthenticatedPrincipal(
            subject_identifier=f"scientist-{tenant}",
            tenant_identifier=tenant,
            authentication_method="test-bearer",
            scopes=(
                ProtectedAction.EXECUTION_REQUEST.value,
                ProtectedAction.PLANNING_EVALUATE.value,
                ProtectedAction.RUN_READ.value,
            ),
            audit_identifier=f"scientist-{tenant}",
        )
        for token, tenant in (("alpha-token", "tenant-alpha"), ("beta-token", "tenant-beta"))
    }
    grants = tuple(
        ResourceGrant(
            tenant_identifier=principal.tenant_identifier,
            subject_identifier=principal.subject_identifier,
            resource_type="scientific-execution",
            resource_identifier="*",
            allowed_actions=(
                ProtectedAction.EXECUTION_REQUEST,
                ProtectedAction.PLANNING_EVALUATE,
                ProtectedAction.RUN_READ,
            ),
        )
        for principal in principals.values()
    )
    security = SecurityServices(
        authenticator=StaticAuthenticator(principals),
        grant_provider=InMemoryGrantProvider(grants, allow_wildcards=True),
        audit_sink=InMemoryAuditSink(),
    )
    application = create_app(
        coordinator=coordinator,
        experiment_store=ExperimentStore(tmp_path / "experiments"),
        natural_language_store=NaturalLanguageInterpretationStore(
            tmp_path / "interpretations",
            None,
            unavailable_reason="not needed for the cross-phase gate",
        ),
        workflow_service=WorkflowService(
            root=tmp_path / "workflows",
            capability_adapter=CapabilityAdapterRegistry(),
            maximum_parallelism=1,
        ),
        scientific_execution_repository=execution_repository,
        scientific_objective_runtime=runtime,
        scientific_payload_store=payload_store,
        security_services=security,
    )
    source_payload = b"bounded cross-phase molecular input"
    source_digest = hashlib.sha256(source_payload).hexdigest()
    source = ArtifactReference(
        artifact_identifier=semantic_input.artifact_identifier,
        schema_version=CapabilityVersion(major=1, minor=0, patch=0),
        artifact_type="molecular_structure",
        media_type="chemical/x-mdl-molfile",
        content_sha256=source_digest,
        byte_size=len(source_payload),
        provenance=CreationProvenance(
            producer="cross-phase-test",
            producer_version=CapabilityVersion(major=1, minor=0, patch=0),
        ),
    )
    with TestClient(application) as client:
        alpha = {"Authorization": "Bearer alpha-token"}
        beta = {"Authorization": "Bearer beta-token"}
        uploaded = client.post(
            "/api/v1/scientific/artifacts",
            json={
                "reference": source.model_dump(mode="json"),
                "payload_base64": base64.b64encode(source_payload).decode("ascii"),
            },
            headers=alpha,
        )
        assert uploaded.status_code == 201
        owned_source = ArtifactReference.model_validate(uploaded.json())
        owned_semantic_input = semantic_input.model_copy(
            update={"artifact_identifier": owned_source.artifact_identifier}
        )
        compiled = client.post(
            "/api/v1/scientific/objectives/compile",
            json={
                "question": objective.original_request,
                "input_references": [owned_semantic_input.model_dump(mode="json")],
                "artifact_references": [owned_source.model_dump(mode="json")],
            },
            headers=alpha,
        )
        assert compiled.status_code == 201, compiled.text
        execution_identifier = compiled.json()["execution_identifier"]
        completed = client.post(
            f"/api/v1/scientific/executions/{execution_identifier}/execute",
            json={},
            headers=alpha,
        )
        assert completed.status_code == 200
        result = completed.json()
        assert result["status"] == "succeeded"
        assert result["verified"] is True
        assert result["workflow_snapshot_fingerprint"]
        assert result["scientist_result"]["verification_status"] == "passed"
        assert application.state.security_services.audit_sink.records
        assert client.get(
            f"/api/v1/scientific/executions/{execution_identifier}",
            headers=beta,
        ).status_code == 404
        assert client.post(
            "/api/v1/scientific/objectives/compile",
            json={
                "question": objective.original_request,
                "input_references": [owned_semantic_input.model_dump(mode="json")],
                "artifact_references": [owned_source.model_dump(mode="json")],
            },
            headers=beta,
        ).status_code == 422

    payload_store.start()
    try:
        assert payload_store.read(owned_source) == source_payload
    finally:
        payload_store.close()


def test_terminal_client_uses_upload_compile_execute_api_sequence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    molecule = tmp_path / "molecule.mol"
    molecule.write_bytes(b"terminal-client-molecule")
    monkeypatch.setenv("PULSATE_ACCESS_TOKEN", "bounded-test-token")
    routes: list[str] = []

    def post(
        _base_url: str,
        route: str,
        _token: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        routes.append(route)
        if route == "/api/v1/scientific/artifacts":
            reference = ArtifactReference.model_validate(payload["reference"])
            return reference.model_copy(
                update={
                    "artifact_identifier": "scientific-input-owned-terminal",
                    "metadata": {"tenant_identifier_sha256": "a" * 64},
                }
            ).model_dump(mode="json")
        if route == "/api/v1/scientific/objectives/compile":
            return {
                "status": "planned",
                "execution_identifier": "scientific-execution-" + ("b" * 32),
            }
        return {"status": "succeeded", "verified": True}

    monkeypatch.setattr(scientific_cli, "_post", post)
    assert scientific_cli.scientist_main(
        [
            "Which conformer is preferred in water?",
            "--input",
            f"molecular_structure={molecule}",
        ]
    ) == 0
    assert routes == [
        "/api/v1/scientific/artifacts",
        "/api/v1/scientific/objectives/compile",
        "/api/v1/scientific/executions/scientific-execution-"
        + ("b" * 32)
        + "/execute",
    ]
    assert '"status": "succeeded"' in capsys.readouterr().out
