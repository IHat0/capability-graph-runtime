import type { ResearchSessionWorkspace } from '../hooks/useResearchSession'
import { VirtualOrganismResult } from './VirtualOrganismResult'
import { FunctionalPredictionTable, FunctionalTissueEvidence, PhysiologicalModels } from './FunctionalPharmacologyResult'

export function VirtualInvestigationResult({ research }: { research: ResearchSessionWorkspace }) {
  const investigation = research.visualization?.prospective_assessment?.virtual_investigation
  if (!investigation) return null
  const sponsorSide = investigation.candidates.some(c => c.dossier_acquisition.experiment_provenance?.experiment_class === 'sponsor_side_prospective')
  const provenanceLabels: Record<string, string> = {
    historically_public_measurement: 'Historically public sourced measurement', sourced_measurement: 'Sourced measurement · modern recovery',
    sponsor_side_measured_development: 'Actual supplied sponsor measurement', sourced_foundational_information: 'Sourced foundational information',
    predicted_computational_input: 'Predicted · calibrated model', sponsor_side_scenario_assumption: 'Sponsor-side scenario assumption', missing: 'Missing',
  }
  return <article className="research-result virtual-investigation" aria-label="Virtual Investigation">
    <p className="section-kicker">Verified evidence chain</p>
    <h2>Virtual Investigation</h2>
    <p>Exposure → molecular hypotheses → functional models</p>
    {sponsorSide && <p><strong>Sponsor-side prospective experiment</strong> · Not an authentic private historical dataset. Sourced measurements, predictions and exploratory scenario assumptions remain distinct.</p>}
    <p>{sponsorSide ? 'Public-history comparator cutoff' : 'Historical cutoff'}: {investigation.prospective_policy.cutoff_instant ?? investigation.prospective_policy.cutoff ?? 'Not configured; candidate-specific historical inputs are quarantined'}. General modern annotations are not silently described as historically available.</p>
    <p>{sponsorSide ? 'Modern recovery may supply isolated ordinary ADME/PK measurements with original-source provenance; it is not claimed to have been historically public.' : 'Candidate measurements require exact dated original-source evidence.'} Candidate-specific clinical and physiological observations are excluded regardless of date.</p>
    {investigation.candidates.map(candidate => <section key={candidate.candidate_identifier} aria-label={`${candidate.identity.name} virtual investigation`}>
      <h3>{candidate.identity.name}</h3>
      <p><strong>{candidate.candidate_status}</strong> · {candidate.assessment_reason}</p>
      {candidate.functional_research_assessment && <section aria-label="Functional research concern gates">
        <h4>Functional research assessment: {candidate.functional_research_assessment.status}</h4>
        <p>{candidate.functional_research_assessment.reason} {candidate.functional_research_assessment.scope} REJECT is unavailable.</p>
        {candidate.functional_research_assessment.rule_results.map(rule=><p key={rule.identifier}>{rule.identifier}: {rule.passed ? 'research gates passed' : `missing gates: ${rule.missing_gates.join(', ')}`}</p>)}
      </section>}
      <p>Evidence levels: {Object.entries(candidate.inference_levels).map(([level, available]) => `${level.replaceAll('_', ' ')}: ${available ? 'supported at stated scope' : 'not established'}`).join(' · ')}</p>
      <section aria-label="Virtual Human">
        <h4>Virtual Human</h4>
        <p>{candidate.regimen.source ?? candidate.regimen.reason ?? candidate.regimen.status.replaceAll('_', ' ')}</p>
        {candidate.dossier_acquisition.experiment_provenance && <section aria-label="Sponsor-side input provenance">
          <h5>Sponsor-side input provenance</h5>
          <p>No private historical measurement claim. Ranges are conditional scenarios, not clinical confidence intervals.</p>
          <table><thead><tr><th>Input</th><th>Value / levels</th><th>Units</th><th>Evidence class</th><th>Uncertainty / justification</th></tr></thead>
            <tbody>{candidate.dossier_acquisition.experiment_provenance.input_receipts.map(input => <tr key={input.identifier}>
              <td>{input.role.replaceAll('_', ' ')}</td><td>{input.values.length ? input.values.map(value => typeof value === 'number' ? value.toPrecision(5) : JSON.stringify(value)).join(' · ') : 'Missing'}</td>
              <td>{input.unit ?? 'Structured control'}</td><td>{provenanceLabels[input.provenance_class] ?? input.provenance_class}</td>
              <td>{input.uncertainty}<br />{input.rationale}<br /><small>Source SHA-256 {input.source_sha256}</small></td>
            </tr>)}</tbody></table>
          <small>Dossier SHA-256 {candidate.dossier_acquisition.experiment_provenance.dossier_file_sha256 ?? candidate.dossier_acquisition.experiment_provenance.canonical_document_sha256}</small>
        </section>}
        <ul>{[...candidate.dossier_acquisition.missing, ...(candidate.regimen.missing ?? [])].map((m,i) => <li key={i}>{m}</li>)}</ul>
        {candidate.dossier_acquisition.conflicts.map((c,i) => <p key={i}>{candidate.dossier_acquisition.experiment_provenance ? 'Declared uncertainty' : 'Unresolved conflict'} — {c.parameter}: {c.reason}</p>)}
        {candidate.virtual_organism && research.visualization && <VirtualOrganismResult research={{ ...research,
          visualization: { ...research.visualization, virtual_organism: candidate.virtual_organism } }} />}
        {!candidate.virtual_organism && <p>No nominal Human exposure curve was computed. Missing inputs are not replaced by invented physiology or clearance.</p>}
        {!!candidate.reviewed_exposure_snapshots?.length && <section aria-label="Published Human exposure endpoints">
          <h5>Published Human exposure endpoints</h5>
          <p>These are source-bound endpoints, not simulated timecourses. An estimated free concentration is not a measured tissue concentration.</p>
          {candidate.reviewed_exposure_snapshots.map(endpoint => <details key={endpoint.datum.identifier} open>
            <summary>{endpoint.species} · {endpoint.organ} · {endpoint.compartment}: {endpoint.values_umol_l[0].toPrecision(5)} µmol/L</summary>
            <p>Evidence class: {endpoint.classification}. Peak time: {endpoint.times_h[0] === null ? 'Not reported; no timestamp invented' : `${endpoint.times_h[0]} h`}.</p>
            <p>{endpoint.compartment_translation ? `Explicit compartment-equivalence assumption: ${endpoint.compartment_translation.rationale}. ${endpoint.compartment_translation.limitations}` : 'No plasma-to-tissue equivalence has been assumed.'}</p>
            <p>{endpoint.uncertainty}<br />{endpoint.applicability}<br />{endpoint.limitation}</p>
            <a href={endpoint.path.startsWith('https://') ? endpoint.path : undefined}>Original exposure source</a>
            <p><small>Original source SHA-256 {endpoint.datum.original_source?.source_sha256 ?? 'Not supplied'}</small></p>
          </details>)}
        </section>}
        {candidate.native_sensitivity && <section aria-label="Conflicting-input sensitivity">
          <h5>{candidate.dossier_acquisition.experiment_provenance ? 'Sponsor-input uncertainty scenarios' : 'Conflicting-input sensitivity'} · nominal result withheld</h5>
          {candidate.native_sensitivity.reason && <p>{candidate.native_sensitivity.reason}</p>}
          {candidate.native_sensitivity.design && <p>{candidate.native_sensitivity.design.cases.length} conditional scenarios of {candidate.native_sensitivity.design.total_combinations} discrete combinations. {candidate.native_sensitivity.design.limitation}</p>}
          {candidate.native_sensitivity.summary && <>
            <p>{candidate.native_sensitivity.summary.qualitative_conclusion}</p>
            <details><summary>Computed exposure ranges, not clinical confidence intervals</summary>
              <table><thead><tr><th>Native endpoint</th><th>Peak range (µmol/L)</th><th>AUC range (µmol·h/L)</th><th>Sampled Tmax range (h)</th></tr></thead>
                <tbody>{Object.entries(candidate.native_sensitivity.summary.endpoint_ranges).map(([endpoint,range]) =>
                  <tr key={endpoint}><td>{endpoint}</td><td>{range.peak_range_umol_l.map(v=>v.toPrecision(5)).join(' – ')}</td>
                    <td>{range.auc_range_umol_h_l.map(v=>v.toPrecision(5)).join(' – ')}</td><td>{range.sampled_tmax_range_h?.map(v=>v.toPrecision(5)).join(' – ') ?? 'Not established'}</td></tr>)}</tbody></table>
            </details>
          </>}
          {candidate.native_sensitivity.executions?.map(execution => <details key={execution.case_identifier}>
            <summary>{execution.case_identifier}: {execution.status}</summary>
            {execution.reason && <p>{execution.reason}</p>}
            {execution.virtual_organism && research.visualization && <VirtualOrganismResult research={{...research,
              visualization:{...research.visualization,virtual_organism:execution.virtual_organism}}} />}
          </details>)}
        </section>}
        {candidate.native_exposure_endpoints && <details>
          <summary>Native Cmax, Tmax, AUC and tissue/plasma ratios</summary>
          {[...(candidate.native_exposure_endpoints.reference ? [{ case_identifier: 'Reference experiment', metrics: candidate.native_exposure_endpoints.reference }] : []),
            ...candidate.native_exposure_endpoints.conditional].map(caseResult => <section key={caseResult.case_identifier} aria-label={`${caseResult.case_identifier} native endpoints`}>
            <h5>{caseResult.case_identifier}</h5><p>{caseResult.metrics.interpretation}</p>
            <table><thead><tr><th>Subject / organ / compartment</th><th>Cmax (µmol/L)</th><th>Sampled Tmax (h)</th><th>Windowed AUC (µmol·h/L)</th></tr></thead>
              <tbody>{caseResult.metrics.endpoints.map((endpoint,i) => <tr key={i}>
                <td>{endpoint.subject_identifier ?? 'Reference'} · {endpoint.organ} · {endpoint.compartment}<br /><small>{endpoint.native_path}</small></td>
                <td>{endpoint.peak_umol_l.toPrecision(5)}</td><td>{endpoint.sampled_tmax_h.length ? endpoint.sampled_tmax_h.join(', ') : 'No positive peak'}</td>
                <td>{endpoint.auc_umol_h_l.toPrecision(5)} over {endpoint.window_h.join(' – ')} h</td>
              </tr>)}</tbody></table>
            <table><thead><tr><th>Subject / tissue</th><th>Concentration basis</th><th>Tissue/plasma AUC ratio</th></tr></thead>
              <tbody>{caseResult.metrics.tissue_plasma_ratios.map((ratio,i) => <tr key={i}>
                <td>{ratio.subject_identifier ?? 'Reference'} · {ratio.organ} · {ratio.compartment}</td>
                <td>{ratio.concentration_basis}</td><td>{ratio.auc_tissue_plasma_ratio.toPrecision(5)}</td>
              </tr>)}</tbody></table>
          </section>)}
        </details>}
      </section>
      <section aria-label="Molecular investigation">
        <h4>Molecular investigation</h4>
        {candidate.functional_activity_prediction && <FunctionalPredictionTable prediction={candidate.functional_activity_prediction} />}
        <FunctionalTissueEvidence evidence={candidate.functional_tissue_relevance} sourcedTargets={candidate.quantitative_activity?.filter(a=>a.functional_assay_sha256).map(a=>a.target_accession)} />
        {candidate.general_safety_panel && <section aria-label="General safety-pharmacology coverage">
          <h5>General safety-pharmacology panel</h5><p>{candidate.general_safety_panel.scope}</p>
          <p>{candidate.general_safety_panel.coverage_statement}</p>
          <details><summary>{candidate.general_safety_panel.targets.length} predeclared targets — tested and untested coverage</summary>
            <table><thead><tr><th>Human target</th><th>General source domains</th><th>Evidence status</th></tr></thead>
              <tbody>{candidate.general_safety_panel.targets.map(target => <tr key={target.target_accession}>
                <td>{target.gene} · {target.target_accession}</td><td>{target.source_domains.join(', ')}</td>
                <td>{target.status.replaceAll('_', ' ')}<br />{target.limitation}</td>
              </tr>)}</tbody></table>
            {candidate.general_safety_panel.source_count_discrepancies.map((d,i) => <p key={i}>Source count discrepancy: {d.source_label} states {d.stated_count}; its explicit list contains {d.enumerated_count}. No targets were invented to reconcile it.</p>)}
          </details><small>Panel SHA-256 {candidate.general_safety_panel.panel_sha256}</small>
        </section>}
        {candidate.target_activity_prediction && <section aria-label="Quantitative binding predictions">
          <h5>Candidate binding hypotheses · not measured potency</h5>
          <p>{candidate.target_activity_prediction.scope}</p>
          <p>These predictions retain their nominal binding-assay concentration basis. They are not functional IC50, unbound tissue potency, occupancy or a physiological response. Refused or unmodeled targets are not negative findings.</p>
          <div className="candidate-table-wrap" role="region" aria-label="Binding estimates and validation" tabIndex={0}>
          <table className="candidate-table binding-evidence-table"><thead><tr><th>Human target</th><th>Binding estimate (µmol/L)</th><th>Domain / validation</th></tr></thead>
            <tbody>{candidate.target_activity_prediction.targets.map((target,i) => <tr key={`${target.target_accession}-${i}`}>
              <td>{target.gene} · {target.target_accession}</td>
              <td>{target.status === 'predicted' && target.value_umol_l != null ? <>
                Predicted {target.kind}: {target.value_umol_l.toPrecision(5)}<br />
                Range: {target.interval_umol_l?.map(v=>v.toPrecision(5)).join(' – ')}<br />{target.interval_definition}
              </> : <>Not predicted: {target.status.replaceAll('_',' ')}. {target.reason}</>}
                <br />{target.uncertainty ?? target.limitation}</td>
              <td>{target.applicability?.status.replaceAll('_',' ')}
                {target.research_gate && <details><summary>Internal held-out research gate: {target.research_gate.accepted ? 'passed' : 'failed'}</summary>
                  <p>{target.research_gate.scope}</p>
                  <p>n = {target.research_gate.metrics.n}; log-space MAE = {target.research_gate.metrics.mae?.toPrecision(4) ?? 'unavailable'}; RMSE = {target.research_gate.metrics.rmse?.toPrecision(4) ?? 'unavailable'}; marginal coverage = {target.research_gate.metrics.interval_coverage?.toPrecision(4) ?? 'unavailable'}; rank correlation = {target.research_gate.metrics.spearman?.toPrecision(4) ?? 'unavailable'}.</p>
                  {!!target.research_gate.failed_metrics.length && <p>Failed metrics: {target.research_gate.failed_metrics.join(', ')}.</p>}
                  <small>Model SHA-256 {target.model_sha256}</small>
                </details>}
              </td>
            </tr>)}</tbody></table>
          </div>
          <small>Frozen model set SHA-256 {candidate.target_activity_prediction.manifest_sha256}</small>
        </section>}
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
        {!!candidate.quantitative_activity?.some(a=>a.functional_assay_sha256) && <section aria-label="Measured pharmacological assays">
          <h5>Measured pharmacological assays · separate from binding predictions</h5>
          <p>Endpoint, action and concentration basis are retained. Pharmacology prerequisites alone do not qualify a Human model transfer.</p>
          {candidate.quantitative_activity.filter(a=>a.functional_assay_sha256).map(a=><details key={a.functional_assay_sha256} open>
            <summary>{a.target_accession} · {a.species} · {a.assay_type} {a.kind}: {a.value.toPrecision(5)} {a.unit}</summary>
            <p>Established action: {a.functional_direction ?? 'Unknown'} · Hill coefficient: {a.hill_coefficient ?? 'Not sourced'}.</p>
            <p>Concentration basis: {a.concentration_basis.replaceAll('_',' ')}{a.concentration_basis_classification && ` · ${a.concentration_basis_classification}`}. {a.measured_unbound_concentration === false && 'Unbound assay concentration was not measured.'}</p>
            <p>Assay host: {a.assay_host ?? 'Not supplied'} · {a.assay_context}.</p>
            <p>{a.functional_transfer_supported ? 'Pharmacology prerequisites present; exact model, tissue and exposure transfer still requires qualification.' : 'Not eligible for physiological transfer under the current pharmacology contract.'}</p>
            <p>{a.uncertainty}<br />{a.applicability}</p>
            <a href={a.source.startsWith('https://') ? a.source : undefined}>Original assay source</a>
            <p><small>Original source SHA-256 {a.datum?.original_source?.source_sha256 ?? 'Not supplied'}<br />Extraction SHA-256 {a.source_sha256}<br />Assay contract SHA-256 {a.functional_assay_sha256}</small></p>
          </details>)}
        </section>}
        <h5>Exposure × activity relevance</h5>
        {!candidate.exposure_activity.length && <p>No compatible candidate-specific quantitative activity/exposure pair. Docking scores were not converted to potency.</p>}
        {candidate.exposure_activity.map((a,i) => <div key={i}><p>{a.target_accession}: {a.status.replaceAll('_', ' ')}. {a.reason ?? a.limitation} {a.uncertainty}</p>
          {a.activity_interval_umol_l && <p>Activity range: {a.activity_interval_umol_l.map(v=>v.toPrecision(5)).join(' – ')} µmol/L. {a.interval_definition}</p>}
          {a.comparisons && <details><summary>Conditional exposure/activity margins</summary>
            <table><thead><tr><th>Case / subject / native path</th><th>Peak (µmol/L)</th><th>Peak/activity ratio range</th><th>Comparison</th></tr></thead>
              <tbody>{a.comparisons.map((c,j)=><tr key={j}><td>{c.exposure_case_identifier ?? 'Reference'} · {c.subject_identifier ?? 'Reference'} · {c.native_path}</td>
                <td>{c.peak_umol_l.toPrecision(5)}</td><td>{c.peak_activity_ratio_interval.map(v=>v.toPrecision(5)).join(' – ')}</td><td>{c.status.replaceAll('_', ' ')}</td></tr>)}</tbody></table>
          </details>}
        </div>)}
      </section>
      <PhysiologicalModels models={candidate.functional_models} />
      <details><summary>Prospective-source eligibility audit</summary><ul>{candidate.dossier_acquisition.eligibility.decisions.map(d => <li key={d.identifier}>{d.identifier}: {d.status.replaceAll('_',' ')} · {d.reason}</li>)}</ul></details>
    </section>)}
    <p>{investigation.verification_scope}. No clinical consequence is automatically inferred.</p>
    <small>Policy SHA-256: {investigation.policy_sha256 ?? 'unconfigured'}</small>
  </article>
}
