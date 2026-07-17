#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
trusted_reference="${1:?usage: run-quantum-candidate-benchmark.sh TRUSTED_REFERENCE RESULT_ROOT}"
result_root="${2:?usage: run-quantum-candidate-benchmark.sh TRUSTED_REFERENCE RESULT_ROOT}"
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
  --entrypoint python "$candidate_image_id" -c 'from pathlib import Path; Path("/output/probe").write_text("ok")'; then
  printf 'Candidate UID 10002 cannot write the output root. Grant both UID 10002 and the host user write access (for example with a narrow ACL); ownership is never changed automatically.\n' >&2
  exit 2
fi

log="$result_root/host-logs/benchmark.log"
set +e
PYTHONPATH="$repo_root/src" "$host_python" -m cgr.quantum_candidate.cli \
  --manifest "$repo_root/benchmark-manifests/quantum-candidate/lih-candidate-benchmark-v1.json" \
  --trusted-reference "$trusted_reference" \
  --result-root "$result_root" \
  --fixture-root "$repo_root/benchmark-fixtures/quantum-candidate-v1" \
  --candidate-image "$candidate_image_id" \
  --candidate-lock "$repo_root/requirements/quantum-preflight.lock" 2>&1 | tee "$log"
pipeline_status=("${PIPESTATUS[@]}")
controller_status="${pipeline_status[0]}"
tee_status="${pipeline_status[1]}"
set -e
if (( tee_status != 0 )); then
  printf 'tee failed with status %s\n' "$tee_status" >&2
  exit "$tee_status"
fi
if (( controller_status != 0 )); then
  printf 'benchmark controller failed with status %s\n' "$controller_status" >&2
  exit "$controller_status"
fi

summary="$(find "$result_root/quantum-candidate-benchmark" -name benchmark-summary.json -type f -printf '%T@ %p\n' | sort -n | tail -1 | cut -d' ' -f2-)"
if [[ -z "$summary" || ! -f "$summary" ]]; then
  printf 'Benchmark summary is absent.\n' >&2
  exit 5
fi
"$host_python" - "$summary" <<'PY'
import json, pathlib, sys
summary = json.loads(pathlib.Path(sys.argv[1]).read_text())
assert summary["skipped_cases"] == 0, "benchmark cases were skipped"
assert summary["missing_cases"] == 0, "benchmark cases are missing"
assert summary["false_accepts"] == 0, "benchmark has false accepts"
assert summary["false_rejects"] == 0, "benchmark has false rejects"
assert summary["benchmark_passed"] is True, "benchmark did not pass"
print("trusted_image_id=" + summary.get("trusted_image_identifier", "verified-reference-package"))
print("candidate_image_id=" + summary["candidate_image_identifier"])
PY
report="${summary%summary.json}report.json"
printf 'trusted_image_id=%s\n' "$trusted_image_id"
printf 'benchmark_report=%s\n' "$report"
