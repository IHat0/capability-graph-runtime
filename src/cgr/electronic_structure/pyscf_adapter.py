"""PySCF-backed Phase 4 electronic-structure adapter."""

from __future__ import annotations

import hashlib
import importlib.metadata
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from pydantic import ValidationError

from cgr.kernel.contracts import (
    CapabilityVersion,
    ExecutionStatus,
    HealthStatus,
)
from cgr.molecular import (
    MolecularConformerSet,
    MolecularEnvironment,
    MolecularSimulationSystem,
    MolecularSnapshot,
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
)

from .qm_region import (
    ElectronicQMRegionPreparation,
    prepare_qm_region,
)
from .reaction_path import (
    ElectronicFrequencyAnalysis,
    ElectronicFrequencyMode,
    ElectronicGradientResult,
    ElectronicReactionPathPoint,
    ElectronicReactionPathResult,
    ElectronicTransitionStateSearch,
)

from .contracts import (
    ElectronicActiveSpace,
    ElectronicActiveSpaceSelection,
    ElectronicAtom,
    ElectronicHartreeFockResult,
    ElectronicImplicitSolventResult,
    ElectronicMolecule,
    ElectronicQMMMHybridResult,
    ElectronicQMMMHartreeFockResult,
    ElectronicOrbital,
    ElectronicOrbitalSelectionScore,
    ElectronicOrbitalSet,
    ElectronicReferenceCalculation,
    ElectronicStructureConfiguration,
    ElectronicTensor,
    QMMMEmbeddingFoundation,
    QMMMEmbeddingSite,
    QMMMBoundaryChargeAdjustment,
)

_SCHEMA_VERSION = CapabilityVersion(major=1, minor=0, patch=0)
_ADAPTER_VERSION = CapabilityVersion(major=1, minor=0, patch=0)
_EXPECTED_PYSCF_VERSION = "2.13.1"
_MAXIMUM_PAYLOAD_BYTES = 256 * 1024 * 1024
_MAXIMUM_ATOMS = 100_000
_MAXIMUM_ACTIVE_ORBITALS = 32

QM_REGION_PREPARE = "electronic.qm_region_prepare"
MOLECULE_CONSTRUCT = "electronic.molecule_construct"
CONFIGURATION_DEFINE = "electronic.configuration_define"
HARTREE_FOCK = "electronic.hartree_fock"
ORBITALS_GENERATE = "electronic.orbitals_generate"
REFERENCE_CALCULATE = "electronic.reference_calculate"
ACTIVE_SPACE_SELECT = "electronic.active_space_select"
ACTIVE_SPACE_CONSTRUCT = "electronic.active_space_construct"
QMMM_EMBEDDING_PREPARE = "electronic.qmmm_embedding_prepare"
QMMM_HARTREE_FOCK = "electronic.qmmm_hartree_fock"
QMMM_HYBRID_EXECUTE = "electronic.qmmm_hybrid_execute"
IMPLICIT_SOLVENT_HARTREE_FOCK = "electronic.implicit_solvent_hartree_fock"
GRADIENT_CALCULATE = "electronic.gradient_calculate"
FREQUENCY_ANALYZE = "electronic.frequency_analyze"
TRANSITION_STATE_SEARCH = "electronic.transition_state_search"
REACTION_PATH_CONFIRM = "electronic.reaction_path_confirm"


@runtime_checkable
class ElectronicArtifactPayloadStore(Protocol):
    """Read and persist exact bytes behind electronic artifact references."""

    def read(self, reference: ArtifactReference) -> bytes:
        """Return exact bytes addressed by a complete reference."""
        ...


@runtime_checkable
class ElectronicPrivateStateStore(Protocol):
    """Read private native state bound to a public scientific manifest."""

    def read(self, reference: ArtifactReference) -> bytes: ...

    def write(self, reference: ArtifactReference, payload: bytes) -> None:
        """Persist exact bytes under a content-addressed reference."""
        ...


class PySCFAdapterConfigurationError(ValueError):
    """Controlled construction failure for the PySCF adapter."""


def _distribution_version() -> str | None:
    try:
        return importlib.metadata.version("pyscf")
    except importlib.metadata.PackageNotFoundError:
        return None


def _pyscf_identity() -> tuple[str, str] | None:
    try:
        import pyscf
    except ImportError:
        return None
    return (
        _distribution_version() or "unknown",
        str(getattr(pyscf, "__version__", "unknown")),
    )


def _pyscf_modules() -> tuple[object, object, object, object, object]:
    import numpy
    from pyscf import ao2mo, gto, mp, scf

    return numpy, gto, scf, mp, ao2mo


def _descriptor(
    capability_name: str,
    *,
    accepted_artifact_types: tuple[str, ...],
    produced_artifact_types: tuple[str, ...],
    deterministic: bool,
) -> CapabilityDescriptor:
    return CapabilityDescriptor(
        capability_name=capability_name,
        version=_ADAPTER_VERSION,
        accepted_artifact_types=accepted_artifact_types,
        produced_artifact_types=produced_artifact_types,
        required_tools=("pyscf",),
        required_runtime="python",
        determinism=(
            DeterminismClassification.DETERMINISTIC
            if deterministic
            else DeterminismClassification.NONDETERMINISTIC
        ),
    )


def pyscf_capability_envelopes() -> tuple[CapabilityExecutionEnvelope, ...]:
    """Return planner declarations for the Phase 4 PySCF capabilities."""

    definitions = (
        (
            QM_REGION_PREPARE,
            ("molecular_environment",),
            (
                "electronic_qm_region_preparation",
                "electronic_molecule",
            ),
            (
                "qm_region_construction",
                "qm_mm_boundary_capping",
                "charge_spin_determination",
            ),
            True,
            (
                "QM seeds are expanded deterministically through the "
                "prepared solute topology.",
                "Single or unspecified-order covalent boundaries are capped "
                "with explicit link hydrogens.",
                "Explicit multiple-bond and transition-metal boundary cuts "
                "fail closed.",
                "Charge is accepted only when every selected atom carries "
                "formal-charge evidence.",
                "Transition-metal regions retain multiple parity-compatible "
                "spin candidates rather than assuming one ground state.",
            ),
        ),
        (
            MOLECULE_CONSTRUCT,
            (
                "molecular_conformer_set",
                "molecular_environment",
                "molecular_snapshot",
            ),
            ("electronic_molecule",),
            ("electronic_molecule_construction",),
            True,
            (
                "Molecular charge and 2S spin must be supplied explicitly.",
                "Snapshot geometries require the matching molecular environment topology.",
            ),
        ),
        (
            CONFIGURATION_DEFINE,
            ("electronic_molecule",),
            ("electronic_structure_configuration",),
            ("basis_charge_spin_configuration",),
            True,
            (
                "Version 1 supports RHF, UHF and ROHF reference declarations.",
                "Every scientifically meaningful SCF control must be explicit.",
            ),
        ),
        (
            HARTREE_FOCK,
            ("electronic_molecule", "electronic_structure_configuration"),
            ("electronic_hartree_fock_result",),
            ("hartree_fock", "orbital_generation"),
            False,
            (
                "SCF convergence and floating-point values can vary across BLAS implementations and hardware.",
                "Nonconverged SCF calculations fail closed and do not produce a public reference artifact.",
            ),
        ),
        (
            ORBITALS_GENERATE,
            (
                "electronic_hartree_fock_result",
                "electronic_structure_configuration",
            ),
            ("electronic_orbital_set",),
            ("orbital_generation",),
            True,
            (
                "Orbitals are copied from a validated generic Hartree-Fock result; no native PySCF object is exposed.",
            ),
        ),
        (
            REFERENCE_CALCULATE,
            (
                "electronic_molecule",
                "electronic_structure_configuration",
                "electronic_hartree_fock_result",
            ),
            ("electronic_reference_calculation",),
            ("classical_reference_calculation",),
            False,
            (
                "Version 1 supports Hartree-Fock summaries and restricted MP2 references.",
                "MP2 is limited to converged closed-shell RHF inputs.",
            ),
        ),
        (
            ACTIVE_SPACE_SELECT,
            (
                "electronic_molecule",
                "electronic_structure_configuration",
                "electronic_hartree_fock_result",
            ),
            ("electronic_active_space_selection",),
            (
                "active_space_selection",
                "automatic_active_space_selection",
                "metal_center_active_space_selection",
                "reaction_center_active_space_selection",
            ),
            True,
            (
                "RHF and ROHF use their common canonical spatial orbitals; "
                "UHF is transformed to unrestricted natural orbitals.",
                "Reaction-centre selection retains occupied/antibonding partners "
                "with explicit bond and target-AO projection evidence.",
                "Metal/ligand selection resolves requested metal-shell and ligand "
                "AO labels and fails below the explicit projection threshold.",
                "Selections remain subject to scientific verification.",
            ),
        ),
        (
            ACTIVE_SPACE_CONSTRUCT,
            (
                "electronic_molecule",
                "electronic_structure_configuration",
                "electronic_hartree_fock_result",
                "electronic_active_space_selection",
            ),
            ("electronic_active_space",),
            ("active_space_construction",),
            False,
            (
                "RHF/ROHF spatial orbitals and UHF-derived unrestricted natural "
                "orbitals produce a common spatial active-space Hamiltonian.",
                "Active spaces are bounded to 32 spatial orbitals.",
                "The output uses chemist-order spatial-orbital integrals and an explicit constant energy.",
            ),
        ),
        (
            IMPLICIT_SOLVENT_HARTREE_FOCK,
            ("electronic_molecule", "electronic_structure_configuration"),
            ("electronic_implicit_solvent_result",),
            (
                "implicit_solvent_electronic_structure",
                "aqueous_electronic_energy",
                "continuum_solvation",
            ),
            False,
            (
                "PySCF self-consistent PCM is evaluated alongside the matching "
                "vacuum Hartree-Fock reference.",
                "The reported difference is an electronic solvation contribution, "
                "not a thermal or Gibbs free energy.",
            ),
        ),
        (
            GRADIENT_CALCULATE,
            ("electronic_molecule", "electronic_structure_configuration"),
            ("electronic_gradient_result",),
            ("electronic_gradient", "stationary_point_assessment"),
            False,
            (
                "PySCF analytical SCF nuclear gradients are reported in "
                "Hartree/Bohr with an explicit stationary threshold.",
            ),
        ),
        (
            FREQUENCY_ANALYZE,
            (
                "electronic_molecule",
                "electronic_structure_configuration",
                "electronic_gradient_result",
            ),
            ("electronic_frequency_analysis",),
            ("hessian", "harmonic_frequencies", "transition_state_characterization"),
            False,
            (
                "Analytical SCF Hessians are projected to remove translations and "
                "rotations before harmonic analysis.",
                "Imaginary modes are significant only beyond an explicit threshold.",
            ),
        ),
        (
            TRANSITION_STATE_SEARCH,
            ("electronic_molecule", "electronic_structure_configuration"),
            (
                "electronic_transition_state_search",
                "electronic_molecule",
                "electronic_structure_configuration",
            ),
            ("transition_state_search", "saddle_point_optimization"),
            False,
            (
                "geomeTRIC is run in transition-state mode using PySCF analytical "
                "gradients and an initial analytical Hessian where supported.",
                "Search convergence never constitutes transition-state verification; "
                "frequency and reaction-path evidence remain mandatory.",
            ),
        ),
        (
            REACTION_PATH_CONFIRM,
            (
                "electronic_transition_state_search",
                "electronic_structure_configuration",
                "electronic_frequency_analysis",
            ),
            ("electronic_reaction_path_result",),
            ("reaction_path", "irc_confirmation", "transition_state_verification"),
            False,
            (
                "A bounded forward/reverse mass-weighted steepest-descent path "
                "is initialized from the sole significant imaginary mode.",
                "Path confirmation requires energy descent in both directions "
                "and geometrically distinct endpoints; it does not identify products by name.",
            ),
        ),
        (
            QMMM_EMBEDDING_PREPARE,
            (
                "electronic_qm_region_preparation",
                "electronic_molecule",
                "molecular_environment",
                "molecular_simulation_system",
            ),
            ("qmmm_embedding_foundation",),
            (
                "qmmm_embedding_foundation",
                "electrostatic_embedding_preparation",
            ),
            True,
            (
                "Embedding charges come only from the parameterized "
                "OpenMM NonbondedForce artifact.",
                "QM particles are excluded using the explicit Phase 8 "
                "QM-region preparation artifact.",
                "Simple single-bond covalent cuts use an explicit charge-shift "
                "scheme over bonded MM-side neighbors.",
                "Every removed and redistributed force-field charge is retained "
                "as auditable adjustment evidence.",
                "This capability prepares static point charges; the actual "
                "PySCF embedded electronic calculation is a separate capability.",
            ),
        ),
        (
            QMMM_HARTREE_FOCK,
            (
                "electronic_molecule",
                "electronic_structure_configuration",
                "qmmm_embedding_foundation",
            ),
            ("electronic_qmmm_hartree_fock_result",),
            (
                "qmmm_hartree_fock",
                "electrostatic_embedding_execution",
                "coupled_qm_mm_electronic_calculation",
            ),
            False,
            (
                "PySCF point-charge electrostatic embedding is used.",
                "RHF, UHF and ROHF references are supported through "
                "the existing explicit electronic configuration.",
                "The embedded energy includes QM energy, QM-nuclei/MM "
                "electrostatics and electron-density/MM electrostatics.",
                "MM internal energy, MM-MM electrostatics, QM-MM van der "
                "Waals and other MM bonded/nonbonded terms are not included.",
                "Supported covalent cuts consume the explicit link-atom and "
                "charge-shift boundary preparation artifact.",
            ),
        ),
        (
            QMMM_HYBRID_EXECUTE,
            (
                "electronic_molecule",
                "electronic_structure_configuration",
                "electronic_qm_region_preparation",
                "qmmm_embedding_foundation",
                "electronic_qmmm_hartree_fock_result",
                "molecular_environment",
                "molecular_simulation_system",
            ),
            ("electronic_qmmm_hybrid_result",),
            (
                "qmmm_total_energy",
                "qmmm_gradient",
                "covalent_qmmm_boundary_execution",
            ),
            False,
            (
                "The supported subtractive expression removes both the QM-region "
                "MM model energy and classical QM/MM electrostatics, preventing "
                "double counting with PySCF electronic embedding.",
                "Version 1 accepts non-periodic OpenMM systems with one conventional "
                "NonbondedForce and standard harmonic/periodic bonded forces.",
                "Link-atom gradients are projected onto real QM/MM boundary atoms "
                "by the analytical link-coordinate Jacobian.",
                "Periodic electrostatics, polarizable force fields, custom forces, "
                "and multiple-bond or metal-crossing boundaries fail closed.",
            ),
        ),

    )

    return tuple(
        CapabilityExecutionEnvelope(
            descriptor=_descriptor(
                capability_name,
                accepted_artifact_types=accepted,
                produced_artifact_types=produced,
                deterministic=deterministic,
            ),
            supported_objective_types=objectives,
            execution_targets=("local_cpu",),
            known_limitations=limitations,
            metadata={
                "scientific_domain": "electronic_structure",
                "adapter_family": "pyscf",
            },
        )
        for (
            capability_name,
            accepted,
            produced,
            objectives,
            deterministic,
            limitations,
        ) in definitions
    )


def _stable_identifier(prefix: str, *values: object) -> str:
    encoded = "\x1f".join(str(value) for value in values).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(encoded).hexdigest()[:32]}"


def _flatten(array: object) -> tuple[float, ...]:
    return tuple(float(value) for value in array.reshape(-1).tolist())


def _tensor(
    array: object,
    *,
    unit: str,
    index_convention: str,
) -> ElectronicTensor:
    shape = tuple(int(value) for value in array.shape)
    return ElectronicTensor(
        shape=shape,
        values=_flatten(array),
        unit=unit,
        index_convention=index_convention,
    )


@dataclass(frozen=True)
class HybridQMMMPotentialEvaluation:
    """One native evaluation of CGR's authoritative supported hybrid potential."""

    total_energy_hartree: float
    gradient_hartree_per_bohr: object
    embedded_qm_energy_hartree: float
    full_mm_energy_hartree: float
    qm_model_mm_energy_hartree: float
    classical_cross_electrostatic_energy_hartree: float
    molecule: ElectronicMolecule


@dataclass
class HybridQMMMPotential:
    """Coordinate-variable view of the same formulation used by the public capability."""

    adapter: "PySCFElectronicStructureAdapter"
    molecule: ElectronicMolecule
    configuration: ElectronicStructureConfiguration
    preparation: ElectronicQMRegionPreparation
    embedding: QMMMEmbeddingFoundation
    qmmm_result: ElectronicQMMMHartreeFockResult
    environment: MolecularEnvironment
    system_manifest: MolecularSimulationSystem
    private_xml: bytes
    full_system: object
    model_system: object
    cross_system: object
    evaluation_count: int = 0
    energy_history_hartree: list[float] = field(default_factory=list)
    gradient_norm_history_hartree_per_bohr: list[float] = field(default_factory=list)

    formulation: str = "electrostatic_embedding_subtractive_openmm_pyscf"

    def evaluate(self, coordinates_angstrom: object) -> HybridQMMMPotentialEvaluation:
        evaluation = self.adapter._evaluate_hybrid_qmmm_potential(
            potential=self,
            coordinates_angstrom=coordinates_angstrom,
        )
        import numpy

        gradient = numpy.asarray(evaluation.gradient_hartree_per_bohr, dtype=float)
        self.evaluation_count += 1
        self.energy_history_hartree.append(evaluation.total_energy_hartree)
        self.gradient_norm_history_hartree_per_bohr.append(
            float(numpy.sqrt(numpy.mean(gradient * gradient)))
        )
        return evaluation


