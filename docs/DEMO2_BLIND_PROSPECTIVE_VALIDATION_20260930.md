# Demo 1 freeze and Demo 2 prospective assessment

This report contains no historical outcome comparison. The prospective result is frozen; do not revise it after a reveal.

## Demo 1 Git preservation

- Branch: `feat/pulsate-scientific-autonomy-v1`.
- Previous HEAD: `5356d1e4bffdf6472ed8369699d35894b5e004f4`.
- New HEAD: `8f1c11d73b9a847df4fad689aa1e639f1015092b`.
- Commit: `Freeze Demo 1 classical construction and interactive 3D`.
- Normal feature-branch push succeeded; `git ls-remote` returned the same new SHA.
- 26 selectively reviewed files; 1,262 insertions and 22 deletions. Cached whitespace check passed.
- Included construction implementation, composition, result/scene projection, frontend viewer/result fixes, relevant tests/browser acceptance/offline validation, and the Demo 1 report.
- Excluded and preserved `pyproject.toml`, `src/cgr/swebench/integration.py`, all runtime/evidence/private/unrelated files, and the unused local quantum-proposal WIP module. Its six tests were preserved separately in `tests/test_quantum_proposal.py`; the committed classical construction tests do not depend on it.
- No Demo 2 commit or push has been made.

## Unchanged Demo 2 baseline

Exact question: **Investigate torcetrapib as a CETP drug candidate.**

Baseline session: `research-session-28c09722bd42ea54b34896dac6d847be`.

Real Qwen selected molecular construction instead of candidate assessment. The first construction step failed because the candidate was outside the bounded closed-shell electronic-construction policy. The baseline did not answer the question. It was captured before production implementation edits in:

`.test-runs/demo2-blind-unchanged-baseline-20260930/`.

Subsequent attempts are preserved, not discarded. They exposed abbreviated-target extraction, overly broad mutation exclusion, missing answer dependencies, and a short-summary size-limit failure. These were diagnosed against real Pulsate rather than supplying a scientific answer externally.

## General repairs

1. Added a quote-grounded `assess_drug_candidate` operation and a prospective output contract composed with the existing protein–ligand discovery profile. No candidate generation is inferred from investigating an existing compound.
2. Retained literal protein/compound roles, including abbreviated target names, using the real provider with structured extraction. No entity-specific routes were added.
3. For this prospective drug-assessment policy, absent organism scope is explicitly recorded as a human-target assumption. An unspecified domain requires one unique source-annotated processed chain, with complete experimental cross-reference coverage; otherwise clarification remains necessary. Existing non-prospective strict scope behavior is retained.
4. Restricted UniProt retrieval to identity, organism, sequence, domain, chain, signal and PDB fields; no comments, disease annotations, publications or clinical narrative. Exact retrieval URLs and response hashes are in the persisted source audit.
5. Added an exploratory engineered-construct policy. Observed changed residues must be outside a 6 Å ligand neighborhood. Conflicts, internal unobserved changes, site-proximal changes, ambiguous sites and missing site-neighbor residues remain excluded. Explicitly missing terminal changes can use a disclosed observed-boundary proxy, never a fabricated mutation coordinate. This policy does **not** infer wild-type equivalence or absence of allosteric effects.
6. Reused existing RDKit graph/conformer/descriptors, Meeko preparation, native Vina screening, candidate trace, blocking verification and scene projection. Recorded actual MMFF convergence and seeds in the existing numerical receipts.
7. Added a candidate-level assessment integrating verified descriptors and docking, exact graph/public identity revalidation, contextual property hypotheses, explicit unsupported risk dimensions, and classical computation selection. The current evidence-coverage policy cannot advance or reject a drug on docking/descriptors alone; it returns insufficient evidence for that broader decision.
8. Answer assembly now depends on **every required scientific deliverable**, not only scene/verification. The separate IBM authorization handoff is explicitly excluded from this barrier and remains unchanged.
9. Separated the bounded execution summary from the complete durable scientific answer. A failed workflow after an earlier verification preserves its artifacts and intermediate summaries without claiming fully verified completion.
10. Added concise prospective result cards and automatic restoration of the existing protein/pose viewer on completion and reopening. No second viewer was introduced.

No production occurrence of the challenge candidate or target name was added. General rule-of-five-style thresholds are contextual oral-small-molecule heuristics, not clinical outcome constants or toxicity predictions.

## Actual frozen ResearchSession

- Session: `research-session-18d556f04191e07c9148e05529d1505d`.
- Execution: `scientific-execution-c359d9366c051c71f3b46375ea0e35f6`.
- Status: completed, revision 3; 14/14 workflow steps succeeded.
- Interpretation and evidence-grounded answer selection: real `Qwen/Qwen2.5-Coder-7B-Instruct`, OpenAI-compatible HTTP provider.
- No uploads, scientific follow-up replies, VQE or IBM submissions.
- Assessment timestamp: `2026-09-30T21:43:29` UTC (full precision retained in the artifact).
- Frozen session snapshot timestamp: `2026-09-30T21:44:05.133Z`.

Actual selected workflow:

