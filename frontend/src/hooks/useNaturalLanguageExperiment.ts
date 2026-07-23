import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { pulsateApi, type WorkspaceApi } from '../api/client'
import type {
  ApprovedExperimentResponse,
  InterpretationResponse,
  InterpretedScientificSpecification,
} from '../api/types'

function messageFor(error: unknown): string {
  return error instanceof Error ? error.message : 'The scientific question could not be interpreted.'
}

const ELEMENT_SYMBOLS = new Set(
  `H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni
  Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe
  Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg
  Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Rf Db Sg
  Bh Hs Mt Ds Rg Cn Nh Fl Mc Lv Ts Og`.split(/\s+/),
)

function rebuildClientAssumptions(specification: InterpretedScientificSpecification): void {
  const assumed: string[] = []
  const fields: Array<[string, { value: unknown; provenance: string }]> = [
    ['scientific_objective', specification.scientific_objective],
    ['requested_quantity', specification.requested_quantity],
    ['molecule.name', specification.molecule.name],
    ['molecule.formula', specification.molecule.formula],
    ['molecule.smiles', specification.molecule.smiles],
    ['molecule.inchi', specification.molecule.inchi],
    ['molecule.geometry_description', specification.molecule.geometry_description],
    ['coordinate_unit', specification.coordinate_unit],
    ['charge', specification.charge],
    ['multiplicity', specification.multiplicity],
    ['basis', specification.basis],
    ['electronic_structure_method', specification.electronic_structure_method],
    ['active_space', specification.active_space],
    ['mapper', specification.mapper],
    ['ansatz', specification.ansatz],
    ['optimizer', specification.optimizer],
    ['tolerance', specification.tolerance],
    ['requested_execution_target', specification.requested_execution_target],
    ['requested_backend', specification.requested_backend],
    ['shots', specification.shots],
    ['precision', specification.precision],
  ]
  for (const [path, field] of fields) {
    if (field.provenance === 'assumed') assumed.push(`${path}=${String(field.value)} (scientist approval required)`)
  }
  specification.assumptions = assumed
}

export function unresolvedScientificFields(specification: InterpretedScientificSpecification | null): string[] {
  if (!specification) return []
  const missing: string[] = []
  const required = [
    ['scientific_objective', specification.scientific_objective.value],
    ['requested_quantity', specification.requested_quantity.value],
    ['coordinate_unit', specification.coordinate_unit.value],
    ['charge', specification.charge.value],
    ['multiplicity', specification.multiplicity.value],
    ['basis', specification.basis.value],
    ['electronic_structure_method', specification.electronic_structure_method.value],
    ['active_space', specification.active_space.value],
    ['mapper', specification.mapper.value],
    ['ansatz', specification.ansatz.value],
    ['optimizer', specification.optimizer.value],
    ['tolerance', specification.tolerance.value],
    ['requested_execution_target', specification.requested_execution_target.value],
  ] as const
  for (const [name, value] of required) {
    if (value === null || value === '') missing.push(name)
  }
  const molecule = specification.molecule
  if (![molecule.name.value, molecule.formula.value, molecule.smiles.value, molecule.inchi.value, molecule.atoms.value].some(Boolean)) {
    missing.push('molecular_identity')
  }
  if (!molecule.atoms.value?.length || molecule.atoms.value.some((atom) => atom.coordinates === null)) {
    missing.push('geometry')
  } else if (molecule.atoms.value.some((atom) =>
    !ELEMENT_SYMBOLS.has(atom.element)
    || atom.coordinates!.some((coordinate) => !Number.isFinite(coordinate)))) {
    missing.push('invalid_geometry')
  }
  if (!Number.isInteger(specification.charge.value)) missing.push('invalid_charge')
  if (!Number.isInteger(specification.multiplicity.value) || Number(specification.multiplicity.value) <= 0) {
    missing.push('invalid_multiplicity')
  }
  if (!Number.isFinite(specification.tolerance.value) || Number(specification.tolerance.value) <= 0) {
    missing.push('invalid_tolerance')
  }
  if (!['angstrom', 'bohr'].includes(String(specification.coordinate_unit.value))) {
    missing.push('invalid_coordinate_unit')
  }
  if (!['ibm_quantum', 'local_simulator'].includes(String(specification.requested_execution_target.value))) {
    missing.push('invalid_execution_target')
  }
  if (molecule.bond_lengths.value?.some((bond) => !Number.isFinite(bond.value) || bond.value <= 0)) {
    missing.push('invalid_bond_length')
  }
  if (molecule.bond_lengths.value?.some((bond) =>
    !Number.isInteger(bond.atom_indices[0])
    || !Number.isInteger(bond.atom_indices[1])
    || bond.atom_indices[0] < 0
    || bond.atom_indices[1] < 0
    || bond.atom_indices[0] === bond.atom_indices[1]
    || bond.atom_indices[0] >= (molecule.atoms.value?.length ?? 0)
    || bond.atom_indices[1] >= (molecule.atoms.value?.length ?? 0))) {
    missing.push('invalid_bond_indices')
  }
  if (molecule.bond_lengths.value?.some((bond) =>
    !['angstrom', 'bohr'].includes(bond.unit)
    || bond.unit !== specification.coordinate_unit.value)) {
    missing.push('invalid_bond_unit')
  }
  if (specification.shots.value !== null
    && (!Number.isInteger(specification.shots.value) || specification.shots.value <= 0)) {
    missing.push('invalid_shots')
  }
  if (specification.precision.value !== null
    && (!Number.isFinite(specification.precision.value) || specification.precision.value <= 0)) {
    missing.push('invalid_precision')
  }
  return missing
}

