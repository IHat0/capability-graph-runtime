# Pulsate candidate-reasoning batch — verified results

Date: 2026-09-07. Branch: `feat/pulsate-scientific-autonomy-v1`.
Unchanged HEAD: `b43d2f9492c56c00ed06c2af9091081b48b5b462`.
All batch implementation changes remain **uncommitted and unstaged**. No push.

## Outcome

All three new questions completed through the actual browser, real Qwen interpretation, supplied structures, real RDKit/Meeko/Vina computation, blocking verification, and scientist-facing answers. Both Question 3 follow-ups completed in the same ResearchSession without replacing the original computation or rerunning docking. All three prior browser acceptance cases also passed, including 3D pose rendering and saved-session reopening.

This establishes the tested supplied-structure comparison and evidence-follow-up path, not universal drug-discovery capability or experimental ranking accuracy.

## Inputs and computation

The validation uses one public target structure, 3MXF, and four existing supplied public ligand files: JQ1, EAM, EST, OHT. These are software acceptance inputs, not a claim that all are suitable candidates for this target. Every distinct supplied candidate was evaluated; shortlist selection did not restrict computation.

Runtime: isolated EC2 directory `/home/ubuntu/cgr-drug-product-20260906/repaired`, API bound to loopback port 8012. Real model: Qwen/Qwen2.5-Coder-7B-Instruct via the existing local vLLM service. Dependencies: RDKit 2026.3.4, Meeko 0.7.1, Vina 1.2.7, OpenMM 8.5.2, isolated PDBFixer 1.12.0.

Each new initial question completed 13 execution steps. Work included structure ingestion; receptor preparation; resolving the deposited-ligand-defined docking search region; ligand preparation, molecular properties and docking for all four candidates; deterministic ranking; evidence trace; blocking verification; answer and scene production. The search region is not an independently predicted pocket. Three modeled missing side-chain/terminal atoms are disclosed as predictions. No backbone/loop reconstruction or pKa calculation is claimed.

Computed ordering (lower Vina pose score is favored):

| Rank | Candidate | Vina pose score, kcal/mol | QED |
|---|---|---:|---:|
| 1 | EAM | -9.2370 | 0.6786 |
| 2 | EST | -8.7830 | 0.7572 |
| 3 | JQ1 | -8.7510 | 0.4464 |
| 4 | OHT | -8.1740 | 0.5032 |

These values come from actual execution artifacts, not production constants. QED had zero ranking weight in the recorded supplied-candidate policy.

## Sequential before/after findings

### Question 1 — candidate set and requested shortlist

Before repair, the unchanged product evaluated and ranked all four compounds, but selected only EAM despite the request for two. It did not explain numerical trade-offs. Baseline session: `research-session-ff9dce4254af7234e7b70c67814ae782`.

Classification: missing typed selection constraint and execution-policy wiring; incomplete answer synthesis. N-candidate evaluation already worked and was preserved.

General repair: a quote-grounded candidate-selection count survives interpretation, deterministic validation and campaign composition. It limits selection, never evaluation. An explicitly requested shortlist may extend beyond the first Pareto front. All candidate ranking statements must survive answer synthesis, with score/descriptor differences derived from recorded evidence.

Intermediate real attempts showed Qwen omitting the optional count. The parser was revised to treat the input as JSON-contained text to extract from, rather than an instruction to answer. A diagnostic model response included unsupported example conclusions; those were not used as scientific results. Final pre-browser rerun selected EAM and EST: `research-session-e3c5683d9eed809060aeb38b4f780a2e`. Final browser proof below uses the accumulated repairs.

### Question 2 — confidence and limitations

Before this repair, the workflow completed but the answer largely repeated generic verification and method caveats; it did not clearly distinguish computational integrity from confidence in the ranking.

Classification: evidence interpretation / answer-synthesis limitation.

General repair: derive score separation from the actual candidate vectors; explain absent quantified uncertainty; state that passed verification does not establish experimental correctness. Preserve these confidence and limitation statements even if the model tries to omit them. No confidence percentage or statistical significance is manufactured.

Final pre-browser rerun: `research-session-f225144b1285fb989a661f3d9b92cb74`. The smallest recorded gap is 0.0320 kcal/mol; the answer explicitly says that separation alone does not establish robustness.

### Question 3 — persistent conversational reasoning

Before repair, the initial question completed, but each follow-up returned status `planned`, cleared the displayed scientist result and replaced compilation instead of answering from the completed calculation. HTTP 200 was not product success. Baseline session: `research-session-b503562f47587cc699b91c2d0346ece1`.

Classification: completed-session reply handling and missing evidence-query behavior, plus no completed-session reply form.

General repair: the model proposes only bounded candidate references and metric selectors. Deterministic code reads verified persisted campaign/trace evidence, resolves ranks and the previous discussed subset, computes comparisons, and saves a provenance-bearing review. The completed result, execution compilation and scene remain intact. The UI accepts and displays follow-ups after completion.

The first follow-up compares EAM and JQ1: JQ1's Vina score is 0.4860 kcal/mol higher. QED did not cause the loss because its original weight was zero. The second follow-up retains that pair, uses docking evidence alone, and reports no change. Neither follow-up runs new docking.

## Final browser answers — verbatim

