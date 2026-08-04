"""Canonical contracts for the Phase 4 scientific workflow graph."""

from __future__ import annotations

import math
from enum import Enum
from typing import Any, Self

from pydantic import Field, field_validator, model_validator
from pydantic_core import core_schema

from cgr.science.canonical import (
    CanonicalModel,
    canonical_json,
    sha256_fingerprint,
    validate_identifier,
    validate_sha256,
)

_MAX_NODES = 1000
_MAX_EDGES = 5000
_MAX_BINDINGS = 10000
_MAX_PORTS_PER_NODE = 50
_MAX_SWEEP_EXPANSIONS = 100
_MAX_TOTAL_SWEEP_EXPANSIONS = 1000
_MAX_RETRY_COUNT = 10
_MAX_GRAPH_DEPTH = 100
_MAX_STRING_LENGTH = 4096
_MAX_METADATA_KEYS = 100
_MAX_METADATA_VALUE_LENGTH = 1024
_MAX_DEPENDENCIES = 2000
_MAX_COMPARISON_MEMBERS = 50
_MAX_APPROVAL_REQUIREMENTS = 64
_MAX_RESOURCE_CEILINGS = 64
_MAX_CONDITION_DEPTH = 10
_MAX_JSON_COLLECTION_ITEMS = 100
_MAX_JSON_OBJECT_KEYS = 50


