import type { WorkflowRunWorkspaceState } from '../hooks/useWorkflowRun'
import type {
  WorkflowGraphDefinitionResponse,
  WorkflowNodeStateResponse,
} from '../api/workflows'

interface WorkflowWorkspaceProps {
  workflow: WorkflowRunWorkspaceState
}

const terminalStatuses = new Set(['succeeded', 'failed', 'blocked', 'cancelled'])

function stateFor(
  nodeIdentifier: string,
  states: WorkflowNodeStateResponse[],
): WorkflowNodeStateResponse | undefined {
  return states
    .filter((state) => state.node_id === nodeIdentifier)
    .sort((left, right) => right.attempt_number - left.attempt_number)[0]
}

function dependencyMap(graph: WorkflowGraphDefinitionResponse): Map<string, string[]> {
  const incoming = new Map<string, string[]>()
  for (const node of graph.nodes) incoming.set(node.node_identifier, [])
  for (const edge of graph.edges) incoming.get(edge.target_node_id)?.push(edge.source_node_id)
  for (const values of incoming.values()) values.sort()
  return incoming
}

export function WorkflowWorkspace({ workflow }: WorkflowWorkspaceProps) {
  const run = workflow.run
  const graph = workflow.graph
  const incoming = graph ? dependencyMap(graph) : new Map<string, string[]>()
  const pendingApprovals = run?.approval_states.filter((item) => item.status === 'pending') ?? []
  const canMutate = run !== null && !terminalStatuses.has(run.graph_state.status)
  const errorLabel = workflow.errorStatus === 401 || workflow.errorStatus === 403
    ? 'Workflow access is not authorized.'
    : workflow.errorStatus === 404
      ? 'Workflow run was not found.'
      : workflow.error

  return (
    <section id="workflow" className="workflow-workspace" aria-labelledby="workflow-workspace-title">
      <header className="workflow-workspace__header">
        <div>
          <span className="eyebrow">Phase 4 orchestration</span>
          <h2 id="workflow-workspace-title">Scientific workflow graph</h2>
          <p>Inspect persistent branches, approvals, retries, budgets, artifacts, and verification state alongside the molecular workspace.</p>
        </div>
        <div className="workflow-run-loader">
          <label htmlFor="workflow-run-identifier">Workflow run</label>
          <div>
            <input
              id="workflow-run-identifier"
              value={workflow.graphRunIdentifierInput}
              disabled={workflow.loading || workflow.mutating}
              placeholder="workflow-run.identifier"
              onChange={(event) => workflow.setGraphRunIdentifierInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') void workflow.open()
              }}
            />
            <button type="button" disabled={workflow.loading || workflow.mutating} onClick={() => void workflow.open()}>
              {workflow.loading ? 'Opening…' : 'Open graph'}
            </button>
            {run ? <button type="button" className="secondary" onClick={workflow.clear}>Clear</button> : null}
          </div>
        </div>
      </header>

      {errorLabel ? <div className="workflow-state workflow-state--error" role="alert">{errorLabel}</div> : null}
      {!run || !graph ? (
        <div className="workflow-empty">
          <strong>{workflow.loading ? 'Loading verified workflow state…' : 'No workflow run open'}</strong>
          <span>Open a run identifier to view its immutable definition and current persisted execution state.</span>
        </div>
      ) : (
        <>
          <div className="workflow-summary" aria-live="polite">
            <div><span>Status</span><strong data-status={run.graph_state.status}>{run.graph_state.status.replaceAll('_', ' ')}</strong></div>
            <div><span>Progress</span><strong>{run.graph_state.completed_nodes}/{run.graph_state.total_nodes} complete</strong></div>
            <div><span>Budget</span><strong>{run.budget_state.status.replaceAll('_', ' ')}</strong></div>
            <div><span>Revision</span><strong>{run.revision}</strong></div>
            <div><span>Project</span><strong>{graph.metadata?.molecular_project_identifier ?? 'Not bound'}</strong></div>
            <div><span>Execution</span><strong>{graph.nodes.some((node) => node.node_kind === 'quantum_ibm') ? 'Selective IBM path' : 'Classical / local quantum'}</strong></div>
          </div>

          <div className="workflow-actions">
            <button type="button" disabled={!canMutate || workflow.mutating} onClick={() => void workflow.resume()}>
              {workflow.mutating ? 'Updating…' : 'Start / resume'}
            </button>
            <button type="button" className="secondary" disabled={workflow.loading || workflow.mutating} onClick={() => void workflow.refresh()}>
              Refresh
            </button>
            <button type="button" className="danger" disabled={!canMutate || workflow.mutating} onClick={() => void workflow.cancel('Cancelled from the workflow workspace.')}>
              Cancel run
            </button>
          </div>

          {pendingApprovals.length > 0 ? (
            <section className="workflow-approvals" aria-labelledby="workflow-approvals-title">
              <h3 id="workflow-approvals-title">Approvals awaiting action</h3>
              {pendingApprovals.map((approval) => (
                <article key={approval.approval_id}>
                  <div><strong>{approval.approval_id}</strong><span>{approval.node_id ?? 'Graph-wide approval'}</span></div>
                  <div>
                    <button type="button" disabled={workflow.mutating} onClick={() => void workflow.decideApproval(approval.approval_id, true)}>Approve</button>
                    <button type="button" className="danger" disabled={workflow.mutating} onClick={() => void workflow.decideApproval(approval.approval_id, false)}>Deny</button>
                  </div>
                </article>
              ))}
            </section>
          ) : null}

          <div className="workflow-graph" role="list" aria-label="Workflow nodes">
            {graph.nodes.map((node) => {
              const state = stateFor(node.node_identifier, run.node_states)
              const dependencies = incoming.get(node.node_identifier) ?? []
              return (
                <article className="workflow-node" data-status={state?.status ?? 'pending'} key={node.node_identifier} role="listitem">
                  <header>
                    <span>{node.node_kind.replaceAll('_', ' ')}</span>
                    <strong>{state?.status.replaceAll('_', ' ') ?? 'pending'}</strong>
                  </header>
                  <h3>{node.node_identifier}</h3>
                  <dl>
                    <div><dt>Capability</dt><dd>{node.capability_identity ?? 'Intrinsic graph operation'}</dd></div>
                    <div><dt>Target</dt><dd>{node.execution_target ?? 'Generic'}</dd></div>
                    <div><dt>Project</dt><dd>{node.molecular_project_identifier ? <a href="#structure">{node.molecular_project_identifier}</a> : 'Not bound'}</dd></div>
                    <div><dt>Systems</dt><dd>{node.molecular_system_identifiers.length > 0 ? node.molecular_system_identifiers.map((identifier, index) => <span key={identifier}>{index > 0 ? ', ' : ''}<a href="#structure">{identifier}</a></span>) : 'Not bound'}</dd></div>
                    <div><dt>Regions</dt><dd>{node.molecular_region_identifiers.length > 0 ? node.molecular_region_identifiers.map((identifier, index) => <span key={identifier}>{index > 0 ? ', ' : ''}<a href="#structure">{identifier}</a></span>) : 'Whole system / not specified'}</dd></div>
                    <div><dt>Attempt</dt><dd>{state ? `${state.attempt_number}/${state.max_attempts}` : '0/1'}</dd></div>
                    <div><dt>Depends on</dt><dd>{dependencies.length > 0 ? dependencies.join(', ') : 'Root node'}</dd></div>
                  </dl>
                  {state?.error_message ? <p className="workflow-node__error">{state.error_message}</p> : null}
                  {state?.retry_reason ? <p className="workflow-node__notice">Retry: {state.retry_reason}</p> : null}
                  {state?.produced_artifact_types.length ? <p className="workflow-node__notice">Artifacts: {state.produced_artifact_types.join(', ')}</p> : null}
                </article>
              )
            })}
          </div>

          <div className="workflow-evidence-grid">
            <section>
              <h3>Branch and recovery evidence</h3>
              {run.decision_states.length === 0 ? <p>No branch decisions recorded yet.</p> : (
                <ol>{run.decision_states.slice(-8).map((decision) => (
                  <li key={decision.decision_identifier}><strong>{decision.decision_kind.replaceAll('_', ' ')}</strong><span>{decision.outcome.replaceAll('_', ' ')}</span></li>
                ))}</ol>
              )}
              {(workflow.evidence?.retries.length ?? 0) > 0 ? (
                <>
                  <h4>Retry history</h4>
                  <ol>{workflow.evidence?.retries.slice(-6).map((retry) => (
                    <li key={retry.retry_id}><strong>{retry.node_id}</strong><span>attempt {retry.attempt_number}: {retry.outcome ?? 'scheduled'}</span></li>
                  ))}</ol>
                </>
              ) : null}
            </section>
            <section>
              <h3>Cost and resources</h3>
              <dl>
                {Object.entries(run.budget_state.ceiling_values).map(([key, ceiling]) => (
                  <div key={key}><dt>{key}</dt><dd>{run.budget_state.actual_consumption[key] ?? 0} / {ceiling}</dd></div>
                ))}
              </dl>
              {Object.keys(run.budget_state.unknown_consumption).length > 0 ? <p className="workflow-node__notice">Some consumption remains unknown and is preserved as evidence.</p> : null}
            </section>
            <section>
              <h3>Provenance</h3>
              <p>Graph <code>{graph.definition_fingerprint.slice(0, 16)}…</code></p>
              <p>Snapshot <code>{workflow.evidence?.snapshot_fingerprint.slice(0, 16) ?? run.snapshot_fingerprint.slice(0, 16)}…</code></p>
              <p>{workflow.evidence?.artifacts.length ?? 0} content-addressed artifact reference(s)</p>
              <p>{graph.metadata?.rejected_alternative_identifiers.length ?? 0} rejected planning alternative(s)</p>
              <p>{(graph.metadata?.unresolved_requirement_identifiers.length ?? 0) + (graph.metadata?.unresolved_goal_identifiers.length ?? 0)} unresolved planning item(s)</p>
            </section>
          </div>
        </>
      )}
    </section>
  )
}
