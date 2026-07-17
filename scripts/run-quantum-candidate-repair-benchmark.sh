#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
trusted_reference="${1:?usage: run-quantum-candidate-repair-benchmark.sh TRUSTED_REFERENCE RESULT_ROOT}"
result_root="${2:?usage: run-quantum-candidate-repair-benchmark.sh TRUSTED_REFERENCE RESULT_ROOT}"
trusted_image="${CGR_QUANTUM_IMAGE:-cgr-quantum-preflight:1.0.0}"
candidate_image="${CGR_QUANTUM_CANDIDATE_IMAGE:-cgr-quantum-candidate:1.0.0}"
host_python="${CGR_HOST_PYTHON:-python3}"

trusted_image_id="$(docker image inspect --format '{{.Id}}' "$trusted_image")"
candidate_image_id="$(docker image inspect --format '{{.Id}}' "$candidate_image")"
mkdir -p "$result_root/host-logs" "$result_root/container-output-probe"
chmod 0733 "$result_root/container-output-probe"
if ! test -w "$result_root"; then
  printf 'Result root is not writable by the host user: %s\n' "$result_root" >&2
  exit 2
fi
if ! docker run --rm --network none --read-only --user 10002 \
  --mount "type=bind,src=$result_root/container-output-probe,dst=/output" \
  --entrypoint python "$candidate_image_id" -c 'from pathlib import Path; Path("/output/repair-probe").write_text("ok")'; then
  printf 'Candidate UID 10002 cannot write the output root; grant a narrow ACL to UID 10002 and the host user.\n' >&2
  exit 2
fi

log="$result_root/host-logs/quantum-repair-benchmark.log"
set +e
PYTHONPATH="$repo_root/src" "$host_python" -m cgr.quantum_repair.cli benchmark \
  --manifest "$repo_root/benchmark-manifests/quantum-repair/lih-candidate-repair-benchmark-v1.json" \
  --diagnosis-manifest "$repo_root/benchmark-manifests/quantum-candidate/lih-candidate-benchmark-v1.json" \
  --trusted-reference "$trusted_reference" \
  --result-root "$result_root" \
  --candidate-image "$candidate_image_id" \
  --candidate-lock "$repo_root/requirements/quantum-preflight.lock" \
  --fixture-root "$repo_root/benchmark-fixtures/quantum-repair-v1" \
  --diagnosis-support "$repo_root/benchmark-fixtures/quantum-candidate-v1/_support/standalone_candidate.py" 2>&1 | tee "$log"
pipeline_status=("${PIPESTATUS[@]}")
controller_status="${pipeline_status[0]}"
tee_status="${pipeline_status[1]}"
set -e
if (( tee_status != 0 )); then
  exit "$tee_status"
fi
if (( controller_status != 0 )); then
  exit "$controller_status"
fi
summary="$(find "$result_root/quantum-repair-benchmark" -name repair-benchmark-summary.json -type f -printf '%T@ %p\n' | sort -n | tail -1 | cut -d' ' -f2-)"
if [[ -z "$summary" || ! -f "$summary" ]]; then
  printf 'Repair benchmark summary is absent.\n' >&2
  exit 12
fi
"$host_python" - "$summary" <<'PY'
import json, pathlib, sys
summary = json.loads(pathlib.Path(sys.argv[1]).read_text())
assert summary["repair_benchmark_passed"] is True
assert summary["missing_cases"] == 0 and summary["skipped_cases"] == 0
assert summary["false_intermediate_authorizations"] == 0
assert summary["network_enabled_executions"] == 0
assert summary["trusted_evidence_exposure_cases"] == 0
print(pathlib.Path(sys.argv[1]).with_name("repair-benchmark-report.json"))
PY
printf 'trusted_image_id=%s\ncandidate_image_id=%s\nrepair_benchmark_summary=%s\n' \
  "$trusted_image_id" "$candidate_image_id" "$summary"
