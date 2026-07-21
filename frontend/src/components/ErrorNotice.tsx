import type { WorkspaceError } from '../hooks/useExperimentWorkspace'

export function ErrorNotice({ errors }: { errors: WorkspaceError[] }) {
  if (errors.length === 0) return null
  const uniqueErrors = errors.filter((error, index) => errors.findIndex((candidate) => candidate.scope === error.scope && candidate.message === error.message) === index)
  const retainedSceneError = uniqueErrors.find((error) => error.message.startsWith('Selected preset failed to load.'))
  const visibleErrors = retainedSceneError ? [retainedSceneError] : uniqueErrors
  return (
    <div className="error-stack" role="status" aria-live="polite">
      {visibleErrors.map((error, index) => (
        <div className="error-notice" key={`${error.scope}-${index}`}>
          <span className="error-notice__mark" aria-hidden="true">!</span>
          <div><strong>{error.scope === 'health' ? 'Backend status unavailable' : 'Partial data unavailable'}</strong><span>{error.message}</span></div>
        </div>
      ))}
    </div>
  )
}
