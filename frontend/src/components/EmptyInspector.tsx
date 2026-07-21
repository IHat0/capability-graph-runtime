export function EmptyInspector() {
  return (
    <aside className="empty-inspector" id="workspace-help" aria-labelledby="empty-inspector-title">
      <p className="section-kicker">Workspace guide</p>
      <h2 id="empty-inspector-title">Start a new experiment</h2>
      <p>Describe your scientific objective to begin, or load a preset.</p>
      <ol className="intro-steps">
        <li><span>1</span><div><strong>Describe the objective</strong><small>State the molecule and scientific question.</small></div></li>
        <li><span>2</span><div><strong>Review the generated experiment</strong><small>Inspect structure, methods, and provenance.</small></div></li>
        <li><span>3</span><div><strong>Run after verification</strong><small>Execution remains unavailable until connected.</small></div></li>
      </ol>
      <div className="empty-configuration">
        <strong>No experiment configured yet.</strong>
        <span>Provide an objective or choose a preset to begin.</span>
      </div>
    </aside>
  )
}
