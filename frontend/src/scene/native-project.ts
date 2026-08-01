export const MOLECULAR_RESOURCE_MAXIMUM_BYTES = 128 * 1024 * 1024

const IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$/
const SHA256 = /^[0-9a-f]{64}$/
const MEDIA_TYPE = /^[A-Za-z0-9!#$&^_.+-]+\/[A-Za-z0-9!#$&^_.+-]+$/
const CREDENTIAL_KEY = /(?:^|[._-])(api[_-]?key|authorization|credential|crn|password|secret|token)(?:$|[._-])/i

export type NativeStructureFormat = 'pdb' | 'mmcif' | 'mol' | 'sdf' | 'xyz'
export type MolecularCoordinateUnit = 'angstrom' | 'bohr' | 'nanometer'

export interface MolecularSchemaVersion {
  major: 1
  minor: 0
  patch: 0
}

export interface MolecularArtifactIdentity {
  schema_version: MolecularSchemaVersion
  artifact_identifier: string
  artifact_type: string
  media_type: string
  content_sha256: string
  byte_size: number
}

export interface MolecularMemberReference {
  member_kind: 'atom' | 'residue' | 'chain' | 'component'
  member_identifier: string
}

export interface ProjectedMolecularComponent {
  component_identifier: string
  structure_identifier: string
  component_type: string
  label: string | null
  members: MolecularMemberReference[]
}

export interface ProjectedMolecularSelection {
  selection_identifier: string
  structure_identifier: string
  label: string | null
  selection_source: string
  members: MolecularMemberReference[]
}

export interface ProjectedMolecularRegion {
  region_identifier: string
  structure_identifier: string
  region_type: string
  selection_identifier: string
  parent_region_identifiers: string[]
}

export interface ProjectedMolecularStructureMetadata {
  structure_identifier: string
  system_identifier: string
  native_format: NativeStructureFormat
  coordinate_unit: MolecularCoordinateUnit
  atom_count: number
  bond_count: number
  residue_count: number
  chain_count: number
  model_count: number
  frame_count: number
  source_artifact: MolecularArtifactIdentity
  topology_artifact: MolecularArtifactIdentity
  native_structure_url: string
  topology_url: string
}

export interface ProjectedMolecularSceneMetadata {
  schema_version: MolecularSchemaVersion
  project_identifier: string
  scene_identifier: string
  primary_structure_identifier: string
  structures: ProjectedMolecularStructureMetadata[]
  components: ProjectedMolecularComponent[]
  selections: ProjectedMolecularSelection[]
  regions: ProjectedMolecularRegion[]
  artifact_references: MolecularArtifactIdentity[]
}

export interface MolecularTopologyAtom {
  atom_identifier: string
  atom_index: number
  element_symbol: string
  atom_name: string | null
  model_index: number
  chain_identifier: string | null
  residue_identifier: string | null
  component_identifier: string | null
  formal_charge: number | null
  source_serial: string | null
}

export interface MolecularTopologyBond {
  bond_identifier: string
  atom_identifier_a: string
  atom_identifier_b: string
  bond_order: number | null
  bond_type: string
}

export interface MolecularTopologyResidue {
  residue_identifier: string
  residue_name: string
  chain_identifier: string | null
  atom_identifiers: string[]
  source_sequence_identifier: string | null
}

export interface MolecularTopologyChain {
  chain_identifier: string
  atom_identifiers: string[]
  residue_identifiers: string[]
}

export interface MolecularTopologyIndex {
  schema_version: MolecularSchemaVersion
  structure_identifier: string
  source_structure: {
    artifact_identifier: string
    content_sha256: string
  }
  structure_format: NativeStructureFormat
  parser_identifier: string
  parser_version: string
  atoms: MolecularTopologyAtom[]
  bonds: MolecularTopologyBond[]
  residues: MolecularTopologyResidue[]
  chains: MolecularTopologyChain[]
  model_count: number
  frame_count: number
  coordinate_unit: MolecularCoordinateUnit
}

export interface FetchedMolecularResource {
  bytes: Uint8Array
  mediaType: string
  contentSha256: string
  artifactIdentifier: string
}

export interface LoadedMolecularStructure {
  metadata: ProjectedMolecularStructureMetadata
  native: FetchedMolecularResource
  topologyResource: FetchedMolecularResource
  topology: MolecularTopologyIndex
  atomIdsBySourceIndex: string[]
  sourceIndexByAtomId: Map<string, number>
}

export interface ResolvedProjectRegion {
  id: string
  label: string
  kind: 'selection' | 'region'
  atoms: Array<{ structureIdentifier: string; atomIdentifier: string }>
}

export interface LoadedMolecularProjectScene {
  kind: 'native-project'
  metadata: ProjectedMolecularSceneMetadata
  structures: LoadedMolecularStructure[]
  resolvedSelections: ResolvedProjectRegion[]
  resolvedRegions: ResolvedProjectRegion[]
}

function fail(message: string): never {
  throw new Error(message)
}

function record(value: unknown, label: string): Record<string, unknown> {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    fail(`Malformed ${label}.`)
  }
  return value as Record<string, unknown>
}

