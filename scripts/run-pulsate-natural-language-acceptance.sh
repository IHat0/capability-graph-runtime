#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="${PYTHON:-python}"
evidence_log="${PULSATE_NL_ACCEPTANCE_EVIDENCE_LOG:-$repo_root/pulsate-natural-language-evidence.log}"
acceptance_root="$(mktemp -d "${TMPDIR:-/tmp}/pulsate-nl-acceptance.XXXXXX")"
interpretation_root="$acceptance_root/interpretations"
approved_root="$interpretation_root/approved"
experiment_root="$acceptance_root/experiments"
run_root="$acceptance_root/runs"
api_log="$acceptance_root/api.log"
default_state_before_path="$acceptance_root/default-state-before.json"
default_interpretation_root="$repo_root/.pulsate-interpretations"
default_experiment_root="$repo_root/.pulsate-experiments"
default_run_root="$repo_root/.pulsate-runs"
api_pid=""

: "${PULSATE_NL_MODEL_BASE_URL:?Configure the real OpenAI-compatible model endpoint.}"
: "${PULSATE_NL_MODEL_API_KEY:?Configure the model API key.}"
: "${PULSATE_NL_MODEL_NAME:?Configure the model name.}"

mkdir -p "$approved_root" "$experiment_root" "$run_root"

cleanup() {
  local status="$1"
  if [[ "$status" -ne 0 && -f "$api_log" ]]; then
    echo "Fresh Pulsate API log tail (bounded and redacted):" >&2
    tail -n 80 "$api_log" \
      | sed -E 's/(authorization|api[_-]?key|token|bearer)[^[:space:]]*/[redacted]/Ig' \
      | tail -c 16384 >&2
    echo >&2
  fi
  if [[ -n "$api_pid" ]] && kill -0 "$api_pid" >/dev/null 2>&1; then
    kill "$api_pid" >/dev/null 2>&1 || true
    for _cleanup_attempt in {1..50}; do
      if ! kill -0 "$api_pid" >/dev/null 2>&1; then
        break
      fi
      sleep 0.1
    done
    if kill -0 "$api_pid" >/dev/null 2>&1; then
      kill -KILL "$api_pid" >/dev/null 2>&1 || true
    fi
    wait "$api_pid" >/dev/null 2>&1 || true
  fi
  rm -rf -- "$acceptance_root"
}
trap 'status=$?; trap - EXIT; cleanup "$status"; exit "$status"' EXIT

if [[ -n "${PULSATE_NL_ACCEPTANCE_PORT:-}" ]]; then
  api_port="$PULSATE_NL_ACCEPTANCE_PORT"
else
  api_port="$(
    "$python_bin" - <<'PY'
import socket

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
  )"
fi

"$python_bin" - "$api_port" <<'PY'
import socket
import sys

port = int(sys.argv[1])
if not 1 <= port <= 65535:
    raise SystemExit("The acceptance API port is outside the valid range.")
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    try:
        sock.bind(("127.0.0.1", port))
    except OSError as exc:
        raise SystemExit(f"The dedicated acceptance API port is already in use: {port}") from exc
PY

PULSATE_NL_DEFAULT_STATE_SNAPSHOT="$default_state_before_path" \
PULSATE_NL_DEFAULT_INTERPRETATION_ROOT="$default_interpretation_root" \
PULSATE_NL_DEFAULT_EXPERIMENT_ROOT="$default_experiment_root" \
PULSATE_NL_DEFAULT_RUN_ROOT="$default_run_root" \
"$python_bin" - <<'PY'
import hashlib
import json
import os
from pathlib import Path


