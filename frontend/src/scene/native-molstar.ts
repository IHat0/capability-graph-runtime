import type { BuiltInTrajectoryFormat } from 'molstar/lib/mol-plugin-state/formats/trajectory'
import type { LoadedMolecularProjectScene } from './native-project'
import type { MolecularScene } from './types'

export const NATIVE_FORMAT_TO_MOLSTAR_FORMAT = Object.freeze<Record<string, BuiltInTrajectoryFormat>>({
  pdb: 'pdb',
  mmcif: 'mmcif',
  mol: 'mol',
  sdf: 'sdf',
  xyz: 'xyz',
})

export function isNativeProjectScene(scene: MolecularScene | LoadedMolecularProjectScene): scene is LoadedMolecularProjectScene {
  return 'kind' in scene && scene.kind === 'native-project'
}

export function nativeMolstarFormat(format: string): BuiltInTrajectoryFormat {
  const parser = NATIVE_FORMAT_TO_MOLSTAR_FORMAT[format]
  if (!parser) throw new Error(`Unsupported native molecular format: ${format}.`)
  return parser
}

export function validateMolstarAtomSourceIndices(
  sourceIndices: readonly number[],
  atomIdsBySourceIndex: readonly string[],
): void {
  if (sourceIndices.length !== atomIdsBySourceIndex.length
    || sourceIndices.some((index) => !Number.isSafeInteger(index) || index < 0 || index >= atomIdsBySourceIndex.length)
    || new Set(sourceIndices).size !== atomIdsBySourceIndex.length
    || new Set(atomIdsBySourceIndex).size !== atomIdsBySourceIndex.length) {
    throw new Error('Mol* native atom indices do not completely map to the validated topology.')
  }
}
