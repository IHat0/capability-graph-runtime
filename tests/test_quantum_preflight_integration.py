from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from cgr.quantum_preflight.environment import require_dependencies
from cgr.quantum_preflight.acceptance import run_acceptance
from cgr.quantum_preflight.manifests import load_manifest

ROOT = Path(__file__).resolve().parents[1]
ENABLED = os.environ.get("CGR_QUANTUM_INTEGRATION") == "1"

pytestmark = [
    pytest.mark.quantum_integration,
    pytest.mark.skipif(not ENABLED, reason="requires explicit pinned Linux container run"),
]


def test_real_lih_reference_mutation_and_determinism(tmp_path: Path) -> None:
    assert sys.platform == "linux"
    require_dependencies()  # Missing packages are a hard failure when enabled.
    manifest = load_manifest(ROOT / "benchmark-manifests/quantum-preflight/lih-ground-state-v1.json")
    lock = ROOT / "requirements/quantum-preflight.lock"
    image_id = os.environ.get("CGR_QUANTUM_IMAGE_ID")
    assert image_id, "CGR_QUANTUM_IMAGE_ID is required when integration is enabled"
    summary = run_acceptance(
        manifest,
        result_root=tmp_path,
        lock_path=lock,
        image_identifier=image_id,
    )
    assert summary["authorized"] is True
    assert summary["acceptance_passed"] is True
    assert Path(summary["acceptance_report_path"]).is_file()
