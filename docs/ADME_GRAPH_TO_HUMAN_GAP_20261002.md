# Graph-only to Virtual Human: verified boundary, not completed automation

2026-10-02. Branch `feat/pulsate-scientific-autonomy-v1`; HEAD remains
`1b114f2b156bbafef507ab21c4d37940ad6641c9`. Continued ADME changes are
uncommitted and unstaged. No production service changes, cloud startup, IBM
submission, retrospective prohibited-candidate investigation, or new QSP work.

## Outcome: stop condition B

**Pulsate cannot yet autonomously produce a defensible Human PK curve from an
unseen generated graph alone.** Five newly selected, training-unseen graphs
completed real Qwen/browser ResearchSessions, numerical endpoint inference,
blocking replay verification, evidence export and saved-session reopening.
All five correctly returned `insufficient_parameterization`; none ran PK-Sim.

This is evidence about the available implementation, not a claim that graph-only
PBPK is fundamentally impossible or that all open predictors are inadequate.
No success was manufactured by supplying a complete dossier after prediction.
No new pKa or clearance model was promoted without calibration/qualification.

The decisive evidence is narrower than a general statement about ML quality:

* The actual AqSolDB training download has columns `Drug_ID, Drug, Y`, not
  intrinsic/apparent classification or reference pH. A mass-unit conversion
  cannot recover these missing conditions. Supplying an unrelated pH afterward
  does not make that prediction pH-specific.
* The actual human hepatocyte download has columns `ID, X, Y`, not incubation
  binding, incubation conditions, or a qualified unbound-clearance contract.
  PK-Sim's actual hepatocyte scaling equations explicitly include assay binding.
  Apparent cell-normalized CLint is not whole-organ plasma clearance.
* The frozen CLint model is not rescued by structural similarity: held-out rank
  correlation is 0.172 overall and 0.296 in its nominal in-domain subset.
* No calibrated, site-qualified ionization service or qualified total-renal
  model is configured. Native physiology alone does not supply these drug facts.

Reproducible evidence: `.test-runs/graph-human-gap-20261002/native-endpoint-audit.json`
contains database formulas, input aliases, dimensions, dataset headers/hashes,
and all endpoint validation metrics. `research/source-manifest.json` preserves
revision-pinned primary-source text and hashes; no downloaded code was executed.

## Actual repairs

1. Mixed/unknown-pH aqueous-solubility predictions remain numerical ADME evidence
   but **no longer populate native solubility**. Provenance identifies the
   heterogeneous quantity, unknown reference pH, lack of intrinsic-solubility
   evidence and native ineligibility. Measured/reviewed native solubility remains
   authoritative. A separately provided pH cannot launder the mixed endpoint.
2. Native predicted-parameter gating checks endpoint meaning as well as units
   and domain: apparent CLint cannot be renamed plasma hepatic clearance,
   filtration cannot be renamed total renal clearance, and mixed-condition logS
   cannot be renamed known-pH solubility.
3. The dossier's experimental normalization weight is **not assigned to virtual
   subject `OriginData.Weight`**. It remains in native clearance processes where
   the experimental `ml/min/kg` endpoint needs it. Graphs without such endpoints
   no longer ask for an arbitrary weight as a missing drug property.
4. Unsupported/nonfinite/nonnumeric ionization inputs fail closed. The declared
   aqueous adapter range is -10 through 25 pKa units; rejection outside this range
   is a supported-domain restriction, not a claim that other pKa values cannot
   exist. Dimorphite sentinel values are not accepted as numerical pKa.
5. Every new prediction includes a replay-verified native translation audit:
   endpoint evidence, unresolved dependencies, conditional equations, clearance
   basis, renal-mechanism scope and physiology/reference-weight distinction.
   The existing UI shows this trace. Unexecuted equations are explicitly labelled
   unexecuted, not presented as successful numerical conversion receipts.

