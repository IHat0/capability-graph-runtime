export type WorkflowGraphStatus =
  | 'defined'
  | 'validated'
  | 'awaiting_approval'
  | 'ready'
  | 'queued'
  | 'running'
  | 'paused'
  | 'succeeded'
  | 'failed'
  | 'blocked'
  | 'cancelled'
  | 'recovered'

export type WorkflowNodeStatus =
  | 'pending'
  | 'ready'
  | 'queued'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'retryable'
  | 'retry_scheduled'
  | 'blocked'
  | 'skipped_by_condition'
  | 'skipped_by_policy'
  | 'cancelled'
  | 'awaiting_approval'
  | 'recovered'

export interface WorkflowNodeDefinition {
  node_identifier: string
  node_kind: string
  capability_identity: string | null
  execution_target: string | null
  molecular_project_identifier: string | null
  molecular_system_identifiers: string[]
  molecular_region_identifiers: string[]
  estimated_cost: number | null
  estimated_cost_currency: string | null
  verification_requirements: string[]
}

export interface WorkflowEdgeDefinition {
  edge_identifier: string
  edge_kind: string
  source_node_id: string
  target_node_id: string
  source_port_id: string | null
  target_port_id: string | null
}

export interface WorkflowGraphDefinitionResponse {
  graph_identifier: string
  version: number
  definition_fingerprint: string
  nodes: WorkflowNodeDefinition[]
  edges: WorkflowEdgeDefinition[]
  root_node_ids: string[]
  terminal_node_ids: string[]
  metadata: null | {
    tenant_identifier: string | null
    objective_identifier: string | null
    molecular_project_identifier: string | null
    molecular_system_identifiers: string[]
    candidate_plan_fingerprint: string | null
    rejected_alternative_identifiers: string[]
    unresolved_requirement_identifiers: string[]
    unresolved_goal_identifiers: string[]
  }
}

export interface WorkflowGraphStateResponse {
  graph_run_identifier: string
  graph_identifier: string
  graph_version: number
  graph_definition_fingerprint: string
  status: WorkflowGraphStatus
  tenant_identifier: string
  created_by_principal: string
  created_at_epoch: number
  started_at_epoch: number | null
  completed_at_epoch: number | null
  current_node_id: string | null
  total_nodes: number
  completed_nodes: number
  failed_nodes: number
  skipped_nodes: number
  retry_count: number
  budget_status: string
  error_message: string | null
}

export interface WorkflowNodeStateResponse {
  node_id: string
  graph_run_identifier: string
  status: WorkflowNodeStatus
  attempt_number: number
  max_attempts: number
  started_at_epoch: number | null
  completed_at_epoch: number | null
  error_code: string | null
  error_message: string | null
  retry_reason: string | null
  condition_outcome: string | null
  approval_status: string | null
  cost_consumed: number | null
  produced_artifact_types: string[]
  execution_metadata: Record<string, string>
}

export interface WorkflowApprovalStateResponse {
  approval_id: string
  node_id: string | null
  status: 'pending' | 'granted' | 'denied' | 'expired'
  expiry_epoch: number | null
  decision_reason: string | null
}

export interface WorkflowDecisionStateResponse {
  decision_identifier: string
  decision_kind: string
  subject_identifier: string
  outcome: string
  decided_at_epoch: number
  related_node_ids: string[]
}

export interface WorkflowRetryStateResponse {
  retry_id: string
  node_id: string
  attempt_number: number
  original_failure_code: string
  original_failure_message: string
  scheduled_at_epoch: number
  executed_at_epoch: number | null
  outcome: 'succeeded' | 'failed' | 'cancelled' | null
}

export interface WorkflowBudgetStateResponse {
  status: string
  planning_estimates: Record<string, number>
  reserved_budget: Record<string, number>
  actual_consumption: Record<string, number>
  unknown_consumption: Record<string, number>
  ceiling_values: Record<string, number>
  remaining_budget: Record<string, number>
  terminal_over_budget: boolean
}

