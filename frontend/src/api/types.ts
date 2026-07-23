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
  local_simulator?: ExecutionTargetCapability
  ibm_quantum?: IBMExecutionCapability
}

export interface ExecutionTargetCapability {
  available: boolean
  reason: string | null
  maximum_run_seconds: number | null
}

export interface IBMExecutionCapability extends ExecutionTargetCapability {
  backend_name: string | null
  target_precision: number | null
  optimization_level?: number
  hardware_role?: string
}

export type RunStatus =
  | 'queued'
  | 'validating'
  | 'running_quantum_workflow'
  | 'running_local_preflight'
  | 'awaiting_ibm_submission'
  | 'queued_on_ibm'
  | 'running_on_ibm'
  | 'verifying_ibm_result'
  | 'authorized'
  | 'rejected'
  | 'failed'
  | 'interrupted'

export interface RunIdentity {
  run_identifier: string
  source_type: 'preset' | 'dynamic_experiment'
  source_identifier: string
  preset_identifier: string | null
  experiment_identifier: string
  experiment_fingerprint: string
  expected_experiment_sha256: string
  structure_identifier: string
}

export interface RunStateResponse extends RunIdentity {
  execution_target: 'local_simulator' | 'ibm_quantum'
  status: RunStatus
  created_at: string
  updated_at: string
  status_url: string
  structure_sha256?: string | null
  hamiltonian_sha256?: string | null
  receipt_sha256?: string | null
  execution_environment_identity?: string | null
  error?: { code: string; message: string }
  molecule?: SceneResponse
  ibm_job_identifier?: string | null
  ibm_backend_name?: string | null
}

export interface IBMExecutionEvidence {
  hardware_role: string
  submission_status: string
  job_identifier: string | null
  backend_name: string | null
  execution_integrity_passed: boolean
  scientific_quality_passed: boolean
  raw_qubit_expectation_hartree?: number
  non_nuclear_electronic_shift_hartree?: number
  electronic_constant_offsets_hartree?: Record<string, number>
  ibm_electronic_energy_hartree?: number
  nuclear_repulsion_energy_hartree?: number
  ibm_total_energy_hartree?: number
  local_exact_total_energy_hartree?: number
  local_vqe_total_energy_hartree?: number
  returned_standard_error?: number | null
  source_bound_circuit_sha256?: string
  transpiled_circuit_sha256?: string
  source_observable_sha256?: string
  transpiled_observable_sha256?: string
  layout_sha256?: string
  runtime_options?: {
    max_execution_time: number
    job_tags: string[]
  }
  execution_image_identifier?: string
  scientific_preflight_image_identifier?: string
  ibm_runtime_image_identifier?: string
  ibm_receipt_sha256?: string
  [key: string]: unknown
}

export interface ExperimentPlanResponse {
  schema_version: string
  experiment_identifier: string
  original_question: string
  specification: Record<string, unknown> | null
  assumptions: string[]
  warnings: string[]
  missing_fields: string[]
  ready_for_execution: boolean
  requested_execution_target: 'local_simulator' | 'ibm_quantum'
  specification_sha256: string | null
  experiment_fingerprint: string | null
  expected_experiment_sha256: string | null
  structure_identifier: string | null
  structure_hash: string | null
  molecule: SceneResponse | null
  created_at: string
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
  ibm_execution?: IBMExecutionEvidence | null
}

export interface RunVerificationResponse extends RunIdentity {
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
  ibm_execution?: IBMExecutionEvidence | null
}

export interface PublicArtifactIdentity {
  artifact_identifier: string
  artifact_type: string
  content_sha256: string
}

export interface RunReceiptResponse extends RunIdentity {
  schema_version: string
  execution_identifier: string
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
  ibm_execution?: IBMExecutionEvidence | null
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
  structure_identifier?: string
  expected_experiment_sha256?: string
  specification_sha256?: string
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
