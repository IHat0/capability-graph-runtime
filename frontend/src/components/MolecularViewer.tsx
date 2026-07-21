import { useEffect, useMemo, useRef, useState } from 'react'
import type { MolecularScene } from '../scene/types'
import { structureBounds } from '../scene/geometry'
import { ATOM_LABEL_THRESHOLD, MolstarViewer, type MolstarViewerHandle } from './MolstarViewer'

function CameraIcon({ kind }: { kind: 'reset' | 'fit' }) {
  return kind === 'reset'
    ? <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 12a8 8 0 1 0 2.35-5.65L4 8.7M4 4v4.7h4.7" /></svg>
    : <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 4H4v5M15 4h5v5M9 20H4v-5M15 20h5v-5" /></svg>
}

export function MolecularViewer({ scene, loading }: { scene: MolecularScene; loading: boolean }) {
  const viewerRef = useRef<MolstarViewerHandle>(null)
  const [selectedAtomId, setSelectedAtomId] = useState<string | null>(null)
  const [rendering, setRendering] = useState({ loading: false, error: null as string | null })
  const selectedAtom = scene.atoms.find((atom) => atom.id === selectedAtomId)
  const selectedMeasurements = scene.measurements.filter((measurement) => measurement.atomIds.includes(selectedAtomId ?? ''))
  const bounds = useMemo(() => structureBounds(scene.atoms, scene.coordinateUnit), [scene])

  useEffect(() => setSelectedAtomId(null), [scene.id])

  return (
    <section className="viewer-shell" id="structure" aria-labelledby="viewer-title">
      <div className="viewer-titlebar">
        <div>
          <span className="section-kicker">Structure</span>
          <h1 id="viewer-title">Molecular workspace</h1>
        </div>
        <div className="viewer-meta">
          <span>{scene.atoms.length} atoms · {scene.bonds.length} bonds</span>
          {scene.bondDataSource === 'inferred' && <span className="viewer-note">Visual bonds inferred</span>}
          {scene.bondDataSource === 'skipped' && <span className="viewer-note">Bond inference skipped</span>}
          {scene.atoms.length > ATOM_LABEL_THRESHOLD && <span className="viewer-note">Atom labels hidden</span>}
        </div>
      </div>

      <div className="viewer-stage">
        <MolstarViewer ref={viewerRef} scene={scene} onAtomSelected={setSelectedAtomId} onRenderingStateChange={setRendering} />
        {(loading || rendering.loading) && <div className="viewer-loading" role="status"><span />Updating structure…</div>}
        {rendering.error && <div className="viewer-render-error" role="alert"><strong>Rendering unavailable</strong><span>{rendering.error}</span></div>}
        <div className="viewer-toolbar" aria-label="Molecular camera controls">
          <button type="button" onClick={() => viewerRef.current?.resetCamera()}><CameraIcon kind="reset" />Reset camera</button>
          <button type="button" onClick={() => viewerRef.current?.fitStructure()}><CameraIcon kind="fit" />Fit structure</button>
        </div>
      </div>

      <div className="inspection-strip">
        <div className="inspection-block inspection-block--selected">
          <span className="section-kicker">Selected atom</span>
          {selectedAtom ? (
            <div className="atom-readout"><strong>{selectedAtom.element}</strong><span>{selectedAtom.id}</span><code>{selectedAtom.position.map((value) => value.toFixed(4)).join(', ')} {scene.coordinateUnit}</code></div>
          ) : <p>Select an atom in the structure to inspect its exact coordinates.</p>}
        </div>
        <div className="inspection-block">
          <span className="section-kicker">Measurements</span>
          {selectedMeasurements.length > 0 ? selectedMeasurements.map((measurement) => (
            <div className="measurement-readout" key={measurement.id}>
              <strong>{measurement.atomIds.join(' ↔ ')}</strong>
              <span>Declared: {measurement.declaredValue !== undefined ? `${measurement.declaredValue.toFixed(6)} ${measurement.unit}` : 'not supplied'}</span>
              <span>Backend derived: {measurement.backendDerivedValue !== undefined ? `${measurement.backendDerivedValue.toFixed(6)} ${measurement.unit}` : 'not supplied'}</span>
              <span>Viewer geometry: {measurement.geometricValue.toFixed(6)} {measurement.unit}</span>
              {measurement.backendDifference !== undefined && <span>Backend − viewer: {measurement.backendDifference.toExponential(3)} {measurement.unit}</span>}
              {measurement.declaredDifference !== undefined && <span>Declared − viewer: {measurement.declaredDifference.toExponential(3)} {measurement.unit}</span>}
            </div>
          )) : <p>{scene.measurements.length ? 'Select a measured atom to see declared and geometric distances.' : 'No measurements supplied for this scene.'}</p>}
        </div>
        <div className="inspection-block inspection-block--bounds">
          <span className="section-kicker">Spatial extent</span>
          {bounds ? <><strong>{bounds.size.map((value) => value.toFixed(3)).join(' × ')}</strong><span>{scene.coordinateUnit} · radius {bounds.radius.toFixed(3)}</span></> : <p>Unavailable</p>}
        </div>
      </div>
      {scene.normalizationWarnings.length > 0 && <div className="normalization-warning" role="status">{scene.normalizationWarnings.join(' ')}</div>}
    </section>
  )
}
