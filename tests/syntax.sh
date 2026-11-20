#!/usr/bin/env bash
# The syntax of every script: inferpilot.js as the Workflow tool compiles it (the runner adds control),
# the modules and tests as Node loads them, the shell scripts as bash reads them, and every Python file
# parsed (not compiled to files: no __pycache__ in the checkout). Exit 1 at the first that fails.
#
#   tests/syntax.sh            # all of them
#   tests/syntax.sh js|sh|py   # one kind
set -u
cd "$(dirname "$0")/.." || exit 2

js() {
  node --input-type=module -e "
    import { readFileSync } from 'node:fs';
    import { scriptBody } from './runtime/engine.mjs';
    const AsyncFunction = Object.getPrototypeOf(async () => {}).constructor;
    new AsyncFunction('agent', 'parallel', 'pipeline', 'phase', 'log', 'workflow', 'args', 'budget',
      scriptBody(readFileSync('inferpilot.js', 'utf8')));" || return 1
  local f
  for f in runtime/*.mjs tests/*.mjs tests/fixtures/*.mjs; do node --check "$f" || return 1; done
}

sh() {
  local f
  for f in scripts/*.sh templates/harness/*.sh tests/*.sh; do bash -n "$f" || return 1; done
}

py() {
  python3 - inferpilot/*.py scripts/*.py tests/*.py setup.py <<'EOF'
import ast, sys
for path in sys.argv[1:]:
    with open(path, encoding="utf-8") as f:
        ast.parse(f.read(), path)
EOF
}

case "${1:-all}" in
  js|sh|py) "$1" ;;
  all) js && sh && py ;;
  *) sed -n '6,7s/^# *//p' "$0" >&2; exit 2 ;;
esac
