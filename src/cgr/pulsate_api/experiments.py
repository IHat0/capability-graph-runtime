"""Dynamic, molecule-neutral experiment planning and immutable persistence."""

from __future__ import annotations

import json
import math
import re
import stat
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal, Self

from pydantic import Field, field_validator, model_validator

from cgr.kernel.contracts import CapabilityVersion
from cgr.quantum_preflight.artifacts import artifact_reference, write_json_atomic
from cgr.quantum_preflight.contracts import (
    CartesianAtom,
    ElectronicStructureModel,
    ManifestEnvelope,
    MolecularSystem,
    QuantumChemistryExperiment,
    QuantumExecutionPolicy,
    QuantumModel,
    QuantumVerificationPolicy,
)
from cgr.science import CanonicalModel, CreationProvenance, ScientificExperiment
from cgr.science.contracts import ExperimentExecutionPolicy

SPECIFICATION_SCHEMA = "cgr.pulsate-scientific-experiment-specification/1.0.0"
PLAN_SCHEMA = "cgr.pulsate-experiment-plan/1.0.0"
_EXPERIMENT_IDENTIFIER = re.compile(r"^experiment-[0-9a-f]{32}$")
_MAX_EXPERIMENT_JSON_BYTES = 2 * 1024 * 1024
_SUPPORTED_ELEMENTS = {"H": 1, "He": 2, "Li": 3}
_STO3G_SPATIAL_ORBITALS = {"H": 1, "He": 1, "Li": 5}
_MOLECULE_ALIASES: tuple[tuple[str, tuple[str, str], int | None], ...] = (
    ("helium hydride cation", ("He", "H"), 1),
    ("helium hydride ion", ("He", "H"), 1),
    ("lithium hydride", ("Li", "H"), None),
    ("hydrogen molecule", ("H", "H"), None),
    ("molecular hydrogen", ("H", "H"), None),
)
_ELEMENT_TOKEN = re.compile(r"([A-Z][a-z]?)([0-9]*)")
_FORMULA_CANDIDATE = re.compile(r"(?<![A-Za-z])([A-Z][A-Za-z0-9]*)([+-]?)(?![A-Za-z])")
_NUMBER = r"(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
_BONDED_DISTANCE = re.compile(
    rf"(?:bond\s+(?:length|distance)(?:\s+(?:of|is))?|\bat)\s*({_NUMBER})\s*"
    r"(angstroms?|ångströms?|å|bohrs?)(?=\s|[.,;!?)]|$)",
    re.IGNORECASE,
)
_UNITLESS_DISTANCE = re.compile(
    rf"(?:bond\s+(?:length|distance)(?:\s+(?:of|is))?|\bat)\s*({_NUMBER})(?!\s*[A-Za-z])",
    re.IGNORECASE,
)
_CHARGE = re.compile(r"\bcharge\s*(?:of|is|=)?\s*([+-]?\d+)\b", re.IGNORECASE)
_MULTIPLICITIES = {"singlet": 1, "doublet": 2, "triplet": 3}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class PlannerInputError(ValueError):
    """The bounded language parser found an invalid scientific declaration."""


class ExperimentNotFoundError(LookupError):
    """A dynamic experiment record does not exist or is not executable."""


class ActiveSpacePolicy(CanonicalModel):
    active_electron_count: int = Field(gt=0)
    active_spatial_orbital_count: int = Field(gt=0)
    active_orbital_indices: tuple[int, ...]

    @model_validator(mode="after")
    def validate_orbitals(self) -> Self:
        if len(self.active_orbital_indices) != self.active_spatial_orbital_count:
            raise ValueError("Active-space orbital count does not match its indices.")
        if any(index < 0 for index in self.active_orbital_indices):
            raise ValueError("Active-space orbital indices must be non-negative.")
        if len(set(self.active_orbital_indices)) != len(self.active_orbital_indices):
            raise ValueError("Active-space orbital indices must be unique.")
        if self.active_electron_count > 2 * self.active_spatial_orbital_count:
            raise ValueError("Active-electron count exceeds the declared active-space capacity.")
        return self


