# Full-Cycle SWE-agent Sandbox

This local milestone runs CGR as the outer controller, official pristine
SWE-agent v1.1.0 as a subprocess, SWE-ReX local execution through Git Bash, and
a deterministic OpenAI-compatible model endpoint. The endpoint only returns
scripted model messages; it does not edit files or create SWE-agent artifacts.

The exact pinned SWE-agent source commit is:

```text
0f3acafacabc0def8cc76b4e48acb4b6cf302cb9
```

The isolated Windows runtime used for the demonstrated cycle contains
SWE-ReX 1.4.0 and LiteLLM 1.63.14. SWE-agent source is pristine: neither the
strict parser patch nor the action-validator patch is applied or required.

After creating `.sandbox-sweagent-venv` and cloning the pinned source into
`.sandbox-sweagent-src`, the canonical command is:

```powershell
.\.sandbox-sweagent-venv\Scripts\cgr-sandbox-full-cycle.exe
```

One command creates a fresh committed task repository, starts the local model
API, invokes the real CGR adapter and SWE-agent process, executes shell actions,
collects the official trajectory/prediction/patch, runs `unittest`, hashes the
artifacts, stops the model server, and returns a structured exit status.

Each run is retained under
`benchmark-results/sweagent-full-cycle-sandbox/attempt-NNN`. A task-level
failure is serialized, while an unhandled infrastructure exception produces a
nonzero top-level result with `failure-traceback.log` retained.
