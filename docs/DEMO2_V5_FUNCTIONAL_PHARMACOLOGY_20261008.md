# Demo 2 v5 — general functional pharmacology

## Status and scope

Final unrelated computational acceptance passed on 2026-10-09 on
`feat/pulsate-scientific-autonomy-v1`, baseline
`039289bc49a313f2248be3c37966d6cd984e87a1`. The completed session was recovered
by hash verification, bounded representation and answer reassembly, not by
repeating science. This is research-grade software acceptance, not independent
biological, clinical or safety qualification. See the final recovery section.

The held-out v4 session is untouched. Its frozen result remains
`e118059494a5b9fcb13950529933a068be9bcc19ca5314029ef609105e048e61`.
No held-out functional query, scientific execution, dossier change or historical
reveal is authorized. The 2026-10-09 final-acceptance/storage-repair instructions
authorize a selective engine commit/push after saved-session/browser acceptance
passes. They supersede the historical commit prohibition in the unchanged
2026-10-08 protocol. Earlier progress/pending statements below are preserved as
historical validation notes, superseded by the final recovery section.

## Candidate-independent universe and source policy

The frozen universe unions all enumerated targets from the March 2026 Reaction
Biology general InVEST/PDE/CYP panels and the independently preserved legacy
41-row general safety panel. It has 103 unique reviewed Human protein identities;
seven ambiguous source rows remain visibly unsupported. An unspecified receptor
complex, subunit combination or conflicting panel label is not silently mapped
to one gene.

- Universe SHA-256: `52bb4b6270b5457d96f738f49fc97ff38043d027976451a525ee963fb8383f08`.
- Reaction Biology primary PDF SHA-256: `6585b5f7487cee27d6ed5841c7ad879f3ef201299fee2173707d4531e2ca5a1d`.
- Original protocol bytes SHA-256: `5f5390e5ad275156e7b377ddb3c6f79d1d9274b8f229caadcab1481b9bec3c87`.
- Offline identity-denial dossier SHA-256: `c14389593bff5c98d2026eb35e88c61042a02f5f17ffacfece5aef8316607e96`.
- General Human tissue source package SHA-256: `7403b35ad468b65b062f0f2102162b21250b38ac1ab5550ace54682f1a718fb0`.
- Complete 103-target acquisition r5 manifest SHA-256: `9d6376e7000e95d38be4371b957b11c217c3dc63b8721a8f19bb82c36c5e3ccd`.

The bounded acquisition contains 62,839 admitted assay-record entries across
94 acquired target datasets. Nine other target datasets have insufficient
functional evidence. Counts are not unique compounds across endpoints.

All source files and rendered-page reviews remain in
`.test-runs/demo2-v5-functional-20261008/`. General annotations retain their
exact target, source and hash. Expression is not converted into potency, tissue
exposure, a physiological parameter or a toxicity finding.

## Functional modeling and independent replay

Direct confidence-9 single Human protein assays only. Functional IC50 and EC50
are separate target/action/readout/concentration-basis/variant strata. Binding
Ki/Kd, AC50 and percent endpoints are not pooled into functional potency.
Unknown direction, multiple readouts and undeclared target variants refuse.
An explicitly described challenge agonist does not turn a tested antagonist
into an agonist.

The fixed baseline uses Morgan radius-2 512-bit fingerprints, eight RDKit
descriptors and a 64-tree forest (depth 12, leaf 3, feature fraction 0.7,
seed 20261008). Graph-deduplicated scaffold hash partitions are 70/15/15.
Minimum independent train/calibration/test sizes are 100/30/30.
Intervals are 90% marginal split-conformal assay intervals, not guaranteed
conditional-domain coverage or joint exposure/activity confidence intervals.

Every gate must pass: in-domain n >= 30, interval coverage >= 0.8, MAE <= 0.6,
RMSE <= 0.9, Spearman >= 0.5 and median fold error <= 4. Applicability also
requires all descriptor ranges and nearest training similarity at least the
larger of 0.4 or the calibration tenth percentile. Exact training overlap and
weak/OOD applicability refuse. No threshold or architecture was tuned after
test scoring.

Final r3 has 51 frozen strata and 236 insufficient-data stratum refusals.

- Model-set SHA-256: `a78b1b0b2a42b78b0d62cb9d1e9666362978b88630fbd9248663d60c92ccf75f`.
- Validation-report SHA-256: `d818e226475981d41d957b3a5e62b2d017d9827514a645c797f1f8e42a0dff08`.
- Gate SHA-256: `c381ecef4e526b594db995c33a9e0927977d8971b4b812afe1a5a66296eed018`.

