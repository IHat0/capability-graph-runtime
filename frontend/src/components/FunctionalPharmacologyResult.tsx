import type { FunctionalActivityPrediction, NativeFunctionalExposure, VirtualInvestigation } from '../api/types'

export function FunctionalPredictionTable({ prediction }: { prediction: FunctionalActivityPrediction }) {
  return <section aria-label="Functional pharmacology">
    <h3>Functional pharmacology · predicted, not measured</h3>
    <p>{prediction.coverage.supported_model_targets} supported functional targets · {prediction.coverage.unsupported_model_targets} unsupported targets.</p>
    <p>{prediction.coverage.accepted_candidate_targets} targets have an accepted in-domain prediction for this candidate. Unsupported/refused is not a negative finding.</p>
    <p>Action and endpoint remain separate. Nominal assay potency is not free Human-tissue potency. A mechanism hypothesis is not toxicity proof.</p>
    <details open={prediction.coverage.accepted_candidate_targets > 0}>
      <summary>Functional estimates, intervals and explicit refusals</summary>
      <div className="candidate-table-wrap" role="region" aria-label="Functional pharmacology evidence" tabIndex={0}>
        <table className="candidate-table"><thead><tr><th>Human target</th><th>Action / endpoint</th><th>Potency / 90% marginal interval (µmol/L)</th><th>Applicability / validation</th></tr></thead>
          <tbody>{prediction.targets.map((target,i)=><tr key={`${target.target_accession}-${i}`}>
            <td>{target.gene} · {target.target_accession}</td>
            <td>{target.action ?? 'Not established'} · {target.kind ?? 'Not modeled'}<br />{target.readout?.replaceAll('_',' ')}</td>
            <td>{target.status==='predicted' && target.value_umol_l != null ? <>Predicted: {target.value_umol_l.toPrecision(5)}<br />{target.interval_umol_l?.map(v=>v.toPrecision(5)).join(' – ')}<br />{target.interval_definition}</> : <>{target.status.replaceAll('_',' ')}: {target.reason}</>}<br />{target.limitation}<br />{target.uncertainty}</td>
            <td>{target.applicability?.status.replaceAll('_',' ')}
              {target.applicability?.nearest_training_tanimoto != null && <p>Nearest training similarity: {target.applicability.nearest_training_tanimoto.toPrecision(3)}</p>}
              {target.research_gate && <details><summary>Frozen research gate: {target.research_gate.accepted ? 'passed' : 'failed'}</summary>
                <p>n={target.research_gate.metrics.n}; MAE={target.research_gate.metrics.mae?.toPrecision(4) ?? 'unavailable'}; RMSE={target.research_gate.metrics.rmse?.toPrecision(4) ?? 'unavailable'}; rank correlation={target.research_gate.metrics.spearman?.toPrecision(4) ?? 'unavailable'}; fold error={target.research_gate.metrics.median_multiplicative_error?.toPrecision(4) ?? 'unavailable'}; coverage={target.research_gate.metrics.interval_coverage?.toPrecision(4) ?? 'unavailable'}.</p>
                <p>{target.research_gate.scope}<br />Failed metrics: {target.research_gate.failed_metrics.join(', ') || 'None'}.</p>
              </details>}
              <small>Model SHA-256 {target.model_sha256 ?? 'No model'}</small>
            </td>
          </tr>)}</tbody></table>
      </div>
      {prediction.unresolved_source_rows.map((row,i)=><p key={i}>Unresolved panel identity: {row.label} — {row.reason}</p>)}
    </details>
    <small>Universe SHA-256 {prediction.universe_sha256}<br />Model set SHA-256 {prediction.manifest_sha256}</small>
  </section>
}

