# Pulsate Scientific Foundation v1

## Runtime Boundary

CGR remains the runtime foundation for Pulsate Labs. Its existing coding repair
path is the first proven capability, not a discarded prototype. This foundation
is additive: it does not replace the coding runtime, SWE-agent integration,
phase gate, or QuixBugs workflow.

The `cgr.science` package introduces immutable contracts for scientific
experiments, artifacts, lineage, capability invocations, verification,
workflow transitions, molecular structure metadata, and molecular scenes. It
reuses CGR's semantic `CapabilityVersion`, `ExecutionContext`, and
`ExecutionStatus` contracts while keeping scientific identity independent of
operational timestamps.

## Artifact Identity And Lineage

Scientific data is referenced through content-addressed `ArtifactReference`
objects. References contain SHA-256 identities and bounded metadata, never raw
file contents. Canonical UTF-8 JSON provides deterministic fingerprints;
semantically unordered collections are normalized before serialization.
Secrets and local absolute paths are prohibited from portable artifact
metadata.

Every transformation creates an `ArtifactLineageEdge` between exact artifact
identities. A lineage edge records the producing capability and version, and
may include execution and verification evidence. The in-memory lineage graph
rejects self-reference and duplicate edges. This version intentionally does
not introduce a graph database or artifact storage service.

## Experiments And Authorization

A `ScientificExperiment` distinguishes the user's objective and inputs from
derived information, explicit assumptions, and unresolved information.
Blocking assumptions remain visible until explicitly approved. Capability
results cannot report success while containing failed blocking verification.

The generic workflow contracts describe phases, allowed transitions, required
artifacts, and required verifiers. Pure transition validation rejects unknown
or illegal transitions, advancement from terminal phases, missing declared
outputs, and completion with failed blocking verification. These contracts are
future-facing and do not replace the coding-specific SWE-agent phase gate.

## Molecular Visual Invariant

`MolecularStructure` records explicit structure format, coordinate units, and
known metadata without parsing molecular files or inventing absent charge or
spin values. `MolecularScene` records representations, structural selections,
quantum-region highlighting, measurements, labels, and optional camera state.

The central visual invariant is exact identity: every scene element must refer
to one of the fingerprinted molecular structure artifacts declared by the
scene. The future viewer must therefore display the same structure bytes used
by computation. Changing the structure, selected quantum region, measurement,
or scientifically meaningful representation state changes the scene
fingerprint.

## Deliberate Deferrals

This foundation does not add Qiskit, RDKit, PySCF, OpenMM, Mol*, Three.js, IBM
Quantum execution, molecular parsing, chemistry algorithms, or frontend
rendering. It makes no claim of quantum advantage and no claim of exact
protein-ligand simulation.

The next implementation milestone is a visual molecular workspace that
consumes these exact structure and scene contracts. Computation and execution
capabilities can be added later behind the same artifact, lineage,
verification, and authorization boundaries.
