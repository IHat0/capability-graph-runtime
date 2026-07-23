import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, type PulsateApi } from '../api/client'
import type { RunReceiptResponse, RunResultsResponse, RunStateResponse, RunVerificationResponse } from '../api/types'
import { RUN_POLL_INTERVAL_MS, usePresetRun } from './usePresetRun'

const identity = {
  run_identifier: 'run-11111111111111111111111111111111',
  source_type: 'preset' as const,
  source_identifier: 'fixture-preset',
  preset_identifier: 'fixture-preset',
  experiment_identifier: 'fixture-experiment',
  experiment_fingerprint: 'fixture-fingerprint',
  expected_experiment_sha256: 'expected-sha',
  structure_identifier: 'molecular_structure',
}

type SceneProps = {
  selectedPresetId: string | null
  displayedPresetId: string | null
  experimentIdentifier: string
  experimentFingerprint: string
  expectedExperimentSha256: string
  structureIdentifier: string
  structureSha256: string
  experimentRecordIdentifier?: string | null
  executionTarget?: 'local_simulator' | 'ibm_quantum'
}

const scene: SceneProps = {
  selectedPresetId: identity.preset_identifier,
  displayedPresetId: identity.preset_identifier,
  experimentIdentifier: identity.experiment_identifier,
  experimentFingerprint: identity.experiment_fingerprint,
  expectedExperimentSha256: identity.expected_experiment_sha256,
  structureIdentifier: identity.structure_identifier,
  structureSha256: 'structure-sha',
}

function state(status: RunStateResponse['status']): RunStateResponse {
  return {
    ...identity, execution_target: 'local_simulator', status,
    created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
    status_url: `/api/v1/runs/${identity.run_identifier}`,
  }
}

const results: RunResultsResponse = {
  ...identity,
  structure_sha256: scene.structureSha256, hamiltonian_sha256: 'hamiltonian-sha',
  exact_scientific_result_sha256: 'exact-result-sha',
  vqe_scientific_result_sha256: 'vqe-result-sha', scientific_outcome_sha256: 'outcome-sha',
  exact_total_energy_hartree: -7.862128833438594, vqe_total_energy_hartree: -7.8621288334,
  absolute_difference_hartree: 3.8594e-11, tolerance_hartree: 1e-6, energy_unit: 'hartree',
  exact_solver_metadata: {}, vqe_solver_metadata: {}, optimizer_evaluations: 12, converged: true,
  compatibility_warnings: [], execution_environment_identity: 'environment-sha', receipt_sha256: 'receipt-sha',
}

const verification: RunVerificationResponse = {
  ...identity, structure_sha256: scene.structureSha256,
  verification_completed: true, verification_passed: true, authorization_state: 'authorized',
  blocking_findings: [], nonblocking_findings: [], tolerance_check: { passed: true },
  scientific_identity_checks: [], artifact_integrity_checks: [], checks: [], compatibility_warnings: [],
}

const receipt: RunReceiptResponse = {
  ...identity, schema_version: 'cgr.quantum-preflight-receipt/2.0.0',
  execution_identifier: 'execution-test', structure_sha256: scene.structureSha256,
  hamiltonian_sha256: results.hamiltonian_sha256,
  exact_scientific_result_sha256: results.exact_scientific_result_sha256,
  vqe_scientific_result_sha256: results.vqe_scientific_result_sha256,
  scientific_outcome_sha256: results.scientific_outcome_sha256,
  execution_environment_identity: results.execution_environment_identity,
  receipt_sha256: results.receipt_sha256, verification_passed: true,
  authorization_state: 'authorized', authorized: true, artifacts: [],
}