class FrozenDict(dict[str, Any]):
    """JSON-serializable dictionary that rejects mutation after validation."""

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source_type: Any, handler: Any
    ) -> core_schema.CoreSchema:
        del source_type
        dictionary_schema = handler.generate_schema(dict[str, Any])
        return core_schema.no_info_after_validator_function(
            lambda value: cls(value),
            dictionary_schema,
            serialization=core_schema.plain_serializer_function_ser_schema(
                lambda value: dict(value),
                return_schema=dictionary_schema,
                when_used="always",
            ),
        )

    def _immutable(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise TypeError("FrozenDict values are immutable.")

    __setitem__ = _immutable
    __delitem__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable


class NodeKind(str, Enum):
    """Closed workflow-node categories."""

    CLASSICAL = "classical"
    QUANTUM_LOCAL = "quantum_local"
    QUANTUM_IBM = "quantum_ibm"
    COMPARISON = "comparison"
    AGGREGATION = "aggregation"
    CONDITIONAL_BRANCH = "conditional_branch"
    APPROVAL_GATE = "approval_gate"
    PARAMETER_SWEEP = "parameter_sweep"
    DATA_SOURCE = "data_source"
    DATA_SINK = "data_sink"


class EdgeKind(str, Enum):
    """Closed workflow-edge categories."""

    DEPENDENCY = "dependency"
    DATA_FLOW = "data_flow"
    CONDITIONAL = "conditional"
    COMPARISON_MEMBER = "comparison_member"
    SWEEP_INSTANCE = "sweep_instance"


class PortType(str, Enum):
    """Closed data categories allowed at workflow ports."""

    ARTIFACT = "artifact"
    VALUE = "value"
    REFERENCE = "reference"


class ConditionKind(str, Enum):
    """Closed declarative condition types; no executable expressions."""

    SUCCESS = "success"
    FAILURE = "failure"
    ARTIFACT_PRESENT = "artifact_present"
    VERIFICATION_CLASS = "verification_class"
    NUMERIC_COMPARE = "numeric_compare"
    FEASIBILITY_CLASS = "feasibility_class"
    APPROVAL_RESULT = "approval_result"
    BUDGET_REMAINING = "budget_remaining"
    CANDIDATE_SELECTED = "candidate_selected"


class ComparisonOperator(str, Enum):
    """Closed numeric comparison operators."""

    LT = "lt"
    LE = "le"
    EQ = "eq"
    NE = "ne"
    GE = "ge"
    GT = "gt"


class ApprovalStatus(str, Enum):
    """Closed approval-decision states."""

    PENDING = "pending"
    GRANTED = "granted"
    DENIED = "denied"
    EXPIRED = "expired"


class RetryOutcome(str, Enum):
    """Closed retry-attempt outcomes."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class FailureAction(str, Enum):
    """Closed failure-propagation actions."""

    STOP = "stop"
    RETRY = "retry"
    SKIP = "skip"
    FALLBACK = "fallback"


class DependencyType(str, Enum):
    """Closed dependency semantics."""

    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"
    CONDITIONAL = "conditional"


class ComparisonKind(str, Enum):
    """Closed comparison completion and selection semantics."""

    BEST_SUCCESS = "best_success"
    ALL_COMPLETE = "all_complete"
    FIRST_SUCCESS = "first_success"


def _bounded_text(value: str | None, *, label: str) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label} cannot be blank.")
    if len(normalized) > _MAX_STRING_LENGTH:
        raise ValueError(f"{label} exceeds {_MAX_STRING_LENGTH} characters.")
    return normalized


def _finite_nonnegative(value: float | None, *, label: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric.")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{label} must be finite.")
    if numeric < 0:
        raise ValueError(f"{label} cannot be negative.")
    return numeric


def _identifier_tuple(
    value: tuple[str, ...],
    *,
    label: str,
    maximum: int,
    allow_empty: bool = True,
) -> tuple[str, ...]:
    if not allow_empty and not value:
        raise ValueError(f"{label} cannot be empty.")
    if len(value) > maximum:
        raise ValueError(f"{label} cannot exceed {maximum} entries.")
    normalized = tuple(validate_identifier(item, label=label) for item in value)
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{label} entries must be unique.")
    return tuple(sorted(normalized))


def _frozen_string_map(
    value: dict[str, str] | FrozenDict,
    *,
    label: str,
    maximum: int = _MAX_METADATA_KEYS,
) -> FrozenDict:
    if len(value) > maximum:
        raise ValueError(f"{label} cannot exceed {maximum} entries.")
    normalized: dict[str, str] = {}
    for key, item in value.items():
        normalized_key = validate_identifier(key, label=f"{label} key")
        if not isinstance(item, str):
            raise ValueError(f"{label} values must be strings.")
        normalized_value = item.strip()
        if len(normalized_value) > _MAX_METADATA_VALUE_LENGTH:
            raise ValueError(f"{label} values exceed the maximum length.")
        normalized[normalized_key] = normalized_value
    return FrozenDict(sorted(normalized.items()))


def _frozen_numeric_map(
    value: dict[str, float] | FrozenDict,
    *,
    label: str,
    maximum: int = _MAX_RESOURCE_CEILINGS,
) -> FrozenDict:
    if len(value) > maximum:
        raise ValueError(f"{label} cannot exceed {maximum} entries.")
    normalized: dict[str, float] = {}
    for key, item in value.items():
        normalized_key = validate_identifier(key, label=f"{label} key")
        normalized_value = _finite_nonnegative(item, label=f"{label} value")
        assert normalized_value is not None
        normalized[normalized_key] = normalized_value
    return FrozenDict(sorted(normalized.items()))


def _freeze_json(value: Any, *, depth: int = 0) -> Any:
    if depth > _MAX_CONDITION_DEPTH:
        raise ValueError("Structured JSON value is too deeply nested.")
    if value is None or isinstance(value, (str, bool, int)):
        if isinstance(value, str) and len(value) > _MAX_STRING_LENGTH:
            raise ValueError("Structured JSON strings exceed the maximum length.")
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Structured JSON numbers must be finite.")
        return value
    if isinstance(value, (list, tuple)):
        if len(value) > _MAX_JSON_COLLECTION_ITEMS:
            raise ValueError("Structured JSON arrays exceed the maximum length.")
        return tuple(_freeze_json(item, depth=depth + 1) for item in value)
    if isinstance(value, dict):
        if len(value) > _MAX_JSON_OBJECT_KEYS:
            raise ValueError("Structured JSON objects contain too many keys.")
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            normalized_key = validate_identifier(str(key), label="structured JSON key")
            normalized[normalized_key] = _freeze_json(item, depth=depth + 1)
        return FrozenDict(sorted(normalized.items()))
    raise ValueError("Structured values must be JSON-compatible.")


class InputPort(CanonicalModel):
    """One bounded input declaration for a workflow node."""

    port_identifier: str
    port_type: PortType
    artifact_kind: str | None = None
    required: bool = True
    multiple: bool = False
    external: bool = False
    default_value: Any | None = None
    description: str | None = Field(default=None, max_length=_MAX_STRING_LENGTH)

    @field_validator("port_identifier")
    @classmethod
    def validate_port_identifier(cls, value: str) -> str:
        return validate_identifier(value, label="input port identifier")

    @field_validator("artifact_kind")
    @classmethod
    def validate_artifact_kind(cls, value: str | None) -> str | None:
        return validate_identifier(value, label="artifact kind") if value else None

    @field_validator("default_value")
    @classmethod
    def validate_default_value(cls, value: Any | None) -> Any | None:
        return _freeze_json(value) if value is not None else None

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        return _bounded_text(value, label="Input-port description")

    @model_validator(mode="after")
    def validate_port_semantics(self) -> Self:
        if self.port_type is PortType.ARTIFACT and self.artifact_kind is None:
            raise ValueError("Artifact input ports require artifact_kind.")
        if self.port_type is not PortType.ARTIFACT and self.artifact_kind is not None:
            raise ValueError("Only artifact input ports may declare artifact_kind.")
        if self.external and self.default_value is not None:
            raise ValueError("External input ports cannot also declare default values.")
        return self


class OutputPort(CanonicalModel):
    """One bounded output declaration for a workflow node."""

    port_identifier: str
    port_type: PortType
    artifact_kind: str | None = None
    multiple: bool = False
    description: str | None = Field(default=None, max_length=_MAX_STRING_LENGTH)

    @field_validator("port_identifier")
    @classmethod
    def validate_port_identifier(cls, value: str) -> str:
        return validate_identifier(value, label="output port identifier")

    @field_validator("artifact_kind")
    @classmethod
    def validate_artifact_kind(cls, value: str | None) -> str | None:
        return validate_identifier(value, label="artifact kind") if value else None

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        return _bounded_text(value, label="Output-port description")

    @model_validator(mode="after")
    def validate_port_semantics(self) -> Self:
        if self.port_type is PortType.ARTIFACT and self.artifact_kind is None:
            raise ValueError("Artifact output ports require artifact_kind.")
        if self.port_type is not PortType.ARTIFACT and self.artifact_kind is not None:
            raise ValueError("Only artifact output ports may declare artifact_kind.")
        return self


class RetryPolicy(CanonicalModel):
    """Bounded retry declaration; it is not evidence that a retry occurred."""

    max_attempts: int = Field(default=1, ge=1, le=_MAX_RETRY_COUNT)
    retry_delay_seconds: float = Field(default=0.0, ge=0)
    retryable_error_codes: tuple[str, ...] = ()
    backoff_multiplier: float = Field(default=1.0, ge=1.0)
    max_delay_seconds: float | None = Field(default=None, ge=0)

    @field_validator("retry_delay_seconds", "backoff_multiplier", "max_delay_seconds")
    @classmethod
    def validate_finite_numbers(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("Retry-policy numbers must be finite.")
        return value

    @field_validator("retryable_error_codes")
    @classmethod
    def order_error_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _identifier_tuple(
            value,
            label="retryable error code",
            maximum=_MAX_METADATA_KEYS,
        )

    @model_validator(mode="after")
    def validate_delay_bounds(self) -> Self:
        if (
            self.max_delay_seconds is not None
            and self.max_delay_seconds < self.retry_delay_seconds
        ):
            raise ValueError("max_delay_seconds cannot be below the initial delay.")
        return self


class ApprovalRequirement(CanonicalModel):
    """Approval bound to an exact immutable workflow context."""

    approval_identifier: str
    graph_identifier: str
    graph_version: int = Field(ge=1)
    graph_definition_fingerprint: str
    node_identifier: str | None = None
    operation_identity: str | None = None
    capability_identity: str | None = None
    cost_ceiling: float | None = None
    resource_ceilings: FrozenDict = Field(default_factory=FrozenDict)
    input_binding_ids: tuple[str, ...] = ()
    validity_epoch: float | None = Field(default=None, gt=0)
    description: str | None = Field(default=None, max_length=_MAX_STRING_LENGTH)

    @field_validator(
        "approval_identifier",
        "graph_identifier",
        "node_identifier",
        "operation_identity",
        "capability_identity",
    )
    @classmethod
    def validate_identifiers(cls, value: str | None) -> str | None:
        return validate_identifier(value) if value is not None else None

    @field_validator("graph_definition_fingerprint")
    @classmethod
    def validate_graph_fingerprint(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("cost_ceiling")
    @classmethod
    def validate_cost_ceiling(cls, value: float | None) -> float | None:
        return _finite_nonnegative(value, label="approval cost ceiling")

    @field_validator("resource_ceilings")
    @classmethod
    def validate_resource_ceilings(cls, value: FrozenDict) -> FrozenDict:
        return _frozen_numeric_map(value, label="approval resource ceilings")

    @field_validator("input_binding_ids")
    @classmethod
    def order_binding_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _identifier_tuple(
            value,
            label="approval input binding identifier",
            maximum=_MAX_BINDINGS,
        )

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        return _bounded_text(value, label="Approval description")

    @model_validator(mode="after")
    def require_approval_subject(self) -> Self:
        if self.node_identifier is None and self.operation_identity is None:
            raise ValueError("Approval must bind a node or operation identity.")
        return self


class ExecutionPolicy(CanonicalModel):
    """Bounded orchestration policy; it does not authorize execution."""

    concurrency_limit: int = Field(default=1, ge=1, le=64)
    timeout_seconds: float | None = Field(default=None, gt=0)
    priority: int = Field(default=0, ge=-100, le=100)
    isolated: bool = False
    requires_approval: bool = False
    approval_requirements: tuple[ApprovalRequirement, ...] = ()

    @field_validator("timeout_seconds")
    @classmethod
    def validate_timeout(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("Execution timeout must be finite.")
        return value

    @field_validator("approval_requirements")
    @classmethod
    def order_approvals(
        cls, value: tuple[ApprovalRequirement, ...]
    ) -> tuple[ApprovalRequirement, ...]:
        if len(value) > _MAX_APPROVAL_REQUIREMENTS:
            raise ValueError("Too many approval requirements.")
        identifiers = [item.approval_identifier for item in value]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Approval requirement identifiers must be unique.")
        return tuple(sorted(value, key=lambda item: item.approval_identifier))

    @model_validator(mode="after")
    def validate_approval_consistency(self) -> Self:
        if self.requires_approval != bool(self.approval_requirements):
            raise ValueError(
                "requires_approval must match the presence of approval requirements."
            )
        return self


class CostCeiling(CanonicalModel):
    """One non-negative finite monetary ceiling."""

    currency: str
    amount: float

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, value: str) -> str:
        return validate_identifier(value, label="currency").upper()

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, value: float) -> float:
        normalized = _finite_nonnegative(value, label="cost ceiling")
        assert normalized is not None
        return normalized


class ResourceCeiling(CanonicalModel):
    """One non-negative finite resource ceiling."""

    resource_type: str
    quantity: float
    unit: str

    @field_validator("resource_type", "unit")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("quantity")
    @classmethod
    def validate_quantity(cls, value: float) -> float:
        normalized = _finite_nonnegative(value, label="resource ceiling")
        assert normalized is not None
        return normalized


class QuantumEscalationPolicy(CanonicalModel):
    """Fail-closed declaration for selective IBM escalation."""

    allow_ibm_submission: bool = False
    required_approvals: tuple[str, ...] = ()
    preflight_required: bool = True
    max_qubits: int | None = Field(default=None, ge=1)
    allowed_backends: tuple[str, ...] = ()
    cost_acknowledgement_required: bool = True

    @field_validator("required_approvals", "allowed_backends")
    @classmethod
    def order_identifiers(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _identifier_tuple(
            value,
            label="quantum escalation identifier",
            maximum=_MAX_METADATA_KEYS,
        )

    @model_validator(mode="after")
    def enforce_ibm_controls(self) -> Self:
        if self.allow_ibm_submission and (
            not self.preflight_required
            or not self.cost_acknowledgement_required
            or not self.required_approvals
        ):
            raise ValueError(
                "IBM submission requires preflight, cost acknowledgement, and approval."
            )
        return self


class FailureRecoveryPolicy(CanonicalModel):
    """Bounded failure propagation and recovery declaration."""

    on_failure: FailureAction = FailureAction.STOP
    retry_policy: RetryPolicy | None = None
    fallback_node_id: str | None = None
    preserve_successful_branches: bool = True
    recovery_checkpoint_required: bool = True

    @field_validator("fallback_node_id")
    @classmethod
    def validate_fallback(cls, value: str | None) -> str | None:
        return validate_identifier(value) if value is not None else None

    @model_validator(mode="after")
    def validate_action(self) -> Self:
        if (self.on_failure is FailureAction.RETRY) != (self.retry_policy is not None):
            raise ValueError("Retry failure action requires exactly one retry policy.")
        if (self.on_failure is FailureAction.FALLBACK) != (
            self.fallback_node_id is not None
        ):
            raise ValueError("Fallback failure action requires exactly one fallback node.")
        return self


class PortBinding(CanonicalModel):
    """Data binding from one declared output port to one input port."""

    binding_identifier: str
    source_node_id: str
    source_port_id: str
    destination_node_id: str
    destination_port_id: str
    value_override: Any | None = None

    @field_validator(
        "binding_identifier",
        "source_node_id",
        "source_port_id",
        "destination_node_id",
        "destination_port_id",
    )
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("value_override")
    @classmethod
    def validate_override(cls, value: Any | None) -> Any | None:
        return _freeze_json(value) if value is not None else None


class DependencyDeclaration(CanonicalModel):
    """Explicit node dependency in addition to edge-level data flow."""

    dependency_identifier: str
    subject_node_id: str
    prerequisite_node_ids: tuple[str, ...]
    dependency_type: DependencyType = DependencyType.SEQUENTIAL

    @field_validator("dependency_identifier", "subject_node_id")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("prerequisite_node_ids")
    @classmethod
    def order_prerequisites(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _identifier_tuple(
            value,
            label="prerequisite node identifier",
            maximum=_MAX_NODES,
            allow_empty=False,
        )

    @model_validator(mode="after")
    def reject_self_dependency(self) -> Self:
        if self.subject_node_id in self.prerequisite_node_ids:
            raise ValueError("A dependency cannot reference its subject as a prerequisite.")
        return self


class ConditionalEdge(CanonicalModel):
    """Declarative condition bound to one exact edge."""

    edge_identifier: str
    source_node_id: str
    target_node_id: str
    condition_kind: ConditionKind
    condition_expression: FrozenDict
    source_port_id: str | None = None
    target_port_id: str | None = None

    @field_validator(
        "edge_identifier",
        "source_node_id",
        "target_node_id",
        "source_port_id",
        "target_port_id",
    )
    @classmethod
    def validate_identifiers(cls, value: str | None) -> str | None:
        return validate_identifier(value) if value is not None else None

    @field_validator("condition_expression", mode="before")
    @classmethod
    def validate_expression(cls, value: Any) -> FrozenDict:
        frozen = _freeze_json(value)
        if not isinstance(frozen, FrozenDict) or not frozen:
            raise ValueError("Condition expressions must be nonempty JSON objects.")
        return frozen


class ComparisonGroup(CanonicalModel):
    """Bounded group of nodes whose outputs are compared deterministically."""

    group_identifier: str
    member_node_ids: tuple[str, ...]
    aggregation_node_id: str | None = None
    selection_criteria: FrozenDict | None = None
    comparison_kind: ComparisonKind = ComparisonKind.BEST_SUCCESS

    @field_validator("group_identifier", "aggregation_node_id")
    @classmethod
    def validate_identifiers(cls, value: str | None) -> str | None:
        return validate_identifier(value) if value is not None else None

    @field_validator("member_node_ids")
    @classmethod
    def order_members(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        ordered = _identifier_tuple(
            value,
            label="comparison member identifier",
            maximum=_MAX_COMPARISON_MEMBERS,
            allow_empty=False,
        )
        if len(ordered) < 2:
            raise ValueError("Comparison groups require at least two members.")
        return ordered

    @field_validator("selection_criteria", mode="before")
    @classmethod
    def validate_criteria(cls, value: Any | None) -> FrozenDict | None:
        if value is None:
            return None
        frozen = _freeze_json(value)
        if not isinstance(frozen, FrozenDict):
            raise ValueError("Selection criteria must be a JSON object.")
        return frozen


class ParameterSweep(CanonicalModel):
    """One bounded deterministic parameter expansion."""

    sweep_identifier: str
    base_node_id: str
    parameter_name: str
    parameter_values: tuple[Any, ...]
    generated_node_prefix: str | None = None

    @field_validator(
        "sweep_identifier", "base_node_id", "parameter_name", "generated_node_prefix"
    )
    @classmethod
    def validate_identifiers(cls, value: str | None) -> str | None:
        return validate_identifier(value) if value is not None else None

    @field_validator("parameter_values")
    @classmethod
    def validate_parameter_values(cls, value: tuple[Any, ...]) -> tuple[Any, ...]:
        if not value:
            raise ValueError("Parameter sweeps require at least one value.")
        if len(value) > _MAX_SWEEP_EXPANSIONS:
            raise ValueError(
                f"Parameter sweeps cannot exceed {_MAX_SWEEP_EXPANSIONS} expansions."
            )
        normalized = tuple(_freeze_json(item) for item in value)
        identities = [canonical_json(item) for item in normalized]
        if len(identities) != len(set(identities)):
            raise ValueError("Parameter sweep values must be semantically unique.")
        return tuple(item for _, item in sorted(zip(identities, normalized)))


class WorkflowNode(CanonicalModel):
    """Immutable workflow node definition."""

    node_identifier: str
    node_kind: NodeKind
    capability_identity: str | None = None
    input_ports: tuple[InputPort, ...] = ()
    output_ports: tuple[OutputPort, ...] = ()
    execution_policy: ExecutionPolicy | None = None
    retry_policy: RetryPolicy | None = None
    failure_recovery_policy: FailureRecoveryPolicy | None = None
    quantum_escalation_policy: QuantumEscalationPolicy | None = None
    metadata: FrozenDict = Field(default_factory=FrozenDict)

    @field_validator("node_identifier", "capability_identity")
    @classmethod
    def validate_identifiers(cls, value: str | None) -> str | None:
        return validate_identifier(value) if value is not None else None

    @field_validator("input_ports")
    @classmethod
    def order_input_ports(cls, value: tuple[InputPort, ...]) -> tuple[InputPort, ...]:
        if len(value) > _MAX_PORTS_PER_NODE:
            raise ValueError("Too many input ports.")
        identifiers = [item.port_identifier for item in value]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Input-port identifiers must be unique per node.")
        return tuple(sorted(value, key=lambda item: item.port_identifier))

    @field_validator("output_ports")
    @classmethod
    def order_output_ports(
        cls, value: tuple[OutputPort, ...]
    ) -> tuple[OutputPort, ...]:
        if len(value) > _MAX_PORTS_PER_NODE:
            raise ValueError("Too many output ports.")
        identifiers = [item.port_identifier for item in value]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Output-port identifiers must be unique per node.")
        return tuple(sorted(value, key=lambda item: item.port_identifier))

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: FrozenDict) -> FrozenDict:
        return _frozen_string_map(value, label="workflow node metadata")

    @model_validator(mode="after")
    def validate_node_policies(self) -> Self:
        if self.node_kind is NodeKind.QUANTUM_IBM and self.quantum_escalation_policy is None:
            raise ValueError("IBM quantum nodes require a quantum escalation policy.")
        if (
            self.node_kind not in {NodeKind.QUANTUM_LOCAL, NodeKind.QUANTUM_IBM}
            and self.quantum_escalation_policy is not None
        ):
            raise ValueError("Only quantum nodes may declare quantum escalation policy.")
        return self


class WorkflowEdge(CanonicalModel):
    """Immutable directed edge between two workflow nodes."""

    edge_identifier: str
    edge_kind: EdgeKind
    source_node_id: str
    target_node_id: str
    source_port_id: str | None = None
    target_port_id: str | None = None
    condition: ConditionalEdge | None = None
    metadata: FrozenDict = Field(default_factory=FrozenDict)

    @field_validator(
        "edge_identifier",
        "source_node_id",
        "target_node_id",
        "source_port_id",
        "target_port_id",
    )
    @classmethod
    def validate_identifiers(cls, value: str | None) -> str | None:
        return validate_identifier(value) if value is not None else None

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: FrozenDict) -> FrozenDict:
        return _frozen_string_map(value, label="workflow edge metadata")

    @model_validator(mode="after")
    def validate_condition_binding(self) -> Self:
        if (self.edge_kind is EdgeKind.CONDITIONAL) != (self.condition is not None):
            raise ValueError("Conditional edges require exactly one condition declaration.")
        if self.condition is not None and (
            self.condition.edge_identifier != self.edge_identifier
            or self.condition.source_node_id != self.source_node_id
            or self.condition.target_node_id != self.target_node_id
            or self.condition.source_port_id != self.source_port_id
            or self.condition.target_port_id != self.target_port_id
        ):
            raise ValueError("Conditional-edge identity must match its containing edge.")
        return self


class WorkflowGraphMetadata(CanonicalModel):
    """Bounded scientific references and non-authorizing planning estimates."""

    objective_identifier: str | None = None
    molecular_project_identifier: str | None = None
    system_identifier: str | None = None
    candidate_plan_fingerprint: str | None = None
    estimated_cost: float | None = None
    estimated_runtime_seconds: float | None = None
    verification_requirements: tuple[str, ...] = ()
    scientific_provenance: FrozenDict = Field(default_factory=FrozenDict)

    @field_validator(
        "objective_identifier", "molecular_project_identifier", "system_identifier"
    )
    @classmethod
    def validate_identifiers(cls, value: str | None) -> str | None:
        return validate_identifier(value) if value is not None else None

    @field_validator("candidate_plan_fingerprint")
    @classmethod
    def validate_plan_fingerprint(cls, value: str | None) -> str | None:
        return validate_sha256(value) if value is not None else None

    @field_validator("estimated_cost", "estimated_runtime_seconds")
    @classmethod
    def validate_estimates(cls, value: float | None) -> float | None:
        return _finite_nonnegative(value, label="workflow estimate")

    @field_validator("verification_requirements")
    @classmethod
    def order_verification_requirements(
        cls, value: tuple[str, ...]
    ) -> tuple[str, ...]:
        return _identifier_tuple(
            value,
            label="verification requirement",
            maximum=_MAX_METADATA_KEYS,
        )

    @field_validator("scientific_provenance")
    @classmethod
    def validate_provenance(cls, value: FrozenDict) -> FrozenDict:
        return _frozen_string_map(value, label="scientific provenance")


class WorkflowGraphVersion(CanonicalModel):
    """Immutable version receipt for one workflow graph definition."""

    graph_identifier: str
    version_number: int = Field(ge=1)
    created_at_epoch: float = Field(gt=0)
    created_by_principal: str
    graph_definition_fingerprint: str
    changelog: str | None = Field(default=None, max_length=_MAX_STRING_LENGTH)

    @field_validator("graph_identifier", "created_by_principal")
    @classmethod
    def validate_identifiers(cls, value: str) -> str:
        return validate_identifier(value)

    @field_validator("created_at_epoch")
    @classmethod
    def validate_created_at(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("created_at_epoch must be finite.")
        return value

    @field_validator("graph_definition_fingerprint")
    @classmethod
    def validate_definition_fingerprint(cls, value: str) -> str:
        return validate_sha256(value)

    @field_validator("changelog")
    @classmethod
    def normalize_changelog(cls, value: str | None) -> str | None:
        return _bounded_text(value, label="Workflow graph changelog")


class WorkflowGraphDefinition(CanonicalModel):
    """Complete immutable graph definition with a verified embedded fingerprint."""

    graph_identifier: str
    version: int = Field(ge=1)
    nodes: tuple[WorkflowNode, ...]
    edges: tuple[WorkflowEdge, ...] = ()
    bindings: tuple[PortBinding, ...] = ()
    dependencies: tuple[DependencyDeclaration, ...] = ()
    root_node_ids: tuple[str, ...]
    terminal_node_ids: tuple[str, ...]
    parameter_sweeps: tuple[ParameterSweep, ...] = ()
    comparison_groups: tuple[ComparisonGroup, ...] = ()
    global_execution_policy: ExecutionPolicy | None = None
    global_retry_policy: RetryPolicy | None = None
    global_failure_recovery_policy: FailureRecoveryPolicy | None = None
    cost_ceilings: tuple[CostCeiling, ...] = ()
    resource_ceilings: tuple[ResourceCeiling, ...] = ()
    metadata: WorkflowGraphMetadata | None = None
    definition_fingerprint: str | None = None

    @field_validator("graph_identifier")
    @classmethod
    def validate_graph_identifier(cls, value: str) -> str:
        return validate_identifier(value, label="workflow graph identifier")

    @field_validator("nodes")
    @classmethod
    def order_nodes(cls, value: tuple[WorkflowNode, ...]) -> tuple[WorkflowNode, ...]:
        if not value:
            raise ValueError("Workflow graphs require at least one node.")
        if len(value) > _MAX_NODES:
            raise ValueError(f"Workflow graphs cannot exceed {_MAX_NODES} nodes.")
        return tuple(sorted(value, key=lambda item: item.node_identifier))

    @field_validator("edges")
    @classmethod
    def order_edges(cls, value: tuple[WorkflowEdge, ...]) -> tuple[WorkflowEdge, ...]:
        if len(value) > _MAX_EDGES:
            raise ValueError(f"Workflow graphs cannot exceed {_MAX_EDGES} edges.")
        return tuple(sorted(value, key=lambda item: item.edge_identifier))

    @field_validator("bindings")
    @classmethod
    def order_bindings(cls, value: tuple[PortBinding, ...]) -> tuple[PortBinding, ...]:
        if len(value) > _MAX_BINDINGS:
            raise ValueError(f"Workflow graphs cannot exceed {_MAX_BINDINGS} bindings.")
        return tuple(sorted(value, key=lambda item: item.binding_identifier))

    @field_validator("dependencies")
    @classmethod
    def order_dependencies(
        cls, value: tuple[DependencyDeclaration, ...]
    ) -> tuple[DependencyDeclaration, ...]:
        if len(value) > _MAX_DEPENDENCIES:
            raise ValueError(
                f"Workflow graphs cannot exceed {_MAX_DEPENDENCIES} dependencies."
            )
        return tuple(sorted(value, key=lambda item: item.dependency_identifier))

    @field_validator("root_node_ids", "terminal_node_ids")
    @classmethod
    def order_boundary_nodes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _identifier_tuple(
            value,
            label="workflow boundary node identifier",
            maximum=_MAX_NODES,
            allow_empty=False,
        )

    @field_validator("parameter_sweeps")
    @classmethod
    def order_sweeps(
        cls, value: tuple[ParameterSweep, ...]
    ) -> tuple[ParameterSweep, ...]:
        identifiers = [item.sweep_identifier for item in value]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Parameter-sweep identifiers must be unique.")
        if sum(len(item.parameter_values) for item in value) > _MAX_TOTAL_SWEEP_EXPANSIONS:
            raise ValueError("Total parameter-sweep expansion exceeds the graph limit.")
        return tuple(sorted(value, key=lambda item: item.sweep_identifier))

    @field_validator("comparison_groups")
    @classmethod
    def order_comparison_groups(
        cls, value: tuple[ComparisonGroup, ...]
    ) -> tuple[ComparisonGroup, ...]:
        identifiers = [item.group_identifier for item in value]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Comparison-group identifiers must be unique.")
        return tuple(sorted(value, key=lambda item: item.group_identifier))

    @field_validator("cost_ceilings")
    @classmethod
    def order_cost_ceilings(
        cls, value: tuple[CostCeiling, ...]
    ) -> tuple[CostCeiling, ...]:
        currencies = [item.currency for item in value]
        if len(currencies) != len(set(currencies)):
            raise ValueError("Cost ceilings must be unique per currency.")
        return tuple(sorted(value, key=lambda item: item.currency))

    @field_validator("resource_ceilings")
    @classmethod
    def order_resource_ceilings(
        cls, value: tuple[ResourceCeiling, ...]
    ) -> tuple[ResourceCeiling, ...]:
        identities = [(item.resource_type, item.unit) for item in value]
        if len(identities) != len(set(identities)):
            raise ValueError("Resource ceilings must be unique per type and unit.")
        if len(value) > _MAX_RESOURCE_CEILINGS:
            raise ValueError("Too many resource ceilings.")
        return tuple(sorted(value, key=lambda item: (item.resource_type, item.unit)))

    @field_validator("definition_fingerprint")
    @classmethod
    def validate_supplied_fingerprint(cls, value: str | None) -> str | None:
        return validate_sha256(value) if value is not None else None

    @model_validator(mode="after")
    def verify_definition_fingerprint(self) -> Self:
        expected = sha256_fingerprint(self.canonical_identity())
        if (
            self.definition_fingerprint is not None
            and self.definition_fingerprint != expected
        ):
            raise ValueError("Workflow graph definition fingerprint does not match content.")
        object.__setattr__(self, "definition_fingerprint", expected)
        return self

    def canonical_identity(self) -> Any:
        """Return semantic graph data without the embedded receipt field."""
        return self.model_dump(mode="json", exclude={"definition_fingerprint"})

    def get_node(self, node_id: str) -> WorkflowNode | None:
        """Return one node by stable identifier without raising."""
        normalized = validate_identifier(node_id, label="workflow node identifier")
        return next(
            (item for item in self.nodes if item.node_identifier == normalized),
            None,
        )

    def get_edges_from(self, node_id: str) -> tuple[WorkflowEdge, ...]:
        """Return canonical outgoing edges."""
        normalized = validate_identifier(node_id, label="workflow node identifier")
        return tuple(item for item in self.edges if item.source_node_id == normalized)

    def get_edges_to(self, node_id: str) -> tuple[WorkflowEdge, ...]:
        """Return canonical incoming edges."""
        normalized = validate_identifier(node_id, label="workflow node identifier")
        return tuple(item for item in self.edges if item.target_node_id == normalized)
