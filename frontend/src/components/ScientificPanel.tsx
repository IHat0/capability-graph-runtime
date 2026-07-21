import type { PresetSummaryResponse } from '../api/types'
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

export function ScientificPanel({ scene, presets, selectedPresetId, displayedPresetId, staleSceneMessage, loading, onPresetChange, presetRun }: {
  scene: MolecularScene
  presets: PresetSummaryResponse[]
  selectedPresetId: string | null
  displayedPresetId: string | null
  staleSceneMessage: string | null
  loading: boolean
  onPresetChange: (identifier: string) => void
  presetRun: ReturnType<typeof usePresetRun>
}) {
  return (
    <aside className="science-panel" aria-label="Experiment inspector">
      <section className="inspector-selector" id="preset-menu">
        <PresetSelector presets={presets} value={selectedPresetId} disabled={loading} onChange={onPresetChange} />
        {staleSceneMessage && <div className="stale-scene-notice" role="status"><strong>Retained structure</strong><span>{staleSceneMessage}</span></div>}
        <div className="displayed-preset-row"><span>Displayed preset</span><strong>{displayedPresetId ?? 'Unknown'}</strong></div>
      </section>

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

      <WorkflowStepper run={presetRun.run} results={presetRun.results} verification={presetRun.verification} />
      <ResultSummary run={presetRun.run} results={presetRun.results} verification={presetRun.verification} receipt={presetRun.receipt} />

      <section className="execution-control">
        <button type="button" disabled={!presetRun.canRun} onClick={() => void presetRun.startRun()}>
          {presetRun.creating ? 'Creating run…' : presetRun.run && !['authorized', 'rejected', 'failed', 'interrupted'].includes(presetRun.run.status) ? 'Run in progress' : 'Run experiment'}
        </button>
        <p>{presetRun.disabledReason ?? (presetRun.run ? `Run ${presetRun.run.run_identifier} is ${presetRun.run.status}.` : 'Verified local simulator execution is available.')}</p>
      </section>
    </aside>
  )
}
