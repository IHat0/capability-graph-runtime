import type { MolecularScene } from './types'
import { pointToAngstrom } from './units'

export interface MolstarStructureData {
  format: 'mol2'
  data: string
  label: string
  atomIdsBySourceIndex: string[]
  sourceIndexByAtomId: ReadonlyMap<string, number>
}

function safeToken(value: string): string {
  return value.replace(/[^A-Za-z0-9_.-]/g, '_') || 'pulsate-scene'
}

function numeric(value: number): string {
  if (!Number.isFinite(value)) throw new Error('Mol* adapter received a non-finite coordinate.')
  return Object.is(value, -0) ? '0' : value.toString()
}

/**
 * Convert the generic Pulsate scene to an in-memory MOL2 structure for Mol*.
 * Coordinates are serialized from the supplied numbers without rounding.
 * Unknown bond order is encoded as MOL2 `un` (unknown), not guessed.
 */
export function sceneToMolstarStructure(scene: MolecularScene): MolstarStructureData {
  const sourceIndexByAtomId = new Map(scene.atoms.map((atom, index) => [atom.id, index]))
  const atomLines = scene.atoms.map((atom, index) => {
    const [x, y, z] = pointToAngstrom(atom.position, scene.coordinateUnit)
    return `${index + 1} A${index + 1} ${numeric(x)} ${numeric(y)} ${numeric(z)} ${safeToken(atom.element)} 1 PUL`
  })
  const bondLines = scene.bonds.flatMap((bond, index) => {
    const left = sourceIndexByAtomId.get(bond.atomIds[0])
    const right = sourceIndexByAtomId.get(bond.atomIds[1])
    if (left === undefined || right === undefined) return []
    const order = bond.order === 1 || bond.order === 2 || bond.order === 3 ? String(bond.order) : 'un'
    return [`${index + 1} ${left + 1} ${right + 1} ${order}`]
  })
  const lines = [
    '@<TRIPOS>MOLECULE',
    safeToken(scene.id),
    `${atomLines.length} ${bondLines.length} 1 0 0`,
    'SMALL',
    'NO_CHARGES',
    '',
    '@<TRIPOS>ATOM',
    ...atomLines,
    '@<TRIPOS>BOND',
    ...bondLines,
    '@<TRIPOS>SUBSTRUCTURE',
    '1 PUL 1',
    '',
  ]
  return {
    format: 'mol2',
    data: lines.join('\n'),
    label: scene.experimentId ?? scene.id,
    atomIdsBySourceIndex: scene.atoms.map((atom) => atom.id),
    sourceIndexByAtomId,
  }
}