class ScientificExperimentSpecification(CanonicalModel):
    schema_version: Literal[SPECIFICATION_SCHEMA] = SPECIFICATION_SCHEMA
    objective: Literal["molecular_ground_state_energy"]
    atoms: tuple[str, ...]
    coordinates: tuple[tuple[float, float, float], ...]
    coordinate_units: Literal["angstrom", "bohr"]
    molecular_charge: int = Field(ge=-8, le=8)
    multiplicity: Literal[1, 2, 3]
    basis_set: Literal["sto-3g"]
    reference_method: Literal["restricted_hartree_fock"]
    frozen_core_policy: Literal["automatic_closed_shell_core"]
    active_space_policy: ActiveSpacePolicy
    mapper: Literal["jordan_wigner"]
    ansatz: Literal["uccsd"]
    optimizer: Literal["slsqp"]
    verification_policy: QuantumVerificationPolicy
    execution_policy: QuantumExecutionPolicy
    execution_target: Literal["local_simulator", "ibm_quantum"]

    @field_validator("atoms")
    @classmethod
    def validate_atoms(cls, atoms: tuple[str, ...]) -> tuple[str, ...]:
        if not atoms:
            raise ValueError("At least one atom is required.")
        if len(atoms) != 2:
            raise ValueError("Dynamic experiment intake v1 supports exactly two atoms.")
        unsupported = [element for element in atoms if element not in _SUPPORTED_ELEMENTS]
        if unsupported:
            raise ValueError(f"Unsupported element symbol '{unsupported[0]}'.")
        return atoms

    @field_validator("coordinates")
    @classmethod
    def validate_coordinates(
        cls, coordinates: tuple[tuple[float, float, float], ...]
    ) -> tuple[tuple[float, float, float], ...]:
        if any(not all(math.isfinite(component) for component in point) for point in coordinates):
            raise ValueError("All molecular coordinates must be finite three-vectors.")
        return coordinates

    @model_validator(mode="after")
    def validate_scientific_consistency(self) -> Self:
        if len(self.atoms) != len(self.coordinates):
            raise ValueError("Atom and coordinate counts must match.")
        distance = math.dist(self.coordinates[0], self.coordinates[1])
        if not math.isfinite(distance) or distance <= 0:
            raise ValueError("The two atoms must declare a positive finite separation.")
        electrons = sum(_SUPPORTED_ELEMENTS[element] for element in self.atoms) - self.molecular_charge
        if electrons <= 0:
            raise ValueError("Molecular charge leaves no electrons.")
        basis_capacity = 2 * sum(_STO3G_SPATIAL_ORBITALS[element] for element in self.atoms)
        if electrons > basis_capacity:
            raise ValueError("Molecular charge exceeds the STO-3G electron capacity.")
        unpaired = self.multiplicity - 1
        if unpaired > electrons or (electrons - unpaired) % 2:
            raise ValueError("Electron count and multiplicity are physically incompatible.")
        frozen_electrons = sum(2 for element in self.atoms if _SUPPORTED_ELEMENTS[element] > 2)
        expected_active = electrons - frozen_electrons
        if self.active_space_policy.active_electron_count != expected_active:
            raise ValueError("Active-electron count disagrees with the declared automatic core policy.")
        core_orbitals = frozen_electrons // 2
        expected_indices = tuple(
            range(
                core_orbitals,
                core_orbitals + self.active_space_policy.active_spatial_orbital_count,
            )
        )
        if self.active_space_policy.active_orbital_indices != expected_indices:
            raise ValueError("Active-orbital indices disagree with the declared automatic core policy.")
        policy = self.execution_policy
        if (
            policy.maximum_duration_seconds > 3600
            or policy.maximum_memory_mib > 4096
            or policy.maximum_processes > 256
            or policy.maximum_result_bytes > 10 * 1024 * 1024
            or policy.maximum_log_bytes > 1024 * 1024
            or policy.cpu_limit > 2.0
        ):
            raise ValueError("Execution resources exceed the server-controlled limits.")
        if not policy.network_disabled:
            raise ValueError("Dynamic local execution must disable networking.")
        return self

    @property
    def specification_sha256(self) -> str:
        return self.fingerprint


@dataclass(frozen=True)
class PlannedSpecification:
    specification: ScientificExperimentSpecification | None
    assumptions: tuple[str, ...]
    warnings: tuple[str, ...]
    missing_fields: tuple[str, ...]
    requested_execution_target: Literal["local_simulator", "ibm_quantum"]

    @property
    def ready_for_execution(self) -> bool:
        return self.specification is not None and not self.missing_fields


