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
  | 'pbpk_compound_dossier'
  | 'quantitative_activity_evidence'
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
  execution_steps?: Array<{
    step_identifier: string
    capability_name: string
    status: 'pending' | 'running' | 'succeeded' | 'failed' | 'blocked'
    error_code: string | null
    error_message: string | null
  }>
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
  display_name?: string
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
  construction_summary?: {
    name: string; formula: string; atom_count: number
    generated_conformers: number; converged_conformers: number
    structure_artifact_identifier: string; identity_verified: boolean
    active_electron_count?: number; active_spatial_orbital_count?: number
    logical_qubits?: number; hardware_status?: string
    selected_compute?: 'classical'; computation_reason?: string
    xyz_artifact_identifier?: string; workflow?: string[]
    energies: Array<{ label: string; value: number; unit: string }>
  } | null
  virtual_organism?: {
    assessment_artifact_identifier: string; assessment_sha256: string
    functional_exposure?: NativeFunctionalExposure
    sponsor_dossiers?: Array<{ experiment_provenance: { experiment_class: string; historical_private_data_claim: false;
      input_receipts: Array<{ identifier: string; role: string; provenance_class: string; values: unknown[];
        unit: string | null; rationale: string; uncertainty: string; source_sha256: string }> } }>
    schema_version: 'pulsate.virtual-organism/v1'
    candidate: { name: string; inchikey: string; pubchem_cid: number | null; source_url: string | null }
    status: 'exposure_supported' | 'insufficient_parameterization'
    request: { dose: number; dose_unit: string; route: string; duration_h: number; seed: number; population_size: number; administration_times_h: number[] }
    runs: Array<{ species: string; file_artifacts: Record<string, string>; result: { subject_count: number } }>
    population: { interval_scope: string; series: Array<{ species: string; path: string; organ: string; compartment: string; subject_count: number; times_h: number[]; median_umol_l: number[]; p05_umol_l: number[]; p95_umol_l: number[] }> }
    comparison: { comparison_scope: string; subjects: Array<{ species: string; subject_identifier: string; plasma_metrics: { cmax_umol_l: number; tmax_h: number; auc_0_t_umol_h_l: number }; tissue_to_plasma_auc_ratios: Record<string, number> }> }
    evidence_quality: Record<string, number>
    missing: Array<{ species: string; reason: string }>
    exposure_relevance: { status: string; reason: string }
    activity_comparisons?: Array<{ status: string; reason: string; activity?: { target: string; species: string; kind: string; value: number; unit: string; source: string; assay_context: string; compatibility_limitations: string }; peak_exposure_to_activity_ratios?: number[] }>
    verification: { passed: boolean; verification_scope: string }
    parameters: Array<{ name: string; species: string; value: number | null; interval?: [number, number] | null; unit: string; classification: string; source: string; method: string; uncertainty: string | null }>
    adme_parameterization?: {
      verification: { passed: boolean; scope: string }
      prediction: {
        status: string; request_sha256: string; timestamp: string
        descriptors: Record<string, number>
        native_translation_audit?: NativeADMETranslationAudit[]
        dossiers: Array<{ species: string; ionization_status: string; ionization_source: string; parameter_conflicts: Array<{ parameter: string; reason: string }>
          parameters: Record<string, ADMEParameter>; adme_parameters: Record<string, ADMEParameter> }>
      }
    } | null
    drug_parameter_uncertainty?: { scope: string; dose_analysis?: Array<{ species: string; dose_normalized_cmax_max_min_ratio: number; dose_normalized_auc_max_min_ratio: number; tissue_order_changes: boolean }>; scenarios: Array<{ species: string; scenario_identifier: string; scenario_kind: string;
      population?: { interval_scope: string; series: Array<{ species: string; path: string; organ: string; compartment: string; subject_count: number; times_h: number[]; median_umol_l: number[]; p05_umol_l: number[]; p95_umol_l: number[] }> };
      scenario_policy: Record<string, unknown>; file_artifacts: Record<string, string>;
      series: Array<{ path: string; organ: string; compartment: string; subject_identifier: string; times_h: number[]; values_umol_l: number[] }> }> }
    assumptions: string[]; limitations: string[]
    computation_selection: { selected_compute: string; quantum_selected: boolean; reason: string }
  } | null
  prospective_assessment?: {
    virtual_investigation?: VirtualInvestigation | null
    assessment_artifact_identifier: string; assessment_sha256: string
    verification_scope: string; assumptions: string[]; blinding_limitations: string[]
    computation_selection: { selected_compute: string; quantum_selected: boolean; reason: string }
    target_selection: { uniprot_accession?: string; domain?: { description: string }; selected?: {
      pdb_id: string; chain: string; resolution_angstrom: number; mutation_records: string[]; missing_records: string[]
    }; policy?: string; limitations?: string[] }
    source_audit: Array<{ url: string; class: string; sha256: string }>
    candidates: Array<{
      candidate_identifier: string; name: string; recommendation: string; reason: string
      identity: { standard_inchikey: string; public_identity_checked: boolean; pubchem_cid?: number }
      properties: Record<string, number>
      intended_target: { best_vina_score_kcal_per_mol: number; limitation: string }
      hypotheses: Array<{ metric: string; computed_value: number; screen_threshold: number; hypothesis: string; limitation: string }>
      unsupported_risk_dimensions: string[]
      alternative_targets?: { status: string; targets: Array<{
        status: string; reason?: string
        target: { uniprot_accession: string; pdb_id: string; similarity: number }
        comparison?: { alternative_score_kcal_per_mol: number; raw_score_difference_kcal_per_mol: number; limitation: string }
        functional_hypotheses?: Array<{ go_identifier: string; annotation: string; hypothesis: string; limitation: string }>
      }> }
      orthogonal_follow_up?: string[]
    }>
  } | null
  grounding_policy: 'persisted_artifact_or_deterministic_computation_only'
}

