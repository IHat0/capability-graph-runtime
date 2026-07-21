import { describe, expect, it } from 'vitest'
import { fromAngstrom, pointToAngstrom, toAngstrom } from './units'
import { inferBonds } from './geometry'
import type { MolecularAtom } from './types'

describe('coordinate units', () => {
  it('converts supported aliases to and from angstrom', () => {
    expect(toAngstrom(1, 'angstroms')).toBe(1)
    expect(toAngstrom(1, 'bohr')).toBeCloseTo(0.529177210903, 12)
    expect(toAngstrom(1, 'nm')).toBe(10)
    expect(fromAngstrom(10, 'nanometer')).toBe(1)
    const converted = pointToAngstrom([1, 2, 3], 'bohrs')
    expect(converted[0]).toBeCloseTo(0.529177210903, 12)
    expect(converted[1]).toBeCloseTo(1.058354421806, 12)
    expect(converted[2]).toBeCloseTo(1.587531632709, 12)
  })

  it('converts inference thresholds into the declared scene unit', () => {
    const atoms: MolecularAtom[] = [
      { id: 'h-1', element: 'H', position: [0, 0, 0] },
      { id: 'h-2', element: 'H', position: [0, 0, 1.4] },
    ]
    expect(inferBonds(atoms, 'bohr').bonds).toHaveLength(1)
  })

  it('rejects unknown units', () => {
    expect(() => toAngstrom(1, 'parsec')).toThrow('Unsupported coordinate unit')
  })
})
