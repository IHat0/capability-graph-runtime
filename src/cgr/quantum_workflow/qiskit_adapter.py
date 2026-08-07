"""Pinned local Qiskit adapters for generic Phase 5 quantum workflows."""

from __future__ import annotations

import hashlib
import importlib.metadata
import math
from collections.abc import Iterable, Mapping
from math import comb
from typing import Protocol, runtime_checkable

from pydantic import ValidationError

from cgr.electronic_structure import ElectronicActiveSpace
from cgr.kernel.contracts import (
    CapabilityVersion,
    ExecutionStatus,
    HealthStatus,
)
from cgr.science import (
    ArtifactLineageEdge,
    ArtifactReference,
    CapabilityDescriptor,
    CapabilityExecutionEnvelope,
    CapabilityInvocation,
    CapabilityResult,
    CreationProvenance,
    DeterminismClassification,
    ExecutionEvidence,
    FailureInformation,
    ScientificEngineAdapterDeclaration,
    ScientificEngineHealthReport,
    ScientificEngineIdentity,
    sha256_fingerprint,
)

from .contracts import (
    ExactDiagonalizationResult,
    FermionicHamiltonianTerm,
    MappedQubitHamiltonian,
    OptimizationEvaluation,
    PauliHamiltonianTerm,
    QuantumComplexCoefficient,
    QuantumRealParameter,
    SecondQuantizedHamiltonian,
    StatevectorSimulationResult,
    VariationalAnsatz,
    VariationalGroundStateResult,
)

_SCHEMA_VERSION = CapabilityVersion(major=1, minor=0, patch=0)
_ADAPTER_VERSION = CapabilityVersion(major=1, minor=0, patch=0)
_EXPECTED_VERSIONS = {
    "qiskit": "2.3.1",
    "qiskit-nature": "0.8.0",
    "qiskit-algorithms": "0.4.0",
}
_MAXIMUM_PAYLOAD_BYTES = 256 * 1024 * 1024
_MAXIMUM_EXACT_QUBITS = 12
_MAXIMUM_EXACT_MATRIX_DIMENSION = 1 << _MAXIMUM_EXACT_QUBITS
_MAXIMUM_STATEVECTOR_QUBITS = 16
_MAXIMUM_OPTIMIZATION_EVALUATIONS = 100_000

HAMILTONIAN_CONSTRUCT = "quantum.hamiltonian_construct"
FERMION_TO_QUBIT_MAP = "quantum.fermion_to_qubit_map"
EXACT_DIAGONALIZE = "quantum.exact_diagonalize"
ANSATZ_CONSTRUCT = "quantum.ansatz_construct"
VQE_EXECUTE = "quantum.vqe_execute"
STATEVECTOR_SIMULATE = "quantum.statevector_simulate"


@runtime_checkable
class QuantumArtifactPayloadStore(Protocol):
    """Read and persist exact bytes behind quantum artifact references."""

    def read(self, reference: ArtifactReference) -> bytes:
        """Return exact bytes addressed by a complete reference."""
        ...

    def write(self, reference: ArtifactReference, payload: bytes) -> None:
        """Persist exact bytes under a content-addressed reference."""
        ...


class QiskitQuantumWorkflowAdapterConfigurationError(ValueError):
    """Controlled construction failure for the Qiskit workflow adapter."""


def _installed_version(package: str) -> str | None:
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return None


def _runtime_versions() -> dict[str, str] | None:
    versions: dict[str, str] = {}
    for package in _EXPECTED_VERSIONS:
        version = _installed_version(package)
        if version is None:
            return None
        versions[package] = version
    return versions


def _qiskit_modules() -> tuple[object, object, object, object, object]:
    import numpy
    from qiskit.quantum_info import SparsePauliOp
    from qiskit_nature.second_q.hamiltonians import ElectronicEnergy
    from qiskit_nature.second_q.mappers import JordanWignerMapper
    from qiskit_nature.second_q.operators import FermionicOp

    return (
        numpy,
        SparsePauliOp,
        ElectronicEnergy,
        JordanWignerMapper,
        FermionicOp,
    )


def _variational_modules() -> tuple[object, ...]:
    import numpy
    from qiskit.primitives import StatevectorEstimator
    from qiskit.quantum_info import SparsePauliOp, Statevector
    from qiskit_algorithms import VQE
    from qiskit_algorithms.optimizers import SLSQP
    from qiskit_algorithms.utils import algorithm_globals
    from qiskit_nature.second_q.circuit.library import HartreeFock, UCCSD
    from qiskit_nature.second_q.mappers import JordanWignerMapper

    return (
        numpy,
        SparsePauliOp,
        Statevector,
        StatevectorEstimator,
        VQE,
        SLSQP,
        algorithm_globals,
        HartreeFock,
        UCCSD,
        JordanWignerMapper,
    )


def _descriptor(
    capability_name: str,
    *,
    accepted_artifact_types: tuple[str, ...],
    produced_artifact_types: tuple[str, ...],
    required_tools: tuple[str, ...],
    deterministic: bool,
) -> CapabilityDescriptor:
    return CapabilityDescriptor(
        capability_name=capability_name,
        version=_ADAPTER_VERSION,
        accepted_artifact_types=accepted_artifact_types,
        produced_artifact_types=produced_artifact_types,
        required_tools=required_tools,
        required_runtime="python",
        determinism=(
            DeterminismClassification.DETERMINISTIC
            if deterministic
            else DeterminismClassification.NONDETERMINISTIC
        ),
    )


def quantum_workflow_capability_envelopes() -> tuple[CapabilityExecutionEnvelope, ...]:
    """Return planner declarations for the generic Phase 5 capabilities."""

    definitions = (
        (
            HAMILTONIAN_CONSTRUCT,
            ("electronic_active_space",),
            ("second_quantized_hamiltonian",),
            ("qiskit_nature",),
            ("second_quantized_hamiltonian_construction",),
            True,
            (
                (
                    "Version 1 consumes spin-restricted chemist-order "
                    "active-space integrals."
                ),
                (
                    "The constant energy remains explicit and is not folded "
                    "into the fermionic operator."
                ),
                "Hamiltonian construction is bounded to 32 active spatial orbitals.",
            ),
        ),
        (
            FERMION_TO_QUBIT_MAP,
            ("second_quantized_hamiltonian",),
            ("mapped_qubit_hamiltonian",),
            ("qiskit", "qiskit_nature"),
            ("fermion_to_qubit_mapping",),
            True,
            (
                "Version 1 supports Jordan-Wigner mapping without qubit reduction.",
                "Every mapped Hamiltonian must pass an explicit Hermiticity check.",
            ),
        ),
        (
            EXACT_DIAGONALIZE,
            ("mapped_qubit_hamiltonian",),
            ("exact_diagonalization_result",),
            ("qiskit",),
            ("exact_active_space_diagonalization", "molecular_ground_state"),
            False,
            (
                (
                    "Version 1 exact diagonalization supports Jordan-Wigner "
                    "Hamiltonians only."
                ),
                "The full mapped matrix is bounded to 12 qubits and 4096 basis states.",
                (
                    "The solver restricts the matrix to the declared alpha/beta "
                    "particle sector before diagonalization."
                ),
            ),
        ),
        (
            ANSATZ_CONSTRUCT,
            ("mapped_qubit_hamiltonian",),
            ("variational_ansatz",),
            ("qiskit", "qiskit_nature"),
            ("variational_ansatz_construction",),
            True,
            (
                (
                    "Version 1 supports a one-repetition particle-conserving "
                    "UCCSD ansatz with a Hartree-Fock initial state."
                ),
                "Version 1 requires an explicit all-zero initial point.",
                "Version 1 supports Jordan-Wigner mapped active spaces only.",
            ),
        ),
        (
            VQE_EXECUTE,
            ("mapped_qubit_hamiltonian", "variational_ansatz"),
            ("variational_ground_state_result",),
            ("qiskit", "qiskit_nature", "qiskit_algorithms"),
            ("variational_ground_state", "molecular_ground_state"),
            True,
            (
                (
                    "Version 1 uses exact local statevector expectation values "
                    "and the SLSQP optimizer."
                ),
                (
                    "The VQE capability cannot consume an exact-diagonalization "
                    "result or reference energy."
                ),
                "Optimizer evaluations and variational parameters are bounded.",
            ),
        ),
        (
            STATEVECTOR_SIMULATE,
            (
                "mapped_qubit_hamiltonian",
                "variational_ansatz",
                "variational_ground_state_result",
            ),
            ("statevector_simulation_result",),
            ("qiskit", "qiskit_nature"),
            ("statevector_simulation", "variational_state_reconstruction"),
            True,
            (
                "Version 1 reconstructs the optimized variational state locally.",
                "Canonical amplitude emission is bounded to 16 qubits.",
                (
                    "The result must pass normalization, particle-sector, "
                    "and VQE-energy consistency checks."
                ),
            ),
        ),
    )
    return tuple(
        CapabilityExecutionEnvelope(
            descriptor=_descriptor(
                capability_name,
                accepted_artifact_types=accepted,
                produced_artifact_types=produced,
                required_tools=required_tools,
                deterministic=deterministic,
            ),
            supported_objective_types=objectives,
            execution_targets=("local_cpu",),
            known_limitations=limitations,
            metadata={
                "scientific_domain": "quantum_workflow",
                "adapter_family": "qiskit_nature",
                "ibm_submission_enabled": False,
            },
        )
        for (
            capability_name,
            accepted,
            produced,
            required_tools,
            objectives,
            deterministic,
            limitations,
        ) in definitions
    )


