# Demo 1: accepted classical construction and automatic interactive 3D

## Current revised acceptance — this supersedes the historical report below

Exact real browser query: **Create caffeine.** No uploads or public 3D coordinates.
Session: `research-session-db4acb2f8e76a4c9fc89daf43169c6c3`.
All eight selected execution steps completed and blocking verification passed.
The quantum path was **not selected**, because classical methods were sufficient
for this structural construction and ordinary electronic-reference task. This is
a valid scientific decision, not a pending capability or a Demo 1 failure.

Current evidence directory:

`.test-runs/demo1-classical-browser-acceptance-20260930/molecular-construction-liv-d220e-uage-molecular-construction-chromium/`

It contains `initial-session.json`, `result.json`, `visualization.json`,
`artifact-manifest.json`, 17 downloaded scientific artifacts, working browser
downloads `generated-molecule.sdf` / `generated-molecule.xyz`, and independently
checked `independent-validation.json` / `independent-render.png`.
`structure-3d.png` and `saved-structure-and-results.png` show actual Mol* rendering
automatically on completion and after page reload, with no viewer-button click.

Exact successful capability sequence:

1. `molecular.identity_construct`: identity properties only; graph construction;
   hydrogens/formal charge; eight ETKDGv3 conformers; MMFF94s optimization;
   lowest finite converged sampled energy, with an audited deterministic tie-break.
2. `electronic.configuration_define`: disclosed gas-phase STO-3G/RHF controls.
3. `electronic.hartree_fock`: full-molecule PySCF calculation.
4. `electronic.computation_select`: persisted classical-sufficiency decision.
5. `molecular.identity_verify`: fresh InChI reference and independent graph/SDF
   comparison, after electronic analysis; no reference coordinates acquired.
6. `scientific_verification.molecular_construction`: blocking identity, generated
   geometry, convergence, molecule/configuration/electron identity, energy-component
   and method-selection checks; persisted execution receipt.
7. `molecular.scene_project`: persisted scene evidence.
8. `scientist.result_assemble`: grounded scientist-facing answer.

The UI displays: identity resolution → molecular construction → 3D conformer
generation → geometry optimization → classical electronic analysis → independent
identity verification. The actual generated molecule appears prominently beside
name, formula, atom count, optimization results, computed energies, classical
decision and download controls.

Computed results: C8H10N4O2; 24 atoms including hydrogens; 25 bonds; charge 0;
eight generated/eight converged conformers; selected MMFF94s energy
**−123.493727138 kcal/mol**; RHF/STO-3G total **−667.719183656 Hartree**, converged
in 14 iterations, with 102 electrons and 80 spatial orbitals. These are different
observables and are not combined. Minimal-basis gas-phase RHF is not a high-accuracy
correlated-energy prediction. Construction is computational, not physical synthesis
or a new molecular identity, and eight sampled minima do not prove a global minimum.

Independent exported-evidence checks passed: all SHA-256 identities; graph/formula,
charge, connectivity, bond orders, stereochemical identity, InChIKey, finite geometry,
and noncollapsed bonds. V2000 serialization differs from retained generated coordinates
by at most `4.96011e-5` angstrom; production XYZ agrees within `1e-12` angstrom.
Both public payloads contain properties only. Independent graph reconstruction uses
a fresh retrieval and different representation from the same database, not independent
database curation or experimental validation.

General policy: `structural_construction_classical_sufficiency_v1` uses the requested
structural task and actual converged closed-shell reference, not an entity name or
quantum availability. It records the orbital gap and explicitly notes that RHF
convergence/integer occupations do not prove absence of strong correlation. Invalid
references do not manufacture a sufficient result. Explicit higher-level electronic
requests retain existing validated active-space, mapper, VQE and IBM authorization
paths. No active space, VQE, hardware proposal or IBM job belongs to this Demo 1 run.
Qiskit/IBM infrastructure remains preserved.

Viewer repairs reuse the existing scene/Mol* path: automatic completion/reopen loading;
element-preserving MOL2 atom names (A1/A2 previously erased parsed elements);
ball-and-stick with element colors; no dense generated-atom or unsolicited bond-distance
labels; camera framing after canvas commit; readiness only after an actual rendered
frame. The last repair prevents the initially blank saved-session screenshot race.

