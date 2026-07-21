from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from cgr.quantum_preflight.artifacts import artifact_reference, verify_artifact_bytes, write_json_atomic
from cgr.quantum_preflight.environment import environment_compatibility_identity, require_dependencies
from cgr.quantum_preflight.errors import QuantumDependencyError, QuantumIntegrityError, QuantumManifestError
from cgr.quantum_preflight.manifests import load_manifest, with_bond_distance
from cgr.quantum_preflight.operators import (
    encode_complex,
    encode_float,
    operator_fingerprint,
    serialize_fermionic_operator,
    serialize_qubit_operator,
)
from cgr.quantum_preflight.identities import (
    AuthorizedScientificOutcome,
    ScientificResultArtifact,
    ScientificResultIdentity,
    verifier_outcome_identities,
)
from cgr.quantum_preflight.receipt import (
    QuantumPreflightReceipt,
    assemble_receipt,
    inspect_receipt,
    verify_receipt_identities,
)
from cgr.quantum_preflight.reference import resolve_active_orbitals
from cgr.quantum_preflight.runner import _ARTIFACT_TYPES, _scientific_result_identity
from cgr.quantum_preflight.results import EnergyResult, VQEResult
from cgr.quantum_preflight.verification import blocking_findings, verify_execution
from cgr.quantum_preflight.warnings import CompatibilityWarning, CompatibilityWarningEvidence
from cgr.science import ArtifactLineageEdge, ArtifactLineageGraph
from cgr.quantum_preflight.artifacts import SCHEMA_VERSION

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "benchmark-manifests/quantum-preflight/lih-ground-state-v1.json"


@pytest.fixture
def manifest():
    return load_manifest(MANIFEST)


@pytest.mark.quantum_unit
def test_manifest_has_stable_declared_identity(manifest) -> None:
    assert manifest.expected_experiment_sha256 == manifest.experiment.fingerprint
    assert manifest.experiment.to_canonical_json() == manifest.experiment.to_canonical_json()
    assert manifest.experiment.molecular_system.total_electron_count == 4
    assert manifest.experiment.molecular_system.structure_artifact_identifier == "molecular_structure"


@pytest.mark.quantum_unit
def test_required_scientific_artifact_types_are_declared() -> None:
    assert {
        "quantum_chemistry_experiment",
        "molecular_structure",
        "environment_manifest",
        "qcschema",
        "electronic_structure_problem_summary",
        "active_space",
        "fermionic_hamiltonian",
        "qubit_hamiltonian",
        "exact_ground_state_result",
        "vqe_ground_state_result",
        "optimization_trace",
        "verification_report",
        "quantum_preflight_receipt",
    }.issubset(_ARTIFACT_TYPES.values())


@pytest.mark.quantum_unit
@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("coordinate_unit", None, "coordinate_unit"),
        ("molecular_charge", 0.5, "integer"),
        ("spin_multiplicity", 0, "greater than 0"),
    ],
)
def test_molecular_declarations_are_strict(manifest, field, value, message) -> None:
    data = manifest.model_dump(mode="json")
    data["expected_experiment_sha256"] = None
    molecule = data["experiment"]["molecular_system"]
    if value is None:
        molecule.pop(field)
    else:
        molecule[field] = value
    with pytest.raises((ValidationError, QuantumManifestError), match=message):
        type(manifest).model_validate(data)


@pytest.mark.quantum_unit
def test_bond_distance_and_unit_mismatch_fail(manifest) -> None:
    data = manifest.model_dump(mode="json")
    data["expected_experiment_sha256"] = None
    data["experiment"]["molecular_system"]["declared_bond_distance"] = 1.7
    with pytest.raises(ValidationError, match="Derived bond distance"):
        type(manifest).model_validate(data)


@pytest.mark.quantum_unit
def test_electron_multiplicity_parity_fails(manifest) -> None:
    data = manifest.model_dump(mode="json")
    data["expected_experiment_sha256"] = None
    data["experiment"]["molecular_system"]["spin_multiplicity"] = 2
    with pytest.raises(ValidationError, match="incompatible parity"):
        type(manifest).model_validate(data)


