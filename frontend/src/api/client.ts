import type {
  HealthResponse,
  ApprovedExperimentResponse,
  ExperimentPlanResponse,
  InterpretationResponse,
  InterpretedScientificSpecification,
  PresetDetailResponse,
  PresetListResponse,
  PresetSummaryResponse,
  SceneResponse,
  RunCapabilityResponse,
  RunReceiptResponse,
  RunResultsResponse,
  RunStateResponse,
  RunStatus,
  RunVerificationResponse,
  FetchedMolecularResource,
  ProjectedMolecularSceneMetadata,
  ProjectedMolecularStructureMetadata,
  ResearchArtifactReference,
  ResearchInputArtifactType,
  ResearchInputReference,
  ResearchInputUpload,
  ResearchSessionResponse,
  ResearchVisualizationWorkspace,
  UploadedResearchInput,
} from './types'
import {
  MOLECULAR_RESOURCE_MAXIMUM_BYTES,
  parseProjectedMolecularScene,
} from '../scene/native-project'
import {
  parseWorkflowEvidence,
  parseWorkflowGraph,
  parseWorkflowNodes,
  parseWorkflowRun,
  type WorkflowEvidenceResponse,
  type WorkflowGraphDefinitionResponse,
  type WorkflowNodesResponse,
  type WorkflowRunSnapshotResponse,
} from './workflows'
import {
  parseMolecularPlanningResponse,
  type CandidateResearchPlanResponse,
  type MolecularPlanningRequest,
} from './planning'

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status?: number,
    readonly cause?: unknown,
    readonly code?: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function hasString(value: Record<string, unknown>, key: string): boolean {
  return typeof value[key] === 'string' && value[key].trim().length > 0
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

function isOptionalFiniteNumber(value: unknown): boolean {
  return value === undefined || value === null || isFiniteNumber(value)
}

function isOptionalString(value: unknown): boolean {
  return value === undefined || (typeof value === 'string' && value.trim().length > 0)
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === 'string' && item.trim().length > 0)
}

function malformed(message: string): never {
  throw new ApiError(message)
}

function parseHealth(value: unknown): HealthResponse {
  if (!isRecord(value) || !hasString(value, 'service') || !hasString(value, 'status') || !hasString(value, 'version')) {
    throw new ApiError('The backend returned a malformed health response.')
  }
  return value as unknown as HealthResponse
}

const runStatuses = new Set<RunStatus>([
  'queued', 'validating', 'running_quantum_workflow', 'running_local_preflight',
  'awaiting_ibm_submission', 'queued_on_ibm', 'running_on_ibm', 'verifying_ibm_result',
  'authorized', 'rejected', 'failed', 'interrupted',
])

function containsCredentialField(value: unknown): boolean {
  if (Array.isArray(value)) return value.some(containsCredentialField)
  if (!isRecord(value)) return false
  return Object.entries(value).some(([key, item]) =>
    /token|credential|password|api[_-]?key/i.test(key) || containsCredentialField(item))
}

function isOptionalIBMExecution(value: unknown): boolean {
  return value === undefined || value === null || (isRecord(value)
    && hasString(value, 'hardware_role') && hasString(value, 'submission_status')
    && (value.job_identifier === null || hasString(value, 'job_identifier'))
    && (value.backend_name === null || hasString(value, 'backend_name'))
    && typeof value.execution_integrity_passed === 'boolean'
    && typeof value.scientific_quality_passed === 'boolean'
    && !containsCredentialField(value))
}

function parseCapability(value: unknown): RunCapabilityResponse {
  if (!isRecord(value) || typeof value.available !== 'boolean' || !isStringArray(value.execution_targets)
    || !(value.reason === null || typeof value.reason === 'string')
    || !(value.maximum_run_seconds === null || (isFiniteNumber(value.maximum_run_seconds) && value.maximum_run_seconds > 0))) {
    malformed('The backend returned a malformed execution capability response.')
  }
  if (containsCredentialField(value)) malformed('The backend returned unsafe execution capability data.')
  return value as unknown as RunCapabilityResponse
}

function hasRunIdentity(value: Record<string, unknown>): boolean {
  const sourceType = value.source_type
  const sourceIdentifier = value.source_identifier
  const presetIdentifier = value.preset_identifier
  const experimentIdentifier = value.experiment_identifier
  const sourceIsValid = sourceType === 'preset'
    ? typeof presetIdentifier === 'string' && presetIdentifier.trim().length > 0 && sourceIdentifier === presetIdentifier
    : (sourceType === 'dynamic_experiment' || sourceType === 'approved_experiment')
      && presetIdentifier === null && sourceIdentifier === experimentIdentifier
  return hasString(value, 'run_identifier') && hasString(value, 'source_identifier') && sourceIsValid
    && hasString(value, 'experiment_identifier') && hasString(value, 'experiment_fingerprint')
    && hasString(value, 'expected_experiment_sha256') && hasString(value, 'structure_identifier')
}

function parseRunState(value: unknown): RunStateResponse {
  if (!isRecord(value) || !hasRunIdentity(value) || !runStatuses.has(value.status as RunStatus)
    || !['local_simulator', 'ibm_quantum'].includes(String(value.execution_target)) || !hasString(value, 'created_at')
    || !hasString(value, 'updated_at') || !hasString(value, 'status_url')) {
    malformed('The backend returned a malformed run status response.')
  }
  if (value.error !== undefined && (!isRecord(value.error) || !hasString(value.error, 'code') || !hasString(value.error, 'message'))) {
    malformed('The backend returned a malformed run error.')
  }
  return value as unknown as RunStateResponse
}

function parseRunResults(value: unknown): RunResultsResponse {
  if (!isRecord(value) || !hasRunIdentity(value) || !hasString(value, 'structure_sha256')
    || !hasString(value, 'hamiltonian_sha256') || !hasString(value, 'energy_unit')
    || !hasString(value, 'exact_scientific_result_sha256') || !hasString(value, 'vqe_scientific_result_sha256')
    || !hasString(value, 'scientific_outcome_sha256')
    || !isFiniteNumber(value.exact_total_energy_hartree) || !isFiniteNumber(value.vqe_total_energy_hartree)
    || !isFiniteNumber(value.absolute_difference_hartree) || !isFiniteNumber(value.tolerance_hartree)
    || !isRecord(value.exact_solver_metadata) || !isRecord(value.vqe_solver_metadata)
    || !(value.optimizer_evaluations === null || isFiniteNumber(value.optimizer_evaluations))
    || !(value.converged === null || typeof value.converged === 'boolean')
    || !Array.isArray(value.compatibility_warnings)
    || !hasString(value, 'execution_environment_identity') || !hasString(value, 'receipt_sha256')
    || !isOptionalIBMExecution(value.ibm_execution)) {
    malformed('The backend returned malformed scientific run results.')
  }
  return value as unknown as RunResultsResponse
}

