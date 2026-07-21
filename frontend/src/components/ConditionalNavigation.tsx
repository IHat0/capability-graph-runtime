type NavigationItem = 'home' | 'structure' | 'workflow' | 'results' | 'evidence' | 'settings' | 'help'

const labels: Record<NavigationItem, string> = {
  home: 'Home',
  structure: 'Structure',
  workflow: 'Workflow',
  results: 'Results',
  evidence: 'Evidence',
  settings: 'Settings',
  help: 'Help',
}

const targets: Record<NavigationItem, string> = {
  home: 'workspace-home',
  structure: 'structure',
  workflow: 'workflow',
  results: 'results',
  evidence: 'evidence',
  settings: 'preset-menu',
  help: 'workspace-help',
}

function NavigationIcon({ item }: { item: NavigationItem }) {
  const common = { fill: 'none', stroke: 'currentColor', strokeWidth: 1.7, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const }
  if (item === 'home') return <svg viewBox="0 0 24 24" aria-hidden="true"><path {...common} d="m4 10 8-6 8 6v9H4zM9 19v-6h6v6" /></svg>
  if (item === 'structure') return <svg viewBox="0 0 24 24" aria-hidden="true"><circle {...common} cx="7" cy="7" r="2.5" /><circle {...common} cx="17" cy="7" r="2.5" /><circle {...common} cx="12" cy="17" r="2.5" /><path {...common} d="m9 8.5 2 6m4-6-2 6M9.5 7h5" /></svg>
  if (item === 'workflow') return <svg viewBox="0 0 24 24" aria-hidden="true"><circle {...common} cx="6" cy="6" r="2" /><circle {...common} cx="18" cy="12" r="2" /><circle {...common} cx="6" cy="18" r="2" /><path {...common} d="M8 6h3a3 3 0 0 1 3 3v0a3 3 0 0 0 3 3M8 18h3a3 3 0 0 0 3-3v0" /></svg>
  if (item === 'results') return <svg viewBox="0 0 24 24" aria-hidden="true"><path {...common} d="M5 19V9m7 10V5m7 14v-7" /></svg>
  if (item === 'evidence') return <svg viewBox="0 0 24 24" aria-hidden="true"><path {...common} d="M6 3h9l4 4v14H6zM15 3v5h4M9 12h7M9 16h5" /></svg>
  if (item === 'settings') return <svg viewBox="0 0 24 24" aria-hidden="true"><path {...common} d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.09a2 2 0 0 1 1 1.74v.5a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.38a2 2 0 0 0-.73-2.73l-.15-.09a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2Z" /><circle {...common} cx="12" cy="12" r="3" /></svg>
  return <svg viewBox="0 0 24 24" aria-hidden="true"><circle {...common} cx="12" cy="12" r="9" /><path {...common} d="M9.7 9a2.5 2.5 0 1 1 3.2 2.4c-.9.3-1.4.9-1.4 1.8M12 17h.01" /></svg>
}

export function ConditionalNavigation({ hasScene }: { hasScene: boolean }) {
  const items: NavigationItem[] = hasScene
    ? ['home', 'structure', 'workflow', 'results', 'evidence', 'settings', 'help']
    : ['home', 'settings', 'help']

  return (
    <nav className="navigation-rail" aria-label="Workspace navigation">
      {items.map((item) => (
        <a key={item} href={`#${targets[item]}`} aria-label={labels[item]} title={labels[item]} aria-current={item === 'home' ? 'page' : undefined}>
          <NavigationIcon item={item} />
          <span>{labels[item]}</span>
        </a>
      ))}
    </nav>
  )
}
