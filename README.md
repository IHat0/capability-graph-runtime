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

## Next Possible Steps

- Add additional real model provider plugins.
- Add persistence for `LearningMemory`.
- Add async and parallel execution.
- Add richer verifiers.
- Add benchmark-driven routing.
