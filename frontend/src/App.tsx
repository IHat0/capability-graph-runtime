import { ConditionalNavigation } from './components/ConditionalNavigation'
import { EmptyInspector } from './components/EmptyInspector'
import { ErrorNotice } from './components/ErrorNotice'
import { Header } from './components/Header'
import { MolecularViewer } from './components/MolecularViewer'
import { NaturalLanguageWorkspace } from './components/NaturalLanguageWorkspace'
import { ScientificPanel } from './components/ScientificPanel'
import { useExperimentWorkspace } from './hooks/useExperimentWorkspace'
import { useNaturalLanguageExperiment } from './hooks/useNaturalLanguageExperiment'
import { usePresetRun } from './hooks/usePresetRun'

export function App() {
  const workspace = useExperimentWorkspace()
  const naturalLanguage = useNaturalLanguageExperiment()
  const hasScene = workspace.scene !== null
  const presetRun = usePresetRun({
    selectedPresetId: workspace.selectedPresetId,
    displayedPresetId: workspace.displayedPresetId,
    experimentIdentifier: workspace.scene?.experimentId,
    experimentFingerprint: workspace.scene?.experimentFingerprint,
    expectedExperimentSha256: workspace.scene?.expectedExperimentSha256,
    structureIdentifier: workspace.scene?.structureId,
    structureSha256: workspace.scene?.structureHash,
    experimentRecordIdentifier: workspace.plan?.ready_for_execution
      ? workspace.plan.experiment_identifier
      : null,
    executionTarget: workspace.plan?.ready_for_execution
      ? workspace.plan.requested_execution_target
      : 'local_simulator',
  })
  const errors = presetRun.error
    ? [...workspace.errors, { scope: 'run' as const, message: presetRun.error }]
    : workspace.errors

  return (
    <div className="app-shell">
      <Header />
      <ErrorNotice errors={errors} />
      <div className="workspace-frame">
        <ConditionalNavigation hasScene={hasScene} />
        {workspace.initialLoading ? (
          <main className="initial-state" aria-live="polite">
            <span aria-hidden="true" />
            <strong>Opening the scientific workspace…</strong>
            <small>Connecting to the preset catalogue.</small>
          </main>
        ) : workspace.scene ? (
          <main className="loaded-workspace" id="workspace-home">
            <MolecularViewer scene={workspace.scene} loading={workspace.presetLoading} />
            <ScientificPanel
              scene={workspace.scene}
              presets={workspace.presets}
              selectedPresetId={workspace.selectedPresetId}
              displayedPresetId={workspace.displayedPresetId}
              staleSceneMessage={workspace.staleSceneMessage}
              loading={workspace.presetLoading}
              onPresetChange={workspace.selectPreset}
              presetRun={presetRun}
              plan={workspace.plan}
            />
          </main>
        ) : (
          <div className="empty-layout">
            <NaturalLanguageWorkspace
              presets={workspace.presets}
              loading={workspace.presetLoading}
              onPresetChange={workspace.selectPreset}
              naturalLanguage={naturalLanguage}
            />
            <EmptyInspector />
          </div>
        )}
      </div>
    </div>
  )
}
