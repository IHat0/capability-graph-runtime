#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
base_image="${PULSATE_HTTP_IMAGE:-cgr-pulsate-http-integration:1.0.0}"
derived_image="${PULSATE_IBM_IMAGE:-cgr-pulsate-ibm-runtime:1.0.0}"
source_checkpoint="$(git -C "$repo_root" rev-parse HEAD)"

if ! git -C "$repo_root" diff --quiet || ! git -C "$repo_root" diff --cached --quiet; then
  printf 'Refusing to build: tracked source files differ from %s.\n' "$source_checkpoint" >&2
  exit 1
fi

PULSATE_HTTP_IMAGE="$base_image" \
  bash "$repo_root/scripts/build-pulsate-http-integration-image.sh"
base_image_id="$(docker image inspect --format '{{.Id}}' "$base_image")"
base_image_hex="${base_image_id#sha256:}"
pinned_base_image="cgr-pulsate-http-pinned:${base_image_hex}"

cleanup() {
  docker image rm "$pinned_base_image" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker image tag "$base_image_id" "$pinned_base_image"
pinned_base_image_id="$(docker image inspect --format '{{.Id}}' "$pinned_base_image")"
if [[ "$pinned_base_image_id" != "$base_image_id" ]]; then
  echo "Pinned Pulsate HTTP base-image reference mismatch" >&2
  exit 1
fi

docker build \
  --pull=false \
  --file "$repo_root/docker/pulsate-ibm-runtime/Dockerfile" \
  --tag "$derived_image" \
  --build-arg "BASE_IMAGE=$pinned_base_image" \
  --build-arg "BASE_IMAGE_NAME=$base_image" \
  --build-arg "BASE_IMAGE_ID=$base_image_id" \
  --build-arg "SOURCE_CHECKPOINT=$source_checkpoint" \
  --label "org.opencontainers.image.base.name=$base_image" \
  --label "io.pulsate.base.image.id=$base_image_id" \
  --label "org.opencontainers.image.revision=$source_checkpoint" \
  "$repo_root"

post_build_base_id="$(docker image inspect --format '{{.Id}}' "$pinned_base_image")"
if [[ "$post_build_base_id" != "$base_image_id" ]]; then
  echo "Pinned Pulsate HTTP base-image reference changed during build" >&2
  exit 1
fi

derived_image_id="$(docker image inspect --format '{{.Id}}' "$derived_image")"
recorded_base_id="$(docker image inspect --format '{{ index .Config.Labels "io.pulsate.base.image.id" }}' "$derived_image_id")"
recorded_revision="$(docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$derived_image_id")"
if [[ "$recorded_base_id" != "$base_image_id" || "$recorded_revision" != "$source_checkpoint" ]]; then
  echo "Pulsate IBM Runtime image provenance mismatch" >&2
  exit 1
fi

printf 'Source checkpoint: %s\n' "$source_checkpoint"
printf 'Pulsate HTTP base image tag: %s\n' "$base_image"
printf 'Exact base image ID: %s\n' "$base_image_id"
printf 'Temporary pinned local base reference: %s\n' "$pinned_base_image"
printf 'IBM Runtime image tag: %s\n' "$derived_image"
printf 'Exact IBM Runtime image ID: %s\n' "$derived_image_id"
