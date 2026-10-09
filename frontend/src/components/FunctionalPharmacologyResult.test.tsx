import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { PhysiologicalModels } from './FunctionalPharmacologyResult'

describe('bounded physiological waveform evidence', () => {
  it('renders only the display projection and discloses the full original sample count/hash', () => {
    const models: Parameters<typeof PhysiologicalModels>[0]['models'] = [{ status:'computed', result:{
      model:{identifier:'synthetic-model',name:'Synthetic model',species:'Human',sha256:'a'.repeat(64)},
      inference_level:'single_cell', response:{'synthetic.APD90':{unit:'ms',baseline_final:200,
        perturbed_final:201.1234567890123,final_difference:1.1234567890123,maximum_absolute_difference:1.1234567890123}},
      limitation:'Synthetic fixture, not clinical QTc.', verification:{passed:true},
      waveform_evidence:{full_response_sha256:'b'.repeat(64),scope:'Full samples remain downloadable.',curves:[{
        condition:'baseline',columns:['time (ms)','V (mV)'],sample_count:20001,waveform_sha256:'c'.repeat(64),
        display_projection:{method:'uniform-index-plus-column-extrema/v1',values:[[0,-80],[500,20],[1000,-80]],
          sample_count:3,scope:'Display only; endpoints use full evidence.'}}]}}}]
    render(<PhysiologicalModels models={models} />)
    expect(screen.getByRole('img',{name:'baseline physiological waveform display projection'})).toBeTruthy()
    expect(screen.getByText(/3 display samples from 20001 raw samples/)).toBeTruthy()
    expect(screen.getByText(/Raw waveform SHA-256/).textContent).toContain('c'.repeat(64))
    expect(screen.getByText(/Full numerical response SHA-256/).textContent).toContain('b'.repeat(64))
    expect(screen.getByText('synthetic.APD90 (ms)')).toBeTruthy()
  })
})