The following text is the actual saved `scientific_result` returned by the product, not a replacement answer written for this report.

### Question 1

Scientist question:

> Evaluate every supplied compound against this protein target, rank them from strongest to weakest under the computational evidence available, explain the major trade-offs between the candidates, and tell me which two deserve further computational investigation. Do not discard any supplied candidate.

Session: `research-session-a2f5110dbd170043096dab155487a2ea`. Status: completed; 13/13 steps completed. Synthesis provider: openai_compatible_http; model: Qwen/Qwen2.5-Coder-7B-Instruct.

Pulsate answer:

Pulsate understood the confirmed research operations as: dock candidates, verify and rank results. Input: protein structure: 3MXF.pdb. Input: ligand structure: JQ1.sdf. Input: ligand structure: EAM.sdf. Input: ligand structure: EST.sdf. Input: ligand structure: OHT.sdf. Input: molecular_structure:molecular-structure-367d85fe560af938197c11fee004cabc. The verified workflow completed these capabilities: structure ingestion, force field select, protein protonation prepare, semantic target resolve, binding pocket resolve, docking receptor prepare, campaign initialize, design loop bind, campaign iterate, design loop trace, scientific verification.candidate ranking, scene project. The verified candidate comparison evaluated 4 supplied candidates and selected: EST, EAM. Rank 1: EAM (selected). drug likeness = 0.6786; vina pose score = -9.2370 kcal/mol. Candidate selected from deterministic Pareto ranking. Rank 2: EST (selected). drug likeness = 0.7572; vina pose score = -8.7830 kcal/mol. Compared with EAM: vina pose score differs by +0.4540 kcal/mol (lower is favored); drug likeness differs by +0.0786 (QED descriptor; not potency). Candidate selected from deterministic Pareto ranking. Rank 3: JQ1 (rejected). drug likeness = 0.4464; vina pose score = -8.7510 kcal/mol. Compared with EAM: vina pose score differs by +0.4860 kcal/mol (lower is favored); drug likeness differs by -0.2322 (QED descriptor; not potency). Candidate was eligible but not selected under the selection policy. Rank 4: OHT (rejected). drug likeness = 0.5032; vina pose score = -8.1740 kcal/mol. Compared with EAM: vina pose score differs by +1.0630 kcal/mol (lower is favored); drug likeness differs by -0.1754 (QED descriptor; not potency). Candidate was eligible but not selected under the selection policy. Blocking verification checks recorded computational integrity; it does not establish the probability that this ranking is experimentally correct. The supported conclusion is a pose-score ordering under the recorded screening setup, not measured affinity, efficacy, safety, or selectivity. Different receptor conformations, protonation states, retained waters, search sampling, or scoring methods could change the ordering; their effects on this ranking have not been quantified here. The available docking score vectors contain no quantified uncertainty. No calibrated ranking confidence, confidence interval, or statistical significance can be assigned from them. The smallest reported docking-score separation is 0.0320 kcal/mol. A numerical separation alone does not establish that the ordering is robust. The reported conclusion is backed by blocking verification that passed. Semantic entity resolution must precede any atom-index materialization. Protein-only rigid docking excludes deposited ligand, waters and nonpolymer additives; any later modeled atoms are recorded separately. Alternate conformations use highest mean occupancy, with a lexical tie break. PDBFixer modeled 3 missing side-chain/terminal atoms in existing residues; these coordinates are predictions, not experimental observations. No missing residue loops or backbone atoms are reconstructed; Meeko standard protonation templates are used without pKa calculation. Vina scores rank generated poses and are not binding free energies. Screening uses a rigid receptor and supplied charge/stereochemical states; activity and efficacy are not established. For supplied-candidate comparison, ranking is by Vina score; QED is reported separately, not a potency measurement. All distinct supplied candidates were evaluated without creating new molecular identities. The docking region is centered at [28.751032258064516, 15.82567741935484, -2.335483870967742] Angstrom, with dimensions [19.285, 19.838, 20.494] Angstrom. It was resolved using deposited ligand coordinates with 6A margin; this is a structure-defined search region, not an independent pocket prediction. The 31 supporting evidence artifacts are retained for inspection and download. Recommended next computation: review the selected candidates and run a separately approved higher-fidelity refinement on the most promising verified candidates.

### Question 2

Scientist question:

> Which candidate looks strongest in this docking screen, how confident should I be in that ranking, and what assumptions or limitations of the calculations could realistically change the ordering? Tell me what the current evidence supports and what it does not.

Session: `research-session-40c851be8fe589d06962507fcd6ad471`. Status: completed; 13/13 steps completed. Synthesis provider: openai_compatible_http; model: Qwen/Qwen2.5-Coder-7B-Instruct.

Pulsate answer:

