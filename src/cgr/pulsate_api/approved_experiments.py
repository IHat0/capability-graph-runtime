"""Compile immutable, scientist-approved experiments for existing run workers."""

from __future__ import annotations

import json
import math
import re
import stat
from dataclasses import dataclass
from typing import Any

from cgr.quantum_preflight.contracts import (
    ManifestEnvelope,
    QuantumExecutionPolicy,
    QuantumVerificationPolicy,
)
from cgr.science import sha256_fingerprint

from .experiments import (
    ActiveSpacePolicy,
    ScientificExperimentSpecification,
    compile_manifest,
    molecule_projection,
)
from .natural_language import (
    ApprovedExperimentResponse,
    InterpretationResponse,
    NaturalLanguageInterpretationStore,
)

_EXPERIMENT_IDENTIFIER = re.compile(r"^experiment-[0-9a-f]{32}$")
_INTERPRETATION_IDENTIFIER = re.compile(r"^interpretation-[0-9a-f]{32}$")
_ACTIVE_SPACE = re.compile(
    r"^(\d+) electrons? in (\d+) spatial orbitals?$", re.IGNORECASE
)
_FORMULA_TOKEN = re.compile(r"([A-Z][a-z]?)([1-9][0-9]*)?")
_INTENT_SEPARATORS = re.compile(
    r"[\s_\-\N{HYPHEN}\N{NON-BREAKING HYPHEN}\N{EN DASH}\N{EM DASH}]+"
)
_MAXIMUM_APPROVED_EXPERIMENT_BYTES = 2 * 1024 * 1024
_SUPPORTED_ATOMIC_NUMBERS = {"H": 1, "He": 2, "Li": 3}
_SUPPORTED_GROUND_STATE_OBJECTIVES = frozenset(
    {
        "prepare an electronic ground state experiment",
        "prepare a ground state experiment",
        "calculate ground state energy",
        "calculate the ground state energy",
        "calculate the electronic ground state energy",
        "calculate the molecular ground state energy",
        "calculate electronic ground state energy",
        "calculate molecular ground state energy",
        "compute the ground state energy",
        "compute the electronic ground state energy",
        "compute the molecular ground state energy",
        "compute electronic ground state energy",
        "compute molecular ground state energy",
        "ground state energy",
        "electronic ground state energy",
        "molecular ground state energy",
    }
)
_SUPPORTED_GROUND_STATE_QUANTITIES = frozenset(
    {
        "ground state energy",
        "electronic ground state energy",
        "molecular ground state energy",
    }
)
_COMPILED_OBJECTIVE = "molecular_ground_state_energy"


class ApprovedExperimentNotFoundError(LookupError):
    """The requested approved experiment does not exist."""


class ApprovedExperimentValidationError(ValueError):
    """An approved experiment cannot be compiled without changing its science."""


@dataclass(frozen=True)
class CompiledApprovedExperiment:
    manifest: ManifestEnvelope
    molecule: dict[str, Any]
    execution_target: str
    provenance: dict[str, Any]


def _required_value(wrapper: Any, field_name: str) -> Any:
    value = getattr(wrapper, "value", None)
    if value is None:
        raise ApprovedExperimentValidationError(
            f"Approved experiment is missing required field {field_name}."
        )
    return value


def _formula_elements(formula: str) -> tuple[str, ...]:
    elements: list[str] = []
    position = 0
    for match in _FORMULA_TOKEN.finditer(formula):
        if match.start() != position:
            raise ApprovedExperimentValidationError(
                "Approved molecular formula is unsupported."
            )
        symbol = match.group(1)
        count = int(match.group(2) or "1")
        elements.extend([symbol] * count)
        position = match.end()
    if position != len(formula) or not elements:
        raise ApprovedExperimentValidationError(
            "Approved molecular formula is unsupported."
        )
    return tuple(elements)


def _canonical_intent(value: str) -> str:
    return _INTENT_SEPARATORS.sub(" ", value.casefold()).strip()


