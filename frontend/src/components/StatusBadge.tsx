export type StatusTone = 'good' | 'warning' | 'muted' | 'error' | 'info'

export function StatusBadge({ tone, children }: { tone: StatusTone; children: React.ReactNode }) {
  return <span className={`status-badge status-badge--${tone}`}><span aria-hidden="true" className="status-badge__dot" />{children}</span>
}