function parseVerification(value: unknown): RunVerificationResponse {
  if (!isRecord(value) || !hasRunIdentity(value) || !hasString(value, 'structure_sha256')
    || typeof value.verification_completed !== 'boolean' || typeof value.verification_passed !== 'boolean'
    || !['authorized', 'rejected'].includes(String(value.authorization_state))
    || !Array.isArray(value.blocking_findings) || !Array.isArray(value.nonblocking_findings)
    || !(value.tolerance_check === null || isRecord(value.tolerance_check))
    || !Array.isArray(value.scientific_identity_checks) || !Array.isArray(value.artifact_integrity_checks)
    || !Array.isArray(value.checks) || !Array.isArray(value.compatibility_warnings)
    || !isOptionalIBMExecution(value.ibm_execution)) {
    malformed('The backend returned malformed scientific verification evidence.')
  }
  return value as unknown as RunVerificationResponse
}

function parseReceipt(value: unknown): RunReceiptResponse {
  if (!isRecord(value) || !hasRunIdentity(value) || !hasString(value, 'schema_version')
    || !hasString(value, 'execution_identifier') || !hasString(value, 'structure_sha256')
    || !hasString(value, 'hamiltonian_sha256') || !hasString(value, 'exact_scientific_result_sha256')
    || !hasString(value, 'vqe_scientific_result_sha256') || !hasString(value, 'scientific_outcome_sha256')
    || !hasString(value, 'execution_environment_identity') || !hasString(value, 'receipt_sha256')
    || typeof value.verification_passed !== 'boolean'
    || !['authorized', 'rejected'].includes(String(value.authorization_state))
    || typeof value.authorized !== 'boolean' || !Array.isArray(value.artifacts)
    || !value.artifacts.every((item) => isRecord(item) && hasString(item, 'artifact_identifier')
      && hasString(item, 'artifact_type') && hasString(item, 'content_sha256'))
    || !isOptionalIBMExecution(value.ibm_execution)) {
    malformed('The backend returned a malformed authorization receipt.')
  }
  return value as unknown as RunReceiptResponse
}

function isPresetSummary(value: unknown): value is PresetSummaryResponse {
  return isRecord(value)
    && hasString(value, 'preset_identifier')
    && hasString(value, 'experiment_identifier')
    && isStringArray(value.elements)
    && isFiniteNumber(value.atom_count)
    && Number.isInteger(value.atom_count)
    && value.atom_count >= 0
    && hasString(value, 'coordinate_unit')
    && isOptionalFiniteNumber(value.declared_bond_distance)
    && isFiniteNumber(value.molecular_charge)
    && isFiniteNumber(value.spin_multiplicity)
    && hasString(value, 'basis_set')
    && isOptionalString(value.experiment_fingerprint)
}

function parsePresetList(value: unknown): PresetListResponse {
  if (!isRecord(value) || !Array.isArray(value.presets) || !value.presets.every(isPresetSummary) || !isFiniteNumber(value.count) || !Number.isInteger(value.count) || value.count < 0) {
    throw new ApiError('The backend returned a malformed experiment preset list.')
  }
  return value as unknown as PresetListResponse
}

function parsePresetDetail(value: unknown): PresetDetailResponse {
  if (!isRecord(value) || !hasString(value, 'preset_identifier') || !isRecord(value.manifest) || !isRecord(value.manifest.experiment)) {
    throw new ApiError('The backend returned malformed experiment details.')
  }
  const manifest = value.manifest
  const experimentValue = manifest.experiment
  if (!isRecord(experimentValue)) malformed('The backend returned malformed experiment details.')
  const experiment = experimentValue
  if (!(manifest.expected_experiment_sha256 === undefined || manifest.expected_experiment_sha256 === null || (typeof manifest.expected_experiment_sha256 === 'string' && manifest.expected_experiment_sha256.trim().length > 0))) {
    malformed('The backend returned a malformed expected experiment hash.')
  }
  if (!isOptionalString(experiment.experiment_identifier)) malformed('The backend returned a malformed experiment identifier.')
  if (experiment.molecular_system !== undefined) {
    if (!isRecord(experiment.molecular_system)) malformed('The backend returned malformed molecular-system details.')
    const molecular = experiment.molecular_system
    if (!isOptionalFiniteNumber(molecular.molecular_charge)
      || !isOptionalFiniteNumber(molecular.spin_multiplicity)
      || !isOptionalString(molecular.coordinate_unit)
      || !isOptionalString(molecular.structure_artifact_identifier)) {
      malformed('The backend returned malformed molecular-system details.')
    }
  }
  if (experiment.electronic_structure !== undefined) {
    if (!isRecord(experiment.electronic_structure)) malformed('The backend returned malformed electronic-structure details.')
    const electronic = experiment.electronic_structure
    const orbitalIndices = electronic.active_orbital_indices
    if (!isOptionalString(electronic.basis_set)
      || !isOptionalString(electronic.reference_method)
      || !isOptionalFiniteNumber(electronic.active_electron_count)
      || !isOptionalFiniteNumber(electronic.active_spatial_orbital_count)
      || !(orbitalIndices === undefined || (Array.isArray(orbitalIndices) && orbitalIndices.every((index) => isFiniteNumber(index) && Number.isInteger(index))))) {
      malformed('The backend returned malformed electronic-structure details.')
    }
  }
  if (experiment.quantum_model !== undefined) {
    if (!isRecord(experiment.quantum_model)
      || !isOptionalString(experiment.quantum_model.mapper)
      || !isOptionalString(experiment.quantum_model.ansatz)) {
      malformed('The backend returned malformed quantum-model details.')
    }
  }
  if (experiment.execution_policy !== undefined) {
    if (!isRecord(experiment.execution_policy)
      || !isOptionalString(experiment.execution_policy.runtime_identifier)
      || !(experiment.execution_policy.network_disabled === undefined || typeof experiment.execution_policy.network_disabled === 'boolean')
      || !isOptionalFiniteNumber(experiment.execution_policy.maximum_duration_seconds)) {
      malformed('The backend returned malformed execution-policy details.')
    }
  }
  return value as unknown as PresetDetailResponse
}

function isRawAtom(value: unknown): boolean {
  return isRecord(value)
    && hasString(value, 'atom_identifier')
    && hasString(value, 'element')
    && Array.isArray(value.coordinates)
    && value.coordinates.length === 3
    && value.coordinates.every((coordinate) => typeof coordinate === 'number' && Number.isFinite(coordinate))
}