function exactKeys(value: Record<string, unknown>, keys: readonly string[], label: string) {
  const actual = Object.keys(value).sort()
  const expected = [...keys].sort()
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) {
    fail(`Malformed ${label}.`)
  }
}

function identifier(value: unknown, label: string): string {
  if (typeof value !== 'string' || !IDENTIFIER.test(value)) fail(`Malformed ${label}.`)
  return value
}

function hash(value: unknown, label: string): string {
  if (typeof value !== 'string' || !SHA256.test(value)) fail(`Malformed ${label}.`)
  return value
}

function integer(value: unknown, label: string, minimum = 0): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < minimum) fail(`Malformed ${label}.`)
  return value
}

function nullableString(value: unknown, label: string, maximumLength?: number): string | null {
  if (value === null) return null
  if (typeof value !== 'string' || value.trim().length === 0
    || (maximumLength !== undefined && value.length > maximumLength)) fail(`Malformed ${label}.`)
  return value
}

function requiredLabel(value: unknown, label: string, maximumLength: number): string {
  if (typeof value !== 'string' || value.trim().length === 0 || value.length > maximumLength) {
    fail(`Malformed ${label}.`)
  }
  return value
}

function schemaVersion(value: unknown): MolecularSchemaVersion {
  const item = record(value, 'molecular schema version')
  exactKeys(item, ['major', 'minor', 'patch'], 'molecular schema version')
  if (item.major !== 1 || item.minor !== 0 || item.patch !== 0) fail('Unsupported molecular schema version.')
  return { major: 1, minor: 0, patch: 0 }
}

function rejectCredentialFields(value: unknown) {
  if (Array.isArray(value)) {
    value.forEach(rejectCredentialFields)
    return
  }
  if (value === null || typeof value !== 'object') return
  for (const [key, nested] of Object.entries(value)) {
    if (CREDENTIAL_KEY.test(key)) fail('Molecular scene metadata contains a prohibited credential field.')
    rejectCredentialFields(nested)
  }
}

function artifact(value: unknown, label: string): MolecularArtifactIdentity {
  const item = record(value, label)
  exactKeys(item, ['schema_version', 'artifact_identifier', 'artifact_type', 'media_type', 'content_sha256', 'byte_size'], label)
  const mediaType = typeof item.media_type === 'string' && MEDIA_TYPE.test(item.media_type) ? item.media_type.toLowerCase() : fail(`Malformed ${label}.`)
  return {
    schema_version: schemaVersion(item.schema_version),
    artifact_identifier: identifier(item.artifact_identifier, `${label} identifier`),
    artifact_type: identifier(item.artifact_type, `${label} type`),
    media_type: mediaType,
    content_sha256: hash(item.content_sha256, `${label} hash`),
    byte_size: integer(item.byte_size, `${label} byte size`),
  }
}

function member(value: unknown): MolecularMemberReference {
  const item = record(value, 'molecular member reference')
  exactKeys(item, ['member_kind', 'member_identifier'], 'molecular member reference')
  if (!['atom', 'residue', 'chain', 'component'].includes(String(item.member_kind))) fail('Malformed molecular member kind.')
  return {
    member_kind: item.member_kind as MolecularMemberReference['member_kind'],
    member_identifier: identifier(item.member_identifier, 'molecular member identifier'),
  }
}

