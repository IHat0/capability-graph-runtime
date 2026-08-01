import { StructureElement } from 'molstar/lib/mol-model/structure'
import type { PluginContext } from 'molstar/lib/mol-plugin/context'
import { Color } from 'molstar/lib/mol-util/color'
import type { LoadedMolecularProjectScene } from './native-project'
import { nativeMolstarFormat, validateMolstarAtomSourceIndices } from './native-molstar'

export interface NativeAtomLookup {
  structureIdentifier: string
  atomIdsBySourceIndex: string[]
}

export async function loadNativeProjectScene(
  plugin: PluginContext,
  scene: LoadedMolecularProjectScene,
  modelLookup: WeakMap<object, NativeAtomLookup>,
  isCurrent: () => boolean,
): Promise<boolean> {
  if (!isCurrent()) return false
  await plugin.clear()
  if (!isCurrent()) return false
  const structureRefs = new Map<string, NonNullable<typeof plugin.managers.structure.hierarchy.current.structures[number]>>()
  try {
    for (const structure of scene.structures) {
      if (!isCurrent()) return false
      const format = nativeMolstarFormat(structure.metadata.native_format)
      const data = await plugin.builders.data.rawData({
        data: structure.native.bytes,
        label: structure.metadata.structure_identifier,
      })
      const trajectory = await plugin.builders.structure.parseTrajectory(data, format)
      if (!isCurrent()) return false
      await plugin.builders.structure.hierarchy.applyPreset(trajectory, 'default')
      if (!isCurrent()) return false
      const structureRef = plugin.managers.structure.hierarchy.current.structures.at(-1)
      const molstarStructure = structureRef?.cell.obj?.data
      if (!structureRef || !molstarStructure) throw new Error('Mol* did not create a declared native structure.')
      if (molstarStructure.models.length !== 1) {
        throw new Error('Mol* native structures must contain exactly one model.')
      }
      structureRefs.set(structure.metadata.structure_identifier, structureRef)
      const model = molstarStructure.models[0]
      const sourceIndices = model.atomicHierarchy.atomSourceIndex
      const mappedIndices: number[] = []
      for (let row = 0; row < sourceIndices.rowCount; row += 1) {
        mappedIndices.push(sourceIndices.value(row))
      }
      validateMolstarAtomSourceIndices(mappedIndices, structure.atomIdsBySourceIndex)
      modelLookup.set(model, {
        structureIdentifier: structure.metadata.structure_identifier,
        atomIdsBySourceIndex: structure.atomIdsBySourceIndex,
      })
    }
    for (const declared of [...scene.resolvedSelections, ...scene.resolvedRegions]) {
      const atomsByStructure = new Map<string, number[]>()
      for (const atom of declared.atoms) {
        const structure = scene.structures.find((entry) => entry.metadata.structure_identifier === atom.structureIdentifier)
        const sourceIndex = structure?.sourceIndexByAtomId.get(atom.atomIdentifier)
        if (sourceIndex === undefined) throw new Error('Declared molecular region atom mapping is unavailable.')
        const indices = atomsByStructure.get(atom.structureIdentifier) ?? []
        indices.push(sourceIndex)
        atomsByStructure.set(atom.structureIdentifier, indices)
      }
      for (const [structureIdentifier, indices] of atomsByStructure) {
        const structureRef = structureRefs.get(structureIdentifier)
        if (!structureRef) throw new Error('Declared molecular region structure is unavailable.')
        const expression = StructureElement.Schema.toExpression({ items: indices.map((atom_index) => ({ atom_index })) })
        const component = await plugin.builders.structure.tryCreateComponentFromExpression(
          structureRef.cell,
          expression,
          `pulsate-project-${declared.kind}-${declared.id}-${structureIdentifier}`,
          { label: declared.label, tags: ['pulsate-project-region', `pulsate-project-${declared.kind}`] },
        )
        if (component) {
          await plugin.builders.structure.representation.addRepresentation(component, {
            type: 'gaussian-surface',
            typeParams: { alpha: 0.2, radiusOffset: 0.22, resolution: 0.5, visuals: ['gaussian-surface-wireframe'] },
            color: 'uniform',
            colorParams: { value: Color(declared.kind === 'region' ? 0x267a52 : 0x3f6f9d) },
          })
        }
      }
    }
    if (!isCurrent()) return false
    plugin.managers.camera.reset(undefined, 0)
    return true
  } catch (error) {
    if (isCurrent()) await plugin.clear()
    throw error
  }
}
