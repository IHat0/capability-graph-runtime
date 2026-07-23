import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, pulsateApi } from './client'
import { currentFixtureDetail, currentFixtureScene } from '../test/fixtures'

function jsonResponse(value: unknown): Response {
  return new Response(JSON.stringify(value), { status: 200, headers: { 'Content-Type': 'application/json' } })
}

describe('Pulsate API client failure handling', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('wraps backend connection failures without losing the cause', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('connection refused')))
    await expect(pulsateApi.getHealth()).rejects.toMatchObject({ name: 'ApiError', message: 'Unable to reach the Pulsate API.' })
  })

  it('rejects malformed successful responses', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ presets: 'invalid' }), { status: 200 })))
    await expect(pulsateApi.getPresets()).rejects.toBeInstanceOf(ApiError)
  })

  it('preserves cancellation as an AbortError', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new DOMException('aborted', 'AbortError')))
    await expect(pulsateApi.getScene('fixture', new AbortController().signal)).rejects.toMatchObject({ name: 'AbortError' })
  })

  it.each([
    ['an empty atom identifier', { ...currentFixtureScene, atoms: [{ ...currentFixtureScene.atoms[0], atom_identifier: '' }] }],
    ['a malformed coordinate triple', { ...currentFixtureScene, atoms: [{ ...currentFixtureScene.atoms[0], coordinates: [0, 0, 'invalid'] }] }],
    ['duplicate atom identifiers', { ...currentFixtureScene, atoms: [currentFixtureScene.atoms[0], currentFixtureScene.atoms[0]] }],
    ['an empty bond identifier', { ...currentFixtureScene, bonds: [{ ...currentFixtureScene.bonds![0], bond_identifier: '' }] }],
    ['invalid bond references', { ...currentFixtureScene, bonds: [{ ...currentFixtureScene.bonds![0], atom_identifiers: ['atom-a', 'missing'] }] }],
    ['non-pair bond references', { ...currentFixtureScene, bonds: [{ ...currentFixtureScene.bonds![0], atom_identifiers: ['atom-a'] }] }],
    ['non-finite-shaped bond data', { ...currentFixtureScene, bonds: [{ ...currentFixtureScene.bonds![0], order: 'single' }] }],
    ['malformed quantum region', { ...currentFixtureScene, quantum_region: { selection_identifier: '', atom_identifiers: ['atom-a'] } }],
    ['a quantum region with an unknown atom', { ...currentFixtureScene, quantum_region: { selection_identifier: 'region', atom_identifiers: ['missing'] } }],
    ['malformed scientific model', { ...currentFixtureScene, scientific_model: { charge: 'zero' } }],
    ['malformed artifact references', { ...currentFixtureScene, artifact_references: ['valid', 42] }],
  ])('rejects nested scene data with %s', async (_label, payload) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(payload)))
    await expect(pulsateApi.getScene('fixture')).rejects.toBeInstanceOf(ApiError)
  })

  it('rejects malformed nested preset details', async () => {
    const malformed = {
      ...currentFixtureDetail,
      manifest: {
        ...currentFixtureDetail.manifest,
        experiment: {
          ...currentFixtureDetail.manifest.experiment,
          electronic_structure: { active_orbital_indices: [0, 'one'] },
        },
      },
    }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(malformed)))
    await expect(pulsateApi.getPreset('fixture')).rejects.toBeInstanceOf(ApiError)
  })

  it('rejects malformed run state and scientific result contracts', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ run_identifier: 'run-incomplete', status: 'authorized' })))
    await expect(pulsateApi.getRun('run-incomplete')).rejects.toBeInstanceOf(ApiError)
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({
      run_identifier: 'run-test', preset_identifier: 'preset-test', exact_total_energy_hartree: 'not-a-number',
    })))
    await expect(pulsateApi.getRunResults('run-test')).rejects.toBeInstanceOf(ApiError)
  })

  it('posts and validates a dynamic experiment plan', async () => {
    const experimentIdentifier = `experiment-${'a'.repeat(32)}`
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({
      schema_version: 'cgr.pulsate-experiment-plan/1.0.0',
      experiment_identifier: experimentIdentifier,
      original_question: 'Compute H2', specification: { objective: 'molecular_ground_state_energy' },
      assumptions: [], warnings: [], missing_fields: [], ready_for_execution: true,
      requested_execution_target: 'local_simulator',
      specification_sha256: 'spec-hash', experiment_fingerprint: 'experiment-hash',
      expected_experiment_sha256: 'experiment-hash', structure_identifier: 'molecular_structure',
      structure_hash: 'structure-hash', molecule: {
        ...currentFixtureScene,
        experiment_identifier: experimentIdentifier,
        expected_experiment_sha256: 'experiment-hash',
        structure_identifier: 'molecular_structure',
      },
      created_at: '2026-01-01T00:00:00Z',
    }))
    vi.stubGlobal('fetch', fetchMock)

    const plan = await pulsateApi.planExperiment('Compute H2')
    expect(plan.experiment_identifier).toBe(experimentIdentifier)
    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/experiments/plan')
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ question: 'Compute H2' })
  })

  it('creates dynamic runs with only the experiment identifier', async () => {
    const experimentIdentifier = `experiment-${'b'.repeat(32)}`
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({
      run_identifier: `run-${'c'.repeat(32)}`, source_type: 'dynamic_experiment',
      source_identifier: experimentIdentifier, preset_identifier: null,
      experiment_identifier: experimentIdentifier, experiment_fingerprint: 'experiment-hash',
      expected_experiment_sha256: 'experiment-hash', structure_identifier: 'molecular_structure',
      execution_target: 'local_simulator', status: 'queued',
      created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
      status_url: `/api/v1/runs/run-${'c'.repeat(32)}`,
    }))
    vi.stubGlobal('fetch', fetchMock)

    await pulsateApi.createExperimentRun(experimentIdentifier, 'dynamic-key-0001')
    const body = JSON.parse(fetchMock.mock.calls[0][1].body)
    expect(body).toEqual({ experiment_identifier: experimentIdentifier, execution_target: 'local_simulator' })
    expect(body).not.toHaveProperty('preset_identifier')
  })

  it('submits an IBM dynamic target without credentials or caller options', async () => {
    const experimentIdentifier = `experiment-${'d'.repeat(32)}`
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({
      run_identifier: `run-${'e'.repeat(32)}`, source_type: 'dynamic_experiment',
      source_identifier: experimentIdentifier, preset_identifier: null,
      experiment_identifier: experimentIdentifier, experiment_fingerprint: 'experiment-hash',
      expected_experiment_sha256: 'experiment-hash', structure_identifier: 'molecular_structure',
      execution_target: 'ibm_quantum', status: 'awaiting_ibm_submission',
      created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
      status_url: `/api/v1/runs/run-${'e'.repeat(32)}`,
    }))
    vi.stubGlobal('fetch', fetchMock)
    await pulsateApi.createExperimentRun(experimentIdentifier, 'ibm-key-0001', undefined, 'ibm_quantum')
    const body = JSON.parse(fetchMock.mock.calls[0][1].body)
    expect(body).toEqual({ experiment_identifier: experimentIdentifier, execution_target: 'ibm_quantum' })
    expect(JSON.stringify(body)).not.toMatch(/token|credential|backend|precision/i)
  })

  it('rejects credential material in capability responses', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({
      available: true, execution_targets: ['local_simulator', 'ibm_quantum'], reason: null,
      maximum_run_seconds: 180, ibm_quantum: {
        available: true, backend_name: 'ibm_test', reason: null,
        maximum_run_seconds: 1800, target_precision: 0.015,
        token: 'must-never-reach-browser',
      },
    })))
    await expect(pulsateApi.getRunCapability()).rejects.toBeInstanceOf(ApiError)
  })
})
