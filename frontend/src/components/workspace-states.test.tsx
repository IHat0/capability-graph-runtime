import { fireEvent, render, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { describe, expect, it, vi } from 'vitest'
import type {
  ExperimentPlanResponse,
  IBMExecutionEvidence,
  PresetSummaryResponse,
  RunStateResponse,
  RunVerificationResponse,
} from '../api/types'
import { deriveExistingRunHeaderStatus } from '../existingRunHeaderStatus'
import { ConditionalNavigation } from './ConditionalNavigation'
import { EmptyInspector } from './EmptyInspector'
import { EmptyWorkspace } from './EmptyWorkspace'
import { Header } from './Header'
import { ResultSummary } from './ResultSummary'
import { MolecularProjectInput } from './MolecularProjectInput'

const genericPreset: PresetSummaryResponse = {
  preset_identifier: 'generic-preset-v1',
  experiment_identifier: 'generic-experiment-v1',
  elements: ['C', 'O'],
  atom_count: 2,
  coordinate_unit: 'angstrom',
  molecular_charge: 0,
  spin_multiplicity: 1,
  basis_set: 'minimal',
}

const rejectedRunIdentifier = `run-${'1'.repeat(32)}`
const rejectedExperimentIdentifier = `experiment-${'2'.repeat(32)}`
const rejectedExperimentFingerprint = 'a'.repeat(64)
const rejectedIdentity = {
  run_identifier: rejectedRunIdentifier,
  source_type: 'dynamic_experiment' as const,
  source_identifier: rejectedExperimentIdentifier,
  preset_identifier: null,
  experiment_identifier: rejectedExperimentIdentifier,
  experiment_fingerprint: rejectedExperimentFingerprint,
  expected_experiment_sha256: rejectedExperimentFingerprint,
  structure_identifier: 'molecular_structure',
}
const rejectedIbmEvidence: IBMExecutionEvidence = {
  hardware_role: 'final_energy_evaluation_at_locally_optimized_parameters',
  submission_status: 'completed',
  job_identifier: 'ibm-job-header-status',
  backend_name: 'ibm_backend_test',
  execution_integrity_passed: true,
  scientific_quality_passed: false,
  ibm_total_energy_hartree: -7.8,
}
const rejectedIbmRun: RunStateResponse = {
  ...rejectedIdentity,
  execution_target: 'ibm_quantum',
  status: 'rejected',
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T01:00:00Z',
  status_url: `/api/v1/runs/${rejectedRunIdentifier}`,
  structure_sha256: 'structure-sha',
  molecule: {
    scene_identifier: 'scene.rejected-header-run',
    coordinate_unit: 'angstrom',
    atoms: [
      { atom_identifier: 'li', element: 'Li', coordinates: [0, 0, -0.9] },
      { atom_identifier: 'h', element: 'H', coordinates: [0, 0, 0.9] },
    ],
  },
}
const rejectedIbmVerification: RunVerificationResponse = {
  ...rejectedIdentity,
  structure_sha256: 'structure-sha',
  verification_completed: true,
  verification_passed: false,
  authorization_state: 'rejected',
  blocking_findings: ['scientific tolerance exceeded'],
  nonblocking_findings: [],
  tolerance_check: { passed: false },
  scientific_identity_checks: [],
  artifact_integrity_checks: [],
  checks: [],
  compatibility_warnings: [],
  ibm_execution: rejectedIbmEvidence,
}

describe('progressive workspace disclosure', () => {
  it('uses an ECG wordmark and a centred settings glyph', () => {
    const { container, rerender } = render(<Header />)
    expect(screen.getByRole('link', { name: 'Pulsate Labs' })).toBeTruthy()
    expect(screen.queryByText('P')).toBeNull()
    expect(container.querySelector('.wordmark__wave path')?.getAttribute('d')).toContain('4-10')

    rerender(<ConditionalNavigation hasScene={false} />)
    const settings = screen.getByRole('link', { name: 'Settings' })
    const centre = settings.querySelector('circle')
    expect(centre?.getAttribute('cx')).toBe('12')
    expect(centre?.getAttribute('cy')).toBe('12')
  })

  it('keeps the ordinary workspace header defaults when no existing run is active', () => {
    const status = deriveExistingRunHeaderStatus({
      loading: false,
      run: null,
      results: null,
      verification: null,
      receipt: null,
    })
    render(<Header status={status} />)

    expect(screen.getByText('Local simulator')).toBeTruthy()
    expect(screen.getByText('Not executed')).toBeTruthy()
  })

  it('separates verified IBM execution from scientific rejection in persisted-run status', () => {
    const status = deriveExistingRunHeaderStatus({
      loading: false,
      run: rejectedIbmRun,
      results: null,
      verification: rejectedIbmVerification,
      receipt: null,
    })
    render(<Header status={status} />)

    expect(screen.queryByText('Not executed')).toBeNull()
    expect(screen.queryByText('Local simulator')).toBeNull()
    expect(screen.getByText('IBM execution verified')).toBeTruthy()
    expect(screen.getByText('Scientific result rejected')).toBeTruthy()
  })

  it('shows only Home, Settings, and Help navigation before a scene exists', () => {
    render(<ConditionalNavigation hasScene={false} />)
    expect(screen.getAllByRole('link').map((link) => link.getAttribute('aria-label'))).toEqual(['Home', 'Settings', 'Help'])
    expect(screen.queryByRole('link', { name: 'Structure' })).toBeNull()
    expect(screen.queryByRole('link', { name: 'Workflow' })).toBeNull()
    expect(screen.queryByRole('link', { name: 'Results' })).toBeNull()
    expect(screen.queryByRole('link', { name: 'Evidence' })).toBeNull()
  })

  it('adds structure-specific navigation only after a scene exists', () => {
    render(<ConditionalNavigation hasScene />)
    for (const name of ['Structure', 'Workflow', 'Results', 'Evidence']) {
      expect(screen.getByRole('link', { name })).toBeTruthy()
    }
  })

  it('keeps the empty inspector minimal without inactive result sections', () => {
    render(<EmptyInspector />)
    expect(screen.getByRole('heading', { name: 'Start a new experiment' })).toBeTruthy()
    expect(screen.queryByRole('heading', { name: 'Results' })).toBeNull()
    expect(screen.queryByRole('heading', { name: 'Verification' })).toBeNull()
    expect(screen.queryByText('Receipt')).toBeNull()
  })

  it('uses the fetched preset list and keeps natural-language planning truthful', () => {
    const onPresetChange = vi.fn()
    const onPlan = vi.fn()
    const onQuestionChange = vi.fn()
    const { rerender } = render(<EmptyWorkspace presets={[genericPreset]} loading={false} onPresetChange={onPresetChange} question="" planning={false} plan={null} onQuestionChange={onQuestionChange} onPlan={onPlan} />)
    expect((screen.getByRole('button', { name: 'Continue' }) as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByText(/Describe a two-atom ground-state energy experiment/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Ground-state energy|Bond scan|Compare VQE/i })).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: /Use a preset/ }))
    fireEvent.change(screen.getByRole('combobox', { name: 'Verified preset' }), { target: { value: genericPreset.preset_identifier } })
    expect(onPresetChange).toHaveBeenCalledWith(genericPreset.preset_identifier)
    fireEvent.change(screen.getByRole('textbox', { name: 'Scientific objective' }), { target: { value: 'Compute H2' } })
    expect(onQuestionChange).toHaveBeenCalledWith('Compute H2')

    const incompletePlan: ExperimentPlanResponse = {
      schema_version: 'cgr.pulsate-experiment-plan/1.0.0',
      experiment_identifier: `experiment-${'a'.repeat(32)}`,
      original_question: 'Compute H2', specification: null,
      assumptions: ['basis_set=sto-3g (system default)'], warnings: [],
      missing_fields: ['bond_length'], ready_for_execution: false,
      requested_execution_target: 'local_simulator',
      specification_sha256: null, experiment_fingerprint: null,
      expected_experiment_sha256: null, structure_identifier: null,
      structure_hash: null, molecule: null, created_at: '2026-01-01T00:00:00Z',
    }
    rerender(<EmptyWorkspace presets={[genericPreset]} loading={false} onPresetChange={onPresetChange} question="Compute H2" planning={false} plan={incompletePlan} onQuestionChange={onQuestionChange} onPlan={onPlan} />)
    expect(screen.getByText(/More information is required: bond_length/)).toBeTruthy()
    expect(screen.getByText(/basis_set=sto-3g/)).toBeTruthy()
  })

  it('does not embed known preset identifiers in runtime workspace components', () => {
    const runtimeComponents = ['../App.tsx', './EmptyWorkspace.tsx', './PresetMenu.tsx', './PresetSelector.tsx', './ScientificPanel.tsx']
      .map((relativePath) => readFileSync(new URL(relativePath, import.meta.url), 'utf8'))
      .join('\n')
    expect(runtimeComponents).not.toMatch(/h2-ground-state-v1|lih-ground-state-v1/i)
  })

  it('keeps IBM pending and displays IBM receipt identities only when evidence exists', () => {
    const { rerender } = render(<ResultSummary run={null} results={null} verification={null} receipt={null} />)
    expect(screen.getAllByText('Not executed').length).toBeGreaterThan(0)
    expect(screen.queryByRole('button', { name: 'View receipt' })).toBeNull()
    rerender(<ResultSummary run={null} results={null} verification={null} receipt={{
      schema_version: 'cgr.quantum-preflight-receipt/2.0.0',
      run_identifier: 'run-test', source_type: 'preset', source_identifier: 'preset-test', preset_identifier: 'preset-test',
      execution_identifier: 'execution-test', experiment_identifier: 'experiment-test',
      experiment_fingerprint: 'experiment-sha', expected_experiment_sha256: 'experiment-sha',
      structure_identifier: 'structure-test', structure_sha256: 'structure-sha',
      hamiltonian_sha256: 'hamiltonian-sha', exact_scientific_result_sha256: 'exact-sha',
      vqe_scientific_result_sha256: 'vqe-sha', scientific_outcome_sha256: 'outcome-sha',
      execution_environment_identity: 'environment-sha', receipt_sha256: 'receipt-sha',
      verification_passed: true, authorization_state: 'authorized', authorized: true, artifacts: [],
      ibm_execution: {
        hardware_role: 'final_energy_evaluation_at_locally_optimized_parameters',
        submission_status: 'completed', job_identifier: 'ibm-job-test', backend_name: 'ibm_backend_test',
        execution_integrity_passed: true, scientific_quality_passed: true,
        ibm_receipt_sha256: 'ibm-receipt-sha',
      },
    }} />)
    expect(screen.getByRole('button', { name: 'View receipt' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'View receipt' }))
    expect(screen.getByRole('region', { name: 'Authorization receipt' })).toBeTruthy()
    expect(screen.getByText('ibm-job-test')).toBeTruthy()
    expect(screen.getByText('ibm_backend_test')).toBeTruthy()
    expect(document.body.textContent).not.toMatch(/token|credential/i)
  })

  it('opens and clears a molecular project only through explicit read-only controls', () => {
    const onOpen = vi.fn()
    const onClear = vi.fn()
    const onProjectChange = vi.fn()
    const onSceneChange = vi.fn()
    const { rerender } = render(<MolecularProjectInput
      projectIdentifier="project-native"
      sceneIdentifier="scene-native"
      loading={false}
      active={false}
      onProjectChange={onProjectChange}
      onSceneChange={onSceneChange}
      onOpen={onOpen}
      onClear={onClear}
    />)

    fireEvent.click(screen.getByRole('button', { name: 'Open project' }))
    expect(onOpen).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('button', { name: 'Clear project' })).toBeNull()

    rerender(<MolecularProjectInput
      projectIdentifier="project-native"
      sceneIdentifier="scene-native"
      loading={false}
      active
      onProjectChange={onProjectChange}
      onSceneChange={onSceneChange}
      onOpen={onOpen}
      onClear={onClear}
    />)
    fireEvent.click(screen.getByRole('button', { name: 'Clear project' }))
    expect(onClear).toHaveBeenCalledTimes(1)
  })
})
