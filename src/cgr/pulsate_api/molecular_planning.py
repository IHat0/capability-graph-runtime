"""Read-only molecular-project planning service for the Pulsate API."""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping, Sized
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from cgr.kernel.contracts import CapabilityVersion
from cgr.molecular import (
    MolecularArtifactRepository,
    MolecularArtifactRepositoryError,
    MolecularPlanningProjectionError,
    MolecularProject,
    MolecularTopologyIndex,
    project_molecular_planning_facts,
)
from cgr.science import (
    CandidateResearchPlan,
    CapabilityExecutionEnvelope,
    CreationProvenance,
    ResourceAvailabilitySnapshot,
    ScientificCapabilityCatalog,
    ScientificCapabilityNotFoundError,
    ScientificObjective,
    construct_candidate_research_plan,
)
from cgr.science.canonical import validate_identifier
from cgr.science.planning import PlanningConstraints


MOLECULAR_PLANNING_REQUEST_MAXIMUM_BYTES = 2 * 1024 * 1024
MOLECULAR_PLANNING_TOPOLOGY_MAXIMUM_BYTES = 128 * 1024 * 1024
MOLECULAR_PLANNING_CATALOGUE_MAXIMUM_CAPABILITIES = 256

_OBJECTIVE_REFERENCE_MAXIMUM_COUNT = 4096
_OBJECTIVE_DECLARATION_MAXIMUM_COUNT = 512
_PLANNING_DECLARATION_MAXIMUM_COUNT = 512
_RESOURCE_DECLARATION_MAXIMUM_COUNT = 1024
_MAXIMUM_WALL_TIME_SECONDS = 315_576_000.0


def _require_bounded_collection(
    container: Mapping[str, object],
    field: str,
    *,
    maximum: int,
) -> None:
    value = container.get(field)
    if isinstance(value, Sized) and not isinstance(value, (str, bytes)):
        if len(value) > maximum:
            raise ValueError(
                "Molecular planning request contains too many declarations."
            )


class MolecularPlanningError(ValueError):
    """Base error for controlled read-only molecular planning failures."""


class MolecularPlanningNotFoundError(MolecularPlanningError):
    """Raised when a requested project or capability identity is absent."""


class MolecularPlanningProjectNotFoundError(MolecularPlanningNotFoundError):
    """Raised when the requested molecular project is absent."""


class MolecularPlanningCapabilityNotFoundError(MolecularPlanningNotFoundError):
    """Raised when an exact requested capability is absent."""


class MolecularPlanningRequestError(MolecularPlanningError):
    """Raised when request identities do not resolve within the project."""


class MolecularPlanningIntegrityError(MolecularPlanningError):
    """Raised when project or topology evidence fails integrity validation."""


class MolecularPlanningUnavailableError(MolecularPlanningError):
    """Raised when repository-backed planning is unavailable."""


