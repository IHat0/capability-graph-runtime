"""OpenMM-backed Phase 5.3 adapter with private native engine state."""

from __future__ import annotations

import hashlib
import io
import importlib.metadata
import importlib.resources
import math
import re
import statistics
from collections.abc import Mapping
from contextlib import ExitStack
from pathlib import PurePosixPath
from typing import Protocol, runtime_checkable

from pydantic import ValidationError

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
)

from .cheminformatics import MolecularConformerSet
from .rdkit_adapter import MolecularArtifactPayloadStore
from .preparation import (
    MolecularMetalElectronicState,
    MolecularProteinProtonationPreparation,
    MolecularProteinResidueState,
)
from .parameterization import MolecularLigandParameterization
from .simulation import (
    MolecularDynamicsRun,
    MolecularDynamicsSettings,
    MolecularEnsembleSummary,
    MolecularEnvironment,
    MolecularForceFieldSelection,
    MolecularMinimizationResult,
    MolecularMinimizationSettings,
    MolecularPeriodicBox,
    MolecularSimulationAtom,
    MolecularSimulationBond,
    MolecularSimulationSystem,
    MolecularSnapshot,
    MolecularSystemConstructionSettings,
    MolecularTrajectory,
    MolecularVector3,
    MolecularVelocity,
)

_SCHEMA_VERSION = CapabilityVersion(major=1, minor=0, patch=0)
_ADAPTER_VERSION = CapabilityVersion(major=1, minor=0, patch=0)
_EXPECTED_OPENMM_VERSION = "8.5.2"
_MAXIMUM_PAYLOAD_BYTES = 256 * 1024 * 1024
_MAXIMUM_PRIVATE_STATE_BYTES = 256 * 1024 * 1024
_MAXIMUM_PARTICLES = 1_000_000
_MAXIMUM_TRAJECTORY_FRAMES = 10_001
_MAXIMUM_TRAJECTORY_PARTICLE_FRAMES = 5_000_000

_SUPPORTED_WATER_MODELS = frozenset({"tip3p", "spce", "tip4pew", "tip5p", "swm4ndp"})
_SUPPORTED_POSITIVE_IONS = frozenset({"Cs+", "K+", "Li+", "Na+", "Rb+"})
_SUPPORTED_NEGATIVE_IONS = frozenset({"Cl-", "Br-", "F-", "I-"})
_SUPPORTED_LIPIDS = frozenset({"POPC", "POPE", "DLPC", "DLPE", "DMPC", "DOPC", "DPPC"})

FORCE_FIELD_SELECT = "molecular.force_field_select"
PROTEIN_PROTONATION_PREPARE = "molecular.protein_protonation_prepare"
ENVIRONMENT_PREPARE = "molecular.environment_prepare"
SYSTEM_CONSTRUCT = "molecular.system_construct"
LIGAND_OPENMM_SYSTEM_CONSTRUCT = "molecular.ligand_openmm_system_construct"
ENERGY_MINIMIZE = "molecular.energy_minimize"
DYNAMICS_RUN = "molecular.dynamics_run"
TRAJECTORY_GENERATE = "molecular.trajectory_generate"
SNAPSHOT_EXTRACT = "molecular.snapshot_extract"
ENSEMBLE_SUMMARIZE = "molecular.ensemble_summarize"


@runtime_checkable
class OpenMMPrivateStateStore(Protocol):
    """Persist native OpenMM state away from public scientific artifacts."""

    def read(self, reference: ArtifactReference) -> bytes:
        """Return private state associated with a public system manifest."""
        ...

    def write(self, reference: ArtifactReference, payload: bytes) -> None:
        """Persist private state under a public manifest identity."""
        ...


class OpenMMAdapterConfigurationError(ValueError):
    """Controlled construction failure for the OpenMM adapter."""


def _distribution_version() -> str | None:
    try:
        return importlib.metadata.version("openmm")
    except importlib.metadata.PackageNotFoundError:
        return None


def _openmm_identity() -> tuple[str, str, str, str] | None:
    try:
        import openmm
    except ImportError:
        return None
    return (
        _distribution_version() or "unknown",
        str(getattr(openmm.version, "version", "unknown")),
        str(getattr(openmm.version, "short_version", "unknown")),
        str(getattr(openmm.version, "git_revision", "unknown")),
    )


def _openmm_modules() -> tuple[object, object, object]:
    import openmm
    from openmm import app, unit

    return openmm, app, unit


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
        required_tools=("openmm",),
        required_runtime="python",
        determinism=(
            DeterminismClassification.DETERMINISTIC
            if deterministic
            else DeterminismClassification.NONDETERMINISTIC
        ),
    )


