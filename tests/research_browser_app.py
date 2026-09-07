"""Loopback acceptance app: real scientific handlers, controlled semantic fixture.

The fixture supplies only the analysis intent; all structure facts, calculations,
verification, scenes and persistence run through the production implementation.
Never use this test authenticator for deployment.
"""
import os
from pathlib import Path

from cgr.pulsate_api.app import create_app, _load_preset
from cgr.pulsate_api.experiments import ExperimentStore
from cgr.pulsate_api.natural_language import NaturalLanguageInterpretationStore
from cgr.pulsate_api.research_sessions import ResearchSessionRepository
from cgr.pulsate_api.runs import RunCoordinator
from cgr.pulsate_api.scientific_executions import ScientificExecutionRepository
from cgr.pulsate_api.scientific_production import create_scientific_production_composition
from cgr.pulsate_api.security import (
    SecurityServices, StaticAuthenticator, InMemoryAuditSink, InMemoryGrantProvider,
)
from cgr.pulsate_api.security_contracts import AuthenticatedPrincipal, ProtectedAction, ResourceGrant
from cgr.pulsate_api.workflows import WorkflowService
from cgr.workflow_graph import CapabilityAdapterRegistry
import json


class AnalysisIntentFixture:
    provider_kind = "acceptance_semantic_fixture"
    model_name = "analysis-intent-only"

    def complete(self, messages):
        if "Translate the scientist request" in messages[0]["content"]:
            text = messages[-1]["content"]
            return json.dumps({"requirements": [{
                "operation": "analyze_structure",
                "requested_output": "structure_analysis",
                "supporting_quote": text,
            }]})
        return "{}"


class NoPresetExecution:
    def execute(self, *args, **kwargs):
        raise AssertionError("This acceptance app executes only research sessions.")


def build_app():
    root = Path(os.environ["PULSATE_BROWSER_ACCEPTANCE_ROOT"]).resolve()
    token = os.environ["PULSATE_BROWSER_ACCEPTANCE_TOKEN"]
    principal = AuthenticatedPrincipal(
        subject_identifier="browser-scientist", tenant_identifier="browser-acceptance",
        authentication_method="acceptance-bearer",
        scopes=tuple(action.value for action in ProtectedAction),
        audit_identifier="browser-acceptance",
    )
    repository = ScientificExecutionRepository(root / "executions")
    composition = create_scientific_production_composition(
        application_data_root=root, execution_repository=repository,
    )
    return create_app(
        coordinator=RunCoordinator(
            run_root=root / "runs", manifest_resolver=_load_preset,
            executor=NoPresetExecution(), enabled=False,
        ),
        experiment_store=ExperimentStore(root / "experiments"),
        natural_language_store=NaturalLanguageInterpretationStore(
            root / "interpretations", AnalysisIntentFixture(),
        ),
        workflow_service=WorkflowService(
            root=root / "workflows", capability_adapter=CapabilityAdapterRegistry(),
            maximum_parallelism=1,
        ),
        scientific_execution_repository=repository,
        scientific_objective_runtime=composition.runtime,
        scientific_payload_store=composition.payload_store,
        research_session_repository=ResearchSessionRepository(root / "sessions"),
        security_services=SecurityServices(
            authenticator=StaticAuthenticator({token: principal}),
            grant_provider=InMemoryGrantProvider((ResourceGrant(
                tenant_identifier=principal.tenant_identifier,
                subject_identifier=principal.subject_identifier,
                resource_type="scientific-execution", resource_identifier="*",
                allowed_actions=tuple(ProtectedAction),
            ),), allow_wildcards=True),
            audit_sink=InMemoryAuditSink(),
        ),
    )