def _parse_formula(question: str) -> tuple[tuple[str, ...] | None, int | None]:
    lowered = question.casefold()
    alias_matches = [alias for alias in _MOLECULE_ALIASES if alias[0] in lowered]
    if len(alias_matches) > 1:
        raise PlannerInputError("Multiple molecular systems were supplied.")
    if alias_matches:
        _, atoms, charge = alias_matches[0]
        return atoms, charge
    invalid_diatomic: str | None = None
    for candidate in _FORMULA_CANDIDATE.finditer(question):
        formula, suffix = candidate.groups()
        parts = list(_ELEMENT_TOKEN.finditer(formula))
        if not parts or "".join(part.group(0) for part in parts) != formula:
            continue
        atoms: list[str] = []
        for part in parts:
            count = int(part.group(2) or "1")
            if count <= 0 or count > 8:
                raise PlannerInputError("Molecular formula atom counts must be between 1 and 8.")
            atoms.extend([part.group(1)] * count)
        if len(atoms) != 2:
            invalid_diatomic = formula
            continue
        suffix_charge = 1 if suffix == "+" else -1 if suffix == "-" else None
        return tuple(atoms), suffix_charge
    if invalid_diatomic is not None:
        raise PlannerInputError(
            f"Dynamic experiment intake v1 requires a two-atom formula, not {invalid_diatomic}."
        )
    return None, None


def _parse_distance(question: str) -> tuple[float | None, str | None, bool]:
    match = _BONDED_DISTANCE.search(question)
    if match:
        value = float(match.group(1))
        unit = "bohr" if match.group(2).lower().startswith("bohr") else "angstrom"
        if not math.isfinite(value) or value <= 0:
            raise PlannerInputError("Bond length must be a positive finite number.")
        return value, unit, False
    unitless = _UNITLESS_DISTANCE.search(question)
    if unitless:
        value = float(unitless.group(1))
        if not math.isfinite(value) or value <= 0:
            raise PlannerInputError("Bond length must be a positive finite number.")
        return value, "angstrom", True
    if re.search(r"\bbond\s+(?:length|distance)\b", question, re.IGNORECASE):
        raise PlannerInputError("Bond length is malformed or lacks a supported numeric value.")
    if re.search(
        r"\bat\s+\S+\s+(?:angstroms?|ångströms?|å|bohrs?)(?=\s|[.,;!?)]|$)",
        question,
        re.IGNORECASE,
    ):
        raise PlannerInputError("Bond length is malformed or lacks a supported numeric value.")
    return None, None, False


def _parse_setting_intent(question: str) -> tuple[dict[str, bool], tuple[str, ...]]:
    """Detect explicit supported settings and fail-closed unsupported declarations."""
    lowered = question.casefold()
    explicit: dict[str, bool] = {
        "basis_set": False,
        "reference_method": False,
        "mapper": False,
        "ansatz": False,
        "optimizer": False,
    }
    unsupported: list[str] = []

    accepted_basis = bool(re.search(r"\b(?:minimal\s+basis|sto-?3g(?:\s+basis)?)\b", lowered))
    basis_mentions = re.findall(
        r"\b([a-z0-9][a-z0-9+*_-]*)\s+basis\b|"
        r"\bbasis(?:\s+set)?\s*(?:of|is|=)\s*([a-z0-9][a-z0-9+*_-]*)\b",
        lowered,
    )
    if accepted_basis:
        explicit["basis_set"] = True
    if basis_mentions:
        values = {left or right for left, right in basis_mentions}
        if any(value not in {"minimal", "sto-3g", "sto3g"} for value in values):
            unsupported.append("unsupported_basis_set")

    accepted_reference = bool(
        re.search(r"\b(?:restricted\s+hartree[- ]fock|rhf)\b", lowered)
    )
    unsupported_reference = bool(
        re.search(
            r"\b(?:unrestricted\s+hartree[- ]fock|uhf|dft|mp2|ccsd)\b",
            lowered,
        )
        or (
            re.search(r"\bhartree[- ]fock\b", lowered) is not None
            and not accepted_reference
        )
        or re.search(
            r"\b(?:reference\s+method|method)\s*(?:(?:of|is|=)\s*)?"
            r"(?!restricted\s+hartree[- ]fock\b|rhf\b)[a-z0-9_-]+",
            lowered,
        )
    )
    if accepted_reference:
        explicit["reference_method"] = True
    if unsupported_reference:
        unsupported.append("unsupported_reference_method")

    setting_patterns = (
        (
            "mapper",
            r"\b(?:jordan[- ]wigner|jordan_wigner)\b",
            r"\b(?:mapper\s*(?:(?:of|is|=)\s*)?[a-z0-9_-]+|[a-z0-9_-]+\s+mapper)\b",
            "unsupported_mapper",
        ),
        (
            "ansatz",
            r"\buccsd\b",
            r"\b(?:ansatz\s*(?:(?:of|is|=)\s*)?[a-z0-9_-]+|[a-z0-9_-]+\s+ansatz)\b",
            "unsupported_ansatz",
        ),
        (
            "optimizer",
            r"\bslsqp\b",
            r"\b(?:optimizer\s*(?:(?:of|is|=)\s*)?[a-z0-9_-]+|[a-z0-9_-]+\s+optimizer)\b",
            "unsupported_optimizer",
        ),
    )
    for name, accepted_pattern, mention_pattern, missing_code in setting_patterns:
        accepted = bool(re.search(accepted_pattern, lowered))
        mentioned = bool(re.search(mention_pattern, lowered))
        explicit[name] = accepted
        if mentioned and not accepted:
            unsupported.append(missing_code)
    return explicit, tuple(sorted(set(unsupported)))


