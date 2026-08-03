import { useState, type FormEvent } from 'react'
import type {
  CandidateResearchPlanResponse,
  MolecularPlanningRequest,
  PlanningAssignment,
  PlanningFeasibilityStatus,
  RejectedPlanningAlternative,
} from '../api/planning'
import type { MolecularPlanningState } from '../hooks/useMolecularPlanning'
import type { LoadedMolecularProjectScene } from '../scene/native-project'

const STATUS_LABELS: Record<PlanningFeasibilityStatus, string> = {
  feasible: 'Feasible',
  conditional: 'Conditional',
  unknown: 'Unknown',
  infeasible: 'Infeasible',
}

function selectedValues(target: HTMLSelectElement): string[] {
  return Array.from(target.selectedOptions, (option) => option.value)
}

function parseArray(value: string, label: string): unknown[] {
  let parsed: unknown
  try {
    parsed = JSON.parse(value)
  } catch {
    throw new Error(`${label} must be valid JSON.`)
  }
  if (!Array.isArray(parsed)) throw new Error(`${label} must be a JSON array.`)
  return parsed
}

function statusClass(status: PlanningFeasibilityStatus): string {
  return status === 'feasible' ? 'good' : status === 'conditional' ? 'warning' : status === 'infeasible' ? 'error' : ''
}