function parseScene(value: unknown): SceneResponse {
  if (!isRecord(value) || !hasString(value, 'scene_identifier') || !hasString(value, 'coordinate_unit') || !Array.isArray(value.atoms) || !value.atoms.every(isRawAtom)) {
    throw new ApiError('The backend returned malformed molecular scene data.')
  }
  if (!isOptionalString(value.scene_stage)
    || !isOptionalString(value.experiment_identifier)
    || !isOptionalString(value.experiment_fingerprint)
    || !isOptionalString(value.structure_hash)
    || !isOptionalString(value.structure_identifier)
    || !isOptionalString(value.expected_experiment_sha256)
    || !isOptionalString(value.specification_sha256)) {
    malformed('The backend returned malformed molecular scene identity data.')
  }
  const atomIdentifiers = new Set<string>()
  for (const atom of value.atoms as Array<Record<string, unknown>>) {
    const identifier = atom.atom_identifier as string
    if (atomIdentifiers.has(identifier)) malformed('The backend returned duplicate atom identifiers.')
    atomIdentifiers.add(identifier)
  }
  if (value.bonds !== undefined) {
    if (!Array.isArray(value.bonds)) malformed('The backend returned malformed molecular bond data.')
    const bondIdentifiers = new Set<string>()
    for (const bond of value.bonds) {
      if (!isRecord(bond)
        || !hasString(bond, 'bond_identifier')
        || !isStringArray(bond.atom_identifiers)
        || bond.atom_identifiers.length !== 2
        || bond.atom_identifiers[0] === bond.atom_identifiers[1]
        || !bond.atom_identifiers.every((identifier) => atomIdentifiers.has(identifier))
        || !isOptionalFiniteNumber(bond.order)
        || !isOptionalFiniteNumber(bond.declared_distance)
        || !isOptionalFiniteNumber(bond.derived_distance)) {
        malformed('The backend returned malformed molecular bond data.')
      }
      if (bondIdentifiers.has(bond.bond_identifier as string)) malformed('The backend returned duplicate bond identifiers.')
      bondIdentifiers.add(bond.bond_identifier as string)
    }
  }
  if (value.quantum_region !== undefined && value.quantum_region !== null) {
    if (!isRecord(value.quantum_region)
      || !hasString(value.quantum_region, 'selection_identifier')
      || !isStringArray(value.quantum_region.atom_identifiers)
      || !value.quantum_region.atom_identifiers.every((identifier) => atomIdentifiers.has(identifier))) {
      malformed('The backend returned malformed quantum-region data.')
    }
  }
  if (value.scientific_model !== undefined) {
    if (!isRecord(value.scientific_model)) malformed('The backend returned malformed scientific-model data.')
    const model = value.scientific_model
    const orbitalIndices = model.active_orbital_indices
    if (!isOptionalFiniteNumber(model.charge)
      || !isOptionalFiniteNumber(model.spin_multiplicity)
      || !isOptionalString(model.basis_set)
      || !isOptionalString(model.reference_method)
      || !isOptionalFiniteNumber(model.active_electron_count)
      || !isOptionalFiniteNumber(model.active_spatial_orbital_count)
      || !(orbitalIndices === undefined || (Array.isArray(orbitalIndices) && orbitalIndices.every((index) => isFiniteNumber(index) && Number.isInteger(index))))
      || !isOptionalString(model.mapper)
      || !isOptionalString(model.ansatz)) {
      malformed('The backend returned malformed scientific-model data.')
    }
  }
  if (value.provenance !== undefined && !isRecord(value.provenance)) {
    malformed('The backend returned malformed scene provenance data.')
  }
  if (value.artifact_references !== undefined && !isStringArray(value.artifact_references)) {
    malformed('The backend returned malformed artifact references.')
  }
  return value as unknown as SceneResponse
}

const researchStatuses = new Set([
  'understanding', 'awaiting_clarification', 'awaiting_approval', 'planned',
  'running', 'verifying', 'replanning', 'completed', 'failed',
])

function isResearchArtifactReference(value: unknown): value is ResearchArtifactReference {
  return isRecord(value)
    && hasString(value, 'artifact_identifier')
    && hasString(value, 'artifact_type')
    && hasString(value, 'media_type')
    && hasString(value, 'content_sha256')
    && (value.byte_size === null || (isFiniteNumber(value.byte_size) && Number.isInteger(value.byte_size) && value.byte_size >= 0))
    && isRecord(value.schema_version)
    && isRecord(value.provenance)
    && isRecord(value.metadata)
    && Array.isArray(value.parents)
}

function parseResearchArtifact(value: unknown): ResearchArtifactReference {
  if (!isResearchArtifactReference(value) || containsCredentialField(value)) {
    malformed('The backend returned malformed or unsafe research artifact evidence.')
  }
  return value
}

function isResearchInputReference(value: unknown): value is ResearchInputReference {
  return isRecord(value)
    && hasString(value, 'reference_identifier')
    && hasString(value, 'artifact_type')
    && hasString(value, 'artifact_identifier')
}

function isResearchEvidenceProposal(value: unknown): boolean {
  return isRecord(value)
    && hasString(value, 'proposal_identifier')
    && hasString(value, 'source_kind')
    && hasString(value, 'summary')
    && Array.isArray(value.input_references)
    && value.input_references.every(isResearchInputReference)
    && Array.isArray(value.artifact_references)
    && value.artifact_references.every(isResearchArtifactReference)
    && Array.isArray(value.supporting_quotes)
    && value.supporting_quotes.every((quote) => isRecord(quote)
      && hasString(quote, 'field_name')
      && hasString(quote, 'turn_identifier')
      && hasString(quote, 'supporting_quote'))
    && (value.provider_kind === null || typeof value.provider_kind === 'string')
    && (value.model_name === null || typeof value.model_name === 'string')
    && (value.reason === null || typeof value.reason === 'string')
    && (value.covalent_reaction_target === null || isRecord(value.covalent_reaction_target))
    && (value.partial_covalent_reaction_target === null || isRecord(value.partial_covalent_reaction_target))
    && Array.isArray(value.entity_candidates)
    && value.entity_candidates.every((candidate) => isRecord(candidate)
      && ['protein', 'ligand'].includes(String(candidate.entity_type))
      && ['uniprot', 'pubchem'].includes(String(candidate.source_kind))
      && hasString(candidate, 'source_identifier')
      && hasString(candidate, 'display_label')
      && (candidate.structure_identifier === null || hasString(candidate, 'structure_identifier'))
      && ['high', 'ambiguous'].includes(String(candidate.confidence)))
}