Validation: final product regressions 298 passed; focused
construction/requirements/visualization suite 34 passed;
frontend 180 passed across 22 files and build passed; numerical PySCF/Qiskit regressions
31 passed/one skipped (infrastructure tests, not Demo 1 workflow; no IBM submission).
Exact browser acceptance and independent exported-evidence validation passed. The
final browser acceptance explicitly exercised drag rotation and working SDF/XYZ
download buttons, asserted zero uploaded inputs and no quantum capabilities, and
checked automatic saved-session rendering. All 12 existing real browser acquisition,
target-selection, docking and conversational candidate regressions also passed.

Changes remain uncommitted/unpushed on `feat/pulsate-scientific-autonomy-v1`, HEAD
`5356d1e4bffdf6472ed8369699d35894b5e004f4`. Only the approved isolated API on
loopback 8012 was restarted; production services, unrelated files and prior evidence
were preserved. Torcetrapib and Demo 3 were not started.

## Historical superseded quantum-benchmark attempt — retained for audit only

The following describes the former acceptance target, NOT the current Demo 1 workflow.

Exact scientist query: **Create caffeine.** No structures, quantum instructions,
or numerical target values were supplied. Real Qwen interpretation and real
RDKit, PySCF, and Qiskit calculations completed through the browser.

The local workflow is demonstrated. **Final Demo 1 acceptance is still pending:
no IBM hardware job has run, and no backend-specific submission is ready for
approval.** The saved hardware artifact is a validated logical proposal.

## Reproducible evidence

Final ResearchSession: `research-session-dd18a210af5cde534b9553f3d4f00fb8`.
The session completed all 14 steps, passed blocking verification, and reopened
with its generated structure. The browser downloaded 27 scientific artifacts
and checked each SHA-256 against the server's stored identity.

Final evidence directory, relative to the repository:

`.test-runs/molecular-construction-final-20260930/molecular-construction-liv-d220e-uage-molecular-construction-chromium/`

Important files there:

- `result.json`: actual complete session and scientist-facing answer.
- `execution-record.json`: full artifact references, parentage, provenance, and canonical workflow.
- `artifact-manifest.json` and `visualization.json`: exported identities and hashes.
- `generated-molecule.sdf`: computationally constructed graph and coordinates.
- `generated-coordinates.xyz`: all 24 generated Cartesian coordinates at retained precision.
- `independent-validation.json` and `independent-render.png`: separate SDF checks and rendering.
- `artifacts/`: identity specifications, eight-conformer record, full RHF, active
  space, Hamiltonians, VQE trace, exact result, verification, QPY, and logical hardware proposal.

The compact table beside the reopened 3D molecule was captured separately in:

`.test-runs/molecular-construction-saved-view-20260930/molecular-construction-live-saved-constructed-molecule-view-chromium/`

## Actual Pulsate answer

The principal result returned by Pulsate was:

> Constructed caffeine (C8H10N4O2) with 24 atoms including hydrogens. Generated 8 geometries; 8 converged. Selected force-field energy: -123.493727 kcal/mol (mmff94s). Fresh post-construction identity verification passed: True. No public 3D coordinates were used.

> Full-molecule RHF total energy: -667.719183656 Hartree. Reduced active-space exact total: -667.724258453 Hartree; statevector VQE total: -667.724258452 Hartree. VQE–exact difference on the same Hamiltonian: 3.49e-10 Hartree. These totals include inactive-orbital and nuclear contributions; the force-field energy is a different observable. The reduced quantum problem is not the entire molecule. No IBM hardware job has run.

The interface displays the structure, calculated quantities, verification and
workflow directly. Extended explanation and assumptions are available in an
expandable section, with the complete evidence available for download.

## Construction and independent validation

Before any source repair, the unchanged browser query returned clarification
about the primary objective. The existing planner had no operation for
constructing a known molecular identity; candidate generation meant generating
different identities. This baseline is retained under
`.test-runs/molecular-construction-baseline-connected-20260930/`.

The repaired interpreter proposes `construct_molecule`, which deterministic CGR
validation composes into the existing numerical pipeline. There is no production
branch for the acceptance entity or wording.