Numerical model version remains `pulsate.adme-rf/v1`; the changed boundary policy
is separately pinned as `pulsate.native-readiness/2` in the prediction request hash.
Older saved sessions remain readable. Old numerical artifacts remain historical;
they are not silently rewritten under the new boundary policy.

## pKa / ionization research

No new numerical pKa predictor is installed or claimed validated. The strongest
candidate cannot be established by a repository README or paper RMSE alone.
The following primary implementations were inspected and revision-pinned:

| Approach | Actual contract and limitations | License |
| --- | --- | --- |
| QupKake, `f7a294e91929e4e4f0a7100ad5cf5a64a34a7d4f` | Acid/basic site models plus micro-pKa regression with GFN2-xTB features. Requires PyTorch/Geometric/Lightning and xTB. Inspected output is site-index/type and a point pKa; it does not itself supply this product's calibrated error intervals, frozen applicability domain or macrostate ensemble qualification. | BSD-3-Clause |
| pKaSolver, `a6ec86e0337474efc9a6797b5a6374e6961a2748` | Public lite weights exclude the licensed Epik transfer step. The authors limit lite to monoprotic use. Code reports mean and standard deviation of 25 networks; that spread is not independently calibrated predictive coverage. | MIT |
| Uni-pKa, `7cfbf6532d7e14f427abaed5859bd1001b4b6377` | Microstate enumeration and thermodynamic free-energy aggregation can support macro-pKa. Templates, state completeness, weights/data and the older Uni-Mol environment require qualification; no site/domain/calibrated adapter exists here. | Apache-2.0 |
| MolGpKa, `4dc835219baad18c2e705274ac54d49597703966` | Separate acidic/basic GNN models and public weights; older dependency environment. No frozen calibrated product-domain adapter established here. | MIT |
| Dimorphite-DL | Functional-group protonation rules and group pKa ranges, not a calibrated molecule-specific micro/macro predictor. Wide ranges and enumeration heuristics must not be relabelled calibrated numerical evidence. | Apache-2.0 |

