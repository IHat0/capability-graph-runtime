import { useCallback, useEffect, useRef, useState } from 'react'
import { pulsateApi } from '../api/client'
import type { CandidateResearchPlanResponse, MolecularPlanningRequest } from '../api/planning'

export interface MolecularPlanningApi {
  evaluateMolecularProjectPlan(
    projectIdentifier: string,
    request: MolecularPlanningRequest,
    signal?: AbortSignal,
  ): Promise<CandidateResearchPlanResponse>
}

export function useMolecularPlanning(
  projectIdentifier: string | null,
  api: MolecularPlanningApi = pulsateApi,
) {
  const [result, setResult] = useState<CandidateResearchPlanResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const controllerRef = useRef<AbortController | null>(null)
  const projectRef = useRef(projectIdentifier)

  useEffect(() => {
    if (projectRef.current === projectIdentifier) return
    projectRef.current = projectIdentifier
    controllerRef.current?.abort()
    controllerRef.current = null
    setResult(null)
    setError(null)
    setLoading(false)
  }, [projectIdentifier])

  const evaluate = useCallback(async (request: MolecularPlanningRequest) => {
    if (!projectIdentifier || loading) return false
    controllerRef.current?.abort()
    const controller = new AbortController()
    controllerRef.current = controller
    setLoading(true)
    setError(null)
    try {
      const next = await api.evaluateMolecularProjectPlan(projectIdentifier, request, controller.signal)
      if (controller.signal.aborted) return false
      if (next.project_identifier !== projectIdentifier
        || next.objective_identifier !== request.objective.objective_identifier) {
        throw new Error('Molecular planning response identity mismatch.')
      }
      setResult(next)
      return true
    } catch (cause) {
      if (controller.signal.aborted) return false
      setResult(null)
      setError(cause instanceof Error && cause.message
        ? cause.message
        : 'The molecular research plan is unavailable.')
      return false
    } finally {
      if (controllerRef.current === controller) {
        controllerRef.current = null
        setLoading(false)
      }
    }
  }, [api, loading, projectIdentifier])

  useEffect(() => () => controllerRef.current?.abort(), [])

  return {
    result: result?.project_identifier === projectIdentifier ? result : null,
    loading,
    error,
    evaluate,
  }
}

export type MolecularPlanningState = ReturnType<typeof useMolecularPlanning>
