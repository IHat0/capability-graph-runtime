"""Repository-wide test classifications with explicit release-gate membership."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient


TEST_DEVELOPMENT_BEARER = "pulsate-test-development-bearer"
os.environ["PULSATE_ENVIRONMENT"] = "development"
os.environ["PULSATE_DEVELOPMENT_AUTH_ENABLED"] = "true"
os.environ["PULSATE_DEVELOPMENT_AUTH_TOKEN"] = TEST_DEVELOPMENT_BEARER
os.environ["PULSATE_DEVELOPMENT_SUBJECT"] = "test-user"
os.environ["PULSATE_DEVELOPMENT_TENANT"] = "test-tenant"
os.environ["PULSATE_DEVELOPMENT_AUDIT_ID"] = "test-user"
os.environ["PULSATE_DEVELOPMENT_AUTH_SCOPES"] = ",".join(
    (
        "artifact.read",
        "execution.approve",
        "execution.request",
        "experiment.read",
        "planning.evaluate",
        "project.read",
        "run.read",
        "scene.read",
        "workflow.read",
        "workflow.create",
        "workflow.execute",
        "workflow.approve",
        "workflow.cancel",
    )
)

_test_client_init = TestClient.__init__


def _authenticated_test_client_init(
    self: TestClient, *args: Any, **kwargs: Any
) -> None:
    """Give legacy API tests an explicit development bearer credential."""

    headers = dict(kwargs.pop("headers", {}) or {})
    headers.setdefault("Authorization", f"Bearer {TEST_DEVELOPMENT_BEARER}")
    _test_client_init(self, *args, headers=headers, **kwargs)


TestClient.__init__ = _authenticated_test_client_init  # type: ignore[method-assign]


PHASE_1_4_BACKEND_CORE_FILES = frozenset(
    {
        "test_molecular_artifact_repository.py",
        "test_molecular_contracts.py",
        "test_molecular_ingestion.py",
        "test_molecular_planning.py",
        "test_molecular_project_repository.py",
        "test_molecular_scene_projection.py",
        "test_cross_phase_scientific_integration.py",
        "test_pulsate_api.py",
        "test_pulsate_experiments.py",
        "test_pulsate_deployment.py",
        "test_pulsate_enterprise_gate.py",
        "test_pulsate_molecular_planning.py",
        "test_pulsate_molecular_scenes.py",
        "test_pulsate_production_catalogue.py",
        "test_pulsate_runs.py",
        "test_scientific_capabilities.py",
        "test_scientific_feasibility.py",
        "test_scientific_foundation.py",
        "test_scientific_planning.py",
        "test_scientific_resources.py",
        "test_pulsate_security.py",
        "test_pulsate_production_security.py",
        "test_pulsate_recovery.py",
        "test_workflow_graph_foundation.py",
        "test_workflow_graph_runtime.py",
        "test_workflow_quantum_adapter.py",
        "test_workflow_api.py",
    }
)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Apply core markers only to the explicit Phase 1-4 file manifest."""

    for item in items:
        if Path(str(item.path)).name in PHASE_1_4_BACKEND_CORE_FILES:
            item.add_marker(pytest.mark.core)
            item.add_marker(pytest.mark.backend_core)
