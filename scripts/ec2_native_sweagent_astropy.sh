#!/usr/bin/env bash
set -euo pipefail

cd "$HOME/CGR-Ticket-1.1"

expected_branch='feat/swebench-verified-pilot'
upstream_commit='0f3acafacabc0def8cc76b4e48acb4b6cf302cb9'
expected_patch_sha256='5914d306f77feaf5e1252de96b14357822127f898b574f93e2468cab3c3f4a28'
instance_id='astropy__astropy-7671'
manifest='benchmark-manifests/swebench-verified-pilot-v1.json'
result_root="$PWD/benchmark-results/swebench-native-pilot-v1"

: "${CGR_NATIVE_EXPECTED_CGR_COMMIT:?Set CGR_NATIVE_EXPECTED_CGR_COMMIT to the pushed CGR commit.}"
: "${CGR_DRAFT_BASE_URL:?Set CGR_DRAFT_BASE_URL.}"
: "${CGR_DRAFT_API_KEY:?Set CGR_DRAFT_API_KEY.}"
: "${CGR_DRAFT_MODEL:?Set CGR_DRAFT_MODEL.}"
: "${CGR_DRAFT_MAX_MODEL_LEN:?Set CGR_DRAFT_MAX_MODEL_LEN.}"
: "${CGR_SWE_AGENT_SOURCE:?Set CGR_SWE_AGENT_SOURCE.}"
: "${CGR_SWE_AGENT_EXECUTABLE:?Set CGR_SWE_AGENT_EXECUTABLE.}"
[[ "$CGR_DRAFT_MAX_MODEL_LEN" == "16384" ]]

source .venv-sweagent/bin/activate
export CGR_SWEBENCH_EVALUATOR_PYTHON="$PWD/.venv-swebench-eval/bin/python"
[[ -x "$CGR_SWEBENCH_EVALUATOR_PYTHON" ]] || {
  echo "Missing dedicated evaluator runtime. Run scripts/setup_swebench_evaluator.sh." >&2
  exit 2
}

[[ "$(git branch --show-current)" == "$expected_branch" ]]
[[ "$(git rev-parse HEAD)" == "$CGR_NATIVE_EXPECTED_CGR_COMMIT" ]]
[[ "$(git -C "$CGR_SWE_AGENT_SOURCE" rev-parse HEAD)" == "$upstream_commit" ]]
patch_sha256="$(sha256sum patches/sweagent-v1.1.0-strict-thought-action.patch | awk '{print $1}')"
[[ "$patch_sha256" == "$expected_patch_sha256" ]]
git -C "$CGR_SWE_AGENT_SOURCE" apply --reverse --check \
  "$PWD/patches/sweagent-v1.1.0-strict-thought-action.patch"

sweagent_python="${CGR_SWE_AGENT_PYTHON:-$(dirname "$CGR_SWE_AGENT_EXECUTABLE")/python}"
export CGR_SWE_AGENT_PYTHON="$sweagent_python"
sweagent_identity="$("$sweagent_python" -c 'import os, pathlib, sys, sweagent; source = pathlib.Path(os.environ["CGR_SWE_AGENT_SOURCE"]).resolve(); imported = pathlib.Path(sweagent.__file__).resolve(); assert source in imported.parents, imported; print(f"{sys.executable}|{imported}")')"
"$sweagent_python" -c 'from sweagent.tools.parsing import StrictThoughtActionParser; assert StrictThoughtActionParser().type == "strict_thought_action"'
evaluator_identity="$("$CGR_SWEBENCH_EVALUATOR_PYTHON" -c 'import importlib.metadata,pathlib,sys,swebench,swebench.harness; version=importlib.metadata.version("swebench"); assert version == "3.0.17", version; print(f"{sys.executable}|{pathlib.Path(swebench.__file__).resolve()}|{pathlib.Path(swebench.harness.__file__).resolve()}|{version}")')"

curl --fail --silent --show-error \
  -H "Authorization: Bearer $CGR_DRAFT_API_KEY" \
  "$CGR_DRAFT_BASE_URL/models" >/dev/null
