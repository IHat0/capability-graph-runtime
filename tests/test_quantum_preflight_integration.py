from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from cgr.quantum_preflight.environment import require_dependencies
from cgr.quantum_preflight.manifests import load_manifest, with_bond_distance
from cgr.quantum_preflight.runner import run_trusted_reference

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
    image_id = os.environ["CGR_QUANTUM_IMAGE_ID"]
    first = run_trusted_reference(manifest, result_root=tmp_path, lock_path=lock, image_identifier=image_id)
    second = run_trusted_reference(manifest, result_root=tmp_path, lock_path=lock, image_identifier=image_id)
    mutated = run_trusted_reference(
        with_bond_distance(manifest, 1.7), result_root=tmp_path, lock_path=lock, image_identifier=image_id
    )
    assert first["authorized"] and second["authorized"] and mutated["authorized"]
    for field in ("experiment_sha256", "structure_sha256", "qcschema_sha256", "fermionic_hamiltonian_sha256", "qubit_hamiltonian_sha256"):
        assert first[field] == second[field]
        assert first[field] != mutated[field]
    assert abs(first["exact_total_energy_hartree"] - second["exact_total_energy_hartree"]) <= 1e-12
    assert abs(first["vqe_total_energy_hartree"] - second["vqe_total_energy_hartree"]) <= manifest.experiment.verification_policy.energy_difference_tolerance_hartree
    assert abs(first["exact_total_energy_hartree"] - mutated["exact_total_energy_hartree"]) > 1e-10
    receipt = json.loads(Path(first["receipt_path"]).read_text(encoding="utf-8"))
    assert receipt["payload"]["authorized"] is True