@pytest.mark.quantum_unit
def test_active_space_constraints(manifest) -> None:
    data = manifest.model_dump(mode="json")
    data["expected_experiment_sha256"] = None
    model = data["experiment"]["electronic_structure"]
    model["active_electron_count"] = 6
    with pytest.raises(ValidationError, match="cannot exceed"):
        type(manifest).model_validate(data)
    model["active_electron_count"] = 2
    model["active_orbital_indices"] = [1, 1]
    with pytest.raises(ValidationError, match="unique"):
        type(manifest).model_validate(data)


@pytest.mark.quantum_unit
def test_observable_tolerance_and_bounded_policy(manifest) -> None:
    for path, value, message in (
        (("requested_observable",), "energy", "total energy"),
        (("verification_policy", "energy_difference_tolerance_hartree"), 0, "greater than 0"),
        (("execution_policy", "network_disabled"), False, "disable networking"),
    ):
        data = manifest.model_dump(mode="json")
        data["expected_experiment_sha256"] = None
        target = data["experiment"]
        for part in path[:-1]:
            target = target[part]
        target[path[-1]] = value
        with pytest.raises(ValidationError, match=message):
            type(manifest).model_validate(data)


@pytest.mark.quantum_unit
def test_blocking_parent_experiment_cannot_execute(manifest) -> None:
    data = manifest.model_dump(mode="json")
    data["expected_experiment_sha256"] = None
    data["experiment"]["parent_experiment"]["execution_policy"]["execution_allowed"] = False
    with pytest.raises(ValidationError, match="not execution-ready"):
        type(manifest).model_validate(data)


@pytest.mark.quantum_unit
def test_stale_manifest_fingerprint_is_rejected(manifest) -> None:
    data = manifest.model_dump(mode="json")
    data["experiment"]["molecular_system"]["atoms"][1]["coordinates"][2] = 1.7
    data["experiment"]["molecular_system"]["declared_bond_distance"] = 1.7
    with pytest.raises(ValidationError, match="stale or substituted"):
        type(manifest).model_validate(data)


@pytest.mark.quantum_unit
def test_mutated_structure_changes_identity(manifest) -> None:
    mutated = with_bond_distance(manifest, 1.7)
    assert mutated.experiment.molecular_system.fingerprint != manifest.experiment.molecular_system.fingerprint
    assert mutated.experiment.fingerprint != manifest.experiment.fingerprint


@pytest.mark.quantum_unit
def test_operator_serialization_is_order_independent_and_complex() -> None:
    first = serialize_fermionic_operator({"+_1 -_0": 2 - 3j, "": -0.0}, register_length=4)
    second = serialize_fermionic_operator([("", 0.0), ("+_1 -_0", 2 - 3j)], register_length=4)
    assert first == second
    assert first["terms"][0]["coefficient"] == encode_complex(0.0)
    assert operator_fingerprint(first) == operator_fingerprint(second)


@pytest.mark.quantum_unit
def test_qubit_terms_sort_and_mapper_enters_identity() -> None:
    one = serialize_qubit_operator([("ZI", 2), ("IX", 1)], number_of_qubits=2, mapper="jordan_wigner")
    two = serialize_qubit_operator([("IX", 1), ("ZI", 2)], number_of_qubits=2, mapper="jordan_wigner")
    wrong = {**two, "mapper": "parity"}
    assert [term["label"] for term in one["terms"]] == ["IX", "ZI"]
    assert operator_fingerprint(one) == operator_fingerprint(two)
    assert operator_fingerprint(one) != operator_fingerprint(wrong)


@pytest.mark.quantum_unit
@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_nonfinite_operator_coefficients_fail(value: float) -> None:
    with pytest.raises(QuantumIntegrityError, match="finite"):
        serialize_qubit_operator({"I": value}, number_of_qubits=1, mapper="jordan_wigner")