export function useNaturalLanguageExperiment(api: WorkspaceApi = pulsateApi) {
  const [question, setQuestionState] = useState('')
  const [interpretation, setInterpretation] = useState<InterpretationResponse | null>(null)
  const [reviewedSpecification, setReviewedSpecification] = useState<InterpretedScientificSpecification | null>(null)
  const [acceptedAssumptions, setAcceptedAssumptions] = useState(false)
  const [approvedExperiment, setApprovedExperiment] = useState<ApprovedExperimentResponse | null>(null)
  const [interpreting, setInterpreting] = useState(false)
  const [approving, setApproving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const controllerRef = useRef<AbortController | null>(null)

  useEffect(() => () => controllerRef.current?.abort(), [])

  const updateQuestion = useCallback((value: string) => {
    if (value !== question) {
      controllerRef.current?.abort()
      controllerRef.current = null
      setInterpretation(null)
      setReviewedSpecification(null)
      setApprovedExperiment(null)
      setAcceptedAssumptions(false)
      setError(null)
      setInterpreting(false)
      setApproving(false)
    }
    setQuestionState(value)
  }, [question])

  const updateReviewedSpecification = useCallback((
    value: InterpretedScientificSpecification,
  ) => {
    const next = structuredClone(value)
    rebuildClientAssumptions(next)
    setReviewedSpecification(next)
    setAcceptedAssumptions(false)
    setApprovedExperiment(null)
    setError(null)
  }, [])

  const interpretQuestion = useCallback(async () => {
    const normalized = question.trim()
    if (!normalized || interpreting) return
    controllerRef.current?.abort()
    const controller = new AbortController()
    controllerRef.current = controller
    setInterpreting(true)
    setError(null)
    setInterpretation(null)
    setReviewedSpecification(null)
    setApprovedExperiment(null)
    setAcceptedAssumptions(false)
    try {
      if (!api.interpretQuestion) throw new Error('Natural-language interpretation is unavailable.')
      const response = await api.interpretQuestion(normalized, controller.signal)
      if (controller.signal.aborted) return
      setInterpretation(response)
      setReviewedSpecification(structuredClone(response.specification))
    } catch (nextError) {
      if (!controller.signal.aborted) setError(messageFor(nextError))
    } finally {
      if (!controller.signal.aborted) setInterpreting(false)
    }
  }, [api, interpreting, question])

  const approve = useCallback(async () => {
    if (!interpretation || !reviewedSpecification || approving) return
    if (interpretation.original_question !== question.trim()) {
      setError('The visible question no longer matches this interpretation.')
      return
    }
    controllerRef.current?.abort()
    const controller = new AbortController()
    controllerRef.current = controller
    setApproving(true)
    setError(null)
    try {
      if (!api.approveInterpretation) throw new Error('Scientific approval is unavailable.')
      const response = await api.approveInterpretation(
        interpretation.interpretation_identifier,
        reviewedSpecification,
        acceptedAssumptions,
        controller.signal,
      )
      if (!controller.signal.aborted) setApprovedExperiment(response)
    } catch (nextError) {
      if (!controller.signal.aborted) setError(messageFor(nextError))
    } finally {
      if (!controller.signal.aborted) setApproving(false)
    }
  }, [acceptedAssumptions, api, approving, interpretation, question, reviewedSpecification])

  const unresolvedFields = useMemo(
    () => unresolvedScientificFields(reviewedSpecification),
    [reviewedSpecification],
  )
  const hasAssumptions = Boolean(reviewedSpecification?.assumptions.length)
  const approvalDisabled = !reviewedSpecification
    || unresolvedFields.length > 0
    || (hasAssumptions && !acceptedAssumptions)
    || interpreting
    || approving
    || approvedExperiment !== null
    || interpretation?.original_question !== question.trim()

  return {
    question,
    updateQuestion,
    interpretation,
    reviewedSpecification,
    updateReviewedSpecification,
    acceptedAssumptions,
    setAcceptedAssumptions,
    approvedExperiment,
    interpreting,
    approving,
    error,
    unresolvedFields,
    approvalDisabled,
    interpretQuestion,
    approve,
  }
}
