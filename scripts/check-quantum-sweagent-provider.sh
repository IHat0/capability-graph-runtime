#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
provider_config="${1:-$repo_root/benchmark-manifests/quantum-repair/sweagent-qwen-provider-v1.json}"
evidence_root="${2:-$HOME/cgr-evidence/quantum-model-repair/provider-health}"
host_python="${CGR_HOST_PYTHON:-python3}"
: "${CGR_REPAIR_MODEL_API_KEY:?Set CGR_REPAIR_MODEL_API_KEY without printing it.}"
export CGR_SWE_AGENT_SOURCE="$repo_root/.swe-agent-src"

test "$(git -C "$repo_root/.swe-agent-src" rev-parse HEAD)" = \
  "0f3acafacabc0def8cc76b4e48acb4b6cf302cb9"
test -z "$(git -C "$repo_root/.swe-agent-src" status --porcelain=v1 --untracked-files=all)"
test "$(git -C "$repo_root/.quixbugs-src" rev-parse HEAD)" = \
  "4257f44b0ff1181dedaedee6a447e133219fcebf"
test -z "$(git -C "$repo_root/.quixbugs-src" status --porcelain=v1 --untracked-files=all)"
mkdir -p "$evidence_root"
test -w "$evidence_root"
log="$evidence_root/provider-health.log"

set +e
PYTHONPATH="$repo_root/src" "$host_python" -m cgr.quantum_repair.cli provider-check \
  --provider sweagent-openai-compatible \
  --provider-config "$provider_config" \
  --evidence-root "$evidence_root" 2>&1 | tee "$log"
pipeline_status=("${PIPESTATUS[@]}")
provider_status="${pipeline_status[0]}"
tee_status="${pipeline_status[1]}"
set -e
(( tee_status == 0 )) || exit "$tee_status"
(( provider_status == 0 )) || exit "$provider_status"

test "$(git -C "$repo_root/.swe-agent-src" rev-parse HEAD)" = \
  "0f3acafacabc0def8cc76b4e48acb4b6cf302cb9"
test -z "$(git -C "$repo_root/.swe-agent-src" status --porcelain=v1 --untracked-files=all)"
printf 'provider_health_log=%s\n' "$log"
