#!/usr/bin/env bash
set -euo pipefail

cd "$HOME/CGR-Ticket-1.1"

# Keep SWE-agent separate from CGR's existing virtual environment.
python3.12 -m venv .venv-sweagent
source .venv-sweagent/bin/activate
python -m pip install --upgrade pip
python -m pip install 'sweagent==1.1.0'
python -m pip install -e .

export CGR_DRAFT_BASE_URL='http://127.0.0.1:8000/v1'
export CGR_DRAFT_API_KEY='cgr-aws-key'
export CGR_DRAFT_MODEL='Qwen/Qwen2.5-Coder-7B-Instruct'
export CGR_DRAFT_MAX_MODEL_LEN=16384
export CGR_SWEBENCH_SCAFFOLD_ID='swe-agent-v1.1.0-0f3acaf'
export CGR_SWE_AGENT_EXECUTABLE="$PWD/.venv-sweagent/bin/sweagent"
export CGR_SWEBENCH_AGENT_COMMAND='[
  "cgr-swebench-swe-agent-adapter",
  "--workspace", "{workspace}",
  "--problem-file", "{problem_file}",
  "--mode", "{mode}",
  "--max-steps", "{max_steps}",
  "--max-calls", "{max_calls}"
]'

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT
workspace="$tmpdir/workspace"
mkdir -p "$workspace"
git -C "$workspace" init -q
git -C "$workspace" config user.email 'cgr-smoke@example.invalid'
git -C "$workspace" config user.name 'CGR smoke'
printf 'def add(a, b):\n    return a - b\n' > "$workspace/math_utils.py"
git -C "$workspace" add math_utils.py
git -C "$workspace" commit -qm initial
printf 'Fix math_utils.py so add(2, 3) returns 5. Preserve the existing function.\n' > "$tmpdir/problem.txt"

set +e
CGR_SWEBENCH_DEBUG_TRACE=1 cgr-swebench-swe-agent-adapter \
  --workspace "$workspace" \
  --problem-file "$tmpdir/problem.txt" \
  --mode baseline \
  --max-steps 8 \
  --max-calls 5 | tee "$tmpdir/adapter-result.json"
adapter_status=${PIPESTATUS[0]}
set -e

test "$adapter_status" -eq 0
git -C "$workspace" diff --exit-code -- math_utils.py && exit 1
grep -q 'return a + b' "$workspace/math_utils.py"
find "$tmpdir/.cgr-sweagent-trajectories" -type f -print
grep -q '"ok": true' "$tmpdir/adapter-result.json"
! grep -R --fixed-strings --quiet "$CGR_DRAFT_API_KEY" "$tmpdir/.cgr-sweagent-trajectories" "$tmpdir/adapter-result.json"