def plan_scientific_question(
    question: str,
    *,
    ibm_quantum_available: bool = False,
    ibm_unavailable_reason: str | None = None,
) -> PlannedSpecification:
    normalized = question.strip()
    if not normalized or len(normalized) > 4096:
        raise PlannerInputError("Question must contain between 1 and 4096 characters.")
    assumptions: list[str] = []
    warnings: list[str] = []
    missing: list[str] = []

    ibm_requested = bool(
        re.search(
            r"\b(?:ibm\s+quantum|ibm\s+hardware|quantum\s+hardware|execute\s+on\s+ibm)\b",
            normalized,
            re.IGNORECASE,
        )
    )
    local_requested = bool(
        re.search(r"\b(?:local\s+simulator|simulator)\b", normalized, re.IGNORECASE)
    )
    requested_execution_target: Literal["local_simulator", "ibm_quantum"] = (
        "ibm_quantum" if ibm_requested else "local_simulator"
    )
    if ibm_requested and local_requested:
        missing.append("ambiguous_execution_target")
        warnings.append("Multiple incompatible execution targets were requested.")
    elif ibm_requested and not ibm_quantum_available:
        missing.append("ibm_quantum_execution_unavailable")
        warnings.append(
            ibm_unavailable_reason
            or "IBM Quantum execution was requested but is unavailable on this server."
        )
    elif not local_requested:
        assumptions.append("execution_target=local_simulator (system default)")

    explicit_settings, unsupported_settings = _parse_setting_intent(normalized)
    missing.extend(unsupported_settings)
    warnings.extend(
        f"The explicitly requested setting is not supported: {setting}."
        for setting in unsupported_settings
    )

    objective_supported = bool(
        re.search(r"\bground[- ]state\b", normalized, re.IGNORECASE)
        and re.search(r"\benerg(?:y|ies)\b", normalized, re.IGNORECASE)
    )
    if not objective_supported:
        missing.append("objective")

    atoms, formula_charge = _parse_formula(normalized)
    if atoms is None:
        missing.append("atoms")
    elif any(element not in _SUPPORTED_ELEMENTS for element in atoms):
        unsupported = next(element for element in atoms if element not in _SUPPORTED_ELEMENTS)
        raise PlannerInputError(f"Unsupported element symbol '{unsupported}'.")

    distance, coordinate_units, defaulted_unit = _parse_distance(normalized)
    if distance is None:
        missing.append("bond_length")
    elif defaulted_unit:
        assumptions.append("coordinate_units=angstrom (system default)")

    explicit_charge = _CHARGE.search(normalized)
    charge = int(explicit_charge.group(1)) if explicit_charge else formula_charge
    if explicit_charge and formula_charge is not None and charge != formula_charge:
        raise PlannerInputError("Formula charge and explicit molecular charge disagree.")
    if charge is None and atoms is not None:
        neutral_electrons = sum(_SUPPORTED_ELEMENTS[element] for element in atoms)
        if neutral_electrons % 2 == 0:
            charge = 0
            assumptions.append("molecular_charge=0 (electron-consistent neutral formula)")
        else:
            missing.append("molecular_charge")

    if atoms is not None and charge is not None:
        electrons = sum(_SUPPORTED_ELEMENTS[element] for element in atoms) - charge
        basis_capacity = 2 * sum(_STO3G_SPATIAL_ORBITALS[element] for element in atoms)
        frozen_electrons = sum(2 for element in atoms if _SUPPORTED_ELEMENTS[element] > 2)
        active_electrons = electrons - frozen_electrons
        if electrons <= 0:
            raise PlannerInputError("Molecular charge leaves no electrons.")
        if electrons > basis_capacity:
            raise PlannerInputError("Molecular charge exceeds the STO-3G electron capacity.")
        if active_electrons <= 0 or active_electrons > 4:
            raise PlannerInputError(
                "Active-electron count exceeds the fixed two-orbital active-space capacity."
            )

    multiplicity_matches = [
        value for name, value in _MULTIPLICITIES.items()
        if re.search(rf"\b{name}\b", normalized, re.IGNORECASE)
    ]
    if len(set(multiplicity_matches)) > 1:
        raise PlannerInputError("Multiple incompatible spin multiplicities were supplied.")
    multiplicity = multiplicity_matches[0] if multiplicity_matches else None
    if multiplicity is None and atoms is not None and charge is not None:
        electrons = sum(_SUPPORTED_ELEMENTS[element] for element in atoms) - charge
        if electrons > 0 and electrons % 2 == 0:
            multiplicity = 1
            assumptions.append("multiplicity=singlet (closed-shell electron-consistent default)")
        else:
            missing.append("multiplicity")

    assumptions.extend(
        [
            "frozen_core_policy=automatic_closed_shell_core (system default)",
            "execution_policy=bounded_network_disabled (server controlled)",
        ]
    )
    normalized_settings = {
        "basis_set": "sto-3g",
        "reference_method": "restricted_hartree_fock",
        "mapper": "jordan_wigner",
        "ansatz": "uccsd",
        "optimizer": "slsqp",
    }
    assumptions.extend(
        f"{name}={value} ({'normalized explicit request' if explicit_settings[name] else 'system default'})"
        for name, value in normalized_settings.items()
        if f"unsupported_{name}" not in unsupported_settings
    )
    if atoms is not None and charge is not None and multiplicity is not None:
        electrons = sum(_SUPPORTED_ELEMENTS[element] for element in atoms) - charge
        unpaired = multiplicity - 1
        if electrons <= 0 or unpaired > electrons or (electrons - unpaired) % 2:
            raise PlannerInputError(
                "Electron count and multiplicity are physically incompatible."
            )
    if multiplicity is not None and multiplicity != 1:
        missing.append("closed_shell_singlet_for_v1_execution")
        warnings.append(
            "The multiplicity was parsed, but dynamic execution v1 supports the trusted restricted closed-shell path only."
        )
    if missing:
        return PlannedSpecification(
            None,
            tuple(assumptions),
            tuple(warnings),
            tuple(sorted(set(missing))),
            requested_execution_target,
        )

    assert atoms is not None and distance is not None and coordinate_units is not None
    assert charge is not None and multiplicity is not None
    electrons = sum(_SUPPORTED_ELEMENTS[element] for element in atoms) - charge
    frozen_electrons = sum(2 for element in atoms if _SUPPORTED_ELEMENTS[element] > 2)
    active_electrons = electrons - frozen_electrons
    core_orbitals = frozen_electrons // 2
    try:
        specification = ScientificExperimentSpecification(
            objective="molecular_ground_state_energy",
            atoms=atoms,
            coordinates=((0.0, 0.0, 0.0), (0.0, 0.0, distance)),
            coordinate_units=coordinate_units,
            molecular_charge=charge,
            multiplicity=multiplicity,
            basis_set="sto-3g",
            reference_method="restricted_hartree_fock",
            frozen_core_policy="automatic_closed_shell_core",
            active_space_policy=ActiveSpacePolicy(
                active_electron_count=active_electrons,
                active_spatial_orbital_count=2,
                active_orbital_indices=(core_orbitals, core_orbitals + 1),
            ),
            mapper="jordan_wigner",
            ansatz="uccsd",
            optimizer="slsqp",
            verification_policy=QuantumVerificationPolicy(
                exact_solver="numpy_minimum_eigensolver",
                required_energy_fields=(
                    "electronic_energy_hartree",
                    "total_energy_hartree",
                ),
                energy_difference_tolerance_hartree=1e-5,
                hermiticity_tolerance=1e-10,
                particle_number_tolerance=1e-6,
                required_artifact_lineage=True,
            ),
            execution_policy=QuantumExecutionPolicy(
                runtime_identifier="quantum_preflight_linux",
                maximum_duration_seconds=180,
                maximum_memory_mib=4096,
                maximum_processes=256,
                maximum_result_bytes=10 * 1024 * 1024,
                maximum_log_bytes=1024 * 1024,
                network_disabled=True,
                cpu_limit=2.0,
            ),
            execution_target=requested_execution_target,
        )
    except ValueError as exc:
        raise PlannerInputError(str(exc)) from exc
    return PlannedSpecification(
        specification,
        tuple(assumptions),
        tuple(warnings),
        (),
        requested_execution_target,
    )