Final r3 deployment/control replay passed all primary-label, identity, partition,
domain, calibration and numerical-report checks: 51 models, 21 passing strata
and 21 unique supported Human targets; 82 targets remain unsupported. None of
the six unrelated controls had an accepted in-domain prediction. That is a
refusal, not a negative pharmacological result. Final replay SHA-256:
`eff096619466ea0cf9287fe65da495bdcabbeae8037fcf1740e21ebb8d56aeb2`.
There are 41 built IC50 and ten built EC50 strata. All 21 passing strata are
IC50: inhibitor, blocker or antagonist endpoints. No agonist EC50 predictor
passed the frozen gate; those numbers are refused. Sourced GPCR agonism remains
separate evidence, not a deployed quantitative agonism model. AC50, percent
inhibition/activation, potentiation and unspecified variants are not deployed
quantitative predictors in this milestone.

Every model's train/calibration/test counts, overall/in-domain/weak/OOD errors,
rank correlation, fold error, interval coverage and failed gates are preserved
in `.test-runs/demo2-v5-functional-20261008/final-model-report-r2/all-model-metrics.csv`.
CSV SHA-256: `0bcc743909bc01935d733e0981f8a7e61b6e5c6943f305b51d815b5d7fe883b0`.
Across strata there are 33,483 training, 6,986 calibration and 6,803 test graph
entries; these sums are not counts of unique compounds across targets. Empty
domain subsets have absent metrics, not a manufactured zero error.

Supported source families: eight kinase, four PDE, three transporter, two
protease, one ion-channel, one GPCR, one MAO and one CYP target. Nuclear receptors,
COX and bromodomain targets have no accepted predictor. Those are unsupported,
not negative candidate findings. The frozen panel is not universal endocrine,
renal or safety coverage. Seven ambiguous panel rows also remain unresolved.
For example, the hERG IC50 model passes with in-domain test n=320,
MAE=0.4225087124 and RMSE=0.5561517103 log10 units, Spearman=0.6295477780,
median fold error=2.1330820081 and coverage=0.86875. Nevertheless Dofetilide
refuses: nearest training similarity 0.3538461538 is below the frozen 0.4
threshold. Neither its source measurements nor its known control role override
that applicability refusal.

Successful expensive replay is cached by the full verified dataset/source/model
content hashes, not path/mtime or candidate identity. Every load still rereads
and hash-checks all declared bytes. Failed checks are never cached. Candidate
inference remains newly computed; cached source replay cannot substitute for
source bytes or admit an undeclared source.

## Leakage boundary

The exact held-out graph, parents, salt fragments, normalized connectivities and
tautomers are denied before numeric labels. Complete normalization is required
for every eligible small-molecule denial graph. Larger macromolecules remain
outside the feature domain; truncated tautomer enumeration is not called exact.
All six predeclared development controls are also denied in train/calibration/
test. Nearby non-identical analogs may remain only under the frozen general
similarity policy, with nearest-neighbor similarity disclosed.

Requests first project compound/assay identities and verify exact molecular
graphs and assay metadata. Numeric requests specify both admitted molecule and
assay identifier sets. Unexpected identities, endpoints or relations quarantine
before admission. Metadata mentioning the held-out identity quarantines before
labels. Names of unrelated assay challenge/comparator controls do not erase
otherwise valid non-control training rows; those controls' exact labels still
remain excluded. Control-label validation is a separate, role-restricted audit
and never feeds training.

All 103 dataset byte hashes were checked again, and every record in all 94
acquired datasets was replayed against original graph, direct Human target,
action/readout, endpoint, relation, units and numeric source. The exact normalized
graph exclusion firewall passed, not only the subset used by fitted models.
Offline full-dataset leakage/source audit SHA-256:
`5e6e1a563076f71efa7c8abb8cb9e5e756bd9bd04138b3e564dbe78bd9f4dc66`.
Dataset protocol SHA `5b3aa989e1ac709069c353719666aa6fccc4a8a11a3a4c430ee0a4b3f7340db6`
is the canonical JSON hash; the original formatted protocol bytes retain the
separately recorded `5f5390…` hash. No protocol content or held-out v4 bytes changed.

## Unrelated source-backed controls

