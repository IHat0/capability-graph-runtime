"""Phase 5A generic Hamiltonian, mapping and exact-solver regressions."""

from __future__ import annotations

import hashlib
import math
import sys

import pytest
from pydantic import ValidationError

from cgr.electronic_structure import ElectronicActiveSpace, ElectronicTensor
from cgr.kernel.contracts import (
    CapabilityVersion,
    ExecutionContext,
    ExecutionStatus,
    HealthStatus,
)
from cgr.quantum_workflow import (
    EXACT_DIAGONALIZE,
    FERMION_TO_QUBIT_MAP,
    HAMILTONIAN_CONSTRUCT,
    ExactDiagonalizationResult,
    FermionicHamiltonianTerm,
    MappedQubitHamiltonian,
    PauliHamiltonianTerm,
    QiskitQuantumWorkflowAdapter,
    QuantumComplexCoefficient,
    SecondQuantizedHamiltonian,
    quantum_workflow_capability_envelopes,
)
from cgr.science import (
    ArtifactPointer,
    ArtifactReference,
    CapabilityInvocation,
    CreationProvenance,
)

VERSION = CapabilityVersion(major=1, minor=0, patch=0)
EXPERIMENT = ArtifactPointer(
    artifact_identifier="experiment.phase5a-quantum",
    content_sha256="e" * 64,
)


class MemoryPayloadStore:
    """Exact content-addressed payload store used by adapter regressions."""

    def __init__(self) -> None:
        self.payloads: dict[tuple[str, str], bytes] = {}

    def read(self, reference: ArtifactReference) -> bytes:
        return self.payloads[(reference.artifact_identifier, reference.content_sha256)]

    def write(self, reference: ArtifactReference, payload: bytes) -> None:
        assert hashlib.sha256(payload).hexdigest() == reference.content_sha256
        assert reference.byte_size == len(payload)
        key = (reference.artifact_identifier, reference.content_sha256)
        existing = self.payloads.get(key)
        assert existing is None or existing == payload
        self.payloads[key] = payload


def _source_reference(
    store: MemoryPayloadStore,
    identifier: str,
    artifact_type: str,
    model: object,
) -> ArtifactReference:
    payload = model.to_canonical_json().encode("utf-8")
    reference = ArtifactReference(
        artifact_identifier=identifier,
        schema_version=VERSION,
        artifact_type=artifact_type,
        media_type="application/json",
        content_sha256=hashlib.sha256(payload).hexdigest(),
        byte_size=len(payload),
        provenance=CreationProvenance(
            producer="test.fixture",
            producer_version=VERSION,
        ),
    )
    store.write(reference, payload)
    return reference


def _invocation(
    adapter: QiskitQuantumWorkflowAdapter,
    capability_name: str,
    *,
    inputs: tuple[ArtifactReference, ...],
    parameters: dict[str, object],
    execution_identifier: str,
) -> CapabilityInvocation:
    envelope = next(
        envelope
        for envelope in adapter.declaration.capabilities
        if envelope.descriptor.capability_name == capability_name
    )
    return CapabilityInvocation(
        capability=envelope.descriptor,
        input_artifacts=inputs,
        experiment=EXPERIMENT,
        context=ExecutionContext(execution_id=execution_identifier),
        parameters=parameters,
    )


def _synthetic_active_space() -> ElectronicActiveSpace:
    return ElectronicActiveSpace(
        schema_version=VERSION,
        active_space_identifier="active.synthetic-h2",
        molecule_identifier="molecule.synthetic-h2",
        hartree_fock_result_identifier="hf.synthetic-h2",
        active_electron_count=2,
        alpha_electron_count=1,
        beta_electron_count=1,
        active_spatial_orbital_count=2,
        active_spin_orbital_count=4,
        active_orbital_indices=(0, 1),
        constant_energy_hartree=0.7,
        one_body_integrals=ElectronicTensor(
            shape=(2, 2),
            values=(-1.0, 0.0, 0.0, -0.5),
            unit="hartree",
            index_convention="active_pq",
        ),
        two_body_integrals=ElectronicTensor(
            shape=(2, 2, 2, 2),
            values=(0.0,) * 16,
            unit="hartree",
            index_convention="chemist_active_pqrs",
        ),
    )


