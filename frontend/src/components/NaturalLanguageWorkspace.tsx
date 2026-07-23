import { useState } from 'react'
import type {
  InterpretedAtom,
  InterpretedBondLength,
  InterpretedScientificSpecification,
  ProvenancedValue,
  ScientificFieldProvenance,
} from '../api/types'
import type { ReturnTypeOfNaturalLanguageHook } from './natural-language-types'
import type { PresetSummaryResponse } from '../api/types'
import { PresetMenu } from './PresetMenu'

const provenanceLabels: Record<ScientificFieldProvenance, string> = {
  explicit: 'Explicit',
  derived: 'Derived',
  assumed: 'Assumed',
  missing: 'Missing',
}

function ReviewField({
  label,
  field,
  onChange,
  numeric = false,
  readOnly = false,
}: {
  label: string
  field: ProvenancedValue<string | number>
  onChange?: (value: string) => void
  numeric?: boolean
  readOnly?: boolean
}) {
  return (
    <label className={`review-field provenance-${field.provenance}`}>
      <span>{label}</span>
      <input
        aria-label={label}
        type={numeric ? 'number' : 'text'}
        step={numeric ? 'any' : undefined}
        value={field.value ?? ''}
        placeholder={field.provenance === 'missing' ? 'Required information missing' : ''}
        readOnly={readOnly}
        onChange={(event) => onChange?.(event.target.value)}
      />
      <small className="provenance-label">{provenanceLabels[field.provenance]}</small>
    </label>
  )
}

function geometrySummary(specification: InterpretedScientificSpecification): string {
  const atoms = specification.molecule.atoms
  if (!atoms.value?.length) return 'No atom coordinates supplied'
  if (atoms.value.some((atom) => atom.coordinates === null)) {
    return `${atoms.value.map((atom) => atom.element).join(' ')}; coordinates missing`
  }
  return atoms.value.map((atom) =>
    `${atom.element} (${atom.coordinates!.map((value) => value.toFixed(4)).join(', ')})`,
  ).join('; ')
}

function updateString(
  current: InterpretedScientificSpecification,
  key: 'scientific_objective' | 'requested_quantity' | 'coordinate_unit' | 'basis'
    | 'electronic_structure_method' | 'active_space' | 'mapper' | 'ansatz'
    | 'optimizer' | 'requested_execution_target' | 'requested_backend',
  value: string,
): InterpretedScientificSpecification {
  const next = structuredClone(current)
  next[key] = { value: value.trim() || null, provenance: value.trim() ? 'explicit' : 'missing' }
  return next
}

function updateNumber(
  current: InterpretedScientificSpecification,
  key: 'charge' | 'multiplicity' | 'tolerance' | 'shots' | 'precision',
  value: string,
): InterpretedScientificSpecification {
  const next = structuredClone(current)
  const parsed = value.trim() === '' ? null : Number(value)
  const number = parsed !== null && Number.isFinite(parsed) ? parsed : null
  next[key] = { value: number, provenance: number === null ? 'missing' : 'explicit' }
  return next
}

function updateMolecule(
  current: InterpretedScientificSpecification,
  key: 'name' | 'formula' | 'smiles' | 'inchi' | 'geometry_description',
  value: string,
): InterpretedScientificSpecification {
  const next = structuredClone(current)
  next.molecule[key] = { value: value.trim() || null, provenance: value.trim() ? 'explicit' : 'missing' }
  return next
}

