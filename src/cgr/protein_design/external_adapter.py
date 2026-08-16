"""Hardened subprocess boundary for replaceable protein-design engines.

Each configured engine is launched without a shell and communicates through a
small JSON manifest.  This keeps RFdiffusion, ProteinMPNN, Boltz, or later
replacements outside the canonical CGR contracts while preserving exact input
hashes, output hashes, lineage, engine identity, and execution evidence.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from cgr.kernel.contracts import CapabilityVersion, ExecutionStatus, HealthStatus
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
from cgr.science.canonical import validate_bounded_metadata, validate_identifier

BACKBONE_GENERATE = "protein.backbone_generate"
SEQUENCE_DESIGN = "protein.sequence_design"
STRUCTURE_PREDICT = "protein.structure_predict"

_VERSION = CapabilityVersion(major=1, minor=0, patch=0)
_MAX_OUTPUT_BYTES = 512 * 1024 * 1024
_MAX_OUTPUT_COUNT = 256


class ProteinArtifactPayloadStore(Protocol):
    def read(self, reference: ArtifactReference) -> bytes: ...

    def write(self, reference: ArtifactReference, payload: bytes) -> None: ...


@dataclass(frozen=True, slots=True)
class ExternalProteinEngineConfiguration:
    """Exact installed runner identity; no shell command interpolation."""

    adapter_identifier: str
    engine_identifier: str
    engine_version: str
    capability_name: str
    command: tuple[str, ...]
    accepted_artifact_types: tuple[str, ...]
    produced_artifact_types: tuple[str, ...]
    timeout_seconds: int = 3600
    execution_target: str = "local_gpu"

    def __post_init__(self) -> None:
        validate_identifier(self.adapter_identifier)
        validate_identifier(self.engine_identifier)
        validate_identifier(self.capability_name)
        validate_identifier(self.execution_target)
        if not self.engine_version.strip():
            raise ValueError("External protein engines require an exact version.")
        if not self.command or any(not item for item in self.command):
            raise ValueError("External protein engines require an argv command.")
        if not 1 <= self.timeout_seconds <= 604_800:
            raise ValueError("External protein engine timeout is out of bounds.")


class _OutputManifestItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_type: str
    media_type: str
    path: str = Field(min_length=1, max_length=4096)
    parent_artifact_identifiers: tuple[str, ...] = ()
    metadata: dict[str, object] = Field(default_factory=dict)

    @field_validator("artifact_type", "parent_artifact_identifiers")
    @classmethod
    def identifiers(cls, value):
        if isinstance(value, tuple):
            return tuple(validate_identifier(item) for item in value)
        return validate_identifier(value)

    @field_validator("metadata")
    @classmethod
    def bounded_metadata(cls, value: dict[str, object]) -> dict[str, object]:
        return validate_bounded_metadata(value)


class _OutputManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    outputs: tuple[_OutputManifestItem, ...] = Field(
        min_length=1,
        max_length=_MAX_OUTPUT_COUNT,
    )
    diagnostics: dict[str, object] = Field(default_factory=dict)

    @field_validator("diagnostics")
    @classmethod
    def bounded_diagnostics(cls, value: dict[str, object]) -> dict[str, object]:
        return validate_bounded_metadata(value)


def _envelope(configuration: ExternalProteinEngineConfiguration) -> CapabilityExecutionEnvelope:
    descriptor = CapabilityDescriptor(
        capability_name=configuration.capability_name,
        version=_VERSION,
        accepted_artifact_types=configuration.accepted_artifact_types,
        produced_artifact_types=configuration.produced_artifact_types,
        required_tools=(configuration.engine_identifier,),
        required_runtime="external_process",
        determinism=DeterminismClassification.NONDETERMINISTIC,
    )
    return CapabilityExecutionEnvelope(
        descriptor=descriptor,
        supported_objective_types=("de_novo_protein_design",),
        execution_targets=(configuration.execution_target,),
        known_limitations=(
            "Generated structures and sequences are computational candidates, not experimental evidence.",
            "Reproducibility depends on the pinned engine, weights, runtime, inputs, and random seed.",
        ),
        metadata={
            "scientific_domain": "protein_design",
            "adapter_family": "external_json_manifest",
        },
    )


class ExternalProteinEngineAdapter:
    """Execute one pinned protein engine through a validated file manifest."""

    def __init__(
        self,
        store: ProteinArtifactPayloadStore,
        configuration: ExternalProteinEngineConfiguration,
    ) -> None:
        self._store = store
        self.configuration = configuration
        self._declaration = ScientificEngineAdapterDeclaration(
            adapter_identifier=configuration.adapter_identifier,
            adapter_version=_VERSION,
            engine=ScientificEngineIdentity(
                engine_identifier=configuration.engine_identifier,
                engine_version=configuration.engine_version,
            ),
            capabilities=(_envelope(configuration),),
            metadata={"native_objects_public": False},
        )

    @property
    def declaration(self) -> ScientificEngineAdapterDeclaration:
        return self._declaration

    def health(self) -> ScientificEngineHealthReport:
        executable = self.configuration.command[0]
        resolved = (
            str(Path(executable).resolve())
            if Path(executable).is_file()
            else shutil.which(executable)
        )
        if resolved is None:
            return ScientificEngineHealthReport(
                status=HealthStatus.UNAVAILABLE,
                reason_code="external_engine_runner_missing",
                message="The configured protein engine runner is unavailable.",
                diagnostics={
                    "engine_identifier": self.configuration.engine_identifier,
                    "engine_version": self.configuration.engine_version,
                },
            )
        return ScientificEngineHealthReport(
            status=HealthStatus.HEALTHY,
            diagnostics={
                "engine_identifier": self.configuration.engine_identifier,
                "engine_version": self.configuration.engine_version,
                "runner_resolved": True,
            },
        )

    @staticmethod
    def _failure(code: str, message: str, *, retryable: bool = False) -> CapabilityResult:
        return CapabilityResult(
            status=ExecutionStatus.FAILED,
            failure=FailureInformation(
                code=code,
                message=message,
                retryable=retryable,
            ),
        )

    def invoke(self, invocation: CapabilityInvocation) -> CapabilityResult:
        descriptor = self._declaration.capabilities[0].descriptor
        if invocation.capability != descriptor:
            return self._failure(
                "capability_not_declared",
                "The requested protein capability is not declared by this adapter.",
            )
        health = self.health()
        if health.status is not HealthStatus.HEALTHY:
            return self._failure(
                health.reason_code or "external_engine_unavailable",
                health.message or "The external protein engine is unavailable.",
            )
        try:
            return self._execute(invocation)
        except subprocess.TimeoutExpired:
            return self._failure(
                "external_engine_timeout",
                "The external protein engine exceeded its configured time limit.",
                retryable=True,
            )
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            return self._failure(
                "external_engine_invalid_result",
                "The external protein engine did not return valid, bounded evidence.",
            )

    def _execute(self, invocation: CapabilityInvocation) -> CapabilityResult:
        with tempfile.TemporaryDirectory(prefix="cgr-protein-engine-") as directory:
            root = Path(directory).resolve()
            input_root = root / "inputs"
            output_root = root / "outputs"
            input_root.mkdir()
            output_root.mkdir()
            inputs = []
            input_by_identifier = {
                item.artifact_identifier: item for item in invocation.input_artifacts
            }
            for reference in invocation.input_artifacts:
                payload = self._store.read(reference)
                if hashlib.sha256(payload).hexdigest() != reference.content_sha256:
                    raise ValueError("Protein engine input failed content verification.")
                path = input_root / reference.artifact_identifier
                path.write_bytes(payload)
                inputs.append(
                    {
                        "artifact_identifier": reference.artifact_identifier,
                        "artifact_type": reference.artifact_type,
                        "media_type": reference.media_type,
                        "content_sha256": reference.content_sha256,
                        "path": str(path),
                    }
                )
            request_path = root / "request.json"
            manifest_path = root / "manifest.json"
            request_path.write_text(
                json.dumps(
                    {
                        "protocol_version": "pulsate-protein-engine-v1",
                        "capability_name": invocation.capability.capability_name,
                        "engine_identifier": self.configuration.engine_identifier,
                        "engine_version": self.configuration.engine_version,
                        "execution_identifier": invocation.context.execution_id,
                        "parameters": dict(invocation.parameters),
                        "inputs": inputs,
                        "output_directory": str(output_root),
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
            environment = {
                name: value
                for name in (
                    "PATH",
                    "HOME",
                    "USERPROFILE",
                    "TEMP",
                    "TMP",
                    "TMPDIR",
                    "CUDA_VISIBLE_DEVICES",
                    "HF_HOME",
                    "TORCH_HOME",
                )
                if (value := os.environ.get(name)) is not None
            }
            completed = subprocess.run(
                (
                    *self.configuration.command,
                    "--request",
                    str(request_path),
                    "--manifest",
                    str(manifest_path),
                ),
                cwd=root,
                env=environment,
                check=False,
                capture_output=True,
                timeout=self.configuration.timeout_seconds,
            )
            if completed.returncode != 0:
                return self._failure(
                    "external_engine_execution_failed",
                    "The external protein engine exited without successful evidence.",
                    retryable=True,
                )
            manifest = _OutputManifest.model_validate_json(
                manifest_path.read_text(encoding="utf-8")
            )
            outputs: list[ArtifactReference] = []
            lineage: list[ArtifactLineageEdge] = []
            seen: set[tuple[str, str]] = set()
            total_bytes = 0
            allowed_output_types = set(self.configuration.produced_artifact_types)
            for item in manifest.outputs:
                if item.artifact_type not in allowed_output_types:
                    raise ValueError("External engine returned an undeclared artifact type.")
                path = Path(item.path).resolve()
                if root not in path.parents or not path.is_file():
                    raise ValueError("External engine output escaped its job directory.")
                payload = path.read_bytes()
                total_bytes += len(payload)
                if total_bytes > _MAX_OUTPUT_BYTES:
                    raise ValueError("External protein engine output exceeded its limit.")
                digest = hashlib.sha256(payload).hexdigest()
                identity = (item.artifact_type, digest)
                if identity in seen:
                    raise ValueError("External engine returned duplicate content identities.")
                seen.add(identity)
                parent_ids = item.parent_artifact_identifiers or tuple(input_by_identifier)
                if not set(parent_ids).issubset(input_by_identifier):
                    raise ValueError("External engine declared an unknown parent artifact.")
                parents = tuple(input_by_identifier[name] for name in parent_ids)
                reference = ArtifactReference(
                    artifact_identifier=f"{item.artifact_type}-{digest[:32]}",
                    schema_version=_VERSION,
                    artifact_type=item.artifact_type,
                    media_type=item.media_type,
                    content_sha256=digest,
                    byte_size=len(payload),
                    metadata={
                        **item.metadata,
                        "engine_identifier": self.configuration.engine_identifier,
                        "engine_version": self.configuration.engine_version,
                    },
                    provenance=CreationProvenance(
                        producer=invocation.capability.capability_name,
                        producer_version=invocation.capability.version,
                        execution_identifier=invocation.context.execution_id,
                        source=self.configuration.engine_identifier,
                    ),
                    parents=tuple(parent.pointer for parent in parents),
                )
                self._store.write(reference, payload)
                outputs.append(reference)
                lineage.extend(
                    ArtifactLineageEdge(
                        source=parent.pointer,
                        destination=reference.pointer,
                        relationship_type="computed_from",
                        producing_capability=invocation.capability.capability_name,
                        producing_capability_version=invocation.capability.version,
                        execution_identifier=invocation.context.execution_id,
                    )
                    for parent in parents
                )
            return CapabilityResult(
                status=ExecutionStatus.SUCCESS,
                output_artifacts=tuple(outputs),
                lineage=tuple(lineage),
                diagnostics={
                    **manifest.diagnostics,
                    "engine_identifier": self.configuration.engine_identifier,
                    "engine_version": self.configuration.engine_version,
                    "output_count": len(outputs),
                },
                execution_evidence=ExecutionEvidence(
                    runtime_identifier=self.configuration.execution_target,
                    exit_code=completed.returncode,
                    evidence_artifacts=tuple(item.pointer for item in outputs),
                    details={
                        "engine_identifier": self.configuration.engine_identifier,
                        "engine_version": self.configuration.engine_version,
                        "stdout_sha256": hashlib.sha256(completed.stdout).hexdigest(),
                        "stderr_sha256": hashlib.sha256(completed.stderr).hexdigest(),
                    },
                ),
            )


def _configuration_from_environment(
    environment: Mapping[str, str],
    *,
    prefix: str,
    adapter_identifier: str,
    engine_identifier: str,
    capability_name: str,
    accepted: tuple[str, ...],
    produced: tuple[str, ...],
) -> ExternalProteinEngineConfiguration | None:
    command_value = environment.get(prefix + "_COMMAND_JSON", "").strip()
    version = environment.get(prefix + "_VERSION", "").strip()
    if not command_value and not version:
        return None
    if not command_value or not version:
        raise ValueError(
            f"{prefix}_COMMAND_JSON and {prefix}_VERSION must be configured together."
        )
    command_document = json.loads(command_value)
    if (
        not isinstance(command_document, list)
        or not 1 <= len(command_document) <= 32
        or any(not isinstance(item, str) or not item for item in command_document)
    ):
        raise ValueError(f"{prefix}_COMMAND_JSON must be a bounded JSON argv array.")
    timeout = int(environment.get(prefix + "_TIMEOUT_SECONDS", "3600"))
    execution_target = environment.get(
        prefix + "_EXECUTION_TARGET", "local_gpu"
    ).strip()
    if execution_target not in {"local_gpu", "hpc_batch"}:
        raise ValueError(
            f"{prefix}_EXECUTION_TARGET must be local_gpu or hpc_batch."
        )
    return ExternalProteinEngineConfiguration(
        adapter_identifier=adapter_identifier,
        engine_identifier=engine_identifier,
        engine_version=version,
        capability_name=capability_name,
        command=tuple(command_document),
        accepted_artifact_types=accepted,
        produced_artifact_types=produced,
        timeout_seconds=timeout,
        execution_target=execution_target,
    )


def configured_protein_design_adapters(
    store: ProteinArtifactPayloadStore,
    environment: Mapping[str, str] | None = None,
) -> tuple[ExternalProteinEngineAdapter, ...]:
    """Return only explicitly configured, pinned protein engine adapters."""

    values = environment if environment is not None else os.environ
    definitions = (
        (
            "PULSATE_RFDIFFUSION",
            "adapter.rfdiffusion.backbone",
            "engine.rfdiffusion",
            BACKBONE_GENERATE,
            ("protein_design_specification",),
            ("protein_backbone_candidate",),
        ),
        (
            "PULSATE_PROTEINMPNN",
            "adapter.proteinmpnn.sequence",
            "engine.proteinmpnn",
            SEQUENCE_DESIGN,
            ("protein_backbone_candidate",),
            ("protein_sequence_candidate",),
        ),
        (
            "PULSATE_BOLTZ",
            "adapter.boltz.structure_prediction",
            "engine.boltz",
            STRUCTURE_PREDICT,
            ("protein_sequence_candidate",),
            ("predicted_protein_structure", "protein_prediction_confidence"),
        ),
    )
    adapters = []
    for definition in definitions:
        configuration = _configuration_from_environment(
            values,
            prefix=definition[0],
            adapter_identifier=definition[1],
            engine_identifier=definition[2],
            capability_name=definition[3],
            accepted=definition[4],
            produced=definition[5],
        )
        if configuration is not None:
            adapters.append(ExternalProteinEngineAdapter(store, configuration))
    return tuple(adapters)


__all__ = [
    "BACKBONE_GENERATE",
    "SEQUENCE_DESIGN",
    "STRUCTURE_PREDICT",
    "ExternalProteinEngineAdapter",
    "ExternalProteinEngineConfiguration",
    "configured_protein_design_adapters",
]
