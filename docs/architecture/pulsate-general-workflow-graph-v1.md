# Pulsate General Workflow Graph v1

## Status and purpose

This document defines Product Phase 4: General Workflow Graph. Phase 4 turns
an approved `CandidateResearchPlan` into a deterministic, persistent and
scientifically traceable directed workflow that can coordinate classical work,
local quantum work and selectively authorized IBM execution. It does not add
new scientific engines; Phase 5 owns engine families. It does not implement the
candidate-generation intelligence of Phase 7.

Phase 4 preserves the existing natural-language, planning, Qiskit, IBM,
verification and molecular-workspace spine. The graph supervises capability
invocations through a generic boundary rather than embedding chemistry or
molecule-specific algorithms in the orchestrator.

## Package ownership

- `cgr.science.planning` owns `CandidateResearchPlan` and planning evidence.
- `cgr.workflow_graph.contracts` owns immutable graph definitions and policies.
- `cgr.workflow_graph.state` owns immutable graph-run snapshots.
- `cgr.workflow_graph.validation` owns fail-closed static graph validation.
- `cgr.workflow_graph.compiler` owns deterministic plan-to-graph compilation.
- `cgr.workflow_graph.repository` owns graph-definition and run-state storage.
- `cgr.workflow_graph.capability_adapter` owns the generic invocation boundary.
- `cgr.workflow_graph.orchestrator` owns scheduling and state transitions.
- `cgr.pulsate_api.workflow_quantum` adapts the existing verified run
  coordinator without reimplementing IBM submission or verification controls.
- `cgr.pulsate_api.workflows` and `workflow_routes` own the authenticated
  application and HTTP surfaces.
- The frontend workflow workspace presents graph state alongside, and never in
  place of, the Mol* molecular workspace.

The kernel `CapabilityGraph` remains the capability-discovery structure. A
`WorkflowGraphDefinition` is a scientific execution plan. They are distinct
contracts and neither replaces the other.

## Canonical graph definition

A graph definition is immutable, bounded and SHA-256 fingerprinted. It contains:

- graph identity and version;
- nodes, ports, edges and bindings;
- explicit roots and terminal nodes;
- dependency declarations;
- declarative conditions;
- comparison groups;
- bounded parameter sweeps;
- retry, execution, approval and failure/recovery policies;
- cost and resource ceilings;
- selective quantum-escalation policy;
- objective, project, system, plan, capability and provenance metadata.

Collection order is canonicalized. Identifiers, text, mappings, nesting depth,
node/edge/binding counts, retry counts, sweep expansion and graph depth are
bounded. Graph contracts cannot contain Python, shell commands, templates or
arbitrary executable expressions.

The graph fingerprint covers the canonical definition. Exact approval state is
stored separately because an approval that contains the graph fingerprint
cannot be embedded in the same value from which that fingerprint is derived.
Runtime approval records bind the graph identity, version and definition
fingerprint.

## Static validation

`WorkflowGraphValidator` returns deterministic structured errors and does not
crash on malformed references. It validates:

- identity uniqueness and semantic duplicate edges/bindings;
- node, port, root and terminal references;
- port type and artifact-kind compatibility;
- required input satisfaction and multiplicity;
- self-edges, reachability and bounded depth;
- directed cycles and deterministic topological order;
- dependency identity, consistency and cycles;
- comparison membership and aggregation semantics;
- parameter-sweep bases and aggregate expansion limits;
- retry, approval, cost/resource, failure and quantum policy consistency;
- contradictory or ambiguous graph structure.

Iteration is represented by bounded sweep and retry policies. Uncontrolled
cyclic graphs are rejected.

## Deterministic compilation

`WorkflowGraphCompiler` is pure: it does not execute capabilities, access the
network, create subprocesses or submit quantum work. It converts an approved
`CandidateResearchPlan` into a graph while preserving:

- objective, molecular-project and molecular-system identities;
- candidate-plan, planning-resource and feasibility fingerprints;
- capability and version assignments;
- execution target and resource estimates;
- verification and approval requirements;
- expected artifact flow;
- planning limitations and provenance.

Equivalent canonical plans and policies produce equivalent canonical graphs.
IBM-targeted assignments are not compiled as executable IBM nodes unless the
compilation policy explicitly permits that target and names the required
approval identities. Compilation is not authorization.

## Persistence and integrity

Definitions and mutable run state are separated:

- graph definitions are immutable, versioned and published without replacement;
- graph runs are revisioned immutable snapshots stored through atomic replace;
- updates use optimistic revision checks;
- repository roots and every traversed component reject links, reparse points,
  traversal and containment escapes;