Completed direct public assay audits, without clinical/outcome narrative:

- Dofetilide: four admissible Human hERG functional current IC50 records.
- Isoproterenol: 20 GPCR agonist EC50 records; cAMP, calcium flux and arrestin
  readouts remain separate.
- Propranolol: seven antagonist/current-block IC50 records; action/readout and
  nominal concentration basis remain explicit.
- Donepezil: 27 admissible enzyme-inhibitor IC50 records across separately
  identified AChE, MAO-B and PDE5A endpoints; no pooling of protocols.
- Acetaminophen and insulin: zero admissible direct functional records in the
  bounded audit, not a demonstrated negative effect.
- Insulin model-domain refusal: independently replayed in the final r3 bank.
- Acetaminophen right-censored source audit: zero admissible records, not a
  negative control or evidence of safety.

Remaining-control audit SHA-256:
`6a4562a86b26cf0edf61053dee07a90dc7c4292c606f958cca26caa78bcf9eae`.
These direct ChEMBL records are curated assay-source evidence, not independently
publication-qualified measurements. They demonstrate preserved functional
semantics and do not replace model-held-out validation or exact physiological
transfer qualification.

The already-preserved primary Table 3 provides two Dofetilide CaV1.2 IC50 lower
bounds, one per assay protocol. They remain censored observations, not point
potencies or proof of zero activity. They cannot obtain a default Hill slope,
free-fraction conversion, exposure/activity ratio or physiological transfer.
Publication plotting-CSV sentinel values are not admitted as measured IC50s.
Primary-bound replay SHA-256:
`aeeeb45a9ba14fab887a23a7c861d84622b37ca1a89c27dcd8ca2453ac2b4bbc`.

## Native exposure, physiology and product path

The isolated browser session is
`research-session-0e33e7a5cf624ee00432e31dea04afff` on local API 8026 / preview
5194. This initial native run uses the preserved r1 seven-model bank (three
passing targets), SHA-256
`f7e095033c48afd198f36696d50b19e28e5f6fcda71c17dfdd760edee7944f9f`.
The final r3 51-model/21-target bank has passed offline deployment/control replay
separately; its real-browser integration is not yet claimed. Do not conflate
these two evidence batches.

Real Qwen initially proposed irrelevant multi-goal compositions. A general
prompt correction and a real same-session resumption repair allowed the
scientist to narrow the single exposure-study goal without accepting the invalid
proposal. A seamless first-turn success has not yet been demonstrated.

The corrected plan uses the existing ADME, native parameterization, organism
construction, PK-Sim simulation, population/comparison and blocking exposure
verification capabilities. Both sponsor-side input scenarios and both Human
subjects remain; no nominal values are selected. Native PBPK and exposure
verification completed. The original session subsequently completed at
2026-10-08T23:27:07Z. Its preserved session SHA-256 is
`4f8cc11709043d533aaa4fb577a2d4a46340ccc72e3bad25cf34a0421a7c0329`.
It proves the native chain can execute, but is not final acceptance: extraction
conflated a 30-minute infusion with the requested 24-hour PBPK horizon, the
oversized waveform prevented visualization, and the final answer omitted
physiology. None of its original evidence has been overwritten or repaired.

The optional direct-exposure bridge keeps exact source-backed activity apart
from model predictions. Only source-justified compatible unbound exposure can
enter overlap. Nominal QSAR predictions do not invent tissue mapping, free
fraction, Hill slope, effect direction or physiology. Existing exact transfer
contracts govern native numerical simulations and fresh replay. Cardiac
qualification stays State B `exploratory_only`, without independent candidate
decision authority. APD90 is not QTc, and numerical reproducibility is not
biological, clinical or independent certification.

The seven necessary CONCERN gates are predeclared. No source-reviewed general
undesirable-mechanism rule is manufactured from a result. REJECT is unavailable;
missing criteria, compatible exposure or quantified robustness remain
INSUFFICIENT EVIDENCE.

The shared UI separates predictions, measured activity, censored bounds, exact
Human annotation sources, overlap, native physiological values and refusals.
Exploratory limitations are prominent. Raw evidence remains downloadable.
Final saved-session/browser/hash acceptance remains pending the fresh repaired
r3 computation described below.

