#!/usr/bin/env bash
# What a pull request is checked with, offline and without a GPU. basic, on every pull request: the syntax
# of every script, shellcheck, the runner's self-test, the two backends side by side, and the dry run's and
# unit tests' basic part. Each task group (feature, issue, ci, perf, scan, kernel) adds its own dry-run
# scenarios and unit tests, run when a change reaches its task (tests/groups.json, tests/groups.py).
# CI runs it on every pull request (.github/workflows/tests.yml); run it before you push. Needs Python
# 3.10+ and Node.js 20+; shellcheck is used when installed.
#
#   tests/run_all.sh                      # basic and every task group
#   tests/run_all.sh --changed [<base>]   # basic and the groups your changes since <base> reach (origin/main)
#   tests/run_all.sh --group perf,kernel  # basic and these groups
set -u
cd "$(dirname "$0")/.." || exit 2

usage() { sed -n '9,11s/^# *//p' "$0" >&2; exit 2; }

python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null \
  || { echo "tests/run_all.sh: needs python3 3.10 or newer" >&2; exit 2; }
node -e 'process.exit(Number(process.versions.node.split(".")[0]) < 20 ? 1 : 0)' 2>/dev/null \
  || { echo "tests/run_all.sh: needs Node.js 20 or newer (bootstrap --runner installs it in a container)" >&2; exit 2; }

[ $# -le 2 ] || usage
every=$(python3 -B tests/groups.py select --all) || exit 2
case "${1:-}" in
  "") groups=$every ;;
  --changed) echo "what changed since ${2:-origin/main}:"
             groups=$(python3 -B tests/groups.py select --changed "${2:-origin/main}") || exit 2 ;;
  --group) [ -n "${2:-}" ] || usage
           groups=$(python3 -B tests/groups.py select --group "$2") || exit 2 ;;
  *) usage ;;
esac
echo "task groups: ${groups:-none} (basic always)"

passed=0
failed=()
step() {  # step <name> <command...>: one line for it, and its output when it fails
  local name=$1 out rc start end
  shift
  start=$(date +%s%N)
  out=$("$@" 2>&1)
  rc=$?
  end=$(date +%s%N)
  if [ "$rc" -eq 0 ]; then
    passed=$((passed + 1))
    printf 'ok    %-44s %5.1f s  %s\n' "$name" "$(( (end - start) / 1000000 ))e-3" \
      "$(printf '%s\n' "$out" | grep -E 'checks passed|^Ran ' | tail -1)"
  else
    failed+=("$name")
    printf 'FAIL  %-44s exit %s\n' "$name" "$rc"
    printf '%s\n' "$out" | tail -60 | sed 's/^/      /'
  fi
}

step "syntax: inferpilot.js, runtime, tests" tests/syntax.sh js
step "syntax: shell scripts" tests/syntax.sh sh
step "syntax: Python" tests/syntax.sh py
if command -v shellcheck >/dev/null; then
  step "shellcheck: shell scripts" shellcheck -S warning scripts/*.sh templates/harness/*.sh tests/*.sh
else
  echo "skip  shellcheck: not installed (CI's runners have it)"
fi
step "runner (tests/runtime_selftest.mjs)" node tests/runtime_selftest.mjs
step "both backends (tests/backend_parity.mjs)" node tests/backend_parity.mjs
# shellcheck disable=SC2086
if [ "$groups" = "$every" ]; then
  step "dry run: all (tests/dry_run.mjs)" node tests/dry_run.mjs
  step "unit tests: all" python3 -B -m unittest discover -s tests
else
  step "dry run: basic and those groups" node tests/dry_run.mjs --only "$(python3 -B tests/groups.py dry-run $groups)"
  step "unit tests: basic and those groups" python3 -B tests/groups.py unittest $groups
fi

if [ ${#failed[@]} -eq 0 ]; then
  echo "all $passed steps passed"
else
  echo "${#failed[@]} of $((passed + ${#failed[@]})) steps failed: ${failed[*]}"
  exit 1
fi
