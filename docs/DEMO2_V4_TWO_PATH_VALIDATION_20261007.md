# Demo 2 v4: sponsor path completed; physiological qualification not passed

This is an engineering acceptance record, **not** the final v4 engine freeze or
held-out investigation. No Torcetrapib v4 ResearchSession was created or executed.
Committed HEAD remains `ebdeaba9733379b61e8879cf004c633377c2f2eb` on
`feat/pulsate-scientific-autonomy-v1`. All changes remain uncommitted and unpushed.
Earlier results and unrelated local files are preserved.

## A. Real candidate identity -> sponsor scenarios -> actual native Human PBPK

The fresh, actual browser question was:

> Simulate dofetilide in a Virtual Human using the supplied frozen sponsor-side
> scenario dossier: 0.5 mg intravenous over a 30 minute infusion for 24 hours,
> with a population of 2 adults. Show the independently simulated parameter
> ranges, plasma, unbound plasma, blood, heart, liver, kidney and brain
> concentration-time curves. These are explicit exploratory scenarios, not
> measured private sponsor parameters, oral-study validation or a clinical
> safety prediction.

Session: `research-session-6640365fc40a72a5af56d01fd971e1ee`.
It completed without clarification. Real Qwen interpreted the ordinary question;
PK-Sim 12.3.173 built and executed the native models; blocking computational
verification passed. The saved session reopened and its actual curves remained
visible. No quantum computation or IBM job ran.

Pulsate's exact principal answer:

> Virtual Organism: exposure supported; 1 species (Human), 0 reference
> computation(s) and 2 conditional input scenario(s). This is exposure evidence,
> not a safety assessment.

Workflow:

```text
pharmacokinetics.adme_predict
-> pharmacokinetics.adme_verify
-> pharmacokinetics.parameterize
-> physiology.organism_construct
-> pharmacokinetics.pbpk_simulate
-> pharmacokinetics.population_simulate
-> pharmacokinetics.cross_species_compare
-> pharmacokinetics.exposure_verify
-> discovery.exposure_relevance_assess
-> scientist.result_assemble
```

### Input classification and source boundaries

The verified molecular identity is `IXTMWRCNAAVVAI-UHFFFAOYSA-N` from the
preserved public identity record and RDKit graph verification. It is not a
synthetic molecule fixture. No private sponsor dataset is claimed.

