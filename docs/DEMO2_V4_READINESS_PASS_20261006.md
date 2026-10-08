# Demo 2 v4 — readiness evidence, not a final engine freeze

Status: **sponsor/PBPK browser acceptance passed; real-compound exposure-to-physiology positive acceptance NOT passed**.
The final v4 investigation has not run. There is no general-engine commit/push or
final joint source/model/rule/UI freeze. Both readiness objectives are required
before those actions. Unavailable independent clinical certification is not the
reason for this result; the remaining gaps are scientific/data/native-model gaps.

Branch: `feat/pulsate-scientific-autonomy-v1`.
Committed HEAD remains `ebdeaba9733379b61e8879cf004c633377c2f2eb`.
The existing working tree and all earlier experiment evidence were preserved.

## 1. Actual scientist interface → sponsor dossier → PK-Sim

Exact unrelated validation query:

> Simulate ethanol in a Virtual Human after a 0.1 mg intravenous bolus over a
> 2 hour simulation duration with a population of 2 human adults. Use the
> configured synthetic sponsor-side dossier and disclose that these are
> synthetic parameter scenarios, not measured or clinical pharmacokinetics.

This is a **synthetic sponsor-dossier test**, not measured ethanol ADME or
validated ethanol PK. It does not satisfy the separate real-compound physiology
acceptance criterion.

Final current-source browser session:
`research-session-6d74f3b4aa722bd246825758ea45ada9`.
It completed, blocking verification passed, and all execution steps succeeded.
Real Qwen interpreted the question; native PK-Sim 12.3.173 ran on Windows.
No quantum engine or IBM job was selected.

Pulsate ran candidate parameterization → native model construction → exposure
simulation → population aggregation → cross-species summary → native output
verification → exposure relevance assessment. Human was the only requested
species. Two bounded sponsor-input scenarios ran, with **two Human subjects per
scenario**. The parent ranges were never replaced by an arbitrary nominal input.

Exact succeeded capability sequence:

```text
pharmacokinetics.adme_predict
pharmacokinetics.adme_verify
pharmacokinetics.parameterize
physiology.organism_construct
pharmacokinetics.pbpk_simulate
pharmacokinetics.population_simulate
pharmacokinetics.cross_species_compare
pharmacokinetics.exposure_verify
discovery.exposure_relevance_assess
scientist.result_assemble
```

Graph-based ADME predictions remain separate evidence; the configured sponsor
scenario dossier, not an unreviewed predicted point, supplies these native cases.

The actual Pulsate answer reports peaks of the empirical 95th-percentile plasma
curves of **0.300757** and **0.156974 µmol/L**, respectively. These are not the
population maximum, measured values or clinical confidence bounds.

Each case preserved 98 subject/compartment series. Native organ curves include
Bone, Brain, Fat, Gonads, Heart, Kidney, LargeIntestine, Liver, Lung, Muscle,
Pancreas, PeripheralVenousBlood, Skin, SmallIntestine, Spleen and Stomach.
Plasma, unbound plasma, blood, and native organ compartments are available.
No unsupported adrenal observable was invented.

Browser checks passed for classification/source tables, no nominal selection,
range audit, evidence counts, compartment selection, scenario selection,
downloaded native evidence, and saved-session reopening.
All **31 exported artifact SHA-256 values** were checked against their bytes.

Final browser evidence:
`.test-runs/demo2-v4-readiness-20261006/sponsor-browser-r4/`.
Frozen **unrelated result** SHA-256:
`55a1e27cfd224389e6462ee4d8d54b88818e898cd8c31e43848c53071339626d`.
Sponsor test-policy SHA-256:
`2e56dec085f684139c85d62919991d4fe8aec3c8a5eff63b71ca13d4954d5aa3`.
This result hash is not a final production-engine freeze.

A read-only saved-session chart screenshot from the preceding same-input r3
confirmation is at
`.test-runs/demo2-v4-readiness-20261006/sponsor-readonly-visual-qa-r2/native-heart-unbound-curve.png`.
It was visually inspected. Its plotted median and empirical band come from real
native Heart interstitial-unbound output, not generated illustrative values.

## 2. Real functional evidence and explicit refusals

