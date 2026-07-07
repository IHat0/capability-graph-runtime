# Capability Graph Runtime

CGR is a model-agnostic capability runtime that can route, execute, verify,
fuse, compete, and learn over plugins.

# CGR Demo

Install:

```bash
python -m pip install -e ".[dev]"
```

Run checks:

```bash
pytest
ruff check .
mypy .
```

Run the smoke test and end-to-end demo:

```bash
cgr-smoke
cgr-demo
```
