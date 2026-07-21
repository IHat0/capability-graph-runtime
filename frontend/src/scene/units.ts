import type { Vector3Tuple } from '../api/types'

export type CanonicalCoordinateUnit = 'angstrom' | 'bohr' | 'nanometre'

export interface CoordinateUnitDefinition {
  canonical: CanonicalCoordinateUnit
  angstromsPerUnit: number
  display: string
}

export class UnsupportedCoordinateUnitError extends Error {
  constructor(readonly unit: string) {
    super(`Unsupported coordinate unit "${unit}". Supported units are angstrom, bohr, and nanometre/nanometer aliases.`)
    this.name = 'UnsupportedCoordinateUnitError'
  }
}

const ANGSTROM: CoordinateUnitDefinition = { canonical: 'angstrom', angstromsPerUnit: 1, display: 'angstrom' }
const BOHR: CoordinateUnitDefinition = { canonical: 'bohr', angstromsPerUnit: 0.529177210903, display: 'bohr' }
const NANOMETRE: CoordinateUnitDefinition = { canonical: 'nanometre', angstromsPerUnit: 10, display: 'nanometre' }

const UNITS = new Map<string, CoordinateUnitDefinition>([
  ['angstrom', ANGSTROM], ['angstroms', ANGSTROM], ['ångström', ANGSTROM], ['ångströms', ANGSTROM], ['å', ANGSTROM],
  ['bohr', BOHR], ['bohrs', BOHR], ['a0', BOHR],
  ['nanometre', NANOMETRE], ['nanometres', NANOMETRE], ['nanometer', NANOMETRE], ['nanometers', NANOMETRE], ['nm', NANOMETRE],
])

export function coordinateUnit(unit: string): CoordinateUnitDefinition {
  const definition = UNITS.get(unit.trim().toLowerCase())
  if (!definition) throw new UnsupportedCoordinateUnitError(unit)
  return definition
}

export function toAngstrom(value: number, unit: string): number {
  return value * coordinateUnit(unit).angstromsPerUnit
}

export function fromAngstrom(value: number, unit: string): number {
  return value / coordinateUnit(unit).angstromsPerUnit
}

export function pointToAngstrom(point: Vector3Tuple, unit: string): Vector3Tuple {
  const factor = coordinateUnit(unit).angstromsPerUnit
  return [point[0] * factor, point[1] * factor, point[2] * factor]
}