function api(overrides: Partial<PulsateApi> = {}): PulsateApi {
  return {
    getHealth: vi.fn(), getPresets: vi.fn(), getPreset: vi.fn(), getScene: vi.fn(),
    planExperiment: vi.fn(), interpretQuestion: vi.fn(), approveInterpretation: vi.fn(),
    getRunCapability: vi.fn().mockResolvedValue({
      available: true, execution_targets: ['local_simulator'], reason: null, maximum_run_seconds: 180,
    }),
    createRun: vi.fn().mockResolvedValue(state('queued')),
    createExperimentRun: vi.fn().mockResolvedValue(state('queued')),
    getRun: vi.fn().mockResolvedValue(state('authorized')),
    getRunResults: vi.fn().mockResolvedValue(results),
    getRunVerification: vi.fn().mockResolvedValue(verification),
    getRunReceipt: vi.fn().mockResolvedValue(receipt),
    ...overrides,
  }
}

function renderRun(runApi: PulsateApi, props: SceneProps = scene) {
  return renderHook(
    ({ currentScene }) => usePresetRun({ api: runApi, ...currentScene }),
    { initialProps: { currentScene: props } },
  )
}

function storeActiveRun(): void {
  localStorage.setItem(`pulsate.active-run.v1:${window.location.origin}:${identity.preset_identifier}`, JSON.stringify({
    runIdentifier: identity.run_identifier, presetIdentifier: identity.preset_identifier,
  }))
}

beforeEach(() => localStorage.clear())

