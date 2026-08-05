import { describe, expect, it } from 'vitest'
import {
  parseWorkflowEvidence,
  parseWorkflowGraph,
  parseWorkflowRun,
} from './workflows'

const hash = 'a'.repeat(64)

function graphFixture() {
  return {
    graph_identifier: 'workflow.test',
    version: 1,
    definition_fingerprint: hash,
    nodes: [{
      node_identifier: 'node.one',
      node_kind: 'classical',
      capability_identity: 'capability.one',
      execution_target: null,
      molecular_project_identifier: 'project.one',
      molecular_system_identifiers: ['system.one'],
      molecular_region_identifiers: [],
      estimated_cost: null,
      estimated_cost_currency: null,
      verification_requirements: [],
    }],
    edges: [],
    root_node_ids: ['node.one'],
    terminal_node_ids: ['node.one'],
    metadata: {
      tenant_identifier: 'tenant.one',
      objective_identifier: 'objective.one',
      molecular_project_identifier: 'project.one',
      molecular_system_identifiers: ['system.one'],
      candidate_plan_fingerprint: hash,
      rejected_alternative_identifiers: [],
      unresolved_requirement_identifiers: [],
      unresolved_goal_identifiers: [],
    },
  }
}

function runFixture() {
  return {
    revision: 1,
    snapshot_fingerprint: hash,
    graph_state: {
      graph_run_identifier: 'workflow-run.one',
      graph_identifier: 'workflow.test',
      graph_version: 1,
      graph_definition_fingerprint: hash,
      status: 'ready',
      tenant_identifier: 'tenant.one',
      created_by_principal: 'scientist.one',
      created_at_epoch: 1,
      started_at_epoch: null,
      completed_at_epoch: null,
      current_node_id: null,
      total_nodes: 1,
      completed_nodes: 0,
      failed_nodes: 0,
      skipped_nodes: 0,
      retry_count: 0,
      budget_status: 'within_budget',
      error_message: null,
    },
    node_states: [{
      node_id: 'node.one',
      graph_run_identifier: 'workflow-run.one',
      status: 'ready',
      attempt_number: 0,
      max_attempts: 1,
      started_at_epoch: null,
      completed_at_epoch: null,
      error_code: null,
      error_message: null,
      retry_reason: null,
      condition_outcome: null,
      approval_status: null,
      cost_consumed: null,
      produced_artifact_types: [],
      execution_metadata: {},
    }],
    approval_states: [],
    decision_states: [],
    budget_state: {
      status: 'within_budget',
      planning_estimates: {},
      reserved_budget: {},
      actual_consumption: {},
      unknown_consumption: {},
      ceiling_values: {},
      remaining_budget: {},
      terminal_over_budget: false,
    },
  }
}

describe('workflow API parsers', () => {
  it('accepts bounded graph and run evidence', () => {
    expect(parseWorkflowGraph(graphFixture()).graph_identifier).toBe('workflow.test')
    expect(parseWorkflowRun(runFixture()).graph_state.status).toBe('ready')
    expect(parseWorkflowEvidence({
      graph_run_identifier: 'workflow-run.one',
      graph_definition_fingerprint: hash,
      snapshot_fingerprint: hash,
      revision: 1,
      decisions: [],
      retries: [{
        retry_id: 'retry.one', node_id: 'node.one', attempt_number: 2,
        original_failure_code: 'temporary', original_failure_message: 'Retry later.',
        scheduled_at_epoch: 2, executed_at_epoch: null, outcome: null,
      }],
      approvals: [],
      artifacts: [{ artifact_identifier: 'artifact.one', content_sha256: hash }],
    }).retries).toHaveLength(1)
  })

  it('rejects malformed identities and unsafe artifact evidence', () => {
    expect(() => parseWorkflowGraph({ ...graphFixture(), graph_identifier: '../unsafe' })).toThrow()
    expect(() => parseWorkflowRun({ ...runFixture(), snapshot_fingerprint: 'bad' })).toThrow()
    expect(() => parseWorkflowEvidence({
      graph_run_identifier: 'workflow-run.one',
      graph_definition_fingerprint: hash,
      snapshot_fingerprint: hash,
      revision: 1,
      decisions: [],
      retries: [],
      approvals: [],
      artifacts: [{ artifact_identifier: 'artifact.one', content_sha256: 'bad' }],
    })).toThrow()
  })
})
