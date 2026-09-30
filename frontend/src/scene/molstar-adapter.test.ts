import { describe, expect, it } from 'vitest'
import { normalizeScene } from './normalize'
import { sceneToMolstarStructure } from './molstar-adapter'
import { currentFixtureScene } from '../test/fixtures'
import { parseMol2 } from 'molstar/lib/mol-io/reader/mol2/parser'
import { trajectoryFromMol2 } from 'molstar/lib/mol-model-formats/structure/mol2'
import {
  isNativeProjectScene,
  nativeMolstarFormat,
  NATIVE_FORMAT_TO_MOLSTAR_FORMAT,
  validateMolstarAtomSourceIndices,
} from './native-molstar'
import { projectedMolecularSceneFixture } from '../test/molecular-project-fixtures'

describe('Mol* structure adapter', () => {
  it('preserves supplied coordinate strings and marks unknown bond order as unknown', () => {
    const adapted = sceneToMolstarStructure(normalizeScene(currentFixtureScene))
    expect(adapted.format).toBe('mol2')
    expect(adapted.data).toContain('1 H1 0 0 0 H 1 PUL')
    expect(adapted.data).toContain('2 H2 0 0 0.735 H 1 PUL')
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
    expect(adapted.data).toContain('2 H2 0 0 0.529177210903 H 1 PUL')
  })

  it('preserves element identities through the actual Mol* model parser', async () => {
    const elements = ['C', 'N', 'O', 'H', 'Cl', 'S']
    const adapted = sceneToMolstarStructure(normalizeScene({
      scene_identifier: 'generated-fixture', coordinate_unit: 'angstrom', bonds: [],
      atoms: elements.map((element, index) => ({ atom_identifier: `atom-${index}`, element, coordinates: [index, 0, 0] })),
    }))
    const parsed = await parseMol2(adapted.data, 'generated-fixture').run()
    if (parsed.isError) throw new Error(parsed.message)
    const trajectory = await trajectoryFromMol2(parsed.result).run()
    expect(Array.from(trajectory.representative.atomicHierarchy.atoms.type_symbol.toArray())).toEqual(elements.map(element => element.toUpperCase()))
  })

  it('uses an explicit deterministic native format allow-list', () => {
    expect(NATIVE_FORMAT_TO_MOLSTAR_FORMAT).toEqual({
      pdb: 'pdb',
      mmcif: 'mmcif',
      mol: 'mol',
      sdf: 'sdf',
      xyz: 'xyz',
    })
    expect(nativeMolstarFormat('mmcif')).toBe('mmcif')
    expect(() => nativeMolstarFormat('mol2')).toThrow('Unsupported native molecular format')
  })

  it('keeps native project scenes outside the coordinate MOL2 adapter path', () => {
    const metadata = projectedMolecularSceneFixture()
    expect(isNativeProjectScene({
      kind: 'native-project',
      metadata,
      structures: [],
      resolvedSelections: [],
      resolvedRegions: [],
    })).toBe(true)
    expect(isNativeProjectScene(normalizeScene(currentFixtureScene))).toBe(false)
  })

  it('accepts only complete safe Mol* source-index mappings', () => {
    expect(() => validateMolstarAtomSourceIndices([0], ['atom-0'])).not.toThrow()
    expect(() => validateMolstarAtomSourceIndices([0], ['atom-0', 'atom-1'])).toThrow('do not completely map')
    expect(() => validateMolstarAtomSourceIndices([0, 0], ['atom-0', 'atom-1'])).toThrow('do not completely map')
    expect(() => validateMolstarAtomSourceIndices([0, 2], ['atom-0', 'atom-1'])).toThrow('do not completely map')
    expect(() => validateMolstarAtomSourceIndices([0, 1], ['atom-0', 'atom-0'])).toThrow('do not completely map')
  })
})