def snapshot_optional_tree(root):
    if root.is_symlink():
        return {"root_kind": "symlink", "target": os.readlink(root)}
    if not root.exists():
        return {"root_kind": "missing"}
    if not root.is_dir():
        return {"root_kind": "other"}
    snapshot = {"root_kind": "directory", "entries": {}}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            snapshot["entries"][relative] = {
                "kind": "symlink",
                "target": os.readlink(path),
            }
        elif path.is_dir():
            snapshot["entries"][relative] = {"kind": "directory"}
        elif path.is_file():
            content = path.read_bytes()
            snapshot["entries"][relative] = {
                "kind": "file",
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
    return snapshot


roots = {
    "interpretations": Path(os.environ["PULSATE_NL_DEFAULT_INTERPRETATION_ROOT"]),
    "experiments": Path(os.environ["PULSATE_NL_DEFAULT_EXPERIMENT_ROOT"]),
    "runs": Path(os.environ["PULSATE_NL_DEFAULT_RUN_ROOT"]),
}
snapshot = {name: snapshot_optional_tree(root) for name, root in roots.items()}
Path(os.environ["PULSATE_NL_DEFAULT_STATE_SNAPSHOT"]).write_text(
    json.dumps(snapshot, sort_keys=True),
    encoding="utf-8",
)
PY

# The fresh API process retains only the language-model configuration. It
# receives no local-execution enablement, IBM credentials, cost acknowledgement,
# preflight handoff, or runtime-image configuration.
unset PULSATE_EXECUTION_ENABLED
unset PULSATE_IBM_ACKNOWLEDGE_COSTS
unset PULSATE_IBM_QUANTUM_TOKEN
unset PULSATE_IBM_QUANTUM_INSTANCE
unset PULSATE_IBM_QUANTUM_BACKEND
unset PULSATE_IBM_IMAGE_IDENTIFIER
unset PULSATE_IBM_PREFLIGHT_HANDOFF_ROOT
unset PULSATE_IBM_SCIENTIFIC_IMAGE_IDENTIFIER
unset PULSATE_RUN_IBM_INTEGRATION

PYTHONPATH="$repo_root/src" \
PULSATE_INTERPRETATION_ROOT="$interpretation_root" \
PULSATE_EXPERIMENT_ROOT="$experiment_root" \
PULSATE_RUN_ROOT="$run_root" \
"$python_bin" -m uvicorn cgr.pulsate_api.app:app \
  --host 127.0.0.1 \
  --port "$api_port" \
  >"$api_log" 2>&1 &
api_pid="$!"
api_base_url="http://127.0.0.1:$api_port"

ready="false"
for _attempt in {1..100}; do
  if ! kill -0 "$api_pid" >/dev/null 2>&1; then
    echo "The fresh Pulsate API exited before readiness." >&2
    exit 1
  fi
  if PULSATE_NL_ACCEPTANCE_API_BASE_URL="$api_base_url" "$python_bin" - <<'PY'
import os
import urllib.error
import urllib.request

try:
    with urllib.request.urlopen(
        os.environ["PULSATE_NL_ACCEPTANCE_API_BASE_URL"] + "/api/v1/health",
        timeout=1,
    ) as response:
        if response.status != 200:
            raise SystemExit(1)
except (urllib.error.URLError, TimeoutError, OSError):
    raise SystemExit(1) from None
PY
  then
    ready="true"
    break
  fi
  sleep 0.1
done
if [[ "$ready" != "true" ]]; then
  echo "The fresh Pulsate API did not become ready." >&2
  exit 1
fi

PULSATE_NL_ACCEPTANCE_API_BASE_URL="$api_base_url" \
PULSATE_NL_ACCEPTANCE_EVIDENCE_LOG="$evidence_log" \
PULSATE_NL_ACCEPTANCE_RUN_ROOT="$run_root" \
PULSATE_NL_ACCEPTANCE_ISOLATED_ROOT="$acceptance_root" \
PULSATE_NL_DEFAULT_STATE_SNAPSHOT="$default_state_before_path" \
PULSATE_NL_DEFAULT_INTERPRETATION_ROOT="$default_interpretation_root" \
PULSATE_NL_DEFAULT_EXPERIMENT_ROOT="$default_experiment_root" \
PULSATE_NL_DEFAULT_RUN_ROOT="$default_run_root" \
"$python_bin" - <<'PY'
import hashlib
import json
import math
import os
from pathlib import Path
import urllib.request

api = os.environ["PULSATE_NL_ACCEPTANCE_API_BASE_URL"].rstrip("/")
log_path = Path(os.environ["PULSATE_NL_ACCEPTANCE_EVIDENCE_LOG"])
run_root = Path(os.environ["PULSATE_NL_ACCEPTANCE_RUN_ROOT"]).resolve(strict=True)
isolated_root = Path(os.environ["PULSATE_NL_ACCEPTANCE_ISOLATED_ROOT"]).resolve(strict=True)
default_state_snapshot_path = Path(os.environ["PULSATE_NL_DEFAULT_STATE_SNAPSHOT"])
default_state_roots = {
    "interpretations": Path(os.environ["PULSATE_NL_DEFAULT_INTERPRETATION_ROOT"]),
    "experiments": Path(os.environ["PULSATE_NL_DEFAULT_EXPERIMENT_ROOT"]),
    "runs": Path(os.environ["PULSATE_NL_DEFAULT_RUN_ROOT"]),
}
questions = [
    "Calculate the ground-state energy of lithium hydride at a bond length of 1.6 angstrom using STO-3G on IBM Quantum.",
    "What is the electronic ground-state energy of H2 with the nuclei 0.735 angstrom apart? Use the Jordan-Wigner mapper and IBM Quantum.",
    "Prepare a ground-state experiment for linear beryllium hydride with 1.33 angstrom Be-H bonds.",
    "Study caffeine on IBM Quantum.",
]


def request(path, payload=None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        api + path,
        data=data,
        headers=headers,
        method="POST" if data else "GET",
    )
    with urllib.request.urlopen(req, timeout=180) as response:
        body = response.read(2 * 1024 * 1024 + 1)
    if len(body) > 2 * 1024 * 1024:
        raise RuntimeError("Pulsate acceptance response exceeded the evidence bound.")
    return json.loads(body)


def field(specification, path):
    value = specification
    for segment in path.split("."):
        value = value[segment]
    return value.get("value") if isinstance(value, dict) and "provenance" in value else value


def close(actual, expected):
    return isinstance(actual, (int, float)) and math.isclose(actual, expected, abs_tol=1e-9)


def snapshot_tree(root):
    snapshot = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            snapshot[relative] = {"kind": "symlink", "target": os.readlink(path)}
        elif path.is_dir():
            snapshot[relative] = {"kind": "directory"}
        elif path.is_file():
            content = path.read_bytes()
            snapshot[relative] = {
                "kind": "file",
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
    return snapshot


def snapshot_optional_tree(root):
    if root.is_symlink():
        return {"root_kind": "symlink", "target": os.readlink(root)}
    if not root.exists():
        return {"root_kind": "missing"}
    if not root.is_dir():
        return {"root_kind": "other"}
    return {"root_kind": "directory", "entries": snapshot_tree(root)}


def execution_artifacts(root):
    findings = []
    execution_keys = {
        "run_identifier",
        "job_identifier",
        "ibm_job_identifier",
        "execution_identifier",
        "ibm_execution",
        "submission_attempt_identifier",
    }
    execution_names = {
        "ibm-worker",
        "quantum-worker",
        "worker-result.json",
        "receipt.json",
        "prepared-submission.json",
        "submitted-job.json",
        "local-preflight.json",
        "submission.json",
        "job.json",
        "submission-attempt.json",
        "status.json",
        "result.json",
        "failure.json",
        "prepared-isa-circuit.qpy",
        "prepared-isa-observable.json",
        "evidence.qpy",
        "qubit-hamiltonian.json",
        "ansatz-manifest.json",
        "launcher-readiness.json",
        "handoffs",
    }
    for path in sorted(root.rglob("*")):
        if path.name in execution_names:
            findings.append(path.relative_to(root).as_posix())
        if path.is_file() and path.suffix == ".json":
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, UnicodeError):
                continue
            stack = [payload]
            while stack:
                item = stack.pop()
                if isinstance(item, dict):
                    if execution_keys.intersection(item):
                        findings.append(path.relative_to(root).as_posix())
                        break
                    stack.extend(item.values())
                elif isinstance(item, list):
                    stack.extend(item)
    return sorted(set(findings))


before = request("/api/v1/experiments/interpreter/capability")
if not before.get("available"):
    raise RuntimeError("The production natural-language provider is unavailable.")
if before.get("provider_kind") != "openai_compatible_http":
    raise RuntimeError("A fake or non-production interpreter was selected.")
if before.get("model_name") != os.environ["PULSATE_NL_MODEL_NAME"]:
    raise RuntimeError("The fresh API reported an unrelated model identity.")
if before.get("model_request_count") != 0:
    raise RuntimeError("The fresh API did not begin with a zero model-request count.")

run_root_before = snapshot_tree(run_root)
responses = [
    request("/api/v1/experiments/interpret", {"question": question})
    for question in questions
]
after = request("/api/v1/experiments/interpreter/capability")
request_delta = after.get("model_request_count", 0) - before.get("model_request_count", 0)
if request_delta < len(questions):
    raise RuntimeError("The model request counter did not prove real requests occurred.")
if after.get("model_name") != os.environ["PULSATE_NL_MODEL_NAME"]:
    raise RuntimeError("The reported model identity does not match configuration.")

lih, h2, beh2, caffeine = responses
lih_spec = lih["specification"]
lih_identity = {
    str(field(lih_spec, "molecule.name") or "").casefold(),
    str(field(lih_spec, "molecule.formula") or "").casefold(),
}
if not ({"lithium hydride", "lih"} & lih_identity):
    raise RuntimeError("LiH molecular identity was not preserved.")
lih_bonds = field(lih_spec, "molecule.bond_lengths") or []
if not any(close(bond.get("value"), 1.6) and bond.get("unit") == "angstrom" for bond in lih_bonds):
    raise RuntimeError("LiH 1.6 angstrom bond evidence was not preserved.")
if str(field(lih_spec, "basis") or "").casefold() != "sto-3g":
    raise RuntimeError("LiH STO-3G basis was not preserved.")
if field(lih_spec, "requested_execution_target") != "ibm_quantum":
    raise RuntimeError("LiH IBM target was not preserved.")
if lih["interpretation_status"] != "ready_for_review":
    raise RuntimeError("The LiH draft is not complete enough for scientist review.")

h2_spec = h2["specification"]
h2_identity = {
    str(field(h2_spec, "molecule.name") or "").casefold(),
    str(field(h2_spec, "molecule.formula") or "").casefold(),
}
if "h2" not in h2_identity:
    raise RuntimeError("H2 molecular identity was not preserved.")
h2_bonds = field(h2_spec, "molecule.bond_lengths") or []
h2_atoms = field(h2_spec, "molecule.atoms") or []
h2_bond_preserved = any(
    close(bond.get("value"), 0.735) and bond.get("unit") == "angstrom"
    for bond in h2_bonds
)
if not h2_bond_preserved and len(h2_atoms) == 2:
    left = h2_atoms[0].get("coordinates")
    right = h2_atoms[1].get("coordinates")
    if left and right:
        h2_bond_preserved = close(
            math.sqrt(sum((left[index] - right[index]) ** 2 for index in range(3))),
            0.735,
        )
if not h2_bond_preserved:
    raise RuntimeError("H2 0.735 angstrom separation was not preserved.")
if str(field(h2_spec, "mapper") or "").casefold().replace("-", "_") != "jordan_wigner":
    raise RuntimeError("H2 Jordan-Wigner mapper was not preserved.")
if field(h2_spec, "requested_execution_target") != "ibm_quantum":
    raise RuntimeError("H2 IBM target was not preserved.")
if h2["interpretation_status"] != "ready_for_review":
    raise RuntimeError("The H2 draft is not ready for scientist review.")

beh2_spec = beh2["specification"]
beh2_identity = {
    str(field(beh2_spec, "molecule.name") or "").casefold(),
    str(field(beh2_spec, "molecule.formula") or "").casefold(),
}
if not ({"beryllium hydride", "beh2"} & beh2_identity):
    raise RuntimeError("BeH2 molecular identity was not preserved.")
beh2_atoms = field(beh2_spec, "molecule.atoms")
beh2_geometry_missing = "geometry" in beh2.get("missing_required_information", [])
if not ((isinstance(beh2_atoms, list) and len(beh2_atoms) == 3) or beh2_geometry_missing):
    raise RuntimeError("BeH2 neither preserved three atoms nor reported unresolved geometry.")
if beh2_geometry_missing:
    if beh2["execution_support_status"] != "needs_clarification":
        raise RuntimeError("Unresolved BeH2 geometry was not reported as needing clarification.")
elif beh2["execution_support_status"] != "requires_compiler_capability":
    raise RuntimeError("Complete BeH2 was not reported as requiring compiler capability.")

caffeine_spec = caffeine["specification"]
caffeine_identity = {
    str(field(caffeine_spec, "molecule.name") or "").casefold(),
    str(field(caffeine_spec, "molecule.formula") or "").casefold(),
}
if "caffeine" not in caffeine_identity:
    raise RuntimeError("Caffeine molecular identity was not preserved.")
if field(caffeine_spec, "molecule.atoms"):
    raise RuntimeError("The model fabricated caffeine Cartesian geometry.")
if caffeine["interpretation_status"] != "needs_clarification":
    raise RuntimeError("Incomplete caffeine input did not request clarification.")

approved = request(
    f"/api/v1/experiments/{lih['interpretation_identifier']}/approve",
    {"specification": lih_spec, "accepted_assumptions": True},
)
if approved.get("requested_execution_target") != "ibm_quantum":
    raise RuntimeError("Approval did not preserve the intended IBM Quantum target.")
if approved.get("status") != "ready_for_ibm_submission":
    raise RuntimeError("Approval did not remain in the pre-submission state.")
if not str(approved.get("experiment_identifier", "")).startswith("experiment-"):
    raise RuntimeError("Approval did not create an immutable experiment identifier.")
if len(str(approved.get("specification_sha256", ""))) != 64:
    raise RuntimeError("Approval did not create a canonical specification SHA-256.")

run_root_after = snapshot_tree(run_root)
if run_root_after != run_root_before:
    raise RuntimeError("The isolated run root changed during interpretation or approval.")
default_state_before = json.loads(
    default_state_snapshot_path.read_text(encoding="utf-8")
)
default_state_after = {
    name: snapshot_optional_tree(root)
    for name, root in default_state_roots.items()
}
if default_state_after != default_state_before:
    raise RuntimeError(
        "A default repository state root changed during isolated acceptance."
    )
detected_execution_artifacts = execution_artifacts(isolated_root)
if detected_execution_artifacts:
    raise RuntimeError(
        "Execution artifacts were created: " + ", ".join(detected_execution_artifacts)
    )


def bounded_summary(response):
    specification = response["specification"]
    return {
        "interpretation_identifier": response["interpretation_identifier"],
        "interpretation_status": response["interpretation_status"],
        "execution_support_status": response["execution_support_status"],
        "molecule": {
            "name": specification["molecule"]["name"],
            "formula": specification["molecule"]["formula"],
            "atoms": specification["molecule"]["atoms"],
            "bond_lengths": specification["molecule"]["bond_lengths"],
        },
        "basis": specification["basis"],
        "mapper": specification["mapper"],
        "requested_execution_target": specification["requested_execution_target"],
        "missing_required_information": response["missing_required_information"][:32],
        "warnings": response["warnings"][:32],
    }


evidence = {
    "provider_kind": after["provider_kind"],
    "model_name": after["model_name"],
    "model_request_count_before": before["model_request_count"],
    "model_request_count_after": after["model_request_count"],
    "model_request_count_delta": request_delta,
    "interpretation_summaries": [bounded_summary(item) for item in responses],
    "approved_experiment_identifier": approved["experiment_identifier"],
    "specification_sha256": approved["specification_sha256"],
    "approval_status": approved["status"],
    "run_root_before": run_root_before,
    "run_root_after": run_root_after,
    "default_state_before_sha256": hashlib.sha256(
        json.dumps(default_state_before, sort_keys=True).encode("utf-8")
    ).hexdigest(),
    "default_state_after_sha256": hashlib.sha256(
        json.dumps(default_state_after, sort_keys=True).encode("utf-8")
    ).hexdigest(),
    "detected_execution_artifacts": detected_execution_artifacts,
}
encoded = (json.dumps(evidence, indent=2, sort_keys=True) + "\n").encode("utf-8")
if len(encoded) > 64 * 1024:
    raise RuntimeError("Acceptance evidence exceeded its 64 KiB ceiling.")
log_path.parent.mkdir(parents=True, exist_ok=True)
with log_path.open("wb") as stream:
    stream.write(encoded)
print(encoded.decode("utf-8"), end="")
PY
