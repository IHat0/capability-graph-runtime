import { act, renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { ProjectedMolecularSceneMetadata, ProjectedMolecularStructureMetadata } from '../scene/native-project'
import { fetchedResource, molecularTopologyFixture, projectedMolecularSceneFixture } from '../test/molecular-project-fixtures'
import { type MolecularProjectSceneApi, useMolecularProjectScene } from './useMolecularProjectScene'

function topologyResource(structure: ProjectedMolecularStructureMetadata) {
  const bytes = new TextEncoder().encode(JSON.stringify(molecularTopologyFixture(structure.structure_identifier, structure.native_format)))
  return fetchedResource(bytes, structure.topology_artifact.artifact_identifier)
}

function api(overrides: Partial<MolecularProjectSceneApi> = {}): MolecularProjectSceneApi {
  return {
    getProjectedMolecularScene: vi.fn().mockResolvedValue(projectedMolecularSceneFixture()),
    getNativeMolecularStructure: vi.fn().mockImplementation(async (structure: ProjectedMolecularStructureMetadata) => fetchedResource(new Uint8Array([1, 2, 3]), structure.source_artifact.artifact_identifier)),
    getMolecularTopology: vi.fn().mockImplementation(async (structure: ProjectedMolecularStructureMetadata) => topologyResource(structure)),
    ...overrides,
  }
}

async function enterAndOpen(result: { current: ReturnType<typeof useMolecularProjectScene> }) {
  act(() => {
    result.current.setProjectIdentifierInput('project-native')
    result.current.setSceneIdentifierInput('scene-native')
  })
  let accepted = false
  await act(async () => { accepted = await result.current.open() })
  return accepted
}

describe('useMolecularProjectScene', () => {
  it('loads all resources with one read-only generation and primary structure first', async () => {
    const projectApi = api()
    const { result } = renderHook(() => useMolecularProjectScene(projectApi))

    expect(await enterAndOpen(result)).toBe(true)

    expect(result.current.error).toBeNull()
    expect(result.current.scene?.structures.map((entry) => entry.metadata.structure_identifier)).toEqual(['structure-a', 'structure-b'])
    expect(projectApi.getNativeMolecularStructure).toHaveBeenCalledTimes(2)
    expect(projectApi.getMolecularTopology).toHaveBeenCalledTimes(2)
    const signals = [
      vi.mocked(projectApi.getProjectedMolecularScene).mock.calls[0][2],
      ...vi.mocked(projectApi.getNativeMolecularStructure).mock.calls.map((call) => call[1]),
      ...vi.mocked(projectApi.getMolecularTopology).mock.calls.map((call) => call[1]),
    ]
    expect(new Set(signals).size).toBe(1)
    expect(result.current.scene?.resolvedRegions[0].atoms).toEqual([{ structureIdentifier: 'structure-a', atomIdentifier: 'structure-a-atom-0' }])
  })

  it('aborts on clear and ignores the late generation', async () => {
    let resolveMetadata!: (value: ReturnType<typeof projectedMolecularSceneFixture>) => void
    const pending = new Promise<ReturnType<typeof projectedMolecularSceneFixture>>((resolve) => { resolveMetadata = resolve })
    let signal: AbortSignal | undefined
    const projectApi = api({
      getProjectedMolecularScene: vi.fn((_project, _scene, nextSignal) => {
        signal = nextSignal
        return pending
      }),
    })
    const { result } = renderHook(() => useMolecularProjectScene(projectApi))
    act(() => {
      result.current.setProjectIdentifierInput('project-native')
      result.current.setSceneIdentifierInput('scene-native')
    })
    act(() => { void result.current.open() })
    await waitFor(() => expect(result.current.loading).toBe(true))

    act(() => result.current.clear())
    resolveMetadata(projectedMolecularSceneFixture())

    expect(signal?.aborted).toBe(true)
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.scene).toBeNull()
  })

  it('aborts and invalidates a pending generation when either identity input changes', async () => {
    let signal: AbortSignal | undefined
    const projectApi = api({
      getProjectedMolecularScene: vi.fn((_project, _scene, nextSignal) => {
        signal = nextSignal
        return new Promise<ProjectedMolecularSceneMetadata>(() => undefined)
      }),
    })
    const { result } = renderHook(() => useMolecularProjectScene(projectApi))
    act(() => {
      result.current.setProjectIdentifierInput('project-native')
      result.current.setSceneIdentifierInput('scene-native')
    })
    act(() => { void result.current.open() })
    await waitFor(() => expect(result.current.loading).toBe(true))

    act(() => result.current.setSceneIdentifierInput('scene-replacement'))

    expect(signal?.aborted).toBe(true)
    expect(result.current.loading).toBe(false)
    expect(result.current.scene).toBeNull()
  })

  it('fails atomically when one required structure fails', async () => {
    const projectApi = api({
      getNativeMolecularStructure: vi.fn(async (structure) => {
        if (structure.structure_identifier === 'structure-b') throw new Error('raw secret bytes')
        return fetchedResource(new Uint8Array([1]), structure.source_artifact.artifact_identifier)
      }),
    })
    const { result } = renderHook(() => useMolecularProjectScene(projectApi))

    await enterAndOpen(result)

    expect(result.current.scene).toBeNull()
    expect(result.current.error).toBe('The molecular project scene is unavailable or failed integrity validation.')
    expect(result.current.error).not.toContain('raw secret bytes')
  })

  it('preserves the previous valid scene after a failed replacement', async () => {
    const metadata = projectedMolecularSceneFixture()
    const getMetadata = vi.fn()
      .mockResolvedValueOnce(metadata)
      .mockRejectedValueOnce(new Error('replacement failed'))
    const projectApi = api({ getProjectedMolecularScene: getMetadata })
    const { result } = renderHook(() => useMolecularProjectScene(projectApi))
    await enterAndOpen(result)
    const previous = result.current.scene

    act(() => {
      result.current.setProjectIdentifierInput('project-other')
      result.current.setSceneIdentifierInput('scene-other')
    })
    await act(async () => result.current.open())

    expect(result.current.scene).toBe(previous)
    expect(result.current.error).not.toBeNull()
  })

  it('aborts its generation when unmounted', async () => {
    let signal: AbortSignal | undefined
    const projectApi = api({
      getProjectedMolecularScene: vi.fn((_project, _scene, nextSignal) => {
        signal = nextSignal
        return new Promise<ProjectedMolecularSceneMetadata>(() => undefined)
      }),
    })
    const { result, unmount } = renderHook(() => useMolecularProjectScene(projectApi))
    act(() => {
      result.current.setProjectIdentifierInput('project-native')
      result.current.setSceneIdentifierInput('scene-native')
    })
    act(() => { void result.current.open() })
    await waitFor(() => expect(result.current.loading).toBe(true))

    unmount()

    expect(signal?.aborted).toBe(true)
  })
})
