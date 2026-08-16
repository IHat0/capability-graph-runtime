import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, pulsateApi, type PulsateApi } from '../api/client'
import type {
  ResearchInputArtifactType,
  ResearchSessionResponse,
  ResearchVisualizationWorkspace,
} from '../api/types'
import { euclideanDistance } from '../scene/geometry'
import { normalizeScene } from '../scene/normalize'
import type { MolecularScene } from '../scene/types'

const visualizableMediaTypes = new Set([
  'chemical/x-pdb',
  'chemical/x-pdbqt',
  'chemical/x-mdl-molfile',
  'chemical/x-mdl-sdfile',
  'chemical/x-mol2',
])

function message(error: unknown): string {
  return error instanceof ApiError || error instanceof Error
    ? error.message
    : 'The research request could not be completed.'
}

export interface ResearchAttachment {
  identifier: string
  artifactType: ResearchInputArtifactType
  file: File
}

function applyVisualization(
  scene: MolecularScene,
  visualization: ResearchVisualizationWorkspace | null,
): MolecularScene {
  if (!visualization) return scene
  const atomsByEvidenceIdentity = new Map(
    scene.atoms.map((atom) => [
      `${atom.structureArtifactId ?? ''}\u001f${atom.sourceAtomId ?? atom.id}`,
      atom,
    ]),
  )
  const sceneStructures = new Set(
    scene.atoms.flatMap((atom) => atom.structureArtifactId ? [atom.structureArtifactId] : []),
  )
  const selections = visualization.selections.flatMap((selection) => {
    if (!selection.structure_artifact_identifier
      || !sceneStructures.has(selection.structure_artifact_identifier)) return []
    const atomIds = scene.atoms.filter((atom) => (
      atom.structureArtifactId === selection.structure_artifact_identifier
      && (selection.atom_identifiers.includes(atom.sourceAtomId ?? atom.id)
        || Boolean(atom.residueId && selection.residue_identifiers.includes(atom.residueId)))
    )).map((atom) => atom.id)
    if (atomIds.length === 0) return []
    return [{
      id: selection.selection_identifier,
      label: selection.label,
      atomIds,
      kind: selection.kind === 'binding_pocket' ? 'active-region' as const : 'selection' as const,
    }]
  })
  const interactionMeasurements = visualization.interactions.flatMap((interaction) => {
    if (interaction.structure_artifact_identifiers.length < 2 || interaction.atom_identifiers.length < 2) return []
    const left = atomsByEvidenceIdentity.get(`${interaction.structure_artifact_identifiers[0]}\u001f${interaction.atom_identifiers[0]}`)
    const right = atomsByEvidenceIdentity.get(`${interaction.structure_artifact_identifiers[1]}\u001f${interaction.atom_identifiers[1]}`)
    if (!left || !right) return []
    const geometricValue = euclideanDistance(left.position, right.position)
    return [{
      id: interaction.interaction_identifier,
      kind: 'distance' as const,
      atomIds: [left.id, right.id] as [string, string],
      unit: 'angstrom',
      declaredValue: interaction.distance_angstrom ?? undefined,
      backendDerivedValue: interaction.distance_angstrom ?? undefined,
      geometricValue,
      declaredDifference: interaction.distance_angstrom === null ? undefined : interaction.distance_angstrom - geometricValue,
      backendDifference: interaction.distance_angstrom === null ? undefined : interaction.distance_angstrom - geometricValue,
    }]
  })
  const interactionSelections = interactionMeasurements.map((measurement) => ({
    id: `selection-${measurement.id}`,
    label: visualization.interactions.find((item) => item.interaction_identifier === measurement.id)?.interaction_type.replaceAll('_', ' ') ?? 'Computed interaction',
    atomIds: [...measurement.atomIds],
    kind: 'interaction' as const,
  }))
  const overlayRegions = visualization.overlays.flatMap((overlay) => {
    const atomIds = scene.atoms.filter((atom) => (
      (!overlay.structure_artifact_identifier || atom.structureArtifactId === overlay.structure_artifact_identifier)
      && (overlay.atom_identifiers.includes(atom.sourceAtomId ?? atom.id)
        || Boolean(atom.residueId && overlay.residue_identifiers.includes(atom.residueId)))
    )).map((atom) => atom.id)
    if (atomIds.length === 0) return []
    return [{
      id: overlay.overlay_identifier,
      label: overlay.label,
      kind: overlay.kind === 'qm_region'
        ? 'quantum' as const
        : overlay.kind === 'binding_pocket'
          ? 'binding-pocket' as const
          : overlay.kind === 'active_region'
            ? 'active' as const
            : 'custom' as const,
      atomIds,
    }]
  })
  return {
    ...scene,
    selections: [...scene.selections, ...selections, ...interactionSelections],
    regions: [...scene.regions, ...overlayRegions],
    measurements: [...scene.measurements, ...interactionMeasurements],
  }
}