describe('preset run lifecycle', () => {
  it('submits a planned experiment identifier without invoking the preset endpoint', async () => {
    const experimentRecordIdentifier = `experiment-${'a'.repeat(32)}`
    const dynamicState = {
      ...state('queued'),
      source_type: 'dynamic_experiment' as const,
      source_identifier: experimentRecordIdentifier,
      preset_identifier: null,
      experiment_identifier: experimentRecordIdentifier,
    }
    const createRun = vi.fn()
    const createExperimentRun = vi.fn().mockResolvedValue(dynamicState)
    const { result } = renderRun(api({ createRun, createExperimentRun }), {
      ...scene,
      selectedPresetId: null,
      displayedPresetId: experimentRecordIdentifier,
      experimentIdentifier: experimentRecordIdentifier,
      experimentRecordIdentifier,
    })
    await waitFor(() => expect(result.current.canRun).toBe(true))
    await act(async () => { await result.current.startRun() })

    expect(createExperimentRun).toHaveBeenCalledWith(
      experimentRecordIdentifier, expect.any(String), expect.any(AbortSignal), 'local_simulator',
    )
    expect(createRun).not.toHaveBeenCalled()
    expect(result.current.run?.experiment_identifier).toBe(experimentRecordIdentifier)
    expect(result.current.run?.source_type).toBe('dynamic_experiment')
    expect(result.current.run?.source_identifier).toBe(experimentRecordIdentifier)
    expect(result.current.run?.preset_identifier).toBeNull()
  })

  it('submits an IBM plan only when the IBM capability is available', async () => {
    const experimentRecordIdentifier = `experiment-${'b'.repeat(32)}`
    const createExperimentRun = vi.fn().mockResolvedValue({
      ...state('queued'), execution_target: 'ibm_quantum' as const,
      source_type: 'dynamic_experiment' as const,
      source_identifier: experimentRecordIdentifier,
      preset_identifier: null,
      experiment_identifier: experimentRecordIdentifier,
    })
    const runApi = api({
      getRunCapability: vi.fn().mockResolvedValue({
        available: true, execution_targets: ['local_simulator', 'ibm_quantum'], reason: null,
        maximum_run_seconds: 180,
        local_simulator: { available: true, reason: null, maximum_run_seconds: 180 },
        ibm_quantum: { available: true, reason: null, maximum_run_seconds: 1800, backend_name: 'ibm_test', target_precision: 0.015 },
      }),
      createExperimentRun,
    })
    const { result } = renderRun(runApi, {
      ...scene, selectedPresetId: null, displayedPresetId: experimentRecordIdentifier,
      experimentIdentifier: experimentRecordIdentifier, experimentRecordIdentifier,
      executionTarget: 'ibm_quantum',
    })
    await waitFor(() => expect(result.current.canRun).toBe(true))
    await act(async () => { await result.current.startRun() })
    expect(createExperimentRun).toHaveBeenCalledWith(
      experimentRecordIdentifier, expect.any(String), expect.any(AbortSignal), 'ibm_quantum',
    )
  })

  it('disables execution without a displayed preset and for a stale selection', async () => {
    const absent = renderRun(api(), { ...scene, selectedPresetId: null, displayedPresetId: null })
    await waitFor(() => expect(absent.result.current.capability?.available).toBe(true))
    expect(absent.result.current.canRun).toBe(false)
    absent.unmount()
    const stale = renderRun(api(), { ...scene, selectedPresetId: 'new-preset' })
    await waitFor(() => expect(stale.result.current.capability?.available).toBe(true))
    expect(stale.result.current.canRun).toBe(false)
    expect(stale.result.current.disabledReason).toContain('not the structure currently displayed')
  })

  it('creates only one run for a double submission and uses the displayed preset', async () => {
    let resolve!: (value: RunStateResponse) => void
    const pending = new Promise<RunStateResponse>((next) => { resolve = next })
    const createRun = vi.fn().mockReturnValue(pending)
    const { result } = renderRun(api({ createRun }))
    await waitFor(() => expect(result.current.canRun).toBe(true))
    act(() => { void result.current.startRun(); void result.current.startRun() })
    expect(createRun).toHaveBeenCalledTimes(1)
    expect(createRun.mock.calls[0][0]).toBe(identity.preset_identifier)
    await act(async () => resolve(state('queued')))
  })

  it('polls to a terminal state, stops, and loads authoritative evidence', async () => {
    vi.useFakeTimers()
    try {
      const getRun = vi.fn().mockResolvedValue(state('authorized'))
      const { result } = renderRun(api({ getRun }))
      await act(async () => { await Promise.resolve() })
      await act(async () => { await result.current.startRun() })
      expect(result.current.run?.status).toBe('queued')
      await act(async () => { await vi.advanceTimersByTimeAsync(RUN_POLL_INTERVAL_MS) })
      await act(async () => { await Promise.resolve() })
      expect(result.current.run?.status).toBe('authorized')
      expect(result.current.results?.exact_total_energy_hartree).toBe(-7.862128833438594)
      expect(result.current.receipt?.authorized).toBe(true)
      await act(async () => { await vi.advanceTimersByTimeAsync(RUN_POLL_INTERVAL_MS * 2) })
      expect(getRun).toHaveBeenCalledTimes(1)
    } finally {
      vi.useRealTimers()
    }
  })

  it('keeps rejection distinct from runtime failure', async () => {
    storeActiveRun()
    const rejectedVerification = { ...verification, verification_passed: false, authorization_state: 'rejected' as const }
    const { result } = renderRun(api({
      getRun: vi.fn().mockResolvedValue(state('rejected')),
      getRunVerification: vi.fn().mockResolvedValue(rejectedVerification),
      getRunReceipt: vi.fn().mockResolvedValue({
        ...receipt, verification_passed: false, authorization_state: 'rejected', authorized: false,
      }),
    }))
    await waitFor(() => expect(result.current.verification?.authorization_state).toBe('rejected'))
    expect(result.current.run?.status).toBe('rejected')
    expect(result.current.error).toBeNull()
  })

  it.each([
    ['experiment fingerprint', { experiment_fingerprint: 'different-experiment' }],
    ['expected experiment SHA', { expected_experiment_sha256: 'different-expected-sha' }],
    ['structure identifier', { structure_identifier: 'different-structure' }],
    ['structure hash', { structure_sha256: 'different-structure-sha' }],
  ])('refuses evidence with a mismatched %s', async (_label, mismatch) => {
    storeActiveRun()
    const { result } = renderRun(api({
      getRunResults: vi.fn().mockResolvedValue({ ...results, ...mismatch }),
    }))
    await waitFor(() => expect(result.current.error).toContain('identity does not match'))
    expect(result.current.results).toBeNull()
  })

  it.each([
    ['verification structure', 'verification', 'structure_sha256', 'cross-structure'],
    ['receipt structure', 'receipt', 'structure_sha256', 'cross-structure'],
    ['receipt Hamiltonian', 'receipt', 'hamiltonian_sha256', 'cross-hamiltonian'],
    ['exact result identity', 'receipt', 'exact_scientific_result_sha256', 'cross-exact'],
    ['VQE result identity', 'receipt', 'vqe_scientific_result_sha256', 'cross-vqe'],
    ['scientific outcome identity', 'receipt', 'scientific_outcome_sha256', 'cross-outcome'],
    ['environment identity', 'receipt', 'execution_environment_identity', 'cross-environment'],
    ['receipt identity', 'receipt', 'receipt_sha256', 'cross-receipt'],
    ['verification decision', 'verification', 'verification_passed', false],
    ['authorization state', 'verification', 'authorization_state', 'rejected'],
    ['receipt authorization boolean', 'receipt', 'authorized', false],
  ])('refuses cross-evidence disagreement in %s', async (_label, target, field, value) => {
    storeActiveRun()
    const overrides = target === 'verification'
      ? { getRunVerification: vi.fn().mockResolvedValue({ ...verification, [field]: value }) }
      : { getRunReceipt: vi.fn().mockResolvedValue({ ...receipt, [field]: value }) }
    const { result } = renderRun(api(overrides))
    await waitFor(() => expect(result.current.error).toContain('identity does not match'))
    expect(result.current.results).toBeNull()
    expect(result.current.verification).toBeNull()
    expect(result.current.receipt).toBeNull()
  })

  it('aborts a pending create and ignores its late response after the scene changes', async () => {
    let resolve!: (value: RunStateResponse) => void
    let createSignal: AbortSignal | undefined
    const createRun = vi.fn((_preset: string, _key: string, signal?: AbortSignal) => {
      createSignal = signal
      return new Promise<RunStateResponse>((next) => { resolve = next })
    })
    const hook = renderRun(api({ createRun }))
    await waitFor(() => expect(hook.result.current.canRun).toBe(true))
    act(() => { void hook.result.current.startRun() })
    hook.rerender({ currentScene: {
      ...scene, selectedPresetId: 'next-preset', displayedPresetId: 'next-preset',
      experimentIdentifier: 'next-experiment', experimentFingerprint: 'next-fingerprint',
    } })
    expect(createSignal?.aborted).toBe(true)
    await act(async () => resolve(state('queued')))
    expect(hook.result.current.run).toBeNull()
    expect(hook.result.current.creating).toBe(false)
  })

  it('aborts a pending create when the workspace unmounts', async () => {
    let createSignal: AbortSignal | undefined
    const createRun = vi.fn((_preset: string, _key: string, signal?: AbortSignal) => {
      createSignal = signal
      return new Promise<RunStateResponse>(() => undefined)
    })
    const hook = renderRun(api({ createRun }))
    await waitFor(() => expect(hook.result.current.canRun).toBe(true))
    act(() => { void hook.result.current.startRun() })
    hook.unmount()
    expect(createSignal?.aborted).toBe(true)
  })

  it('reuses the idempotency key after an uncertain create failure', async () => {
    const createRun = vi.fn()
      .mockRejectedValueOnce(new ApiError('The response was lost.'))
      .mockResolvedValueOnce(state('queued'))
    const { result } = renderRun(api({ createRun }))
    await waitFor(() => expect(result.current.canRun).toBe(true))
    await act(async () => { await result.current.startRun() })
    await waitFor(() => expect(result.current.canRun).toBe(true))
    await act(async () => { await result.current.startRun() })
    expect(createRun).toHaveBeenCalledTimes(2)
    expect(createRun.mock.calls[1][1]).toBe(createRun.mock.calls[0][1])
    expect(result.current.run?.run_identifier).toBe(identity.run_identifier)
  })
})
