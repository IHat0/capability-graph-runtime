import { describe, expect, it } from 'vitest'
import { euclideanDistance, safeBondTransform, structureBounds } from './geometry'
import type { MolecularAtom } from './types'

describe('molecular geometry utilities', () => {
  it('calculates Euclidean distance for arbitrary 3D points', () => {
    expect(euclideanDistance([1, 2, 3], [4, 6, 3])).toBe(5)
  })

  it('calculates bounds, center, and a nonzero fit radius', () => {
    const atoms: MolecularAtom[] = [
      { id: 'a', element: 'C', position: [-2, 1, 0] },
      { id: 'b', element: 'O', position: [4, 3, 8] },
      { id: 'c', element: 'H', position: [1, -1, 2] },
    ]
    const bounds = structureBounds(atoms)
    expect(bounds?.min).toEqual([-2, -1, 0])
    expect(bounds?.max).toEqual([4, 3, 8])
    expect(bounds?.center).toEqual([1, 1, 4])
    expect(bounds?.size).toEqual([6, 4, 8])
    expect(bounds?.radius).toBeGreaterThan(5)
  })

  it('rejects a zero-length bond transform safely', () => {
    expect(safeBondTransform([0, 0, 0], [0, 0, 0])).toBeNull()
  })
})
