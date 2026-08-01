import type { PluginContext } from 'molstar/lib/mol-plugin/context'
import { describe, expect, it, vi } from 'vitest'
import { fetchedResource, molecularTopologyFixture, projectedMolecularSceneFixture } from '../test/molecular-project-fixtures'
import { loadNativeProjectScene } from './native-molstar-loader'
import {
  resolveProjectRegions,
  validateLoadedStructure,
  type LoadedMolecularProjectScene,
  type LoadedMolecularStructure,
} from './native-project'

function loadedProjectScene(): LoadedMolecularProjectScene {
  const metadata = projectedMolecularSceneFixture()
  const orderedMetadata = [
    metadata.structures.find((entry) => entry.structure_identifier === metadata.primary_structure_identifier)!,
    ...metadata.structures.filter((entry) => entry.structure_identifier !== metadata.primary_structure_identifier),
  ]
  const structures: LoadedMolecularStructure[] = orderedMetadata.map((structure) => {
    const topology = molecularTopologyFixture(structure.structure_identifier, structure.native_format)
    return {
      metadata: structure,
      native: fetchedResource(new Uint8Array([1, 2, 3]), structure.source_artifact.artifact_identifier),
      topologyResource: fetchedResource(new Uint8Array([4, 5, 6]), structure.topology_artifact.artifact_identifier),
      topology,
      ...validateLoadedStructure(structure, topology),
    }
  })
  const resolved = resolveProjectRegions(metadata, structures)
  return {
    kind: 'native-project',
    metadata,
    structures,
    resolvedSelections: resolved.selections,
    resolvedRegions: resolved.regions,
  }
}

function fakePlugin(modelsPerStructure: number[] = [1, 1]) {
  const structures: unknown[] = []
  const models: object[] = []
  const labels: string[] = []
  const formats: string[] = []
  const componentCells: unknown[] = []
  let structureIndex = 0
  const clear = vi.fn(async () => { structures.splice(0) })
  const plugin = {
    clear,
    builders: {
      data: {
        rawData: vi.fn(async ({ label }: { label: string }) => {
          labels.push(label)
          return { label }
        }),
      },
      structure: {
        parseTrajectory: vi.fn(async (data: { label: string }, format: string) => {
          formats.push(format)
          return data
        }),
        hierarchy: {
          applyPreset: vi.fn(async () => {
            const count = modelsPerStructure[structureIndex] ?? 1
            const structureModels = Array.from({ length: count }, () => {
              const model = {
                atomicHierarchy: {
                  atomSourceIndex: { rowCount: 2, value: (row: number) => row },
                },
              }
              models.push(model)
              return model
            })
            structures.push({ cell: { obj: { data: { models: structureModels } } } })
            structureIndex += 1
          }),
        },
        tryCreateComponentFromExpression: vi.fn(async (cell: unknown) => {
          componentCells.push(cell)
          return { cell: { id: 'component' } }
        }),
        representation: { addRepresentation: vi.fn(async () => undefined) },
      },
    },
    managers: {
      structure: { hierarchy: { current: { structures } } },
      camera: { reset: vi.fn() },
    },
  }
  return {
    plugin: plugin as unknown as PluginContext,
    clear,
    labels,
    formats,
    models,
    componentCells,
    parseTrajectory: plugin.builders.structure.parseTrajectory,
    applyPreset: plugin.builders.structure.hierarchy.applyPreset,
  }
}

describe('native Mol* loader boundary', () => {
  it('loads two structures primary-first, preserves order, and scopes atom mappings', async () => {
    const scene = loadedProjectScene()
    const fake = fakePlugin()
    const lookup = new WeakMap<object, { structureIdentifier: string; atomIdsBySourceIndex: string[] }>()

    await expect(loadNativeProjectScene(fake.plugin, scene, lookup, () => true)).resolves.toBe(true)

    expect(fake.labels).toEqual(['structure-a', 'structure-b'])
    expect(fake.formats).toEqual(['pdb', 'mmcif'])
    expect(fake.applyPreset).toHaveBeenCalledTimes(2)
    expect(lookup.get(fake.models[0])?.structureIdentifier).toBe('structure-a')
    expect(lookup.get(fake.models[1])?.structureIdentifier).toBe('structure-b')
    expect(lookup.get(fake.models[0])?.atomIdsBySourceIndex).toEqual([
      'structure-a-atom-0', 'structure-a-atom-1',
    ])
    expect(fake.componentCells).toHaveLength(2)
    expect(fake.componentCells[0]).toBe(fake.componentCells[1])
  })

  it('fails visibly for an unsupported native parser format', async () => {
    const scene = loadedProjectScene()
    scene.structures[0].metadata.native_format = 'mol2' as never
    const fake = fakePlugin()
    await expect(loadNativeProjectScene(fake.plugin, scene, new WeakMap(), () => true))
      .rejects.toThrow('Unsupported native molecular format')
    expect(fake.clear).toHaveBeenCalledTimes(2)
  })

  it.each([['zero', 0], ['multiple', 2]] as const)(
    'rejects %s Mol* models for one declared structure',
    async (_label, modelCount) => {
      const fake = fakePlugin([modelCount])
      await expect(loadNativeProjectScene(fake.plugin, loadedProjectScene(), new WeakMap(), () => true))
        .rejects.toThrow('exactly one model')
      expect(fake.clear).toHaveBeenCalledTimes(2)
    },
  )

  it('clears the partially loaded plugin state when the second structure fails', async () => {
    const fake = fakePlugin()
    fake.parseTrajectory.mockImplementationOnce(async (data) => data)
      .mockRejectedValueOnce(new Error('second structure failed'))

    await expect(loadNativeProjectScene(fake.plugin, loadedProjectScene(), new WeakMap(), () => true))
      .rejects.toThrow('second structure failed')
    expect(fake.applyPreset).toHaveBeenCalledTimes(1)
    expect(fake.clear).toHaveBeenCalledTimes(2)
  })

  it('returns without publishing a stale generation', async () => {
    const fake = fakePlugin()
    let current = true
    fake.parseTrajectory.mockImplementationOnce(async (data) => {
      current = false
      return data
    })
    const lookup = new WeakMap<object, { structureIdentifier: string; atomIdsBySourceIndex: string[] }>()

    await expect(loadNativeProjectScene(fake.plugin, loadedProjectScene(), lookup, () => current)).resolves.toBe(false)

    expect(fake.applyPreset).not.toHaveBeenCalled()
    expect(fake.models).toHaveLength(0)
  })
})
