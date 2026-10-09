import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { ResearchSessionWorkspace } from '../hooks/useResearchSession'
import type { ResearchVisualizationWorkspace } from '../api/types'
import { VirtualOrganismResult } from './VirtualOrganismResult'

// Synthetic component fixtures test rendering only, never scientific acceptance.
function state(missing = false): ResearchSessionWorkspace {
  const curves = ['Human', 'Rat'].flatMap(species => ['PeripheralVenousBlood', 'Liver'].map(organ => ({
    species, organ, path: `${species}/${organ}`, compartment: organ === 'Liver' ? 'Tissue' : 'Plasma (Peripheral Venous Blood)',
    subject_count: 2, times_h: [0, 1, 2], median_umol_l: [0, 2, 1], p05_umol_l: [0, 1, .5], p95_umol_l: [0, 3, 1.5],
  })))
  const assessment: NonNullable<ResearchVisualizationWorkspace['virtual_organism']> = {
    assessment_artifact_identifier: 'assessment-A', assessment_sha256: 'a'.repeat(64), schema_version: 'pulsate.virtual-organism/v1',
    candidate: { name: 'candidate-A', inchikey: 'identity-A', pubchem_cid: null, source_url: null },
    status: missing ? 'insufficient_parameterization' : 'exposure_supported',
    request: { dose: 1, dose_unit: 'mg/kg', route: 'Intravenous', duration_h: 2, seed: 29, population_size: 2, administration_times_h: [0] },
    runs: missing ? [] : ['Human', 'Rat'].map(species => ({ species, result: { subject_count: 2 }, file_artifacts: { 'experiment-Results.csv': 'raw-A' } })),
    population: { interval_scope: 'Empirical sampled range, not a clinical confidence interval.', series: missing ? [] : curves },
    comparison: { comparison_scope: 'Finite-window evidence only.', subjects: [] }, evidence_quality: { measured: 1, missing: missing ? 1 : 0 },
    missing: missing ? [{ species: 'Human', reason: 'No reviewed clearance evidence.' }] : [],
    exposure_relevance: { status: 'insufficient_evidence', reason: 'Docking scores are not potency.' },
    verification: { passed: true, verification_scope: 'Integrity only.' }, parameters: [], assumptions: [], limitations: [],
    computation_selection: { selected_compute: 'classical', quantum_selected: false, reason: 'Mechanistic ODE calculation.' },
  }
  return { visualization: { virtual_organism: assessment }, exportArtifact: vi.fn() } as unknown as ResearchSessionWorkspace
}

