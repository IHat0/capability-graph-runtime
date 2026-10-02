# Virtual Organism v1 — implementation and validation

## Outcome and scope

Pulsate now has a reusable native PK-Sim boundary integrated into its existing
ResearchSession → capability graph → execution → blocking verification → answer
→ visualization path. Real unrelated human and rat exposure experiments ran.
The real Qwen model interpreted the browser questions; native PK-Sim computed
the curves. Numerical statements come from verified artifacts, not an LLM.

This is an isolated Windows development milestone, **not an EC2 production
deployment, biological qualification, toxicity prediction or human-safety claim**.
Unknown compound ADME is not fabricated. A supplied/generated graph can enter
without a public identity record, but it still needs appropriate species ADME.

Branch remains `feat/pulsate-scientific-autonomy-v1`; HEAD remains
`38b2087a415ef7235fc5b167c9a46665c3eeb145`. Nothing was staged, committed or
pushed. The pre-existing `pyproject.toml` and `src/cgr/swebench/integration.py`
changes were preserved, as were local/private files and failed-run evidence.

## Engine, dependencies and deployment

See [engine decision](VIRTUAL_ORGANISM_ENGINE_DECISION.md) for the researched
PK-Sim/OSPSuite-R versus EPA httk choice, primary sources, licensing and Linux
requirements. PK-Sim supplies the native whole-body physiology builder and
CVODES solver; Pulsate does not substitute its own compartment ODEs.

Validated executable: **PKSim.CLI 12.3.173+7617934a187ed8d58e4e6ca0a7dac1cc40b59296**.
Its `4Comp` model has four subcompartments per organ, not four organs.
The portable Windows runtime used .NET Framework 4.8.1. Pinned hashes:

| Item | SHA-256 |
| --- | --- |
| Official portable archive | `94a704b67c05042131cd5e51b4237b1633466a08cd6ec77506322b4d009c8567` |
| PKSim.CLI.exe | `ca230754d2da0a150ff4a38265138916e978762370cdb155303500c90daafed1` |
| PKSimDB.sqlite | `aec61433ddddf39ec5c832bda90a6977a25ea5427e77edc08f2d97a751fe1469` |

Actual validation Python environment: Python 3.12.14, RDKit 2026.3.5,
Pydantic 2.13.4, NumPy 2.5.2, FastAPI 0.139.0, Uvicorn 0.51.0.
Browser interpretation used the existing running
`Qwen/Qwen2.5-Coder-7B-Instruct` service through a loopback SSH tunnel.
Its credential was captured only into process memory, never shown or saved.
Local API 8013 / frontend 5180 were isolated; production services were unchanged.

The engine and reviewed catalogue must be configured explicitly with
`PULSATE_PBPK_EXECUTABLE` and `PULSATE_PBPK_CATALOG_FILE`. Binaries, source model
data and acceptance evidence remain untracked local files, not bundled product
dependencies. No dependency change to `pyproject.toml` was made by this batch.
PK-Sim/OSPSuite-R GPLv2 notices and licence review matter before distribution.
The current EC2 host lacked the supported R/.NET runtime; Linux deployment was
not validated or silently installed. This adapter pins the Windows CLI version.

## Reusable workflow

The actual composed capability sequence is:

1. `pharmacokinetics.parameterize`
2. `physiology.organism_construct`
3. `pharmacokinetics.pbpk_simulate`
4. `pharmacokinetics.population_simulate`
5. `pharmacokinetics.cross_species_compare`
6. `pharmacokinetics.exposure_verify`
7. `discovery.exposure_relevance_assess`
8. `scientist.result_assemble`

The native solver performs individual or population integration. The population
capability aggregates the actual native subject curves; it is not a second
invented solver. The comparison capability compares independently built species
models. Single-species experiments retain this reusable composition but do not
claim a cross-species calculation occurred.

Scientific controls are extracted with a replaceable model, then checked against
literal scientist quotes. Missing dose/units/route/time becomes a resumable
clarification, not a default therapeutic dose. Follow-up controls are re-extracted
from scientist turns only, not generated prompts. The same-session missing-dose
browser test proved the reply updates the accepted PBPK contract and executes.

PBPK selects classical computation with an explicit receipt reason: mechanistic
ODE exposure simulation does not request an electronic/quantum subproblem.
Existing Qiskit/IBM infrastructure is unchanged; no quantum job was submitted.

## Physiology and dosing

Human reference physiology comes from the reviewed native individual or the
native healthy adult European ICRP building block. It is not a patient-specific
model. Human populations are bounded to 32 adults, with explicit age range,
sex proportion and deterministic seed. The default disclosed sampling policy is
ages 20–50, 50% female and seed 29. Native sampled age, sex, weight, height and
physiology parameters—including organ volumes/flows and model biological
variation—are persisted. Drug-parameter uncertainty is **not** sampled.