function members(value: unknown): MolecularMemberReference[] {
  if (!Array.isArray(value) || value.length === 0) fail('Malformed molecular members.')
  const parsed = value.map(member)
  uniqueBy(parsed, (entry) => `${entry.member_kind}:${entry.member_identifier}`, 'molecular member references')
  return parsed
}

function component(value: unknown): ProjectedMolecularComponent {
  const item = record(value, 'molecular component')
  exactKeys(item, ['component_identifier', 'structure_identifier', 'component_type', 'label', 'members'], 'molecular component')
  return {
    component_identifier: identifier(item.component_identifier, 'component identifier'),
    structure_identifier: identifier(item.structure_identifier, 'component structure identifier'),
    component_type: identifier(item.component_type, 'component type'),
    label: nullableString(item.label, 'component label', 512),
    members: members(item.members),
  }
}

function selection(value: unknown): ProjectedMolecularSelection {
  const item = record(value, 'molecular selection')
  exactKeys(item, ['selection_identifier', 'structure_identifier', 'label', 'selection_source', 'members'], 'molecular selection')
  return {
    selection_identifier: identifier(item.selection_identifier, 'selection identifier'),
    structure_identifier: identifier(item.structure_identifier, 'selection structure identifier'),
    label: nullableString(item.label, 'selection label', 512),
    selection_source: identifier(item.selection_source, 'selection source'),
    members: members(item.members),
  }
}

function region(value: unknown): ProjectedMolecularRegion {
  const item = record(value, 'molecular region')
  exactKeys(item, ['region_identifier', 'structure_identifier', 'region_type', 'selection_identifier', 'parent_region_identifiers'], 'molecular region')
  if (!Array.isArray(item.parent_region_identifiers)) fail('Malformed molecular parent regions.')
  const regionIdentifier = identifier(item.region_identifier, 'region identifier')
  const parentRegionIdentifiers = item.parent_region_identifiers.map((entry) => identifier(entry, 'parent region identifier'))
  uniqueBy(parentRegionIdentifiers, (entry) => entry, 'parent region identifiers')
  if (parentRegionIdentifiers.includes(regionIdentifier)) fail('Molecular region cannot be its own parent.')
  return {
    region_identifier: regionIdentifier,
    structure_identifier: identifier(item.structure_identifier, 'region structure identifier'),
    region_type: identifier(item.region_type, 'region type'),
    selection_identifier: identifier(item.selection_identifier, 'region selection identifier'),
    parent_region_identifiers: parentRegionIdentifiers,
  }
}

function validateResourceUrl(
  value: unknown,
  route: string,
  projectIdentifier: string,
  sceneIdentifier: string,
  structureIdentifier: string,
): string {
  if (typeof value !== 'string' || !value.startsWith(`${route}?`) || value.includes('#')) fail('Malformed molecular resource URL.')
  let parsed: URL
  try {
    parsed = new URL(value, 'https://pulsate.invalid')
  } catch {
    fail('Malformed molecular resource URL.')
  }
  if (parsed.origin !== 'https://pulsate.invalid' || parsed.username || parsed.password || parsed.pathname !== route) {
    fail('Unsafe molecular resource URL.')
  }
  if (/^[A-Za-z][A-Za-z0-9+.-]*:/.test(value) || value.startsWith('//')) fail('Unsafe molecular resource URL.')
  const expected = new Map([
    ['project_identifier', projectIdentifier],
    ['scene_identifier', sceneIdentifier],
    ['structure_identifier', structureIdentifier],
  ])
  if ([...parsed.searchParams.keys()].length !== expected.size) fail('Molecular resource URL identity mismatch.')
  for (const [key, expectedValue] of expected) {
    const values = parsed.searchParams.getAll(key)
    if (values.length !== 1 || values[0] !== expectedValue) fail('Molecular resource URL identity mismatch.')
  }
  return value
}