describe('VirtualOrganismResult', () => {
  it('renders source-backed functional evidence, tissue annotations, refusals and exploratory physiology separately', () => {
    const research = state()
    research.visualization!.virtual_organism!.functional_exposure = {
      schema:'pulsate.native-functional-exposure/v1',
      functional_activity_prediction:{schema:'pulsate.functional-activity-prediction/v1',
        scope:'Synthetic rendering fixture only',manifest_sha256:'b'.repeat(64),universe_sha256:'c'.repeat(64),
        coverage:{supported_model_targets:0,unsupported_model_targets:1,accepted_candidate_targets:0,universe_targets:1},
        unresolved_source_rows:[],targets:[{gene:'TEST',target_accession:'P00001',status:'unsupported',reason:'Insufficient training data'}]},
      quantitative_activity:[{target_accession:'P00001',kind:'IC50',value:.2,functional_direction:'blocker',
        concentration_basis:'unbound',uncertainty:'Synthetic assay uncertainty',functional_assay_sha256:'d'.repeat(64)}],
      excluded_activity:[{reason:'Censored point refused',bounded_activity:{target_accession:'P00002',kind:'IC50',
        relation:'>',activity_bound_umol_l:10,functional_direction:'blocker',concentration_basis:'assay_nominal',
        uncertainty:'Unknown exact potency',limitation:'Censored bound is not zero activity',source_sha256:'a'.repeat(64)}}],
      functional_tissue_relevance:{targets:[{target_accession:'P00001',activity_status:'unsupported',
        priority_status:'unsupported_functional_activity',limitation:'Expression is not a candidate effect',
        tissue_evidence:{go:[{identifier:'GO:TEST',term:'Synthetic tissue annotation',evidence:'Fixture only'}]}}]},
      exposure_activity:[{target_accession:'P00001',status:'modeled_peak_below_activity_point',
        peak_activity_ratios:[.1],limitation:'Subthreshold peak does not establish absence of effect'}],
      functional_models:[{status:'computed',exposure_case_identifier:'case-A',result:{
        model:{identifier:'synthetic-ode',name:'Synthetic native model',sha256:'e'.repeat(64),species:'Human'},
        inference_level:'cellular_functional',perturbation:{transfer_receipt:{qualification_state:'exploratory_only',
          qualified:false,candidate_decision_authority:false,qualification_scope:{benchmark_discrepancy:'Synthetic failed biological gate'}}},
        response:{test_output:{unit:'test-unit',baseline_final:1,perturbed_final:2,final_difference:1,maximum_absolute_difference:1}},
        waveform_evidence:{full_response_sha256:'1'.repeat(64),scope:'Unchanged raw download.',curves:[
          {condition:'baseline',columns:['time','V'],sample_count:20001,waveform_sha256:'2'.repeat(64)}]},
        verification:{passed:true},limitation:'Numerical fixture, not a biological prediction'}}],
      functional_transfer_refusals:[{reason:'No exact transfer for another target'}],
      verification:{passed:true,scope:'Numerical reproducibility, not clinical validity'},
      scope:'Mechanism hypothesis is not toxicity proof',native_exposure_sha256:'f'.repeat(64),artifact_sha256:'0'.repeat(64),
    }
    render(<VirtualOrganismResult research={research} />)
    const region=within(screen.getByRole('region',{name:'Native functional exposure result'}))
    expect(region.getByText('baseline: 20001 recorded waveform samples.')).toBeTruthy()
    expect(region.getByText('Raw waveform SHA-256 ' + '2'.repeat(64))).toBeTruthy()
    expect(region.getByText(/0 targets have an accepted in-domain prediction/)).toBeTruthy()
    expect(region.getByText(/P00001 · blocker · IC50/)).toBeTruthy()
    expect(region.getByText(/P00002 · blocker · IC50 > 10.000/)).toBeTruthy()
    expect(region.getByText(/Predictor unavailable; independently sourced activity is shown separately/)).toBeTruthy()
    expect(region.getByText(/Synthetic tissue annotation/)).toBeTruthy()
    expect(region.getByText(/Subthreshold peak does not establish absence of effect/)).toBeTruthy()
    expect(region.getByRole('note',{name:'Exploratory physiology limitation'})).toBeTruthy()
    expect(region.getByText(/cannot independently change the candidate status/)).toBeTruthy()
    expect(region.getByText('No exact transfer for another target')).toBeTruthy()
    expect(region.getByText('test_output (test-unit)')).toBeTruthy()
  })

  it('opens a conditional sponsor population automatically without inventing a nominal result', () => {
    const research = state()
    const assessment = research.visualization!.virtual_organism!
    const population = { ...assessment.population, series: assessment.population.series.filter(c => c.species === 'Human') }
    assessment.runs = []; assessment.population.series = []
    assessment.parameters = [{name:'fraction_unbound',species:'Human',value:null,interval:[.1,.2],unit:'fraction',
      classification:'assumed',source:'Synthetic fixture',method:'No nominal',uncertainty:'Scenario range'}]
    assessment.sponsor_dossiers = [{experiment_provenance:{experiment_class:'sponsor_side_prospective',historical_private_data_claim:false,
      input_receipts:[{identifier:'scenario-1',role:'fraction_unbound',provenance_class:'sponsor_side_scenario_assumption',
        values:[.1,.2],unit:'fraction',rationale:'Synthetic fixture only',uncertainty:'Not a measured distribution',source_sha256:'a'.repeat(64)},
      {identifier:'missing-1',role:'intestinal_permeability',provenance_class:'missing',values:[],unit:'cm/s',
        rationale:'Not supplied',uncertainty:'No prediction',source_sha256:'b'.repeat(64)}]}}]
    assessment.drug_parameter_uncertainty = {scope:'Conditional synthetic scenarios, no nominal',scenarios:[{
      species:'Human',scenario_identifier:'case-A',scenario_kind:'sponsor_input_sensitivity',scenario_policy:{nominal_withheld:true},
      series:[],population,file_artifacts:{'scenario-Results.csv':'raw-case-A'}}]}
    render(<VirtualOrganismResult research={research} />)
    expect(screen.getByRole('heading',{name:'Virtual human'})).toBeTruthy()
    expect(screen.getByText('Conditional scenario case-A — not a nominal prediction.')).toBeTruthy()
    const region=screen.getByRole('region',{name:'Sponsor-side input provenance'})
    expect(within(region).getByText('sponsor side scenario assumption')).toBeTruthy()
    expect(within(region).getByText('Missing cm/s')).toBeTruthy()
    expect(screen.getByText(/Median and empirical 5–95% range · 2 simulated subjects/)).toBeTruthy()
    expect(screen.getByText('0.1 – 0.2 (no nominal selected) fraction')).toBeTruthy()
    fireEvent.change(screen.getByLabelText('Inspect scenario compartment'),{target:{value:'Human/Liver'}})
    expect(screen.getByRole('img',{name:'Human Liver Tissue concentration time course'})).toBeTruthy()
    expect(screen.queryByLabelText('Inspect modeled compartment')).toBeNull()
    fireEvent.click(screen.getByRole('button',{name:'Download scenario concentration time series'}))
    expect(research.exportArtifact).toHaveBeenCalledWith('raw-case-A')
  })

  it('shows actual curve arrays, tissue and species selection, and raw downloads', () => {
    const research = state()
    render(<VirtualOrganismResult research={research} />)
    expect(screen.getByRole('heading', { name: 'Virtual human' })).toBeTruthy()
    expect(screen.getByRole('img', { name: /Human PeripheralVenousBlood/ })).toBeTruthy()
    expect(screen.getByText(/Median and empirical 5–95% range/)).toBeTruthy()
    fireEvent.change(screen.getByLabelText('Inspect modeled compartment'), { target: { value: 'Human/Liver' } })
    expect(screen.getByRole('img', { name: /Human Liver Tissue/ })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /^Rat$/ }))
    expect(screen.getByRole('heading', { name: 'Virtual rat' })).toBeTruthy()
    expect(screen.getByRole('img', { name: /Rat PeripheralVenousBlood/ })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Download raw concentration time series' }))
    expect(research.exportArtifact).toHaveBeenCalledWith('raw-A')
    fireEvent.click(screen.getByRole('button', { name: 'Download Virtual Organism evidence' }))
    expect(research.exportArtifact).toHaveBeenCalledWith('assessment-A')
  })

  it('discloses bounded exposure sampling while raw downloads retain the original artifact', () => {
    const research=state()
    const curve=research.visualization!.virtual_organism!.population.series[0]
    Object.assign(curve,{display_projection:{method:'uniform-index-plus-column-extrema/v1',
      original_sample_count:24001,display_sample_count:curve.times_h.length,
      source_table_sha256:'d'.repeat(64),source_json_pointer:'/population/series/0',
      externalized_fields:['times_h','median_umol_l'],scope:'Display only'}})
    render(<VirtualOrganismResult research={research} />)
    expect(screen.getByText(/24001 original samples/)).toBeTruthy()
    expect(screen.getByText(/Source table SHA-256/).textContent).toContain('d'.repeat(64))
    fireEvent.click(screen.getByRole('button',{name:'Download raw concentration time series'}))
    expect(research.exportArtifact).toHaveBeenCalledWith('raw-A')
  })

  it('never draws an invented chart when critical parameterization is missing', () => {
    render(<VirtualOrganismResult research={state(true)} />)
    expect(screen.getByRole('heading', { name: 'Insufficient parameterization' })).toBeTruthy()
    expect(screen.getByRole('status').textContent).toContain('No reviewed clearance evidence.')
    expect(screen.queryByRole('img')).toBeNull()
    expect(screen.getByText(/does not certify human safety/)).toBeTruthy()
  })

  it('leaves old sessions without a PBPK assessment unchanged', () => {
    const { container } = render(<VirtualOrganismResult research={{ visualization: null } as ResearchSessionWorkspace} />)
    expect(container.childElementCount).toBe(0)
  })

  it('prominently distinguishes missing and predicted evidence, model domain and marginal uncertainty', () => {
    const research = state(true)
    research.visualization!.virtual_organism!.adme_parameterization = {
      verification: { passed: true, scope: 'Synthetic UI replay fixture' },
      prediction: { status: 'predicted', request_sha256: 'b'.repeat(64), timestamp: '2026-10-02T00:00:00Z', descriptors: { MolWt: 123 },
        native_translation_audit: [{ species:'Human',scope:'Synthetic readiness audit',
          ionization:{status:'missing',source:'No calibrated site model',reason:'No invented pKa'},
          solubility:{status:'unresolved',reference_ph:null,method:'No unqualified HH equation',reason:'Mixed-pH endpoint is not intrinsic solubility'},
          blood_plasma:{status:'native_mechanism_not_yet_instantiated',method:'Native RBC composition',required_inputs:['Hematocrit'],reason:'No B/P=1'},
          hepatic:{status:'unresolved',equations:['Synthetic conditional equation'],required_inputs:['Incubation binding'],reason:'No CLint to plasma shortcut',value:null,unit:'ml/min/kg'},
          renal:{status:'unresolved',component_equation:'Synthetic filtration component',unresolved_mechanisms:['secretion','reabsorption'],reason:'Filtration is not total clearance',total_clearance_established:false},
          reference_weight:{status:'conditional_not_subject_input',value_kg:null,method:'Native subject physiology',reason:'No arbitrary weight request'} }],
        dossiers: [{ species: 'Human', ionization_status: 'missing', ionization_source: 'No reviewed pKa', parameter_conflicts: [],
          parameters: { renal_clearance: { value: null, unit: 'ml/min/kg', classification: 'missing', source: 'Unavailable', method: 'No renal model', uncertainty: null } },
          adme_parameters: { fraction_unbound: { value: .1, unit: 'fraction', classification: 'predicted', source: 'Synthetic', method: 'Fixture',
            interval: [.01, .5], uncertainty: 'Marginal interval; not joint PK confidence', prediction: { model: 'Fixture RF', model_version: 'v1', model_sha256: 'a'.repeat(64), endpoint_definition: 'Synthetic binding endpoint',
              applicability: { status: 'out_of_domain', nearest_training_tanimoto: .1, training_graph_seen: false }, translation: null } } } }],
      },
    }
    render(<VirtualOrganismResult research={research} />)
    const section = within(screen.getByRole('region', { name: 'ADME Parameterization' }))
    expect(section.getByText('predicted', { exact: true })).toBeTruthy()
    expect(section.getByText('out of domain')).toBeTruthy()
    expect(section.getByText(/Marginal interval/)).toBeTruthy()
    expect(section.getByText(/No renal model/)).toBeTruthy()
    expect(section.getByRole('region', { name:'Human PBPK translation trace' })).toBeTruthy()
    expect(section.getByText(/Mixed-pH endpoint is not intrinsic solubility/)).toBeTruthy()
    expect(section.getByText(/Filtration is not total clearance/)).toBeTruthy()
    expect(section.getByText(/No arbitrary weight request/)).toBeTruthy()
    expect(screen.queryByRole('img')).toBeNull()
  })
})
