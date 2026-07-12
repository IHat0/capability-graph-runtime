#!/usr/bin/env bash
set -euo pipefail

readonly REPOSITORY_URL="https://github.com/jkoppel/QuixBugs"
readonly PINNED_COMMIT="4257f44b0ff1181dedaedee6a447e133219fcebf"
readonly SOURCE_FILE="python_programs/gcd.py"
readonly TEST_FILE="python_testcases/test_gcd.py"

root="${1:-.quixbugs-src}"
python_bin="${CGR_QUIXBUGS_PYTHON:-python}"

if [[ ! -d "$root/.git" ]]; then
  git clone "$REPOSITORY_URL" "$root"
else
  git -C "$root" fetch origin "$PINNED_COMMIT"
fi

git -C "$root" checkout --detach "$PINNED_COMMIT"
git -C "$root" reset --hard "$PINNED_COMMIT"
git -C "$root" clean -fd -- .pytest_cache python_programs/__pycache__ python_testcases/__pycache__

test -f "$root/$SOURCE_FILE"
test -f "$root/$TEST_FILE"
test "$(git -C "$root" rev-parse HEAD)" = "$PINNED_COMMIT"
test -z "$(git -C "$root" status --porcelain=v1)"

set +e
(cd "$root" && "$python_bin" -m pytest -q "$TEST_FILE")
verifier_status=$?
set -e
if [[ $verifier_status -eq 0 ]]; then
  echo "error: quixbugs.gcd unexpectedly passes in the pinned buggy checkout" >&2
  exit 1
fi

printf 'quixbugs_root=%s\npinned_commit=%s\ninitial_verifier_exit_code=%s\n' \
  "$root" "$PINNED_COMMIT" "$verifier_status"
