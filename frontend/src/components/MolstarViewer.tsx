import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react'
import { StructureElement, StructureProperties } from 'molstar/lib/mol-model/structure'
import { PluginContext } from 'molstar/lib/mol-plugin/context'
import { DefaultPluginSpec } from 'molstar/lib/mol-plugin/spec'
import { Color } from 'molstar/lib/mol-util/color'
import { Vec3 } from 'molstar/lib/mol-math/linear-algebra'
import type { MolecularScene } from '../scene/types'
import { sceneToMolstarStructure, type MolstarStructureData } from '../scene/molstar-adapter'
import { structureBounds } from '../scene/geometry'
import { pointToAngstrom } from '../scene/units'
import { LatestLoadQueue } from '../scene/latest-load-queue'

export const ATOM_LABEL_THRESHOLD = 64

export interface MolstarViewerHandle {
  fitStructure(): void
  resetCamera(): void
}

export interface MolstarViewerProps {
  scene: MolecularScene
  onAtomSelected: (atomId: string | null) => void
  onRenderingStateChange: (state: { loading: boolean; error: string | null }) => void
}

function measurementLabel(scene: MolecularScene, measurement: MolecularScene['measurements'][number]): string {
  const declared = measurement.declaredValue === undefined ? null : `declared ${measurement.declaredValue.toFixed(4)}`
  const backend = measurement.backendDerivedValue === undefined ? null : `backend ${measurement.backendDerivedValue.toFixed(4)}`
  const geometric = `viewer ${measurement.geometricValue.toFixed(4)}`
  return [declared, backend, geometric].filter(Boolean).join(' · ') + ` ${scene.coordinateUnit}`
}

function frameScene(plugin: PluginContext, scene: MolecularScene, durationMs: number) {
  const renderedAtoms = scene.atoms.map((atom) => ({ ...atom, position: pointToAngstrom(atom.position, scene.coordinateUnit) }))
  const bounds = structureBounds(renderedAtoms, 'angstrom')
  const camera = plugin.canvas3d?.camera
  if (!bounds || !camera) return
  const longestAxis = bounds.size.indexOf(Math.max(...bounds.size))
  const directions = [
    { direction: [0.15, 0.35, 1], up: [1, 0, 0] },
    { direction: [1, 0.15, 0.35], up: [0, 1, 0] },
    { direction: [1, 0.35, 0.15], up: [0, 0, 1] },
  ] as const
  const view = directions[longestAxis]
  const snapshot = camera.getFocus(
    Vec3.create(bounds.center[0], bounds.center[1], bounds.center[2]),
    bounds.radius * 1.3,
    Vec3.create(view.up[0], view.up[1], view.up[2]),
    Vec3.create(view.direction[0], view.direction[1], view.direction[2]),
  )
  plugin.managers.camera.setSnapshot(snapshot, durationMs)
}

async function loadMolstarScene(plugin: PluginContext, scene: MolecularScene, adapted: MolstarStructureData, isCurrent: () => boolean): Promise<boolean> {
  if (!isCurrent()) return false
  await plugin.clear()
  if (!isCurrent()) return false
  const data = await plugin.builders.data.rawData({ data: adapted.data, label: adapted.label })
  const trajectory = await plugin.builders.structure.parseTrajectory(data, adapted.format)
  if (!isCurrent()) return false
  await plugin.builders.structure.hierarchy.applyPreset(trajectory, 'default')
  if (!isCurrent()) return false

  const structureRef = plugin.managers.structure.hierarchy.current.structures[0]
  const structure = structureRef?.cell.obj?.data
  if (!structure || !structureRef) throw new Error('Mol* did not create a structure from the supplied coordinates.')

  for (const region of scene.regions) {
    if (!isCurrent()) return false
    const indices = region.atomIds.flatMap((atomId) => {
      const sourceIndex = adapted.sourceIndexByAtomId.get(atomId)
      return sourceIndex === undefined ? [] : [sourceIndex]
    })
    if (indices.length === 0) continue
    const expression = StructureElement.Schema.toExpression({ items: indices.map((atom_index) => ({ atom_index })) })
    const component = await plugin.builders.structure.tryCreateComponentFromExpression(
      structureRef.cell,
      expression,
      `pulsate-region-${region.id}`,
      { label: region.label, tags: ['pulsate-region', `pulsate-region-${region.kind}`] },
    )
    if (component) {
      await plugin.builders.structure.representation.addRepresentation(component, {
        type: 'gaussian-surface',
        typeParams: {
          alpha: 0.2,
          radiusOffset: 0.22,
          resolution: 0.5,
          visuals: ['gaussian-surface-wireframe'],
        },
        color: 'uniform',
        colorParams: { value: Color(0x267a52) },
      })
    }
  }

  for (const measurement of scene.measurements) {
    if (!isCurrent()) return false
    const left = adapted.sourceIndexByAtomId.get(measurement.atomIds[0])
    const right = adapted.sourceIndexByAtomId.get(measurement.atomIds[1])
    if (left === undefined || right === undefined) continue
    const leftLoci = StructureElement.Loci.fromSchema(structure, { atom_index: left })
    const rightLoci = StructureElement.Loci.fromSchema(structure, { atom_index: right })
    await plugin.managers.structure.measurement.addDistance(leftLoci, rightLoci, {
      customText: measurementLabel(scene, measurement),
      selectionTags: ['pulsate-measurement'],
      reprTags: ['pulsate-measurement'],
      visualParams: { textSize: 0.18, textColor: Color(0x4f4f4c) },
    })
  }
  if (scene.atoms.length <= ATOM_LABEL_THRESHOLD) {
    for (const atom of scene.atoms) {
      if (!isCurrent()) return false
      const sourceIndex = adapted.sourceIndexByAtomId.get(atom.id)
      if (sourceIndex === undefined) continue
      const atomLoci = StructureElement.Loci.fromSchema(structure, { atom_index: sourceIndex })
      await plugin.managers.structure.measurement.addLabel(atomLoci, {
        selectionTags: ['pulsate-atom-label'],
        reprTags: ['pulsate-atom-label'],
        visualParams: {
          customText: `${atom.element} · ${atom.id}`,
          tooltip: `Atom ${atom.id} (${atom.element})`,
          textSize: 0.32,
          textColor: Color(0x171717),
          borderColor: Color(0xffffff),
          borderWidth: 0.12,
          scaleByRadius: false,
          offsetX: 1.15,
          offsetZ: 1,
          background: true,
          backgroundColor: Color(0xffffff),
          backgroundOpacity: 0.72,
          backgroundMargin: 0.12,
        },
      })
    }
  }
  if (!isCurrent()) return false
  frameScene(plugin, scene, 0)
  return true
}