The six-person acceptance population contained three males and three females,
ages 23.616–46.297 years, weights 58.656–80.124 kg. It is actual PK-Sim population
output, not six rescaled copies of a fixed 70 kg male.

Rat models are independently built from native Rat physiology and species ADME,
not obtained by renaming a human PKML. Initial rat physiology uses UNKNOWN sex;
sex/age-dependent maturation and rat population sampling are not qualified.
Requests for rat population sampling are not silently reduced to one rat.

Supported explicit scenarios: IV bolus, positive-duration IV infusion, oral
solution, mg or mg/kg, single administration or up to 32 explicitly listed
administration times. Simulation is bounded to 720 h. Observation horizon is
distinct from infusion duration. Other formulations, dose escalation schedules
and disease/genotype physiology need additional scientific contracts.

## Identity, parameterization and provenance

Production logic has no compound-name, public-CID or expected-energy/PK branches.
Reviewed catalogue data are selected by full InChIKey and species, with confined
paths, source attribution and input hashes. Public lookup obtains identity graph
data, not finished 3D coordinates or invented ADME. Supplied graph/dossier inputs
take precedence and are independently identity checked with RDKit.

General input types added to the existing CLI/API/UI are `pbpk_compound_dossier`
and `quantitative_activity_evidence`. `CompoundDossier`, `PBPKParameter`,
`PBPKRequest` and `QuantitativeActivity` define the JSON contracts in
`src/cgr/pulsate_api/virtual_organism.py`; their Pydantic JSON schemas are available
programmatically. This is the route for a new/generated molecule with reviewed
parameters, not substitution of a familiar reference drug.

Dossier controls include MW, logP, explicit pKa/neutral-state evidence, solubility
and reference pH, unbound plasma fraction, binding partner, species plasma
hepatic/renal clearance and reference experimental body weight. Oral dossiers
also require intestinal permeability. Each parameter has value, units,
measured/sourced/calculated/estimated/assumed/missing class, source, method and
uncertainty where available. Salts/mixtures require an explicit active-moiety and
dose-basis model; they are not treated as one interchangeable neutral graph.

Native reference snapshots can retain their reviewed enzyme/renal processes and
partition methods. Raw upstream origin records are preserved. A sourced or
manually fitted parameter is not relabeled as a new measurement. Uncharacterized
upstream values remain disclosed assumptions. Native partition/permeability and
blood-cell binding methods are model predictions, not measured tissue exposure.

Validation data (not production routing) used:

- Human caffeine: pinned OSP Example_Caffeine commit
  `9ee0907dd430fdbd3ff9e817e8b7e2ba1030438e`, native CYP1A2/renal model.
- Human theophylline: pinned OSP Example_Theophylline commit
  `3c2e45084661484dacbd4cbb6f531b14f4b76de6`, including upstream manually fitted
  CYP1A2 process. This is reference replay, not independent clinical prediction.