Construction retrieved only PubChem identity properties: CID 2519, charge zero,
and SMILES `CN1C=NC2=C1C(=O)N(C(=O)N2C)C`. It did not request SDF or conformer
coordinates. RDKit parsed the graph, added hydrogens, generated eight ETKDGv3
conformers with seed 29, optimized them with MMFF94s, and selected the lowest
converged sampled force-field energy. All eight geometries are retained.

After construction, a fresh request retrieved InChI, InChIKey, formula and charge
for that record. A new graph was reconstructed from InChI and compared with an
independent read of the generated SDF. Formula, charge, atom count, connectivity,
bond orders, stereochemical identity, finite coordinates and noncollapsed bonds
passed. The reference supplied no 3D coordinates.

An additional offline RDKit SDF read and Matplotlib 3D render passed, verifying
24 atoms, 25 bonds and InChIKey `RYYVLZVUVIJVGH-UHFFFAOYSA-N`. SDF serialization
agreed with retained generated coordinates within `4.96011e-5` angstrom,
consistent with V2000's four decimal places.

Validation is independent of construction in timing, representation and graph
reconstruction. Both identity lookups use PubChem; this is not independent
database curation or experimental validation.

## Actual composed computation

Qwen interpretation → identity-only acquisition → RDKit graph/hydrogen
preparation → conformer generation/optimization → identity verification → PySCF
configuration → full RHF → frontier selection → active-space construction →
fermionic Hamiltonian → Jordan–Wigner mapping → exact diagonalization and UCCSD
VQE → blocking verification and logical hardware proposal → scene projection →
scientist-facing result.

Full-molecule reference: neutral closed-shell singlet, gas phase, STO-3G RHF,
102 electrons and 80 spatial orbitals. RHF converged in 14 iterations.

Pulsate applies a disclosed **bounded frontier-correlation benchmark policy**.
It checks the actual converged closed-shell RHF occupations before allowing the
quantum path, then selects canonical orbitals 50 and 51 (zero-based HOMO/LUMO).
The computed gap was approximately `0.451710` Hartree. Two electrons and two
spatial orbitals form the active space; 50 occupied orbitals remain inactive.
The selection evidence retains this rationale and the actual orbital indices.

This is automatic selection under a declared workflow policy, not an inference
that quantum computing is necessary to construct the molecule or provides an
advantage. Only the reduced four-qubit problem undergoes VQE.

| Computed quantity | Result | Units |
| --- | ---: | --- |
| Selected MMFF94s energy | -123.493727 | kcal/mol |
| Full RHF total | -667.719183656 | Hartree |
| Exact CAS(2e,2o) total | -667.724258453 | Hartree |
| UCCSD statevector VQE total | -667.724258452 | Hartree |
| VQE–exact absolute difference | 3.49e-10 | Hartree |
| Nuclear contribution | +931.290966837 | Hartree |
| Combined inactive electronic contribution | -1598.259807772 | Hartree |
| Constant added to active-space energy | -666.968840934 | Hartree |

VQE used SLSQP, an exact-statevector estimator, a `1e-9` convergence threshold,
and a maximum of 300 iterations. It converged after five recorded optimizer
evaluations. Mapping used four Jordan–Wigner qubits without symmetry reduction.
Exact comparison used the same active Hamiltonian and particle sector.

Numerical environment: RDKit 2026.3.4, PySCF 2.13.1, Qiskit 2.3.1,
Qiskit Nature 0.8.0, SciPy 1.16.3, NumPy 2.2.6. The actual model was
Qwen/Qwen2.5-Coder-7B-Instruct.

## Logical hardware proposal and remaining blockers

Pulsate reconstructed the optimized UCCSD circuit from its persisted
Hamiltonian and optimized parameters, checked input lineage, transpiled a
portable logical circuit, serialized it as QPY, loaded it again and recomputed
its ideal expectation. That serialization reproduced the verified VQE total
with zero reported difference at the stored precision.

The current proposal is a **single IBM Runtime EstimatorV2 expectation of the
classically optimized parameters**, not hardware VQE optimization:

