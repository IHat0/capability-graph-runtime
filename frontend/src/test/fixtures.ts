import type { PresetDetailResponse, SceneResponse } from '../api/types'

export const currentFixtureScene: SceneResponse = {
  scene_identifier: 'scene.fixture',
  scene_stage: 'declared',
  experiment_identifier: 'fixture-ground-state-v1',
  experiment_fingerprint: 'experiment-fingerprint',
  structure_hash: 'structure-hash',
  coordinate_unit: 'angstrom',
  atoms: [
    { atom_identifier: 'atom-a', element: 'H', coordinates: [0, 0, 0] },
    { atom_identifier: 'atom-b', element: 'H', coordinates: [0, 0, 0.735] },
  ],
  bonds: [{
    bond_identifier: 'bond.a-b',
    atom_identifiers: ['atom-a', 'atom-b'],
    declared_distance: 0.735,
    derived_distance: 0.735,
  }],
  quantum_region: { selection_identifier: 'selection.full', atom_identifiers: ['atom-a', 'atom-b'] },
  scientific_model: {
    charge: 0,
    spin_multiplicity: 1,
    basis_set: 'sto-3g',
    reference_method: 'restricted_hartree_fock',
    active_electron_count: 2,
    active_spatial_orbital_count: 2,
    mapper: 'jordan_wigner',
    ansatz: 'uccsd',
  },
}

export const currentFixtureDetail: PresetDetailResponse = {
  preset_identifier: 'fixture-ground-state-v1',
  manifest: {
    expected_experiment_sha256: 'expected-experiment-sha256',
    experiment: {
      experiment_identifier: 'fixture-ground-state-v1',
      molecular_system: {
        molecular_charge: 0,
        spin_multiplicity: 1,
        structure_artifact_identifier: 'molecular_structure',
      },
      electronic_structure: {
        basis_set: 'sto-3g',
        reference_method: 'restricted_hartree_fock',
        active_electron_count: 2,
        active_spatial_orbital_count: 2,
        active_orbital_indices: [0, 1],
      },
      quantum_model: { mapper: 'jordan_wigner', ansatz: 'uccsd' },
      execution_policy: { runtime_identifier: 'quantum_preflight_linux', network_disabled: true, maximum_duration_seconds: 180 },
    },
  },
}