- Rat caffeine and acetanilide: measured binding/total-clearance/reference-weight
  inputs from [Matsunaga et al. (2001)](https://www.jstage.jst.go.jp/article/bpb/24/9/24_9_1037/_pdf).
  Assigning total body clearance to a lumped hepatic process is an explicitly
  estimated approximation; zero renal contribution is an explicit assumption.
  Albumin-only binding and native passive distribution are also approximations.
  Total clearance is an input, not a newly predicted validation endpoint.

The generated-graph case used acetanilide graph identity under an arbitrary
research label, with fresh RDKit coordinates and its reviewed rat dossier. It
proves graph-based entry without public name lookup; it does **not** prove that a
novel molecule's unknown ADME can be inferred automatically.

## Outputs and integrity verification

Time-series units are hours and µmol/L. Actual native outputs cover peripheral
venous plasma, unbound plasma and whole blood, plus model-supported total tissue,
interstitial-unbound and intracellular-unbound concentrations. The validated
organ set includes liver, kidney, brain, heart, fat, muscle, bone, gonads, stomach,
small/large intestine, lung, pancreas, skin and spleen.

Reported metrics are sampled Cmax/Tmax, trapezoidal AUC 0–t, total-tissue/plasma
AUC ratios, and empirical subject medians/5–95% ranges. Terminal half-life,
systemic clearance and bioavailability are left null rather than inferred from a
finite sampled window. A sampled bolus maximum is not instantaneous Cmax.

Verification independently checks graph identity; candidate/species coverage;
source/input hashes; reconstructed scenario snapshots; actual native PKML
species, dose, route, administration times, infusion duration, MW and active
clearance processes/units; native sampled physiology; raw-file hashes;
time coverage; finite nonnegative concentrations; required organs; positive
plasma exposure after positive dose; and recomputed PK/population/comparison
metrics. Builder configuration alone is not proof a clearance process executed.
Failed/warned/incomplete/timeout native runs retain refusal receipts and partial
evidence and are never accepted as exposure curves. Exit code 0 alone is not
success. Accepted curves and sampled physiology are finite; unused native
auxiliary enzyme properties may contain inactive NaN entries in PKML, which are
not accepted concentration output or guessed drug parameters.

Activity evidence is hash/identity/unit checked before assessment. Comparisons
require a sourced Ki/Kd/IC50/EC50, species match and exact unbound compartment
path. Peak exposure/activity ratios are **prioritization hypotheses only**,
not occupancy, functional effect or toxicity. Docking scores and total tissue
concentrations are not converted into potency. Real browser cases had no
compatible quantitative assay and correctly reported insufficient mechanism
evidence. Activity-interface tests use explicitly synthetic unit fixtures, not
invented scientific acceptance results.

## Real numerical and browser evidence

Nine accepted native cases cover human reference/population/oral/repeated dose,
second human compound, rat reference/doubled dose/comparison and second rat
compound. All computed values are retained under
`.test-runs/virtual-organism-research-20261001/native-validation-r4`.

| Scenario | Sampled plasma Cmax (µmol/L) | Tmax (h) | AUC 0–t (µmol·h/L) |
| --- | ---: | ---: | ---: |
| Human caffeine, 3 mg/kg over 10 min, 16 h | 51.27663 | 0.2 | 153.238144125 |
| Rat caffeine, same administration, 16 h | 27.99545 | 0.2 | 67.9055864225 |
| Human caffeine, oral solution 250 mg, 24 h | 29.66875 | 0.65 | 176.110880955 |
| Human caffeine, 3 mg/kg at 0/4/8 h, 16 h | 76.69094 | 8.2 | 501.2315555 |
| Human theophylline, IV bolus 100 mg, 12 h | 27.57905 | 0.05 | 114.2044144 |
| Rat caffeine, IV bolus 20 mg/kg, 12 h | 233.6162 | 0.05 | 438.24043015 |
| Rat acetanilide, IV bolus 20 mg/kg, 8 h | 146.3498 | 0.1 | 252.053904125 |

Rat dose doubling reproduced the expected scaling within maximum absolute
rounding/solver difference 0.00156 µmol/L. Repeated native runs reproduced the
human individual, seeded population and two rat curve arrays exactly. Complete
file bytes need not match because exported PKML object GUIDs can differ.

Six real browser acceptance cases completed with verified answers, hash-checked
downloads and saved-session reopening:

| Browser case | ResearchSession ID |
| --- | --- |
| Six-person human population | `research-session-a31b9bb221b338c9ab0ea3187c7de95d` |
| Independently built human/rat comparison | `research-session-83ae6e5bac7816e0423e068037405ac1` |
| Missing dose → reply → same-session computation | `research-session-0b03d666b3581297c9112d2aef8e1d6e` |
| Supplied/generated graph plus rat ADME dossier | `research-session-b49e756284a1346104b0449dbb8b7347` |
| Second human compound | `research-session-46ec17c8b5a24752ed06587f4159f70e` |
| Missing critical ADME: honest insufficient parameterization | `research-session-623f78c7ca691710f87f50127a98c925` |

Exact questions, actual Pulsate answers, workflow steps, session/assessment
hashes and 104 downloaded-artifact checks are in
`.test-runs/virtual-organism-research-20261001/final-evidence-manifest.json`.
The manifest independently hashes 149 evidence files; SHA-256:
`fdc41ebda7c0f05623197c0f63babd7fb5847bd4a1280f4b08d1615bc24581db`.
Failed earlier runs are preserved and are not counted as acceptance.

For example, the actual human/rat question was:

> Compare plasma and organ exposure of caffeine in a human and a rat after a single intravenous dose of 3 mg/kg infused over 10 minutes, over 16 hours. Use one individual of each species.

Pulsate's verified principal result was:

> Virtual Organism state: exposure supported. Human subject 0: sampled plasma Cmax 51.2766 umol/l at 0.2 h; finite-window AUC 153.238 umol*h/l. No terminal extrapolation. Rat subject 0: sampled plasma Cmax 27.9955 umol/l at 0.2 h; finite-window AUC 67.9056 umol*h/l. No terminal extrapolation.

The six-person population produced Cmax 49.5464–53.42606 µmol/L and AUC
92.2200–292.4758 µmol·h/L. This variation is under the stated native physiology
sampling policy, not a clinical confidence interval or sampled ADME uncertainty.
Missing human acetone parameterization returned no accepted prediction and no
invented chart; the UI allows attachment of a reviewed dossier and continuation.

## Observed comparisons: discrepancies retained, no tuning

Caffeine oral-solution 250 mg replay versus 12 upstream transcribed Cysneiros
observations had RMSE **0.8969049629 mg/L**, mean bias **−0.2825553 mg/L**.
Baseline, exact formulation and study cohort were not matched. This is not an
independent out-of-sample model qualification. Primary study:
[Cysneiros et al. (2007)](https://pubmed.ncbi.nlm.nih.gov/17443132/).

The initial theophylline 100 mg bolus comparison did not match the observations'
208 mg / 4-minute infusion source protocol and is not valid predictive evidence.
That failed comparison remains preserved. A separate, unchanged source-declared
`Kaumeier IV 208 mg fit` replay used the explicitly linked protocol/observations:
seven participant-series RMSEs **1.32524–2.68369 mg/L**, with native Cmax
54.51107 µmol/L and 24-hour AUC 300.506840375 µmol·h/L. The source enzyme process
was already fitted; no parameters were changed or fitted by this batch.
Evidence is in `observation-comparison-r2`, not an overwrite of r1.

Rat finite-window clearance × MRT diagnostics differed from reported Vss by
−18.36% (caffeine) and +35.32% (acetanilide). These are diagnostics, not validated
Vss predictions: terminal tails, coarse early bolus sampling, lumped clearance,
native passive partition assumptions and UNKNOWN-sex physiology matter.
Observed clearance was an input, not a successfully predicted endpoint.

## UI and regressions

The existing Research Workspace now prominently shows a Virtual Organism result
without replacing the molecule viewer. It offers Human/Rat selection, a native
compartment selector, actual concentration curves and empirical population
bands, PK/organ ratios, source/classification tables, assumptions, mechanism
limits, raw curves, sampled physiology and complete assessment downloads.
Reloading reopens the same persisted result and chart.

Screenshots are preserved in each browser directory, including
`browser-population-final/.../virtual-organism.png` and
`browser-cross-species-final/.../virtual-organism.png`; both were visually inspected.
No decorative organ detail or SAFE label substitutes for numerical evidence.

Final cumulative Python regressions: **182 passed, 6 skipped**. Skips were
optional local numerical dependencies: PySCF (3), OpenMM (1), Meeko (1), Qiskit
(1). They are not claimed as successful real numerical reruns. Preserved
construction, discovery, prospective-risk, security/tenant-session, capability
truth, compute routing and quantum contract coverage passed where available.
Frontend: **187 passed across 23 files**; TypeScript and production build passed.
The existing large Mol* bundle warning remains. `git diff --check` passed.
Anti-overfit tests scan new production modules for validation compound names,
held-out names/identifiers/hash and candidate-specific routing.

## Source files and remaining boundaries

New: `virtual_organism.py`, `virtual_organism_handlers.py`,
`VirtualOrganismResult.tsx`/its unit test, `virtual-organism-live.spec.ts`,
`test_virtual_organism.py`, engine decision and this report.

Modified integration: `scientific_requirements.py`, `scientific_objectives.py`,
`research_sessions.py`, `scientific_capability_catalogue.py`,
`scientific_production.py`, `scientific_runtime.py`, `research_visualization.py`,
`scientific_cli.py`, frontend API types/client, `ResearchWorkspace.tsx`, and
`test_research_sessions.py`. Runtime sources/evidence stay untracked.

Not established: universal automatic ADME acquisition, clinical dose selection,
patient/disease/genotype-specific predictions, transporter qualification,
drug-parameter uncertainty sampling, rat population/sex/age qualification,
all formulations, mixture/salt dose semantics, clinical potency, functional
engagement, physiological consequences or human safety. The demonstrated rat
models are explicitly exploratory; the human examples are reference replays.

Before future QSP/QST coupling, qualified exposure models and uncertainty,
compatible quantitative target activity, assay/free-concentration reconciliation,
functional mechanism/effect models and separate biological validation are needed.
Persisted unbound/total tissue time courses, species/population context, scenario,
parameter provenance and assay references are the interface—not toxicity claims.
No QSP/QST subsystem was added during this milestone.

Both frozen Demo 2 results were read-only hash checked, not rerun, reinterpreted
or overwritten. V1 remains
`0ea2dc54761c4eb81f82661c5739c5a3e3219dc3ea22df44fed09da087af3a10`;
v2 remains
`2837ca98dc688f7af5858b6ba8785a13c67ce460022ad4d0d3862d22915cd140`.
No held-out historical outcome search or post-result tuning occurred. Stop here;
the Virtual Organism batch remains uncommitted and unpushed for review.