function parseResearchSession(value: unknown): ResearchSessionResponse {
  if (!isRecord(value)
    || !hasString(value, 'session_identifier')
    || !hasString(value, 'created_at')
    || !hasString(value, 'updated_at')
    || !isFiniteNumber(value.revision)
    || !Number.isInteger(value.revision)
    || value.revision < 1
    || !researchStatuses.has(String(value.status))
    || !hasString(value, 'scientist_summary')
    || !Array.isArray(value.conversation)
    || !value.conversation.every((turn) => isRecord(turn)
      && hasString(turn, 'turn_identifier')
      && ['scientist', 'pulsate'].includes(String(turn.role))
      && hasString(turn, 'content')
      && hasString(turn, 'created_at'))
    || !Array.isArray(value.input_references)
    || !value.input_references.every(isResearchInputReference)
    || !Array.isArray(value.artifact_references)
    || !value.artifact_references.every(isResearchArtifactReference)
    || !Array.isArray(value.unapproved_input_artifact_identifiers)
    || !value.unapproved_input_artifact_identifiers.every((item) => typeof item === 'string')
    || !Array.isArray(value.next_questions)
    || !value.next_questions.every((prompt) => isRecord(prompt)
      && hasString(prompt, 'requirement_identifier')
      && hasString(prompt, 'question'))
    || !isRecord(value.clarification_attempts)
    || !(value.intent_proposal === null || (isRecord(value.intent_proposal)
      && hasString(value.intent_proposal, 'task_type')
      && hasString(value.intent_proposal, 'supporting_quote')
      && hasString(value.intent_proposal, 'provider_kind')
      && hasString(value.intent_proposal, 'model_name')))
    || !(value.requirement_proposal === null || (isRecord(value.requirement_proposal)
      && hasString(value.requirement_proposal, 'proposal_identifier')
      && hasString(value.requirement_proposal, 'summary')
      && hasString(value.requirement_proposal, 'provider_kind')
      && hasString(value.requirement_proposal, 'model_name')
      && Array.isArray(value.requirement_proposal.requirements)
      && value.requirement_proposal.requirements.every((requirement) => isRecord(requirement)
        && hasString(requirement, 'operation')
        && hasString(requirement, 'requested_output')
        && hasString(requirement, 'supporting_quote'))))
    || !(value.accepted_research_requirements === null
      || (isRecord(value.accepted_research_requirements)
        && isStringArray(value.accepted_research_requirements.operations)
        && isStringArray(value.accepted_research_requirements.requested_outputs)
        && isStringArray(value.accepted_research_requirements.required_artifact_types)
        && hasString(value.accepted_research_requirements, 'capability_profile')
        && hasString(value.accepted_research_requirements, 'source_proposal_identifier')))
    || !(value.evidence_proposal === null || isResearchEvidenceProposal(value.evidence_proposal))
    || !(value.accepted_partial_covalent_reaction_target === null
      || isRecord(value.accepted_partial_covalent_reaction_target))
    || !Array.isArray(value.accepted_evidence)
    || !value.accepted_evidence.every(isResearchEvidenceProposal)
    || !(value.compilation === null || (isRecord(value.compilation)
      && hasString(value.compilation, 'compilation_identifier')
      && hasString(value.compilation, 'execution_identifier')
      && hasString(value.compilation, 'effective_question')
      && isRecord(value.compilation.canonical_objective)
      && isRecord(value.compilation.canonical_plan)
      && isRecord(value.compilation.canonical_graph)))
    || !(value.execution_status === null || typeof value.execution_status === 'string')
    || !(value.scene_identifier === null || typeof value.scene_identifier === 'string')
    || !(value.scientist_result === null || (isRecord(value.scientist_result)
      && hasString(value.scientist_result, 'original_request')
      && hasString(value.scientist_result, 'resolved_interpretation')
      && hasString(value.scientist_result, 'scientific_result')
      && ['passed', 'failed', 'inconclusive'].includes(String(value.scientist_result.verification_status))
      && isStringArray(value.scientist_result.structures_and_entities)
      && isStringArray(value.scientist_result.methods)
      && isStringArray(value.scientist_result.assumptions)
      && isStringArray(value.scientist_result.uncertainty)
      && isStringArray(value.scientist_result.replanning_history)
      && isStringArray(value.scientist_result.important_limitations)
      && isStringArray(value.scientist_result.evidence_artifact_identifiers)
      && isStringArray(value.scientist_result.scene_identifiers)
      && (value.scientist_result.principal_result === null || hasString(value.scientist_result, 'principal_result'))
      && isStringArray(value.scientist_result.candidate_ranking)
      && isStringArray(value.scientist_result.confidence_and_uncertainty)
      && (value.scientist_result.recommended_next_step === null || hasString(value.scientist_result, 'recommended_next_step'))
      && (value.scientist_result.synthesis_provider_kind === null || hasString(value.scientist_result, 'synthesis_provider_kind'))
      && (value.scientist_result.synthesis_model_name === null || hasString(value.scientist_result, 'synthesis_model_name'))))) {
    malformed('The backend returned a malformed research session.')
  }
  if (containsCredentialField(value)) malformed('The backend returned unsafe research-session data.')
  return value as unknown as ResearchSessionResponse
}

function parseResearchVisualization(value: unknown): ResearchVisualizationWorkspace {
  if (!isRecord(value)
    || value.schema_version !== 'pulsate.research-visualization/v1'
    || !hasString(value, 'session_identifier')
    || !Number.isInteger(value.revision)
    || !(value.scene_identifier === null || typeof value.scene_identifier === 'string')
    || value.grounding_policy !== 'persisted_artifact_or_deterministic_computation_only'
    || !Array.isArray(value.structures)
    || !value.structures.every((item) => isRecord(item)
      && hasString(item, 'artifact_identifier')
      && hasString(item, 'artifact_type')
      && hasString(item, 'media_type')
      && hasString(item, 'label')
      && ['protein', 'ligand_or_candidate', 'molecular_structure'].includes(String(item.role))
      && (item.candidate_identifier === null || hasString(item, 'candidate_identifier'))
      && (item.generation === null || Number.isInteger(item.generation))
      && typeof item.selected === 'boolean'
      && Number.isInteger(item.conformation_count)
      && Number(item.conformation_count) >= 1
      && (item.source_kind === null || hasString(item, 'source_kind'))
      && (item.source_identifier === null || hasString(item, 'source_identifier'))
      && (item.confidence === null || hasString(item, 'confidence'))
      && hasString(item, 'content_sha256'))
    || !Array.isArray(value.selections)
    || !value.selections.every((item) => isRecord(item)
      && hasString(item, 'selection_identifier')
      && hasString(item, 'label')
      && hasString(item, 'kind')
      && (item.structure_artifact_identifier === null || hasString(item, 'structure_artifact_identifier'))
      && isStringArray(item.atom_identifiers)
      && isStringArray(item.residue_identifiers)
      && hasString(item, 'evidence_artifact_identifier'))
    || !Array.isArray(value.interactions)
    || !value.interactions.every((item) => isRecord(item)
      && hasString(item, 'interaction_identifier')
      && hasString(item, 'interaction_type')
      && (item.candidate_identifier === null || hasString(item, 'candidate_identifier'))
      && isStringArray(item.structure_artifact_identifiers)
      && isStringArray(item.atom_identifiers)
      && isStringArray(item.residue_identifiers)
      && (item.distance_angstrom === null || isFiniteNumber(item.distance_angstrom))
      && (item.angle_degree === null || isFiniteNumber(item.angle_degree))
      && hasString(item, 'calculation_method')
      && hasString(item, 'evidence_artifact_identifier'))
    || !Array.isArray(value.overlays)
    || !value.overlays.every((item) => isRecord(item)
      && hasString(item, 'overlay_identifier')
      && hasString(item, 'kind')
      && hasString(item, 'label')
      && (item.structure_artifact_identifier === null || hasString(item, 'structure_artifact_identifier'))
      && (item.candidate_identifier === null || hasString(item, 'candidate_identifier'))
      && (item.unit === null || hasString(item, 'unit'))
      && isStringArray(item.atom_identifiers)
      && isStringArray(item.residue_identifiers)
      && hasString(item, 'verification_status')
      && (item.uncertainty === null || hasString(item, 'uncertainty'))
      && hasString(item, 'evidence_artifact_identifier')
      && (item.method === null || hasString(item, 'method')))
    || !Array.isArray(value.candidates)
    || !value.candidates.every((item) => isRecord(item)
      && hasString(item, 'candidate_identifier')
      && Number.isInteger(item.generation)
      && isStringArray(item.parent_candidate_identifiers)
      && (item.transformation === null || isRecord(item.transformation))
      && Array.isArray(item.properties_and_calculations)
      && item.properties_and_calculations.every(isRecord)
      && Array.isArray(item.verification)
      && item.verification.every(isRecord)
      && (item.selection_rationale === null || hasString(item, 'selection_rationale'))
      && typeof item.selected === 'boolean'
      && isStringArray(item.structure_artifact_identifiers))
    || !Array.isArray(value.lineage) || !value.lineage.every(isRecord)
    || !Array.isArray(value.comparisons)
    || !value.comparisons.every((item) => isRecord(item)
      && hasString(item, 'comparison_identifier')
      && hasString(item, 'kind')
      && hasString(item, 'left_identifier')
      && hasString(item, 'right_identifier')
      && (item.evidence_artifact_identifier === null || hasString(item, 'evidence_artifact_identifier')))
    || !isStringArray(value.verification_artifact_identifiers)
    || !Array.isArray(value.export_items)
    || !value.export_items.every((item) => isRecord(item)
      && hasString(item, 'artifact_identifier')
      && hasString(item, 'artifact_type')
      && hasString(item, 'media_type')
      && hasString(item, 'content_sha256')
      && ['structure', 'evidence', 'report'].includes(String(item.category)))) {
    malformed('The backend returned a malformed research visualization workspace.')
  }
  if (containsCredentialField(value)) malformed('The backend returned unsafe visualization data.')
  return value as unknown as ResearchVisualizationWorkspace
}

