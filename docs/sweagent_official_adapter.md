# Official SWE-agent Scaffold

The production SWE-bench repository-solving scaffold is the official
[SWE-agent](https://github.com/SWE-agent/SWE-agent) release `v1.1.0` (release
commit `0f3acaf`). It requires Python 3.11 or newer. CGR's first-party
`cgr-swebench-agent` remains available for experiments but is deprecated for
benchmark generation.

Install it in an isolated environment on the Linux benchmark host. This does
not alter CGR's own virtual environment:

```bash
cd ~/CGR-Ticket-1.1
python3.12 -m venv .venv-sweagent
. .venv-sweagent/bin/activate
python -m pip install --upgrade pip
python -m pip install 'git+https://github.com/SWE-agent/SWE-agent.git@0f3acafacabc0def8cc76b4e48acb4b6cf302cb9'
```

Configure the local OpenAI-compatible vLLM endpoint and the official adapter:

```bash
export CGR_DRAFT_BASE_URL='http://127.0.0.1:8000/v1'
export CGR_DRAFT_API_KEY='cgr-aws-key'
export CGR_DRAFT_MODEL='Qwen/Qwen2.5-Coder-7B-Instruct'
export CGR_DRAFT_MAX_MODEL_LEN=16384
export CGR_SWEBENCH_SCAFFOLD_ID='swe-agent-v1.1.0-0f3acaf'
export CGR_SWE_AGENT_SOURCE="$HOME/CGR-Ticket-1.1/.swe-agent-src"
export CGR_SWE_AGENT_EXECUTABLE="$HOME/CGR-Ticket-1.1/.venv-sweagent/bin/sweagent"
export CGR_SWEBENCH_AGENT_COMMAND='[
  "cgr-swebench-swe-agent-adapter",
  "--workspace", "{workspace}",
  "--problem-file", "{problem_file}",
  "--mode", "{mode}",
  "--max-steps", "{max_steps}",
  "--max-calls", "{max_calls}"
]'
```

The adapter invokes `sweagent run` using the maintained `config/default.yaml`
and minimal local-model overrides: LiteLLM `openai/<model>` naming, the vLLM
base URL, `thought_action` parsing, zero monetary cost limits, the CGR model-call
budget, deterministic temperature, and an input/output split derived from
`CGR_DRAFT_MAX_MODEL_LEN`. It writes a second absolute YAML config with
`agent.history_processors: []` and a strict one-Bash-fence contract for local Qwen,
so cache-control-specific history handling and Anthropic-native editor instructions
are not enabled. The adapter validates and applies only the unified
patch exported by SWE-agent to CGR's temporary workspace. CGR then retains patch
validation, destructive-change rejection, prediction hashing, and official
evaluation ownership.

For a manual adapter smoke on EC2, make a disposable Git repository and invoke
the command above. A successful run has a non-empty `git diff`, a SWE-agent
trajectory under the workspace's parent `.cgr-sweagent-trajectories` directory,
and a JSON result with `ok: true`. Logs and CGR records redact `CGR_DRAFT_API_KEY`.

Do not run official evaluation until generation has completed and prediction
hashes are locked. The frozen manifest, evaluator, dataset, and vLLM identity are
not changed by this adapter.