class ApprovedExperimentExecutionResolver:
    """Resolve, verify, and structurally compile one immutable approval record."""

    def __init__(self, store: NaturalLanguageInterpretationStore) -> None:
        self.store = store

    def __call__(self, experiment_identifier: str) -> CompiledApprovedExperiment:
        if _EXPERIMENT_IDENTIFIER.fullmatch(experiment_identifier) is None:
            raise ApprovedExperimentNotFoundError("Approved experiment not found.")
        root = self.store.root
        approved_root = root / "approved"
        directory = approved_root / experiment_identifier
        record = directory / "experiment.json"
        try:
            approved_root_metadata = approved_root.lstat()
            directory_metadata = directory.lstat()
            record_metadata = record.lstat()
            if (
                stat.S_ISLNK(approved_root_metadata.st_mode)
                or not stat.S_ISDIR(approved_root_metadata.st_mode)
                or stat.S_ISLNK(directory_metadata.st_mode)
                or not stat.S_ISDIR(directory_metadata.st_mode)
                or stat.S_ISLNK(record_metadata.st_mode)
                or not stat.S_ISREG(record_metadata.st_mode)
                or record_metadata.st_size > _MAXIMUM_APPROVED_EXPERIMENT_BYTES
                or approved_root.resolve(strict=True).parent != root
                or directory.resolve(strict=True).parent != approved_root.resolve(strict=True)
                or record.resolve(strict=True).parent != directory.resolve(strict=True)
            ):
                raise ApprovedExperimentNotFoundError(
                    "Approved experiment not found."
                )
            payload = json.loads(record.read_bytes().decode("utf-8"))
        except (FileNotFoundError, NotADirectoryError, OSError, UnicodeError, json.JSONDecodeError):
            raise ApprovedExperimentNotFoundError(
                "Approved experiment not found."
            ) from None
        try:
            approved = ApprovedExperimentResponse.model_validate(payload)
            self._validate_interpretation_link(approved)
            return self._compile(approved, experiment_identifier)
        except ApprovedExperimentValidationError:
            raise
        except ValueError as exc:
            raise ApprovedExperimentValidationError(
                "Approved experiment record is invalid."
            ) from exc

    def _validate_interpretation_link(
        self,
        approved: ApprovedExperimentResponse,
    ) -> None:
        if (
            _INTERPRETATION_IDENTIFIER.fullmatch(
                approved.interpretation_identifier
            )
            is None
        ):
            raise ApprovedExperimentValidationError(
                "Approved interpretation identity is invalid."
            )
        directory = self.store.root / approved.interpretation_identifier
        record = directory / "interpretation.json"
        try:
            directory_metadata = directory.lstat()
            record_metadata = record.lstat()
            if (
                stat.S_ISLNK(directory_metadata.st_mode)
                or not stat.S_ISDIR(directory_metadata.st_mode)
                or stat.S_ISLNK(record_metadata.st_mode)
                or not stat.S_ISREG(record_metadata.st_mode)
                or record_metadata.st_size > _MAXIMUM_APPROVED_EXPERIMENT_BYTES
                or directory.resolve(strict=True).parent != self.store.root
                or record.resolve(strict=True).parent
                != directory.resolve(strict=True)
            ):
                raise ApprovedExperimentValidationError(
                    "Approved interpretation evidence is uncontrolled."
                )
            original = InterpretationResponse.model_validate_json(
                record.read_text(encoding="utf-8")
            )
        except ApprovedExperimentValidationError:
            raise
        except (OSError, UnicodeError, ValueError) as exc:
            raise ApprovedExperimentValidationError(
                "Approved interpretation evidence is unavailable."
            ) from exc
        if (
            original.interpretation_identifier
            != approved.interpretation_identifier
            or original.original_question != approved.original_question
            or approved.specification.original_question
            != approved.original_question
            or original.model_provenance
            != approved.specification.model_provenance
        ):
            raise ApprovedExperimentValidationError(
                "Approved interpretation provenance is mismatched."
            )

    @staticmethod
    def _compile(
        approved: ApprovedExperimentResponse,
        experiment_identifier: str,
    ) -> CompiledApprovedExperiment:
        if approved.experiment_identifier != experiment_identifier:
            raise ApprovedExperimentValidationError(
                "Approved experiment identifier mismatch."
            )
        if approved.status != "ready_for_ibm_submission":
            raise ApprovedExperimentValidationError(
                "Approved experiment is not ready for IBM submission."
            )
        if approved.requested_execution_target != "ibm_quantum":
            raise ApprovedExperimentValidationError(
                "Approved experiment execution target is not IBM Quantum."
            )
        specification_payload = approved.specification.model_dump(mode="json")
        specification_sha256 = sha256_fingerprint(specification_payload)
        if approved.specification_sha256 != specification_sha256:
            raise ApprovedExperimentValidationError(
                "Approved specification SHA-256 mismatch."
            )
        specification = approved.specification
        if (
            specification.interpretation_status != "ready_for_review"
            or specification.execution_support_status != "supported"
            or specification.missing_required_information
        ):
            raise ApprovedExperimentValidationError(
                "Approved specification is incomplete or unsupported."
            )

        scientific_objective = str(
            _required_value(
                specification.scientific_objective,
                "scientific_objective",
            )
        )
        requested_quantity = str(
            _required_value(
                specification.requested_quantity,
                "requested_quantity",
            )
        )
        if (
            _canonical_intent(scientific_objective)
            not in _SUPPORTED_GROUND_STATE_OBJECTIVES
            or _canonical_intent(requested_quantity)
            not in _SUPPORTED_GROUND_STATE_QUANTITIES
        ):
            raise ApprovedExperimentValidationError(
                "Approved scientific intent exceeds compiler capability."
            )
        name = specification.molecule.name.value
        formula = str(
            _required_value(specification.molecule.formula, "molecule.formula")
        )
        approved_atoms = _required_value(
            specification.molecule.atoms, "molecule.atoms"
        )
        coordinate_unit = str(
            _required_value(specification.coordinate_unit, "coordinate_unit")
        ).casefold()
        if coordinate_unit not in {"angstrom", "bohr"}:
            raise ApprovedExperimentValidationError(
                "Approved coordinate unit is unsupported."
            )
        if len(approved_atoms) != 2 or any(
            atom.coordinates is None for atom in approved_atoms
        ):
            raise ApprovedExperimentValidationError(
                "Approved execution supports exactly two atoms with Cartesian coordinates."
            )
        elements = tuple(atom.element for atom in approved_atoms)
        if elements != _formula_elements(formula):
            raise ApprovedExperimentValidationError(
                "Approved formula does not match the ordered atom list."
            )
        if any(element not in _SUPPORTED_ATOMIC_NUMBERS for element in elements):
            raise ApprovedExperimentValidationError(
                "Approved atom list contains an unsupported element."
            )
        coordinates = tuple(
            tuple(float(component) for component in atom.coordinates or ())
            for atom in approved_atoms
        )
        bonds = _required_value(
            specification.molecule.bond_lengths, "molecule.bond_lengths"
        )
        if (
            len(bonds) != 1
            or bonds[0].atom_indices != (0, 1)
            or bonds[0].unit != coordinate_unit
            or not math.isclose(
                math.dist(coordinates[0], coordinates[1]),
                bonds[0].value,
                rel_tol=0.0,
                abs_tol=1e-12,
            )
        ):
            raise ApprovedExperimentValidationError(
                "Approved bond length does not match the Cartesian structure."
            )

        charge = int(_required_value(specification.charge, "charge"))
        multiplicity = int(
            _required_value(specification.multiplicity, "multiplicity")
        )
        basis = str(_required_value(specification.basis, "basis")).casefold()
        method = str(
            _required_value(
                specification.electronic_structure_method,
                "electronic_structure_method",
            )
        ).casefold()
        mapper = str(_required_value(specification.mapper, "mapper")).casefold()
        ansatz = str(_required_value(specification.ansatz, "ansatz")).casefold()
        optimizer = str(
            _required_value(specification.optimizer, "optimizer")
        ).casefold()
        active_text = str(
            _required_value(specification.active_space, "active_space")
        ).strip()
        active_match = _ACTIVE_SPACE.fullmatch(active_text)
        if active_match is None:
            raise ApprovedExperimentValidationError(
                "Approved active-space declaration is unsupported."
            )
        active_electrons, active_orbitals = (
            int(active_match.group(1)),
            int(active_match.group(2)),
        )
        if (
            basis != "sto-3g"
            or method not in {"rhf", "restricted_hartree_fock"}
            or mapper != "jordan_wigner"
            or ansatz != "uccsd"
            or optimizer != "slsqp"
        ):
            raise ApprovedExperimentValidationError(
                "Approved scientific settings exceed compiler capability."
            )
        tolerance = float(
            _required_value(specification.tolerance, "tolerance")
        )
        precision = float(
            _required_value(specification.precision, "precision")
        )
        target = str(
            _required_value(
                specification.requested_execution_target,
                "requested_execution_target",
            )
        )
        if target != approved.requested_execution_target:
            raise ApprovedExperimentValidationError(
                "Approved execution target identity mismatch."
            )
        requested_backend = specification.requested_backend.value
        if specification.shots.value is not None:
            raise ApprovedExperimentValidationError(
                "Shot-based execution is outside approved compiler capability."
            )
        frozen_electrons = sum(
            2 for element in elements if _SUPPORTED_ATOMIC_NUMBERS[element] > 2
        )
        core_orbitals = frozen_electrons // 2
        try:
            executable = ScientificExperimentSpecification(
                objective="molecular_ground_state_energy",
                atoms=elements,
                coordinates=coordinates,
                coordinate_units=coordinate_unit,
                molecular_charge=charge,
                multiplicity=multiplicity,
                basis_set=basis,
                reference_method="restricted_hartree_fock",
                frozen_core_policy="automatic_closed_shell_core",
                active_space_policy=ActiveSpacePolicy(
                    active_electron_count=active_electrons,
                    active_spatial_orbital_count=active_orbitals,
                    active_orbital_indices=tuple(
                        range(core_orbitals, core_orbitals + active_orbitals)
                    ),
                ),
                mapper=mapper,
                ansatz=ansatz,
                optimizer=optimizer,
                verification_policy=QuantumVerificationPolicy(
                    exact_solver="numpy_minimum_eigensolver",
                    required_energy_fields=(
                        "electronic_energy_hartree",
                        "total_energy_hartree",
                    ),
                    energy_difference_tolerance_hartree=tolerance,
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
                execution_target="ibm_quantum",
            )
        except ValueError as exc:
            raise ApprovedExperimentValidationError(
                "Approved specification is outside compiler capability."
            ) from exc
        manifest = compile_manifest(
            executable,
            experiment_identifier=experiment_identifier,
            optimizer_tolerance=tolerance,
            execution_parameters={
                "approved_scientific_objective": scientific_objective,
                "approved_requested_quantity": requested_quantity,
                "approved_specification_sha256": specification_sha256,
                "compiled_objective": _COMPILED_OBJECTIVE,
                "requested_precision": precision,
                "requested_execution_target": target,
            },
        )
        compiled_manifest_sha256 = sha256_fingerprint(
            manifest.model_dump(mode="json")
        )
        molecule = molecule_projection(
            executable,
            experiment_identifier=experiment_identifier,
            manifest=manifest,
        )
        molecule.update(
            {
                "molecular_formula": formula,
                "bond_lengths": [
                    bond.model_dump(mode="json") for bond in bonds
                ],
                "specification_sha256": specification_sha256,
                "approved_specification_sha256": specification_sha256,
                "compiled_execution_manifest_sha256": compiled_manifest_sha256,
            }
        )
        if name is not None:
            molecule["molecule_name"] = name
        provenance = {
            "interpretation_identifier": approved.interpretation_identifier,
            "approved_experiment_identifier": experiment_identifier,
            "approved_scientific_objective": scientific_objective,
            "approved_requested_quantity": requested_quantity,
            "approved_specification_sha256": specification_sha256,
            "compiled_execution_manifest_sha256": compiled_manifest_sha256,
            "compiled_objective": _COMPILED_OBJECTIVE,
            "molecule_structure_sha256": molecule["structure_hash"],
            "mapper": mapper,
            "ansatz": ansatz,
            "optimizer": optimizer,
            "optimizer_tolerance": tolerance,
            "execution_target": target,
            "requested_precision": precision,
            "requested_backend": requested_backend,
        }
        return CompiledApprovedExperiment(
            manifest=manifest,
            molecule=molecule,
            execution_target=target,
            provenance=provenance,
        )