function projectedStructure(
  value: unknown,
  projectIdentifier: string,
  sceneIdentifier: string,
): ProjectedMolecularStructureMetadata {
  const item = record(value, 'projected molecular structure')
  exactKeys(item, [
    'structure_identifier', 'system_identifier', 'native_format', 'coordinate_unit',
    'atom_count', 'bond_count', 'residue_count', 'chain_count', 'model_count',
    'frame_count', 'source_artifact', 'topology_artifact', 'native_structure_url',
    'topology_url',
  ], 'projected molecular structure')
  const structureIdentifier = identifier(item.structure_identifier, 'structure identifier')
  if (!['pdb', 'mmcif', 'mol', 'sdf', 'xyz'].includes(String(item.native_format))) fail('Unsupported native molecular format.')
  if (!['angstrom', 'bohr', 'nanometer'].includes(String(item.coordinate_unit))) fail('Unsupported molecular coordinate unit.')
  const modelCount = integer(item.model_count, 'model count', 1)
  const frameCount = integer(item.frame_count, 'frame count', 1)
  if (modelCount !== 1 || frameCount !== 1) fail('Multi-model molecular resources are unsupported.')
  return {
    structure_identifier: structureIdentifier,
    system_identifier: identifier(item.system_identifier, 'system identifier'),
    native_format: item.native_format as NativeStructureFormat,
    coordinate_unit: item.coordinate_unit as MolecularCoordinateUnit,
    atom_count: integer(item.atom_count, 'atom count', 1),
    bond_count: integer(item.bond_count, 'bond count'),
    residue_count: integer(item.residue_count, 'residue count'),
    chain_count: integer(item.chain_count, 'chain count'),
    model_count: modelCount,
    frame_count: frameCount,
    source_artifact: artifact(item.source_artifact, 'source artifact'),
    topology_artifact: artifact(item.topology_artifact, 'topology artifact'),
    native_structure_url: validateResourceUrl(item.native_structure_url, '/api/v1/molecular/scenes/native-structure', projectIdentifier, sceneIdentifier, structureIdentifier),
    topology_url: validateResourceUrl(item.topology_url, '/api/v1/molecular/scenes/topology', projectIdentifier, sceneIdentifier, structureIdentifier),
  }
}

function uniqueBy<T>(items: T[], key: (item: T) => string, label: string) {
  const identities = items.map(key)
  if (new Set(identities).size !== identities.length) fail(`Duplicate ${label}.`)
}