export interface WorkflowRunSnapshotResponse {
  revision: number
  snapshot_fingerprint: string
  graph_state: WorkflowGraphStateResponse
  node_states: WorkflowNodeStateResponse[]
  approval_states: WorkflowApprovalStateResponse[]
  decision_states: WorkflowDecisionStateResponse[]
  budget_state: WorkflowBudgetStateResponse
}

export interface WorkflowNodesResponse {
  graph_run_identifier: string
  nodes: WorkflowNodeStateResponse[]
}

export interface WorkflowEvidenceResponse {
  graph_run_identifier: string
  graph_definition_fingerprint: string
  snapshot_fingerprint: string
  revision: number
  decisions: WorkflowDecisionStateResponse[]
  retries: WorkflowRetryStateResponse[]
  approvals: WorkflowApprovalStateResponse[]
  artifacts: Array<{ artifact_identifier: string; content_sha256: string }>
}

const IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$/
const SHA256 = /^[0-9a-f]{64}$/
const GRAPH_STATUSES = new Set<WorkflowGraphStatus>([
  'defined', 'validated', 'awaiting_approval', 'ready', 'queued', 'running',
  'paused', 'succeeded', 'failed', 'blocked', 'cancelled', 'recovered',
])
const NODE_STATUSES = new Set<WorkflowNodeStatus>([
  'pending', 'ready', 'queued', 'running', 'succeeded', 'failed', 'retryable',
  'retry_scheduled', 'blocked', 'skipped_by_condition', 'skipped_by_policy',
  'cancelled', 'awaiting_approval', 'recovered',
])

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function identifier(value: unknown): value is string {
  return typeof value === 'string' && IDENTIFIER.test(value)
}

function optionalIdentifier(value: unknown): value is string | null {
  return value === null || identifier(value)
}

function finite(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function safeInteger(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value)
}

function optionalFinite(value: unknown): value is number | null {
  return value === null || finite(value)
}

function strings(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === 'string')
}

function numericMap(value: unknown): value is Record<string, number> {
  return record(value) && Object.entries(value).every(([key, item]) => identifier(key) && finite(item))
}

function malformed(message: string): never {
  throw new Error(message)
}

function parseNodeDefinition(value: unknown): WorkflowNodeDefinition {
  if (!record(value) || !identifier(value.node_identifier) || !identifier(value.node_kind)
    || !optionalIdentifier(value.capability_identity) || !optionalIdentifier(value.execution_target)
    || !optionalIdentifier(value.molecular_project_identifier)
    || !strings(value.molecular_system_identifiers) || !strings(value.molecular_region_identifiers)
    || !optionalFinite(value.estimated_cost) || !optionalIdentifier(value.estimated_cost_currency)
    || !strings(value.verification_requirements)) {
    malformed('The backend returned malformed workflow node data.')
  }
  return value as unknown as WorkflowNodeDefinition
}

function parseEdgeDefinition(value: unknown): WorkflowEdgeDefinition {
  if (!record(value) || !identifier(value.edge_identifier) || !identifier(value.edge_kind)
    || !identifier(value.source_node_id) || !identifier(value.target_node_id)
    || !optionalIdentifier(value.source_port_id) || !optionalIdentifier(value.target_port_id)) {
    malformed('The backend returned malformed workflow edge data.')
  }
  return value as unknown as WorkflowEdgeDefinition
}