- canonical JSON, bounded byte sizes and content fingerprints detect corruption;
- existing identities cannot be silently overwritten.

A `WorkflowRunSnapshot` contains graph, node, edge, binding, external-input,
approval, retry, decision and budget state. Its fingerprint covers the complete
snapshot except the fingerprint field itself. A persisted snapshot with a
mismatched fingerprint or cross-run identity is rejected.

## State model

Graph and node transitions are explicit and validated. Graph states include
creation, validation, approval wait, readiness, queueing, running, pausing,
success, failure, blocking, cancellation and recovery. Node states distinguish
pending/readiness, queueing/running, success/failure, retryability and scheduled
retry, approval wait, conditional or policy skip, blocking, cancellation and
recovery.

Administrative states use attempt zero. An executed attempt must have a
positive attempt number. A successful process return is only adapter evidence;
the adapter result status, verification evidence and required artifacts decide
node success.

Terminal timestamps, count totals, retry limits, finite cost/resource values,
approval bindings and snapshot relationships are validated. Unknown consumption
is represented explicitly and is never converted into fabricated zero cost.

## Generic capability boundary

The orchestrator invokes `CapabilityAdapter` with a canonical
`CapabilityInvocation`. The invocation binds:

- graph and run identity;
- graph-definition fingerprint;
- node and deterministic invocation identity;
- attempt number;
- capability identity and execution target;
- canonical parameters, input values and artifact references;
- correlation and tenant identity.

A registry resolves an exact capability identity and fails closed when no
adapter exists. `CapabilityInvocationResult` distinguishes success, retryable
failure, non-retryable failure, blocking and cancellation, and carries only
bounded values, artifact references, known/unknown consumption and evidence
fingerprints. Phase 5 can register new adapters without changing the graph
architecture.

## Orchestration semantics

`WorkflowOrchestrator` selects ready nodes from satisfied dependencies using
canonical ordering. It supports bounded parallel execution of independent
nodes, while state publication remains atomic and revision checked.

The orchestrator provides:

- deterministic ready-node selection;
- external graph inputs and port-binding transfer;
- declarative conditional branches;
- bounded parameter-sweep expansion with deterministic generated identities;
- comparison groups and deterministic candidate selection;
- bounded retries with persisted not-before times and backoff;
- retryable versus terminal failures;
- partial retry without repeating successful independent work;
- stop, skip and fallback failure policies;
- approval pause, grant/deny/expiry and version-bound authorization;
- cost/resource reservation and actual-consumption enforcement;
- optional fail-closed known-consumption policy;
- safe cancellation with attributed decision evidence;
- persistent restart/resume and duplicate-work prevention;
- terminal-state and inconsistent-state rejection.

Every consequential branch, comparison, retry, approval, cancellation, budget
or recovery outcome is persisted as state or a canonical `DecisionState`.

## Declarative conditions

Conditions are closed, bounded data contracts. Supported families cover:

- upstream success or failure;
- verified artifact presence;
- verification or feasibility classification;
- bounded numeric comparison;
- approval result;
- remaining budget;
- explicit candidate-selection outcome.

The evaluator never executes arbitrary source text. Every decision records its
kind, subject, related nodes, inputs and outcome.

## Comparisons and parameter sweeps

A sweep expands one declared base node over a bounded set of canonical values.
Generated node and binding identities are deterministic. Aggregate expansion is
checked before execution and remains subject to graph-level cost, resource and
parallelism limits.

Comparison groups specify member nodes, completion semantics, optional
aggregation and deterministic selection criteria. Partial failures are
preserved rather than erased. The graph mechanism can support later discovery
loops, but Phase 4 does not generate scientific candidates itself.

## Approvals and authorization

Graph approvals and HTTP authorization are separate controls and both are
required. An approval record binds:

- approval identity;
- tenant, graph, version and graph-definition fingerprint;
- node and operation/capability identity;
- relevant input bindings;
- cost and resource ceilings;
- approver, correlation identity, decision and validity interval.

Approval of one graph version never authorizes a modified definition. API
routes also require the existing principal/grant boundary, tenant isolation,
enumeration-resistant not-found behavior and write auditing.

## Cost and resource enforcement

Planning estimates, reserved budget, actual known consumption, unknown
consumption, ceilings and remaining budget are stored separately. The
orchestrator checks reservations before invocation and applies reported actual
consumption afterward. Over-ceiling results block the node and graph and retain
the result as evidence. When `require_known_consumption` is enabled, unknown
consumption fails closed.