type Models = VirtualInvestigation['candidates'][number]['functional_models']
type Tissues = VirtualInvestigation['candidates'][number]['functional_tissue_relevance']
export function FunctionalTissueEvidence({ evidence, sourcedTargets = [] }: { evidence: Tissues; sourcedTargets?: string[] }) {
  if (!evidence) return null
  const relevant = new Set(sourcedTargets)
  const targets = evidence.targets.filter(t=>t.activity_status==='predicted' || relevant.has(t.target_accession))
  return <details>
    <summary>General Human tissue relevance · not a candidate physiological effect</summary>
    {!targets.length && <p>No accepted candidate activity to prioritize with these general annotations. Expression alone is not evidence of an effect.</p>}
    {targets.map((target,i)=><section key={`${target.target_accession}-${i}`}>
      <h5>{target.target_accession} · {target.action ?? 'See separately sourced action'}</h5>
      <p>{target.activity_status==='predicted' ? target.priority_status.replaceAll('_',' ') : 'Predictor unavailable; independently sourced activity is shown separately'}. {target.limitation}</p>
      <p>{target.tissue_evidence?.limitation ?? target.tissue_evidence?.reason ?? 'No exact Human tissue annotation available.'}</p>
      <ul>{target.tissue_evidence?.go?.map(g=><li key={g.identifier}>{g.identifier}: {g.term} ({g.evidence})</li>)}</ul>
      {target.tissue_evidence?.expression?.map((source,j)=><p key={j}><a href={source.source_url} target="_blank" rel="noreferrer">Exact Human expression source</a>: {source.historical_qualification}<br /><small>SHA-256 {source.source_sha256}</small></p>)}
    </section>)}
  </details>
}

export function PhysiologicalModels({ models }: { models: Models }) {
  return <section aria-label="Functional models"><h3>Physiological models</h3>
    {!models.length && <p>No reviewed mechanistic model catalogue configured. No physiological story is generated.</p>}
    {models.map((f,i)=><div key={i}>
      {f.status==='computed' && f.result ? <>
        <h4>{f.result.model.name}</h4><p>Species: {f.result.model.species} · level: {f.result.inference_level.replaceAll('_',' ')} · numerical replay {f.result.verification.passed ? 'passed' : 'failed'}</p>
        {f.result.perturbation?.transfer_receipt?.qualification_state==='exploratory_only' && <section className="exploratory-physiology-warning" aria-label="Exploratory physiology limitation" role="note">
          <h4>Exploratory physiology only — not a qualified prediction</h4>
          <p>This computation cannot independently change the candidate status to CONCERN, REJECT or ADVANCE.</p>
          <p>{f.result.perturbation.transfer_receipt.qualification_scope?.benchmark_discrepancy}</p>
          {f.result.perturbation.transfer_receipt.qualification_scope?.benchmarks?.map(b=><p key={b.identifier}>{b.identifier}: maximum observed-benchmark discrepancy {b.maximum_absolute_error.toPrecision(5)} {b.unit}; predeclared error budget {b.error_budget.toPrecision(5)} {b.unit} · {b.biological_error_budget_passed ? 'passed' : 'failed'}.</p>)}
          <ul>{f.result.perturbation.transfer_receipt.qualification_scope?.limitations?.map((text,j)=><li key={j}>{text}</li>)}</ul>
        </section>}
        {f.exposure_case_identifier && <p>Conditional native input case: {f.exposure_case_identifier}. This is not a nominal prediction.</p>}
        <p>Numerical replay checks software reproducibility; it does not establish independent scientific certification or clinical validity.</p>
        <table><thead><tr><th>Model output</th><th>Baseline final</th><th>Perturbed final</th><th>Difference</th></tr></thead><tbody>{Object.entries(f.result.response).map(([key,r])=><tr key={key}><td>{key} ({r.unit})</td><td>{r.baseline_final.toPrecision(5)}</td><td>{r.perturbed_final.toPrecision(5)}</td><td>{r.final_difference.toPrecision(5)}</td></tr>)}</tbody></table>
        <p>{f.result.limitation}</p><small>Model SHA-256 {f.result.model.sha256}</small>
        {f.result.waveform_evidence && <section aria-label="Raw waveform receipt"><p>{f.result.waveform_evidence.scope}<br /><small>Full numerical response SHA-256 {f.result.waveform_evidence.full_response_sha256}</small></p>
          {f.result.waveform_evidence.curves.map((curve,j)=><div key={j}><p>{curve.condition}: {curve.sample_count} recorded waveform samples.<br /><small>Raw waveform SHA-256 {curve.waveform_sha256}</small></p>
            {curve.display_projection && <WaveformPlot condition={curve.condition} columns={curve.columns} projection={curve.display_projection} originalCount={curve.sample_count} />}
          </div>)}
        </section>}
      </> : <p><strong>{f.model_identifier}: not computed.</strong> {f.reason}</p>}
    </div>)}
  </section>
}

