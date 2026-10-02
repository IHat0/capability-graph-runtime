# ADME Prediction v1 — verified partial implementation

2026-10-02. Branch `feat/pulsate-scientific-autonomy-v1`.
Virtual Organism v1 was selectively committed and normally pushed as
`1b114f2b156bbafef507ab21c4d37940ad6641c9`; local and remote tips matched.
ADME changes remain uncommitted and unpushed. Unrelated `pyproject.toml` and
`src/cgr/swebench/integration.py` changes were preserved byte-for-byte.

## Product answer, without overstating completion

**Pulsate can now take an unseen supplied molecular graph, verify it, calculate
descriptors, run numerical ADME predictors, build the existing species dossier,
show uncertainty and determine PBPK sufficiency. It cannot yet reliably create
a sufficient novel-compound Human PBPK model from a graph alone.**

The actual dossier-free generated-graph browser case returned verified partial
predictions and `insufficient_parameterization`, not a fabricated curve. Missing
inputs were native hepatic plasma clearance, renal clearance, reference body
weight, solubility reference pH and reviewed ionization/pKa. Its binding prediction
was weakly supported and therefore also blocked automatic downstream use.
Human hepatocyte intrinsic clearance and Caco-2 predictions do not resolve those
native-input gaps. Rat-specific intrinsic clearance is unavailable.

A source-backed partial Rat dossier, with binding deliberately withheld, WAS
completed using a real numerical binding prediction and executed through real
PK-Sim, including five independent scenarios. This proves conditional integration
and actual parameter-sensitivity propagation, not fully autonomous novel Human PK.

## Selected tools, provenance and deployment

See [engineering decision](ADME_PREDICTION_ENGINE_DECISION.md) for endpoints,
alternatives, licenses and translation boundaries. RDKit 2026.3.5 (BSD) and six
endpoint/species CPU random forests were selected. scikit-learn 1.8.0 (BSD-3),
SciPy 1.16.3, NumPy 2.5.2, joblib 1.5.2 and threadpoolctl 3.6.0 were installed only
in the isolated training dependency directory. Production inference uses RDKit,
bounded JSON trees and the existing Pydantic contracts, not pickle or an LLM.

ADMET-AI/Chemprop, OpenADMET/ChemEleon, Dimorphite-DL and httk were evaluated but
not used to manufacture missing native inputs. Their endpoint/calibration,
training-overlap, ionization and biological-input limitations are documented in
the decision. No commercial licensed pKa service was silently substituted.

Configure `PULSATE_ADME_MODEL_MANIFEST` to the reviewed local model manifest.
Models, data, training dependencies, runtime files and private evidence are NOT
bundled into tracked source. RDKit must match the training version; mismatch
fails closed. A neighbouring held-out report is hash-recorded and must reference
the same frozen manifest. Numerical prediction works on CPU on Windows/Linux.
The current native PBPK runtime remains the previously validated Windows
PK-Sim CLI 12.3.173; that executable and physiological database are still required.
The Qwen HTTP service is needed only for scientist-intent/control interpretation
and evidence-backed presentation, not numerical prediction.

AqSolDB data: CC BY 4.0. Several other TDC dataset entries say "Not Specified"
alongside a CC BY link; redistribution/commercial licensing requires review.
Training-data access does not itself grant distribution rights for a release.
PK-Sim distribution remains subject to its GPL-2 licensing review.

## Frozen models and numerical validation

All model and calibration artifacts were written and hashed before ANY held-out
prediction was evaluated. No refit or tuning followed test results. The fixed
64-tree policy, preprocessing, calibration and domain rules are in
`scripts/train_adme_models.py` and the decision document. Scaffold-group buckets
target 70/15/15, not exact row percentages; large scaffold groups produce uneven
splits. Graph duplicates are median-collapsed before splitting. Mixtures,
unsupported elements, invalid/nonfinite records are rejected, with counts retained.

332 human clearance boundary records (<=3 or >=150 in raw endpoint units) were
excluded under the predeclared presumptive-censoring policy. The raw data do not
prove every boundary record is censored; that is a preprocessing assumption and
limits coverage of low/high turnover compounds. All exclusions and duplicate
counts are in the model training provenance. This is NOT a complete raw-dataset
benchmark or independent prospective laboratory validation.

