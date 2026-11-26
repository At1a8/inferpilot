#!/bin/bash
# G4's load (knowledge/validation_doctrine.md): the run's performance client, run back to back against one
# server until HOURS have passed. The pass then in progress finishes, so the load lasts longer than HOURS.
#
#   PORT=30000 OUT_DIR=RUN_DIR/logs/g4_feature HOURS=48 CONCURRENCY=64 \
#     bash scripts/stability.sh RUN_DIR/scripts/perf.sh
#
# The client is the task's client_script, or InferPilot's templates/harness/perf.sh as rendered for the
# run, and runs unchanged: with PORT and OUT_DIR as given here and, when set, CONCURRENCY (perf.sh reads
# it: one concurrency, the highest of the task's workload). perf.sh keeps its concurrency full while it
# runs; the server is idle only while one pass ends and the next starts, and every pass's line says when it
# started and how long it took, so the record shows how much of the run was under load.
#
# Each pass adds one line to OUT_DIR/stability.jsonl: its number, when it started (UTC), its seconds, the
# client's exit status, what the client appended to OUT_DIR/bench.jsonl in it (perf.sh's result, with the
# throughput and the requests completed; [] for a client that writes none), the memory in use on every GPU
# (bytes, as its DRM device reports it, by PCI address; the devices are read under DRM_ROOT, /sys/class/drm
# unless given) and on the host (kB: its total less what is available) after it. The client's own output
# goes to OUT_DIR/stability.log. OUT_DIR is touched after every pass: the machine's lock takes a run
# directory with nothing new for hours as a dead run's.
#
# The first pass whose client does not exit 0 ends the run: a request failed or the server is gone, and
# G4 has failed there. Exit status: 0 after HOURS of passes that all exited 0, 1 when one did not, 2 on a
# usage error.
set -u
usage() {
  echo "usage: PORT=<port> OUT_DIR=<dir> [HOURS=48] [CONCURRENCY=<n>] stability.sh <client script>" >&2
  exit 2
}
client="${1:-}"
HOURS="${HOURS:-48}"
if [ $# -ne 1 ] || [ ! -f "$client" ] || [ -z "${PORT:-}" ] || [ -z "${OUT_DIR:-}" ]; then usage; fi
if ! end=$(python3 - "$HOURS" 2>/dev/null <<'EOF'
import math, sys, time
hours = float(sys.argv[1])
if not math.isfinite(hours) or hours < 0:
    sys.exit(1)
print(int(time.time() + hours * 3600))
EOF
); then
  echo "stability.sh: HOURS=$HOURS is not a number of hours" >&2
  exit 2
fi
mkdir -p "$OUT_DIR" || exit 2
pass=0
while :; do
  pass=$((pass + 1))
  before=0
  [ -f "$OUT_DIR/bench.jsonl" ] && before=$(wc -l < "$OUT_DIR/bench.jsonl")
  started=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  t0=$(date +%s)
  echo "=== pass $pass, $started" >> "$OUT_DIR/stability.log"
  bash "$client" < /dev/null >> "$OUT_DIR/stability.log" 2>&1
  rc=$?
  python3 - "$OUT_DIR" "$pass" "$started" "$(($(date +%s) - t0))" "$rc" "$before" "${DRM_ROOT:-/sys/class/drm}" <<'EOF' || exit 2
import glob, json, os, re, sys
out, number, started, seconds, status, before, drm = sys.argv[1:]
client = []
try:
    with open(os.path.join(out, "bench.jsonl"), encoding="utf-8") as f:
        for line in f.read().splitlines()[int(before):]:
            try:
                client.append(json.loads(line))
            except ValueError:
                client.append(line)
except OSError:
    pass
vram = {}
for path in sorted(glob.glob(os.path.join(drm, "card*", "device", "mem_info_vram_used"))):
    device = os.path.dirname(path)
    name = os.path.basename(os.path.realpath(device))
    if not re.fullmatch(r"[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-9a-f]", name):
        name = os.path.basename(os.path.dirname(device))
    try:
        with open(path, encoding="utf-8") as f:
            vram[name] = int(f.read().strip())
    except (OSError, ValueError):
        vram[name] = None
host = None
try:
    with open("/proc/meminfo", encoding="utf-8") as f:
        kb = {line.split(":")[0]: int(line.split()[1]) for line in f if line.startswith(("MemTotal:", "MemAvailable:"))}
    host = kb["MemTotal"] - kb["MemAvailable"]
except (OSError, ValueError, IndexError, KeyError):
    pass
with open(os.path.join(out, "stability.jsonl"), "a", encoding="utf-8") as f:
    f.write(json.dumps({"pass": int(number), "started": started, "seconds": int(seconds), "exit": int(status),
                        "client": client, "vram_used": vram, "host_mem_used_kb": host}) + "\n")
EOF
  touch "$OUT_DIR"
  if [ "$rc" -ne 0 ]; then
    echo "stability.sh: pass $pass: the client exited $rc, and the run ends here ($OUT_DIR/stability.log)" >&2
    exit 1
  fi
  [ "$(date +%s)" -lt "$end" ] || break
done
exit 0