def _atom_identifiers(elements: tuple[str, ...]) -> tuple[str, ...]:
    counts: dict[str, int] = {}
    identifiers: list[str] = []
    for element in elements:
        counts[element] = counts.get(element, 0) + 1
        identifiers.append(f"{element.lower()}-{counts[element]}")
    return tuple(identifiers)


def compile_manifest(
    specification: ScientificExperimentSpecification,
    *,
    experiment_identifier: str,
) -> ManifestEnvelope:
    if not _EXPERIMENT_IDENTIFIER.fullmatch(experiment_identifier):
        raise ValueError("Dynamic experiment identifier is invalid.")
    if specification.execution_target not in {"local_simulator", "ibm_quantum"}:
        raise ValueError("Dynamic execution target is unsupported.")
    if specification.multiplicity != 1:
        raise ValueError("Dynamic execution v1 requires a closed-shell singlet specification.")
    atom_identifiers = _atom_identifiers(specification.atoms)
    atoms = tuple(
        CartesianAtom(
            atom_identifier=identifier,
            element=element,
            coordinates=coordinates,
        )
        for identifier, element, coordinates in zip(
            atom_identifiers, specification.atoms, specification.coordinates
        )
    )
    molecule = MolecularSystem(
        atoms=atoms,
        coordinate_unit=specification.coordinate_units,
        molecular_charge=specification.molecular_charge,
        spin_multiplicity=specification.multiplicity,
        declared_bond_distance=math.dist(
            specification.coordinates[0], specification.coordinates[1]
        ),
        structure_artifact_identifier="molecular_structure",
    )
    active = specification.active_space_policy
    electronic = ElectronicStructureModel(
        basis_set=specification.basis_set,
        reference_method=specification.reference_method,
        driver_identifier="pyscf",
        frozen_core=active.active_orbital_indices[0] > 0,
        active_electron_count=active.active_electron_count,
        active_spatial_orbital_count=active.active_spatial_orbital_count,
        active_orbital_indices=active.active_orbital_indices,
        hamiltonian_representation="second_quantized",
    )
    version = CapabilityVersion(major=1, minor=0, patch=0)
    parent = ScientificExperiment(
        experiment_identifier=f"{experiment_identifier}-objective",
        schema_version=version,
        original_objective="Calculate the declared molecular ground-state total energy.",
        normalized_objective="Compute and independently verify the declared molecular ground-state total energy.",
        scientific_domain="quantum_chemistry",
        constraints=("cpu_only", "local_execution", "network_disabled", "no_ibm_runtime"),
        requested_outputs=(
            "exact_ground_state_result",
            "quantum_preflight_receipt",
            "vqe_ground_state_result",
        ),
        execution_policy=ExperimentExecutionPolicy(
            execution_allowed=True,
            require_all_blocking_assumptions_approved=True,
            permitted_runtimes=(specification.execution_policy.runtime_identifier,),
            parameters={
                "maximum_duration_seconds": specification.execution_policy.maximum_duration_seconds
            },
        ),
        provenance=CreationProvenance(
            producer="pulsate_labs",
            producer_version=version,
            source="cgr",
        ),
    )
    experiment = QuantumChemistryExperiment(
        experiment_identifier=experiment_identifier,
        schema_version=version,
        parent_experiment=parent,
        objective_type="molecular_ground_state",
        requested_observable="total_energy_hartree",
        molecular_system=molecule,
        electronic_structure=electronic,
        quantum_model=QuantumModel(
            mapper=specification.mapper,
            ansatz=specification.ansatz,
            initial_state="hartree_fock",
            optimizer=specification.optimizer,
            optimizer_settings={"maxiter": 200, "ftol": 1e-9, "disp": False},
            initial_point_policy="all_zeros",
            maximum_iterations=200,
            convergence_threshold=1e-9,
            random_seed=1701,
            simulator_type="statevector_estimator",
        ),
        verification_policy=specification.verification_policy,
        execution_policy=specification.execution_policy,
    )
    return ManifestEnvelope(
        manifest_schema="cgr.quantum-preflight-manifest/1.0.0",
        experiment=experiment,
        expected_experiment_sha256=experiment.fingerprint,
    )