class RequestedCapabilityIdentity(BaseModel):
    """Exact catalogue identity requested for one planning evaluation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    capability_name: str
    capability_version: CapabilityVersion

    @field_validator("capability_name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return validate_identifier(value, label="capability name")


class MolecularPlanningRequest(BaseModel):
    """Existing canonical inputs for a pure planning computation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    objective: ScientificObjective
    constraints: PlanningConstraints
    resources: ResourceAvailabilitySnapshot
    requested_execution_target: str | None = None
    selected_capabilities: tuple[RequestedCapabilityIdentity, ...] = Field(
        default=(),
        max_length=MOLECULAR_PLANNING_CATALOGUE_MAXIMUM_CAPABILITIES,
    )

    @model_validator(mode="before")
    @classmethod
    def validate_request_collection_bounds(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        objective = value.get("objective")
        if isinstance(objective, Mapping):
            for field in (
                "molecular_system_identifiers",
                "structure_identifiers",
                "structural_region_identifiers",
            ):
                _require_bounded_collection(
                    objective,
                    field,
                    maximum=_OBJECTIVE_REFERENCE_MAXIMUM_COUNT,
                )
            for field in (
                "comparison_state_identifiers",
                "requested_property_identifiers",
                "scientific_constraints",
                "stopping_criteria",
                "assumptions",
            ):
                _require_bounded_collection(
                    objective,
                    field,
                    maximum=_OBJECTIVE_DECLARATION_MAXIMUM_COUNT,
                )
        constraints = value.get("constraints")
        if isinstance(constraints, Mapping):
            for field in (
                "permitted_execution_targets",
                "forbidden_execution_targets",
                "resource_ceilings",
                "cost_ceilings",
                "required_verifications",
                "approval_requirements",
                "policy_notes",
            ):
                _require_bounded_collection(
                    constraints,
                    field,
                    maximum=_PLANNING_DECLARATION_MAXIMUM_COUNT,
                )
        resources = value.get("resources")
        if isinstance(resources, Mapping):
            for field in (
                "available_resources",
                "available_execution_targets",
                "unavailable_reasons",
            ):
                _require_bounded_collection(
                    resources,
                    field,
                    maximum=_RESOURCE_DECLARATION_MAXIMUM_COUNT,
                )
        _require_bounded_collection(
            value,
            "selected_capabilities",
            maximum=MOLECULAR_PLANNING_CATALOGUE_MAXIMUM_CAPABILITIES,
        )
        return value

    @field_validator("requested_execution_target")
    @classmethod
    def validate_target(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_identifier(value, label="execution target")

    @field_validator("selected_capabilities")
    @classmethod
    def order_capabilities(
        cls,
        value: tuple[RequestedCapabilityIdentity, ...],
    ) -> tuple[RequestedCapabilityIdentity, ...]:
        identities = [
            (
                item.capability_name,
                item.capability_version.major,
                item.capability_version.minor,
                item.capability_version.patch,
            )
            for item in value
        ]
        if len(identities) != len(set(identities)):
            raise ValueError("Selected capability identities must be unique.")
        return tuple(
            item
            for _, item in sorted(
                zip(identities, value, strict=True),
                key=lambda pair: pair[0],
            )
        )

    @model_validator(mode="after")
    def validate_parsed_collection_bounds(self) -> Self:
        objective_references = (
            self.objective.molecular_system_identifiers,
            self.objective.structure_identifiers,
            self.objective.structural_region_identifiers,
        )
        objective_declarations = (
            self.objective.comparison_state_identifiers,
            self.objective.requested_property_identifiers,
            self.objective.scientific_constraints,
            self.objective.stopping_criteria,
            self.objective.assumptions,
        )
        planning_declarations = (
            self.constraints.permitted_execution_targets,
            self.constraints.forbidden_execution_targets,
            self.constraints.resource_ceilings,
            self.constraints.cost_ceilings,
            self.constraints.required_verifications,
            self.constraints.approval_requirements,
            self.constraints.policy_notes,
        )
        resource_declarations = (
            self.resources.available_resources,
            self.resources.available_execution_targets,
            self.resources.unavailable_reasons,
        )
        if (
            any(
                len(items) > _OBJECTIVE_REFERENCE_MAXIMUM_COUNT
                for items in objective_references
            )
            or any(
                len(items) > _OBJECTIVE_DECLARATION_MAXIMUM_COUNT
                for items in objective_declarations
            )
            or any(
                len(items) > _PLANNING_DECLARATION_MAXIMUM_COUNT
                for items in planning_declarations
            )
            or any(
                len(items) > _RESOURCE_DECLARATION_MAXIMUM_COUNT
                for items in resource_declarations
            )
        ):
            raise ValueError(
                "Molecular planning request contains too many declarations."
            )
        if (
            self.constraints.maximum_wall_time_seconds is not None
            and self.constraints.maximum_wall_time_seconds
            > _MAXIMUM_WALL_TIME_SECONDS
        ):
            raise ValueError("Molecular planning wall-time ceiling is too large.")
        return self


class MolecularPlanningResponse(BaseModel):
    """Thin response retaining the complete canonical candidate plan."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    project_identifier: str
    objective_identifier: str
    plan_identifier: str
    plan_fingerprint: str
    planning_facts_fingerprint: str
    plan: CandidateResearchPlan
    capability_declarations: tuple[CapabilityExecutionEnvelope, ...] = ()

    @model_validator(mode="after")
    def validate_plan_identity(self) -> Self:
        if (
            self.project_identifier != self.plan.project_identifier
            or self.objective_identifier != self.plan.objective_identifier
            or self.plan_identifier != self.plan.plan_identifier
            or self.plan_fingerprint != self.plan.fingerprint
            or self.planning_facts_fingerprint != self.plan.planning_facts.fingerprint
        ):
            raise ValueError("Molecular planning response identity is inconsistent.")
        return self


class MolecularPlanningService:
    """Load immutable project evidence and construct a non-authorizing plan."""

    def __init__(
        self,
        *,
        project_resolver: Callable[[str], MolecularProject | None],
        artifact_repository: MolecularArtifactRepository,
        catalogue: ScientificCapabilityCatalog,
    ) -> None:
        self._project_resolver = project_resolver
        self._artifact_repository = artifact_repository
        if (
            len(catalogue.envelopes())
            > MOLECULAR_PLANNING_CATALOGUE_MAXIMUM_CAPABILITIES
        ):
            raise ValueError("Molecular planning catalogue exceeds its safe limit.")
        self._catalogue = catalogue
        self._lock = threading.RLock()
        self._started = False

    @property
    def catalogue(self) -> ScientificCapabilityCatalog:
        """Return the configured deterministic, non-executing catalogue."""

        return self._catalogue

    def start(self) -> None:
        """Start the read-only artifact dependency idempotently."""

        with self._lock:
            if self._started:
                return
            try:
                self._artifact_repository.start()
            except MolecularArtifactRepositoryError:
                raise MolecularPlanningUnavailableError(
                    "Molecular planning service could not start."
                ) from None
            self._started = True

    def close(self) -> None:
        """Close the owned repository handle idempotently."""

        with self._lock:
            self._started = False
            self._artifact_repository.close()

    def evaluate(
        self,
        *,
        project_identifier: str,
        request: MolecularPlanningRequest,
    ) -> MolecularPlanningResponse:
        """Construct a deterministic plan without persistence or execution."""

        with self._lock:
            if not self._started:
                raise MolecularPlanningUnavailableError(
                    "Molecular planning service is unavailable."
                )
            try:
                project_identifier = validate_identifier(
                    project_identifier,
                    label="molecular project identifier",
                )
                project = self._resolve_project(project_identifier)
                self._validate_objective(project, request.objective)
                topologies = self._load_topologies(project)
                facts = project_molecular_planning_facts(
                    project=project,
                    topology_indices=topologies,
                )
                catalogue = self._selected_catalogue(
                    request.selected_capabilities
                )
                constraints = self._effective_constraints(request)
                plan = construct_candidate_research_plan(
                    objective=request.objective,
                    constraints=constraints,
                    facts=facts,
                    resources=request.resources,
                    catalogue=catalogue,
                    provenance=CreationProvenance(
                        producer="pulsate-api.molecular-planning",
                        producer_version=project.schema_version,
                        source="cgr",
                    ),
                )
            except MolecularPlanningProjectionError:
                raise MolecularPlanningIntegrityError(
                    "Molecular planning evidence is invalid."
                ) from None
            except MolecularPlanningError:
                raise
            except (TypeError, ValueError):
                raise MolecularPlanningIntegrityError(
                    "Molecular planning could not be completed safely."
                ) from None
            except Exception:
                raise MolecularPlanningUnavailableError(
                    "Molecular planning service failed safely."
                ) from None
            return MolecularPlanningResponse(
                project_identifier=project.project_identifier,
                objective_identifier=request.objective.objective_identifier,
                plan_identifier=plan.plan_identifier,
                plan_fingerprint=plan.fingerprint,
                planning_facts_fingerprint=plan.planning_facts.fingerprint,
                plan=plan,
                capability_declarations=catalogue.envelopes(),
            )

    def _resolve_project(self, project_identifier: str) -> MolecularProject:
        try:
            project = self._project_resolver(project_identifier)
        except Exception:
            raise MolecularPlanningUnavailableError(
                "Molecular project resolution is unavailable."
            ) from None
        if project is None:
            raise MolecularPlanningProjectNotFoundError(
                "Requested molecular project was not found."
            )
        if (
            not isinstance(project, MolecularProject)
            or project.project_identifier != project_identifier
        ):
            raise MolecularPlanningIntegrityError(
                "Resolved molecular project identity is inconsistent."
            )
        return project

    @staticmethod
    def _validate_objective(
        project: MolecularProject,
        objective: ScientificObjective,
    ) -> None:
        if objective.project_identifier != project.project_identifier:
            raise MolecularPlanningRequestError(
                "Scientific objective project identity does not match the request."
            )
        references = (
            (
                objective.molecular_system_identifiers,
                {item.system_identifier for item in project.systems},
                "molecular system",
            ),
            (
                objective.structure_identifiers,
                {item.structure_identifier for item in project.structures},
                "molecular structure",
            ),
            (
                objective.structural_region_identifiers,
                {item.region_identifier for item in project.regions},
                "structural region",
            ),
        )
        for requested, available, label in references:
            if not set(requested).issubset(available):
                raise MolecularPlanningRequestError(
                    f"Scientific objective references an unknown {label}."
                )

    def _load_topologies(
        self,
        project: MolecularProject,
    ) -> tuple[MolecularTopologyIndex, ...]:
        topologies: list[MolecularTopologyIndex] = []
        total_declared_bytes = 0
        for structure in project.structures:
            reference = structure.topology_artifact
            if reference is None:
                continue
            if reference.artifact_type != "molecular_topology":
                raise MolecularPlanningIntegrityError(
                    "A declared topology artifact has an invalid type."
                )
            if (
                reference.byte_size is None
                or reference.byte_size <= 0
                or reference.byte_size
                > MOLECULAR_PLANNING_TOPOLOGY_MAXIMUM_BYTES
            ):
                raise MolecularPlanningIntegrityError(
                    "Declared molecular topology evidence exceeds its safe limit."
                )
            total_declared_bytes += reference.byte_size
            if total_declared_bytes > MOLECULAR_PLANNING_TOPOLOGY_MAXIMUM_BYTES:
                raise MolecularPlanningIntegrityError(
                    "Declared molecular topology evidence exceeds its safe limit."
                )
            try:
                payload = self._artifact_repository.read(reference)
                topology = MolecularTopologyIndex.model_validate_json(payload)
            except (MolecularArtifactRepositoryError, ValueError, TypeError):
                raise MolecularPlanningIntegrityError(
                    "Declared molecular topology evidence is unavailable or invalid."
                ) from None
            if payload != topology.to_canonical_json().encode("utf-8"):
                raise MolecularPlanningIntegrityError(
                    "Declared molecular topology evidence is not canonical."
                )
            topologies.append(topology)
        return tuple(topologies)

    def _selected_catalogue(
        self,
        identities: tuple[RequestedCapabilityIdentity, ...],
    ) -> ScientificCapabilityCatalog:
        if not identities:
            return self._catalogue
        selected = []
        for identity in identities:
            try:
                selected.append(
                    self._catalogue.get(
                        identity.capability_name,
                        identity.capability_version,
                    )
                )
            except ScientificCapabilityNotFoundError:
                raise MolecularPlanningCapabilityNotFoundError(
                    "Requested scientific capability was not found."
                ) from None
        return ScientificCapabilityCatalog(selected)

    @staticmethod
    def _effective_constraints(
        request: MolecularPlanningRequest,
    ) -> PlanningConstraints:
        target = request.requested_execution_target
        if target is None:
            return request.constraints
        constraints = request.constraints
        if target in constraints.forbidden_execution_targets or (
            constraints.permitted_execution_targets
            and target not in constraints.permitted_execution_targets
        ):
            raise MolecularPlanningRequestError(
                "Requested execution target conflicts with planning constraints."
            )
        return PlanningConstraints.model_validate(
            {
                **constraints.model_dump(mode="python"),
                "permitted_execution_targets": (target,),
            }
        )


__all__ = [
    "MolecularPlanningError",
    "MolecularPlanningCapabilityNotFoundError",
    "MolecularPlanningIntegrityError",
    "MolecularPlanningNotFoundError",
    "MolecularPlanningProjectNotFoundError",
    "MolecularPlanningRequest",
    "MolecularPlanningRequestError",
    "MolecularPlanningResponse",
    "MolecularPlanningService",
    "MolecularPlanningUnavailableError",
    "MOLECULAR_PLANNING_CATALOGUE_MAXIMUM_CAPABILITIES",
    "MOLECULAR_PLANNING_REQUEST_MAXIMUM_BYTES",
    "MOLECULAR_PLANNING_TOPOLOGY_MAXIMUM_BYTES",
    "RequestedCapabilityIdentity",
]