[QupKake primary project](https://github.com/Shualdon/QupKake),
[pKaSolver authors' limitations](https://github.com/mayrf/pkasolver),
[Uni-pKa primary project](https://github.com/dptech-corp/Uni-pKa),
[MolGpKa primary project](https://github.com/xundrug/molgpka),
[Dimorphite-DL primary project](https://github.com/durrantlab/dimorphite_dl).

Micro-pKa values attach to particular protonation transitions/sites; macro-pKa
values aggregate microstates. Treating independent site predictions as a complete
polyprotic/zwitterionic speciation ensemble would require validation not performed
here. A neutral formal charge does not prove absence of ionization. Predicted
site/value/interval/model/domain fields therefore remain unavailable, rather than
being populated with group averages. Unsupported ionization includes unqualified
polyprotic, ampholytic, tautomer-coupled and zwitterionic cases.

## Solubility, blood/plasma, IVIVE and renal mechanisms

For a qualified ideal monoprotic system with known intrinsic solubility S0,
the conditional equations are `S = S0*(1+10^(pH-pKa))` for an acid and
`S = S0*(1+10^(pKa-pH))` for a base. They do not establish S0, reference pH,
solid state, salt precipitation, temperature or ionic strength. They were **not
executed** for the held-outs. Neither measured logD at 7.4 nor a pKa point
prediction repairs an unknown-pH training endpoint by itself.

The actual PK-Sim 12.3.173 database has a mechanistic RBC partition formula:

```text
B/P = 1 - Hct + Hct*fu_plasma*(f_water_RBC + f_lipid_RBC*10^logP + f_protein_RBC*KProt)
```

Thus B/P is not intrinsically a missing capability of PK-Sim. Its required RBC
composition, hematocrit, species-matched binding and empirical protein-partition
method must be audited in the actual subject/model. The native Standard equation
is not a calibrated universal RBC predictor, and has no explicit independent pKa
term; ionization/tissue-partitioning assumptions need qualification. No B/P=1
substitution or fake graph-only B/P value was introduced.

The initial **conditional**, not yet automatically qualified, hepatic contract is
linear well-stirred equilibrium IVIVE:

```text
CLint_u [mL/min] = CLint_apparent [µL/min/10^6 cells]
                  * hepatocellularity [10^6 cells/g]
                  * liver_mass [g] / (1000 * fu_inc)
fu_blood = fu_plasma / (B/P)
CL_blood [mL/min] = Q_blood * fu_blood * CLint_u / (Q_blood + fu_blood * CLint_u)
CL_plasma [mL/min] = CL_blood * (B/P)
```

The 1000 factor is µL-to-mL, not µL-to-L. The basis conversion follows the same
elimination rate expressed against different reference concentrations. Native
hepatic plus portal blood flow, liver size and hepatocellularity must come from
the relevant subject/species with documented units. Apparent assay clearance
must not be declared unbound without `fu_inc` or justified assay evidence.
Assay transport limitations also need review. No fixed 70 kg person, default
incubation binding, or subject-invariant whole-organ CL was manufactured.

Parallel-tube assumptions instead yield `Q*(1-exp(-fu_blood*CLint_u/Q))`;
dispersion approaches require additional mixing/dispersion assumptions. Neither
alternative supplies missing binding/scaling evidence. No alternative was selected
to force execution. PK-Sim already supports intrinsic first-order/hepatocyte
processes and plasma-clearance inversion. Its native database equations and input
aliases demonstrate the need for assay binding, liver cellular scaling and
blood/plasma basis. A native process must not be added alongside an already
representative lumped clearance and counted twice.
[Official PK-Sim compound/process documentation](https://docs.open-systems-pharmacology.org/working-with-pk-sim/pk-sim-documentation/pk-sim-compounds-definition-and-work-flow).

PK-Sim also supports a native glomerular-filtration process. Its rate uses
species/subject kidney physiology, GFR fraction, plasma concentration and fu.
The mechanistic component is `CL_filtration_plasma = fu_plasma * GFR`; it is
**not total renal clearance**. Active secretion, active reabsorption and passive
reabsorption remain unqualified for the graphs. A GFR-only exploratory model can
be represented natively, but no defensible compound-domain qualification was
established here; no arbitrary total-renal zero or hidden surrogate GFR fraction
was used. Human CLint is never transferred to Rat. Rat graph-only remains partial.

## Clearance model quality and alternatives

The original six endpoint models remain byte-identical, not refitted after outcomes.
Human hepatocyte CLint model hash:
`c28681a72c500daa6c1f6b09061338e18b1885ecde674ff90681a11a87e7fb35`.

| Frozen scaffold-held-out subset | n | MAE log10 | RMSE log10 | Median fold error | Spearman | Observed marginal coverage |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Overall | 138 | 0.337567 | 0.417616 | 1.92347 | 0.172280 | 80.43% |
| Nominal in-domain | 93 | 0.298414 | 0.374321 | 1.73897 | 0.296144 | 86.02% |

These are endpoint errors against experimental CLint labels, not plasma PK errors.
Nominal intervals target 90% marginal exchangeable coverage; neither subset
achieves that target under the scaffold-held-out evaluation. Similarity cannot
be treated as validation of clinical clearance.

OpenADMET's CheMeleon release is a concrete stronger-model candidate: multitask
microsomal training combines ChEMBL, ASAP and ExpansionRx data. However its
card specifies scaled in-vivo intrinsic endpoints rather than this unscaled
hepatocyte assay endpoint, includes both ExpansionRx train and test in released
weights, and states std columns are empty without an ensemble. The authors'
analogous benchmark model is not identical to those released weights. Adopting
it would require endpoint-specific curation, training-overlap review and independent
calibration; no stronger local comparative result was established in this task.
It was inspected, **not run or claimed superior on our held-outs**.
[Primary model card](https://huggingface.co/openadmet/microsomal-clearance-chemeleon-v1).

ADMET-AI/Chemprop and alternative descriptors/pretraining are available approaches,
not replacements for assay definition or fresh independent evaluation. No new
trained model, self-reported ensemble spread, or classifier-derived rate was
promoted as qualified native clearance. Existing per-graph experimental errors,
species-specific models and censoring limitations remain preserved.

## Frozen held-out protocol and real outcomes

The model manifest SHA-256 is unchanged:
`90d9bacd8c35d408f44a146addfb15222ce589b88b2ee73eb6a7d47b13d2dc0d`.
The pre-selection scientific/model/equation/domain/sufficiency freeze hashes
18 files, including all model artifacts and the numerical validation report:
`3bd43c694330442706cba7efdfaedf8d3192578c113861daf10e77188cbe1b38`.
All frozen hashes still matched after the investigations. Production source was
not tuned after outcomes. A Vite test-harness module-resolution fault was repaired
before the first form submission; no scientific investigation was restarted.

After this freeze, five valid organic graphs were selected by chemistry category
and absence from **all** endpoint training graph hashes. No endpoint predictions
were inspected during selection. `selected-graphs.json` preserves canonical
SMILES, InChIKeys, graph/input hashes and the novelty check. Numerical inference
and its deterministic verification replay belong to one investigation, not two
tuned experiments. The exact question for each was:

> Using the supplied [case name] molecular graph, automatically predict its ADME and assess whether exploratory Human PBPK at 1 mg/kg intravenous bolus over 8 h is supported. Do not invent missing biological inputs.

| Case / stress | Exact ResearchSession | Result / native runs |
| --- | --- | --- |
| GH1 / neutral organic graph | `research-session-08dc28fc6988fa9792ca233dfabfb2a4` | insufficient / 0 |
| GH2 / multiple basic sites | `research-session-7547b2050e3ad46ff24faa59bf428499` | insufficient / 0 |
| GH3 / acid/base ampholyte | `research-session-b7f2b26a9a30d070c3c931637c065090` | insufficient / 0 |
| GH4 / ester/ether clearance | `research-session-6c228cf483d55ee9c22127c21410f1e0` | insufficient / 0 |
| GH5 / size/lipophilicity domain | `research-session-b3de7625a8cd392574849a43d8b33345` | insufficient / 0 |

All received a useful verified partial ADME answer, not an invented exposure plot.
Each produced five actual numerical endpoint predictions with model/data hashes,
intervals and domain flags. Every training-membership flag was false. Plasma fu
was weakly supported or out-of-domain for all five. GH2 CLint was in-domain, but
this did not bypass the missing assay-to-organ evidence. Ionization, known-pH
solubility and hepatic/renal native quantities remained unresolved. No arbitrary
reference-weight request remained in the graph-only refusal.

Actual computed points (intervals and full provenance are in exported evidence):

| Case | Human fu | Aqueous mixed-condition solubility mg/L | Human apparent CLint log10(µL/min/10^6 cells) |
| --- | ---: | ---: | ---: |
| GH1 | 0.130335 | 268.786899 | 1.393947 |
| GH2 | 0.304785 | 2726.149362 | 1.317927 |
| GH3 | 0.320505 | 1687.342622 | 1.105754 |
| GH4 | 0.164696 | 286.926186 | 1.356540 |
| GH5 | 0.018989 | 0.025425 | 1.333421 |

These out/weak-domain points are not accepted PBPK inputs. No observed clinical
PK values were used to complete their dossiers. **Graph-only predicted-vs-observed
PK discrepancies are not computable:** no accepted graph-only native curve exists.
Claiming an accumulated PK error or a successful known-compound prediction-only
validation here would be false.

## Separate native regression and physiology evidence

One additional browser session used the supplied reference graph and explicitly
available reviewed species-specific caffeine models, not prediction-only ADME:
`research-session-8409f6bd02f9b516b98141a0aebfd110`.
Request: Human and Rat, 3 mg/kg IV infusion over 10 minutes, 16 h observation.
Real PK-Sim ran twice, native reaction/transport networks and raw curves were
verified, plasma/liver visuals displayed, all evidence downloaded and reopening
passed. The native executable/database stayed hash-pinned to 12.3.173.

| Reviewed native regression | Sampled Cmax µmol/L | Tmax h | AUC0–16 µmol·h/L |
| --- | ---: | ---: | ---: |
| Human | 51.276630 | 0.2 | 153.238144125 |
| Rat | 28.116170 | 0.2 | 67.908525605 |

This is not validation against observed PK and does not satisfy graph-only
acceptance. Reviewed native model inputs/measurements took precedence over new
ADME predictions. Native output records Human subject weight ~73 kg and Rat
subject weight 0.227777 kg; the Rat clearance-process experimental normalization
weight remains 0.199 kg. This directly demonstrates the corrected distinction.
The Human reference model was unchanged. The Rat output need not reproduce an
older forced-reference-weight subject and is not relabelled a biological improvement.

## Uncertainty, verification, UI and preserved evidence

No new joint ensemble is claimed. Existing one-at-a-time marginal endpoint
sensitivity remains separate from physiological population variability and
explicit dose/scenario comparisons. Missing native translations prevent a
meaningful graph-only PK parameter ensemble. Unknown cross-endpoint dependence
and structural-model error are not relabelled a joint 90% interval.

The workflow remains:

```text
pharmacokinetics.adme_predict -> adme_verify -> parameterize
-> physiology.organism_construct -> pbpk_simulate -> population_simulate
-> cross_species_compare -> exposure_verify -> discovery.exposure_relevance_assess
-> scientist.result_assemble
```

Unsupported graph parameterizations produce no native models/runs. Classical
compute selection remains explicit; quantum infrastructure is unchanged.

Tests cover 1000-fold/log-unit corruption, µL/min/million cells or L/h disguised
as ml/min/kg, plasma/blood basis substitution in receipts, species substitution,
invalid fu and pKa, missing reference pH, unsupported ionization, mixed-solubility
relabeling, total/route clearance double counting, source precedence, molecular
identity, model hashes, native physiological weight and translated-audit replay.
Synthetic contract fixtures are **not** scientific accuracy evidence.

* Python product/regression tests: **221 passed**.
* Frontend: **188 passed**, 23 files; TypeScript and production build passed.
  The existing large visualization-bundle warning remains.
* Real browser investigations: **6 passed** (five new partial cases, one reviewed
  native regression); read-only test branch skipped once per invocation.
* **83 exported artifact hashes** independently matched across the six sessions.
* Source/model/policy freeze: **18/18 hashes unchanged** after held-out runs.

Evidence root: `.test-runs/graph-human-gap-20261002/`.
`verified-evidence-summary.json` preserves endpoint points/intervals/model hashes,
translation audits, exact sessions, native physiology records, raw PK summaries
and final scientist answers. Each browser directory has session/visualization
JSON, export manifests/artifacts and actual/reopened screenshots.
`heldout-GH2-human-translation-trace.png` shows the real graph-only UI boundary;
`browser-reviewed-regression-native-plasma-curve.png` shows actual native Human
output. These are read-only captures of persisted sessions, not mock screens.

No files were committed, pushed, broadly staged, discarded or cleaned. Unrelated
`pyproject.toml` and `src/cgr/swebench/integration.py` modifications retain their
pre-task hashes. Prior blind results and prior evidence roots were not rewritten.

**Bottom line:** the verified graph-to-ADME partial result and honest native
boundary are stronger; the generated-molecule-to-Virtual-Human milestone remains
incomplete. Current endpoint definitions and qualification do not justify closing
it by assembling default biological values. Stop condition B applies.
