import { ConditionalNavigation } from './components/ConditionalNavigation'
import { EmptyInspector } from './components/EmptyInspector'
import { ErrorNotice } from './components/ErrorNotice'
import { ExistingRunInput } from './components/ExistingRunInput'
import { Header } from './components/Header'
import { MolecularViewer } from './components/MolecularViewer'
import { NaturalLanguageWorkspace } from './components/NaturalLanguageWorkspace'
import { ScientificPanel } from './components/ScientificPanel'
import { deriveExistingRunHeaderStatus } from './existingRunHeaderStatus'
import { useExistingRun } from './hooks/useExistingRun'
import { useExperimentWorkspace } from './hooks/useExperimentWorkspace'
import { useNaturalLanguageExperiment } from './hooks/useNaturalLanguageExperiment'
import { usePresetRun } from './hooks/usePresetRun'

export function App() {
  const workspace = useExperimentWorkspace()
  const existingRun = useExistingRun()
  const naturalLanguage = useNaturalLanguageExperiment()
  const displayedScene = existingRun.scene ?? workspace.scene
  const hasScene = displayedScene !== null
  const readOnlyLookup = existingRun.loading || existingRun.run !== null
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
  const workspaceErrors = presetRun.error && !existingRun.run
    ? [...workspace.errors, { scope: 'run' as const, message: presetRun.error }]
    : workspace.errors
  const errors = existingRun.error
    ? [...workspaceErrors, { scope: 'run' as const, message: existingRun.error }]
    : workspaceErrors
  const selectPreset = (identifier: string) => {
    existingRun.clearOpenedRun()
    workspace.selectPreset(identifier)
  }
  const headerStatus = deriveExistingRunHeaderStatus({
    loading: existingRun.loading,
    run: existingRun.run,
    results: existingRun.results,
    verification: existingRun.verification,
    receipt: existingRun.receipt,
  })

  return (
    <div className="app-shell">
      <Header runControl={<ExistingRunInput
        value={existingRun.runIdentifierInput}
        loading={existingRun.loading}
        openedRunIdentifier={existingRun.run?.run_identifier ?? null}
        onChange={existingRun.setRunIdentifierInput}
        onOpen={() => void existingRun.openRun()}
      />} status={headerStatus} />
      <ErrorNotice errors={errors} />
      <div className="workspace-frame">
        <ConditionalNavigation hasScene={hasScene} />
        {workspace.initialLoading ? (
          <main className="initial-state" aria-live="polite">
            <span aria-hidden="true" />
            <strong>Opening the scientific workspace…</strong>
            <small>Connecting to the preset catalogue.</small>
          </main>
        ) : displayedScene ? (
          <main className="loaded-workspace" id="workspace-home">
            <MolecularViewer scene={displayedScene} loading={workspace.presetLoading || existingRun.loading} />
            <ScientificPanel
              scene={displayedScene}
              presets={workspace.presets}
              selectedPresetId={workspace.selectedPresetId}
              displayedPresetId={existingRun.run?.source_identifier ?? workspace.displayedPresetId}
              staleSceneMessage={existingRun.run ? null : workspace.staleSceneMessage}
              loading={workspace.presetLoading || existingRun.loading}
              onPresetChange={selectPreset}
              presetRun={presetRun}
              openedRun={existingRun.run ? {
                run: existingRun.run,
                results: existingRun.results,
                verification: existingRun.verification,
                receipt: existingRun.receipt,
              } : null}
              readOnlyLookup={readOnlyLookup}
              plan={existingRun.run ? null : workspace.plan}
            />
          </main>
        ) : (
          <div className="empty-layout">
            <NaturalLanguageWorkspace
              presets={workspace.presets}
              loading={workspace.presetLoading}
              onPresetChange={selectPreset}
              naturalLanguage={naturalLanguage}
            />
            <EmptyInspector />
          </div>
        )}
      </div>
    </div>
  )
}
