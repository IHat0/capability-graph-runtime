import type { Vector3Tuple } from '../api/types'

export interface MolecularAtom {
  id: string
  element: string
  position: Vector3Tuple
  label?: string
  fragmentId?: string
  residueId?: string
  provenance?: Record<string, unknown>
}

export interface MolecularBond {
  id: string
  atomIds: [string, string]
  order?: number
  source: 'declared' | 'inferred'
}

export interface MolecularSelection {
  id: string
  label: string
  atomIds: string[]
  kind: 'selection' | 'quantum-region' | 'active-region' | 'interaction'
}

export interface MolecularRegion {
  id: string
  label: string
  kind: 'quantum' | 'active' | 'binding-pocket' | 'custom'
  atomIds: string[]
}

export interface MolecularMeasurement {
  id: string
  kind: 'distance'
  atomIds: [string, string]
  unit: string
  declaredValue?: number
  backendDerivedValue?: number
  geometricValue: number
  declaredDifference?: number
  backendDifference?: number
}

export interface ScientificMetadata {
  charge?: number
  spinMultiplicity?: number
  basisSet?: string
  referenceMethod?: string
  activeElectronCount?: number
  activeSpatialOrbitalCount?: number
  activeOrbitalIndices?: number[]
  mapper?: string
  ansatz?: string
  runtimeIdentifier?: string
  networkDisabled?: boolean
  maximumDurationSeconds?: number
}

export interface MolecularScene {
  id: string
  structureId?: string
  structureHash?: string
  experimentFingerprint?: string
  expectedExperimentSha256?: string
  experimentId?: string
  stage?: string
  coordinateUnit: string
  atoms: MolecularAtom[]
  bonds: MolecularBond[]
  bondDataSource: 'explicit' | 'inferred' | 'none' | 'skipped'
  normalizationWarnings: string[]
  selections: MolecularSelection[]
  regions: MolecularRegion[]
  measurements: MolecularMeasurement[]
  provenance?: Record<string, unknown>
  artifactReferences: string[]
  scientific: ScientificMetadata
}