function parseExperimentPlan(value: unknown): ExperimentPlanResponse {
  if (!isRecord(value) || !hasString(value, 'schema_version')
    || !hasString(value, 'experiment_identifier') || !hasString(value, 'original_question')
    || typeof value.ready_for_execution !== 'boolean'
    || !['local_simulator', 'ibm_quantum'].includes(String(value.requested_execution_target))
    || !Array.isArray(value.assumptions) || !value.assumptions.every((item) => typeof item === 'string')
    || !Array.isArray(value.warnings) || !value.warnings.every((item) => typeof item === 'string')
    || !Array.isArray(value.missing_fields) || !value.missing_fields.every((item) => typeof item === 'string')
    || !(value.specification === null || isRecord(value.specification))
    || !(value.specification_sha256 === null || hasString(value, 'specification_sha256'))
    || !(value.experiment_fingerprint === null || hasString(value, 'experiment_fingerprint'))
    || !(value.expected_experiment_sha256 === null || hasString(value, 'expected_experiment_sha256'))
    || !(value.structure_identifier === null || hasString(value, 'structure_identifier'))
    || !(value.structure_hash === null || hasString(value, 'structure_hash'))
    || !(value.molecule === null || isRecord(value.molecule)) || !hasString(value, 'created_at')) {
    malformed('The backend returned a malformed experiment plan.')
  }
  if (value.molecule !== null) parseScene(value.molecule)
  if (value.ready_for_execution && (value.specification === null || value.molecule === null)) {
    malformed('The backend returned an executable plan without a scientific specification and molecule.')
  }
  return value as unknown as ExperimentPlanResponse
}

function parseInterpretation(value: unknown): InterpretationResponse {
  if (!isRecord(value) || !hasString(value, 'schema_version')
    || !hasString(value, 'interpretation_identifier') || !hasString(value, 'original_question')
    || !isRecord(value.specification) || !isStringArray(value.assumptions)
    || !Array.isArray(value.missing_required_information)
    || !Array.isArray(value.warnings) || !hasString(value, 'interpretation_status')
    || !hasString(value, 'execution_support_status') || !isRecord(value.model_provenance)
    || typeof value.scientist_approval_possible !== 'boolean' || !hasString(value, 'created_at')
    || containsCredentialField(value)) {
    malformed('The backend returned a malformed or unsafe interpretation.')
  }
  return value as unknown as InterpretationResponse
}

function parseApprovedExperiment(value: unknown): ApprovedExperimentResponse {
  if (!isRecord(value) || !hasString(value, 'schema_version')
    || !hasString(value, 'experiment_identifier') || !hasString(value, 'interpretation_identifier')
    || !hasString(value, 'original_question') || !isRecord(value.specification)
    || !hasString(value, 'specification_sha256')
    || !['ibm_quantum', 'local_simulator'].includes(String(value.requested_execution_target))
    || !['ready_for_ibm_submission', 'approved_pending_compiler_support'].includes(String(value.status))
    || value.assumptions_accepted !== true
    || !isStringArray(value.scientist_reviewed_overrides)
    || !hasString(value, 'approved_at')
    || containsCredentialField(value)) {
    malformed('The backend returned a malformed or unsafe approved experiment.')
  }
  return value as unknown as ApprovedExperimentResponse
}

export type AccessTokenProvider = () => Promise<string | null>

const PUBLIC_API_PATHS = new Set(['/api/v1/health', '/live', '/ready'])

async function authenticatedHeaders(
  path: string,
  supplied: HeadersInit | undefined,
  accessTokenProvider: AccessTokenProvider,
): Promise<Headers> {
  const headers = new Headers(supplied)
  if (path.startsWith('/api/v1/') && !PUBLIC_API_PATHS.has(path.split('?', 1)[0])) {
    const token = await accessTokenProvider()
    if (token !== null) {
      if (token.length === 0 || token.length > 8192 || /\s/.test(token)) {
        throw new ApiError('Authentication is required to access Pulsate.', 401, undefined, 'invalid_access_token')
      }
      headers.set('Authorization', `Bearer ${token}`)
    }
  }
  return headers
}

function controlledHttpError(status: number, message: string, code?: string): ApiError {
  if (status === 401) return new ApiError('Authentication is required to access Pulsate.', status, undefined, code)
  if (status === 403) return new ApiError('Access to the requested Pulsate resource is denied.', status, undefined, code)
  if (status === 404) return new ApiError('The requested Pulsate resource was not found.', status, undefined, code)
  if (status === 503) return new ApiError('The Pulsate service is temporarily unavailable.', status, undefined, code)
  return new ApiError(message, status, undefined, code)
}

async function requestJson<T>(path: string, parser: (value: unknown) => T, accessTokenProvider: AccessTokenProvider, signal?: AbortSignal, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    const headers = await authenticatedHeaders(path, { Accept: 'application/json', ...init?.headers }, accessTokenProvider)
    response = await fetch(path, { ...init, signal, headers })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    if (error instanceof ApiError) throw error
    throw new ApiError('Unable to reach the Pulsate API.', undefined, error)
  }

  if (!response.ok) {
    let message = `Pulsate API request failed (${response.status}).`
    let code: string | undefined
    try {
      const body = await response.json()
      if (isRecord(body) && typeof body.detail === 'string') message = body.detail
      else if (isRecord(body) && isRecord(body.detail)) {
        if (typeof body.detail.message === 'string') message = body.detail.message
        if (typeof body.detail.code === 'string') code = body.detail.code
      }
    } catch { /* Preserve the status-based message for non-JSON errors. */ }
    throw controlledHttpError(response.status, message, code)
  }

  try {
    return parser(await response.json())
  } catch (error) {
    if (error instanceof ApiError) throw error
    throw new ApiError('The Pulsate API returned invalid JSON.', response.status, error)
  }
}

async function sha256Hex(bytes: Uint8Array): Promise<string> {
  const digest = await globalThis.crypto.subtle.digest('SHA-256', bytes)
  return [...new Uint8Array(digest)].map((value) => value.toString(16).padStart(2, '0')).join('')
}

const RESEARCH_INPUT_MAXIMUM_BYTES = 16 * 1024 * 1024

