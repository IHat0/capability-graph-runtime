import { useState } from 'react'
import type { ResearchSessionWorkspace } from '../hooks/useResearchSession'
import type { ResearchVisualizationWorkspace } from '../api/types'

type Assessment = NonNullable<ResearchVisualizationWorkspace['virtual_organism']>
type Curve = Assessment['population']['series'][number]

export function ExposureChart({ curve }: { curve: Curve }) {
  const width = 720, height = 280, left = 72, top = 20, bottom = 235, right = 700
  const xmax = Math.max(...curve.times_h, 1), ymax = Math.max(...curve.p95_umol_l, 1e-12)
  const x = (t: number) => left + t / xmax * (right - left)
  const y = (v: number) => bottom - v / ymax * (bottom - top)
  const line = (values: number[]) => values.map((v, i) => `${x(curve.times_h[i])},${y(v)}`).join(' ')
  const band = line(curve.p95_umol_l) + ' ' + curve.p05_umol_l.map((v, i) => `${x(curve.times_h[i])},${y(v)}`).reverse().join(' ')
  return <figure>
    <svg viewBox={`0 0 ${width} ${height}`} style={{ width: '100%', maxWidth: width }} role="img" aria-label={`${curve.species} ${curve.organ} ${curve.compartment} concentration time course`}>
      <title>{curve.species} · {curve.organ} · {curve.compartment}</title>
      <desc>Actual PK-Sim output, median and empirical fifth–ninety-fifth percentile across {curve.subject_count} simulated subjects. Concentration in micromoles per litre; time in hours.</desc>
      <path d={`M${left},${top} V${bottom} H${right}`} fill="none" stroke="currentColor" />
      {[0, .25, .5, .75, 1].map(f => <g key={f}>
        <text x={left - 8} y={y(f * ymax) + 4} textAnchor="end" fill="currentColor" fontSize="11">{(f * ymax).toPrecision(3)}</text>
        <text x={x(f * xmax)} y={bottom + 18} textAnchor="middle" fill="currentColor" fontSize="11">{(f * xmax).toPrecision(3)}</text>
      </g>)}
      <polygon points={band} fill="#35b5ac" opacity=".2" />
      <polyline points={line(curve.median_umol_l)} fill="none" stroke="#35b5ac" strokeWidth="2.5" />
      <text x={16} y={14} fill="currentColor" fontSize="12">µmol/L</text>
      <text x={(left + right) / 2} y={height - 6} textAnchor="middle" fill="currentColor" fontSize="12">Time (h)</text>
    </svg>
    <figcaption>{curve.subject_count === 1 ? 'Individual concentration time course' : `Median and empirical 5–95% range · ${curve.subject_count} simulated subjects`}. This is modeled exposure, not a measured response.</figcaption>
  </figure>
}

