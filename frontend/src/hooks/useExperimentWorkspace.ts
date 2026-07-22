import { useCallback, useEffect, useRef, useState } from 'react'
import { pulsateApi, type WorkspaceApi } from '../api/client'
import type { ExperimentPlanResponse, HealthResponse, PresetDetailResponse, PresetSummaryResponse } from '../api/types'
import { normalizeScene } from '../scene/normalize'
import type { MolecularScene } from '../scene/types'

export interface WorkspaceError {
  scope: 'health' | 'presets' | 'details' | 'scene' | 'run'
  message: string
}

function messageFor(error: unknown): string {
  return error instanceof Error ? error.message : 'An unexpected API error occurred.'
}

export function useExperimentWorkspace(api: WorkspaceApi = pulsateApi) {
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [presets, setPresets] = useState<PresetSummaryResponse[]>([])
  const [selectedPresetId, setSelectedPresetId] = useState<string | null>(null)
  const [displayedPresetId, setDisplayedPresetId] = useState<string | null>(null)
  const [detail, setDetail] = useState<PresetDetailResponse | null>(null)
  const [scene, setScene] = useState<MolecularScene | null>(null)
  const [initialLoading, setInitialLoading] = useState(true)
  const [presetLoading, setPresetLoading] = useState(false)
  const [errors, setErrors] = useState<WorkspaceError[]>([])
  const [staleSceneMessage, setStaleSceneMessage] = useState<string | null>(null)
  const [plan, setPlan] = useState<ExperimentPlanResponse | null>(null)
  const [planning, setPlanning] = useState(false)
  const [planQuestion, setPlanQuestion] = useState('')
  const requestSequence = useRef(0)
  const planRequestSequence = useRef(0)
  const planControllerRef = useRef<AbortController | null>(null)
  const sceneRef = useRef<MolecularScene | null>(null)
  const displayedPresetIdRef = useRef<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    async function initialize() {
      const [healthResult, presetsResult] = await Promise.allSettled([
        api.getHealth(controller.signal),
        api.getPresets(controller.signal),
      ])
      if (controller.signal.aborted) return
      const nextErrors: WorkspaceError[] = []
      if (healthResult.status === 'fulfilled') setHealth(healthResult.value)
      else nextErrors.push({ scope: 'health', message: messageFor(healthResult.reason) })
      if (presetsResult.status === 'fulfilled') {
        setPresets(presetsResult.value.presets)
      } else {
        nextErrors.push({ scope: 'presets', message: messageFor(presetsResult.reason) })
      }
      setErrors(nextErrors)
      setInitialLoading(false)
    }
    void initialize()
    return () => controller.abort()
  }, [api])

  useEffect(() => {
    if (!selectedPresetId) return
    const controller = new AbortController()
    const sequence = ++requestSequence.current
    setPresetLoading(true)
    setStaleSceneMessage(null)
    setErrors((current) => current.filter((error) => error.scope !== 'details' && error.scope !== 'scene'))

    async function load() {
      const [detailResult, sceneResult] = await Promise.allSettled([
        api.getPreset(selectedPresetId!, controller.signal),
        api.getScene(selectedPresetId!, controller.signal),
      ])
      if (controller.signal.aborted || sequence !== requestSequence.current) return
      const nextErrors: WorkspaceError[] = []
      let nextDetail: PresetDetailResponse | undefined
      if (detailResult.status === 'fulfilled') {
        nextDetail = detailResult.value
      } else {
        nextErrors.push({ scope: 'details', message: messageFor(detailResult.reason) })
      }
      if (sceneResult.status === 'fulfilled') {
        try {
          const normalized = normalizeScene(sceneResult.value, nextDetail)
          setScene(normalized)
          sceneRef.current = normalized
          setDetail(nextDetail ?? null)
          setDisplayedPresetId(selectedPresetId!)
          displayedPresetIdRef.current = selectedPresetId!
          setStaleSceneMessage(null)
        } catch (error) {
          nextErrors.push({ scope: 'scene', message: `Unable to normalize molecular scene: ${messageFor(error)}` })
        }
      } else {
        nextErrors.push({ scope: 'scene', message: messageFor(sceneResult.reason) })
      }
      if (nextErrors.some((error) => error.scope === 'scene') && sceneRef.current && displayedPresetIdRef.current) {
        const staleMessage = `Selected preset failed to load. Showing the last valid structure from ${displayedPresetIdRef.current}.`
        setStaleSceneMessage(staleMessage)
        nextErrors.push({ scope: 'scene', message: staleMessage })
      }
      setErrors((current) => [...current.filter((error) => error.scope === 'health' || error.scope === 'presets'), ...nextErrors])
      setPresetLoading(false)
    }
    void load()
    return () => controller.abort()
  }, [api, selectedPresetId])

  const selectPreset = useCallback((identifier: string) => {
    planRequestSequence.current += 1
    planControllerRef.current?.abort()
    planControllerRef.current = null
    setPlanning(false)
    setPlan(null)
    setSelectedPresetId(identifier)
  }, [])

  useEffect(() => () => {
    planRequestSequence.current += 1
    planControllerRef.current?.abort()
    planControllerRef.current = null
  }, [])

  const planExperiment = useCallback(async () => {
    const question = planQuestion.trim()
    if (!question || planning) return
    planControllerRef.current?.abort()
    const controller = new AbortController()
    planControllerRef.current = controller
    const sequence = ++planRequestSequence.current
    setPlanning(true)
    setErrors((current) => current.filter((error) => error.scope !== 'details' && error.scope !== 'scene'))
    try {
      if (!api.planExperiment) throw new Error('Dynamic experiment planning is unavailable.')
      const nextPlan = await api.planExperiment(question, controller.signal)
      if (controller.signal.aborted || sequence !== planRequestSequence.current) return
      setPlan(nextPlan)
      if (nextPlan.ready_for_execution && nextPlan.molecule) {
        const normalized = normalizeScene(nextPlan.molecule)
        requestSequence.current += 1
        setSelectedPresetId(null)
        setDisplayedPresetId(nextPlan.experiment_identifier)
        displayedPresetIdRef.current = nextPlan.experiment_identifier
        setDetail(null)
        setScene(normalized)
        sceneRef.current = normalized
        setStaleSceneMessage(null)
      }
    } catch (error) {
      if (controller.signal.aborted || sequence !== planRequestSequence.current) return
      setErrors((current) => [...current, { scope: 'details', message: messageFor(error) }])
    } finally {
      if (sequence === planRequestSequence.current) {
        planControllerRef.current = null
        setPlanning(false)
      }
    }
  }, [api, planQuestion, planning])

  return {
    health,
    presets,
    selectedPresetId,
    displayedPresetId,
    detail,
    scene,
    initialLoading,
    presetLoading,
    errors,
    staleSceneMessage,
    plan,
    planning,
    planQuestion,
    setPlanQuestion,
    planExperiment,
    selectPreset,
  }
}
