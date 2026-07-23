import { act, renderHook, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { WorkspaceApi } from '../api/client'
import { naturalLanguageInterpretation } from '../test/fixtures'
import { useNaturalLanguageExperiment } from './useNaturalLanguageExperiment'

function api(overrides: Partial<WorkspaceApi> = {}): WorkspaceApi {
  return {
    getHealth: async () => ({ service: 'pulsate-api', status: 'healthy', version: 'test' }),
    getPresets: async () => ({ presets: [], count: 0 }),
    getPreset: async () => ({ preset_identifier: 'unused', manifest: { experiment: {} } }),
    getScene: async () => ({ scene_identifier: 'unused', coordinate_unit: 'angstrom', atoms: [] }),
    ...overrides,
  }
}

describe('natural-language experiment flow', () => {
  it('calls interpretation, requires assumption acknowledgement, and approves without starting a job', async () => {
    const interpretQuestion = vi.fn(async () => structuredClone(naturalLanguageInterpretation))
    const approveInterpretation = vi.fn(async (identifier, specification, accepted) => ({
      schema_version: 'cgr.pulsate-approved-experiment/1.0.0',
      experiment_identifier: `experiment-${'b'.repeat(32)}`,
      interpretation_identifier: identifier,
      original_question: specification.original_question,
      specification,
      specification_sha256: 'f'.repeat(64),
      requested_execution_target: 'ibm_quantum' as const,
      status: 'ready_for_ibm_submission' as const,
      assumptions_accepted: accepted as true,
      scientist_reviewed_overrides: [],
      approved_at: '2026-07-23T00:00:00Z',
    }))
    const { result } = renderHook(() => useNaturalLanguageExperiment(api({
      interpretQuestion,
      approveInterpretation,
    })))

    act(() => result.current.updateQuestion(naturalLanguageInterpretation.original_question))
    await act(async () => result.current.interpretQuestion())
    expect(interpretQuestion).toHaveBeenCalledWith(
      naturalLanguageInterpretation.original_question,
      expect.any(AbortSignal),
    )
    expect(result.current.reviewedSpecification?.molecule.name.value).toBe('lithium hydride')
    expect(result.current.approvalDisabled).toBe(true)

    act(() => result.current.setAcceptedAssumptions(true))
    expect(result.current.approvalDisabled).toBe(false)
    await act(async () => result.current.approve())
    await waitFor(() => expect(result.current.approvedExperiment?.experiment_identifier).toMatch(/^experiment-/))
    expect(approveInterpretation).toHaveBeenCalledOnce()

    const edited = structuredClone(result.current.reviewedSpecification!)
    edited.charge = { value: 1, provenance: 'explicit' }
    act(() => result.current.updateReviewedSpecification(edited))
    expect(result.current.acceptedAssumptions).toBe(false)
    expect(result.current.approvedExperiment).toBeNull()

    act(() => result.current.updateQuestion('A changed scientific question'))
    expect(result.current.interpretation).toBeNull()
    expect(result.current.reviewedSpecification).toBeNull()
    expect(result.current.approvalDisabled).toBe(true)
  })

  it('keeps approval disabled when required information is missing', async () => {
    const incomplete = structuredClone(naturalLanguageInterpretation)
    incomplete.specification.molecule.atoms = { value: null, provenance: 'missing' }
    incomplete.specification.missing_required_information = ['geometry']
    const { result } = renderHook(() => useNaturalLanguageExperiment(api({
      interpretQuestion: async () => incomplete,
      approveInterpretation: vi.fn(),
    })))
    incomplete.original_question = 'Study caffeine on IBM Quantum'
    incomplete.specification.original_question = incomplete.original_question
    act(() => result.current.updateQuestion(incomplete.original_question))
    await act(async () => result.current.interpretQuestion())
    act(() => result.current.setAcceptedAssumptions(true))
    expect(result.current.unresolvedFields).toContain('geometry')
    expect(result.current.approvalDisabled).toBe(true)
  })

  it('keeps approval disabled for deterministic scientific validation failures', async () => {
    const invalid = structuredClone(naturalLanguageInterpretation)
    invalid.specification.tolerance = { value: -1, provenance: 'explicit' }
    const { result } = renderHook(() => useNaturalLanguageExperiment(api({
      interpretQuestion: async () => invalid,
      approveInterpretation: vi.fn(),
    })))
    invalid.original_question = 'A complete but invalid chemistry question'
    invalid.specification.original_question = invalid.original_question
    act(() => result.current.updateQuestion(invalid.original_question))
    await act(async () => result.current.interpretQuestion())
    act(() => result.current.setAcceptedAssumptions(true))
    expect(result.current.unresolvedFields).toContain('invalid_tolerance')
    expect(result.current.approvalDisabled).toBe(true)
  })

  it('rejects invalid atom symbols and invalid optional execution controls', async () => {
    const invalid = structuredClone(naturalLanguageInterpretation)
    invalid.original_question = 'Review an invalid scientific draft'
    invalid.specification.original_question = invalid.original_question
    invalid.specification.molecule.atoms.value![0].element = 'NotAnElement'
    invalid.specification.shots = { value: 1.5, provenance: 'explicit' }
    invalid.specification.precision = { value: 0, provenance: 'explicit' }
    const { result } = renderHook(() => useNaturalLanguageExperiment(api({
      interpretQuestion: async () => invalid,
      approveInterpretation: vi.fn(),
    })))
    act(() => result.current.updateQuestion(invalid.original_question))
    await act(async () => result.current.interpretQuestion())
    expect(result.current.unresolvedFields).toEqual(expect.arrayContaining([
      'invalid_geometry',
      'invalid_shots',
      'invalid_precision',
    ]))
    expect(result.current.approvalDisabled).toBe(true)
  })
})
