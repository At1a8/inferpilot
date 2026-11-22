#!/bin/bash
# InferPilot's performance client template: sglang's serving benchmark on random prompts of a fixed length,
# one run per concurrency, each with PROMPTS_PER_CONC prompts and WARMUP_PER_CONC warm-up requests per
# concurrent request, after a flush of the prefix cache. A feature or perf task whose user took it
# (client_script: template), or Spec's fallback client for the source's scenarios, measures with this one;
# a perf task's numbers count, a feature's are reported, never a claim. scripts/harness.py renders it into
# the run directory, where each parameter line below holds the task's value (its workload), else the one
# the feature's source tests with, else the default written here. InferPilot sets PORT, and OUT_DIR, where
# each run writes bench_<ISL>_<OSL>_c<n>_<time>.log and appends its result to bench.jsonl.
# Parameters, each described on its line: ISL, OSL, RANGE_RATIO, CONCURRENCY, PROMPTS_PER_CONC,
# WARMUP_PER_CONC, DATASET, DATASET_PATH, REQUEST_RATE, TEMPERATURE, SEED, PROFILE, PROFILE_STEPS, BENCH_ARGS.
set -u
# >>> parameters (scripts/harness.py reads and renders these lines)
ISL="${ISL:-8192}"                          # int; input tokens per request
OSL="${OSL:-1024}"                          # int; output tokens per request
RANGE_RATIO="${RANGE_RATIO:-1}"             # float; --random-range-ratio; 1: every request exactly ISL and OSL
CONCURRENCY="${CONCURRENCY:-32}"            # ints; the concurrencies, one run each
PROMPTS_PER_CONC="${PROMPTS_PER_CONC:-4}"   # int; prompts per run, per concurrent request
WARMUP_PER_CONC="${WARMUP_PER_CONC:-2}"     # int; warm-up requests per run, per concurrent request
DATASET="${DATASET:-random}"                # str; --dataset-name
DATASET_PATH="${DATASET_PATH-}"             # str; --dataset-path; empty: none
REQUEST_RATE="${REQUEST_RATE:-inf}"         # str; requests per second; inf: as fast as the concurrency allows
TEMPERATURE="${TEMPERATURE-}"               # float; --temperature; empty: the benchmark's
SEED="${SEED:-1}"                           # int; --seed
PROFILE="${PROFILE:-0}"                     # bool; 1: a torch profile of PROFILE_STEPS steps in each run
PROFILE_STEPS="${PROFILE_STEPS:-4}"         # int; --profile-num-steps
BENCH_ARGS="${BENCH_ARGS-}"                 # str; more flags for the benchmark
# <<< parameters
: "${PORT:?InferPilot sets PORT}" "${OUT_DIR:?InferPilot sets OUT_DIR}"
ulimit -n 65536 2>/dev/null || true
mkdir -p "$OUT_DIR"
# Newer sglang has the benchmark as sglang.benchmark.serving, older images as sglang.bench_serving.
bench=sglang.benchmark.serving
python3 -c 'import sglang.benchmark.serving' 2>/dev/null || bench=sglang.bench_serving
status=0
for c in $CONCURRENCY; do
  args=(--port "$PORT" --dataset-name "$DATASET" --random-input-len "$ISL" --random-output-len "$OSL"
        --random-range-ratio "$RANGE_RATIO" --max-concurrency "$c" --num-prompts $((PROMPTS_PER_CONC * c))
        --warmup-requests $((WARMUP_PER_CONC * c)) --request-rate "$REQUEST_RATE" --seed "$SEED" --flush-cache
        --output-file "$OUT_DIR/bench.jsonl")
  [ -n "$DATASET_PATH" ] && args+=(--dataset-path "$DATASET_PATH")
  [ -n "$TEMPERATURE" ] && args+=(--temperature "$TEMPERATURE")
  [ "$PROFILE" = 1 ] && args+=(--profile --profile-num-steps "$PROFILE_STEPS")
  log="$OUT_DIR/bench_${ISL}_${OSL}_c${c}_$(date -u +%Y%m%d-%H%M%SZ).log"
  echo "python3 -m $bench ${args[*]} $BENCH_ARGS" > "$log"
  before=0
  [ -f "$OUT_DIR/bench.jsonl" ] && before=$(wc -l < "$OUT_DIR/bench.jsonl")
  # shellcheck disable=SC2086  # a flag list
  python3 -m "$bench" "${args[@]}" $BENCH_ARGS >> "$log" 2>&1 || status=1
  # The benchmark exits 0 when requests failed: a run counts only when every prompt completed.
  if ! python3 - "$OUT_DIR/bench.jsonl" "$before" "$((PROMPTS_PER_CONC * c))" >> "$log" 2>&1 <<'EOF'
import json, sys
path, before, want = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
try:
    new = open(path, encoding="utf-8").read().splitlines()[before:]
except OSError:
    new = []
got = json.loads(new[-1]).get("completed") if new else None
if got != want:
    print("perf.sh: %s of the %d requests completed: this run is no measurement" % (got, want))
    sys.exit(1)
EOF
  then
    echo "perf.sh: concurrency $c: not every request completed ($log)" >&2
    status=1
  fi
done
exit "$status"
