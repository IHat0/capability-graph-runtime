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
  source_type: 'preset' | 'dynamic_experiment' | 'approved_experiment'
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

export type ScientificFieldProvenance = 'explicit' | 'derived' | 'assumed' | 'missing'

export interface ProvenancedValue<T> {
  value: T | null
  provenance: ScientificFieldProvenance
}

export interface InterpretedAtom {
  element: string
  coordinates: Vector3Tuple | null
}

export interface InterpretedBondLength {
  atom_indices: [number, number]
  value: number
  unit: 'angstrom' | 'bohr'
}

export interface ModelProvenance {
  provider_kind: 'openai_compatible_http' | 'controlled_test_provider'
  model_name: string
  prompt_sha256: string
  response_sha256: string
  requested_at: string
  repair_attempted: boolean
  request_count_for_interpretation: number
}

export interface InterpretedScientificSpecification {
  schema_version: string
  original_question: string
  scientific_objective: ProvenancedValue<string>
  requested_quantity: ProvenancedValue<string>
  molecule: {
    name: ProvenancedValue<string>
    formula: ProvenancedValue<string>
    smiles: ProvenancedValue<string>
    inchi: ProvenancedValue<string>
    atoms: ProvenancedValue<InterpretedAtom[]>
    geometry_description: ProvenancedValue<string>
    bond_lengths: ProvenancedValue<InterpretedBondLength[]>
  }
  coordinate_unit: ProvenancedValue<string>
  charge: ProvenancedValue<number>
  multiplicity: ProvenancedValue<number>
  basis: ProvenancedValue<string>
  electronic_structure_method: ProvenancedValue<string>
  active_space: ProvenancedValue<string>
  mapper: ProvenancedValue<string>
  ansatz: ProvenancedValue<string>
  optimizer: ProvenancedValue<string>
  tolerance: ProvenancedValue<number>
  requested_execution_target: ProvenancedValue<string>
  requested_backend: ProvenancedValue<string>
  shots: ProvenancedValue<number>
  precision: ProvenancedValue<number>
  assumptions: string[]
  missing_required_information: string[]
  warnings: string[]
  interpretation_status: 'ready_for_review' | 'needs_clarification' | 'interpretation_failed'
  execution_support_status: 'supported' | 'requires_compiler_capability' | 'needs_clarification'
  model_provenance: ModelProvenance
}

export interface InterpretationResponse {
  schema_version: string
  interpretation_identifier: string
  original_question: string
  specification: InterpretedScientificSpecification
  assumptions: string[]
  missing_required_information: string[]
  warnings: string[]
  interpretation_status: string
  execution_support_status: string
  model_provenance: ModelProvenance
  scientist_approval_possible: boolean
  created_at: string
}