Pulsate understood the confirmed research operations as: dock candidates, verify and rank results, estimate relevant quantities. Input: protein structure: 3MXF.pdb. Input: ligand structure: JQ1.sdf. Input: ligand structure: EAM.sdf. Input: ligand structure: EST.sdf. Input: ligand structure: OHT.sdf. Input: molecular_structure:molecular-structure-27251376eebfb197ca1a1424f7502841. The verified workflow completed these capabilities: structure ingestion, force field select, protein protonation prepare, semantic target resolve, binding pocket resolve, docking receptor prepare, campaign initialize, design loop bind, campaign iterate, design loop trace, scientific verification.candidate ranking, scene project. The verified candidate comparison evaluated 4 supplied candidates and selected: EAM. Rank 1: EAM (selected). drug likeness = 0.6786; vina pose score = -9.2370 kcal/mol. Candidate selected from deterministic Pareto ranking. Rank 2: EST (rejected). drug likeness = 0.7572; vina pose score = -8.7830 kcal/mol. Compared with EAM: vina pose score differs by +0.4540 kcal/mol (lower is favored); drug likeness differs by +0.0786 (QED descriptor; not potency). Candidate was eligible but not selected under the selection policy. Rank 3: JQ1 (rejected). drug likeness = 0.4464; vina pose score = -8.7510 kcal/mol. Compared with EAM: vina pose score differs by +0.4860 kcal/mol (lower is favored); drug likeness differs by -0.2322 (QED descriptor; not potency). Candidate was eligible but not selected under the selection policy. Rank 4: OHT (rejected). drug likeness = 0.5032; vina pose score = -8.1740 kcal/mol. Compared with EAM: vina pose score differs by +1.0630 kcal/mol (lower is favored); drug likeness differs by -0.1754 (QED descriptor; not potency). Candidate was eligible but not selected under the selection policy. Blocking verification checks recorded computational integrity; it does not establish the probability that this ranking is experimentally correct. The supported conclusion is a pose-score ordering under the recorded screening setup, not measured affinity, efficacy, safety, or selectivity. Different receptor conformations, protonation states, retained waters, search sampling, or scoring methods could change the ordering; their effects on this ranking have not been quantified here. The available docking score vectors contain no quantified uncertainty. No calibrated ranking confidence, confidence interval, or statistical significance can be assigned from them. The smallest reported docking-score separation is 0.0320 kcal/mol. A numerical separation alone does not establish that the ordering is robust. The reported conclusion is backed by blocking verification that passed. Semantic entity resolution must precede any atom-index materialization. Protein-only rigid docking excludes deposited ligand, waters and nonpolymer additives; any later modeled atoms are recorded separately. Alternate conformations use highest mean occupancy, with a lexical tie break. PDBFixer modeled 3 missing side-chain/terminal atoms in existing residues; these coordinates are predictions, not experimental observations. No missing residue loops or backbone atoms are reconstructed; Meeko standard protonation templates are used without pKa calculation. Vina scores rank generated poses and are not binding free energies. Screening uses a rigid receptor and supplied charge/stereochemical states; activity and efficacy are not established. For supplied-candidate comparison, ranking is by Vina score; QED is reported separately, not a potency measurement. All distinct supplied candidates were evaluated without creating new molecular identities. The docking region is centered at [28.751032258064516, 15.82567741935484, -2.335483870967742] Angstrom, with dimensions [19.285, 19.838, 20.494] Angstrom. It was resolved using deposited ligand coordinates with 6A margin; this is a structure-defined search region, not an independent pocket prediction. The 32 supporting evidence artifacts are retained for inspection and download. Recommended next computation: review the selected candidates and run a separately approved higher-fidelity refinement on the most promising verified candidates.

### Question 3

Scientist question:

> Compare all of these compounds against this target and tell me which is the strongest candidate.

Session: `research-session-8b7d62abfcbee86fb9fb97ddca30d312`. Status: completed; 13/13 steps completed. Synthesis provider: openai_compatible_http; model: Qwen/Qwen2.5-Coder-7B-Instruct.

Pulsate answer:

Pulsate understood the confirmed research operations as: dock candidates, verify and rank results. Input: protein structure: 3MXF.pdb. Input: ligand structure: JQ1.sdf. Input: ligand structure: EAM.sdf. Input: ligand structure: EST.sdf. Input: ligand structure: OHT.sdf. Input: molecular_structure:molecular-structure-f9799bbcbbaf10a4a91a3950d8c097c7. The verified workflow completed these capabilities: structure ingestion, force field select, protein protonation prepare, semantic target resolve, binding pocket resolve, docking receptor prepare, campaign initialize, design loop bind, campaign iterate, design loop trace, scientific verification.candidate ranking, scene project. The verified candidate comparison evaluated 4 supplied candidates and selected: EAM. Rank 1: EAM (selected). drug likeness = 0.6786; vina pose score = -9.2370 kcal/mol. Candidate selected from deterministic Pareto ranking. Rank 2: EST (rejected). drug likeness = 0.7572; vina pose score = -8.7830 kcal/mol. Compared with EAM: vina pose score differs by +0.4540 kcal/mol (lower is favored); drug likeness differs by +0.0786 (QED descriptor; not potency). Candidate was eligible but not selected under the selection policy. Rank 3: JQ1 (rejected). drug likeness = 0.4464; vina pose score = -8.7510 kcal/mol. Compared with EAM: vina pose score differs by +0.4860 kcal/mol (lower is favored); drug likeness differs by -0.2322 (QED descriptor; not potency). Candidate was eligible but not selected under the selection policy. Rank 4: OHT (rejected). drug likeness = 0.5032; vina pose score = -8.1740 kcal/mol. Compared with EAM: vina pose score differs by +1.0630 kcal/mol (lower is favored); drug likeness differs by -0.1754 (QED descriptor; not potency). Candidate was eligible but not selected under the selection policy. Blocking verification checks recorded computational integrity; it does not establish the probability that this ranking is experimentally correct. The supported conclusion is a pose-score ordering under the recorded screening setup, not measured affinity, efficacy, safety, or selectivity. Different receptor conformations, protonation states, retained waters, search sampling, or scoring methods could change the ordering; their effects on this ranking have not been quantified here. The available docking score vectors contain no quantified uncertainty. No calibrated ranking confidence, confidence interval, or statistical significance can be assigned from them. The smallest reported docking-score separation is 0.0320 kcal/mol. A numerical separation alone does not establish that the ordering is robust. The reported conclusion is backed by blocking verification that passed. Semantic entity resolution must precede any atom-index materialization. Protein-only rigid docking excludes deposited ligand, waters and nonpolymer additives; any later modeled atoms are recorded separately. Alternate conformations use highest mean occupancy, with a lexical tie break. PDBFixer modeled 3 missing side-chain/terminal atoms in existing residues; these coordinates are predictions, not experimental observations. No missing residue loops or backbone atoms are reconstructed; Meeko standard protonation templates are used without pKa calculation. Vina scores rank generated poses and are not binding free energies. Screening uses a rigid receptor and supplied charge/stereochemical states; activity and efficacy are not established. For supplied-candidate comparison, ranking is by Vina score; QED is reported separately, not a potency measurement. All distinct supplied candidates were evaluated without creating new molecular identities. The docking region is centered at [28.751032258064516, 15.82567741935484, -2.335483870967742] Angstrom, with dimensions [19.285, 19.838, 20.494] Angstrom. It was resolved using deposited ligand coordinates with 6A margin; this is a structure-defined search region, not an independent pocket prediction. The 33 supporting evidence artifacts are retained for inspection and download. Recommended next computation: review the selected candidates and run a separately approved higher-fidelity refinement on the most promising verified candidates.

### Question 3 follow-up 1

> Why did the third-ranked compound lose to the first?

This is a read-only analysis of the verified, persisted evidence. No new docking calculation ran. Compared candidates: EAM, JQ1. Evidence used: vina_pose_score. EAM: vina_pose_score = -9.2370 kcal/mol. JQ1: vina_pose_score = -8.7510 kcal/mol. JQ1 minus EAM: +0.4860 kcal/mol for vina_pose_score; the recorded direction is minimize. The recorded evidence favors EAM under the selected criteria. The favored candidate set does not change relative to the preceding comparison or original ranking within this subset. The original ranking assigned zero weight to drug_likeness; those descriptors did not cause a candidate to lose. Blocking verification checks recorded computational integrity; it does not establish the probability that this ranking is experimentally correct. The supported conclusion is a pose-score ordering under the recorded screening setup, not measured affinity, efficacy, safety, or selectivity. Different receptor conformations, protonation states, retained waters, search sampling, or scoring methods could change the ordering; their effects on this ranking have not been quantified here. The available docking score vectors contain no quantified uncertainty. No calibrated ranking confidence, confidence interval, or statistical significance can be assigned from them. The smallest reported docking-score separation is 0.4860 kcal/mol. A numerical separation alone does not establish that the ordering is robust.

Original execution: `scientific-execution-7ebf10e2d3489d64f7e8a5ceb58df075`. New scientific computation: false. Conclusion changed: false.

Evidence identifiers: `discovery-campaign-a97d1e89015ac1702fc9f8a9552ce6f5`, `discovery-design-loop-trace-ae9d77a567c13391f4b5fcd4e40da224`.

SHA-256: `adc4430c40efb12d6d8486d8c80ccce7ed33374392f2dca889ab51a80d37738c`, `b0b963bffe984e6b8cf8888095e0c8a58b63a06527eb393eadb4156c28d6b1ab`.

### Question 3 follow-up 2

> Now ignore drug-likeness and compare those two only using the docking evidence. Does your conclusion change?

This is a read-only analysis of the verified, persisted evidence. No new docking calculation ran. Compared candidates: EAM, JQ1. Evidence used: vina_pose_score. EAM: vina_pose_score = -9.2370 kcal/mol. JQ1: vina_pose_score = -8.7510 kcal/mol. JQ1 minus EAM: +0.4860 kcal/mol for vina_pose_score; the recorded direction is minimize. The recorded evidence favors EAM under the selected criteria. The favored candidate set does not change relative to the preceding comparison or original ranking within this subset. The original ranking assigned zero weight to drug_likeness; those descriptors did not cause a candidate to lose. Blocking verification checks recorded computational integrity; it does not establish the probability that this ranking is experimentally correct. The supported conclusion is a pose-score ordering under the recorded screening setup, not measured affinity, efficacy, safety, or selectivity. Different receptor conformations, protonation states, retained waters, search sampling, or scoring methods could change the ordering; their effects on this ranking have not been quantified here. The available docking score vectors contain no quantified uncertainty. No calibrated ranking confidence, confidence interval, or statistical significance can be assigned from them. The smallest reported docking-score separation is 0.4860 kcal/mol. A numerical separation alone does not establish that the ordering is robust.

Original execution: `scientific-execution-7ebf10e2d3489d64f7e8a5ceb58df075`. New scientific computation: false. Conclusion changed: false.

