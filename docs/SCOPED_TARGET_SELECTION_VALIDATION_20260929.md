# Scoped public receptor selection: validation record

## Repository and scope

Branch: `feat/pulsate-scientific-autonomy-v1`.

The prior public-acquisition batch was selectively committed and pushed as
`cffff94c3af5cc8b26310040d77e64e74b43cdce`. Origin was independently checked again
on 2026-09-29 and still points to that commit. This new development batch is
uncommitted. Unrelated `pyproject.toml` and `src/cgr/swebench/integration.py`
changes and all local evidence were preserved.

Validation used the user-approved isolated API on loopback port 8012, the
existing Qwen model, and real RDKit/Meeko/Vina computation. Production services
and IBM jobs were not used. These are application/scientific-workflow tests,
not proof of production authentication or calibrated experimental validity.

## Initial behavior and generic repairs

The three requests were first attempted without changing the implementation.
The explicit comparison request was rejected after the model proposed an
unsupported operation; explicit target scope was missed; and a same-session
domain clarification did not resolve the target ambiguity.

Repairs retain enum validation and exact scientist quotations. They recover
computational intent without adding operation aliases for acceptance wording;
extract organism/domain across turns; normalize duplicate target mentions;
preserve all partially acquired candidates; and persist the receptor-selection
report as a hash-bound companion artifact. Transient public-source errors have
bounded retries. Empty RCSB HTTP 204 searches are no-match results, not oversized
coordinate files. Default coordinate acquisition still rejects empty files.

Selection uses reviewed UniProt records, explicit organism/domain, deposited
domain coverage, experimental resolution, DBREF coordinate mapping, declared
mutation/conflict records, and declared missing residue/atom records. It reviews
at most 16 resolution-sorted, identity-matched experimental records and records
exclusions. No target, compound, PDB accession or expected score from these
acceptance scenarios is embedded in the production additions.

## Scientific audit caught a false acceptance

An intermediate EGFR session (`research-session-6a213d2dac4c60d6690243d1652c9323`)
completed computational verification but selected the MES component of PDB
7SI1 as its docking region. Organic size and spatial contact alone do not
establish a scientifically relevant site. This run is **rejected diagnostic
evidence**, not a successful scientific acceptance. Its files remain under
`.test-runs/target-selection-20260929-r7-remaining/`.

The repaired automatic-selection policy requires a deposited component whose
full standard InChIKey equals a uniquely resolved requested compound's public
identity. RCSB search hits are independently checked against chemical-component
records. An execution guard rejects older automatic reports lacking this match.
This is deliberately conservative: other suitable sites may be excluded, and a
candidate co-crystal can bias receptor conformation. Those limitations are
included in the answer. No molecule/additive blacklist was added.

## Final real browser acceptance

Evidence root: `.test-runs/target-selection-20260929-r9/`.
All three tests passed. Each completed 13 execution steps, passed blocking
verification, downloaded and hash-checked its selection report, opened the 3D
workspace without render errors, and reopened its saved ResearchSession.

| Scenario | ResearchSession | Selected receptor | Pulsate's computed pose-score order (kcal/mol) |
|---|---|---|---|
| Explicit human BRD4 bromodomain 1; JQ1 and I-BET762; no uploads | `research-session-9f0407d57d8df3e028aff7f08d50a1be` | 3P5O, chain A, 1.6 A; deposited EAM matches I-BET762 | I-BET762 -9.210; JQ1 -8.131 |
| Human EGFR kinase domain; erlotinib and gefitinib; no uploads | `research-session-7e3514e31d6e89021fe840d57ae318e6` | 1M17, chain A, 2.6 A; deposited AQ4 matches erlotinib | gefitinib -7.997; erlotinib -7.127 |
| Unscoped BRD4 request, then human BD1 clarification in the same session | `research-session-c59cb74537d261b228c8ffaa2a8ac275` | 3P5O, chain A, 1.6 A | I-BET762 -9.210; JQ1 -8.131 |

These numbers are copied from actual Pulsate execution responses, not supplied
by the engineer or an expected-value fixture. They are docking pose scores,
not measured affinities or evidence of efficacy, safety, or clinical superiority.

All scenarios were interpreted as docking the candidates and verifying/ranking
the results. The executed path included structure ingestion, force-field
selection, protonation/preparation, target and pocket resolution, receptor
preparation, candidate campaign execution, trace retention, blocking ranking
verification, and scene projection. Full questions, results, and answers are
in each scenario's `initial-session.json` and `execution.json`; scenario 3 also
has `scoped-reply.json`. `selection-evidence.json` contains source URLs/hashes,
the target annotation, deposited component match, and receptor exclusions.

Pulsate resolved JQ1 to PubChem 46907787, I-BET762 to 46943432, erlotinib to
176870, and gefitinib to 123631. Full identity matching did not find a deposited
component for the resolved JQ1 record, so the BRD4 site was anchored by the exact
I-BET762 match; the JQ1 candidate was retained and computed, not discarded.

The selected BRD4 structure reports missing side-chain atoms at GLN A59 and
GLN A123. The selected EGFR structure reports missing residue records, retained
in full in its report and answer. Neither selected entry declares a mutation/
conflict in the checked records. The deposited missing-site-neighbor screen
flagged none. This is an annotation/proximity screen, not independent sequence
validation or proof that all disorder is absent. The answers identify modeled
atoms and warn that receptor conformation, protonation, sampling, and scoring
could change the ranking; no calibrated confidence interval is claimed.

## Regression verification

- Final backend/provider regression suite: 275 passed, including seven
  site-identity regressions.
- Frontend: 176 tests across 22 files passed; production build passed with the
  existing large-bundle warning.
- New real browser acceptance: 3 passed.
- Earlier nine real browser scenarios: all passed (supplied-structure discovery,
  candidate-set/confidence/follow-up reasoning, and public acquisition). Evidence
  is preserved in `.test-runs/target-selection-prior-regressions-20260929/`.
- `git diff --check` passed. No development files were staged. Production diff
  and new modules were scanned for the acceptance entity names/PDB identifiers;
  none were present in production additions.
- Read-only SHA-256 comparison confirmed all 11 deployed production files match
  the current local working tree used for this report.

This is bounded evidence for the tested question-to-answer paths, not a claim
that arbitrary targets, ligands, or binding-site policies are now supported.
