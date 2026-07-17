"""Reviewed deterministic provider and source materializer for repair acceptance only."""

from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path
from typing import Any

from .contracts import (
    ProviderCapability,
    QuantumRepairDirective,
    QuantumRepairPatch,
    SourceManifest,
    StructuredEdit,
)
from .patches import create_patch

_STANDARD_MAIN = """from repairable_candidate import main

if __name__ == \"__main__\":
    main()
"""

_MAIN_DEFECTS = {
    "candidate_syntax_error": 'from repairable_candidate import main\n\nif __name__ == "__main__":\n    main(\n',
    "candidate_import_error": "import deliberately_missing_quantum_package\n\n"
    + _STANDARD_MAIN,
    "candidate_runtime_error": 'raise RuntimeError("deliberate repair benchmark failure")\n\n'
    + _STANDARD_MAIN,
    "candidate_timeout": "while True:\n    pass\n\n" + _STANDARD_MAIN,
    "candidate_forbidden_dependency": "import cgr\n\n" + _STANDARD_MAIN,
    "candidate_network_attempt": 'import socket\nsocket.create_connection(("192.0.2.1", 443), timeout=0.1)\n\n'
    + _STANDARD_MAIN,
}

_CONFIG_DEFECTS: dict[str, tuple[str, Any]] = {
    "candidate_output_missing": ("no_output", True),
    "candidate_protocol_invalid": ("malformed_output", True),
    "candidate_evidence_incomplete": ("bare_fabricated_energy", True),
    "candidate_coordinate_unit_mismatch": ("coordinate_unit", "bohr"),
    "candidate_structure_mismatch": ("bond_distance", 1.7),
    "candidate_charge_mismatch": ("molecular_charge", 1),
    "candidate_multiplicity_mismatch": ("spin_multiplicity", 3),
    "candidate_basis_mismatch": ("basis_set", "6-31g"),
    "candidate_active_space_mismatch": ("active_electron_count", 4),
    "candidate_mapper_mismatch": ("mapper", "parity"),
    "candidate_qubit_hamiltonian_mismatch": ("hamiltonian_tamper", True),
    "candidate_total_energy_semantics_invalid": (
        "total_energy_semantics",
        "electronic_only",
    ),
    "candidate_nuclear_repulsion_missing": ("include_nuclear_repulsion", False),
    "candidate_vqe_not_converged": ("vqe_converged", False),
    "candidate_energy_disagreement": ("energy_offset", 0.1),
    "candidate_content_hash_mismatch": ("forged_content_hash", True),
    "candidate_scientific_identity_mismatch": ("forged_scientific_identity", True),
    "candidate_lineage_mismatch": ("cross_linked_artifacts", True),
    "candidate_untrusted_authorization_claim": ("authorized_claim", True),
    "candidate_output_path_violation": ("output_path_escape", True),
}


class ReviewedBenchmarkRepairProvider:
    """Acceptance adapter; deliberately absent from normal provider registration."""

    def __init__(self, public_experiment: dict[str, Any]) -> None:
        self.public_experiment = copy.deepcopy(public_experiment)
        self.invocations = 0
        supported = tuple(sorted(set(_MAIN_DEFECTS) | set(_CONFIG_DEFECTS)))
        self._capability = ProviderCapability(
            provider_identifier="reviewed-quantum-repair-benchmark",
            provider_version="1.0.0",
            provider_type="deterministic",
            supported_finding_codes=supported,
            maximum_patch_bytes=64 * 1024,
            deterministic=True,
            network_required=False,
            tool_requirements=(),
            trust_classification="reviewed",
        )

    @property
    def capability(self) -> ProviderCapability:
        return self._capability

    def propose_repair(
        self,
        *,
        directive: QuantumRepairDirective,
        source_root: Path,
        source_manifest: SourceManifest,
    ) -> QuantumRepairPatch:
        self.invocations += 1
        finding = directive.primary_finding_code
        if finding in _MAIN_DEFECTS:
            path = source_root / "main.py"
            edit = StructuredEdit(
                relative_path="main.py",
                old_text=path.read_text(encoding="utf-8"),
                new_text=_STANDARD_MAIN,
            )
        elif finding in _CONFIG_DEFECTS:
            key, _ = _CONFIG_DEFECTS[finding]
            config_path = source_root / "repair-config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            corrected = _correct_value(key, self.public_experiment)
            old_line = f"  {json.dumps(key)}: {json.dumps(config[key])}"
            new_line = f"  {json.dumps(key)}: {json.dumps(corrected)}"
            edit = StructuredEdit(
                relative_path="repair-config.json",
                old_text=old_line,
                new_text=new_line,
            )
        else:
            raise ValueError(f"Benchmark provider does not support finding {finding}.")
        return create_patch(
            patch_identifier=f"patch-{directive.attempt_number:03d}",
            directive=directive,
            source_manifest=source_manifest,
            provider_identifier=self.capability.provider_identifier,
            provider_version=self.capability.provider_version,
            provider_type=self.capability.provider_type,
            edits=(edit,),
            rationale="Repair the diagnosed candidate-owned defect using public task declarations.",
            claimed_addressed_findings=(finding,),
        )


def materialize_benchmark_source(
    *,
    template_root: Path,
    support_root: Path,
    diagnosis_support: Path,
    destination: Path,
    candidate_identifier: str,
    defects: tuple[str, ...],
) -> None:
    if destination.exists():
        raise ValueError("Repair benchmark source destination already exists.")
    shutil.copytree(template_root, destination)
    shutil.copy2(support_root / "repairable_candidate.py", destination)
    shutil.copy2(diagnosis_support, destination / "standalone_candidate.py")
    config_path = destination / "repair-config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["candidate_identifier"] = candidate_identifier
    main_defects = [item for item in defects if item in _MAIN_DEFECTS]
    if len(main_defects) > 1:
        raise ValueError(
            "Composite repair cases support at most one blocking source defect."
        )
    if main_defects:
        (destination / "main.py").write_text(
            _MAIN_DEFECTS[main_defects[0]], encoding="utf-8"
        )
    for finding in defects:
        if finding in _CONFIG_DEFECTS:
            key, value = _CONFIG_DEFECTS[finding]
            config[key] = value
        elif finding not in _MAIN_DEFECTS:
            raise ValueError(f"Unknown repair benchmark defect: {finding}")
    config_path.write_text(
        json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _correct_value(key: str, experiment: dict[str, Any]) -> Any:
    molecule = experiment["molecular_system"]
    electronic = experiment["electronic_structure"]
    quantum = experiment["quantum_model"]
    public_values = {
        "bond_distance": molecule["declared_bond_distance"],
        "coordinate_unit": molecule["coordinate_unit"],
        "molecular_charge": molecule["molecular_charge"],
        "spin_multiplicity": molecule["spin_multiplicity"],
        "basis_set": electronic["basis_set"],
        "active_electron_count": electronic["active_electron_count"],
        "active_spatial_orbital_count": electronic["active_spatial_orbital_count"],
        "mapper": quantum["mapper"],
        "total_energy_semantics": "molecular_total",
        "energy_offset": 0.0,
        "include_nuclear_repulsion": True,
        "vqe_converged": True,
    }
    return public_values.get(key, False)