def molecule_projection(
    specification: ScientificExperimentSpecification,
    *,
    experiment_identifier: str,
    manifest: ManifestEnvelope,
) -> dict[str, Any]:
    molecule = manifest.experiment.molecular_system
    structure_payload = {
        **molecule.model_dump(mode="json"),
        "driver_spin": molecule.driver_spin,
        "total_electron_count": molecule.total_electron_count,
    }
    structure_sha256 = artifact_reference(
        "molecular_structure",
        "molecular_structure",
        structure_payload,
        filename="molecular-structure.json",
    ).content_sha256
    atoms = [
        {
            "atom_identifier": atom.atom_identifier,
            "element": atom.element,
            "coordinates": list(atom.coordinates),
        }
        for atom in molecule.atoms
    ]
    return {
        "scene_identifier": f"scene.{experiment_identifier}",
        "scene_stage": "planned",
        "experiment_identifier": experiment_identifier,
        "experiment_fingerprint": manifest.experiment.fingerprint,
        "expected_experiment_sha256": manifest.expected_experiment_sha256,
        "specification_sha256": specification.fingerprint,
        "structure_identifier": molecule.structure_artifact_identifier,
        "structure_hash": structure_sha256,
        "coordinate_unit": specification.coordinate_units,
        "coordinate_units": specification.coordinate_units,
        "elements": list(specification.atoms),
        "molecular_charge": specification.molecular_charge,
        "multiplicity": specification.multiplicity,
        "atoms": atoms,
        "bonds": [
            {
                "bond_identifier": "bond.0-1",
                "atom_identifiers": [atoms[0]["atom_identifier"], atoms[1]["atom_identifier"]],
                "declared_distance": molecule.declared_bond_distance,
                "derived_distance": molecule.declared_bond_distance,
            }
        ],
        "quantum_region": {
            "selection_identifier": "selection.full-diatomic-system",
            "atom_identifiers": [atom["atom_identifier"] for atom in atoms],
        },
        "scientific_model": {
            "charge": specification.molecular_charge,
            "spin_multiplicity": specification.multiplicity,
            "basis_set": specification.basis_set,
            "reference_method": specification.reference_method,
            "active_electron_count": specification.active_space_policy.active_electron_count,
            "active_spatial_orbital_count": specification.active_space_policy.active_spatial_orbital_count,
            "active_orbital_indices": list(specification.active_space_policy.active_orbital_indices),
            "mapper": specification.mapper,
            "ansatz": specification.ansatz,
        },
    }


