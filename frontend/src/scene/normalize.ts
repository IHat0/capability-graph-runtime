import type { PresetDetailResponse, SceneResponse } from '../api/types'
import { euclideanDistance, inferBonds } from './geometry'
import type { MolecularBond, MolecularMeasurement, MolecularScene, ScientificMetadata } from './types'
import { coordinateUnit } from './units'

function finiteNumber(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined
}

export function normalizeScene(raw: SceneResponse, detail?: PresetDetailResponse): MolecularScene {
  coordinateUnit(raw.coordinate_unit)
  const atoms = raw.atoms.map((atom) => ({
    id: atom.atom_identifier,
    element: atom.element,
    position: [...atom.coordinates] as [number, number, number],
    label: atom.atom_identifier,
  }))
  const atomById = new Map(atoms.map((atom) => [atom.id, atom]))
  const explicitBonds: MolecularBond[] = (raw.bonds ?? []).flatMap((bond) => {
    if (bond.atom_identifiers.length < 2) return []
    const [left, right] = bond.atom_identifiers
    if (!atomById.has(left) || !atomById.has(right) || left === right) return []
    return [{
      id: bond.bond_identifier,
      atomIds: [left, right],
      order: finiteNumber(bond.order),
      source: 'declared' as const,
    }]
  })
  const inference = raw.bonds === undefined ? inferBonds(atoms, raw.coordinate_unit) : undefined
  const bonds = inference?.bonds ?? explicitBonds

  // The current endpoint attaches declared/derived diatomic distances to bonds.
  // This adapter converts each independently into generic measurements so no
  // downstream module needs to know that endpoint-specific convention.
  const measurements: MolecularMeasurement[] = (raw.bonds ?? []).flatMap((bond) => {
    if (bond.atom_identifiers.length < 2) return []
    const [left, right] = bond.atom_identifiers
    const a = atomById.get(left)
    const b = atomById.get(right)
    if (!a || !b || left === right) return []
    const declaredValue = finiteNumber(bond.declared_distance)
    const backendDerivedValue = finiteNumber(bond.derived_distance)
    const geometricValue = euclideanDistance(a.position, b.position)
    return [{
      id: `measurement.${bond.bond_identifier}`,
      kind: 'distance' as const,
      atomIds: [left, right] as [string, string],
      unit: raw.coordinate_unit,
      declaredValue,
      backendDerivedValue,
      geometricValue,
      declaredDifference: declaredValue === undefined ? undefined : declaredValue - geometricValue,
      backendDifference: backendDerivedValue === undefined ? undefined : backendDerivedValue - geometricValue,
    }]
  })

  const quantumRegion = raw.quantum_region
  const validQuantumAtomIds = quantumRegion?.atom_identifiers.filter((id) => atomById.has(id)) ?? []
  const experiment = detail?.manifest.experiment
  const molecular = experiment?.molecular_system
  const electronic = experiment?.electronic_structure
  const quantum = experiment?.quantum_model
  const execution = experiment?.execution_policy
  const sceneModel = raw.scientific_model
  const scientific: ScientificMetadata = {
    charge: finiteNumber(sceneModel?.charge) ?? finiteNumber(molecular?.molecular_charge),
    spinMultiplicity: finiteNumber(sceneModel?.spin_multiplicity) ?? finiteNumber(molecular?.spin_multiplicity),
    basisSet: sceneModel?.basis_set ?? electronic?.basis_set,
    referenceMethod: sceneModel?.reference_method ?? electronic?.reference_method,
    activeElectronCount: finiteNumber(sceneModel?.active_electron_count) ?? finiteNumber(electronic?.active_electron_count),
    activeSpatialOrbitalCount: finiteNumber(sceneModel?.active_spatial_orbital_count) ?? finiteNumber(electronic?.active_spatial_orbital_count),
    activeOrbitalIndices: sceneModel?.active_orbital_indices ?? electronic?.active_orbital_indices,
    mapper: sceneModel?.mapper ?? quantum?.mapper,
    ansatz: sceneModel?.ansatz ?? quantum?.ansatz,
    runtimeIdentifier: execution?.runtime_identifier,
    networkDisabled: execution?.network_disabled,
    maximumDurationSeconds: execution?.maximum_duration_seconds,
  }

  return {
    id: raw.scene_identifier,
    structureId: raw.structure_identifier ?? molecular?.structure_artifact_identifier,
    structureHash: raw.structure_hash,
    experimentFingerprint: raw.experiment_fingerprint,
    expectedExperimentSha256: raw.expected_experiment_sha256 ?? detail?.manifest.expected_experiment_sha256 ?? undefined,
    experimentId: raw.experiment_identifier ?? experiment?.experiment_identifier,
    stage: raw.scene_stage,
    coordinateUnit: raw.coordinate_unit,
    atoms,
    bonds,
    bondDataSource: raw.bonds === undefined ? (inference?.warning ? 'skipped' : bonds.length > 0 ? 'inferred' : 'none') : 'explicit',
    normalizationWarnings: inference?.warning ? [inference.warning] : [],
    selections: quantumRegion ? [{
      id: quantumRegion.selection_identifier,
      label: 'Declared quantum region',
      atomIds: validQuantumAtomIds,
      kind: 'quantum-region',
    }] : [],
    regions: quantumRegion ? [{
      id: quantumRegion.selection_identifier,
      label: 'Quantum region',
      atomIds: validQuantumAtomIds,
      kind: 'quantum',
    }] : [],
    measurements,
    provenance: raw.provenance,
    artifactReferences: raw.artifact_references ?? [],
    scientific,
  }
}