@pytest.mark.quantum_unit
def test_negative_zero_normalizes() -> None:
    assert encode_float(-0.0) == encode_float(0.0) == "0x0.0p+0"


@pytest.mark.quantum_unit
def test_artifact_bytes_detect_substitution(tmp_path: Path) -> None:
    payload = {"value": "original"}
    reference = artifact_reference("sample", "sample", payload, filename="sample.json")
    path = tmp_path / "sample.json"
    from cgr.quantum_preflight.artifacts import artifact_document

    write_json_atomic(path, artifact_document("sample", payload), maximum_bytes=4096)
    assert verify_artifact_bytes(path, reference)
    path.write_text(json.dumps({"artifact_type": "sample", "payload": {"value": "changed"}}), encoding="utf-8")
    assert not verify_artifact_bytes(path, reference)


def _synthetic_evidence(manifest):
    experiment = manifest.experiment
    payloads = {
        "experiment": experiment.model_dump(mode="json"),
        "molecular_structure": {
            **experiment.molecular_system.model_dump(mode="json"),
            "driver_spin": experiment.molecular_system.driver_spin,
            "total_electron_count": 4,
        },
        "environment": {
            "python_version": "3.12.11",
            "python_implementation": "CPython",
            "os": "linux",
            "architecture": "x86_64",
            "python_major_minor": "3.12",
            "network_disabled": True,
            "credential_variable_names_present": [],
            "direct_package_versions": {
                "qiskit": "2.3.1", "qiskit-nature": "0.8.0",
                "qiskit-algorithms": "0.4.0", "qiskit-aer": "0.17.1", "pyscf": "2.13.1",
            },
            "transitive_package_versions": {"numpy": "2.0.0", "scipy": "1.14.0"},
            "dependency_lock_sha256": "a" * 64,
            "container_image_identifier": "sha256:test",
            "thread_limits": {
                "PYTHONHASHSEED": "0", "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1",
                "MKL_NUM_THREADS": "1", "NUMEXPR_NUM_THREADS": "1",
            },
            "deterministic_seed_policy": "algorithm_globals.random_seed=manifest.random_seed",
            "blas_information": {"name": "openblas", "version": "0.3"},
        },
        "qcschema": {"schema_name": "qcschema"},
        "electronic_problem": {
            "driver_identifier": "pyscf", "basis_set": "sto-3g",
            "reference_method": "restricted_hartree_fock", "frozen_core_policy": True,
            "pre_transform_particle_count": [2, 2],
            "pre_transform_spatial_orbitals": 6, "nuclear_repulsion_energy_hartree": 1.0,
        },
        "active_space": {
            "resolved_active_orbital_indices": [1, 2], "active_electron_count": 2,
        },
        "fermionic_hamiltonian": serialize_fermionic_operator({"": 1}, register_length=4),
        "qubit_hamiltonian": serialize_qubit_operator({"II": 1}, number_of_qubits=2, mapper="jordan_wigner"),
        "hamiltonian_metrics": {"maximum_antihermitian_coefficient": 0.0, "qubit_sha256": None},
        "optimization_trace": [{"evaluation": 1}],
        "ansatz_manifest": {"ansatz": "uccsd"},
        "compatibility_warnings": CompatibilityWarningEvidence(status="clean").model_dump(mode="json"),
    }
    compatibility = environment_compatibility_identity(payloads["environment"])
    payloads["environment"]["compatibility_identity"] = compatibility.model_dump(mode="json")
    payloads["environment"]["environment_compatibility_sha256"] = compatibility.fingerprint
    refs = {}
    for name in ("experiment", "environment", "molecular_structure", "qcschema", "electronic_problem", "active_space", "fermionic_hamiltonian", "qubit_hamiltonian", "optimization_trace", "ansatz_manifest", "compatibility_warnings"):
        refs[name] = artifact_reference(name, _ARTIFACT_TYPES.get(name, name), payloads[name], filename=f"{name}.json")
    payloads["hamiltonian_metrics"]["qubit_sha256"] = refs["qubit_hamiltonian"].content_sha256
    exact = EnergyResult(
        solver_identifier="exact", solver_version="1.0", hamiltonian_sha256=refs["qubit_hamiltonian"].content_sha256,
        environment_sha256=refs["environment"].content_sha256, electronic_energy_hartree=1.0,
        nuclear_repulsion_energy_hartree=1.0, total_energy_hartree=2.0, raw_eigenvalue_hartree=1.0,
        particle_count=2.0, particle_sector_filter_applied=True,
        number_of_spatial_orbitals=2, number_of_spin_orbitals=4, number_of_qubits=2,
        completed=True, duration_seconds=1.0,
    )
    vqe = VQEResult(
        solver_identifier="vqe", solver_version="1.0", hamiltonian_sha256=refs["qubit_hamiltonian"].content_sha256,
        environment_sha256=refs["environment"].content_sha256, electronic_energy_hartree=1.000001,
        nuclear_repulsion_energy_hartree=1.0, total_energy_hartree=2.000001, raw_eigenvalue_hartree=1.000001,
        particle_count=2.0, completed=True, duration_seconds=1.0, optimizer_identifier="slsqp",
        number_of_spatial_orbitals=2, number_of_spin_orbitals=4, number_of_qubits=2,
        optimizer_status="completed", optimizer_evaluations=1, initial_point_sha256="0" * 64,
        optimized_parameters_sha256="1" * 64, ansatz_identifier="uccsd",
        initial_state_identifier="hartree_fock", converged=True,
    )
    exact_identity = _scientific_result_identity(
        "exact_ground_state", exact, experiment, payloads, refs
    )
    vqe_identity = _scientific_result_identity(
        "vqe_ground_state", vqe, experiment, payloads, refs
    )
    payloads["exact_result"] = ScientificResultArtifact(
        scientific_identity=exact_identity,
        scientific_result_sha256=exact_identity.fingerprint,
        execution_result=exact,
    ).model_dump(mode="json")
    payloads["vqe_result"] = ScientificResultArtifact(
        scientific_identity=vqe_identity,
        scientific_result_sha256=vqe_identity.fingerprint,
        execution_result=vqe,
    ).model_dump(mode="json")
    refs["exact_result"] = artifact_reference(
        "exact_result", _ARTIFACT_TYPES["exact_result"], payloads["exact_result"],
        filename="exact-result.json",
    )
    refs["vqe_result"] = artifact_reference(
        "vqe_result", _ARTIFACT_TYPES["vqe_result"], payloads["vqe_result"],
        filename="vqe-result.json",
    )
    links = (
        ("experiment", "molecular_structure"), ("molecular_structure", "qcschema"),
        ("qcschema", "electronic_problem"), ("electronic_problem", "active_space"),
        ("active_space", "fermionic_hamiltonian"), ("fermionic_hamiltonian", "qubit_hamiltonian"),
        ("qubit_hamiltonian", "exact_result"), ("qubit_hamiltonian", "vqe_result"),
    )
    lineage = ArtifactLineageGraph(edges=tuple(
        ArtifactLineageEdge(source=refs[a].pointer, destination=refs[b].pointer, relationship_type="produces",
                            producing_capability="test", producing_capability_version=SCHEMA_VERSION)
        for a, b in links
    ))
    return payloads, refs, lineage