def _real_h2_active_space() -> tuple[ElectronicActiveSpace, float]:
    numpy = pytest.importorskip("numpy")
    pytest.importorskip("pyscf")
    from pyscf import ao2mo, fci, gto, scf

    molecule = gto.M(
        atom="H 0 0 0; H 0 0 0.74",
        basis="sto-3g",
        charge=0,
        spin=0,
        unit="Angstrom",
        verbose=0,
    )
    mean_field = scf.RHF(molecule)
    mean_field.conv_tol = 1e-12
    mean_field.kernel()
    assert mean_field.converged

    coefficients = numpy.asarray(mean_field.mo_coeff)
    one_body = coefficients.T @ mean_field.get_hcore() @ coefficients
    two_body = numpy.asarray(
        ao2mo.kernel(molecule, coefficients, compact=False)
    ).reshape((2, 2, 2, 2))
    constant = float(molecule.energy_nuc())
    electronic_fci, _ = fci.direct_spin1.kernel(
        one_body,
        two_body,
        2,
        (1, 1),
    )
    expected_total = float(electronic_fci + constant)

    active = ElectronicActiveSpace(
        schema_version=VERSION,
        active_space_identifier="active.real-h2",
        molecule_identifier="molecule.real-h2",
        hartree_fock_result_identifier="hf.real-h2",
        active_electron_count=2,
        alpha_electron_count=1,
        beta_electron_count=1,
        active_spatial_orbital_count=2,
        active_spin_orbital_count=4,
        active_orbital_indices=(0, 1),
        constant_energy_hartree=constant,
        one_body_integrals=ElectronicTensor(
            shape=(2, 2),
            values=tuple(float(value) for value in one_body.reshape(-1)),
            unit="hartree",
            index_convention="active_pq",
        ),
        two_body_integrals=ElectronicTensor(
            shape=(2, 2, 2, 2),
            values=tuple(float(value) for value in two_body.reshape(-1)),
            unit="hartree",
            index_convention="chemist_active_pqrs",
        ),
    )
    return active, expected_total


def test_public_declaration_does_not_import_qiskit() -> None:
    before = set(sys.modules)
    envelopes = quantum_workflow_capability_envelopes()
    after = set(sys.modules)

    assert len(envelopes) == 3
    assert {envelope.descriptor.capability_name for envelope in envelopes} == {
        HAMILTONIAN_CONSTRUCT,
        FERMION_TO_QUBIT_MAP,
        EXACT_DIAGONALIZE,
    }
    assert not any(
        name == "qiskit"
        or name.startswith("qiskit.")
        or name == "qiskit_nature"
        or name.startswith("qiskit_nature.")
        for name in after - before
    )
    assert all(
        envelope.metadata["ibm_submission_enabled"] is False for envelope in envelopes
    )
    assert all(not envelope.approval_requirements for envelope in envelopes)


def test_adapter_health_is_controlled_for_the_installed_runtime() -> None:
    adapter = QiskitQuantumWorkflowAdapter(MemoryPayloadStore())
    health = adapter.health()

    assert health.status in {
        HealthStatus.HEALTHY,
        HealthStatus.DEGRADED,
        HealthStatus.UNAVAILABLE,
    }
    if health.status is not HealthStatus.HEALTHY:
        assert health.reason_code in {
            "dependency_missing",
            "dependency_version_mismatch",
        }


def test_quantum_coefficients_are_canonical_and_finite() -> None:
    coefficient = QuantumComplexCoefficient.from_value(complex(-0.0, 0.125))
    assert coefficient.real_hex == "0x0.0p+0"
    assert coefficient.imaginary_hex == "0x1.0000000000000p-3"
    assert coefficient.value == complex(0.0, 0.125)

    with pytest.raises(ValidationError, match="finite"):
        QuantumComplexCoefficient(
            real_hex="inf",
            imaginary_hex="0x0.0p+0",
        )


def test_hamiltonian_contracts_reject_inconsistent_quantum_identity() -> None:
    coefficient = QuantumComplexCoefficient.from_value(1.0)
    with pytest.raises(ValidationError, match="register length"):
        SecondQuantizedHamiltonian(
            schema_version=VERSION,
            hamiltonian_identifier="hamiltonian.invalid",
            active_space_identifier="active.invalid",
            active_space_sha256="a" * 64,
            molecule_identifier="molecule.invalid",
            active_electron_count=2,
            alpha_electron_count=1,
            beta_electron_count=1,
            active_spatial_orbital_count=2,
            active_spin_orbital_count=4,
            constant_energy_hartree=0.0,
            register_length=3,
            integral_symmetry_tolerance=1e-10,
            terms=(
                FermionicHamiltonianTerm(
                    label="+_0 -_0",
                    coefficient=coefficient,
                ),
            ),
        )

    with pytest.raises(ValidationError, match="Pauli label"):
        MappedQubitHamiltonian(
            schema_version=VERSION,
            mapping_identifier="mapping.invalid",
            second_quantized_hamiltonian_identifier="hamiltonian.invalid",
            second_quantized_hamiltonian_sha256="b" * 64,
            active_space_identifier="active.invalid",
            active_space_sha256="a" * 64,
            molecule_identifier="molecule.invalid",
            mapper="jordan_wigner",
            active_electron_count=2,
            alpha_electron_count=1,
            beta_electron_count=1,
            active_spatial_orbital_count=2,
            active_spin_orbital_count=4,
            number_of_qubits=4,
            constant_energy_hartree=0.0,
            hermiticity_tolerance=1e-10,
            maximum_antihermitian_coefficient=0.0,
            terms=(
                PauliHamiltonianTerm(
                    label="ZZ",
                    coefficient=coefficient,
                ),
            ),
        )