Read-only real-browser reopening on 2026-10-08 showed the original session still
running with eight of ten steps completed. The browser acceptance assertion
correctly refused to count it as completed. Progress screenshot/trace are
preserved in `.test-runs/demo2-v5-functional-20261008/browser-progress-r1/`.
This is progress evidence, not final scientist-answer or numerical-physiology
acceptance. The new browser assertions require real sourced activity, native
numerical responses, transfer refusals, unchanged exploratory-only/no-decision
authority, evidence downloads and saved-session reopening.

Dense paced waveforms exposed a representation risk: final synthesis previously
silently omitted JSON over 4 MiB, and visualization refused JSON over 8 MiB.
The bounded read-only projection now checks each full response against its
fresh numerical replay hash, retains all numerical endpoints/receipts and shows
waveform sample counts/hashes. Full samples remain in the unmodified download.
Only these physiological documents can be read up to 32 MiB, and the projected
summary must still satisfy the original 4/8 MiB bounds. Other oversized or
corrupt evidence refuses. This does not change solver settings, scientific
results or State B qualification. Only the isolated API on 8026 was restarted
on 2026-10-09 to load this repair into a separate `native-browser-r2` runtime.

## Final acceptance repair — 2026-10-09

Only four integration defects are in scope. No target, frozen model, scientific
gate, physiological equation or protocol was added, retrained or relaxed.

- Literal observation hours are distinct from infusion, pacing and waveform
  clocks. Repeated same-session infusion declarations in different wording are
  deduplicated; contradictory observation horizons still require clarification.
  A bare dose/numeric quote no longer grounds a duration.
- The complete artifact hash and byte size are checked before bounded read-only
  waveform projection. Exact endpoints, complete-response receipts, waveform
  counts and hashes remain; original downloads are unchanged.
- Answer assembly refuses corrupt physiological evidence instead of silently
  omitting it. Verified functional, compatible exposure and native physiological
  statements are mandatory, including exact endpoint values, scenario/protocol,
  State B `exploratory_only` and no independent candidate-decision authority.
  The UI displays the complete answer even when its principal summary is capped.
- The separate acceptance runtime copies the unchanged final r3 deployment:
  51 strata, 21 supported Human targets, 82 unsupported targets, manifest
  `a78b1b0b2a42b78b0d62cb9d1e9666362978b88630fbd9248663d60c92ccf75f`.

The one fresh session is
`research-session-f3b3aefe3bd05e324b8f5e6fa933114c`, created through the actual
browser with real Qwen. An initial irrelevant ranking proposal required a
same-session scope clarification. A subsequent clock-parser refusal exposed
mixed infusion wording and was fixed generically before computation began.
The session was not replaced and no scientific result was used to tune it.

Its real PK-Sim exposure artifact is
`034076a5c7774340a543ba8aeb486ed2ea9369c23a69ffcdfca76c275946be09`
(1,780,220 bytes). All 196 individual compartment curves span exactly 0–24 h.
Both sponsor scenarios contain two Human subjects; infusion remains 30 minutes.
Thirty available artifact byte hashes/sizes were independently checked while
the session was running. Native physiological calculations are still running;
no final numerical answer, visualization, reopening or full acceptance is
claimed yet.

## Regressions and remaining acceptance

- Combined backend suite after final-acceptance repair, including mixed-wording
  clock/session recovery: 237 passed. The focused clock/session subset passed
  68 tests. These counts are overlapping, not additive.
- Focused bounded/source semantics: 50 passed.
- Full frontend source suite: 206 passed (24 files), including all 69 focused
  parser and functional/native component tests; these are not additive totals.
- TypeScript app compilation passed; `git diff --check` passed.
- Existing mocked persisted-run/Mol* browser regression: 1 passed using its
  normal SwiftShader settings. A diagnostic run without those settings reported
  GPU ReadPixels performance warnings; the test/assertions were not weakened.

Execution input/source snapshot SHA-256:
`d48a2601a4f5b813b33564e3a27716cde9f7740b47c77c09b6bd88705f879cce`.
It pins 25 engine/frontend/protocol files, 4,573 copied model-bank/source files
and 258 policy/input/source receipts, with `acceptance_completed=false`.
It is not a final acceptance freeze or a committed milestone.

The original browser execute-response assertion timed out after five minutes
while the persisted native computation continued. The test now observes the
same persisted session rather than re-submitting an execution. Read-only
browser acceptance/reopening continues in `native-browser-r2/browser-resume-r3`.

Pending: native result completion, final scientific synthesis, final r3-bank
browser integration, completed saved-session reopening,
artifact download/hash checks and browser screenshots. Stop before the held-out
candidate regardless of whether these unrelated tests find a useful mechanism.

