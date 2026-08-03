import { act, fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { CandidateResearchPlanResponse, MolecularPlanningRequest } from '../api/planning'
import type { MolecularPlanningState } from '../hooks/useMolecularPlanning'
import type { LoadedMolecularProjectScene } from '../scene/native-project'
import { projectedMolecularSceneFixture } from '../test/molecular-project-fixtures'
import { MolecularPlanningPanel, MolecularPlanningResults } from './MolecularPlanningPanel'

function scene(): LoadedMolecularProjectScene {
  return {
    kind: 'native-project',
    metadata: projectedMolecularSceneFixture(),
    structures: [],
    resolvedSelections: [],
    resolvedRegions: [],
  }
}

function feasibility(status: 'feasible' | 'conditional' | 'unknown' | 'infeasible') {
  return {
    status,
    execution_target: 'target.local',
    findings: [{
      finding_identifier: `finding.${status}`,
      status,
      explanation: `${status} because of declared evidence.`,
      related_fact_identifiers: ['fact.structure-atom-count'],
      evidence_artifacts: [{ artifact_identifier: 'artifact.structure', content_sha256: 'a'.repeat(64) }],
    }],
    estimates: [{
      estimate_type: 'runtime', lower_bound: null, expected_value: 4, upper_bound: null,
      unit: 'seconds', confidence: 0.8, estimation_basis: 'Declared estimate.',
    }],
    fidelity: { fidelity_level: 'screening', conditions: [], limitations: ['Screening evidence only.'] },
    unresolved_requirement_identifiers: status === 'conditional' ? ['approval.scientist'] : [],
    required_approvals: status === 'conditional' ? ['approval.scientist'] : [],
    required_verifications: [],
  }
}

function response(): CandidateResearchPlanResponse {
  return {
    project_identifier: 'project-native',
    objective_identifier: 'objective.workspace-planning',
    plan_identifier: 'plan.workspace',
    plan_fingerprint: 'a'.repeat(64),
    planning_facts_fingerprint: 'b'.repeat(64),
    capability_declarations: [
      {
        descriptor: { capability_name: 'capability.selected', version: { major: 1, minor: 0, patch: 0 } },
        known_limitations: ['Does not authorize execution.'],
      },
      {
        descriptor: { capability_name: 'capability.rejected', version: { major: 1, minor: 0, patch: 0 } },
        known_limitations: ['Declared objective is unsupported.'],
      },
    ],
    plan: {
      plan_identifier: 'plan.workspace',
      objective_identifier: 'objective.workspace-planning',
      project_identifier: 'project-native',
      selected_assignments: [{
        assignment_identifier: 'assignment.selected', capability_name: 'capability.selected',
        capability_version: { major: 1, minor: 0, patch: 0 }, execution_target: 'target.local',
        feasibility: feasibility('conditional'),
      }],
      rejected_alternatives: [{
        alternative_identifier: 'alternative.rejected', capability_name: 'capability.rejected',
        capability_version: { major: 1, minor: 0, patch: 0 }, execution_target: 'target.local',
        feasibility: feasibility('infeasible'), rejection_reasons: ['The objective type is unsupported.'],
      }],
      unresolved_requirement_identifiers: ['approval.scientist'],
      unresolved_goal_identifiers: [],
      aggregate_estimates: [],
      verification_requirements: [{ verifier_identifier: 'verifier.result-integrity' }],
      approval_requirements: ['approval.scientist'],
      evidence_artifacts: [{ artifact_identifier: 'artifact.structure', content_sha256: 'a'.repeat(64) }],
      planning_facts: { facts: [{ fact_identifier: 'fact.structure-atom-count', subject_identifier: 'structure-a' }] },
    },
  }
}

function planning(overrides: Partial<MolecularPlanningState> = {}): MolecularPlanningState {
  return {
    result: null,
    loading: false,
    error: null,
    evaluate: vi.fn(async () => true),
    ...overrides,
  }
}

describe('molecular planning workspace', () => {
  it('builds a canonical read-only request from project-backed selectors', async () => {
    const evaluate = vi.fn(async (request: MolecularPlanningRequest) => {
      void request
      return true
    })
    render(<MolecularPlanningPanel scene={scene()} planning={planning({ evaluate })} />)

    expect(screen.getByRole('option', { name: 'system-structure-a' })).toBeTruthy()
    expect(screen.getByRole('option', { name: 'structure-a' })).toBeTruthy()
    expect(screen.getByRole('option', { name: 'region-a' })).toBeTruthy()
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Evaluate plan' })))

    expect(evaluate).toHaveBeenCalledOnce()
    expect(evaluate.mock.calls[0][0]).toMatchObject({
      objective: {
        project_identifier: 'project-native',
        structure_identifiers: ['structure-b', 'structure-a'],
        structural_region_identifiers: ['region-a'],
      },
      requested_execution_target: 'target.local',
    })
    expect(screen.queryByRole('button', { name: /run|execute|submit/i })).toBeNull()
    expect(screen.getByText(/No run, engine, or IBM submission is created/)).toBeTruthy()
  })

  it('shows selected and rejected capability evidence without execution controls', () => {
    render(<MolecularPlanningResults response={response()} />)

    expect(screen.getByText('Conditional')).toBeTruthy()
    expect(screen.getByText('Infeasible')).toBeTruthy()
    expect(screen.getByText('capability.selected')).toBeTruthy()
    expect(screen.getByText('capability.rejected')).toBeTruthy()
    expect(screen.getByText('Does not authorize execution.')).toBeTruthy()
    expect(screen.getByText(/Required, not approved: approval.scientist/)).toBeTruthy()
    expect(screen.getByText(/artifact.structure/)).toBeTruthy()
    expect(screen.getByText(/Planning subjects: structure-a/)).toBeTruthy()
    expect(document.body.textContent).not.toMatch(/granted|verification completed/i)
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('labels all four feasibility states distinctly', () => {
    const value = response()
    value.plan.selected_assignments[0].feasibility = feasibility('feasible')
    value.plan.rejected_alternatives = [
      ...value.plan.rejected_alternatives,
      {
        alternative_identifier: 'alternative.conditional', capability_name: 'capability.conditional',
        capability_version: { major: 1, minor: 0, patch: 0 }, execution_target: 'target.local',
        feasibility: feasibility('conditional'), rejection_reasons: ['A higher-ranked alternative was selected.'],
      },
      {
        alternative_identifier: 'alternative.unknown', capability_name: 'capability.unknown',
        capability_version: { major: 1, minor: 0, patch: 0 }, execution_target: null,
        feasibility: { ...feasibility('unknown'), execution_target: null }, rejection_reasons: ['Required evidence is unresolved.'],
      },
    ]
    render(<MolecularPlanningResults response={value} />)

    for (const label of ['Feasible', 'Conditional', 'Unknown', 'Infeasible']) {
      expect(screen.getByText(label)).toBeTruthy()
    }
  })

  it('shows loading and empty-catalogue states without execution language', () => {
    const value = response()
    value.plan.selected_assignments = []
    value.plan.rejected_alternatives = []
    const { rerender } = render(<MolecularPlanningPanel scene={scene()} planning={planning({ loading: true })} />)
    expect((screen.getByRole('button', { name: 'Evaluating plan...' }) as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByLabelText('Evaluate molecular research plan').closest('section')?.getAttribute('aria-busy')).toBe('true')
    expect(screen.getByRole('status').textContent).toContain('Evaluating the read-only molecular research plan.')

    rerender(<MolecularPlanningResults response={value} />)
    expect(screen.getByText(/No capability assignment is currently supported/)).toBeTruthy()
    expect(screen.getByRole('status').textContent).toContain('Candidate research plan plan.workspace loaded')
    expect(screen.queryByRole('button')).toBeNull()
  })

  it('keeps a controlled backend error in the planning panel', () => {
    render(<MolecularPlanningPanel scene={scene()} planning={planning({ error: 'Declared topology evidence is unavailable.' })} />)
    expect(screen.getByRole('alert').textContent).toBe('Declared topology evidence is unavailable.')
  })
})