def test_missing_qiskit_dependencies_fail_without_native_output() -> None:
    adapter = QiskitQuantumWorkflowAdapter(MemoryPayloadStore())
    if adapter.health().status is HealthStatus.HEALTHY:
        pytest.skip("Pinned Qiskit runtime is installed.")

    store = MemoryPayloadStore()
    adapter = QiskitQuantumWorkflowAdapter(store)
    active_reference = _source_reference(
        store,
        "fixture.synthetic-active",
        "electronic_active_space",
        _synthetic_active_space(),
    )
    result = adapter.invoke(
        _invocation(
            adapter,
            HAMILTONIAN_CONSTRUCT,
            inputs=(active_reference,),
            parameters={"integral_symmetry_tolerance": 1e-10},
            execution_identifier="execution.phase5a-missing",
        )
    )

    assert result.status is ExecutionStatus.FAILED
    assert result.failure is not None
    assert result.failure.code in {
        "dependency_missing",
        "dependency_version_mismatch",
    }
    assert not result.output_artifacts


def test_real_qiskit_pipeline_matches_independent_pyscf_fci() -> None:
    pytest.importorskip("qiskit")
    pytest.importorskip("qiskit_nature")

    store = MemoryPayloadStore()
    adapter = QiskitQuantumWorkflowAdapter(store)
    assert adapter.health().status is HealthStatus.HEALTHY

    active, expected_total = _real_h2_active_space()
    active_reference = _source_reference(
        store,
        "fixture.real-h2-active",
        "electronic_active_space",
        active,
    )

    hamiltonian_result = adapter.invoke(
        _invocation(
            adapter,
            HAMILTONIAN_CONSTRUCT,
            inputs=(active_reference,),
            parameters={"integral_symmetry_tolerance": 1e-9},
            execution_identifier="execution.phase5a-hamiltonian",
        )
    )
    assert hamiltonian_result.status is ExecutionStatus.SUCCESS
    hamiltonian_reference = hamiltonian_result.output_artifacts[0]
    hamiltonian = SecondQuantizedHamiltonian.model_validate_json(
        store.read(hamiltonian_reference)
    )
    assert hamiltonian.active_space_sha256 == active_reference.content_sha256
    assert hamiltonian.register_length == 4
    assert len(hamiltonian.terms) > 0
    assert "qiskit" not in hamiltonian.to_canonical_json().lower()

    mapping_result = adapter.invoke(
        _invocation(
            adapter,
            FERMION_TO_QUBIT_MAP,
            inputs=(hamiltonian_reference,),
            parameters={
                "mapper": "jordan_wigner",
                "hermiticity_tolerance": 1e-10,
            },
            execution_identifier="execution.phase5a-mapping",
        )
    )
    assert mapping_result.status is ExecutionStatus.SUCCESS
    mapped_reference = mapping_result.output_artifacts[0]
    mapped = MappedQubitHamiltonian.model_validate_json(store.read(mapped_reference))
    assert mapped.mapper == "jordan_wigner"
    assert mapped.number_of_qubits == 4
    assert mapped.maximum_antihermitian_coefficient <= 1e-10
    assert "qiskit" not in mapped.to_canonical_json().lower()

    exact_result = adapter.invoke(
        _invocation(
            adapter,
            EXACT_DIAGONALIZE,
            inputs=(mapped_reference,),
            parameters={
                "solver": "numpy_eigh_particle_sector",
                "particle_number_tolerance": 1e-10,
                "hermiticity_tolerance": 1e-10,
            },
            execution_identifier="execution.phase5a-exact",
        )
    )
    assert exact_result.status is ExecutionStatus.SUCCESS
    exact = ExactDiagonalizationResult.model_validate_json(
        store.read(exact_result.output_artifacts[0])
    )
    assert exact.particle_sector_filter_applied
    assert exact.full_hilbert_space_dimension == 16
    assert exact.particle_sector_dimension == 4
    assert exact.maximum_eigenpair_residual_hartree < 1e-9
    assert exact.particle_sector_leakage_hartree < 1e-10
    assert math.isclose(
        exact.total_energy_hartree,
        expected_total,
        rel_tol=1e-8,
        abs_tol=1e-8,
    )
    assert exact.total_energy_hartree < -1.0
    assert exact_result.execution_evidence is not None
    assert exact_result.execution_evidence.details["ibm_submission_performed"] is False
