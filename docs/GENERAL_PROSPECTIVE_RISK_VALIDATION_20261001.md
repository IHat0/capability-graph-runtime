# General prospective risk assessment — held-out rerun not authorized

## Preserved blind baseline

Branch: `feat/pulsate-scientific-autonomy-v1`.

Demo 1 parent: `8f1c11d73b9a847df4fad689aa1e639f1015092b`.

Demo 2 v1 commit: `8bba2505bb8b1efe7ff201365a971a38c1aae9d4` — **Preserve blind prospective Torcetrapib baseline (Demo 2 v1)**. Normal feature-branch push succeeded and remote/local tips matched. Selectively staged 21 product/frontend/test/report files, 861 insertions and 35 deletions; cached whitespace review passed. No temporary artifacts, `.test-runs`, private files, `pyproject.toml` or `src/cgr/swebench/integration.py` were staged. Those unrelated working copies were preserved.

The original validation report was committed without rewriting its historical statements. Its references to uncommitted work describe the earlier validation freeze, not the repository state after this preservation commit.

Original frozen session: `research-session-18d556f04191e07c9148e05529d1505d`.

Original frozen result SHA-256: `0ea2dc54761c4eb81f82661c5739c5a3e3219dc3ea22df44fed09da087af3a10`, rechecked unchanged during this task. Its assessment, 24 artifacts, source snapshot and write-protected EC2 copy were not revised. The original server data in `/home/ubuntu/cgr-drug-product-20260906/runtime-repaired` was left intact; new validation sessions use `runtime-general-prospective-20261001`.

**No held-out rerun and no historical-outcome retrieval were conducted. New development remains uncommitted and unpushed.**

## Scope of the general improvement

The existing engine performed intended-target docking and graph descriptors, but had no reusable alternative-target path. This batch adds structural binding-space hypothesis generation, real available alternative-target docking, uncalibrated cross-target comparison, controlled functional annotation mapping, independent receipt verification, and scientist-facing disclosure.

It does **not** add a validated clinical safety model or promise an advance/reject recommendation from docking. Required functional, exposure and safety evidence remains missing, so the present candidate-level evidence gate still returns insufficient evidence. A descriptor flag or alternative interaction hypothesis is a follow-up question, not a proven liability.

## Algorithm and source boundary

1. Start from every verified candidate's canonical graph and the sourced intended-target accession/organism. Without those identities, record insufficient evidence rather than guess which target is intended.
2. Ask RCSB for chemically similar deposited components using a SMILES fingerprint search. The public relevance score is **not** used as a chemical similarity value.
3. Read component descriptors through a descriptor-only RCSB GraphQL projection: `SMILES`, `SMILES_stereo`, `InChIKey`. Independently reconstruct the source graph identity and calculate chiral Morgan radius-2 / 2,048-bit Tanimoto with RDKit. Retain similarity >=0.7. This is a disclosed hypothesis-generation threshold, not a validated target-activity probability.
4. Search experimental entries containing eligible components, then examine actual ligand/protein contacts in legacy PDB coordinates. Require X-ray resolution <=3 Å, an unambiguous DBREF sequence mapping, a source-matched chemical component, a unique ligand-defined site with observed neighbors, and no unresolved deposited mutation/conflict. Alternative targets must match the sourced organism and differ from the intended accession.
5. Collapse repeated structural records for a target deterministically: similarity, resolution, accession, PDB and chain. Preserve every exclusion and bounded overflow in the panel artifact. This ranking selects screening inputs, not the scientifically best receptor in the whole archive.
6. Use UniProt's `accession,organism_name,protein_name,go` projection. Retain controlled GO terms and their evidence attribution. No comments, disease/clinical narrative, compound outcome record, article abstract or article text is fetched. Bibliographic identifiers present in foundational annotations are retained as provenance, not followed to literature. Coordinate files may contain deposited bibliographic headers; those headers are not used for hypothesis inference or supplied to the answer model.
7. Where eligible, reuse PDBFixer preparation, pinned Meeko/Gasteiger preparation and the existing native Vina evaluator with fresh candidate conformers, MMFF94s, seed 20260808, exhaustiveness 1 and up to 3 poses. Missing side-chain/terminal atoms in existing residues may be predicted by the existing preparation method and are recorded as modeled; missing backbone loops are not constructed. Unreviewed alternate conformations and deposited metal cofactors cause an explicit unsupported result, not silent deletion.
8. Report the raw alternative-minus-intended Vina score difference. Cross-receptor differences are uncalibrated: they are not affinity ratios, selectivity measurements, statistical significance or evidence of toxicity. A nonpositive difference is a follow-up prioritization flag only.
9. Map computed interaction hypotheses to sourced GO molecular-function/biological-process terms conditionally. The annotation describes the protein; docking does not establish perturbation, inhibition, direction, tissue exposure or downstream effects. Metabolic proteins may appear in this broad interaction panel; they are not automatically classified as pharmacological off-targets.
10. Independently reread the native receipts, hashes, candidate/receptor identities, candidate/target coverage, intended score comparison and functional annotation projection. Block a contradictory report. Verification is computational/integrity verification, not biological or clinical certification.