export function parseWorkflowGraph(value: unknown): WorkflowGraphDefinitionResponse {
  if (!record(value) || !identifier(value.graph_identifier)
    || !safeInteger(value.version) || (value.version as number) < 1
    || typeof value.definition_fingerprint !== 'string' || !SHA256.test(value.definition_fingerprint)
    || !Array.isArray(value.nodes) || !Array.isArray(value.edges)
    || !strings(value.root_node_ids) || !strings(value.terminal_node_ids)) {
    malformed('The backend returned malformed workflow graph data.')
  }
  const nodes = value.nodes.map(parseNodeDefinition)
  const edges = value.edges.map(parseEdgeDefinition)
  const metadata = value.metadata
  if (metadata !== null && (!record(metadata)
    || !optionalIdentifier(metadata.tenant_identifier)
    || !optionalIdentifier(metadata.objective_identifier)
    || !optionalIdentifier(metadata.molecular_project_identifier)
    || !strings(metadata.molecular_system_identifiers)
    || !strings(metadata.rejected_alternative_identifiers)
    || !strings(metadata.unresolved_requirement_identifiers)
    || !strings(metadata.unresolved_goal_identifiers)
    || (metadata.candidate_plan_fingerprint !== null
      && (typeof metadata.candidate_plan_fingerprint !== 'string'
        || !SHA256.test(metadata.candidate_plan_fingerprint))))) {
    malformed('The backend returned malformed workflow provenance data.')
  }
  return { ...value, nodes, edges } as unknown as WorkflowGraphDefinitionResponse
}

function parseGraphState(value: unknown): WorkflowGraphStateResponse {
  if (!record(value) || !identifier(value.graph_run_identifier)
    || !identifier(value.graph_identifier) || !safeInteger(value.graph_version)
    || typeof value.graph_definition_fingerprint !== 'string' || !SHA256.test(value.graph_definition_fingerprint)
    || !GRAPH_STATUSES.has(value.status as WorkflowGraphStatus)
    || !identifier(value.tenant_identifier) || !identifier(value.created_by_principal)
    || !finite(value.created_at_epoch) || !optionalFinite(value.started_at_epoch)
    || !optionalFinite(value.completed_at_epoch) || !optionalIdentifier(value.current_node_id)
    || ![value.total_nodes, value.completed_nodes, value.failed_nodes, value.skipped_nodes, value.retry_count]
      .every((item) => safeInteger(item) && item >= 0)
    || !identifier(value.budget_status)
    || !(value.error_message === null || typeof value.error_message === 'string')) {
    malformed('The backend returned malformed workflow run state.')
  }
  return value as unknown as WorkflowGraphStateResponse
}

function parseNodeState(value: unknown): WorkflowNodeStateResponse {
  if (!record(value) || !identifier(value.node_id) || !identifier(value.graph_run_identifier)
    || !NODE_STATUSES.has(value.status as WorkflowNodeStatus)
    || !safeInteger(value.attempt_number) || !safeInteger(value.max_attempts)
    || !optionalFinite(value.started_at_epoch) || !optionalFinite(value.completed_at_epoch)
    || !optionalIdentifier(value.error_code)
    || !(value.error_message === null || typeof value.error_message === 'string')
    || !(value.retry_reason === null || typeof value.retry_reason === 'string')
    || !optionalIdentifier(value.condition_outcome) || !optionalIdentifier(value.approval_status)
    || !optionalFinite(value.cost_consumed) || !strings(value.produced_artifact_types)
    || !record(value.execution_metadata)) {
    malformed('The backend returned malformed workflow node state.')
  }
  return value as unknown as WorkflowNodeStateResponse
}

function parseApproval(value: unknown): WorkflowApprovalStateResponse {
  if (!record(value) || !identifier(value.approval_id) || !optionalIdentifier(value.node_id)
    || !['pending', 'granted', 'denied', 'expired'].includes(String(value.status))
    || !optionalFinite(value.expiry_epoch)
    || !(value.decision_reason === null || typeof value.decision_reason === 'string')) {
    malformed('The backend returned malformed workflow approval evidence.')
  }
  return value as unknown as WorkflowApprovalStateResponse
}

function parseDecision(value: unknown): WorkflowDecisionStateResponse {
  if (!record(value) || !identifier(value.decision_identifier) || !identifier(value.decision_kind)
    || !identifier(value.subject_identifier) || !identifier(value.outcome)
    || !finite(value.decided_at_epoch) || !strings(value.related_node_ids)) {
    malformed('The backend returned malformed workflow decision evidence.')
  }
  return value as unknown as WorkflowDecisionStateResponse
}

