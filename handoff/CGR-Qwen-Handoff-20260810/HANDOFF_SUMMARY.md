# CGR Qwen branch handoff

Created: 2026-08-10 (Africa/Johannesburg)

This dedicated GitHub branch transfers the relevant local working state and
the remaining implementation problem to another coding agent. It contains no
private question, credentials, environment files, server keys, access tokens,
cached runs, or deployment data.

## Repository state

- GitHub repository: `IHat0/capability-graph-runtime`
- Remote URL: `https://github.com/IHat0/capability-graph-runtime.git`
- Handoff branch: `qwen/pulsate-text-resolution-handoff-v1`
- Base branch: `feat/pulsate-scientific-autonomy-v1`
- Base commit: `4344155c83aabd0a9a6773f114b19d56afae0b2f`
- Head title: `Stabilize hybrid QM MM transition verification`
- Local checkout used for this export: `C:\Projects\CGR\CGR-Pulsate`
- Production checkout used previously: `~/CGR-Pulsate-CrossPhase`

The handoff branch is a deliberately labeled WIP snapshot. It was not merged
or deployed, and the original working checkout was not staged or committed.

## Requested outcome

The user should enter one ordinary natural-language question with:

```bash
python -m cgr.pulsate_api.scientific_cli "$CGR_QUESTION"
```

For a sufficiently specific request, the program should automatically resolve
the required records and internal execution contract, run the existing plan,
and return a result. The user should not have to provide files, database IDs,
internal query syntax, atom labels, charge, spin, or other specialist flags.

When multiple materially different interpretations remain, the program may ask
one concise plain-language confirmation. It must then continue automatically;
the user must not have to reconstruct a long command.

The private question must remain outside coding-agent conversations. Develop
and test with neutral fixtures. Final private testing is performed directly by
the user through the local or server CLI.

## Current behavior

The main pipeline already contains:

1. Natural-language task classification.
2. A multi-step capability planner.
3. Preparation and execution handlers.
4. Verification, persistence, and result assembly.
5. Authenticated upload, compile, status, and execution endpoints.

The current text-only path can recognize the request, acquire an explicitly
identified public record, upload it, and compile a plan. It currently stops at
`clarification_required` because it has not yet automatically:

1. Split an embedded secondary input from the downloaded record.
2. Resolve exactly one secondary input when several non-primary components are
   present.
3. Identify the relevant source-side target.
4. Identify and map the corresponding secondary-input target and partner.
5. Derive and validate the required execution-state values.
6. Record a reviewed, evidence-backed contract and continue execution.

An additional implementation finding is important: the existing executor
assumes one internal transition pattern, while downloaded records may represent
a second pattern. Automatic grounding must distinguish these patterns and route
them correctly instead of forcing every record into the existing assumption.

## Earlier failures and what they established

- Placeholder strings supplied to typed CLI options failed argument parsing.
  This was expected: placeholders are not usable values.
- The initial text-only plan call failed authentication when the client token
  was missing or stale.
- After credential repair, the request acquired the exact public record and
  compiled successfully, proving ingress, authentication, acquisition, upload,
  classification, and planning were connected.
- Compilation then stopped at the two grounding blockers described above,
  proving the remaining failure is application integration rather than SSH,
  environment activation, or basic API connectivity.

## Repairs already present in this working state

The local state includes work that:

- activates bounded exact-record acquisition with provenance;
- prevents the complete private question from being sent to external record
  services;
- adds CLI options for exact identifiers and reviewed internal values;
- threads a validated execution contract through objective compilation and
  execution records;
- replaces fixture-specific target selection with explicit identity and unique
  mapped-query resolution;
- improves verification evidence and converts uncontrolled iterator failures
  into controlled diagnostic failures;
- adds authenticated cross-phase tests and acquisition tests;
- contains adjacent deployment, recovery, runtime, and security hardening that
  was already uncommitted in this checkout.

The CLI options are an intermediate diagnostic interface, not the final user
experience. The missing automatic resolver still needs to generate those values
without requiring the user to know them.

## Proposed completion design

Implement a deterministic resolver before objective compilation:

1. Parse the question locally into task intent and bounded entity candidates.
2. Prefer exact identifiers present in the question.
3. For names, query trusted public indexes using only the bounded derived name,
   never the complete question.
4. Acquire the exact selected record with content hash and provenance.
5. Inspect record-level relationship data and coordinates.
6. Separate the primary and secondary inputs while excluding water, ions,
   buffers, and unrelated components.
7. Resolve the source-side target and the corresponding secondary-input target
   using declared links first and conservative geometry only as a fallback.
8. Determine which supported internal transition pattern applies.
9. Produce and validate the mapped internal query and execution-state values.
10. Record evidence, provenance, confidence, assumptions, and alternatives.
11. Auto-continue only for a unique high-confidence interpretation.
12. Otherwise present one plain-language confirmation and resume with the
    confirmed contract.
13. Return controlled diagnostics for unsupported or insufficiently grounded
    cases; never fabricate missing values.

The automatic resolver should be implemented as a separate, testable module.
The CLI should orchestrate it, while the server continues to enforce the final
validated contract.

## Acceptance criteria

- The exact one-argument CLI path is tested from text through compile and
  execute without manual files or internal flags.
- An embedded secondary input is extracted automatically.
- Common irrelevant components are excluded.
- Exact links are preferred over distance-only inference.
- Multiple plausible inputs or targets trigger a concise confirmation.
- Confirmation resumes automatically.
- Every derived value carries evidence and confidence.
- The complete private question is never submitted to external record services.
- Unsupported cases fail closed with a stable error code.
- Existing focused and broader tests remain green.
- No example-specific identifier, chain, residue, atom, or query is hardcoded.

## Validation evidence so far

- Focused and broader server-side repair suites previously completed with 20
  passing tests and lint success in the authoritative environment.
- One heavy acceptance execution completed all planned nodes and persisted a
  verified success after the verification repair.
- A later heavy numerical run did not locate the required stationary point; it
  now failed through a controlled diagnostic rather than an uncaught exception.
- A public smoke test completed acquisition, upload, compilation, and the
  approval boundary.
- The text-only private test remains partial: acquisition and planning work,
  but automatic grounding and continuation are not complete.

Do not claim that the full text-to-result experience already works.

## Branch layout

- The source and test files contain the actual relevant working-tree changes
  relative to base commit `4344155`.
- `repository-state.txt`: branch, commit, status, and diff statistics.
- `QWEN_PROMPT.md`: ready-to-paste continuation instructions.

The snapshot includes adjacent changes from earlier work in the same dirty
checkout. Review the diff; do not assume that every modified line originated
in one conversation.