function WaveformPlot({ condition, columns, projection, originalCount }: {
  condition: string; columns: string[]; projection: { method: string; values: number[][]; sample_count: number; scope: string }; originalCount: number
}) {
  const rows = projection.values
  if (!rows.length || columns.length < 2) return null
  const xs = rows.map(row => row[0]), ys = rows.map(row => row[1])
  const xmin = Math.min(...xs), xmax = Math.max(...xs), ymin = Math.min(...ys), ymax = Math.max(...ys)
  const points = rows.map(row => `${55 + (row[0] - xmin) / (xmax - xmin || 1) * 620},${220 - (row[1] - ymin) / (ymax - ymin || 1) * 190}`).join(' ')
  return <figure><svg viewBox="0 0 720 265" style={{ width: '100%', maxWidth: 720 }} role="img" aria-label={`${condition} physiological waveform display projection`}>
    <title>{condition} · {columns[1]} against {columns[0]}</title>
    <path d="M55,20 V220 H675" fill="none" stroke="currentColor" /><polyline points={points} fill="none" stroke="#35b5ac" strokeWidth="2" />
    <text x="55" y="250" fill="currentColor" fontSize="12">{columns[0]} · {xmin}–{xmax}</text>
    <text x="55" y="14" fill="currentColor" fontSize="12">{columns[1]} · {ymin.toPrecision(4)}–{ymax.toPrecision(4)}</text>
  </svg><figcaption>{projection.sample_count} display samples from {originalCount} raw samples · {projection.method}. {projection.scope}</figcaption></figure>
}

export function FunctionalExposureResult({ evidence }: { evidence: NativeFunctionalExposure }) {
  return <section aria-label="Native functional exposure result">
    <FunctionalPredictionTable prediction={evidence.functional_activity_prediction} />
    <h3>Independently sourced functional activity</h3>
    {evidence.quantitative_activity.filter(a=>a.functional_assay_sha256).map((a,i)=><p key={i}>{a.target_accession} · {a.functional_direction} · {a.kind}: {a.value.toPrecision(5)} µmol/L · {a.concentration_basis}. {a.uncertainty}<br /><small>Assay contract SHA-256 {a.functional_assay_sha256}</small></p>)}
    {evidence.excluded_activity?.filter(a=>a.bounded_activity).map((a,i)=>{const b=a.bounded_activity!;return <p key={i}>{b.target_accession} · {b.functional_direction} · {b.kind} {b.relation} {b.activity_bound_umol_l.toPrecision(5)} µmol/L · {b.concentration_basis}. {b.limitation} {b.uncertainty}<br /><small>Original source SHA-256 {b.source_sha256}</small></p>})}
    <FunctionalTissueEvidence evidence={evidence.functional_tissue_relevance} sourcedTargets={evidence.quantitative_activity.filter(a=>a.functional_assay_sha256).map(a=>a.target_accession)} />
    <h3>Exposure relevance</h3>
    {evidence.exposure_activity.map((a,i)=><details key={i} open={!!a.comparisons?.length || !!a.peak_activity_ratios?.length}><summary>{a.target_accession}: {a.status.replaceAll('_',' ')}</summary>
      <p>{a.reason ?? a.limitation} {a.uncertainty}</p>
      {a.activity_interval_umol_l && <p>Functional potency range: {a.activity_interval_umol_l.map(v=>v.toPrecision(5)).join(' – ')} µmol/L. {a.interval_definition}</p>}
      {a.peak_activity_ratios && <p>Matching unbound peak / activity ratios: {a.peak_activity_ratios.map(v=>v.toPrecision(5)).join(', ')}</p>}
      {a.comparisons && <table><thead><tr><th>Case / subject / compartment</th><th>Unbound peak (µmol/L)</th><th>Exposure/activity ratio interval</th><th>Interpretation</th></tr></thead><tbody>{a.comparisons.map((c,j)=><tr key={j}><td>{c.exposure_case_identifier} · {c.subject_identifier} · {c.native_path}</td><td>{c.peak_umol_l.toPrecision(5)}</td><td>{c.peak_activity_ratio_interval.map(v=>v.toPrecision(5)).join(' – ')}</td><td>{c.status}</td></tr>)}</tbody></table>}
    </details>)}
    <PhysiologicalModels models={evidence.functional_models} />
    <details><summary>Exact transfer refusals ({evidence.functional_transfer_refusals.length})</summary>{evidence.functional_transfer_refusals.map((refusal,i)=><p key={i}>{refusal.reason}</p>)}</details>
    <p>{evidence.verification.scope}</p><p>{evidence.scope}</p>
    <small>Functional evidence SHA-256 {evidence.artifact_sha256} · native exposure SHA-256 {evidence.native_exposure_sha256}</small>
  </section>
}