function GeometryEditor({
  specification,
  setSpecification,
}: {
  specification: InterpretedScientificSpecification
  setSpecification: (value: InterpretedScientificSpecification) => void
}) {
  const [geometryText, setGeometryText] = useState(
    JSON.stringify(specification.molecule.atoms.value ?? [], null, 2),
  )
  const changeGeometry = (text: string) => {
    setGeometryText(text)
    const next = structuredClone(specification)
    try {
      const parsed: unknown = JSON.parse(text)
      if (!Array.isArray(parsed) || parsed.length === 0 || !parsed.every((atom): atom is InterpretedAtom =>
        typeof atom === 'object' && atom !== null
        && typeof (atom as { element?: unknown }).element === 'string'
        && Array.isArray((atom as { coordinates?: unknown }).coordinates)
        && (atom as { coordinates: unknown[] }).coordinates.length === 3
        && (atom as { coordinates: unknown[] }).coordinates.every((coordinate) =>
          typeof coordinate === 'number' && Number.isFinite(coordinate)))) {
        throw new Error('Invalid atom geometry')
      }
      next.molecule.atoms = { value: parsed, provenance: 'explicit' }
    } catch {
      next.molecule.atoms = { value: null, provenance: 'missing' }
    }
    setSpecification(next)
  }
  return (
    <>
      <label htmlFor="atom-geometry-json">Review atom coordinates as JSON</label>
      <textarea
        id="atom-geometry-json"
        rows={6}
        spellCheck={false}
        value={geometryText}
        onChange={(event) => changeGeometry(event.target.value)}
      />
      <small>Each atom requires an element and a finite three-number coordinates array.</small>
    </>
  )
}

function BondEditor({
  specification,
  setSpecification,
}: {
  specification: InterpretedScientificSpecification
  setSpecification: (value: InterpretedScientificSpecification) => void
}) {
  const [bondText, setBondText] = useState(
    JSON.stringify(specification.molecule.bond_lengths.value ?? [], null, 2),
  )
  const changeBonds = (text: string) => {
    setBondText(text)
    const next = structuredClone(specification)
    try {
      const parsed: unknown = JSON.parse(text)
      if (!Array.isArray(parsed) || !parsed.every((bond): bond is InterpretedBondLength =>
        typeof bond === 'object' && bond !== null
        && Array.isArray((bond as { atom_indices?: unknown }).atom_indices)
        && (bond as { atom_indices: unknown[] }).atom_indices.length === 2
        && (bond as { atom_indices: unknown[] }).atom_indices.every((index) =>
          typeof index === 'number' && Number.isInteger(index))
        && typeof (bond as { value?: unknown }).value === 'number'
        && Number.isFinite((bond as { value: number }).value)
        && ['angstrom', 'bohr'].includes(String((bond as { unit?: unknown }).unit)))) {
        throw new Error('Invalid bond lengths')
      }
      next.molecule.bond_lengths = {
        value: parsed.length ? parsed : null,
        provenance: parsed.length ? 'explicit' : 'missing',
      }
    } catch {
      next.molecule.bond_lengths = { value: null, provenance: 'missing' }
    }
    setSpecification(next)
  }
  return (
    <>
      <label htmlFor="bond-lengths-json">Review bond lengths and units as JSON</label>
      <textarea
        id="bond-lengths-json"
        rows={5}
        spellCheck={false}
        value={bondText}
        onChange={(event) => changeBonds(event.target.value)}
      />
      <small>
        Each bond requires two zero-based atom indices, a finite positive value, and an angstrom or bohr unit.
        {' '}<span className={`provenance-label provenance-${specification.molecule.bond_lengths.provenance}`}>
          {provenanceLabels[specification.molecule.bond_lengths.provenance]}
        </span>
      </small>
    </>
  )
}

