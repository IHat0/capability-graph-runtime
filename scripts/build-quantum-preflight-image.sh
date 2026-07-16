#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
image="${CGR_QUANTUM_IMAGE:-cgr-quantum-preflight:1.0.0}"
docker build --pull \
  --file "$repo_root/docker/quantum-preflight/Dockerfile" \
  --tag "$image" \
  "$repo_root"
docker image inspect --format '{{.Id}}' "$image"
