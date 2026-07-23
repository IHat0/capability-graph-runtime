#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
derived_image="${PULSATE_IBM_IMAGE:-cgr-pulsate-ibm-runtime:1.0.0}"
scientific_image="${PULSATE_HTTP_IMAGE:-cgr-pulsate-http-integration:1.0.0}"

for variable in \
  PULSATE_RUN_IBM_INTEGRATION \
  PULSATE_IBM_ACKNOWLEDGE_COSTS \
  PULSATE_IBM_QUANTUM_TOKEN \
  PULSATE_IBM_QUANTUM_INSTANCE \
  PULSATE_IBM_QUANTUM_BACKEND
do
  if [[ -z "${!variable:-}" ]]; then
    printf 'Live IBM integration requires %s.\n' "$variable" >&2
    exit 1
  fi
done
if [[ "${PULSATE_RUN_IBM_INTEGRATION,,}" != "true" || "${PULSATE_IBM_ACKNOWLEDGE_COSTS,,}" != "true" ]]; then
  echo "Live IBM integration requires both explicit boolean cost gates." >&2
  exit 1
fi

"$repo_root/scripts/build-pulsate-ibm-runtime-image.sh"
derived_image_id="$(docker image inspect --format '{{.Id}}' "$derived_image")"
scientific_image_id="$(docker image inspect --format '{{.Id}}' "$scientific_image")"
if [[ "$scientific_image_id" == "$derived_image_id" ]]; then
  echo "Scientific preflight and IBM Runtime image identities must be distinct." >&2
  exit 1
fi
suffix="$$"
run_volume="pulsate-ibm-controlled-run-${suffix}"
network_name="pulsate-ibm-network-${suffix}"
endpoint_name="pulsate-fake-endpoint-${suffix}"

cleanup() {
  docker container rm --force "$endpoint_name" >/dev/null 2>&1 || true
  docker network rm "$network_name" >/dev/null 2>&1 || true
  docker volume rm "$run_volume" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker volume create "$run_volume" >/dev/null
docker network create "$network_name" >/dev/null
docker run --rm --user 0 --entrypoint /bin/sh \
  --volume "$run_volume:/pulsate-run" \
  "$derived_image_id" \
  -c 'chown 10001:10001 /pulsate-run'
docker run --detach --rm \
  --name "$endpoint_name" \
  --network "$network_name" \
  --network-alias pulsate-fake-endpoint \
  --read-only \
  --security-opt no-new-privileges \
  --cap-drop ALL \
  "$derived_image_id" \
  http.server 8765 >/dev/null

# Phase 1: the complete trusted PySCF/Qiskit preflight runs with an
# OS-enforced Docker --network none boundary and receives no IBM credentials.
docker run --rm \
  --network none \
  --read-only \
  --cpus 2 \
  --memory 4g \
  --pids-limit 256 \
  --security-opt no-new-privileges \
  --cap-drop ALL \
  --tmpfs /tmp:rw,nosuid,nodev,size=1g,mode=1777 \
  --volume "$run_volume:/pulsate-run" \
  --env PULSATE_IBM_INTEGRATION_PHASE=local_preflight \
  --env PULSATE_IBM_SHARED_ROOT=/pulsate-run \
  --env PULSATE_FAKE_ENDPOINT_HOST=pulsate-fake-endpoint \
  --env PULSATE_FAKE_ENDPOINT_PORT=8765 \
  --env "PULSATE_QUANTUM_IMAGE_IDENTIFIER=$scientific_image_id" \
  --env "PULSATE_IBM_IMAGE_IDENTIFIER=$derived_image_id" \
  "$scientific_image_id" \
  pytest -q -s -rs -p no:cacheprovider \
  tests/test_pulsate_ibm_integration.py::test_no_network_local_preflight_phase

# Phase 2: only validated persisted preflight is reused. This container has
# network access and injects credentials solely into its IBM worker process.
docker run --rm \
  --network "$network_name" \
  --read-only \
  --cpus 2 \
  --memory 4g \
  --pids-limit 256 \
  --security-opt no-new-privileges \
  --cap-drop ALL \
  --tmpfs /tmp:rw,nosuid,nodev,size=1g,mode=1777 \
  --volume "$run_volume:/pulsate-run" \
  --env PULSATE_IBM_INTEGRATION_PHASE=ibm_runtime \
  --env PULSATE_IBM_SHARED_ROOT=/pulsate-run \
  --env PULSATE_FAKE_ENDPOINT_URL=http://pulsate-fake-endpoint:8765 \
  --env PULSATE_RUN_IBM_INTEGRATION \
  --env PULSATE_IBM_ACKNOWLEDGE_COSTS \
  --env PULSATE_IBM_QUANTUM_TOKEN \
  --env PULSATE_IBM_QUANTUM_INSTANCE \
  --env PULSATE_IBM_QUANTUM_BACKEND \
  --env "PULSATE_IBM_IMAGE_IDENTIFIER=$derived_image_id" \
  --env "PULSATE_IBM_SCIENTIFIC_IMAGE_IDENTIFIER=$scientific_image_id" \
  "$derived_image_id" \
  pytest -q -s -rs -p no:cacheprovider \
  tests/test_pulsate_ibm_integration.py::test_network_enabled_ibm_runtime_phase