export function NaturalLanguageWorkspace({
  presets,
  loading,
  onPresetChange,
  naturalLanguage,
}: {
  presets: PresetSummaryResponse[]
  loading: boolean
  onPresetChange: (identifier: string) => void
  naturalLanguage: ReturnTypeOfNaturalLanguageHook
}) {
  const specification = naturalLanguage.reviewedSpecification
  const setSpecification = naturalLanguage.updateReviewedSpecification
  const changeString = (key: Parameters<typeof updateString>[1], value: string) => {
    if (specification) setSpecification(updateString(specification, key, value))
  }
  const changeNumber = (key: Parameters<typeof updateNumber>[1], value: string) => {
    if (specification) setSpecification(updateNumber(specification, key, value))
  }
  const changeMolecule = (key: Parameters<typeof updateMolecule>[1], value: string) => {
    if (specification) setSpecification(updateMolecule(specification, key, value))
  }

  return (
    <main className="natural-language-workspace" id="workspace-home">
      <section className="objective-composer" aria-labelledby="objective-title">
        <p className="section-kicker">New experiment</p>
        <h1 id="objective-title">Ask a chemistry question</h1>
        <p className="objective-intro">
          Pulsate asks the configured scientific language model for a draft. You remain responsible for reviewing every value and assumption.
        </p>
        <label htmlFor="scientific-question">Natural-language chemistry question</label>
        <textarea
          id="scientific-question"
          rows={6}
          placeholder="Prepare an electronic ground-state experiment for linear beryllium hydride with 1.33 angstrom Be-H bonds on IBM Quantum."
          value={naturalLanguage.question}
          onChange={(event) => naturalLanguage.updateQuestion(event.target.value)}
        />
        <div className="objective-actions">
          <PresetMenu presets={presets} disabled={loading || naturalLanguage.interpreting} onSelect={onPresetChange} />
          <button
            className="primary-button"
            type="button"
            disabled={naturalLanguage.interpreting || !naturalLanguage.question.trim()}
            onClick={() => void naturalLanguage.interpretQuestion()}
          >
            {naturalLanguage.interpreting ? 'Interpreting question…' : 'Interpret question'}
          </button>
        </div>
        <div aria-live="polite">
          {naturalLanguage.interpreting && <p>Waiting for the configured model and validating its draft…</p>}
          {naturalLanguage.error && <p className="inline-error">{naturalLanguage.error}</p>}
        </div>
      </section>

      {specification ? (
        <section className="interpretation-review" aria-labelledby="review-title">
          <div className="review-heading">
            <div>
              <p className="section-kicker">Scientist review</p>
              <h2 id="review-title">Structured scientific specification</h2>
            </div>
            <span className={`support-status support-${specification.execution_support_status}`}>
              {specification.execution_support_status.replaceAll('_', ' ')}
            </span>
          </div>
          <p className="model-provenance">
            Drafted by {specification.model_provenance.model_name} · {specification.model_provenance.request_count_for_interpretation} model request{specification.model_provenance.request_count_for_interpretation === 1 ? '' : 's'}
          </p>
          <p className="interpreted-question">
            <strong>Interpreted original question</strong>
            <span>{specification.original_question}</span>
          </p>
          <div className="review-grid">
            <ReviewField label="Scientific objective" field={specification.scientific_objective} onChange={(value) => changeString('scientific_objective', value)} />
            <ReviewField label="Requested quantity" field={specification.requested_quantity} onChange={(value) => changeString('requested_quantity', value)} />
            <ReviewField label="Molecule name" field={specification.molecule.name} onChange={(value) => changeMolecule('name', value)} />
            <ReviewField label="Formula" field={specification.molecule.formula} onChange={(value) => changeMolecule('formula', value)} />
            <ReviewField label="SMILES" field={specification.molecule.smiles} onChange={(value) => changeMolecule('smiles', value)} />
            <ReviewField label="InChI" field={specification.molecule.inchi} onChange={(value) => changeMolecule('inchi', value)} />
            <ReviewField label="Geometry description" field={specification.molecule.geometry_description} onChange={(value) => changeMolecule('geometry_description', value)} />
            <ReviewField label="Coordinate unit" field={specification.coordinate_unit} onChange={(value) => changeString('coordinate_unit', value)} />
            <ReviewField label="Charge" field={specification.charge} numeric onChange={(value) => changeNumber('charge', value)} />
            <ReviewField label="Multiplicity" field={specification.multiplicity} numeric onChange={(value) => changeNumber('multiplicity', value)} />
            <ReviewField label="Basis" field={specification.basis} onChange={(value) => changeString('basis', value)} />
            <ReviewField label="Electronic structure method" field={specification.electronic_structure_method} onChange={(value) => changeString('electronic_structure_method', value)} />
            <ReviewField label="Active space" field={specification.active_space} onChange={(value) => changeString('active_space', value)} />
            <ReviewField label="Mapper" field={specification.mapper} onChange={(value) => changeString('mapper', value)} />
            <ReviewField label="Ansatz" field={specification.ansatz} onChange={(value) => changeString('ansatz', value)} />
            <ReviewField label="Optimizer" field={specification.optimizer} onChange={(value) => changeString('optimizer', value)} />
            <ReviewField label="Tolerance" field={specification.tolerance} numeric onChange={(value) => changeNumber('tolerance', value)} />
            <ReviewField label="Execution target" field={specification.requested_execution_target} onChange={(value) => changeString('requested_execution_target', value)} />
            <ReviewField label="IBM backend" field={specification.requested_backend} onChange={(value) => changeString('requested_backend', value)} />
            <ReviewField label="Shots" field={specification.shots} numeric onChange={(value) => changeNumber('shots', value)} />
            <ReviewField label="Precision" field={specification.precision} numeric onChange={(value) => changeNumber('precision', value)} />
          </div>
          <div className="geometry-evidence">
            <strong>Atom geometry</strong>
            <p>{geometrySummary(specification)}</p>
            <span className={`provenance-label provenance-${specification.molecule.atoms.provenance}`}>
              {provenanceLabels[specification.molecule.atoms.provenance]}
            </span>
            <GeometryEditor
              key={naturalLanguage.interpretation?.interpretation_identifier}
              specification={specification}
              setSpecification={setSpecification}
            />
            <BondEditor
              key={`bonds-${naturalLanguage.interpretation?.interpretation_identifier}`}
              specification={specification}
              setSpecification={setSpecification}
            />
          </div>
          <div className="review-notices">
            {specification.assumptions.length > 0 && (
              <div><strong>Assumptions requiring approval</strong><ul>{specification.assumptions.map((item) => <li key={item}>{item}</li>)}</ul></div>
            )}
            {naturalLanguage.unresolvedFields.length > 0 && (
              <div><strong>Missing required information</strong><p>{naturalLanguage.unresolvedFields.join(', ')}</p></div>
            )}
            {specification.warnings.length > 0 && (
              <div><strong>Warnings</strong><ul>{specification.warnings.map((item) => <li key={item}>{item}</li>)}</ul></div>
            )}
          </div>
          {specification.assumptions.length > 0 && (
            <label className="assumption-acceptance">
              <input
                type="checkbox"
                checked={naturalLanguage.acceptedAssumptions}
                onChange={(event) => naturalLanguage.setAcceptedAssumptions(event.target.checked)}
              />
              I have reviewed and accept the listed scientific assumptions.
            </label>
          )}
          <button
            className="primary-button approval-button"
            type="button"
            disabled={naturalLanguage.approvalDisabled}
            onClick={() => void naturalLanguage.approve()}
          >
            {naturalLanguage.approving ? 'Approving…' : 'Approve immutable experiment'}
          </button>
        </section>
      ) : (
        <section className="empty-molecular-canvas" aria-label="Interpretation guidance">
          <strong>Model output is a draft, never execution authorization.</strong>
          <span>Interpret a question to review its molecular identity, scientific settings, provenance, and missing information.</span>
        </section>
      )}

      {naturalLanguage.approvedExperiment && (
        <section className="approved-experiment" aria-live="polite">
          <p className="section-kicker">Approved experiment</p>
          <h2>
            {naturalLanguage.approvedExperiment.requested_execution_target === 'ibm_quantum'
              && naturalLanguage.approvedExperiment.status === 'ready_for_ibm_submission'
              ? 'Ready for IBM submission'
              : naturalLanguage.approvedExperiment.status === 'approved_pending_compiler_support'
                ? 'Approved pending compiler support'
                : 'Approved experiment'}
          </h2>
          <dl>
            <div><dt>Immutable experiment identifier</dt><dd>{naturalLanguage.approvedExperiment.experiment_identifier}</dd></div>
            <div><dt>Specification SHA-256</dt><dd>{naturalLanguage.approvedExperiment.specification_sha256}</dd></div>
            <div><dt>Execution target</dt><dd>{naturalLanguage.approvedExperiment.requested_execution_target.replaceAll('_', ' ')}</dd></div>
            <div><dt>Status</dt><dd>{naturalLanguage.approvedExperiment.status.replaceAll('_', ' ')}</dd></div>
          </dl>
          {naturalLanguage.approvedExperiment.requested_execution_target === 'ibm_quantum'
            && <p>No IBM job has been submitted.</p>}
        </section>
      )}
    </main>
  )
}
