#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
image="${CGR_QUANTUM_IMAGE:-cgr-quantum-preflight:1.0.0}"
log_destination="${1:-}"
image_id="$(docker image inspect --format '{{.Id}}' "$image")"
printf 'image_identifier=%s\n' "$image_id"
if [[ -z "$log_destination" ]]; then
  if ! log_destination="$(mktemp)"; then
    printf 'Unable to create a temporary integration log.\n' >&2
    exit 10
  fi
  trap 'rm -f "$log_destination"' EXIT
else
  if ! mkdir -p "$(dirname "$log_destination")"; then
    printf 'Unable to create integration log directory: %s\n' "$(dirname "$log_destination")" >&2
    exit 10
  fi
fi
if ! : >> "$log_destination"; then
  printf 'Integration log is not writable: %s\n' "$log_destination" >&2
  exit 10
fi

set +e
docker run --rm \
  --network none \
  --read-only \
  --cpus 2 \
  --memory 4g \
  --pids-limit 256 \
  --security-opt no-new-privileges \
  --cap-drop ALL \
  --tmpfs /tmp:rw,nosuid,nodev,size=1g,mode=1777 \
  --env CGR_QUANTUM_INTEGRATION=1 \
  --env "CGR_QUANTUM_IMAGE_ID=$image_id" \
  --entrypoint python \
  "$image" -m pytest -q -s -rs -p no:cacheprovider \
  tests/test_quantum_preflight_integration.py::test_real_lih_reference_mutation_and_determinism \
  2>&1 | tee "$log_destination"
pipeline_status=("${PIPESTATUS[@]}")
set -e
integration_status=${pipeline_status[0]}
logging_status=${pipeline_status[1]}
printf 'integration_exit=%s\nlog_path=%s\n' "$integration_status" "$log_destination"
if [[ $integration_status -ne 0 ]]; then
  exit "$integration_status"
fi
if [[ $logging_status -ne 0 ]]; then
  printf 'Integration logging failed with exit code %s.\n' "$logging_status" >&2
  exit "$logging_status"
fi
if grep -Eq '(^|[^0-9])[1-9][0-9]* skipped([^0-9]|$)' "$log_destination"; then
  printf 'Enabled integration test was skipped.\n' >&2
  exit 4
fi
if ! grep -Eq '(^|[^0-9])[1-9][0-9]* passed([^0-9]|$)' "$log_destination"; then
  printf 'Enabled integration executed zero passing tests.\n' >&2
  exit 4
fi
printf 'OFFICIAL QUANTUM INTEGRATION PASSED\n'