def _synthetic_outcome(manifest, payloads, refs, results):
    exact = payloads["exact_result"]
    vqe = payloads["vqe_result"]
    agreement = payloads["numerical_agreement"]
    warnings = CompatibilityWarningEvidence.model_validate(payloads["compatibility_warnings"])
    return AuthorizedScientificOutcome(
        experiment_sha256=manifest.experiment.fingerprint,
        molecular_structure_sha256=refs["molecular_structure"].content_sha256,
        electronic_problem_sha256=refs["electronic_problem"].content_sha256,
        active_space_sha256=refs["active_space"].content_sha256,
        fermionic_hamiltonian_sha256=refs["fermionic_hamiltonian"].content_sha256,
        qubit_hamiltonian_sha256=refs["qubit_hamiltonian"].content_sha256,
        exact_scientific_result_sha256=exact["scientific_result_sha256"],
        vqe_scientific_result_sha256=vqe["scientific_result_sha256"],
        exact_total_energy=exact["execution_result"]["total_energy_hartree"],
        vqe_total_energy=vqe["execution_result"]["total_energy_hartree"],
        absolute_difference=agreement["absolute_difference_hartree"],
        tolerance=agreement["tolerance_hartree"],
        comparison_passed=agreement["passed"],
        verification_policy_sha256=manifest.experiment.verification_policy.fingerprint,
        verifier_outcomes=verifier_outcome_identities(results),
        authorization_decision=not blocking_findings(results),
        environment_compatibility_sha256=payloads["environment"]["environment_compatibility_sha256"],
        compatibility_warnings_sha256=warnings.fingerprint,
        compatibility_status=warnings.status,
    )


