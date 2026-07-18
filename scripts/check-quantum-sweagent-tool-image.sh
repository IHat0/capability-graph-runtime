#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
provider_config="${1:?usage: check-quantum-sweagent-tool-image.sh PROVIDER_CONFIG [EVIDENCE_ROOT]}"
evidence_root="${2:-$HOME/cgr-evidence/quantum-model-repair/tool-preflight}"
cgr_python="${CGR_PYTHON:?Set CGR_PYTHON to the project Python executable.}"
PYTHONPATH="$repo_root/src" "$cgr_python" -c 'import pydantic, cgr' || {
  echo "CGR_PYTHON cannot import required project dependencies." >&2; exit 2;
}
mkdir -p "$evidence_root"
PYTHONPATH="$repo_root/src" "$cgr_python" -m cgr.quantum_repair.cli tool-check \
  --provider-config "$provider_config" --evidence-root "$evidence_root"
