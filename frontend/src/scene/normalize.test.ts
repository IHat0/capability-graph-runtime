import { describe, expect, it } from 'vitest'
import type { SceneResponse } from '../api/types'
import { currentFixtureDetail, currentFixtureScene } from '../test/fixtures'
import { normalizeScene } from './normalize'

describe('scene normalization', () => {
  it('normalizes the current coordinate scene without identifier-specific branches', () => {
    const scene = normalizeScene(currentFixtureScene, currentFixtureDetail)
    expect(scene.atoms).toEqual([
      { id: 'atom-a', element: 'H', position: [0, 0, 0], label: 'atom-a' },
      { id: 'atom-b', element: 'H', position: [0, 0, 0.735], label: 'atom-b' },
    ])
    expect(scene.bonds[0]).toMatchObject({ atomIds: ['atom-a', 'atom-b'], source: 'declared' })
    expect(scene.measurements[0]).toMatchObject({ declaredValue: 0.735, backendDerivedValue: 0.735, geometricValue: 0.735 })
    expect(scene.regions[0].atomIds).toEqual(['atom-a', 'atom-b'])
    expect(scene.scientific.activeOrbitalIndices).toEqual([0, 1])
  })

  it('keeps every provenance identity field separate', () => {
    const scene = normalizeScene(currentFixtureScene, currentFixtureDetail)
    expect(scene.structureId).toBe('molecular_structure')
    expect(scene.structureHash).toBe('structure-hash')
    expect(scene.experimentFingerprint).toBe('experiment-fingerprint')
    expect(scene.expectedExperimentSha256).toBe('expected-experiment-sha256')
  })

  it('independently computes geometry when the backend-derived distance disagrees', () => {
    const scene = normalizeScene({
      ...currentFixtureScene,
      bonds: [{ ...currentFixtureScene.bonds![0], derived_distance: 9.25 }],
    })
    expect(scene.measurements[0].backendDerivedValue).toBe(9.25)
    expect(scene.measurements[0].geometricValue).toBeCloseTo(0.735, 12)
    expect(scene.measurements[0].backendDifference).toBeCloseTo(8.515, 12)
  })

  it('supports more than two atoms and deterministically infers temporary visual bonds', () => {
    const polyatomic: SceneResponse = {
      scene_identifier: 'scene.polyatomic',
      coordinate_unit: 'angstrom',
      atoms: [
        { atom_identifier: 'o', element: 'O', coordinates: [0, 0, 0] },
        { atom_identifier: 'h-left', element: 'H', coordinates: [0.96, 0, 0] },
        { atom_identifier: 'h-right', element: 'H', coordinates: [-0.24, 0.93, 0] },
        { atom_identifier: 'unknown', element: 'Xx', coordinates: [8, 8, 8] },
      ],
    }
    const scene = normalizeScene(polyatomic)
    expect(scene.atoms).toHaveLength(4)
    expect(scene.bonds).toHaveLength(2)
    expect(scene.bonds.every((bond) => bond.source === 'inferred')).toBe(true)
    expect(scene.bondDataSource).toBe('inferred')
  })

  it('does not infer bonds for unknown elements', () => {
    const scene = normalizeScene({
      scene_identifier: 'scene.unknown-elements',
      coordinate_unit: 'angstrom',
      atoms: [
        { atom_identifier: 'x-1', element: 'Xx', coordinates: [0, 0, 0] },
        { atom_identifier: 'x-2', element: 'Xx', coordinates: [0, 0, 0.2] },
      ],
    })
    expect(scene.bonds).toEqual([])
    expect(scene.bondDataSource).toBe('none')
  })

  it('skips quadratic inference above the conservative atom limit', () => {
    const atoms = Array.from({ length: 501 }, (_, index) => ({
      atom_identifier: `atom-${index}`,
      element: 'C',
      coordinates: [index * 2, 0, 0] as [number, number, number],
    }))
    const scene = normalizeScene({ scene_identifier: 'scene.large', coordinate_unit: 'angstrom', atoms })
    expect(scene.bonds).toEqual([])
    expect(scene.bondDataSource).toBe('skipped')
    expect(scene.normalizationWarnings[0]).toContain('safety limit is 500')
  })

  it('does not infer bonds when the backend explicitly supplies an empty list', () => {
    const scene = normalizeScene({ ...currentFixtureScene, bonds: [] })
    expect(scene.bonds).toEqual([])
    expect(scene.bondDataSource).toBe('explicit')
  })

  it('rejects unknown coordinate units instead of assuming angstrom', () => {
    expect(() => normalizeScene({ ...currentFixtureScene, coordinate_unit: 'furlong' })).toThrow('Unsupported coordinate unit')
  })
})
