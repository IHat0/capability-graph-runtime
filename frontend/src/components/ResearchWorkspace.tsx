import type { ResearchInputArtifactType } from '../api/types'
import type { ResearchSessionWorkspace } from '../hooks/useResearchSession'
import { VirtualOrganismResult } from './VirtualOrganismResult'

const attachmentKinds: Array<{ value: ResearchInputArtifactType; label: string; accept: string }> = [
  { value: 'molecular_structure', label: 'Molecule structure', accept: '.pdb,.sdf,.mol,.mol2,.pdbqt,.smi,.smiles' },
  { value: 'pbpk_compound_dossier', label: 'Species ADME dossier', accept: '.json' },
  { value: 'quantitative_activity_evidence', label: 'Quantitative activity evidence', accept: '.json' },
  { value: 'protein_structure', label: 'Protein structure', accept: '.pdb,.pdbqt' },
  { value: 'ligand_structure', label: 'Ligand structure', accept: '.sdf,.mol,.mol2,.pdb,.pdbqt,.smi,.smiles' },
]

function statusLabel(status: string): string {
  return status.replaceAll('_', ' ')
}

interface ResearchWorkspaceProps {
  research: ResearchSessionWorkspace
  inspector?: boolean
}

export function ResearchWorkspace({ research, inspector = false }: ResearchWorkspaceProps) {
  const session = research.session
  const constructionComplete = Boolean(session?.scientist_result && research.visualization?.construction_summary)
  const assessmentComplete = Boolean(session?.scientist_result && research.visualization?.prospective_assessment)
  const organismComplete = Boolean(session?.scientist_result && research.visualization?.virtual_organism)
  const compactResult = constructionComplete || assessmentComplete || organismComplete
  const responseNeeded = session?.status === 'awaiting_clarification' || session?.status === 'awaiting_approval'
  const acquiredApprovalNeeded = Boolean(session?.unapproved_input_artifact_identifiers.length)
  return (
    <main className={`research-workspace${inspector ? ' research-workspace--inspector' : ''}`} id="workspace-home">
      <header className="research-heading">
        <div>
          <p className="section-kicker">Unified research session</p>
          <h1>{compactResult ? 'Research result' : 'Ask Pulsate a scientific question.'}</h1>
          <p>{compactResult ? session?.conversation.find(turn => turn.role === 'scientist')?.content : 'Describe your goal and any conditions you know. Pulsate will explain its assumptions and ask when a necessary detail is missing.'}</p>
        </div>
        {session && <button className="secondary-button" type="button" onClick={research.newSession}>New question</button>}
      </header>

      <details className="research-resume">
        <summary>Reopen a research session</summary>
        <form onSubmit={(event) => { event.preventDefault(); void research.resume() }}>
          <label htmlFor="saved-research-session">Saved session identifier</label>
          <input id="saved-research-session" value={research.sessionIdentifierInput}
            onChange={(event) => research.setSessionIdentifierInput(event.target.value)} />
          <button className="secondary-button" type="submit" disabled={research.busy || !research.sessionIdentifierInput.trim()}>Open session</button>
        </form>
        <p>Bookmark this page to return to the same research later.</p>
      </details>

      {!session ? (
        <form className="research-composer" onSubmit={(event) => { event.preventDefault(); void research.start() }}>
          <label htmlFor="research-question">Scientific question</label>
          <textarea
            id="research-question"
            value={research.question}
            onChange={(event) => research.setQuestion(event.target.value)}
            placeholder="State the system, the analysis or design goal, and any conditions you already know."
            disabled={research.busy}
          />
          <AttachmentControls research={research} />
          <button className="primary-button" type="submit" disabled={research.busy || !research.question.trim()}>
            {research.busy ? 'Understanding question…' : 'Start research session'}
          </button>
        </form>
      ) : (
        <>
          <section className="research-status" aria-live="polite">
            <span className={`research-status__dot research-status__dot--${session.status}`} />
            <div>
              <strong>{statusLabel(session.status)}</strong>
              <p>{assessmentComplete ? 'Prospective computational assessment complete. Screening evidence is not proof of efficacy or safety.' : research.visualization?.construction_summary ? 'Generated geometry and electronic calculations are complete. Inspect the molecule and computed values below.' : session.scientist_result ? (session.scientist_result.principal_result ?? 'The calculation is complete. Review the verified result below.') : session.scientist_summary}</p>
              <small>Session {session.session_identifier} · revision {session.revision}</small>
            </div>
          </section>

          {constructionComplete && <ConstructionResult research={research} />}
          {assessmentComplete && <ProspectiveResult research={research} />}
          {organismComplete && <VirtualOrganismResult research={research} />}
          {session.execution_steps && session.execution_steps.length > 0 && (compactResult
            ? <details><summary>Completed calculation record</summary><ExecutionProgress steps={session.execution_steps} /></details>
            : <ExecutionProgress steps={session.execution_steps} />)}
          {session.status === 'failed' && <p role="alert">This calculation did not complete. Review its failed step. Its evidence and conversation are preserved.</p>}

          <details open={!compactResult}><summary>Session conversation</summary><ol className="research-conversation">
            {session.conversation.map((turn) => {
              const evidenceArtifacts = session.accepted_evidence.flatMap((evidence) => (
                evidence.supporting_quotes.some((quote) => quote.turn_identifier === turn.turn_identifier)
                  ? evidence.artifact_references
                  : []
              ))
              return (
              <li key={turn.turn_identifier} className={`research-turn research-turn--${turn.role}`}>
                <span>{turn.role === 'scientist' ? 'You' : 'Pulsate'}</span>
                <p>{turn.content}</p>
                {evidenceArtifacts.length > 0 && <div className="research-turn__entities">{evidenceArtifacts.map((artifact) => (
                  <button type="button" key={artifact.artifact_identifier} onClick={() => void research.openScene(artifact.artifact_identifier)}>
                    Focus {statusLabel(artifact.artifact_type)}
                  </button>
                ))}</div>}
              </li>
              )
            })}
          </ol></details>

          {session.intent_proposal && responseNeeded && (
            <label className="research-confirmation">
              <input
                type="checkbox"
                checked={research.acceptIntentProposal}
                onChange={(event) => research.setAcceptIntentProposal(event.target.checked)}
              />
              Confirm the proposed goal: {statusLabel(session.intent_proposal.task_type)}
            </label>
          )}

          {session.requirement_proposal && responseNeeded && (
            <section className="research-evidence-proposal">
              <strong>Research operations review required</strong>
              <p>{session.requirement_proposal.summary}</p>
              <ul>
                {session.requirement_proposal.requirements.map((item) => (
                  <li key={item.operation}>
                    <span>{statusLabel(item.operation)}</span>
                    <q>{item.supporting_quote}</q>
                  </li>
                ))}
              </ul>
              <label className="research-confirmation">
                <input
                  type="checkbox"
                  checked={research.acceptRequirementProposal}
                  onChange={(event) => research.setAcceptRequirementProposal(event.target.checked)}
                />
                Confirm these operations and requested outputs for deterministic planning
              </label>
            </section>
          )}

          {session.evidence_proposal && responseNeeded && (
            <section className="research-evidence-proposal">
              <strong>Evidence review required</strong>
              <p>{session.evidence_proposal.summary}</p>
              {session.evidence_proposal.supporting_quotes.length > 0 && (
                <ul>
                  {session.evidence_proposal.supporting_quotes.map((item) => (
                    <li key={item.field_name}>
                      <span>{statusLabel(item.field_name)}</span>
                      <q>{item.supporting_quote}</q>
                    </li>
                  ))}
                </ul>
              )}
              {session.evidence_proposal.partial_covalent_reaction_target && (
                <p>
                  This is partial evidence. Confirmed fields will be retained, and
                  Pulsate will ask only for the remaining fields.
                </p>
              )}
              {session.evidence_proposal.entity_candidates.length > 0 && (
                <ul>
                  {session.evidence_proposal.entity_candidates.map((candidate) => (
                    <li key={`${candidate.source_kind}-${candidate.source_identifier}-${candidate.structure_identifier ?? ''}`}>
                      <span>{candidate.display_label}</span>
                      <small>{candidate.source_kind} {candidate.source_identifier}{candidate.structure_identifier ? ` · structure ${candidate.structure_identifier}` : ''}</small>
                    </li>
                  ))}
                </ul>
              )}
              {session.evidence_proposal.source_kind !== 'entity_resolution_candidates' && <label className="research-confirmation">
                <input
                  type="checkbox"
                  checked={research.acceptEvidenceProposal}
                  onChange={(event) => research.setAcceptEvidenceProposal(event.target.checked)}
                />
                Confirm these exact values and resolved inputs for this session
              </label>}
            </section>
          )}

          {acquiredApprovalNeeded && responseNeeded && !session.evidence_proposal && (
            <label className="research-confirmation">
              <input
                type="checkbox"
                checked={research.acceptEvidenceProposal}
                onChange={(event) => research.setAcceptEvidenceProposal(event.target.checked)}
              />
              Confirm the acquired or generated input provenance for this session
            </label>
          )}

          {(responseNeeded || session.status === 'completed') && (
            <form className="research-composer research-composer--reply" onSubmit={(event) => { event.preventDefault(); void research.respond() }}>
              <label htmlFor="research-reply">Your reply</label>
              <textarea
                id="research-reply"
                value={research.reply}
                onChange={(event) => research.setReply(event.target.value)}
                placeholder={research.visualization?.virtual_organism?.status === 'insufficient_parameterization' ? 'Provide the missing species ADME evidence or attach a reviewed dossier to continue this exposure experiment.' : session.status === 'completed' ? "Ask about the recorded candidates, ranking, or limitations." : "Answer Pulsate’s question. You can also attach exact input files here."}
                disabled={research.busy}
              />
              {(session.status !== 'completed' || research.visualization?.virtual_organism?.status === 'insufficient_parameterization') && <AttachmentControls research={research} />}
              <button className="primary-button" type="submit" disabled={research.busy || (!research.reply.trim() && !research.acceptIntentProposal && !research.acceptEvidenceProposal && !research.acceptRequirementProposal && research.attachments.length === 0)}>
                {research.busy ? 'Processing…' : session.status === 'completed' && research.visualization?.virtual_organism?.status !== 'insufficient_parameterization' ? 'Ask about evidence' : 'Continue'}
              </button>
            </form>
          )}

          {session.status === 'planned' && (
            <section className="research-ready">
              <div>
                <strong>Your research plan is ready.</strong>
                <p>Run the planned calculations using the reviewed inputs and conditions.</p>
              </div>
              <button className="primary-button" type="button" disabled={research.busy} onClick={() => void research.execute()}>
                {research.busy ? 'Running…' : 'Run research'}
              </button>
            </section>
          )}

          {session.compilation && research.visualization && (
            <section className="research-plan-entities">
              <strong>Structures used in this research</strong>
              <div>{research.visualization.structures.map((structure) => (
                <button type="button" key={structure.artifact_identifier} onClick={() => void research.openScene(structure.artifact_identifier)}>
                  {structure.label}
                </button>
              ))}</div>
            </section>
          )}

          {session.scientist_result && (compactResult ? <>
            <details><summary>Scientific answer, assumptions, and methods</summary><Result result={session.scientist_result} research={research} /></details>
          </> : <Result result={session.scientist_result} research={research} />)}

          {research.visualization && <DiscoveryWorkspace research={research} />}

          {research.visualizableArtifacts.length > 0 && (
            <section className="research-scenes">
              <h2>Structure evidence</h2>
              <p>Open coordinates directly from an exact file attached to this session.</p>
              {research.visualizableArtifacts.map((artifact) => (
                <button
                  className="secondary-button"
                  type="button"
                  key={artifact.artifact_identifier}
                  disabled={research.busy}
                  onClick={() => void research.openScene(artifact.artifact_identifier)}
                >
                  <span>{statusLabel(artifact.artifact_type)}</span>
                  <small>{research.loadingSceneArtifact === artifact.artifact_identifier ? 'Opening…' : 'View'}</small>
                </button>
              ))}
            </section>
          )}
        </>
      )}

      {research.error && <p className="inline-error" role="alert">{research.error}</p>}
    </main>
  )
}

