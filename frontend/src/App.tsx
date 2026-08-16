import { ConditionalNavigation } from './components/ConditionalNavigation'
import { ErrorNotice } from './components/ErrorNotice'
import { ExistingRunInput } from './components/ExistingRunInput'
import { Header } from './components/Header'
import { MolecularViewer } from './components/MolecularViewer'
import { MolecularProjectInput } from './components/MolecularProjectInput'
import { MolecularProjectPanel } from './components/MolecularProjectPanel'
import { ResearchWorkspace } from './components/ResearchWorkspace'
import { ScientificPanel } from './components/ScientificPanel'
import { WorkflowWorkspace } from './components/WorkflowWorkspace'
import { deriveExistingRunHeaderStatus } from './existingRunHeaderStatus'
import { useExistingRun } from './hooks/useExistingRun'
import { useExperimentWorkspace } from './hooks/useExperimentWorkspace'
import { useMolecularProjectScene } from './hooks/useMolecularProjectScene'
import { useMolecularPlanning } from './hooks/useMolecularPlanning'
import { useResearchSession } from './hooks/useResearchSession'
import { usePresetRun } from './hooks/usePresetRun'
import { useWorkflowRun } from './hooks/useWorkflowRun'

export function App() {
  const workspace = useExperimentWorkspace()
  const existingRun = useExistingRun()
  const molecularProject = useMolecularProjectScene()
  const molecularPlanning = useMolecularPlanning(molecularProject.scene?.metadata.project_identifier ?? null)
  const research = useResearchSession()
  const workflow = useWorkflowRun()
  const coordinateScene = existingRun.scene ?? research.scene
  const displayedScene = molecularProject.scene ?? coordinateScene
  const hasScene = displayedScene !== null
  const readOnlyLookup = molecularProject.loading || molecularProject.scene !== null || existingRun.loading || existingRun.run !== null
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
  let errors = existingRun.error
    ? [...workspaceErrors, { scope: 'run' as const, message: existingRun.error }]
    : workspaceErrors
  if (molecularProject.error) errors = [...errors, { scope: 'scene' as const, message: molecularProject.error }]
  const selectPreset = (identifier: string) => {
    molecularProject.clear()
    existingRun.clearOpenedRun()
    workspace.selectPreset(identifier)
  }
  const existingRunHeaderStatus = deriveExistingRunHeaderStatus({
    loading: existingRun.loading,
    run: existingRun.run,
    results: existingRun.results,
    verification: existingRun.verification,
    receipt: existingRun.receipt,
  })
  const headerStatus = molecularProject.loading
    ? { primary: 'Read-only project', secondary: 'Opening scene' }
    : molecularProject.scene
      ? { primary: 'Native molecular project', secondary: 'Read-only verified resources' }
      : existingRunHeaderStatus

  const openRun = async () => {
    if (await existingRun.openRun()) molecularProject.clear()
  }
  const openProject = async () => {
    if (await molecularProject.open()) existingRun.clearOpenedRun()
  }

  return (
    <div className="app-shell">
      <Header runControl={<div className="read-only-open-controls">
        <ExistingRunInput
          value={existingRun.runIdentifierInput}
          loading={existingRun.loading}
          openedRunIdentifier={existingRun.run?.run_identifier ?? null}
          onChange={existingRun.setRunIdentifierInput}
          onOpen={() => void openRun()}
        />
        <MolecularProjectInput
          projectIdentifier={molecularProject.projectIdentifierInput}
          sceneIdentifier={molecularProject.sceneIdentifierInput}
          loading={molecularProject.loading}
          active={molecularProject.scene !== null}
          onProjectChange={molecularProject.setProjectIdentifierInput}
          onSceneChange={molecularProject.setSceneIdentifierInput}
          onOpen={() => void openProject()}
          onClear={molecularProject.clear}
        />
      </div>} status={headerStatus} />
      <ErrorNotice errors={errors} />
      <div className="workspace-content">
        <div className="workspace-frame">
        <ConditionalNavigation hasScene={hasScene} />
        {workspace.initialLoading && (existingRun.loading || molecularProject.loading) ? (
          <main className="initial-state" aria-live="polite">
            <span aria-hidden="true" />
            <strong>Opening the scientific workspace…</strong>
            <small>Connecting to the preset catalogue.</small>
          </main>
        ) : displayedScene ? (
          <main className="loaded-workspace" id="workspace-home">
            <MolecularViewer scene={displayedScene} loading={workspace.presetLoading || existingRun.loading || molecularProject.loading} />
            {molecularProject.scene ? <MolecularProjectPanel scene={molecularProject.scene} planning={molecularPlanning} /> : research.scene && !existingRun.scene ? (
              <ResearchWorkspace research={research} inspector />
            ) : <ScientificPanel
              scene={coordinateScene!}
              presets={workspace.presets}
              selectedPresetId={workspace.selectedPresetId}
              displayedPresetId={existingRun.run?.source_identifier ?? workspace.displayedPresetId}
              staleSceneMessage={existingRun.run ? null : workspace.staleSceneMessage}
              loading={workspace.presetLoading || existingRun.loading || molecularProject.loading}
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
            />}
          </main>
        ) : (
          <div className="research-layout">
            <ResearchWorkspace research={research} />
          </div>
        )}
        </div>
        <WorkflowWorkspace workflow={workflow} />
      </div>
    </div>
  )
}