1. molecular.structure_ingestion
2. molecular.force_field_select
3. molecular.protein_protonation_prepare
4. molecular.semantic_target_resolve
5. molecular.binding_pocket_resolve
6. molecular.docking_receptor_prepare
7. discovery.campaign_initialize
8. discovery.design_loop_bind
9. discovery.campaign_iterate
10. discovery.design_loop_trace
11. scientific_verification.candidate_ranking
12. discovery.prospective_assess
13. molecular.scene_project
14. scientist.result_assemble

Input acquisition and target selection precede this execution graph in the same session. Candidate graph loading, validation, fresh ETKDGv3 conformer generation, MMFF94s preparation, descriptors and docking occur inside the existing campaign step, with their own persisted candidate-scoped evidence.

## Foundational inputs and source boundary

Candidate: PubChem CID `159325`; full standard InChIKey `CMSGWTNRGKRWGS-NQIIRXRSSA-N`. RDKit reconstructed the candidate graph and independently checked its full key against a fresh identity-only property response. This verifies graph/source identity, not independently curated public chemistry.

Receptor: UniProt `P11597`, annotated processed chain 18–493; experimental PDB `4EWS`, chain A, 2.59 Å X-ray record; deposited site component `0RP` matched the candidate by full InChIKey. One candidate-matched experimental record met the bounded coverage/resolution criteria. This co-crystal-based policy favors a candidate-compatible receptor conformation and is not an unbiased archive-wide receptor optimum.

Recorded allowed sources:

- `pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/torcetrapib/cids/JSON`: identity resolution.
- `pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/159325/property/InChIKey/JSON`: identity-only comparison.
- `pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/159325/SDF?record_type=3d`: public molecular structure input. Demo 2 allows this foundational input; unlike Demo 1, it is **not** a no-public-3D construction demonstration. Numerical conformers used for screening were generated afresh from the graph.
- `rest.uniprot.org/uniprotkb/search`: projection `accession,id,gene_names,protein_name,organism_name,sequence,ft_domain,ft_chain,ft_signal,xref_pdb`; exact query URL/hash retained.
- `search.rcsb.org/rcsbsearch/v2/query`: full InChIKey component lookup and component-containing entry lookup; exact encoded queries/hashes retained.
- `data.rcsb.org/rest/v1/core/chemcomp/0RP`: deposited component identity.
- `files.rcsb.org/download/4EWS.pdb`: experimental coordinates and deposited structural annotations.

Excluded: article/literature retrieval, clinical outcomes, compound narrative records, disease comments/publications. No historical reveal/search was conducted. Pretrained Qwen weights cannot be proven outcome-blind; the model was restricted to literal extraction and selection of deterministic evidence-backed statements, not generation of scientific outcome claims. This is a retrieval-blinded computational experiment, not proof of a history-free model.

Deposited engineered changes (author numbering): C1A, N88D, C131A, N240D, N341D and V405I. Residues 1–4 are declared missing. The mutation at unobserved residue 1 used the first observed terminal boundary: 15.0791 Å to ligand, explicitly marked as a proxy. Other measured minimum mutation-to-ligand distances: 25.2064, 8.6582, 16.8400, 23.1307 and 44.4086 Å. No site-neighbor missing records were flagged by the implemented annotation/proximity rule. These checks do not eliminate unannotated disorder, mutation effects or allostery. No missing backbone loop was manufactured.

## Computed results and frozen conclusion

| Quantity | Actual computation |
| --- | --- |
| Candidate graph formal charge | 0 |
| Molecular weight | 600.478 g/mol |
| RDKit predicted logP | 8.1979 |
| QED | 0.3249388882 |
| Hydrogen-bond donors / acceptors | 0 / 4 |
| Rotatable bonds | 5 |
| MMFF94s optimized conformer energy | 28.5215959662 kcal/mol |
| MMFF optimization | converged |
| Native Vina pose scores | −9.434, −9.068 kcal/mol |

RDKit version `2026.03.4`; ETKDGv3/docking seed `20260808`; Meeko/Gasteiger ligand preparation; rigid-receptor Vina. Exhaustiveness **1**, requested 3 poses, returned 2. This is a low-exhaustiveness screening run, not exhaustive docking, calibrated affinity, ensemble sensitivity analysis or a validated safety prediction. Two poses from one seeded run are not uncertainty estimates.

Search box center: `[9.4297317073, 1.4907073171, 37.8970975610]` Å; dimensions `[23.593, 20.956, 23.278]` Å, derived from the deposited ligand with a 6 Å margin.

Pulsate's frozen overall recommendation: **insufficient evidence**.

Intended-target conclusion: a supported screening pose/score for the selected engineered structural model, **not** established functional inhibition, affinity, efficacy, safety or wild-type binding.

Derived hypotheses: high molecular weight raises oral-exposure questions; high predicted lipophilicity raises solubility and nonspecific-binding questions. These are generic computed-descriptor flags, not measured liabilities or claims of a clinical mechanism. No expected off-target panel or retrospective explanation was supplied.

Unsupported/unresolved: measured solubility/exposure/metabolism/PK; validated toxicity/organ safety; alternative-target selectivity; functional target inhibition/efficacy; receptor/conformer/protonation ensembles; higher-fidelity binding free energies. No unsupported ADMET result was generated.