function researchInputMediaType(fileName: string): string {
  const extension = fileName.slice(fileName.lastIndexOf('.')).toLowerCase()
  return {
    '.pdb': 'chemical/x-pdb',
    '.sdf': 'chemical/x-mdl-sdfile',
    '.mol': 'chemical/x-mdl-molfile',
    '.mol2': 'chemical/x-mol2',
    '.pdbqt': 'chemical/x-pdbqt',
    '.smi': 'chemical/x-daylight-smiles',
    '.smiles': 'chemical/x-daylight-smiles',
  }[extension] ?? 'application/octet-stream'
}

function bytesToBase64(bytes: Uint8Array): string {
  let binary = ''
  for (let offset = 0; offset < bytes.length; offset += 32_768) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 32_768))
  }
  return btoa(binary)
}

async function readBoundedMolecularResource(
  response: Response,
  maximumBytes: number,
): Promise<Uint8Array> {
  if (!response.body) {
    throw new ApiError('The molecular resource response body is unavailable.', response.status)
  }
  const reader = response.body.getReader()
  const chunks: Uint8Array[] = []
  let receivedBytes = 0
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      receivedBytes += value.byteLength
      if (receivedBytes > maximumBytes) {
        await reader.cancel()
        throw new ApiError('The molecular resource exceeded its declared byte size.', response.status)
      }
      chunks.push(value)
    }
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    if (error instanceof ApiError) throw error
    throw new ApiError('The molecular resource could not be read.', response.status, error)
  } finally {
    reader.releaseLock()
  }
  const bytes = new Uint8Array(receivedBytes)
  let offset = 0
  for (const chunk of chunks) {
    bytes.set(chunk, offset)
    offset += chunk.byteLength
  }
  return bytes
}

async function requestMolecularResource(
  path: string,
  artifact: ProjectedMolecularStructureMetadata['source_artifact'],
  accessTokenProvider: AccessTokenProvider,
  signal?: AbortSignal,
): Promise<FetchedMolecularResource> {
  if (artifact.byte_size > MOLECULAR_RESOURCE_MAXIMUM_BYTES) {
    throw new ApiError('The molecular resource exceeds the browser rendering limit.')
  }
  let response: Response
  try {
    const headers = await authenticatedHeaders(path, { Accept: artifact.media_type }, accessTokenProvider)
    response = await fetch(path, { method: 'GET', signal, headers })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    if (error instanceof ApiError) throw error
    throw new ApiError('Unable to reach the Pulsate API.', undefined, error)
  }
  if (!response.ok) throw controlledHttpError(response.status, `Molecular resource request failed (${response.status}).`)
  const declaredLength = response.headers.get('Content-Length')
  if (declaredLength === null || !/^\d+$/.test(declaredLength) || Number(declaredLength) !== artifact.byte_size) {
    throw new ApiError('The molecular resource byte-size evidence is inconsistent.')
  }
  const contentType = response.headers.get('Content-Type')?.split(';', 1)[0].trim().toLowerCase()
  if (contentType !== artifact.media_type.toLowerCase()) {
    throw new ApiError('The molecular resource media type is inconsistent.')
  }
  const contentHash = response.headers.get('X-Content-SHA256')
  if (contentHash !== artifact.content_sha256) {
    throw new ApiError('The molecular resource identity header is inconsistent.')
  }
  const etag = response.headers.get('ETag')
  if (etag !== null && etag !== `"${artifact.content_sha256}"`) {
    throw new ApiError('The molecular resource ETag is inconsistent.')
  }
  const bytes = await readBoundedMolecularResource(response, artifact.byte_size)
  if (bytes.byteLength !== artifact.byte_size) {
    throw new ApiError('The molecular resource byte size is inconsistent.')
  }
  let computedHash: string
  try {
    computedHash = await sha256Hex(bytes)
  } catch (error) {
    throw new ApiError('The molecular resource content identity could not be verified.', response.status, error)
  }
  if (computedHash !== artifact.content_sha256) {
    throw new ApiError('The molecular resource content hash is inconsistent.')
  }
  return {
    bytes,
    mediaType: artifact.media_type,
    contentSha256: artifact.content_sha256,
    artifactIdentifier: artifact.artifact_identifier,
  }
}

async function requestResearchArtifact(
  path: string,
  artifactIdentifier: string,
  contentSha256: string,
  mediaType: string,
  accessTokenProvider: AccessTokenProvider,
  signal?: AbortSignal,
): Promise<FetchedMolecularResource> {
  let response: Response
  try {
    const headers = await authenticatedHeaders(path, { Accept: mediaType }, accessTokenProvider)
    response = await fetch(path, { method: 'GET', signal, headers })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    if (error instanceof ApiError) throw error
    throw new ApiError('Unable to download research evidence.', undefined, error)
  }
  if (!response.ok) {
    throw controlledHttpError(response.status, `Research artifact request failed (${response.status}).`)
  }
  const bytes = await readBoundedMolecularResource(response, 32 * 1024 * 1024)
  const computedHash = await sha256Hex(bytes)
  if (computedHash !== contentSha256) {
    throw new ApiError('The research artifact content hash is inconsistent.')
  }
  return { bytes, mediaType, contentSha256, artifactIdentifier }
}

