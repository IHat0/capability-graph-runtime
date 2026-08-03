import { act, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../api/client'
import type {
  IBMExecutionEvidence,
  RunReceiptResponse,
  RunResultsResponse,
  RunStateResponse,
  RunVerificationResponse,
  SceneResponse,
} from '../api/types'
import { ExistingRunInput } from '../components/ExistingRunInput'
import { ResultSummary } from '../components/ResultSummary'
import { ScientificPanel } from '../components/ScientificPanel'
import { normalizeScene } from '../scene/normalize'
import { type ExistingRunApi, useExistingRun } from './useExistingRun'

const runIdentifier = `run-${'1'.repeat(32)}`
const experimentIdentifier = `experiment-${'2'.repeat(32)}`
const fingerprint = 'a'.repeat(64)
const identity = {
  run_identifier: runIdentifier,
  source_type: 'dynamic_experiment' as const,
  source_identifier: experimentIdentifier,
  preset_identifier: null,
  experiment_identifier: experimentIdentifier,
  experiment_fingerprint: fingerprint,
  expected_experiment_sha256: fingerprint,
  structure_identifier: 'molecular_structure',
}

const ibmEvidence: IBMExecutionEvidence = {
  hardware_role: 'final_energy_evaluation_at_locally_optimized_parameters',
  submission_status: 'completed',
  job_identifier: 'ibm-job-read-only',
  backend_name: 'ibm_backend_test',
  execution_integrity_passed: true,
  scientific_quality_passed: false,
  ibm_total_energy_hartree: -7.8,
}

const run: RunStateResponse = {
  ...identity,
  execution_target: 'ibm_quantum',
  status: 'rejected',
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T01:00:00Z',
  status_url: `/api/v1/runs/${runIdentifier}`,
  structure_sha256: 'structure-sha',
  molecule: {
    scene_identifier: 'state-molecule-must-not-be-used',
    coordinate_unit: 'angstrom',
    atoms: [{ atom_identifier: 'wrong', element: 'C', coordinates: [9, 9, 9] }],
  },
}

const scene: SceneResponse = {
  scene_identifier: `scene.${experimentIdentifier}`,
  scene_stage: 'declared',
  experiment_identifier: experimentIdentifier,
  experiment_fingerprint: fingerprint,
  coordinate_unit: 'angstrom',
  atoms: [
    { atom_identifier: 'li', element: 'Li', coordinates: [0, 0, -0.9] },
    { atom_identifier: 'h', element: 'H', coordinates: [0, 0, 0.9] },
  ],
  bonds: [{
    bond_identifier: 'bond.li-h',
    atom_identifiers: ['li', 'h'],
    declared_distance: 1.8,
    derived_distance: 1.8,
  }],
  quantum_region: {
    selection_identifier: 'selection.full',
    atom_identifiers: ['li', 'h'],
  },
  scientific_model: {
    charge: 0,
    spin_multiplicity: 1,
    basis_set: 'sto-3g',
    reference_method: 'restricted_hartree_fock',
    active_electron_count: 2,
    active_spatial_orbital_count: 2,
    mapper: 'jordan_wigner',
    ansatz: 'uccsd',
  },
}

const results: RunResultsResponse = {
  ...identity,
  structure_sha256: 'structure-sha',
  hamiltonian_sha256: 'hamiltonian-sha',
  exact_scientific_result_sha256: 'exact-sha',
  vqe_scientific_result_sha256: 'vqe-sha',
  scientific_outcome_sha256: 'outcome-sha',
  exact_total_energy_hartree: -7.86,
  vqe_total_energy_hartree: -7.85,
  absolute_difference_hartree: 0.01,
  tolerance_hartree: 0.001,
  energy_unit: 'hartree',
  exact_solver_metadata: {},
  vqe_solver_metadata: {},
  optimizer_evaluations: 12,
  converged: true,
  compatibility_warnings: [],
  execution_environment_identity: 'environment-sha',
  receipt_sha256: 'receipt-sha',
  ibm_execution: ibmEvidence,
}

const verification: RunVerificationResponse = {
  ...identity,
  structure_sha256: results.structure_sha256,
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
  ibm_execution: ibmEvidence,
}

const receipt: RunReceiptResponse = {
  ...identity,
  schema_version: 'cgr.quantum-preflight-receipt/2.0.0',
  execution_identifier: 'execution-read-only',
  structure_sha256: results.structure_sha256,
  hamiltonian_sha256: results.hamiltonian_sha256,
  exact_scientific_result_sha256: results.exact_scientific_result_sha256,
  vqe_scientific_result_sha256: results.vqe_scientific_result_sha256,
  scientific_outcome_sha256: results.scientific_outcome_sha256,
  execution_environment_identity: results.execution_environment_identity,
  receipt_sha256: results.receipt_sha256,
  verification_passed: false,
  authorization_state: 'rejected',
  authorized: false,
  artifacts: [],
  ibm_execution: ibmEvidence,
}

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function responseForPath(path: string): Response {
  if (path === `/api/v1/runs/${runIdentifier}`) return jsonResponse(run)
  if (path.endsWith('/scene')) return jsonResponse(scene)
  if (path.endsWith('/results')) return jsonResponse(results)
  if (path.endsWith('/verification')) return jsonResponse(verification)
  if (path.endsWith('/receipt')) return jsonResponse(receipt)
  return jsonResponse({ detail: { code: 'run_not_found', message: 'Run not found.' } }, 404)
}

function ExistingRunPanelHarness({ api, startRun }: {
  api: ExistingRunApi
  startRun: () => Promise<void>
}) {
  const existingRun = useExistingRun(api)
  const displayedScene = existingRun.scene ?? normalizeScene(scene)
  const presetRun = {
    capability: null,
    run: null,
    results: null,
    verification: null,
    receipt: null,
    creating: false,
    error: null,
    canRun: true,
    disabledReason: null,
    startRun,
  }

  return <>
    <ExistingRunInput
      value={existingRun.runIdentifierInput}
      loading={existingRun.loading}
      openedRunIdentifier={existingRun.run?.run_identifier ?? null}
      onChange={existingRun.setRunIdentifierInput}
      onOpen={() => void existingRun.openRun()}
    />
    <ScientificPanel
      scene={displayedScene}
      presets={[]}
      selectedPresetId={null}
      displayedPresetId={existingRun.run?.source_identifier ?? 'background-preset'}
      staleSceneMessage={null}
      loading={existingRun.loading}
      onPresetChange={vi.fn()}
      presetRun={presetRun}
      openedRun={existingRun.run ? {
        run: existingRun.run,
        results: existingRun.results,
        verification: existingRun.verification,
        receipt: existingRun.receipt,
      } : null}
      readOnlyLookup={existingRun.loading || existingRun.run !== null}
      plan={null}
    />
  </>
}

afterEach(() => vi.unstubAllGlobals())

describe('read-only existing run loading', () => {
  it('provides an explicit run identifier input and open action', () => {
    const onChange = vi.fn()
    const onOpen = vi.fn()
    const { rerender } = render(<ExistingRunInput
      value=""
      loading={false}
      openedRunIdentifier={null}
      onChange={onChange}
      onOpen={onOpen}
    />)

    fireEvent.change(screen.getByRole('textbox', { name: 'Run identifier' }), {
      target: { value: runIdentifier },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Open run' }))
    expect(onChange).toHaveBeenCalledWith(runIdentifier)
    expect(onOpen).toHaveBeenCalledOnce()

    rerender(<ExistingRunInput
      value={runIdentifier}
      loading
      openedRunIdentifier={null}
      onChange={onChange}
      onOpen={onOpen}
    />)
    expect((screen.getByRole('button', { name: 'Opening...' }) as HTMLButtonElement).disabled).toBe(true)
    expect(document.body.textContent).not.toContain('OpeningÔÇª')
    expect(document.body.textContent).not.toContain('Openingâ€¦')
  })

  it.each([
    [401, 'Authentication is required to access Pulsate.'],
    [403, 'Access to the requested Pulsate resource is denied.'],
  ])('preserves the controlled HTTP %s security state', async (status, message) => {
    const api: ExistingRunApi = {
      getRun: vi.fn().mockRejectedValue(new ApiError(message, status)),
      getRunScene: vi.fn(),
      getRunResults: vi.fn(),
      getRunVerification: vi.fn(),
      getRunReceipt: vi.fn(),
    }
    const { result } = renderHook(() => useExistingRun(api))
    act(() => result.current.setRunIdentifierInput(runIdentifier))

    await act(async () => { expect(await result.current.openRun()).toBe(false) })

    expect(result.current.error).toBe(message)
    expect(api.getRunScene).not.toHaveBeenCalled()
  })

  it('loads a rejected IBM terminal run and uses only its persisted scene and GET evidence', async () => {
    const fetchMock = vi.fn((input: string | URL | Request, init?: RequestInit) => {
      expect(init?.method ?? 'GET').toBe('GET')
      return Promise.resolve(responseForPath(String(input)))
    })
    vi.stubGlobal('fetch', fetchMock)
    const { result } = renderHook(() => useExistingRun())

    act(() => result.current.setRunIdentifierInput(`  ${runIdentifier}  `))
    let accepted = false
    await act(async () => { accepted = await result.current.openRun() })

    expect(accepted).toBe(true)
    expect(result.current.runIdentifierInput).toBe(runIdentifier)
    expect(result.current.run?.status).toBe('rejected')
    expect(result.current.scene?.atoms.map((atom) => atom.element)).toEqual(['Li', 'H'])
    expect(result.current.scene?.measurements[0].declaredValue).toBe(1.8)
    expect(result.current.scene?.selections[0].atomIds).toEqual(['li', 'h'])
    expect(result.current.scene?.scientific.basisSet).toBe('sto-3g')
    expect(result.current.results?.ibm_execution?.execution_integrity_passed).toBe(true)
    expect(result.current.verification?.ibm_execution?.scientific_quality_passed).toBe(false)
    expect(result.current.receipt?.authorized).toBe(false)
    expect(fetchMock.mock.calls.map(([path]) => String(path))).toEqual([
      `/api/v1/runs/${runIdentifier}`,
      `/api/v1/runs/${runIdentifier}/scene`,
      `/api/v1/runs/${runIdentifier}/results`,
      `/api/v1/runs/${runIdentifier}/verification`,
      `/api/v1/runs/${runIdentifier}/receipt`,
    ])
    expect(fetchMock.mock.calls.every(([, init]) => (init?.method ?? 'GET') !== 'POST')).toBe(true)

    render(<ResultSummary
      run={result.current.run}
      results={result.current.results}
      verification={result.current.verification}
      receipt={result.current.receipt}
    />)
    expect(screen.getByText('IBM execution integrity').parentElement?.textContent).toContain('Passed')
    expect(screen.getByText('IBM scientific quality').parentElement?.textContent).toContain('Rejected')
    expect(screen.getByText('Authorization').parentElement?.textContent).toContain('rejected')
  })

  it('preserves previously accepted run evidence when a replacement fails', async () => {
    const replacementIdentifier = `run-${'8'.repeat(32)}`
    const api: ExistingRunApi = {
      getRun: vi.fn().mockResolvedValueOnce(run).mockRejectedValueOnce(new Error('replacement failed')),
      getRunScene: vi.fn().mockResolvedValue(scene),
      getRunResults: vi.fn().mockResolvedValue(results),
      getRunVerification: vi.fn().mockResolvedValue(verification),
      getRunReceipt: vi.fn().mockResolvedValue(receipt),
    }
    const { result } = renderHook(() => useExistingRun(api))
    act(() => result.current.setRunIdentifierInput(runIdentifier))
    await act(async () => { expect(await result.current.openRun()).toBe(true) })
    const acceptedScene = result.current.scene

    act(() => result.current.setRunIdentifierInput(replacementIdentifier))
    await act(async () => { expect(await result.current.openRun()).toBe(false) })

    expect(result.current.run?.run_identifier).toBe(runIdentifier)
    expect(result.current.scene).toBe(acceptedScene)
    expect(result.current.results).toBe(results)
    expect(result.current.verification).toBe(verification)
    expect(result.current.receipt).toBe(receipt)
  })

  it('enters read-only mode while a valid lookup is unresolved and remains read-only after loading', async () => {
    let resolveRun!: (value: RunStateResponse) => void
    const api: ExistingRunApi = {
      getRun: vi.fn(() => new Promise<RunStateResponse>((resolve) => {
        resolveRun = resolve
      })),
      getRunScene: vi.fn().mockResolvedValue(scene),
      getRunResults: vi.fn().mockResolvedValue(results),
      getRunVerification: vi.fn().mockResolvedValue(verification),
      getRunReceipt: vi.fn().mockResolvedValue(receipt),
    }
    const startRun = vi.fn(async () => undefined)
    render(<ExistingRunPanelHarness api={api} startRun={startRun} />)

    fireEvent.change(screen.getByRole('textbox', { name: 'Run identifier' }), {
      target: { value: runIdentifier },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Open run' }))

    expect(await screen.findByText('Opening persisted run')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Run experiment' })).toBeNull()
    expect(startRun).not.toHaveBeenCalled()
    expect(api.getRunScene).not.toHaveBeenCalled()

    await act(async () => resolveRun(run))
    await waitFor(() => expect(screen.getByText('Read-only persisted run')).toBeTruthy())

    expect(screen.queryByRole('button', { name: 'Run experiment' })).toBeNull()
    expect(startRun).not.toHaveBeenCalled()
    expect(api.getRunScene).toHaveBeenCalledOnce()
    expect(api.getRunResults).toHaveBeenCalledOnce()
    expect(api.getRunVerification).toHaveBeenCalledOnce()
    expect(api.getRunReceipt).toHaveBeenCalledOnce()
  })

  it('rejects a malformed identifier without making an API request', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    const { result } = renderHook(() => useExistingRun())

    act(() => result.current.setRunIdentifierInput('run-not-valid'))
    await act(async () => result.current.openRun())

    expect(result.current.error).toContain('valid run identifier')
    expect(result.current.scene).toBeNull()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('shows a controlled not-found error for an unknown run', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      jsonResponse({ detail: { code: 'run_not_found', message: 'Run not found.' } }, 404),
    ))
    const { result } = renderHook(() => useExistingRun())
    act(() => result.current.setRunIdentifierInput(runIdentifier))
    await act(async () => result.current.openRun())

    expect(result.current.error).toBe('Run not found. Check the run identifier and try again.')
    expect(result.current.scene).toBeNull()
  })

  it('shows a controlled unavailable-scene error', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse(run))
      .mockResolvedValueOnce(jsonResponse({
        detail: { code: 'scene_unavailable', message: 'Run scene is unavailable.' },
      }, 409))
    vi.stubGlobal('fetch', fetchMock)
    const { result } = renderHook(() => useExistingRun())
    act(() => result.current.setRunIdentifierInput(runIdentifier))
    await act(async () => result.current.openRun())

    expect(result.current.error).toBe('The persisted molecular scene is unavailable or corrupt.')
    expect(result.current.run).toBeNull()
  })

  it('fails closed when the fetched run identifier differs from the requested identifier', async () => {
    const differentRunIdentifier = `run-${'9'.repeat(32)}`
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({
      ...run,
      run_identifier: differentRunIdentifier,
      status_url: `/api/v1/runs/${differentRunIdentifier}`,
    }))
    vi.stubGlobal('fetch', fetchMock)
    const { result } = renderHook(() => useExistingRun())
    act(() => result.current.setRunIdentifierInput(runIdentifier))
    await act(async () => result.current.openRun())

    expect(result.current.error).toBe(
      'Run evidence identity mismatch. The requested run was not displayed.',
    )
    expect(result.current.run).toBeNull()
    expect(result.current.scene).toBeNull()
    expect(result.current.results).toBeNull()
    expect(result.current.verification).toBeNull()
    expect(result.current.receipt).toBeNull()
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(String(fetchMock.mock.calls[0][0])).toBe(`/api/v1/runs/${runIdentifier}`)
  })

  it('fails closed when the scene identity disagrees with the persisted run', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(jsonResponse(run))
      .mockResolvedValueOnce(jsonResponse({
        ...scene,
        experiment_fingerprint: 'b'.repeat(64),
      }))
    vi.stubGlobal('fetch', fetchMock)
    const { result } = renderHook(() => useExistingRun())
    act(() => result.current.setRunIdentifierInput(runIdentifier))
    await act(async () => result.current.openRun())

    expect(result.current.error).toBe('Run evidence identity mismatch. The requested run was not displayed.')
    expect(result.current.run).toBeNull()
    expect(result.current.scene).toBeNull()
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('fails closed when terminal evidence disagrees with the run identity', async () => {
    const mismatchedVerification = {
      ...verification,
      experiment_fingerprint: 'b'.repeat(64),
    }
    const fetchMock = vi.fn((input: string | URL | Request) => {
      const path = String(input)
      if (path.endsWith('/verification')) return Promise.resolve(jsonResponse(mismatchedVerification))
      return Promise.resolve(responseForPath(path))
    })
    vi.stubGlobal('fetch', fetchMock)
    const { result } = renderHook(() => useExistingRun())
    act(() => result.current.setRunIdentifierInput(runIdentifier))
    await act(async () => result.current.openRun())

    expect(result.current.error).toBe('Run evidence identity mismatch. The requested run was not displayed.')
    expect(result.current.run).toBeNull()
    expect(result.current.scene).toBeNull()
    expect(result.current.results).toBeNull()
    expect(result.current.verification).toBeNull()
    expect(result.current.receipt).toBeNull()
  })
})
