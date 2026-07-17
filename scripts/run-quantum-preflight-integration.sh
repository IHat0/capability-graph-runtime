#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
image="${CGR_QUANTUM_IMAGE:-cgr-quantum-preflight:1.0.0}"
log_destination="${1:-}"
image_id="$(docker image inspect --format '{{.Id}}' "$image")"
printf 'image_identifier=%s\n' "$image_id"
if [[ -z "$log_destination" ]]; then
  log_destination="$(mktemp)"
  trap 'rm -f "$log_destination"' EXIT
else
  mkdir -p "$(dirname "$log_destination")"
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
  --tmpfs /tmp:rw,nosuid,nodev,size=1g \
  --env CGR_QUANTUM_INTEGRATION=1 \
  --env "CGR_QUANTUM_IMAGE_ID=$image_id" \
  --entrypoint python \
  "$image" -m pytest -m quantum_integration -rs -q 2>&1 | tee "$log_destination"
pytest_status=${PIPESTATUS[0]}
set -e
if [[ $pytest_status -ne 0 ]]; then
  exit "$pytest_status"
fi
if grep -Eq '[1-9][0-9]* skipped' "$log_destination" || ! grep -Eq '[1-9][0-9]* passed' "$log_destination"; then
  printf 'Enabled integration must execute at least one test and may not report skipped success.\n' >&2
  exit 4
fi
