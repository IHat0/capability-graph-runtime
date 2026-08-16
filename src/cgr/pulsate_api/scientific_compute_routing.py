"""Deterministic, evidence-bound compute routing for scientific workflows.

The language model describes scientific requirements.  It never chooses a
machine.  This module maps validated capability identities to execution
targets using an immutable resource snapshot assembled from configured runtime
providers.  External submission remains subject to the workflow's approval
contract.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from cgr.kernel.contracts import CapabilityVersion
from cgr.science import (
    CreationProvenance,
    ResourceAvailabilitySnapshot,
    ResourceQuantity,
)
from cgr.science.canonical import validate_identifier

_VERSION = CapabilityVersion(major=1, minor=0, patch=0)
_PROVENANCE = CreationProvenance(
    producer="pulsate-scientific-compute-router",
    producer_version=_VERSION,
    source="configured-runtime-declarations",
)

LOCAL_CPU = "local_cpu"
LOCAL_GPU = "local_gpu"
HPC_BATCH = "hpc_batch"
LOCAL_SIMULATOR = "local_simulator"
IBM_QUANTUM = "ibm_quantum"

_KNOWN_TARGETS = (
    LOCAL_CPU,
    LOCAL_GPU,
    HPC_BATCH,
    LOCAL_SIMULATOR,
    IBM_QUANTUM,
)


class ScientificComputeRoute(BaseModel):
    """One deterministic assignment decision and its declared support state."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    capability_name: str
    execution_target: str
    available: bool
    authorization_required: bool = False
    reason: str
    compatible_targets: tuple[str, ...]

    @field_validator("capability_name", "execution_target")
    @classmethod
    def identifiers(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("compatible_targets")
    @classmethod
    def targets(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(validate_identifier(item) for item in value)
        if not normalized or len(normalized) != len(set(normalized)):
            raise ValueError("Compatible compute targets must be nonempty and unique.")
        return normalized

    @field_validator("reason")
    @classmethod
    def reason_is_bounded(cls, value: str) -> str:
        normalized = " ".join(value.strip().split())
        if not normalized or len(normalized) > 1024:
            raise ValueError("Compute-route reasons must be nonempty and bounded.")
        return normalized


class ScientificComputeRouter:
    """Select targets from exact capability and runtime declarations.

    ``capability_execution_targets`` is ordered: the first currently available
    target wins.  This enables a configured local-GPU/HPC preference without
    treating either resource as present merely because code knows its name.
    """

    def __init__(
        self,
        *,
        capability_execution_targets: Mapping[str, Iterable[str]] | None = None,
        quantum_capability_provider: Callable[[], Mapping[str, Any]] | None = None,
        execution_environment: str = "pulsate_scientific_runtime",
    ) -> None:
        self.execution_environment = validate_identifier(execution_environment)
        self.quantum_capability_provider = quantum_capability_provider
        normalized: dict[str, tuple[str, ...]] = {}
        for name, targets in (capability_execution_targets or {}).items():
            capability_name = validate_identifier(name)
            target_values = tuple(validate_identifier(item) for item in targets)
            if not target_values or len(target_values) != len(set(target_values)):
                raise ValueError(
                    "Capability compute targets must be nonempty and unique."
                )
            unknown = set(target_values) - set(_KNOWN_TARGETS)
            if unknown:
                raise ValueError(
                    "Unsupported scientific execution targets: "
                    + ", ".join(sorted(unknown))
                )
            normalized[capability_name] = target_values
        self._capability_targets = dict(sorted(normalized.items()))

    def _quantum_capability(self) -> Mapping[str, Any]:
        if self.quantum_capability_provider is None:
            return {}
        try:
            value = self.quantum_capability_provider()
        except Exception:
            return {}
        return value if isinstance(value, Mapping) else {}

    def resource_snapshot(self) -> ResourceAvailabilitySnapshot:
        """Capture one immutable declared resource view without dispatching work."""

        quantum = self._quantum_capability()
        declared_quantum_targets = quantum.get("execution_targets", ())
        if not isinstance(declared_quantum_targets, (list, tuple)):
            declared_quantum_targets = ()
        quantum_targets = {
            item
            for item in declared_quantum_targets
            if isinstance(item, str) and item in {LOCAL_SIMULATOR, IBM_QUANTUM}
        }

        external_targets = {
            target
            for targets in self._capability_targets.values()
            for target in targets
            if target in {LOCAL_GPU, HPC_BATCH}
        }
        available = {LOCAL_CPU, LOCAL_SIMULATOR, *external_targets}
        available.update(quantum_targets)

        reasons: dict[str, str] = {}
        if LOCAL_GPU not in available:
            reasons[LOCAL_GPU] = (
                "No healthy local-GPU scientific executor is configured."
            )
        if HPC_BATCH not in available:
            reasons[HPC_BATCH] = "No healthy HPC dispatcher is configured."
        if IBM_QUANTUM not in available:
            ibm = quantum.get("ibm_quantum")
            reason = ibm.get("reason") if isinstance(ibm, Mapping) else None
            reasons[IBM_QUANTUM] = (
                str(reason).strip()
                if isinstance(reason, str) and reason.strip()
                else "IBM Quantum execution is not currently declared available."
            )

        observed_at = datetime.now(UTC)
        identity = observed_at.strftime("%Y%m%dT%H%M%S%fZ")
        return ResourceAvailabilitySnapshot(
            snapshot_identifier=f"scientific-resources-{identity}",
            schema_version=_VERSION,
            observed_at=observed_at,
            execution_environment=self.execution_environment,
            available_resources=(
                ResourceQuantity(
                    dimension="local_cpu_runtime",
                    unit="available",
                    value=True,
                ),
            ),
            available_execution_targets=tuple(sorted(available)),
            unavailable_reasons=reasons,
            provenance=_PROVENANCE,
        )

    def route(
        self,
        capability_name: str,
        *,
        requested_quantum_target: str,
        authorization_required: bool,
        resources: ResourceAvailabilitySnapshot,
    ) -> ScientificComputeRoute:
        """Choose one target; never infer availability from a target name."""

        name = validate_identifier(capability_name)
        if name == "quantum.ibm_execution_prepare":
            compatible = (IBM_QUANTUM,)
            reason = (
                "The validated objective requests IBM hardware; this node is an "
                "authorization boundary and does not itself submit a job."
            )
        elif name.startswith(("quantum.", "quantum_workflow.")):
            compatible = (LOCAL_SIMULATOR,)
            reason = (
                "Quantum construction and preflight execute on the declared local "
                "simulator before any optional hardware handoff."
            )
        elif name in self._capability_targets:
            compatible = self._capability_targets[name]
            reason = (
                "The selected external scientific executor declares these ordered "
                "targets for this exact capability."
            )
        else:
            compatible = (LOCAL_CPU,)
            reason = (
                "The registered in-process scientific capability is assigned to "
                "the local CPU runtime."
            )

        # An explicit non-IBM objective can never acquire an IBM route merely
        # because a provider is available.
        if IBM_QUANTUM in compatible and requested_quantum_target != IBM_QUANTUM:
            raise ValueError(
                "IBM routing requires an IBM-targeted scientific objective."
            )

        available_targets = set(resources.available_execution_targets)
        selected = next(
            (target for target in compatible if target in available_targets),
            compatible[0],
        )
        available = selected in available_targets
        if not available:
            reason += " " + resources.unavailable_reasons.get(
                selected,
                "The target has no declared availability evidence.",
            )
        return ScientificComputeRoute(
            capability_name=name,
            execution_target=selected,
            available=available,
            authorization_required=authorization_required,
            reason=reason,
            compatible_targets=compatible,
        )

    def summary(self) -> dict[str, object]:
        snapshot = self.resource_snapshot()
        return {
            "available": True,
            "policy": "capability_requirements_and_declared_resources",
            "automatic_selection": True,
            "language_model_selects_hardware": False,
            "external_submission_requires_authorization": True,
            "resource_snapshot_identifier": snapshot.snapshot_identifier,
            "resource_snapshot_fingerprint": snapshot.fingerprint,
            "available_execution_targets": snapshot.available_execution_targets,
            "unavailable_reasons": snapshot.unavailable_reasons,
            "capability_target_preferences": self._capability_targets,
        }


__all__ = [
    "HPC_BATCH",
    "IBM_QUANTUM",
    "LOCAL_CPU",
    "LOCAL_GPU",
    "LOCAL_SIMULATOR",
    "ScientificComputeRoute",
    "ScientificComputeRouter",
]