def _mutate_vqe_energy(payloads, refs, graph) -> None:
    del refs, graph
    artifact = payloads["vqe_result"]
    artifact["execution_result"].update(total_energy_hartree=3.0, electronic_energy_hartree=2.0)
    artifact["scientific_identity"].update(total_energy=3.0, electronic_energy=2.0)
    identity = ScientificResultIdentity.model_validate(artifact["scientific_identity"])
    artifact["scientific_result_sha256"] = identity.fingerprint


@pytest.mark.quantum_unit
def test_complete_synthetic_evidence_authorizes(manifest) -> None:
    payloads, refs, lineage = _synthetic_evidence(manifest)
    results = verify_execution(manifest.experiment, refs, payloads, lineage)
    assert not blocking_findings(results)
    lineage_ref = artifact_reference("lineage", "lineage", lineage.model_dump(mode="json"), filename="lineage.json")
    receipt = assemble_receipt(
        execution_identifier="run-001",
        experiment=refs["experiment"].pointer,
        artifacts=tuple(ref.pointer for ref in refs.values()),
        verification_results=results,
        lineage=lineage_ref.pointer,
        compatibility_warnings=refs["compatibility_warnings"].pointer,
        scientific_outcome=_synthetic_outcome(manifest, payloads, refs, results),
        execution_completed=True,
    )
    assert receipt.authorized


@pytest.mark.quantum_unit
def test_run_identifier_changes_receipt_content_not_scientific_outcome(manifest) -> None:
    payloads, refs, lineage = _synthetic_evidence(manifest)
    results = verify_execution(manifest.experiment, refs, payloads, lineage)
    lineage_ref = artifact_reference("lineage", "lineage", lineage.model_dump(mode="json"), filename="lineage.json")
    outcome = _synthetic_outcome(manifest, payloads, refs, results)
    receipts = [
        assemble_receipt(
            execution_identifier=run_id,
            experiment=refs["experiment"].pointer,
            artifacts=tuple(ref.pointer for ref in refs.values()),
            verification_results=results,
            lineage=lineage_ref.pointer,
            compatibility_warnings=refs["compatibility_warnings"].pointer,
            scientific_outcome=outcome,
            execution_completed=True,
        )
        for run_id in ("run-001", "run-002")
    ]
    receipt_refs = [
        artifact_reference("receipt", "receipt", item.model_dump(mode="json"), filename="receipt.json")
        for item in receipts
    ]
    assert receipts[0].scientific_outcome_sha256 == receipts[1].scientific_outcome_sha256
    assert receipt_refs[0].content_sha256 != receipt_refs[1].content_sha256


@pytest.mark.quantum_unit
def test_legacy_receipt_is_inspectable_but_cannot_be_hardened() -> None:
    inspected = inspect_receipt({"schema_version": "cgr.quantum-preflight-receipt/1.0.0"})
    assert inspected.legacy and not inspected.hardened


