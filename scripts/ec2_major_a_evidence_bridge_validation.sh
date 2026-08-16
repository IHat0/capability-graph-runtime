#!/usr/bin/env bash
set -euo pipefail

: "${CGR_VALIDATION_ROOT:?Set CGR_VALIDATION_ROOT to the isolated validation root.}"
: "${PULSATE_API_URL:?Load the validation client environment.}"
: "${PULSATE_ACCESS_TOKEN:?Load the validation bearer token.}"

code_root="${CGR_VALIDATION_ROOT}/code"
cd "${code_root}"
source "${HOME}/.venvs/cgr-phase5a-qiskit/bin/activate"
export PYTHONPATH="${code_root}/src"

python -m pytest \
  tests/test_research_sessions.py \
  tests/test_scientific_acquisition.py \
  tests/test_scientific_resolution.py \
  -q \
  --basetemp "${CGR_VALIDATION_ROOT}/pytest-evidence-bridge"

capability_file="${CGR_VALIDATION_ROOT}/evidence-bridge-capability.json"
curl --silent --show-error --fail-with-body \
  -H "Authorization: Bearer ${PULSATE_ACCESS_TOKEN}" \
  "${PULSATE_API_URL}/api/v1/research/capability" \
  > "${capability_file}"

python - "${capability_file}" <<'PY'
import json
import sys
from pathlib import Path

document = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert document["research_session"] == "unified"
assert document["conversation"] == "resumable"
assert document["evidence_interpreter"]["available"] is True
assert document["evidence_interpreter"]["requires_exact_quotes"] is True
assert document["evidence_interpreter"]["requires_scientist_confirmation"] is True
assert document["structure_evidence_resolver"]["available"] is True
print("Major A evidence-bridge capability is ready.")
PY

cat <<'TEXT'

Automated validation passed.

For the private end-to-end acceptance, create a new session with the existing
silent-read CLI sequence. Do not reuse a session created by the previous build.
If Pulsate returns an evidence_proposal, inspect only its safe field names and
then confirm it with:

  python -m cgr.pulsate_api.scientific_cli \
    --session-id "$CGR_SESSION_ID" \
    --accept-evidence-proposal \
    --approve-acquired-inputs \
    "I confirm the reviewed evidence proposal." \
    --plan-only

Expected terminal states are planned, or awaiting_clarification with a new
diagnostic question that explains exactly what evidence could not be validated.
The same unchanged checklist must not be repeated.
TEXT
