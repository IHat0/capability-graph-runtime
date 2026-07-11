# SWE-bench Verified Pilot Protocol

## Scope

`coding_repo_v0` was a development benchmark, not external evidence. SWE-bench
Verified is CGR's first serious external benchmark stage. The first evaluation is a
blind, precommitted ten-instance pilot drawn from
`princeton-nlp/SWE-bench_Verified`.

This is not a full 500-instance score. For example, 2/10 means 20% on this pilot,
not 20% on the full benchmark. Reports must acknowledge substantial small-sample
uncertainty. The first result is preserved before instance-specific analysis or
tuning. Repeated tuning on these ten instances is no longer blind evidence. Future
stages should expand to 25, 50, and eventually all 500 Verified instances.

## Integrity Boundary

Generation may use the public issue, the repository at `base_commit`, visible tests,
and generic local checks. Model-facing records use an allowlist and exclude `patch`,
`test_patch`, `gold_patch`, `FAIL_TO_PASS`, `PASS_TO_PASS`, evaluator versions, and
expected changed files.

The official SWE-bench Docker harness is the final judge. It runs only after all
predictions are generated, locked, and SHA-256 hashed. Official failures never enter
generation or repair for the same attempt. A locally passing patch is not called
resolved until the official harness reports it.

No pilot instance may be replaced after observing performance. No task-specific
rule, post-`base_commit` history, solution pull request, future commit, or
answer-revealing discussion may be used.

## Frozen Selection

The manifest is `benchmark-manifests/swebench-verified-pilot-v1.json`. Selection uses
only `instance_id`, repository, and `base_commit`. Records are ranked by:

```text
SHA-256("cgr-swebench-verified-pilot-v1" + NUL + instance_id)
```

One ranked instance per repository is selected first, followed by ranked fill to ten.
IDs are sorted before hashing and freezing. The command refuses to overwrite a frozen
manifest without the explicit development-only force flag.

```bash
cgr-swebench-freeze-pilot
cgr-swebench-integrity-check
```

## Environment And Gold Smoke

Install the optional integration and inspect prerequisites:

```bash
pip install -e ".[swebench]"
cgr-swebench-doctor
```

The doctor never contacts the model. It reports Docker CLI/daemon state, platform,
architecture, disk, Git, required packages, Qwen configuration, and manifest state.
Linux is the supported local Docker harness platform. Limitations are reported
honestly; there is no internal-evaluator fallback.

When Docker is healthy, validate the official harness without Qwen:

```bash
cgr-swebench-gold-smoke
```

This evaluates gold for `sympy__sympy-20590`, one worker, run ID
`cgr-gold-smoke`. Success is reported only when the official report marks it resolved.

## Fair Modes And Budgets

All modes use the same Qwen identity and repository-action adapter.

- `baseline`: one trajectory, at most 8 calls, 20 steps, and 1800 seconds.
- `cgr_single`: one primary plus bounded repair, at most 10 calls, 24 steps, and 2100 seconds.
- `cgr_multi`: three trajectories, at most 18 calls, 36 steps, and 3600 seconds.

The first-party adapter is configured through `CGR_SWEBENCH_AGENT_COMMAND`, a JSON
argument array using `{workspace}`, `{problem_file}`, `{mode}`, `{max_steps}`, and
`{max_calls}`. It uses `CGR_DRAFT_API_KEY`, `CGR_DRAFT_BASE_URL`, and
`CGR_DRAFT_MODEL` for the OpenAI-compatible Qwen endpoint.

```bash
export CGR_SWEBENCH_SCAFFOLD_ID="cgr-first-party-agent-v1"
export CGR_SWEBENCH_AGENT_COMMAND='[
  "cgr-swebench-agent",
  "--workspace", "{workspace}",
  "--problem-file", "{problem_file}",
  "--mode", "{mode}",
  "--max-steps", "{max_steps}",
  "--max-calls", "{max_calls}"
]'
```