function parseRetry(value: unknown): WorkflowRetryStateResponse {
  if (!record(value) || !identifier(value.retry_id) || !identifier(value.node_id)
    || !safeInteger(value.attempt_number) || value.attempt_number < 1
    || !identifier(value.original_failure_code)
    || typeof value.original_failure_message !== 'string'
    || !finite(value.scheduled_at_epoch) || !optionalFinite(value.executed_at_epoch)
    || !(value.outcome === null || ['succeeded', 'failed', 'cancelled'].includes(String(value.outcome)))) {
    malformed('The backend returned malformed workflow retry evidence.')
  }
  return value as unknown as WorkflowRetryStateResponse
}

function parseBudget(value: unknown): WorkflowBudgetStateResponse {
  if (!record(value) || !identifier(value.status)
    || !numericMap(value.planning_estimates) || !numericMap(value.reserved_budget)
    || !numericMap(value.actual_consumption) || !numericMap(value.unknown_consumption)
    || !numericMap(value.ceiling_values) || !numericMap(value.remaining_budget)
    || typeof value.terminal_over_budget !== 'boolean') {
    malformed('The backend returned malformed workflow budget evidence.')
  }
  return value as unknown as WorkflowBudgetStateResponse
}

export function parseWorkflowRun(value: unknown): WorkflowRunSnapshotResponse {
  if (!record(value) || !safeInteger(value.revision)
    || typeof value.snapshot_fingerprint !== 'string' || !SHA256.test(value.snapshot_fingerprint)
    || !Array.isArray(value.node_states) || !Array.isArray(value.approval_states)
    || !Array.isArray(value.decision_states)) {
    malformed('The backend returned malformed workflow run data.')
  }
  return {
    ...value,
    graph_state: parseGraphState(value.graph_state),
    node_states: value.node_states.map(parseNodeState),
    approval_states: value.approval_states.map(parseApproval),
    decision_states: value.decision_states.map(parseDecision),
    budget_state: parseBudget(value.budget_state),
  } as unknown as WorkflowRunSnapshotResponse
}

export function parseWorkflowNodes(value: unknown): WorkflowNodesResponse {
  if (!record(value) || !identifier(value.graph_run_identifier) || !Array.isArray(value.nodes)) {
    malformed('The backend returned malformed workflow node collection data.')
  }
  return {
    graph_run_identifier: value.graph_run_identifier,
    nodes: value.nodes.map(parseNodeState),
  }
}

export function parseWorkflowEvidence(value: unknown): WorkflowEvidenceResponse {
  if (!record(value) || !identifier(value.graph_run_identifier)
    || typeof value.graph_definition_fingerprint !== 'string' || !SHA256.test(value.graph_definition_fingerprint)
    || typeof value.snapshot_fingerprint !== 'string' || !SHA256.test(value.snapshot_fingerprint)
    || !safeInteger(value.revision) || !Array.isArray(value.decisions)
    || !Array.isArray(value.retries) || !Array.isArray(value.approvals)
    || !Array.isArray(value.artifacts)) {
    malformed('The backend returned malformed workflow evidence.')
  }
  const artifacts = value.artifacts.map((artifact) => {
    if (!record(artifact) || !identifier(artifact.artifact_identifier)
      || typeof artifact.content_sha256 !== 'string' || !SHA256.test(artifact.content_sha256)) {
      malformed('The backend returned malformed workflow artifact evidence.')
    }
    return artifact as unknown as { artifact_identifier: string; content_sha256: string }
  })
  return {
    ...value,
    decisions: value.decisions.map(parseDecision),
    retries: value.retries.map(parseRetry),
    approvals: value.approvals.map(parseApproval),
    artifacts,
  } as unknown as WorkflowEvidenceResponse
}
