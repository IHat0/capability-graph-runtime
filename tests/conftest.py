"""Repository-wide test classifications with explicit release-gate membership."""

from __future__ import annotations

from pathlib import Path

import pytest


PHASE_1_3_BACKEND_CORE_FILES = frozenset(
    {
        "test_molecular_artifact_repository.py",
        "test_molecular_contracts.py",
        "test_molecular_ingestion.py",
        "test_molecular_planning.py",
        "test_molecular_project_repository.py",
        "test_molecular_scene_projection.py",
        "test_pulsate_api.py",
        "test_pulsate_experiments.py",
        "test_pulsate_molecular_planning.py",
        "test_pulsate_molecular_scenes.py",
        "test_pulsate_runs.py",
        "test_scientific_capabilities.py",
        "test_scientific_feasibility.py",
        "test_scientific_foundation.py",
        "test_scientific_planning.py",
        "test_scientific_resources.py",
    }
)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Apply core markers only to the explicit Phase 1-3 file manifest."""

    for item in items:
        if Path(str(item.path)).name in PHASE_1_3_BACKEND_CORE_FILES:
            item.add_marker(pytest.mark.core)
            item.add_marker(pytest.mark.backend_core)
