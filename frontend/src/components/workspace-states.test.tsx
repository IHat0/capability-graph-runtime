import { fireEvent, render, screen } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { describe, expect, it, vi } from 'vitest'
import type { PresetSummaryResponse } from '../api/types'
import { ConditionalNavigation } from './ConditionalNavigation'
import { EmptyInspector } from './EmptyInspector'
import { EmptyWorkspace } from './EmptyWorkspace'
import { Header } from './Header'
import { ResultSummary } from './ResultSummary'

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
    render(<EmptyWorkspace presets={[genericPreset]} loading={false} onPresetChange={onPresetChange} />)
    expect((screen.getByRole('button', { name: 'Continue' }) as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByText(/Natural-language experiment planning is not connected yet/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Ground-state energy|Bond scan|Compare VQE/i })).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: /Use a preset/ }))
    fireEvent.change(screen.getByRole('combobox', { name: 'Verified preset' }), { target: { value: genericPreset.preset_identifier } })
    expect(onPresetChange).toHaveBeenCalledWith(genericPreset.preset_identifier)
  })

  it('does not embed known preset identifiers in runtime workspace components', () => {
    const runtimeComponents = ['../App.tsx', './EmptyWorkspace.tsx', './PresetMenu.tsx', './PresetSelector.tsx', './ScientificPanel.tsx']
      .map((relativePath) => readFileSync(new URL(relativePath, import.meta.url), 'utf8'))
      .join('\n')
    expect(runtimeComponents).not.toMatch(/h2-ground-state-v1|lih-ground-state-v1/i)
  })

  it('keeps IBM unconfigured and hides receipt actions until evidence exists', () => {
    const { rerender } = render(<ResultSummary run={null} results={null} verification={null} receipt={null} />)
    expect(screen.getByText('Not configured')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'View receipt' })).toBeNull()
    rerender(<ResultSummary run={null} results={null} verification={null} receipt={{
      schema_version: 'cgr.quantum-preflight-receipt/2.0.0',
      run_identifier: 'run-test', preset_identifier: 'preset-test',
      execution_identifier: 'execution-test', experiment_identifier: 'experiment-test',
      experiment_fingerprint: 'experiment-sha', expected_experiment_sha256: 'experiment-sha',
      structure_identifier: 'structure-test', structure_sha256: 'structure-sha',
      hamiltonian_sha256: 'hamiltonian-sha', exact_scientific_result_sha256: 'exact-sha',
      vqe_scientific_result_sha256: 'vqe-sha', scientific_outcome_sha256: 'outcome-sha',
      execution_environment_identity: 'environment-sha', receipt_sha256: 'receipt-sha',
      verification_passed: true, authorization_state: 'authorized', authorized: true, artifacts: [],
    }} />)
    expect(screen.getByRole('button', { name: 'View receipt' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'View receipt' }))
    expect(screen.getByRole('region', { name: 'Authorization receipt' })).toBeTruthy()
  })
})
