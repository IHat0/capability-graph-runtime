"""Loading and deterministic mutation of trusted preflight manifests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .contracts import ManifestEnvelope
from .errors import QuantumManifestError


def load_manifest(path: Path) -> ManifestEnvelope:
    """Load a strict manifest without importing scientific dependencies."""
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
        return ManifestEnvelope.model_validate(raw)
    except (OSError, json.JSONDecodeError, ValidationError, ValueError) as exc:
        raise QuantumManifestError(f"Invalid quantum preflight manifest: {exc}") from exc


def with_bond_distance(
    manifest: ManifestEnvelope, distance_angstrom: float
) -> ManifestEnvelope:
    """Create the runtime-only LiH mutation used to prove input sensitivity."""
    if manifest.experiment.molecular_system.coordinate_unit != "angstrom":
        raise QuantumManifestError("The LiH mutation helper requires angstrom coordinates.")
    atoms = list(manifest.experiment.molecular_system.atoms)
    atoms[1] = atoms[1].model_copy(update={"coordinates": (0.0, 0.0, distance_angstrom)})
    molecule = manifest.experiment.molecular_system.model_copy(
        update={"atoms": tuple(atoms), "declared_bond_distance": distance_angstrom}
    )
    experiment = manifest.experiment.model_copy(update={"molecular_system": molecule})
    return ManifestEnvelope(
        manifest_schema=manifest.manifest_schema,
        experiment=experiment,
        expected_experiment_sha256=None,
    )
