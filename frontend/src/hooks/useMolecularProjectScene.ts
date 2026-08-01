import { useCallback, useEffect, useRef, useState } from 'react'
import { pulsateApi } from '../api/client'
import type { FetchedMolecularResource, ProjectedMolecularSceneMetadata, ProjectedMolecularStructureMetadata } from '../api/types'
import {
  parseMolecularTopology,
  resolveProjectRegions,
  validateLoadedStructure,
  type LoadedMolecularProjectScene,
  type LoadedMolecularStructure,
} from '../scene/native-project'

export interface MolecularProjectSceneApi {
  getProjectedMolecularScene(projectIdentifier: string, sceneIdentifier: string, signal?: AbortSignal): Promise<ProjectedMolecularSceneMetadata>
  getNativeMolecularStructure(structure: ProjectedMolecularStructureMetadata, signal?: AbortSignal): Promise<FetchedMolecularResource>
  getMolecularTopology(structure: ProjectedMolecularStructureMetadata, signal?: AbortSignal): Promise<FetchedMolecularResource>
}

function controlledMessage(error: unknown): string {
  if (error instanceof DOMException && error.name === 'AbortError') return ''
  return 'The molecular project scene is unavailable or failed integrity validation.'
}

export function useMolecularProjectScene(api: MolecularProjectSceneApi = pulsateApi) {
  const [projectIdentifierInput, setProjectInput] = useState('')
  const [sceneIdentifierInput, setSceneInput] = useState('')
  const [scene, setScene] = useState<LoadedMolecularProjectScene | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const controllerRef = useRef<AbortController | null>(null)
  const generationRef = useRef(0)

  const invalidate = useCallback(() => {
    generationRef.current += 1
    controllerRef.current?.abort()
    controllerRef.current = null
    setLoading(false)
  }, [])

  const setProjectIdentifierInput = useCallback((value: string) => {
    invalidate()
    setProjectInput(value)
  }, [invalidate])

  const setSceneIdentifierInput = useCallback((value: string) => {
    invalidate()
    setSceneInput(value)
  }, [invalidate])

  const clear = useCallback(() => {
    invalidate()
    setScene(null)
    setError(null)
  }, [invalidate])

  const open = useCallback(async () => {
    const projectIdentifier = projectIdentifierInput.trim()
    const sceneIdentifier = sceneIdentifierInput.trim()
    if (!projectIdentifier || !sceneIdentifier) {
      setError('Enter both a project identifier and scene identifier.')
      return false
    }
    invalidate()
    const generation = generationRef.current
    const controller = new AbortController()
    controllerRef.current = controller
    setProjectInput(projectIdentifier)
    setSceneInput(sceneIdentifier)
    setLoading(true)
    setError(null)
    try {
      const metadata = await api.getProjectedMolecularScene(
        projectIdentifier,
        sceneIdentifier,
        controller.signal,
      )
      if (controller.signal.aborted || generation !== generationRef.current) return false
      if (metadata.project_identifier !== projectIdentifier || metadata.scene_identifier !== sceneIdentifier) {
        throw new Error('Molecular project scene identity mismatch.')
      }
      const ordered = [
        metadata.structures.find((entry) => entry.structure_identifier === metadata.primary_structure_identifier)!,
        ...metadata.structures.filter((entry) => entry.structure_identifier !== metadata.primary_structure_identifier),
      ]
      const structures = await Promise.all(ordered.map(async (structure): Promise<LoadedMolecularStructure> => {
        const [native, topologyResource] = await Promise.all([
          api.getNativeMolecularStructure(structure, controller.signal),
          api.getMolecularTopology(structure, controller.signal),
        ])
        const text = new TextDecoder('utf-8', { fatal: true }).decode(topologyResource.bytes)
        const topology = parseMolecularTopology(JSON.parse(text) as unknown)
        const mapping = validateLoadedStructure(structure, topology)
        return { metadata: structure, native, topologyResource, topology, ...mapping }
      }))
      if (controller.signal.aborted || generation !== generationRef.current) return false
      const resolved = resolveProjectRegions(metadata, structures)
      setScene({
        kind: 'native-project',
        metadata,
        structures,
        resolvedSelections: resolved.selections,
        resolvedRegions: resolved.regions,
      })
      return true
    } catch (cause) {
      if (controller.signal.aborted || generation !== generationRef.current) return false
      const message = controlledMessage(cause)
      if (message) setError(message)
      return false
    } finally {
      if (generation === generationRef.current) {
        controllerRef.current = null
        setLoading(false)
      }
    }
  }, [api, invalidate, projectIdentifierInput, sceneIdentifierInput])

  useEffect(() => () => {
    generationRef.current += 1
    controllerRef.current?.abort()
  }, [])

  return {
    projectIdentifierInput,
    sceneIdentifierInput,
    setProjectIdentifierInput,
    setSceneIdentifierInput,
    scene,
    loading,
    error,
    open,
    clear,
  }
}