export function useResearchSession(api: PulsateApi = pulsateApi) {
  const [question, setQuestion] = useState('')
  const [reply, setReply] = useState('')
  const [session, setSession] = useState<ResearchSessionResponse | null>(null)
  const [attachments, setAttachments] = useState<ResearchAttachment[]>([])
  const [acceptIntentProposal, setAcceptIntentProposal] = useState(false)
  const [acceptRequirementProposal, setAcceptRequirementProposal] = useState(false)
  const [acceptEvidenceProposal, setAcceptEvidenceProposal] = useState(false)
  const [scene, setScene] = useState<MolecularScene | null>(null)
  const [visualization, setVisualization] = useState<ResearchVisualizationWorkspace | null>(null)
  const [selectedCandidateIdentifier, setSelectedCandidateIdentifier] = useState<string | null>(null)
  const [verifiedPropertiesOnly, setVerifiedPropertiesOnly] = useState(false)
  const [loadingSceneArtifact, setLoadingSceneArtifact] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const controller = useRef<AbortController | null>(null)
  const visualizationController = useRef<AbortController | null>(null)

  useEffect(() => () => {
    controller.current?.abort()
    visualizationController.current?.abort()
  }, [])

  useEffect(() => {
    visualizationController.current?.abort()
    if (!session) {
      setVisualization(null)
      return
    }
    const nextController = new AbortController()
    visualizationController.current = nextController
    void api.getResearchVisualization(session.session_identifier, nextController.signal)
      .then((workspace) => {
        if (!nextController.signal.aborted && workspace.revision === session.revision) {
          setVisualization(workspace)
        }
      })
      .catch((caught) => {
        if (!(caught instanceof DOMException && caught.name === 'AbortError')) setError(message(caught))
      })
    return () => nextController.abort()
  }, [api, session])

  const beginRequest = useCallback(() => {
    controller.current?.abort()
    controller.current = new AbortController()
    setBusy(true)
    setError(null)
    return controller.current.signal
  }, [])

  const upload = useCallback(async (signal: AbortSignal, startingPosition: number) => {
    const uploaded = []
    for (const [position, attachment] of attachments.entries()) {
      uploaded.push(await api.uploadResearchInput(
        { artifactType: attachment.artifactType, file: attachment.file },
        startingPosition + position,
        signal,
      ))
    }
    return {
      inputs: uploaded.map((item) => item.inputReference),
      artifacts: uploaded.map((item) => item.artifactReference),
    }
  }, [api, attachments])

  const start = useCallback(async () => {
    const trimmed = question.trim()
    if (!trimmed || busy) return
    const signal = beginRequest()
    try {
      const evidence = await upload(signal, 1)
      const created = await api.createResearchSession(trimmed, evidence.inputs, evidence.artifacts, signal)
      setSession(created)
      setQuestion('')
      setAttachments([])
      setScene(null)
    } catch (caught) {
      if (!(caught instanceof DOMException && caught.name === 'AbortError')) setError(message(caught))
    } finally {
      setBusy(false)
    }
  }, [api, beginRequest, busy, question, upload])

  const respond = useCallback(async () => {
    const trimmed = reply.trim()
    const approvalOnly = Boolean(
      (session?.status === 'awaiting_approval' && acceptEvidenceProposal)
      || Boolean(session?.requirement_proposal && acceptRequirementProposal)
      || Boolean(
        session?.evidence_proposal
        && session.evidence_proposal.source_kind !== 'entity_resolution_candidates'
        && acceptEvidenceProposal,
      )
    )
    if (!session || (!trimmed && !approvalOnly) || busy) return
    const signal = beginRequest()
    try {
      const evidence = await upload(signal, session.input_references.length + 1)
      const updated = await api.replyResearchSession(
        session.session_identifier,
        trimmed || 'I confirm the reviewed evidence.',
        acceptIntentProposal,
        acceptRequirementProposal,
        acceptEvidenceProposal,
        acceptEvidenceProposal,
        evidence.inputs,
        evidence.artifacts,
        signal,
      )
      setSession(updated)
      setReply('')
      setAttachments([])
      setAcceptIntentProposal(false)
      setAcceptRequirementProposal(false)
      setAcceptEvidenceProposal(false)
    } catch (caught) {
      if (!(caught instanceof DOMException && caught.name === 'AbortError')) setError(message(caught))
    } finally {
      setBusy(false)
    }
  }, [acceptEvidenceProposal, acceptIntentProposal, acceptRequirementProposal, api, beginRequest, busy, reply, session, upload])

  const execute = useCallback(async () => {
    if (!session || busy) return
    const signal = beginRequest()
    try {
      setSession(await api.executeResearchSession(session.session_identifier, signal))
    } catch (caught) {
      if (!(caught instanceof DOMException && caught.name === 'AbortError')) setError(message(caught))
    } finally {
      setBusy(false)
    }
  }, [api, beginRequest, busy, session])

  const openScene = useCallback(async (artifactIdentifier: string, secondaryArtifactIdentifier?: string) => {
    if (!session || busy) return
    const signal = beginRequest()
    setLoadingSceneArtifact(artifactIdentifier)
    try {
      const response = secondaryArtifactIdentifier
        ? await api.getResearchComplexScene(
          session.session_identifier,
          artifactIdentifier,
          secondaryArtifactIdentifier,
          signal,
        )
        : await api.getResearchScene(session.session_identifier, artifactIdentifier, signal)
      setScene(applyVisualization(normalizeScene(response), visualization))
    } catch (caught) {
      if (!(caught instanceof DOMException && caught.name === 'AbortError')) setError(message(caught))
    } finally {
      setLoadingSceneArtifact(null)
      setBusy(false)
    }
  }, [api, beginRequest, busy, session, visualization])

  const openConformation = useCallback(async (artifactIdentifier: string, conformationIndex: number) => {
    if (!session || busy) return
    const signal = beginRequest()
    setLoadingSceneArtifact(artifactIdentifier)
    try {
      const response = await api.getResearchConformationScene(
        session.session_identifier,
        artifactIdentifier,
        conformationIndex,
        signal,
      )
      setScene(applyVisualization(normalizeScene(response), visualization))
    } catch (caught) {
      if (!(caught instanceof DOMException && caught.name === 'AbortError')) setError(message(caught))
    } finally {
      setLoadingSceneArtifact(null)
      setBusy(false)
    }
  }, [api, beginRequest, busy, session, visualization])

  const focusCandidate = useCallback(async (candidateIdentifier: string) => {
    if (!visualization) return
    const candidate = visualization.candidates.find((item) => item.candidate_identifier === candidateIdentifier)
    const candidateStructure = visualization.structures.find((item) => (
      item.candidate_identifier === candidateIdentifier
      && item.artifact_type.includes('docking_poses')
    )) ?? visualization.structures.find((item) => item.candidate_identifier === candidateIdentifier)
    const protein = visualization.structures.find((item) => item.role === 'protein')
    if (!candidate || !candidateStructure) return
    setSelectedCandidateIdentifier(candidateIdentifier)
    await openScene(
      protein?.artifact_identifier ?? candidateStructure.artifact_identifier,
      protein ? candidateStructure.artifact_identifier : undefined,
    )
  }, [openScene, visualization])

  const compareCandidates = useCallback(async (leftIdentifier: string, rightIdentifier: string) => {
    if (!visualization) return
    const left = visualization.structures.find((item) => (
      item.candidate_identifier === leftIdentifier || item.artifact_identifier === leftIdentifier
    ))
    const right = visualization.structures.find((item) => (
      item.candidate_identifier === rightIdentifier || item.artifact_identifier === rightIdentifier
    ))
    if (!left || !right) return
    setSelectedCandidateIdentifier(right.candidate_identifier)
    await openScene(left.artifact_identifier, right.artifact_identifier)
  }, [openScene, visualization])

  const exportArtifact = useCallback(async (artifactIdentifier: string) => {
    if (!session) return
    const reference = visualization?.export_items.find((item) => item.artifact_identifier === artifactIdentifier)
      ?? session.artifact_references.find((item) => item.artifact_identifier === artifactIdentifier)
    if (!reference) return
    const signal = beginRequest()
    try {
      const resource = await api.downloadResearchArtifact(
        session.session_identifier,
        artifactIdentifier,
        reference.content_sha256,
        reference.media_type,
        signal,
      )
      const blob = new Blob([resource.bytes], { type: resource.mediaType })
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = artifactIdentifier
      anchor.click()
      URL.revokeObjectURL(url)
    } catch (caught) {
      if (!(caught instanceof DOMException && caught.name === 'AbortError')) setError(message(caught))
    } finally {
      setBusy(false)
    }
  }, [api, beginRequest, session, visualization])

  const exportVisualization = useCallback(() => {
    if (!visualization) return
    const blob = new Blob([JSON.stringify(visualization, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `${visualization.session_identifier}-visualization.json`
    anchor.click()
    URL.revokeObjectURL(url)
  }, [visualization])

  const addAttachments = useCallback((files: FileList | null, artifactType: ResearchInputArtifactType) => {
    if (!files) return
    const next = Array.from(files).map((file, position): ResearchAttachment => ({
      identifier: `${file.name}-${file.size}-${file.lastModified}-${artifactType}-${position}`,
      artifactType,
      file,
    }))
    setAttachments((current) => [...current, ...next].slice(0, 64))
  }, [])

  const removeAttachment = useCallback((identifier: string) => {
    setAttachments((current) => current.filter((item) => item.identifier !== identifier))
  }, [])

  const newSession = useCallback(() => {
    controller.current?.abort()
    setQuestion('')
    setReply('')
    setSession(null)
    setAttachments([])
    setAcceptIntentProposal(false)
    setAcceptRequirementProposal(false)
    setAcceptEvidenceProposal(false)
    setScene(null)
    setVisualization(null)
    setSelectedCandidateIdentifier(null)
    setVerifiedPropertiesOnly(false)
    setError(null)
    setBusy(false)
  }, [])

  const visualizableArtifacts = session?.artifact_references.filter(
    (artifact) => visualizableMediaTypes.has(artifact.media_type),
  ) ?? []

  return {
    question, setQuestion, reply, setReply, session, attachments, scene, visualization, busy, error,
    acceptIntentProposal, setAcceptIntentProposal, loadingSceneArtifact,
    acceptRequirementProposal, setAcceptRequirementProposal,
    acceptEvidenceProposal, setAcceptEvidenceProposal,
    selectedCandidateIdentifier, verifiedPropertiesOnly, setVerifiedPropertiesOnly,
    visualizableArtifacts, start, respond, execute, openScene, openConformation, focusCandidate, compareCandidates,
    exportArtifact, exportVisualization, addAttachments,
    removeAttachment, newSession,
  }
}

export type ResearchSessionWorkspace = ReturnType<typeof useResearchSession>
