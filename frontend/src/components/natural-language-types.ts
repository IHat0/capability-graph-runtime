import type { Dispatch, SetStateAction } from 'react'
import type {
  ApprovedExperimentResponse,
  InterpretationResponse,
  InterpretedScientificSpecification,
} from '../api/types'

export interface ReturnTypeOfNaturalLanguageHook {
  question: string
  updateQuestion: (value: string) => void
  interpretation: InterpretationResponse | null
  reviewedSpecification: InterpretedScientificSpecification | null
  updateReviewedSpecification: (value: InterpretedScientificSpecification) => void
  acceptedAssumptions: boolean
  setAcceptedAssumptions: Dispatch<SetStateAction<boolean>>
  approvedExperiment: ApprovedExperimentResponse | null
  interpreting: boolean
  approving: boolean
  error: string | null
  unresolvedFields: string[]
  approvalDisabled: boolean
  interpretQuestion: () => Promise<void>
  approve: () => Promise<void>
}
