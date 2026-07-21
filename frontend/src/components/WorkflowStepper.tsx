import type { RunResultsResponse, RunStateResponse, RunVerificationResponse } from '../api/types'

export function WorkflowStepper({ run, results, verification }: {
  run: RunStateResponse | null
  results: RunResultsResponse | null
  verification: RunVerificationResponse | null
}) {
  const validated = Boolean(run && !['queued', 'validating'].includes(run.status))
  const workflowComplete = results !== null
  const phases = [
    ['Experiment validated', validated ? 'Manifest identity validated' : run?.status === 'validating' ? 'Validating manifest identity' : 'Pending run'],
    ['Problem prepared', workflowComplete ? 'Proven by authoritative artifacts' : 'Pending runner evidence'],
    ['Hamiltonian constructed', workflowComplete ? 'Identity available' : 'Pending runner evidence'],
    ['Classical reference completed', workflowComplete ? 'Exact result available' : 'Pending runner evidence'],
    ['Quantum workflow completed', workflowComplete ? 'VQE result available' : run?.status === 'running_quantum_workflow' ? 'Trusted runner executing' : 'Pending execution'],
    ['Verification completed', verification?.verification_completed ? (verification.verification_passed ? 'Passed' : 'Completed with blocking findings') : 'Pending evidence'],
    ['Authorized', run?.status === 'authorized' ? 'Authorized by trusted receipt' : run?.status === 'rejected' ? 'Rejected by scientific verification' : run?.status === 'failed' ? 'Runtime failed' : run?.status === 'interrupted' ? 'Run interrupted' : 'Pending verification'],
  ] as const
  const completed = [validated, workflowComplete, workflowComplete, workflowComplete, workflowComplete, Boolean(verification), run?.status === 'authorized']

  return (
    <section className="inspector-section" id="workflow" aria-labelledby="workflow-title">
      <p className="section-kicker">Workflow</p>
      <h2 id="workflow-title">Scientific phases</h2>
      <ol className="workflow-list">
        {phases.map(([phase, status], index) => {
          const current = !completed[index] && (index === 0 || completed[index - 1])
          const terminalProblem = index === phases.length - 1 && ['rejected', 'failed', 'interrupted'].includes(run?.status ?? '')
          return (
            <li key={phase} className={terminalProblem ? 'workflow-list__error' : completed[index] ? 'workflow-list__complete' : current ? 'workflow-list__current' : undefined}>
              <span aria-hidden="true">{completed[index] ? '✓' : index + 1}</span>
              <div><strong>{phase}</strong><small>{status}</small></div>
            </li>
          )
        })}
      </ol>
    </section>
  )
}
