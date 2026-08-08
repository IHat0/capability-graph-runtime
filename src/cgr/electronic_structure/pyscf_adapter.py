"""PySCF-backed Phase 4 electronic-structure adapter."""

from __future__ import annotations

import hashlib
import importlib.metadata
import math
from collections.abc import Mapping
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

from .contracts import (
    ElectronicActiveSpace,
    ElectronicActiveSpaceSelection,
    ElectronicAtom,
    ElectronicHartreeFockResult,
    ElectronicMolecule,
    ElectronicQMMMHartreeFockResult,
    ElectronicOrbital,
    ElectronicOrbitalSelectionScore,
    ElectronicOrbitalSet,
    ElectronicReferenceCalculation,
    ElectronicStructureConfiguration,
    ElectronicTensor,
    QMMMEmbeddingFoundation,
    QMMMEmbeddingSite,
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


@runtime_checkable
class ElectronicArtifactPayloadStore(Protocol):
    """Read and persist exact bytes behind electronic artifact references."""

    def read(self, reference: ArtifactReference) -> bytes:
        """Return exact bytes addressed by a complete reference."""
        ...

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
                "Version 1 selects canonical RHF orbitals.",
                "Frontier selection uses occupied and virtual ordering.",
                "Target-AO projection ranks orbitals by population on "
                "resolved target atoms.",
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
                "Version 1 constructs spin-restricted active-space integrals from converged RHF orbitals.",
                "Active spaces are bounded to 32 spatial orbitals.",
                "The output uses chemist-order spatial-orbital integrals and an explicit constant energy.",
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
                "Covalent QM/MM boundaries fail closed until an explicit "
                "charge-shift boundary treatment is available.",
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
                "Covalent QM/MM boundaries remain unavailable until an "
                "explicit boundary-charge treatment is implemented.",
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


class PySCFElectronicStructureAdapter:
    """Execute Phase 4 capabilities through a pinned PySCF runtime."""

    def __init__(
        self,
        payload_store: ElectronicArtifactPayloadStore,
        *,
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
            QMMM_HARTREE_FOCK,
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
            if capability_name == QMMM_EMBEDDING_PREPARE:
                return self._prepare_qmmm_embedding(invocation)
            if capability_name == QMMM_HARTREE_FOCK:
                return self._run_qmmm_hartree_fock(invocation)
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
        except Exception:
            return self._failure(
                "electronic_execution_failed",
                "The electronic-structure capability failed without valid evidence.",
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

        if (
            configuration.reference_method != "rhf"
            or result.reference_method != "rhf"
            or molecule.spin != 0
            or not result.converged
        ):
            raise ValueError(
                "Automatic active-space selection requires "
                "a converged RHF singlet."
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
        }:
            raise ValueError(
                "Unsupported automatic active-space selection method."
            )

        if active_electrons % 2:
            raise ValueError(
                "RHF automatic selection requires an even "
                "active-electron count."
            )

        occupied_needed = active_electrons // 2
        virtual_needed = active_orbitals - occupied_needed

        if virtual_needed < 0:
            raise ValueError(
                "Active-space orbital count is too small "
                "for the requested electrons."
            )

        occupations = tuple(
            float(alpha + beta)
            for alpha, beta in zip(
                result.orbital_occupations_alpha,
                result.orbital_occupations_beta,
                strict=True,
            )
        )

        occupied = tuple(
            index
            for index, occupation in enumerate(occupations)
            if math.isclose(
                occupation,
                2.0,
                rel_tol=0,
                abs_tol=1e-7,
            )
        )

        virtual = tuple(
            index
            for index, occupation in enumerate(occupations)
            if math.isclose(
                occupation,
                0.0,
                rel_tol=0,
                abs_tol=1e-7,
            )
        )

        if len(occupied) + len(virtual) != len(occupations):
            raise ValueError(
                "Automatic selection requires integer RHF occupations."
            )

        if (
            occupied_needed > len(occupied)
            or virtual_needed > len(virtual)
        ):
            raise ValueError(
                "Requested active-space composition is unavailable."
            )

        target_atom_indices: tuple[int, ...] = ()
        projection_scores = {
            index: 0.0
            for index in range(result.spatial_orbital_count)
        }

        raw_targets = self._parameter(
            invocation,
            "target_atom_indices",
            required=False,
        )

        if method == "frontier":
            if raw_targets is not None:
                raise ValueError(
                    "Frontier selection cannot receive target atoms."
                )

            chosen_occupied = (
                occupied[-occupied_needed:]
                if occupied_needed
                else ()
            )
            chosen_virtual = (
                virtual[:virtual_needed]
                if virtual_needed
                else ()
            )

        else:
            if not isinstance(raw_targets, str):
                raise ValueError(
                    "Target-AO selection requires target atom indices."
                )

            parts = tuple(
                part.strip()
                for part in raw_targets.split(",")
            )

            if (
                not parts
                or any(
                    not part or not part.isdigit()
                    for part in parts
                )
            ):
                raise ValueError(
                    "Target atom indices must be comma-separated integers."
                )

            target_atom_indices = tuple(
                sorted(int(part) for part in parts)
            )

            if len(target_atom_indices) != len(
                set(target_atom_indices)
            ):
                raise ValueError(
                    "Target atom indices must be unique."
                )

            if any(
                index >= len(molecule.atoms)
                for index in target_atom_indices
            ):
                raise ValueError(
                    "A target atom index is outside the molecule."
                )

            numpy, gto, _, _, _ = _pyscf_modules()

            native_molecule = self._build_pyscf_molecule(
                molecule,
                configuration,
                gto,
            )

            ao_slices = numpy.asarray(
                native_molecule.aoslice_by_atom()
            )

            target_ao_indices: list[int] = []

            for atom_index in target_atom_indices:
                start_ao = int(ao_slices[atom_index, 2])
                stop_ao = int(ao_slices[atom_index, 3])
                target_ao_indices.extend(
                    range(start_ao, stop_ao)
                )

            if not target_ao_indices:
                raise ValueError(
                    "Target atoms contain no basis functions."
                )

            n_ao = result.atomic_orbital_count
            n_mo = result.spatial_orbital_count

            coefficients = numpy.asarray(
                result.mo_coefficients_alpha.values,
                dtype=float,
            ).reshape((n_ao, n_mo))

            overlap = numpy.asarray(
                result.overlap_matrix_ao.values,
                dtype=float,
            ).reshape((n_ao, n_ao))

            overlap_coefficients = overlap @ coefficients

            for orbital_index in range(n_mo):
                population = float(
                    sum(
                        coefficients[
                            ao_index,
                            orbital_index,
                        ]
                        * overlap_coefficients[
                            ao_index,
                            orbital_index,
                        ]
                        for ao_index in target_ao_indices
                    )
                )

                projection_scores[orbital_index] = round(
                    abs(population),
                    12,
                )

            ranked_occupied = tuple(
                sorted(
                    occupied,
                    key=lambda index: (
                        -projection_scores[index],
                        -index,
                    ),
                )
            )

            ranked_virtual = tuple(
                sorted(
                    virtual,
                    key=lambda index: (
                        -projection_scores[index],
                        index,
                    ),
                )
            )

            chosen_occupied = ranked_occupied[
                :occupied_needed
            ]
            chosen_virtual = ranked_virtual[
                :virtual_needed
            ]

        active_indices = tuple(
            sorted((*chosen_occupied, *chosen_virtual))
        )

        if len(active_indices) != active_orbitals:
            raise ValueError(
                "Automatic selection produced the wrong orbital count."
            )

        resolved_electrons = sum(
            occupations[index]
            for index in active_indices
        )

        if not math.isclose(
            resolved_electrons,
            active_electrons,
            rel_tol=0,
            abs_tol=1e-7,
        ):
            raise ValueError(
                "Automatic selection produced the wrong electron count."
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
                *target_atom_indices,
                *active_indices,
            ),
            molecule_identifier=molecule.molecule_identifier,
            hartree_fock_result_identifier=(
                result.result_identifier
            ),
            selection_method=method,
            active_electron_count=active_electrons,
            active_spatial_orbital_count=active_orbitals,
            active_orbital_indices=active_indices,
            target_atom_indices=target_atom_indices,
            orbital_scores=tuple(
                ElectronicOrbitalSelectionScore(
                    orbital_index=index,
                    occupation=occupations[index],
                    target_projection_score=(
                        projection_scores[index]
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
        if (
            configuration.reference_method != "rhf"
            or result.reference_method != "rhf"
            or molecule.spin != 0
            or not result.converged
        ):
            raise ValueError("Version 1 active spaces require a converged RHF singlet.")
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

        occupations = tuple(
            alpha + beta
            for alpha, beta in zip(
                result.orbital_occupations_alpha,
                result.orbital_occupations_beta,
                strict=True,
            )
        )
        resolved_active = sum(occupations[index] for index in active_indices)
        if not math.isclose(resolved_active, active_electrons, abs_tol=1e-7):
            raise ValueError(
                "Active orbital occupations do not match active electrons."
            )
        core_indices = tuple(
            index
            for index, occupation in enumerate(occupations)
            if index not in active_indices
            and math.isclose(occupation, 2.0, abs_tol=1e-7)
        )
        inactive_electrons = molecule.electron_count - active_electrons
        if inactive_electrons < 0 or inactive_electrons != 2 * len(core_indices):
            raise ValueError(
                "Inactive electrons do not resolve to closed-shell core orbitals."
            )

        numpy, gto, scf, _, ao2mo = _pyscf_modules()
        native_molecule = self._build_pyscf_molecule(molecule, configuration, gto)
        native_scf = scf.RHF(native_molecule)
        hcore_ao = numpy.asarray(native_scf.get_hcore())
        n_ao = result.atomic_orbital_count
        n_mo = result.spatial_orbital_count
        coefficients = numpy.asarray(
            result.mo_coefficients_alpha.values,
            dtype=float,
        ).reshape((n_ao, n_mo))
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

        alpha_active = int(
            round(
                sum(result.orbital_occupations_alpha[index] for index in active_indices)
            )
        )
        beta_active = int(
            round(
                sum(result.orbital_occupations_beta[index] for index in active_indices)
            )
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

        if preparation.boundary_links:
            raise ValueError(
                "Covalent QM/MM boundaries require an explicit "
                "boundary-charge treatment before electrostatic embedding."
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

        if len(molecule.atoms) != len(selected):
            raise ValueError(
                "Uncapped QM molecule atom count must match "
                "the selected source-particle count."
            )

        sites: list[QMMMEmbeddingSite] = []

        for particle_index in range(len(environment.atoms)):
            if particle_index in selected:
                continue

            atom = environment.atoms[particle_index]
            position = environment.positions[particle_index]
            charge = system.particle_partial_charges_e[
                particle_index
            ]

            sites.append(
                QMMMEmbeddingSite(
                    site_index=len(sites),
                    source_particle_index=particle_index,
                    element_symbol=atom.element_symbol,
                    x_angstrom=10.0 * position.x,
                    y_angstrom=10.0 * position.y,
                    z_angstrom=10.0 * position.z,
                    charge_e=float(charge),
                    charge_source="openmm_nonbonded_force",
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
            electrostatic_embedding_ready=True,
            boundary_policy="no_covalent_boundary",
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

        if embedding.boundary_policy != "no_covalent_boundary":
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
