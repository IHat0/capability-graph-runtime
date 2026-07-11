#!/usr/bin/env bash
set -euo pipefail

cd "$HOME/CGR-Ticket-1.1"

# Keep SWE-agent separate from CGR's existing virtual environment.
if [[ ! -x .venv-sweagent/bin/python ]]; then
  python3.12 -m venv .venv-sweagent
fi
source .venv-sweagent/bin/activate
python -m pip install --quiet --upgrade pip
python -m pip install --quiet 'git+https://github.com/SWE-agent/SWE-agent.git@0f3acafacabc0def8cc76b4e48acb4b6cf302cb9'
python -m pip install --quiet -e .

export CGR_DRAFT_BASE_URL='http://127.0.0.1:8000/v1'
export CGR_DRAFT_API_KEY='cgr-aws-key'
export CGR_DRAFT_MODEL='Qwen/Qwen2.5-Coder-7B-Instruct'
export CGR_DRAFT_MAX_MODEL_LEN=16384
export CGR_SWEBENCH_SCAFFOLD_ID='swe-agent-v1.1.0-0f3acaf'
export CGR_SWE_AGENT_SOURCE="$PWD/.swe-agent-src"
export CGR_SWE_AGENT_EXECUTABLE="$PWD/.venv-sweagent/bin/sweagent"
export CGR_SWEBENCH_AGENT_COMMAND='[
  "cgr-swebench-swe-agent-adapter",
  "--workspace", "{workspace}",
  "--problem-file", "{problem_file}",
  "--mode", "{mode}",
  "--max-steps", "{max_steps}",
  "--max-calls", "{max_calls}"
]'

test -f "$CGR_SWE_AGENT_SOURCE/config/default.yaml"

tmpdir="${CGR_SWE_AGENT_SMOKE_ROOT:-$(mktemp -d)}"
mkdir -p "$tmpdir"
workspace="$tmpdir/workspace"
mkdir -p "$workspace"
git -C "$workspace" init -q
git -C "$workspace" config user.email 'cgr-smoke@example.invalid'
git -C "$workspace" config user.name 'CGR smoke'
printf 'def add(a, b):\n    return a - b\n' > "$workspace/math_utils.py"
git -C "$workspace" add math_utils.py
git -C "$workspace" commit -qm initial
printf 'Fix math_utils.py so add(2, 3) returns 5. Preserve the existing function.\n' > "$tmpdir/problem.txt"

# `sweagent run --help` returns 2 in v1.1.0. Keep it as a diagnostic only.
preflight_stdout="$tmpdir/preflight-run-help.stdout"
preflight_stderr="$tmpdir/preflight-run-help.stderr"
set +e
"$CGR_SWE_AGENT_EXECUTABLE" run --help >"$preflight_stdout" 2>"$preflight_stderr"
preflight_status=$?
set -e

adapter_stdout="$tmpdir/adapter.stdout"
adapter_stderr="$tmpdir/adapter.stderr"
set +e
CGR_SWEBENCH_DEBUG_TRACE=1 cgr-swebench-swe-agent-adapter \
  --workspace "$workspace" \
  --problem-file "$tmpdir/problem.txt" \
  --mode baseline \
  --max-steps 8 \
  --max-calls 5 >"$adapter_stdout" 2>"$adapter_stderr"
adapter_status=$?
set -e

diff_path="$tmpdir/final-git-diff.patch"
git -C "$workspace" diff -- math_utils.py >"$diff_path"
artifact_root="$tmpdir/.cgr-sweagent-trajectories"
mapfile -t artifacts < <(find "$artifact_root" -type f -print 2>/dev/null || true)

final_status=0
[[ "$adapter_status" -eq 0 ]] || final_status=1
grep -q '"ok": true' "$adapter_stdout" || final_status=1
grep -q 'return a + b' "$workspace/math_utils.py" || final_status=1
[[ -s "$diff_path" ]] || final_status=1
[[ "${#artifacts[@]}" -gt 0 ]] || final_status=1
if grep -R --fixed-strings --quiet "$CGR_DRAFT_API_KEY" \
  "$artifact_root" "$adapter_stdout" "$adapter_stderr" 2>/dev/null; then
  final_status=1
fi

printf '\n=== SWE-agent preflight ===\n'
printf 'preflight_status=%s\n' "$preflight_status"
printf 'preflight_stdout=%s\npreflight_stderr=%s\n' "$preflight_stdout" "$preflight_stderr"
cat "$preflight_stdout"
cat "$preflight_stderr" >&2
printf '\n=== Adapter ===\n'
printf 'adapter_exit_code=%s\n' "$adapter_status"
printf 'adapter_stdout=%s\nadapter_stderr=%s\n' "$adapter_stdout" "$adapter_stderr"
cat "$adapter_stdout"
cat "$adapter_stderr" >&2
printf '\n=== Final workspace ===\n'
printf 'git_diff=%s\n' "$diff_path"
cat "$diff_path"
printf 'math_utils.py:\n'
cat "$workspace/math_utils.py"
printf '\n=== Trajectories/artifacts ===\n'
printf '%s\n' "${artifacts[@]}"
printf 'smoke_root=%s\nfinal_status=%s\n' "$tmpdir" "$final_status"
exit "$final_status"