export interface ADMEParameter {
  value: number | null; unit: string; classification: string; source: string; method: string
  uncertainty: string | null; interval?: [number, number] | null
  prediction?: { model: string; model_version: string; model_sha256: string; endpoint_definition: string;
    validation?: { overall?: { n: number; mae: number; rmse: number; spearman: number | null; interval_coverage: number }; unit?: string; scope?: string; report_sha256?: string; status?: string };
    applicability: { status: string; nearest_training_tanimoto: number; training_graph_seen: boolean }; translation: { formula: string } | null } | null
}

export interface NativeExposureEndpoints {
  endpoints: Array<{ species: string; organ: string; compartment: string; native_path?: string | null;
    subject_identifier?: string | null; peak_umol_l: number; sampled_tmax_h: number[];
    auc_umol_h_l: number; window_h: number[]; limitation: string }>
  tissue_plasma_ratios: Array<{ species: string; organ: string; compartment: string; subject_identifier?: string | null;
    concentration_basis: string; auc_tissue_plasma_ratio: number; window_h: number[] }>
  interpretation: string
}

export interface FunctionalActivityPrediction {
  schema: 'pulsate.functional-activity-prediction/v1'
  scope: string; manifest_sha256: string; universe_sha256: string
  coverage: { supported_model_targets: number; unsupported_model_targets: number; accepted_candidate_targets: number; universe_targets: number }
  unresolved_source_rows: Array<{ label: string; status: string; reason: string }>
  targets: Array<{ gene: string; target_accession: string; status: 'predicted' | 'refused' | 'unsupported'; reason?: string;
    kind?: string; action?: string; readout?: string; classification?: string; concentration_basis?: string;
    value_umol_l?: number; interval_umol_l?: number[]; interval_definition?: string; uncertainty?: string; limitation?: string;
    applicability?: { status: string; training_graph_seen?: boolean; nearest_training_tanimoto?: number };
    model_sha256?: string; research_gate?: { accepted: boolean; failed_metrics: string[]; scope: string;
      metrics: { n: number; mae?: number; rmse?: number; interval_coverage?: number; spearman?: number | null; median_multiplicative_error?: number } } }>
}

