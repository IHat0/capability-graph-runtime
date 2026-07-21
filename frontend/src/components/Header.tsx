function NeutralStatus({ children }: { children: React.ReactNode }) {
  return <span className="header-status-item"><span aria-hidden="true" className="neutral-dot" />{children}</span>
}

export function Header() {
  return (
    <header className="app-header">
      <a className="wordmark" href="#workspace-home" aria-label="Pulsate Labs">
        <svg className="wordmark__wave" viewBox="0 0 36 20" aria-hidden="true">
          <path d="M1 10h7l2-2.5 3 5 4-10 4 15 3-7.5h11" />
        </svg>
        <span>Pulsate Labs</span>
      </a>
      <strong className="workspace-name">Scientific Workspace</strong>
      <div className="header-status" aria-label="Workspace status">
        <NeutralStatus>Local simulator</NeutralStatus>
        <span className="header-status-item">Not executed</span>
      </div>
    </header>
  )
}
