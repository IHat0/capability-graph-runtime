#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
image="${CGR_QUANTUM_IMAGE:-cgr-quantum-preflight:1.0.0}"
result_root="${1:-$HOME/cgr-evidence/quantum-preflight}"
mkdir -p "$result_root"
image_id="$(docker image inspect --format '{{.Id}}' "$image")"
lock_hash="$(sha256sum "$repo_root/requirements/quantum-preflight.lock" | awk '{print $1}')"
git_commit="$(git -C "$repo_root" rev-parse HEAD)"
printf 'image_identifier=%s\nlock_sha256=%s\n' "$image_id" "$lock_hash"

if ! docker run --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges --pids-limit 16 --memory 64m --cpus 0.25 \
  --mount "type=bind,src=$result_root,dst=/output" --entrypoint /bin/sh "$image" \
  -c 'probe=/output/.cgr-write-probe-$$; : > "$probe" && rm -f "$probe"'; then
  printf 'Output is not writable by container UID 10001. Run: sudo chown -R 10001:10001 %q\n' "$result_root" >&2
  exit 10
fi

set +e
timeout --signal=TERM --kill-after=20s 620s docker run --rm \
  --name "cgr-quantum-acceptance-$$" \
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
  --env "CGR_GIT_COMMIT=$git_commit" \
  --entrypoint python \
  "$image" -m cgr.quantum_preflight.acceptance \
  --manifest /input/manifest.json \
  --lock-file /input/quantum-preflight.lock \
  --result-root /output \
  --image-identifier "$image_id" \
  --max-seconds 180
container_status=$?
set -e
if [[ $container_status -ne 0 ]]; then
  exit "$container_status"
fi

report="$(find "$result_root/quantum-preflight-acceptance" -name acceptance-report.json -print | sort | tail -n 1)"
if [[ -z "$report" || ! -f "$report" ]]; then
  printf 'Acceptance execution produced no acceptance-report.json; skipped execution is failure.\n' >&2
  exit 10
fi
python3 - "$report" <<'PY'
import json
import sys
report = json.load(open(sys.argv[1], encoding="utf-8"))
if report.get("authorized") is not True or report.get("acceptance_passed") is not True:
    raise SystemExit("Acceptance report is not authorized and passed.")
PY
printf 'acceptance_report=%s\n' "$report"
