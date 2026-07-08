# Capability Graph Runtime

CGR is a model-agnostic capability runtime for building AI systems that can route, execute, verify, fuse, compete, and learn over AI/tool plugins.

Instead of hardcoding one model or one tool into an application, CGR lets models and tools register capabilities into a runtime. The runtime can then decide which plugin should handle a task, compare multiple candidates, fuse outputs, verify results, and record performance observations for better future routing.

## Why CGR Exists

Most AI systems call one model or tool directly. CGR treats models and tools as
plugins with declared capabilities. The runtime can route tasks to the right
plugin, compare outputs, fuse results, verify outputs, and record performance
observations for future routing.

## Current MVP Features

- Plugin contracts
- Plugin registry
- Kernel runtime
- Event bus
- Runtime health snapshots
- Capability router
- Memory-aware routing
- Learning memory
- Competition engine
- Fusion engine
- Verification layer
- Capability graph representation
- Graph execution engine
- External plugin loader
- Built-in calculator and text stats plugins
- Mock reasoning and coding model plugins
- CLI smoke/demo commands

## Architecture Overview

```text
User / CLI
    ↓
KernelRuntime
    ↓
CapabilityRouter
    ↓
PluginRegistry
    ↓
Plugins
```

Supporting systems:

- `EventBus` records runtime events.
- `LearningMemory` records execution observations.
- `CompetitionEngine` can run multiple candidates.
- `FusionEngine` can combine outputs.
- The verification layer can check outputs.
- `CapabilityGraph` can model workflows.

## Installation

```bash
python -m pip install -e ".[dev]"
```

## Validation

```bash
pytest
ruff check .
mypy .
```

## CLI Commands

Run the smoke command:

```bash
cgr-smoke
```

Expected output:

```json
{"message": "Hello CGR!"}
```

Run the end-to-end demo:

```bash
cgr-demo
```

It prints one JSON object containing:

- `model_pipeline`
- `calculator`
- `text_stats`
- `runtime_health`

## Built-in Demo Plugins

- `EchoPlugin` echoes its input for smoke testing.
- `CalculatorPlugin` evaluates restricted arithmetic expressions.
- `TextStatsPlugin` computes deterministic text statistics.
- `MockReasoningModelPlugin` produces deterministic reasoning responses.
- `MockCodingModelPlugin` produces deterministic coding responses.

## Project Status

This is an MVP runtime architecture. Deterministic local and mock model plugins
remain the default; an optional OpenAI-compatible Responses API provider is
available for real model calls.

## Optional OpenAI Provider Demo

The core demo uses local deterministic plugins. The OpenAI provider is optional
and is not required for tests or loaded by default.

PowerShell:

```powershell
$env:OPENAI_API_KEY="..."
$env:OPENAI_MODEL="gpt-4.1-mini"  # optional
cgr-openai-demo
```

## Local Benchmark

Run the deterministic local benchmark (no API keys required):

```bash
cgr-benchmark
```

Write machine-readable JSON results and a human-readable Markdown report:

```bash
cgr-benchmark --json-out benchmark-results/local.json --markdown-out benchmark-results/local.md
```

JSON is suitable for tooling and automation. Markdown is useful for README
notes, outreach, and benchmark summaries. The benchmark covers the calculator,
text stats, mock reasoning model, and mock coding model.

## Optional OpenAI Provider Benchmark

This tiny `model.reason` benchmark is intended for private provider evaluation.
It is not required for tests and may incur provider costs.

```powershell
$env:OPENAI_API_KEY="..."
cgr-openai-benchmark
cgr-openai-benchmark --json-out benchmark-results/openai.json --markdown-out benchmark-results/openai.md
```

## Real Coding-Agent A/B Evaluation

CGR can compare a direct model baseline with single-agent and multi-agent
draft/critique/repair coding modes. Exercise the full path locally without API
keys:

```bash
cgr-coding-ab-local
```

`cgr-coding-ab-real` runs the same local SWE-style tasks through two explicit
OpenAI-compatible providers configured with `CGR_DRAFT_*` and `CGR_CRITIC_*`
environment variables. This is the path toward GLM versus GLM+CGR measurement;
it makes real provider calls and may incur costs. Official SWE-bench and
SWE-bench Pro integration remain future work.

## Hard Coding A/B Evaluation

`cgr-coding-ab-real` runs the tiny sanity suite. `cgr-coding-ab-hard` runs eight
harder local coding challenges with executable tests and is the better command
for checking whether CGR improves a real model. It uses the same `CGR_DRAFT_*`
and `CGR_CRITIC_*` provider configuration and is still not official SWE-bench.

```bash
cgr-coding-ab-hard
cgr-coding-ab-hard --max-tasks 4 --retry-failed
cgr-coding-ab-hard --task-id hard.merge_counts --debug-trace
```

`--retry-failed` allows one additional multi-model semantic repair attempt using
the executable test source and failure diagnostics.
`--debug-trace` exposes candidate scores, repair counts, selected candidates,
and capped verifier/prompt previews for targeted task diagnosis.

## Coding v1 Benchmark

Coding v1 expands evaluation to 26 executable Python tasks. Baseline calls the
model directly, `cgr_single` adds verifier-guided repair, and `cgr_multi` adds
multi-candidate repair plus the monotonic single-path fallback. Visible tests
are available to repair prompts; hidden tests are used for final scoring but
their source is not shown to the model.

```bash
cgr-coding-ab-v1 --max-tasks 5
cgr-coding-ab-v1 --task-id v1.parse_bool_extended --debug-trace
cgr-coding-ab-v1 --runs 3
cgr-coding-ab-v1 --reference-check
cgr-coding-ab-v1 > benchmark-results/coding-v1.json
```

`--reference-check` runs the bundled reference implementations against both
visible and hidden tests locally without model credentials or provider calls.

## CGR Booster Engine

CGR's main product goal is to improve an LLM by wrapping it in orchestration
that generates candidates, critiques and repairs weak answers, verifies and
scores outputs, and selects the strongest result. The key comparison is **base
model alone versus base model + CGR**.

```bash
cgr-boost-local
```

This command exercises the complete comparison path with deterministic local
model fixtures. It proves the measurement and trace shape, not real model
improvement. Real GLM and OpenAI-compatible provider runs are the next step.

## Next Possible Steps

- Add additional real model provider plugins.
- Add persistence for `LearningMemory`.
- Add async and parallel execution.
- Add richer verifiers.
- Add benchmark-driven routing.
