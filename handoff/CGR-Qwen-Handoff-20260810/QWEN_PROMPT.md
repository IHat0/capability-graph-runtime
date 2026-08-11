# Prompt for Qwen

You are continuing work from a dedicated WIP branch in this repository:

- Repository: `IHat0/capability-graph-runtime`
- Branch: `qwen/pulsate-text-resolution-handoff-v1`
- Base commit: `4344155c83aabd0a9a6773f114b19d56afae0b2f`

Open that branch, then read
`handoff/CGR-Qwen-Handoff-20260810/HANDOFF_SUMMARY.md` completely before acting.
Inspect `repository-state.txt`, the branch diff from the base commit, and the
source and tests. The branch already contains the relevant newer working state.

Your task is to complete the one-question text-to-result workflow described in
the handoff. Implement and test it; do not merely audit or propose a design.

Hard constraints:

- Keep your new changes local unless the user explicitly asks you to publish
  them later.
- Do not deploy or create a pull request.
- Do not run `git checkout`, `reset`, `clean`, `stash`, `merge`, or `rebase`.
- Do not SSH into or modify the production server.
- Do not read, print, modify, or package credentials, tokens, key files, or
  environment files.
- Preserve all existing uncommitted and unrelated user changes.
- Never request or reproduce the user's private question.
- Never send a complete user question to an external service.
- Do not hardcode the previously tested example or any example-specific values.
- Use deterministic parsing and validation. Fail closed when evidence is
  insufficient.
- Safe local inspection, code editing, and non-destructive tests are authorized.

Required outcome:

```bash
python -m cgr.pulsate_api.scientific_cli "$CGR_QUESTION"
```

For a specific unambiguous question, that command must resolve inputs and the
internal contract, compile, execute, and return a result without additional
files or specialist flags. For genuine ambiguity, ask one plain-language
confirmation and automatically resume afterward.

Before editing:

1. Confirm the current branch and `git status --short`.
2. Inspect the supplied WIP branch state and its diff from the base commit.
3. Read the relevant resolver, CLI, objective, execution, runtime, handler, and
   test files.
4. Verify the handoff summary against the code; treat code as authoritative.

After implementation:

1. Run focused resolver/CLI tests.
2. Run the broader affected suite and lint.
3. Exercise the exact one-argument CLI sequence with neutral fixtures.
4. Report files changed, commands run, test results, remaining limitations, and
   final `git status --short`.
5. Do not commit or push.
