import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { naturalLanguageInterpretation } from '../test/fixtures'
import type { ReturnTypeOfNaturalLanguageHook } from './natural-language-types'
import { NaturalLanguageWorkspace } from './NaturalLanguageWorkspace'

function state(overrides: Partial<ReturnTypeOfNaturalLanguageHook> = {}): ReturnTypeOfNaturalLanguageHook {
  return {
    question: '',
    updateQuestion: vi.fn(),
    interpretation: null,
    reviewedSpecification: null,
    updateReviewedSpecification: vi.fn(),
    acceptedAssumptions: false,
    setAcceptedAssumptions: vi.fn(),
    approvedExperiment: null,
    interpreting: false,
    approving: false,
    error: null,
    unresolvedFields: [],
    approvalDisabled: true,
    interpretQuestion: vi.fn(async () => undefined),
    approve: vi.fn(async () => undefined),
    ...overrides,
  }
}

describe('natural-language scientist workspace', () => {
  it('accepts a free-form question and presents interpretation as the primary action', () => {
    const updateQuestion = vi.fn()
    const interpretQuestion = vi.fn(async () => undefined)
    render(<NaturalLanguageWorkspace presets={[]} loading={false} onPresetChange={vi.fn()} naturalLanguage={state({
      question: 'Study a transition-metal complex on IBM Quantum',
      updateQuestion,
      interpretQuestion,
    })} />)
    fireEvent.change(screen.getByRole('textbox', { name: 'Natural-language chemistry question' }), {
      target: { value: 'A different free-form chemistry question' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Interpret question' }))
    expect(updateQuestion).toHaveBeenCalledWith('A different free-form chemistry question')
    expect(interpretQuestion).toHaveBeenCalledOnce()
    expect(screen.queryByRole('button', { name: /run locally/i })).toBeNull()
  })

  it('shows structured values, provenance, warnings, and requires acknowledgement', () => {
    const setAcceptedAssumptions = vi.fn()
    const updateReviewedSpecification = vi.fn()
    render(<NaturalLanguageWorkspace presets={[]} loading={false} onPresetChange={vi.fn()} naturalLanguage={state({
      question: naturalLanguageInterpretation.original_question,
      interpretation: naturalLanguageInterpretation,
      reviewedSpecification: naturalLanguageInterpretation.specification,
      updateReviewedSpecification,
      setAcceptedAssumptions,
    })} />)
    expect(screen.getByDisplayValue('lithium hydride')).toBeTruthy()
    expect(screen.getAllByText('Explicit').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Derived').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Assumed').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Missing').length).toBeGreaterThan(0)
    expect(screen.getAllByText(naturalLanguageInterpretation.original_question)).toHaveLength(2)
    expect((screen.getByRole('spinbutton', { name: 'Precision' }) as HTMLInputElement).value).toBe('0.015')
    expect((screen.getByRole('spinbutton', { name: 'Shots' }) as HTMLInputElement).value).toBe('')
    expect((screen.getByRole('textbox', {
      name: 'Review bond lengths and units as JSON',
    }) as HTMLTextAreaElement).value).toContain('"angstrom"')
    const checkbox = screen.getByRole('checkbox', { name: /accept the listed scientific assumptions/i })
    fireEvent.click(checkbox)
    expect(setAcceptedAssumptions).toHaveBeenCalledWith(true)
    fireEvent.change(screen.getByRole('textbox', { name: 'Review atom coordinates as JSON' }), {
      target: { value: '[{"element":"Li","coordinates":[-0.9,0,0]},{"element":"H","coordinates":[0.9,0,0]}]' },
    })
    expect(updateReviewedSpecification.mock.calls.at(-1)?.[0].molecule.atoms.provenance).toBe('explicit')
    expect((screen.getByRole('button', { name: 'Approve immutable experiment' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('displays immutable approval evidence and does not claim an IBM job was submitted', () => {
    render(<NaturalLanguageWorkspace presets={[]} loading={false} onPresetChange={vi.fn()} naturalLanguage={state({
      question: naturalLanguageInterpretation.original_question,
      interpretation: naturalLanguageInterpretation,
      reviewedSpecification: naturalLanguageInterpretation.specification,
      acceptedAssumptions: true,
      approvedExperiment: {
        schema_version: 'cgr.pulsate-approved-experiment/1.0.0',
        experiment_identifier: `experiment-${'c'.repeat(32)}`,
        interpretation_identifier: naturalLanguageInterpretation.interpretation_identifier,
        original_question: naturalLanguageInterpretation.original_question,
        specification: naturalLanguageInterpretation.specification,
        specification_sha256: 'd'.repeat(64),
        requested_execution_target: 'ibm_quantum',
        status: 'ready_for_ibm_submission',
        assumptions_accepted: true,
        scientist_reviewed_overrides: [],
        approved_at: '2026-07-23T00:00:00Z',
      },
    })} />)
    expect(screen.getByText(`experiment-${'c'.repeat(32)}`)).toBeTruthy()
    expect(screen.getByText('d'.repeat(64))).toBeTruthy()
    expect(screen.getByText('No IBM job has been submitted.')).toBeTruthy()
  })

  it('does not label a non-IBM pending approval as ready for IBM submission', () => {
    render(<NaturalLanguageWorkspace presets={[]} loading={false} onPresetChange={vi.fn()} naturalLanguage={state({
      question: naturalLanguageInterpretation.original_question,
      interpretation: naturalLanguageInterpretation,
      reviewedSpecification: naturalLanguageInterpretation.specification,
      approvedExperiment: {
        schema_version: 'cgr.pulsate-approved-experiment/1.0.0',
        experiment_identifier: `experiment-${'e'.repeat(32)}`,
        interpretation_identifier: naturalLanguageInterpretation.interpretation_identifier,
        original_question: naturalLanguageInterpretation.original_question,
        specification: naturalLanguageInterpretation.specification,
        specification_sha256: 'f'.repeat(64),
        requested_execution_target: 'local_simulator',
        status: 'approved_pending_compiler_support',
        assumptions_accepted: true,
        scientist_reviewed_overrides: ['requested_execution_target'],
        approved_at: '2026-07-23T00:00:00Z',
      },
    })} />)
    expect(screen.getByRole('heading', { name: 'Approved pending compiler support' })).toBeTruthy()
    expect(screen.queryByRole('heading', { name: 'Ready for IBM submission' })).toBeNull()
    expect(screen.getByText('local simulator')).toBeTruthy()
    expect(screen.queryByText('No IBM job has been submitted.')).toBeNull()
  })
})
