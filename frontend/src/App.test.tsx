import { act, fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { RunStateResponse } from './api/types'
import { App } from './App'
import { normalizeScene } from './scene/normalize'
import type { LoadedMolecularProjectScene } from './scene/native-project'
import type { MolecularScene } from './scene/types'
import { currentFixtureScene } from './test/fixtures'
import { projectedMolecularSceneFixture } from './test/molecular-project-fixtures'

const hooks = vi.hoisted(() => ({
  workspace: vi.fn(),
  existingRun: vi.fn(),
  molecularProject: vi.fn(),
  molecularPlanning: vi.fn(),
  research: vi.fn(),
  presetRun: vi.fn(),
}))

vi.mock('./hooks/useExperimentWorkspace', () => ({ useExperimentWorkspace: hooks.workspace }))
vi.mock('./hooks/useExistingRun', () => ({ useExistingRun: hooks.existingRun }))
vi.mock('./hooks/useMolecularProjectScene', () => ({ useMolecularProjectScene: hooks.molecularProject }))
vi.mock('./hooks/useMolecularPlanning', () => ({ useMolecularPlanning: hooks.molecularPlanning }))
vi.mock('./hooks/useResearchSession', () => ({ useResearchSession: hooks.research }))
vi.mock('./hooks/usePresetRun', () => ({ usePresetRun: hooks.presetRun }))
vi.mock('./components/Header', () => ({ Header: ({ runControl }: { runControl: React.ReactNode }) => <header>{runControl}</header> }))
vi.mock('./components/ErrorNotice', () => ({ ErrorNotice: () => null }))
vi.mock('./components/ConditionalNavigation', () => ({ ConditionalNavigation: () => null }))
vi.mock('./components/MolecularViewer', () => ({
  MolecularViewer: ({ scene }: { scene: { id?: string; kind?: string; metadata?: { project_identifier: string } } }) => (
    <div data-testid="displayed-source">
      {scene.kind === 'native-project' ? `project:${scene.metadata?.project_identifier}` : `coordinate:${scene.id}`}
    </div>
  ),
}))
vi.mock('./components/MolecularProjectPanel', () => ({ MolecularProjectPanel: () => <aside>Project inspector</aside> }))
vi.mock('./components/ScientificPanel', () => ({ ScientificPanel: () => <aside>Run evidence</aside> }))
vi.mock('./components/ResearchWorkspace', () => ({ ResearchWorkspace: () => null }))
vi.mock('./components/EmptyInspector', () => ({ EmptyInspector: () => null }))

const coordinateScene = normalizeScene(currentFixtureScene)
const runIdentifier = `run-${'1'.repeat(32)}`
const run = {
  run_identifier: runIdentifier,
  source_type: 'preset',
  source_identifier: 'preset-fixture',
  preset_identifier: 'preset-fixture',
  experiment_identifier: 'experiment-fixture',
  experiment_fingerprint: 'a'.repeat(64),
  expected_experiment_sha256: 'a'.repeat(64),
  structure_identifier: 'structure-fixture',
  execution_target: 'local_simulator',
  status: 'authorized',
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  status_url: `/api/v1/runs/${runIdentifier}`,
} satisfies RunStateResponse

function nativeScene(identifier = 'project-native'): LoadedMolecularProjectScene {
  return {
    kind: 'native-project',
    metadata: { ...projectedMolecularSceneFixture(), project_identifier: identifier },
    structures: [],
    resolvedSelections: [],
    resolvedRegions: [],
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((accept) => { resolve = accept })
  return { promise, resolve }
}

function states() {
  const workspace = {
    initialLoading: false, presetLoading: false, scene: coordinateScene,
    presets: [], selectedPresetId: 'preset-fixture', displayedPresetId: 'preset-fixture',
    staleSceneMessage: null, errors: [], plan: null, selectPreset: vi.fn(),
  }
  const existing = {
    runIdentifierInput: runIdentifier, setRunIdentifierInput: vi.fn(),
    run: run as RunStateResponse | null,
    scene: coordinateScene as MolecularScene | null,
    results: null, verification: null, receipt: null,
    loading: false, error: null, openRun: vi.fn(async () => false),
    clearOpenedRun: vi.fn(() => { existing.run = null; existing.scene = null }),
  }
  const project = {
    projectIdentifierInput: 'project-native', sceneIdentifierInput: 'scene-native',
    setProjectIdentifierInput: vi.fn(), setSceneIdentifierInput: vi.fn(),
    scene: null as LoadedMolecularProjectScene | null,
    loading: false, error: null, open: vi.fn(async () => false),
    clear: vi.fn(() => { project.scene = null }),
  }
  hooks.workspace.mockImplementation(() => workspace)
  hooks.existingRun.mockImplementation(() => existing)
  hooks.molecularProject.mockImplementation(() => project)
  hooks.molecularPlanning.mockReturnValue({ result: null, loading: false, error: null, evaluate: vi.fn() })
  hooks.research.mockReturnValue({ scene: null })
  hooks.presetRun.mockReturnValue({ error: null })
  return { workspace, existing, project }
}

beforeEach(() => vi.clearAllMocks())

describe('transactional workspace source switching', () => {
  it('preserves an existing run when project opening fails', async () => {
    const { existing, project } = states()
    const replacement = deferred<boolean>()
    project.open.mockReturnValue(replacement.promise)
    const view = render(<App />)

    fireEvent.click(screen.getByRole('button', { name: 'Open project' }))
    view.rerender(<App />)

    expect(screen.getByTestId('displayed-source').textContent).toBe(`coordinate:${coordinateScene.id}`)
    expect(screen.getByText('Run evidence')).toBeTruthy()
    await act(async () => replacement.resolve(false))
    view.rerender(<App />)

    expect(screen.getByTestId('displayed-source').textContent).toBe(`coordinate:${coordinateScene.id}`)
    expect(screen.getByText('Run evidence')).toBeTruthy()
    expect(existing.clearOpenedRun).not.toHaveBeenCalled()
  })

  it('replaces an existing run only after project opening succeeds', async () => {
    const { existing, project } = states()
    project.open.mockImplementation(async () => { project.scene = nativeScene(); return true })
    const view = render(<App />)

    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Open project' })))
    view.rerender(<App />)

    expect(existing.clearOpenedRun).toHaveBeenCalledOnce()
    expect(screen.getByTestId('displayed-source').textContent).toBe('project:project-native')
  })

  it('preserves a project when existing-run opening fails', async () => {
    const { existing, project } = states()
    project.scene = nativeScene()
    existing.run = null
    existing.scene = null
    const replacement = deferred<boolean>()
    existing.openRun.mockReturnValue(replacement.promise)
    const view = render(<App />)

    fireEvent.click(screen.getByRole('button', { name: 'Open run' }))
    view.rerender(<App />)

    expect(screen.getByTestId('displayed-source').textContent).toBe('project:project-native')
    await act(async () => replacement.resolve(false))
    view.rerender(<App />)

    expect(project.clear).not.toHaveBeenCalled()
    expect(screen.getByTestId('displayed-source').textContent).toBe('project:project-native')
  })

  it('replaces a project only after existing-run opening succeeds', async () => {
    const { existing, project } = states()
    project.scene = nativeScene()
    existing.run = null
    existing.scene = null
    existing.openRun.mockImplementation(async () => {
      existing.run = run
      existing.scene = coordinateScene
      return true
    })
    const view = render(<App />)

    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Open run' })))
    view.rerender(<App />)

    expect(project.clear).toHaveBeenCalledOnce()
    expect(screen.getByTestId('displayed-source').textContent).toBe(`coordinate:${coordinateScene.id}`)
  })

  it('does not clear a project after a late coordinate-scene completion', () => {
    const { workspace, project } = states()
    project.scene = nativeScene()
    const view = render(<App />)
    workspace.scene = { ...coordinateScene, id: 'late-coordinate-scene' }
    view.rerender(<App />)

    expect(project.clear).not.toHaveBeenCalled()
    expect(screen.getByTestId('displayed-source').textContent).toBe('project:project-native')
  })

  it('preserves the previous project when project-to-project replacement fails', async () => {
    const { project } = states()
    project.scene = nativeScene('project-previous')
    project.open.mockResolvedValue(false)
    const view = render(<App />)

    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Open project' })))
    view.rerender(<App />)

    expect(screen.getByTestId('displayed-source').textContent).toBe('project:project-previous')
  })
})