Pinned TDC metadata commit: `c310c35f27e3f506411018ac43d97b8ba23ca652`.
Frozen model-manifest SHA-256:
`90d9bacd8c35d408f44a146addfb15222ce589b88b2ee73eb6a7d47b13d2dc0d`.

| Endpoint | Train / calibration / test | MAE | RMSE | Median fold error | Spearman | Interval coverage |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Aqueous logS | 4758 / 1001 / 2958 | 0.6623 | 0.8927 | 3.211 | 0.8634 | 92.90% |
| Human log10 fu | 1110 / 222 / 275 | 0.4637 | 0.5835 | 2.339 | 0.6435 | 84.36% |
| Rat log10 fu | 482 / 116 / 117 | 0.5113 | 0.6458 | 2.755 | 0.5451 | 88.03% |
| Human log10 hepatocyte CLint | 528 / 107 / 138 | 0.3376 | 0.4176 | 1.923 | 0.1723 | 80.43% |
| logD at pH 7.4 | 2935 / 558 / 698 | 0.6901 | 0.8758 | 3.993 | 0.6965 | 88.68% |
| log10 Caco-2 permeability | 608 / 130 / 159 | 0.3143 | 0.4039 | 1.839 | 0.8031 | 89.31% |

MAE/RMSE use ORIGINAL MODEL LOG ENDPOINT units, not mg/L or percentage binding.
Fold error is `10**abs(log prediction − log reference)`. Rank correlation and
coverage use all retained test rows, not a selected successful subset.
The complete per-graph predictions, experimental labels, errors, chemical
descriptors, domain assessments and domain-stratified metrics remain in
`.test-runs/adme-prediction-20261002/models-v1/held-out-validation.json`.

Clearance rank correlation is poor. Most endpoints under-cover the nominal 90%
target under scaffold shift. These weaknesses are shown in the UI and are NOT
summarized as an accuracy PASS. They were not repaired by retuning on the test set.

| Model file | SHA-256 |
| --- | --- |
| aqueous_solubility-chemistry.json | `affc71a5520b7409a5ad4c95e7b690ff4da9c536b856f61ee07ede9bb0248adf` |
| fraction_unbound-Human.json | `132815b94140cff13497bf36db34f211869a80ef54154c32278948ccd9847aa6` |
| fraction_unbound-Rat.json | `b3a2b6f7d3ffaa7d53e64a9df726b3771d9b122b817b1ba3e322c9c7112ab8f6` |
| hepatocyte_intrinsic_clearance-Human.json | `c28681a72c500daa6c1f6b09061338e18b1885ecde674ff90681a11a87e7fb35` |
| logd_ph74-chemistry.json | `0cc664b99e54da13b41f9adfc55fb76a5eba79c6a925efbe65f4830ba2c7cd0c` |
| caco2_permeability-Human.json | `ae71c8c48b86937709f519d2f998363684e585d7231b642e963be1deeb28e7dd` |

Source URLs, raw-data hashes and preprocessing/split hashes are in each model,
the training configuration and `research/manifest.json`. AqSolDB, AZ PPBR,
AZ human hepatocyte clearance, AZ lipophilicity and Wang Caco-2 supply distinct
experimental endpoints. Actual PPBR species labels were filtered: human and rat
are separate models, not transfer of a human binding prediction to rat.

## Applicability, evidence precedence and uncertainty

Nearest-training radius-2 Morgan Tanimoto plus descriptor ranges determine
in-domain / weakly-supported / out-of-domain status. The calibration similarity
10th percentile is frozen; a further 0.10 similarity margin distinguishes weak
support. This is a heuristic, not a confidence probability. Training membership
is explicitly reported. Weak/out-of-domain native predictions block PBPK.

90% split-conformal marginal intervals use held-out calibration residuals in
log endpoint units. They have no individual, OOD or joint multi-endpoint coverage
guarantee. fu intervals are physically bounded at 1, with clipping disclosed.
Molar solubility converts to mg/L with graph-derived MW; unknown reference pH
remains missing. No other empirical PBPK conversion is silently performed.

Measured/reviewed sources precede predictions. Conflicts are preserved; a supplied
incomplete dossier cannot hide an available catalogue measurement. Additional
predictions may be unused when a reviewed complete native model wins. Native
input provenance distinguishes that from a model actually using predicted ADME.

