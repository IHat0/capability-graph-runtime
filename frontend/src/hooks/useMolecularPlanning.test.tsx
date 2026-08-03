import { act, renderHook } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { CandidateResearchPlanResponse, MolecularPlanningRequest } from '../api/planning'
import { useMolecularPlanning } from './useMolecularPlanning'

const request = {
  objective: { objective_identifier: 'objective.workspace' },
  constraints: {},
  resources: {},
  requested_execution_target: null,
  selected_capabilities: [],
} satisfies MolecularPlanningRequest

function response(projectIdentifier = 'project.one'): CandidateResearchPlanResponse {
  return {
    project_identifier: projectIdentifier,
    objective_identifier: 'objective.workspace',
    plan_identifier: 'plan.one',
    plan_fingerprint: 'a'.repeat(64),
    planning_facts_fingerprint: 'b'.repeat(64),
    capability_declarations: [],
    plan: {
      plan_identifier: 'plan.one', objective_identifier: 'objective.workspace',
      project_identifier: projectIdentifier,
      selected_assignments: [], rejected_alternatives: [],
      unresolved_requirement_identifiers: [], unresolved_goal_identifiers: [],
      aggregate_estimates: [], verification_requirements: [], approval_requirements: [],
      evidence_artifacts: [], planning_facts: { facts: [] },
    },
  }
}

describe('useMolecularPlanning', () => {
  it('binds a plan response to the opened project and objective', async () => {
    const api = { evaluateMolecularProjectPlan: vi.fn(async () => response()) }
    const { result } = renderHook(() => useMolecularPlanning('project.one', api))

    await act(async () => { await result.current.evaluate(request) })

    expect(result.current.result?.plan_identifier).toBe('plan.one')
    expect(result.current.error).toBeNull()
  })

  it('fails closed on cross-project response identity mismatch', async () => {
    const api = { evaluateMolecularProjectPlan: vi.fn(async () => response('project.other')) }
    const { result } = renderHook(() => useMolecularPlanning('project.one', api))

    await act(async () => { await result.current.evaluate(request) })

    expect(result.current.result).toBeNull()
    expect(result.current.error).toBe('Molecular planning response identity mismatch.')
  })
})
