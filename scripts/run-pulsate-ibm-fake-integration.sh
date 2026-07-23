#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
derived_image="${PULSATE_IBM_IMAGE:-cgr-pulsate-ibm-runtime:1.0.0}"
scientific_image="${PULSATE_HTTP_IMAGE:-cgr-pulsate-http-integration:1.0.0}"

# This path is deliberately non-paid: inherited credentials and live-run gates
# are removed before either phase starts.
unset PULSATE_RUN_IBM_INTEGRATION
unset PULSATE_IBM_ACKNOWLEDGE_COSTS
unset PULSATE_IBM_QUANTUM_TOKEN
unset PULSATE_IBM_QUANTUM_INSTANCE
unset PULSATE_IBM_QUANTUM_BACKEND

bash "$repo_root/scripts/build-pulsate-ibm-runtime-image.sh"
derived_image_id="$(docker image inspect --format '{{.Id}}' "$derived_image")"
scientific_image_id="$(docker image inspect --format '{{.Id}}' "$scientific_image")"
if [[ "$scientific_image_id" == "$derived_image_id" ]]; then
  echo "Scientific preflight and IBM Runtime image identities must be distinct." >&2
  exit 1
fi
suffix="$$"
run_volume="pulsate-ibm-fake-run-${suffix}"
network_name="pulsate-ibm-fake-network-${suffix}"
preflight_name="pulsate-ibm-preflight-${suffix}"

cleanup() {
  docker container rm --force "$preflight_name" >/dev/null 2>&1 || true
  docker network rm "$network_name" >/dev/null 2>&1 || true
  docker volume rm "$run_volume" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker volume create "$run_volume" >/dev/null
docker network create --internal "$network_name" >/dev/null
docker run --rm --user 0 --entrypoint /bin/sh \
  --volume "$run_volume:/pulsate-run" \
  "$derived_image_id" \
  -c 'mkdir -p /pulsate-run/exchange/requests /pulsate-run/exchange/handoffs /pulsate-run/runs /pulsate-run/experiments && chown -R 10001:10001 /pulsate-run'

docker run --detach --rm \
  --name "$preflight_name" \
  --network none \
  --read-only \
  --cpus 2 \
  --memory 4g \
  --pids-limit 256 \
  --security-opt no-new-privileges \
  --cap-drop ALL \
  --tmpfs /tmp:rw,nosuid,nodev,size=1g,mode=1777 \
  --volume "$run_volume:/pulsate-run" \
  --entrypoint python \
  "$scientific_image_id" \
  -m cgr.pulsate_api.ibm_preflight_coordinator \
  --exchange-root /pulsate-run/exchange \
  --run-root /pulsate-run/runs \
  --repository-root /app \
  --scientific-image-identifier "$scientific_image_id" \
  --ibm-runtime-image-identifier "$derived_image_id" >/dev/null

for _attempt in $(seq 1 100); do
  if docker exec "$preflight_name" test -f /pulsate-run/exchange/launcher-readiness.json; then
    break
  fi
  sleep 0.1
done
if ! docker exec "$preflight_name" test -f /pulsate-run/exchange/launcher-readiness.json; then
  echo "Run-bound no-network preflight coordinator did not become ready." >&2
  exit 1
fi

docker run --rm \
  --network "$network_name" \
  --read-only \
  --cpus 2 \
  --memory 2g \
  --pids-limit 256 \
  --security-opt no-new-privileges \
  --cap-drop ALL \
  --tmpfs /tmp:rw,nosuid,nodev,size=512m,mode=1777 \
  --volume "$run_volume:/pulsate-run" \
  --env PULSATE_IBM_FAKE_ACCEPTANCE=true \
  --env PULSATE_IBM_SHARED_ROOT=/pulsate-run \
  --env PULSATE_IBM_FAKE_ENDPOINT_URL=http://127.0.0.1:8765 \
  --env "PULSATE_IBM_SCIENTIFIC_IMAGE_IDENTIFIER=$scientific_image_id" \
  --env "PULSATE_IBM_IMAGE_IDENTIFIER=$derived_image_id" \
  "$derived_image_id" \
  pytest -q -s -rs -p no:cacheprovider \
  tests/test_pulsate_ibm_fake_integration.py
