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

CGR_CONFIGURED_EVALUATOR_PYTHON="$evaluator_python" "$evaluator_python" - <<'PY'
import importlib.metadata
import os
import pathlib
import sys
import swebench
import swebench.harness

print(f"configured_evaluator_python={os.path.abspath(os.path.expanduser(os.environ['CGR_CONFIGURED_EVALUATOR_PYTHON']))}")
print(f"reported_evaluator_python={sys.executable}")
print(f"swebench_version={importlib.metadata.version('swebench')}")
print(f"swebench_package={pathlib.Path(swebench.__file__).resolve()}")
print(f"swebench_harness={pathlib.Path(swebench.harness.__file__).resolve()}")
PY