def _stable_identifier(prefix: str, *values: object) -> str:
    encoded = "\x1f".join(str(value) for value in values).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(encoded).hexdigest()[:32]}"


def _coefficient(value: complex | float) -> QuantumComplexCoefficient:
    number = complex(value)
    if not math.isfinite(number.real) or not math.isfinite(number.imag):
        raise ValueError("Quantum operator coefficients must be finite.")
    return QuantumComplexCoefficient.from_value(number)


def _fermionic_terms(
    values: Iterable[tuple[str, complex | float]],
) -> tuple[FermionicHamiltonianTerm, ...]:
    combined: dict[str, complex] = {}
    for label, value in values:
        if not isinstance(label, str):
            raise ValueError("Fermionic operator labels must be strings.")
        number = complex(value)
        _coefficient(number)
        combined[label] = combined.get(label, 0j) + number
    return tuple(
        FermionicHamiltonianTerm(
            label=label,
            coefficient=_coefficient(combined[label]),
        )
        for label in sorted(combined)
        if combined[label] != 0
    )


def _pauli_terms(
    values: Iterable[tuple[str, complex | float]],
) -> tuple[PauliHamiltonianTerm, ...]:
    combined: dict[str, complex] = {}
    for label, value in values:
        if not isinstance(label, str):
            raise ValueError("Pauli operator labels must be strings.")
        number = complex(value)
        _coefficient(number)
        combined[label] = combined.get(label, 0j) + number
    return tuple(
        PauliHamiltonianTerm(
            label=label,
            coefficient=_coefficient(combined[label]),
        )
        for label in sorted(combined)
        if combined[label] != 0
    )


def _maximum_antihermitian_coefficient(operator: object) -> float:
    residual = (operator - operator.adjoint()).simplify()
    coefficients = residual.coeffs
    if len(coefficients) == 0:
        return 0.0
    maximum = max(abs(complex(value)) for value in coefficients)
    if not math.isfinite(maximum):
        raise ValueError("Hamiltonian Hermiticity residual is non-finite.")
    return float(maximum)


def _real_parameters(values: Iterable[float]) -> tuple[QuantumRealParameter, ...]:
    return tuple(
        QuantumRealParameter.from_value(index, float(value))
        for index, value in enumerate(values)
    )


def _parameter_hash(values: tuple[QuantumRealParameter, ...]) -> str:
    return sha256_fingerprint([item.value_hex for item in values])


def _native_mapped_operator(
    mapped: MappedQubitHamiltonian, sparse_pauli_type: object
) -> object:
    operator = sparse_pauli_type.from_list(
        [(term.label, term.coefficient.value) for term in mapped.terms]
    )
    if int(operator.num_qubits) != mapped.number_of_qubits:
        raise ValueError("Reconstructed qubit count differs from the artifact.")
    return operator


def _native_variational_ansatz(
    mapped: MappedQubitHamiltonian,
    *,
    hartree_fock_type: object,
    uccsd_type: object,
    mapper_type: object,
) -> tuple[object, object]:
    if mapped.mapper != "jordan_wigner":
        raise ValueError(
            "Version 1 variational execution requires Jordan-Wigner mapping."
        )
    mapper = mapper_type()
    particles = (mapped.alpha_electron_count, mapped.beta_electron_count)
    initial_state = hartree_fock_type(
        num_spatial_orbitals=mapped.active_spatial_orbital_count,
        num_particles=particles,
        qubit_mapper=mapper,
    )
    ansatz = uccsd_type(
        num_spatial_orbitals=mapped.active_spatial_orbital_count,
        num_particles=particles,
        qubit_mapper=mapper,
        reps=1,
        initial_state=initial_state,
        generalized=False,
        preserve_spin=True,
        include_imaginary=False,
    )
    if int(ansatz.num_qubits) != mapped.number_of_qubits:
        raise ValueError("Variational ansatz qubit count differs from the Hamiltonian.")
    return initial_state, ansatz


def _canonical_statevector(
    values: Iterable[complex],
    *,
    phase_tolerance: float,
) -> tuple[int, tuple[QuantumComplexCoefficient, ...]]:
    data = tuple(complex(value) for value in values)
    anchor = next(
        (index for index, value in enumerate(data) if abs(value) > phase_tolerance),
        None,
    )
    if anchor is None:
        raise ValueError("Statevector has no nonzero amplitude.")
    phase = data[anchor] / abs(data[anchor])
    canonical = [value / phase for value in data]
    canonical[anchor] = complex(abs(data[anchor]), 0.0)
    return anchor, tuple(
        QuantumComplexCoefficient.from_value(value) for value in canonical
    )