## Final storage/handoff recovery — 2026-10-09

Same session: `research-session-f3b3aefe3bd05e324b8f5e6fa933114c`.
Final status `completed`, revision 7; verification `passed`; all ten execution
steps have completed handoffs. The original failed graph remains historical
evidence: recovered step status records handoff completion, not another solver
run. No scientific calculation or numerical result was rerun or changed.

The original execution handoff was 5,538,656 bytes and its projected answer input
5,890,452 bytes. The global 4 MiB bounds remain unchanged.

- Durable execution record: **1,811,121 bytes**.
- Bounded root reference manifest: **932,120 bytes**; two immutable index pages
  each remain below 4 MiB. The original oversized v1 index is retained unchanged
  as downloadable historical evidence, not embedded in the live root/session.
- Bounded endpoint/display/synthesis projection: **3,197,487 bytes**.
- Final scientist-facing answer: **37,487 bytes**; SHA-256
  `e49039bdad8d2f09baac96ea60de81ecec3f770e622ab98302b1b917aca03760`.
- Compact visualization wire document: **3,195,735 bytes**. Pretty-printed
  browser evidence JSON is larger because of whitespace, not the wire payload.
- Every **4,844 original** artifact hash/size verified. Eleven new immutable
  recovery/index/archive artifacts bring the final audit to **4,855**; all verify.
- Both sponsor-input scenarios, two virtual Human subjects per scenario, all
  **196 individual exposure curves**, exact **0–24 h** horizon and **30-minute
  infusion** remain unchanged.

Raw assessment SHA-256
`8b69fb74ea31dc75722b43a16bd94d30d5dc89ce275d1301452eb226c17f4be4`
(15,984,159 bytes); raw functional response SHA-256
`a0d4f1cab70d73e46d83c0a34a44a1eaa03e5f8236d7954a18938127fc60e2a0`
(11,731,586 bytes). The PBPK artifact above is unchanged. Full numerical response
receipts and every waveform hash/sample count replay from these saved bytes;
this integrity audit does not invoke a solver, predictor or native simulation.

### Exact physiological endpoints preserved

All eight cases use ten Tusscher & Panfilov 2006 endocardial ventricular-cell
model `tt2006-native-endocardial`, perturbation
`rapid_time_dependent_potassium_current.g_Kr`. Full scenario, subject/exposure,
assay, transfer/protocol, model and qualification receipts remain in raw evidence.
Scenario A is `sensitivity-fdabf6494f94f21062122438`; scenario B is
`sensitivity-1bce87090756b8989d08b8ea`. Subject rows below retain original raw
ordering; they are distinct exact exposure hashes, not pooled observations.

| Scenario | Assay protocol | Subject row | Baseline APD90 (ms) | Perturbed APD90 (ms) | Delta APD90 (ms) |
|---|---|---|---:|---:|---:|
| A | Dofetilide-Pharm-source-matched | 1 | 295.05627104809133 | 316.2015521611143 | 21.145281113022975 |
| A | Dofetilide-Pharm-source-matched | 2 | 295.05627104809133 | 322.9714558039577 | 27.91518475586639 |
| B | Dofetilide-Pharm-source-matched | 1 | 295.05627104809133 | 296.6402790398112 | 1.5840079917198864 |
| B | Dofetilide-Pharm-source-matched | 2 | 295.05627104809133 | 297.7249582373529 | 2.6686871892615613 |
| A | Dofetilide-CiPA-source-matched | 1 | 295.05627104809133 | 313.95964282924393 | 18.9033717811526 |
| A | Dofetilide-CiPA-source-matched | 2 | 295.05627104809133 | 320.9124799524668 | 25.856208904375478 |
| B | Dofetilide-CiPA-source-matched | 1 | 295.05627104809133 | 296.16871696912494 | 1.1124459210336113 |
| B | Dofetilide-CiPA-source-matched | 2 | 295.05627104809133 | 297.00530739983367 | 1.9490363517423361 |

Observed exploratory delta range: **+1.11 to +27.92 ms** (exact min/max
1.1124459210336113 / 27.91518475586639). This is not a confidence interval,
population interval, QTc estimate, whole-organ prediction or safety finding.
Qualification remains **State B `exploratory_only`**, with no independent
candidate-decision authority. Numerical replay is not biological certification.
The final answer is Pulsate's real configured-model assembly of verified evidence,
with mandatory exact statements; no engineering-written scientific answer replaces it.

