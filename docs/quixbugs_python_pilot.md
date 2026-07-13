# QuixBugs Python Pilot

This pilot runs exactly one pinned Python task, `quixbugs.gcd`, through CGR's
proven full-cycle controller and pristine official SWE-agent v1.1.0. It is an
integration pilot, not full QuixBugs support.

## Pinned Inputs

- QuixBugs repository: `https://github.com/jkoppel/QuixBugs`
- QuixBugs commit: `4257f44b0ff1181dedaedee6a447e133219fcebf`
- SWE-agent commit: `0f3acafacabc0def8cc76b4e48acb4b6cf302cb9`
- Python dependency: `pytest==8.3.5`
- Verifier: `python -m pytest -q python_testcases/test_gcd.py`

Prepare the canonical read-only source checkout:

```bash
export CGR_QUIXBUGS_PYTHON="$PWD/.sandbox-sweagent-venv/bin/python"
scripts/prepare_quixbugs_pilot.sh .quixbugs-src
```

The preparation command pins and cleans only known generated test state, checks
the selected files and commit, and requires the buggy task's verifier to fail.
Each pilot attempt uses a fresh disposable clone; the canonical checkout is not
modified by SWE-agent. The disposable clone replaces its inherited host-local
origin with `./.git/cgr-origin.bundle`. That bundle contains the pinned commit,
travels with the uploaded Git repository, and lets SWE-agent's normal `git
fetch` initialization run without network or access to the canonical checkout.

## Local Integration Proof

```bash
cgr-quixbugs-pilot \
  --mode baseline \
  --max-attempts 1 \
  --task-id quixbugs.gcd \
  --quixbugs-root .quixbugs-src \
  --deterministic-model \
  --deployment-type local
```

The deterministic option starts a local OpenAI-compatible endpoint. Responses
still pass through the normal provider API and drive the real SWE-agent loop,
shell, submission, Git diff, verifier, and artifact pipeline.

## Attempt-Level CGR Repair

Baseline mode preserves the original single-attempt directory and result
schema. CGR mode runs that same complete baseline attempt as a child process.
If it is unresolved, CGR diagnoses the real trajectory and repository state,
stores concise corrective evidence, and launches one fresh child attempt at
the pinned commit:

```bash
cgr-quixbugs-pilot \
  --mode cgr \
  --max-attempts 2 \
  --task-id quixbugs.gcd \
  --quixbugs-root .quixbugs-src \
  --deployment-type docker
```

Baseline accepts exactly one attempt; CGR accepts one to three in this version.
The default remains two attempts, while `--max-attempts 3` enables two bounded
diagnosis-and-repair transitions. CGR results use `run-NNN/` with
`run-result.json`, numbered `diagnosis-NNN.json` and
`corrective-message-NNN.md` transitions, child `attempt-NNN/` directories, and
`selected.patch` when available. Each fresh attempt receives only the latest
correction. Selection prefers a passing verifier, nonempty patch, tests,
tracked changes, target inspection, then the later attempt. No LLM judges
trajectory prose.

Before SWE-agent starts, CGR copies the pinned host pytest runtime and its
pure-Python dependencies into `.git/cgr-test-runtime`. A post-startup command
imports that runtime inside the deployed environment before the first model
request. Corrections advertise the resulting `PYTHONPATH=.git/cgr-test-runtime
<agent-python> -m pytest ...` command, never a host-only interpreter path in
Docker. The same startup gate verifies noninteractive `python` and `sed`
editing mechanisms; guidance prohibits interactive editors, commits, pushes,
and Git-remote changes.

Test telemetry combines each action with its observation. A missing pytest
module, missing executable, or pre-start permission failure is an environment
failure, not an executed test. Selection credits tests only after recognizable
pytest pass or failure output.

## External Model Run

Set the existing provider variables and omit `--deterministic-model`:

```bash
export CGR_DRAFT_BASE_URL="http://127.0.0.1:8000/v1"
export CGR_DRAFT_API_KEY="${CGR_DRAFT_API_KEY:?set the local provider key}"
export CGR_DRAFT_MODEL="Qwen/Qwen2.5-Coder-7B-Instruct"
export CGR_DRAFT_MAX_MODEL_LEN=16384
export CGR_SWE_AGENT_SOURCE="$PWD/.sandbox-sweagent-src"
export CGR_SWE_AGENT_PYTHON="$PWD/.sandbox-sweagent-venv/bin/python"
export CGR_SWE_AGENT_EXECUTABLE="$PWD/.sandbox-sweagent-venv/bin/sweagent"

cgr-quixbugs-pilot \
  --mode baseline \
  --max-attempts 1 \
  --task-id quixbugs.gcd \
  --quixbugs-root .quixbugs-src \
  --deployment-type docker
```

Baseline results remain under `quixbugs.gcd/attempt-NNN`; CGR parent results
use `quixbugs.gcd/run-NNN`. A model failure, no patch, failing verifier, or
exhausted budget is serialized as a task outcome; infrastructure failures
remain distinct.

## Bounded Actionable Recovery

`--max-attempts` remains the configured base-attempt budget. With three base
attempts, CGR may grant one final actionable recovery attempt, up to an absolute
hard cap of four, only when the last scheduled attempt reveals a grounded
successful command that made no target-file change and therefore requires a
confirmed edit. Generic budget exhaustion does not qualify. Result artifacts
record `configured_base_attempts`, `actionable_recovery_attempts`, and
`absolute_hard_cap` separately.