Supported numerical outputs: MW, formal charge, calculated logP, TPSA, HBD/HBA,
other declared graph descriptors, logD7.4, aqueous logS/mg-L translation, Human/Rat
fu, Human hepatocyte CLint and Human Caco-2 apparent permeability.
Unsupported numerical endpoints/native translations: calibrated micro-pKa,
blood/plasma ratio, Rat CLint, hepatic plasma clearance without validated IVIVE,
renal clearance, specific intestinal permeability, CYP/transporter kinetics and
clinical metabolic stability. No classifier is converted to an invented rate.

## Real browser / native results

Qwen model: `Qwen/Qwen2.5-Coder-7B-Instruct`, existing running service only.
Local isolated API: 127.0.0.1:8014; frontend: 127.0.0.1:5182. Separate runtime
and catalogue; no production service changes or stopped cloud-resource starts.
Model credential was held only in process memory, not logged or persisted.

All cases used actual ResearchSessions, real interpretation, production capability
handlers, numerical inference, blocking verification, scientist-facing results,
artifact download hash checking and saved-session reopening.

### 1. Dossier-free unseen graph

Session `research-session-1dac241c93cedce0c27a6915d3ebef2d`.
SMILES `CCOc1ccc(CC(=O)O)cc1`, arbitrary label generated-G1. No public-name lookup,
no public 3D download, no ADME dossier. All endpoint-model training-membership
checks were false. The structure was supplied as a graph; this case does not
claim the current task synthesized that graph or established worldwide novelty.

Exact request is in the saved session. Human fu 0.220658, interval
[0.0312352, 1], weakly supported. Rat fu 0.118252, interval [0.0103600, 1], weakly
supported. Solubility prediction 2606.68 mg/L, interval [61.78, 109980.44], with no
established pH. Other endpoints and all provenance are preserved. Both species
returned exact critical missing inputs and no accepted native exposure curve.
This is the correct partial result, not a graph-only Human PBPK success.

### 2. Conditional Rat integration and real uncertainty propagation

Session `research-session-02c263a46c21b93be15cf2af2e1205dc`.
Graph-reference-R1 is the acetanilide graph supplied under an arbitrary label,
with a source-backed partial Rat dossier and no binding value. The isolated
catalogue withheld that matching reference so it could not accidentally fill the
test from a known measurement. Original reference files were not changed.
This is a conditional integration test, NOT a completely novel molecule or a
complete-dossier-free Human claim.

Rat fu prediction **0.1814745**, marginal interval [0.0158988, 1]. The binding
forest had not seen this graph. Withheld experimental reference fu **0.63**
(reported SD 0.04, serum ultrafiltration; original publication linked in the
source dossier). Absolute fu error is **0.4485255**, about **3.47-fold** under-
prediction. The wide interval contains the reference. This is not good point
accuracy, and no tuning followed this comparison.

Five actual PK-Sim input snapshots, executable PKML models and raw CSV results:

| Scenario | Dose mg/kg | fu | Sampled plasma Cmax µmol/L | AUC 0–4h µmol·h/L |
| --- | ---: | ---: | ---: | ---: |
| nominal | 0.1 | 0.1814745 | 1.879998 | 1.2993890 |
| fu low | 0.1 | 0.0158988 | 4.706222 | 1.2890973 |
| fu high | 0.1 | 1 | 0.4907292 | 0.9459277 |
| dose 2 | 1 | nominal | 18.799990 | 12.9939628 |
| dose 3 | 3 | nominal | 56.399960 | 38.9815463 |

The explicit sweep was 0.1/1/3 mg/kg, not a selected therapeutic dose. Max/min
dose-normalized Cmax ratio 1.00000053; AUC ratio 1.00000878. Modeled tissue
ordering was unchanged. Near-proportionality belongs to this fixed lumped model
and tested range, not evidence against real biological saturation.

Sensitivity uses one-at-a-time marginal interval boundaries with fixed reference
physiology. It is NOT a sampled joint distribution, joint 90% PK interval, or
full uncertainty propagation of every biological/structural assumption. Sourced
clearance, ionization and other inputs remain conditional assumptions/evidence.

### 3. Existing Human population / Rat reference regression

Session `research-session-73debf39fa7db7fd6ef3b241a4743d09`.
Real request: reviewed caffeine exposure, 3 mg/kg intravenous infusion over 10
minutes, 16 h horizon, four human adults and one reference Rat.