class PySCFElectronicStructureAdapter:
    """Execute Phase 4 capabilities through a pinned PySCF runtime."""

    def __init__(
        self,
        payload_store: ElectronicArtifactPayloadStore,
        *,
        private_state_store: ElectronicPrivateStateStore | None = None,
        maximum_payload_bytes: int = _MAXIMUM_PAYLOAD_BYTES,
    ) -> None:
        if not isinstance(payload_store, ElectronicArtifactPayloadStore):
            raise PySCFAdapterConfigurationError(
                "The PySCF adapter requires an electronic artifact payload store."
            )
        if maximum_payload_bytes <= 0:
            raise PySCFAdapterConfigurationError(
                "The PySCF payload limit must be positive."
            )
        self._payload_store = payload_store
        if private_state_store is not None and not isinstance(
            private_state_store, ElectronicPrivateStateStore
        ):
            raise PySCFAdapterConfigurationError(
                "The PySCF private-state store does not implement exact reads."
            )
        self._private_state_store = private_state_store
        self._maximum_payload_bytes = maximum_payload_bytes
        distribution = _distribution_version() or "unavailable"
        self._declaration = ScientificEngineAdapterDeclaration(
            adapter_identifier="adapter.pyscf.electronic_structure",
            adapter_version=_ADAPTER_VERSION,
            engine=ScientificEngineIdentity(
                engine_identifier="engine.pyscf",
                engine_version=distribution,
            ),
            capabilities=pyscf_capability_envelopes(),
            metadata={
                "scientific_domain": "electronic_structure",
                "native_objects_public": False,
                "integral_artifacts_public": True,
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
        """Report the exact optional PySCF dependency identity."""

        identity = _pyscf_identity()
        if identity is None:
            return ScientificEngineHealthReport(
                status=HealthStatus.UNAVAILABLE,
                reason_code="dependency_missing",
                message="The optional PySCF dependency is unavailable.",
                diagnostics={"expected_version": _EXPECTED_PYSCF_VERSION},
            )
        distribution, module_version = identity
        diagnostics = {
            "distribution_version": distribution,
            "module_version": module_version,
        }
        if (
            distribution != _EXPECTED_PYSCF_VERSION
            or module_version != _EXPECTED_PYSCF_VERSION
        ):
            return ScientificEngineHealthReport(
                status=HealthStatus.DEGRADED,
                reason_code="dependency_version_mismatch",
                message="The installed PySCF version does not match the repository pin.",
                diagnostics={
                    **diagnostics,
                    "expected_version": _EXPECTED_PYSCF_VERSION,
                },
            )
        return ScientificEngineHealthReport(
            status=HealthStatus.HEALTHY,
            diagnostics=diagnostics,
        )

    def invoke(self, invocation: CapabilityInvocation) -> CapabilityResult:
        """Execute one declared electronic-structure capability."""

        if not isinstance(invocation, CapabilityInvocation):
            return self._failure(
                "invalid_invocation",
                "The PySCF adapter requires a scientific capability invocation.",
            )
        capability_name = invocation.capability.capability_name
        envelope = self._envelopes.get(capability_name)
        if envelope is None or invocation.capability != envelope.descriptor:
            return self._failure(
                "capability_not_declared",
                "The requested capability is not declared by this PySCF adapter.",
            )

        requires_engine = capability_name in {
            HARTREE_FOCK,
            REFERENCE_CALCULATE,
            ACTIVE_SPACE_SELECT,
            ACTIVE_SPACE_CONSTRUCT,
            IMPLICIT_SOLVENT_HARTREE_FOCK,
            GRADIENT_CALCULATE,
            FREQUENCY_ANALYZE,
            TRANSITION_STATE_SEARCH,
            REACTION_PATH_CONFIRM,
            QMMM_HARTREE_FOCK,
            QMMM_HYBRID_EXECUTE,
        }
        if requires_engine:
            health = self.health()
            if health.status is not HealthStatus.HEALTHY:
                return self._failure(
                    health.reason_code or "adapter_unhealthy",
                    health.message
                    or "The PySCF adapter is not healthy enough to execute.",
                )

        try:
            if capability_name == QM_REGION_PREPARE:
                return self._prepare_qm_region(invocation)
            if capability_name == MOLECULE_CONSTRUCT:
                return self._construct_molecule(invocation)
            if capability_name == CONFIGURATION_DEFINE:
                return self._define_configuration(invocation)
            if capability_name == HARTREE_FOCK:
                return self._run_hartree_fock(invocation)
            if capability_name == ORBITALS_GENERATE:
                return self._generate_orbitals(invocation)
            if capability_name == REFERENCE_CALCULATE:
                return self._calculate_reference(invocation)
            if capability_name == ACTIVE_SPACE_SELECT:
                return self._select_active_space(invocation)
            if capability_name == ACTIVE_SPACE_CONSTRUCT:
                return self._construct_active_space(invocation)
            if capability_name == IMPLICIT_SOLVENT_HARTREE_FOCK:
                return self._run_implicit_solvent_hartree_fock(invocation)
            if capability_name == GRADIENT_CALCULATE:
                return self._calculate_gradient(invocation)
            if capability_name == FREQUENCY_ANALYZE:
                return self._analyze_frequencies(invocation)
            if capability_name == TRANSITION_STATE_SEARCH:
                return self._search_transition_state(invocation)
            if capability_name == REACTION_PATH_CONFIRM:
                return self._confirm_reaction_path(invocation)
            if capability_name == QMMM_EMBEDDING_PREPARE:
                return self._prepare_qmmm_embedding(invocation)
            if capability_name == QMMM_HARTREE_FOCK:
                return self._run_qmmm_hartree_fock(invocation)
            if capability_name == QMMM_HYBRID_EXECUTE:
                return self._run_qmmm_hybrid(invocation)
        except (ValidationError, ValueError, KeyError, IndexError):
            return self._failure(
                "electronic_input_invalid",
                "The electronic-structure input failed controlled validation.",
            )
        except ImportError:
            return self._failure(
                "dependency_missing",
                "The optional PySCF dependency is unavailable.",
            )
        except Exception as error:
            detail = " ".join(str(error).split())[:512]
            return self._failure(
                "electronic_execution_failed",
                (
                    "The electronic-structure capability failed unexpectedly "
                    f"({type(error).__name__}: {detail or 'no diagnostic supplied'})."
                ),
            )
        return self._failure(
            "capability_not_implemented",
            "The requested electronic-structure capability is not implemented.",
        )

    def _read_payload(self, reference: ArtifactReference) -> bytes:
        payload = self._payload_store.read(reference)
        if not isinstance(payload, bytes):
            raise ValueError("Electronic artifact payload stores must return bytes.")
        if not payload or len(payload) > self._maximum_payload_bytes:
            raise ValueError("Electronic artifact payload size is invalid.")
        if hashlib.sha256(payload).hexdigest() != reference.content_sha256:
            raise ValueError("Electronic artifact bytes do not match their SHA-256.")
        if reference.byte_size is not None and reference.byte_size != len(payload):
            raise ValueError(
                "Electronic artifact bytes do not match their declared size."
            )
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
            raise ValueError("Generated electronic artifact payload size is invalid.")
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
        identity = _pyscf_identity()
        distribution = identity[0] if identity is not None else "not_required"
        module_version = identity[1] if identity is not None else "not_required"
        return CapabilityResult(
            status=ExecutionStatus.SUCCESS,
            output_artifacts=artifacts,
            lineage=lineage,
            diagnostics=dict(diagnostics or {}),
            execution_evidence=ExecutionEvidence(
                runtime_identifier="pyscf.local",
                exit_code=0,
                evidence_artifacts=tuple(artifact.pointer for artifact in artifacts),
                details={
                    "capability_name": invocation.capability.capability_name,
                    "pyscf_distribution_version": distribution,
                    "pyscf_module_version": module_version,
                    "native_objects_public": False,
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
                raise ValueError(f"Missing required electronic parameter: {name}.")
            return None
        return parameters[name]

    @classmethod
    def _string_parameter(
        cls,
        invocation: CapabilityInvocation,
        name: str,
        *,
        required: bool = True,
        maximum_length: int = 4096,
    ) -> str:
        value = cls._parameter(invocation, name, required=required)
        if not isinstance(value, str):
            raise ValueError(f"Electronic parameter {name} must be text.")
        normalized = value.strip()
        if not normalized or len(normalized) > maximum_length:
            raise ValueError(f"Electronic parameter {name} is invalid.")
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
            raise ValueError(f"Electronic parameter {name} must be an integer.")
        if not minimum <= value <= maximum:
            raise ValueError(
                f"Electronic parameter {name} is outside its allowed range."
            )
        return value

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
            raise ValueError(f"Electronic parameter {name} must be numeric.")
        normalized = float(value)
        if not math.isfinite(normalized) or not minimum <= normalized <= maximum:
            raise ValueError(
                f"Electronic parameter {name} is outside its allowed range."
            )
        return normalized

    @classmethod
    def _boolean_parameter(
        cls,
        invocation: CapabilityInvocation,
        name: str,
    ) -> bool:
        value = cls._parameter(invocation, name, required=True)
        if not isinstance(value, bool):
            raise ValueError(f"Electronic parameter {name} must be boolean.")
        return value

    @classmethod
    def _index_list_parameter(
        cls,
        invocation: CapabilityInvocation,
        name: str,
    ) -> tuple[int, ...]:
        raw = cls._string_parameter(invocation, name, maximum_length=4096)
        parts = [part.strip() for part in raw.split(",")]
        if not parts or any(not part or not part.isdigit() for part in parts):
            raise ValueError(
                f"Electronic parameter {name} must be comma-separated indices."
            )
        indices = tuple(int(part) for part in parts)
        if len(indices) != len(set(indices)):
            raise ValueError(f"Electronic parameter {name} contains duplicate indices.")
        return indices

    @staticmethod
    def _inputs_by_type(
        invocation: CapabilityInvocation,
    ) -> dict[str, ArtifactReference]:
        values: dict[str, ArtifactReference] = {}
        for reference in invocation.input_artifacts:
            if reference.artifact_type in values:
                raise ValueError(
                    "Electronic capabilities accept at most one artifact per type."
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
                f"Electronic capability requires an {artifact_type} artifact."
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

    @staticmethod
    def _alpha_beta_counts(electron_count: int, spin: int) -> tuple[int, int]:
        if spin < 0 or spin > electron_count or (electron_count - spin) % 2:
            raise ValueError("Spin is incompatible with electron count.")
        alpha = (electron_count + spin) // 2
        return alpha, electron_count - alpha

    def _prepare_qm_region(
        self,
        invocation: CapabilityInvocation,
    ) -> CapabilityResult:
        inputs = self._inputs_by_type(invocation)

        environment_reference = self._require_input(
            inputs,
            "molecular_environment",
        )

        if len(inputs) != 1:
            raise ValueError(
                "QM-region preparation requires one exact environment input."
            )

        environment = self._load_model(
            environment_reference,
            MolecularEnvironment,
        )

        seed_indices = self._index_list_parameter(
            invocation,
            "seed_particle_indices",
        )

        expansion_depth = self._integer_parameter(
            invocation,
            "expansion_bond_depth",
            minimum=0,
            maximum=8,
        )

        include_seed_residues = self._boolean_parameter(
            invocation,
            "include_seed_residues",
        )

        maximum_spin = self._integer_parameter(
            invocation,
            "maximum_transition_metal_spin",
            minimum=0,
            maximum=12,
        )

        result = prepare_qm_region(
            environment,
            source_artifact_identifier=(
                environment_reference.artifact_identifier
            ),
            seed_particle_indices=seed_indices,
            expansion_bond_depth=expansion_depth,
            include_seed_residues=include_seed_residues,
            maximum_transition_metal_spin=maximum_spin,
        )

        molecule_references = tuple(
            self._write_model(
                invocation,
                model=molecule,
                artifact_type="electronic_molecule",
                identifier_prefix="electronic-molecule",
                parents=(environment_reference,),
                metadata={
                    "atom_count": len(molecule.atoms),
                    "electron_count": molecule.electron_count,
                    "molecular_charge": molecule.molecular_charge,
                    "spin": molecule.spin,
                    "qm_region_prepared": True,
                },
            )
            for molecule in result.molecules
        )

        preparation_reference = self._write_model(
            invocation,
            model=result.preparation,
            artifact_type="electronic_qm_region_preparation",
            identifier_prefix="electronic-qm-region",
            parents=(
                environment_reference,
                *molecule_references,
            ),
            metadata={
                "selected_particle_count": len(
                    result.preparation.selected_particle_indices
                ),
                "boundary_link_count": len(
                    result.preparation.boundary_links
                ),
                "molecular_charge": (
                    result.preparation
                    .charge_spin_assessment
                    .molecular_charge
                ),
                "spin_candidate_count": len(
                    result.preparation
                    .charge_spin_assessment
                    .spin_candidates
                ),
            },
        )

        lineage: list[ArtifactLineageEdge] = []

        for reference in molecule_references:
            lineage.extend(
                self._lineage(
                    invocation,
                    parents=(environment_reference,),
                    child=reference,
                    relationship_type="constructs",
                )
            )

        lineage.extend(
            self._lineage(
                invocation,
                parents=(
                    environment_reference,
                    *molecule_references,
                ),
                child=preparation_reference,
                relationship_type="prepares",
            )
        )

        return self._success(
            invocation,
            artifacts=(
                preparation_reference,
                *molecule_references,
            ),
            lineage=tuple(lineage),
            diagnostics={
                "selected_particle_count": len(
                    result.preparation.selected_particle_indices
                ),
                "boundary_link_count": len(
                    result.preparation.boundary_links
                ),
                "spin_candidate_count": len(
                    result.preparation
                    .charge_spin_assessment
                    .spin_candidates
                ),
            },
        )

    def _construct_molecule(
        self,
        invocation: CapabilityInvocation,
    ) -> CapabilityResult:
        inputs = self._inputs_by_type(invocation)
        charge = self._integer_parameter(
            invocation, "molecular_charge", minimum=-1000, maximum=1000
        )
        spin = self._integer_parameter(invocation, "spin", minimum=0, maximum=1000)

        conformer_reference = inputs.get("molecular_conformer_set")
        environment_reference = inputs.get("molecular_environment")
        snapshot_reference = inputs.get("molecular_snapshot")
        source_references = tuple(invocation.input_artifacts)

        atoms: list[ElectronicAtom] = []
        source_geometry_identifier: str

        if conformer_reference is not None:
            if environment_reference is not None or snapshot_reference is not None:
                raise ValueError(
                    "Conformer construction cannot mix simulation artifacts."
                )
            conformer_set = self._load_model(conformer_reference, MolecularConformerSet)
            conformer_index = self._integer_parameter(
                invocation,
                "conformer_index",
                minimum=0,
                maximum=len(conformer_set.conformers) - 1,
            )
            conformer = next(
                item
                for item in conformer_set.conformers
                if item.conformer_index == conformer_index
            )
            coordinate_by_index = {
                coordinate.atom_index: coordinate
                for coordinate in conformer.coordinates
            }
            for graph_atom in conformer_set.source_graph.atoms:
                coordinate = coordinate_by_index[graph_atom.atom_index]
                atoms.append(
                    ElectronicAtom(
                        atom_index=graph_atom.atom_index,
                        atomic_number=graph_atom.atomic_number,
                        element_symbol=graph_atom.element_symbol,
                        x_angstrom=coordinate.x,
                        y_angstrom=coordinate.y,
                        z_angstrom=coordinate.z,
                        source_atom_index=graph_atom.source_atom_index,
                    )
                )
            source_geometry_identifier = conformer.conformer_identifier
            source_artifact_identifier = conformer_reference.artifact_identifier
            source_formal_charge: int | None = conformer_set.source_graph.formal_charge
        else:
            if environment_reference is None:
                raise ValueError(
                    "Molecule construction requires a conformer set or environment."
                )
            environment = self._load_model(environment_reference, MolecularEnvironment)
            positions = environment.positions
            if snapshot_reference is not None:
                snapshot = self._load_model(snapshot_reference, MolecularSnapshot)
                if snapshot.particle_count != len(environment.atoms):
                    raise ValueError(
                        "Snapshot and environment particle counts do not match."
                    )
                positions = snapshot.positions
                source_geometry_identifier = snapshot.snapshot_identifier
                source_artifact_identifier = snapshot_reference.artifact_identifier
            else:
                source_geometry_identifier = environment.environment_identifier
                source_artifact_identifier = environment_reference.artifact_identifier
            selected_atoms = environment.atoms[: environment.source_solute_atom_count]
            selected_positions = positions[: environment.source_solute_atom_count]
            if len(selected_atoms) != len(selected_positions):
                raise ValueError(
                    "Environment solute topology and positions do not match."
                )
            formal_charges = tuple(atom.formal_charge for atom in selected_atoms)
            source_formal_charge = (
                sum(
                    charge_value
                    for charge_value in formal_charges
                    if charge_value is not None
                )
                if all(charge_value is not None for charge_value in formal_charges)
                else None
            )
            for index, (source_atom, position) in enumerate(
                zip(selected_atoms, selected_positions, strict=True)
            ):
                if (
                    source_atom.atomic_number is None
                    or source_atom.element_symbol is None
                    or source_atom.particle_kind != "atom"
                ):
                    raise ValueError(
                        "Electronic molecules cannot contain extra particles."
                    )
                atoms.append(
                    ElectronicAtom(
                        atom_index=index,
                        atomic_number=source_atom.atomic_number,
                        element_symbol=source_atom.element_symbol,
                        x_angstrom=10.0 * position.x,
                        y_angstrom=10.0 * position.y,
                        z_angstrom=10.0 * position.z,
                        source_atom_index=source_atom.source_atom_index,
                    )
                )

        if not atoms or len(atoms) > _MAXIMUM_ATOMS:
            raise ValueError(
                "Electronic molecule atom count is outside the supported range."
            )
        electron_count = sum(atom.atomic_number for atom in atoms) - charge
        alpha, beta = self._alpha_beta_counts(electron_count, spin)
        molecule = ElectronicMolecule(
            schema_version=_SCHEMA_VERSION,
            molecule_identifier=_stable_identifier(
                "electronic-molecule",
                source_geometry_identifier,
                charge,
                spin,
                *(atom.model_dump_json() for atom in atoms),
            ),
            source_artifact_identifier=source_artifact_identifier,
            source_geometry_identifier=source_geometry_identifier,
            atoms=tuple(atoms),
            molecular_charge=charge,
            source_formal_charge=source_formal_charge,
            spin=spin,
            electron_count=electron_count,
            alpha_electron_count=alpha,
            beta_electron_count=beta,
        )
        reference = self._write_model(
            invocation,
            model=molecule,
            artifact_type="electronic_molecule",
            identifier_prefix="electronic-molecule",
            parents=source_references,
            metadata={
                "atom_count": len(atoms),
                "electron_count": electron_count,
                "molecular_charge": charge,
                "spin": spin,
            },
        )
        return self._success(
            invocation,
            artifacts=(reference,),
            lineage=self._lineage(
                invocation,
                parents=source_references,
                child=reference,
                relationship_type="constructs",
            ),
            diagnostics={
                "atom_count": len(atoms),
                "electron_count": electron_count,
            },
        )

    def _define_configuration(
        self,
        invocation: CapabilityInvocation,
    ) -> CapabilityResult:
        inputs = self._inputs_by_type(invocation)
        molecule_reference = self._require_input(inputs, "electronic_molecule")
        if len(inputs) != 1:
            raise ValueError("Configuration definition accepts only one molecule.")
        molecule = self._load_model(molecule_reference, ElectronicMolecule)

        basis_set = self._string_parameter(invocation, "basis_set", maximum_length=128)
        reference_method = self._string_parameter(
            invocation, "reference_method", maximum_length=16
        ).lower()
        if reference_method not in {"rhf", "uhf", "rohf"}:
            raise ValueError("Unsupported Hartree-Fock reference method.")
        if reference_method == "rhf" and molecule.spin != 0:
            raise ValueError("RHF requires a closed-shell spin-zero molecule.")
        if reference_method == "rohf" and molecule.spin == 0:
            raise ValueError("ROHF requires an open-shell molecule.")

        configuration = ElectronicStructureConfiguration(
            schema_version=_SCHEMA_VERSION,
            configuration_identifier=_stable_identifier(
                "electronic-configuration",
                molecule.molecule_identifier,
                basis_set,
                reference_method,
                self._float_parameter(
                    invocation,
                    "convergence_tolerance",
                    minimum=1e-15,
                    maximum=1e-2,
                ),
                self._integer_parameter(
                    invocation,
                    "maximum_iterations",
                    minimum=1,
                    maximum=100_000,
                ),
                self._boolean_parameter(invocation, "direct_scf"),
                self._boolean_parameter(invocation, "density_fitting"),
                self._boolean_parameter(invocation, "symmetry"),
                self._string_parameter(
                    invocation, "initial_guess", maximum_length=16
                ).lower(),
            ),
            molecule_identifier=molecule.molecule_identifier,
            basis_set=basis_set,
            reference_method=reference_method,
            convergence_tolerance=self._float_parameter(
                invocation,
                "convergence_tolerance",
                minimum=1e-15,
                maximum=1e-2,
            ),
            maximum_iterations=self._integer_parameter(
                invocation,
                "maximum_iterations",
                minimum=1,
                maximum=100_000,
            ),
            direct_scf=self._boolean_parameter(invocation, "direct_scf"),
            density_fitting=self._boolean_parameter(invocation, "density_fitting"),
            symmetry=self._boolean_parameter(invocation, "symmetry"),
            initial_guess=self._string_parameter(
                invocation, "initial_guess", maximum_length=16
            ).lower(),
        )
        reference = self._write_model(
            invocation,
            model=configuration,
            artifact_type="electronic_structure_configuration",
            identifier_prefix="electronic-configuration",
            parents=(molecule_reference,),
            metadata={
                "basis_set": configuration.basis_set,
                "reference_method": configuration.reference_method,
                "maximum_iterations": configuration.maximum_iterations,
            },
        )
        return self._success(
            invocation,
            artifacts=(reference,),
            lineage=self._lineage(
                invocation,
                parents=(molecule_reference,),
                child=reference,
                relationship_type="configures",
            ),
            diagnostics={
                "basis_set": configuration.basis_set,
                "reference_method": configuration.reference_method,
            },
        )

    @staticmethod
    def _build_pyscf_molecule(
        molecule: ElectronicMolecule,
        configuration: ElectronicStructureConfiguration,
        gto: object,
    ) -> object:
        native = gto.Mole()
        native.atom = [
            (
                atom.element_symbol,
                (atom.x_angstrom, atom.y_angstrom, atom.z_angstrom),
            )
            for atom in molecule.atoms
        ]
        native.unit = "Angstrom"
        native.charge = molecule.molecular_charge
        native.spin = molecule.spin
        native.basis = configuration.basis_set
        native.symmetry = configuration.symmetry
        native.verbose = 0
        native.build(parse_arg=False)
        return native

    @staticmethod
    def _build_scf(
        molecule: ElectronicMolecule,
        configuration: ElectronicStructureConfiguration,
        native_molecule: object,
        scf: object,
    ) -> object:
        if configuration.reference_method == "rhf":
            native = scf.RHF(native_molecule)
        elif configuration.reference_method == "uhf":
            native = scf.UHF(native_molecule)
        else:
            native = scf.ROHF(native_molecule)
        native.max_cycle = configuration.maximum_iterations
        native.conv_tol = configuration.convergence_tolerance
        native.direct_scf = configuration.direct_scf
        native.init_guess = configuration.initial_guess
        if configuration.density_fitting:
            native = native.density_fit()
        return native

    @staticmethod
    def _spin_data(
        numpy: object,
        *,
        method: str,
        mo_coeff: object,
        mo_energy: object,
        mo_occ: object,
    ) -> tuple[object, object, object, object, object, object]:
        if method == "uhf":
            coeff_alpha, coeff_beta = mo_coeff
            energy_alpha, energy_beta = mo_energy
            occupation_alpha, occupation_beta = mo_occ
            return (
                numpy.asarray(coeff_alpha),
                numpy.asarray(coeff_beta),
                numpy.asarray(energy_alpha),
                numpy.asarray(energy_beta),
                numpy.asarray(occupation_alpha),
                numpy.asarray(occupation_beta),
            )
        coefficients = numpy.asarray(mo_coeff)
        energies = numpy.asarray(mo_energy)
        occupations = numpy.asarray(mo_occ)
        occupation_alpha = numpy.clip(occupations, 0.0, 1.0)
        occupation_beta = numpy.clip(occupations - 1.0, 0.0, 1.0)
        return (
            coefficients,
            coefficients.copy(),
            energies,
            energies.copy(),
            occupation_alpha,
            occupation_beta,
        )

    @staticmethod
    def _density_matrices(
        numpy: object,
        *,
        coeff_alpha: object,
        coeff_beta: object,
        occupation_alpha: object,
        occupation_beta: object,
    ) -> tuple[object, object]:
        density_alpha = (coeff_alpha * occupation_alpha) @ coeff_alpha.T
        density_beta = (coeff_beta * occupation_beta) @ coeff_beta.T
        return numpy.asarray(density_alpha), numpy.asarray(density_beta)

    @staticmethod
    def _common_spatial_orbitals(
        numpy: object,
        result: ElectronicHartreeFockResult,
    ) -> tuple[object, object, object, object, str]:
        """Return one orthonormal spatial basis and auditable spin populations.

        RHF and ROHF already use a common spatial-orbital basis.  UHF does not;
        for that case the spin-summed one-particle density is diagonalized in
        the symmetrically orthogonalized AO basis to form unrestricted natural
        orbitals (UNOs).  The UNO basis is the common spatial representation
        required by the public active-space integral contract.
        """

        n_ao = result.atomic_orbital_count
        n_mo = result.spatial_orbital_count
        alpha_coefficients = numpy.asarray(
            result.mo_coefficients_alpha.values,
            dtype=float,
        ).reshape((n_ao, n_mo))

        if result.reference_method != "uhf":
            alpha_occupations = numpy.asarray(
                result.orbital_occupations_alpha,
                dtype=float,
            )
            beta_occupations = numpy.asarray(
                result.orbital_occupations_beta,
                dtype=float,
            )
            basis = (
                "canonical_rhf"
                if result.reference_method == "rhf"
                else "canonical_rohf"
            )
            return (
                alpha_coefficients,
                alpha_occupations + beta_occupations,
                alpha_occupations,
                beta_occupations,
                basis,
            )

        overlap = numpy.asarray(
            result.overlap_matrix_ao.values,
            dtype=float,
        ).reshape((n_ao, n_ao))
        density_alpha = numpy.asarray(
            result.density_matrix_alpha_ao.values,
            dtype=float,
        ).reshape((n_ao, n_ao))
        density_beta = numpy.asarray(
            result.density_matrix_beta_ao.values,
            dtype=float,
        ).reshape((n_ao, n_ao))

        overlap_values, overlap_vectors = numpy.linalg.eigh(overlap)
        if float(numpy.min(overlap_values)) <= 1e-10:
            raise ValueError(
                "UHF natural orbitals require a positive-definite AO overlap."
            )
        overlap_half = (
            overlap_vectors
            @ numpy.diag(numpy.sqrt(overlap_values))
            @ overlap_vectors.T
        )
        overlap_inverse_half = (
            overlap_vectors
            @ numpy.diag(1.0 / numpy.sqrt(overlap_values))
            @ overlap_vectors.T
        )
        orthogonal_density = (
            overlap_half @ (density_alpha + density_beta) @ overlap_half
        )
        occupations, natural_vectors = numpy.linalg.eigh(orthogonal_density)
        order = numpy.argsort(occupations)[::-1]
        occupations = numpy.clip(occupations[order], 0.0, 2.0)
        coefficients = overlap_inverse_half @ natural_vectors[:, order]
        alpha_occupations = numpy.diag(
            coefficients.T @ overlap @ density_alpha @ overlap @ coefficients
        )
        beta_occupations = numpy.diag(
            coefficients.T @ overlap @ density_beta @ overlap @ coefficients
        )
        alpha_occupations = numpy.clip(alpha_occupations, 0.0, 1.0)
        beta_occupations = numpy.clip(beta_occupations, 0.0, 1.0)
        return (
            coefficients,
            occupations,
            alpha_occupations,
            beta_occupations,
            "unrestricted_natural_orbital",
        )

    def _run_native_scf(
        self,
        molecule: ElectronicMolecule,
        configuration: ElectronicStructureConfiguration,
    ) -> tuple[object, object, object, object]:
        numpy, gto, scf, _, _ = _pyscf_modules()
        native_molecule = self._build_pyscf_molecule(molecule, configuration, gto)
        native_scf = self._build_scf(molecule, configuration, native_molecule, scf)
        total_energy = float(native_scf.kernel())
        return numpy, native_molecule, native_scf, total_energy

    def _run_hartree_fock(
        self,
        invocation: CapabilityInvocation,
    ) -> CapabilityResult:
        inputs = self._inputs_by_type(invocation)
        molecule_reference = self._require_input(inputs, "electronic_molecule")
        configuration_reference = self._require_input(
            inputs, "electronic_structure_configuration"
        )
        if len(inputs) != 2:
            raise ValueError(
                "Hartree-Fock requires exactly molecule and configuration."
            )
        molecule = self._load_model(molecule_reference, ElectronicMolecule)
        configuration = self._load_model(
            configuration_reference, ElectronicStructureConfiguration
        )
        if configuration.molecule_identifier != molecule.molecule_identifier:
            raise ValueError("Electronic configuration does not match its molecule.")

        numpy, native_molecule, native_scf, total_energy = self._run_native_scf(
            molecule, configuration
        )
        if not bool(native_scf.converged):
            return self._failure(
                "scf_not_converged",
                "The Hartree-Fock calculation did not converge under the explicit controls.",
                retryable=True,
            )

        (
            coeff_alpha,
            coeff_beta,
            energy_alpha,
            energy_beta,
            occupation_alpha,
            occupation_beta,
        ) = self._spin_data(
            numpy,
            method=configuration.reference_method,
            mo_coeff=native_scf.mo_coeff,
            mo_energy=native_scf.mo_energy,
            mo_occ=native_scf.mo_occ,
        )
        density_alpha, density_beta = self._density_matrices(
            numpy,
            coeff_alpha=coeff_alpha,
            coeff_beta=coeff_beta,
            occupation_alpha=occupation_alpha,
            occupation_beta=occupation_beta,
        )
        overlap = numpy.asarray(native_scf.get_ovlp())
        hcore = numpy.asarray(native_scf.get_hcore())
        nuclear = float(native_molecule.energy_nuc())
        electronic = total_energy - nuclear
        result = ElectronicHartreeFockResult(
            schema_version=_SCHEMA_VERSION,
            result_identifier=_stable_identifier(
                "hartree-fock",
                molecule.molecule_identifier,
                configuration.configuration_identifier,
                total_energy,
                bool(native_scf.converged),
            ),
            molecule_identifier=molecule.molecule_identifier,
            configuration_identifier=configuration.configuration_identifier,
            reference_method=configuration.reference_method,
            converged=True,
            iterations=int(getattr(native_scf, "cycles", 0) or 0),
            electron_count=molecule.electron_count,
            alpha_electron_count=molecule.alpha_electron_count,
            beta_electron_count=molecule.beta_electron_count,
            atomic_orbital_count=int(coeff_alpha.shape[0]),
            spatial_orbital_count=int(coeff_alpha.shape[1]),
            electronic_energy_hartree=electronic,
            nuclear_repulsion_energy_hartree=nuclear,
            total_energy_hartree=total_energy,
            orbital_energies_alpha_hartree=tuple(
                float(value) for value in energy_alpha.tolist()
            ),
            orbital_energies_beta_hartree=tuple(
                float(value) for value in energy_beta.tolist()
            ),
            orbital_occupations_alpha=tuple(
                float(value) for value in occupation_alpha.tolist()
            ),
            orbital_occupations_beta=tuple(
                float(value) for value in occupation_beta.tolist()
            ),
            mo_coefficients_alpha=_tensor(
                coeff_alpha,
                unit="dimensionless",
                index_convention="ao_by_spatial_orbital",
            ),
            mo_coefficients_beta=_tensor(
                coeff_beta,
                unit="dimensionless",
                index_convention="ao_by_spatial_orbital",
            ),
            overlap_matrix_ao=_tensor(
                overlap,
                unit="dimensionless",
                index_convention="ao_by_ao",
            ),
            core_hamiltonian_ao=_tensor(
                hcore,
                unit="hartree",
                index_convention="ao_by_ao",
            ),
            density_matrix_alpha_ao=_tensor(
                density_alpha,
                unit="electron",
                index_convention="ao_by_ao",
            ),
            density_matrix_beta_ao=_tensor(
                density_beta,
                unit="electron",
                index_convention="ao_by_ao",
            ),
        )
        parents = (molecule_reference, configuration_reference)
        reference = self._write_model(
            invocation,
            model=result,
            artifact_type="electronic_hartree_fock_result",
            identifier_prefix="hartree-fock",
            parents=parents,
            metadata={
                "reference_method": configuration.reference_method,
                "basis_set": configuration.basis_set,
                "converged": True,
                "spatial_orbital_count": result.spatial_orbital_count,
                "total_energy_hartree": result.total_energy_hartree,
            },
        )
        return self._success(
            invocation,
            artifacts=(reference,),
            lineage=self._lineage(
                invocation,
                parents=parents,
                child=reference,
                relationship_type="calculates",
            ),
            diagnostics={
                "converged": True,
                "iterations": result.iterations,
                "spatial_orbital_count": result.spatial_orbital_count,
                "total_energy_hartree": result.total_energy_hartree,
            },
        )

    def _generate_orbitals(
        self,
        invocation: CapabilityInvocation,
    ) -> CapabilityResult:
        inputs = self._inputs_by_type(invocation)
        result_reference = self._require_input(inputs, "electronic_hartree_fock_result")
        configuration_reference = self._require_input(
            inputs, "electronic_structure_configuration"
        )
        if len(inputs) != 2:
            raise ValueError("Orbital generation requires result and configuration.")
        result = self._load_model(result_reference, ElectronicHartreeFockResult)
        configuration = self._load_model(
            configuration_reference, ElectronicStructureConfiguration
        )
        if result.configuration_identifier != configuration.configuration_identifier:
            raise ValueError("Hartree-Fock result and configuration do not match.")

        n_ao = result.atomic_orbital_count
        n_mo = result.spatial_orbital_count

        def orbitals(
            channel: str,
            energies: tuple[float, ...],
            occupations: tuple[float, ...],
            coefficients: ElectronicTensor,
        ) -> tuple[ElectronicOrbital, ...]:
            return tuple(
                ElectronicOrbital(
                    orbital_index=index,
                    spin_channel=channel,
                    energy_hartree=energies[index],
                    occupation=occupations[index],
                    coefficients=tuple(
                        coefficients.values[row * n_mo + index] for row in range(n_ao)
                    ),
                )
                for index in range(n_mo)
            )

        orbital_set = ElectronicOrbitalSet(
            schema_version=_SCHEMA_VERSION,
            orbital_set_identifier=_stable_identifier(
                "orbital-set",
                result.result_identifier,
                configuration.basis_set,
            ),
            hartree_fock_result_identifier=result.result_identifier,
            basis_set=configuration.basis_set,
            atomic_orbital_count=n_ao,
            spatial_orbital_count=n_mo,
            alpha_orbitals=orbitals(
                "alpha",
                result.orbital_energies_alpha_hartree,
                result.orbital_occupations_alpha,
                result.mo_coefficients_alpha,
            ),
            beta_orbitals=orbitals(
                "beta",
                result.orbital_energies_beta_hartree,
                result.orbital_occupations_beta,
                result.mo_coefficients_beta,
            ),
        )
        parents = (result_reference, configuration_reference)
        reference = self._write_model(
            invocation,
            model=orbital_set,
            artifact_type="electronic_orbital_set",
            identifier_prefix="orbital-set",
            parents=parents,
            metadata={
                "basis_set": configuration.basis_set,
                "spatial_orbital_count": n_mo,
                "atomic_orbital_count": n_ao,
            },
        )
        return self._success(
            invocation,
            artifacts=(reference,),
            lineage=self._lineage(
                invocation,
                parents=parents,
                child=reference,
                relationship_type="projects",
            ),
            diagnostics={"spatial_orbital_count": n_mo},
        )

    def _calculate_reference(
        self,
        invocation: CapabilityInvocation,
    ) -> CapabilityResult:
        inputs = self._inputs_by_type(invocation)
        molecule_reference = self._require_input(inputs, "electronic_molecule")
        configuration_reference = self._require_input(
            inputs, "electronic_structure_configuration"
        )
        result_reference = self._require_input(inputs, "electronic_hartree_fock_result")
        if len(inputs) != 3:
            raise ValueError("Reference calculation requires three exact inputs.")
        molecule = self._load_model(molecule_reference, ElectronicMolecule)
        configuration = self._load_model(
            configuration_reference, ElectronicStructureConfiguration
        )
        result = self._load_model(result_reference, ElectronicHartreeFockResult)
        if (
            configuration.molecule_identifier != molecule.molecule_identifier
            or result.molecule_identifier != molecule.molecule_identifier
            or result.configuration_identifier != configuration.configuration_identifier
            or not result.converged
        ):
            raise ValueError(
                "Reference calculation inputs do not form one converged problem."
            )

        method = self._string_parameter(invocation, "method", maximum_length=32).lower()
        if method not in {"hartree_fock", "mp2"}:
            raise ValueError("Unsupported classical reference method.")

        correlation = 0.0
        reference_energy = result.total_energy_hartree
        converged = True
        if method == "mp2":
            if configuration.reference_method != "rhf" or molecule.spin != 0:
                raise ValueError("Version 1 MP2 requires a closed-shell RHF reference.")
            _, _, _, mp, _ = _pyscf_modules()
            _, _, native_scf, rerun_energy = self._run_native_scf(
                molecule, configuration
            )
            if not bool(native_scf.converged):
                return self._failure(
                    "reference_scf_not_converged",
                    "The explicit SCF reference could not be reproduced for MP2.",
                    retryable=True,
                )
            if not math.isclose(
                rerun_energy,
                result.total_energy_hartree,
                rel_tol=1e-8,
                abs_tol=1e-8,
            ):
                return self._failure(
                    "reference_identity_mismatch",
                    "The recomputed SCF reference does not match the supplied artifact.",
                )
            native_mp2 = mp.MP2(native_scf)
            correlation, _ = native_mp2.kernel()
            correlation = float(correlation)
            converged = bool(getattr(native_mp2, "converged", True))
            if not converged:
                return self._failure(
                    "mp2_not_converged",
                    "The MP2 reference calculation did not converge.",
                    retryable=True,
                )

        reference = ElectronicReferenceCalculation(
            schema_version=_SCHEMA_VERSION,
            reference_identifier=_stable_identifier(
                "electronic-reference",
                result.result_identifier,
                method,
                correlation,
            ),
            molecule_identifier=molecule.molecule_identifier,
            configuration_identifier=configuration.configuration_identifier,
            hartree_fock_result_identifier=result.result_identifier,
            method=method,
            converged=converged,
            reference_energy_hartree=reference_energy,
            correlation_energy_hartree=correlation,
            total_energy_hartree=reference_energy + correlation,
        )
        parents = (molecule_reference, configuration_reference, result_reference)
        output = self._write_model(
            invocation,
            model=reference,
            artifact_type="electronic_reference_calculation",
            identifier_prefix="electronic-reference",
            parents=parents,
            metadata={
                "method": method,
                "converged": converged,
                "total_energy_hartree": reference.total_energy_hartree,
            },
        )
        return self._success(
            invocation,
            artifacts=(output,),
            lineage=self._lineage(
                invocation,
                parents=parents,
                child=output,
                relationship_type="references",
            ),
            diagnostics={
                "method": method,
                "converged": converged,
                "total_energy_hartree": reference.total_energy_hartree,
            },
        )

    def _select_active_space(
        self,
        invocation: CapabilityInvocation,
    ) -> CapabilityResult:
        inputs = self._inputs_by_type(invocation)

        molecule_reference = self._require_input(
            inputs,
            "electronic_molecule",
        )
        configuration_reference = self._require_input(
            inputs,
            "electronic_structure_configuration",
        )
        result_reference = self._require_input(
            inputs,
            "electronic_hartree_fock_result",
        )

        if len(inputs) != 3:
            raise ValueError(
                "Active-space selection requires three exact inputs."
            )

        molecule = self._load_model(
            molecule_reference,
            ElectronicMolecule,
        )
        configuration = self._load_model(
            configuration_reference,
            ElectronicStructureConfiguration,
        )
        result = self._load_model(
            result_reference,
            ElectronicHartreeFockResult,
        )

        if not result.converged:
            raise ValueError(
                "Automatic active-space selection requires a converged reference."
            )

        if (
            configuration.molecule_identifier
            != molecule.molecule_identifier
            or result.molecule_identifier
            != molecule.molecule_identifier
            or result.configuration_identifier
            != configuration.configuration_identifier
        ):
            raise ValueError(
                "Active-space selection inputs do not form "
                "one electronic problem."
            )

        active_electrons = self._integer_parameter(
            invocation,
            "active_electron_count",
            minimum=1,
            maximum=2 * _MAXIMUM_ACTIVE_ORBITALS,
        )
        active_orbitals = self._integer_parameter(
            invocation,
            "active_spatial_orbital_count",
            minimum=1,
            maximum=_MAXIMUM_ACTIVE_ORBITALS,
        )

        method = self._string_parameter(
            invocation,
            "selection_method",
            maximum_length=64,
        ).lower()

        if method not in {
            "frontier",
            "target_ao_projection",
            "reaction_center_projection",
            "metal_ligand_projection",
        }:
            raise ValueError(
                "Unsupported automatic active-space selection method."
            )

        if (
            active_electrons < molecule.spin
            or (active_electrons - molecule.spin) % 2
        ):
            raise ValueError(
                "Active electrons are incompatible with the molecular spin."
            )
        numpy, gto, _, _, _ = _pyscf_modules()
        (
            coefficients,
            occupation_array,
            alpha_occupation_array,
            beta_occupation_array,
            orbital_basis,
        ) = self._common_spatial_orbitals(numpy, result)
        occupations = tuple(float(value) for value in occupation_array.tolist())

        core_like = tuple(
            index for index, occupation in enumerate(occupations)
            if occupation >= 1.98
        )
        virtual = tuple(
            index for index, occupation in enumerate(occupations)
            if occupation <= 0.02
        )
        mandatory_open_shell = tuple(
            index for index, occupation in enumerate(occupations)
            if 0.02 < occupation < 1.98
        )
        mandatory_electrons = int(round(sum(
            occupations[index] for index in mandatory_open_shell
        )))
        paired_electrons = active_electrons - mandatory_electrons
        if paired_electrons < 0 or paired_electrons % 2:
            raise ValueError(
                "Requested electrons cannot retain every open-shell orbital."
            )
        occupied_needed = paired_electrons // 2
        virtual_needed = (
            active_orbitals
            - len(mandatory_open_shell)
            - occupied_needed
        )
        if (
            virtual_needed < 0
            or occupied_needed > len(core_like)
            or virtual_needed > len(virtual)
        ):
            raise ValueError("Requested active-space composition is unavailable.")

        raw_targets = self._parameter(
            invocation, "target_atom_indices", required=False
        )
        target_atom_indices: tuple[int, ...] = ()
        target_bond_atom_pairs: tuple[tuple[int, int], ...] = ()
        target_ao_labels: tuple[str, ...] = ()
        projection_threshold = self._float_parameter(
            invocation,
            "projection_threshold",
            minimum=0.0,
            maximum=1.0,
        ) if self._parameter(
            invocation, "projection_threshold", required=False
        ) is not None else 0.1

        if method == "frontier":
            if raw_targets is not None:
                raise ValueError("Frontier selection cannot receive target atoms.")
        else:
            if not isinstance(raw_targets, str):
                raise ValueError("Targeted selection requires target atom indices.")
            parts = tuple(part.strip() for part in raw_targets.split(","))
            if not parts or any(not part.isdigit() for part in parts):
                raise ValueError(
                    "Target atom indices must be comma-separated integers."
                )
            target_atom_indices = tuple(sorted(int(part) for part in parts))
            if (
                len(target_atom_indices) != len(set(target_atom_indices))
                or any(index >= len(molecule.atoms) for index in target_atom_indices)
            ):
                raise ValueError("Target atom indices are invalid for the molecule.")

        if method == "reaction_center_projection":
            raw_bonds = self._parameter(
                invocation, "target_bond_atom_pairs", required=False
            )
            if not isinstance(raw_bonds, str):
                raise ValueError("Reaction-centre selection requires target bonds.")
            parsed_bonds: list[tuple[int, int]] = []
            for raw_pair in raw_bonds.split(","):
                pair = tuple(part.strip() for part in raw_pair.split("-"))
                if len(pair) != 2 or any(not part.isdigit() for part in pair):
                    raise ValueError("Target bonds must use 'atom-atom' notation.")
                normalized_pair = tuple(sorted((int(pair[0]), int(pair[1]))))
                if (
                    normalized_pair[0] == normalized_pair[1]
                    or any(index not in target_atom_indices for index in normalized_pair)
                ):
                    raise ValueError("Target bonds must join distinct target atoms.")
                parsed_bonds.append(normalized_pair)
            target_bond_atom_pairs = tuple(sorted(parsed_bonds))
            if len(target_bond_atom_pairs) != len(set(target_bond_atom_pairs)):
                raise ValueError("Target bonds must be unique.")

        if method == "metal_ligand_projection":
            raw_labels = self._parameter(
                invocation, "target_ao_labels", required=False
            )
            if not isinstance(raw_labels, str):
                raise ValueError("Metal/ligand selection requires target AO labels.")
            target_ao_labels = tuple(
                label.strip() for label in raw_labels.split(";") if label.strip()
            )
            if not target_ao_labels or len(target_ao_labels) != len(
                set(target_ao_labels)
            ):
                raise ValueError("Target AO labels must be unique and nonempty.")

        projection_scores = {
            index: 0.0 for index in range(result.spatial_orbital_count)
        }
        if method != "frontier":
            native_molecule = self._build_pyscf_molecule(
                molecule, configuration, gto
            )
            ao_slices = numpy.asarray(native_molecule.aoslice_by_atom())
            target_ao_indices: set[int] = set()
            for atom_index in target_atom_indices:
                target_ao_indices.update(range(
                    int(ao_slices[atom_index, 2]),
                    int(ao_slices[atom_index, 3]),
                ))
            if target_ao_labels:
                ao_labels = tuple(str(label) for label in native_molecule.ao_labels())
                label_matches: set[int] = set()
                for requested_label in target_ao_labels:
                    tokens = requested_label.lower().split()
                    label_matches.update(
                        index for index, label in enumerate(ao_labels)
                        if all(token in label.lower() for token in tokens)
                    )
                target_ao_indices.intersection_update(label_matches)
            if not target_ao_indices:
                raise ValueError("Semantic targets resolve to no AO basis functions.")
            overlap = numpy.asarray(
                result.overlap_matrix_ao.values, dtype=float
            ).reshape((result.atomic_orbital_count, result.atomic_orbital_count))
            overlap_coefficients = overlap @ coefficients
            for orbital_index in range(result.spatial_orbital_count):
                population = sum(
                    coefficients[ao_index, orbital_index]
                    * overlap_coefficients[ao_index, orbital_index]
                    for ao_index in target_ao_indices
                )
                projection_scores[orbital_index] = round(abs(float(population)), 12)

        if method == "frontier":
            ranked_occupied = tuple(reversed(core_like))
            ranked_virtual = virtual
        else:
            ranked_occupied = tuple(sorted(
                core_like,
                key=lambda index: (-projection_scores[index], -index),
            ))
            ranked_virtual = tuple(sorted(
                virtual,
                key=lambda index: (-projection_scores[index], index),
            ))

        chosen_occupied = ranked_occupied[:occupied_needed]
        chosen_virtual = ranked_virtual[:virtual_needed]
        active_indices = tuple(sorted((
            *mandatory_open_shell,
            *chosen_occupied,
            *chosen_virtual,
        )))

        if len(active_indices) != active_orbitals:
            raise ValueError(
                "Automatic selection produced the wrong orbital count."
            )

        resolved_electrons = sum(occupations[index] for index in active_indices)
        tolerance = 0.25 if orbital_basis == "unrestricted_natural_orbital" else 1e-7
        if not math.isclose(
            resolved_electrons, active_electrons, rel_tol=0, abs_tol=tolerance
        ):
            raise ValueError(
                "Automatic selection produced the wrong electron count."
            )
        if method in {"reaction_center_projection", "metal_ligand_projection"}:
            targeted_occupied = (*mandatory_open_shell, *chosen_occupied)
            if (
                not targeted_occupied
                or not chosen_virtual
                or max(projection_scores[index] for index in targeted_occupied)
                < projection_threshold
                or max(projection_scores[index] for index in chosen_virtual)
                < projection_threshold
            ):
                raise ValueError(
                    "Target projections do not support both occupied and virtual "
                    "active orbitals at the requested threshold."
                )

        selection = ElectronicActiveSpaceSelection(
            schema_version=_SCHEMA_VERSION,
            selection_identifier=_stable_identifier(
                "electronic-active-selection",
                molecule.molecule_identifier,
                result.result_identifier,
                method,
                active_electrons,
                active_orbitals,
                orbital_basis,
                *target_atom_indices,
                *target_bond_atom_pairs,
                *target_ao_labels,
                *active_indices,
            ),
            molecule_identifier=molecule.molecule_identifier,
            hartree_fock_result_identifier=(
                result.result_identifier
            ),
            selection_method=method,
            reference_method=result.reference_method,
            orbital_basis=orbital_basis,
            active_electron_count=active_electrons,
            active_spatial_orbital_count=active_orbitals,
            active_orbital_indices=active_indices,
            target_atom_indices=target_atom_indices,
            target_bond_atom_pairs=target_bond_atom_pairs,
            target_ao_labels=target_ao_labels,
            projection_threshold=projection_threshold,
            orbital_scores=tuple(
                ElectronicOrbitalSelectionScore(
                    orbital_index=index,
                    occupation=occupations[index],
                    alpha_occupation=float(alpha_occupation_array[index]),
                    beta_occupation=float(beta_occupation_array[index]),
                    target_projection_score=(
                        projection_scores[index]
                    ),
                    selection_reasons=tuple(
                        reason for reason, applies in (
                            ("open_shell_mandatory", index in mandatory_open_shell),
                            ("occupied_pair", index in chosen_occupied),
                            ("virtual_partner", index in chosen_virtual),
                            (
                                "target_projection",
                                method != "frontier"
                                and projection_scores[index] >= projection_threshold,
                            ),
                            ("frontier_ordering", method == "frontier"),
                        ) if applies
                    ),
                )
                for index in active_indices
            ),
        )

        parents = (
            molecule_reference,
            configuration_reference,
            result_reference,
        )

        output = self._write_model(
            invocation,
            model=selection,
            artifact_type="electronic_active_space_selection",
            identifier_prefix="electronic-active-selection",
            parents=parents,
            metadata={
                "selection_method": method,
                "active_electron_count": active_electrons,
                "active_spatial_orbital_count": active_orbitals,
                "target_atom_count": len(target_atom_indices),
                "target_bond_count": len(target_bond_atom_pairs),
                "orbital_basis": orbital_basis,
                "reference_method": result.reference_method,
            },
        )

        return self._success(
            invocation,
            artifacts=(output,),
            lineage=self._lineage(
                invocation,
                parents=parents,
                child=output,
                relationship_type="selects",
            ),
            diagnostics={
                "selection_method": method,
                "active_electron_count": active_electrons,
                "active_spatial_orbital_count": active_orbitals,
                "target_atom_count": len(target_atom_indices),
                "target_bond_count": len(target_bond_atom_pairs),
                "orbital_basis": orbital_basis,
                "reference_method": result.reference_method,
            },
        )

    def _construct_active_space(
        self,
        invocation: CapabilityInvocation,
    ) -> CapabilityResult:
        inputs = self._inputs_by_type(invocation)
        molecule_reference = self._require_input(inputs, "electronic_molecule")
        configuration_reference = self._require_input(
            inputs, "electronic_structure_configuration"
        )
        result_reference = self._require_input(
            inputs,
            "electronic_hartree_fock_result",
        )
        selection_reference = inputs.get(
            "electronic_active_space_selection"
        )
        expected_input_count = (
            4 if selection_reference is not None else 3
        )
        if len(inputs) != expected_input_count:
            raise ValueError(
                "Active-space construction received an invalid "
                "input combination."
            )
        molecule = self._load_model(
            molecule_reference,
            ElectronicMolecule,
        )
        configuration = self._load_model(
            configuration_reference, ElectronicStructureConfiguration
        )
        result = self._load_model(result_reference, ElectronicHartreeFockResult)
        if not result.converged:
            raise ValueError("Active spaces require a converged reference.")
        if (
            configuration.molecule_identifier != molecule.molecule_identifier
            or result.molecule_identifier != molecule.molecule_identifier
            or result.configuration_identifier != configuration.configuration_identifier
        ):
            raise ValueError("Active-space inputs do not form one electronic problem.")

        if selection_reference is not None:
            if (
                self._parameter(
                    invocation,
                    "active_orbital_indices",
                    required=False,
                )
                is not None
                or self._parameter(
                    invocation,
                    "active_electron_count",
                    required=False,
                )
                is not None
            ):
                raise ValueError(
                    "Automatic selection evidence cannot be combined "
                    "with manual active-space parameters."
                )

            selection = self._load_model(
                selection_reference,
                ElectronicActiveSpaceSelection,
            )

            if (
                selection.molecule_identifier
                != molecule.molecule_identifier
                or selection.hartree_fock_result_identifier
                != result.result_identifier
                or selection.reference_method != result.reference_method
            ):
                raise ValueError(
                    "Active-space selection does not belong to "
                    "the supplied electronic problem."
                )

            active_indices = selection.active_orbital_indices
            active_electrons = selection.active_electron_count

        else:
            active_indices = self._index_list_parameter(
                invocation,
                "active_orbital_indices",
            )
            active_electrons = self._integer_parameter(
                invocation,
                "active_electron_count",
                minimum=1,
                maximum=2 * _MAXIMUM_ACTIVE_ORBITALS,
            )
        if (
            not active_indices
            or len(active_indices) > _MAXIMUM_ACTIVE_ORBITALS
            or any(index >= result.spatial_orbital_count for index in active_indices)
        ):
            raise ValueError("Active orbital indices are outside the supported range.")

        numpy, gto, scf, _, ao2mo = _pyscf_modules()
        (
            coefficients,
            occupation_array,
            _,
            _,
            orbital_basis,
        ) = self._common_spatial_orbitals(numpy, result)
        if selection_reference is not None and selection.orbital_basis != orbital_basis:
            raise ValueError(
                "Active-space selection orbital representation is not reproducible."
            )
        occupations = tuple(float(value) for value in occupation_array.tolist())
        resolved_active = sum(occupations[index] for index in active_indices)
        occupation_tolerance = (
            0.25 if orbital_basis == "unrestricted_natural_orbital" else 1e-7
        )
        if not math.isclose(
            resolved_active,
            active_electrons,
            abs_tol=occupation_tolerance,
        ):
            raise ValueError(
                "Active orbital occupations do not match active electrons."
            )
        core_indices = tuple(
            index
            for index, occupation in enumerate(occupations)
            if index not in active_indices
            and math.isclose(occupation, 2.0, abs_tol=0.02)
        )
        inactive_electrons = molecule.electron_count - active_electrons
        if inactive_electrons < 0 or inactive_electrons != 2 * len(core_indices):
            raise ValueError(
                "Inactive electrons do not resolve to closed-shell core orbitals."
            )

        native_molecule = self._build_pyscf_molecule(molecule, configuration, gto)
        native_scf = self._build_scf(
            molecule,
            configuration,
            native_molecule,
            scf,
        )
        hcore_ao = numpy.asarray(native_scf.get_hcore())
        active_coefficients = coefficients[:, active_indices]

        if core_indices:
            core_coefficients = coefficients[:, core_indices]
            core_density = 2.0 * core_coefficients @ core_coefficients.T
            core_potential = numpy.asarray(
                scf.hf.get_veff(native_molecule, core_density)
            )
            constant_energy = float(
                native_molecule.energy_nuc()
                + numpy.einsum("ij,ji->", core_density, hcore_ao)
                + 0.5 * numpy.einsum("ij,ji->", core_density, core_potential)
            )
        else:
            core_potential = numpy.zeros_like(hcore_ao)
            constant_energy = float(native_molecule.energy_nuc())

        effective_hcore = hcore_ao + core_potential
        one_body = active_coefficients.T @ effective_hcore @ active_coefficients
        n_active = len(active_indices)
        two_body = numpy.asarray(
            ao2mo.kernel(native_molecule, active_coefficients, compact=False)
        ).reshape((n_active, n_active, n_active, n_active))

        alpha_active = (active_electrons + molecule.spin) // 2
        beta_active = active_electrons - alpha_active
        if (
            molecule.alpha_electron_count - alpha_active != len(core_indices)
            or molecule.beta_electron_count - beta_active != len(core_indices)
        ):
            raise ValueError(
                "Inactive electrons do not form a shared closed-shell core."
            )
        active_space = ElectronicActiveSpace(
            schema_version=_SCHEMA_VERSION,
            active_space_identifier=_stable_identifier(
                "electronic-active-space",
                result.result_identifier,
                active_electrons,
                *active_indices,
            ),
            molecule_identifier=molecule.molecule_identifier,
            hartree_fock_result_identifier=result.result_identifier,
            active_electron_count=active_electrons,
            alpha_electron_count=alpha_active,
            beta_electron_count=beta_active,
            active_spatial_orbital_count=n_active,
            active_spin_orbital_count=2 * n_active,
            active_orbital_indices=active_indices,
            frozen_core_orbital_indices=core_indices,
            constant_energy_hartree=constant_energy,
            one_body_integrals=_tensor(
                one_body,
                unit="hartree",
                index_convention="active_pq",
            ),
            two_body_integrals=_tensor(
                two_body,
                unit="hartree",
                index_convention="chemist_active_pqrs",
            ),
        )
        parents = (
            molecule_reference,
            configuration_reference,
            result_reference,
            *(
                (selection_reference,)
                if selection_reference is not None
                else ()
            ),
        )
        output = self._write_model(
            invocation,
            model=active_space,
            artifact_type="electronic_active_space",
            identifier_prefix="electronic-active-space",
            parents=parents,
            metadata={
                "active_electron_count": active_electrons,
                "active_spatial_orbital_count": n_active,
                "frozen_core_orbital_count": len(core_indices),
                "integral_convention": active_space.integral_convention,
                "orbital_basis": orbital_basis,
                "reference_method": result.reference_method,
            },
        )
        return self._success(
            invocation,
            artifacts=(output,),
            lineage=self._lineage(
                invocation,
                parents=parents,
                child=output,
                relationship_type="transforms",
            ),
            diagnostics={
                "active_electron_count": active_electrons,
                "active_spatial_orbital_count": n_active,
                "frozen_core_orbital_count": len(core_indices),
                "orbital_basis": orbital_basis,
                "reference_method": result.reference_method,
            },
        )

    def _prepare_qmmm_embedding(
        self,
        invocation: CapabilityInvocation,
    ) -> CapabilityResult:
        inputs = self._inputs_by_type(invocation)

        preparation_reference = self._require_input(
            inputs,
            "electronic_qm_region_preparation",
        )
        molecule_reference = self._require_input(
            inputs,
            "electronic_molecule",
        )
        environment_reference = self._require_input(
            inputs,
            "molecular_environment",
        )
        system_reference = self._require_input(
            inputs,
            "molecular_simulation_system",
        )

        if len(inputs) != 4:
            raise ValueError(
                "QM/MM embedding preparation requires exactly "
                "QM-region, molecule, environment and system artifacts."
            )

        preparation = self._load_model(
            preparation_reference,
            ElectronicQMRegionPreparation,
        )
        molecule = self._load_model(
            molecule_reference,
            ElectronicMolecule,
        )
        environment = self._load_model(
            environment_reference,
            MolecularEnvironment,
        )
        system = self._load_model(
            system_reference,
            MolecularSimulationSystem,
        )

        if (
            preparation.environment_identifier
            != environment.environment_identifier
        ):
            raise ValueError(
                "QM-region preparation and molecular environment disagree."
            )

        if (
            molecule.molecule_identifier
            not in preparation.electronic_molecule_identifiers
        ):
            raise ValueError(
                "Electronic molecule is not a candidate produced by "
                "the supplied QM-region preparation."
            )

        if (
            molecule.source_geometry_identifier
            != environment.environment_identifier
        ):
            raise ValueError(
                "QM molecule geometry does not match the environment."
            )

        if (
            system.environment_identifier
            != environment.environment_identifier
        ):
            raise ValueError(
                "Simulation system and molecular environment disagree."
            )

        if (
            system.force_field_selection_identifier
            != environment.force_field_selection_identifier
        ):
            raise ValueError(
                "Simulation system and molecular environment use "
                "different force-field selections."
            )

        if system.particle_count != len(environment.atoms):
            raise ValueError(
                "Simulation-system particle count does not match "
                "the molecular environment."
            )

        if system.partial_charge_source != "openmm_nonbonded_force":
            raise ValueError(
                "QM/MM embedding requires OpenMM NonbondedForce charges."
            )

        selected = set(
            preparation.selected_particle_indices
        )

        if (
            not selected
            or any(
                index >= len(environment.atoms)
                for index in selected
            )
        ):
            raise ValueError(
                "QM-region particle membership is invalid for "
                "the molecular environment."
            )

        if len(molecule.atoms) != len(selected) + len(preparation.boundary_links):
            raise ValueError(
                "QM molecule atom count must match selected particles and links."
            )

        original_charges = {
            particle_index: float(system.particle_partial_charges_e[particle_index])
            for particle_index in range(len(environment.atoms))
            if particle_index not in selected
        }
        adjusted_charges = dict(original_charges)
        adjustment_boundaries: dict[int, set[int]] = {}
        adjustment_roles: dict[int, str] = {}

        if preparation.boundary_links:
            adjacency: dict[int, set[int]] = {
                index: set() for index in range(len(environment.atoms))
            }
            for bond in environment.bonds:
                adjacency[bond.atom_index_a].add(bond.atom_index_b)
                adjacency[bond.atom_index_b].add(bond.atom_index_a)

            boundary_mm_indices = [
                link.mm_particle_index for link in preparation.boundary_links
            ]
            if len(boundary_mm_indices) != len(set(boundary_mm_indices)):
                raise ValueError(
                    "Charge shifting does not support multiple cuts through one "
                    "MM boundary atom."
                )

            for link in preparation.boundary_links:
                boundary_index = link.mm_particle_index
                if boundary_index not in original_charges:
                    raise ValueError("Boundary MM atom is not in the MM environment.")
                recipients = tuple(sorted(
                    neighbor for neighbor in adjacency[boundary_index]
                    if neighbor not in selected and neighbor != boundary_index
                ))
                if not recipients:
                    raise ValueError(
                        "Boundary charge has no bonded MM-side redistribution sites."
                    )
                removed_charge = adjusted_charges[boundary_index]
                adjusted_charges[boundary_index] = 0.0
                adjustment_boundaries.setdefault(boundary_index, set()).add(
                    boundary_index
                )
                adjustment_roles[boundary_index] = "boundary_charge_removed"
                share = removed_charge / len(recipients)
                for recipient in recipients:
                    adjusted_charges[recipient] += share
                    adjustment_boundaries.setdefault(recipient, set()).add(
                        boundary_index
                    )
                    adjustment_roles[recipient] = "neighbor_charge_recipient"

        adjustments = tuple(
            QMMMBoundaryChargeAdjustment(
                source_particle_index=particle_index,
                original_charge_e=original_charges[particle_index],
                adjusted_charge_e=adjusted_charges[particle_index],
                charge_delta_e=(
                    adjusted_charges[particle_index]
                    - original_charges[particle_index]
                ),
                boundary_mm_particle_indices=tuple(sorted(boundaries)),
                role=adjustment_roles[particle_index],
            )
            for particle_index, boundaries in sorted(adjustment_boundaries.items())
        )

        sites: list[QMMMEmbeddingSite] = []

        for particle_index in range(len(environment.atoms)):
            if particle_index in selected:
                continue

            atom = environment.atoms[particle_index]
            position = environment.positions[particle_index]
            charge = adjusted_charges[particle_index]

            sites.append(
                QMMMEmbeddingSite(
                    site_index=len(sites),
                    source_particle_index=particle_index,
                    element_symbol=atom.element_symbol,
                    x_angstrom=10.0 * position.x,
                    y_angstrom=10.0 * position.y,
                    z_angstrom=10.0 * position.z,
                    charge_e=float(charge),
                    charge_source=(
                        "openmm_boundary_charge_shift"
                        if particle_index in adjustment_boundaries
                        else "openmm_nonbonded_force"
                    ),
                )
            )

        if not sites:
            raise ValueError(
                "QM/MM embedding requires at least one MM particle "
                "outside the QM region."
            )

        embedding_total_charge = sum(
            site.charge_e
            for site in sites
        )
        unmodified_embedding_total_charge = sum(original_charges.values())

        foundation = QMMMEmbeddingFoundation(
            schema_version=_SCHEMA_VERSION,
            embedding_identifier=_stable_identifier(
                "qmmm-embedding",
                preparation.preparation_identifier,
                molecule.molecule_identifier,
                environment.environment_identifier,
                system.system_identifier,
                *(
                    site.model_dump_json()
                    for site in sites
                ),
            ),
            molecule_identifier=molecule.molecule_identifier,
            qm_region_preparation_identifier=(
                preparation.preparation_identifier
            ),
            environment_identifier=environment.environment_identifier,
            simulation_system_identifier=system.system_identifier,
            qm_source_particle_count=len(selected),
            embedding_sites=tuple(sites),
            embedding_total_charge_e=embedding_total_charge,
            unmodified_embedding_total_charge_e=(
                unmodified_embedding_total_charge
            ),
            boundary_charge_adjustments=adjustments,
            electrostatic_embedding_ready=True,
            boundary_policy=(
                "charge_shift"
                if preparation.boundary_links
                else "no_covalent_boundary"
            ),
            charge_model="openmm_nonbonded_force",
        )

        parents = (
            preparation_reference,
            molecule_reference,
            environment_reference,
            system_reference,
        )

        output = self._write_model(
            invocation,
            model=foundation,
            artifact_type="qmmm_embedding_foundation",
            identifier_prefix="qmmm-embedding",
            parents=parents,
            metadata={
                "embedding_site_count": len(sites),
                "qm_source_particle_count": len(selected),
                "embedding_total_charge_e": (
                    foundation.embedding_total_charge_e
                ),
                "electrostatic_embedding_ready": True,
                "charge_model": foundation.charge_model,
                "boundary_policy": foundation.boundary_policy,
                "boundary_charge_adjustment_count": len(adjustments),
            },
        )

        return self._success(
            invocation,
            artifacts=(output,),
            lineage=self._lineage(
                invocation,
                parents=parents,
                child=output,
                relationship_type="embeds",
            ),
            diagnostics={
                "embedding_site_count": len(sites),
                "qm_source_particle_count": len(selected),
                "embedding_total_charge_e": (
                    foundation.embedding_total_charge_e
                ),
                "electrostatic_embedding_ready": True,
                "boundary_policy": foundation.boundary_policy,
                "boundary_charge_adjustment_count": len(adjustments),
            },
        )

    def build_hybrid_qmmm_potential(
        self,
        references: tuple[ArtifactReference, ...],
    ) -> HybridQMMMPotential:
        """Validate immutable topology/state and construct a reusable hybrid potential."""

        if self._private_state_store is None:
            raise ValueError("Hybrid QM/MM requires private OpenMM system state.")
        inputs = {reference.artifact_type: reference for reference in references}
        required = {
            "electronic_molecule",
            "electronic_structure_configuration",
            "electronic_qm_region_preparation",
            "qmmm_embedding_foundation",
            "electronic_qmmm_hartree_fock_result",
            "molecular_environment",
            "molecular_simulation_system",
        }
        if set(inputs) != required or len(references) != len(required):
            raise ValueError("Hybrid QM/MM requires seven exact scientific inputs.")
        molecule = self._load_model(inputs["electronic_molecule"], ElectronicMolecule)
        configuration = self._load_model(
            inputs["electronic_structure_configuration"],
            ElectronicStructureConfiguration,
        )
        preparation = self._load_model(
            inputs["electronic_qm_region_preparation"],
            ElectronicQMRegionPreparation,
        )
        embedding = self._load_model(
            inputs["qmmm_embedding_foundation"], QMMMEmbeddingFoundation
        )
        qmmm_result = self._load_model(
            inputs["electronic_qmmm_hartree_fock_result"],
            ElectronicQMMMHartreeFockResult,
        )
        environment = self._load_model(
            inputs["molecular_environment"], MolecularEnvironment
        )
        system_manifest = self._load_model(
            inputs["molecular_simulation_system"], MolecularSimulationSystem
        )
        if (
            configuration.molecule_identifier != molecule.molecule_identifier
            or embedding.molecule_identifier != molecule.molecule_identifier
            or qmmm_result.molecule_identifier != molecule.molecule_identifier
            or qmmm_result.configuration_identifier
            != configuration.configuration_identifier
            or qmmm_result.embedding_identifier != embedding.embedding_identifier
            or preparation.environment_identifier != environment.environment_identifier
            or embedding.environment_identifier != environment.environment_identifier
            or system_manifest.environment_identifier != environment.environment_identifier
            or embedding.simulation_system_identifier != system_manifest.system_identifier
        ):
            raise ValueError("Hybrid QM/MM inputs do not describe one calculation.")
        if system_manifest.periodic or system_manifest.settings.nonbonded_method != "no_cutoff":
            raise ValueError("Hybrid QM/MM v1 supports non-periodic no-cutoff systems only.")
        if system_manifest.particle_count != len(environment.atoms):
            raise ValueError("Hybrid QM/MM particle identities are inconsistent.")
        private_xml = self._private_state_store.read(
            inputs["molecular_simulation_system"]
        )
        if (
            not isinstance(private_xml, bytes)
            or hashlib.sha256(private_xml).hexdigest()
            != system_manifest.private_state_sha256
        ):
            raise ValueError("Private OpenMM system state failed integrity validation.")
        full_system, model_system, cross_system = self._qmmm_openmm_systems(
            private_xml=private_xml,
            preparation=preparation,
            embedding=embedding,
            particle_count=system_manifest.particle_count,
        )
        return HybridQMMMPotential(
            adapter=self,
            molecule=molecule,
            configuration=configuration,
            preparation=preparation,
            embedding=embedding,
            qmmm_result=qmmm_result,
            environment=environment,
            system_manifest=system_manifest,
            private_xml=private_xml,
            full_system=full_system,
            model_system=model_system,
            cross_system=cross_system,
        )

    def _evaluate_hybrid_qmmm_potential(
        self,
        *,
        potential: HybridQMMMPotential,
        coordinates_angstrom: object,
    ) -> HybridQMMMPotentialEvaluation:
        """Evaluate energy and full real-particle gradient at arbitrary coordinates."""

        numpy, gto, scf, _, _ = _pyscf_modules()
        from pyscf import qmmm

        coordinates = numpy.asarray(coordinates_angstrom, dtype=float)
        particle_count = potential.system_manifest.particle_count
        if coordinates.shape != (particle_count, 3) or not numpy.all(
            numpy.isfinite(coordinates)
        ):
            raise ValueError("Hybrid QM/MM coordinates have invalid dimensions or values.")
        selected = potential.preparation.selected_particle_indices
        expected_qm_atoms = len(selected) + len(potential.preparation.boundary_links)
        if len(potential.molecule.atoms) != expected_qm_atoms:
            raise ValueError("Hybrid QM/MM molecule no longer matches its boundary topology.")
        dynamic_atoms: list[ElectronicAtom] = []
        for local_index, particle_index in enumerate(selected):
            source = potential.molecule.atoms[local_index]
            environment_atom = potential.environment.atoms[particle_index]
            if (
                source.atomic_number != environment_atom.atomic_number
                or source.source_atom_index != environment_atom.source_atom_index
            ):
                raise ValueError("Hybrid QM/MM atom identity changed during optimization.")
            dynamic_atoms.append(source.model_copy(update={
                "x_angstrom": float(coordinates[particle_index, 0]),
                "y_angstrom": float(coordinates[particle_index, 1]),
                "z_angstrom": float(coordinates[particle_index, 2]),
            }))
        for offset, link in enumerate(potential.preparation.boundary_links):
            source = potential.molecule.atoms[len(selected) + offset]
            qm_position = coordinates[link.qm_particle_index]
            mm_position = coordinates[link.mm_particle_index]
            vector = mm_position - qm_position
            length = float(numpy.linalg.norm(vector))
            if length <= 1.0e-10:
                raise ValueError("A QM/MM boundary bond has zero length.")
            link_position = qm_position + link.link_distance_angstrom * vector / length
            dynamic_atoms.append(source.model_copy(update={
                "x_angstrom": float(link_position[0]),
                "y_angstrom": float(link_position[1]),
                "z_angstrom": float(link_position[2]),
            }))
        dynamic_molecule = potential.molecule.model_copy(
            update={"atoms": tuple(dynamic_atoms)}
        )
        dynamic_sites = tuple(
            site.model_copy(update={
                "x_angstrom": float(coordinates[site.source_particle_index, 0]),
                "y_angstrom": float(coordinates[site.source_particle_index, 1]),
                "z_angstrom": float(coordinates[site.source_particle_index, 2]),
            })
            for site in potential.embedding.embedding_sites
        )
        native_molecule = self._build_pyscf_molecule(
            dynamic_molecule, potential.configuration, gto
        )
        native_scf = self._build_scf(
            dynamic_molecule, potential.configuration, native_molecule, scf
        )
        native_scf = qmmm.mm_charge(
            native_scf,
            tuple((site.x_angstrom, site.y_angstrom, site.z_angstrom) for site in dynamic_sites),
            tuple(site.charge_e for site in dynamic_sites),
            unit="Angstrom",
        )
        embedded_energy = float(native_scf.kernel())
        if not bool(native_scf.converged):
            raise ValueError("The hybrid QM/MM embedded SCF calculation did not converge.")
        positions_nm = tuple(tuple(float(value) / 10.0 for value in row) for row in coordinates)
        full_energy_kj, full_gradient = self._openmm_energy_gradient(
            potential.full_system, positions_nm
        )
        model_energy_kj, model_gradient = self._openmm_energy_gradient(
            potential.model_system, positions_nm
        )
        cross_energy_kj, cross_gradient = self._openmm_energy_gradient(
            potential.cross_system, positions_nm
        )
        hartree_kj_per_mol = 2625.4996394799
        full_energy = full_energy_kj / hartree_kj_per_mol
        model_energy = model_energy_kj / hartree_kj_per_mol
        cross_energy = cross_energy_kj / hartree_kj_per_mol
        gradient_method = native_scf.nuc_grad_method()
        qm_gradient = numpy.asarray(gradient_method.kernel(), dtype=float)
        density = numpy.asarray(native_scf.make_rdm1())
        if density.ndim == 3:
            density = density.sum(axis=0)
        if not hasattr(gradient_method, "grad_hcore_mm") or not hasattr(
            gradient_method, "grad_nuc_mm"
        ):
            raise ValueError("Installed PySCF lacks analytical MM-site gradients.")
        mm_gradient = numpy.asarray(
            gradient_method.grad_hcore_mm(density) + gradient_method.grad_nuc_mm(),
            dtype=float,
        )
        if qm_gradient.shape != (expected_qm_atoms, 3) or mm_gradient.shape != (
            len(dynamic_sites), 3
        ):
            raise ValueError("Hybrid QM/MM analytical gradient dimensions are invalid.")
        real_qmmm_gradient = numpy.zeros((particle_count, 3))
        for local_index, particle_index in enumerate(selected):
            real_qmmm_gradient[particle_index] += qm_gradient[local_index]
        for site, value in zip(dynamic_sites, mm_gradient, strict=True):
            real_qmmm_gradient[site.source_particle_index] += value
        identity = numpy.eye(3)
        for offset, link in enumerate(potential.preparation.boundary_links):
            link_gradient = qm_gradient[len(selected) + offset]
            vector = coordinates[link.mm_particle_index] - coordinates[link.qm_particle_index]
            length = float(numpy.linalg.norm(vector))
            unit_vector = vector / length
            projector = (identity - numpy.outer(unit_vector, unit_vector)) / length
            derivative_mm = link.link_distance_angstrom * projector
            derivative_qm = identity - derivative_mm
            real_qmmm_gradient[link.qm_particle_index] += derivative_qm.T @ link_gradient
            real_qmmm_gradient[link.mm_particle_index] += derivative_mm.T @ link_gradient
        total_gradient = real_qmmm_gradient + full_gradient - model_gradient - cross_gradient
        total_energy = embedded_energy + full_energy - model_energy - cross_energy
        return HybridQMMMPotentialEvaluation(
            total_energy_hartree=total_energy,
            gradient_hartree_per_bohr=total_gradient,
            embedded_qm_energy_hartree=embedded_energy,
            full_mm_energy_hartree=full_energy,
            qm_model_mm_energy_hartree=model_energy,
            classical_cross_electrostatic_energy_hartree=cross_energy,
            molecule=dynamic_molecule,
        )

    def _run_qmmm_hybrid(
        self,
        invocation: CapabilityInvocation,
    ) -> CapabilityResult:
        """Execute the supported subtractive electrostatic-embedding model."""
        import numpy

        inputs = self._inputs_by_type(invocation)
        potential = self.build_hybrid_qmmm_potential(tuple(inputs.values()))
        coordinates_angstrom = numpy.asarray(
            [(10.0 * item.x, 10.0 * item.y, 10.0 * item.z) for item in potential.environment.positions],
            dtype=float,
        )
        evaluation = potential.evaluate(coordinates_angstrom)
        if not math.isclose(
            evaluation.embedded_qm_energy_hartree,
            potential.qmmm_result.embedded_total_energy_hartree,
            rel_tol=1e-9,
            abs_tol=1e-8,
        ):
            raise ValueError("Embedded QM energy is not reproducible from its inputs.")
        total_energy = evaluation.total_energy_hartree
        total_gradient = numpy.asarray(
            evaluation.gradient_hartree_per_bohr, dtype=float
        )
        result = ElectronicQMMMHybridResult(
            schema_version=_SCHEMA_VERSION,
            result_identifier=_stable_identifier(
                "electronic-qmmm-hybrid",
                potential.molecule.molecule_identifier,
                potential.configuration.configuration_identifier,
                potential.embedding.embedding_identifier,
                total_energy,
            ),
            molecule_identifier=potential.molecule.molecule_identifier,
            configuration_identifier=potential.configuration.configuration_identifier,
            embedding_identifier=potential.embedding.embedding_identifier,
            environment_identifier=potential.environment.environment_identifier,
            simulation_system_identifier=potential.system_manifest.system_identifier,
            qmmm_hartree_fock_result_identifier=potential.qmmm_result.result_identifier,
            embedded_qm_energy_hartree=evaluation.embedded_qm_energy_hartree,
            full_mm_energy_hartree=evaluation.full_mm_energy_hartree,
            subtracted_qm_model_mm_energy_hartree=evaluation.qm_model_mm_energy_hartree,
            subtracted_classical_qm_mm_electrostatic_energy_hartree=(
                evaluation.classical_cross_electrostatic_energy_hartree
            ),
            boundary_energy_correction_hartree=0.0,
            total_hybrid_energy_hartree=total_energy,
            gradient_hartree_per_bohr=_tensor(
                total_gradient,
                unit="hartree_per_bohr",
                index_convention="real_particle_by_cartesian",
            ),
            particle_count=potential.system_manifest.particle_count,
            link_atom_gradient_projected=bool(potential.preparation.boundary_links),
            included_physics=(
                "qm_internal_energy",
                "quantum_qm_mm_electrostatics",
                "mm_internal_energy",
                "mm_mm_nonbonded_energy",
                "qm_mm_vdw_and_bonded_energy",
            ),
            excluded_physics=(
                "polarizable_embedding",
                "periodic_long_range_electrostatics",
                "thermal_free_energy",
            ),
        )
        parents = tuple(inputs.values())
        output = self._write_model(
            invocation,
            model=result,
            artifact_type="electronic_qmmm_hybrid_result",
            identifier_prefix="electronic-qmmm-hybrid",
            parents=parents,
            metadata={
                "formulation": result.formulation,
                "total_hybrid_energy_hartree": total_energy,
                "no_double_counting_verified": True,
                "link_atom_gradient_projected": result.link_atom_gradient_projected,
            },
        )
        return self._success(
            invocation,
            artifacts=(output,),
            lineage=self._lineage(
                invocation,
                parents=parents,
                child=output,
                relationship_type="combines_without_double_counting",
            ),
            diagnostics={
                "total_hybrid_energy_hartree": total_energy,
                "full_mm_energy_hartree": evaluation.full_mm_energy_hartree,
                "subtracted_qm_model_mm_energy_hartree": evaluation.qm_model_mm_energy_hartree,
                "subtracted_classical_qm_mm_electrostatic_energy_hartree": (
                    evaluation.classical_cross_electrostatic_energy_hartree
                ),
                "no_double_counting_verified": True,
                "authoritative_potential_evaluation_count": potential.evaluation_count,
            },
        )

    @staticmethod
    def _openmm_energy_gradient(
        system: object,
        positions_nm: tuple[tuple[float, float, float], ...],
    ) -> tuple[float, object]:
        import numpy
        import openmm
        from openmm import unit

        integrator = openmm.VerletIntegrator(0.001 * unit.picoseconds)
        platform = openmm.Platform.getPlatformByName("Reference")
        context = openmm.Context(system, integrator, platform)
        try:
            context.setPositions(
                unit.Quantity(
                    [openmm.Vec3(*position) for position in positions_nm],
                    unit.nanometer,
                )
            )
            state = context.getState(getEnergy=True, getForces=True)
            energy = float(
                state.getPotentialEnergy().value_in_unit(unit.kilojoule_per_mole)
            )
            forces = numpy.asarray(
                state.getForces(asNumpy=True).value_in_unit(
                    unit.kilojoule_per_mole / unit.nanometer
                ),
                dtype=float,
            )
        finally:
            del context, integrator
        bohr_nm = 0.0529177210903
        hartree_kj_per_mol = 2625.4996394799
        gradient = -forces * bohr_nm / hartree_kj_per_mol
        return energy, gradient

    @staticmethod
    def _qmmm_openmm_systems(
        *,
        private_xml: bytes,
        preparation: ElectronicQMRegionPreparation,
        embedding: QMMMEmbeddingFoundation,
        particle_count: int,
    ) -> tuple[object, object, object]:
        """Build full, QM-model and isolated cross-Coulomb OpenMM systems."""

        import openmm
        from openmm import unit

        full = openmm.XmlSerializer.deserialize(private_xml.decode("utf-8"))
        if full.getNumParticles() != particle_count:
            raise ValueError("Private OpenMM system particle count is inconsistent.")
        qm_indices = set(preparation.selected_particle_indices)
        mm_indices = set(range(particle_count)) - qm_indices
        if {site.source_particle_index for site in embedding.embedding_sites} != mm_indices:
            raise ValueError("Embedding sites must cover every and only MM particle.")
        nonbonded = tuple(
            full.getForce(index)
            for index in range(full.getNumForces())
            if isinstance(full.getForce(index), openmm.NonbondedForce)
        )
        if len(nonbonded) != 1:
            raise ValueError("Hybrid QM/MM requires exactly one NonbondedForce.")
        force = nonbonded[0]
        if (
            force.getNonbondedMethod() != openmm.NonbondedForce.NoCutoff
            or force.getNumParticleParameterOffsets()
            or force.getNumExceptionParameterOffsets()
        ):
            raise ValueError("Unsupported OpenMM nonbonded method or parameter offsets.")
        original_charge: list[float] = []
        sigma_values: list[object] = []
        epsilon_values: list[object] = []
        for index in range(particle_count):
            charge, sigma, epsilon = force.getParticleParameters(index)
            original_charge.append(float(charge.value_in_unit(unit.elementary_charge)))
            sigma_values.append(sigma)
            epsilon_values.append(epsilon)
        adjusted_charge = list(original_charge)
        for site in embedding.embedding_sites:
            adjusted_charge[site.source_particle_index] = site.charge_e
        for index in range(particle_count):
            force.setParticleParameters(
                index,
                adjusted_charge[index] * unit.elementary_charge,
                sigma_values[index],
                epsilon_values[index],
            )
        adjusted_exceptions: dict[tuple[int, int], float] = {}
        for exception_index in range(force.getNumExceptions()):
            i, j, charge_product, sigma, epsilon = force.getExceptionParameters(
                exception_index
            )
            i, j = int(i), int(j)
            product = float(
                charge_product.value_in_unit(unit.elementary_charge ** 2)
            )
            if (i in qm_indices) != (j in qm_indices):
                denominator = original_charge[i] * original_charge[j]
                if abs(denominator) <= 1e-14:
                    if abs(product) > 1e-14:
                        raise ValueError("A cross-boundary exception cannot be rescaled.")
                    adjusted_product = 0.0
                else:
                    adjusted_product = (
                        product / denominator * adjusted_charge[i] * adjusted_charge[j]
                    )
                force.setExceptionParameters(
                    exception_index,
                    i,
                    j,
                    adjusted_product * unit.elementary_charge ** 2,
                    sigma,
                    epsilon,
                )
                adjusted_exceptions[tuple(sorted((i, j)))] = adjusted_product

        serialized_full = openmm.XmlSerializer.serialize(full)
        model = openmm.XmlSerializer.deserialize(serialized_full)
        supported = (
            openmm.NonbondedForce,
            openmm.HarmonicBondForce,
            openmm.HarmonicAngleForce,
            openmm.PeriodicTorsionForce,
            openmm.RBTorsionForce,
            openmm.CMMotionRemover,
        )
        for force_index in range(model.getNumForces()):
            model_force = model.getForce(force_index)
            if not isinstance(model_force, supported):
                raise ValueError(
                    f"Unsupported OpenMM force at QM/MM boundary: {type(model_force).__name__}."
                )
            if isinstance(model_force, openmm.NonbondedForce):
                for index in mm_indices:
                    _, sigma, _ = model_force.getParticleParameters(index)
                    model_force.setParticleParameters(
                        index,
                        0.0 * unit.elementary_charge,
                        sigma,
                        0.0 * unit.kilojoule_per_mole,
                    )
                for exception_index in range(model_force.getNumExceptions()):
                    i, j, charge_product, sigma, epsilon = model_force.getExceptionParameters(
                        exception_index
                    )
                    if int(i) not in qm_indices or int(j) not in qm_indices:
                        model_force.setExceptionParameters(
                            exception_index,
                            i,
                            j,
                            0.0 * unit.elementary_charge ** 2,
                            sigma,
                            0.0 * unit.kilojoule_per_mole,
                        )
            elif isinstance(model_force, openmm.HarmonicBondForce):
                for index in range(model_force.getNumBonds()):
                    i, j, length, k = model_force.getBondParameters(index)
                    if int(i) not in qm_indices or int(j) not in qm_indices:
                        model_force.setBondParameters(
                            index, i, j, length, 0.0 * k
                        )
            elif isinstance(model_force, openmm.HarmonicAngleForce):
                for index in range(model_force.getNumAngles()):
                    i, j, k_atom, angle, k_force = model_force.getAngleParameters(index)
                    if not {int(i), int(j), int(k_atom)}.issubset(qm_indices):
                        model_force.setAngleParameters(
                            index, i, j, k_atom, angle, 0.0 * k_force
                        )
            elif isinstance(model_force, openmm.PeriodicTorsionForce):
                for index in range(model_force.getNumTorsions()):
                    i, j, k_atom, l, periodicity, phase, k_force = (
                        model_force.getTorsionParameters(index)
                    )
                    if not {int(i), int(j), int(k_atom), int(l)}.issubset(qm_indices):
                        model_force.setTorsionParameters(
                            index, i, j, k_atom, l, periodicity, phase, 0.0 * k_force
                        )
            elif isinstance(model_force, openmm.RBTorsionForce):
                for index in range(model_force.getNumTorsions()):
                    values = model_force.getTorsionParameters(index)
                    atoms = tuple(int(value) for value in values[:4])
                    if not set(atoms).issubset(qm_indices):
                        model_force.setTorsionParameters(
                            index, *values[:4], *(0.0 * value for value in values[4:])
                        )

        cross = openmm.System()
        for _ in range(particle_count):
            cross.addParticle(0.0)
        cross_nonbonded = openmm.CustomNonbondedForce(
            "138.935456*q1*q2/r"
        )
        cross_nonbonded.addPerParticleParameter("q")
        cross_nonbonded.setNonbondedMethod(openmm.CustomNonbondedForce.NoCutoff)
        for charge in adjusted_charge:
            cross_nonbonded.addParticle((charge,))
        cross_nonbonded.addInteractionGroup(qm_indices, mm_indices)
        cross_exceptions = openmm.CustomBondForce("138.935456*charge_product/r")
        cross_exceptions.addPerBondParameter("charge_product")
        for exception_index in range(force.getNumExceptions()):
            i, j, charge_product, _, _ = force.getExceptionParameters(exception_index)
            i, j = int(i), int(j)
            cross_nonbonded.addExclusion(i, j)
            if (i in qm_indices) != (j in qm_indices):
                product = adjusted_exceptions.get(
                    tuple(sorted((i, j))),
                    float(charge_product.value_in_unit(unit.elementary_charge ** 2)),
                )
                if abs(product) > 0.0:
                    cross_exceptions.addBond(i, j, (product,))
        cross.addForce(cross_nonbonded)
        cross.addForce(cross_exceptions)
        return full, model, cross

    def _run_implicit_solvent_hartree_fock(
        self,
        invocation: CapabilityInvocation,
    ) -> CapabilityResult:
        inputs = self._inputs_by_type(invocation)
        molecule_reference = self._require_input(inputs, "electronic_molecule")
        configuration_reference = self._require_input(
            inputs, "electronic_structure_configuration"
        )
        if len(inputs) != 2:
            raise ValueError(
                "Implicit-solvent Hartree-Fock requires molecule and configuration."
            )
        molecule = self._load_model(molecule_reference, ElectronicMolecule)
        configuration = self._load_model(
            configuration_reference, ElectronicStructureConfiguration
        )
        if configuration.molecule_identifier != molecule.molecule_identifier:
            raise ValueError("Implicit-solvent inputs do not form one problem.")

        solvent_name = self._string_parameter(
            invocation, "solvent_name", maximum_length=80
        )
        solvent_model = self._string_parameter(
            invocation, "solvent_model", maximum_length=16
        ).upper()
        supported_models = {
            "IEF-PCM": "IEF-PCM",
            "C-PCM": "C-PCM",
            "SS(V)PE": "SS(V)PE",
            "COSMO": "COSMO",
        }
        if solvent_model not in supported_models:
            raise ValueError("Unsupported PySCF PCM solvent model.")
        dielectric = self._float_parameter(
            invocation,
            "dielectric_constant",
            minimum=1.0000001,
            maximum=1000.0,
        )

        _, native_molecule, vacuum_scf, vacuum_energy = self._run_native_scf(
            molecule, configuration
        )
        if not bool(vacuum_scf.converged):
            return self._failure(
                "vacuum_scf_not_converged",
                "The matched vacuum Hartree-Fock reference did not converge.",
                retryable=True,
            )

        _, _, scf, _, _ = _pyscf_modules()
        solvent_scf = self._build_scf(
            molecule, configuration, native_molecule, scf
        ).PCM()
        solvent_scf.with_solvent.method = supported_models[solvent_model]
        solvent_scf.with_solvent.eps = dielectric
        solvent_scf.with_solvent.equilibrium_solvation = True
        solvated_energy = float(solvent_scf.kernel())
        if not bool(solvent_scf.converged):
            return self._failure(
                "solvated_scf_not_converged",
                "The self-consistent PCM Hartree-Fock calculation did not converge.",
                retryable=True,
            )

        result = ElectronicImplicitSolventResult(
            schema_version=_SCHEMA_VERSION,
            result_identifier=_stable_identifier(
                "electronic-implicit-solvent",
                molecule.molecule_identifier,
                configuration.configuration_identifier,
                solvent_name,
                solvent_model,
                dielectric,
                vacuum_energy,
                solvated_energy,
            ),
            molecule_identifier=molecule.molecule_identifier,
            configuration_identifier=configuration.configuration_identifier,
            reference_method=configuration.reference_method,
            solvent_name=solvent_name,
            solvent_model=supported_models[solvent_model],
            dielectric_constant=dielectric,
            equilibrium_solvation=True,
            converged=True,
            vacuum_total_energy_hartree=vacuum_energy,
            solvated_total_energy_hartree=solvated_energy,
            electronic_solvation_contribution_hartree=(
                solvated_energy - vacuum_energy
            ),
            energy_semantics="solvated_electronic_energy",
            thermal_correction_included=False,
            gibbs_free_energy=False,
        )
        parents = (molecule_reference, configuration_reference)
        output = self._write_model(
            invocation,
            model=result,
            artifact_type="electronic_implicit_solvent_result",
            identifier_prefix="electronic-implicit-solvent",
            parents=parents,
            metadata={
                "solvent_name": solvent_name,
                "solvent_model": result.solvent_model,
                "dielectric_constant": dielectric,
                "electronic_solvation_contribution_hartree": (
                    result.electronic_solvation_contribution_hartree
                ),
                "energy_semantics": result.energy_semantics,
            },
        )
        return self._success(
            invocation,
            artifacts=(output,),
            lineage=self._lineage(
                invocation,
                parents=parents,
                child=output,
                relationship_type="solvates",
            ),
            diagnostics={
                "converged": True,
                "solvent_name": solvent_name,
                "solvent_model": result.solvent_model,
                "dielectric_constant": dielectric,
                "solvated_total_energy_hartree": solvated_energy,
                "electronic_solvation_contribution_hartree": (
                    result.electronic_solvation_contribution_hartree
                ),
            },
        )

    def _calculate_gradient(
        self,
        invocation: CapabilityInvocation,
    ) -> CapabilityResult:
        inputs = self._inputs_by_type(invocation)
        molecule_reference = self._require_input(inputs, "electronic_molecule")
        configuration_reference = self._require_input(
            inputs, "electronic_structure_configuration"
        )
        if len(inputs) != 2:
            raise ValueError("Gradient calculation requires two exact inputs.")
        molecule = self._load_model(molecule_reference, ElectronicMolecule)
        configuration = self._load_model(
            configuration_reference, ElectronicStructureConfiguration
        )
        if configuration.molecule_identifier != molecule.molecule_identifier:
            raise ValueError("Gradient inputs do not form one electronic problem.")
        threshold = self._float_parameter(
            invocation,
            "stationary_threshold_hartree_per_bohr",
            minimum=1e-8,
            maximum=1e-2,
        )
        numpy, _, native_scf, energy = self._run_native_scf(
            molecule, configuration
        )
        if not bool(native_scf.converged):
            return self._failure(
                "gradient_scf_not_converged",
                "The SCF reference for the analytical gradient did not converge.",
                retryable=True,
            )
        gradient = numpy.asarray(native_scf.nuc_grad_method().kernel(), dtype=float)
        rms = float(numpy.sqrt(numpy.mean(gradient * gradient)))
        maximum = float(numpy.max(numpy.abs(gradient)))
        result = ElectronicGradientResult(
            schema_version=_SCHEMA_VERSION,
            result_identifier=_stable_identifier(
                "electronic-gradient",
                molecule.molecule_identifier,
                configuration.configuration_identifier,
                energy,
                *_flatten(gradient),
            ),
            molecule_identifier=molecule.molecule_identifier,
            configuration_identifier=configuration.configuration_identifier,
            energy_hartree=energy,
            gradient_hartree_per_bohr=_tensor(
                gradient,
                unit="hartree_per_bohr",
                index_convention="atom_by_cartesian",
            ),
            rms_gradient_hartree_per_bohr=rms,
            maximum_gradient_hartree_per_bohr=maximum,
            stationary_threshold_hartree_per_bohr=threshold,
            stationary=maximum <= threshold,
        )
        parents = (molecule_reference, configuration_reference)
        output = self._write_model(
            invocation,
            model=result,
            artifact_type="electronic_gradient_result",
            identifier_prefix="electronic-gradient",
            parents=parents,
            metadata={
                "energy_hartree": energy,
                "rms_gradient_hartree_per_bohr": rms,
                "maximum_gradient_hartree_per_bohr": maximum,
                "stationary": result.stationary,
            },
        )
        return self._success(
            invocation,
            artifacts=(output,),
            lineage=self._lineage(
                invocation, parents=parents, child=output, relationship_type="derives"
            ),
            diagnostics={
                "energy_hartree": energy,
                "rms_gradient_hartree_per_bohr": rms,
                "maximum_gradient_hartree_per_bohr": maximum,
                "stationary": result.stationary,
            },
        )

    def _analyze_frequencies(
        self,
        invocation: CapabilityInvocation,
    ) -> CapabilityResult:
        inputs = self._inputs_by_type(invocation)
        molecule_reference = self._require_input(inputs, "electronic_molecule")
        configuration_reference = self._require_input(
            inputs, "electronic_structure_configuration"
        )
        gradient_reference = self._require_input(
            inputs, "electronic_gradient_result"
        )
        if len(inputs) != 3:
            raise ValueError("Frequency analysis requires three exact inputs.")
        molecule = self._load_model(molecule_reference, ElectronicMolecule)
        configuration = self._load_model(
            configuration_reference, ElectronicStructureConfiguration
        )
        gradient = self._load_model(gradient_reference, ElectronicGradientResult)
        if (
            configuration.molecule_identifier != molecule.molecule_identifier
            or gradient.molecule_identifier != molecule.molecule_identifier
            or gradient.configuration_identifier
            != configuration.configuration_identifier
        ):
            raise ValueError("Frequency inputs do not form one electronic problem.")
        threshold = self._float_parameter(
            invocation,
            "significant_imaginary_threshold_cm_inverse",
            minimum=1.0,
            maximum=1000.0,
        )
        raw_pair = self._parameter(
            invocation, "reaction_atom_pair", required=False
        )
        reaction_pair: tuple[int, int] | None = None
        if raw_pair is not None:
            if not isinstance(raw_pair, str):
                raise ValueError("Reaction atom pair must use 'atom-atom' notation.")
            parts = tuple(part.strip() for part in raw_pair.split("-"))
            if len(parts) != 2 or any(not part.isdigit() for part in parts):
                raise ValueError("Reaction atom pair must use 'atom-atom' notation.")
            reaction_pair = tuple(sorted((int(parts[0]), int(parts[1]))))
            if (
                reaction_pair[0] == reaction_pair[1]
                or reaction_pair[1] >= len(molecule.atoms)
            ):
                raise ValueError("Reaction atom pair is invalid for the molecule.")

        numpy, native_molecule, native_scf, _ = self._run_native_scf(
            molecule, configuration
        )
        if not bool(native_scf.converged):
            return self._failure(
                "hessian_scf_not_converged",
                "The SCF reference for the Hessian did not converge.",
                retryable=True,
            )
        hessian = numpy.asarray(native_scf.Hessian().kernel(), dtype=float)
        from pyscf.hessian import thermo

        harmonic = thermo.harmonic_analysis(
            native_molecule,
            hessian,
            exclude_trans=True,
            exclude_rot=True,
            imaginary_freq=False,
        )
        wavenumbers = numpy.asarray(harmonic["freq_wavenumber"], dtype=float)
        displacements = numpy.asarray(harmonic["norm_mode"], dtype=float)
        reduced_masses = numpy.asarray(harmonic["reduced_mass"], dtype=float)
        modes: list[ElectronicFrequencyMode] = []
        for mode_index, wavenumber in enumerate(wavenumbers.tolist()):
            mode_displacements = displacements[mode_index]
            participation: float | None = None
            if reaction_pair is not None:
                atom_a, atom_b = reaction_pair
                coords = native_molecule.atom_coords(unit="Angstrom")
                bond = numpy.asarray(coords[atom_b] - coords[atom_a], dtype=float)
                bond_norm = float(numpy.linalg.norm(bond))
                mode_norm = float(numpy.linalg.norm(mode_displacements))
                if bond_norm <= 1e-12 or mode_norm <= 1e-12:
                    raise ValueError("Reaction-mode participation is undefined.")
                stretch = float(numpy.dot(
                    mode_displacements[atom_b] - mode_displacements[atom_a],
                    bond / bond_norm,
                ))
                participation = min(1.0, abs(stretch) / mode_norm)
            modes.append(ElectronicFrequencyMode(
                mode_index=mode_index,
                wavenumber_cm_inverse=float(wavenumber),
                reduced_mass_amu=float(reduced_masses[mode_index]),
                imaginary=float(wavenumber) < 0.0,
                significant_imaginary=float(wavenumber) <= -threshold,
                normalized_displacements=_tensor(
                    mode_displacements,
                    unit="dimensionless",
                    index_convention="atom_by_cartesian",
                ),
                reaction_coordinate_participation=participation,
            ))
        significant_count = sum(mode.significant_imaginary for mode in modes)
        analysis = ElectronicFrequencyAnalysis(
            schema_version=_SCHEMA_VERSION,
            analysis_identifier=_stable_identifier(
                "electronic-frequency",
                molecule.molecule_identifier,
                configuration.configuration_identifier,
                threshold,
                *wavenumbers.tolist(),
            ),
            molecule_identifier=molecule.molecule_identifier,
            configuration_identifier=configuration.configuration_identifier,
            gradient_result_identifier=gradient.result_identifier,
            hessian_hartree_per_bohr2=_tensor(
                hessian,
                unit="hartree_per_bohr2",
                index_convention="atom_atom_cartesian_cartesian",
            ),
            modes=tuple(modes),
            significant_imaginary_threshold_cm_inverse=threshold,
            significant_imaginary_mode_count=significant_count,
            exactly_one_significant_imaginary_mode=significant_count == 1,
            thermal_corrections_calculated=False,
        )
        parents = (molecule_reference, configuration_reference, gradient_reference)
        output = self._write_model(
            invocation,
            model=analysis,
            artifact_type="electronic_frequency_analysis",
            identifier_prefix="electronic-frequency",
            parents=parents,
            metadata={
                "mode_count": len(modes),
                "significant_imaginary_mode_count": significant_count,
                "exactly_one_significant_imaginary_mode": significant_count == 1,
            },
        )
        return self._success(
            invocation,
            artifacts=(output,),
            lineage=self._lineage(
                invocation,
                parents=parents,
                child=output,
                relationship_type="characterizes",
            ),
            diagnostics={
                "mode_count": len(modes),
                "significant_imaginary_mode_count": significant_count,
                "exactly_one_significant_imaginary_mode": significant_count == 1,
            },
        )

    def _search_transition_state(
        self,
        invocation: CapabilityInvocation,
    ) -> CapabilityResult:
        inputs = self._inputs_by_type(invocation)
        molecule_reference = self._require_input(inputs, "electronic_molecule")
        configuration_reference = self._require_input(
            inputs, "electronic_structure_configuration"
        )
        if len(inputs) != 2:
            raise ValueError("Transition-state search requires two exact inputs.")
        molecule = self._load_model(molecule_reference, ElectronicMolecule)
        configuration = self._load_model(
            configuration_reference, ElectronicStructureConfiguration
        )
        if configuration.molecule_identifier != molecule.molecule_identifier:
            raise ValueError("Transition-state inputs do not form one problem.")
        raw_pair = self._string_parameter(
            invocation, "reaction_atom_pair", maximum_length=64
        )
        parts = tuple(part.strip() for part in raw_pair.split("-"))
        if len(parts) != 2 or any(not part.isdigit() for part in parts):
            raise ValueError("Reaction atom pair must use 'atom-atom' notation.")
        reaction_pair = tuple(sorted((int(parts[0]), int(parts[1]))))
        if reaction_pair[0] == reaction_pair[1] or reaction_pair[1] >= len(
            molecule.atoms
        ):
            raise ValueError("Reaction atom pair is invalid for the molecule.")
        maximum_steps = self._integer_parameter(
            invocation, "maximum_steps", minimum=1, maximum=1000
        )
        _, _, native_scf, _ = self._run_native_scf(molecule, configuration)
        if not bool(native_scf.converged):
            return self._failure(
                "ts_initial_scf_not_converged",
                "The initial transition-state SCF calculation did not converge.",
                retryable=True,
            )
        try:
            geometric_version = importlib.metadata.version("geometric")
        except importlib.metadata.PackageNotFoundError:
            return self._failure(
                "dependency_missing",
                "Transition-state search requires pinned geomeTRIC 1.1.1.",
            )
        if geometric_version != "1.1.1":
            return self._failure(
                "dependency_version_mismatch",
                "Installed geomeTRIC does not match the repository pin 1.1.1.",
            )
        optimizer = native_scf.nuc_grad_method().optimizer(solver="geomeTRIC")
        optimized_native = optimizer.kernel({
            "transition": True,
            "hessian": True,
            "maxiter": maximum_steps,
        })
        coords = optimized_native.atom_coords(unit="Angstrom")
        optimized_molecule = ElectronicMolecule(
            schema_version=_SCHEMA_VERSION,
            molecule_identifier=_stable_identifier(
                "electronic-ts-geometry",
                molecule.molecule_identifier,
                *coords.reshape(-1).tolist(),
            ),
            source_artifact_identifier=molecule.source_artifact_identifier,
            source_geometry_identifier=molecule.source_geometry_identifier,
            atoms=tuple(
                ElectronicAtom(
                    atom_index=atom.atom_index,
                    atomic_number=atom.atomic_number,
                    element_symbol=atom.element_symbol,
                    x_angstrom=float(coords[atom.atom_index, 0]),
                    y_angstrom=float(coords[atom.atom_index, 1]),
                    z_angstrom=float(coords[atom.atom_index, 2]),
                    source_atom_index=atom.source_atom_index,
                )
                for atom in molecule.atoms
            ),
            molecular_charge=molecule.molecular_charge,
            source_formal_charge=molecule.source_formal_charge,
            spin=molecule.spin,
            electron_count=molecule.electron_count,
            alpha_electron_count=molecule.alpha_electron_count,
            beta_electron_count=molecule.beta_electron_count,
        )
        numpy, _, final_scf, final_energy = self._run_native_scf(
            optimized_molecule, configuration
        )
        if not bool(final_scf.converged):
            return self._failure(
                "ts_final_scf_not_converged",
                "The optimized transition-state SCF calculation did not converge.",
                retryable=True,
            )
        final_gradient = numpy.asarray(
            final_scf.nuc_grad_method().kernel(), dtype=float
        )
        rms = float(numpy.sqrt(numpy.mean(final_gradient * final_gradient)))
        maximum = float(numpy.max(numpy.abs(final_gradient)))
        search = ElectronicTransitionStateSearch(
            schema_version=_SCHEMA_VERSION,
            search_identifier=_stable_identifier(
                "electronic-ts-search",
                molecule.molecule_identifier,
                optimized_molecule.molecule_identifier,
                final_energy,
            ),
            initial_molecule_identifier=molecule.molecule_identifier,
            configuration_identifier=configuration.configuration_identifier,
            optimizer="geometric",
            optimizer_version=geometric_version,
            converged=bool(getattr(optimizer, "converged", False)),
            maximum_steps=maximum_steps,
            intended_reaction_atom_pair=reaction_pair,
            optimized_molecule=optimized_molecule,
            final_energy_hartree=final_energy,
            final_rms_gradient_hartree_per_bohr=rms,
            final_maximum_gradient_hartree_per_bohr=maximum,
            transition_state_verified=False,
        )
        optimized_configuration = configuration.model_copy(update={
            "configuration_identifier": _stable_identifier(
                "electronic-ts-configuration",
                configuration.configuration_identifier,
                optimized_molecule.molecule_identifier,
            ),
            "molecule_identifier": optimized_molecule.molecule_identifier,
        })
        parents = (molecule_reference, configuration_reference)
        output = self._write_model(
            invocation,
            model=search,
            artifact_type="electronic_transition_state_search",
            identifier_prefix="electronic-ts-search",
            parents=parents,
            metadata={
                "optimizer": "geometric",
                "optimizer_version": geometric_version,
                "converged": search.converged,
                "transition_state_verified": False,
                "final_energy_hartree": final_energy,
            },
        )
        molecule_output = self._write_model(
            invocation,
            model=optimized_molecule,
            artifact_type="electronic_molecule",
            identifier_prefix="electronic-ts-molecule",
            parents=(output,),
            metadata={
                "transition_state_candidate": True,
                "transition_state_verified": False,
            },
        )
        configuration_output = self._write_model(
            invocation,
            model=optimized_configuration,
            artifact_type="electronic_structure_configuration",
            identifier_prefix="electronic-ts-configuration",
            parents=(configuration_reference, molecule_output),
            metadata={
                "reference_method": optimized_configuration.reference_method,
                "basis_set": optimized_configuration.basis_set,
            },
        )
        return self._success(
            invocation,
            artifacts=(output, molecule_output, configuration_output),
            lineage=(
                *self._lineage(
                    invocation, parents=parents, child=output, relationship_type="searches"
                ),
                *self._lineage(
                    invocation, parents=(output,), child=molecule_output,
                    relationship_type="optimizes_into",
                ),
                *self._lineage(
                    invocation,
                    parents=(configuration_reference, molecule_output),
                    child=configuration_output,
                    relationship_type="rebinds_to",
                ),
            ),
            diagnostics={
                "optimizer": "geometric",
                "converged": search.converged,
                "transition_state_verified": False,
                "final_rms_gradient_hartree_per_bohr": rms,
                "final_maximum_gradient_hartree_per_bohr": maximum,
            },
        )

    def _confirm_reaction_path(
        self,
        invocation: CapabilityInvocation,
    ) -> CapabilityResult:
        inputs = self._inputs_by_type(invocation)
        search_reference = self._require_input(
            inputs, "electronic_transition_state_search"
        )
        configuration_reference = self._require_input(
            inputs, "electronic_structure_configuration"
        )
        frequency_reference = self._require_input(
            inputs, "electronic_frequency_analysis"
        )
        if len(inputs) != 3:
            raise ValueError("Reaction-path confirmation requires three exact inputs.")
        search = self._load_model(search_reference, ElectronicTransitionStateSearch)
        configuration = self._load_model(
            configuration_reference, ElectronicStructureConfiguration
        )
        frequency = self._load_model(
            frequency_reference, ElectronicFrequencyAnalysis
        )
        transition_state = search.optimized_molecule
        if (
            not search.converged
            or not frequency.exactly_one_significant_imaginary_mode
            or frequency.molecule_identifier != transition_state.molecule_identifier
            or frequency.configuration_identifier
            != configuration.configuration_identifier
            or configuration.molecule_identifier != transition_state.molecule_identifier
        ):
            raise ValueError(
                "Reaction-path inputs do not describe one converged, characterized saddle."
            )
        step_size = self._float_parameter(
            invocation, "step_size_bohr", minimum=1.0e-4, maximum=0.5
        )
        step_count = self._integer_parameter(
            invocation, "step_count_per_direction", minimum=2, maximum=100
        )
        imaginary_mode = next(
            mode for mode in frequency.modes if mode.significant_imaginary
        )
        numpy, native_molecule, _, _ = self._run_native_scf(
            transition_state, configuration
        )
        masses = numpy.asarray(native_molecule.atom_mass_list(), dtype=float)
        mode = numpy.asarray(
            imaginary_mode.normalized_displacements.values, dtype=float
        ).reshape(imaginary_mode.normalized_displacements.shape)
        mass_weighted_mode = mode / numpy.sqrt(masses)[:, None]
        mode_norm = float(numpy.linalg.norm(mass_weighted_mode))
        if mode_norm <= 1.0e-12:
            raise ValueError("The significant imaginary mode has zero displacement.")
        mass_weighted_mode /= mode_norm
        transition_coords = numpy.asarray(
            native_molecule.atom_coords(unit="Bohr"), dtype=float
        )

        def molecule_at(coords_bohr: object, direction: str, index: int) -> ElectronicMolecule:
            coords_angstrom = numpy.asarray(coords_bohr, dtype=float) * 0.529177210903
            return ElectronicMolecule(
                schema_version=_SCHEMA_VERSION,
                molecule_identifier=_stable_identifier(
                    "electronic-reaction-path-geometry",
                    search.search_identifier,
                    direction,
                    index,
                    *coords_angstrom.reshape(-1).tolist(),
                ),
                source_artifact_identifier=transition_state.source_artifact_identifier,
                source_geometry_identifier=transition_state.source_geometry_identifier,
                atoms=tuple(
                    ElectronicAtom(
                        atom_index=atom.atom_index,
                        atomic_number=atom.atomic_number,
                        element_symbol=atom.element_symbol,
                        x_angstrom=float(coords_angstrom[atom.atom_index, 0]),
                        y_angstrom=float(coords_angstrom[atom.atom_index, 1]),
                        z_angstrom=float(coords_angstrom[atom.atom_index, 2]),
                        source_atom_index=atom.source_atom_index,
                    )
                    for atom in transition_state.atoms
                ),
                molecular_charge=transition_state.molecular_charge,
                source_formal_charge=transition_state.source_formal_charge,
                spin=transition_state.spin,
                electron_count=transition_state.electron_count,
                alpha_electron_count=transition_state.alpha_electron_count,
                beta_electron_count=transition_state.beta_electron_count,
            )

        points: list[ElectronicReactionPathPoint] = []
        endpoint_coords: dict[str, object] = {}
        endpoint_energies: dict[str, float] = {}
        for direction, sign in (("forward", 1.0), ("reverse", -1.0)):
            coords = transition_coords + sign * step_size * mass_weighted_mode
            for index in range(step_count):
                path_molecule = molecule_at(coords, direction, index)
                _, _, native_scf, energy = self._run_native_scf(
                    path_molecule, configuration
                )
                if not bool(native_scf.converged):
                    return self._failure(
                        "reaction_path_scf_not_converged",
                        "An SCF point on the reaction path did not converge.",
                        retryable=True,
                    )
                gradient = numpy.asarray(
                    native_scf.nuc_grad_method().kernel(), dtype=float
                )
                rms = float(numpy.sqrt(numpy.mean(gradient * gradient)))
                points.append(ElectronicReactionPathPoint(
                    direction=direction,
                    step_index=index,
                    molecule=path_molecule,
                    energy_hartree=energy,
                    rms_gradient_hartree_per_bohr=rms,
                ))
                if index + 1 < step_count:
                    mass_weighted_gradient = gradient / masses[:, None]
                    gradient_norm = float(numpy.linalg.norm(mass_weighted_gradient))
                    if gradient_norm <= 1.0e-14:
                        raise ValueError("Reaction path reached an undefined zero-gradient step.")
                    coords = coords - step_size * mass_weighted_gradient / gradient_norm
            endpoint_coords[direction] = coords
            endpoint_energies[direction] = energy

        endpoint_rmsd = float(numpy.sqrt(numpy.mean(
            (numpy.asarray(endpoint_coords["forward"])
             - numpy.asarray(endpoint_coords["reverse"])) ** 2
        )))
        forward_decreased = endpoint_energies["forward"] < search.final_energy_hartree
        reverse_decreased = endpoint_energies["reverse"] < search.final_energy_hartree
        distinct_endpoints = endpoint_rmsd > max(step_size, 1.0e-3)
        result = ElectronicReactionPathResult(
            schema_version=_SCHEMA_VERSION,
            path_identifier=_stable_identifier(
                "electronic-reaction-path",
                search.search_identifier,
                frequency.analysis_identifier,
                step_size,
                step_count,
                *endpoint_energies.values(),
            ),
            transition_state_search_identifier=search.search_identifier,
            frequency_analysis_identifier=frequency.analysis_identifier,
            method="mass_weighted_steepest_descent",
            step_size_bohr=step_size,
            points=tuple(points),
            forward_energy_decreased=forward_decreased,
            reverse_energy_decreased=reverse_decreased,
            distinct_endpoints=distinct_endpoints,
            path_confirmation_passed=(
                forward_decreased and reverse_decreased and distinct_endpoints
            ),
        )
        parents = (search_reference, configuration_reference, frequency_reference)
        output = self._write_model(
            invocation,
            model=result,
            artifact_type="electronic_reaction_path_result",
            identifier_prefix="electronic-reaction-path",
            parents=parents,
            metadata={
                "step_count_per_direction": step_count,
                "forward_energy_decreased": forward_decreased,
                "reverse_energy_decreased": reverse_decreased,
                "distinct_endpoints": distinct_endpoints,
                "path_confirmation_passed": result.path_confirmation_passed,
            },
        )
        return self._success(
            invocation,
            artifacts=(output,),
            lineage=self._lineage(
                invocation, parents=parents, child=output, relationship_type="confirms"
            ),
            diagnostics={
                "step_count_per_direction": step_count,
                "endpoint_rmsd_bohr": endpoint_rmsd,
                "path_confirmation_passed": result.path_confirmation_passed,
            },
        )

    def _run_qmmm_hartree_fock(
        self,
        invocation: CapabilityInvocation,
    ) -> CapabilityResult:
        inputs = self._inputs_by_type(invocation)

        molecule_reference = self._require_input(
            inputs,
            "electronic_molecule",
        )
        configuration_reference = self._require_input(
            inputs,
            "electronic_structure_configuration",
        )
        embedding_reference = self._require_input(
            inputs,
            "qmmm_embedding_foundation",
        )

        if len(inputs) != 3:
            raise ValueError(
                "QM/MM Hartree-Fock requires exactly molecule, "
                "configuration and embedding artifacts."
            )

        molecule = self._load_model(
            molecule_reference,
            ElectronicMolecule,
        )
        configuration = self._load_model(
            configuration_reference,
            ElectronicStructureConfiguration,
        )
        embedding = self._load_model(
            embedding_reference,
            QMMMEmbeddingFoundation,
        )

        if (
            configuration.molecule_identifier
            != molecule.molecule_identifier
        ):
            raise ValueError(
                "Electronic configuration does not match "
                "the QM/MM molecule."
            )

        if (
            embedding.molecule_identifier
            != molecule.molecule_identifier
        ):
            raise ValueError(
                "QM/MM embedding does not match "
                "the electronic molecule."
            )

        if not embedding.electrostatic_embedding_ready:
            raise ValueError(
                "QM/MM embedding is not ready for "
                "electrostatic execution."
            )

        if embedding.boundary_policy not in {
            "no_covalent_boundary",
            "charge_shift",
        }:
            raise ValueError(
                "Unsupported QM/MM boundary policy."
            )

        numpy, gto, scf, _, _ = _pyscf_modules()
        from pyscf import qmmm

        native_molecule = self._build_pyscf_molecule(
            molecule,
            configuration,
            gto,
        )
        native_scf = self._build_scf(
            molecule,
            configuration,
            native_molecule,
            scf,
        )

        mm_coordinates = tuple(
            (
                site.x_angstrom,
                site.y_angstrom,
                site.z_angstrom,
            )
            for site in embedding.embedding_sites
        )
        mm_charges = tuple(
            site.charge_e
            for site in embedding.embedding_sites
        )

        native_scf = qmmm.mm_charge(
            native_scf,
            mm_coordinates,
            mm_charges,
            unit="Angstrom",
        )

        total_energy = float(native_scf.kernel())

        if not bool(native_scf.converged):
            return self._failure(
                "qmmm_scf_not_converged",
                "The electrostatically embedded Hartree-Fock "
                "calculation did not converge.",
                retryable=True,
            )

        (
            coeff_alpha,
            coeff_beta,
            energy_alpha,
            energy_beta,
            occupation_alpha,
            occupation_beta,
        ) = self._spin_data(
            numpy,
            method=configuration.reference_method,
            mo_coeff=native_scf.mo_coeff,
            mo_energy=native_scf.mo_energy,
            mo_occ=native_scf.mo_occ,
        )

        density_alpha, density_beta = self._density_matrices(
            numpy,
            coeff_alpha=coeff_alpha,
            coeff_beta=coeff_beta,
            occupation_alpha=occupation_alpha,
            occupation_beta=occupation_beta,
        )

        overlap = numpy.asarray(native_scf.get_ovlp())
        embedded_hcore = numpy.asarray(native_scf.get_hcore())

        qm_nuclear = float(
            native_molecule.energy_nuc()
        )
        embedded_nuclear = float(
            native_scf.energy_nuc()
        )
        qm_mm_nuclear = (
            embedded_nuclear
            - qm_nuclear
        )
        embedded_electronic = (
            total_energy
            - embedded_nuclear
        )

        result = ElectronicQMMMHartreeFockResult(
            schema_version=_SCHEMA_VERSION,
            result_identifier=_stable_identifier(
                "qmmm-hartree-fock",
                molecule.molecule_identifier,
                configuration.configuration_identifier,
                embedding.embedding_identifier,
                total_energy,
                bool(native_scf.converged),
            ),
            molecule_identifier=molecule.molecule_identifier,
            configuration_identifier=(
                configuration.configuration_identifier
            ),
            embedding_identifier=embedding.embedding_identifier,
            reference_method=configuration.reference_method,
            converged=True,
            iterations=int(
                getattr(native_scf, "cycles", 0)
                or 0
            ),
            electron_count=molecule.electron_count,
            alpha_electron_count=molecule.alpha_electron_count,
            beta_electron_count=molecule.beta_electron_count,
            atomic_orbital_count=int(
                coeff_alpha.shape[0]
            ),
            spatial_orbital_count=int(
                coeff_alpha.shape[1]
            ),
            embedding_site_count=len(
                embedding.embedding_sites
            ),
            embedding_total_charge_e=(
                embedding.embedding_total_charge_e
            ),
            qm_nuclear_repulsion_energy_hartree=(
                qm_nuclear
            ),
            qm_mm_nuclear_interaction_energy_hartree=(
                qm_mm_nuclear
            ),
            embedded_electronic_energy_hartree=(
                embedded_electronic
            ),
            embedded_total_energy_hartree=(
                total_energy
            ),
            orbital_energies_alpha_hartree=tuple(
                float(value)
                for value in energy_alpha.tolist()
            ),
            orbital_energies_beta_hartree=tuple(
                float(value)
                for value in energy_beta.tolist()
            ),
            orbital_occupations_alpha=tuple(
                float(value)
                for value in occupation_alpha.tolist()
            ),
            orbital_occupations_beta=tuple(
                float(value)
                for value in occupation_beta.tolist()
            ),
            mo_coefficients_alpha=_tensor(
                coeff_alpha,
                unit="dimensionless",
                index_convention="ao_by_spatial_orbital",
            ),
            mo_coefficients_beta=_tensor(
                coeff_beta,
                unit="dimensionless",
                index_convention="ao_by_spatial_orbital",
            ),
            overlap_matrix_ao=_tensor(
                overlap,
                unit="dimensionless",
                index_convention="ao_by_ao",
            ),
            embedded_core_hamiltonian_ao=_tensor(
                embedded_hcore,
                unit="hartree",
                index_convention="ao_by_ao",
            ),
            density_matrix_alpha_ao=_tensor(
                density_alpha,
                unit="electron",
                index_convention="ao_by_ao",
            ),
            density_matrix_beta_ao=_tensor(
                density_beta,
                unit="electron",
                index_convention="ao_by_ao",
            ),
        )

        parents = (
            molecule_reference,
            configuration_reference,
            embedding_reference,
        )

        output = self._write_model(
            invocation,
            model=result,
            artifact_type=(
                "electronic_qmmm_hartree_fock_result"
            ),
            identifier_prefix="qmmm-hartree-fock",
            parents=parents,
            metadata={
                "reference_method": (
                    result.reference_method
                ),
                "embedding_site_count": (
                    result.embedding_site_count
                ),
                "embedding_total_charge_e": (
                    result.embedding_total_charge_e
                ),
                "embedded_total_energy_hartree": (
                    result.embedded_total_energy_hartree
                ),
                "energy_model": result.energy_model,
            },
        )

        return self._success(
            invocation,
            artifacts=(output,),
            lineage=self._lineage(
                invocation,
                parents=parents,
                child=output,
                relationship_type="calculates",
            ),
            diagnostics={
                "converged": True,
                "iterations": result.iterations,
                "reference_method": (
                    result.reference_method
                ),
                "embedding_site_count": (
                    result.embedding_site_count
                ),
                "embedded_total_energy_hartree": (
                    result.embedded_total_energy_hartree
                ),
            },
        )