export interface VirtualInvestigation {
  policy_sha256: string | null
  prospective_policy: { cutoff: string | null; cutoff_instant?: string | null; cutoff_source: string | null; allow_modern_general_knowledge: boolean }
  verification_scope: string
  bioactivity_structural?: { status: string; reason?: string;
    panel?: { candidates: Array<{ candidate_identifier: string; excluded: Array<{ target_accession?: string; reason: string }> }> };
    screening?: { candidates: Array<{ candidate_identifier: string; targets: Array<{ status: string; reason?: string;
      target: { uniprot_accession: string; pdb_id: string; chain: string }; docking?: { vina_scores_kcal_per_mol: number[] } }> }> };
    verification?: { passed: boolean } }
  candidates: Array<{
    candidate_identifier: string; identity: { name: string; inchikey: string }
    candidate_status: string; assessment_reason: string
    inference_levels: Record<string, boolean>
    reviewed_exposure_snapshots?: Array<{ species: string; organ: string; compartment: string;
      values_umol_l: number[]; times_h: Array<number | null>; classification: string;
      timecourse_available: false; path: string; uncertainty: string; applicability: string; limitation: string;
      compartment_translation?: { rationale: string; limitations: string } | null;
      datum: { identifier: string; original_source?: { source_sha256: string } } }>
    quantitative_activity?: Array<{ functional_assay_sha256?: string; target_accession: string; species: string;
      kind: string; value: number; unit: string; assay_type?: string; assay_host?: string; functional_direction?: string;
      concentration_basis: string; concentration_basis_classification?: string; measured_unbound_concentration?: boolean;
      assay_context: string; hill_coefficient?: number; functional_transfer_supported?: boolean;
      uncertainty: string; applicability?: string; source: string; source_sha256: string;
      datum?: { original_source?: { source_sha256: string } } }>
    bioactivity_hypotheses: { status?: string; reason?: string; targets: Array<{
      target_accession: string; status: string
        evidence: Array<{ neighbour_chembl_id: string; local_similarity: number; classification: string; organism?: string; intended_target?: boolean | null;
        quantitative_neighbour_evidence: { kind: string; value_umol_l: number; uncertainty: string } }>
      tissue_relevance: { classification?: string; limitation?: string; status?: string; reason?: string; missing?: string[];
        go?: Array<{ identifier: string; term: string; evidence: string }>;
        expression?: Array<{ source_url: string; source_sha256: string; historical_qualification: string }> }
    }> }
    dossier_acquisition: { status: string; missing: string[]; conflicts: Array<{ parameter: string; reason: string }>;
      experiment_provenance?: { experiment_class: 'sponsor_side_prospective'; historical_private_data_claim: false;
        dossier_file_sha256?: string | null; canonical_document_sha256: string;
        input_receipts: Array<{ identifier: string; role: string; provenance_class: string; values: unknown[];
          unit: string | null; rationale: string; uncertainty: string; source_sha256: string }> };
      eligibility: { decisions: Array<{ identifier: string; eligible?: boolean; status: string; historically_qualified?: boolean; reason: string }> } }
    regimen: { status: string; source?: string; classification?: string; reason?: string; missing?: string[] }
    virtual_organism: ResearchVisualizationWorkspace['virtual_organism']
    native_sensitivity?: { status: string; reason?: string; nominal_withheld?: boolean;
      design?: { total_combinations: number; exhaustive: boolean; limitation: string; cases: Array<{ case_identifier: string }> };
      executions?: Array<{ case_identifier: string; status: string; reason?: string;
        virtual_organism?: ResearchVisualizationWorkspace['virtual_organism'] }>;
      summary?: { nominal_withheld: boolean; complete_discrete_space: boolean; qualitative_conclusion: string;
        endpoint_ranges: Record<string, { peak_range_umol_l: number[]; auc_range_umol_h_l: number[]; sampled_tmax_range_h?: number[] | null }> } }
    native_exposure_endpoints?: { reference: NativeExposureEndpoints | null;
      conditional: Array<{ case_identifier: string; metrics: NativeExposureEndpoints }> }
    general_safety_panel?: { panel_sha256: string; version: string; scope: string; coverage_statement: string;
      source_count_discrepancies: Array<{ source_label: string; stated_count: number; enumerated_count: number }>;
      targets: Array<{ gene: string; target_accession: string; name: string; source_domains: string[]; status: string;
        intended_target: boolean | null; limitation: string }> } | null
    target_activity_prediction?: { scope: string; manifest_sha256: string; panel_sha256: string;
      targets: Array<{ gene: string; target_accession: string; status: string; reason?: string; kind?: string;
        unit?: string; value_umol_l?: number; interval_umol_l?: number[]; interval_definition?: string;
        concentration_basis?: string; functional_direction?: string | null; limitation?: string; uncertainty?: string;
        applicability?: { status: string; training_graph_seen: boolean; nearest_training_tanimoto: number };
        research_gate?: { accepted: boolean; failed_metrics: string[]; scope: string;
          metrics: { n: number; mae?: number; rmse?: number; interval_coverage?: number; spearman?: number | null } };
        model_sha256?: string }> } | null
    functional_activity_prediction?: FunctionalActivityPrediction | null
    functional_tissue_relevance?: { targets: Array<{ target_accession: string; action?: string; activity_status: string;
      priority_status: string; limitation: string; tissue_evidence?: { classification?: string; limitation?: string; reason?: string;
        go?: Array<{ identifier: string; term: string; evidence: string }>;
        expression?: Array<{ source_url: string; source_sha256: string; historical_qualification: string }> } | null }> } | null
    functional_research_assessment?: { status: string; reason: string; scope: string; reject_available: false;
      rule_results: Array<{ identifier: string; passed: boolean; missing_gates: string[] }> }
    exposure_activity: Array<{ status: string; target_accession: string; reason?: string; limitation?: string; peak_activity_ratios?: number[]; uncertainty?: string;
      activity_interval_umol_l?: number[]; interval_definition?: string;
      comparisons?: Array<{ exposure_case_identifier?: string | null; subject_identifier?: string | null; native_path?: string | null;
        peak_umol_l: number; peak_activity_ratio_interval: number[]; status: string }> }>
    functional_models: Array<{ status: string; model_identifier?: string; reason?: string; exposure_case_identifier?: string | null;
      result?: { model: { identifier: string; name: string; sha256: string; species: string }; inference_level: string;
        perturbation?: { transfer_receipt?: { qualified?: boolean; qualification_state?: string;
          candidate_decision_authority?: boolean; qualification_scope?: { benchmark_discrepancy?: string;
            limitations?: string[]; benchmarks?: Array<{ identifier: string; maximum_absolute_error: number;
              unit: string; error_budget: number; biological_error_budget_passed: boolean }> } } };
        response: Record<string, { unit: string; baseline_final: number; perturbed_final: number; final_difference: number; maximum_absolute_difference: number }>;
        waveform_evidence?: { full_response_sha256: string; scope: string;
          curves: Array<{ condition: string; columns: string[]; sample_count: number; waveform_sha256: string;
            display_projection?: { method: string; values: number[][]; sample_count: number; scope: string } }> };
        limitation: string; verification: { passed: boolean } } }>
  }>
}

