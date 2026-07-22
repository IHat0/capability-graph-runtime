#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
derived_image="${PULSATE_HTTP_IMAGE:-cgr-pulsate-http-integration:1.0.0}"

bash "$repo_root/scripts/build-pulsate-http-integration-image.sh"
derived_image_id="$(docker image inspect --format '{{.Id}}' "$derived_image")"

docker run --rm \
  --network none \
  --read-only \
  --cpus 2 \
  --memory 4g \
  --pids-limit 256 \
  --security-opt no-new-privileges \
  --cap-drop ALL \
  --tmpfs /tmp:rw,nosuid,nodev,size=1g,mode=1777 \
  --entrypoint python \
  "$derived_image_id" \
  -c 'from importlib.metadata import version; import fastapi, starlette, httpx, uvicorn, pytest, pydantic, qiskit, qiskit_nature, pyscf; names = ("fastapi", "starlette", "httpx", "uvicorn", "pytest", "pydantic", "qiskit", "qiskit-nature", "pyscf"); [print(f"{name}=={version(name)}") for name in names]'

docker run --rm \
  --network none \
  --read-only \
  --cpus 2 \
  --memory 4g \
  --pids-limit 256 \
  --security-opt no-new-privileges \
  --cap-drop ALL \
  --tmpfs /tmp:rw,nosuid,nodev,size=1g,mode=1777 \
  "$derived_image_id" \
  pip check

docker run --rm \
  --network none \
  --read-only \
  --cpus 2 \
  --memory 4g \
  --pids-limit 256 \
  --security-opt no-new-privileges \
  --cap-drop ALL \
  --tmpfs /tmp:rw,nosuid,nodev,size=1g,mode=1777 \
  --env PULSATE_RUN_HTTP_INTEGRATION=true \
  --env PULSATE_EXECUTION_ENABLED=true \
  --env PULSATE_RUN_ROOT=/tmp/pulsate-runs \
  --env "PULSATE_QUANTUM_IMAGE_IDENTIFIER=$derived_image_id" \
  "$derived_image_id" \
  pytest -q -s -rs -p no:cacheprovider tests/test_pulsate_runs_integration.py
