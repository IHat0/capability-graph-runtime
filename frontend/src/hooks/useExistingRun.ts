import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, pulsateApi, type PulsateApi } from '../api/client'
import type {
  RunIdentity,
  RunReceiptResponse,
  RunResultsResponse,
  RunStateResponse,
  RunVerificationResponse,
  SceneResponse,
} from '../api/types'
import { normalizeScene } from '../scene/normalize'
import type { MolecularScene } from '../scene/types'
import { completedEvidenceMatches } from './usePresetRun'

export const CANONICAL_RUN_IDENTIFIER = /^run-[0-9a-f]{32}$/

export type ExistingRunApi = Pick<
  PulsateApi,
  'getRun' | 'getRunScene' | 'getRunResults' | 'getRunVerification' | 'getRunReceipt'
>

type LoadStage = 'run' | 'scene' | 'artifacts'

function identityMatches(reference: RunIdentity, candidate: RunIdentity): boolean {
  return candidate.run_identifier === reference.run_identifier
    && candidate.source_type === reference.source_type
    && candidate.source_identifier === reference.source_identifier
    && candidate.preset_identifier === reference.preset_identifier
    && candidate.experiment_identifier === reference.experiment_identifier
    && candidate.experiment_fingerprint === reference.experiment_fingerprint
    && candidate.expected_experiment_sha256 === reference.expected_experiment_sha256
    && candidate.structure_identifier === reference.structure_identifier
}

function sceneIdentityMatches(run: RunStateResponse, scene: SceneResponse): boolean {
  return run.experiment_fingerprint === run.expected_experiment_sha256
    && scene.experiment_identifier === run.experiment_identifier
    && scene.experiment_fingerprint === run.experiment_fingerprint
    && (scene.expected_experiment_sha256 === undefined
      || scene.expected_experiment_sha256 === run.expected_experiment_sha256)
    && (scene.structure_identifier === undefined
      || scene.structure_identifier === run.structure_identifier)
    && (scene.structure_hash === undefined
      || run.structure_sha256 === undefined
      || run.structure_sha256 === null
      || scene.structure_hash === run.structure_sha256)
}

function evidenceMatches(
  run: RunStateResponse,
  results: RunResultsResponse,
  verification: RunVerificationResponse,
  receipt: RunReceiptResponse,
): boolean {
  return identityMatches(run, results)
    && identityMatches(run, verification)
    && identityMatches(run, receipt)
    && completedEvidenceMatches(results, verification, receipt)
}

function controlledError(error: unknown, stage: LoadStage): string {
  if (error instanceof ApiError) {
    if (error.status === undefined && error.cause !== undefined) {
      return 'Unable to reach the Pulsate API.'
    }
    if (error.code === 'run_not_found' || (stage === 'run' && error.status === 404)) {
      return 'Run not found. Check the run identifier and try again.'
    }
    if (error.status !== undefined && error.status >= 500) {
      return 'The Pulsate API could not load this run.'
    }
    if (error.code === 'scene_unavailable' || stage === 'scene') {
      return 'The persisted molecular scene is unavailable or corrupt.'
    }
    if (stage === 'artifacts') {
      return 'Terminal scientific evidence is unavailable for this run.'
    }
  }
  return 'The Pulsate API could not load this run.'
}

export function useExistingRun(api: ExistingRunApi = pulsateApi) {
  const [runIdentifierInput, setRunIdentifierInput] = useState('')
  const [run, setRun] = useState<RunStateResponse | null>(null)
  const [scene, setScene] = useState<MolecularScene | null>(null)
  const [results, setResults] = useState<RunResultsResponse | null>(null)
  const [verification, setVerification] = useState<RunVerificationResponse | null>(null)
  const [receipt, setReceipt] = useState<RunReceiptResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const generationRef = useRef(0)
  const controllerRef = useRef<AbortController | null>(null)

  const clearOpenedRun = useCallback(() => {
    generationRef.current += 1
    controllerRef.current?.abort()
    controllerRef.current = null
    setLoading(false)
    setError(null)
    setRun(null)
    setScene(null)
    setResults(null)
    setVerification(null)
    setReceipt(null)
  }, [])

  const openRun = useCallback(async (): Promise<boolean> => {
    const runIdentifier = runIdentifierInput.trim()
    if (!CANONICAL_RUN_IDENTIFIER.test(runIdentifier)) {
      generationRef.current += 1
      controllerRef.current?.abort()
      controllerRef.current = null
      setLoading(false)
      setError('Enter a valid run identifier in the form run- followed by 32 lowercase hexadecimal characters.')
      return false
    }

    generationRef.current += 1
    controllerRef.current?.abort()
    const generation = generationRef.current
    const controller = new AbortController()
    controllerRef.current = controller
    setRunIdentifierInput(runIdentifier)
    setLoading(true)
    setError(null)

    let stage: LoadStage = 'run'
    try {
      const nextRun = await api.getRun(runIdentifier, controller.signal)
      if (controller.signal.aborted || generation !== generationRef.current) return false
      if (nextRun.run_identifier !== runIdentifier) {
        throw new Error('identity_mismatch')
      }
      stage = 'scene'
      const rawScene = await api.getRunScene(runIdentifier, controller.signal)
      if (controller.signal.aborted || generation !== generationRef.current) return false
      if (!sceneIdentityMatches(nextRun, rawScene)) {
        throw new Error('identity_mismatch')
      }
      const normalizedScene = normalizeScene(rawScene)

      let nextResults: RunResultsResponse | null = null
      let nextVerification: RunVerificationResponse | null = null
      let nextReceipt: RunReceiptResponse | null = null
      if (nextRun.status === 'authorized' || nextRun.status === 'rejected') {
        stage = 'artifacts'
        const [loadedResults, loadedVerification, loadedReceipt] = await Promise.all([
          api.getRunResults(runIdentifier, controller.signal),
          api.getRunVerification(runIdentifier, controller.signal),
          api.getRunReceipt(runIdentifier, controller.signal),
        ])
        if (controller.signal.aborted || generation !== generationRef.current) return false
        nextResults = loadedResults
        nextVerification = loadedVerification
        nextReceipt = loadedReceipt
        if (!evidenceMatches(nextRun, nextResults, nextVerification, nextReceipt)) {
          throw new Error('identity_mismatch')
        }
      }

      setRun(nextRun)
      setScene({
        ...normalizedScene,
        experimentId: nextRun.experiment_identifier,
        experimentFingerprint: nextRun.experiment_fingerprint,
        expectedExperimentSha256: nextRun.expected_experiment_sha256,
        structureId: nextRun.structure_identifier,
      })
      setResults(nextResults)
      setVerification(nextVerification)
      setReceipt(nextReceipt)
      return true
    } catch (cause) {
      if (controller.signal.aborted || generation !== generationRef.current) return false
      setError(
        cause instanceof Error && cause.message === 'identity_mismatch'
          ? 'Run evidence identity mismatch. The requested run was not displayed.'
          : controlledError(cause, stage),
      )
      return false
    } finally {
      if (generation === generationRef.current) {
        controllerRef.current = null
        setLoading(false)
      }
    }
  }, [api, runIdentifierInput])

  useEffect(() => () => {
    generationRef.current += 1
    controllerRef.current?.abort()
  }, [])

  return {
    runIdentifierInput,
    setRunIdentifierInput,
    openRun,
    clearOpenedRun,
    run,
    scene,
    results,
    verification,
    receipt,
    loading,
    error,
  }
}