@pytest.mark.quantum_unit
def test_receipt_identity_recomputation_rejects_full_pointer_substitution(manifest) -> None:
    payloads, refs, lineage = _synthetic_evidence(manifest)
    results = verify_execution(manifest.experiment, refs, payloads, lineage)
    lineage_ref = artifact_reference("lineage", "lineage", lineage.model_dump(mode="json"), filename="lineage.json")
    outcome = _synthetic_outcome(manifest, payloads, refs, results)
    receipt = assemble_receipt(
        execution_identifier="run-001",
        experiment=refs["experiment"].pointer,
        artifacts=tuple(ref.pointer for ref in refs.values()),
        verification_results=results,
        lineage=lineage_ref.pointer,
        compatibility_warnings=refs["compatibility_warnings"].pointer,
        scientific_outcome=outcome,
        execution_completed=True,
    )
    arguments = {
        "receipt": receipt,
        "exact_result": payloads["exact_result"],
        "vqe_result": payloads["vqe_result"],
        "exact_result_pointer": refs["exact_result"].pointer,
        "vqe_result_pointer": refs["vqe_result"].pointer,
        "expected_outcome": outcome,
    }
    assert not verify_receipt_identities(**arguments)
    arguments["exact_result_pointer"] = refs["exact_result"].pointer.model_copy(
        update={"content_sha256": "f" * 64}
    )
    failures = verify_receipt_identities(**arguments)
    assert "receipt.run_specific_artifact_substitution" in failures
    assert "receipt.exact_result_content_mismatch" in failures


@pytest.mark.quantum_unit
def test_future_blocking_compatibility_warning_prevents_authorization(manifest) -> None:
    payloads, refs, lineage = _synthetic_evidence(manifest)
    payloads["compatibility_warnings"] = CompatibilityWarningEvidence(
        warnings=(
            CompatibilityWarning(
                code="dependency_runtime_warning",
                category="RuntimeWarning",
                origin_module="unknown",
                normalized_message="future incompatible runtime behavior",
                count=1,
                severity="error",
                blocking=True,
                suggested_action="review_dependency_runtime_warning",
                dependency_name="unknown",
                dependency_version="unknown",
                first_observed_phase="vqe_execution",
            ),
        ),
        status="blocking",
    ).model_dump(mode="json")
    results = verify_execution(manifest.experiment, refs, payloads, lineage)
    assert blocking_findings(results)
    outcome = _synthetic_outcome(manifest, payloads, refs, results)
    assert outcome.authorization_decision is False


@pytest.mark.quantum_unit
@pytest.mark.parametrize(
    ("mutation", "finding"),
    [
        (lambda payload, refs, graph: payload["qubit_hamiltonian"].update(mapper="parity"), "hamiltonian.mapper_mismatch"),
        (lambda payload, refs, graph: payload["hamiltonian_metrics"].update(maximum_antihermitian_coefficient=1.0), "hamiltonian.non_hermitian"),
        (lambda payload, refs, graph: payload.update(optimization_trace=[]), "vqe.optimization_trace_missing"),
        (_mutate_vqe_energy, "agreement.energy_tolerance_exceeded"),
        (lambda payload, refs, graph: graph.edges.__class__, "lineage.required_edge_missing"),
    ],
)
def test_negative_evidence_fails_for_intended_reason(manifest, mutation, finding) -> None:
    payloads, refs, lineage = _synthetic_evidence(manifest)
    if finding == "lineage.required_edge_missing":
        lineage = ArtifactLineageGraph(edges=lineage.edges[:-1])
    else:
        mutation(payloads, refs, lineage)
    results = verify_execution(manifest.experiment, refs, payloads, lineage)
    assert finding in {item.code for result in results for item in result.findings}
    assert blocking_findings(results)