The [FDA development-property source](https://www.accessdata.fda.gov/drugsatfda_docs/label/2016/020931s012s013lbl.pdf)
and [original Human PK source XML](https://www.ebi.ac.uk/europepmc/webservices/rest/PMC4579946/fullTextXML)
motivate the frozen exploratory dossier. Relevant original PDF pages were
rendered and visually checked; source bytes are preserved. Background references
do not turn every scenario input into a measured datum.

All uncertain native ADME inputs remain **sponsor-side scenario assumptions**:
logP 1-3; fraction unbound 0.3-0.4; reference-pH solubility 1-100 mg/L;
hepatic plasma clearance 0.3-2 and renal plasma clearance 1-5 mL/min/kg;
two explicitly assumed ionization profiles. Reference pH, clearance-normalization
weight and binding-partner assumptions are separately disclosed. The IV regimen
is hypothetical, not a claim to reproduce the published oral PK experiment.
No observed Cmax was fitted, and no exact convenient nominal parameter set was
selected. Graph-derived ADME prediction evidence remains separate and does not
replace a missing qualified candidate-specific predictor.

Two level-covering native input scenarios were frozen **before** simulation.
Each built a native Human model and simulated two Human subjects. This is not
an exhaustive evaluation of all interactions, a probabilistic drug-parameter
distribution or validated candidate-specific PK. Native subject physiology,
not the reference clearance-normalization weight, determines each virtual body.

Input hashes:

- Dossier: `2aa00d4a352cbf7172f32ca7df95618616f054411176237fa25bf9d9a2a03f1a`.
- Scenario rationale/levels: `431efa7e1c56c79cbb8eb0bb0b997a9ef30ab4eeb824cc86ae5ad1a3c098a1ca`.
- Isolated policy: `a7a178364098a94e99248a2a20d01affddfb3ca4efb17f36a5da7e16f71dac67`.

### Genuinely computed exposure

Both scenarios retain 98 subject/compartment series. Native organs include Bone,
Brain, Fat, Gonads, Heart, Kidney, LargeIntestine, Liver, Lung, Muscle, Pancreas,
PeripheralVenousBlood, Skin, SmallIntestine, Spleen and Stomach. Plasma, unbound
plasma, whole blood and native organ compartment curves are present. No adrenal
curve was invented because this native export does not represent one.

| Conditional case | Subject | Sampled plasma Cmax (umol/L) | AUC 0-24h (umol*h/L) |
| --- | --- | --- | --- |
| `sensitivity-fdabf6494f94f21062122438` | 0 | 0.07021204 | 0.159483300925 |
| same | 1 | 0.09217648 | 0.1931340532 |
| `sensitivity-1bce87090756b8989d08b8ea` | 0 | 0.005420268 | 0.0102246817775 |
| same | 1 | 0.006889513 | 0.0119097063475 |

Sampled Tmax is 0.5h in these exported plasma series. The peaks of the two-subject
empirical 95th-percentile plasma curves are respectively 0.091078258 and
0.00681605075 umol/L. They are **not** measured Cmax, population maxima or clinical
confidence bounds. No terminal extrapolated AUC, clearance, half-life or oral
bioavailability was manufactured from these sampled curves.

All **31 exported artifacts** were downloaded through the actual session API and
their SHA-256 values checked against their bytes. Native model and result files,
source identity, dossier provenance, final answer, plots and reopening evidence
are preserved under:
`.test-runs/demo2-v4-readiness-20261006/unrelated-sponsor-browser-r5/`.

Frozen unrelated result SHA-256:
`732f4fedff3c4b6ff15901c61aa727a89ccba4ed8b210faae1757fd761da69d5`.
This is **not** a joint general-engine freeze.

A separate read-only saved-session inspection captured and visually verified:
`.test-runs/demo2-v4-readiness-20261006/unrelated-sponsor-saved-qa-r1/saved-native-heart-result.png`.
That inspection did not execute a new scientific request.

## B. Source-grounded native physiology: actual calculation, no qualification pass

The real Human exposure source's Table 3 reports an **authors-estimated** free
plasma Cmax of 2.2 nM = 0.0022 umol/L. The original column, molecular identity,
species, endpoint, units and original source bytes independently replay.
It is labelled sourced, not measured free cardiac tissue. No reported peak time
was supplied, so `times_h` remains `[null]`; a fictitious PK timecourse was not
constructed. Original source SHA-256:
`1d995c44ef084cfbd9faf0f21910bd6db59b048c48273f9737bbfd41d8d2cd19`.

[Barral et al.](https://journals.plos.org/ploscompbiol/article?id=10.1371/journal.pcbi.1012913)
provide actual Human hERG expressed in CHO functional voltage-clamp activity:
the selected Pharm assay's IC50 is 0.029 umol/L with sourced Hill coefficient
1.10 and explicit blocking direction. The source assay is not native Human
ventricular tissue. Original printed table values and assay protocol are
preserved rather than selecting a different literature potency for a better fit.
The complete recording-solution and cell-culture context on original appendix
page 3 was visually inspected and checked with two independent PDF text parsers.

The conditional probe explicitly assumes approximately protein-free assay bath
and passive equilibrium between free plasma and extracellular cardiac exposure.
Neither free assay concentration nor cardiac exposure was measured. Culture
carryover, adsorption, cell partition, assay-state dependence, transport and
binding kinetics remain limitations. The source endpoint and these assumptions
were frozen before this native computation. They were **not** configured as a
qualified production transfer.

The existing Hill current-block rule yields a current fraction remaining of
`0.9446282703275025`. The established ORd 2011 endocardial model's exact native
parameter is `IKr.GKr_b`, native unit `milliS_per_microF`, baseline value 0.046.
It connects to the membrane-voltage equation. This is a sourced functional
block calculation, not an arbitrary 50% control or conversion of binding Ki.

The original numerical equations, constants and initial conditions are
unchanged. A preserved dimensional-metadata correction resolved the compiler's
unit-annotation issues; all original and regenerated numerical function ASTs
remain identical. The corrected native source hash is:
`175a0ba1d2a5f1239024be69b6142c06e400ff8496b3e6c28703e09046152465`.

Actual native simulation used 1Hz endocardial pacing, 1500 warmup cycles per
condition, a 0.05ms observation grid, SciPy BDF and pinned non-fastmath Numba
acceleration. Extracellular ion conditions and tolerances are recorded. The
0-to-1000ms native observation clock is not an invented Human plasma peak time.

Genuinely computed APD90:

- Baseline: 290.0344546294165 ms.
- Perturbed: 299.6976030917173 ms.
- Change: **+9.663148462300853 ms**.
- Normalized last-cycle state differences: approximately 2.77e-9 and 2.81e-9.

This is a conditional single-cell model response, **not** observed Human QT,
arrhythmia probability, clinical safety, or a completed qualified physiology
ResearchSession. Numerical convergence does not establish that interpretation.

Frozen conditional input SHA-256:
`b542ea7f6cd52c3a98323b25bc745468e57a3a7ba34d55a5882272c2317cd9ec`.
Native result SHA-256:
`0f610df6792884dfdcddd8181f0c1abc6e078a1051d81a889aa28a91ae7cc7f9`.
Fresh independent numerical replay **passed** for this exact result. Receipt
SHA-256: `548328f78a02d10a88b60e8bc27fc0de97f6092938b6c1d783aa63cd7ac35619`.
It checks native model identity, declared identifiers/units and a new integration;
it does not turn numerical agreement into biological qualification. Full inputs,
source bytes, native result and verification are preserved under
`.test-runs/demo2-v4-readiness-20261006/real-exposure-native-conditional-r2/`.

### Exact remaining scientific gap

The matched model's actual comparisons with the preserved Human tissue dataset
are as follows; these discrepancies were not hidden or fitted away:

| Assay concentration (umol/L) | Computed delta APD90 (ms) | Observed source mean (ms) | Source SEM (ms) | Absolute discrepancy (ms) |
| --- | --- | --- | --- | --- |
| 0.001 | 4.120628 | 20.146906 | 4.762286 | 16.026278 |
| 0.01 | 46.187925 | 88.795710 | 8.413278 | 42.607785 |
| 0.1 | 286.793709 | 256.350160 | 21.338198 | 30.443549 |
| 0.2 | 421.593801 | 317.748269 | 32.877906 | 103.845532 |

The CSV's 0.01 point is 88.795710 ms; the paper's displayed 82(8) summary is not
silently substituted. SEM and between-preparation SD describe different
quantities; neither was relabelled as a convenient arbitrary prediction-error
budget. The low-concentration native result independently replayed, with receipt
SHA-256 `d5baa569580d5f5e3eb549b0c9d40bbb3c66eac34096bf78e7d8d030e5e01578`.

The [ORd original validation paper](https://journals.plos.org/ploscompbiol/article?id=10.1371/journal.pcbi.1002061)
and its original Figure 8 were also preserved and visually inspected. Its
established framework and author-reported validation do not independently
qualify Pulsate's present assay/exposure/model-to-tissue integration. No exact
observed numeric table was reconstructed from plotted pixels or admitted as if
it were original measured source data.

There is currently **no defensible, predeclared observed-error/uncertainty
qualification for this transfer**. Approximate free-concentration assumptions
also remain conditional. Marking the package passed, increasing an error budget
after seeing these deviations, or treating identical solver replays as
biological evidence would not solve that scientific gap. No external clinical
certifier or universal physiology platform is being demanded; the missing link
is this one model-to-tissue transfer's own bounded validation evidence.

Production refusal behavior remains intact: binding-only evidence, unknown
direction, unsourced Hill parameters, incompatible concentrations, altered
source hashes and failed/missing qualification do not authorize a transfer.
The original nominal-bath records and their real `no_qualified_transfer`
refusals are preserved. The new conditional probe uses the existing explicit
numerical-validation route and is not admitted to the production model library.

Consequently **B has not passed**, even though a real source-grounded native
calculation exists. There is no justified CONCERN or REJECT assessment from it,
and no claimed positive qualified physiology UI/session acceptance.

## Scoped repairs and regressions

- Separate explicit infusion duration from the independent simulation horizon;
  resumable duplicate confirmations do not invent contradictory infusions.
- Constrain an absent optional dose sweep to an empty array, preserving explicit
  requested-sweep validation. Actual Qwen hallucinated a sweep before this fix;
  those unsuccessful attempts remain preserved.
- Recognize canonical molar units through dimension-checked normalization with
  exact original bytes and explicitly non-primary conversion receipts.
- Initialize typed native arrays before compiling a model with zero computed
  constants; equations, tolerances and validation thresholds remain unchanged.
- Display sourced Human exposure endpoints without fabricating timestamps,
  timecourses or tissue equivalence.
- Count conditional scenarios separately from species in the actual final answer.

Latest relevant regression results: **418 focused backend tests** plus **23 shared
runtime/acquisition integration tests** (441 total), **17 frontend tests**,
TypeScript app and tooling checks passed, actual fresh browser **1 passed/1 unrelated test
skipped**, and read-only saved reopening passed. Backend JUnit:
`.test-runs/demo2-v4-readiness-20261006/regressions-r8.xml`.
Shared integration JUnit:
`.test-runs/demo2-v4-readiness-20261006/shared-integration-r1.xml`.
`git diff --check` passes. The index remains empty.

Unrelated `pyproject.toml` and `src/cgr/swebench/integration.py` byte hashes still
match the preserved pre-pass values. No broad staging, destructive Git command,
commit, push, production-service change, stopped cloud-resource start, instance
termination, IBM submission or held-out candidate/history retrieval occurred.
No secret was printed or persisted.

## Freeze decision

A is demonstrated under honestly labelled, source-motivated sponsor scenarios.
B's scientifically qualified positive chain is not established. Therefore the
joint general engine freeze/commit/push and the one held-out v4 run remain
**not performed**. This is a specific unresolved scientific transfer-validation
blocker, not a request for a new architecture or universal qualification project.
