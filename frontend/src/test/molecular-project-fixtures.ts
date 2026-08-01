import type {
  FetchedMolecularResource,
  MolecularTopologyIndex,
  NativeStructureFormat,
  ProjectedMolecularSceneMetadata,
} from '../scene/native-project'

const SOURCE_HASH = 'a'.repeat(64)
const TOPOLOGY_HASH = 'b'.repeat(64)

function artifact(identifier: string, type: string, mediaType: string, hash: string, byteSize: number) {
  return {
    schema_version: { major: 1 as const, minor: 0 as const, patch: 0 as const },
    artifact_identifier: identifier,
    artifact_type: type,
    media_type: mediaType,
    content_sha256: hash,
    byte_size: byteSize,
  }
}

function structure(structureIdentifier: string, nativeFormat: NativeStructureFormat) {
  const project = 'project-native'
  const scene = 'scene-native'
  const query = `project_identifier=${project}&scene_identifier=${scene}&structure_identifier=${structureIdentifier}`
  return {
    structure_identifier: structureIdentifier,
    system_identifier: `system-${structureIdentifier}`,
    native_format: nativeFormat,
    coordinate_unit: 'angstrom' as const,
    atom_count: 2,
    bond_count: 0,
    residue_count: 0,
    chain_count: 0,
    model_count: 1,
    frame_count: 1,
    source_artifact: artifact(`source-${structureIdentifier}`, 'molecular_structure', nativeFormat === 'pdb' ? 'chemical/x-pdb' : 'chemical/x-mmcif', SOURCE_HASH, 3),
    topology_artifact: artifact(`topology-${structureIdentifier}`, 'molecular_topology', 'application/json', TOPOLOGY_HASH, 3),
    native_structure_url: `/api/v1/molecular/scenes/native-structure?${query}`,
    topology_url: `/api/v1/molecular/scenes/topology?${query}`,
  }
}

export function projectedMolecularSceneFixture(): ProjectedMolecularSceneMetadata {
  const structureB = structure('structure-b', 'mmcif')
  const structureA = structure('structure-a', 'pdb')
  return {
    schema_version: { major: 1, minor: 0, patch: 0 },
    project_identifier: 'project-native',
    scene_identifier: 'scene-native',
    primary_structure_identifier: 'structure-a',
    structures: [structureB, structureA],
    components: [{
      component_identifier: 'component-a',
      structure_identifier: 'structure-a',
      component_type: 'ligand',
      label: 'Ligand',
      members: [{ member_kind: 'atom', member_identifier: 'structure-a-atom-0' }],
    }],
    selections: [{
      selection_identifier: 'selection-a',
      structure_identifier: 'structure-a',
      label: 'Selected atom',
      selection_source: 'imported',
      members: [{ member_kind: 'component', member_identifier: 'component-a' }],
    }],
    regions: [{
      region_identifier: 'region-a',
      structure_identifier: 'structure-a',
      region_type: 'binding_pocket',
      selection_identifier: 'selection-a',
      parent_region_identifiers: [],
    }],
    artifact_references: [
      structureB.source_artifact,
      structureB.topology_artifact,
      structureA.source_artifact,
      structureA.topology_artifact,
    ],
  }
}

export function molecularTopologyFixture(
  structureIdentifier: string,
  structureFormat: NativeStructureFormat,
): MolecularTopologyIndex {
  return {
    schema_version: { major: 1, minor: 0, patch: 0 },
    structure_identifier: structureIdentifier,
    source_structure: {
      artifact_identifier: `source-${structureIdentifier}`,
      content_sha256: SOURCE_HASH,
    },
    structure_format: structureFormat,
    parser_identifier: 'fixture-parser',
    parser_version: '1.0.0',
    atoms: [0, 1].map((atomIndex) => ({
      atom_identifier: `${structureIdentifier}-atom-${atomIndex}`,
      atom_index: atomIndex,
      element_symbol: 'H',
      atom_name: `H${atomIndex + 1}`,
      model_index: 0,
      chain_identifier: null,
      residue_identifier: null,
      component_identifier: null,
      formal_charge: null,
      source_serial: String(atomIndex + 1),
    })),
    bonds: [],
    residues: [],
    chains: [],
    model_count: 1,
    frame_count: 1,
    coordinate_unit: 'angstrom',
  }
}

export function fetchedResource(bytes: Uint8Array, artifactIdentifier: string, contentSha256 = 'c'.repeat(64)): FetchedMolecularResource {
  return { bytes, mediaType: 'application/octet-stream', contentSha256, artifactIdentifier }
}
