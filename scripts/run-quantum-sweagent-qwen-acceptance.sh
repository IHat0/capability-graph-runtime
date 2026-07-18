#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
trusted_reference="${1:?usage: run-quantum-sweagent-qwen-acceptance.sh TRUSTED_REFERENCE RESULT_ROOT PROVIDER_CONFIG SMOKE_REPORT}"
result_root="${2:-$HOME/cgr-evidence/quantum-model-repair}"
provider_config="${3:?Provide the generated immutable provider configuration.}"
smoke_report="${4:?Provide a passing provider-smoke-report.json.}"
candidate_image="${CGR_QUANTUM_CANDIDATE_IMAGE:-cgr-quantum-candidate:1.0.0}"
trusted_image="${CGR_QUANTUM_IMAGE:-cgr-quantum-preflight:1.0.0}"
cgr_python="${CGR_PYTHON:?Set CGR_PYTHON to the project Python executable.}"
: "${CGR_REPAIR_MODEL_API_KEY:?Set CGR_REPAIR_MODEL_API_KEY without printing it.}"
export CGR_SWE_AGENT_SOURCE="$repo_root/.swe-agent-src"
PYTHONPATH="$repo_root/src" "$cgr_python" -c 'import pydantic, cgr' || {
  echo "CGR_PYTHON cannot import required project dependencies." >&2; exit 2;
}

candidate_image_id="$(docker image inspect --format '{{.Id}}' "$candidate_image")"
trusted_image_id="$(docker image inspect --format '{{.Id}}' "$trusted_image")"
mkdir -p "$result_root/host-logs" "$result_root/candidate-output-probe"
test -w "$result_root/host-logs"
chmod 0733 "$result_root/candidate-output-probe"
docker run --rm --network none --read-only --user 10002 \
  --cap-drop ALL --security-opt no-new-privileges --pids-limit 16 \
  --mount "type=bind,src=$result_root/candidate-output-probe,dst=/output" \
  --entrypoint python "$candidate_image_id" \
  -c 'from pathlib import Path; Path("/output/model-provider-probe").write_text("ok")'

"$repo_root/scripts/check-quantum-sweagent-provider.sh" \
  "$provider_config" "$result_root/provider-health"
log="$result_root/host-logs/model-provider-acceptance.log"
set +e
PYTHONPATH="$repo_root/src" "$cgr_python" -m cgr.quantum_repair.cli model-acceptance \
  --manifest "$repo_root/benchmark-manifests/quantum-repair/lih-sweagent-qwen-acceptance-v1.json" \
  --provider-config "$provider_config" \
  --trusted-reference "$trusted_reference" \
  --result-root "$result_root" \
  --candidate-image "$candidate_image_id" \
  --candidate-lock "$repo_root/requirements/quantum-preflight.lock" \
  --fixture-root "$repo_root/benchmark-fixtures/quantum-repair-v1" \
  --diagnosis-support "$repo_root/benchmark-fixtures/quantum-candidate-v1/_support/standalone_candidate.py" \
  --smoke-report "$smoke_report" \
  2>&1 | tee "$log"
pipeline_status=("${PIPESTATUS[@]}")
controller_status="${pipeline_status[0]}"
tee_status="${pipeline_status[1]}"
set -e
(( tee_status == 0 )) || exit "$tee_status"
(( controller_status == 0 )) || exit "$controller_status"

summary="$(find "$result_root/quantum-model-repair" \
  -name model-provider-acceptance-summary.json -type f -printf '%T@ %p\n' |
  sort -n | tail -1 | cut -d' ' -f2-)"
test -n "$summary" && test -f "$summary"
"$cgr_python" - "$summary" <<'PY'
import json, pathlib, sys
value = json.loads(pathlib.Path(sys.argv[1]).read_text())
assert value["model_provider_acceptance_completed"] is True
assert value["model_provider_acceptance_passed"] is True
assert value["total_cases"] == 12
assert value["controls_authorized_without_provider"] == 1
assert value["false_authorizations"] == 0
assert value["false_intermediate_authorizations"] == 0
assert value["patch_policy_bypasses"] == 0
assert value["deterministic_fallback_invocations"] == 0
assert value["trusted_evidence_exposure_cases"] == 0
assert value["provider_trusted_evidence_access"] == 0
assert value["network_enabled_candidate_executions"] == 0
assert value["candidate_model_endpoint_access"] == 0
assert value["receipt_verification_failures"] == 0
assert value["replay_verification_failures"] == 0
assert value["budget_parity_failures"] == 0
assert value["provider_preflight_failures"] == 0
assert value["tool_sandbox_bootstrap_failures"] == 0
assert value["missing_cases"] == 0 and value["skipped_cases"] == 0
assert value["cgr_broken_cases_authorized"] >= 8
assert value["absolute_improvement"] >= 2
assert value["cgr_composite_cases_authorized"] >= 1
PY

test "$(git -C "$repo_root/.swe-agent-src" rev-parse HEAD)" = \
  "0f3acafacabc0def8cc76b4e48acb4b6cf302cb9"
test -z "$(git -C "$repo_root/.swe-agent-src" status --porcelain=v1 --untracked-files=all)"
printf 'trusted_image_id=%s\ncandidate_image_id=%s\nacceptance_summary=%s\n' \
  "$trusted_image_id" "$candidate_image_id" "$summary"
