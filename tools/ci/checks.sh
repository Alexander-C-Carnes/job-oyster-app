#!/usr/bin/env bash
# The checks oyster-ci runs on every `ship` PR (docs/ci.md), so a session runs the same thing before opening one:
#
#   tools/ci/checks.sh              # from a worktree or the main checkout; areas changed since origin/main
#   tools/ci/checks.sh <base>       # … since the merge base with <base>
#
# Always: the tests (pytest) and the privacy check on HEAD (tools/privacy_check.py: nothing personal in a
# public repo). Shell scripts and Swift files the change touches are also parsed. Python is $OYSTER_CI_PY,
# else this checkout's .venv, else the main checkout's. Failures print lines starting with ✘ (the gate quotes the last).
set -uo pipefail
cd "$(git rev-parse --show-toplevel)" || exit 1

PY="${OYSTER_CI_PY:-}"
if [[ -z "$PY" ]]; then
  common="$(cd "$(git rev-parse --git-common-dir)/.." && pwd)"
  for p in .venv/bin/python "$common/.venv/bin/python"; do
    if [[ -x "$p" ]]; then PY="$p"; break; fi
  done
fi
[[ -n "$PY" ]] || { echo "✘ no Python venv: run ./oyster update in the main checkout"; exit 1; }

BASE="$(git merge-base "${1:-origin/main}" HEAD 2>/dev/null || echo HEAD)"
FILES="$(git diff --name-only "$BASE"; git ls-files --others --exclude-standard)"
failed=0
fail() { echo "✘ $*"; failed=1; }

echo "▶ tests"
out="$(mktemp)"
"$PY" -m pytest -q -p no:cacheprovider >"$out" 2>&1; rc=$?
tail -n 25 "$out"
if (( rc )); then
  fail "tests failed: $(grep -E '^(FAILED|ERROR) ' "$out" | head -3 | sed -E 's/^(FAILED|ERROR) //; s/ - .*//' | paste -sd ' ' -)"
fi
rm -f "$out"

echo "▶ privacy (this repo is public)"
tmp="$(mktemp -d)"
if ! out="$(TMPDIR="$tmp" "$PY" tools/privacy_check.py --rev HEAD 2>&1)"; then
  echo "$out" | tail -n 20
  fail "personal details in the files: $(echo "$out" | grep -m1 -E '^  [^ ]+:[0-9]+:' | sed 's/^  //')"
else
  echo "$out" | grep -m1 'Checked' || true
fi
rm -rf "$tmp"

shells="$(grep -E '(\.sh$|^oyster$|^start\.sh$)' <<<"$FILES" || true)"
if [[ -n "$shells" ]]; then
  echo "▶ shell"
  while read -r f; do
    [[ -f "$f" ]] || continue
    bash -n "$f" || fail "$f doesn't parse (bash -n)"
  done <<<"$shells"
  if command -v shellcheck >/dev/null && grep -q '^tools/ci/' <<<"$shells"; then
    shellcheck -S error tools/ci/*.sh || fail "shellcheck errors in tools/ci"
  fi
fi

swift="$(grep -E '^mac/.*\.swift$' <<<"$FILES" || true)"
if [[ -n "$swift" ]] && command -v swiftc >/dev/null; then
  echo "▶ swift"
  while read -r f; do
    [[ -f "$f" ]] || continue
    swiftc -typecheck "$f" || fail "$f doesn't compile (swiftc -typecheck)"
  done <<<"$swift"
fi

(( failed )) && exit 1
echo "✔ checks passed"
