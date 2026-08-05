"""Deterministic fail-closed static validation for Phase 4 workflow graphs."""

from __future__ import annotations

import heapq
from enum import Enum
from typing import Iterable

from pydantic import field_validator

from cgr.science.canonical import CanonicalModel, validate_identifier

from .contracts import (
    FailureAction,
    NodeKind,
    WorkflowGraphDefinition,
    _MAX_GRAPH_DEPTH,
    _MAX_TOTAL_SWEEP_EXPANSIONS,
)


class ValidationSeverity(str, Enum):
    """Closed validation severity."""

    ERROR = "error"
    WARNING = "warning"


class ValidationError(CanonicalModel):
    """One bounded deterministic static-validation finding."""

    code: str
    message: str
    subject_id: str | None = None
    severity: ValidationSeverity = ValidationSeverity.ERROR

    @field_validator("code", "subject_id")
    @classmethod
    def validate_identifiers(cls, value: str | None) -> str | None:
        return validate_identifier(value) if value is not None else None

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > 1024:
            raise ValueError("Validation messages must be nonempty and bounded.")
        return normalized


class ValidationResult(CanonicalModel):
    """Immutable deterministic result of static graph validation."""

    valid: bool
    errors: tuple[ValidationError, ...] = ()
    warnings: tuple[ValidationError, ...] = ()
    topological_order: tuple[str, ...] = ()
    maximum_depth: int | None = None