export function VirtualOrganismResult({ research }: { research: ResearchSessionWorkspace }) {
  const assessment = research.visualization?.virtual_organism
  const [species, setSpecies] = useState('Human')
  const [path, setPath] = useState('')
  if (!assessment) return null
  const selectedSpecies = assessment.runs.some(r => r.species === species) ? species : assessment.runs[0]?.species
  const curves = assessment.population.series.filter(c => c.species === selectedSpecies)
  const curve = curves.find(c => c.path === path) ?? curves.find(c => c.compartment === 'Plasma (Peripheral Venous Blood)') ?? curves[0]
  const run = assessment.runs.find(r => r.species === selectedSpecies)
  return <article className="research-result virtual-organism-result" aria-label="Virtual Organism result">
    <p className="section-kicker">Virtual Organism · classical mechanistic PBPK</p>
    <h2>{selectedSpecies ? `Virtual ${selectedSpecies.toLowerCase()}` : 'Insufficient parameterization'}</h2>
    <p><strong>{assessment.candidate.name}</strong> · {assessment.request.dose} {assessment.request.dose_unit} · {assessment.request.route} · dose times {assessment.request.administration_times_h.join(', ')} h · {assessment.request.duration_h} h simulation</p>
    <p>{assessment.status.replaceAll('_', ' ')}. Computational verification {assessment.verification.passed ? 'passed' : 'not passed'}; biological validity and safety are not established.</p>
    <div role="group" aria-label="Species selection">{assessment.runs.map(r => <button type="button" className="secondary-button" key={r.species} aria-pressed={selectedSpecies === r.species} onClick={() => { setSpecies(r.species); setPath('') }}>{r.species}</button>)}</div>
    {curve && <>
      <label htmlFor="exposure-compartment">Inspect modeled compartment</label>
      <select id="exposure-compartment" value={curve.path} onChange={event => setPath(event.target.value)}>{curves.map(c => <option key={c.path} value={c.path}>{c.organ} · {c.compartment}</option>)}</select>
      <ExposureChart curve={curve} />
      <p>{assessment.population.interval_scope} Seed {assessment.request.seed}.</p>
      {run && Object.entries(run.file_artifacts).filter(([name]) => name.endsWith('-Results.csv') || name.endsWith('-Population.csv')).map(([name, id]) => <button key={id} type="button" onClick={() => void research.exportArtifact(id)}>Download {name.endsWith('-Population.csv') ? 'sampled physiology' : 'raw concentration time series'}</button>)}
    </>}
    <details><summary>PK metrics and tissue/plasma comparison</summary>
      <p>{assessment.comparison.comparison_scope}</p>
      <table><thead><tr><th>Species / subject</th><th>Sampled Cmax (µmol/L)</th><th>Tmax (h)</th><th>AUC 0–t (µmol·h/L)</th></tr></thead><tbody>{assessment.comparison.subjects.map(s => <tr key={`${s.species}-${s.subject_identifier}`}><td>{s.species} / {s.subject_identifier}</td><td>{s.plasma_metrics.cmax_umol_l.toPrecision(5)}</td><td>{s.plasma_metrics.tmax_h.toPrecision(4)}</td><td>{s.plasma_metrics.auc_0_t_umol_h_l.toPrecision(5)}</td></tr>)}</tbody></table>
      {assessment.comparison.subjects.filter(s => s.species === selectedSpecies).map(s => <details key={s.subject_identifier}><summary>{s.species} subject {s.subject_identifier} · organ exposure overview</summary><table><thead><tr><th>Modeled tissue</th><th>Tissue / plasma AUC ratio</th></tr></thead><tbody>{Object.entries(s.tissue_to_plasma_auc_ratios).map(([organ, ratio]) => <tr key={organ}><td>{organ}</td><td>{ratio.toPrecision(5)}</td></tr>)}</tbody></table><p>Ratio of finite-window total tissue and plasma exposure, not unbound target-site potency or tissue response.</p></details>)}
      <p>Half-life, clearance and bioavailability are not inferred from this finite time window.</p>
    </details>
    <h3>Exposure-to-mechanism evidence</h3><p>{assessment.exposure_relevance.reason}</p>
    {assessment.activity_comparisons?.map((comparison, index) => <details key={index}><summary>{comparison.activity?.target ?? 'Activity comparison'} · {comparison.status.replaceAll('_', ' ')}</summary><p>{comparison.reason}</p>{comparison.activity && <p>{comparison.activity.species} · {comparison.activity.kind} {comparison.activity.value} {comparison.activity.unit} · {comparison.activity.source}. {comparison.activity.assay_context}. {comparison.activity.compatibility_limitations}</p>}<p>Per-subject peak unbound exposure / activity ratios: {comparison.peak_exposure_to_activity_ratios?.map(r => r.toPrecision(4)).join(', ') ?? 'Not computed'}</p></details>)}
    {assessment.missing.map(m => <p role="status" key={m.species}>{m.species}: {m.reason}</p>)}
    <h3>Evidence quality</h3><p>{Object.entries(assessment.evidence_quality).map(([label, count]) => `${label.replaceAll('_', ' ')}: ${count}`).join(' · ')}</p>
    <details><summary>Parameter provenance, assumptions and limitations</summary>
      <table><thead><tr><th>Species / parameter</th><th>Value / units</th><th>Evidence class</th><th>Source / method</th></tr></thead><tbody>{assessment.parameters.map((p,i) => <tr key={i}><td>{p.species} / {p.name}</td><td>{p.value ?? 'missing'} {p.unit}</td><td>{p.classification}</td><td>{p.source} · {p.method}{p.uncertainty && ` · ${p.uncertainty}`}</td></tr>)}</tbody></table>
      <ul>{[...assessment.assumptions, ...assessment.limitations].map((text,i) => <li key={i}>{text}</li>)}</ul>
    </details>
    <p>{assessment.computation_selection.reason}</p>
    <button type="button" onClick={() => void research.exportArtifact(assessment.assessment_artifact_identifier)}>Download Virtual Organism evidence</button>
    <p>PBPK predicts exposure under assumptions. It does not certify human safety or compute organ function.</p>
  </article>
}
