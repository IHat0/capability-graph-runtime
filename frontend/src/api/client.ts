import type {
  HealthResponse,
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
} from './types'

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status?: number,
    readonly cause?: unknown,
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
  'queued', 'validating', 'running_quantum_workflow', 'authorized', 'rejected', 'failed', 'interrupted',
])

function parseCapability(value: unknown): RunCapabilityResponse {
  if (!isRecord(value) || typeof value.available !== 'boolean' || !isStringArray(value.execution_targets)
    || !(value.reason === null || typeof value.reason === 'string')
    || !(value.maximum_run_seconds === null || (isFiniteNumber(value.maximum_run_seconds) && value.maximum_run_seconds > 0))) {
    malformed('The backend returned a malformed execution capability response.')
  }
  return value as unknown as RunCapabilityResponse
}

function hasRunIdentity(value: Record<string, unknown>): boolean {
  return hasString(value, 'run_identifier') && hasString(value, 'preset_identifier')
    && hasString(value, 'experiment_identifier') && hasString(value, 'experiment_fingerprint')
    && hasString(value, 'expected_experiment_sha256') && hasString(value, 'structure_identifier')
}

function parseRunState(value: unknown): RunStateResponse {
  if (!isRecord(value) || !hasRunIdentity(value) || !runStatuses.has(value.status as RunStatus)
    || value.execution_target !== 'local_simulator' || !hasString(value, 'created_at')
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
    || !hasString(value, 'execution_environment_identity') || !hasString(value, 'receipt_sha256')) {
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
    || !Array.isArray(value.checks) || !Array.isArray(value.compatibility_warnings)) {
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
      && hasString(item, 'artifact_type') && hasString(item, 'content_sha256'))) {
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
    || !isOptionalString(value.structure_hash)) {
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

async function requestJson<T>(path: string, parser: (value: unknown) => T, signal?: AbortSignal, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, { ...init, signal, headers: { Accept: 'application/json', ...init?.headers } })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new ApiError('Unable to reach the Pulsate API.', undefined, error)
  }

  if (!response.ok) {
    let message = `Pulsate API request failed (${response.status}).`
    try {
      const body = await response.json()
      if (isRecord(body) && isRecord(body.detail) && typeof body.detail.message === 'string') message = body.detail.message
    } catch { /* Preserve the status-based message for non-JSON errors. */ }
    throw new ApiError(message, response.status)
  }

  try {
    return parser(await response.json())
  } catch (error) {
    if (error instanceof ApiError) throw error
    throw new ApiError('The Pulsate API returned invalid JSON.', response.status, error)
  }
}

export interface PulsateApi {
  getHealth(signal?: AbortSignal): Promise<HealthResponse>
  getPresets(signal?: AbortSignal): Promise<PresetListResponse>
  getPreset(identifier: string, signal?: AbortSignal): Promise<PresetDetailResponse>
  getScene(identifier: string, signal?: AbortSignal): Promise<SceneResponse>
  getRunCapability(signal?: AbortSignal): Promise<RunCapabilityResponse>
  createRun(presetIdentifier: string, idempotencyKey: string, signal?: AbortSignal): Promise<RunStateResponse>
  getRun(runIdentifier: string, signal?: AbortSignal): Promise<RunStateResponse>
  getRunResults(runIdentifier: string, signal?: AbortSignal): Promise<RunResultsResponse>
  getRunVerification(runIdentifier: string, signal?: AbortSignal): Promise<RunVerificationResponse>
  getRunReceipt(runIdentifier: string, signal?: AbortSignal): Promise<RunReceiptResponse>
}

export type WorkspaceApi = Pick<PulsateApi, 'getHealth' | 'getPresets' | 'getPreset' | 'getScene'>

function presetPath(identifier: string, suffix = ''): string {
  return `/api/v1/experiments/presets/${encodeURIComponent(identifier)}${suffix}`
}

export const pulsateApi: PulsateApi = {
  getHealth: (signal) => requestJson('/api/v1/health', parseHealth, signal),
  getPresets: (signal) => requestJson('/api/v1/experiments/presets', parsePresetList, signal),
  getPreset: (identifier, signal) => requestJson(presetPath(identifier), parsePresetDetail, signal),
  getScene: (identifier, signal) => requestJson(presetPath(identifier, '/scene'), parseScene, signal),
  getRunCapability: (signal) => requestJson('/api/v1/runs/capability', parseCapability, signal),
  createRun: (presetIdentifier, idempotencyKey, signal) => requestJson('/api/v1/runs', parseRunState, signal, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Idempotency-Key': idempotencyKey },
    body: JSON.stringify({ preset_identifier: presetIdentifier, execution_target: 'local_simulator' }),
  }),
  getRun: (runIdentifier, signal) => requestJson(`/api/v1/runs/${encodeURIComponent(runIdentifier)}`, parseRunState, signal),
  getRunResults: (runIdentifier, signal) => requestJson(`/api/v1/runs/${encodeURIComponent(runIdentifier)}/results`, parseRunResults, signal),
  getRunVerification: (runIdentifier, signal) => requestJson(`/api/v1/runs/${encodeURIComponent(runIdentifier)}/verification`, parseVerification, signal),
  getRunReceipt: (runIdentifier, signal) => requestJson(`/api/v1/runs/${encodeURIComponent(runIdentifier)}/receipt`, parseReceipt, signal),
}
