# Demo 2 v4 — development validation checkpoint

This is **not the final held-out candidate result**. No v4 held-out investigation
or candidate target-activity prediction has been performed. The sponsor dossier,
candidate-transfer library, production source and UI have not received their final
joint freeze. Do not begin the one final investigation from this checkpoint alone.

Branch: `feat/pulsate-scientific-autonomy-v1`.
Committed baseline remains `ebdeaba9733379b61e8879cf004c633377c2f2eb`.
Development changes remain uncommitted and unpushed. Previous experiments are not
relabelled as v4 or overwritten.

## Real unrelated browser investigation

Scientist query:

> Investigate nilotinib as a human ABL1 kinase-domain drug candidate using the
> experimental structure PDB 2HYY, chain B.

Real ResearchSession: `research-session-b28315d24cd9c7572dbb8777a62204a5`.
The isolated API used real Qwen, public structure acquisition, RDKit/Meeko/Vina,
blocking verification, the general safety panel, scientist-facing evidence and
the existing 3D viewer. Intended-target Vina score was **-10.439 kcal/mol**; it is
not affinity, potency, efficacy or safety.

All 41 general-panel members were retained: 29 refused estimates and 12 unsupported
models. No numerical binding estimate was forced for this compound. The final
research status was **INSUFFICIENT EVIDENCE**, not a favourable safety finding.
This run had no sponsor ADME regimen or native Human PBPK/QSP claim.

The initial export check encountered an SSH connection reset. The isolated API
had returned the evidence successfully. Restoring transport and reopening the
same saved session required no scientific rerun. The read-only browser check
passed; **85 artifacts** were exported and their exact SHA-256 values checked.
The saved session reopened with its actual 3D scene.

Evidence directory:
`.test-runs/demo2-v4-development-20261005/browser-unrelated-reopen-r2/virtual-investigation-live-c93c5-investigation-and-reopening-chromium/`

Frozen unrelated-result SHA-256:
`66ee08fa9fc01da66ea9f23c1cb7fbd1cd8d448ebfdd1a2e634f7e054fb2ee2e`.
Original source-validation archive:
`ca6e83d8bfbb7a2ca262751b40b3deec8ed9252c3e96a1a3509992a760c684de`.

## General quantitative binding engine

Panel selection precedes candidate evaluation and follows the explicit membership
table of the general ProfhEX source, not any candidate's later outcome. The article's
model count and explicit table count differ; the panel retains the 41 explicitly
enumerated unique Human targets, rather than inventing the missing memberships.

The data acquisition firewall excluded the challenge connectivity before fetching
numerical assay labels. Only direct Human, exact target-assay binding **Ki** records
were used. Pooled Ki/Kd/IC50/EC50 labels were not treated as equivalent potency.

Forty datasets completed. One target dataset remained unsupported after a network
failure, rather than fitting its partial data. Twenty-nine models met the minimum
split sizes; **22 passed** the predeclared internal research gate, seven failed it,
and twelve panel members had no eligible deployed model. Failed gates were not
weakened or tuned using the challenge.

Fresh runtime verification replayed the frozen models against **6,961** internal
scaffold-held-out assay rows, checked preserved source hashes and exercised an
unrelated held-out graph. That graph received one accepted binding estimate and
40 refused/unsupported estimates. This is internal numerical/model validation,
not an external biological validation experiment.

Manifest SHA-256:
`20896372e6f536c311d83384245c42a375ef94d37ba3fc8bd3ed34b5eea9b49d`.
Internal numerical replay receipt:
`ee17d341d2d6c7c91ffd1ade3e62cd8c9bf899007551dd0e5cb632b5f3454ea9`.

Predictions remain **nominal binding-assay quantities**. They are not measured
candidate potency, functional inhibition, a free tissue concentration, occupancy,
or a physiological effect. The runtime refuses unsupported exposure/activity and
candidate-to-QSP transfers instead of aliasing Ki to functional IC50.

## General native Human model inspection and numerical controls

