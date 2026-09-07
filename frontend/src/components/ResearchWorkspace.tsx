import type { ResearchInputArtifactType } from '../api/types'
import type { ResearchSessionWorkspace } from '../hooks/useResearchSession'

const attachmentKinds: Array<{ value: ResearchInputArtifactType; label: string; accept: string }> = [
  { value: 'molecular_structure', label: 'Molecule structure', accept: '.pdb,.sdf,.mol,.mol2,.pdbqt,.smi,.smiles' },
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
  const responseNeeded = session?.status === 'awaiting_clarification' || session?.status === 'awaiting_approval'
  const acquiredApprovalNeeded = Boolean(session?.unapproved_input_artifact_identifiers.length)
  return (
    <main className={`research-workspace${inspector ? ' research-workspace--inspector' : ''}`} id="workspace-home">
      <header className="research-heading">
        <div>
          <p className="section-kicker">Unified research session</p>
          <h1>Ask Pulsate a scientific question.</h1>
          <p>Describe your goal and any conditions you know. Pulsate will explain its assumptions and ask when a necessary detail is missing.</p>
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
              <p>{session.scientist_result ? (session.scientist_result.principal_result ?? 'The calculation is complete. Review the verified result below.') : session.scientist_summary}</p>
              <small>Session {session.session_identifier} · revision {session.revision}</small>
            </div>
          </section>

          {session.execution_steps && session.execution_steps.length > 0 && <ExecutionProgress steps={session.execution_steps} />}
          {session.status === 'failed' && <p role="alert">This calculation did not complete. Review its failed step. Its evidence and conversation are preserved.</p>}

          <ol className="research-conversation">
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
          </ol>

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
                placeholder={session.status === 'completed' ? "Ask about the recorded candidates, ranking, or limitations." : "Answer Pulsate’s question. You can also attach exact input files here."}
                disabled={research.busy}
              />
              {session.status !== 'completed' && <AttachmentControls research={research} />}
              <button className="primary-button" type="submit" disabled={research.busy || (!research.reply.trim() && !research.acceptIntentProposal && !research.acceptEvidenceProposal && !research.acceptRequirementProposal && research.attachments.length === 0)}>
                {research.busy ? 'Processing…' : session.status === 'completed' ? 'Ask about evidence' : 'Continue'}
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

          {session.scientist_result && <Result result={session.scientist_result} research={research} />}

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
