import { useState } from 'react'
import type { RunReceiptResponse, RunResultsResponse, RunStateResponse, RunVerificationResponse } from '../api/types'

function scientificValue(value: number | undefined, unit = 'Ha'): React.ReactNode {
  if (value === undefined) return 'Not executed'
  const full = value.toString()
  return <span title={`${full} ${unit}`}>{value.toPrecision(11)} {unit}</span>
}

function nested(receipt: RunReceiptResponse, path: string[]): unknown {
  let value: unknown = receipt
  for (const key of path) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined
    value = (value as Record<string, unknown>)[key]
  }
  return value
}

function ReceiptPanel({ receipt, onClose }: { receipt: RunReceiptResponse; onClose: () => void }) {
  const rows: Array<[string, unknown]> = [
    ['Run', receipt.run_identifier],
    ['Preset', receipt.preset_identifier],
    ['Execution', receipt.execution_identifier],
    ['Experiment SHA-256', nested(receipt, ['experiment', 'content_sha256']) ?? receipt.experiment_identifier],
    ['Structure SHA-256', nested(receipt, ['scientific_outcome', 'molecular_structure_sha256']) ?? receipt.structure_sha256],
    ['Hamiltonian SHA-256', receipt.hamiltonian_sha256],
    ['Exact result identity', receipt.exact_scientific_result_sha256],
    ['VQE result identity', receipt.vqe_scientific_result_sha256],
    ['Verification outcome', receipt.verification_passed],
    ['Environment identity', receipt.execution_environment_identity],
    ['Authorized', receipt.authorized],
  ]
  return (
    <div className="receipt-panel" role="region" aria-label="Authorization receipt">
      <div className="receipt-panel__header"><strong>Structured receipt</strong><button type="button" onClick={onClose}>Close</button></div>
      <dl className="receipt-identities">
        {rows.filter(([, value]) => value !== undefined).map(([label, value]) => (
          <div key={label}><dt>{label}</dt><dd>{String(value)}</dd></div>
        ))}
      </dl>
    </div>
  )
}

export function ResultSummary({ run, results, verification, receipt }: {
  run: RunStateResponse | null
  results: RunResultsResponse | null
  verification: RunVerificationResponse | null
  receipt: RunReceiptResponse | null
}) {
  const [showReceipt, setShowReceipt] = useState(false)
  return (
    <>
      <section className="inspector-section" id="results" aria-labelledby="results-title">
        <p className="section-kicker">Results</p>
        <h2 id="results-title">Energy summary</h2>
        <dl className="result-list">
          <div><dt>Exact energy</dt><dd>{scientificValue(results?.exact_total_energy_hartree)}</dd></div>
          <div><dt>VQE energy</dt><dd>{scientificValue(results?.vqe_total_energy_hartree)}</dd></div>
          <div><dt>Absolute difference</dt><dd>{scientificValue(results?.absolute_difference_hartree)}</dd></div>
          <div><dt>Tolerance</dt><dd>{scientificValue(results?.tolerance_hartree)}</dd></div>
          <div><dt>Energy unit</dt><dd>{results?.energy_unit ?? 'Not executed'}</dd></div>
          <div><dt>IBM energy</dt><dd>Not configured</dd></div>
        </dl>
      </section>
      <section className="inspector-section" id="evidence" aria-labelledby="evidence-title">
        <p className="section-kicker">Evidence</p>
        <h2 id="evidence-title">Verification</h2>
        <dl className="result-list">
          <div><dt>Run</dt><dd>{run?.status ?? 'Not started'}</dd></div>
          <div><dt>Verification</dt><dd>{verification ? (verification.verification_passed ? 'Passed' : 'Rejected') : 'Pending execution'}</dd></div>
          <div><dt>Authorization</dt><dd>{verification?.authorization_state ?? 'Pending execution'}</dd></div>
          <div><dt>Receipt</dt><dd>{receipt ? 'Available' : 'Unavailable'}</dd></div>
        </dl>
        {receipt ? <button className="receipt-action" type="button" onClick={() => setShowReceipt((value) => !value)}>{showReceipt ? 'Hide receipt' : 'View receipt'}</button>
          : <p className="supporting-copy">Receipt unavailable until scientific execution reaches verification.</p>}
        {showReceipt && receipt && <ReceiptPanel receipt={receipt} onClose={() => setShowReceipt(false)} />}
      </section>
    </>
  )
}