The first-party adapter is CGR's bounded action layer, not an external scaffold.

For a vLLM endpoint with a constrained context window, configure the matching
limit before generation. The agent conservatively estimates prompt tokens, reserves
completion space, and sends an explicit `max_tokens` value on each chat request.

```bash
export CGR_DRAFT_MAX_MODEL_LEN=4096
export CGR_DRAFT_MAX_COMPLETION_TOKENS=512
```

If the complete issue plus bounded initial repository context cannot fit, the agent
fails before making a provider request. It removes only nonsemantic whitespace from
the issue; it never cuts a code block or silently drops requirements. Bounded tool
outputs explicitly identify truncation so the model can refine its next request.

The bounded repository surface supports file listing/search/reads, visible tests,
edits, patch application, diff inspection, and candidate reversion. `.git`, path
traversal, network actions, and answer-seeking history commands are denied.

The first-party agent's canonical JSON actions are `list_files`, `search_text`,
`read_file`, `inspect_symbols`, `write_file`, `replace_text`, `apply_patch`,
`run_tests`, `inspect_diff`, `revert`, and `finish`. It also normalizes the limited
compatibility aliases `edit_file`, `grep`, `git_diff`, `revert_candidate`, and
`done` before validating their canonical field schemas. `finish` succeeds only
when a non-empty Git diff remains in the workspace.

## Generation Failures And Debugging

The first-party agent requests OpenAI-compatible JSON-object mode. If a provider
explicitly rejects `response_format`, the same request is retried once without that
option; this transport retry is recorded in the debug trace and does not create an
extra repository action. The returned action must still be a single JSON object,
either raw or inside one complete Markdown code fence. Prose, malformed JSON,
unknown actions, and invalid action fields are rejected. One correction request is
then allowed within the configured model-call budget:

```text
Return only one valid JSON action object matching the required schema.
```

An agent exits nonzero when it exhausts its call or step budget, cannot recover a
valid action, encounters an unrecoverable action failure, or reaches `finish`
without a non-empty unified Git diff. A successful adapter exit therefore means a
valid candidate diff remains in its workspace, not merely that an API request
completed.

Use `--debug-trace` for a failed generation to retain redacted raw model output,
parse/schema errors, correction outcomes, and response-format fallback events in
the generation record. API keys are redacted. The pilot records adapter stdout and
stderr, exit code, and parsed agent debug trace alongside the specific
`generation_error`. HTTP diagnostics retain the provider status/body plus prompt
and completion-token budget, with API keys redacted. Any requested instance without
a candidate patch makes generation return nonzero and `generated: false`. It does not write an
empty `predictions.jsonl` or a success hash. For a partial run, successful and
failed instance IDs are reported separately; use `--resume` to retry the incomplete
set after fixing the underlying issue.

## Two-Phase Workflow

Do not run the provider until this infrastructure and manifest are committed,
integrity passes, gold smoke passes, and provider connectivity is confirmed.

```bash
# Phase 1: generation only
cgr-swebench-pilot --all-modes --generate-only

# Phase 2: locked official evaluation
cgr-swebench-integrity-check
cgr-swebench-pilot --all-modes --evaluate-only
```

Other bounded forms:

```bash
cgr-swebench-pilot --mode baseline --dry-run
cgr-swebench-pilot --instance-id <frozen-id> --mode cgr_single --generate-only
cgr-swebench-pilot --mode cgr_multi --resume
cgr-swebench-pilot --mode cgr_multi --debug-trace
```

Generation records local verification separately from official evaluation. Empty and
binary patches are rejected. Every prediction is a unified Git diff that applies at
the recorded base commit. Integrity checks prediction hashes, instance/base-commit
consistency, model identity, forbidden-field leakage, and phase separation.

Final reporting includes raw resolved rates, model-call/time totals, an instance
matrix, improvements/regressions, patch and harness failures, software versions,
manifest hash, and prediction hashes. Harness failures remain distinct from unresolved
patches.