Evidence identifiers: `discovery-campaign-a97d1e89015ac1702fc9f8a9552ce6f5`, `discovery-design-loop-trace-ae9d77a567c13391f4b5fcd4e40da224`.

SHA-256: `adc4430c40efb12d6d8486d8c80ccce7ed33374392f2dca889ab51a80d37738c`, `b0b963bffe984e6b8cf8888095e0c8a58b63a06527eb393eadb4156c28d6b1ab`.

## Verification and preserved capability

- Real Playwright browser tests: **6 passed in 4.9 minutes** (three new cases and all three prior supplied-structure cases).
- Backend focused cumulative suite: **127 passed** across candidate controls/review, scientific requirements/runtime, ResearchSession persistence/progress, discovery campaign/foundation, and molecular discovery components.
- Frontend: **176 passed in 22 files**.
- Production frontend type check/build: passed. Existing large-bundle warning remains.
- Independent download integrity check: **90/90 artifact downloads matched their advertised SHA-256**, 30 per final pre-browser question result.
- Independent follow-up check: original scientist result, execution compilation and scene unchanged for both replies.
- Browser follow-up checks: exactly one execution request for the initial Question 3 plus both follow-ups; preserved results; same candidate pair; docking-only metric selection; conversation available after reload.
- Prior three browser cases rechecked actual computation, verification, all steps succeeded, expected supplied-candidate counts, 3D pose rendering, and reopening.
- One newly added test was briefly misplaced during patch insertion; it was corrected and the full 127-test focused suite passed afterward. An attempted test invocation in the numerical-only local sandbox lacked pytest; the installed development Python ran the final passing suite. Neither issue was concealed as a product success.

## Generality / anti-overfitting review

The production diff plus new production module were inspected. No occurrences of 3MXF, JQ1, EAM, EST, OHT, the observed scores (9.237, 8.783, 8.751, 8.174), observed gaps (0.032, 0.486), or the acceptance-question openings were found in the new production logic.

Names, exact questions and expected acceptance assertions occur only in browser test fixtures/evidence/report. Production uses candidate collections, validated shortlist counts, persisted objective identifiers/directions/weights, model-selected references, and arithmetic on recorded scores.

Regression variations include shortlist counts two, three, seven and 25; alternate phrasing; non-existent rank; absent metric; forbidden model-supplied answer field; missing prior subset; alternate metric that actually reverses the favored candidate; and conflicting objectives that return multiple non-dominated candidates rather than inventing weights. These tests use synthetic labels and different scores. Quantities in them are test data, not scientific output.

General constants in production include bounded list/text sizes, count validation limits, written English numeral recognition, zero-weight policy detection, numeric formatting precision, and the existing Vina/QED metric identifiers. They do not identify a molecule or acceptance question.

## Remaining limits

- Real validation is four compounds against one target here, plus the prior one-/two-compound cases. It is not a performance benchmark for huge collections or proof of every scientific question.
- Docking scores do not establish measured affinity, efficacy, safety, selectivity, or calibrated ranking confidence. Receptor conformation, protonation, waters, sampling and scoring choices could change the order; their effects were not quantified.
- Evidence follow-ups are bounded read-only comparisons over the completed campaign. New calculations, new structures, missing measurements and ambiguous references are not silently executed or invented.
- Duplicate ranks across generations require clarification. Natural-language interpretation can still fail; the system must not treat model output as measured science.
- Written shortlist quantities currently have direct literal validation for English zero through twenty and digit representations; this is not a universal multilingual number parser.
- No automatic acquisition stage, new generation capability, quantum job, production authentication change or unrelated infrastructure work was performed. The runtime uses an isolated test-auth harness, not a new validation of production auth.
- The EC2 instance and production services were left running/unmodified. Only isolated validation was used. No commit or push.

## Files changed in this batch

Modified production:
- `src/cgr/pulsate_api/scientific_requirements.py`
- `src/cgr/pulsate_api/phase8_scientific_handlers.py`
- `src/cgr/pulsate_api/scientific_runtime.py`
- `src/cgr/pulsate_api/research_sessions.py`
- `frontend/src/components/ResearchWorkspace.tsx`

New, untracked implementation/tests:
- `src/cgr/pulsate_api/scientific_candidate_evidence.py` — 180 lines
- `tests/test_candidate_selection_controls.py` — 29 lines
- `tests/test_candidate_evidence_review.py` — 145 lines
- `frontend/e2e/candidate-reasoning-live.spec.ts` — 72 lines

The pre-existing `pyproject.toml` and `src/cgr/swebench/integration.py` edits are unrelated to this batch and were preserved untouched. Untracked validation evidence, archives, personal files and old work were not staged or deleted.

Tracked batch-only diff: 5 files, 127 insertions and 7 deletions. The four new implementation/test files add 426 lines and are not counted by ordinary unstaged `git diff --stat`. This report is also new/untracked.

## Evidence locations

Local before/after API JSON: [validation directory](C:/Projects/CGR/CGR-Pulsate/.test-runs/drug-product-20260906).
Exact final browser responses, follow-ups and screenshots: [browser evidence](C:/Projects/CGR/CGR-Pulsate/.test-runs/drug-product-20260906/batch1-final-browser).

