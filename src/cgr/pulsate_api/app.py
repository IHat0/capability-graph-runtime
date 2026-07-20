"""FastAPI surface for the Pulsate Labs scientific workspace."""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException

from cgr.quantum_preflight.contracts import ManifestEnvelope
from cgr.quantum_preflight.manifests import load_manifest

REPO_ROOT = Path(__file__).resolve().parents[3]
MANIFEST_ROOT = REPO_ROOT / "benchmark-manifests" / "quantum-preflight"

_PRESET_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{0,126}$")

app = FastAPI(
    title="Pulsate Labs API",
    version="0.1.0",
    description="Verified small-molecule quantum-science product API.",
)


def _manifest_paths() -> tuple[Path, ...]:
    """Discover every declared quantum-preflight preset."""

    return tuple(sorted(MANIFEST_ROOT.glob("*.json")))


def _resolve_manifest(preset_identifier: str) -> Path:
    """Resolve one preset without permitting path traversal."""

    if not _PRESET_IDENTIFIER.fullmatch(preset_identifier):
        raise HTTPException(status_code=404, detail="Experiment preset not found.")

    path = MANIFEST_ROOT / f"{preset_identifier}.json"

    if not path.is_file() or path.resolve().parent != MANIFEST_ROOT.resolve():
        raise HTTPException(status_code=404, detail="Experiment preset not found.")

    return path


def _load_preset(preset_identifier: str) -> ManifestEnvelope:
    return load_manifest(_resolve_manifest(preset_identifier))


def _preset_summary(path: Path) -> dict[str, Any]:
    manifest = load_manifest(path)
    experiment = manifest.experiment
    molecule = experiment.molecular_system

    return {
        "preset_identifier": path.stem,
        "experiment_identifier": experiment.experiment_identifier,
        "elements": [atom.element for atom in molecule.atoms],
        "atom_count": len(molecule.atoms),
        "coordinate_unit": molecule.coordinate_unit,
        "declared_bond_distance": molecule.declared_bond_distance,
        "molecular_charge": molecule.molecular_charge,
        "spin_multiplicity": molecule.spin_multiplicity,
        "basis_set": experiment.electronic_structure.basis_set,
        "experiment_fingerprint": experiment.fingerprint,
    }


def _declared_scene(manifest: ManifestEnvelope) -> dict[str, Any]:
    """Create a viewer payload directly from the declared experiment."""

    experiment = manifest.experiment
    molecule = experiment.molecular_system
    atoms = [
        {
            "atom_identifier": atom.atom_identifier,
            "element": atom.element,
            "coordinates": list(atom.coordinates),
        }
        for atom in molecule.atoms
    ]

    first, second = molecule.atoms
    derived_distance = math.dist(first.coordinates, second.coordinates)

    return {
        "scene_identifier": f"scene.{experiment.experiment_identifier}",
        "scene_stage": "declared",
        "experiment_identifier": experiment.experiment_identifier,
        "experiment_fingerprint": experiment.fingerprint,
        "coordinate_unit": molecule.coordinate_unit,
        "atoms": atoms,
        "bonds": [
            {
                "bond_identifier": "bond.0-1",
                "atom_identifiers": [
                    first.atom_identifier,
                    second.atom_identifier,
                ],
                "declared_distance": molecule.declared_bond_distance,
                "derived_distance": derived_distance,
            }
        ],
        "quantum_region": {
            "selection_identifier": "selection.full-diatomic-system",
            "atom_identifiers": [
                atom.atom_identifier
                for atom in molecule.atoms
            ],
        },
        "scientific_model": {
            "charge": molecule.molecular_charge,
            "spin_multiplicity": molecule.spin_multiplicity,
            "basis_set": experiment.electronic_structure.basis_set,
            "reference_method": experiment.electronic_structure.reference_method,
            "active_electron_count": (
                experiment.electronic_structure.active_electron_count
            ),
            "active_spatial_orbital_count": (
                experiment.electronic_structure.active_spatial_orbital_count
            ),
            "mapper": experiment.quantum_model.mapper,
            "ansatz": experiment.quantum_model.ansatz,
        },
    }


@app.get("/api/v1/health")
def health() -> dict[str, str]:
    return {
        "service": "pulsate-api",
        "status": "healthy",
        "version": "0.1.0",
    }


@app.get("/api/v1/experiments/presets")
def list_presets() -> dict[str, Any]:
    presets = [_preset_summary(path) for path in _manifest_paths()]

    return {
        "presets": presets,
        "count": len(presets),
    }


@app.get("/api/v1/experiments/presets/{preset_identifier}")
def read_preset(preset_identifier: str) -> dict[str, Any]:
    manifest = _load_preset(preset_identifier)

    return {
        "preset_identifier": preset_identifier,
        "manifest": manifest.model_dump(mode="json"),
    }


@app.get("/api/v1/experiments/presets/{preset_identifier}/scene")
def read_preset_scene(preset_identifier: str) -> dict[str, Any]:
    manifest = _load_preset(preset_identifier)
    return _declared_scene(manifest)