### Representation and browser proof

Before projection, full payload hashes/sizes and native numerical-replay receipts
are checked. Aligned curve arrays and waveforms are explicitly externalized.
Deterministic `uniform-index-plus-column-extrema/v1` display sampling preserves
exact first/last samples and extrema; scientific endpoints/integrals are copied,
never recomputed from display samples. Each waveform retains **20,001** original
samples and shows **130** display samples, original hashes and projection scope.
Units, protocols, models, qualification and provenance stay in the bounded
summary; raw files remain immutable downloads. Root/page indices are hash-checked
and resolved only through the owned session, never by an unrestricted global ID.

Real browser acceptance `native-browser-r2/browser-storage-recovery-r6` passed:
same completed session reopened, complete evidence-grounded answer visible,
exact raw-vs-projected endpoints equal by assay/scenario/subject exposure hash,
all 16 waveform plots visible, bounded exposure/answer/visualization, every export
download hash verified, both index pages downloaded and verified, first/last
manifest-only references resolved, saved session reopened again. Every browser
research request in this recovery test is restricted to GET; no execution POST
is permitted. Earlier test failures were preserved and fixed as test-identity/
disclosure-format defects, without changing scientific values or expected scores.
Computer-use inspection separately confirmed the actual waveform on screen;
`browser-storage-recovery-r5/recovered-waveform-visible.jpg` preserves that view.

### Final regressions and freeze

- Backend: **242 passed**, including 3.9 MiB allowed handoff, automatic reference
  externalization, bounded pages/root/projection, exact endpoints, immutable raw
  hashes, durable reopening, corrupt/missing refusal, and no-engine reassembly.
- Frontend: **208 passed / 25 files**; TypeScript app compilation passed.
- Existing mocked persisted-run/Mol* browser: **1 passed**, normal SwiftShader.
- Actual completed-session read-only browser: **1 passed**.
- `git diff --check`: passed; staged check required before committing.

The final source/bank/evidence freeze is in
`.test-runs/demo2-v5-functional-20261008/storage-handoff-final-freeze-20261009-r2/`.
The tracked bounded freeze document pins those receipts without committing the
runtime, scientific data, model sandbox, screenshots, credentials or archives.
The earlier execution-source snapshot is preserved as historical evidence,
not overwritten. Final model-set SHA remains `a78b1b0b…ccf75f`: 51 strata,
21 supported Human targets, 82 unsupported. All candidate predictions here were
refused by unchanged applicability gates; sourced assay results are separate.

These repairs affect representation only. No retraining, scientific policy/gate,
source, PBPK/physiology equations/settings, qualification or numerical output
changed during recovery. No clinical inference is established. No held-out
investigation or IBM submission was performed. Freeze, selectively commit/push
the coherent v5 batch, and stop.

Final freeze receipts (SHA-256), also pinned in
`docs/DEMO2_V5_ACCEPTANCE_FREEZE_20261009.json`:

- Acceptance freeze: `ea4db0c9c981dc3736e98bfb47e6c3573945c53274aef3f7a210954566e1e2b7`.
- Engine source manifest (53 files): `e1309d2c2b07ce89facc9fab65059a815357923c178bd11bf1312cb00d0db55e`.
- Model/source manifest (4,573 bank files plus 258 inputs): `c61ff8230b1055ff2e9015576d7243fe40ad458f51dfdb917f8419f8122d87fb`.
- Complete recovered evidence manifest: `fd62b99231ae809f678bcd10e3a16638f311e17994a329fb390a1ec470d70536`.
- Artifact/receipt audit: `82dfe74599f6b8a864c630126f1fce28c9e7c8f150b509b7c2f0910642794fbf`.
- Live bounded root: `bc9c024d030999b16be79d999a04ace33264f579a6ecbd21502320d8a7364edc`.
- Index page 1 (3,145,757 bytes): `b2fe2837a0c90a991c8b83bfbb3a7caf4f4c6b78a6fc3ac403c4979503308c57`.
- Index page 2 (1,711,313 bytes): `5164406d92d11a44446f5f2c4b46311cb0ea4e425ed79f82e0b513e78b6b580e`.

The model/source aggregate exactly matches its pre-repack freeze. Staged Git
text is compared against the source manifest's LF-normalized hashes before
commit; raw runtime hashes retain their original exact-byte meaning.