export function parseProjectedMolecularScene(value: unknown): ProjectedMolecularSceneMetadata {
  rejectCredentialFields(value)
  const item = record(value, 'projected molecular scene')
  exactKeys(item, ['schema_version', 'project_identifier', 'scene_identifier', 'primary_structure_identifier', 'structures', 'components', 'selections', 'regions', 'artifact_references'], 'projected molecular scene')
  const projectIdentifier = identifier(item.project_identifier, 'project identifier')
  const sceneIdentifier = identifier(item.scene_identifier, 'scene identifier')
  if (!Array.isArray(item.structures) || item.structures.length === 0) fail('Malformed projected molecular structures.')
  if (!Array.isArray(item.components) || !Array.isArray(item.selections) || !Array.isArray(item.regions) || !Array.isArray(item.artifact_references)) fail('Malformed projected molecular scene collections.')
  const structures = item.structures.map((entry) => projectedStructure(entry, projectIdentifier, sceneIdentifier))
  const components = item.components.map(component)
  const selections = item.selections.map(selection)
  const regions = item.regions.map(region)
  const artifacts = item.artifact_references.map((entry) => artifact(entry, 'scene artifact'))
  uniqueBy(structures, (entry) => entry.structure_identifier, 'structure identifiers')
  uniqueBy(components, (entry) => entry.component_identifier, 'component identifiers')
  uniqueBy(selections, (entry) => entry.selection_identifier, 'selection identifiers')
  uniqueBy(regions, (entry) => entry.region_identifier, 'region identifiers')
  uniqueBy(artifacts, (entry) => entry.artifact_identifier, 'artifact identifiers')
  const primary = identifier(item.primary_structure_identifier, 'primary structure identifier')
  if (!structures.some((entry) => entry.structure_identifier === primary)) fail('Primary molecular structure is missing.')
  const structureIds = new Set(structures.map((entry) => entry.structure_identifier))
  if (components.some((entry) => !structureIds.has(entry.structure_identifier))
    || selections.some((entry) => !structureIds.has(entry.structure_identifier))
    || regions.some((entry) => !structureIds.has(entry.structure_identifier))) fail('Molecular scene member structure identity mismatch.')
  const selectionIds = new Set(selections.map((entry) => entry.selection_identifier))
  if (regions.some((entry) => !selectionIds.has(entry.selection_identifier))) fail('Molecular region selection identity mismatch.')
  const regionIds = new Set(regions.map((entry) => entry.region_identifier))
  if (regions.some((entry) => entry.parent_region_identifiers.some((parent) => !regionIds.has(parent)))) fail('Molecular parent region identity mismatch.')
  const selectionById = new Map(selections.map((entry) => [entry.selection_identifier, entry]))
  const regionById = new Map(regions.map((entry) => [entry.region_identifier, entry]))
  if (regions.some((entry) => selectionById.get(entry.selection_identifier)?.structure_identifier !== entry.structure_identifier
    || entry.parent_region_identifiers.some((parent) => regionById.get(parent)?.structure_identifier !== entry.structure_identifier))) {
    fail('Molecular region structure identity mismatch.')
  }
  const visitedRegions = new Set<string>()
  const visitingRegions = new Set<string>()
  const visitRegion = (regionIdentifier: string) => {
    if (visitingRegions.has(regionIdentifier)) fail('Molecular region parent graph is cyclic.')
    if (visitedRegions.has(regionIdentifier)) return
    visitingRegions.add(regionIdentifier)
    regionById.get(regionIdentifier)?.parent_region_identifiers.forEach(visitRegion)
    visitingRegions.delete(regionIdentifier)
    visitedRegions.add(regionIdentifier)
  }
  regions.forEach((entry) => visitRegion(entry.region_identifier))
  const artifactIdentities = new Map(artifacts.map((entry) => [entry.artifact_identifier, entry]))
  for (const structure of structures) {
    for (const declared of [structure.source_artifact, structure.topology_artifact]) {
      const projected = artifactIdentities.get(declared.artifact_identifier)
      if (!projected || projected.content_sha256 !== declared.content_sha256
        || projected.byte_size !== declared.byte_size || projected.media_type !== declared.media_type
        || projected.artifact_type !== declared.artifact_type) fail('Projected molecular artifact identity mismatch.')
    }
  }
  return {
    schema_version: schemaVersion(item.schema_version),
    project_identifier: projectIdentifier,
    scene_identifier: sceneIdentifier,
    primary_structure_identifier: primary,
    structures,
    components,
    selections,
    regions,
    artifact_references: artifacts,
  }
}

function stringArray(value: unknown, label: string, minimumLength = 0): string[] {
  if (!Array.isArray(value) || value.length < minimumLength) fail(`Malformed ${label}.`)
  const parsed = value.map((entry) => identifier(entry, label))
  uniqueBy(parsed, (entry) => entry, label)
  return parsed
}

