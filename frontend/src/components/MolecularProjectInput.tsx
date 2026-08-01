export function MolecularProjectInput({
  projectIdentifier,
  sceneIdentifier,
  loading,
  active,
  onProjectChange,
  onSceneChange,
  onOpen,
  onClear,
}: {
  projectIdentifier: string
  sceneIdentifier: string
  loading: boolean
  active: boolean
  onProjectChange: (value: string) => void
  onSceneChange: (value: string) => void
  onOpen: () => void
  onClear: () => void
}) {
  return <form className="molecular-project-form" onSubmit={(event) => { event.preventDefault(); onOpen() }} aria-label="Open molecular project">
    <input aria-label="Project identifier" value={projectIdentifier} onChange={(event) => onProjectChange(event.target.value)} placeholder="Project identifier" disabled={loading} />
    <input aria-label="Scene identifier" value={sceneIdentifier} onChange={(event) => onSceneChange(event.target.value)} placeholder="Scene identifier" disabled={loading} />
    <button type="submit" disabled={loading}>{loading ? 'Opening...' : 'Open project'}</button>
    {active && <button type="button" onClick={onClear}>Clear project</button>}
  </form>
}
