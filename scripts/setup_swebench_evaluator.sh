#!/usr/bin/env bash
set -euo pipefail

cd "$HOME/CGR-Ticket-1.1"

evaluator_version='3.0.17'
evaluator_root="$PWD/.venv-swebench-eval"
evaluator_python="$evaluator_root/bin/python"

if [[ ! -x "$evaluator_python" ]]; then
  python3.12 -m venv "$evaluator_root"
fi

if ! "$evaluator_python" -c \
  "import importlib.metadata,swebench,swebench.harness; assert importlib.metadata.version('swebench') == '$evaluator_version'" \
  >/dev/null 2>&1; then
  "$evaluator_python" -m pip install --quiet --upgrade pip
  "$evaluator_python" -m pip install --quiet "swebench==$evaluator_version"
fi

"$evaluator_python" - <<'PY'
import importlib.metadata
import pathlib
import swebench
import swebench.harness

print(f"evaluator_python={pathlib.Path(__import__('sys').executable).resolve()}")
print(f"swebench_version={importlib.metadata.version('swebench')}")
print(f"swebench_package={pathlib.Path(swebench.__file__).resolve()}")
print(f"swebench_harness={pathlib.Path(swebench.harness.__file__).resolve()}")
PY
