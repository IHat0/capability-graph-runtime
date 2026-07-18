#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
context="$repo_root/docker/quantum-sweagent-tool"
output_root="${1:-$HOME/cgr-evidence/quantum-model-repair/tool-image}"
base_image="${CGR_SWEAGENT_TOOL_BASE_IMAGE:?Set CGR_SWEAGENT_TOOL_BASE_IMAGE to an immutable repository@sha256 digest.}"
cgr_python="${CGR_PYTHON:?Set CGR_PYTHON to the project Python executable.}"
[[ "$base_image" == *@sha256:* ]] || { echo "Base image must use repository@sha256 identity." >&2; exit 2; }
PYTHONPATH="$repo_root/src" "$cgr_python" -c 'import pydantic, cgr' || {
  echo "CGR_PYTHON cannot import required project dependencies." >&2; exit 2;
}
mkdir -p "$output_root"

build_input_sha256="$("$cgr_python" - "$context" "$base_image" <<'PY'
import hashlib, json, pathlib, sys
root = pathlib.Path(sys.argv[1])
value = {
    "base_image": sys.argv[2],
    "dockerfile_sha256": hashlib.sha256((root / "Dockerfile").read_bytes()).hexdigest(),
    "requirements_sha256": hashlib.sha256((root / "requirements.lock").read_bytes()).hexdigest(),
    "contract_sha256": hashlib.sha256((root / "build-contract.json").read_bytes()).hexdigest(),
}
print(hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest())
PY
)"
tag="cgr-quantum-sweagent-tool:v1-${build_input_sha256:0:12}"
docker build --pull \
  --build-arg "BASE_IMAGE=$base_image" \
  --build-arg "BUILD_INPUT_SHA256=$build_input_sha256" \
  --build-arg "SWEAGENT_COMMIT=0f3acafacabc0def8cc76b4e48acb4b6cf302cb9" \
  --build-arg "SWEREX_VERSION=1.4.0" \
  --tag "$tag" "$context"
image_id="$(docker image inspect --format '{{.Id}}' "$tag")"
[[ "$image_id" == sha256:* ]] || { echo "Build produced no immutable image ID." >&2; exit 3; }

"$cgr_python" - \
  "$repo_root/benchmark-manifests/quantum-repair/sweagent-qwen-provider-v1.json" \
  "$output_root/provider-config.json" "$image_id" "$build_input_sha256" \
  "$base_image" "$tag" <<'PY'
import hashlib, json, pathlib, sys
template, target = map(pathlib.Path, sys.argv[1:3])
value = json.loads(template.read_text())
value["tool_container_image"] = sys.argv[3]
value["tool_image_build_input_sha256"] = sys.argv[4]
target.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
provenance = {
    "schema_version": "cgr.quantum-sweagent-tool-build-result/1.0.0",
    "base_image": sys.argv[5], "image_tag": sys.argv[6], "image_id": sys.argv[3],
    "build_input_sha256": sys.argv[4],
    "provider_config_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
    "build_result": "passed",
}
(target.parent / "build-provenance.json").write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n")
PY

printf 'image_tag=%s\nimage_id=%s\nbuild_input_sha256=%s\nbuild_result=passed\nprovider_config=%s\n' \
  "$tag" "$image_id" "$build_input_sha256" "$output_root/provider-config.json"
