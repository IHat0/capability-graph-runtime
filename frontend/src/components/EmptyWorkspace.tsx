import type { PresetSummaryResponse } from '../api/types'
import { PresetMenu } from './PresetMenu'

function ObjectiveComposer({ presets, loading, onPresetChange }: {
  presets: PresetSummaryResponse[]
  loading: boolean
  onPresetChange: (identifier: string) => void
}) {
  return (
    <section className="objective-composer" aria-labelledby="objective-title">
      <p className="section-kicker">New experiment</p>
      <h1 id="objective-title">Describe your scientific objective</h1>
      <p className="objective-intro">Define the molecule, method, and comparison you want to investigate.</p>
      <label className="sr-only" htmlFor="scientific-objective">Scientific objective</label>
      <textarea
        id="scientific-objective"
        rows={5}
        placeholder="Calculate the ground-state energy of lithium hydride at 1.6 angstrom using a minimal basis and compare the quantum result with a classical reference."
      />
      <div className="objective-actions">
        <PresetMenu presets={presets} disabled={loading} onSelect={onPresetChange} />
        <button className="primary-button" type="button" disabled aria-describedby="planning-status">Continue</button>
      </div>
      <p className="planning-status" id="planning-status">Natural-language experiment planning is not connected yet. Use a verified preset to explore the current workspace.</p>
    </section>
  )
}

function EmptyMolecularCanvas() {
  return (
    <section className="empty-molecular-canvas" aria-labelledby="empty-canvas-title">
      <svg viewBox="0 0 120 80" aria-hidden="true">
        <circle cx="36" cy="42" r="7" />
        <circle cx="82" cy="30" r="10" />
        <path d="M43 40 72 33" />
        <circle cx="66" cy="60" r="5" />
        <path d="m70 56 9-17" />
      </svg>
      <strong id="empty-canvas-title">Molecular scene will appear here after experiment generation.</strong>
      <span>Choose a verified preset to inspect an existing scientific structure.</span>
    </section>
  )
}

export function EmptyWorkspace({ presets, loading, onPresetChange }: {
  presets: PresetSummaryResponse[]
  loading: boolean
  onPresetChange: (identifier: string) => void
}) {
  return (
    <main className="empty-workspace" id="workspace-home">
      <ObjectiveComposer presets={presets} loading={loading} onPresetChange={onPresetChange} />
      <EmptyMolecularCanvas />
    </main>
  )
}