The production maximum parallelism is validated through
`PULSATE_WORKFLOW_MAX_PARALLELISM` and is bounded from 1 through 64. It is an
operational safety limit, not a performance claim.

## Selective quantum escalation

`RunCoordinatorWorkflowAdapter` is the only Phase 4 quantum bridge. It reuses
the existing `RunCoordinator`, approved-experiment resolver, local/IBM
preflight, idempotency, provenance, receipt and verification path.

For IBM nodes it requires the graph's quantum policy, exact bound approval and
an approved experiment source. It does not bypass credentials, cost
acknowledgement, preflight, backend restrictions, workload identity or
verification. Tests use fake coordinators; Phase 4 implementation and tests do
not submit a real IBM Quantum job.

## HTTP API

The authenticated v1 routes are:

- `POST /api/v1/workflows/compile`
- `GET /api/v1/workflows/{graph}/versions/{version}`
- `POST /api/v1/workflows/{graph}/versions/{version}/validate`
- `POST /api/v1/workflow-runs`
- `GET /api/v1/workflow-runs/{run}`
- `POST /api/v1/workflow-runs/{run}/approvals/{approval}`
- `POST /api/v1/workflow-runs/{run}/resume`
- `POST /api/v1/workflow-runs/{run}/cancel`
- `GET /api/v1/workflow-runs/{run}/nodes`
- `GET /api/v1/workflow-runs/{run}/evidence`

Requests are strict and bounded. Responses remove storage locations and pass
the existing public-response safety check. The surface returns controlled typed
errors and does not expose absolute paths, credentials, secrets or raw internal
exceptions.

## Frontend workspace

The workflow workspace is placed below the existing molecular workspace. It
provides controlled empty, loading, unauthorized, not-found, blocked, failed,
cancelled and active states. It displays:

- graph and run identities;
- node kinds, dependencies, states and attempts;
- classical, local-quantum and selective IBM targets;
- pending approvals and decisions;
- branch, comparison, retry and recovery evidence;
- cost/resource ceilings and consumption;
- produced artifact kinds;
- project linkage and graph/snapshot fingerprints.

The UI parses backend data through strict runtime guards and aborts stale
requests. It never receives repository paths.

## Observability and audit

The existing request security middleware provides request/correlation,
principal, tenant, protected action, resource and decision audit records.
Workflow routes add bounded-cardinality metrics and structured events for
compilation, validation, run creation/resume/cancellation, approvals and
failures. Orchestrator observer events include safe graph/run/node/attempt and
outcome fields. Arbitrary user payloads, molecule content, credentials and
absolute paths are excluded.

## Backup and recovery

The production persistence inventory includes the required `workflows/`
component with schema identity `pulsate-workflows/v1`. Offline coordinated
backup hashes graph definitions and run snapshots with the other repositories.
Restore publishes only into a new root and validates the complete inventory.

After restore, the service reopens persisted snapshots. Successful nodes and
external invocation identities remain recorded, so resume does not silently
repeat successful external work. Operators must still validate restored
readiness before traffic.

## Enterprise evidence

Phase 4 adds four mandatory producers to the existing evidence registry:

- `workflow-graph-backend`;
- `workflow-api-security`;
- `workflow-recovery`;
- `frontend-workflow-workspace`.

They extend, and do not weaken, E1-E7. Final E7 remains
`PENDING_REMOTE_EVIDENCE`; Phase 4 does not invent performance thresholds or
convert unavailable Docker, scanner or remote evidence into PASS.

## Explicit non-goals and limitations

Phase 4 does not:

- introduce RDKit, OpenMM, PySCF, Qiskit Nature or other Phase 5 adapters;
- generate discovery candidates or implement Phase 7 intelligence;
- permit arbitrary executable graph expressions;
- provide distributed scheduling, high availability or multi-region recovery;
- claim exactly-once execution against an external system that ignores the
  existing idempotency contract;
- invent actual cost when an adapter reports unknown consumption;
- submit an IBM job during tests;
- resolve the five existing performance-policy blockers;
- declare Pulsate enterprise-ready.

## Definition of done

Phase 4 is implemented when the compiler, graph/run repositories, generic
adapter boundary, complete orchestrator, verified quantum bridge, authenticated
API, frontend workspace, observability, backup/restore inventory, enterprise
gates, documentation and regression tests are all present and validated.
Environment-unavailable frontend, Docker, scanner or remote workflow evidence
must remain truthfully BLOCKED or PENDING until it actually runs.