Source classes: PubChem identity/structure inputs from existing acquisition; RCSB chemical descriptors, chemical/experimental-entry searches and experimental coordinates; UniProt identity/organism and controlled GO annotation projections. Exact requests, response hashes and source bodies are persisted. The collector has an HTTPS/path allowlist and does not admit arbitrary or narrative URLs.

Technical primary references: [RCSB Search API](https://search.rcsb.org/), [RCSB Data API](https://data.rcsb.org/), [UniProt result-field configuration](https://rest.uniprot.org/configure/uniprotkb/result-fields).

## Bounds and missing evidence

- Review up to 8 components and 16 experimental entries per candidate, then retain at most 3 distinct alternative proteins. At most 12 candidate-target screening pairs are selected. Every candidate is represented, including zero-hit, budget-limited and unsupported cases.
- If the candidate count exceeds that pair budget, do not secretly prioritize the first candidates; record why no uniform panel can be allocated. The test with 13 synthetic candidates checks this behavior.
- Foundational retrieval is time-bounded; source failures and remaining coverage gaps are retained. Later candidates can be time-budget limited, visibly, rather than silently dropped.
- Structural archive coverage, search ordering, co-crystal selection and similarity impose selection bias. No hit is not a safety finding. Same-accession domain selectivity is not tested by this alternative-accession panel.
- One receptor, one seed and low-exhaustiveness docking are exploratory. No conformational/protonation ensemble, calibrated binding free energy or uncertainty interval is claimed.
- Functional annotations do not prove that a contact in one domain perturbs every annotated function of the full protein.
- No validated ADMET/toxicity model, measured exposure, tissue-expression calculation, functional inhibition model, MD refinement or orthogonal electronic calculation was introduced. Orthogonal assays/ensemble/free-energy investigations are recommendations, explicitly not computations that ran.
- Classical sufficiency remains correct for this graph/structure/rigid-docking work. No electronic difficulty was established by these results. No VQE/IBM job ran, and the existing quantum infrastructure was not removed.

## Planner/runtime/product integration

An ordinary `assess_drug_candidate` request composes three new prerequisites:

`discovery.off_target_hypothesize` → `discovery.off_target_screen` → `discovery.off_target_verify` → existing `discovery.prospective_assess` → existing `scientist.result_assemble`.

These follow blocking intended-target verification. Scene projection can occur in parallel, but the final answer waits for the assessment. The real accepted unrelated sessions used 17 steps: the existing 14-step discovery/assessment/scene/result path plus these three capabilities.

The selected Qwen provider extracts intent/entities and selects evidence-backed statements; it never supplies an off-target list or scientific liability claim. Full candidate accounting and risk limitations are mandatory answer statements. The interface retains computed and unsupported targets, raw comparison limits, sourced functional follow-up hypotheses, evidence downloads and saved-session reopening.

A new many-candidate summary regression first reproduced the existing 8,192/4,096-character principal/follow-up field failures. The repair bounds only the short summaries, explicitly labels shortening, and retains the complete evidence-backed answer and all candidate information. It does not weaken scientific validation or discard a candidate.

## Actual unrelated browser evidence

These values are native computations or explicit refusal records, not test constants.

| Request | Actual outcome |
| --- | --- |
| Investigate I-BET762 as a human BRD4 bromodomain 1 drug candidate. | Completed, 17 steps, verification passed. Intended receptor 3P5O; Vina −9.210 kcal/mol. Structural binding-space search independently nominated Q15059 / 24OS, with local graph similarity 1.0. Native alternative-target Vina −8.797 kcal/mol; raw difference +0.413 kcal/mol. Fresh preparation converged. Conditional functional annotations were sourced. Overall insufficient evidence; no toxicity or calibrated selectivity claim. |
| Investigate erlotinib as a human EGFR kinase domain drug candidate. | Completed, 17 steps, verification passed. Intended receptor 1M17; Vina −7.127 kcal/mol. The structural search nominated P04798 / 6DWN. Alternative docking was **not performed** because the protein-only method cannot discard a deposited metal cofactor. No alternative score or functional-effect claim was manufactured. Overall insufficient evidence. |

Sessions:

- `research-session-21d346be60f798467991591130c05f1e`: assessment SHA-256 `acf06b59ef6116251bf30f2a48035396fa48e3fb6a1f94fdd0ea9cb428fb9306`.
- `research-session-f305fa75b3c7d8448460ef12443d55b4`: assessment SHA-256 `f5c1d40427b570204ab831cd790cee786f42f4ffd38679a30f67af27d2fcff52`.

Both real-browser tests passed in 4.4 minutes. The suite additionally requires at least one **real** successful alternative-target computation; explicitly unsupported cases do not satisfy that numerical-proof requirement. All 60 and 47 downloadable artifact bodies were independently rehashed with zero mismatches. Result/reopening screenshots and original session/visualization JSON are in `.test-runs/general-off-target-browser-final-20261001/`. The existing intended-target atomic scene loads automatically; new alternative poses are retained as evidence, not automatically substituted for the intended-target scene. This is not a protein-cartoon/rendering redesign.

Preserved development attempts:

- A JQ1-only request asked for clarification under the pre-existing exact source/component identity policy. It was not counted as a successful calculation, nor was that policy weakened. Evidence remains in `general-off-target-browser-r1-20261001`.
- Initial lookups exposed an unsupported RCSB search attribute. The implementation was repaired using the existing chemical-component query contract, with a regression assertion. Failed and zero-coverage attempts remain in the r1/r2 evidence directories.
- An approval review rejected a launcher containing persisted credentials. No such launcher was written. The accepted credential-free launcher reads the already-configured model authentication into process memory; it persists no new credential values and changes no production service.
- The unrelated numerical sessions preceded the generic bounded-summary repair. That later adjustment affects only over-length UI summary fields, not these short summaries, computations, source selection or artifacts. The final source archive includes the repair.

## Validation and code review

- Broad product Python suite: **371 passed**, covering scientific planning, requirements, runtime, research sessions, acquisition, verification, visualization, construction and new risk contracts.
- New generic/synthetic contracts: **26 passed** locally and on the EC2 numerical dependency runtime. Tests cover two source-derived alternatives for each of two synthetic candidates; intended/alternative distinction; local graph similarity; panel bounds and exclusions; controlled annotation provenance; missing source/dependency behavior; no fabricated scores; independent score/hash/annotation/coverage verification; all-candidate accounting.
- Frontend: **184 passed**; TypeScript/Vite production build passed, with the existing bundle-size warning.
- Two unrelated real browser cases: **passed**, real Qwen/RDKit/Meeko/Vina where eligible, independent verification, complete downloads, UI limitations and reopening.
- Earlier real browser regression batch: **13 passed, 1 skipped** in 8.9 minutes, against the final backend. Covered N-candidate comparison/confidence/conversation, supplied-structure docking, public acquisition, defensible receptor selection and same-session clarification, plus a fresh exact Demo 1 construction/optimization/RHF/identity/interactive-view/reopening/download run. The separate optional preselected saved-construction-session test was skipped because no identifier was supplied; reopening was exercised by the fresh Demo 1 test. Evidence is preserved in `.test-runs/general-prospective-browser-regressions-20261001/`.
- Production diff/new-module scans found no held-out name/target, accession, receptor, CID or expected score literals, nor the outcome/mechanism marker patterns in the audit. This is a special-case audit, not a claim that generic source annotations contain no biological terms. The generic `insufficient_evidence` value remains an evidence-coverage decision for every candidate, not a held-out branch.
- Frozen baseline hash unchanged; Git index empty after baseline preservation. No new improvement was committed or pushed.

Final uploaded backend/test source archive: `.test-runs/general-off-target-source-final-20261001.tar.gz`, SHA-256 `b2f57e00fbce14af0dca86007b0fe0656d027611963f68688a6153cbcd6e51e3`.

Files changed in the new batch:

- New `src/cgr/pulsate_api/scientific_off_targets.py` and `tests/test_off_target_assessment.py`.
- `phase8_scientific_handlers.py`: register existing-store handlers and make the artifact bridge retain the actual off-target producer/execution/parents.
- `scientific_capability_catalogue.py`: compose panel, screening and independent verification as assessment prerequisites.
- `scientific_prospective.py`: integrate complete alternative-target coverage/evidence and limitations.
- `scientific_runtime.py` and `tests/test_scientific_runtime.py`: preserve mandatory grounded statements and complete long answers.
- Frontend `api/client.ts`, `api/client.test.ts`, `api/types.ts`, `components/ResearchWorkspace.tsx`: validate/render optional alternative-target evidence while older frozen sessions remain readable.
- New `frontend/e2e/off-target-assessment-live.spec.ts` and this report.

Unrelated `pyproject.toml`, `src/cgr/swebench/integration.py`, existing untracked WIP and all validation evidence remain preserved and unstaged.

## Held-out protocol validity and stopping point

There is no known use of the held-out candidate, outcome, expected mechanism or target list in new implementation tuning. The original assessment remains identifiable and unchanged. A future authorized rerun must create a **new** session/result, never overwrite v1, and be labeled as the improved-engine assessment rather than the original prediction.

Important caveats: pretrained model weights cannot be proven outcome-blind; current structural/GO archives are not a historical date-cutoff dataset; structural similarity panels are incomplete; protein annotations and docking do not predict patient safety. This remains retrieval-restricted exploratory computation, not proof of a fully history-free or clinically predictive experiment. These caveats must accompany any later reveal/comparison.

No historical search/reveal or held-out rerun has been conducted. Stop here pending explicit authorization.