export interface PulsateApi {
  getHealth(signal?: AbortSignal): Promise<HealthResponse>
  getPresets(signal?: AbortSignal): Promise<PresetListResponse>
  getPreset(identifier: string, signal?: AbortSignal): Promise<PresetDetailResponse>
  getScene(identifier: string, signal?: AbortSignal): Promise<SceneResponse>
  planExperiment(question: string, signal?: AbortSignal): Promise<ExperimentPlanResponse>
  interpretQuestion(question: string, signal?: AbortSignal): Promise<InterpretationResponse>
  approveInterpretation(identifier: string, specification: InterpretedScientificSpecification, acceptedAssumptions: boolean, signal?: AbortSignal): Promise<ApprovedExperimentResponse>
  uploadResearchInput(input: ResearchInputUpload, position: number, signal?: AbortSignal): Promise<UploadedResearchInput>
  createResearchSession(question: string, inputs?: ResearchInputReference[], artifacts?: ResearchArtifactReference[], signal?: AbortSignal): Promise<ResearchSessionResponse>
  getResearchSession(sessionIdentifier: string, signal?: AbortSignal): Promise<ResearchSessionResponse>
  replyResearchSession(sessionIdentifier: string, message: string, acceptIntentProposal?: boolean, acceptRequirementProposal?: boolean, acceptEvidenceProposal?: boolean, acceptAcquiredInputEvidence?: boolean, inputs?: ResearchInputReference[], artifacts?: ResearchArtifactReference[], signal?: AbortSignal): Promise<ResearchSessionResponse>
  executeResearchSession(sessionIdentifier: string, signal?: AbortSignal): Promise<ResearchSessionResponse>
  getResearchScene(sessionIdentifier: string, artifactIdentifier: string, signal?: AbortSignal): Promise<SceneResponse>
  getResearchConformationScene(sessionIdentifier: string, artifactIdentifier: string, conformationIndex: number, signal?: AbortSignal): Promise<SceneResponse>
  getResearchComplexScene(sessionIdentifier: string, primaryArtifactIdentifier: string, secondaryArtifactIdentifier?: string, signal?: AbortSignal): Promise<SceneResponse>
  getResearchVisualization(sessionIdentifier: string, signal?: AbortSignal): Promise<ResearchVisualizationWorkspace>
  downloadResearchArtifact(sessionIdentifier: string, artifactIdentifier: string, contentSha256: string, mediaType: string, signal?: AbortSignal): Promise<FetchedMolecularResource>
  getRunCapability(signal?: AbortSignal): Promise<RunCapabilityResponse>
  createRun(presetIdentifier: string, idempotencyKey: string, signal?: AbortSignal): Promise<RunStateResponse>
  createExperimentRun(experimentIdentifier: string, idempotencyKey: string, signal?: AbortSignal, executionTarget?: 'local_simulator' | 'ibm_quantum'): Promise<RunStateResponse>
  getRun(runIdentifier: string, signal?: AbortSignal): Promise<RunStateResponse>
  getRunScene(runIdentifier: string, signal?: AbortSignal): Promise<SceneResponse>
  getRunResults(runIdentifier: string, signal?: AbortSignal): Promise<RunResultsResponse>
  getRunVerification(runIdentifier: string, signal?: AbortSignal): Promise<RunVerificationResponse>
  getRunReceipt(runIdentifier: string, signal?: AbortSignal): Promise<RunReceiptResponse>
  getProjectedMolecularScene(projectIdentifier: string, sceneIdentifier: string, signal?: AbortSignal): Promise<ProjectedMolecularSceneMetadata>
  getNativeMolecularStructure(structure: ProjectedMolecularStructureMetadata, signal?: AbortSignal): Promise<FetchedMolecularResource>
  getMolecularTopology(structure: ProjectedMolecularStructureMetadata, signal?: AbortSignal): Promise<FetchedMolecularResource>
  evaluateMolecularProjectPlan(projectIdentifier: string, request: MolecularPlanningRequest, signal?: AbortSignal): Promise<CandidateResearchPlanResponse>
  getWorkflowGraph(graphIdentifier: string, graphVersion: number, signal?: AbortSignal): Promise<WorkflowGraphDefinitionResponse>
  getWorkflowRun(graphRunIdentifier: string, signal?: AbortSignal): Promise<WorkflowRunSnapshotResponse>
  getWorkflowNodes(graphRunIdentifier: string, signal?: AbortSignal): Promise<WorkflowNodesResponse>
  getWorkflowEvidence(graphRunIdentifier: string, signal?: AbortSignal): Promise<WorkflowEvidenceResponse>
  resumeWorkflowRun(graphRunIdentifier: string, maximumCycles?: number, signal?: AbortSignal): Promise<WorkflowRunSnapshotResponse>
  cancelWorkflowRun(graphRunIdentifier: string, reason?: string, signal?: AbortSignal): Promise<WorkflowRunSnapshotResponse>
  decideWorkflowApproval(graphRunIdentifier: string, approvalIdentifier: string, granted: boolean, decisionReason?: string, signal?: AbortSignal): Promise<WorkflowRunSnapshotResponse>
}

export type WorkspaceApi = Pick<PulsateApi, 'getHealth' | 'getPresets' | 'getPreset' | 'getScene'>
  & Partial<Pick<PulsateApi, 'planExperiment' | 'interpretQuestion' | 'approveInterpretation'>>

function presetPath(identifier: string, suffix = ''): string {
  return `/api/v1/experiments/presets/${encodeURIComponent(identifier)}${suffix}`
}

export interface PulsateApiConfiguration {
  accessTokenProvider?: AccessTokenProvider
}

declare global {
  interface Window {
    pulsateAccessTokenProvider?: AccessTokenProvider
  }
}

const hostedAccessToken: AccessTokenProvider = async () => {
  const provider = typeof window === 'undefined' ? undefined : window.pulsateAccessTokenProvider
  return provider ? provider() : null
}

