export function ExistingRunInput({
  value,
  loading,
  openedRunIdentifier,
  onChange,
  onOpen,
}: {
  value: string
  loading: boolean
  openedRunIdentifier: string | null
  onChange: (value: string) => void
  onOpen: () => void
}) {
  return (
    <form
      className="existing-run-form"
      aria-label="Open an existing run"
      onSubmit={(event) => {
        event.preventDefault()
        onOpen()
      }}
    >
      <label className="sr-only" htmlFor="existing-run-identifier">Run identifier</label>
      <input
        id="existing-run-identifier"
        type="text"
        autoComplete="off"
        spellCheck={false}
        placeholder="run-11111111111111111111111111111111"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
      <button type="submit" disabled={loading}>
        {loading ? 'Opening...' : 'Open run'}
      </button>
      <span className="sr-only" role="status" aria-live="polite">
        {loading ? 'Opening persisted run.' : openedRunIdentifier ? `Opened run ${openedRunIdentifier}.` : ''}
      </span>
    </form>
  )
}
