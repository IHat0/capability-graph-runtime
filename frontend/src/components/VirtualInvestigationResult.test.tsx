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
  it('shows functional actions separately from binding and refuses unsupported numbers',()=>{
    const research=fixture()
    research.visualization!.prospective_assessment!.virtual_investigation!.candidates[0].functional_activity_prediction={
      schema:'pulsate.functional-activity-prediction/v1',scope:'Synthetic rendering contract only',
      universe_sha256:'a'.repeat(64),manifest_sha256:'b'.repeat(64),
      coverage:{supported_model_targets:1,unsupported_model_targets:1,accepted_candidate_targets:1,universe_targets:2},
      unresolved_source_rows:[{label:'Unspecified complex',status:'unsupported_identity',reason:'Subunits unknown'}],
      targets:[{gene:'GENEA',target_accession:'P00001',status:'predicted',action:'inhibitor',kind:'IC50',readout:'enzymatic_activity',
        classification:'predicted',concentration_basis:'assay_nominal',value_umol_l:.2,interval_umol_l:[.05,.8],
        applicability:{status:'in_domain',training_graph_seen:false,nearest_training_tanimoto:.7},
        interval_definition:'Synthetic marginal interval',limitation:'Not measured functional activity'},
        {gene:'GENEB',target_accession:'P00002',status:'unsupported',reason:'No sufficient Human data'}]}
    render(<VirtualInvestigationResult research={research}/>)
    expect(screen.getByRole('heading',{name:'Functional pharmacology · predicted, not measured'})).toBeTruthy()
    expect(screen.getByText('1 supported functional targets · 1 unsupported targets.')).toBeTruthy()
    expect(screen.getByText(/Predicted: 0.20000/)).toBeTruthy()
    expect(screen.getByText(/inhibitor · IC50/)).toBeTruthy()
    expect(screen.getByText(/unsupported: No sufficient Human data/)).toBeTruthy()
    expect(screen.getByText(/Nominal assay potency is not free Human-tissue potency/)).toBeTruthy()
    expect(screen.getByText(/Unspecified complex — Subunits unknown/)).toBeTruthy()
  })
  it('prominently shows exploratory physiology and its failed benchmark without implying candidate risk',()=>{
    const research=fixture()
    research.visualization!.prospective_assessment!.virtual_investigation!.candidates[0].functional_models=[{
      status:'computed',result:{model:{identifier:'synthetic',name:'Synthetic native model',sha256:'a'.repeat(64),species:'Human'},
        inference_level:'cellular_functional',verification:{passed:true},limitation:'Synthetic UI evidence only',
        response:{'V.APD90':{unit:'ms',baseline_final:1,perturbed_final:2,final_difference:1,maximum_absolute_difference:1}},
        perturbation:{transfer_receipt:{qualified:false,qualification_state:'exploratory_only',candidate_decision_authority:false,
          qualification_scope:{benchmark_discrepancy:'Synthetic tissue benchmark mismatch',limitations:['Unresolved translation uncertainty'],
            benchmarks:[{identifier:'Synthetic benchmark',maximum_absolute_error:9,unit:'ms',error_budget:1,biological_error_budget_passed:false}]}}}}}]
    render(<VirtualInvestigationResult research={research}/>)
    expect(screen.getByRole('heading',{name:'Exploratory physiology only — not a qualified prediction'})).toBeTruthy()
    expect(screen.getByText(/cannot independently change the candidate status/)).toBeTruthy()
    expect(screen.getByText('Synthetic tissue benchmark mismatch')).toBeTruthy()
    expect(screen.getByText(/maximum observed-benchmark discrepancy 9.0000 ms/)).toBeTruthy()
    expect(screen.getByText('Unresolved translation uncertainty')).toBeTruthy()
  })
  it('does not turn estimated free exposure endpoints into measured tissue curves or invented times', () => {
    const research = fixture()
    research.visualization!.prospective_assessment!.virtual_investigation!.candidates[0].reviewed_exposure_snapshots = [{
      species:'Human',organ:'Peripheral Venous Blood',compartment:'Plasma Unbound',values_umol_l:[.002],times_h:[null],
      classification:'sourced',timecourse_available:false,path:'https://example.invalid/original',
      uncertainty:'Synthetic estimated exposure, not measured tissue',applicability:'Contract test only',
      limitation:'No timecourse',compartment_translation:null,
      datum:{identifier:'synthetic-exposure',original_source:{source_sha256:'e'.repeat(64)}}}]
    render(<VirtualInvestigationResult research={research} />)
    expect(screen.getByRole('region',{name:'Published Human exposure endpoints'})).toBeTruthy()
    expect(screen.getByText(/Not reported; no timestamp invented/)).toBeTruthy()
    expect(screen.getByText('No plasma-to-tissue equivalence has been assumed.')).toBeTruthy()
    expect(screen.getByText(/estimated free concentration is not a measured tissue concentration/)).toBeTruthy()
  })
  it('keeps measured functional assays distinct and refuses to label nominal bath evidence as Human function',()=>{
    const research=fixture()
    research.visualization!.prospective_assessment!.virtual_investigation!.candidates[0].quantitative_activity=[{
      functional_assay_sha256:'f'.repeat(64),target_accession:'PTEST',species:'Human',kind:'IC50',value:.1,unit:'umol/l',
      assay_type:'functional',assay_host:'Synthetic CHO test',functional_direction:'blocker',concentration_basis:'assay_nominal',
      assay_context:'Synthetic component fixture only',hill_coefficient:1.2,functional_transfer_supported:false,
      uncertainty:'Free bath exposure is not established',source:'https://example.invalid/fixture',source_sha256:'a'.repeat(64),
      datum:{original_source:{source_sha256:'b'.repeat(64)}}}]
    render(<VirtualInvestigationResult research={research} />)
    expect(screen.getByRole('region',{name:'Measured pharmacological assays'})).toBeTruthy()
    expect(screen.getByText('PTEST · Human · functional IC50: 0.10000 umol/l')).toBeTruthy()
    expect(screen.getByText(/Established action: blocker · Hill coefficient: 1.2/)).toBeTruthy()
    expect(screen.getByText(/Concentration basis: assay nominal/)).toBeTruthy()
    expect(screen.getByText('Not eligible for physiological transfer under the current pharmacology contract.')).toBeTruthy()
    expect(screen.getByText(/Original source SHA-256 bbbbb/)).toBeTruthy()
  })
  it('shows predicted Ki intervals but never replaces refused targets with numeric values', () => {
    const research=fixture()
    research.visualization!.prospective_assessment!.virtual_investigation!.candidates[0].target_activity_prediction={
      scope:'Synthetic research-binding contract only',manifest_sha256:'c'.repeat(64),panel_sha256:'d'.repeat(64),
      targets:[{gene:'GENEA',target_accession:'P00001',status:'predicted',kind:'Ki',unit:'umol/l',value_umol_l:.1,
        interval_umol_l:[.01,1],interval_definition:'Synthetic marginal interval',concentration_basis:'assay_nominal',
        functional_direction:null,uncertainty:'Synthetic calibration only',applicability:{status:'in_domain',training_graph_seen:false,nearest_training_tanimoto:.8},
        research_gate:{accepted:true,failed_metrics:[],scope:'Not independent biological qualification',metrics:{n:30,mae:.4,rmse:.5,interval_coverage:.9,spearman:.6}},model_sha256:'e'.repeat(64)},
        {gene:'GENEB',target_accession:'P00002',status:'refused',reason:'Synthetic failed model gate'}],
    }
    render(<VirtualInvestigationResult research={research} />)
    expect(screen.getByText('Candidate binding hypotheses · not measured potency')).toBeTruthy()
    expect(screen.getByText(/Predicted Ki: 0.10000/)).toBeTruthy()
    expect(screen.getByText(/Not predicted: refused. Synthetic failed model gate/)).toBeTruthy()
    expect(screen.getByText(/not functional IC50, unbound tissue potency, occupancy/)).toBeTruthy()
    expect(screen.getByText(/Internal held-out research gate: passed/)).toBeTruthy()
    expect(screen.getByText(/Not independent biological qualification/)).toBeTruthy()
    expect(screen.getByRole('region',{name:'Binding estimates and validation'}).getAttribute('tabindex')).toBe('0')
  })
  it('retains untested general targets without implying potency or negative results', () => {
    const research = fixture()
    research.visualization!.prospective_assessment!.virtual_investigation!.candidates[0].general_safety_panel = {
      panel_sha256: 'f'.repeat(64), version: 'synthetic', scope: 'General panel fixture',
      coverage_statement: 'Not evaluated is not a negative activity result or evidence of safety.',
      source_count_discrepancies: [{ source_label: 'Domain (3)', stated_count: 3, enumerated_count: 2 }],
      targets: [{ gene: 'GENEA', target_accession: 'P00001', name: 'Synthetic target', intended_target: false,
        source_domains: ['Domain (3)'], status: 'not_evaluated', limitation: 'Panel inclusion is not candidate potency.' }],
    }
    render(<VirtualInvestigationResult research={research} />)
    expect(screen.getByText(/^not evaluated/)).toBeTruthy()
    expect(screen.getByText(/Not evaluated is not a negative activity result/)).toBeTruthy()
    expect(screen.getByText(/No targets were invented to reconcile it/)).toBeTruthy()
  })
  it('renders only native sampled exposure metrics with explicit concentration basis', () => {
    const research = fixture()
    research.visualization!.prospective_assessment!.virtual_investigation!.candidates[0].native_exposure_endpoints = {
      reference: null, conditional: [{ case_identifier: 'Synthetic A', metrics: {
        endpoints: [{ species: 'Human', organ: 'Liver', compartment: 'Interstitial Unbound', subject_identifier: '0',
          peak_umol_l: 2, sampled_tmax_h: [1], auc_umol_h_l: 3, window_h: [0,2], limitation: 'Sampled fixture' }],
        tissue_plasma_ratios: [{ species: 'Human', organ: 'Liver', compartment: 'Interstitial Unbound', subject_identifier: '0',
          concentration_basis: 'unbound', auc_tissue_plasma_ratio: 4, window_h: [0,2] }],
        interpretation: 'Conditional ratio, not a partition coefficient or clinical effect',
      } }],
    }
    render(<VirtualInvestigationResult research={research} />)
    expect(screen.getByText('2.0000')).toBeTruthy()
    expect(screen.getByText('3.0000 over 0 – 2 h')).toBeTruthy()
    expect(screen.getByText('unbound')).toBeTruthy()
    expect(screen.getByText(/not a partition coefficient/)).toBeTruthy()
  })
  it('keeps sponsor assumptions visibly distinct from measurements and private history', () => {
    const research = fixture()
    research.visualization!.prospective_assessment!.virtual_investigation!.candidates[0].dossier_acquisition.experiment_provenance = {
      experiment_class: 'sponsor_side_prospective', historical_private_data_claim: false,
      canonical_document_sha256: 'c'.repeat(64), input_receipts: [{ identifier: 'synthetic-fu', role: 'fraction_unbound',
        provenance_class: 'sponsor_side_scenario_assumption', values: [.1, .2], unit: 'fraction',
        rationale: 'Synthetic UI levels only', uncertainty: 'Not measured', source_sha256: 'd'.repeat(64) },
        { identifier: 'synthetic-missing', role: 'hepatic_clearance', provenance_class: 'missing', values: [],
          unit: 'ml/min/kg', rationale: 'Synthetic missing fixture', uncertainty: 'Unknown', source_sha256: 'e'.repeat(64) }],
    }
    render(<VirtualInvestigationResult research={research} />)
    expect(screen.getByText('Sponsor-side prospective experiment')).toBeTruthy()
    expect(screen.getByText('Sponsor-side scenario assumption')).toBeTruthy()
    expect(screen.getByText('0.10000 · 0.20000')).toBeTruthy()
    expect(screen.getAllByText('Missing')).toHaveLength(2)
    expect(screen.getByText(/Not an authentic private historical dataset/)).toBeTruthy()
    expect(screen.getByText(/Modern recovery may supply isolated ordinary ADME/)).toBeTruthy()
    expect(screen.queryByText(/Candidate measurements require exact dated/)).toBeNull()
  })
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
