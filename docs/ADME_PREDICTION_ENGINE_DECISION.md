# ADME Prediction v1: scientific and engineering decision

Decision fixed before training/held-out evaluation, 2026-10-02. This is an
exploratory endpoint service, not a validated clinical PK predictor. The language
model never supplies numerical endpoints. Virtual Organism freeze:
`1b114f2b156bbafef507ab21c4d37940ad6641c9`.

## Selected modular toolchain

RDKit 2026.3.5 supplies molecular identity, molecular weight, formal charge,
Wildman–Crippen calculated logP, TPSA and hydrogen-bond counts. Calculated logP is
not experimental logD or an experimental partition coefficient. RDKit is BSD.
[Official descriptor documentation](https://www.rdkit.org/docs/source/rdkit.Chem.Crippen.html).

Endpoint-specific random forests use scikit-learn 1.8.0 (BSD-3-Clause), 64 trees,
depth 12, minimum leaf 3, feature fraction 0.7, seed 20261002. Inputs are a
512-bit radius-2 Morgan fingerprint plus eight RDKit descriptors. No tuning on
the held-out partition or product acceptance molecules is permitted. Training
needs NumPy/SciPy/scikit-learn on CPU; inference needs only RDKit and bounded,
hash-pinned JSON trees, not pickle or executable model files. Windows and Linux
inference share the same code. No GPU or LLM is required for inference.
[Algorithm documentation](https://scikit-learn.org/1.8/modules/generated/sklearn.ensemble.RandomForestRegressor.html).

Public data are pinned through TDC commit
`c310c35f27e3f506411018ac43d97b8ba23ca652` and individual downloaded SHA-256s:

| Endpoint | Definition / model-space units | Species | PBPK use |
| --- | --- | --- | --- |
| AqSolDB | log10 aqueous molar solubility | chemistry | Convert to mg/L; unknown assay pH remains unknown |
| AZ PPBR | log10(1 − bound percentage / 100) | Human and Rat, separate models | Fraction unbound; never exchange species |
| AZ hepatocyte clearance | log10 apparent intrinsic clearance, µL/min/10^6 cells | Human | Not systemic plasma clearance |
| AZ lipophilicity | logD at pH 7.4 | chemistry | Display only; never substitute for logP |
| Wang Caco-2 | log10 apparent permeability, cm/s | Human cell assay | Display only; not native specific intestinal permeability |

The PPBR download contains several species despite the summary dataset
description; the trainer filters the actual Species column. Zero unbound
fractions and clearance assay boundary values are excluded as censored data,
not assigned artificial exact labels. Invalid graphs, mixtures, unsupported
elements and nonfinite labels are rejected. Duplicate canonical graphs are
collapsed by median, with counts recorded. Experimental assay heterogeneity,
condition gaps and censoring affect validity.
[Dataset catalogue and original references](https://tdcommons.ai/single_pred_tasks/adme/).
Raw files, metadata, source URLs and hashes stay local. AqSolDB is CC BY 4.0.
TDC lists "Not Specified" alongside a CC BY link for several other datasets;
their commercial redistribution rights require review. TDC's software license
does not resolve underlying dataset rights. Do not bundle datasets or trained
weights into a release until that review is complete.

## Fixed validation and domain policy

Canonical-graph deduplication precedes deterministic scaffold-group assignment
to 70/15/15 train/calibration/test partitions. Empty scaffolds use the graph key
so unrelated acyclic compounds do not all become one group. Calibration and test
graphs never enter fitting. All splits, rejection counts, model hashes and
per-molecule test errors are persisted. Models and calibration metadata are
written and hashed BEFORE held-out predictions are evaluated; no refit follows.
These are internal scaffold hold-outs, not independent prospective experiments.

Absolute calibration residuals produce a 90% split-conformal interval in each
model's original log endpoint. The finite-sample order statistic is recorded.
This offers marginal coverage only under exchangeability, not guaranteed
coverage under scaffold shift or for an individual new molecule.
[Conformal prediction reference](https://arxiv.org/abs/2107.07511).

Applicability uses nearest-training Morgan Tanimoto and descriptor ranges.
The calibration 10th-percentile similarity is the frozen domain threshold;
below it is out-of-domain and blocked from PBPK. The next 0.10 similarity margin
is weakly supported and also blocked from automatic PBPK. The threshold is a
heuristic, not a calibrated probability. Training graph membership is reported
explicitly; it must not be presented as unseen validation. Error metrics include
MAE, RMSE, rank correlation, multiplicative log-endpoint error and actual interval
coverage, both overall and by domain. Poor metrics remain visible.

## PBPK boundary and evidence precedence

Predictions extend the existing `PBPKParameter` and `CompoundDossier`, with model
hash/version, source dataset hashes, graph/request hashes, UTC timestamp,
interval, applicability, endpoint/species and translation receipts. Existing
measured/reviewed sourced evidence wins; conflicting predictions are retained.
Calculated/predicted evidence then precedes assumptions and missing values.

Only exact unit translations are automatic: log fraction to fraction, and molar
solubility to mg/L using graph-derived MW. AqSolDB does not establish solubility
at a particular pH. Neither human/rat renal clearance, blood/plasma ratio nor
incubation-unbound fraction is supplied by these selected models. Ionization is
not inferred from an LLM or absence of formal charge. Missing ionization evidence
is explicit; a neutral formal charge does not imply a neutral molecule at pH 7.4.

Hepatocyte intrinsic clearance requires validated IVIVE scaling, incubation
binding, blood/plasma conversion and hepatic physiology before a plasma-clearance
input is defensible. Consequently v1 retains it as an ADME endpoint, not an
invented native hepatic clearance. Caco-2 likewise requires a reviewed model
specific conversion. The sufficiency gate must return these exact blockers.
No total/hepatic/renal clearance double counting is introduced.

When predictions fill an otherwise evidence-complete species dossier, bounded
low/nominal/high parameter sensitivity scenarios run through real PK-Sim with
separate drug-parameter and physiological summaries. These are sensitivity
envelopes of calibrated marginal endpoint intervals, NOT a joint 90% PK interval;
cross-endpoint covariance and structural-model error are unknown. Each scenario
has distinct input/native hashes. Explicit bounded dose sweeps are scenario
comparisons, never therapeutic-dose recommendations.

## Alternatives evaluated but not numerical shortcuts

* ADMET-AI (MIT): Chemprop-based local CPU/GPU models offer useful endpoints,
  but DrugBank percentile ranks are not uncertainty intervals. Current v2 differs
  from the original v1 paper. Reproducible endpoint calibration and training-overlap
  review are required before adopting pretrained weights.
  [Official implementation](https://github.com/swansonk14/admet_ai).
* OpenADMET microsomal ChemEleon model (Apache-2.0): multispecies support is
  useful, but endpoints are already scaled in-vivo intrinsic clearance; the
  released model includes the referenced train AND test data. Empty std columns
  are not uncertainty estimates. It is not an independent hold-out oracle and
  cannot be interchanged with hepatocyte clearance.
  [Model card](https://huggingface.co/openadmet/microsomal-clearance-chemeleon-v1).
* Dimorphite-DL (Apache-2.0): empirical functional-group pKa ranges are valuable
  protonation heuristics, not calibrated molecule-specific micro-pKa values.
  Wide and overlapping ranges plus installed RDKit compatibility require separate
  validation. No unvalidated site mean is promoted to a native ionization input.
  [Primary project](https://github.com/durrantlab/dimorphite_dl).
* httk (GPL-2): published IVIVE workflows are useful references, but require
  measured/appropriate biological inputs and an R runtime. They do not make
  missing renal route or incubation binding disappear.
  [Official manual](https://stat.ethz.ch/CRAN/web/packages/httk/httk.pdf).
* CYP/P-gp binary classifiers do not supply continuous enzyme/transporter
  kinetics. No categorical output is turned into a fabricated PK-Sim rate.

## Deliberate limitations

No defensible numerical pKa, blood/plasma ratio, renal clearance, rat intrinsic
clearance, systemic-clearance translation, intestinal-permeability translation,
CYP kinetics or transporter kinetics is claimed. A graph alone may therefore
yield a useful partial ADME dossier but no accepted PBPK curve. This is an
explicit partial result, not complete novel-compound automation. No quantum,
pharmacodynamic, efficacy, clinical-dose or safety inference is introduced.
