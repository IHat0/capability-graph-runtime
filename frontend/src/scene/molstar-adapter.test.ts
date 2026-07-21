import { describe, expect, it } from 'vitest'
import { normalizeScene } from './normalize'
import { sceneToMolstarStructure } from './molstar-adapter'
import { currentFixtureScene } from '../test/fixtures'

describe('Mol* structure adapter', () => {
  it('preserves supplied coordinate strings and marks unknown bond order as unknown', () => {
    const adapted = sceneToMolstarStructure(normalizeScene(currentFixtureScene))
    expect(adapted.format).toBe('mol2')
    expect(adapted.data).toContain('1 A1 0 0 0 H 1 PUL')
    expect(adapted.data).toContain('2 A2 0 0 0.735 H 1 PUL')
    expect(adapted.data).toContain('1 1 2 un')
    expect(adapted.atomIdsBySourceIndex).toEqual(['atom-a', 'atom-b'])
  })

  it('converts bohr coordinates to angstrom only at the Mol* boundary', () => {
    const scene = normalizeScene({
      ...currentFixtureScene,
      coordinate_unit: 'bohr',
      atoms: [
        { atom_identifier: 'atom-a', element: 'H', coordinates: [0, 0, 0] },
        { atom_identifier: 'atom-b', element: 'H', coordinates: [0, 0, 1] },
      ],
      bonds: [],
    })
    const adapted = sceneToMolstarStructure(scene)
    expect(scene.atoms[1].position).toEqual([0, 0, 1])
    expect(adapted.data).toContain('2 A2 0 0 0.529177210903 H 1 PUL')
  })
})
