import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { ResearchSessionWorkspace } from '../hooks/useResearchSession'
import { ResearchWorkspace } from './ResearchWorkspace'

function state(overrides: Partial<ResearchSessionWorkspace> = {}): ResearchSessionWorkspace {
  return {
    question: '', setQuestion: vi.fn(), reply: '', setReply: vi.fn(), session: null,
    attachments: [], scene: null, visualization: null, busy: false, error: null,
    acceptIntentProposal: false, setAcceptIntentProposal: vi.fn(),
    acceptRequirementProposal: false, setAcceptRequirementProposal: vi.fn(),
    acceptEvidenceProposal: false, setAcceptEvidenceProposal: vi.fn(),
    loadingSceneArtifact: null, visualizableArtifacts: [],
    selectedCandidateIdentifier: null, verifiedPropertiesOnly: false, setVerifiedPropertiesOnly: vi.fn(),
    start: vi.fn(), respond: vi.fn(), execute: vi.fn(), openScene: vi.fn(), openConformation: vi.fn(),
    focusCandidate: vi.fn(), compareCandidates: vi.fn(), exportArtifact: vi.fn(), exportVisualization: vi.fn(),
    addAttachments: vi.fn(), removeAttachment: vi.fn(), newSession: vi.fn(),
    ...overrides,
  }
}

describe('ResearchWorkspace', () => {
  it('starts from one natural-language question without a prerequisite checklist', () => {
    const research = state({ question: 'Investigate this system.' })
    render(<ResearchWorkspace research={research} />)

    fireEvent.submit(screen.getByRole('button', { name: 'Start research session' }).closest('form')!)

    expect(research.start).toHaveBeenCalledOnce()
    expect(screen.queryByText(/required fields/i)).toBeNull()
  })

  it('shows the follow-up conversation instead of a terminal failure', () => {
    const research = state({
      session: {
        session_identifier: `research-session-${'a'.repeat(32)}`,
        created_at: '2026-08-13T10:00:00Z', updated_at: '2026-08-13T10:00:00Z', revision: 1,
        status: 'awaiting_clarification',
        conversation: [{
          turn_identifier: 'turn-01', role: 'pulsate', content: 'Which conditions should be used?',
          created_at: '2026-08-13T10:00:00Z', requirement_identifier: 'requirement-conditions',
        }],
        input_references: [], artifact_references: [],
        unapproved_input_artifact_identifiers: [],
        next_questions: [{ requirement_identifier: 'requirement-conditions', question: 'Which conditions should be used?' }],
        clarification_attempts: { 'requirement-conditions': 1 },
        intent_proposal: null, requirement_proposal: null,
        accepted_research_requirements: null, evidence_proposal: null,
        accepted_partial_covalent_reaction_target: null, accepted_evidence: [],
        compilation: null, execution_status: null, scene_identifier: null,
        scientist_result: null, scientist_summary: 'I need one detail before planning.',
      },
      reply: 'Use the conditions stated above.',
    })
    render(<ResearchWorkspace research={research} />)

    expect(screen.getByText('Which conditions should be used?')).toBeTruthy()
    fireEvent.submit(screen.getByRole('button', { name: 'Continue' }).closest('form')!)
    expect(research.respond).toHaveBeenCalledOnce()
  })
})
