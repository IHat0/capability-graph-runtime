import type { ResearchSessionWorkspace } from '../hooks/useResearchSession'
import { VirtualOrganismResult } from './VirtualOrganismResult'

export function VirtualInvestigationResult({ research }: { research: ResearchSessionWorkspace }) {
  const investigation = research.visualization?.prospective_assessment?.virtual_investigation
  if (!investigation) return null
  return <article className="research-result virtual-investigation" aria-label="Virtual Investigation">
    <p className="section-kicker">Verified evidence chain</p>
    <h2>Virtual Investigation</h2>
    <p>Exposure → molecular hypotheses → functional models</p>
    <p>Historical cutoff: {investigation.prospective_policy.cutoff_instant ?? investigation.prospective_policy.cutoff ?? 'Not configured; candidate-specific historical inputs are quarantined'}. General modern annotations are not silently described as historically available.</p>
    <p>Candidate measurements require exact dated original-source evidence. Candidate-specific clinical and physiological observations are excluded regardless of date.</p>
    {investigation.candidates.map(candidate => <section key={candidate.candidate_identifier} aria-label={`${candidate.identity.name} virtual investigation`}>
      <h3>{candidate.identity.name}</h3>
      <p><strong>{candidate.candidate_status}</strong> · {candidate.assessment_reason}</p>
      <p>Evidence levels: {Object.entries(candidate.inference_levels).map(([level, available]) => `${level.replaceAll('_', ' ')}: ${available ? 'supported at stated scope' : 'not established'}`).join(' · ')}</p>
      <section aria-label="Virtual Human">
        <h4>Virtual Human</h4>
        <p>{candidate.regimen.source ?? candidate.regimen.reason ?? candidate.regimen.status.replaceAll('_', ' ')}</p>
        <ul>{[...candidate.dossier_acquisition.missing, ...(candidate.regimen.missing ?? [])].map((m,i) => <li key={i}>{m}</li>)}</ul>
        {candidate.dossier_acquisition.conflicts.map((c,i) => <p key={i}>Unresolved conflict — {c.parameter}: {c.reason}</p>)}
        {candidate.virtual_organism && research.visualization && <VirtualOrganismResult research={{ ...research,
          visualization: { ...research.visualization, virtual_organism: candidate.virtual_organism } }} />}
        {!candidate.virtual_organism && <p>No nominal Human exposure curve was computed. Missing inputs are not replaced by invented physiology or clearance.</p>}
        {candidate.native_sensitivity && <section aria-label="Conflicting-input sensitivity">
          <h5>Conflicting-input sensitivity · nominal result withheld</h5>
          {candidate.native_sensitivity.reason && <p>{candidate.native_sensitivity.reason}</p>}
          {candidate.native_sensitivity.design && <p>{candidate.native_sensitivity.design.cases.length} conditional scenarios of {candidate.native_sensitivity.design.total_combinations} discrete combinations. {candidate.native_sensitivity.design.limitation}</p>}
          {candidate.native_sensitivity.summary && <>
            <p>{candidate.native_sensitivity.summary.qualitative_conclusion}</p>
            <details><summary>Computed exposure ranges, not clinical confidence intervals</summary>
              <table><thead><tr><th>Native endpoint</th><th>Peak range (µmol/L)</th><th>AUC range (µmol·h/L)</th></tr></thead>
                <tbody>{Object.entries(candidate.native_sensitivity.summary.endpoint_ranges).map(([endpoint,range]) =>
                  <tr key={endpoint}><td>{endpoint}</td><td>{range.peak_range_umol_l.map(v=>v.toPrecision(5)).join(' – ')}</td>
                    <td>{range.auc_range_umol_h_l.map(v=>v.toPrecision(5)).join(' – ')}</td></tr>)}</tbody></table>
            </details>
          </>}
          {candidate.native_sensitivity.executions?.map(execution => <details key={execution.case_identifier}>
            <summary>{execution.case_identifier}: {execution.status}</summary>
            {execution.reason && <p>{execution.reason}</p>}
            {execution.virtual_organism && research.visualization && <VirtualOrganismResult research={{...research,
              visualization:{...research.visualization,virtual_organism:execution.virtual_organism}}} />}
          </details>)}
        </section>}
      </section>
      <section aria-label="Molecular investigation">
        <h4>Molecular investigation</h4>
        <p>Activities below belong to unrelated neighbouring molecules, not {candidate.identity.name}. Chemical similarity nominates hypotheses; it does not predict potency.</p>
        {candidate.bioactivity_hypotheses.reason && <p>{candidate.bioactivity_hypotheses.reason}</p>}
        {!candidate.bioactivity_hypotheses.targets.length && <p>No bioactivity-space target hypotheses supported in this bounded search. This is not evidence of absence of risk.</p>}
        {candidate.bioactivity_hypotheses.targets.map(target => <div key={target.target_accession}>
          <h5>UniProt {target.target_accession}</h5>
          <p>{target.evidence.every(e => e.intended_target === true) ? 'Intended-target neighbour evidence' :
            target.evidence.some(e => e.intended_target == null) ? 'Relationship to intended target unresolved' : 'Alternative molecular hypothesis, not established candidate activity'}</p>
          <table><thead><tr><th>Unrelated ligand</th><th>Assay species</th><th>Local similarity</th><th>Measured neighbour activity</th></tr></thead>
            <tbody>{target.evidence.map((e,i) => <tr key={i}><td>{e.neighbour_chembl_id}</td><td>{e.organism ?? 'Not specified'}</td>
              <td>{e.local_similarity.toPrecision(3)}</td>
              <td>{e.quantitative_neighbour_evidence.kind}: {e.quantitative_neighbour_evidence.value_umol_l.toPrecision(4)} µmol/L<br />{e.quantitative_neighbour_evidence.uncertainty}</td></tr>)}</tbody></table>
          <details><summary>Target function and tissue relevance</summary><p>{target.tissue_relevance.limitation ?? target.tissue_relevance.reason}</p>
            <ul>{target.tissue_relevance.go?.map(g => <li key={g.identifier}>{g.identifier}: {g.term} ({g.evidence})</li>)}</ul>
            {target.tissue_relevance.expression?.map((e,i) => <p key={i}><a href={e.source_url} target="_blank" rel="noreferrer">Exact identity-matched expression source</a>: {e.historical_qualification} · SHA-256 {e.source_sha256}</p>)}
          </details>
        </div>)}
        {investigation.bioactivity_structural && <section aria-label="Bioactivity structural evaluation">
          <h5>Structure-supported hypothesis evaluation</h5>
          <p>{investigation.bioactivity_structural.reason ?? 'Neighbour assays nominate targets; an exact active neighbour or a separately assay-qualified reference defines the observed pocket. Scores are not candidate potency, selectivity or safety.'}</p>
          {investigation.bioactivity_structural.screening?.candidates.find(c=>c.candidate_identifier===candidate.candidate_identifier)?.targets.map(target =>
            <p key={target.target.uniprot_accession}>{target.target.uniprot_accession}: {target.status} · PDB {target.target.pdb_id}, chain {target.target.chain}. {target.reason ??
              `Vina scores: ${target.docking?.vina_scores_kcal_per_mol.join(', ')} kcal/mol (uncalibrated docking evidence only).`}</p>)}
          <details><summary>Structural refusals and bounded coverage</summary>
            <ul>{investigation.bioactivity_structural.panel?.candidates.find(c=>c.candidate_identifier===candidate.candidate_identifier)?.excluded.map((item,i)=>
              <li key={i}>{item.target_accession ?? 'Structure'}: {item.reason}</li>)}</ul>
          </details>
        </section>}
        <h5>Exposure × activity relevance</h5>
        {!candidate.exposure_activity.length && <p>No compatible candidate-specific quantitative activity/exposure pair. Docking scores were not converted to potency.</p>}
        {candidate.exposure_activity.map((a,i) => <p key={i}>{a.target_accession}: {a.status.replaceAll('_', ' ')}. {a.reason ?? a.limitation} {a.uncertainty}</p>)}
      </section>
      <section aria-label="Functional models">
        <h4>Functional models</h4>
        {!candidate.functional_models.length && <p>No reviewed mechanistic model catalogue configured. No physiological story is generated.</p>}
        {candidate.functional_models.map((f,i) => <div key={i}>
          {f.status === 'computed' && f.result ? <>
            <h5>{f.result.model.name}</h5><p>Species: {f.result.model.species} · level: {f.result.inference_level.replaceAll('_', ' ')} · numerical replay {f.result.verification.passed ? 'passed' : 'failed'}</p>
            {f.exposure_case_identifier && <p>Conditional native input case: {f.exposure_case_identifier}. This is not a nominal prediction.</p>}
            <p>Numerical replay checks software reproducibility; it does not establish independent scientific certification or clinical validity.</p>
            <table><thead><tr><th>Model output</th><th>Baseline final</th><th>Perturbed final</th><th>Difference</th></tr></thead>
              <tbody>{Object.entries(f.result.response).map(([key,r]) => <tr key={key}><td>{key} ({r.unit})</td><td>{r.baseline_final.toPrecision(5)}</td><td>{r.perturbed_final.toPrecision(5)}</td><td>{r.final_difference.toPrecision(5)}</td></tr>)}</tbody></table>
            <p>{f.result.limitation}</p><small>Model SHA-256 {f.result.model.sha256}</small>
          </> : <p><strong>{f.model_identifier}: not computed.</strong> {f.reason}</p>}
        </div>)}
      </section>
      <details><summary>Prospective-source eligibility audit</summary><ul>{candidate.dossier_acquisition.eligibility.decisions.map(d => <li key={d.identifier}>{d.identifier}: {d.status.replaceAll('_',' ')} · {d.reason}</li>)}</ul></details>
    </section>)}
    <p>{investigation.verification_scope}. No clinical consequence is automatically inferred.</p>
    <small>Policy SHA-256: {investigation.policy_sha256 ?? 'unconfigured'}</small>
  </article>
}
