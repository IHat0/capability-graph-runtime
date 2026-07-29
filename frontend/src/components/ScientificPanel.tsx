import type {
  ExperimentPlanResponse,
  PresetSummaryResponse,
  RunReceiptResponse,
  RunResultsResponse,
  RunStateResponse,
  RunVerificationResponse,
} from '../api/types'
import type { MolecularScene } from '../scene/types'
import { humanize } from '../utils/format'
import { PresetSelector } from './PresetSelector'
import { ResultSummary } from './ResultSummary'
import { WorkflowStepper } from './WorkflowStepper'
import type { usePresetRun } from '../hooks/usePresetRun'

function Value({ value, fallback = 'Not supplied' }: { value: React.ReactNode; fallback?: string }) {
  return <>{value === undefined || value === null || value === '' ? fallback : value}</>
}

function PropertyList({ rows }: { rows: Array<[string, React.ReactNode]> }) {
  return <dl className="property-list">{rows.map(([label, value]) => <div key={label}><dt>{label}</dt><dd><Value value={value} /></dd></div>)}</dl>
}

export function ScientificPanel({ scene, presets, selectedPresetId, displayedPresetId, staleSceneMessage, loading, onPresetChange, presetRun, openedRun, readOnlyLookup = false, plan }: {
  scene: MolecularScene
  presets: PresetSummaryResponse[]
  selectedPresetId: string | null
  displayedPresetId: string | null
  staleSceneMessage: string | null
  loading: boolean
  onPresetChange: (identifier: string) => void
  presetRun: ReturnType<typeof usePresetRun>
  openedRun?: {
    run: RunStateResponse
    results: RunResultsResponse | null
    verification: RunVerificationResponse | null
    receipt: RunReceiptResponse | null
  } | null
  readOnlyLookup?: boolean
  plan: ExperimentPlanResponse | null
}) {
  const evidence = openedRun ?? presetRun
  return (
    <aside className="science-panel" aria-label="Experiment inspector">
      <section className="inspector-selector" id="preset-menu">
        <PresetSelector presets={presets} value={selectedPresetId} disabled={loading} onChange={onPresetChange} />
        {openedRun && <div className="displayed-preset-row"><span>Opened run</span><strong>{openedRun.run.run_identifier}</strong></div>}
        {plan?.ready_for_execution && <div className="displayed-preset-row"><span>Dynamic experiment</span><strong>{plan.experiment_identifier}</strong></div>}
        {staleSceneMessage && <div className="stale-scene-notice" role="status"><strong>Retained structure</strong><span>{staleSceneMessage}</span></div>}
        <div className="displayed-preset-row"><span>{openedRun ? 'Scientific source' : 'Displayed preset'}</span><strong>{displayedPresetId ?? 'Unknown'}</strong></div>
      </section>

      {plan && (
        <section className="inspector-section" aria-labelledby="planning-title">
          <p className="section-kicker">Scientific intake</p>
          <h2 id="planning-title">Validated plan</h2>
          <p>{plan.original_question}</p>
          {plan.assumptions.length > 0 && <p><strong>Assumptions:</strong> {plan.assumptions.join('; ')}</p>}
          {plan.warnings.length > 0 && <p><strong>Warnings:</strong> {plan.warnings.join('; ')}</p>}
          <p><strong>Requested execution:</strong> {plan.requested_execution_target === 'ibm_quantum' ? 'IBM Quantum hardware' : 'Local simulator'}</p>
        </section>
      )}

      <section className="inspector-section" aria-labelledby="experiment-title">
        <p className="section-kicker">Experiment</p>
        <h1 id="experiment-title">{humanize(scene.experimentId ?? displayedPresetId)}</h1>
        <PropertyList rows={[
          ['Atoms', scene.atoms.length], ['Bonds', scene.bonds.length], ['Coordinate unit', scene.coordinateUnit],
          ['Charge', scene.scientific.charge], ['Multiplicity', scene.scientific.spinMultiplicity],
        ]} />
      </section>

      <section className="inspector-section" aria-labelledby="electronic-title">
        <p className="section-kicker">Electronic structure</p>
        <h2 id="electronic-title">Model parameters</h2>
        <PropertyList rows={[
          ['Method', scene.scientific.referenceMethod], ['Basis', scene.scientific.basisSet],
          ['Active electrons', scene.scientific.activeElectronCount], ['Active orbitals', scene.scientific.activeSpatialOrbitalCount],
          ['Orbital indices', scene.scientific.activeOrbitalIndices?.join(', ')],
        ]} />
      </section>

      <section className="inspector-section" aria-labelledby="quantum-title">
        <p className="section-kicker">Quantum model</p>
        <h2 id="quantum-title">Execution definition</h2>
        <PropertyList rows={[
          ['Mapper', scene.scientific.mapper], ['Ansatz', scene.scientific.ansatz],
          ['Execution policy', scene.scientific.runtimeIdentifier],
          ['Network', scene.scientific.networkDisabled === true ? 'Disabled by policy' : 'Not supplied'],
        ]} />
      </section>

      <WorkflowStepper run={evidence.run} results={evidence.results} verification={evidence.verification} />
      <ResultSummary run={evidence.run} results={evidence.results} verification={evidence.verification} receipt={evidence.receipt} />

      {readOnlyLookup ? <section className="execution-control execution-control--read-only">
        <strong>{openedRun ? 'Read-only persisted run' : 'Opening persisted run'}</strong>
        <p>{openedRun
          ? 'This workspace loaded existing evidence only. It did not create, resume, or execute a run.'
          : 'The requested run is being loaded read-only. Preset execution is unavailable during this lookup.'}</p>
      </section> : <section className="execution-control">
        <button type="button" disabled={!presetRun.canRun} onClick={() => void presetRun.startRun()}>
          {presetRun.creating ? 'Creating run…' : presetRun.run && !['authorized', 'rejected', 'failed', 'interrupted'].includes(presetRun.run.status) ? 'Run in progress' : 'Run experiment'}
        </button>
        <p>{presetRun.disabledReason ?? (presetRun.run ? `Run ${presetRun.run.run_identifier} is ${presetRun.run.status}.` : plan?.requested_execution_target === 'ibm_quantum' ? 'Trusted local preflight and IBM Quantum execution are available.' : 'Verified local simulator execution is available.')}</p>
      </section>}
    </aside>
  )
}
