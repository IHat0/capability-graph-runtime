#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "The approved Qiskit pre-submission acceptance runs only on Linux." >&2
  exit 2
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="${PYTHON:-python3}"

exec "$python_bin" \
  "$repo_root/scripts/run-pulsate-approved-qiskit-preflight-acceptance.py"