export const MolstarViewer = forwardRef<MolstarViewerHandle, MolstarViewerProps>(function MolstarViewer(
  { scene, onAtomSelected, onRenderingStateChange },
  ref,
) {
  const hostRef = useRef<HTMLDivElement>(null)
  const pluginRef = useRef<PluginContext | null>(null)
  const adapterRef = useRef<MolstarStructureData | null>(null)
  const sceneRef = useRef(scene)
  sceneRef.current = scene
  const loadQueue = useRef(new LatestLoadQueue())
  const [initialized, setInitialized] = useState(false)

  useImperativeHandle(ref, () => ({
    fitStructure: () => {
      if (pluginRef.current) frameScene(pluginRef.current, sceneRef.current, 250)
    },
    resetCamera: () => {
      pluginRef.current?.managers.camera.reset(undefined, 250)
    },
  }), [])

  useEffect(() => {
    const host = hostRef.current
    if (!host) return
    let disposed = false
    const plugin = new PluginContext({
      ...DefaultPluginSpec(),
      canvas3d: { renderer: { backgroundColor: Color(0xffffff) } },
    })
    const sceneLoadQueue = loadQueue.current
    pluginRef.current = plugin
    let clickSubscription: { unsubscribe(): void } | undefined
    let resizeObserver: ResizeObserver | undefined

    async function initialize() {
      try {
        await plugin.init()
        if (disposed || !plugin.mount(host!)) return
        plugin.managers.interactivity.setProps({ granularity: 'element' })
        clickSubscription = plugin.behaviors.interaction.click.subscribe(({ current }) => {
          const loci = current.loci
          if (!StructureElement.Loci.is(loci)) {
            onAtomSelected(null)
            return
          }
          const location = StructureElement.Loci.getFirstLocation(loci)
          if (!location) return
          const sourceIndex = StructureProperties.atom.sourceIndex(location)
          onAtomSelected(adapterRef.current?.atomIdsBySourceIndex[sourceIndex] ?? null)
        })
        resizeObserver = new ResizeObserver(() => plugin.handleResize())
        resizeObserver.observe(host!)
        setInitialized(true)
      } catch (error) {
        onRenderingStateChange({ loading: false, error: error instanceof Error ? error.message : 'Unable to initialize Mol*.' })
      }
    }
    void initialize()

    return () => {
      disposed = true
      sceneLoadQueue.invalidate()
      clickSubscription?.unsubscribe()
      resizeObserver?.disconnect()
      plugin.dispose()
      pluginRef.current = null
    }
  }, [onAtomSelected, onRenderingStateChange])

  useEffect(() => {
    const plugin = pluginRef.current
    if (!plugin || !initialized) return
    onRenderingStateChange({ loading: true, error: null })
    loadQueue.current.enqueue(
      async (isLatest) => {
        const adapted = sceneToMolstarStructure(scene)
        const loaded = await loadMolstarScene(plugin, scene, adapted, isLatest)
        if (loaded && isLatest()) {
          adapterRef.current = adapted
        }
      },
      () => onRenderingStateChange({ loading: false, error: null }),
      (error) => onRenderingStateChange({ loading: false, error: error instanceof Error ? error.message : 'Mol* could not render the structure.' }),
    )
  }, [initialized, onRenderingStateChange, scene])

  return <div className="molstar-host" ref={hostRef} aria-label="Interactive molecular structure viewer" />
})
