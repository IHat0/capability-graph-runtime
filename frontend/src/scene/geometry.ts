import type { Vector3Tuple } from '../api/types'
import type { MolecularAtom, MolecularBond } from './types'
import { elementAppearance, knownCovalentRadius } from './elements'
import { fromAngstrom } from './units'

export const MAX_INFERENCE_ATOMS = 500

export interface BondInferenceResult {
  bonds: MolecularBond[]
  warning?: string
}

export interface StructureBounds {
  min: Vector3Tuple
  max: Vector3Tuple
  center: Vector3Tuple
  size: Vector3Tuple
  radius: number
}

export function euclideanDistance(a: Vector3Tuple, b: Vector3Tuple): number {
  return Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2])
}

export function structureBounds(atoms: MolecularAtom[], coordinateUnit = 'angstrom'): StructureBounds | null {
  if (atoms.length === 0) return null
  const min: Vector3Tuple = [...atoms[0].position]
  const max: Vector3Tuple = [...atoms[0].position]
  for (const atom of atoms.slice(1)) {
    for (let axis = 0; axis < 3; axis += 1) {
      min[axis] = Math.min(min[axis], atom.position[axis])
      max[axis] = Math.max(max[axis], atom.position[axis])
    }
  }
  const size: Vector3Tuple = [max[0] - min[0], max[1] - min[1], max[2] - min[2]]
  const center: Vector3Tuple = [
    (min[0] + max[0]) / 2,
    (min[1] + max[1]) / 2,
    (min[2] + max[2]) / 2,
  ]
  const minimumRadius = fromAngstrom(0.75, coordinateUnit)
  const radius = Math.max(minimumRadius, ...atoms.map((atom) => euclideanDistance(atom.position, center) + fromAngstrom(elementAppearance(atom.element).radius, coordinateUnit)))
  return { min, max, center, size, radius }
}

export interface BondTransform {
  midpoint: Vector3Tuple
  length: number
  direction: Vector3Tuple
}

export function safeBondTransform(start: Vector3Tuple, end: Vector3Tuple): BondTransform | null {
  const length = euclideanDistance(start, end)
  if (!Number.isFinite(length) || length <= Number.EPSILON) return null
  return {
    midpoint: [(start[0] + end[0]) / 2, (start[1] + end[1]) / 2, (start[2] + end[2]) / 2],
    length,
    direction: [(end[0] - start[0]) / length, (end[1] - start[1]) / length, (end[2] - start[2]) / length],
  }
}

/**
 * Temporary deterministic distance-based inference for visualization only.
 * It uses known covalent radii and never molecule names. It does not
 * determine bond order, aromaticity, periodic bonds, or chemistry semantics.
 * Scenes above MAX_INFERENCE_ATOMS are skipped to bound O(n²) work.
 */
export function inferBonds(atoms: MolecularAtom[], coordinateUnit: string): BondInferenceResult {
  if (atoms.length > MAX_INFERENCE_ATOMS) {
    return {
      bonds: [],
      warning: `Temporary bond inference was skipped because the structure has ${atoms.length} atoms; the safety limit is ${MAX_INFERENCE_ATOMS}.`,
    }
  }
  const bonds: MolecularBond[] = []
  for (let left = 0; left < atoms.length; left += 1) {
    for (let right = left + 1; right < atoms.length; right += 1) {
      const a = atoms[left]
      const b = atoms[right]
      const leftRadius = knownCovalentRadius(a.element)
      const rightRadius = knownCovalentRadius(b.element)
      if (leftRadius === undefined || rightRadius === undefined) continue
      const distance = euclideanDistance(a.position, b.position)
      const threshold = fromAngstrom((leftRadius + rightRadius) * 1.25, coordinateUnit)
      const minimumDistance = fromAngstrom(0.1, coordinateUnit)
      if (distance > minimumDistance && distance <= threshold) {
        bonds.push({ id: `inferred.${a.id}.${b.id}`, atomIds: [a.id, b.id], source: 'inferred' })
      }
    }
  }
  return { bonds }
}
