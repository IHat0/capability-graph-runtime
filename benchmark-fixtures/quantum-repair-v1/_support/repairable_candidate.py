"""Benchmark-only declarative defect adapter; never registered as product repair logic."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import standalone_candidate


def _mode(config: dict[str, Any], experiment: dict[str, Any]) -> str:
    molecule = experiment["molecular_system"]
    electronic = experiment["electronic_structure"]
    quantum = experiment["quantum_model"]
    ordered = (
        (config["authorized_claim"], "claims-authorization"),
        (config["no_output"], "no-output"),
        (config["malformed_output"], "malformed-output"),
        (config["bare_fabricated_energy"], "bare-fabricated-energy"),
        (config["coordinate_unit"] != molecule["coordinate_unit"], "angstrom-bohr-confusion"),
        (config["bond_distance"] != molecule["declared_bond_distance"], "wrong-bond-distance"),
        (config["molecular_charge"] != molecule["molecular_charge"], "wrong-charge"),
        (config["spin_multiplicity"] != molecule["spin_multiplicity"], "wrong-multiplicity"),
        (config["basis_set"] != electronic["basis_set"], "wrong-basis"),
        (
            config["active_electron_count"] != electronic["active_electron_count"]
            or config["active_spatial_orbital_count"]
            != electronic["active_spatial_orbital_count"],
            "wrong-active-space",
        ),
        (config["mapper"] != quantum["mapper"], "wrong-mapper"),
        (config["hamiltonian_tamper"], "wrong-hamiltonian"),
        (config["total_energy_semantics"] != "molecular_total", "electronic-energy-as-total"),
        (not config["include_nuclear_repulsion"], "missing-nuclear-repulsion"),
        (not config["vqe_converged"], "nonconverged-vqe"),
        (config["energy_offset"] != 0.0, "energy-disagreement"),
        (config["forged_content_hash"], "forged-content-hash"),
        (config["forged_scientific_identity"], "forged-scientific-identity"),
        (config["cross_linked_artifacts"], "cross-linked-artifacts"),
    )
    return next((mode for enabled, mode in ordered if enabled), "standalone-qiskit-candidate")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads((Path(__file__).parent / "repair-config.json").read_text())
    raw = args.input.read_bytes()
    public_document = json.loads(raw)
    args.output.mkdir(parents=True, exist_ok=True)
    if config["no_output"]:
        return
    if config["malformed_output"]:
        (args.output / "candidate-summary.json").write_text("{not-json")
        return
    if config["output_path_escape"]:
        os.symlink("/etc/passwd", args.output / "escaped-evidence")
        return
    standalone_candidate.emit(
        _mode(config, public_document["experiment"]),
        config["candidate_identifier"],
        hashlib.sha256(raw).hexdigest(),
        public_document["experiment"],
        args.output,
    )