def openmm_capability_envelopes() -> tuple[CapabilityExecutionEnvelope, ...]:
    """Return planner declarations for the Phase 5.3 OpenMM capabilities."""

    definitions = (
        (
            FORCE_FIELD_SELECT,
            (),
            ("molecular_force_field_selection",),
            ("force_field_selection",),
            True,
            (
                "Version 1 accepts packaged relative OpenMM XML identifiers only; custom local force-field paths are rejected.",
            ),
        ),
        (
            PROTEIN_PROTONATION_PREPARE,
            ("molecular_structure", "molecular_force_field_selection"),
            ("molecular_protein_protonation_preparation", "prepared_molecular_structure"),
            ("protein_protonation", "protein_hydrogen_preparation", "catalytic_residue_state"),
            True,
            (
                "OpenMM Modeller adds hydrogens and selects standard residue variants at the requested pH.",
                "Scientist-supplied overrides use semantic chain/residue/name keys, never atom indices.",
                "No exact pKa or metal oxidation state is inferred.",
            ),
        ),
        (
            ENVIRONMENT_PREPARE,
            ("molecular_conformer_set", "molecular_force_field_selection"),
            ("molecular_environment",),
            ("environment_preparation", "solvation", "membrane_preparation"),
            False,
            (
                "The selected force field must contain templates for every solute and environment residue.",
                "Version 1 projects each conformer-set input as one explicitly named solute residue.",
                "Membrane preparation depends on OpenMM's packaged membrane construction data.",
            ),
        ),
        (
            SYSTEM_CONSTRUCT,
            ("molecular_environment", "molecular_force_field_selection"),
            ("molecular_simulation_system",),
            ("system_construction", "parameter_assignment"),
            True,
            (
                "Native OpenMM System XML is retained in a private engine-state store and never emitted as a public artifact.",
            ),
        ),
        (
            LIGAND_OPENMM_SYSTEM_CONSTRUCT,
            ("molecular_conformer_set", "molecular_ligand_parameterization"),
            ("molecular_environment", "molecular_simulation_system"),
            ("ligand_system_construction", "mmff94s_openmm_conversion"),
            True,
            (
                "The OpenMM System implements RDKit MMFF94s bonded, buffered-14-7 and electrostatic terms with explicit native-unit conversion.",
                "Pair-specific MMFF94s nonbonded parameters and 1-4 electrostatic scaling are retained; unsupported or incomplete assignments fail closed.",
            ),
        ),
        (
            ENERGY_MINIMIZE,
            ("molecular_environment", "molecular_simulation_system"),
            ("molecular_minimization_result", "molecular_snapshot"),
            ("energy_minimization",),
            False,
            (
                "Minimization convergence and exact coordinates can vary across OpenMM platforms and hardware.",
            ),
        ),
        (
            DYNAMICS_RUN,
            (
                "molecular_environment",
                "molecular_simulation_system",
                "molecular_snapshot",
            ),
            ("molecular_dynamics_run", "molecular_snapshot"),
            ("molecular_dynamics",),
            False,
            (
                "Fixed random seeds do not guarantee bitwise equality across OpenMM platforms or hardware.",
            ),
        ),
        (
            TRAJECTORY_GENERATE,
            (
                "molecular_environment",
                "molecular_simulation_system",
                "molecular_snapshot",
            ),
            ("molecular_trajectory",),
            ("trajectory_generation",),
            False,
            (
                "Trajectory recording is bounded to 10,001 frames and 5,000,000 particle-frames per invocation.",
                "Fixed random seeds do not guarantee bitwise equality across OpenMM platforms or hardware.",
            ),
        ),
        (
            SNAPSHOT_EXTRACT,
            ("molecular_trajectory",),
            ("molecular_snapshot",),
            ("snapshot_extraction",),
            True,
            (
                "Snapshot extraction copies one recorded trajectory frame without interpolation.",
            ),
        ),
        (
            ENSEMBLE_SUMMARIZE,
            ("molecular_trajectory",),
            ("molecular_ensemble_summary",),
            ("ensemble_summary",),
            True,
            (
                "Version 1 reports descriptive energy and temperature statistics only; it does not estimate equilibrium uncertainty.",
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
            execution_targets=("local_cpu", "local_opencl", "local_reference"),
            known_limitations=limitations,
            metadata={
                "scientific_domain": "classical_molecular_simulation",
                "adapter_family": "openmm",
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


def _rounded(value: float) -> float:
    return round(float(value), 10)


def _safe_identifier_part(value: str) -> str:
    normalized = "".join(
        character.lower() if character.isalnum() else "-" for character in value
    )
    normalized = "-".join(part for part in normalized.split("-") if part)
    return normalized[:48] or "artifact"


def _safe_topology_identifier(value: str, *, fallback: str) -> str:
    normalized = "".join(
        character if character.isalnum() or character in "._:/-" else "-"
        for character in value.strip()
    ).strip("-._:/")
    if not normalized or not normalized[0].isalnum():
        normalized = fallback
    return normalized[:128]


def _safe_label(value: str, *, fallback: str) -> str:
    normalized = value.strip().replace("\x00", "")
    normalized = "".join(
        character if ord(character) >= 32 else " " for character in normalized
    ).strip()
    return normalized[:128] or fallback


def _stable_identifier(prefix: str, *values: object) -> str:
    encoded = "\x1f".join(str(value) for value in values).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()[:32]
    return f"{prefix}-{digest}"


def _force_kind(value: object) -> str:
    name = type(value).__name__
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
    return _safe_identifier_part(snake).replace("-", "_")


class OpenMMClassicalSimulationAdapter:
    """Execute Phase 5.3 simulation capabilities through a pinned OpenMM runtime."""

    def __init__(
        self,
        payload_store: MolecularArtifactPayloadStore,
        private_state_store: OpenMMPrivateStateStore,
        *,
        maximum_payload_bytes: int = _MAXIMUM_PAYLOAD_BYTES,
        maximum_private_state_bytes: int = _MAXIMUM_PRIVATE_STATE_BYTES,
    ) -> None:
        if not isinstance(payload_store, MolecularArtifactPayloadStore):
            raise OpenMMAdapterConfigurationError(
                "The OpenMM adapter requires a molecular artifact payload store."
            )
        if not isinstance(private_state_store, OpenMMPrivateStateStore):
            raise OpenMMAdapterConfigurationError(
                "The OpenMM adapter requires a private engine-state store."
            )
        if maximum_payload_bytes <= 0 or maximum_private_state_bytes <= 0:
            raise OpenMMAdapterConfigurationError(
                "OpenMM payload and private-state limits must be positive."
            )
        self._payload_store = payload_store
        self._private_state_store = private_state_store
        self._maximum_payload_bytes = maximum_payload_bytes
        self._maximum_private_state_bytes = maximum_private_state_bytes
        distribution = _distribution_version() or "unavailable"
        self._declaration = ScientificEngineAdapterDeclaration(
            adapter_identifier="adapter.openmm.classical_simulation",
            adapter_version=_ADAPTER_VERSION,
            engine=ScientificEngineIdentity(
                engine_identifier="engine.openmm",
                engine_version=distribution,
            ),
            capabilities=openmm_capability_envelopes(),
            metadata={
                "scientific_domain": "classical_molecular_simulation",
                "native_objects_public": False,
                "private_state_required": True,
            },
        )
        self._envelopes = {
            envelope.descriptor.capability_name: envelope
            for envelope in self._declaration.capabilities
        }

    @property
    def declaration(self) -> ScientificEngineAdapterDeclaration:
        """Return the frozen adapter and capability declaration."""

        return self._declaration

    def health(self) -> ScientificEngineHealthReport:
        """Report dependency identity and the available OpenMM platforms."""

        identity = _openmm_identity()
        if identity is None:
            return ScientificEngineHealthReport(
                status=HealthStatus.UNAVAILABLE,
                reason_code="dependency_missing",
                message="The optional OpenMM dependency is unavailable.",
                diagnostics={"expected_version": _EXPECTED_OPENMM_VERSION},
            )
        distribution, build, short, revision = identity
        try:
            openmm, _, _ = _openmm_modules()
            platforms = ",".join(
                openmm.Platform.getPlatform(index).getName()
                for index in range(openmm.Platform.getNumPlatforms())
            )
        except Exception:
            return ScientificEngineHealthReport(
                status=HealthStatus.DEGRADED,
                reason_code="platform_discovery_failed",
                message="OpenMM loaded but its execution platforms could not be enumerated.",
                diagnostics={
                    "distribution_version": distribution,
                    "build_version": build,
                },
            )
        diagnostics = {
            "distribution_version": distribution,
            "build_version": build,
            "short_version": short,
            "git_revision": revision,
            "platforms": platforms,
        }
        if (
            distribution != _EXPECTED_OPENMM_VERSION
            or short != _EXPECTED_OPENMM_VERSION
        ):
            return ScientificEngineHealthReport(
                status=HealthStatus.DEGRADED,
                reason_code="dependency_version_mismatch",
                message="The installed OpenMM version does not match the repository pin.",
                diagnostics={
                    **diagnostics,
                    "expected_version": _EXPECTED_OPENMM_VERSION,
                },
            )
        return ScientificEngineHealthReport(
            status=HealthStatus.HEALTHY,
            diagnostics=diagnostics,
        )

    def invoke(self, invocation: CapabilityInvocation) -> CapabilityResult:
        """Execute one declared classical-simulation capability."""

        if not isinstance(invocation, CapabilityInvocation):
            return self._failure(
                "invalid_invocation",
                "The OpenMM adapter requires a scientific capability invocation.",
            )
        capability_name = invocation.capability.capability_name
        envelope = self._envelopes.get(capability_name)
        if envelope is None or invocation.capability != envelope.descriptor:
            return self._failure(
                "capability_not_declared",
                "The requested capability is not declared by this OpenMM adapter.",
            )
        health = self.health()
        if health.status is not HealthStatus.HEALTHY:
            return self._failure(
                health.reason_code or "adapter_unhealthy",
                health.message
                or "The OpenMM adapter is not healthy enough to execute.",
            )

        try:
            if capability_name == FORCE_FIELD_SELECT:
                return self._select_force_field(invocation)
            if capability_name == PROTEIN_PROTONATION_PREPARE:
                return self._prepare_protein_protonation(invocation)
            if capability_name == ENVIRONMENT_PREPARE:
                return self._prepare_environment(invocation)
            if capability_name == SYSTEM_CONSTRUCT:
                return self._construct_system(invocation)
            if capability_name == LIGAND_OPENMM_SYSTEM_CONSTRUCT:
                return self._construct_ligand_system(invocation)
            if capability_name == ENERGY_MINIMIZE:
                return self._minimize(invocation)
            if capability_name == DYNAMICS_RUN:
                return self._run_dynamics(invocation)
            if capability_name == TRAJECTORY_GENERATE:
                return self._generate_trajectory(invocation)
            if capability_name == SNAPSHOT_EXTRACT:
                return self._extract_snapshot(invocation)
            if capability_name == ENSEMBLE_SUMMARIZE:
                return self._summarize_ensemble(invocation)
        except (ValidationError, ValueError, KeyError, IndexError):
            return self._failure(
                "simulation_input_invalid",
                "The classical-simulation input failed controlled validation.",
            )
        except ImportError:
            return self._failure(
                "dependency_missing",
                "The optional OpenMM dependency is unavailable.",
            )
        except Exception:
            return self._failure(
                "simulation_execution_failed",
                "The classical-simulation capability failed without valid evidence.",
            )
        return self._failure(
            "capability_not_implemented",
            "The requested classical-simulation capability is not implemented.",
        )

    def _read_payload(self, reference: ArtifactReference) -> bytes:
        payload = self._payload_store.read(reference)
        if not isinstance(payload, bytes):
            raise ValueError("Artifact payload stores must return bytes.")
        if not payload or len(payload) > self._maximum_payload_bytes:
            raise ValueError("Simulation artifact payload size is invalid.")
        if hashlib.sha256(payload).hexdigest() != reference.content_sha256:
            raise ValueError("Simulation artifact bytes do not match their SHA-256.")
        if reference.byte_size is not None and reference.byte_size != len(payload):
            raise ValueError(
                "Simulation artifact bytes do not match their declared size."
            )
        return payload

    def _read_private_state(
        self,
        reference: ArtifactReference,
        manifest: MolecularSimulationSystem,
    ) -> bytes:
        payload = self._private_state_store.read(reference)
        if not isinstance(payload, bytes):
            raise ValueError("Private OpenMM state stores must return bytes.")
        if not payload or len(payload) > self._maximum_private_state_bytes:
            raise ValueError("Private OpenMM state size is invalid.")
        if hashlib.sha256(payload).hexdigest() != manifest.private_state_sha256:
            raise ValueError("Private OpenMM state does not match its public manifest.")
        return payload

    def _artifact_reference(
        self,
        *,
        invocation: CapabilityInvocation,
        artifact_type: str,
        media_type: str,
        payload: bytes,
        identifier_prefix: str,
        parents: tuple[ArtifactReference, ...],
        metadata: Mapping[str, str | int | float | bool],
    ) -> ArtifactReference:
        if not payload or len(payload) > self._maximum_payload_bytes:
            raise ValueError("Generated simulation artifact payload size is invalid.")
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
            media_type=media_type,
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

    def _write_artifact(
        self,
        *,
        invocation: CapabilityInvocation,
        artifact_type: str,
        media_type: str,
        payload: bytes,
        identifier_prefix: str,
        parents: tuple[ArtifactReference, ...],
        metadata: Mapping[str, str | int | float | bool],
    ) -> ArtifactReference:
        reference = self._artifact_reference(
            invocation=invocation,
            artifact_type=artifact_type,
            media_type=media_type,
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
        lineage: tuple[ArtifactLineageEdge, ...] = (),
        diagnostics: Mapping[str, str | int | float | bool] | None = None,
        platform: str = "not_applicable",
    ) -> CapabilityResult:
        identity = _openmm_identity()
        distribution = identity[0] if identity is not None else "unknown"
        build = identity[1] if identity is not None else "unknown"
        return CapabilityResult(
            status=ExecutionStatus.SUCCESS,
            output_artifacts=artifacts,
            lineage=lineage,
            diagnostics=dict(diagnostics or {}),
            execution_evidence=ExecutionEvidence(
                runtime_identifier="openmm.local",
                exit_code=0,
                evidence_artifacts=tuple(artifact.pointer for artifact in artifacts),
                details={
                    "capability_name": invocation.capability.capability_name,
                    "openmm_distribution_version": distribution,
                    "openmm_build_version": build,
                    "platform": platform,
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
        default: object = None,
        required: bool = False,
    ) -> object:
        parameters = dict(invocation.parameters)
        if name not in parameters:
            if required:
                raise ValueError(f"Missing required simulation parameter: {name}.")
            return default
        return parameters[name]

    @classmethod
    def _string_parameter(
        cls,
        invocation: CapabilityInvocation,
        name: str,
        *,
        default: str | None = None,
        required: bool = False,
        maximum_length: int = 4096,
    ) -> str:
        value = cls._parameter(invocation, name, default=default, required=required)
        if not isinstance(value, str):
            raise ValueError(f"Simulation parameter {name} must be text.")
        normalized = value.strip()
        if not normalized or len(normalized) > maximum_length:
            raise ValueError(f"Simulation parameter {name} is invalid.")
        return normalized

    @classmethod
    def _integer_parameter(
        cls,
        invocation: CapabilityInvocation,
        name: str,
        *,
        minimum: int,
        maximum: int,
        default: int | None = None,
        required: bool = False,
    ) -> int:
        value = cls._parameter(invocation, name, default=default, required=required)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"Simulation parameter {name} must be an integer.")
        if value < minimum or value > maximum:
            raise ValueError(f"Simulation parameter {name} is outside its range.")
        return value

    @classmethod
    def _float_parameter(
        cls,
        invocation: CapabilityInvocation,
        name: str,
        *,
        minimum: float,
        maximum: float,
        default: float | None = None,
        required: bool = False,
        allow_zero: bool = True,
    ) -> float:
        value = cls._parameter(invocation, name, default=default, required=required)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"Simulation parameter {name} must be numeric.")
        normalized = float(value)
        if not math.isfinite(normalized) or not minimum <= normalized <= maximum:
            raise ValueError(f"Simulation parameter {name} is outside its range.")
        if not allow_zero and normalized == 0:
            raise ValueError(f"Simulation parameter {name} must be nonzero.")
        return normalized

    @classmethod
    def _optional_float_parameter(
        cls,
        invocation: CapabilityInvocation,
        name: str,
        *,
        minimum: float,
        maximum: float,
    ) -> float | None:
        value = cls._parameter(invocation, name, default=None)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"Simulation parameter {name} must be numeric or null.")
        normalized = float(value)
        if not math.isfinite(normalized) or not minimum <= normalized <= maximum:
            raise ValueError(f"Simulation parameter {name} is outside its range.")
        return normalized

    @classmethod
    def _boolean_parameter(
        cls,
        invocation: CapabilityInvocation,
        name: str,
        *,
        required: bool = False,
        default: bool | None = None,
    ) -> bool:
        value = cls._parameter(invocation, name, default=default, required=required)
        if not isinstance(value, bool):
            raise ValueError(f"Simulation parameter {name} must be boolean.")
        return value

    @classmethod
    def _string_list_parameter(
        cls,
        invocation: CapabilityInvocation,
        name: str,
        *,
        required: bool = False,
        maximum_items: int = 32,
    ) -> tuple[str, ...]:
        raw = cls._string_parameter(
            invocation,
            name,
            required=required,
            maximum_length=8192,
        )
        values = tuple(item.strip() for item in raw.split(";") if item.strip())
        if not values or len(values) > maximum_items:
            raise ValueError(f"Simulation parameter {name} has an invalid item count.")
        return values

    @staticmethod
    def _inputs_by_type(
        invocation: CapabilityInvocation,
    ) -> dict[str, ArtifactReference]:
        result: dict[str, ArtifactReference] = {}
        for reference in invocation.input_artifacts:
            if reference.artifact_type in result:
                raise ValueError("Simulation input artifact types must be unique.")
            result[reference.artifact_type] = reference
        return result

    @staticmethod
    def _require_input(
        inputs: Mapping[str, ArtifactReference],
        artifact_type: str,
    ) -> ArtifactReference:
        try:
            return inputs[artifact_type]
        except KeyError:
            raise ValueError(
                f"Missing required simulation input artifact type: {artifact_type}."
            ) from None

    def _load_model(
        self, reference: ArtifactReference, model_type: type[object]
    ) -> object:
        return model_type.model_validate_json(self._read_payload(reference))

    def _lineage(
        self,
        invocation: CapabilityInvocation,
        source: ArtifactReference,
        destination: ArtifactReference,
        relationship_type: str,
    ) -> ArtifactLineageEdge:
        return ArtifactLineageEdge(
            source=source.pointer,
            destination=destination.pointer,
            relationship_type=relationship_type,
            producing_capability=invocation.capability.capability_name,
            producing_capability_version=invocation.capability.version,
            execution_identifier=invocation.context.execution_id,
        )

    @staticmethod
    def _force_field(selection: MolecularForceFieldSelection) -> object:
        identity = _openmm_identity()
        if identity is None:
            raise ImportError("OpenMM is unavailable.")
        if (
            selection.engine_identifier != "engine.openmm"
            or selection.engine_distribution_version != identity[0]
            or selection.engine_build_version != identity[1]
        ):
            raise ValueError(
                "Force-field selection was validated by a different OpenMM runtime."
            )
        _, app, _ = _openmm_modules()
        data_root = importlib.resources.files("openmm.app").joinpath("data")
        with ExitStack() as stack:
            resolved_files: list[str] = []
            for identifier in selection.force_field_files:
                candidate = data_root.joinpath(*PurePosixPath(identifier).parts)
                if not candidate.is_file():
                    raise ValueError(
                        "The requested force-field XML is not packaged with OpenMM."
                    )
                resolved_files.append(
                    str(stack.enter_context(importlib.resources.as_file(candidate)))
                )
            return app.ForceField(*resolved_files)

    @staticmethod
    def _platform(
        name: str, properties: tuple[str, ...]
    ) -> tuple[object, dict[str, str]]:
        openmm, _, _ = _openmm_modules()
        available = {
            openmm.Platform.getPlatform(index).getName(): openmm.Platform.getPlatform(
                index
            )
            for index in range(openmm.Platform.getNumPlatforms())
        }
        try:
            platform = available[name]
        except KeyError:
            raise ValueError("The requested OpenMM platform is unavailable.") from None
        parsed: dict[str, str] = {}
        for item in properties:
            key, value = item.split("=", 1)
            parsed[key] = value
        return platform, parsed

    @classmethod
    def _platform_properties_parameter(
        cls,
        invocation: CapabilityInvocation,
    ) -> tuple[str, ...]:
        value = cls._parameter(invocation, "platform_properties", default="")
        if not isinstance(value, str):
            raise ValueError("Simulation platform properties must be text.")
        if not value.strip():
            return ()
        entries = tuple(item.strip() for item in value.split(";") if item.strip())
        if len(entries) > 16:
            raise ValueError("Too many OpenMM platform properties were supplied.")
        return entries

    @staticmethod
    def _topology_from_conformer(
        conformer_set: MolecularConformerSet,
        *,
        conformer_index: int,
        residue_name: str,
        chain_identifier: str,
        atom_names: tuple[str, ...],
    ) -> tuple[object, object]:
        openmm, app, unit = _openmm_modules()
        try:
            conformer = conformer_set.conformers[conformer_index]
        except IndexError:
            raise ValueError("The requested conformer index does not exist.") from None
        topology = app.Topology()
        chain = topology.addChain(chain_identifier)
        residue = topology.addResidue(residue_name, chain, id="1")
        topology_atoms: list[object] = []
        graph_atoms = conformer_set.source_graph.atoms
        if len(atom_names) != len(graph_atoms):
            raise ValueError(
                "Solute atom names must contain one entry for every source atom."
            )
        for atom, atom_name in zip(graph_atoms, atom_names, strict=True):
            element = app.Element.getByAtomicNumber(atom.atomic_number)
            topology_atoms.append(
                topology.addAtom(
                    atom_name,
                    element,
                    residue,
                    id=str(atom.atom_index + 1),
                )
            )
        for bond in conformer_set.source_graph.bonds:
            order = (
                int(round(bond.bond_order))
                if bond.bond_order in {1.0, 2.0, 3.0}
                else None
            )
            topology.addBond(
                topology_atoms[bond.atom_index_a],
                topology_atoms[bond.atom_index_b],
                order=order,
            )
        positions = unit.Quantity(
            [
                openmm.Vec3(
                    coordinate.x / 10.0,
                    coordinate.y / 10.0,
                    coordinate.z / 10.0,
                )
                for coordinate in conformer.coordinates
            ],
            unit.nanometer,
        )
        return topology, positions

    @staticmethod
    def _vector(
        value: object, unit_module: object, target_unit: object
    ) -> MolecularVector3:
        del unit_module
        converted = (
            value.value_in_unit(target_unit)
            if hasattr(value, "value_in_unit")
            else value
        )
        x = getattr(converted, "x", converted[0])
        y = getattr(converted, "y", converted[1])
        z = getattr(converted, "z", converted[2])
        return MolecularVector3(
            x=_rounded(x),
            y=_rounded(y),
            z=_rounded(z),
        )

    @classmethod
    def _periodic_box_from_vectors(
        cls,
        vectors: object,
        unit_module: object,
    ) -> MolecularPeriodicBox | None:
        if vectors is None:
            return None
        values = tuple(vectors)
        if len(values) != 3:
            raise ValueError("OpenMM periodic boxes must contain three vectors.")
        return MolecularPeriodicBox(
            vector_a=cls._vector(values[0], unit_module, unit_module.nanometer),
            vector_b=cls._vector(values[1], unit_module, unit_module.nanometer),
            vector_c=cls._vector(values[2], unit_module, unit_module.nanometer),
        )

    @classmethod
    def _environment_from_modeller(
        cls,
        *,
        modeller: object,
        conformer_set: MolecularConformerSet,
        selection: MolecularForceFieldSelection,
        environment_type: str,
        source_conformer_index: int,
        water_model: str | None,
        ionic_strength_molar: float | None,
        positive_ion: str | None,
        negative_ion: str | None,
        neutralized: bool | None,
        solvent_padding_nm: float | None,
        solvent_box_shape: str | None,
        lipid_type: str | None,
        membrane_minimum_padding_nm: float | None,
        membrane_center_z_nm: float | None,
        preparation_platform: str | None,
    ) -> MolecularEnvironment:
        _, _, unit = _openmm_modules()
        topology = modeller.getTopology()
        modeller_positions = modeller.getPositions()
        atoms = tuple(
            MolecularSimulationAtom(
                particle_index=int(atom.index),
                particle_kind=(
                    "atom" if atom.element is not None else "extra_particle"
                ),
                atomic_number=(
                    int(atom.element.atomic_number)
                    if atom.element is not None
                    else None
                ),
                element_symbol=(
                    str(atom.element.symbol) if atom.element is not None else None
                ),
                atom_name=_safe_label(
                    str(atom.name), fallback=f"atom-{int(atom.index) + 1}"
                ),
                residue_index=int(atom.residue.index),
                residue_name=_safe_label(str(atom.residue.name), fallback="residue"),
                residue_identifier=_safe_topology_identifier(
                    str(atom.residue.id or atom.residue.index + 1),
                    fallback=f"residue-{int(atom.residue.index) + 1}",
                ),
                chain_index=int(atom.residue.chain.index),
                chain_identifier=_safe_topology_identifier(
                    str(atom.residue.chain.id or atom.residue.chain.index + 1),
                    fallback=f"chain-{int(atom.residue.chain.index) + 1}",
                ),
                source_atom_index=(
                    conformer_set.source_graph.atoms[int(atom.index)].source_atom_index
                    if atom.element is not None
                    and int(atom.index) < len(conformer_set.source_graph.atoms)
                    else None
                ),
                formal_charge=(
                    conformer_set.source_graph.atoms[int(atom.index)].formal_charge
                    if atom.element is not None
                    and int(atom.index) < len(conformer_set.source_graph.atoms)
                    else None
                ),
            )
            for atom in topology.atoms()
        )
        bonds = tuple(
            MolecularSimulationBond(
                atom_index_a=int(bond.atom1.index),
                atom_index_b=int(bond.atom2.index),
                order=(int(bond.order) if bond.order in {1, 2, 3} else None),
            )
            for bond in topology.bonds()
        )
        positions = tuple(
            cls._vector(position, unit, unit.nanometer)
            for position in modeller_positions
        )
        periodic_box = cls._periodic_box_from_vectors(
            topology.getPeriodicBoxVectors(),
            unit,
        )
        environment_identifier = _stable_identifier(
            "molecular-environment",
            conformer_set.conformer_set_identifier,
            selection.selection_identifier,
            environment_type,
            len(atoms),
            periodic_box.fingerprint if periodic_box is not None else "vacuum",
        )
        return MolecularEnvironment(
            schema_version=_SCHEMA_VERSION,
            environment_identifier=environment_identifier,
            environment_type=environment_type,
            source_conformer_set_identifier=conformer_set.conformer_set_identifier,
            source_conformer_index=source_conformer_index,
            force_field_selection_identifier=selection.selection_identifier,
            atoms=atoms,
            bonds=bonds,
            positions=positions,
            source_solute_atom_count=len(conformer_set.source_graph.atoms),
            periodic_box=periodic_box,
            water_model=water_model,
            ionic_strength_molar=ionic_strength_molar,
            positive_ion=positive_ion,
            negative_ion=negative_ion,
            neutralized=neutralized,
            solvent_padding_nm=solvent_padding_nm,
            solvent_box_shape=solvent_box_shape,
            lipid_type=lipid_type,
            membrane_minimum_padding_nm=membrane_minimum_padding_nm,
            membrane_center_z_nm=membrane_center_z_nm,
            preparation_platform=preparation_platform,
        )

    @staticmethod
    def _topology_from_environment(
        environment: MolecularEnvironment,
    ) -> tuple[object, object]:
        openmm, app, unit = _openmm_modules()
        topology = app.Topology()
        chains: dict[int, object] = {}
        residues: dict[tuple[int, int], object] = {}
        atoms: list[object] = []
        for atom in environment.atoms:
            chain = chains.get(atom.chain_index)
            if chain is None:
                chain = topology.addChain(atom.chain_identifier)
                chains[atom.chain_index] = chain
            residue_key = (atom.chain_index, atom.residue_index)
            residue = residues.get(residue_key)
            if residue is None:
                residue = topology.addResidue(
                    atom.residue_name,
                    chain,
                    id=atom.residue_identifier,
                )
                residues[residue_key] = residue
            atoms.append(
                topology.addAtom(
                    atom.atom_name,
                    (
                        app.Element.getByAtomicNumber(atom.atomic_number)
                        if atom.atomic_number is not None
                        else None
                    ),
                    residue,
                    id=str(atom.particle_index + 1),
                )
            )
        for bond in environment.bonds:
            topology.addBond(
                atoms[bond.atom_index_a],
                atoms[bond.atom_index_b],
                order=bond.order,
            )
        if environment.periodic_box is not None:
            box = environment.periodic_box
            topology.setPeriodicBoxVectors(
                unit.Quantity(
                    (
                        openmm.Vec3(box.vector_a.x, box.vector_a.y, box.vector_a.z),
                        openmm.Vec3(box.vector_b.x, box.vector_b.y, box.vector_b.z),
                        openmm.Vec3(box.vector_c.x, box.vector_c.y, box.vector_c.z),
                    ),
                    unit.nanometer,
                )
            )
        positions = unit.Quantity(
            [
                openmm.Vec3(position.x, position.y, position.z)
                for position in environment.positions
            ],
            unit.nanometer,
        )
        return topology, positions

    @staticmethod
    def _positions_from_snapshot(snapshot: MolecularSnapshot) -> object:
        openmm, _, unit = _openmm_modules()
        return unit.Quantity(
            [
                openmm.Vec3(position.x, position.y, position.z)
                for position in snapshot.positions
            ],
            unit.nanometer,
        )

    @staticmethod
    def _velocities_from_snapshot(snapshot: MolecularSnapshot) -> object | None:
        if not snapshot.velocities:
            return None
        openmm, _, unit = _openmm_modules()
        return unit.Quantity(
            [
                openmm.Vec3(velocity.x, velocity.y, velocity.z)
                for velocity in snapshot.velocities
            ],
            unit.nanometer / unit.picosecond,
        )

    @staticmethod
    def _box_vectors(
        box: MolecularPeriodicBox | None,
    ) -> tuple[object, object, object] | None:
        if box is None:
            return None
        openmm, _, unit = _openmm_modules()
        return tuple(
            openmm.Vec3(vector.x, vector.y, vector.z) * unit.nanometer
            for vector in (box.vector_a, box.vector_b, box.vector_c)
        )

    @staticmethod
    def _constraint_mode(value: str, app: object) -> object | None:
        return {
            "none": None,
            "hydrogen_bonds": app.HBonds,
            "all_bonds": app.AllBonds,
            "hydrogen_angles": app.HAngles,
        }[value]

    @staticmethod
    def _nonbonded_method(value: str, app: object) -> object:
        return {
            "no_cutoff": app.NoCutoff,
            "cutoff_nonperiodic": app.CutoffNonPeriodic,
            "cutoff_periodic": app.CutoffPeriodic,
            "ewald": app.Ewald,
            "pme": app.PME,
            "ljpme": app.LJPME,
        }[value]

    def _load_system(
        self,
        reference: ArtifactReference,
    ) -> tuple[MolecularSimulationSystem, object]:
        openmm, _, _ = _openmm_modules()
        manifest = MolecularSimulationSystem.model_validate_json(
            self._read_payload(reference)
        )
        identity = _openmm_identity()
        if identity is None:
            raise ImportError("OpenMM is unavailable.")
        if (
            manifest.engine_identifier != "engine.openmm"
            or manifest.engine_distribution_version != identity[0]
            or manifest.engine_build_version != identity[1]
        ):
            raise ValueError(
                "Private simulation state was created by a different OpenMM runtime."
            )
        private_state = self._read_private_state(reference, manifest)
        system = openmm.XmlSerializer.deserialize(private_state.decode("utf-8"))
        if int(system.getNumParticles()) != manifest.particle_count:
            raise ValueError("Private OpenMM system particle count changed.")
        return manifest, system

    @classmethod
    def _dynamics_settings(
        cls,
        invocation: CapabilityInvocation,
    ) -> MolecularDynamicsSettings:
        ensemble = cls._string_parameter(
            invocation,
            "ensemble",
            required=True,
            maximum_length=16,
        ).lower()
        if ensemble not in {"nve", "nvt", "npt"}:
            raise ValueError("Unsupported molecular dynamics ensemble.")
        integrator = cls._string_parameter(
            invocation,
            "integrator",
            required=True,
            maximum_length=32,
        ).lower()
        if integrator not in {"verlet", "langevin_middle"}:
            raise ValueError("Unsupported molecular dynamics integrator.")
        velocity_source = cls._string_parameter(
            invocation,
            "initial_velocity_source",
            required=True,
            maximum_length=32,
        ).lower()
        if velocity_source not in {"maxwell_boltzmann", "input_snapshot"}:
            raise ValueError("Unsupported initial velocity source.")
        initial_temperature = cls._optional_float_parameter(
            invocation,
            "initial_temperature_kelvin",
            minimum=0.000001,
            maximum=100_000,
        )
        target_temperature = cls._optional_float_parameter(
            invocation,
            "target_temperature_kelvin",
            minimum=0.000001,
            maximum=100_000,
        )
        friction = cls._optional_float_parameter(
            invocation,
            "friction_per_ps",
            minimum=0.000001,
            maximum=1_000_000,
        )
        pressure = cls._optional_float_parameter(
            invocation,
            "pressure_bar",
            minimum=0.000001,
            maximum=1_000_000,
        )
        barostat = cls._parameter(
            invocation,
            "barostat_interval_steps",
            default=None,
        )
        if barostat is not None and (
            isinstance(barostat, bool) or not isinstance(barostat, int)
        ):
            raise ValueError("Barostat interval must be an integer or null.")
        if barostat is not None and not 1 <= barostat <= 10_000_000:
            raise ValueError("Barostat interval is outside its range.")
        random_seed_value = cls._parameter(invocation, "random_seed", default=None)
        if random_seed_value is not None and (
            isinstance(random_seed_value, bool)
            or not isinstance(random_seed_value, int)
        ):
            raise ValueError("Random seed must be an integer or null.")
        if (
            random_seed_value is not None
            and not 1 <= random_seed_value <= 2_147_483_647
        ):
            raise ValueError("Random seed is outside its range.")
        return MolecularDynamicsSettings(
            ensemble=ensemble,
            integrator=integrator,
            timestep_fs=cls._float_parameter(
                invocation,
                "timestep_fs",
                minimum=0.000001,
                maximum=100,
                required=True,
                allow_zero=False,
            ),
            steps=cls._integer_parameter(
                invocation,
                "steps",
                minimum=1,
                maximum=10_000_000,
                required=True,
            ),
            initial_velocity_source=velocity_source,
            initial_temperature_kelvin=initial_temperature,
            target_temperature_kelvin=target_temperature,
            friction_per_ps=friction,
            pressure_bar=pressure,
            barostat_interval_steps=barostat,
            random_seed=random_seed_value,
            platform=cls._string_parameter(
                invocation,
                "platform",
                required=True,
                maximum_length=64,
            ),
            platform_properties=cls._platform_properties_parameter(invocation),
        )

    @staticmethod
    def _integrator_and_system(
        system: object,
        settings: MolecularDynamicsSettings,
    ) -> tuple[object, object]:
        openmm, _, unit = _openmm_modules()
        copied = openmm.XmlSerializer.deserialize(
            openmm.XmlSerializer.serialize(system)
        )
        if settings.ensemble == "nve":
            integrator = openmm.VerletIntegrator(
                settings.timestep_fs * unit.femtosecond
            )
            return copied, integrator
        integrator = openmm.LangevinMiddleIntegrator(
            settings.target_temperature_kelvin * unit.kelvin,
            settings.friction_per_ps / unit.picosecond,
            settings.timestep_fs * unit.femtosecond,
        )
        if settings.random_seed is None:
            raise ValueError("Stochastic dynamics requires a random seed.")
        integrator.setRandomNumberSeed(settings.random_seed)
        if settings.ensemble == "npt":
            barostat = openmm.MonteCarloBarostat(
                settings.pressure_bar * unit.bar,
                settings.target_temperature_kelvin * unit.kelvin,
                settings.barostat_interval_steps,
            )
            barostat.setRandomNumberSeed(settings.random_seed)
            copied.addForce(barostat)
        return copied, integrator

    @classmethod
    def _snapshot_from_state(
        cls,
        *,
        state: object,
        system_manifest: MolecularSimulationSystem,
        snapshot_identifier: str,
        frame_index: int,
        step: int,
        source_trajectory_identifier: str | None = None,
    ) -> MolecularSnapshot:
        _, _, unit = _openmm_modules()
        positions = tuple(
            cls._vector(position, unit, unit.nanometer)
            for position in state.getPositions()
        )
        velocities_list: list[MolecularVelocity] = []
        for index, velocity in enumerate(state.getVelocities()):
            converted = (
                velocity.value_in_unit(unit.nanometer / unit.picosecond)
                if hasattr(velocity, "value_in_unit")
                else velocity
            )
            x = getattr(converted, "x", converted[0])
            y = getattr(converted, "y", converted[1])
            z = getattr(converted, "z", converted[2])
            velocities_list.append(
                MolecularVelocity(
                    atom_index=index,
                    x=_rounded(x),
                    y=_rounded(y),
                    z=_rounded(z),
                )
            )
        velocities = tuple(velocities_list)
        potential = _rounded(
            state.getPotentialEnergy().value_in_unit(unit.kilojoule_per_mole)
        )
        kinetic = _rounded(
            state.getKineticEnergy().value_in_unit(unit.kilojoule_per_mole)
        )
        degrees_of_freedom = system_manifest.degrees_of_freedom
        temperature = None
        if degrees_of_freedom > 0:
            gas_constant = 0.00831446261815324
            temperature = _rounded(2.0 * kinetic / (degrees_of_freedom * gas_constant))
        periodic_box = (
            cls._periodic_box_from_vectors(state.getPeriodicBoxVectors(), unit)
            if system_manifest.periodic
            else None
        )
        return MolecularSnapshot(
            schema_version=_SCHEMA_VERSION,
            snapshot_identifier=snapshot_identifier,
            system_identifier=system_manifest.system_identifier,
            frame_index=frame_index,
            step=step,
            time_ps=_rounded(state.getTime().value_in_unit(unit.picosecond)),
            particle_count=system_manifest.particle_count,
            positions=positions,
            velocities=velocities,
            periodic_box=periodic_box,
            potential_energy_kj_per_mol=potential,
            kinetic_energy_kj_per_mol=kinetic,
            instantaneous_temperature_kelvin=temperature,
            source_trajectory_identifier=source_trajectory_identifier,
        )

    def _initial_state_inputs(
        self,
        inputs: Mapping[str, ArtifactReference],
        system_manifest: MolecularSimulationSystem,
    ) -> tuple[
        object,
        object | None,
        MolecularPeriodicBox | None,
        str | None,
        tuple[ArtifactReference, ...],
    ]:
        environment_reference = inputs.get("molecular_environment")
        snapshot_reference = inputs.get("molecular_snapshot")
        if (environment_reference is None) == (snapshot_reference is None):
            raise ValueError(
                "Dynamics requires exactly one molecular environment or snapshot input."
            )
        if snapshot_reference is not None:
            snapshot = MolecularSnapshot.model_validate_json(
                self._read_payload(snapshot_reference)
            )
            if snapshot.system_identifier != system_manifest.system_identifier:
                raise ValueError(
                    "Initial snapshot belongs to a different simulation system."
                )
            return (
                self._positions_from_snapshot(snapshot),
                self._velocities_from_snapshot(snapshot),
                snapshot.periodic_box,
                snapshot.snapshot_identifier,
                (snapshot_reference,),
            )
        environment = MolecularEnvironment.model_validate_json(
            self._read_payload(environment_reference)
        )
        if environment.environment_identifier != system_manifest.environment_identifier:
            raise ValueError(
                "Initial environment belongs to a different simulation system."
            )
        _, positions = self._topology_from_environment(environment)
        return positions, None, environment.periodic_box, None, (environment_reference,)

    @staticmethod
    def _configure_context_initial_state(
        context: object,
        *,
        positions: object,
        velocities: object | None,
        periodic_box: MolecularPeriodicBox | None,
        settings: MolecularDynamicsSettings,
    ) -> None:
        context.setPositions(positions)
        vectors = OpenMMClassicalSimulationAdapter._box_vectors(periodic_box)
        if vectors is not None:
            context.setPeriodicBoxVectors(*vectors)
        if settings.initial_velocity_source == "maxwell_boltzmann":
            if (
                settings.initial_temperature_kelvin is None
                or settings.random_seed is None
            ):
                raise ValueError(
                    "Maxwell-Boltzmann initialization requires temperature and seed."
                )
            _, _, unit = _openmm_modules()
            context.setVelocitiesToTemperature(
                settings.initial_temperature_kelvin * unit.kelvin,
                settings.random_seed,
            )
            return
        if velocities is None:
            raise ValueError(
                "Input-snapshot velocity initialization requires recorded velocities."
            )
        context.setVelocities(velocities)

    def _select_force_field(self, invocation: CapabilityInvocation) -> CapabilityResult:
        if invocation.input_artifacts:
            raise ValueError("Force-field selection does not accept input artifacts.")
        force_field_files = self._string_list_parameter(
            invocation,
            "force_field_files",
            required=True,
            maximum_items=16,
        )
        water_model_value = self._string_parameter(
            invocation,
            "water_model",
            required=True,
            maximum_length=64,
        ).lower()
        water_model = None if water_model_value == "none" else water_model_value
        if water_model is not None and water_model not in _SUPPORTED_WATER_MODELS:
            raise ValueError("Unsupported OpenMM modeller water model.")
        identity = _openmm_identity()
        if identity is None:
            raise ImportError("OpenMM is unavailable.")
        self._force_field(
            MolecularForceFieldSelection(
                schema_version=_SCHEMA_VERSION,
                selection_identifier="force-field-validation",
                engine_identifier="engine.openmm",
                engine_distribution_version=identity[0],
                engine_build_version=identity[1],
                force_field_files=force_field_files,
                water_model=water_model,
            )
        )
        selection = MolecularForceFieldSelection(
            schema_version=_SCHEMA_VERSION,
            selection_identifier=_stable_identifier(
                "molecular-force-field",
                *force_field_files,
                water_model or "none",
                identity[0],
            ),
            engine_identifier="engine.openmm",
            engine_distribution_version=identity[0],
            engine_build_version=identity[1],
            force_field_files=force_field_files,
            water_model=water_model,
        )
        payload = selection.to_canonical_json().encode("utf-8")
        artifact = self._write_artifact(
            invocation=invocation,
            artifact_type="molecular_force_field_selection",
            media_type="application/vnd.pulsate.molecular-force-field-selection+json",
            payload=payload,
            identifier_prefix="molecular-force-field-selection",
            parents=(),
            metadata={
                "force_field_file_count": len(force_field_files),
                "water_model": water_model or "none",
            },
        )
        return self._success(
            invocation,
            artifacts=(artifact,),
            diagnostics={"selection_identifier": selection.selection_identifier},
        )

    def _prepare_protein_protonation(
        self, invocation: CapabilityInvocation
    ) -> CapabilityResult:
        inputs = self._inputs_by_type(invocation)
        if set(inputs) != {
            "molecular_structure",
            "molecular_force_field_selection",
        }:
            raise ValueError(
                "Protein protonation requires structure and force-field artifacts."
            )
        structure_reference = self._require_input(inputs, "molecular_structure")
        selection_reference = self._require_input(
            inputs, "molecular_force_field_selection"
        )
        if self._string_parameter(
            invocation, "structure_format", required=True, maximum_length=16
        ).lower() != "pdb":
            raise ValueError("OpenMM protein protonation currently requires PDB input.")
        target_ph = self._float_parameter(
            invocation, "target_ph", minimum=0.0, maximum=14.0, required=True
        )
        raw_overrides = self._string_parameter(
            invocation,
            "residue_variant_overrides",
            default="none",
            maximum_length=8192,
        )
        override_entries = (
            ()
            if raw_overrides.strip().lower() == "none"
            else tuple(
                item.strip() for item in raw_overrides.split(";") if item.strip()
            )
        )
        if len(override_entries) > 128:
            raise ValueError("Protein residue overrides must be bounded.")
        overrides: dict[str, str] = {}
        for entry in override_entries:
            if entry.count("=") != 1:
                raise ValueError("Protein residue overrides must use key=variant syntax.")
            raw_key, raw_variant = entry.split("=", 1)
            key = raw_key.strip().lower()
            variant = raw_variant.strip().upper()
            if (
                not re.fullmatch(
                    r"chain-[a-z0-9._-]+-residue-[a-z0-9._-]+-[a-z0-9._-]+",
                    key,
                )
                or not re.fullmatch(r"[A-Z0-9]{1,8}", variant)
            ):
                raise ValueError("A protein residue override is invalid.")
            if key in overrides:
                raise ValueError("Protein residue overrides must be unique.")
            overrides[key] = variant

        selection = MolecularForceFieldSelection.model_validate_json(
            self._read_payload(selection_reference)
        )
        try:
            source_text = self._read_payload(structure_reference).decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError("Protein PDB input must be UTF-8 text.") from None
        openmm, app, unit = _openmm_modules()
        pdb = app.PDBFile(io.StringIO(source_text))
        source_atoms = tuple(pdb.topology.atoms())
        source_residues = tuple(pdb.topology.residues())
        if (
            not source_atoms
            or len(source_atoms) > _MAXIMUM_PARTICLES
            or not source_residues
        ):
            raise ValueError("Protein PDB topology is invalid.")

        semantic_keys: list[str] = []
        requested_variants: list[str | None] = []
        for residue in source_residues:
            chain_label = (residue.chain.id or "blank").strip().lower() or "blank"
            sequence = (residue.id or "unknown").strip().lower() or "unknown"
            residue_name = residue.name.strip().lower()
            key = f"chain-{chain_label}-residue-{sequence}-{residue_name}"
            if key in semantic_keys:
                raise ValueError("Protein residue semantic identities are ambiguous.")
            semantic_keys.append(key)
            requested_variants.append(overrides.get(key))
        if not set(overrides).issubset(semantic_keys):
            raise ValueError("A protein residue override does not resolve.")

        modeller = app.Modeller(pdb.topology, pdb.positions)
        force_field = self._force_field(selection)
        selected_variants = tuple(
            modeller.addHydrogens(
                force_field,
                pH=target_ph,
                variants=requested_variants,
            )
        )
        prepared_atoms = tuple(modeller.topology.atoms())
        prepared_residues = tuple(modeller.topology.residues())
        if (
            len(prepared_atoms) < len(source_atoms)
            or len(prepared_atoms) > _MAXIMUM_PARTICLES
            or len(prepared_residues) != len(source_residues)
            or len(selected_variants) != len(source_residues)
        ):
            raise ValueError("Protein protonation changed unsupported topology identity.")

        system = force_field.createSystem(
            modeller.topology,
            nonbondedMethod=app.NoCutoff,
            constraints=None,
            rigidWater=False,
            removeCMMotion=False,
        )
        nonbonded = tuple(
            system.getForce(index)
            for index in range(system.getNumForces())
            if isinstance(system.getForce(index), openmm.NonbondedForce)
        )
        if len(nonbonded) != 1 or system.getNumParticles() != len(prepared_atoms):
            raise ValueError("Prepared protein charges are not auditable.")
        charges = tuple(
            float(
                nonbonded[0]
                .getParticleParameters(index)[0]
                .value_in_unit(unit.elementary_charge)
            )
            for index in range(system.getNumParticles())
        )
        residue_variant_hypotheses = {
            "ARG": ("ARG", "ARN"),
            "ASP": ("ASP", "ASH"),
            "CYS": ("CYS", "CYX"),
            "GLU": ("GLU", "GLH"),
            "HIS": ("HID", "HIE", "HIP"),
            "LYS": ("LYS", "LYN"),
            "TYR": ("TYR", "TYM"),
        }
        residue_states: list[MolecularProteinResidueState] = []
        particle_offset = 0
        for index, residue in enumerate(prepared_residues):
            atom_count = sum(1 for _ in residue.atoms())
            residue_charge = _rounded(
                sum(charges[particle_offset: particle_offset + atom_count])
            )
            particle_offset += atom_count
            selected = str(selected_variants[index] or residue.name)
            alternatives = tuple(
                variant
                for variant in residue_variant_hypotheses.get(
                    residue.name.upper(), ()
                )
                if variant != selected
            )
            residue_states.append(MolecularProteinResidueState(
                residue_identifier=semantic_keys[index],
                chain_identifier=residue.chain.id or "blank",
                source_sequence_identifier=residue.id or "unknown",
                residue_name=residue.name,
                selected_variant=selected,
                selection_source=(
                    "semantic_override"
                    if semantic_keys[index] in overrides
                    else "openmm_ph_model"
                ),
                particle_partial_charge=residue_charge,
                chemically_ambiguous=bool(alternatives),
                alternative_variants=alternatives,
            ))
        if particle_offset != len(charges):
            raise ValueError("Residue charges do not cover the prepared particles.")
        transition_metals = {
            *range(21, 31), *range(39, 49), *range(72, 81)
        }
        contains_transition_metal = any(
            atom.element is not None
            and atom.element.atomic_number in transition_metals
            for atom in prepared_atoms
        )
        curated_metal_states = {
            "Fe": (
                (2, 6, 1), (2, 6, 3), (2, 6, 5),
                (3, 5, 2), (3, 5, 4), (3, 5, 6),
            ),
            "Co": ((2, 7, 2), (2, 7, 4), (3, 6, 1), (3, 6, 3), (3, 6, 5)),
            "Ni": ((2, 8, 1), (2, 8, 3)),
            "Cu": ((1, 10, 1), (2, 9, 2)),
            "Zn": ((2, 10, 1),),
            "Mn": ((2, 5, 2), (2, 5, 4), (2, 5, 6), (3, 4, 3), (3, 4, 5)),
            "Cr": ((2, 4, 3), (2, 4, 5), (3, 3, 4)),
            "V": ((2, 3, 2), (2, 3, 4), (3, 2, 3), (4, 1, 2)),
            "Mo": ((4, 2, 1), (4, 2, 3), (6, 0, 1)),
        }
        metal_states: list[MolecularMetalElectronicState] = []
        for atom in prepared_atoms:
            if atom.element is None or atom.element.atomic_number not in transition_metals:
                continue
            element_symbol = str(atom.element.symbol)
            hypotheses = curated_metal_states.get(element_symbol)
            if not hypotheses:
                raise ValueError(
                    "Transition-metal oxidation/spin alternatives are unavailable for this element."
                )
            chain = (atom.residue.chain.id or "blank").strip().lower() or "blank"
            sequence = (atom.residue.id or "unknown").strip().lower() or "unknown"
            atom_identity = (
                f"metal-chain-{chain}-residue-{sequence}-"
                f"{element_symbol.lower()}-atom-{int(atom.index) + 1}"
            )
            for oxidation, d_count, multiplicity in hypotheses:
                metal_states.append(MolecularMetalElectronicState(
                    state_identifier=(
                        f"{atom_identity}-oxidation-{oxidation}-multiplicity-{multiplicity}"
                    ),
                    metal_atom_identifier=atom_identity,
                    element_symbol=element_symbol,
                    oxidation_state=oxidation,
                    d_electron_count=d_count,
                    spin_multiplicity=multiplicity,
                    rationale=(
                        "Curated first-row/enzymatic metal hypothesis; coordination geometry, "
                        "ligand-field evidence, and comparative electronic energies are required "
                        "before selecting a state."
                    ),
                ))
        warnings = [
            "OpenMM variants use force-field templates and pH heuristics; exact pKa values were not calculated.",
            "Chemically ambiguous catalytic residues should be evaluated as bounded alternatives when relevant.",
        ]
        if contains_transition_metal:
            warnings.append("Metal oxidation and spin states were not inferred.")

        prepared_stream = io.StringIO()
        app.PDBFile.writeFile(
            modeller.topology, modeller.positions, prepared_stream, keepIds=True
        )
        prepared_artifact = self._write_artifact(
            invocation=invocation,
            artifact_type="prepared_molecular_structure",
            media_type="chemical/x-pdb",
            payload=prepared_stream.getvalue().encode("utf-8"),
            identifier_prefix="prepared-protein-structure",
            parents=(structure_reference, selection_reference),
            metadata={
                "target_ph": target_ph,
                "atom_count": len(prepared_atoms),
                "hydrogen_atoms_added": len(prepared_atoms) - len(source_atoms),
            },
        )
        preparation = MolecularProteinProtonationPreparation(
            schema_version=_SCHEMA_VERSION,
            preparation_identifier=_stable_identifier(
                "molecular-protein-protonation",
                structure_reference.content_sha256,
                selection.selection_identifier,
                target_ph,
                *(f"{key}:{value}" for key, value in sorted(overrides.items())),
            ),
            source_structure_artifact_identifier=structure_reference.artifact_identifier,
            force_field_selection_identifier=selection.selection_identifier,
            target_ph=target_ph,
            source_atom_count=len(source_atoms),
            prepared_atom_count=len(prepared_atoms),
            hydrogen_atoms_added=len(prepared_atoms) - len(source_atoms),
            residue_states=tuple(residue_states),
            total_particle_partial_charge=_rounded(sum(charges)),
            explicit_semantic_variant_overrides=tuple(sorted(overrides)),
            contains_transition_metal=contains_transition_metal,
            metal_electronic_state_alternatives=tuple(metal_states),
            requires_metal_state_selection=contains_transition_metal,
            warnings=tuple(warnings),
        )
        report_artifact = self._write_artifact(
            invocation=invocation,
            artifact_type="molecular_protein_protonation_preparation",
            media_type="application/vnd.pulsate.molecular-protein-protonation+json",
            payload=preparation.to_canonical_json().encode("utf-8"),
            identifier_prefix="molecular-protein-protonation",
            parents=(structure_reference, selection_reference, prepared_artifact),
            metadata={
                "target_ph": target_ph,
                "residue_count": len(residue_states),
                "total_particle_partial_charge": preparation.total_particle_partial_charge,
                "exact_pka_calculated": False,
                "metal_oxidation_states_inferred": False,
            },
        )
        lineage = tuple(
            self._lineage(invocation, parent, prepared_artifact, "protonated_from")
            for parent in (structure_reference, selection_reference)
        ) + (
            self._lineage(
                invocation, prepared_artifact, report_artifact, "documented_by"
            ),
        )
        return self._success(
            invocation,
            artifacts=(report_artifact, prepared_artifact),
            lineage=lineage,
            diagnostics={
                "target_ph": target_ph,
                "hydrogen_atoms_added": preparation.hydrogen_atoms_added,
                "residue_count": len(residue_states),
                "total_particle_partial_charge": preparation.total_particle_partial_charge,
                "contains_transition_metal": contains_transition_metal,
            },
        )

    def _prepare_environment(
        self, invocation: CapabilityInvocation
    ) -> CapabilityResult:
        inputs = self._inputs_by_type(invocation)
        if set(inputs) != {
            "molecular_conformer_set",
            "molecular_force_field_selection",
        }:
            raise ValueError(
                "Environment preparation requires conformer and force-field artifacts."
            )
        conformer_reference = self._require_input(inputs, "molecular_conformer_set")
        selection_reference = self._require_input(
            inputs, "molecular_force_field_selection"
        )
        conformer_set = MolecularConformerSet.model_validate_json(
            self._read_payload(conformer_reference)
        )
        selection = MolecularForceFieldSelection.model_validate_json(
            self._read_payload(selection_reference)
        )
        environment_type = self._string_parameter(
            invocation,
            "environment_type",
            required=True,
            maximum_length=32,
        ).lower()
        if environment_type not in {"vacuum", "solvent", "membrane"}:
            raise ValueError("Unsupported molecular environment type.")
        conformer_index = self._integer_parameter(
            invocation,
            "conformer_index",
            minimum=0,
            maximum=len(conformer_set.conformers) - 1,
            required=True,
        )
        residue_name = self._string_parameter(
            invocation,
            "solute_residue_name",
            required=True,
            maximum_length=32,
        )
        chain_identifier = self._string_parameter(
            invocation,
            "solute_chain_identifier",
            required=True,
            maximum_length=32,
        )
        atom_names = self._string_list_parameter(
            invocation,
            "solute_atom_names",
            required=True,
            maximum_items=len(conformer_set.source_graph.atoms),
        )
        atom_names = tuple(
            _safe_label(name, fallback=f"atom-{index + 1}")
            for index, name in enumerate(atom_names)
        )
        topology, positions = self._topology_from_conformer(
            conformer_set,
            conformer_index=conformer_index,
            residue_name=_safe_label(residue_name, fallback="residue"),
            chain_identifier=chain_identifier,
            atom_names=atom_names,
        )
        _, app, unit = _openmm_modules()
        modeller = app.Modeller(topology, positions)
        force_field = self._force_field(selection)
        water_model: str | None = None
        ionic_strength: float | None = None
        positive_ion: str | None = None
        negative_ion: str | None = None
        neutralized: bool | None = None
        solvent_padding_nm: float | None = None
        solvent_box_shape: str | None = None
        lipid_type: str | None = None
        membrane_minimum_padding_nm: float | None = None
        membrane_center_z_nm: float | None = None
        preparation_platform: str | None = None
        if environment_type in {"solvent", "membrane"}:
            water_model = self._string_parameter(
                invocation,
                "water_model",
                required=True,
                maximum_length=64,
            ).lower()
            if selection.water_model is None or water_model != selection.water_model:
                raise ValueError(
                    "Environment water model must exactly match the force-field selection."
                )
            ionic_strength = self._float_parameter(
                invocation,
                "ionic_strength_molar",
                minimum=0,
                maximum=10,
                required=True,
            )
            positive_ion = self._string_parameter(
                invocation,
                "positive_ion",
                required=True,
                maximum_length=16,
            )
            negative_ion = self._string_parameter(
                invocation,
                "negative_ion",
                required=True,
                maximum_length=16,
            )
            if positive_ion not in _SUPPORTED_POSITIVE_IONS:
                raise ValueError("Unsupported positive ion for OpenMM modeller.")
            if negative_ion not in _SUPPORTED_NEGATIVE_IONS:
                raise ValueError("Unsupported negative ion for OpenMM modeller.")
            neutralized = self._boolean_parameter(
                invocation,
                "neutralize",
                required=True,
            )
            if environment_type == "solvent":
                solvent_padding_nm = self._float_parameter(
                    invocation,
                    "padding_nm",
                    minimum=0.05,
                    maximum=100,
                    required=True,
                    allow_zero=False,
                )
                solvent_box_shape = self._string_parameter(
                    invocation,
                    "box_shape",
                    required=True,
                    maximum_length=32,
                ).lower()
                if solvent_box_shape not in {"cube", "dodecahedron", "octahedron"}:
                    raise ValueError("Unsupported solvent box shape.")
                modeller.addSolvent(
                    force_field,
                    model=water_model,
                    padding=solvent_padding_nm * unit.nanometer,
                    boxShape=solvent_box_shape,
                    positiveIon=positive_ion,
                    negativeIon=negative_ion,
                    ionicStrength=ionic_strength * unit.molar,
                    neutralize=neutralized,
                )
            else:
                lipid_type = self._string_parameter(
                    invocation,
                    "lipid_type",
                    required=True,
                    maximum_length=32,
                ).upper()
                if lipid_type not in _SUPPORTED_LIPIDS:
                    raise ValueError("Unsupported packaged OpenMM membrane lipid type.")
                membrane_minimum_padding_nm = self._float_parameter(
                    invocation,
                    "minimum_padding_nm",
                    minimum=0.05,
                    maximum=100,
                    required=True,
                    allow_zero=False,
                )
                membrane_center_z_nm = self._float_parameter(
                    invocation,
                    "membrane_center_z_nm",
                    minimum=-1000,
                    maximum=1000,
                    required=True,
                )
                preparation_platform = self._string_parameter(
                    invocation,
                    "preparation_platform",
                    required=True,
                    maximum_length=64,
                )
                membrane_platform, _ = self._platform(preparation_platform, ())
                modeller.addMembrane(
                    force_field,
                    lipidType=lipid_type,
                    membraneCenterZ=membrane_center_z_nm * unit.nanometer,
                    minimumPadding=membrane_minimum_padding_nm * unit.nanometer,
                    positiveIon=positive_ion,
                    negativeIon=negative_ion,
                    ionicStrength=ionic_strength * unit.molar,
                    neutralize=neutralized,
                    platform=membrane_platform,
                )
        modeller.addExtraParticles(force_field)
        environment = self._environment_from_modeller(
            modeller=modeller,
            conformer_set=conformer_set,
            selection=selection,
            environment_type=environment_type,
            source_conformer_index=conformer_index,
            water_model=water_model,
            ionic_strength_molar=ionic_strength,
            positive_ion=positive_ion,
            negative_ion=negative_ion,
            neutralized=neutralized,
            solvent_padding_nm=solvent_padding_nm,
            solvent_box_shape=solvent_box_shape,
            lipid_type=lipid_type,
            membrane_minimum_padding_nm=membrane_minimum_padding_nm,
            membrane_center_z_nm=membrane_center_z_nm,
            preparation_platform=preparation_platform,
        )
        if len(environment.atoms) > _MAXIMUM_PARTICLES:
            raise ValueError("Prepared environment exceeds the particle limit.")
        payload = environment.to_canonical_json().encode("utf-8")
        artifact = self._write_artifact(
            invocation=invocation,
            artifact_type="molecular_environment",
            media_type="application/vnd.pulsate.molecular-environment+json",
            payload=payload,
            identifier_prefix="molecular-environment",
            parents=(conformer_reference, selection_reference),
            metadata={
                "environment_type": environment.environment_type,
                "particle_count": len(environment.atoms),
                "solute_atom_count": environment.source_solute_atom_count,
            },
        )
        lineage = tuple(
            self._lineage(invocation, parent, artifact, "prepared_into")
            for parent in (conformer_reference, selection_reference)
        )
        return self._success(
            invocation,
            artifacts=(artifact,),
            lineage=lineage,
            diagnostics={
                "environment_identifier": environment.environment_identifier,
                "particle_count": len(environment.atoms),
            },
        )

    def _construct_ligand_system(
        self, invocation: CapabilityInvocation
    ) -> CapabilityResult:
        """Translate a complete RDKit MMFF94s assignment into executable OpenMM."""

        inputs = self._inputs_by_type(invocation)
        if set(inputs) != {
            "molecular_conformer_set",
            "molecular_ligand_parameterization",
        }:
            raise ValueError(
                "Ligand system construction requires conformers and MMFF94s parameters."
            )
        conformer_reference = self._require_input(inputs, "molecular_conformer_set")
        parameter_reference = self._require_input(
            inputs, "molecular_ligand_parameterization"
        )
        conformers = MolecularConformerSet.model_validate_json(
            self._read_payload(conformer_reference)
        )
        parameters = MolecularLigandParameterization.model_validate_json(
            self._read_payload(parameter_reference)
        )
        graph = conformers.source_graph
        if parameters.source_graph_identifier != graph.graph_identifier:
            raise ValueError("MMFF94s parameters and conformer graph identities disagree.")
        if len(parameters.atoms) != len(graph.atoms) or not parameters.nonbonded_pairs:
            raise ValueError("Executable MMFF94s conversion requires complete pair parameters.")
        conformer_index = self._integer_parameter(
            invocation,
            "conformer_index",
            default=0,
            minimum=0,
            maximum=len(conformers.conformers) - 1,
        )
        selected = conformers.conformers[conformer_index]
        openmm, app, unit = _openmm_modules()
        system = openmm.System()
        for graph_atom in graph.atoms:
            element = app.Element.getByAtomicNumber(graph_atom.atomic_number)
            system.addParticle(element.mass)

        # RDKit MMFF expressions are in kcal/mol, Angstrom and degrees.  Every
        # expression below converts the OpenMM nm/radian coordinates explicitly.
        bond_force = openmm.CustomBondForce(
            "4.184*0.5*143.9325*kb*dr*dr*(1-2*dr+(7/3)*dr*dr); dr=10*r-r0"
        )
        bond_force.addPerBondParameter("kb")
        bond_force.addPerBondParameter("r0")
        angle_force = openmm.CustomAngleForce(
            "4.184*(linear*143.9325*ka*(1+cos(theta))"
            "+(1-linear)*0.5*143.9325*0.0003046174197867086*ka*d*d*(1-0.006981317*d));"
            "d=theta*57.29577951308232-theta0"
        )
        for name in ("ka", "theta0", "linear"):
            angle_force.addPerAngleParameter(name)
        stretch_force = openmm.CustomCompoundBondForce(
            3,
            "4.184*143.9325*0.017453292519943295*dtheta*(k1*dr1+k2*dr2);"
            "dr1=10*pointdistance(x1,y1,z1,x2,y2,z2)-r01;"
            "dr2=10*pointdistance(x2,y2,z2,x3,y3,z3)-r02;"
            "dtheta=pointangle(x1,y1,z1,x2,y2,z2,x3,y3,z3)*57.29577951308232-theta0",
        )
        for name in ("k1", "k2", "r01", "r02", "theta0"):
            stretch_force.addPerBondParameter(name)
        torsion_force = openmm.CustomTorsionForce(
            "4.184*0.5*(v1*(1+cos(theta))+v2*(1-cos(2*theta))+v3*(1+cos(3*theta)))"
        )
        for name in ("v1", "v2", "v3"):
            torsion_force.addPerTorsionParameter(name)
        oop_force = openmm.CustomCompoundBondForce(
            4,
            "4.184*0.5*143.9325*0.0003046174197867086*koop*chi*chi;"
            "chi=asin(sin(pointangle(x3,y3,z3,x2,y2,z2,x4,y4,z4))"
            "*sin(pointdihedral(x1,y1,z1,x2,y2,z2,x3,y3,z3,x4,y4,z4)))*57.29577951308232",
        )
        oop_force.addPerBondParameter("koop")
        nonbonded_force = openmm.CustomBondForce(
            "4.184*(eps*(1.07*rs/(ra+0.07*rs))^7"
            "*(1.12*rs^7/(ra^7+0.12*rs^7)-2)"
            "+332.0716*qprod*escale/(ra+0.05));ra=10*r"
        )
        for name in ("rs", "eps", "qprod", "escale"):
            nonbonded_force.addPerBondParameter(name)

        bond_values: dict[tuple[int, int], tuple[float, float]] = {}
        angle_values: dict[tuple[int, int, int], tuple[float, float]] = {}
        stretch_terms: list[object] = []
        for term in parameters.terms:
            values = term.parameter_values
            if term.term_kind == "bond_stretch":
                _, kb, r0 = values
                i, j = term.atom_indices
                bond_force.addBond(i, j, [kb, r0])
                bond_values[tuple(sorted((i, j)))] = (kb, r0)
            elif term.term_kind == "angle_bend":
                _, ka, theta0 = values
                i, j, k = term.atom_indices
                linear = 1.0 if abs(theta0 - 180.0) <= 1.0e-8 else 0.0
                angle_force.addAngle(i, j, k, [ka, theta0, linear])
                angle_values[(i, j, k)] = (ka, theta0)
            elif term.term_kind == "stretch_bend":
                stretch_terms.append(term)
            elif term.term_kind == "proper_torsion":
                _, v1, v2, v3 = values
                torsion_force.addTorsion(*term.atom_indices, [v1, v2, v3])
            elif term.term_kind == "out_of_plane":
                oop_force.addBond(list(term.atom_indices), [values[0]])
            else:
                raise ValueError("Unsupported MMFF94s force term.")
        for term in stretch_terms:
            _, k1, k2 = term.parameter_values
            i, j, k = term.atom_indices
            _, r01 = bond_values[tuple(sorted((i, j)))]
            _, r02 = bond_values[tuple(sorted((j, k)))]
            _, theta0 = angle_values[(i, j, k)]
            stretch_force.addBond([i, j, k], [k1, k2, r01, r02, theta0])
        for pair in parameters.nonbonded_pairs:
            nonbonded_force.addBond(
                pair.atom_index_a,
                pair.atom_index_b,
                [
                    pair.vdw_r_star,
                    pair.vdw_epsilon,
                    pair.charge_product,
                    pair.electrostatic_scale,
                ],
            )
        for force in (
            bond_force,
            angle_force,
            stretch_force,
            torsion_force,
            oop_force,
            nonbonded_force,
        ):
            system.addForce(force)

        ff_identifier = parameters.parameterization_identifier
        environment_identifier = _stable_identifier(
            "molecular-ligand-vacuum-environment",
            conformers.conformer_set_identifier,
            conformer_index,
            ff_identifier,
        )
        environment = MolecularEnvironment(
            schema_version=_SCHEMA_VERSION,
            environment_identifier=environment_identifier,
            environment_type="vacuum",
            source_conformer_set_identifier=conformers.conformer_set_identifier,
            source_conformer_index=conformer_index,
            force_field_selection_identifier=ff_identifier,
            atoms=tuple(
                MolecularSimulationAtom(
                    particle_index=atom.atom_index,
                    particle_kind="atom",
                    atomic_number=atom.atomic_number,
                    element_symbol=atom.element_symbol,
                    atom_name=f"{atom.element_symbol}{atom.atom_index + 1}",
                    residue_index=0,
                    residue_name="LIG",
                    residue_identifier="ligand-1",
                    chain_index=0,
                    chain_identifier="ligand-chain",
                    source_atom_index=atom.atom_index,
                    formal_charge=atom.formal_charge,
                )
                for atom in graph.atoms
            ),
            bonds=tuple(
                MolecularSimulationBond(
                    atom_index_a=bond.atom_index_a,
                    atom_index_b=bond.atom_index_b,
                    order=(
                        int(round(bond.bond_order))
                        if abs(bond.bond_order - round(bond.bond_order)) < 1.0e-8
                        else None
                    ),
                )
                for bond in graph.bonds
            ),
            positions=tuple(
                MolecularVector3(x=item.x / 10, y=item.y / 10, z=item.z / 10)
                for item in selected.coordinates
            ),
            source_solute_atom_count=len(graph.atoms),
        )
        settings = MolecularSystemConstructionSettings(
            nonbonded_method="no_cutoff",
            constraints="none",
            rigid_water=False,
            remove_center_of_mass_motion=False,
        )
        xml = openmm.XmlSerializer.serialize(system).encode("utf-8")
        identity = _openmm_identity()
        if identity is None:
            raise ImportError("OpenMM is unavailable.")
        masses = tuple(
            float(system.getParticleMass(i).value_in_unit(unit.dalton))
            for i in range(system.getNumParticles())
        )
        private_sha = hashlib.sha256(xml).hexdigest()
        manifest = MolecularSimulationSystem(
            schema_version=_SCHEMA_VERSION,
            system_identifier=_stable_identifier(
                "molecular-mmff94s-openmm-system", environment_identifier, private_sha
            ),
            environment_identifier=environment_identifier,
            force_field_selection_identifier=ff_identifier,
            particle_count=system.getNumParticles(),
            massive_particle_count=sum(value > 0 for value in masses),
            constraint_count=system.getNumConstraints(),
            degrees_of_freedom=3 * sum(value > 0 for value in masses),
            force_kinds=tuple(
                _force_kind(system.getForce(i)) for i in range(system.getNumForces())
            ),
            total_mass_amu=_rounded(sum(masses)),
            periodic=False,
            settings=settings,
            engine_identifier="engine.openmm",
            engine_distribution_version=identity[0],
            engine_build_version=identity[1],
            private_state_sha256=private_sha,
            partial_charge_source="rdkit_mmff94s_parameter_assignment",
            particle_partial_charges_e=tuple(atom.partial_charge for atom in parameters.atoms),
            total_partial_charge_e=parameters.total_partial_charge,
        )
        environment_payload = environment.to_canonical_json().encode("utf-8")
        environment_artifact = self._write_artifact(
            invocation=invocation,
            artifact_type="molecular_environment",
            media_type="application/vnd.pulsate.molecular-environment+json",
            payload=environment_payload,
            identifier_prefix="molecular-ligand-vacuum-environment",
            parents=(conformer_reference, parameter_reference),
            metadata={"environment_type": "vacuum", "force_field": "mmff94s"},
        )
        manifest_payload = manifest.to_canonical_json().encode("utf-8")
        system_artifact = self._artifact_reference(
            invocation=invocation,
            artifact_type="molecular_simulation_system",
            media_type="application/vnd.pulsate.molecular-simulation-system+json",
            payload=manifest_payload,
            identifier_prefix="molecular-mmff94s-openmm-system",
            parents=(environment_artifact, parameter_reference),
            metadata={
                "particle_count": manifest.particle_count,
                "force_field": "mmff94s",
                "native_state_public": False,
            },
        )
        self._private_state_store.write(system_artifact, xml)
        self._payload_store.write(system_artifact, manifest_payload)
        lineage = (
            self._lineage(invocation, conformer_reference, environment_artifact, "coordinates_projected_into"),
            self._lineage(invocation, parameter_reference, environment_artifact, "parameterized_into"),
            self._lineage(invocation, environment_artifact, system_artifact, "executed_as"),
            self._lineage(invocation, parameter_reference, system_artifact, "converted_into"),
        )
        return self._success(
            invocation,
            artifacts=(environment_artifact, system_artifact),
            lineage=lineage,
            diagnostics={
                "force_field": "mmff94s",
                "openmm_system_constructed": True,
                "private_state_sha256": private_sha,
            },
        )

    def _construct_system(self, invocation: CapabilityInvocation) -> CapabilityResult:
        inputs = self._inputs_by_type(invocation)
        if set(inputs) != {"molecular_environment", "molecular_force_field_selection"}:
            raise ValueError(
                "System construction requires environment and force-field artifacts."
            )
        environment_reference = self._require_input(inputs, "molecular_environment")
        selection_reference = self._require_input(
            inputs, "molecular_force_field_selection"
        )
        environment = MolecularEnvironment.model_validate_json(
            self._read_payload(environment_reference)
        )
        selection = MolecularForceFieldSelection.model_validate_json(
            self._read_payload(selection_reference)
        )
        if (
            environment.force_field_selection_identifier
            != selection.selection_identifier
        ):
            raise ValueError(
                "Environment and force-field selection identities disagree."
            )
        nonbonded_method = self._string_parameter(
            invocation,
            "nonbonded_method",
            required=True,
            maximum_length=32,
        ).lower()
        if nonbonded_method not in {
            "no_cutoff",
            "cutoff_nonperiodic",
            "cutoff_periodic",
            "ewald",
            "pme",
            "ljpme",
        }:
            raise ValueError("Unsupported nonbonded method.")
        constraints = self._string_parameter(
            invocation,
            "constraints",
            required=True,
            maximum_length=32,
        ).lower()
        if constraints not in {
            "none",
            "hydrogen_bonds",
            "all_bonds",
            "hydrogen_angles",
        }:
            raise ValueError("Unsupported constraint mode.")
        cutoff = self._optional_float_parameter(
            invocation,
            "nonbonded_cutoff_nm",
            minimum=0.000001,
            maximum=100,
        )
        switch = self._optional_float_parameter(
            invocation,
            "switch_distance_nm",
            minimum=0.000001,
            maximum=100,
        )
        hydrogen_mass = self._optional_float_parameter(
            invocation,
            "hydrogen_mass_amu",
            minimum=0.000001,
            maximum=1000,
        )
        settings = MolecularSystemConstructionSettings(
            nonbonded_method=nonbonded_method,
            nonbonded_cutoff_nm=cutoff,
            switch_distance_nm=switch,
            constraints=constraints,
            rigid_water=self._boolean_parameter(
                invocation, "rigid_water", required=True
            ),
            remove_center_of_mass_motion=self._boolean_parameter(
                invocation,
                "remove_center_of_mass_motion",
                required=True,
            ),
            hydrogen_mass_amu=hydrogen_mass,
            ewald_error_tolerance=self._optional_float_parameter(
                invocation,
                "ewald_error_tolerance",
                minimum=1e-12,
                maximum=0.999999,
            ),
        )
        periodic_methods = {"cutoff_periodic", "ewald", "pme", "ljpme"}
        if nonbonded_method in periodic_methods and environment.periodic_box is None:
            raise ValueError(
                "Periodic nonbonded methods require a periodic environment."
            )
        topology, _ = self._topology_from_environment(environment)
        _, app, unit = _openmm_modules()
        force_field = self._force_field(selection)
        kwargs: dict[str, object] = {
            "nonbondedMethod": self._nonbonded_method(nonbonded_method, app),
            "constraints": self._constraint_mode(constraints, app),
            "rigidWater": settings.rigid_water,
            "removeCMMotion": settings.remove_center_of_mass_motion,
        }
        if settings.ewald_error_tolerance is not None:
            kwargs["ewaldErrorTolerance"] = settings.ewald_error_tolerance
        if cutoff is not None:
            kwargs["nonbondedCutoff"] = cutoff * unit.nanometer
        if switch is not None:
            kwargs["switchDistance"] = switch * unit.nanometer
        if hydrogen_mass is not None:
            kwargs["hydrogenMass"] = hydrogen_mass * unit.dalton
        system = force_field.createSystem(topology, **kwargs)
        openmm, _, _ = _openmm_modules()

        nonbonded_forces = tuple(
            system.getForce(index)
            for index in range(system.getNumForces())
            if isinstance(
                system.getForce(index),
                openmm.NonbondedForce,
            )
        )

        if len(nonbonded_forces) != 1:
            raise ValueError(
                "Constructed OpenMM system must contain exactly one "
                "NonbondedForce for auditable partial-charge extraction."
            )

        nonbonded_force = nonbonded_forces[0]

        if (
            nonbonded_force.getNumParticleParameterOffsets()
            != 0
        ):
            raise ValueError(
                "OpenMM particle parameter offsets are not supported "
                "by the current partial-charge extraction contract."
            )

        if (
            nonbonded_force.getNumParticles()
            != system.getNumParticles()
        ):
            raise ValueError(
                "OpenMM NonbondedForce particle count does not "
                "match the system."
            )

        particle_partial_charges = tuple(
            _rounded(
                nonbonded_force
                .getParticleParameters(index)[0]
                .value_in_unit(unit.elementary_charge)
            )
            for index in range(system.getNumParticles())
        )

        total_partial_charge = _rounded(
            sum(particle_partial_charges)
        )

        xml = openmm.XmlSerializer.serialize(system).encode("utf-8")
        if not xml or len(xml) > self._maximum_private_state_bytes:
            raise ValueError("Generated private OpenMM system state is invalid.")
        private_sha256 = hashlib.sha256(xml).hexdigest()
        identity = _openmm_identity()
        if identity is None:
            raise ImportError("OpenMM is unavailable.")
        particle_masses = tuple(
            float(system.getParticleMass(index).value_in_unit(unit.dalton))
            for index in range(system.getNumParticles())
        )
        total_mass = sum(particle_masses)
        massive_particle_count = sum(mass > 0 for mass in particle_masses)
        degrees_of_freedom = 3 * massive_particle_count - int(
            system.getNumConstraints()
        )
        if settings.remove_center_of_mass_motion:
            degrees_of_freedom -= 3
        if degrees_of_freedom <= 0:
            raise ValueError(
                "Constructed OpenMM system has no physical degrees of freedom."
            )
        manifest = MolecularSimulationSystem(
            schema_version=_SCHEMA_VERSION,
            system_identifier=_stable_identifier(
                "molecular-simulation-system",
                environment.environment_identifier,
                selection.selection_identifier,
                settings.fingerprint,
                private_sha256,
            ),
            environment_identifier=environment.environment_identifier,
            force_field_selection_identifier=selection.selection_identifier,
            particle_count=int(system.getNumParticles()),
            massive_particle_count=massive_particle_count,
            constraint_count=int(system.getNumConstraints()),
            degrees_of_freedom=degrees_of_freedom,
            force_kinds=tuple(
                _force_kind(system.getForce(index))
                for index in range(system.getNumForces())
            ),
            total_mass_amu=_rounded(total_mass),
            periodic=bool(system.usesPeriodicBoundaryConditions()),
            settings=settings,
            engine_identifier="engine.openmm",
            engine_distribution_version=identity[0],
            engine_build_version=identity[1],
            private_state_sha256=private_sha256,
            partial_charge_source="openmm_nonbonded_force",
            particle_partial_charges_e=particle_partial_charges,
            total_partial_charge_e=total_partial_charge,
        )
        payload = manifest.to_canonical_json().encode("utf-8")
        artifact = self._artifact_reference(
            invocation=invocation,
            artifact_type="molecular_simulation_system",
            media_type="application/vnd.pulsate.molecular-simulation-system+json",
            payload=payload,
            identifier_prefix="molecular-simulation-system",
            parents=(environment_reference, selection_reference),
            metadata={
                "particle_count": manifest.particle_count,
                "massive_particle_count": manifest.massive_particle_count,
                "constraint_count": manifest.constraint_count,
                "degrees_of_freedom": manifest.degrees_of_freedom,
                "periodic": manifest.periodic,
                "partial_charge_source": manifest.partial_charge_source,
                "total_partial_charge_e": manifest.total_partial_charge_e,
                "native_state_public": False,
            },
        )
        self._private_state_store.write(artifact, xml)
        self._payload_store.write(artifact, payload)
        lineage = tuple(
            self._lineage(invocation, parent, artifact, "parameterized_into")
            for parent in (environment_reference, selection_reference)
        )
        return self._success(
            invocation,
            artifacts=(artifact,),
            lineage=lineage,
            diagnostics={
                "system_identifier": manifest.system_identifier,
                "particle_count": manifest.particle_count,
                "partial_charge_source": manifest.partial_charge_source,
                "total_partial_charge_e": manifest.total_partial_charge_e,
                "private_state_sha256": private_sha256,
            },
        )

    def _minimize(self, invocation: CapabilityInvocation) -> CapabilityResult:
        inputs = self._inputs_by_type(invocation)
        if set(inputs) != {"molecular_environment", "molecular_simulation_system"}:
            raise ValueError(
                "Energy minimization requires environment and system artifacts."
            )
        environment_reference = self._require_input(inputs, "molecular_environment")
        system_reference = self._require_input(inputs, "molecular_simulation_system")
        environment = MolecularEnvironment.model_validate_json(
            self._read_payload(environment_reference)
        )
        system_manifest, system = self._load_system(system_reference)
        if environment.environment_identifier != system_manifest.environment_identifier:
            raise ValueError("Environment and simulation system identities disagree.")
        platform_name = self._string_parameter(
            invocation,
            "platform",
            required=True,
            maximum_length=64,
        )
        properties = self._platform_properties_parameter(invocation)
        platform, property_map = self._platform(platform_name, properties)
        settings = MolecularMinimizationSettings(
            platform=platform_name,
            platform_properties=properties,
            integration_timestep_fs=self._float_parameter(
                invocation,
                "integration_timestep_fs",
                minimum=0.000001,
                maximum=100,
                required=True,
                allow_zero=False,
            ),
            tolerance_kj_per_mol_nm=self._float_parameter(
                invocation,
                "tolerance_kj_per_mol_nm",
                minimum=0.000001,
                maximum=1e12,
                required=True,
                allow_zero=False,
            ),
            maximum_iterations=self._integer_parameter(
                invocation,
                "maximum_iterations",
                minimum=0,
                maximum=10_000_000,
                required=True,
            ),
        )
        _, positions = self._topology_from_environment(environment)
        openmm, _, unit = _openmm_modules()
        integrator = openmm.VerletIntegrator(
            settings.integration_timestep_fs * unit.femtosecond
        )
        context = openmm.Context(system, integrator, platform, property_map)
        try:
            context.setPositions(positions)
            initial_state = context.getState(getEnergy=True)
            initial_potential = _rounded(
                initial_state.getPotentialEnergy().value_in_unit(
                    unit.kilojoule_per_mole
                )
            )
            openmm.LocalEnergyMinimizer.minimize(
                context,
                settings.tolerance_kj_per_mol_nm
                * unit.kilojoule_per_mole
                / unit.nanometer,
                settings.maximum_iterations,
            )
            state = context.getState(
                getPositions=True,
                getVelocities=True,
                getEnergy=True,
                enforcePeriodicBox=system_manifest.periodic,
            )
            snapshot = self._snapshot_from_state(
                state=state,
                system_manifest=system_manifest,
                snapshot_identifier=_stable_identifier(
                    "molecular-snapshot-minimized",
                    system_manifest.system_identifier,
                    invocation.context.execution_id,
                ),
                frame_index=0,
                step=0,
            )
        finally:
            del context
            del integrator
        snapshot_payload = snapshot.to_canonical_json().encode("utf-8")
        snapshot_artifact = self._write_artifact(
            invocation=invocation,
            artifact_type="molecular_snapshot",
            media_type="application/vnd.pulsate.molecular-snapshot+json",
            payload=snapshot_payload,
            identifier_prefix="molecular-snapshot",
            parents=(environment_reference, system_reference),
            metadata={
                "system_identifier": system_manifest.system_identifier,
                "state": "minimized",
                "platform": platform_name,
            },
        )
        minimization = MolecularMinimizationResult(
            schema_version=_SCHEMA_VERSION,
            minimization_identifier=_stable_identifier(
                "molecular-minimization-result",
                system_manifest.system_identifier,
                invocation.context.execution_id,
                settings.fingerprint,
            ),
            system_identifier=system_manifest.system_identifier,
            environment_identifier=environment.environment_identifier,
            settings=settings,
            snapshot_identifier=snapshot.snapshot_identifier,
            initial_potential_energy_kj_per_mol=initial_potential,
            final_potential_energy_kj_per_mol=snapshot.potential_energy_kj_per_mol,
            energy_change_kj_per_mol=_rounded(
                snapshot.potential_energy_kj_per_mol - initial_potential
            ),
        )
        minimization_payload = minimization.to_canonical_json().encode("utf-8")
        minimization_artifact = self._write_artifact(
            invocation=invocation,
            artifact_type="molecular_minimization_result",
            media_type="application/vnd.pulsate.molecular-minimization-result+json",
            payload=minimization_payload,
            identifier_prefix="molecular-minimization-result",
            parents=(environment_reference, system_reference, snapshot_artifact),
            metadata={
                "system_identifier": system_manifest.system_identifier,
                "snapshot_identifier": snapshot.snapshot_identifier,
                "platform": platform_name,
            },
        )
        lineage = tuple(
            [
                self._lineage(invocation, parent, snapshot_artifact, "minimized_into")
                for parent in (environment_reference, system_reference)
            ]
            + [
                self._lineage(invocation, parent, minimization_artifact, "recorded_in")
                for parent in (
                    environment_reference,
                    system_reference,
                    snapshot_artifact,
                )
            ]
        )
        return self._success(
            invocation,
            artifacts=(snapshot_artifact, minimization_artifact),
            lineage=lineage,
            diagnostics={
                "minimization_identifier": minimization.minimization_identifier,
                "snapshot_identifier": snapshot.snapshot_identifier,
                "initial_potential_energy_kj_per_mol": initial_potential,
                "final_potential_energy_kj_per_mol": (
                    snapshot.potential_energy_kj_per_mol
                ),
            },
            platform=platform_name,
        )

    def _run_dynamics(self, invocation: CapabilityInvocation) -> CapabilityResult:
        inputs = self._inputs_by_type(invocation)
        system_reference = self._require_input(inputs, "molecular_simulation_system")
        if len(inputs) != 2:
            raise ValueError(
                "Molecular dynamics requires a system and one initial state."
            )
        system_manifest, system = self._load_system(system_reference)
        settings = self._dynamics_settings(invocation)
        if settings.ensemble == "npt" and not system_manifest.periodic:
            raise ValueError("NPT dynamics requires a periodic simulation system.")
        positions, velocities, box, initial_snapshot_identifier, state_parents = (
            self._initial_state_inputs(inputs, system_manifest)
        )
        simulated_system, integrator = self._integrator_and_system(system, settings)
        platform, properties = self._platform(
            settings.platform,
            settings.platform_properties,
        )
        openmm, _, _ = _openmm_modules()
        context = openmm.Context(simulated_system, integrator, platform, properties)
        try:
            self._configure_context_initial_state(
                context,
                positions=positions,
                velocities=velocities,
                periodic_box=box,
                settings=settings,
            )
            integrator.step(settings.steps)
            state = context.getState(
                getPositions=True,
                getVelocities=True,
                getEnergy=True,
                enforcePeriodicBox=system_manifest.periodic,
            )
            snapshot = self._snapshot_from_state(
                state=state,
                system_manifest=system_manifest,
                snapshot_identifier=_stable_identifier(
                    "molecular-snapshot-dynamics",
                    system_manifest.system_identifier,
                    invocation.context.execution_id,
                    settings.steps,
                ),
                frame_index=0,
                step=settings.steps,
            )
        finally:
            del context
            del integrator
        snapshot_payload = snapshot.to_canonical_json().encode("utf-8")
        all_parents = (system_reference, *state_parents)
        snapshot_artifact = self._write_artifact(
            invocation=invocation,
            artifact_type="molecular_snapshot",
            media_type="application/vnd.pulsate.molecular-snapshot+json",
            payload=snapshot_payload,
            identifier_prefix="molecular-snapshot",
            parents=all_parents,
            metadata={
                "system_identifier": system_manifest.system_identifier,
                "state": "dynamics_final",
                "platform": settings.platform,
            },
        )
        run = MolecularDynamicsRun(
            schema_version=_SCHEMA_VERSION,
            run_identifier=_stable_identifier(
                "molecular-dynamics-run",
                system_manifest.system_identifier,
                invocation.context.execution_id,
                settings.fingerprint,
            ),
            system_identifier=system_manifest.system_identifier,
            settings=settings,
            initial_snapshot_identifier=initial_snapshot_identifier,
            final_snapshot_identifier=snapshot.snapshot_identifier,
            completed_steps=settings.steps,
            simulated_time_ps=_rounded(settings.steps * settings.timestep_fs / 1000.0),
        )
        run_payload = run.to_canonical_json().encode("utf-8")
        run_artifact = self._write_artifact(
            invocation=invocation,
            artifact_type="molecular_dynamics_run",
            media_type="application/vnd.pulsate.molecular-dynamics-run+json",
            payload=run_payload,
            identifier_prefix="molecular-dynamics-run",
            parents=(*all_parents, snapshot_artifact),
            metadata={
                "system_identifier": system_manifest.system_identifier,
                "ensemble": settings.ensemble,
                "completed_steps": settings.steps,
                "platform": settings.platform,
            },
        )
        lineage = tuple(
            [
                self._lineage(invocation, parent, snapshot_artifact, "advanced_into")
                for parent in all_parents
            ]
            + [
                self._lineage(invocation, parent, run_artifact, "recorded_in")
                for parent in (*all_parents, snapshot_artifact)
            ]
        )
        return self._success(
            invocation,
            artifacts=(snapshot_artifact, run_artifact),
            lineage=lineage,
            diagnostics={
                "run_identifier": run.run_identifier,
                "completed_steps": settings.steps,
                "final_snapshot_identifier": snapshot.snapshot_identifier,
            },
            platform=settings.platform,
        )

    def _generate_trajectory(
        self, invocation: CapabilityInvocation
    ) -> CapabilityResult:
        inputs = self._inputs_by_type(invocation)
        system_reference = self._require_input(inputs, "molecular_simulation_system")
        if len(inputs) != 2:
            raise ValueError(
                "Trajectory generation requires a system and one initial state."
            )
        system_manifest, system = self._load_system(system_reference)
        settings = self._dynamics_settings(invocation)
        if settings.ensemble == "npt" and not system_manifest.periodic:
            raise ValueError("NPT trajectory generation requires a periodic system.")
        report_interval = self._integer_parameter(
            invocation,
            "report_interval_steps",
            minimum=1,
            maximum=settings.steps,
            required=True,
        )
        expected_frames = 2 + (settings.steps - 1) // report_interval
        if expected_frames > _MAXIMUM_TRAJECTORY_FRAMES:
            raise ValueError("Requested trajectory exceeds the frame limit.")
        if (
            expected_frames * system_manifest.particle_count
            > _MAXIMUM_TRAJECTORY_PARTICLE_FRAMES
        ):
            raise ValueError("Requested trajectory exceeds the particle-frame limit.")
        positions, velocities, box, _, state_parents = self._initial_state_inputs(
            inputs,
            system_manifest,
        )
        simulated_system, integrator = self._integrator_and_system(system, settings)
        platform, properties = self._platform(
            settings.platform,
            settings.platform_properties,
        )
        openmm, _, _ = _openmm_modules()
        context = openmm.Context(simulated_system, integrator, platform, properties)
        trajectory_identifier = _stable_identifier(
            "molecular-trajectory",
            system_manifest.system_identifier,
            invocation.context.execution_id,
            settings.fingerprint,
            report_interval,
        )
        snapshots: list[MolecularSnapshot] = []
        try:
            self._configure_context_initial_state(
                context,
                positions=positions,
                velocities=velocities,
                periodic_box=box,
                settings=settings,
            )
            initial_state = context.getState(
                getPositions=True,
                getVelocities=True,
                getEnergy=True,
                enforcePeriodicBox=system_manifest.periodic,
            )
            snapshots.append(
                self._snapshot_from_state(
                    state=initial_state,
                    system_manifest=system_manifest,
                    snapshot_identifier=_stable_identifier(
                        "molecular-trajectory-frame",
                        trajectory_identifier,
                        0,
                    ),
                    frame_index=0,
                    step=0,
                    source_trajectory_identifier=trajectory_identifier,
                )
            )
            completed = 0
            frame_index = 1
            while completed < settings.steps:
                increment = min(report_interval, settings.steps - completed)
                integrator.step(increment)
                completed += increment
                state = context.getState(
                    getPositions=True,
                    getVelocities=True,
                    getEnergy=True,
                    enforcePeriodicBox=system_manifest.periodic,
                )
                snapshots.append(
                    self._snapshot_from_state(
                        state=state,
                        system_manifest=system_manifest,
                        snapshot_identifier=_stable_identifier(
                            "molecular-trajectory-frame",
                            trajectory_identifier,
                            frame_index,
                            completed,
                        ),
                        frame_index=frame_index,
                        step=completed,
                        source_trajectory_identifier=trajectory_identifier,
                    )
                )
                frame_index += 1
        finally:
            del context
            del integrator
        trajectory = MolecularTrajectory(
            schema_version=_SCHEMA_VERSION,
            trajectory_identifier=trajectory_identifier,
            system_identifier=system_manifest.system_identifier,
            settings=settings,
            report_interval_steps=report_interval,
            snapshots=tuple(snapshots),
        )
        payload = trajectory.to_canonical_json().encode("utf-8")
        parents = (system_reference, *state_parents)
        artifact = self._write_artifact(
            invocation=invocation,
            artifact_type="molecular_trajectory",
            media_type="application/vnd.pulsate.molecular-trajectory+json",
            payload=payload,
            identifier_prefix="molecular-trajectory",
            parents=parents,
            metadata={
                "system_identifier": system_manifest.system_identifier,
                "ensemble": settings.ensemble,
                "frame_count": len(snapshots),
                "completed_steps": settings.steps,
                "platform": settings.platform,
            },
        )
        lineage = tuple(
            self._lineage(invocation, parent, artifact, "sampled_into")
            for parent in parents
        )
        return self._success(
            invocation,
            artifacts=(artifact,),
            lineage=lineage,
            diagnostics={
                "trajectory_identifier": trajectory_identifier,
                "frame_count": len(snapshots),
                "completed_steps": settings.steps,
            },
            platform=settings.platform,
        )

    def _extract_snapshot(self, invocation: CapabilityInvocation) -> CapabilityResult:
        inputs = self._inputs_by_type(invocation)
        if set(inputs) != {"molecular_trajectory"}:
            raise ValueError("Snapshot extraction requires one trajectory artifact.")
        trajectory_reference = self._require_input(inputs, "molecular_trajectory")
        trajectory = MolecularTrajectory.model_validate_json(
            self._read_payload(trajectory_reference)
        )
        frame_index = self._integer_parameter(
            invocation,
            "frame_index",
            minimum=0,
            maximum=len(trajectory.snapshots) - 1,
            required=True,
        )
        source = trajectory.snapshots[frame_index]
        snapshot = source.model_copy(
            update={
                "snapshot_identifier": _stable_identifier(
                    "molecular-snapshot-extracted",
                    trajectory.trajectory_identifier,
                    frame_index,
                    invocation.context.execution_id,
                ),
                "frame_index": 0,
                "source_trajectory_identifier": trajectory.trajectory_identifier,
            }
        )
        payload = snapshot.to_canonical_json().encode("utf-8")
        artifact = self._write_artifact(
            invocation=invocation,
            artifact_type="molecular_snapshot",
            media_type="application/vnd.pulsate.molecular-snapshot+json",
            payload=payload,
            identifier_prefix="molecular-snapshot",
            parents=(trajectory_reference,),
            metadata={
                "system_identifier": trajectory.system_identifier,
                "source_trajectory_identifier": trajectory.trajectory_identifier,
                "source_frame_index": frame_index,
            },
        )
        return self._success(
            invocation,
            artifacts=(artifact,),
            lineage=(
                self._lineage(
                    invocation,
                    trajectory_reference,
                    artifact,
                    "extracted_into",
                ),
            ),
            diagnostics={
                "snapshot_identifier": snapshot.snapshot_identifier,
                "source_frame_index": frame_index,
            },
        )

    def _summarize_ensemble(self, invocation: CapabilityInvocation) -> CapabilityResult:
        inputs = self._inputs_by_type(invocation)
        if set(inputs) != {"molecular_trajectory"}:
            raise ValueError("Ensemble summarization requires one trajectory artifact.")
        trajectory_reference = self._require_input(inputs, "molecular_trajectory")
        trajectory = MolecularTrajectory.model_validate_json(
            self._read_payload(trajectory_reference)
        )
        potential = [
            snapshot.potential_energy_kj_per_mol for snapshot in trajectory.snapshots
        ]
        kinetic = [
            snapshot.kinetic_energy_kj_per_mol for snapshot in trajectory.snapshots
        ]
        temperatures = [
            snapshot.instantaneous_temperature_kelvin
            for snapshot in trajectory.snapshots
            if snapshot.instantaneous_temperature_kelvin is not None
        ]
        summary = MolecularEnsembleSummary(
            schema_version=_SCHEMA_VERSION,
            ensemble_identifier=_stable_identifier(
                "molecular-ensemble-summary",
                trajectory.trajectory_identifier,
                trajectory.fingerprint,
            ),
            trajectory_identifier=trajectory.trajectory_identifier,
            system_identifier=trajectory.system_identifier,
            ensemble=trajectory.settings.ensemble,
            frame_count=len(trajectory.snapshots),
            first_step=trajectory.snapshots[0].step,
            last_step=trajectory.snapshots[-1].step,
            mean_potential_energy_kj_per_mol=_rounded(statistics.fmean(potential)),
            standard_deviation_potential_energy_kj_per_mol=_rounded(
                statistics.pstdev(potential)
            ),
            mean_kinetic_energy_kj_per_mol=_rounded(statistics.fmean(kinetic)),
            standard_deviation_kinetic_energy_kj_per_mol=_rounded(
                statistics.pstdev(kinetic)
            ),
            mean_temperature_kelvin=(
                _rounded(statistics.fmean(temperatures)) if temperatures else None
            ),
            standard_deviation_temperature_kelvin=(
                _rounded(statistics.pstdev(temperatures)) if temperatures else None
            ),
        )
        payload = summary.to_canonical_json().encode("utf-8")
        artifact = self._write_artifact(
            invocation=invocation,
            artifact_type="molecular_ensemble_summary",
            media_type="application/vnd.pulsate.molecular-ensemble-summary+json",
            payload=payload,
            identifier_prefix="molecular-ensemble-summary",
            parents=(trajectory_reference,),
            metadata={
                "trajectory_identifier": trajectory.trajectory_identifier,
                "ensemble": trajectory.settings.ensemble,
                "frame_count": len(trajectory.snapshots),
            },
        )
        return self._success(
            invocation,
            artifacts=(artifact,),
            lineage=(
                self._lineage(
                    invocation,
                    trajectory_reference,
                    artifact,
                    "summarized_into",
                ),
            ),
            diagnostics={
                "ensemble_identifier": summary.ensemble_identifier,
                "frame_count": summary.frame_count,
            },
        )
