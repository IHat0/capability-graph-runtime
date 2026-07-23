# Pulsate natural-language scientific input v1

Pulsate now treats an ordinary chemistry question as input to a review
workflow, not as executable scientific truth:

```text
question
  -> configured OpenAI-compatible language model
  -> strict server-owned draft schema
  -> deterministic validation and provenance checks
  -> scientist review and assumption acknowledgement
  -> immutable approved experiment
```

The production provider performs an HTTP `POST` to
`{PULSATE_NL_MODEL_BASE_URL}/chat/completions`. Its model, credential and
timeout come from `PULSATE_NL_MODEL_NAME`, `PULSATE_NL_MODEL_API_KEY`, and
`PULSATE_NL_MODEL_TIMEOUT_SECONDS`. No endpoint, key, or model is hard-coded.
The key is used only in the outbound authorization header and is excluded from
API data, provenance, persisted records, and sanitized provider errors.

The current EC2 Qwen service can be configured externally with an
OpenAI-compatible `/v1` base URL and its deployed model name. Tests inject a
narrow controlled provider, but that provider is explicitly identified as
`controlled_test_provider`; it is not production acceptance.

## Authoritative boundary

Model output is only a draft. The server bounds request and response sizes,
extracts one JSON object, validates it with a strict Pydantic schema that
forbids extra fields, and permits at most one bounded repair request. It never
executes model-generated code, shell text, file paths, environment variables,
or credentials.

Every scientific value carries one of four provenance states:

- `explicit`: stated by the scientist;
- `derived`: deterministically calculated from explicit evidence;
- `assumed`: a proposed default requiring acknowledgement;
- `missing`: not supplied and not safely inferable.

The raw model draft has an additional, input-only `explicit_evidence` map.
Its keys are restricted scientific field paths and each bounded value must be
an exact quotation grounded in the original question after Unicode and
whitespace normalization. The server then performs field-specific consistency
checks for identities, units, basis, mapper, and execution target. Numeric
evidence is context-specific: charge, multiplicity, shots, tolerance, precision,
and bond distances require their own conservative labels rather than merely a
matching number somewhere in the quotation. Scientific objectives must retain
grounded scientific meaning; a grounded energy request cannot authorize an
invented drug-binding objective.

Explicit Cartesian geometry uses a deliberately narrow parser. Each atom must
be a standalone chemical symbol or supported unambiguous element name directly
labeling a parenthesized or bracketed coordinate triple. The parsed atom count,
order, elements, and finite coordinates must exactly match the draft. Ordinary
words containing one-letter element symbols cannot ground atoms.

An ungrounded default is downgraded to an assumption when safe; an invented
atom list, coordinate set, or bond length becomes missing. Raw evidence is
never persisted as execution authority.

The deterministic post-processor may expand a valid molecular formula to an
atom-symbol sequence. For a diatomic with an explicit bond length, it may
derive centered Cartesian coordinates without changing the distance. General
molecule geometry is never invented. An omitted execution target becomes the
visible `ibm_quantum` assumption.

Interpretation is molecule-neutral. Names, formulas, SMILES, InChI, atom
symbols, coordinates, bond lengths, and geometry descriptions are preserved
without a molecule-name whitelist. Interpretation support and compiler support
are separate: a BeH2 or caffeine question can be represented even though the
current two-atom compiler cannot execute it. Missing geometry or scientific
settings produces `needs_clarification`; complete but unsupported structure or
method data produces `requires_compiler_capability`.

## HTTP contracts

`POST /api/v1/experiments/interpret`

```json
{"question": "Calculate the ground-state energy of lithium hydride at 1.6 angstrom using STO-3G on IBM Quantum."}
```

The `201` response contains an `interpretation_identifier`, original question,
strict `specification`, assumptions, missing information, warnings,
interpretation and execution-support statuses, secret-free model provenance,
and `scientist_approval_possible`.

`POST /api/v1/experiments/{interpretation_identifier}/approve`

```json
{
  "specification": {"schema_version": "cgr.pulsate-model-scientific-draft/1.0.0"},
  "accepted_assumptions": true
}
```

The body contains the complete reviewed specification, not merely the abbreviated
example above. The server revalidates it, recomputes deterministic findings,
rebuilds the authoritative assumption list from fields still marked `assumed`,
and rejects unresolved values or unacknowledged assumptions. Client-provided
assumption and warning arrays cannot weaken that decision. Every scientific
field changed from the stored interpretation is recorded in
`scientist_reviewed_overrides` in the immutable record. Formula/atom conflicts,
declared/Cartesian bond conflicts, unit mismatches, and invalid bond indices
block approval rather than surviving as warnings. The `201` response contains
the `experiment_identifier`, canonical `specification_sha256`, requested
execution target, pre-submission status, and override audit.

Provenance is also server-authoritative during review. If a submitted value is
unchanged, the stored provenance is preserved; changing only `assumed` to
`explicit` does not create an override or remove the acknowledgement
requirement. Only an actual value addition, change, or removal is audited as a
scientist override.

`GET /api/v1/experiments/interpreter/capability` reports safe provider identity
and a process-local model request counter for acceptance evidence.

Interpretation and approval never create a run, submit an IBM job, or start a
local scientific calculation. IBM Quantum is the intended execution target;
real IBM submission is the next milestone. Mol* visualization of newly
approved interpretations is also later work.

## Real Qwen acceptance

Configure the `PULSATE_NL_MODEL_*` variables for the real EC2 endpoint, then
run:

```bash
PULSATE_NL_ACCEPTANCE_PORT=8001 \
  bash scripts/run-pulsate-natural-language-acceptance.sh
```

The acceptance creates isolated interpretation, approval, experiment, and run
roots and starts a fresh API process from the current checkout on a dedicated
free port. That child receives the real model configuration but no IBM
credentials, cost acknowledgement, preflight handoff, or local execution
enablement. The script sends LiH, H2, BeH2, and incomplete caffeine questions,
checks their detailed scientific identities and quantities, requires a
production provider with a growing request count, and approves LiH only to
`ready_for_ibm_submission`.

No-execution evidence is observational: the isolated run tree is hashed before
and after, must remain identical, and the complete isolated tree is scanned for
run records, quantum or IBM worker directories, handoffs, submission attempts,
job identifiers, prepared submissions, job state, receipts, and execution
artifacts using the concrete names defined by the current IBM implementation.
The default repository interpretation, experiment, and run roots are also
snapshotted before the child starts and after acceptance, catching accidental
writes outside the isolated roots. On failure, the script prints only a bounded,
redacted tail of the fresh API log before removing temporary state.
The fresh API is terminated reliably, temporary state is removed, and at most
64 KiB of bounded, secret-free interpretation summaries are retained. Passing
unit tests alone is not real Qwen acceptance.