Model source: the unit-corrected Human epicardial ventricular CellML model in
[Physiome changeset de5a4e600b57c8b9b0bd477695648c82351bc6dd](https://models.physiomeproject.org/workspace/604/rawfile/de5a4e600b57c8b9b0bd477695648c82351bc6dd/ten_tusscher_model_2006_epi.cellml).
It was selected for general cardiac electrophysiology before candidate execution.
No candidate-specific history, activity or proposed mechanism entered this check.

Source SHA-256:
`d78f3b39cc2409bde44dc5fca27fc78a4aa2910bb3201a00853c9deed36599f2`.
[libCellML](https://libcellml.org/) 0.7.1 produced zero parser, validator and analyser
errors. Its CellML 1.0-to-2.0 representation and ignored documentation/RDF messages
are retained separately. Native analysis found 19 states, 53 constants and 73
algebraic variables. Equations, initial values and native units were not handwritten.

The downloaded Windows wheel's native binaries were ARM64 despite its x64 wheel
label; the local x64 import correctly failed. The compiler was installed only in
the approved isolated Linux model-inspection directory. Its generated equations
were preserved and executed locally with SciPy, without altering global runtimes.

Generated-equation SHA-256:
`94dc085a92ae9169a69fef8cf7f0682dc7e9ed5b51adc91b74cfe8546d1186f7`.

Four general controls ran: native baseline and explicit 50% reductions of each
of three native parameters. These reductions are numerical validation inputs,
**not drug inhibition estimates**. Both BDF and Radau solved every control,
preserving millisecond/millivolt and concentration units. The largest cross-solver
voltage difference was **0.000023877 mV**, below the predeclared 0.25 mV numerical
agreement threshold. All sampled states were finite; concentrations stayed positive.

Numerical receipt:
`.test-runs/demo2-v4-development-20261005/human-native-cellml-r1/numerical-controls-r1/receipt.json`
SHA-256:
`16fbb19fa1d823f0c4cc6fcaacdb3dab9db1bc3201e9bc34d84cc0b4d92322a6`.

This verifies a first paced beat and software/numerical reproducibility. It does
not establish steady-state electrophysiology, arrhythmia prediction, independent
biological validation, licensing qualification, production CellML integration or
a valid candidate-to-parameter transfer. The existing SBML adapter's validation
was not weakened to accept a different format.

The separately inspected general Human growth-factor SBML model remains refused
for missing unit declarations/dimensional qualification. Prior coagulation source
and transfer limitations likewise remain visible; source existence is not integration
qualification.

## Native Human PBPK and ADME checks — separate evidence levels

The previous v4 native sponsor-interface test executed real PK-Sim numerical runs
for two explicitly **synthetic** conditional ADME cases and two virtual Humans,
with 98 native series per run and fresh numerical replays. All 36 retained artifact
hashes passed; repeated native concentration outputs matched exactly. These are
native interface/reproducibility checks, not actual candidate ADME or accuracy.

Receipt SHA-256:
`c4db581f9455720db5bcd1d81e99dcfa314b348ef940e973bf3ef9c79fe6aab3`.

Native organ extraction and endpoint checks retained person, species, native
path and concentration basis. Independent trapezoidal-AUC verification covered
196 endpoint rows and 184 supported ratios. No adrenal output was invented when
the selected native model did not contain it.

The existing Human-fu model received an additional actual held-out replay after
the shared prediction kernel change: 275 rows, 173 in-domain. This does not validate
logD as logP, heterogeneous solubility as a known-pH quantity, intrinsic clearance
as plasma organ clearance, Caco-2 as native permeability, or formal charge as pKa.

## Generic input-routing repair

A positive unrelated browser test revealed that an explicit SMILES was already
constructed correctly, but the model's later names array included the same graph.
Name acquisition then tried searching that graph as a compound name and asked an
unnecessary clarification. The repair excludes only exact labelled structure-source
values from the names array. It does not guess structure syntax from unlabelled
names, skip other supplied compounds, or add a molecule-specific route.

SMILES, charged SMILES, InChI, PubChem CID, PDB and unlabelled-name regression
fixtures are synthetic parser tests, not scientific benchmarks. Acquisition,
ResearchSession and exact-source regression suite: **44 passed**.

Only our newly created isolated API8019 was refreshed. Its saved runtime and frozen
models were preserved. Earlier APIs8012/8018, production and IBM jobs were untouched.
Updated source-validation archive SHA-256:
`281bb976a4c378a6e83f4089c094ed8cbbdd9f90159bbf0ee22c024b798aaa4e`.

The unchanged positive request then completed in real browser session
`research-session-71940d248d4bfbf807cb6a2688fdc6b9`. It supplied the previously
selected internally held-out graph as labelled SMILES and asked for an exploratory
investigation against Human ABL1 using PDB 2HYY chain B. This is a deliberately
explicit internal contract experiment, not an external biological benchmark or
evidence that the compound is an ABL1 drug.

Pulsate generated the ligand geometry from its exact graph, ran the existing
workflow, passed blocking verification and displayed/reopened the actual 3D scene.
The computed intended-target Vina score was **-7.975 kcal/mol**. One panel estimate
was accepted: Human ADORA2A Ki **1.4986 umol/l**, with marginal interval
**[0.083802, 26.798] umol/l**. The broad interval is retained; its nominal-assay
semantics do not support tissue potency, functional direction, occupancy or QSP
transfer. The other 40 panel entries remained refused/unsupported. Final status
remained **INSUFFICIENT EVIDENCE**.

All **70 exported artifacts** matched their hashes. The browser acceptance test
checked the accepted gate, in-domain status, absent training-graph overlap, positive
ordered interval, absent functional direction and unsupported tissue transfer.
It also checked saved-session reopening and the actual viewer's successful render.

Evidence directory:
`.test-runs/demo2-v4-development-20261005/browser-unrelated-positive-r5/virtual-investigation-live-c93c5-investigation-and-reopening-chromium/`
Frozen positive unrelated-result SHA-256:
`5e6887a4bb8167d0c4cf6d5e3d92b4f242fcb12f857afff79b1f239ed5235c12`.
The transport-only failed attempt and the pre-repair clarification result remain
preserved in separate directories; neither was overwritten.

Visual QA then found the dense table extending outside the narrow result panel.
The binding table now uses the existing horizontally scrollable table treatment
with keyboard focus, readable column widths and wrapping evidence hashes. No
second viewer or unrelated redesign was introduced. A read-only browser reopening
in `browser-unrelated-positive-reopen-r6` passed with the same session, the same 70
artifact hashes and the same frozen-result hash; the scientific calculation was
not rerun. `accepted-binding-evidence.png` records the actual accepted estimate.

## Current regression and preservation checks

- Relevant v4 backend/native/mechanism/answer suite: **225 passed**.
- Additional acquisition/ResearchSession suite after the routing repair: **44 passed**.
- Final combined regression run against the current repaired tree: **269 passed**
  (`combined-r8.xml`); the two earlier counts are historical runs, not extra tests.
- Scientist-facing component: **10 passed**; TypeScript app check passed.
- `git diff --check`: passed (line-ending notices only).
- No commits, pushes, global dependency changes, instance lifecycle operations,
  production changes, IBM submissions or final candidate scientific execution.
- Previous v3 frozen-result SHA remains
  `1d71919c0474b60ef26f680cab69269b7f27bc7d3c29db50f16a9682342a9300`.

## Still required before the final v4 experiment

1. Finish a genuinely justified sponsor dossier, with measured/predicted/scenario/
   missing quantities distinguished, original datum provenance where available,
   explicit dose/regimen/population, uncertainty levels and leakage review. The
   synthetic interface fixtures must never be substituted for this dossier.
2. Complete the general model/library qualification package and unit-aware
   production integration where supportable. Preserve refusals for missing
   functional-activity, assay/free-concentration, tissue, biological benchmark or
   dynamic-transfer evidence; numerical agreement alone does not clear these gates.
3. Both unrelated positive and refusal binding browser paths now pass. Validate
   the final native Human sponsor-dossier path through the scientist interface,
   then freeze/hash the final source, UI, dossier, panel, models, library and rules.
4. Only then run the exact final query once in a new ResearchSession, report the
   actual PBPK/organ/activity/transfer/QSP/assessment layers separately, and stop.
