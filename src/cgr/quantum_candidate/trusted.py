"""Verified read-only view of one hardened trusted-reference package."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cgr.quantum_preflight.contracts import QuantumChemistryExperiment
from cgr.quantum_preflight.identities import ScientificResultArtifact
from cgr.quantum_preflight.receipt import QuantumPreflightReceipt
from cgr.science import sha256_fingerprint


@dataclass(frozen=True)
class TrustedReferenceView:
    receipt: QuantumPreflightReceipt
    receipt_content_sha256: str
    exact_result: ScientificResultArtifact
    vqe_result: ScientificResultArtifact
    molecular_structure: dict[str, Any]
    electronic_problem: dict[str, Any]
    active_space: dict[str, Any]
    fermionic_hamiltonian: dict[str, Any]
    qubit_hamiltonian: dict[str, Any]

    @property
    def exact_total_energy(self) -> float:
        return self.exact_result.execution_result.total_energy_hartree


def load_verified_trusted_reference(
    directory: Path,
    experiment: QuantumChemistryExperiment,
) -> TrustedReferenceView:
    """Fail closed unless receipt identities and every required full artifact hash verify."""
    receipt_path = directory / "receipt.json"
    receipt = QuantumPreflightReceipt.model_validate(_payload(receipt_path))
    if not receipt.authorized:
        raise ValueError("Trusted reference receipt is not authorized.")
    if receipt.scientific_outcome.experiment_sha256 != experiment.fingerprint:
        raise ValueError("Trusted reference belongs to a different experiment.")
    pointers = {item.artifact_identifier: item for item in receipt.artifacts}
    filenames = {
        "molecular_structure": "molecular-structure.json",
        "electronic_problem": "electronic-problem.json",
        "active_space": "active-space.json",
        "fermionic_hamiltonian": "fermionic-hamiltonian.json",
        "qubit_hamiltonian": "qubit-hamiltonian.json",
        "exact_result": "exact-result.json",
        "vqe_result": "vqe-result.json",
    }
    values: dict[str, Any] = {}
    for role, filename in filenames.items():
        path = directory / filename
        pointer = pointers.get(role)
        if pointer is None or not path.is_file():
            raise ValueError(f"Trusted reference is missing required artifact {role}.")
        if hashlib.sha256(path.read_bytes()).hexdigest() != pointer.content_sha256:
            raise ValueError(
                f"Trusted reference artifact {role} failed full-content verification."
            )
        values[role] = _payload(path)
    exact = ScientificResultArtifact.model_validate(values["exact_result"])
    vqe = ScientificResultArtifact.model_validate(values["vqe_result"])
    if exact.scientific_result_sha256 != receipt.exact_scientific_result_sha256:
        raise ValueError("Trusted exact scientific-result identity is inconsistent.")
    if vqe.scientific_result_sha256 != receipt.vqe_scientific_result_sha256:
        raise ValueError("Trusted VQE scientific-result identity is inconsistent.")
    return TrustedReferenceView(
        receipt=receipt,
        receipt_content_sha256=hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
        exact_result=exact,
        vqe_result=vqe,
        molecular_structure=values["molecular_structure"],
        electronic_problem=values["electronic_problem"],
        active_space=values["active_space"],
        fermionic_hamiltonian=values["fermionic_hamiltonian"],
        qubit_hamiltonian=values["qubit_hamiltonian"],
    )


def trusted_payload_fingerprints(view: TrustedReferenceView) -> dict[str, str]:
    return {
        "molecular_structure": sha256_fingerprint(view.molecular_structure),
        "electronic_problem": sha256_fingerprint(view.electronic_problem),
        "active_space": sha256_fingerprint(view.active_space),
        "fermionic_hamiltonian": sha256_fingerprint(view.fermionic_hamiltonian),
        "qubit_hamiltonian": sha256_fingerprint(view.qubit_hamiltonian),
    }


def _payload(path: Path) -> Any:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or "payload" not in value:
        raise ValueError(f"Trusted artifact {path.name} is malformed.")
    return value["payload"]