Human sampled Cmax values: 56.6662, 47.4784, 48.5666, 52.7389 µmol/L; finite-window
AUC: 172.387, 85.3694, 64.9389, 194.525 µmol·h/L. Rat Cmax 27.9955 µmol/L,
AUC 67.9056 µmol·h/L. These are reference-model reproductions, not novel predictions
or newly validated physiological parameters. Additional ADME predictions did not
replace the authoritative reviewed/native or measured inputs. Physiological
population bands remain separate from drug-parameter uncertainty.

## Selected composed workflow and independent checks

`adme_predict → adme_verify → parameterize → organism_construct → pbpk_simulate
→ population_simulate → cross_species_compare → exposure_verify
→ exposure_relevance_assess → scientist.result_assemble`.

Actual capability identities are pharmacokinetics.*, physiology.organism_construct,
discovery.exposure_relevance_assess and scientist.result_assemble. All ten steps
succeeded in the accepted browser cases. Computation remained classical; the
recorded reason is endpoint QSAR/native mechanistic PBPK, not an electronic
problem. Quantum infrastructure was not removed and no IBM/Qiskit job was sent.

Independent replay checks graph identity, original supplied/reviewed dossiers,
model/feature version and hashes, request hash, species/units, predicted values,
domain, intervals, conversions and precedence. Corrupted values, units, intervals,
models, source paths and provenance are blocked by adversarial tests. Native
checks reconstruct scenario snapshots, match every scenario identifier/kind,
verify executable physiology/dosing/clearance inputs, hash files, reparse raw
curves and recompute metrics and population summaries. Clinical validity is not
the meaning of verification PASSED.

Browser failures were preserved and repaired generically: missing ADME semantic
operation, lossy explicit dose lists, conflating infusion time with observation
horizon, and losing Human population scope. Exact unchanged questions were rerun.
No molecule-specific routing or expected result constants were introduced.

## Evidence, regressions, hygiene and limitations

Evidence root: `.test-runs/adme-prediction-20261002/`. Final accepted browser dirs:
`browser-rat-final2`, `browser-reference-final`, `browser-graph-final2`.
Each contains session/visualization JSON, downloaded artifacts and screenshots
including `reopened.png`; all download hashes were compared to stored references.
`browser-readonly-ui-final` additionally captures the current evidence badges,
native-input precedence disclosure and plots by reopening those three sessions
without new computation. The final production verifier was rerun read-only on
all saved native evidence; a deliberately omitted dose scenario was rejected.
`final-evidence-manifest.json` records a bounded immutable evidence file list.

Final Python core/scientific regression run: **198 passed**. Separate optional
PySCF/OpenMM/Qiskit adapter checks: **3 skipped**, dependencies unavailable; no
hardware submission. Frontend: **188 tests / 23 files passed**; TypeScript/build
passed, with the existing large Mol* bundle warning. Final browser acceptance:
**3 cases passed**, including downloads and reopening. Internal numerical errors
above are separate from software regression success.

Production-term scan found no held-out compound/target, historical mechanism or
expected Demo 2 routing additions. Frozen result files were hash-checked only,
not read for outcomes or used to tune/choose tests/models. Frozen hashes remain
`0ea2dc54761c4eb81f82661c5739c5a3e3219dc3ea22df44fed09da087af3a10`
and `2837ca98dc688f7af5858b6ba8785a13c67ce460022ad4d0d3862d22915cd140`.

Changed intended files: adme_prediction.py; train_adme_models.py;
virtual_organism.py / handlers; scientific_requirements.py / runtime;
frontend types, VirtualOrganismResult / tests, styles and browser acceptance;
test_adme_prediction.py / test_virtual_organism.py; these two ADME documents.
Runtime assets, datasets, model weights, evidence, dependencies and old/unrelated
files remain local and unstaged. No ADME commit/push occurred.

No clinical efficacy, safety, toxicity, functional effect or effective dose is
established. No pharmacology/QSP/QST or next demonstration work was begun.
The remaining gap is scientifically defensible, calibrated native parameterization
for a graph alone—not merely an API/planner/UI integration gap. This milestone is
therefore **partial**, with explicit blockers, and must not be represented as
complete automatic novel-compound Human PBPK.
