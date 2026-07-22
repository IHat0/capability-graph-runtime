import { act, renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { WorkspaceApi } from '../api/client'
import type { SceneResponse } from '../api/types'
import { currentFixtureDetail, currentFixtureScene } from '../test/fixtures'
import { useExperimentWorkspace } from './useExperimentWorkspace'

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((next) => { resolve = next })
  return { promise, resolve }
}

describe('experiment request lifecycle', () => {
  it('plans a natural-language experiment and normalizes its returned molecule', async () => {
    const experimentIdentifier = `experiment-${'a'.repeat(32)}`
    const molecule = {
      ...currentFixtureScene,
      scene_identifier: `scene.${experimentIdentifier}`,
      scene_stage: 'planned',
      experiment_identifier: experimentIdentifier,
      experiment_fingerprint: 'dynamic-fingerprint',
      expected_experiment_sha256: 'dynamic-fingerprint',
      structure_identifier: 'molecular_structure',
      structure_hash: 'dynamic-structure-hash',
    }
    const api: WorkspaceApi = {
      getHealth: async () => ({ service: 'pulsate-api', status: 'healthy', version: 'test' }),
      getPresets: async () => ({ count: 0, presets: [] }),
      getPreset: async () => currentFixtureDetail,
      getScene: async () => currentFixtureScene,
      planExperiment: async (question) => ({
        schema_version: 'cgr.pulsate-experiment-plan/1.0.0',
        experiment_identifier: experimentIdentifier,
        original_question: question,
        specification: { objective: 'molecular_ground_state_energy' },
        assumptions: ['basis_set=sto-3g (system default)'], warnings: [], missing_fields: [],
        ready_for_execution: true, requested_execution_target: 'local_simulator', specification_sha256: 'specification-hash',
        experiment_fingerprint: 'dynamic-fingerprint', expected_experiment_sha256: 'dynamic-fingerprint',
        structure_identifier: 'molecular_structure', structure_hash: 'dynamic-structure-hash',
        molecule, created_at: '2026-01-01T00:00:00Z',
      }),
    }
    const { result } = renderHook(() => useExperimentWorkspace(api))
    await waitFor(() => expect(result.current.initialLoading).toBe(false))
    act(() => result.current.setPlanQuestion('Compute the ground-state energy of H2 at 0.735 angstrom'))
    await act(async () => result.current.planExperiment())

    expect(result.current.plan?.ready_for_execution).toBe(true)
    expect(result.current.displayedPresetId).toBe(experimentIdentifier)
    expect(result.current.scene?.experimentId).toBe(experimentIdentifier)
    expect(result.current.scene?.structureHash).toBe('dynamic-structure-hash')
    expect(result.current.selectedPresetId).toBeNull()
  })

  it('ignores stale preset data even when an API implementation does not honor abort', async () => {
    const first = deferred<SceneResponse>()
    const second = deferred<SceneResponse>()
    const api: WorkspaceApi = {
      getHealth: async () => ({ service: 'pulsate-api', status: 'healthy', version: 'test' }),
      getPresets: async () => ({ count: 2, presets: [
        { preset_identifier: 'first', experiment_identifier: 'first', elements: ['H'], atom_count: 1, coordinate_unit: 'angstrom', molecular_charge: 0, spin_multiplicity: 1, basis_set: 'test' },
        { preset_identifier: 'second', experiment_identifier: 'second', elements: ['C'], atom_count: 1, coordinate_unit: 'angstrom', molecular_charge: 0, spin_multiplicity: 1, basis_set: 'test' },
      ] }),
      getPreset: async (identifier) => ({ ...currentFixtureDetail, preset_identifier: identifier }),
      getScene: (identifier) => identifier === 'first' ? first.promise : second.promise,
    }
    const { result } = renderHook(() => useExperimentWorkspace(api))
    await waitFor(() => expect(result.current.presets).toHaveLength(2))
    expect(result.current.selectedPresetId).toBeNull()
    act(() => result.current.selectPreset('first'))
    await waitFor(() => expect(result.current.selectedPresetId).toBe('first'))
    act(() => result.current.selectPreset('second'))
    await act(async () => second.resolve({ ...currentFixtureScene, scene_identifier: 'scene.second', experiment_identifier: 'second' }))
    await waitFor(() => expect(result.current.scene?.id).toBe('scene.second'))
    await act(async () => first.resolve({ ...currentFixtureScene, scene_identifier: 'scene.first', experiment_identifier: 'first' }))
    expect(result.current.scene?.id).toBe('scene.second')
    expect(result.current.displayedPresetId).toBe('second')
  })

  it('preserves and correctly identifies the last valid scene after a selected preset fails', async () => {
    const api: WorkspaceApi = {
      getHealth: async () => ({ service: 'pulsate-api', status: 'healthy', version: 'test' }),
      getPresets: async () => ({ count: 2, presets: [
        { preset_identifier: 'first', experiment_identifier: 'first', elements: ['H'], atom_count: 2, coordinate_unit: 'angstrom', molecular_charge: 0, spin_multiplicity: 1, basis_set: 'test' },
        { preset_identifier: 'second', experiment_identifier: 'second', elements: ['C'], atom_count: 2, coordinate_unit: 'angstrom', molecular_charge: 0, spin_multiplicity: 1, basis_set: 'test' },
      ] }),
      getPreset: async (identifier) => ({ ...currentFixtureDetail, preset_identifier: identifier }),
      getScene: async (identifier) => {
        if (identifier === 'second') throw new Error('scene unavailable')
        return { ...currentFixtureScene, scene_identifier: 'scene.first', experiment_identifier: 'first' }
      },
    }
    const { result } = renderHook(() => useExperimentWorkspace(api))
    await waitFor(() => expect(result.current.presets).toHaveLength(2))
    expect(result.current.scene).toBeNull()
    act(() => result.current.selectPreset('first'))
    await waitFor(() => expect(result.current.displayedPresetId).toBe('first'))
    act(() => result.current.selectPreset('second'))
    await waitFor(() => expect(result.current.staleSceneMessage).toContain('last valid structure from first'))
    expect(result.current.selectedPresetId).toBe('second')
    expect(result.current.displayedPresetId).toBe('first')
    expect(result.current.scene?.id).toBe('scene.first')
    expect(result.current.staleSceneMessage).toContain('last valid structure from first')
    expect(result.current.errors.some((error) => error.scope === 'scene' && error.message.includes('last valid structure from first'))).toBe(true)
  })

  it('turns normalization failures into scene errors and clears loading', async () => {
    const api: WorkspaceApi = {
      getHealth: async () => ({ service: 'pulsate-api', status: 'healthy', version: 'test' }),
      getPresets: async () => ({ count: 1, presets: [
        { preset_identifier: 'invalid-unit', experiment_identifier: 'invalid-unit', elements: ['H'], atom_count: 2, coordinate_unit: 'furlong', molecular_charge: 0, spin_multiplicity: 1, basis_set: 'test' },
      ] }),
      getPreset: async () => currentFixtureDetail,
      getScene: async () => ({ ...currentFixtureScene, coordinate_unit: 'furlong' }),
    }
    const { result } = renderHook(() => useExperimentWorkspace(api))
    await waitFor(() => expect(result.current.presets).toHaveLength(1))
    act(() => result.current.selectPreset('invalid-unit'))
    await waitFor(() => expect(result.current.errors.some((error) => error.scope === 'scene')).toBe(true))
    expect(result.current.scene).toBeNull()
    expect(result.current.presetLoading).toBe(false)
    expect(result.current.errors.some((error) => error.scope === 'scene' && error.message.includes('Unsupported coordinate unit'))).toBe(true)
  })
})
