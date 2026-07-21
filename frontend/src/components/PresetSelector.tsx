import type { PresetSummaryResponse } from '../api/types'
import { humanize } from '../utils/format'

export function PresetSelector({ presets, value, disabled, label = 'Experiment preset', onChange }: {
  presets: PresetSummaryResponse[]
  value: string | null
  disabled: boolean
  label?: string
  onChange: (identifier: string) => void
}) {
  return (
    <label className="field-label" htmlFor="experiment-preset">
      <span>{label}</span>
      <div className="select-wrap">
        <select
          id="experiment-preset"
          value={value ?? ''}
          disabled={disabled || presets.length === 0}
          onChange={(event) => event.target.value && onChange(event.target.value)}
        >
          <option value="" disabled>{presets.length === 0 ? 'No presets available' : 'Choose a verified preset…'}</option>
          {presets.map((preset) => <option key={preset.preset_identifier} value={preset.preset_identifier}>{humanize(preset.experiment_identifier)}</option>)}
        </select>
        <span aria-hidden="true">⌄</span>
      </div>
    </label>
  )
}