export function parseMolecularTopology(value: unknown): MolecularTopologyIndex {
  rejectCredentialFields(value)
  const item = record(value, 'molecular topology')
  exactKeys(item, ['schema_version', 'structure_identifier', 'source_structure', 'structure_format', 'parser_identifier', 'parser_version', 'atoms', 'bonds', 'residues', 'chains', 'model_count', 'frame_count', 'coordinate_unit'], 'molecular topology')
  if (!Array.isArray(item.atoms) || item.atoms.length === 0 || !Array.isArray(item.bonds) || !Array.isArray(item.residues) || !Array.isArray(item.chains)) fail('Malformed molecular topology collections.')
  const source = record(item.source_structure, 'topology source structure')
  exactKeys(source, ['artifact_identifier', 'content_sha256'], 'topology source structure')
  if (!['pdb', 'mmcif', 'mol', 'sdf', 'xyz'].includes(String(item.structure_format))) fail('Unsupported topology structure format.')
  if (!['angstrom', 'bohr', 'nanometer'].includes(String(item.coordinate_unit))) fail('Unsupported topology coordinate unit.')
  const modelCount = integer(item.model_count, 'topology model count', 1)
  const frameCount = integer(item.frame_count, 'topology frame count', 1)
  if (modelCount !== 1 || frameCount !== 1) fail('Multi-model molecular topology is unsupported.')
  const atoms = item.atoms.map((entry): MolecularTopologyAtom => {
    const atom = record(entry, 'topology atom')
    exactKeys(atom, ['atom_identifier', 'atom_index', 'element_symbol', 'atom_name', 'model_index', 'chain_identifier', 'residue_identifier', 'component_identifier', 'formal_charge', 'source_serial'], 'topology atom')
    if (typeof atom.element_symbol !== 'string' || !/^[A-Z][a-z]{0,2}$/.test(atom.element_symbol)) fail('Malformed topology element symbol.')
    if (!(atom.formal_charge === null || Number.isInteger(atom.formal_charge))) fail('Malformed topology formal charge.')
    return {
      atom_identifier: identifier(atom.atom_identifier, 'topology atom identifier'),
      atom_index: integer(atom.atom_index, 'topology atom index'),
      element_symbol: atom.element_symbol,
      atom_name: nullableString(atom.atom_name, 'topology atom name', 128),
      model_index: integer(atom.model_index, 'topology atom model index'),
      chain_identifier: atom.chain_identifier === null ? null : identifier(atom.chain_identifier, 'topology atom chain'),
      residue_identifier: atom.residue_identifier === null ? null : identifier(atom.residue_identifier, 'topology atom residue'),
      component_identifier: atom.component_identifier === null ? null : identifier(atom.component_identifier, 'topology atom component'),
      formal_charge: atom.formal_charge as number | null,
      source_serial: nullableString(atom.source_serial, 'topology source serial', 128),
    }
  })
  uniqueBy(atoms, (entry) => entry.atom_identifier, 'topology atom identities')
  uniqueBy(atoms, (entry) => String(entry.atom_index), 'topology atom indices')
  if (atoms.some((entry) => entry.model_index !== 0)) fail('Topology atom model identity mismatch.')
  const orderedIndices = [...atoms].sort((left, right) => left.atom_index - right.atom_index).map((entry) => entry.atom_index)
  if (orderedIndices.some((entry, index) => entry !== index)) fail('Topology atom indices are not contiguous.')
  const atomIds = new Set(atoms.map((entry) => entry.atom_identifier))
  const bonds = item.bonds.map((entry): MolecularTopologyBond => {
    const bond = record(entry, 'topology bond')
    exactKeys(bond, ['bond_identifier', 'atom_identifier_a', 'atom_identifier_b', 'bond_order', 'bond_type'], 'topology bond')
    const left = identifier(bond.atom_identifier_a, 'topology bond atom')
    const right = identifier(bond.atom_identifier_b, 'topology bond atom')
    if (left === right || !atomIds.has(left) || !atomIds.has(right)) fail('Topology bond atom identity mismatch.')
    if (!(bond.bond_order === null || (typeof bond.bond_order === 'number' && Number.isFinite(bond.bond_order) && bond.bond_order > 0))) fail('Malformed topology bond order.')
    return { bond_identifier: identifier(bond.bond_identifier, 'topology bond identifier'), atom_identifier_a: left, atom_identifier_b: right, bond_order: bond.bond_order as number | null, bond_type: identifier(bond.bond_type, 'topology bond type') }
  })
  const residues = item.residues.map((entry): MolecularTopologyResidue => {
    const residue = record(entry, 'topology residue')
    exactKeys(residue, ['residue_identifier', 'residue_name', 'chain_identifier', 'atom_identifiers', 'source_sequence_identifier'], 'topology residue')
    const atomIdentifiers = stringArray(residue.atom_identifiers, 'residue atom identifier', 1)
    if (atomIdentifiers.some((entry) => !atomIds.has(entry))) fail('Topology residue atom identity mismatch.')
    return { residue_identifier: identifier(residue.residue_identifier, 'residue identifier'), residue_name: requiredLabel(residue.residue_name, 'residue name', 128), chain_identifier: residue.chain_identifier === null ? null : identifier(residue.chain_identifier, 'residue chain'), atom_identifiers: atomIdentifiers, source_sequence_identifier: nullableString(residue.source_sequence_identifier, 'residue source sequence', 128) }
  })
  const chains = item.chains.map((entry): MolecularTopologyChain => {
    const chain = record(entry, 'topology chain')
    exactKeys(chain, ['chain_identifier', 'atom_identifiers', 'residue_identifiers'], 'topology chain')
    const atomIdentifiers = stringArray(chain.atom_identifiers, 'chain atom identifier', 1)
    if (atomIdentifiers.some((entry) => !atomIds.has(entry))) fail('Topology chain atom identity mismatch.')
    return { chain_identifier: identifier(chain.chain_identifier, 'chain identifier'), atom_identifiers: atomIdentifiers, residue_identifiers: stringArray(chain.residue_identifiers, 'chain residue identifier') }
  })
  uniqueBy(bonds, (entry) => entry.bond_identifier, 'topology bond identities')
  uniqueBy(residues, (entry) => entry.residue_identifier, 'topology residue identities')
  uniqueBy(chains, (entry) => entry.chain_identifier, 'topology chain identities')
  const residueIds = new Set(residues.map((entry) => entry.residue_identifier))
  const chainIds = new Set(chains.map((entry) => entry.chain_identifier))
  if (residues.some((entry) => entry.chain_identifier !== null && !chainIds.has(entry.chain_identifier))
    || chains.some((entry) => entry.residue_identifiers.some((identity) => !residueIds.has(identity)))
    || atoms.some((entry) => entry.residue_identifier !== null && !residueIds.has(entry.residue_identifier))
    || atoms.some((entry) => entry.chain_identifier !== null && !chainIds.has(entry.chain_identifier))) fail('Molecular topology membership identity mismatch.')
  const residueById = new Map(residues.map((entry) => [entry.residue_identifier, entry]))
  const chainById = new Map(chains.map((entry) => [entry.chain_identifier, entry]))
  const atomById = new Map(atoms.map((entry) => [entry.atom_identifier, entry]))
  if (atoms.some((entry) => entry.residue_identifier !== null
      && !residueById.get(entry.residue_identifier)?.atom_identifiers.includes(entry.atom_identifier))
    || residues.some((entry) => entry.atom_identifiers.some((atomIdentifier) => atomById.get(atomIdentifier)?.residue_identifier !== entry.residue_identifier))
    || atoms.some((entry) => entry.chain_identifier !== null
      && !chainById.get(entry.chain_identifier)?.atom_identifiers.includes(entry.atom_identifier))
    || chains.some((entry) => entry.atom_identifiers.some((atomIdentifier) => atomById.get(atomIdentifier)?.chain_identifier !== entry.chain_identifier))
    || residues.some((entry) => entry.chain_identifier !== null
      && !chainById.get(entry.chain_identifier)?.residue_identifiers.includes(entry.residue_identifier))
    || chains.some((entry) => entry.residue_identifiers.some((residueIdentifier) => residueById.get(residueIdentifier)?.chain_identifier !== entry.chain_identifier))
    || residues.some((entry) => entry.atom_identifiers.some((atomIdentifier) => atomById.get(atomIdentifier)?.chain_identifier !== entry.chain_identifier))) {
    fail('Molecular topology membership is contradictory.')
  }
  return {
    schema_version: schemaVersion(item.schema_version),
    structure_identifier: identifier(item.structure_identifier, 'topology structure identifier'),
    source_structure: { artifact_identifier: identifier(source.artifact_identifier, 'topology source artifact'), content_sha256: hash(source.content_sha256, 'topology source hash') },
    structure_format: item.structure_format as NativeStructureFormat,
    parser_identifier: identifier(item.parser_identifier, 'topology parser identifier'),
    parser_version: identifier(item.parser_version, 'topology parser version'),
    atoms,
    bonds,
    residues,
    chains,
    model_count: modelCount,
    frame_count: frameCount,
    coordinate_unit: item.coordinate_unit as MolecularCoordinateUnit,
  }
}

