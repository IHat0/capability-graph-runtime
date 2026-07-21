export type Vector3Tuple = [number, number, number]

export interface HealthResponse {
  service: string
  status: string
  version: string
  execution?: RunCapabilityResponse
}

export interface RunCapabilityResponse {
  available: boolean
  execution_targets: string[]
  reason: string | null
  maximum_run_seconds: number | null
}

export type RunStatus =
  | 'queued'
  | 'validating'
  | 'running_quantum_workflow'
  | 'authorized'
  | 'rejected'
  | 'failed'
  | 'interrupted'

export interface RunIdentity {
  run_identifier: string
  preset_identifier: string
  experiment_identifier: string
  experiment_fingerprint: string
  expected_experiment_sha256: string
  structure_identifier: string
}

export interface RunStateResponse extends RunIdentity {
  execution_target: 'local_simulator'
  status: RunStatus
  created_at: string
  updated_at: string
  status_url: string
  structure_sha256?: string | null
  hamiltonian_sha256?: string | null
  receipt_sha256?: string | null
  execution_environment_identity?: string | null
  error?: { code: string; message: string }
}

export interface RunResultsResponse extends RunIdentity {
  structure_sha256: string
  hamiltonian_sha256: string
  exact_scientific_result_sha256: string
  vqe_scientific_result_sha256: string
  scientific_outcome_sha256: string
  exact_total_energy_hartree: number
  vqe_total_energy_hartree: number
  absolute_difference_hartree: number
  tolerance_hartree: number
  energy_unit: 'hartree'
  exact_solver_metadata: Record<string, unknown>
  vqe_solver_metadata: Record<string, unknown>
  optimizer_evaluations: number | null
  converged: boolean | null
  compatibility_warnings: unknown[]
  execution_environment_identity: string
  receipt_sha256: string
}

export interface RunVerificationResponse {
  run_identifier: string
  preset_identifier: string
  experiment_identifier: string
  experiment_fingerprint: string
  expected_experiment_sha256: string
  structure_identifier: string
  structure_sha256: string
  verification_completed: boolean
  verification_passed: boolean
  authorization_state: 'authorized' | 'rejected'
  blocking_findings: unknown[]
  nonblocking_findings: unknown[]
  tolerance_check: Record<string, unknown> | null
  scientific_identity_checks: unknown[]
  artifact_integrity_checks: unknown[]
  checks: unknown[]
  compatibility_warnings: unknown[]
}

export interface PublicArtifactIdentity {
  artifact_identifier: string
  artifact_type: string
  content_sha256: string
}

export interface RunReceiptResponse {
  schema_version: string
  run_identifier: string
  preset_identifier: string
  execution_identifier: string
  experiment_identifier: string
  experiment_fingerprint: string
  expected_experiment_sha256: string
  structure_identifier: string
  structure_sha256: string
  hamiltonian_sha256: string
  exact_scientific_result_sha256: string
  vqe_scientific_result_sha256: string
  scientific_outcome_sha256: string
  execution_environment_identity: string
  receipt_sha256: string
  verification_passed: boolean
  authorization_state: 'authorized' | 'rejected'
  authorized: boolean
  artifacts: PublicArtifactIdentity[]
}

export interface PresetSummaryResponse {
  preset_identifier: string
  experiment_identifier: string
  elements: string[]
  atom_count: number
  coordinate_unit: string
  declared_bond_distance?: number | null
  molecular_charge: number
  spin_multiplicity: number
  basis_set: string
  experiment_fingerprint?: string
}

export interface PresetListResponse {
  presets: PresetSummaryResponse[]
  count: number
}

export interface RawAtomResponse {
  atom_identifier: string
  element: string
  coordinates: Vector3Tuple
}

export interface RawBondResponse {
  bond_identifier: string
  atom_identifiers: string[]
  order?: number | null
  declared_distance?: number | null
  derived_distance?: number | null
}

export interface RawQuantumRegionResponse {
  selection_identifier: string
  atom_identifiers: string[]
}

export interface RawScientificModelResponse {
  charge?: number
  spin_multiplicity?: number
  basis_set?: string
  reference_method?: string
  active_electron_count?: number
  active_spatial_orbital_count?: number
  active_orbital_indices?: number[]
  mapper?: string
  ansatz?: string
}

export interface SceneResponse {
  scene_identifier: string
  scene_stage?: string
  experiment_identifier?: string
  experiment_fingerprint?: string
  structure_hash?: string
  coordinate_unit: string
  atoms: RawAtomResponse[]
  bonds?: RawBondResponse[]
  quantum_region?: RawQuantumRegionResponse | null
  scientific_model?: RawScientificModelResponse
  provenance?: Record<string, unknown>
  artifact_references?: string[]
}

export interface RawMolecularSystem {
  molecular_charge?: number
  spin_multiplicity?: number
  coordinate_unit?: string
  structure_artifact_identifier?: string
}

export interface RawElectronicStructure {
  basis_set?: string
  reference_method?: string
  active_electron_count?: number
  active_spatial_orbital_count?: number
  active_orbital_indices?: number[]
}

export interface RawQuantumModel {
  mapper?: string
  ansatz?: string
}

export interface RawExecutionPolicy {
  runtime_identifier?: string
  network_disabled?: boolean
  maximum_duration_seconds?: number
}

export interface PresetDetailResponse {
  preset_identifier: string
  manifest: {
    expected_experiment_sha256?: string | null
    experiment: {
      experiment_identifier?: string
      molecular_system?: RawMolecularSystem
      electronic_structure?: RawElectronicStructure
      quantum_model?: RawQuantumModel
      execution_policy?: RawExecutionPolicy
    }
  }
}