export function createPulsateApi(configuration: PulsateApiConfiguration = {}): PulsateApi {
  const accessTokenProvider = configuration.accessTokenProvider ?? hostedAccessToken
  const json = <T>(path: string, parser: (value: unknown) => T, signal?: AbortSignal, init?: RequestInit) =>
    requestJson(path, parser, accessTokenProvider, signal, init)
  return {
    getHealth: (signal) => json('/api/v1/health', parseHealth, signal),
    getPresets: (signal) => json('/api/v1/experiments/presets', parsePresetList, signal),
    getPreset: (identifier, signal) => json(presetPath(identifier), parsePresetDetail, signal),
    getScene: (identifier, signal) => json(presetPath(identifier, '/scene'), parseScene, signal),
    planExperiment: (question, signal) => json('/api/v1/experiments/plan', parseExperimentPlan, signal, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question }),
  }),
    interpretQuestion: (question, signal) => json('/api/v1/experiments/interpret', parseInterpretation, signal, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question }),
  }),
    approveInterpretation: (identifier, specification, acceptedAssumptions, signal) => json(
    `/api/v1/experiments/${encodeURIComponent(identifier)}/approve`,
    parseApprovedExperiment,
    signal,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ specification, accepted_assumptions: acceptedAssumptions }),
    },
  ),
    uploadResearchInput: async (input, position, signal) => {
      if (!Number.isInteger(position) || position < 1 || position > 64) {
        throw new ApiError('Research input position is invalid.')
      }
      if (input.file.size > RESEARCH_INPUT_MAXIMUM_BYTES) {
        throw new ApiError('A research input file exceeds the 16 MiB limit.')
      }
      const bytes = new Uint8Array(await input.file.arrayBuffer())
      const digest = await sha256Hex(bytes)
      const localIdentifier = `scientific-input-${digest.slice(0, 32)}`
      const artifact = await json('/api/v1/scientific/artifacts', parseResearchArtifact, signal, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          reference: {
            artifact_identifier: localIdentifier,
            schema_version: { major: 1, minor: 0, patch: 0 },
            artifact_type: input.artifactType,
            media_type: researchInputMediaType(input.file.name),
            content_sha256: digest,
            byte_size: bytes.byteLength,
            storage_location: null,
            metadata: {},
            provenance: {
              producer: 'pulsate-research-workspace',
              producer_version: { major: 1, minor: 0, patch: 0 },
              execution_identifier: `scientific-input-upload-${digest.slice(0, 24)}`,
              source: 'cgr',
            },
            parents: [],
          },
          payload_base64: bytesToBase64(bytes),
        }),
      })
      return {
        inputReference: {
          reference_identifier: `input-${String(position).padStart(2, '0')}-${input.artifactType.replaceAll('_', '-')}`,
          artifact_type: input.artifactType as ResearchInputArtifactType,
          artifact_identifier: artifact.artifact_identifier,
        },
        artifactReference: artifact,
      }
    },
    createResearchSession: (question, inputs = [], artifacts = [], signal) => json(
      '/api/v1/research/sessions',
      parseResearchSession,
      signal,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question, input_references: inputs, artifact_references: artifacts }),
      },
    ),
    getResearchSession: (sessionIdentifier, signal) => json(
      `/api/v1/research/sessions/${encodeURIComponent(sessionIdentifier)}`,
      parseResearchSession,
      signal,
    ),
    replyResearchSession: (
      sessionIdentifier,
      message,
      acceptIntentProposal = false,
      acceptRequirementProposal = false,
      acceptEvidenceProposal = false,
      acceptAcquiredInputEvidence = false,
      inputs = [],
      artifacts = [],
      signal,
    ) => json(
      `/api/v1/research/sessions/${encodeURIComponent(sessionIdentifier)}/reply`,
      parseResearchSession,
      signal,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message,
          accept_intent_proposal: acceptIntentProposal,
          accept_requirement_proposal: acceptRequirementProposal,
          accept_evidence_proposal: acceptEvidenceProposal,
          accept_acquired_input_evidence: acceptAcquiredInputEvidence,
          input_references: inputs.length > 0 ? inputs : null,
          artifact_references: artifacts.length > 0 ? artifacts : null,
        }),
      },
    ),
    executeResearchSession: (sessionIdentifier, signal) => json(
      `/api/v1/research/sessions/${encodeURIComponent(sessionIdentifier)}/execute`,
      parseResearchSession,
      signal,
      { method: 'POST' },
    ),
    getResearchScene: (sessionIdentifier, artifactIdentifier, signal) => json(
      `/api/v1/research/sessions/${encodeURIComponent(sessionIdentifier)}/scene?artifact_identifier=${encodeURIComponent(artifactIdentifier)}`,
      parseScene,
      signal,
    ),
    getResearchConformationScene: (sessionIdentifier, artifactIdentifier, conformationIndex, signal) => json(
      `/api/v1/research/sessions/${encodeURIComponent(sessionIdentifier)}/scene?artifact_identifier=${encodeURIComponent(artifactIdentifier)}&conformation_index=${conformationIndex}`,
      parseScene,
      signal,
    ),
    getResearchComplexScene: (sessionIdentifier, primaryArtifactIdentifier, secondaryArtifactIdentifier, signal) => json(
      `/api/v1/research/sessions/${encodeURIComponent(sessionIdentifier)}/visualization/complex?primary_artifact_identifier=${encodeURIComponent(primaryArtifactIdentifier)}${secondaryArtifactIdentifier ? `&secondary_artifact_identifier=${encodeURIComponent(secondaryArtifactIdentifier)}` : ''}`,
      parseScene,
      signal,
    ),
    getResearchVisualization: (sessionIdentifier, signal) => json(
      `/api/v1/research/sessions/${encodeURIComponent(sessionIdentifier)}/visualization`,
      parseResearchVisualization,
      signal,
    ),
    downloadResearchArtifact: (sessionIdentifier, artifactIdentifier, contentSha256, mediaType, signal) => requestResearchArtifact(
      `/api/v1/research/sessions/${encodeURIComponent(sessionIdentifier)}/artifacts/${encodeURIComponent(artifactIdentifier)}`,
      artifactIdentifier,
      contentSha256,
      mediaType,
      accessTokenProvider,
      signal,
    ),
    getRunCapability: (signal) => json('/api/v1/runs/capability', parseCapability, signal),
    createRun: (presetIdentifier, idempotencyKey, signal) => json('/api/v1/runs', parseRunState, signal, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Idempotency-Key': idempotencyKey },
    body: JSON.stringify({ preset_identifier: presetIdentifier, execution_target: 'local_simulator' }),
  }),
    createExperimentRun: (experimentIdentifier, idempotencyKey, signal, executionTarget = 'local_simulator') => json('/api/v1/runs', parseRunState, signal, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Idempotency-Key': idempotencyKey },
    body: JSON.stringify({ experiment_identifier: experimentIdentifier, execution_target: executionTarget }),
  }),
    getRun: (runIdentifier, signal) => json(`/api/v1/runs/${encodeURIComponent(runIdentifier)}`, parseRunState, signal),
    getRunScene: (runIdentifier, signal) => json(`/api/v1/runs/${encodeURIComponent(runIdentifier)}/scene`, parseScene, signal),
    getRunResults: (runIdentifier, signal) => json(`/api/v1/runs/${encodeURIComponent(runIdentifier)}/results`, parseRunResults, signal),
    getRunVerification: (runIdentifier, signal) => json(`/api/v1/runs/${encodeURIComponent(runIdentifier)}/verification`, parseVerification, signal),
    getRunReceipt: (runIdentifier, signal) => json(`/api/v1/runs/${encodeURIComponent(runIdentifier)}/receipt`, parseReceipt, signal),
    getProjectedMolecularScene: (projectIdentifier, sceneIdentifier, signal) => json(
    `/api/v1/molecular/scenes/projected?project_identifier=${encodeURIComponent(projectIdentifier)}&scene_identifier=${encodeURIComponent(sceneIdentifier)}`,
    parseProjectedMolecularScene,
    signal,
  ),
    getNativeMolecularStructure: (structure, signal) => requestMolecularResource(
    structure.native_structure_url,
    structure.source_artifact,
    accessTokenProvider,
    signal,
  ),
    getMolecularTopology: (structure, signal) => requestMolecularResource(
    structure.topology_url,
    structure.topology_artifact,
    accessTokenProvider,
    signal,
  ),
    evaluateMolecularProjectPlan: (projectIdentifier, request, signal) => json(
    `/api/v1/molecular/projects/${encodeURIComponent(projectIdentifier)}/planning/evaluate`,
    parseMolecularPlanningResponse,
    signal,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    },
  ),
    getWorkflowGraph: (graphIdentifier, graphVersion, signal) => json(
    `/api/v1/workflows/${encodeURIComponent(graphIdentifier)}/versions/${graphVersion}`,
    parseWorkflowGraph,
    signal,
  ),
    getWorkflowRun: (graphRunIdentifier, signal) => json(
    `/api/v1/workflow-runs/${encodeURIComponent(graphRunIdentifier)}`,
    parseWorkflowRun,
    signal,
  ),
    getWorkflowNodes: (graphRunIdentifier, signal) => json(
    `/api/v1/workflow-runs/${encodeURIComponent(graphRunIdentifier)}/nodes`,
    parseWorkflowNodes,
    signal,
  ),
    getWorkflowEvidence: (graphRunIdentifier, signal) => json(
    `/api/v1/workflow-runs/${encodeURIComponent(graphRunIdentifier)}/evidence`,
    parseWorkflowEvidence,
    signal,
  ),
    resumeWorkflowRun: (graphRunIdentifier, maximumCycles = 1000, signal) => json(
    `/api/v1/workflow-runs/${encodeURIComponent(graphRunIdentifier)}/resume`,
    parseWorkflowRun,
    signal,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ maximum_cycles: maximumCycles }),
    },
  ),
    cancelWorkflowRun: (graphRunIdentifier, reason, signal) => json(
    `/api/v1/workflow-runs/${encodeURIComponent(graphRunIdentifier)}/cancel`,
    parseWorkflowRun,
    signal,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reason: reason ?? null }),
    },
  ),
    decideWorkflowApproval: (graphRunIdentifier, approvalIdentifier, granted, decisionReason, signal) => json(
    `/api/v1/workflow-runs/${encodeURIComponent(graphRunIdentifier)}/approvals/${encodeURIComponent(approvalIdentifier)}`,
    parseWorkflowRun,
    signal,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ granted, decision_reason: decisionReason ?? null }),
    },
  ),
  }
}

export const pulsateApi: PulsateApi = createPulsateApi()