function CapabilityResult({ item, rejected, limitations }: {
  item: PlanningAssignment | RejectedPlanningAlternative
  rejected: boolean
  limitations: string[]
}) {
  const feasibility = item.feasibility
  return <article className="planning-capability">
    <div className="planning-capability__heading">
      <strong>{item.capability_name}</strong>
      <span className={`status-badge status-badge--${statusClass(feasibility.status)}`}>
        <span className="status-badge__dot" aria-hidden="true" />
        {STATUS_LABELS[feasibility.status]}
      </span>
    </div>
    <small>v{item.capability_version.major}.{item.capability_version.minor}.{item.capability_version.patch} · {item.execution_target ?? 'No target assigned'}</small>
    {rejected && 'rejection_reasons' in item && <ul>{item.rejection_reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>}
    {feasibility.findings.length > 0 && <details>
      <summary>Why this status</summary>
      <ul>{feasibility.findings.map((finding) => <li key={finding.finding_identifier}>
        {finding.explanation}
        {finding.related_fact_identifiers.length > 0 && <small>Evidence: {finding.related_fact_identifiers.join(', ')}</small>}
      </li>)}</ul>
    </details>}
    {feasibility.estimates.length > 0 && <dl className="planning-estimates">{feasibility.estimates.map((estimate) => <div key={`${estimate.estimate_type}-${estimate.unit}`}>
      <dt>{estimate.estimate_type}</dt>
      <dd>{estimate.expected_value ?? estimate.upper_bound ?? estimate.lower_bound} {estimate.unit} {estimate.confidence === null ? '' : `(${Math.round(estimate.confidence * 100)}% confidence)`}</dd>
    </div>)}</dl>}
    {feasibility.fidelity && <p>Fidelity: <strong>{feasibility.fidelity.fidelity_level}</strong></p>}
    {limitations.length > 0 && <div><strong>Known limitations</strong><ul>{limitations.map((item) => <li key={item}>{item}</li>)}</ul></div>}
  </article>
}

export function MolecularPlanningResults({ response }: { response: CandidateResearchPlanResponse }) {
  const limitations = new Map(response.capability_declarations.map((item) => [
    `${item.descriptor.capability_name}:${item.descriptor.version.major}.${item.descriptor.version.minor}.${item.descriptor.version.patch}`,
    item.known_limitations,
  ]))
  const forCapability = (item: PlanningAssignment | RejectedPlanningAlternative) => limitations.get(
    `${item.capability_name}:${item.capability_version.major}.${item.capability_version.minor}.${item.capability_version.patch}`,
  ) ?? []
  const plan = response.plan
  const factSubjects = [...new Set(plan.planning_facts.facts.flatMap((fact) => (
    typeof fact.subject_identifier === 'string' ? [fact.subject_identifier] : []
  )))]
  const verifierIds = plan.verification_requirements.flatMap((requirement) => (
    typeof requirement.verifier_identifier === 'string' ? [requirement.verifier_identifier] : []
  ))
  return <section className="molecular-planning-results" aria-label="Candidate research plan">
    <span className="sr-only" role="status" aria-live="polite">
      Candidate research plan {response.plan_identifier} loaded with {plan.selected_assignments.length} selected assignments and {plan.rejected_alternatives.length} rejected alternatives.
    </span>
    <p className="section-kicker">Read-only candidate plan</p>
    <h2>Capability assessment</h2>
    <dl className="property-list">
      <div><dt>Plan</dt><dd>{response.plan_identifier}</dd></div>
      <div><dt>Fingerprint</dt><dd>{response.plan_fingerprint}</dd></div>
      <div><dt>Facts fingerprint</dt><dd>{response.planning_facts_fingerprint}</dd></div>
    </dl>
    <h3>Selected assignments</h3>
    {plan.selected_assignments.length > 0
      ? plan.selected_assignments.map((item) => <CapabilityResult key={item.assignment_identifier} item={item} rejected={false} limitations={forCapability(item)} />)
      : <p className="supporting-copy">No capability assignment is currently supported by the declared catalogue and evidence.</p>}
    <h3>Rejected alternatives</h3>
    {plan.rejected_alternatives.length > 0
      ? plan.rejected_alternatives.map((item) => <CapabilityResult key={item.alternative_identifier} item={item} rejected limitations={forCapability(item)} />)
      : <p className="supporting-copy">No rejected alternatives were declared.</p>}
    <h3>Unresolved requirements</h3>
    <p className="supporting-copy">{[...plan.unresolved_requirement_identifiers, ...plan.unresolved_goal_identifiers].join(', ') || 'None declared'}</p>
    <h3>Aggregate estimates</h3>
    {plan.aggregate_estimates.length > 0
      ? <dl className="planning-estimates">{plan.aggregate_estimates.map((estimate) => <div key={`${estimate.estimate_type}-${estimate.unit}`}><dt>{estimate.estimate_type}</dt><dd>{estimate.expected_value ?? estimate.upper_bound ?? estimate.lower_bound} {estimate.unit}</dd></div>)}</dl>
      : <p className="supporting-copy">No aggregate runtime, cost, or resource estimate is supported by the selected assignments.</p>}
    <h3>Approvals and verification</h3>
    <p className="supporting-copy">Required, not approved: {plan.approval_requirements.join(', ') || 'None declared'}.</p>
    <p className="supporting-copy">Required, not completed: {verifierIds.join(', ') || 'None declared'}.</p>
    <h3>Evidence links</h3>
    <p className="supporting-copy">{plan.evidence_artifacts.map((item) => `${item.artifact_identifier} (${item.content_sha256})`).join(', ') || 'No artifact evidence linked'}</p>
    <p className="supporting-copy">Planning subjects: {factSubjects.join(', ') || 'None declared'}</p>
    <div className="execution-control execution-control--read-only">
      <strong>Planning only</strong>
      <p>This candidate plan does not authorize execution, create a run, or submit work.</p>
    </div>
  </section>
}

export function MolecularPlanningPanel({ scene, planning }: {
  scene: LoadedMolecularProjectScene
  planning: MolecularPlanningState
}) {
  const metadata = scene.metadata
  const allSystemIds = [...new Set(metadata.structures.map((item) => item.system_identifier))]
  const [description, setDescription] = useState('Assess the declared molecular systems against available scientific capabilities.')
  const [objectiveType, setObjectiveType] = useState('interaction_analysis')
  const [systemIds, setSystemIds] = useState(() => [...new Set(metadata.structures.map((item) => item.system_identifier))])
  const [structureIds, setStructureIds] = useState(() => metadata.structures.map((item) => item.structure_identifier))
  const [regionIds, setRegionIds] = useState(() => metadata.regions.map((item) => item.region_identifier))
  const [properties, setProperties] = useState('interaction_energy')
  const [target, setTarget] = useState('target.local')
  const [forbiddenTargets, setForbiddenTargets] = useState('')
  const [wallTime, setWallTime] = useState('3600')
  const [confidence, setConfidence] = useState('0.8')
  const [fidelity, setFidelity] = useState('screening')
  const [resourceCeilings, setResourceCeilings] = useState('[]')
  const [costCeilings, setCostCeilings] = useState('[]')
  const [availableResources, setAvailableResources] = useState('[]')
  const [selectedCapabilities, setSelectedCapabilities] = useState('[]')
  const [formError, setFormError] = useState<string | null>(null)
  const [observedAt] = useState(() => new Date().toISOString())

  const submit = (event: FormEvent) => {
    event.preventDefault()
    try {
      const requestedProperties = properties.split(',').map((item) => item.trim()).filter(Boolean)
      const requestedTarget = target.trim() || null
      const request: MolecularPlanningRequest = {
        objective: {
          objective_identifier: 'objective.workspace-planning',
          schema_version: metadata.schema_version,
          objective_type: objectiveType.trim(),
          original_description: description.trim(),
          normalized_description: description.trim(),
          project_identifier: metadata.project_identifier,
          molecular_system_identifiers: systemIds,
          structure_identifiers: structureIds,
          structural_region_identifiers: regionIds,
          comparison_state_identifiers: [],
          requested_property_identifiers: requestedProperties,
          scientific_constraints: [],
          required_confidence: confidence ? Number(confidence) : null,
          stopping_criteria: [],
          assumptions: [],
          provenance: { producer: 'pulsate-workspace', producer_version: metadata.schema_version, source: 'cgr' },
        },
        constraints: {
          permitted_execution_targets: requestedTarget ? [requestedTarget] : [],
          forbidden_execution_targets: forbiddenTargets.split(',').map((item) => item.trim()).filter(Boolean),
          resource_ceilings: parseArray(resourceCeilings, 'Resource ceilings'),
          cost_ceilings: parseArray(costCeilings, 'Cost ceilings'),
          maximum_wall_time_seconds: wallTime ? Number(wallTime) : null,
          minimum_fidelity: fidelity || null,
          required_confidence: confidence ? Number(confidence) : null,
          required_verifications: [],
          approval_requirements: [],
          policy_notes: ['Planning is read-only and non-authorizing.'],
        },
        resources: {
          snapshot_identifier: 'resources.workspace-planning',
          schema_version: metadata.schema_version,
          observed_at: observedAt,
          execution_environment: 'pulsate-workspace',
          available_resources: parseArray(availableResources, 'Available resources'),
          available_execution_targets: requestedTarget ? [requestedTarget] : [],
          unavailable_reasons: {},
          provenance: { producer: 'pulsate-workspace', producer_version: metadata.schema_version, source: 'cgr' },
        },
        requested_execution_target: requestedTarget,
        selected_capabilities: parseArray(selectedCapabilities, 'Selected capabilities') as MolecularPlanningRequest['selected_capabilities'],
      }
      if (!description.trim() || !objectiveType.trim() || systemIds.length === 0 || structureIds.length === 0) {
        throw new Error('Provide an objective and select at least one molecular system and structure.')
      }
      if ((wallTime && (!Number.isFinite(Number(wallTime)) || Number(wallTime) <= 0))
        || (confidence && (!Number.isFinite(Number(confidence)) || Number(confidence) < 0 || Number(confidence) > 1))) {
        throw new Error('Time and confidence constraints must use valid bounded numbers.')
      }
      setFormError(null)
      void planning.evaluate(request)
    } catch (cause) {
      setFormError(cause instanceof Error ? cause.message : 'The planning request is invalid.')
    }
  }

  return <section className="molecular-planning-workspace" aria-busy={planning.loading}>
    <span className="sr-only" role="status" aria-live="polite">
      {planning.loading ? 'Evaluating the read-only molecular research plan.' : ''}
    </span>
    <form className="molecular-planning-form" aria-label="Evaluate molecular research plan" onSubmit={submit}>
      <p className="section-kicker">Capability and resource planner</p>
      <h2>Evaluate a research plan</h2>
      <label>Scientific objective<textarea aria-label="Planning objective" value={description} onChange={(event) => setDescription(event.target.value)} /></label>
      <label>Objective type<input aria-label="Objective type" value={objectiveType} onChange={(event) => setObjectiveType(event.target.value)} /></label>
      <label>Molecular systems<select multiple aria-label="Molecular systems" value={systemIds} onChange={(event) => setSystemIds(selectedValues(event.target))}>{allSystemIds.map((id) => <option key={id}>{id}</option>)}</select></label>
      <label>Structures<select multiple aria-label="Molecular structures" value={structureIds} onChange={(event) => setStructureIds(selectedValues(event.target))}>{metadata.structures.map((item) => <option key={item.structure_identifier}>{item.structure_identifier}</option>)}</select></label>
      <label>Structural regions<select multiple aria-label="Structural regions" value={regionIds} onChange={(event) => setRegionIds(selectedValues(event.target))}>{metadata.regions.map((item) => <option key={item.region_identifier}>{item.region_identifier}</option>)}</select></label>
      <label>Requested properties<input value={properties} onChange={(event) => setProperties(event.target.value)} /></label>
      <label>Requested execution target<input value={target} onChange={(event) => setTarget(event.target.value)} /></label>
      <label>Forbidden execution targets<input value={forbiddenTargets} onChange={(event) => setForbiddenTargets(event.target.value)} placeholder="target.remote, target.hardware" /></label>
      <div className="planning-form-grid">
        <label>Maximum wall time (seconds)<input type="number" min="1" value={wallTime} onChange={(event) => setWallTime(event.target.value)} /></label>
        <label>Required confidence<input type="number" min="0" max="1" step="0.01" value={confidence} onChange={(event) => setConfidence(event.target.value)} /></label>
        <label>Minimum fidelity<select value={fidelity} onChange={(event) => setFidelity(event.target.value)}><option value="">Not specified</option><option value="exploratory">Exploratory</option><option value="screening">Screening</option><option value="quantitative">Quantitative</option><option value="reference">Reference</option></select></label>
      </div>
      <details><summary>Resource, cost, and capability declarations</summary>
        <label>Resource ceilings (JSON)<textarea value={resourceCeilings} onChange={(event) => setResourceCeilings(event.target.value)} /></label>
        <label>Cost ceilings (JSON)<textarea value={costCeilings} onChange={(event) => setCostCeilings(event.target.value)} /></label>
        <label>Available resources (JSON)<textarea value={availableResources} onChange={(event) => setAvailableResources(event.target.value)} /></label>
        <label>Selected exact capabilities (JSON)<textarea value={selectedCapabilities} onChange={(event) => setSelectedCapabilities(event.target.value)} /></label>
      </details>
      <button className="primary-button" type="submit" disabled={planning.loading}>{planning.loading ? 'Evaluating plan...' : 'Evaluate plan'}</button>
      <p className="supporting-copy">Read-only evaluation only. No run, engine, or IBM submission is created.</p>
      {(formError || planning.error) && <p className="planning-form-error" role="alert">{formError ?? planning.error}</p>}
    </form>
    {planning.result && <MolecularPlanningResults response={planning.result} />}
  </section>
}
