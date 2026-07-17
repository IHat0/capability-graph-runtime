#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
image="${CGR_QUANTUM_CANDIDATE_IMAGE:-cgr-quantum-candidate:1.0.0}"
lock="$repo_root/requirements/quantum-preflight.lock"

docker build --pull \
  --file "$repo_root/docker/quantum-candidate/Dockerfile" \
  --tag "$image" \
  "$repo_root"

image_id="$(docker image inspect --format '{{.Id}}' "$image")"
lock_sha256="$(sha256sum "$lock" | awk '{print $1}')"
docker run --rm --network none --read-only --entrypoint python "$image_id" \
  -c 'import importlib.util, os; assert os.getuid() == 10002; assert importlib.util.find_spec("cgr") is None'

printf 'candidate_image_id=%s\n' "$image_id"
printf 'candidate_dependency_lock_sha256=%s\n' "$lock_sha256"
printf 'runtime_network_policy=explicit_--network_none_required\n'
