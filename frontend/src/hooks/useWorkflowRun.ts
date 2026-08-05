import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, pulsateApi, type PulsateApi } from '../api/client'
import type {
  WorkflowEvidenceResponse,
  WorkflowGraphDefinitionResponse,
  WorkflowRunSnapshotResponse,
} from '../api/workflows'

export interface WorkflowRunWorkspaceState {
  graphRunIdentifierInput: string
  setGraphRunIdentifierInput(value: string): void
  loading: boolean
  mutating: boolean
  error: string | null
  errorStatus: number | null
  graph: WorkflowGraphDefinitionResponse | null
  run: WorkflowRunSnapshotResponse | null
  evidence: WorkflowEvidenceResponse | null
  open(): Promise<boolean>
  refresh(): Promise<void>
  resume(): Promise<void>
  cancel(reason?: string): Promise<void>
  decideApproval(approvalIdentifier: string, granted: boolean): Promise<void>
  clear(): void
}

function message(error: unknown): { text: string; status: number | null } {
  if (error instanceof ApiError) {
    return { text: error.message, status: error.status ?? null }
  }
  return { text: 'The workflow run could not be loaded safely.', status: null }
}

export function useWorkflowRun(api: PulsateApi = pulsateApi): WorkflowRunWorkspaceState {
  const [graphRunIdentifierInput, setGraphRunIdentifierInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [mutating, setMutating] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [errorStatus, setErrorStatus] = useState<number | null>(null)
  const [graph, setGraph] = useState<WorkflowGraphDefinitionResponse | null>(null)
  const [run, setRun] = useState<WorkflowRunSnapshotResponse | null>(null)
  const [evidence, setEvidence] = useState<WorkflowEvidenceResponse | null>(null)
  const controller = useRef<AbortController | null>(null)

  useEffect(() => () => controller.current?.abort(), [])

  const load = useCallback(async (identifier: string): Promise<boolean> => {
    controller.current?.abort()
    const active = new AbortController()
    controller.current = active
    setLoading(true)
    setError(null)
    setErrorStatus(null)
    try {
      const nextRun = await api.getWorkflowRun(identifier, active.signal)
      const [nextGraph, nextEvidence] = await Promise.all([
        api.getWorkflowGraph(
          nextRun.graph_state.graph_identifier,
          nextRun.graph_state.graph_version,
          active.signal,
        ),
        api.getWorkflowEvidence(identifier, active.signal),
      ])
      if (active.signal.aborted) return false
      setRun(nextRun)
      setGraph(nextGraph)
      setEvidence(nextEvidence)
      return true
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === 'AbortError') return false
      const detail = message(caught)
      setError(detail.text)
      setErrorStatus(detail.status)
      setRun(null)
      setGraph(null)
      setEvidence(null)
      return false
    } finally {
      if (!active.signal.aborted) setLoading(false)
    }
  }, [api])

  const open = useCallback(async () => {
    const identifier = graphRunIdentifierInput.trim()
    if (!identifier) {
      setError('Enter a workflow run identifier.')
      setErrorStatus(null)
      return false
    }
    return load(identifier)
  }, [graphRunIdentifierInput, load])

  const refresh = useCallback(async () => {
    if (run) await load(run.graph_state.graph_run_identifier)
  }, [load, run])

  const mutate = useCallback(async (
    operation: (identifier: string, signal: AbortSignal) => Promise<WorkflowRunSnapshotResponse>,
  ) => {
    if (!run) return
    controller.current?.abort()
    const active = new AbortController()
    controller.current = active
    setMutating(true)
    setError(null)
    setErrorStatus(null)
    try {
      const updated = await operation(run.graph_state.graph_run_identifier, active.signal)
      if (active.signal.aborted) return
      setRun(updated)
      const [updatedGraph, updatedEvidence] = await Promise.all([
        api.getWorkflowGraph(
          updated.graph_state.graph_identifier,
          updated.graph_state.graph_version,
          active.signal,
        ),
        api.getWorkflowEvidence(updated.graph_state.graph_run_identifier, active.signal),
      ])
      if (!active.signal.aborted) {
        setGraph(updatedGraph)
        setEvidence(updatedEvidence)
      }
    } catch (caught) {
      if (caught instanceof DOMException && caught.name === 'AbortError') return
      const detail = message(caught)
      setError(detail.text)
      setErrorStatus(detail.status)
    } finally {
      if (!active.signal.aborted) setMutating(false)
    }
  }, [api, run])

  const resume = useCallback(
    () => mutate((identifier, signal) => api.resumeWorkflowRun(identifier, 1000, signal)),
    [api, mutate],
  )
  const cancel = useCallback(
    (reason?: string) => mutate((identifier, signal) => api.cancelWorkflowRun(identifier, reason, signal)),
    [api, mutate],
  )
  const decideApproval = useCallback(
    (approvalIdentifier: string, granted: boolean) => mutate(
      (identifier, signal) => api.decideWorkflowApproval(
        identifier,
        approvalIdentifier,
        granted,
        granted ? 'Approved in the workflow workspace.' : 'Denied in the workflow workspace.',
        signal,
      ),
    ),
    [api, mutate],
  )

  const clear = useCallback(() => {
    controller.current?.abort()
    setGraphRunIdentifierInput('')
    setLoading(false)
    setMutating(false)
    setError(null)
    setErrorStatus(null)
    setGraph(null)
    setRun(null)
    setEvidence(null)
  }, [])

  return {
    graphRunIdentifierInput,
    setGraphRunIdentifierInput,
    loading,
    mutating,
    error,
    errorStatus,
    graph,
    run,
    evidence,
    open,
    refresh,
    resume,
    cancel,
    decideApproval,
    clear,
  }
}
