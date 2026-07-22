#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
base_image="${CGR_QUANTUM_IMAGE:-cgr-quantum-preflight:1.0.0}"
derived_image="${PULSATE_HTTP_IMAGE:-cgr-pulsate-http-integration:1.0.0}"
source_checkpoint="$(git -C "$repo_root" rev-parse HEAD)"

if ! git -C "$repo_root" diff --quiet; then
  printf 'Refusing to build: tracked source files differ from %s.\n' "$source_checkpoint" >&2
  exit 1
fi
if ! git -C "$repo_root" diff --cached --quiet; then
  printf 'Refusing to build: staged source files differ from %s.\n' "$source_checkpoint" >&2
  exit 1
fi

CGR_QUANTUM_IMAGE="$base_image" "$repo_root/scripts/build-quantum-preflight-image.sh"
base_image_id="$(docker image inspect --format '{{.Id}}' "$base_image")"

docker run --rm \
  --network none \
  --read-only \
  --entrypoint /bin/sh \
  "$base_image_id" \
  -c 'set -eu
unexpected_metadata="$(find /app/src \
  \( -type d \( -name "*.egg-info" -o -name "*.dist-info" \) \
  -o -type f -name "*.pth" \) -print)"
if [ -n "$unexpected_metadata" ]; then
  printf "Unexpected Python packaging metadata under /app/src:\n%s\n" \
    "$unexpected_metadata" >&2
  exit 1
fi
python -m pip check'

base_image_hex="${base_image_id#sha256:}"
pinned_base_image="cgr-quantum-preflight-pinned:${base_image_hex}"

cleanup() {
  docker image rm "$pinned_base_image" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker image tag "$base_image_id" "$pinned_base_image"
pinned_base_image_id="$(docker image inspect --format '{{.Id}}' "$pinned_base_image")"
if [[ "$pinned_base_image_id" != "$base_image_id" ]]; then
  echo "Pinned base-image reference mismatch" >&2
  exit 1
fi

docker build \
  --pull=false \
  --file "$repo_root/docker/pulsate-http-integration/Dockerfile" \
  --tag "$derived_image" \
  --build-arg "BASE_IMAGE=$pinned_base_image" \
  --build-arg "BASE_IMAGE_NAME=$base_image" \
  --build-arg "BASE_IMAGE_ID=$base_image_id" \
  --build-arg "SOURCE_CHECKPOINT=$source_checkpoint" \
  --label "org.opencontainers.image.base.name=$base_image" \
  --label "io.pulsate.base.image.id=$base_image_id" \
  --label "org.opencontainers.image.revision=$source_checkpoint" \
  "$repo_root"

post_build_pinned_base_image_id="$(docker image inspect --format '{{.Id}}' "$pinned_base_image")"
if [[ "$post_build_pinned_base_image_id" != "$base_image_id" ]]; then
  echo "Pinned base-image reference mismatch after derived build" >&2
  exit 1
fi

derived_image_id="$(docker image inspect --format '{{.Id}}' "$derived_image")"
recorded_base_image_id="$(docker image inspect --format '{{ index .Config.Labels "io.pulsate.base.image.id" }}' "$derived_image_id")"
recorded_source_checkpoint="$(docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$derived_image_id")"

if [[ "$recorded_base_image_id" != "$base_image_id" ]]; then
  printf 'Derived image base provenance mismatch: expected %s, recorded %s.\n' "$base_image_id" "$recorded_base_image_id" >&2
  exit 1
fi
if [[ "$recorded_source_checkpoint" != "$source_checkpoint" ]]; then
  printf 'Derived image revision mismatch: expected %s, recorded %s.\n' "$source_checkpoint" "$recorded_source_checkpoint" >&2
  exit 1
fi

printf 'Source checkpoint: %s\n' "$source_checkpoint"
printf 'Base image tag: %s\n' "$base_image"
printf 'Exact base image ID: %s\n' "$base_image_id"
printf 'Temporary pinned local base reference: %s\n' "$pinned_base_image"
printf 'Derived image tag: %s\n' "$derived_image"
printf 'Exact derived image ID: %s\n' "$derived_image_id"
