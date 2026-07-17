#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
repair_run="${1:?usage: verify-quantum-candidate-repair-run.sh REPAIR_RUN_DIRECTORY}"
host_python="${CGR_HOST_PYTHON:-python3}"
PYTHONPATH="$repo_root/src" "$host_python" -m cgr.quantum_repair.cli verify "$repair_run"
