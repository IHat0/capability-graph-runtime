import type {
  RunReceiptResponse,
  RunResultsResponse,
  RunStateResponse,
  RunVerificationResponse,
} from './api/types'
import type { HeaderStatusView } from './components/Header'

export function deriveExistingRunHeaderStatus({
  loading,
  run,
  results,
  verification,
  receipt,
}: {
  loading: boolean
  run: RunStateResponse | null
  results: RunResultsResponse | null
  verification: RunVerificationResponse | null
  receipt: RunReceiptResponse | null
}): HeaderStatusView | undefined {
  if (loading) {
    return {
      primary: 'Read-only lookup',
      secondary: 'Opening persisted run',
    }
  }
  if (!run) return undefined

  const ibmExecution = verification?.ibm_execution
    ?? results?.ibm_execution
    ?? receipt?.ibm_execution
  return ibmExecution?.execution_integrity_passed === true
    && ibmExecution.scientific_quality_passed === false
    ? {
        primary: 'IBM execution verified',
        secondary: 'Scientific result rejected',
      }
    : {
        primary: 'Read-only persisted run',
        secondary: run.status === 'authorized'
          ? 'Scientific result authorized'
          : `Run ${run.status}`,
      }
}