- Four logical qubits; three optimized parameters; 27 Pauli terms.
- Logical depth 92; 81 RZ, 53 SX, 45 CX and two X gates in the final artifact.
- Nine qubit-wise commuting measurement groups excluding the identity term.
- Draft grouping estimate: 4,096 shots/group, 36,864 shots total.
- Draft target precision: 0.015 Hartree; maximum execution duration: 1,800 seconds.
- The precision and shot estimates are not an accuracy guarantee. Actual
  Runtime usage, resilience overhead and ISA resources require backend preparation.

The draft precision is larger than the approximately 0.0051-Hartree RHF-to-CAS
correction. It must not be used to claim that hardware resolved that correction.
A tighter precision/resource decision is required if that is the hardware goal.

No IBM token, instance or backend is configured in the validation runtime, and
its `qiskit_ibm_runtime` dependency is absent. Local IBM Runtime 0.47.0 is
installed, but the SDK reports zero saved accounts and the relevant local
environment credentials are unset. No credentials were printed or uploaded.

Consequently, the following are **not yet demonstrated**:

- Actual IBM backend selection/calibration and backend-specific ISA transpilation.
- Layout-adjusted observable validation and exact provider resource estimate.
- Approval-bound integration of this new artifact workload with the IBM executor.
- A real hardware result, uncertainty, job receipt or saved hardware verification.

The existing IBM worker reconstructs an experiment-manifest workload; this
construction profile currently exports the verified logical proposal instead.
It must not be represented as an already executable IBM submission bundle.
Configure the IBM account/instance and backend securely, complete backend
preparation and executor handoff, and obtain explicit approval before a job.

Selected final hashes:

| Artifact | SHA-256 |
| --- | --- |
| Generated SDF | `818b0ef664437fb060bf24a125003f76eaefc8725a846e55b88faf76cfff5259` |
| Mapped Hamiltonian | `5072c5fe77beb16d5d4267fd458b5b0d012994564aaf53e7322fa92d4fd6ea78` |
| Logical QPY | `8535526ce7a6685d64a363466a6bb5b090b7af15ddd3ada7a3e80415a61ae277` |

## Regressions and repository state

- 58 focused construction, planner, session, verification and viewer tests passed.
- 275 existing product regression tests passed.
- 176 frontend tests passed; frontend production build passed.
- 31 PySCF/Qiskit regression tests passed; one test was skipped.
- Final exact-query browser acceptance and saved-session visual check passed.
- All 12 earlier real browser scenarios passed across the initial run and
  unchanged retries. Two initial failures are preserved: one acquisition request
  exceeded the 240-second test response timeout; one same-session lookup returned
  safe acquisition clarification. Both passed without implementation/test changes
  on retry. This does not establish that external-source latency is eliminated.
- Independent exported-structure validation/rendering passed.
- Production diff/new production modules contained no acceptance name, formula,
  CID, InChIKey, query literal, expected energy or expected-coordinate branch.
- `git diff --check` passed.

Changed product files: `phase8_scientific_handlers.py`, `research_sessions.py`,
`research_scene.py`, `research_visualization.py`, `scientific_capability_catalogue.py`,
`scientific_objectives.py`, `scientific_production.py`, `scientific_requirements.py`,
`scientific_runtime.py`, and the new `scientific_construction.py` and
`scientific_quantum_proposal.py`, all under `src/cgr/pulsate_api/`.

Frontend changes: `frontend/src/api/client.ts`, `frontend/src/api/types.ts`,
`frontend/src/components/ResearchWorkspace.tsx`, and new
`frontend/e2e/molecular-construction-live.spec.ts`.

Tests/report: new `tests/test_molecular_construction.py`, new offline
`tests/validate_constructed_structure.py`, extended
`tests/test_research_visualization.py`, and this report.

All Demo 1 development remains uncommitted and unpushed on
`feat/pulsate-scientific-autonomy-v1`; HEAD remains
`5356d1e4bffdf6472ed8369699d35894b5e004f4`.
Unrelated `pyproject.toml`, `src/cgr/swebench/integration.py`, temporary files and
earlier evidence were preserved. Only the isolated API on port 8012 was updated.
Production services were not changed, and no IBM jobs were submitted.
