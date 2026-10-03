import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { ResearchSessionWorkspace } from '../hooks/useResearchSession'
import type { VirtualInvestigation } from '../api/types'
import { VirtualInvestigationResult } from './VirtualInvestigationResult'

// Synthetic UI contracts only; these numbers are not scientific validation.
function fixture(): ResearchSessionWorkspace {
  const investigation: VirtualInvestigation = {
    policy_sha256: 'a'.repeat(64), prospective_policy: { cutoff: null, cutoff_source: null, allow_modern_general_knowledge: true },
    verification_scope: 'Synthetic integrity fixture, not Human safety', candidates: [{
      candidate_identifier: 'candidate-A', identity: { name: 'Fixture candidate', inchikey: 'fixture-key' },
      candidate_status: 'INSUFFICIENT EVIDENCE', assessment_reason: 'No qualified Human parameterization',
      inference_levels: { exposure: false, molecular_hypotheses: true, cellular_functional: false, organ_physiology: false, clinical: false },
      dossier_acquisition: { status: 'insufficient', missing: ['Reviewed plasma binding evidence'], conflicts: [], eligibility: { decisions: [] } },
      regimen: { status: 'requires_regimen', reason: 'No permissible dose' }, virtual_organism: null, exposure_activity: [],
      bioactivity_hypotheses: { targets: [{ target_accession: 'P00001', status: 'hypothesis',
        evidence: [{ neighbour_chembl_id: 'CHEMBL-FIXTURE', local_similarity: .7, classification: 'neighbour',
          quantitative_neighbour_evidence: { kind: 'Ki', value_umol_l: .003, uncertainty: 'Synthetic uncertainty' } }],
        tissue_relevance: { limitation: 'Expression is not a drug effect', go: [] } }] },
      functional_models: [{ status: 'refused', model_identifier: 'model-A', reason: 'Wrong species and no reviewed transfer' }],
    }],
  }
  return { visualization: { prospective_assessment: { virtual_investigation: investigation } } } as unknown as ResearchSessionWorkspace
}

describe('VirtualInvestigationResult', () => {
  it('separates neighbour measurements, missing Human exposure and refused models', () => {
    render(<VirtualInvestigationResult research={fixture()} />)
    expect(screen.getByRole('heading', { name: 'Virtual Investigation' })).toBeTruthy()
    expect(screen.getByText(/candidate-specific historical inputs are quarantined/)).toBeTruthy()
    expect(screen.getByText(/Activities below belong to unrelated neighbouring molecules/)).toBeTruthy()
    expect(screen.getByText(/Ki: 0.003000/)).toBeTruthy()
    expect(screen.getByText(/No nominal Human exposure curve was computed/)).toBeTruthy()
    expect(screen.getByText(/Docking scores were not converted to potency/)).toBeTruthy()
    expect(screen.getByText(/Wrong species and no reviewed transfer/)).toBeTruthy()
    expect(screen.queryByRole('img')).toBeNull()
  })
  it('renders only supplied numerical functional outputs at their declared inference level', () => {
    const research = fixture()
    research.visualization!.prospective_assessment!.virtual_investigation!.candidates[0].functional_models = [{ status: 'computed',
      exposure_case_identifier: 'synthetic-input-case-A',
      result: { model: { identifier: 'synthetic-model', name: 'Synthetic response', sha256: 'b'.repeat(64), species: 'Yeast' },
        inference_level: 'cellular_functional', response: { X: { unit: 'mol/l', baseline_final: 2, perturbed_final: 1, final_difference: -1, maximum_absolute_difference: 1 } },
        verification: { passed: true }, limitation: 'Not a Human organ consequence' } }]
    render(<VirtualInvestigationResult research={research} />)
    expect(screen.getByText(/Species: Yeast/)).toBeTruthy()
    expect(screen.getByText('2.0000')).toBeTruthy()
    expect(screen.getByText('-1.0000')).toBeTruthy()
    expect(screen.getByText('Not a Human organ consequence')).toBeTruthy()
    expect(screen.getByText(/Conditional native input case: synthetic-input-case-A/)).toBeTruthy()
    expect(screen.getByText(/does not establish independent scientific certification/)).toBeTruthy()
  })
  it('keeps saved sessions without the new assessment unchanged', () => {
    const { container } = render(<VirtualInvestigationResult research={{ visualization: null } as ResearchSessionWorkspace} />)
    expect(container.childElementCount).toBe(0)
  })
  it('shows the exact reviewed UTC cutoff rather than silently rounding to a day', () => {
    const research = fixture()
    research.visualization!.prospective_assessment!.virtual_investigation!.prospective_policy = {
      cutoff: '2000-01-01', cutoff_instant: '2000-01-01T23:59:00Z',
      cutoff_source: 'Synthetic review', allow_modern_general_knowledge: true,
    }
    render(<VirtualInvestigationResult research={research} />)
    expect(screen.getByText(/2000-01-01T23:59:00Z/)).toBeTruthy()
    expect(screen.getByText(/clinical and physiological observations are excluded regardless of date/)).toBeTruthy()
  })
  it('shows conditional ranges without implying a nominal prediction or clinical confidence', () => {
    const research = fixture()
    research.visualization!.prospective_assessment!.virtual_investigation!.candidates[0].native_sensitivity = {
      status: 'planned', design: { cases: [{ case_identifier: 'test-A' }], total_combinations: 2, exhaustive: false,
        limitation: 'All levels covered; unevaluated interactions remain unresolved.' },
      executions: [{ case_identifier: 'test-A', status: 'unsupported', reason: 'Native input missing' }],
      summary: { nominal_withheld: true, complete_discrete_space: false,
        endpoint_ranges: { 'Synthetic native endpoint': { peak_range_umol_l: [1,2], auc_range_umol_h_l: [3,4] } },
        qualitative_conclusion: 'No biological progression conclusion inferred from concentration ranges.' },
    }
    render(<VirtualInvestigationResult research={research} />)
    expect(screen.getByText('Conflicting-input sensitivity · nominal result withheld')).toBeTruthy()
    expect(screen.getByText(/unevaluated interactions remain unresolved/)).toBeTruthy()
    expect(screen.getByText('Computed exposure ranges, not clinical confidence intervals')).toBeTruthy()
    expect(screen.getByText('Native input missing')).toBeTruthy()
  })
  it('labels structural docking as uncalibrated evidence and preserves refusals', () => {
    const research = fixture()
    research.visualization!.prospective_assessment!.virtual_investigation!.bioactivity_structural = {
      status:'evaluated', screening:{candidates:[{candidate_identifier:'candidate-A',targets:[{
        target:{uniprot_accession:'P00002',pdb_id:'TEST',chain:'A'},status:'screened',
        docking:{vina_scores_kcal_per_mol:[-1]}}]}]},
      panel:{candidates:[{candidate_identifier:'candidate-A',excluded:[{target_accession:'P00003',reason:'No exact-species structure'}]}]},
    }
    render(<VirtualInvestigationResult research={research} />)
    expect(screen.getByText(/uncalibrated docking evidence only/)).toBeTruthy()
    expect(screen.getByText(/No exact-species structure/)).toBeTruthy()
    expect(screen.getByText(/Scores are not candidate potency/)).toBeTruthy()
  })
})
