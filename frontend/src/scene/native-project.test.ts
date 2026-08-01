import { describe, expect, it } from 'vitest'
import { molecularTopologyFixture, projectedMolecularSceneFixture } from '../test/molecular-project-fixtures'
import { parseMolecularTopology, parseProjectedMolecularScene } from './native-project'

function membershipTopology(): Record<string, unknown> {
  const topology = structuredClone(molecularTopologyFixture('structure-a', 'pdb')) as unknown as Record<string, unknown>
  topology.atoms = [0, 1, 2].map((index) => ({
    atom_identifier: `atom-${index}`,
    atom_index: index,
    element_symbol: 'H',
    atom_name: `H${index}`,
    model_index: 0,
    chain_identifier: 'chain-a',
    residue_identifier: index < 2 ? 'residue-a' : null,
    component_identifier: null,
    formal_charge: null,
    source_serial: String(index + 1),
  }))
  topology.residues = [{
    residue_identifier: 'residue-a',
    residue_name: 'RES',
    chain_identifier: 'chain-a',
    atom_identifiers: ['atom-0', 'atom-1'],
    source_sequence_identifier: '1',
  }]
  topology.chains = [{
    chain_identifier: 'chain-a',
    atom_identifiers: ['atom-0', 'atom-1', 'atom-2'],
    residue_identifiers: ['residue-a'],
  }]
  return topology
}

describe('native molecular project contracts', () => {
  it('accepts two independent single-model structures and retains declared order', () => {
    const metadata = parseProjectedMolecularScene(projectedMolecularSceneFixture())
    expect(metadata.structures.map((entry) => entry.structure_identifier)).toEqual(['structure-b', 'structure-a'])
    expect(metadata.primary_structure_identifier).toBe('structure-a')
  })

  it.each(['model_count', 'frame_count'] as const)('rejects projected %s greater than one', (field) => {
    const metadata = structuredClone(projectedMolecularSceneFixture())
    metadata.structures[0][field] = 2
    expect(() => parseProjectedMolecularScene(metadata)).toThrow('Multi-model')
  })

  it('rejects topology atoms outside the only supported model', () => {
    const topology = molecularTopologyFixture('structure-a', 'pdb')
    topology.atoms[0].model_index = 1
    expect(() => parseMolecularTopology(topology)).toThrow('model identity')
  })

  it.each(['model_count', 'frame_count'] as const)('rejects topology %s greater than one', (field) => {
    const topology = molecularTopologyFixture('structure-a', 'pdb')
    topology[field] = 2
    expect(() => parseMolecularTopology(topology)).toThrow('Multi-model')
  })

  it('accepts fully bidirectional atom, residue, and chain membership', () => {
    expect(() => parseMolecularTopology(membershipTopology())).not.toThrow()
  })

  it.each([
    ['atom missing from its residue', (topology: Record<string, unknown>) => {
      (topology.residues as Array<Record<string, unknown>>)[0].atom_identifiers = ['atom-1']
    }],
    ['residue containing an atom assigned elsewhere', (topology: Record<string, unknown>) => {
      (topology.residues as Array<Record<string, unknown>>)[0].atom_identifiers = ['atom-0', 'atom-1', 'atom-2']
    }],
    ['atom missing from its chain', (topology: Record<string, unknown>) => {
      (topology.chains as Array<Record<string, unknown>>)[0].atom_identifiers = ['atom-0', 'atom-1']
    }],
    ['chain containing an atom assigned elsewhere', (topology: Record<string, unknown>) => {
      (topology.atoms as Array<Record<string, unknown>>)[2].chain_identifier = null
    }],
    ['residue missing from its declared chain', (topology: Record<string, unknown>) => {
      (topology.chains as Array<Record<string, unknown>>)[0].residue_identifiers = []
    }],
    ['chain containing a residue assigned elsewhere', (topology: Record<string, unknown>) => {
      (topology.residues as Array<Record<string, unknown>>)[0].chain_identifier = null
    }],
    ['residue atom contradicting the residue chain', (topology: Record<string, unknown>) => {
      (topology.atoms as Array<Record<string, unknown>>)[0].chain_identifier = 'chain-b'
      ;(topology.chains as Array<Record<string, unknown>>)[0].atom_identifiers = ['atom-1', 'atom-2']
      ;(topology.chains as Array<Record<string, unknown>>).push({
        chain_identifier: 'chain-b', atom_identifiers: ['atom-0'], residue_identifiers: [],
      })
    }],
  ])('rejects contradictory topology membership: %s', (_label, mutate) => {
    const topology = membershipTopology()
    mutate(topology)
    expect(() => parseMolecularTopology(topology)).toThrow('contradictory')
  })
})
