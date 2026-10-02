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