export function validateLoadedStructure(
  metadata: ProjectedMolecularStructureMetadata,
  topology: MolecularTopologyIndex,
): { atomIdsBySourceIndex: string[]; sourceIndexByAtomId: Map<string, number> } {
  if (metadata.model_count !== 1
    || metadata.frame_count !== 1
    || topology.model_count !== 1
    || topology.frame_count !== 1
    || topology.atoms.some((atom) => atom.model_index !== 0)
    || topology.structure_identifier !== metadata.structure_identifier
    || topology.structure_format !== metadata.native_format
    || topology.coordinate_unit !== metadata.coordinate_unit
    || topology.atoms.length !== metadata.atom_count
    || topology.bonds.length !== metadata.bond_count
    || topology.residues.length !== metadata.residue_count
    || topology.chains.length !== metadata.chain_count
    || topology.model_count !== metadata.model_count
    || topology.frame_count !== metadata.frame_count
    || topology.source_structure.artifact_identifier !== metadata.source_artifact.artifact_identifier
    || topology.source_structure.content_sha256 !== metadata.source_artifact.content_sha256) fail('Molecular topology identity mismatch.')
  const atomIdsBySourceIndex = new Array<string>(metadata.atom_count)
  const sourceIndexByAtomId = new Map<string, number>()
  for (const atom of topology.atoms) {
    if (atom.atom_index < 0 || atom.atom_index >= metadata.atom_count || atomIdsBySourceIndex[atom.atom_index] !== undefined || sourceIndexByAtomId.has(atom.atom_identifier)) fail('Molecular topology atom mapping is unsafe.')
    atomIdsBySourceIndex[atom.atom_index] = atom.atom_identifier
    sourceIndexByAtomId.set(atom.atom_identifier, atom.atom_index)
  }
  if (atomIdsBySourceIndex.some((entry) => entry === undefined)) fail('Molecular topology atom mapping is incomplete.')
  return { atomIdsBySourceIndex, sourceIndexByAtomId }
}