@pytest.mark.quantum_unit
def test_wrong_result_hamiltonian_and_receipt_fail_closed(manifest) -> None:
    payloads, refs, lineage = _synthetic_evidence(manifest)
    clean_results = verify_execution(manifest.experiment, refs, payloads, lineage)
    clean_outcome = _synthetic_outcome(manifest, payloads, refs, clean_results)
    payloads["exact_result"]["execution_result"]["hamiltonian_sha256"] = "f" * 64
    results = verify_execution(manifest.experiment, refs, payloads, lineage)
    assert "exact.scientific_identity_invalid" in {finding.code for result in results for finding in result.findings}
    lineage_ref = artifact_reference("lineage", "lineage", lineage.model_dump(mode="json"), filename="lineage.json")
    with pytest.raises(ValidationError, match="authorization"):
        QuantumPreflightReceipt(
            schema_version="cgr.quantum-preflight-receipt/2.0.0",
            execution_identifier="run-001",
            experiment=refs["experiment"].pointer,
            artifacts=tuple(ref.pointer for ref in refs.values()),
            verification_results=results,
            lineage=lineage_ref.pointer,
            compatibility_warnings=refs["compatibility_warnings"].pointer,
            compatibility_status="clean",
            exact_scientific_result_sha256=payloads["exact_result"]["scientific_result_sha256"],
            vqe_scientific_result_sha256=payloads["vqe_result"]["scientific_result_sha256"],
            scientific_outcome=clean_outcome,
            scientific_outcome_sha256=clean_outcome.fingerprint,
            execution_completed=True,
            scientific_verification_passed=False,
            artifact_lineage_passed=True,
            authorized=True,
            authorization_policy="all_blocking_verifiers_must_pass",
        )


@pytest.mark.quantum_unit
def test_missing_dependencies_raise_domain_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib.metadata

    def missing(name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", missing)
    with pytest.raises(QuantumDependencyError, match="missing"):
        require_dependencies()


@pytest.mark.quantum_unit
def test_base_imports_do_not_load_quantum_packages() -> None:
    import cgr
    import cgr.quixbugs_pilot
    import cgr.science

    del cgr
    forbidden = ("qiskit", "qiskit_nature", "qiskit_algorithms", "qiskit_aer", "pyscf")
    assert not any(name.startswith(forbidden) for name in sys.modules)


@pytest.mark.quantum_unit
def test_python312_compatibility_deviation_is_locked() -> None:
    direct = (ROOT / "requirements/quantum-preflight.in").read_text(encoding="utf-8")
    evidence = (ROOT / "requirements/quantum-preflight-resolver-evidence.txt").read_text(encoding="utf-8")
    lock = (ROOT / "requirements/quantum-preflight.lock").read_text(encoding="utf-8")
    assert "qiskit==2.3.1" in direct and "qiskit==2.3.1" in lock
    assert "qiskit==2.5.0" in evidence and "Python 3.12" in evidence
    for pin in ("qiskit-nature==0.8.0", "qiskit-algorithms==0.4.0", "qiskit-aer==0.17.1", "pyscf==2.13.1"):
        assert pin in direct and pin in lock


@pytest.mark.quantum_unit
def test_qiskit_nature_080_active_space_compatibility() -> None:
    assert resolve_active_orbitals(
        total_electrons=4,
        active_electrons=2,
        active_orbitals=2,
        total_orbitals=6,
    ) == [1, 2]
    with pytest.raises(QuantumIntegrityError, match="out of range"):
        resolve_active_orbitals(
            total_electrons=4,
            active_electrons=2,
            active_orbitals=6,
            total_orbitals=6,
        )


@pytest.mark.quantum_unit
def test_public_contract_modules_contain_no_heavy_imports() -> None:
    public = (ROOT / "src/cgr/quantum_preflight")
    for filename in ("__init__.py", "contracts.py", "operators.py", "verification.py", "receipt.py", "results.py"):
        source = (public / filename).read_text(encoding="utf-8")
        assert "import qiskit" not in source
        assert "import pyscf" not in source