cgr-swebench-integrity-check --manifest "$manifest" \
  --result-root benchmark-results/swebench-verified-pilot-v1 >/dev/null
"$CGR_SWEBENCH_EVALUATOR_PYTHON" -c 'import swebench, swebench.harness'
docker info >/dev/null

available_kb="$(df -Pk "$PWD" | awk 'NR==2 {print $4}')"
[[ "$available_kb" -ge 52428800 ]] || {
  echo "At least 50 GiB free disk space is required." >&2
  exit 2
}
mkdir -p "$result_root"
[[ -w "$result_root" ]]

command=(
  cgr-swebench-native-pilot
  --mode baseline
  --instance-id "$instance_id"
  --manifest "$manifest"
  --result-root "$result_root"
  --generate-and-evaluate
)

printf 'branch=%s\ncommit=%s\n' "$expected_branch" "$CGR_NATIVE_EXPECTED_CGR_COMMIT"
printf 'sweagent_upstream_commit=%s\nparser_patch_sha256=%s\n' \
  "$upstream_commit" "$patch_sha256"
IFS='|' read -r reported_sweagent_python sweagent_package <<<"$sweagent_identity"
printf 'configured_sweagent_python=%s\nreported_sweagent_python=%s\nsweagent_package=%s\n' \
  "$sweagent_python" "$reported_sweagent_python" "$sweagent_package"
IFS='|' read -r reported_evaluator_python evaluator_package evaluator_harness evaluator_version <<<"$evaluator_identity"
printf 'configured_evaluator_python=%s\nreported_evaluator_python=%s\nswebench_package=%s\nswebench_harness=%s\nswebench_version=%s\n' \
  "$CGR_SWEBENCH_EVALUATOR_PYTHON" "$reported_evaluator_python" "$evaluator_package" "$evaluator_harness" "$evaluator_version"
printf 'model_endpoint=%s\nmodel_identifier=%s\n' \
  "$CGR_DRAFT_BASE_URL" "$CGR_DRAFT_MODEL"
printf 'command='
printf '%q ' "${command[@]}"
printf '\n'

run_log="$result_root/baseline-${instance_id}-runner.log"
set +e
"${command[@]}" 2>&1 | tee "$run_log"
benchmark_status=${PIPESTATUS[0]}

attempt_root="$result_root/baseline/$instance_id"
attempt="$(find "$attempt_root" -mindepth 1 -maxdepth 1 -type d -name 'attempt-*' -print 2>/dev/null | sort | tail -n 1 || true)"
printf 'benchmark_exit_code=%s\n' "$benchmark_status"
printf 'runner_log=%s\n' "$run_log"
printf 'artifact_directory=%s\n' "${attempt:-not-produced}"

if [[ -n "$attempt" && -f "$attempt/generation-result.json" ]]; then
  ATTEMPT="$attempt" python - <<'PY'
import json
import os
from pathlib import Path

attempt = Path(os.environ["ATTEMPT"])
generation = json.loads((attempt / "generation-result.json").read_text())
evaluation_path = attempt / "official-evaluation" / "evaluation-result.json"
evaluation = json.loads(evaluation_path.read_text()) if evaluation_path.exists() else {}
print(f"generation_exit_code={generation.get('generation_exit_code')}")
print(f"generation_status={generation.get('infrastructure_status')}")
print(f"prediction_path={generation.get('prediction_path')}")
print(f"prediction_sha256={generation.get('prediction_sha256')}")
print(f"evaluation_exit_code={evaluation.get('evaluation_exit_code')}")
print(f"evaluation_status={evaluation.get('infrastructure_status')}")
print(f"official_resolved={evaluation.get('resolved')}")
print(f"official_report_path={evaluation.get('official_report_path')}")
PY
  trajectory="$(find "$attempt" -type f -name '*.traj' -print | head -n 1 || true)"
  patch="$(find "$attempt" -type f -name '*.patch' -print | head -n 1 || true)"
  printf 'trajectory_path=%s\n' "${trajectory:-not-produced}"
  printf 'patch_path=%s\n' "${patch:-not-produced}"
fi

printf 'The benchmark command status was captured; this shell remains available.\n'
