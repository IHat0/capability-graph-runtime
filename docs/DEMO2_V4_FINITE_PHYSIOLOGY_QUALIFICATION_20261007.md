# Demo 2 v4: finite Human physiology qualification

This record supersedes the *remaining-gate decision* in the earlier dated
readiness reports, not their preserved observations. The user explicitly
authorized an exploratory-only freeze if this finite qualification fails.
Software verification, source replay and internal benchmark comparison are
**not independent scientific certification or clinical qualification**.

## Frozen experiment and scope

The byte-identical protocol is
`docs/PHYSIOLOGY_FINITE_QUALIFICATION_PROTOCOL_20261007.json`, SHA-256
`d1228f4c10820eca8fc89129986e32fbbe1030189e81ce460ee1655ce9c9b74e`.
It was frozen before the new native calculations. Previous full ORd Pharm
results and early 20-cycle alternative-model probes were already known and are
explicitly disclosed: **this is not a blind qualification experiment**.

Only the existing ORd 2011 model and one established alternative, ten Tusscher /
Panfilov 2006 endocardial, are considered. The alternative was chosen from
[Barral et al.'s overall unrelated benchmark](https://journals.plos.org/ploscompbiol/article?id=10.1371/journal.pcbi.1012913),
not for a held-out candidate or a preferred dofetilide result. Its overall score
is better under both assay protocols; source equations, units and native
IKr/ICaL interfaces are available. The authors also explain why that overall
score alone cannot establish predictive accuracy. No native parameters are fit.

The finite experiment retains four dofetilide concentrations and all three
nifedipine concentrations under both Roche Pharm and CiPA assays for each model.
Four earlier ORd Pharm results are transparently reused. The remaining 24 BDF
conditions and four fixed independent Radau conditions are new native runs.
All conditions use source-matched 1 Hz pacing, 1500 prepaces, 0.05 ms observation
and extracellular ion conditions. Native initial states/equations are preserved.
Last-cycle state discrepancy is recorded; a fixed warmup is not itself proof
of physiological steady state.

Acceptance requires disclosed directions and dofetilide ordering, and at every
concentration intersection between the two eligible assay-protocol predictions
and the observed mean's two-sided 95% Student-t interval. Sample count is
derived from `(STD/SEM)^2`, not invented. This is a source-derived **internal
research gate**, not a literature-endorsed clinical model-error threshold.
Protocol spread is not a complete probabilistic prediction envelope. Unbounded
structural/exposure-transfer uncertainty prevents predictive qualification.
Fixed Radau/BDF comparisons must agree within the published 0.05 ms sampling
interval. No budgets may be changed in response to results.

## Exact original sources and semantic boundaries

| Source | SHA-256 | Reviewed role |
| --- | --- | --- |
| [Original FDA dofetilide label](https://www.accessdata.fda.gov/drugsatfda_docs/label/2016/020931s012s013lbl.pdf) | `807632eea0179d078a584b5bbbc95ab492c2eb9cb98e8bc4ee9ab9521c1bb32e` | Page 2 pharmacology and development PK; clinical QT supports direction only |
| [Original assay appendix](https://journals.plos.org/ploscompbiol/article/file?id=10.1371/journal.pcbi.1012913.s001&type=supplementary) | `ec5fe784d9f927e367d5797650a2634dd75356d706b2ba186c64d50835ccbc6e` | Pages 3-5 bath, host, temperature and protocols; page 6 multiple dofetilide potency estimates |
| [Primary paper XML](https://journals.plos.org/ploscompbiol/article/file?id=10.1371/journal.pcbi.1012913&type=manuscript) | `11d531d9afccda9ee06a57b75dcc701e587b8bba15e7a3ee019c5bcd9cffce10` | Exact Table 3, Human tissue methods and native pacing conditions |
| Original author Pharm APD90 CSV | `bf986fc4bcb79e97e92339d77f6fe4ba94f454c24286e45df626443ead00ef26` | Means, SD, SEM and complete concentrations; author repository commit `44147e974966b9b4254784173c2c6469fcc6e9ed` |
| Original author CiPA APD90 CSV | `6a2c25c3b8d54a6022cd978f2a5ae80384e6bfcb40d73d6585e1fcb00e5eaece` | Other eligible assay protocol at the same pinned repository |
| Original author potency CSV | `3cf7e0b895552f858bd06ec8188828e3e7aaf5d7b4ea23e5d946c371c8504b13` | Full-precision protocol-specific Hill slopes |

The local preserved source manifest records the exact original author-file
URLs. Both Roche dofetilide estimates remain eligible: IC50 0.029 umol/L,
Hill 1.10 (Pharm), and 0.033 umol/L, Hill 1.17 (CiPA). Older estimates spanning
0.002-0.037 umol/L remain disclosed but are not pooled without a compatible
complete protocol. The model is never allowed to choose the potency that fits
the observed APD90 best.

Nifedipine uses the author's full-precision CSV slopes 0.849 and 0.721; the
printed table rounds these to 0.85 and 0.72. The frozen protocol's shorter
"primary Table 3" source label must be read with this precision disclosure.
No numerical input was adjusted after observing a result. Its lowest observed
mean is slightly positive with an interval spanning zero; it is retained, not
discarded to improve apparent negative-control agreement. The dofetilide
0.01 umol/L original CSV mean is 88.795710 ms; the printed paper's 82(8) summary
is not silently substituted.

The frozen sign rule prescribes positive dofetilide and negative nifedipine
responses. A pass of that prescribed-sign check is **not** agreement with every
observed mean: the lowest nifedipine point has the opposite mean sign. Exact
observed-sign agreement and whether the interval includes zero are reported
separately. The immutable rule is not revised after seeing predictions.

These are **five distinct levels**: regulatory PK, heterologous Human-channel
functional assays, Human ex-vivo tissue APD90, a native single-cell AP model,
and clinical QTc. They are not interchangeable. In particular, APD90 is not
QTc. No whole-heart conduction model, clinical translation or arrhythmia
probability is claimed.

## Native model identity and numerical transparency

The original ORd CellML source hash is
`4289b8d6339e94bd7ef326069de93540692bf8dfd420e94b11a1aa72c2d7616b`.
Its separately preserved dimensional-metadata curation has source hash
`175a0ba1d2a5f1239024be69b6142c06e400ff8496b3e6c28703e09046152465`.
The curation record lists every unit annotation correction. Numerical equations,
constants and initializations remain unchanged; the numerical MathML projection
and generated numerical functions were checked separately. This is internal
dimensional engineering review, not independent biological certification.
Generated equation code hash:
`d0c71e4e5dbf6db1f4a11cd431a30624b409e8f87cb8364f9a2e9fc36618b524`.

The single established alternative is the
[pinned Physiome ten Tusscher/Panfilov endocardial source](https://models.physiomeproject.org/workspace/604/rawfile/de5a4e600b57c8b9b0bd477695648c82351bc6dd/ten_tusscher_model_2006_endo.cellml),
source hash
`317a1dc029d7914017709d2e3556df6ce9c8473bf3149c4af279e869b63f2daa`,
generated equation code hash
`3b3f9e516a30785f511daeed1cd94a43f872e01f2a098da6f525210fe6b47531`.
No native biological parameter was fitted in either model. The source-matched
extracellular bath overrides, native stimulus, solver, tolerances, initialization
and last-cycle state differences are retained in every numerical receipt.

## Accepted real exposure path remains unchanged

The accepted actual PK-Sim browser session is
`research-session-6640365fc40a72a5af56d01fd971e1ee`.
Its frozen result hash remains
`732f4fedff3c4b6ff15901c61aa727a89ccba4ed8b210faae1757fd761da69d5`.
Its two explicit ADME scenarios and two independently represented native Human
subjects were not refit to APD90 or QT. This qualification reuses all four actual
Heart Interstitial Unbound curves and both eligible dofetilide assay protocols:
eight fresh conditional peak-exposure native-cell computations, no new PBPK
fit or rerun. The separately paced millisecond observation clock is not a
fabricated PK peak timestamp.

The nominal recording bath is approximately protein-free based on the original
solution lists. Free drug was not measured. Any nominal/free equivalence remains
a visibly classified **assumption**, with culture carryover, adsorption, cell
partition and channel-state/kinetic limitations. Native interstitial unbound
exposure is not mislabeled as a measurement of cardiac extracellular drug.

## Approved native docking transport check

The user-approved one-shot SSH bridge was tested against an unrelated existing
structure/ligand fixture. Real native PDBFixer/OpenMM preparation, Meeko, RDKit
and AutoDock Vina completed, and eight artifact references were hash-verified.
The returned Vina score was -8.568 kcal/mol: a docking score, not measured
affinity or a binding free energy. The exact transport audit receipt hash is
`46885dec278992594d95c5c49728e00a4ba79f7689588e47be4e2f57c892036c`.
This is a component/transport check, not a new ResearchSession or a biological
benchmark. No production service, stopped cloud resource, dependency installation
or IBM job was involved. PK-Sim remains the accepted native Windows engine.

## Decision boundary and source-integrity repairs

The qualified research path still requires passing observed-accuracy and
uncertainty gates. The exploratory path may retain those two failed gates only;
source provenance, units, mapping, domain, nonclinical scope and critical-refusal
gates remain mandatory. Fresh numerical verification is required in both paths.

Exploratory receipts explicitly carry `qualification_state=exploratory_only`,
`qualified=false` and `candidate_decision_authority=false`. Actual numbers and
benchmark discrepancy remain in the scientist answer and prominent UI warning.
They cannot count as qualified inference, match candidate response criteria or
independently authorize CONCERN, REJECT or ADVANCE, even if a caller incorrectly
marks the inference level available. Binding Ki/Kd predictions are not relabeled
as functional IC50, and unknown direction/unsourced Hill inputs still refuse.

Native compiled CellML source identity and its execution bundle have distinct
byte hashes. Endpoint replay now preserves and checks both, with the native
compiler/source/code/inspection contract still verified independently. Original
numerical CSVs can anchor their units/context to separate preserved original
methods bytes rather than inventing a unit in a local extraction wrapper.

## Completion record

All 28 concentration/protocol BDF records, four independent Radau conditions
and eight actual-exposure native conditions have completed. Both models pass
the frozen independent-solver check but fail the complete magnitude gate.
The finite qualification is therefore **State B — exploratory only**, with no
independent candidate-decision authority. No further model search or fitting
is permitted. The exact frozen qualification result SHA-256 is
`37ac419425aa30e803b1e790fe417062537c17940efeae4e7558e5db83d69454`.

| Fixed independent condition | Absolute BDF–Radau ΔAPD90 difference (ms) | Frozen budget (ms) | Result |
| --- | ---: | ---: | --- |
| ORd / positive control, lowest concentration | 0.000016965406 | 0.05 | Passed |
| ORd / negative control, lowest concentration | 0.000002401482 | 0.05 | Passed |
| TP / positive control, lowest concentration | 0.000026521524 | 0.05 | Passed |
| TP / negative control, lowest concentration | 0.000020232207 | 0.05 | Passed |

These are numerical comparisons only, not evidence of biological predictive
accuracy. Every source, numerical input, result and initialization receipt is
preserved locally; the engine manifest will pin their hashes without committing
raw scientific evidence.

### Complete concentration-response evidence

Changes below are APD90 in milliseconds, not clinical QTc. Each model column
retains the range of **both** eligible assay protocols; the range is not a
probabilistic prediction interval. Exact full-precision records and source
variability are preserved separately. Four ORd Pharm rows are disclosed prior
computations; all other BDF records are fresh.

| Control | Nominal bath (µmol/L) | Observed mean ΔAPD90 (ms) | ORd protocol range (ms) | TP endocardial protocol range (ms) |
| --- | ---: | ---: | ---: | ---: |
| Dofetilide | 0.001 | 20.1469 | 2.8085–4.1206 | 0.7383–1.0804 |
| Dofetilide | 0.01 | 88.7957 | 37.7407–46.1879 | 9.2660–11.1583 |
| Dofetilide | 0.1 | 256.3502 | 276.8433–286.7937 | 43.7317–44.5109 |
| Dofetilide | 0.2 | 317.7483 | 418.3737–421.5938 | 51.8714–51.9957 |
| Nifedipine | 0.003 | 7.1197 | −3.2560–−2.5918 | −3.6582–−2.9337 |
| Nifedipine | 0.03 | −9.0303 | −16.6177–−15.7416 | −18.3934–−17.2878 |
| Nifedipine | 0.3 | −23.4250 | −47.4692–−41.5763 | −65.9783–−55.6157 |

Both models preserve dofetilide's increasing response and the prescribed
pharmacological signs, but **fail the complete predeclared magnitude gate**.
Neither protocol spread nor directional agreement repairs the unresolved
uncertainty. In particular, the opposite sign of the lowest negative-control
observed mean remains visible, with its interval spanning zero.

### Actual accepted Human exposure to native current block

No additional PK-Sim computation was performed for this table. The four accepted
native Heart Interstitial Unbound curves generated the fractions through the
preserved IC50/Hill equation. Each fraction is held at the sampled peak during a
separately paced native-cell observation; this is not an hours-long dynamic
whole-heart experiment. A fixed first exposure case passed a fresh identical-input
native numerical replay.

| Accepted input case / Human subject | Sampled native unbound peak (µmol/L) | Pharm / CiPA current fractions remaining | Pharm / CiPA modeled ΔAPD90 (ms) |
| --- | ---: | --- | --- |
| Case 0 / subject 0 | 0.02218927 | 0.5730866081 / 0.6140507050 | 21.1452811 / 18.9033718 |
| Case 0 / subject 1 | 0.03411764 | 0.4554259995 / 0.4902589378 | 27.9151848 / 25.8562089 |
| Case 1 / subject 0 | 0.001427776 | 0.9648482227 / 0.9752593501 | 1.5840080 / 1.1124459 |
| Case 1 / subject 1 | 0.002335971 | 0.9410748673 / 0.9568206460 | 2.6686872 / 1.9490364 |

The finite numerical experiment and production-package audit are complete.
The completed production receipt SHA-256 is
`c9763812ddfc79379f882ecd4bfe9586a54af0fe1d8f0114b8d13a9ca0fa28e3`.
Both fixed fresh native endpoint replays passed, and all eight accepted exposure
conditions match the production-derived current fractions and frozen numerical
controls. The qualification package SHA-256 is
`08fedd43e3e57558bb61ce08382ead9b287093add8e22b54d1c62d4c7cc5e288`;
its separately preserved fresh endpoint review SHA-256 is
`cc18f6c6c50b16d40b13678c0ab2dc4205a05ae813a682c5a7c5934c0d3cc49c`.
The diagnostic fixed low-concentration source-replay subset has maximum
observed discrepancy **19.066485 ms**, exceeding its source-derived diagnostic
budget **15.750529 ms**. This subset cannot replace or override the complete
multi-point qualification; both retain the failed biological accuracy gate.

The package contains all four protocol-specific functional assays and the
unchanged native alternative model. Deployment-relative artifact paths are
checked separately from their scientific byte hashes; moving a pinned file
cannot change its model, compiler, units, solver or numerical controls. The
held-out candidate has not been investigated.

The package audit exposed two transport-only differences: deployment-relative
compilation paths, and a display export that stores species at scenario level
instead of repeating it on every curve. The audit now binds every display curve
to the exact frozen native case and uses Pulsate's existing production exposure
normalizer. It preserves both original and normalized curve hashes. Neither
physical values nor scientific validation checks are relaxed. Incomplete audit
logs remain preserved; only a completed, hash-pinned production receipt counts
as the package audit. Fresh endpoint reviews are saved before the later checks.

The final regression rerun on 8 October 2026 passed **412 backend tests** and
**204 frontend tests** (24 frontend files), plus both TypeScript checks.
Windows sandbox path-resolution denials were rerun outside
the sandbox without changing the path-safety checks. TypeScript checks also
passed earlier in this same pass. Synthetic contract fixtures are tests of
software behavior only; they are not counted as biological evidence.

## General engine freeze boundary

The general runtime freeze contains 74 pinned files; its manifest SHA-256 is
`eb4776d36afe30e0c73f378d000869c6943723c188ff36b673f5864170dbd000`.
The general policy SHA-256 is
`7cc36b5bec5c5468ac6c3f742f99732864197e4d48eac6aaf465d4a62ebc7e45`.
It retains the established broad safety panel, source-pinned binding-prediction
bank, existing general ADME bank and already-reviewed Human-fu gate, accepted
native PK-Sim path, exploratory CellML package and original prospective cutoff.
It adds no new qualified physiological progression threshold or historical
candidate information. Raw models, source material, numerical evidence and
validation harnesses stay local rather than entering the source commit.

The separate engine-freeze manifest pins production source, UI, optional runtime
versions, every completed finite numerical condition, transport audits and
regression receipts. Its baseline is the existing feature-branch HEAD; the
commit containing that manifest is recorded separately to avoid a circular
commit-hash claim. The original blind results remain untouched. Only after
the general engine commit and matching remote SHA may the sponsor dossier be
prepared and the exact held-out query executed once.