class WorkflowGraphValidator:
    """Validate one immutable graph definition without executing capabilities."""

    def __init__(self, graph: WorkflowGraphDefinition):
        self.graph = graph
        self._errors: list[ValidationError] = []
        self._warnings: list[ValidationError] = []
        self._nodes = {}
        for node in graph.nodes:
            self._nodes.setdefault(node.node_identifier, node)

    def validate(self) -> ValidationResult:
        """Run every validation and return deterministically ordered findings."""
        self._errors = []
        self._warnings = []
        self._validate_unique_identifiers()
        self._validate_node_references()
        self._validate_semantic_duplicates()
        self._validate_ports_and_bindings()
        self._validate_boundaries()
        self._validate_dependencies()
        self._validate_sweeps()
        self._validate_comparison_groups()
        self._validate_policies()

        topological = self._try_topological_order()
        maximum_depth: int | None = None
        if topological is None:
            self._error("DIRECTED_CYCLE", "The workflow graph contains a directed cycle.")
        else:
            self._validate_reachability()
            maximum_depth = self._calculate_maximum_depth(topological)
            if maximum_depth > _MAX_GRAPH_DEPTH:
                self._error(
                    "EXCESSIVE_DEPTH",
                    f"Graph depth {maximum_depth} exceeds maximum {_MAX_GRAPH_DEPTH}.",
                )

        errors = tuple(sorted(self._errors, key=self._finding_key))
        warnings = tuple(sorted(self._warnings, key=self._finding_key))
        return ValidationResult(
            valid=not errors,
            errors=errors,
            warnings=warnings,
            topological_order=topological or (),
            maximum_depth=maximum_depth,
        )

    def topological_order(self) -> tuple[str, ...]:
        """Return deterministic dependency-valid order or raise on invalid graphs."""
        result = self.validate()
        if not result.valid:
            codes = ", ".join(item.code for item in result.errors[:8])
            raise ValueError(f"Workflow graph is invalid: {codes}")
        return result.topological_order

    def _error(self, code: str, message: str, subject_id: str | None = None) -> None:
        self._errors.append(
            ValidationError(code=code, message=message, subject_id=subject_id)
        )

    def _warning(self, code: str, message: str, subject_id: str | None = None) -> None:
        self._warnings.append(
            ValidationError(
                code=code,
                message=message,
                subject_id=subject_id,
                severity=ValidationSeverity.WARNING,
            )
        )

    @staticmethod
    def _finding_key(item: ValidationError) -> tuple[str, str, str, str]:
        return (
            item.severity.value,
            item.code,
            item.subject_id or "",
            item.message,
        )

    def _validate_unique_identifiers(self) -> None:
        collections: tuple[tuple[str, Iterable[str]], ...] = (
            ("NODE", (item.node_identifier for item in self.graph.nodes)),
            ("EDGE", (item.edge_identifier for item in self.graph.edges)),
            ("BINDING", (item.binding_identifier for item in self.graph.bindings)),
            (
                "DEPENDENCY",
                (item.dependency_identifier for item in self.graph.dependencies),
            ),
            (
                "SWEEP",
                (item.sweep_identifier for item in self.graph.parameter_sweeps),
            ),
            (
                "COMPARISON_GROUP",
                (item.group_identifier for item in self.graph.comparison_groups),
            ),
        )
        for kind, identifiers_iter in collections:
            identifiers = list(identifiers_iter)
            for identifier in sorted(set(identifiers)):
                if identifiers.count(identifier) > 1:
                    self._error(
                        f"DUPLICATE_{kind}_ID",
                        f"Duplicate {kind.lower().replace('_', ' ')} identifier.",
                        identifier,
                    )

    def _validate_node_references(self) -> None:
        known = set(self._nodes)
        for edge in self.graph.edges:
            if edge.source_node_id not in known:
                self._error(
                    "MISSING_SOURCE_NODE",
                    "Edge references an unknown source node.",
                    edge.edge_identifier,
                )
            if edge.target_node_id not in known:
                self._error(
                    "MISSING_TARGET_NODE",
                    "Edge references an unknown target node.",
                    edge.edge_identifier,
                )
            if edge.source_node_id == edge.target_node_id:
                self._error(
                    "SELF_EDGE",
                    "An edge cannot connect a node to itself.",
                    edge.edge_identifier,
                )
        for boundary, identifiers in (
            ("ROOT", self.graph.root_node_ids),
            ("TERMINAL", self.graph.terminal_node_ids),
        ):
            for identifier in identifiers:
                if identifier not in known:
                    self._error(
                        f"MISSING_{boundary}_NODE",
                        f"Declared {boundary.lower()} node does not exist.",
                        identifier,
                    )

    def _validate_semantic_duplicates(self) -> None:
        seen_edges: dict[tuple[object, ...], str] = {}
        for edge in self.graph.edges:
            key = (
                edge.edge_kind,
                edge.source_node_id,
                edge.target_node_id,
                edge.source_port_id,
                edge.target_port_id,
                edge.condition.fingerprint if edge.condition else None,
            )
            previous = seen_edges.get(key)
            if previous is not None:
                self._error(
                    "DUPLICATE_EDGE_SEMANTICS",
                    f"Edge duplicates the semantics of '{previous}'.",
                    edge.edge_identifier,
                )
            else:
                seen_edges[key] = edge.edge_identifier

        seen_bindings: dict[tuple[str, str, str, str], str] = {}
        for binding in self.graph.bindings:
            key = (
                binding.source_node_id,
                binding.source_port_id,
                binding.destination_node_id,
                binding.destination_port_id,
            )
            previous = seen_bindings.get(key)
            if previous is not None:
                self._error(
                    "DUPLICATE_BINDING_SEMANTICS",
                    f"Binding duplicates the semantics of '{previous}'.",
                    binding.binding_identifier,
                )
            else:
                seen_bindings[key] = binding.binding_identifier

    def _validate_ports_and_bindings(self) -> None:
        for edge in self.graph.edges:
            source = self._nodes.get(edge.source_node_id)
            target = self._nodes.get(edge.target_node_id)
            if source is None or target is None:
                continue
            if (edge.source_port_id is None) != (edge.target_port_id is None):
                self._error(
                    "INCOMPLETE_EDGE_PORT_PAIR",
                    "An edge must declare both source and target ports or neither.",
                    edge.edge_identifier,
                )
                continue
            if edge.source_port_id is not None:
                source_port = next(
                    (
                        item
                        for item in source.output_ports
                        if item.port_identifier == edge.source_port_id
                    ),
                    None,
                )
                target_port = next(
                    (
                        item
                        for item in target.input_ports
                        if item.port_identifier == edge.target_port_id
                    ),
                    None,
                )
                if source_port is None:
                    self._error(
                        "INVALID_SOURCE_PORT",
                        "Edge references an unknown source output port.",
                        edge.edge_identifier,
                    )
                if target_port is None:
                    self._error(
                        "INVALID_TARGET_PORT",
                        "Edge references an unknown target input port.",
                        edge.edge_identifier,
                    )
                if source_port is not None and target_port is not None:
                    self._validate_port_compatibility(
                        source_port,
                        target_port,
                        subject_id=edge.edge_identifier,
                    )

        binding_counts: dict[tuple[str, str], int] = {}
        for binding in self.graph.bindings:
            source = self._nodes.get(binding.source_node_id)
            target = self._nodes.get(binding.destination_node_id)
            if source is None:
                self._error(
                    "MISSING_BINDING_SOURCE_NODE",
                    "Binding references an unknown source node.",
                    binding.binding_identifier,
                )
                continue
            if target is None:
                self._error(
                    "MISSING_BINDING_DESTINATION_NODE",
                    "Binding references an unknown destination node.",
                    binding.binding_identifier,
                )
                continue
            source_port = next(
                (
                    item
                    for item in source.output_ports
                    if item.port_identifier == binding.source_port_id
                ),
                None,
            )
            target_port = next(
                (
                    item
                    for item in target.input_ports
                    if item.port_identifier == binding.destination_port_id
                ),
                None,
            )
            if source_port is None:
                self._error(
                    "INVALID_BINDING_SOURCE_PORT",
                    "Binding references an unknown source output port.",
                    binding.binding_identifier,
                )
            if target_port is None:
                self._error(
                    "INVALID_BINDING_DESTINATION_PORT",
                    "Binding references an unknown destination input port.",
                    binding.binding_identifier,
                )
            if source_port is not None and target_port is not None:
                self._validate_port_compatibility(
                    source_port,
                    target_port,
                    subject_id=binding.binding_identifier,
                )
                key = (binding.destination_node_id, binding.destination_port_id)
                binding_counts[key] = binding_counts.get(key, 0) + 1

        for node in self.graph.nodes:
            for port in node.input_ports:
                count = binding_counts.get((node.node_identifier, port.port_identifier), 0)
                if count > 1 and not port.multiple:
                    self._error(
                        "MULTIPLE_BINDINGS_FOR_SINGULAR_INPUT",
                        "A singular input port has multiple bindings.",
                        node.node_identifier,
                    )
                if (
                    port.required
                    and port.default_value is None
                    and not port.external
                    and count == 0
                ):
                    self._error(
                        "MISSING_REQUIRED_INPUT",
                        f"Required input port '{port.port_identifier}' is unsatisfied.",
                        node.node_identifier,
                    )

    def _validate_port_compatibility(
        self, source_port: object, target_port: object, *, subject_id: str
    ) -> None:
        if source_port.port_type is not target_port.port_type:  # type: ignore[attr-defined]
            self._error(
                "INCOMPATIBLE_PORT_TYPE",
                "Source and destination port types are incompatible.",
                subject_id,
            )
            return
        if (
            source_port.artifact_kind is not None  # type: ignore[attr-defined]
            and target_port.artifact_kind is not None  # type: ignore[attr-defined]
            and source_port.artifact_kind != target_port.artifact_kind  # type: ignore[attr-defined]
        ):
            self._error(
                "INCOMPATIBLE_ARTIFACT_KIND",
                "Source and destination artifact kinds are incompatible.",
                subject_id,
            )

    def _validate_boundaries(self) -> None:
        known = set(self._nodes)
        incoming: dict[str, int] = {item: 0 for item in known}
        outgoing: dict[str, int] = {item: 0 for item in known}
        for source, target in self._dependency_pairs():
            if source in known and target in known:
                outgoing[source] += 1
                incoming[target] += 1
        for root in self.graph.root_node_ids:
            if root in known and incoming[root]:
                self._error(
                    "ROOT_HAS_INCOMING_DEPENDENCY",
                    "A root node cannot have incoming dependencies.",
                    root,
                )
        for terminal in self.graph.terminal_node_ids:
            if terminal in known and outgoing[terminal]:
                self._error(
                    "TERMINAL_HAS_OUTGOING_DEPENDENCY",
                    "A terminal node cannot have outgoing dependencies.",
                    terminal,
                )
        for node_id in sorted(known):
            if incoming[node_id] == 0 and node_id not in self.graph.root_node_ids:
                self._error(
                    "UNDECLARED_ROOT_NODE",
                    "A node without incoming dependencies must be declared as a root.",
                    node_id,
                )
            if outgoing[node_id] == 0 and node_id not in self.graph.terminal_node_ids:
                self._error(
                    "UNDECLARED_TERMINAL_NODE",
                    "A node without outgoing dependencies must be declared as terminal.",
                    node_id,
                )

    def _validate_dependencies(self) -> None:
        edge_pairs = {
            (item.source_node_id, item.target_node_id) for item in self.graph.edges
        }
        seen_pairs: set[tuple[str, str]] = set()
        for dependency in self.graph.dependencies:
            if dependency.subject_node_id not in self._nodes:
                self._error(
                    "UNKNOWN_DEPENDENCY_SUBJECT",
                    "Dependency references an unknown subject node.",
                    dependency.dependency_identifier,
                )
            for prerequisite in dependency.prerequisite_node_ids:
                if prerequisite not in self._nodes:
                    self._error(
                        "UNKNOWN_DEPENDENCY_PREREQUISITE",
                        "Dependency references an unknown prerequisite node.",
                        dependency.dependency_identifier,
                    )
                pair = (prerequisite, dependency.subject_node_id)
                if pair in seen_pairs:
                    self._error(
                        "DUPLICATE_DEPENDENCY_SEMANTICS",
                        "The same prerequisite relationship is declared more than once.",
                        dependency.dependency_identifier,
                    )
                seen_pairs.add(pair)
                if pair not in edge_pairs:
                    self._error(
                        "DEPENDENCY_WITHOUT_EDGE",
                        "Every explicit dependency must have a corresponding graph edge.",
                        dependency.dependency_identifier,
                    )

    def _validate_sweeps(self) -> None:
        total = 0
        prefixes: set[str] = set()
        for sweep in self.graph.parameter_sweeps:
            total += len(sweep.parameter_values)
            node = self._nodes.get(sweep.base_node_id)
            if node is None:
                self._error(
                    "UNKNOWN_SWEEP_BASE",
                    "Parameter sweep references an unknown base node.",
                    sweep.sweep_identifier,
                )
            elif node.node_kind in {
                NodeKind.APPROVAL_GATE,
                NodeKind.CONDITIONAL_BRANCH,
                NodeKind.COMPARISON,
                NodeKind.AGGREGATION,
                NodeKind.DATA_SINK,
            }:
                self._error(
                    "INVALID_SWEEP_BASE_KIND",
                    "Parameter sweeps must reference an executable or data-source node.",
                    sweep.sweep_identifier,
                )
            prefix = sweep.generated_node_prefix or f"{sweep.base_node_id}.sweep"
            if prefix in prefixes:
                self._error(
                    "DUPLICATE_SWEEP_PREFIX",
                    "Generated sweep node prefixes must be unique.",
                    sweep.sweep_identifier,
                )
            prefixes.add(prefix)
        if total > _MAX_TOTAL_SWEEP_EXPANSIONS:
            self._error(
                "EXCESSIVE_TOTAL_SWEEP_EXPANSION",
                "Combined sweep expansion exceeds the graph maximum.",
            )

    def _validate_comparison_groups(self) -> None:
        memberships: dict[str, str] = {}
        for group in self.graph.comparison_groups:
            for member in group.member_node_ids:
                if member not in self._nodes:
                    self._error(
                        "UNKNOWN_COMPARISON_MEMBER",
                        "Comparison group references an unknown member node.",
                        group.group_identifier,
                    )
                previous = memberships.get(member)
                if previous is not None:
                    self._error(
                        "AMBIGUOUS_COMPARISON_MEMBERSHIP",
                        f"Comparison member already belongs to '{previous}'.",
                        group.group_identifier,
                    )
                memberships[member] = group.group_identifier
            if group.aggregation_node_id is not None:
                aggregation = self._nodes.get(group.aggregation_node_id)
                if aggregation is None:
                    self._error(
                        "UNKNOWN_AGGREGATION_NODE",
                        "Comparison group references an unknown aggregation node.",
                        group.group_identifier,
                    )
                elif aggregation.node_kind not in {
                    NodeKind.AGGREGATION,
                    NodeKind.COMPARISON,
                }:
                    self._error(
                        "INVALID_AGGREGATION_NODE_KIND",
                        "Comparison aggregation node has an incompatible node kind.",
                        group.group_identifier,
                    )
                if group.aggregation_node_id in group.member_node_ids:
                    self._error(
                        "AGGREGATION_IS_COMPARISON_MEMBER",
                        "Aggregation node cannot also be a comparison member.",
                        group.group_identifier,
                    )

    def _validate_policies(self) -> None:
        graph_fingerprint = self.graph.definition_fingerprint
        for node in self.graph.nodes:
            recovery = node.failure_recovery_policy
            if recovery is not None and recovery.on_failure is FailureAction.FALLBACK:
                if recovery.fallback_node_id not in self._nodes:
                    self._error(
                        "UNKNOWN_FALLBACK_NODE",
                        "Failure policy references an unknown fallback node.",
                        node.node_identifier,
                    )
                elif recovery.fallback_node_id == node.node_identifier:
                    self._error(
                        "SELF_FALLBACK_NODE",
                        "A node cannot fall back to itself.",
                        node.node_identifier,
                    )
                elif not any(
                    edge.source_node_id == node.node_identifier
                    and edge.target_node_id == recovery.fallback_node_id
                    for edge in self.graph.edges
                ):
                    self._error(
                        "UNREACHABLE_FALLBACK_NODE",
                        "Fallback node must be a direct declared successor of the failing node.",
                        node.node_identifier,
                    )
            policy = node.execution_policy
            if policy is not None:
                for approval in policy.approval_requirements:
                    if (
                        approval.graph_identifier != self.graph.graph_identifier
                        or approval.graph_version != self.graph.version
                        or (
                            approval.graph_definition_fingerprint is not None
                            and approval.graph_definition_fingerprint != graph_fingerprint
                        )
                        or approval.node_identifier != node.node_identifier
                    ):
                        self._error(
                            "APPROVAL_CONTEXT_MISMATCH",
                            "Node approval does not bind the exact graph and node context.",
                            approval.approval_identifier,
                        )
            if node.node_kind is NodeKind.QUANTUM_IBM:
                quantum = node.quantum_escalation_policy
                if quantum is None:
                    self._error(
                        "MISSING_QUANTUM_ESCALATION_POLICY",
                        "IBM quantum node lacks an escalation policy.",
                        node.node_identifier,
                    )
                elif quantum.allow_ibm_submission:
                    approval_ids = {
                        item.approval_identifier
                        for item in (policy.approval_requirements if policy else ())
                    }
                    if not set(quantum.required_approvals).issubset(approval_ids):
                        self._error(
                            "MISSING_QUANTUM_APPROVAL_BINDING",
                            "Quantum escalation approvals are not bound to node approvals.",
                            node.node_identifier,
                        )
        global_policy = self.graph.global_execution_policy
        if global_policy is not None:
            for approval in global_policy.approval_requirements:
                if (
                    approval.graph_identifier != self.graph.graph_identifier
                    or approval.graph_version != self.graph.version
                    or (
                        approval.graph_definition_fingerprint is not None
                        and approval.graph_definition_fingerprint != graph_fingerprint
                    )
                ):
                    self._error(
                        "GLOBAL_APPROVAL_CONTEXT_MISMATCH",
                        "Global approval does not bind the exact graph context.",
                        approval.approval_identifier,
                    )

    def _validate_reachability(self) -> None:
        known = set(self._nodes)
        adjacency = self._adjacency()
        reachable: set[str] = set()
        stack = sorted(
            (item for item in self.graph.root_node_ids if item in known),
            reverse=True,
        )
        while stack:
            node_id = stack.pop()
            if node_id in reachable:
                continue
            reachable.add(node_id)
            for target in reversed(adjacency.get(node_id, ())):
                if target not in reachable:
                    stack.append(target)
        for node_id in sorted(known - reachable):
            self._error(
                "UNREACHABLE_NODE",
                "Node is not reachable from any declared root.",
                node_id,
            )

    def _dependency_pairs(self) -> set[tuple[str, str]]:
        pairs = {
            (item.source_node_id, item.target_node_id) for item in self.graph.edges
        }
        for dependency in self.graph.dependencies:
            pairs.update(
                (prerequisite, dependency.subject_node_id)
                for prerequisite in dependency.prerequisite_node_ids
            )
        return pairs

    def _adjacency(self) -> dict[str, tuple[str, ...]]:
        adjacency: dict[str, set[str]] = {item: set() for item in self._nodes}
        for source, target in self._dependency_pairs():
            if source in adjacency and target in adjacency:
                adjacency[source].add(target)
        return {
            source: tuple(sorted(targets)) for source, targets in adjacency.items()
        }

    def _try_topological_order(self) -> tuple[str, ...] | None:
        known = set(self._nodes)
        adjacency = self._adjacency()
        indegree = {item: 0 for item in known}
        for targets in adjacency.values():
            for target in targets:
                indegree[target] += 1
        heap = [item for item, degree in indegree.items() if degree == 0]
        heapq.heapify(heap)
        ordered: list[str] = []
        while heap:
            source = heapq.heappop(heap)
            ordered.append(source)
            for target in adjacency[source]:
                indegree[target] -= 1
                if indegree[target] == 0:
                    heapq.heappush(heap, target)
        if len(ordered) != len(known):
            return None
        return tuple(ordered)

    def _calculate_maximum_depth(self, order: tuple[str, ...]) -> int:
        adjacency = self._adjacency()
        depth = {item: 1 for item in order}
        for source in order:
            for target in adjacency[source]:
                depth[target] = max(depth[target], depth[source] + 1)
        return max(depth.values(), default=0)