class QiskitQuantumWorkflowAdapter:
    """Execute generic Phase 5 capabilities through pinned local boundaries."""

    def __init__(
        self,
        payload_store: QuantumArtifactPayloadStore,
        *,
        maximum_payload_bytes: int = _MAXIMUM_PAYLOAD_BYTES,
    ) -> None:
        if not isinstance(payload_store, QuantumArtifactPayloadStore):
            raise QiskitQuantumWorkflowAdapterConfigurationError(
                "The Qiskit workflow adapter requires a quantum artifact payload store."
            )
        if maximum_payload_bytes <= 0:
            raise QiskitQuantumWorkflowAdapterConfigurationError(
                "The quantum artifact payload limit must be positive."
            )
        self._payload_store = payload_store
        self._maximum_payload_bytes = maximum_payload_bytes
        self._declaration = ScientificEngineAdapterDeclaration(
            adapter_identifier="adapter.qiskit_nature.quantum_workflow",
            adapter_version=_ADAPTER_VERSION,
            engine=ScientificEngineIdentity(
                engine_identifier="engine.qiskit_nature",
                engine_version=_installed_version("qiskit-nature") or "unavailable",
            ),
            capabilities=quantum_workflow_capability_envelopes(),
            metadata={
                "scientific_domain": "quantum_workflow",
                "native_objects_public": False,
                "ibm_submission_enabled": False,
            },
        )
        self._envelopes = {
            envelope.descriptor.capability_name: envelope
            for envelope in self._declaration.capabilities
        }

    @property
    def declaration(self) -> ScientificEngineAdapterDeclaration:
        """Return the immutable adapter and capability declaration."""

        return self._declaration

    def health(self) -> ScientificEngineHealthReport:
        """Report exact Qiskit and Qiskit Nature dependency identities."""

        observed = {
            package: _installed_version(package) for package in _EXPECTED_VERSIONS
        }
        missing = sorted(
            package for package, version in observed.items() if version is None
        )
        if missing:
            return ScientificEngineHealthReport(
                status=HealthStatus.UNAVAILABLE,
                reason_code="dependency_missing",
                message=(
                    "The optional Qiskit quantum-workflow dependencies are unavailable."
                ),
                diagnostics={
                    "missing_packages": ",".join(missing),
                    **{
                        f"expected_{package.replace('-', '_')}": expected
                        for package, expected in _EXPECTED_VERSIONS.items()
                    },
                },
            )
        mismatched = {
            package: str(observed[package])
            for package, expected in _EXPECTED_VERSIONS.items()
            if observed[package] != expected
        }
        diagnostics = {
            f"{package.replace('-', '_')}_version": str(version)
            for package, version in observed.items()
        }
        if mismatched:
            return ScientificEngineHealthReport(
                status=HealthStatus.DEGRADED,
                reason_code="dependency_version_mismatch",
                message=(
                    "The installed Qiskit quantum-workflow versions do not match "
                    "the repository pins."
                ),
                diagnostics={
                    **diagnostics,
                    **{
                        f"expected_{package.replace('-', '_')}": expected
                        for package, expected in _EXPECTED_VERSIONS.items()
                    },
                },
            )
        return ScientificEngineHealthReport(
            status=HealthStatus.HEALTHY,
            diagnostics=diagnostics,
        )

    def invoke(self, invocation: CapabilityInvocation) -> CapabilityResult:
        """Execute one declared generic quantum-workflow capability."""

        if not isinstance(invocation, CapabilityInvocation):
            return self._failure(
                "invalid_invocation",
                "The Qiskit adapter requires a scientific capability invocation.",
            )
        capability_name = invocation.capability.capability_name
        envelope = self._envelopes.get(capability_name)
        if envelope is None or invocation.capability != envelope.descriptor:
            return self._failure(
                "capability_not_declared",
                "The requested capability is not declared by this Qiskit adapter.",
            )
        health = self.health()
        if health.status is not HealthStatus.HEALTHY:
            return self._failure(
                health.reason_code or "adapter_unhealthy",
                health.message
                or "The Qiskit adapter is not healthy enough to execute.",
            )

        try:
            if capability_name == HAMILTONIAN_CONSTRUCT:
                return self._construct_hamiltonian(invocation)
            if capability_name == FERMION_TO_QUBIT_MAP:
                return self._map_hamiltonian(invocation)
            if capability_name == EXACT_DIAGONALIZE:
                return self._exact_diagonalize(invocation)
            if capability_name == ANSATZ_CONSTRUCT:
                return self._construct_ansatz(invocation)
            if capability_name == VQE_EXECUTE:
                return self._execute_vqe(invocation)
            if capability_name == STATEVECTOR_SIMULATE:
                return self._simulate_statevector(invocation)
        except (ValidationError, ValueError, KeyError, IndexError):
            return self._failure(
                "quantum_input_invalid",
                "The quantum-workflow input failed controlled validation.",
            )
        except ImportError:
            return self._failure(
                "dependency_missing",
                "The optional Qiskit quantum-workflow dependency is unavailable.",
            )
        except Exception:
            return self._failure(
                "quantum_execution_failed",
                "The quantum-workflow capability failed without valid evidence.",
            )
        return self._failure(
            "capability_not_implemented",
            "The requested quantum-workflow capability is not implemented.",
        )

    def _read_payload(self, reference: ArtifactReference) -> bytes:
        payload = self._payload_store.read(reference)
        if not isinstance(payload, bytes):
            raise ValueError("Quantum artifact payload stores must return bytes.")
        if not payload or len(payload) > self._maximum_payload_bytes:
            raise ValueError("Quantum artifact payload size is invalid.")
        if hashlib.sha256(payload).hexdigest() != reference.content_sha256:
            raise ValueError("Quantum artifact bytes do not match their SHA-256.")
        if reference.byte_size is not None and reference.byte_size != len(payload):
            raise ValueError("Quantum artifact bytes do not match their declared size.")
        return payload

    def _artifact_reference(
        self,
        *,
        invocation: CapabilityInvocation,
        artifact_type: str,
        payload: bytes,
        identifier_prefix: str,
        parents: tuple[ArtifactReference, ...],
        metadata: Mapping[str, str | int | float | bool],
    ) -> ArtifactReference:
        if not payload or len(payload) > self._maximum_payload_bytes:
            raise ValueError("Generated quantum artifact payload size is invalid.")
        digest = hashlib.sha256(payload).hexdigest()
        identity_digest = hashlib.sha256(
            (
                f"{invocation.capability.capability_name}:"
                f"{invocation.context.execution_id}:{digest}"
            ).encode("utf-8")
        ).hexdigest()
        return ArtifactReference(
            artifact_identifier=f"{identifier_prefix}-{identity_digest[:32]}",
            schema_version=_SCHEMA_VERSION,
            artifact_type=artifact_type,
            media_type="application/json",
            content_sha256=digest,
            byte_size=len(payload),
            storage_location=None,
            metadata=dict(metadata),
            provenance=CreationProvenance(
                producer=invocation.capability.capability_name,
                producer_version=invocation.capability.version,
                execution_identifier=invocation.context.execution_id,
            ),
            parents=tuple(parent.pointer for parent in parents),
        )

    def _write_model(
        self,
        invocation: CapabilityInvocation,
        *,
        model: object,
        artifact_type: str,
        identifier_prefix: str,
        parents: tuple[ArtifactReference, ...],
        metadata: Mapping[str, str | int | float | bool],
    ) -> ArtifactReference:
        payload = model.to_canonical_json().encode("utf-8")
        reference = self._artifact_reference(
            invocation=invocation,
            artifact_type=artifact_type,
            payload=payload,
            identifier_prefix=identifier_prefix,
            parents=parents,
            metadata=metadata,
        )
        self._payload_store.write(reference, payload)
        return reference

    def _success(
        self,
        invocation: CapabilityInvocation,
        *,
        artifacts: tuple[ArtifactReference, ...],
        lineage: tuple[ArtifactLineageEdge, ...],
        diagnostics: Mapping[str, str | int | float | bool] | None = None,
    ) -> CapabilityResult:
        versions = _runtime_versions() or {}
        return CapabilityResult(
            status=ExecutionStatus.SUCCESS,
            output_artifacts=artifacts,
            lineage=lineage,
            diagnostics=dict(diagnostics or {}),
            execution_evidence=ExecutionEvidence(
                runtime_identifier="qiskit_nature.local",
                exit_code=0,
                evidence_artifacts=tuple(artifact.pointer for artifact in artifacts),
                details={
                    "capability_name": invocation.capability.capability_name,
                    "qiskit_version": versions.get("qiskit", "unavailable"),
                    "qiskit_nature_version": versions.get(
                        "qiskit-nature", "unavailable"
                    ),
                    "qiskit_algorithms_version": versions.get(
                        "qiskit-algorithms", "unavailable"
                    ),
                    "native_objects_public": False,
                    "ibm_submission_performed": False,
                },
            ),
        )

    @staticmethod
    def _failure(
        code: str,
        message: str,
        *,
        retryable: bool = False,
    ) -> CapabilityResult:
        return CapabilityResult(
            status=ExecutionStatus.FAILED,
            failure=FailureInformation(
                code=code,
                message=message,
                retryable=retryable,
            ),
        )

    @staticmethod
    def _parameter(
        invocation: CapabilityInvocation,
        name: str,
        *,
        required: bool = False,
    ) -> object:
        parameters = dict(invocation.parameters)
        if name not in parameters:
            if required:
                raise ValueError(f"Missing required quantum parameter: {name}.")
            return None
        return parameters[name]

    @classmethod
    def _string_parameter(
        cls,
        invocation: CapabilityInvocation,
        name: str,
        *,
        maximum_length: int = 4096,
    ) -> str:
        value = cls._parameter(invocation, name, required=True)
        if not isinstance(value, str):
            raise ValueError(f"Quantum parameter {name} must be text.")
        normalized = value.strip().lower()
        if not normalized or len(normalized) > maximum_length:
            raise ValueError(f"Quantum parameter {name} is invalid.")
        return normalized

    @classmethod
    def _float_parameter(
        cls,
        invocation: CapabilityInvocation,
        name: str,
        *,
        minimum: float,
        maximum: float,
    ) -> float:
        value = cls._parameter(invocation, name, required=True)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"Quantum parameter {name} must be numeric.")
        normalized = float(value)
        if not math.isfinite(normalized) or not minimum <= normalized <= maximum:
            raise ValueError(f"Quantum parameter {name} is outside its allowed range.")
        return normalized

    @classmethod
    def _integer_parameter(
        cls,
        invocation: CapabilityInvocation,
        name: str,
        *,
        minimum: int,
        maximum: int,
    ) -> int:
        value = cls._parameter(invocation, name, required=True)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"Quantum parameter {name} must be an integer.")
        if not minimum <= value <= maximum:
            raise ValueError(f"Quantum parameter {name} is outside its allowed range.")
        return value

    @classmethod
    def _boolean_parameter(
        cls,
        invocation: CapabilityInvocation,
        name: str,
    ) -> bool:
        value = cls._parameter(invocation, name, required=True)
        if not isinstance(value, bool):
            raise ValueError(f"Quantum parameter {name} must be boolean.")
        return value

    @staticmethod
    def _inputs_by_type(
        invocation: CapabilityInvocation,
    ) -> dict[str, ArtifactReference]:
        values: dict[str, ArtifactReference] = {}
        for reference in invocation.input_artifacts:
            if reference.artifact_type in values:
                raise ValueError(
                    "Quantum capabilities accept at most one artifact per type."
                )
            values[reference.artifact_type] = reference
        return values

    @staticmethod
    def _require_input(
        inputs: Mapping[str, ArtifactReference],
        artifact_type: str,
    ) -> ArtifactReference:
        try:
            return inputs[artifact_type]
        except KeyError:
            raise ValueError(
                f"Quantum capability requires a {artifact_type} artifact."
            ) from None

    def _load_model(self, reference: ArtifactReference, model_type: object) -> object:
        return model_type.model_validate_json(self._read_payload(reference))

    @staticmethod
    def _lineage(
        invocation: CapabilityInvocation,
        *,
        parents: tuple[ArtifactReference, ...],
        child: ArtifactReference,
        relationship_type: str,
    ) -> tuple[ArtifactLineageEdge, ...]:
        return tuple(
            ArtifactLineageEdge(
                source=parent.pointer,
                destination=child.pointer,
                relationship_type=relationship_type,
                producing_capability=invocation.capability.capability_name,
                producing_capability_version=invocation.capability.version,
                execution_identifier=invocation.context.execution_id,
            )
            for parent in parents
        )

    def _construct_hamiltonian(
        self,
        invocation: CapabilityInvocation,
    ) -> CapabilityResult:
        inputs = self._inputs_by_type(invocation)
        active_reference = self._require_input(inputs, "electronic_active_space")
        if len(inputs) != 1:
            raise ValueError(
                "Second-quantized Hamiltonian construction requires one active space."
            )
        active = self._load_model(active_reference, ElectronicActiveSpace)
        tolerance = self._float_parameter(
            invocation,
            "integral_symmetry_tolerance",
            minimum=1e-14,
            maximum=1e-4,
        )
        if active.one_body_integrals.index_convention != "active_pq":
            raise ValueError("Active one-body integrals use an unsupported convention.")
        if active.two_body_integrals.index_convention != "chemist_active_pqrs":
            raise ValueError("Active two-body integrals use an unsupported convention.")

        numpy, _, electronic_energy_type, _, _ = _qiskit_modules()
        n = active.active_spatial_orbital_count
        one_body = numpy.asarray(active.one_body_integrals.values, dtype=float).reshape(
            (n, n)
        )
        two_body = numpy.asarray(active.two_body_integrals.values, dtype=float).reshape(
            (n, n, n, n)
        )
        one_body_residual = float(numpy.max(numpy.abs(one_body - one_body.T)))
        two_body_pair_residual = float(
            numpy.max(numpy.abs(two_body - two_body.transpose(2, 3, 0, 1)))
        )
        two_body_exchange_residual = float(
            numpy.max(numpy.abs(two_body - two_body.transpose(1, 0, 3, 2)))
        )
        maximum_integral_residual = max(
            one_body_residual,
            two_body_pair_residual,
            two_body_exchange_residual,
        )
        if (
            not math.isfinite(maximum_integral_residual)
            or maximum_integral_residual > tolerance
        ):
            raise ValueError(
                "Active-space integrals fail the declared symmetry tolerance."
            )

        electronic_energy = electronic_energy_type.from_raw_integrals(
            one_body,
            two_body,
            auto_index_order=True,
        )
        fermionic = electronic_energy.second_q_op().simplify(atol=tolerance)
        terms = _fermionic_terms(fermionic.items())
        if not terms:
            raise ValueError("Qiskit Nature produced an empty fermionic Hamiltonian.")
        register_length = int(fermionic.register_length)
        if register_length != active.active_spin_orbital_count:
            raise ValueError(
                "Qiskit Nature fermionic register length differs from the active space."
            )

        hamiltonian = SecondQuantizedHamiltonian(
            schema_version=_SCHEMA_VERSION,
            hamiltonian_identifier=_stable_identifier(
                "second-quantized-hamiltonian",
                active.active_space_identifier,
                active_reference.content_sha256,
                tolerance,
            ),
            active_space_identifier=active.active_space_identifier,
            active_space_sha256=active_reference.content_sha256,
            molecule_identifier=active.molecule_identifier,
            active_electron_count=active.active_electron_count,
            alpha_electron_count=active.alpha_electron_count,
            beta_electron_count=active.beta_electron_count,
            active_spatial_orbital_count=active.active_spatial_orbital_count,
            active_spin_orbital_count=active.active_spin_orbital_count,
            constant_energy_hartree=active.constant_energy_hartree,
            register_length=register_length,
            integral_symmetry_tolerance=tolerance,
            terms=terms,
        )
        parents = (active_reference,)
        output = self._write_model(
            invocation,
            model=hamiltonian,
            artifact_type="second_quantized_hamiltonian",
            identifier_prefix="second-quantized-hamiltonian",
            parents=parents,
            metadata={
                "active_electron_count": active.active_electron_count,
                "active_spatial_orbital_count": n,
                "register_length": register_length,
                "term_count": len(terms),
                "maximum_integral_symmetry_residual": maximum_integral_residual,
            },
        )
        return self._success(
            invocation,
            artifacts=(output,),
            lineage=self._lineage(
                invocation,
                parents=parents,
                child=output,
                relationship_type="second_quantizes",
            ),
            diagnostics={
                "term_count": len(terms),
                "register_length": register_length,
                "maximum_integral_symmetry_residual": maximum_integral_residual,
            },
        )

    def _map_hamiltonian(
        self,
        invocation: CapabilityInvocation,
    ) -> CapabilityResult:
        inputs = self._inputs_by_type(invocation)
        hamiltonian_reference = self._require_input(
            inputs, "second_quantized_hamiltonian"
        )
        if len(inputs) != 1:
            raise ValueError("Fermion-to-qubit mapping requires one Hamiltonian.")
        hamiltonian = self._load_model(
            hamiltonian_reference, SecondQuantizedHamiltonian
        )
        mapper_name = self._string_parameter(invocation, "mapper")
        if mapper_name != "jordan_wigner":
            raise ValueError("Version 1 supports only the Jordan-Wigner mapper.")
        tolerance = self._float_parameter(
            invocation,
            "hermiticity_tolerance",
            minimum=1e-14,
            maximum=1e-4,
        )

        _, _, _, mapper_type, fermionic_type = _qiskit_modules()
        native_fermionic = fermionic_type(
            {term.label: term.coefficient.value for term in hamiltonian.terms},
            num_spin_orbitals=hamiltonian.register_length,
        )
        mapper = mapper_type()
        native_qubit = mapper.map(native_fermionic).simplify(atol=tolerance)
        terms = _pauli_terms(native_qubit.to_list())
        if not terms:
            raise ValueError("Qiskit produced an empty mapped Hamiltonian.")
        maximum_antihermitian = _maximum_antihermitian_coefficient(native_qubit)
        if maximum_antihermitian > tolerance:
            raise ValueError("Mapped Hamiltonian fails the Hermiticity tolerance.")

        mapped = MappedQubitHamiltonian(
            schema_version=_SCHEMA_VERSION,
            mapping_identifier=_stable_identifier(
                "mapped-qubit-hamiltonian",
                hamiltonian.hamiltonian_identifier,
                hamiltonian_reference.content_sha256,
                mapper_name,
                tolerance,
            ),
            second_quantized_hamiltonian_identifier=(
                hamiltonian.hamiltonian_identifier
            ),
            second_quantized_hamiltonian_sha256=(hamiltonian_reference.content_sha256),
            active_space_identifier=hamiltonian.active_space_identifier,
            active_space_sha256=hamiltonian.active_space_sha256,
            molecule_identifier=hamiltonian.molecule_identifier,
            mapper="jordan_wigner",
            active_electron_count=hamiltonian.active_electron_count,
            alpha_electron_count=hamiltonian.alpha_electron_count,
            beta_electron_count=hamiltonian.beta_electron_count,
            active_spatial_orbital_count=(hamiltonian.active_spatial_orbital_count),
            active_spin_orbital_count=hamiltonian.active_spin_orbital_count,
            number_of_qubits=int(native_qubit.num_qubits),
            constant_energy_hartree=hamiltonian.constant_energy_hartree,
            hermiticity_tolerance=tolerance,
            maximum_antihermitian_coefficient=maximum_antihermitian,
            terms=terms,
        )
        parents = (hamiltonian_reference,)
        output = self._write_model(
            invocation,
            model=mapped,
            artifact_type="mapped_qubit_hamiltonian",
            identifier_prefix="mapped-qubit-hamiltonian",
            parents=parents,
            metadata={
                "mapper": mapped.mapper,
                "number_of_qubits": mapped.number_of_qubits,
                "term_count": len(terms),
                "maximum_antihermitian_coefficient": maximum_antihermitian,
            },
        )
        return self._success(
            invocation,
            artifacts=(output,),
            lineage=self._lineage(
                invocation,
                parents=parents,
                child=output,
                relationship_type="maps_to_qubits",
            ),
            diagnostics={
                "mapper": mapped.mapper,
                "number_of_qubits": mapped.number_of_qubits,
                "term_count": len(terms),
                "maximum_antihermitian_coefficient": maximum_antihermitian,
            },
        )

    def _exact_diagonalize(
        self,
        invocation: CapabilityInvocation,
    ) -> CapabilityResult:
        inputs = self._inputs_by_type(invocation)
        mapped_reference = self._require_input(inputs, "mapped_qubit_hamiltonian")
        if len(inputs) != 1:
            raise ValueError("Exact diagonalization requires one mapped Hamiltonian.")
        mapped = self._load_model(mapped_reference, MappedQubitHamiltonian)
        solver = self._string_parameter(invocation, "solver")
        if solver != "numpy_eigh_particle_sector":
            raise ValueError("Version 1 supports only the bounded NumPy exact solver.")
        particle_tolerance = self._float_parameter(
            invocation,
            "particle_number_tolerance",
            minimum=1e-12,
            maximum=1e-3,
        )
        hermiticity_tolerance = self._float_parameter(
            invocation,
            "hermiticity_tolerance",
            minimum=1e-14,
            maximum=1e-4,
        )
        if mapped.mapper != "jordan_wigner":
            raise ValueError(
                "Particle-sector matrix restriction requires Jordan-Wigner mapping."
            )
        if mapped.number_of_qubits > _MAXIMUM_EXACT_QUBITS:
            raise ValueError("Mapped Hamiltonian exceeds the exact-solver qubit bound.")
        full_dimension = 1 << mapped.number_of_qubits
        if full_dimension > _MAXIMUM_EXACT_MATRIX_DIMENSION:
            raise ValueError("Mapped Hamiltonian exceeds the exact matrix bound.")

        numpy, sparse_pauli_type, _, _, _ = _qiskit_modules()
        native_qubit = sparse_pauli_type.from_list(
            [(term.label, term.coefficient.value) for term in mapped.terms]
        )
        if int(native_qubit.num_qubits) != mapped.number_of_qubits:
            raise ValueError("Reconstructed qubit count differs from the artifact.")
        maximum_antihermitian = _maximum_antihermitian_coefficient(native_qubit)
        if maximum_antihermitian > min(
            mapped.hermiticity_tolerance, hermiticity_tolerance
        ):
            raise ValueError("Exact solver received a non-Hermitian Hamiltonian.")

        matrix = numpy.asarray(native_qubit.to_matrix(), dtype=complex)
        if matrix.shape != (full_dimension, full_dimension):
            raise ValueError("Mapped Hamiltonian matrix has an invalid shape.")
        n = mapped.active_spatial_orbital_count
        alpha_mask = (1 << n) - 1
        beta_mask = alpha_mask << n
        sector_indices = tuple(
            index
            for index in range(full_dimension)
            if (index & alpha_mask).bit_count() == mapped.alpha_electron_count
            and ((index & beta_mask) >> n).bit_count() == mapped.beta_electron_count
        )
        expected_sector = comb(n, mapped.alpha_electron_count) * comb(
            n, mapped.beta_electron_count
        )
        if len(sector_indices) != expected_sector or not sector_indices:
            raise ValueError("Declared particle sector could not be constructed.")
        sector = matrix[numpy.ix_(sector_indices, sector_indices)]
        sector_index_set = set(sector_indices)
        outside_indices = tuple(
            index for index in range(full_dimension) if index not in sector_index_set
        )
        particle_sector_leakage_hartree = 0.0
        if outside_indices:
            coupling = matrix[numpy.ix_(sector_indices, outside_indices)]
            particle_sector_leakage_hartree = float(numpy.max(numpy.abs(coupling)))
        if (
            not math.isfinite(particle_sector_leakage_hartree)
            or particle_sector_leakage_hartree > particle_tolerance
        ):
            raise ValueError(
                "Hamiltonian does not preserve the declared particle sector."
            )

        eigenvalues, eigenvectors = numpy.linalg.eigh(sector)
        if len(eigenvalues) == 0:
            raise ValueError("Exact diagonalization produced no eigenvalues.")
        raw_eigenvalue = complex(eigenvalues[0])
        if abs(raw_eigenvalue.imag) > hermiticity_tolerance:
            raise ValueError(
                "Exact ground-state eigenvalue has an imaginary component."
            )
        vector = eigenvectors[:, 0]
        residual = sector @ vector - raw_eigenvalue.real * vector
        maximum_residual = float(numpy.max(numpy.abs(residual)))
        if not math.isfinite(maximum_residual):
            raise ValueError("Exact eigenpair residual is non-finite.")

        observed_alpha = float(mapped.alpha_electron_count)
        observed_beta = float(mapped.beta_electron_count)
        total_energy = float(raw_eigenvalue.real + mapped.constant_energy_hartree)
        result = ExactDiagonalizationResult(
            schema_version=_SCHEMA_VERSION,
            result_identifier=_stable_identifier(
                "exact-diagonalization",
                mapped.mapping_identifier,
                mapped_reference.content_sha256,
                solver,
                particle_tolerance,
                hermiticity_tolerance,
            ),
            mapped_hamiltonian_identifier=mapped.mapping_identifier,
            mapped_hamiltonian_sha256=mapped_reference.content_sha256,
            active_space_identifier=mapped.active_space_identifier,
            active_space_sha256=mapped.active_space_sha256,
            solver_identifier="numpy_eigh_particle_sector",
            solver_version=_installed_version("numpy") or "unknown",
            mapper=mapped.mapper,
            number_of_qubits=mapped.number_of_qubits,
            active_spatial_orbital_count=mapped.active_spatial_orbital_count,
            alpha_electron_count=mapped.alpha_electron_count,
            beta_electron_count=mapped.beta_electron_count,
            active_electron_count=mapped.active_electron_count,
            full_hilbert_space_dimension=full_dimension,
            particle_sector_dimension=len(sector_indices),
            particle_number_tolerance=particle_tolerance,
            observed_alpha_electron_count=observed_alpha,
            observed_beta_electron_count=observed_beta,
            raw_active_space_eigenvalue_hartree=float(raw_eigenvalue.real),
            constant_energy_hartree=mapped.constant_energy_hartree,
            total_energy_hartree=total_energy,
            maximum_eigenpair_residual_hartree=maximum_residual,
            particle_sector_leakage_hartree=particle_sector_leakage_hartree,
        )
        parents = (mapped_reference,)
        output = self._write_model(
            invocation,
            model=result,
            artifact_type="exact_diagonalization_result",
            identifier_prefix="exact-diagonalization-result",
            parents=parents,
            metadata={
                "solver": result.solver_identifier,
                "number_of_qubits": result.number_of_qubits,
                "particle_sector_dimension": result.particle_sector_dimension,
                "total_energy_hartree": result.total_energy_hartree,
                "maximum_eigenpair_residual_hartree": maximum_residual,
            },
        )
        return self._success(
            invocation,
            artifacts=(output,),
            lineage=self._lineage(
                invocation,
                parents=parents,
                child=output,
                relationship_type="diagonalizes",
            ),
            diagnostics={
                "solver": result.solver_identifier,
                "full_hilbert_space_dimension": full_dimension,
                "particle_sector_dimension": len(sector_indices),
                "total_energy_hartree": total_energy,
                "maximum_eigenpair_residual_hartree": maximum_residual,
                "particle_sector_leakage_hartree": particle_sector_leakage_hartree,
            },
        )

    def _construct_ansatz(
        self,
        invocation: CapabilityInvocation,
    ) -> CapabilityResult:
        inputs = self._inputs_by_type(invocation)
        mapped_reference = self._require_input(inputs, "mapped_qubit_hamiltonian")
        if len(inputs) != 1:
            raise ValueError("Ansatz construction requires one mapped Hamiltonian.")
        mapped = self._load_model(mapped_reference, MappedQubitHamiltonian)

        ansatz_name = self._string_parameter(invocation, "ansatz")
        initial_state_name = self._string_parameter(invocation, "initial_state")
        initial_point_policy = self._string_parameter(
            invocation, "initial_point_policy"
        )
        repetitions = self._integer_parameter(
            invocation, "repetitions", minimum=1, maximum=1
        )
        generalized = self._boolean_parameter(invocation, "generalized")
        preserve_spin = self._boolean_parameter(invocation, "preserve_spin")
        include_imaginary = self._boolean_parameter(invocation, "include_imaginary")
        if ansatz_name != "uccsd":
            raise ValueError("Version 1 supports only the UCCSD ansatz.")
        if initial_state_name != "hartree_fock":
            raise ValueError("Version 1 supports only the Hartree-Fock initial state.")
        if initial_point_policy != "all_zeros":
            raise ValueError("Version 1 supports only an all-zero initial point.")
        if repetitions != 1 or generalized or not preserve_spin or include_imaginary:
            raise ValueError("The requested ansatz settings are unsupported.")

        (
            _,
            _,
            _,
            _,
            _,
            _,
            _,
            hartree_fock_type,
            uccsd_type,
            mapper_type,
        ) = _variational_modules()
        _, native_ansatz = _native_variational_ansatz(
            mapped,
            hartree_fock_type=hartree_fock_type,
            uccsd_type=uccsd_type,
            mapper_type=mapper_type,
        )
        number_of_parameters = int(native_ansatz.num_parameters)
        if number_of_parameters <= 0:
            raise ValueError("The constructed ansatz has no variational parameters.")
        initial_parameters = _real_parameters((0.0,) * number_of_parameters)

        ansatz = VariationalAnsatz(
            schema_version=_SCHEMA_VERSION,
            ansatz_identifier=_stable_identifier(
                "variational-ansatz",
                mapped.mapping_identifier,
                mapped_reference.content_sha256,
                ansatz_name,
                initial_state_name,
                repetitions,
                generalized,
                preserve_spin,
                include_imaginary,
                initial_point_policy,
            ),
            mapped_hamiltonian_identifier=mapped.mapping_identifier,
            mapped_hamiltonian_sha256=mapped_reference.content_sha256,
            active_space_identifier=mapped.active_space_identifier,
            active_space_sha256=mapped.active_space_sha256,
            molecule_identifier=mapped.molecule_identifier,
            mapper=mapped.mapper,
            number_of_qubits=mapped.number_of_qubits,
            active_spatial_orbital_count=mapped.active_spatial_orbital_count,
            active_electron_count=mapped.active_electron_count,
            alpha_electron_count=mapped.alpha_electron_count,
            beta_electron_count=mapped.beta_electron_count,
            ansatz="uccsd",
            initial_state="hartree_fock",
            repetitions=1,
            generalized=False,
            preserve_spin=True,
            include_imaginary=False,
            initial_point_policy="all_zeros",
            number_of_parameters=number_of_parameters,
            initial_parameters=initial_parameters,
        )
        parents = (mapped_reference,)
        output = self._write_model(
            invocation,
            model=ansatz,
            artifact_type="variational_ansatz",
            identifier_prefix="variational-ansatz",
            parents=parents,
            metadata={
                "ansatz": ansatz.ansatz,
                "initial_state": ansatz.initial_state,
                "number_of_qubits": ansatz.number_of_qubits,
                "number_of_parameters": ansatz.number_of_parameters,
            },
        )
        return self._success(
            invocation,
            artifacts=(output,),
            lineage=self._lineage(
                invocation,
                parents=parents,
                child=output,
                relationship_type="constructs_variational_ansatz",
            ),
            diagnostics={
                "ansatz": ansatz.ansatz,
                "initial_state": ansatz.initial_state,
                "number_of_qubits": ansatz.number_of_qubits,
                "number_of_parameters": ansatz.number_of_parameters,
            },
        )

    def _execute_vqe(
        self,
        invocation: CapabilityInvocation,
    ) -> CapabilityResult:
        inputs = self._inputs_by_type(invocation)
        mapped_reference = self._require_input(inputs, "mapped_qubit_hamiltonian")
        ansatz_reference = self._require_input(inputs, "variational_ansatz")
        if len(inputs) != 2:
            raise ValueError(
                "VQE requires exactly one mapped Hamiltonian and one ansatz."
            )
        mapped = self._load_model(mapped_reference, MappedQubitHamiltonian)
        ansatz_spec = self._load_model(ansatz_reference, VariationalAnsatz)
        if (
            ansatz_spec.mapped_hamiltonian_identifier != mapped.mapping_identifier
            or ansatz_spec.mapped_hamiltonian_sha256 != mapped_reference.content_sha256
            or ansatz_spec.active_space_sha256 != mapped.active_space_sha256
        ):
            raise ValueError("VQE ansatz lineage does not match the Hamiltonian.")

        optimizer_name = self._string_parameter(invocation, "optimizer")
        estimator_name = self._string_parameter(invocation, "estimator")
        maximum_iterations = self._integer_parameter(
            invocation, "maximum_iterations", minimum=1, maximum=10_000
        )
        convergence_threshold = self._float_parameter(
            invocation,
            "convergence_threshold",
            minimum=1e-12,
            maximum=1e-2,
        )
        random_seed = self._integer_parameter(
            invocation, "random_seed", minimum=0, maximum=2**32 - 1
        )
        if optimizer_name != "slsqp":
            raise ValueError("Version 1 supports only the SLSQP optimizer.")
        if estimator_name != "exact_statevector_expectation":
            raise ValueError(
                "Version 1 supports only exact statevector expectation values."
            )

        (
            numpy,
            sparse_pauli_type,
            _,
            estimator_type,
            vqe_type,
            optimizer_type,
            algorithm_globals,
            hartree_fock_type,
            uccsd_type,
            mapper_type,
        ) = _variational_modules()
        native_qubit = _native_mapped_operator(mapped, sparse_pauli_type)
        _, native_ansatz = _native_variational_ansatz(
            mapped,
            hartree_fock_type=hartree_fock_type,
            uccsd_type=uccsd_type,
            mapper_type=mapper_type,
        )
        if int(native_ansatz.num_parameters) != ansatz_spec.number_of_parameters:
            raise ValueError("Reconstructed ansatz parameter count is inconsistent.")
        initial_point = numpy.asarray(
            [parameter.value for parameter in ansatz_spec.initial_parameters],
            dtype=float,
        )
        if len(initial_point) != ansatz_spec.number_of_parameters:
            raise ValueError("VQE initial point has an invalid length.")

        trace: list[OptimizationEvaluation] = []

        def callback(
            evaluation: int,
            parameters: object,
            mean: object,
            metadata: object,
        ) -> None:
            del metadata
            if len(trace) >= _MAXIMUM_OPTIMIZATION_EVALUATIONS:
                raise ValueError("VQE optimization trace exceeds its bound.")
            parameter_values = _real_parameters(float(value) for value in parameters)
            energy = complex(mean)
            if abs(energy.imag) > mapped.hermiticity_tolerance:
                raise ValueError("VQE evaluation energy has an imaginary component.")
            trace.append(
                OptimizationEvaluation(
                    evaluation=int(evaluation),
                    raw_active_space_energy_hartree=float(energy.real),
                    parameter_sha256=_parameter_hash(parameter_values),
                )
            )

        algorithm_globals.random_seed = random_seed
        optimizer = optimizer_type(
            maxiter=maximum_iterations,
            ftol=convergence_threshold,
            disp=False,
        )
        solver = vqe_type(
            estimator=estimator_type(seed=random_seed),
            ansatz=native_ansatz,
            optimizer=optimizer,
            initial_point=initial_point,
            callback=callback,
        )
        raw = solver.compute_minimum_eigenvalue(native_qubit)
        raw_energy = complex(raw.eigenvalue)
        if abs(raw_energy.imag) > mapped.hermiticity_tolerance:
            raise ValueError("VQE result energy has an imaginary component.")

        optimized = _real_parameters(
            float(value) for value in getattr(raw, "optimal_point", ())
        )
        if len(optimized) != ansatz_spec.number_of_parameters:
            raise ValueError("VQE optimized parameter count is inconsistent.")
        if not trace:
            raise ValueError("VQE produced no optimization trace.")

        optimizer_result = getattr(raw, "optimizer_result", None)
        success = getattr(optimizer_result, "success", None)
        if success is False:
            raise ValueError("VQE optimizer did not converge.")
        status = getattr(optimizer_result, "status", None)
        status_identifier = "completed" if status is None else f"status_{int(status)}"
        evaluations = max(
            int(getattr(raw, "cost_function_evals", len(trace))),
            len(trace),
            1,
        )
        if evaluations > _MAXIMUM_OPTIMIZATION_EVALUATIONS:
            raise ValueError("VQE optimizer evaluation count exceeds its bound.")

        total_energy = float(raw_energy.real + mapped.constant_energy_hartree)
        result = VariationalGroundStateResult(
            schema_version=_SCHEMA_VERSION,
            result_identifier=_stable_identifier(
                "variational-ground-state",
                mapped.mapping_identifier,
                mapped_reference.content_sha256,
                ansatz_spec.ansatz_identifier,
                ansatz_reference.content_sha256,
                optimizer_name,
                estimator_name,
                maximum_iterations,
                convergence_threshold,
                random_seed,
                _parameter_hash(optimized),
            ),
            mapped_hamiltonian_identifier=mapped.mapping_identifier,
            mapped_hamiltonian_sha256=mapped_reference.content_sha256,
            ansatz_identifier=ansatz_spec.ansatz_identifier,
            ansatz_sha256=ansatz_reference.content_sha256,
            active_space_identifier=mapped.active_space_identifier,
            active_space_sha256=mapped.active_space_sha256,
            algorithm_identifier="variational_quantum_eigensolver",
            estimator_identifier="exact_statevector_expectation",
            optimizer_identifier="slsqp",
            optimizer_version=_installed_version("qiskit-algorithms") or "unavailable",
            optimizer_status=status_identifier,
            maximum_iterations=maximum_iterations,
            convergence_threshold=convergence_threshold,
            random_seed=random_seed,
            number_of_qubits=mapped.number_of_qubits,
            number_of_parameters=ansatz_spec.number_of_parameters,
            optimizer_evaluations=evaluations,
            initial_point_sha256=_parameter_hash(ansatz_spec.initial_parameters),
            optimized_parameters=optimized,
            optimized_parameters_sha256=_parameter_hash(optimized),
            raw_active_space_energy_hartree=float(raw_energy.real),
            constant_energy_hartree=mapped.constant_energy_hartree,
            total_energy_hartree=total_energy,
            reference_energy_used=False,
            converged=True,
            trace=tuple(trace),
        )
        parents = (mapped_reference, ansatz_reference)
        output = self._write_model(
            invocation,
            model=result,
            artifact_type="variational_ground_state_result",
            identifier_prefix="variational-ground-state-result",
            parents=parents,
            metadata={
                "optimizer": result.optimizer_identifier,
                "estimator": result.estimator_identifier,
                "number_of_qubits": result.number_of_qubits,
                "number_of_parameters": result.number_of_parameters,
                "optimizer_evaluations": result.optimizer_evaluations,
                "total_energy_hartree": result.total_energy_hartree,
                "reference_energy_used": False,
            },
        )
        return self._success(
            invocation,
            artifacts=(output,),
            lineage=self._lineage(
                invocation,
                parents=parents,
                child=output,
                relationship_type="optimizes_variational_ground_state",
            ),
            diagnostics={
                "optimizer": result.optimizer_identifier,
                "estimator": result.estimator_identifier,
                "optimizer_evaluations": result.optimizer_evaluations,
                "total_energy_hartree": result.total_energy_hartree,
                "reference_energy_used": False,
            },
        )

    def _simulate_statevector(
        self,
        invocation: CapabilityInvocation,
    ) -> CapabilityResult:
        inputs = self._inputs_by_type(invocation)
        mapped_reference = self._require_input(inputs, "mapped_qubit_hamiltonian")
        ansatz_reference = self._require_input(inputs, "variational_ansatz")
        vqe_reference = self._require_input(inputs, "variational_ground_state_result")
        if len(inputs) != 3:
            raise ValueError(
                "Statevector simulation requires one Hamiltonian, ansatz and VQE result."
            )
        mapped = self._load_model(mapped_reference, MappedQubitHamiltonian)
        ansatz_spec = self._load_model(ansatz_reference, VariationalAnsatz)
        vqe = self._load_model(vqe_reference, VariationalGroundStateResult)
        if mapped.number_of_qubits > _MAXIMUM_STATEVECTOR_QUBITS:
            raise ValueError("Variational state exceeds the statevector qubit bound.")
        if (
            ansatz_spec.mapped_hamiltonian_sha256 != mapped_reference.content_sha256
            or vqe.mapped_hamiltonian_sha256 != mapped_reference.content_sha256
            or vqe.ansatz_sha256 != ansatz_reference.content_sha256
            or vqe.active_space_sha256 != mapped.active_space_sha256
        ):
            raise ValueError("Statevector input lineage is inconsistent.")

        normalization_tolerance = self._float_parameter(
            invocation,
            "normalization_tolerance",
            minimum=1e-14,
            maximum=1e-4,
        )
        particle_sector_tolerance = self._float_parameter(
            invocation,
            "particle_sector_tolerance",
            minimum=1e-14,
            maximum=1e-4,
        )
        energy_consistency_tolerance = self._float_parameter(
            invocation,
            "energy_consistency_tolerance",
            minimum=1e-12,
            maximum=1e-3,
        )
        phase_tolerance = self._float_parameter(
            invocation,
            "global_phase_tolerance",
            minimum=1e-16,
            maximum=1e-6,
        )

        (
            numpy,
            sparse_pauli_type,
            statevector_type,
            _,
            _,
            _,
            _,
            hartree_fock_type,
            uccsd_type,
            mapper_type,
        ) = _variational_modules()
        native_qubit = _native_mapped_operator(mapped, sparse_pauli_type)
        _, native_ansatz = _native_variational_ansatz(
            mapped,
            hartree_fock_type=hartree_fock_type,
            uccsd_type=uccsd_type,
            mapper_type=mapper_type,
        )
        if int(native_ansatz.num_parameters) != vqe.number_of_parameters:
            raise ValueError("Statevector ansatz parameter count is inconsistent.")
        point = numpy.asarray(
            [parameter.value for parameter in vqe.optimized_parameters],
            dtype=float,
        )
        bound = native_ansatz.assign_parameters(point, inplace=False)
        state = statevector_type.from_instruction(bound)
        data = numpy.asarray(state.data, dtype=complex)
        state_dimension = 1 << mapped.number_of_qubits
        if data.shape != (state_dimension,):
            raise ValueError("Statevector has an invalid dimension.")

        norm = float(numpy.vdot(data, data).real)
        normalization_residual = abs(norm - 1.0)
        if (
            not math.isfinite(normalization_residual)
            or normalization_residual > normalization_tolerance
        ):
            raise ValueError("Statevector fails its normalization tolerance.")

        expectation = complex(state.expectation_value(native_qubit))
        if abs(expectation.imag) > mapped.hermiticity_tolerance:
            raise ValueError(
                "Statevector Hamiltonian expectation has an imaginary component."
            )
        total_energy = float(expectation.real + mapped.constant_energy_hartree)
        energy_difference = abs(total_energy - vqe.total_energy_hartree)
        if energy_difference > energy_consistency_tolerance:
            raise ValueError("Statevector energy differs from the VQE result.")

        n = mapped.active_spatial_orbital_count
        alpha_mask = (1 << n) - 1
        beta_mask = alpha_mask << n
        probabilities = numpy.abs(data) ** 2
        sector_probability = 0.0
        observed_alpha = 0.0
        observed_beta = 0.0
        for index, probability_value in enumerate(probabilities):
            probability = float(probability_value)
            alpha_count = (index & alpha_mask).bit_count()
            beta_count = ((index & beta_mask) >> n).bit_count()
            observed_alpha += probability * alpha_count
            observed_beta += probability * beta_count
            if (
                alpha_count == mapped.alpha_electron_count
                and beta_count == mapped.beta_electron_count
            ):
                sector_probability += probability
        sector_probability = min(1.0, max(0.0, sector_probability))
        leakage_probability = max(0.0, 1.0 - sector_probability)
        if leakage_probability > particle_sector_tolerance:
            raise ValueError("Statevector leaks outside the declared particle sector.")

        anchor, amplitudes = _canonical_statevector(
            data,
            phase_tolerance=phase_tolerance,
        )
        result = StatevectorSimulationResult(
            schema_version=_SCHEMA_VERSION,
            result_identifier=_stable_identifier(
                "statevector-simulation",
                mapped.mapping_identifier,
                mapped_reference.content_sha256,
                ansatz_spec.ansatz_identifier,
                ansatz_reference.content_sha256,
                vqe.result_identifier,
                vqe_reference.content_sha256,
                normalization_tolerance,
                particle_sector_tolerance,
                energy_consistency_tolerance,
                phase_tolerance,
            ),
            mapped_hamiltonian_identifier=mapped.mapping_identifier,
            mapped_hamiltonian_sha256=mapped_reference.content_sha256,
            ansatz_identifier=ansatz_spec.ansatz_identifier,
            ansatz_sha256=ansatz_reference.content_sha256,
            vqe_result_identifier=vqe.result_identifier,
            vqe_result_sha256=vqe_reference.content_sha256,
            active_space_identifier=mapped.active_space_identifier,
            active_space_sha256=mapped.active_space_sha256,
            simulator_identifier="exact_statevector",
            number_of_qubits=mapped.number_of_qubits,
            active_spatial_orbital_count=mapped.active_spatial_orbital_count,
            active_electron_count=mapped.active_electron_count,
            alpha_electron_count=mapped.alpha_electron_count,
            beta_electron_count=mapped.beta_electron_count,
            state_dimension=state_dimension,
            global_phase_anchor_index=anchor,
            amplitudes=amplitudes,
            normalization_tolerance=normalization_tolerance,
            normalization_residual=normalization_residual,
            particle_sector_tolerance=particle_sector_tolerance,
            particle_sector_probability=sector_probability,
            particle_sector_leakage_probability=leakage_probability,
            observed_alpha_electron_count=observed_alpha,
            observed_beta_electron_count=observed_beta,
            raw_active_space_expectation_hartree=float(expectation.real),
            constant_energy_hartree=mapped.constant_energy_hartree,
            total_energy_hartree=total_energy,
            vqe_total_energy_hartree=vqe.total_energy_hartree,
            vqe_energy_difference_hartree=energy_difference,
            energy_consistency_tolerance=energy_consistency_tolerance,
        )
        parents = (mapped_reference, ansatz_reference, vqe_reference)
        output = self._write_model(
            invocation,
            model=result,
            artifact_type="statevector_simulation_result",
            identifier_prefix="statevector-simulation-result",
            parents=parents,
            metadata={
                "number_of_qubits": result.number_of_qubits,
                "state_dimension": result.state_dimension,
                "particle_sector_probability": result.particle_sector_probability,
                "total_energy_hartree": result.total_energy_hartree,
                "vqe_energy_difference_hartree": result.vqe_energy_difference_hartree,
            },
        )
        return self._success(
            invocation,
            artifacts=(output,),
            lineage=self._lineage(
                invocation,
                parents=parents,
                child=output,
                relationship_type="simulates_optimized_statevector",
            ),
            diagnostics={
                "number_of_qubits": result.number_of_qubits,
                "state_dimension": result.state_dimension,
                "normalization_residual": result.normalization_residual,
                "particle_sector_leakage_probability": (
                    result.particle_sector_leakage_probability
                ),
                "total_energy_hartree": result.total_energy_hartree,
                "vqe_energy_difference_hartree": result.vqe_energy_difference_hartree,
            },
        )