function ExecutionProgress({ steps }: { steps: NonNullable<NonNullable<ResearchSessionWorkspace['session']>['execution_steps']> }) {
  const completed = steps.filter((step) => step.status === 'succeeded').length
  return <section className="research-execution" aria-label="Calculation progress">
    <h2>Calculation progress</h2>
    <p>{completed} of {steps.length} steps completed</p>
    <progress value={completed} max={steps.length} aria-label="Completed calculation steps" />
    <ol>{steps.map((step) => <li key={step.step_identifier}>
      <strong>{statusLabel(step.capability_name.split('.').slice(1).join(' ') || step.capability_name)}</strong>
      <span> — {statusLabel(step.status)}</span>
      {step.error_message && <p role="alert">{step.error_message}</p>}
    </li>)}</ol>
  </section>
}

function readableValue(value: unknown): string {
  if (value === null || value === undefined) return 'not supplied'
  if (typeof value === 'number') return Number.isFinite(value) ? value.toPrecision(5) : 'invalid'
  if (typeof value === 'string' || typeof value === 'boolean') return String(value)
  return JSON.stringify(value)
}

function DiscoveryWorkspace({ research }: { research: ResearchSessionWorkspace }) {
  const visualization = research.visualization
  if (!visualization) return null
  const selectedInteractions = visualization.interactions.filter((item) => (
    !research.selectedCandidateIdentifier || item.candidate_identifier === research.selectedCandidateIdentifier
  ))
  const visibleOverlays = visualization.overlays.filter((item) => (
    (!research.selectedCandidateIdentifier || !item.candidate_identifier || item.candidate_identifier === research.selectedCandidateIdentifier)
    && (!research.verifiedPropertiesOnly || item.verification_status === 'verified')
  ))
  return (
    <section className="discovery-workspace" aria-labelledby="discovery-workspace-title">
      <header className="discovery-workspace__heading">
        <div>
          <p className="section-kicker">Evidence-grounded visualization</p>
          <h2 id="discovery-workspace-title">Discovery workspace</h2>
          <p>Every structure, contact, overlay, and comparison below resolves to persisted evidence.</p>
        </div>
        <button className="secondary-button" type="button" onClick={research.exportVisualization}>Export scene manifest</button>
      </header>

      <div className="discovery-workspace__structures">
        <h3>Session structures</h3>
        <div className="discovery-chip-list">
          {visualization.structures.map((structure) => (
            <div key={structure.artifact_identifier} className={structure.selected ? 'discovery-chip discovery-chip--selected' : 'discovery-chip'}>
              <button type="button" onClick={() => void research.openScene(structure.artifact_identifier)}>
                <strong>{structure.label}</strong>
                <small>{statusLabel(structure.role)}{structure.generation === null ? '' : ` · generation ${structure.generation}`}</small>
              </button>
              {structure.conformation_count > 1 && <label>
                Conformation
                <select defaultValue="0" onChange={(event) => void research.openConformation(structure.artifact_identifier, Number(event.target.value))}>
                  {Array.from({ length: structure.conformation_count }, (_, index) => <option key={index} value={index}>Pose {index + 1}</option>)}
                </select>
              </label>}
            </div>
          ))}
        </div>
      </div>

      {visualization.candidates.length > 0 && (
        <div className="discovery-workspace__candidates">
          <div className="discovery-section-heading">
            <div><h3>Ranked candidates</h3><p>Selection, properties, verification, and lineage remain candidate-scoped.</p></div>
            <label><input type="checkbox" checked={research.verifiedPropertiesOnly} onChange={(event) => research.setVerifiedPropertiesOnly(event.target.checked)} />Verified properties only</label>
          </div>
          <div className="candidate-table-wrap">
            <table className="candidate-table">
              <thead><tr><th>Candidate</th><th>Generation</th><th>Parents</th><th>Properties</th><th>State</th><th /></tr></thead>
              <tbody>{visualization.candidates.map((candidate) => (
                <tr key={candidate.candidate_identifier} data-selected={candidate.selected || undefined}>
                  <td><strong>{candidate.display_name ?? candidate.candidate_identifier}</strong></td>
                  <td>{candidate.generation}</td>
                  <td>{candidate.parent_candidate_identifiers.join(', ') || 'seed'}</td>
                  <td>{candidate.properties_and_calculations.map((property, position) => (
                    <span key={`${candidate.candidate_identifier}-property-${position}`}>{String(property.objective_identifier ?? 'property')}: {readableValue(property.value)}</span>
                  ))}</td>
                  <td>{candidate.selected ? 'selected' : candidate.verification.length ? 'verified' : 'evaluated'}</td>
                  <td><button type="button" onClick={() => void research.focusCandidate(candidate.candidate_identifier)}>Focus in 3D</button></td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        </div>
      )}

      {visualization.comparisons.length > 0 && (
        <div className="discovery-workspace__comparisons">
          <h3>Aligned candidate comparisons</h3>
          <div className="comparison-list">{visualization.comparisons.map((comparison) => (
            <button type="button" key={comparison.comparison_identifier} onClick={() => void research.compareCandidates(comparison.left_identifier, comparison.right_identifier)}>
              <strong>{comparison.left_identifier} → {comparison.right_identifier}</strong>
              <small>{statusLabel(comparison.kind)}</small>
              <small>{readableValue(comparison.differences)}</small>
            </button>
          ))}</div>
        </div>
      )}

      <div className="discovery-workspace__evidence-grid">
        <section>
          <h3>Computed interactions</h3>
          {selectedInteractions.length ? <ul>{selectedInteractions.map((interaction) => (
            <li key={interaction.interaction_identifier}>
              <strong>{statusLabel(interaction.interaction_type)}</strong>
              <span>{interaction.distance_angstrom?.toFixed(3) ?? 'n/a'} Å{interaction.angle_degree === null ? '' : ` · ${interaction.angle_degree.toFixed(2)}°`}</span>
              <small>{interaction.calculation_method}</small>
            </li>
          ))}</ul> : <p>No computed contacts apply to the current focus.</p>}
        </section>
        <section>
          <h3>Calculation overlays</h3>
          {visibleOverlays.length ? <ul>{visibleOverlays.map((overlay) => (
            <li key={overlay.overlay_identifier} data-verification={overlay.verification_status}>
              <strong>{overlay.label}</strong>
              <span>{readableValue(overlay.value)} {overlay.unit ?? ''}</span>
              <small>{overlay.method ?? 'persisted computation'} · {statusLabel(overlay.verification_status)}</small>
              {overlay.uncertainty && <em>{overlay.uncertainty}</em>}
            </li>
          ))}</ul> : <p>No overlays match the current verification filter.</p>}
        </section>
      </div>

      <details className="discovery-exports">
        <summary>Export structures, evidence, and reports</summary>
        <div>{visualization.export_items.map((item) => (
          <button type="button" key={item.artifact_identifier} onClick={() => void research.exportArtifact(item.artifact_identifier)}>
            <span>{statusLabel(item.category)} · {statusLabel(item.artifact_type)}</span>
            <small>{item.artifact_identifier}</small>
          </button>
        ))}</div>
      </details>
    </section>
  )
}

function AttachmentControls({ research }: { research: ResearchSessionWorkspace }) {
  return (
    <div className="research-attachments">
      <p>Optional exact inputs</p>
      <div className="research-attachment-actions">
        {attachmentKinds.map((kind) => (
          <label className="secondary-button" key={kind.value}>
            {kind.label}
            <input
              className="sr-only"
              type="file"
              multiple
              accept={kind.accept}
              disabled={research.busy}
              onChange={(event) => {
                research.addAttachments(event.target.files, kind.value)
                event.target.value = ''
              }}
            />
          </label>
        ))}
      </div>
      {research.attachments.length > 0 && (
        <ul className="research-attachment-list">
          {research.attachments.map((attachment) => (
            <li key={attachment.identifier}>
              <span><strong>{attachment.file.name}</strong><small>{statusLabel(attachment.artifactType)}</small></span>
              <button type="button" onClick={() => research.removeAttachment(attachment.identifier)}>Remove</button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function ConstructionResult({ research }: { research: ResearchSessionWorkspace }) {
  const built = research.visualization?.construction_summary
  if (!built) return null
  return <article className="research-result">
    <p className="section-kicker">Generated structure · identity {built.identity_verified ? 'verified' : 'unverified'}</p>
    <h2>Constructed {built.name}</h2>
    <p><strong>{built.name}</strong> · {built.formula} · {built.atom_count} atoms</p>
    <p>Displayed coordinates generated by Pulsate. Drag to rotate; scroll to zoom.</p>
    {!research.scene && <button type="button" onClick={() => void research.openScene(built.structure_artifact_identifier)}>Show generated molecule in 3D</button>}
    {built.workflow && <p aria-label="Completed construction workflow">{built.workflow.join(' → ')}</p>}
    <p>{built.converged_conformers} of {built.generated_conformers} generated geometries optimized.</p>
    <table><thead><tr><th>Calculation</th><th>Computed value</th></tr></thead><tbody>
      {built.energies.map(item => <tr key={item.label}><td>{item.label}</td><td>{Math.abs(item.value) < 1e-5 ? item.value.toExponential(3) : item.value.toFixed(9)} {item.unit}</td></tr>)}
    </tbody></table>
    {built.selected_compute === 'classical' ? <><p><strong>Classical computation was sufficient.</strong> The quantum path was not invoked.</p><details><summary>Why these computation methods?</summary><p>{built.computation_reason}</p></details></>
      : <p>Reduced electronic benchmark: {built.active_electron_count} electrons, {built.active_spatial_orbital_count} spatial orbitals, {built.logical_qubits} qubits. IBM hardware: {statusLabel(built.hardware_status ?? 'not_prepared')}.</p>}
    <p>The force-field and electronic energies are different observables. RHF/STO-3G is a minimal-basis gas-phase reference, not a high-accuracy energy prediction.</p>
    <button type="button" onClick={() => void research.exportArtifact(built.structure_artifact_identifier)}>Download SDF evidence</button>
    {built.xyz_artifact_identifier && <button type="button" onClick={() => void research.exportArtifact(built.xyz_artifact_identifier!)}>Download XYZ evidence</button>}
  </article>
}

function ProspectiveResult({ research }: { research: ResearchSessionWorkspace }) {
  const assessment = research.visualization?.prospective_assessment
  if (!assessment) return null
  const target = assessment.target_selection
  return <article className="research-result prospective-result">
    <p className="section-kicker">Prospective assessment · computed evidence only</p>
    <h2>Candidate investigation</h2>
    <p>Intended target: {target.domain.description} · UniProt {target.uniprot_accession}</p>
    <p>Experimental model: PDB {target.selected.pdb_id}, chain {target.selected.chain}, {target.selected.resolution_angstrom} Å.</p>
    {assessment.candidates.map(candidate => <section key={candidate.candidate_identifier}>
      <h3>{candidate.name}</h3>
      <p><strong>Overall: {statusLabel(candidate.recommendation)}</strong></p><p>{candidate.reason}</p>
      <p><strong>Intended-target screening:</strong> Vina {candidate.intended_target.best_vina_score_kcal_per_mol.toFixed(3)} kcal/mol.</p>
      <p>{candidate.intended_target.limitation}</p>
      <button type="button" onClick={() => void research.focusCandidate(candidate.candidate_identifier)}>Inspect candidate and target in 3D</button>
      <details><summary>Computed molecular properties and identity</summary>
        <p>PubChem {candidate.identity.pubchem_cid ?? 'not independently checked'} · {candidate.identity.standard_inchikey}</p>
        <table><thead><tr><th>Graph descriptor</th><th>Computed value</th></tr></thead><tbody>{Object.entries(candidate.properties).map(([key, value]) => <tr key={key}><td>{statusLabel(key)}</td><td>{value.toFixed(3)}</td></tr>)}</tbody></table>
      </details>
      <h4>Liabilities to investigate — hypotheses, not findings</h4>
      {candidate.hypotheses.length ? <ul>{candidate.hypotheses.map(h => <li key={h.metric}>{h.hypothesis} {statusLabel(h.metric)} = {h.computed_value.toFixed(3)} (screen threshold {h.screen_threshold}). {h.limitation}</li>)}</ul>
        : <p>No flags in the implemented descriptor screen. This does not establish safety.</p>}
      <details><summary>Unresolved risk dimensions</summary><ul>{candidate.unsupported_risk_dimensions.map(d => <li key={d}>{d}</li>)}</ul></details>
      {candidate.alternative_targets && <details open><summary>Alternative-target investigation — hypotheses, not toxicity findings</summary>
        {!candidate.alternative_targets.targets.length && <p>No defensible structural panel was obtained within the search bounds. This is missing evidence, not evidence of safety.</p>}
        {candidate.alternative_targets.targets.map(t => <section key={t.target.uniprot_accession}>
          <h4>UniProt {t.target.uniprot_accession} · PDB {t.target.pdb_id}</h4>
          <p>Locally computed chemical similarity: {t.target.similarity.toFixed(3)}. Similarity nominates a hypothesis, not activity.</p>
          {t.comparison && t.status === 'computed' ? <>
            <p>Vina score {t.comparison.alternative_score_kcal_per_mol.toFixed(3)} kcal/mol; raw difference from intended target {t.comparison.raw_score_difference_kcal_per_mol.toFixed(3)} kcal/mol.</p>
            <p>{t.comparison.limitation}</p>
            <details><summary>Sourced functional follow-up hypotheses</summary><ul>{t.functional_hypotheses?.map(h => <li key={h.go_identifier}>{h.go_identifier}: {h.annotation}. {h.hypothesis} {h.limitation}</li>)}</ul></details>
          </> : <p>Not computed: {t.reason}</p>}
        </section>)}
        <ul>{candidate.orthogonal_follow_up?.map(f => <li key={f}>{f}</li>)}</ul>
      </details>}
    </section>)}
    <details><summary>Model selection, assumptions and limitations</summary>
      <p>{target.policy}</p><ul>{[...assessment.assumptions, ...target.limitations].map(t => <li key={t}>{t}</li>)}</ul>
      <p>Deposited mutation records: {target.selected.mutation_records.join('; ') || 'none declared'}.</p>
      <p>Deposited missing residue/atom records: {target.selected.missing_records.join('; ') || 'none declared'}.</p>
    </details>
    <p><strong>Classical computation selected; quantum not invoked.</strong> {assessment.computation_selection.reason}</p>
    <p>{assessment.verification_scope}</p>
    <details><summary>Foundational source audit and blinding limits</summary>
      <ul>{assessment.source_audit.map((s, i) => <li key={i}>{s.class}: <a href={s.url} target="_blank" rel="noreferrer">source record</a> · SHA-256 {s.sha256}</li>)}</ul>
      {assessment.blinding_limitations.map(t => <p key={t}>{t}</p>)}
    </details>
    <p>Assessment SHA-256: {assessment.assessment_sha256}</p>
    <button type="button" onClick={() => void research.exportArtifact(assessment.assessment_artifact_identifier)}>Download prospective assessment evidence</button>
  </article>
}

function Result({ result, research }: { result: NonNullable<ResearchSessionWorkspace['session']>['scientist_result']; research: ResearchSessionWorkspace }) {
  if (!result) return null
  return (
    <article className="research-result">
      <p className="section-kicker">Scientist-facing result · {result.verification_status}</p>
      <h2>Result</h2>
      <p className="research-result__primary">{result.principal_result ?? result.scientific_result}</p>
      <h3>Resolved interpretation</h3>
      <p>{result.resolved_interpretation}</p>
      {result.assumptions.length > 0 && <><h3>Assumptions used</h3><ul>{result.assumptions.map((item) => <li key={item}>{item}</li>)}</ul></>}
      {result.structures_and_entities.length > 0 && <><h3>Structures and entities</h3><div className="research-result__entities">{result.structures_and_entities.map((item, position) => {
        const structure = research.visualization?.structures[position]
        return <button type="button" key={`${item}-${position}`} disabled={!structure} onClick={() => structure && void research.openScene(structure.artifact_identifier)}>{item}</button>
      })}</div></>}
      <h3>Methods</h3>
      <ul>{result.methods.map((item) => <li key={item}>{item}</li>)}</ul>
      {result.candidate_ranking.length > 0 && <><h3>Candidate ranking</h3><ol>{result.candidate_ranking.map((item) => <li key={item}>{item}</li>)}</ol></>}
      {result.confidence_and_uncertainty.length > 0 && <><h3>Confidence</h3><ul>{result.confidence_and_uncertainty.map((item) => <li key={item}>{item}</li>)}</ul></>}
      {result.uncertainty.length > 0 && <><h3>Uncertainty</h3><ul>{result.uncertainty.map((item) => <li key={item}>{item}</li>)}</ul></>}
      {result.important_limitations.length > 0 && <><h3>Limitations</h3><ul>{result.important_limitations.map((item) => <li key={item}>{item}</li>)}</ul></>}
      {result.recommended_next_step && <><h3>Recommended next step</h3><p>{result.recommended_next_step}</p></>}
    </article>
  )
}