export interface ApprovedExperimentResponse {
  schema_version: string
  experiment_identifier: string
  interpretation_identifier: string
  original_question: string
  specification: InterpretedScientificSpecification
  specification_sha256: string
  requested_execution_target: 'ibm_quantum' | 'local_simulator'
  status: 'ready_for_ibm_submission' | 'approved_pending_compiler_support'
  assumptions_accepted: true
  scientist_reviewed_overrides: string[]
  approved_at: string
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
  atom_name?: string | null
  residue_name?: string | null
  chain_identifier?: string | null
  residue_sequence?: string | null
  partial_charge?: number | null
  source_atom_identifier?: string | null
  structure_artifact_identifier?: string | null
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

export type ResearchInputArtifactType =
  | 'protein_structure'
  | 'ligand_structure'
  | 'molecular_structure'
  | 'prepared_receptor'
  | 'prepared_ligand'

export interface ResearchInputReference {
  reference_identifier: string
  artifact_type: ResearchInputArtifactType
  artifact_identifier: string
}

export interface ResearchArtifactReference {
  artifact_identifier: string
  schema_version: { major: number; minor: number; patch: number }
  artifact_type: string
  media_type: string
  content_sha256: string
  byte_size: number | null
  storage_location: string | null
  metadata: Record<string, unknown>
  provenance: {
    producer: string
    producer_version: { major: number; minor: number; patch: number } | null
    execution_identifier: string | null
    source: string
  }
  parents: unknown[]
}

export interface ResearchClarificationPrompt {
  requirement_identifier: string
  question: string
}

export interface ResearchConversationTurn {
  turn_identifier: string
  role: 'scientist' | 'pulsate'
  content: string
  created_at: string
  requirement_identifier: string | null
}

export interface ResearchIntentProposal {
  task_type: string
  supporting_quote: string
  provider_kind: string
  model_name: string
}

export interface ResearchRequirement {
  operation: string
  requested_output: string
  supporting_quote: string
}

export interface ResearchRequirementProposal {
  proposal_identifier: string
  requirements: ResearchRequirement[]
  summary: string
  provider_kind: string
  model_name: string
}

export interface ValidatedResearchRequirements {
  operations: string[]
  requested_outputs: string[]
  required_artifact_types: string[]
  capability_profile: string
  source_proposal_identifier: string
}

export interface ResearchEvidenceQuote {
  field_name: string
  turn_identifier: string
  supporting_quote: string
}

export interface ResearchEvidenceProposal {
  proposal_identifier: string
  source_kind: 'conversation_extraction' | 'deterministic_structure_resolution' | 'exact_identifier_acquisition' | 'named_identifier_acquisition' | 'entity_resolution_candidates'
  summary: string
  input_references: ResearchInputReference[]
  artifact_references: ResearchArtifactReference[]
  covalent_reaction_target: Record<string, unknown> | null
  partial_covalent_reaction_target: Record<string, unknown> | null
  supporting_quotes: ResearchEvidenceQuote[]
  provider_kind: string | null
  model_name: string | null
  reason: string | null
  entity_candidates: Array<{
    entity_type: 'protein' | 'ligand'
    source_kind: 'uniprot' | 'pubchem'
    source_identifier: string
    display_label: string
    structure_identifier: string | null
    confidence: 'high' | 'ambiguous'
  }>
}

export interface ScientistFacingResult {
  original_request: string
  resolved_interpretation: string
  structures_and_entities: string[]
  methods: string[]
  assumptions: string[]
  scientific_result: string
  verification_status: 'passed' | 'failed' | 'inconclusive'
  uncertainty: string[]
  replanning_history: string[]
  important_limitations: string[]
  evidence_artifact_identifiers: string[]
  scene_identifiers: string[]
  principal_result: string | null
  candidate_ranking: string[]
  confidence_and_uncertainty: string[]
  recommended_next_step: string | null
  synthesis_provider_kind: string | null
  synthesis_model_name: string | null
}

export type ResearchSessionStatus =
  | 'understanding'
  | 'awaiting_clarification'
  | 'awaiting_approval'
  | 'planned'
  | 'running'
  | 'verifying'
  | 'replanning'
  | 'completed'
  | 'failed'

export interface ResearchCompilationResponse {
  compilation_identifier: string
  execution_identifier: string
  effective_question: string
  canonical_objective: Record<string, unknown>
  canonical_plan: Record<string, unknown>
  canonical_graph: Record<string, unknown>
}

export interface ResearchSessionResponse {
  session_identifier: string
  created_at: string
  updated_at: string
  revision: number
  status: ResearchSessionStatus
  conversation: ResearchConversationTurn[]
  input_references: ResearchInputReference[]
  artifact_references: ResearchArtifactReference[]
  unapproved_input_artifact_identifiers: string[]
  next_questions: ResearchClarificationPrompt[]
  clarification_attempts: Record<string, number>
  intent_proposal: ResearchIntentProposal | null
  requirement_proposal: ResearchRequirementProposal | null
  accepted_research_requirements: ValidatedResearchRequirements | null
  evidence_proposal: ResearchEvidenceProposal | null
  accepted_partial_covalent_reaction_target: Record<string, unknown> | null
  accepted_evidence: ResearchEvidenceProposal[]
  compilation: ResearchCompilationResponse | null
  execution_status: string | null
  scene_identifier: string | null
  scientist_result: ScientistFacingResult | null
  scientist_summary: string
}

export interface ResearchVisualizationStructure {
  artifact_identifier: string
  artifact_type: string
  media_type: string
  label: string
  role: 'protein' | 'ligand_or_candidate' | 'molecular_structure'
  candidate_identifier: string | null
  generation: number | null
  selected: boolean
  conformation_count: number
  source_kind: string | null
  source_identifier: string | null
  confidence: string | null
  content_sha256: string
}

export interface ResearchVisualizationSelection {
  selection_identifier: string
  label: string
  kind: string
  structure_artifact_identifier: string | null
  atom_identifiers: string[]
  residue_identifiers: string[]
  evidence_artifact_identifier: string
}

export interface ResearchVisualizationInteraction {
  interaction_identifier: string
  interaction_type: string
  candidate_identifier: string | null
  structure_artifact_identifiers: string[]
  atom_identifiers: string[]
  residue_identifiers: string[]
  distance_angstrom: number | null
  angle_degree: number | null
  calculation_method: string
  evidence_artifact_identifier: string
}

export interface ResearchVisualizationOverlay {
  overlay_identifier: string
  kind: string
  label: string
  structure_artifact_identifier: string | null
  candidate_identifier: string | null
  value: unknown
  unit: string | null
  atom_identifiers: string[]
  residue_identifiers: string[]
  verification_status: string
  uncertainty: string | null
  evidence_artifact_identifier: string
  method: string | null
}

export interface ResearchVisualizationCandidate {
  candidate_identifier: string
  generation: number
  parent_candidate_identifiers: string[]
  transformation: Record<string, unknown> | null
  properties_and_calculations: Array<Record<string, unknown>>
  verification: Array<Record<string, unknown>>
  selection_rationale: string | null
  selected: boolean
  structure_artifact_identifiers: string[]
}

export interface ResearchVisualizationComparison {
  comparison_identifier: string
  kind: string
  left_identifier: string
  right_identifier: string
  differences: unknown
  evidence_artifact_identifier: string | null
}

export interface ResearchVisualizationExportItem {
  artifact_identifier: string
  artifact_type: string
  media_type: string
  content_sha256: string
  category: 'structure' | 'evidence' | 'report'
}

export interface ResearchVisualizationWorkspace {
  schema_version: 'pulsate.research-visualization/v1'
  session_identifier: string
  revision: number
  scene_identifier: string | null
  structures: ResearchVisualizationStructure[]
  selections: ResearchVisualizationSelection[]
  interactions: ResearchVisualizationInteraction[]
  overlays: ResearchVisualizationOverlay[]
  candidates: ResearchVisualizationCandidate[]
  lineage: Array<Record<string, unknown>>
  comparisons: ResearchVisualizationComparison[]
  verification_artifact_identifiers: string[]
  export_items: ResearchVisualizationExportItem[]
  grounding_policy: 'persisted_artifact_or_deterministic_computation_only'
}

export interface ResearchInputUpload {
  artifactType: ResearchInputArtifactType
  file: File
}

export interface UploadedResearchInput {
  inputReference: ResearchInputReference
  artifactReference: ResearchArtifactReference
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

export type {
  FetchedMolecularResource,
  LoadedMolecularProjectScene,
  LoadedMolecularStructure,
  MolecularTopologyIndex,
  MolecularArtifactIdentity,
  ProjectedMolecularSceneMetadata,
  ProjectedMolecularStructureMetadata,
} from '../scene/native-project'