export function resolveProjectRegions(
  metadata: ProjectedMolecularSceneMetadata,
  structures: LoadedMolecularStructure[],
): { selections: ResolvedProjectRegion[]; regions: ResolvedProjectRegion[] } {
  const structureById = new Map(structures.map((entry) => [entry.metadata.structure_identifier, entry]))
  const componentById = new Map(metadata.components.map((entry) => [entry.component_identifier, entry]))
  const resolveMembers = (structureIdentifier: string, entries: MolecularMemberReference[], visiting = new Set<string>()) => {
    const structure = structureById.get(structureIdentifier) ?? fail('Molecular member structure does not resolve.')
    const atomIds = new Set<string>()
    for (const entry of entries) {
      if (entry.member_kind === 'atom') {
        if (!structure.sourceIndexByAtomId.has(entry.member_identifier)) fail('Molecular member atom does not resolve.')
        atomIds.add(entry.member_identifier)
      } else if (entry.member_kind === 'residue') {
        const residue = structure.topology.residues.find((candidate) => candidate.residue_identifier === entry.member_identifier) ?? fail('Molecular member residue does not resolve.')
        residue.atom_identifiers.forEach((atom) => atomIds.add(atom))
      } else if (entry.member_kind === 'chain') {
        const chain = structure.topology.chains.find((candidate) => candidate.chain_identifier === entry.member_identifier) ?? fail('Molecular member chain does not resolve.')
        chain.atom_identifiers.forEach((atom) => atomIds.add(atom))
      } else {
        const component = componentById.get(entry.member_identifier) ?? fail('Molecular member component does not resolve.')
        if (component.structure_identifier !== structureIdentifier || visiting.has(component.component_identifier)) fail('Molecular component membership is unsafe.')
        const nextVisiting = new Set(visiting).add(component.component_identifier)
        resolveMembers(structureIdentifier, component.members, nextVisiting).forEach((atom) => atomIds.add(atom.atomIdentifier))
      }
    }
    return [...atomIds].map((atomIdentifier) => ({ structureIdentifier, atomIdentifier }))
  }
  for (const component of metadata.components) {
    resolveMembers(
      component.structure_identifier,
      component.members,
      new Set([component.component_identifier]),
    )
  }
  const selections = metadata.selections.map((entry) => ({
    id: entry.selection_identifier,
    label: entry.label ?? entry.selection_identifier,
    kind: 'selection' as const,
    atoms: resolveMembers(entry.structure_identifier, entry.members),
  }))
  const selectionById = new Map(selections.map((entry) => [entry.id, entry]))
  const regions = metadata.regions.map((entry) => {
    const selected = selectionById.get(entry.selection_identifier) ?? fail('Molecular region selection does not resolve.')
    if (metadata.selections.find((candidate) => candidate.selection_identifier === selected.id)?.structure_identifier !== entry.structure_identifier) fail('Molecular region structure identity mismatch.')
    return { id: entry.region_identifier, label: entry.region_identifier, kind: 'region' as const, atoms: selected.atoms }
  })
  return { selections, regions }
}