Preserved primary source: [Barral et al., 2025](https://journals.plos.org/ploscompbiol/article?id=10.1371/journal.pcbi.1012913).
The original assay appendix was read, including its bath composition and
voltage-clamp protocols. Data/model repository revision:
`44147e974966b9b4254784173c2c6469fcc6e9ed`.

Two real unrelated measured Pharm-protocol assays replay from exact original
Table 3 rows and caption, with exact molecular identity and Human target curation:

| Compound | Human target | Measured nominal-bath IC50 | Sourced Hill slope |
| --- | --- | --- | --- |
| Nifedipine | CaV1.2 / Q13936 | 0.105 µmol/L | 0.85 |
| Dofetilide | hERG / Q12809 | 0.029 µmol/L | 1.10 |

The assay host is CHO expressing Human channels, not native Human tissue.
Printed values were not silently replaced with different CSV precision.
Censored values and CSV sentinel placeholders were not treated as exact potency.
The normalized records remain **nominal-bath activity**, not measured unbound
Heart potency. Neither assay was allowed to enter physiological transfer.
Both produce `no_qualified_transfer`; these are real evidence refusals, not a
positive exposure-to-physiology demonstration.

Functional source replay receipt SHA-256:
`e25c93964bebc9db192297c94791f2a0c02a17d4e14a0c01d250375e9cf7aa7d`.
Complete latest refusal/discrepancy audit:
`.test-runs/demo2-v4-readiness-20261006/functional-readiness-audit-r2/receipt.json`.
SHA-256:
`5e62f80c5c81ba4202cd007bc7181d715ae35731302f414558dba6d6860f730e`.

### Native model probe: computed, but not qualified

The unchanged ten Tusscher 2006 epicardial source has SHA-256
`d78f3b39cc2409bde44dc5fca27fc78a4aa2910bb3201a00853c9deed36599f2`.
Pinned libCellML 0.7.1 generated equations have SHA-256
`94dc085a92ae9169a69fef8cf7f0682dc7e9ed5b51adc91b74cfe8546d1186f7`.
Its original equations/initialization ran using SciPy 1.16.3 BDF, native 1 Hz
pacing and 20 warmup cycles. Actual nifedipine assay IC50/Hill parameters were
used in the explicit current-block numerical probe, not a guessed 50% control.

| Bath concentration (µmol/L) | Computed ΔAPD90 (ms) | Observed source CSV ΔAPD90 (ms) | Source SEM (ms) |
| --- | --- | --- | --- |
| 0.003 | -3.835279 | 7.119712 | 3.660655 |
| 0.03 | -24.641774 | -9.030288 | 12.822597 |
| 0.3 | -94.413137 | -23.425000 | 6.715987 |

These are genuinely computed numerical-probe values, **not a qualified
Human-exposure run**. The epicardial/20-cycle protocol does not match the paper's
endocardial-variant/1500-prepace simulation protocol and experimental ion levels;
recorded state differences do not establish
steady state. The largest discrepancy is 70.988137 ms. No error threshold was
relaxed, fitted or selected to turn this probe into a pass.

### Additional published model inspections

These are import/unit-annotation limitations in the current adapter, not a claim
that the published biological models are universally invalid. No warning was
laundered into qualification merely because the numerical error count was zero.

| Preserved source | SHA-256 | Current import/qualification result |
| --- | --- | --- |
| Physiome ORd 2011 | `ffde17c84fd4618ea8f0fb83b480bfdfb3551a38140cf14801bf6c1dcf6bdac9` | 62 unresolved unit warnings |
| Chaste-curated ORd endocardial | `c615fd645a9f68af0d4cf8441d513e37226558bb4262d4d446adb024b2eaada5` | 63 unresolved unit warnings |
| Chaste CiPA 2017 | `c1918ade65d6f6b08791d4797d2bbb5da6c8653ca7872d9a1fb3360d4a2410a0` | 63 unresolved unit warnings |
| Benchmark-publication ORd 2011 | `4289b8d6339e94bd7ef326069de93540692bf8dfd420e94b11a1aa72c2d7616b` | 62 unresolved unit warnings |
| Benchmark-publication Bartolucci 2020 | `a66c316dd11430834c3f8003525fae050ba3667cfe3c27715f91484719553ce8` | Undeclared legacy Celsius unit; 1 validator/2 analyser errors |

## General repairs in this pass

- Sponsor evidence propagates through native scenario construction and the
  final scientist assessment. Parent ranges count once, retain their origins,
  and display as ranges with no nominal value.
- Missing route-irrelevant evidence remains visible without blocking IV solely
  because oral permeability is missing.
- Every conditional population retains all subjects and native compartment
  quantiles, rather than only the first subject's curve.
- Functional IC50/EC50/Ki/Kd, action, target/species, original source, Hill slope,
  context and concentration basis remain separate from binding predictions.
- Unsupported normalized functional evidence is explicitly refused downstream,
  rather than relying solely on an upstream flag that could be ignored.
- Native computed constants are recomputed **after** applying the perturbation.
- Observed scalar response benchmarks now have a general endpoint replay route,
  with exact-source values, units, native protocol/perturbation hashes, fresh
  numerical replay, distinct conditions and an explicit discrepancy budget.
  It does not itself constitute a biological benchmark pass.
- Native import errors and unresolved physical-unit warnings remain refusals.

New production modules contain no literal caffeine, nifedipine, dofetilide,
verapamil or held-out-candidate routing. Compound names/assay numbers above are
validation data, not production expected-answer constants.

## Verification and repository safety

- **397** relevant backend tests passed; JUnit report:
  `.test-runs/demo2-v4-readiness-20261006/regressions-r6.xml`.
- **16** relevant frontend tests passed; TypeScript app check passed.
- Final sponsor browser: **1 passed**, 1 separate saved-ADME test skipped.
- `git diff --check` passed (line-ending notices only).
- Unrelated `pyproject.toml` and `src/cgr/swebench/integration.py` byte hashes
  still match the preserved pre-pass values.
- No staging, commit, push, destructive Git operation, production-service
  modification, stopped cloud-resource start, instance termination or IBM job.
- Existing Qwen credentials were used only in process memory for approved
  isolated validation; they were not printed or saved.
- Original v1/v2/v3 results and every earlier checkpoint remain untouched.

## Exact remaining acceptance gap

There is still **no real unrelated compound session proving the complete
supported Human exposure → quantitative functional direction → compatible
unbound tissue basis → scientifically qualified native parameter → physiological
simulation → verified numerical answer chain**.

Real assay replay, a native numerical probe, passing software tests, and a
synthetic sponsor PBPK browser pass cannot be substituted for that chain.
The missing concentration-basis/transfer qualification and matched observed
native-model benchmark must be resolved on unrelated evidence. This report does
not authorize the final engine freeze or the one held-out v4 run.
