import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { WorkflowRunWorkspaceState } from '../hooks/useWorkflowRun'
import { WorkflowWorkspace } from './WorkflowWorkspace'

const hash = 'a'.repeat(64)

function state(overrides: Partial<WorkflowRunWorkspaceState> = {}): WorkflowRunWorkspaceState {
  return {
    graphRunIdentifierInput: '',
    setGraphRunIdentifierInput: vi.fn(),
    loading: false,
    mutating: false,
    error: null,
    errorStatus: null,
    graph: null,
    run: null,
    evidence: null,
    open: vi.fn(async () => true),
    refresh: vi.fn(async () => undefined),
    resume: vi.fn(async () => undefined),
    cancel: vi.fn(async () => undefined),
    decideApproval: vi.fn(async () => undefined),
    clear: vi.fn(),
    ...overrides,
  }
}

describe('WorkflowWorkspace', () => {
  it('renders a controlled empty and unauthorized state', () => {
    const workflow = state({ error: 'denied', errorStatus: 403 })
    render(<WorkflowWorkspace workflow={workflow} />)
    expect(screen.getByText('No workflow run open')).not.toBeNull()
    expect(screen.getByRole('alert').textContent).toContain('Workflow access is not authorized.')
  })

  it('renders graph state, approvals, dependencies, budgets, and controls', () => {
    const workflow = state({
      graphRunIdentifierInput: 'workflow-run.one',
      graph: {
        graph_identifier: 'workflow.one',
        version: 1,
        definition_fingerprint: hash,
        nodes: [
          {
            node_identifier: 'node.a',
            node_kind: 'classical',
            capability_identity: 'capability.a',
            execution_target: null,
            molecular_project_identifier: 'project.one',
            molecular_system_identifiers: ['system.one'],
            molecular_region_identifiers: ['region.one'],
            estimated_cost: 1,
            estimated_cost_currency: 'USD',
            verification_requirements: ['verification.one'],
          },
          {
            node_identifier: 'node.b',
            node_kind: 'quantum_ibm',
            capability_identity: 'capability.ibm',
            execution_target: 'ibm_quantum',
            molecular_project_identifier: 'project.one',
            molecular_system_identifiers: ['system.one'],
            molecular_region_identifiers: [],
            estimated_cost: null,
            estimated_cost_currency: null,
            verification_requirements: [],
          },
        ],
        edges: [{
          edge_identifier: 'edge.a-b',
          edge_kind: 'dependency',
          source_node_id: 'node.a',
          target_node_id: 'node.b',
          source_port_id: null,
          target_port_id: null,
        }],
        root_node_ids: ['node.a'],
        terminal_node_ids: ['node.b'],
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
      },
      run: {
        revision: 2,
        snapshot_fingerprint: hash,
        graph_state: {
          graph_run_identifier: 'workflow-run.one',
          graph_identifier: 'workflow.one',
          graph_version: 1,
          graph_definition_fingerprint: hash,
          status: 'awaiting_approval',
          tenant_identifier: 'tenant.one',
          created_by_principal: 'scientist.one',
          created_at_epoch: 1,
          started_at_epoch: null,
          completed_at_epoch: null,
          current_node_id: null,
          total_nodes: 2,
          completed_nodes: 1,
          failed_nodes: 0,
          skipped_nodes: 0,
          retry_count: 0,
          budget_status: 'within_budget',
          error_message: null,
        },
        node_states: [
          {
            node_id: 'node.a', graph_run_identifier: 'workflow-run.one', status: 'succeeded',
            attempt_number: 1, max_attempts: 1, started_at_epoch: 1, completed_at_epoch: 2,
            error_code: null, error_message: null, retry_reason: null, condition_outcome: null,
            approval_status: null, cost_consumed: 1, produced_artifact_types: ['result'], execution_metadata: {},
          },
          {
            node_id: 'node.b', graph_run_identifier: 'workflow-run.one', status: 'awaiting_approval',
            attempt_number: 0, max_attempts: 1, started_at_epoch: null, completed_at_epoch: null,
            error_code: null, error_message: null, retry_reason: null, condition_outcome: null,
            approval_status: 'pending', cost_consumed: null, produced_artifact_types: [], execution_metadata: {},
          },
        ],
        approval_states: [{
          approval_id: 'approval.ibm', node_id: 'node.b', status: 'pending', expiry_epoch: null, decision_reason: null,
        }],
        decision_states: [{
          decision_identifier: 'decision.one', decision_kind: 'comparison', subject_identifier: 'node.a',
          outcome: 'selected', decided_at_epoch: 2, related_node_ids: ['node.a'],
        }],
        budget_state: {
          status: 'within_budget', planning_estimates: { 'cost.USD': 2 }, reserved_budget: {},
          actual_consumption: { 'cost.USD': 1 }, unknown_consumption: {}, ceiling_values: { 'cost.USD': 5 },
          remaining_budget: { 'cost.USD': 4 }, terminal_over_budget: false,
        },
      },
      evidence: {
        graph_run_identifier: 'workflow-run.one', graph_definition_fingerprint: hash,
        snapshot_fingerprint: hash, revision: 2, decisions: [], retries: [{
          retry_id: 'retry.one', node_id: 'node.a', attempt_number: 2,
          original_failure_code: 'temporary', original_failure_message: 'Retry later.',
          scheduled_at_epoch: 2, executed_at_epoch: null, outcome: null,
        }], approvals: [], artifacts: [],
      },
    })
    render(<WorkflowWorkspace workflow={workflow} />)
    expect(screen.getByText('Selective IBM path')).not.toBeNull()
    expect(screen.getByText('approval.ibm')).not.toBeNull()
    expect(screen.getByText('node.a', { selector: 'dd' })).not.toBeNull()
    expect(screen.getByText('1 / 5')).not.toBeNull()
    expect(screen.getByText('Retry history')).not.toBeNull()
    expect(screen.getByText('0 unresolved planning item(s)')).not.toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Approve' }))
    expect(workflow.decideApproval).toHaveBeenCalledWith('approval.ibm', true)
    fireEvent.click(screen.getByRole('button', { name: 'Start / resume' }))
    expect(workflow.resume).toHaveBeenCalled()
  })
})