Key before/after files: `batch-q1-before-result.json`, `batch-q1-after-r3-result.json`, `batch-q2-before-result.json`, `batch-q2-after-result.json`, `batch-q3-before-result.json`, `batch-q3-before-reply-1.json`, `batch-q3-before-reply-2.json`, `batch-q3-after-result.json`, and both `batch-q3-after-reply-*.json`. All copied locally without removing remote evidence.

## Repository status and checks

`git diff --check`: exit 0, no whitespace errors. Git reports line-ending normalization warnings for some existing working copies.
`git diff --cached --stat`: empty. No files staged.
The following captured status/stat output predates adding this report itself; this report adds one additional untracked documentation path. Git cannot enumerate several old inaccessible pytest directories; those were preserved.

```text
warning: could not open directory '.pytest-temp/major-a-evidence-bridge-2/': Permission denied
warning: could not open directory '.pytest-temp/major-a-evidence-bridge-broad/': Permission denied
warning: could not open directory '.pytest-temp/major-a-evidence-bridge-final/': Permission denied
warning: could not open directory '.pytest-temp/major-a-evidence-bridge-final-r2/': Permission denied
warning: could not open directory '.pytest-temp/major-a-evidence-bridge-first-failure/': Permission denied
warning: could not open directory '.pytest-temp/major-a-evidence-bridge-nearby/': Permission denied
warning: could not open directory '.pytest-temp/major-a-evidence-bridge-regression/': Permission denied
warning: could not open directory '.pytest-temp/major-a-evidence-bridge-sessions/': Permission denied
warning: could not open directory '.pytest-temp/major-a-evidence-bridge-sessions-final/': Permission denied
warning: could not open directory '.pytest-tmp-phase3c-project-publication-diagnostic-20260802-b1/projects/77/77c3dd0ad49ed161b2e1f7b2a2c089dfe0b8080a004764f6fa5193882bc9fcce/': Permission denied
warning: could not open directory '.pytest-tmp-phase3c-publication-diagnostic-20260802-a1f7/artifacts/objects/c9/c95db4c8a6a188d52144fb84eb8b1449f3df9ee13a6c1f359ca45e479735b9aa/': Permission denied
warning: could not open directory '.tmp-pytest-evidence-final/': Permission denied
warning: could not open directory '.tmp-pytest-major-a-all/': Permission denied
warning: could not open directory '.tmp-pytest-major-a-core/': Permission denied
warning: could not open directory '.tmp-pytest-major-a-deterministic/': Permission denied
warning: could not open directory '.tmp-pytest-major-a-deterministic-final/': Permission denied
warning: could not open directory '.tmp-pytest-major-a-final/': Permission denied
warning: could not open directory '.tmp-pytest-major-a-no-rdkit/': Permission denied
warning: could not open directory '.tmp-pytest-major-a-partial/': Permission denied
warning: could not open directory '.tmp-pytest-major-a-partial-r2/': Permission denied
warning: could not open directory '.tmp-pytest-major-b-core/': Permission denied
warning: could not open directory '.tmp-pytest-major-b-core-r2/': Permission denied
warning: could not open directory '.tmp-pytest-major-b-core-r3/': Permission denied
warning: could not open directory '.tmp-pytest-major-b-final-local/': Permission denied
warning: could not open directory '.tmp-pytest-major-b-first/': Permission denied
warning: could not open directory '.tmp-pytest-major-b-regression/': Permission denied
warning: could not open directory '.tmp-pytest-major-b-second/': Permission denied
warning: could not open directory '.tmp-pytest-major-b-synthesis-r4/': Permission denied
warning: could not open directory '.tmp-pytest-major-b-synthesis-r5/': Permission denied
warning: could not open directory '.tmp-pytest-routing-fix-2/': Permission denied
 M frontend/src/components/ResearchWorkspace.tsx
 M pyproject.toml
 M src/cgr/pulsate_api/phase8_scientific_handlers.py
 M src/cgr/pulsate_api/research_sessions.py
 M src/cgr/pulsate_api/scientific_requirements.py
 M src/cgr/pulsate_api/scientific_runtime.py
 M src/cgr/swebench/integration.py
?? .codex-pytest-temp-major-df-final/
?? .codex-pytest-temp-major-df-final2/
?? .codex-pytest-temp-major-df-resolver/
?? .codex-pytest-temp-major-e2/
?? .codex-pytest-temp-major-f1/
?? .codex-pytest-temp-major-f2/
?? .codex-pytest-temp-major-f3/
?? .codex-pytest-temp-routing/
?? .codex-test-deps/
?? .codex-tools/
?? .codex/
?? .pnpm-store/
?? .pulsate-research-sessions/
?? .pulsate-runs-diagnostic-20260809/
?? .pytest-current-userenv-20260809/
?? .pytest-repair-scientific/
?? .pytest-repair-six-issues/
?? .pytest-resolver-baseline/
?? .pytest-system-audit-20260810/
?? .pytest-temp-phase8a-regression-20260808-01/
?? .pytest-temp-phase8b-api-regression-r2/
?? .pytest-temp-phase8b-broad-r2/
?? .pytest-temp-phase8b-broad-r3/
?? .pytest-temp-phase8b-broad/
?? .pytest-temp-phase8b-persistence/
?? .pytest-temp/
?? .pytest-tmp-e1-core-after-publication-repair/
?? .pytest-tmp-e1-e7-enterprise-core-final/
?? .pytest-tmp-e1-phase1-3-core-local/
?? .pytest-tmp-e1-phase3c-local/
?? .pytest-tmp-e1-publication-diagnostic-20260803a-1/
?? .pytest-tmp-e1-publication-diagnostic-20260803a-2/
?? .pytest-tmp-e1-publication-diagnostic-20260803a-3/
?? .pytest-tmp-e1-publication-diagnostic-20260803a-4/
?? .pytest-tmp-e1-publication-diagnostic-20260803a-5/
?? .pytest-tmp-e1-publication-diagnostic-20260803a-6/
?? .pytest-tmp-e1-publication-diagnostic-20260803a-7/
?? .pytest-tmp-e1-publication-diagnostic-baseline/
?? .pytest-tmp-e1-publication-local-1/
?? .pytest-tmp-e1-publication-local-2/
?? .pytest-tmp-e1-publication-local-3/
?? .pytest-tmp-e1-publication-local-4/
?? .pytest-tmp-e1-publication-repair-1/
?? .pytest-tmp-e1-publication-repair-2/
?? .pytest-tmp-e1-publication-repair-3/
?? .pytest-tmp-e1-publication-repair-4/
?? .pytest-tmp-e1-publication-repair-final-1/
?? .pytest-tmp-e1-publication-repair-final-10/
?? .pytest-tmp-e1-publication-repair-final-2/
?? .pytest-tmp-e1-publication-repair-final-3/
?? .pytest-tmp-e1-publication-repair-final-4/
?? .pytest-tmp-e1-publication-repair-final-5/
?? .pytest-tmp-e1-publication-repair-final-6/
?? .pytest-tmp-e1-publication-repair-final-7/
?? .pytest-tmp-e1-publication-repair-final-8/
?? .pytest-tmp-e1-publication-repair-final-9/
?? .pytest-tmp-e1-publication-repair-focused-v2/
?? .pytest-tmp-e1-publication-repair-focused/
?? .pytest-tmp-e2-focused-final/
?? .pytest-tmp-e2-focused-rerun/
?? .pytest-tmp-e2-focused/
?? .pytest-tmp-e2-phase1-3-core-final/
?? .pytest-tmp-e2-phase1-3-core/
?? .pytest-tmp-e3a-enterprise-core-local-final/
?? .pytest-tmp-e3a-enterprise-core-warning-clean/
?? .pytest-tmp-e3a-focused-20260803-r2/
?? .pytest-tmp-e3a-focused-20260803-r3/
?? .pytest-tmp-e3a-focused-20260803-r4/
?? .pytest-tmp-e3a-focused-local-final/
?? .pytest-tmp-e3a-focused-warning-clean/
?? .pytest-tmp-e4-enterprise-core-local-final/
?? .pytest-tmp-e4-focused-local-final-rerun/
?? .pytest-tmp-e4-focused-local-final/
?? .pytest-tmp-e5-e7-enterprise-core-local-final/
?? .pytest-tmp-e5-e7-focused-current-final/
?? .pytest-tmp-e5-e7-focused-local-final-rerun/
?? .pytest-tmp-e5-e7-focused-local-final/
?? .pytest-tmp-e7-evidence-local-final-rerun/
?? .pytest-tmp-e7-fingerprint-fix/
?? .pytest-tmp-enterprise-focused-local/
?? .pytest-tmp-enterprise-phase1-3-local/
?? .pytest-tmp-ibm-final/
?? .pytest-tmp-ibm-final2/
?? .pytest-tmp-ibm-final3/
?? .pytest-tmp-ibm-final4/
?? .pytest-tmp-ibm-final5/
?? .pytest-tmp-ibm-orchestration/
?? .pytest-tmp-ibm-orchestration2/
?? .pytest-tmp-phase3-final-validation/
?? .pytest-tmp-phase3-planner-validation/
?? .pytest-tmp-phase3c-api-final-focused/
?? .pytest-tmp-phase3c-api-final-regression/
?? .pytest-tmp-phase3c-focused-final/
?? .pytest-tmp-phase3c-publication-final/
?? .pytest-tmp-phase3c-regression-final/
?? .pytest-tmp-pulsate-final/
?? .pytest-tmp-pulsate-full/
?? .pytest-tmp-pulsate-orchestration-final/
?? .pytest-tmp-quantum-broad/
?? .pytest-tmp-quantum-core/
?? .scientific-acceptance-20260826-r2/
?? .scientific-runtime-py312/
?? .test-runs/
?? .tmp-major-a-final-20260813/
?? .tmp-major-a-focused-20260813/
?? .tmp-major-a-full-core-20260813-r2/
?? .tmp-major-a-full-core-20260813/
?? .tmp-pytest-major-c-focused-r2/
?? .tmp-pytest-major-c-focused-r3/
?? .tmp-pytest-major-c-focused-r4/
?? .tmp-pytest-major-c-focused-r5/
?? .tmp-pytest-major-c-regression/
?? .tmp-pytest-major-c-security/
?? .tmp-pytest-major-d-analysis-1/
?? .tmp-pytest-major-d-analysis-2/
?? .tmp-pytest-major-d-analysis-regression/
?? .tmp-pytest-major-d-d1-d2-regression/
?? .tmp-pytest-major-d-foundation-final/
?? .tmp-pytest-major-d-foundation-regression-1/
?? .tmp-pytest-major-d-molecule-generation-1/
?? .tmp-pytest-major-d-protein-adapter/
?? .tmp-pytest-major-d-protein-planning/
?? .tmp-pytest-major-d-protein-regression/
?? .tmp-pytest-major-d-protein-runtime-r2/
?? .tmp-pytest-major-d-protein-runtime-r3/
?? .tmp-pytest-major-d-protein-runtime/
?? .validation-transfer/
?? .vite-build-e1-20260803-122750/
?? .vite-build-e3a-20260803-141447/
?? .vite-build-e4-20260803-153903/
?? .vite-build-e5-e7-20260803-171315/
?? .vite-diagnostic-current-20260809/
?? "ArmA 3 (2013).torrent"
?? "Codex Image 13 Aug 2026, 01_06_37.svg+xml"
?? "Codex Image 13 Aug 2026, 01_17_13.svg+xml"
?? DropboxInstaller.exe
?? Mohato_CV.docx
?? Mohato_Sefatsa_CV.docx
?? ask-pulsate-browser-test-v2.2.zip
?? ask-pulsate-browser-test-v2.2/
?? ask-pulsate-browser-test-v2.3.zip
?? ask-pulsate-browser-test-v2.3/
?? ask-pulsate-browser-test-v2.4.zip
?? ask-pulsate-browser-test-v2.4/
?? ask-pulsate-browser-test-v2.zip
?? ask-pulsate-browser-test-v2/
?? ask-pulsate-browser-test.zip
?? ask-pulsate-browser-test/
?? cgr-resolver-validation-transfer.zip
?? credit_usage_history_10063251956.csv
?? cyber_security_bursary_statement.pdf
?? docs/DRUG_DISCOVERY_PRODUCT_VALIDATION_20260907.md
?? docs/RESEARCH_WORKSPACE_MILESTONE_20260906.md
?? frontend-existing-run-review-final.diff
?? frontend-existing-run-review-revised.diff
?? frontend-existing-run-review.diff
?? frontend/.tmp-e1-frontend-20260803-122750/
?? frontend/.tmp-e3a-frontend-20260803-141447/
?? frontend/.tmp-e4-frontend-20260803-153903/
?? frontend/.tmp-e5-e7-frontend-20260803-171315/
?? frontend/e2e/candidate-reasoning-live.spec.ts
?? handoff/
?? mermaid-diagram.svg
?? phase4-complete-continuation.patch
?? phase4-complete-continuation.zip
?? phase4-foundation-repair.patch
?? phase4-foundation-repair.zip
?? phase4_pyscf_electronic_structure.patch
?? phase5_2_structure_cheminformatics.patch
?? phase5_3_classical_molecular_simulation.patch
?? phase5_final_noise_ibm.patch
?? phase5_final_noise_ibm_context.txt
?? phase5_quantum_adapter_context.txt
?? phase5_quantum_workflow_context.txt
?? phase5a_quantum_workflow.patch
?? phase5b_vqe_statevector.patch
?? phase5b_vqe_statevector_context.txt
?? phase6_replanning_half2_context.txt
?? phase6_scientific_replanning_half2.patch
?? phase6_verification_half1.patch
?? phase6_verification_half1_context.txt
?? phase7_discovery_foundation_context.txt
?? phase7_discovery_foundation_half1.patch
?? phase7_discovery_loop_half2.patch
?? phase7_discovery_loop_half2_context.txt
?? scene-endpoint-review-revised.diff
?? scene-endpoint-review.diff
?? src/cgr/pulsate_api/scientific_candidate_evidence.py
?? tests/test_candidate_evidence_review.py
?? tests/test_candidate_selection_controls.py
warning: in the working copy of 'frontend/src/components/ResearchWorkspace.tsx', LF will be replaced by CRLF the next time Git touches it
warning: in the working copy of 'pyproject.toml', LF will be replaced by CRLF the next time Git touches it
warning: in the working copy of 'src/cgr/pulsate_api/scientific_requirements.py', LF will be replaced by CRLF the next time Git touches it
 frontend/src/components/ResearchWorkspace.tsx     |  8 ++--
 pyproject.toml                                    |  2 +-
 src/cgr/pulsate_api/phase8_scientific_handlers.py |  7 ++-
 src/cgr/pulsate_api/research_sessions.py          | 27 ++++++++++++
 src/cgr/pulsate_api/scientific_requirements.py    | 54 ++++++++++++++++++++++-
 src/cgr/pulsate_api/scientific_runtime.py         | 38 ++++++++++++++++
 src/cgr/swebench/integration.py                   | 10 +++--
 7 files changed, 135 insertions(+), 11 deletions(-)
warning: in the working copy of 'frontend/src/components/ResearchWorkspace.tsx', LF will be replaced by CRLF the next time Git touches it
warning: in the working copy of 'pyproject.toml', LF will be replaced by CRLF the next time Git touches it
warning: in the working copy of 'src/cgr/pulsate_api/scientific_requirements.py', LF will be replaced by CRLF the next time Git touches it

```
