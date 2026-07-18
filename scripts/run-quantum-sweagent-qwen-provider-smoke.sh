#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
trusted_reference="${1:?usage: run-quantum-sweagent-qwen-provider-smoke.sh TRUSTED_REFERENCE PROVIDER_CONFIG [RESULT_ROOT]}"
provider_config="${2:?usage: run-quantum-sweagent-qwen-provider-smoke.sh TRUSTED_REFERENCE PROVIDER_CONFIG [RESULT_ROOT]}"
result_root="${3:-$HOME/cgr-evidence/quantum-model-repair/provider-smoke}"
cgr_python="${CGR_PYTHON:?Set CGR_PYTHON to the project Python executable.}"
: "${CGR_REPAIR_MODEL_API_KEY:?Set CGR_REPAIR_MODEL_API_KEY without printing it.}"
export CGR_SWE_AGENT_SOURCE="$repo_root/.swe-agent-src"
PYTHONPATH="$repo_root/src" "$cgr_python" -c 'import pydantic, cgr' || {
  echo "CGR_PYTHON cannot import required project dependencies." >&2; exit 2;
}
candidate_image="${CGR_QUANTUM_CANDIDATE_IMAGE:-cgr-quantum-candidate:1.0.0}"
candidate_image_id="$(docker image inspect --format '{{.Id}}' "$candidate_image")"
mkdir -p "$result_root"
PYTHONPATH="$repo_root/src" "$cgr_python" -m cgr.quantum_repair.cli provider-smoke \
  --manifest "$repo_root/benchmark-manifests/quantum-repair/lih-sweagent-qwen-acceptance-v1.json" \
  --provider-config "$provider_config" --trusted-reference "$trusted_reference" \
  --result-root "$result_root" --candidate-image "$candidate_image_id" \
  --candidate-lock "$repo_root/requirements/quantum-preflight.lock" \
  --fixture-root "$repo_root/benchmark-fixtures/quantum-repair-v1" \
  --diagnosis-support "$repo_root/benchmark-fixtures/quantum-candidate-v1/_support/standalone_candidate.py"