Pulsate's next-step statement: do not advance or reject on docking alone; obtain solubility/exposure and functional evidence, an independently justified selectivity/safety panel, and ensemble binding calculations before a candidate-level decision.

Compute choice: classical. Graph descriptors, force-field preparation and rigid docking are supported classical tasks. No difficult electronic subproblem was established; quantum availability alone was not a selection trigger. Existing Qiskit/IBM paths remain intact.

Verification scope: computational integrity, finite outputs, candidate lineage, source/graph identity and recorded screening evidence. **Not biological or clinical correctness.**

## Fingerprintable freeze and browser evidence

Local evidence root:

`.test-runs/demo2-blind-final-r9-20260930/prospective-assessment-liv-a6f70-ive-candidate-investigation-chromium/`

- `frozen-prospective-result.json`: actual session, interpretation/plan/graph, final answer, assessment, visualization and 24-artifact manifest.
- Frozen result SHA-256: `0ea2dc54761c4eb81f82661c5739c5a3e3219dc3ea22df44fed09da087af3a10`.
- Assessment artifact SHA-256: `05143ced2dddade697e76c39d3e6a66453d55eee3b117c82ce651918fab4ad77`.
- Frozen source delta archive `.test-runs/demo2-reviewed-source-r9-20260930.tar.gz`: `5be0854bc7b04c2afb2befe29153d4d9d4613bfd2cd8923a7f51c5374eba9335`, based on Demo 1 commit `8f1c11d...`.
- Original ligand SDF SHA-256: `78abfcf9a8d33c29aa07386faa5c0a440eccccb39025b69ae34246d125470c4d`.
- Original receptor PDB SHA-256: `f9c6fc1808906d070253c81563ac8000ecff95f773dacaf0b72bde69fd889ec4`.
- Docking pose SHA-256: `0ce96f57b1be57f5fe59e2a8b8c2eab247fc5cfd148ca6f2917665506aee49ad`.
- All 24 artifact bodies were downloaded and checked against their persisted SHA-256 identities.
- `prospective-result-3d.png` and `reopened-result.png`: real browser screenshots. Actual 3D coordinates and result cards loaded automatically, with no render error, and remained on reopening. Protein/pose rendering uses the existing atomic scene view; it is not a claim of publication-quality protein cartoons or a simulated trajectory.
- Saved-session URL: `http://127.0.0.1:5173/?research=research-session-18d556f04191e07c9148e05529d1505d` while the isolated runtime and local tunnel are running.
- EC2 write-protected copy: `/home/ubuntu/cgr-drug-product-20260906/demo2-frozen-prospective-20260930/`. Remote frozen-result and source-archive hashes matched local hashes. This is fingerprintable/write-protected evidence, not an independent trusted timestamp or root-proof WORM store.

After this freeze, one backward-compatibility adjustment preserved the original strict acquisition call for non-prospective alias resolution. It does not change the prospective call, computed results or frozen artifacts. The exact frozen source delta remains preserved separately; the working-tree compatibility adjustment is left uncommitted.

## Regressions and repository state

- Product Python suite: **312 passed**, covering prior drug discovery/acquisition/planning/sessions/verification/visualization/construction and new prospective contracts.
- Frontend: **182 passed**, 22 files; production TypeScript/Vite build passed (existing bundle-size warning).
- Final exact blind browser acceptance: **passed**; real Qwen, real RDKit/Meeko/Vina, blocking verification, assessment in the answer, all artifact hashes, automatic 3D and saved-session reopening.
- Existing real-browser regression batch: **13 passed, 1 skipped** in 8.9 minutes. Covered three N-candidate/confidence/conversational-reasoning cases, three supplied-structure drug-discovery cases, three public-acquisition cases, three defensible target-selection/same-session clarification cases, and a fresh exact Demo 1 construction run. The optional separate saved-construction-session test was skipped because no saved-session identifier was supplied; the fresh Demo 1 test itself checked reopening. Evidence is preserved in `.test-runs/demo2-browser-regressions-20260930/`.
- Production diff has no challenge candidate/target literals. Entity-independent tests cover multiple generic request identities, scientific output dependency barriers, role/assumption grounding, remote/proximal/internal-missing/conflict mutation boundaries, evidence-score mismatch/nonfinite rejection, long-answer handling and verified-then-failed state safety.

Demo 2 files: `scientific_requirements.py`, `scientific_capability_catalogue.py`, `scientific_conversation.py`, `scientific_target_selection.py`, `research_sessions.py`, new `scientific_prospective.py`, `phase8_scientific_handlers.py`, `scientific_runtime.py`, `research_visualization.py`, `discovery/molecular_components.py`; frontend API types/parser, ResearchWorkspace cards, useResearchSession automatic complex loading; regression tests and `prospective-assessment-live.spec.ts`; this report.

All Demo 2 work is uncommitted/unpushed. Unrelated existing changes and every baseline/validation evidence directory remain preserved. Production services were not modified, no cloud resources started or terminated, and no IBM hardware jobs submitted.