export interface ScientificDisplayProjection {
  method: string; original_sample_count: number; display_sample_count: number;
  source_table_sha256: string; source_json_pointer: string; externalized_fields: string[]; scope: string
}

export interface NativeFunctionalExposure {
  schema: 'pulsate.native-functional-exposure/v1'
  functional_activity_prediction: FunctionalActivityPrediction
  functional_tissue_relevance?: VirtualInvestigation['candidates'][number]['functional_tissue_relevance']
  quantitative_activity: Array<{ target_accession: string; kind: string; value: number;
    functional_direction: string; concentration_basis: string; uncertainty: string; functional_assay_sha256?: string }>
  excluded_activity?: Array<{ reason: string; bounded_activity?: { target_accession: string; kind: string; relation: string;
    activity_bound_umol_l: number; functional_direction: string; concentration_basis: string; uncertainty: string;
    limitation: string; source_sha256: string } }>
  exposure_activity: VirtualInvestigation['candidates'][number]['exposure_activity']
  functional_models: VirtualInvestigation['candidates'][number]['functional_models']
  functional_transfer_refusals: Array<{ reason: string }>
  verification: { passed: boolean; scope: string }
  scope: string; native_exposure_sha256: string; artifact_sha256: string
}

export interface NativeADMETranslationAudit {
  species: string; scope: string
  ionization: { status: string; source: string; reason: string }
  solubility: { status: string; reference_ph: number | null; method: string; reason: string }
  blood_plasma: { status: string; method: string; required_inputs: string[]; reason: string }
  hepatic: { status: string; equations: string[]; required_inputs: string[]; reason: string; value: number | null; unit: string }
  renal: { status: string; component_equation: string; unresolved_mechanisms: string[]; reason: string; total_clearance_established: boolean }
  reference_weight: { status: string; method: string; reason: string; value_kg: number | null }
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
