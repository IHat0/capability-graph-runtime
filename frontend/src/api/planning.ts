export type PlanningFeasibilityStatus = 'feasible' | 'conditional' | 'unknown' | 'infeasible'

export interface MolecularPlanningRequest {
  objective: Record<string, unknown>
  constraints: Record<string, unknown>
  resources: Record<string, unknown>
  requested_execution_target: string | null
  selected_capabilities: Array<{
    capability_name: string
    capability_version: { major: number; minor: number; patch: number }
  }>
}

export interface PlanningFinding {
  finding_identifier: string
  status: PlanningFeasibilityStatus
  explanation: string
  related_fact_identifiers: string[]
  evidence_artifacts: Array<{ artifact_identifier: string; content_sha256: string }>
}

export interface PlanningEstimate {
  estimate_type: string
  lower_bound: number | null
  expected_value: number | null
  upper_bound: number | null
  unit: string
  confidence: number | null
  estimation_basis: string
}

export interface PlanningFeasibility {
  status: PlanningFeasibilityStatus
  execution_target: string | null
  findings: PlanningFinding[]
  estimates: PlanningEstimate[]
  fidelity: null | {
    fidelity_level: string
    conditions: string[]
    limitations: string[]
  }
  unresolved_requirement_identifiers: string[]
  required_approvals: string[]
  required_verifications: Array<Record<string, unknown>>
}

export interface PlanningAssignment {
  assignment_identifier: string
  capability_name: string
  capability_version: { major: number; minor: number; patch: number }
  execution_target: string | null
  feasibility: PlanningFeasibility
}

export interface RejectedPlanningAlternative {
  alternative_identifier: string
  capability_name: string
  capability_version: { major: number; minor: number; patch: number }
  execution_target: string | null
  feasibility: PlanningFeasibility
  rejection_reasons: string[]
}

export interface CapabilityPlanningDeclaration {
  descriptor: {
    capability_name: string
    version: { major: number; minor: number; patch: number }
  }
  known_limitations: string[]
}

export interface CandidateResearchPlanResponse {
  project_identifier: string
  objective_identifier: string
  plan_identifier: string
  plan_fingerprint: string
  planning_facts_fingerprint: string
  plan: {
    plan_identifier: string
    objective_identifier: string
    project_identifier: string
    selected_assignments: PlanningAssignment[]
    rejected_alternatives: RejectedPlanningAlternative[]
    unresolved_requirement_identifiers: string[]
    unresolved_goal_identifiers: string[]
    aggregate_estimates: PlanningEstimate[]
    verification_requirements: Array<Record<string, unknown>>
    approval_requirements: string[]
    evidence_artifacts: Array<{ artifact_identifier: string; content_sha256: string }>
    planning_facts: { facts: Array<Record<string, unknown>> }
    [key: string]: unknown
  }
  capability_declarations: CapabilityPlanningDeclaration[]
}

const IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$/
const SHA256 = /^[0-9a-f]{64}$/
const STATUSES = new Set<PlanningFeasibilityStatus>(['feasible', 'conditional', 'unknown', 'infeasible'])
const CREDENTIAL_KEY = /(?:^|[._-])(api[_-]?key|authorization|credential|crn|password|secret|token)(?:$|[._-])/i

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function identifier(value: unknown): value is string {
  return typeof value === 'string' && IDENTIFIER.test(value)
}

function strings(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === 'string')
}

function safe(value: unknown): boolean {
  if (Array.isArray(value)) return value.every(safe)
  if (!record(value)) return true
  return Object.entries(value).every(([key, nested]) => !CREDENTIAL_KEY.test(key) && safe(nested))
}

function version(value: unknown): boolean {
  return record(value) && [value.major, value.minor, value.patch].every(
    (item) => typeof item === 'number' && Number.isSafeInteger(item) && item >= 0,
  )
}

function estimate(value: unknown): boolean {
  return record(value) && identifier(value.estimate_type) && identifier(value.unit)
    && typeof value.estimation_basis === 'string'
}

function finding(value: unknown): boolean {
  return record(value) && identifier(value.finding_identifier)
    && STATUSES.has(value.status as PlanningFeasibilityStatus)
    && typeof value.explanation === 'string'
    && strings(value.related_fact_identifiers) && Array.isArray(value.evidence_artifacts)
}

function feasibility(value: unknown): boolean {
  return record(value) && STATUSES.has(value.status as PlanningFeasibilityStatus)
    && (value.execution_target === null || identifier(value.execution_target))
    && Array.isArray(value.findings) && value.findings.every(finding)
    && Array.isArray(value.estimates) && value.estimates.every(estimate)
    && strings(value.unresolved_requirement_identifiers)
    && strings(value.required_approvals) && Array.isArray(value.required_verifications)
    && (value.fidelity === null || (record(value.fidelity)
      && identifier(value.fidelity.fidelity_level)
      && strings(value.fidelity.conditions) && strings(value.fidelity.limitations)))
}

function capability(value: unknown): boolean {
  return record(value) && identifier(value.capability_name) && version(value.capability_version)
    && (value.execution_target === null || identifier(value.execution_target))
    && feasibility(value.feasibility)
}

export function parseMolecularPlanningResponse(value: unknown): CandidateResearchPlanResponse {
  if (!record(value) || !safe(value)
    || !identifier(value.project_identifier) || !identifier(value.objective_identifier)
    || !identifier(value.plan_identifier) || typeof value.plan_fingerprint !== 'string'
    || !SHA256.test(value.plan_fingerprint) || !record(value.plan)
    || typeof value.planning_facts_fingerprint !== 'string'
    || !SHA256.test(value.planning_facts_fingerprint)
    || value.project_identifier !== value.plan.project_identifier
    || value.objective_identifier !== value.plan.objective_identifier
    || value.plan_identifier !== value.plan.plan_identifier
    || !Array.isArray(value.plan.selected_assignments)
    || !value.plan.selected_assignments.every(capability)
    || !Array.isArray(value.plan.rejected_alternatives)
    || !value.plan.rejected_alternatives.every((item) => capability(item)
      && record(item) && strings(item.rejection_reasons))
    || !strings(value.plan.unresolved_requirement_identifiers)
    || !strings(value.plan.unresolved_goal_identifiers)
    || !Array.isArray(value.plan.aggregate_estimates)
    || !value.plan.aggregate_estimates.every(estimate)
    || !Array.isArray(value.plan.verification_requirements)
    || !strings(value.plan.approval_requirements)
    || !Array.isArray(value.plan.evidence_artifacts)
    || !record(value.plan.planning_facts) || !Array.isArray(value.plan.planning_facts.facts)
    || !Array.isArray(value.capability_declarations)
    || !value.capability_declarations.every((item) => record(item) && record(item.descriptor)
      && identifier(item.descriptor.capability_name) && version(item.descriptor.version)
      && strings(item.known_limitations))) {
    throw new Error('The backend returned a malformed molecular planning response.')
  }
  return value as unknown as CandidateResearchPlanResponse
}
