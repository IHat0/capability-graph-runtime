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

This is an MVP runtime architecture. It currently uses deterministic local
plugins and mock model plugins to demonstrate the core system.. Real external model integrations are not
included yet.

## Next Possible Steps

- Add the first real model provider plugin.
- Add persistence for `LearningMemory`.
- Add async and parallel execution.
- Add richer verifiers.
- Add benchmark-driven routing.
