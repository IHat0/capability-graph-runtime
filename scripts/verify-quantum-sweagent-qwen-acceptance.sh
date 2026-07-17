#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
acceptance_root="${1:?usage: verify-quantum-sweagent-qwen-acceptance.sh ACCEPTANCE_ROOT}"
host_python="${CGR_HOST_PYTHON:-python3}"
summary="$acceptance_root/model-provider-acceptance-summary.json"
report="$acceptance_root/model-provider-acceptance-report.json"
test -f "$summary" && test -f "$report"

while IFS= read -r receipt; do
  PYTHONPATH="$repo_root/src" "$host_python" -m cgr.quantum_repair.cli verify \
    "$(dirname "$receipt")"
done < <(find "$acceptance_root" -name repair-run-receipt.json -type f | sort)

"$host_python" - "$summary" "$report" <<'PY'
import json, pathlib, sys
summary = json.loads(pathlib.Path(sys.argv[1]).read_text())
report = json.loads(pathlib.Path(sys.argv[2]).read_text())
assert report["summary"] == summary
assert summary["model_provider_acceptance_passed"] is True
assert summary["safety_failures"] == 0
assert summary["repeatability_failures"] == 0
assert summary["missing_cases"] == 0 and summary["skipped_cases"] == 0
PY
printf 'verified_acceptance_summary=%s\n' "$summary"
