import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ApiError, pulsateApi, type PulsateApi } from '../api/client'
import type {
  RunCapabilityResponse,
  RunReceiptResponse,
  RunResultsResponse,
  RunStateResponse,
  RunVerificationResponse,
} from '../api/types'

const TERMINAL = new Set(['authorized', 'rejected', 'failed', 'interrupted'])
export const RUN_POLL_INTERVAL_MS = 1200

function messageFor(error: unknown): string {
  return error instanceof Error ? error.message : 'An unexpected run API error occurred.'
}

function storageKey(presetIdentifier: string): string {
  return `pulsate.active-run.v1:${window.location.origin}:${presetIdentifier}`
}

function newIdempotencyKey(): string {
  const random = typeof crypto.randomUUID === 'function' ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`
  return `pulsate-${random}`
}

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError'
}

function completedEvidenceMatches(
  results: RunResultsResponse,
  verification: RunVerificationResponse,
  receipt: RunReceiptResponse,
): boolean {
  return results.structure_sha256 === verification.structure_sha256
    && results.structure_sha256 === receipt.structure_sha256
    && results.hamiltonian_sha256 === receipt.hamiltonian_sha256
    && results.exact_scientific_result_sha256 === receipt.exact_scientific_result_sha256
    && results.vqe_scientific_result_sha256 === receipt.vqe_scientific_result_sha256
    && results.scientific_outcome_sha256 === receipt.scientific_outcome_sha256
    && results.execution_environment_identity === receipt.execution_environment_identity
    && results.receipt_sha256 === receipt.receipt_sha256
    && verification.verification_passed === receipt.verification_passed
    && verification.authorization_state === receipt.authorization_state
    && receipt.authorized === (receipt.authorization_state === 'authorized')
}

export function usePresetRun({
  api = pulsateApi,
  selectedPresetId,
  displayedPresetId,
  experimentIdentifier,
  experimentFingerprint,
  expectedExperimentSha256,
  structureIdentifier,
  structureSha256,
}: {
  api?: PulsateApi
  selectedPresetId: string | null
  displayedPresetId: string | null
  experimentIdentifier?: string
  experimentFingerprint?: string
  expectedExperimentSha256?: string
  structureIdentifier?: string
  structureSha256?: string
}) {
  const [capability, setCapability] = useState<RunCapabilityResponse | null>(null)
  const [run, setRun] = useState<RunStateResponse | null>(null)
  const [results, setResults] = useState<RunResultsResponse | null>(null)
  const [verification, setVerification] = useState<RunVerificationResponse | null>(null)
  const [receipt, setReceipt] = useState<RunReceiptResponse | null>(null)
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const creatingRef = useRef(false)
  const generationRef = useRef(0)
  const activeCreateRef = useRef<{ controller: AbortController; generation: number; identityKey: string } | null>(null)
  const pendingIdempotencyKeys = useRef(new Map<string, string>())

  const sceneIdentityKey = useMemo(() => JSON.stringify([
    displayedPresetId, experimentIdentifier ?? null, experimentFingerprint ?? null,
    expectedExperimentSha256 ?? null, structureIdentifier ?? null, structureSha256 ?? null,
  ]), [displayedPresetId, expectedExperimentSha256, experimentFingerprint, experimentIdentifier, structureIdentifier, structureSha256])

  const stateIdentityMatches = useCallback((value: RunStateResponse | RunResultsResponse | RunVerificationResponse | RunReceiptResponse): boolean => (
    value.preset_identifier === displayedPresetId
    && (!experimentIdentifier || value.experiment_identifier === experimentIdentifier)
    && (!experimentFingerprint || value.experiment_fingerprint === experimentFingerprint)
    && (!expectedExperimentSha256 || value.expected_experiment_sha256 === expectedExperimentSha256)
    && (!structureIdentifier || value.structure_identifier === structureIdentifier)
  ), [displayedPresetId, expectedExperimentSha256, experimentFingerprint, experimentIdentifier, structureIdentifier])

  const evidenceIdentityMatches = useCallback((value: RunResultsResponse | RunVerificationResponse | RunReceiptResponse): boolean => (
    stateIdentityMatches(value) && (!structureSha256 || value.structure_sha256 === structureSha256)
  ), [stateIdentityMatches, structureSha256])

  useEffect(() => {
    const controller = new AbortController()
    api.getRunCapability(controller.signal).then(setCapability).catch((cause) => {
      if (controller.signal.aborted) return
      setCapability({ available: false, execution_targets: [], reason: messageFor(cause), maximum_run_seconds: null })
    })
    return () => controller.abort()
  }, [api])

  useEffect(() => {
    generationRef.current += 1
    activeCreateRef.current?.controller.abort()
    activeCreateRef.current = null
    creatingRef.current = false
    setCreating(false)
    setRun(null)
    setResults(null)
    setVerification(null)
    setReceipt(null)
    setError(null)
    if (!displayedPresetId) return
    const saved = localStorage.getItem(storageKey(displayedPresetId))
    if (!saved) return
    try {
      const value = JSON.parse(saved) as { runIdentifier?: unknown; presetIdentifier?: unknown }
      if (typeof value.runIdentifier !== 'string' || value.presetIdentifier !== displayedPresetId) return
      const controller = new AbortController()
      const generation = generationRef.current
      api.getRun(value.runIdentifier, controller.signal).then((restored) => {
        if (controller.signal.aborted || generation !== generationRef.current) return
        if (!stateIdentityMatches(restored)) {
          setError('Stored run identity does not match the displayed experiment; its evidence was not shown.')
          return
        }
        setRun(restored)
        if (restored.status === 'failed' || restored.status === 'interrupted') {
          setError(restored.error?.message ?? `The previous run is ${restored.status}.`)
        }
      }).catch((cause) => {
        if (!controller.signal.aborted && generation === generationRef.current) {
          setError(`Unable to resume the previous run: ${messageFor(cause)}`)
        }
      })
      return () => controller.abort()
    } catch {
      localStorage.removeItem(storageKey(displayedPresetId))
    }
  }, [api, displayedPresetId, sceneIdentityKey, stateIdentityMatches])

  useEffect(() => () => {
    generationRef.current += 1
    activeCreateRef.current?.controller.abort()
    activeCreateRef.current = null
    creatingRef.current = false
  }, [])

  useEffect(() => {
    if (!run || TERMINAL.has(run.status)) return
    let active = true
    let timer: ReturnType<typeof setTimeout> | undefined
    let controller: AbortController | undefined
    const generation = generationRef.current
    const poll = async () => {
      controller = new AbortController()
      try {
        const current = await api.getRun(run.run_identifier, controller.signal)
        if (!active || generation !== generationRef.current) return
        if (!stateIdentityMatches(current)) {
          setError('Run status identity does not match the displayed experiment; polling was stopped.')
          return
        }
        setRun(current)
        if (current.status === 'failed' || current.status === 'interrupted') {
          setError(current.error?.message ?? `The run is ${current.status}.`)
        }
        if (!TERMINAL.has(current.status)) timer = setTimeout(poll, RUN_POLL_INTERVAL_MS)
      } catch (cause) {
        if (!active || controller.signal.aborted || generation !== generationRef.current) return
        setError(`Run status could not be refreshed: ${messageFor(cause)}`)
        timer = setTimeout(poll, RUN_POLL_INTERVAL_MS)
      }
    }
    timer = setTimeout(poll, RUN_POLL_INTERVAL_MS)
    return () => {
      active = false
      if (timer) clearTimeout(timer)
      controller?.abort()
    }
  }, [api, run, stateIdentityMatches])

  useEffect(() => {
    if (!run || !['authorized', 'rejected'].includes(run.status)) return
    const controller = new AbortController()
    const generation = generationRef.current
    Promise.all([
      api.getRunResults(run.run_identifier, controller.signal),
      api.getRunVerification(run.run_identifier, controller.signal),
      api.getRunReceipt(run.run_identifier, controller.signal),
    ]).then(([nextResults, nextVerification, nextReceipt]) => {
      if (controller.signal.aborted || generation !== generationRef.current) return
      if (!evidenceIdentityMatches(nextResults)
        || !evidenceIdentityMatches(nextVerification)
        || !evidenceIdentityMatches(nextReceipt)
        || nextResults.run_identifier !== run.run_identifier
        || nextVerification.run_identifier !== run.run_identifier
        || nextReceipt.run_identifier !== run.run_identifier
        || !completedEvidenceMatches(nextResults, nextVerification, nextReceipt)) {
        setError('Returned evidence identity does not match the active run and experiment; it was not displayed.')
        return
      }
      setResults(nextResults)
      setVerification(nextVerification)
      setReceipt(nextReceipt)
    }).catch((cause) => {
      if (!controller.signal.aborted && generation === generationRef.current) {
        setError(`Completed run evidence could not be loaded: ${messageFor(cause)}`)
      }
    })
    return () => controller.abort()
  }, [api, evidenceIdentityMatches, run])

  const startRun = useCallback(async () => {
    if (creatingRef.current || !displayedPresetId || selectedPresetId !== displayedPresetId || !capability?.available) return
    const generation = generationRef.current
    const identityKey = sceneIdentityKey
    const controller = new AbortController()
    const key = pendingIdempotencyKeys.current.get(identityKey) ?? newIdempotencyKey()
    pendingIdempotencyKeys.current.set(identityKey, key)
    activeCreateRef.current = { controller, generation, identityKey }
    creatingRef.current = true
    setCreating(true)
    setError(null)
    setResults(null)
    setVerification(null)
    setReceipt(null)
    try {
      const created = await api.createRun(displayedPresetId, key, controller.signal)
      if (controller.signal.aborted || generation !== generationRef.current
        || activeCreateRef.current?.generation !== generation
        || activeCreateRef.current.identityKey !== identityKey) return
      if (!stateIdentityMatches(created)) {
        setError('Created run identity does not match the displayed experiment; the run was not attached to this workspace.')
        return
      }
      pendingIdempotencyKeys.current.delete(identityKey)
      localStorage.setItem(storageKey(displayedPresetId), JSON.stringify({
        runIdentifier: created.run_identifier,
        presetIdentifier: displayedPresetId,
      }))
      setRun(created)
    } catch (cause) {
      if (controller.signal.aborted || generation !== generationRef.current || isAbort(cause)) return
      if (cause instanceof ApiError && cause.status !== undefined && cause.status >= 400 && cause.status < 500) {
        pendingIdempotencyKeys.current.delete(identityKey)
      }
      setError(`Run could not be created: ${messageFor(cause)}`)
    } finally {
      if (activeCreateRef.current?.generation === generation && activeCreateRef.current.identityKey === identityKey) {
        activeCreateRef.current = null
        creatingRef.current = false
        setCreating(false)
      }
    }
  }, [api, capability?.available, displayedPresetId, sceneIdentityKey, selectedPresetId, stateIdentityMatches])

  const staleIdentity = Boolean(displayedPresetId && selectedPresetId !== displayedPresetId)
  const active = Boolean(run && !TERMINAL.has(run.status))
  const canRun = Boolean(capability?.available && displayedPresetId && !staleIdentity && !creating && !active)
  const disabledReason = !displayedPresetId
    ? 'Load a verified preset before running.'
    : staleIdentity
      ? 'The selected preset is not the structure currently displayed. Wait for it to load before running.'
      : capability && !capability.available
        ? capability.reason ?? 'Local execution is unavailable.'
        : active
          ? 'The active run must finish before another run is started.'
          : creating
            ? 'Creating a run…'
            : capability === null
              ? 'Checking local execution capability…'
              : null

  return { capability, run, results, verification, receipt, creating, error, canRun, disabledReason, startRun }
}
