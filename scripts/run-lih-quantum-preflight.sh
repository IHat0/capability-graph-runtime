#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
image="${CGR_QUANTUM_IMAGE:-cgr-quantum-preflight:1.0.0}"
result_root="${1:-$repo_root/quantum-preflight-results}"
mkdir -p "$result_root"
image_id="$(docker image inspect --format '{{.Id}}' "$image")"
lock_hash="$(sha256sum "$repo_root/requirements/quantum-preflight.lock" | awk '{print $1}')"
printf 'image_identifier=%s\nlock_sha256=%s\n' "$image_id" "$lock_hash"

timeout --signal=TERM --kill-after=10s 210s docker run --rm \
  --name "cgr-quantum-preflight-$$" \
  --network none \
  --read-only \
  --cpus 2 \
  --memory 4g \
  --pids-limit 256 \
  --stop-timeout 10 \
  --security-opt no-new-privileges \
  --cap-drop ALL \
  --tmpfs /tmp:rw,nosuid,nodev,size=512m \
  --mount "type=bind,src=$repo_root/benchmark-manifests/quantum-preflight/lih-ground-state-v1.json,dst=/input/manifest.json,readonly" \
  --mount "type=bind,src=$repo_root/requirements/quantum-preflight.lock,dst=/input/quantum-preflight.lock,readonly" \
  --mount "type=bind,src=$result_root,dst=/output" \
  --env "CGR_QUANTUM_IMAGE_ID=$image_id" \
  "$image" \
  --manifest /input/manifest.json \
  --lock-file /input/quantum-preflight.lock \
  --result-root /output \
  --max-seconds 180

find "$result_root/lih-ground-state-v1" -name receipt.json -print | sort | tail -n 1
