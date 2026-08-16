import { act, renderHook } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { PulsateApi } from '../api/client'
import type { ResearchSessionResponse } from '../api/types'
import { useResearchSession } from './useResearchSession'

const awaiting: ResearchSessionResponse = {
  session_identifier: `research-session-${'a'.repeat(32)}`,
  created_at: '2026-08-13T10:00:00Z',
  updated_at: '2026-08-13T10:00:00Z',
  revision: 1,
  status: 'awaiting_clarification',
  conversation: [
    {
      turn_identifier: 'turn-01',
      role: 'scientist',
      content: 'Compare these conformers.',
      created_at: '2026-08-13T10:00:00Z',
      requirement_identifier: null,
    },
    {
      turn_identifier: 'turn-02',
      role: 'pulsate',
      content: 'Which environment should be used?',
      created_at: '2026-08-13T10:00:00Z',
      requirement_identifier: 'requirement-environment',
    },
  ],
  input_references: [],
  artifact_references: [],
  unapproved_input_artifact_identifiers: [],
  next_questions: [{
    requirement_identifier: 'requirement-environment',
    question: 'Which environment should be used?',
  }],
  clarification_attempts: { 'requirement-environment': 1 },
  intent_proposal: null,
  requirement_proposal: null,
  accepted_research_requirements: null,
  evidence_proposal: null,
  accepted_partial_covalent_reaction_target: null,
  accepted_evidence: [],
  compilation: null,
  execution_status: null,
  scene_identifier: null,
  scientist_result: null,
  scientist_summary: 'More information is required.',
}

const planned: ResearchSessionResponse = {
  ...awaiting,
  revision: 2,
  status: 'planned',
  conversation: [
    ...awaiting.conversation,
    {
      turn_identifier: 'turn-03',
      role: 'scientist',
      content: 'Use the environment stated in my question.',
      created_at: '2026-08-13T10:01:00Z',
      requirement_identifier: null,
    },
  ],
  next_questions: [],
  compilation: {
    compilation_identifier: 'compilation-01',
    execution_identifier: `scientific-execution-${'b'.repeat(32)}`,
    effective_question: 'Compare these conformers. Use the stated environment.',
    canonical_objective: { objective_identifier: 'objective-01' },
    canonical_plan: { plan_identifier: 'plan-01' },
    canonical_graph: { graph_identifier: 'graph-01' },
  },
  execution_status: 'planned',
  scientist_summary: 'The canonical research graph is ready.',
}

function api(): PulsateApi {
  return {
    uploadResearchInput: vi.fn(),
    createResearchSession: vi.fn().mockResolvedValue(awaiting),
    getResearchSession: vi.fn(),
    replyResearchSession: vi.fn().mockResolvedValue(planned),
    executeResearchSession: vi.fn(),
    getResearchScene: vi.fn(),
    getResearchConformationScene: vi.fn(),
    getResearchComplexScene: vi.fn(),
    getResearchVisualization: vi.fn().mockImplementation(async (sessionIdentifier: string) => ({
      schema_version: 'pulsate.research-visualization/v1',
      session_identifier: sessionIdentifier,
      revision: 1,
      scene_identifier: null,
      structures: [], selections: [], interactions: [], overlays: [], candidates: [],
      lineage: [], comparisons: [], verification_artifact_identifiers: [], export_items: [],
      grounding_policy: 'persisted_artifact_or_deterministic_computation_only',
    })),
    downloadResearchArtifact: vi.fn(),
  } as unknown as PulsateApi
}

describe('useResearchSession', () => {
  it('keeps a clarification and reply in one durable session', async () => {
    const client = api()
    const hook = renderHook(() => useResearchSession(client))

    act(() => hook.result.current.setQuestion('Compare these conformers.'))
    await act(async () => hook.result.current.start())
    expect(hook.result.current.session?.status).toBe('awaiting_clarification')

    act(() => hook.result.current.setReply('Use the environment stated in my question.'))
    await act(async () => hook.result.current.respond())

    expect(client.replyResearchSession).toHaveBeenCalledWith(
      awaiting.session_identifier,
      'Use the environment stated in my question.',
      false,
      false,
      false,
      false,
      [],
      [],
      expect.any(AbortSignal),
    )
    expect(hook.result.current.session?.session_identifier).toBe(awaiting.session_identifier)
    expect(hook.result.current.session?.status).toBe('planned')
  })

  it('uploads exact evidence before creating the session', async () => {
    const client = api()
    const file = new File(['exact coordinates'], 'system.pdb', { type: 'chemical/x-pdb' })
    vi.mocked(client.uploadResearchInput).mockResolvedValue({
      inputReference: {
        reference_identifier: 'input-01-protein-structure',
        artifact_type: 'protein_structure',
        artifact_identifier: 'scientific-input-exact',
      },
      artifactReference: {
        artifact_identifier: 'scientific-input-exact',
        schema_version: { major: 1, minor: 0, patch: 0 },
        artifact_type: 'protein_structure',
        media_type: 'chemical/x-pdb',
        content_sha256: 'a'.repeat(64),
        byte_size: file.size,
        storage_location: null,
        metadata: {},
        provenance: {
          producer: 'pulsate-research-workspace',
          producer_version: { major: 1, minor: 0, patch: 0 },
          execution_identifier: null,
          source: 'cgr',
        },
        parents: [],
      },
    })
    const hook = renderHook(() => useResearchSession(client))

    act(() => {
      hook.result.current.setQuestion('Analyze the attached system.')
      hook.result.current.addAttachments(
        { 0: file, length: 1, item: () => file } as unknown as FileList,
        'protein_structure',
      )
    })
    await act(async () => hook.result.current.start())

    expect(client.uploadResearchInput).toHaveBeenCalledWith(
      { artifactType: 'protein_structure', file },
      1,
      expect.any(AbortSignal),
    )
    expect(client.createResearchSession).toHaveBeenCalledWith(
      'Analyze the attached system.',
      [expect.objectContaining({ artifact_identifier: 'scientific-input-exact' })],
      [expect.objectContaining({ artifact_identifier: 'scientific-input-exact' })],
      expect.any(AbortSignal),
    )
  })
})
