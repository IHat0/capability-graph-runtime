import { useState } from 'react'
import type { PresetSummaryResponse } from '../api/types'
import { PresetSelector } from './PresetSelector'

export function PresetMenu({ presets, disabled, onSelect }: {
  presets: PresetSummaryResponse[]
  disabled: boolean
  onSelect: (identifier: string) => void
}) {
  const [open, setOpen] = useState(false)
  return (
    <div className="preset-menu" id="preset-menu">
      <button className="secondary-button" type="button" aria-expanded={open} aria-controls="preset-options" onClick={() => setOpen((current) => !current)} disabled={presets.length === 0}>
        Use a preset
        <span aria-hidden="true">{open ? '−' : '+'}</span>
      </button>
      {open && (
        <div className="preset-popover" id="preset-options">
          <PresetSelector
            presets={presets}
            value={null}
            disabled={disabled}
            label="Verified preset"
            onChange={(identifier) => {
              onSelect(identifier)
              setOpen(false)
            }}
          />
        </div>
      )}
    </div>
  )
}