class ExperimentStore:
    def __init__(
        self,
        root: Path,
        *,
        ibm_capability: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self.configured_root = Path(root)
        self.root = self.configured_root
        self._lock = threading.RLock()
        self._started = False
        self.ibm_capability = ibm_capability

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            if self.configured_root.is_symlink():
                raise ValueError("Experiment root must be a normal directory.")
            if self.configured_root.exists() and not self.configured_root.is_dir():
                raise ValueError("Experiment root must be a normal directory.")
            self.configured_root.mkdir(parents=True, exist_ok=True)
            self.root = self.configured_root.resolve(strict=True)
            self._started = True

    def close(self) -> None:
        self._started = False

    def plan(self, question: str) -> dict[str, Any]:
        if not self._started:
            raise RuntimeError("Experiment store is not started.")
        ibm_capability = self.ibm_capability() if self.ibm_capability is not None else {}
        planned = plan_scientific_question(
            question,
            ibm_quantum_available=ibm_capability.get("available") is True,
            ibm_unavailable_reason=(
                str(ibm_capability["reason"])
                if ibm_capability.get("reason") is not None
                else None
            ),
        )
        experiment_identifier = f"experiment-{uuid.uuid4().hex}"
        specification = planned.specification
        manifest = (
            compile_manifest(specification, experiment_identifier=experiment_identifier)
            if specification is not None
            else None
        )
        molecule = (
            molecule_projection(
                specification,
                experiment_identifier=experiment_identifier,
                manifest=manifest,
            )
            if specification is not None and manifest is not None
            else None
        )
        now = _utc_now()
        state = {
            "schema_version": PLAN_SCHEMA,
            "experiment_identifier": experiment_identifier,
            "original_question": question,
            "specification": specification.model_dump(mode="json") if specification else None,
            "assumptions": list(planned.assumptions),
            "warnings": list(planned.warnings),
            "missing_fields": list(planned.missing_fields),
            "ready_for_execution": planned.ready_for_execution,
            "requested_execution_target": planned.requested_execution_target,
            "specification_sha256": specification.fingerprint if specification else None,
            "experiment_fingerprint": manifest.experiment.fingerprint if manifest else None,
            "expected_experiment_sha256": manifest.expected_experiment_sha256 if manifest else None,
            "structure_identifier": (
                manifest.experiment.molecular_system.structure_artifact_identifier
                if manifest else None
            ),
            "structure_hash": molecule["structure_hash"] if molecule else None,
            "molecule": molecule,
            "created_at": now,
        }
        directory = self.root / experiment_identifier
        directory.mkdir(mode=0o700)
        write_json_atomic(
            directory / "request.json",
            {"question": question, "created_at": now},
            maximum_bytes=_MAX_EXPERIMENT_JSON_BYTES,
        )
        write_json_atomic(
            directory / "specification.json",
            {
                "schema_version": SPECIFICATION_SCHEMA,
                "specification_sha256": state["specification_sha256"],
                "specification": state["specification"],
            },
            maximum_bytes=_MAX_EXPERIMENT_JSON_BYTES,
        )
        write_json_atomic(
            directory / "state.json", state, maximum_bytes=_MAX_EXPERIMENT_JSON_BYTES
        )
        return self.get(experiment_identifier)

    def _directory(self, experiment_identifier: str) -> Path:
        if not _EXPERIMENT_IDENTIFIER.fullmatch(experiment_identifier):
            raise ExperimentNotFoundError("Experiment not found.")
        directory = self.root / experiment_identifier
        try:
            metadata = directory.lstat()
        except FileNotFoundError as exc:
            raise ExperimentNotFoundError("Experiment not found.") from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise ExperimentNotFoundError("Experiment not found.")
        resolved = directory.resolve(strict=True)
        if resolved.parent != self.root:
            raise ExperimentNotFoundError("Experiment not found.")
        return resolved

    @staticmethod
    def _read_json(directory: Path, filename: str) -> dict[str, Any]:
        path = directory / filename
        try:
            metadata = path.lstat()
        except FileNotFoundError as exc:
            raise ExperimentNotFoundError("Experiment evidence is incomplete.") from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise ValueError("Experiment evidence must be a regular file.")
        if metadata.st_size > _MAX_EXPERIMENT_JSON_BYTES:
            raise ValueError("Experiment evidence exceeds its size limit.")
        data = path.read_bytes()
        if len(data) > _MAX_EXPERIMENT_JSON_BYTES:
            raise ValueError("Experiment evidence exceeds its size limit.")
        value = json.loads(data.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Experiment evidence must be a JSON object.")
        return value

    def get(self, experiment_identifier: str) -> dict[str, Any]:
        if not self._started:
            raise RuntimeError("Experiment store is not started.")
        with self._lock:
            directory = self._directory(experiment_identifier)
            request = self._read_json(directory, "request.json")
            specification_document = self._read_json(directory, "specification.json")
            state = self._read_json(directory, "state.json")
            if state.get("experiment_identifier") != experiment_identifier:
                raise ValueError("Experiment state identifier mismatch.")
            if state.get("original_question") != request.get("question"):
                raise ValueError("Experiment request and state disagree.")
            raw_specification = specification_document.get("specification")
            if raw_specification is None:
                if state.get("ready_for_execution") is not False:
                    raise ValueError("Experiment readiness disagrees with its specification.")
                return state
            specification = ScientificExperimentSpecification.model_validate(raw_specification)
            fingerprint = specification.fingerprint
            if (
                fingerprint != specification_document.get("specification_sha256")
                or fingerprint != state.get("specification_sha256")
                or state.get("specification") != specification.model_dump(mode="json")
            ):
                raise ValueError("Experiment specification identity mismatch.")
            manifest = compile_manifest(
                specification, experiment_identifier=experiment_identifier
            )
            expected_molecule = molecule_projection(
                specification,
                experiment_identifier=experiment_identifier,
                manifest=manifest,
            )
            if (
                state.get("experiment_fingerprint") != manifest.experiment.fingerprint
                or state.get("expected_experiment_sha256")
                != manifest.expected_experiment_sha256
                or state.get("structure_identifier")
                != manifest.experiment.molecular_system.structure_artifact_identifier
                or state.get("structure_hash") != expected_molecule["structure_hash"]
            ):
                raise ValueError("Experiment manifest identity mismatch.")
            if state.get("molecule") != expected_molecule:
                raise ValueError("Experiment molecule projection identity mismatch.")
            return state

    def resolve_for_run(
        self, experiment_identifier: str
    ) -> tuple[ManifestEnvelope, dict[str, Any]]:
        state = self.get(experiment_identifier)
        if state.get("ready_for_execution") is not True:
            raise ExperimentNotFoundError("Experiment is not ready for execution.")
        specification = ScientificExperimentSpecification.model_validate(
            state["specification"]
        )
        if specification.fingerprint != state.get("specification_sha256"):
            raise ValueError("Experiment specification identity mismatch.")
        manifest = compile_manifest(
            specification, experiment_identifier=experiment_identifier
        )
        return manifest, molecule_projection(
            specification,
            experiment_identifier=experiment_identifier,
            manifest=manifest,
        )

    def resolve_for_targeted_run(
        self, experiment_identifier: str
    ) -> tuple[ManifestEnvelope, dict[str, Any], Literal["local_simulator", "ibm_quantum"]]:
        manifest, molecule = self.resolve_for_run(experiment_identifier)
        state = self.get(experiment_identifier)
        specification = ScientificExperimentSpecification.model_validate(state["specification"])
        return manifest, molecule, specification.execution_target
