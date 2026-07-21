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
})
