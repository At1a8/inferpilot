#!/bin/bash
# InferPilot's server template: one sglang server for MODEL_PATH, in the foreground, after the model's recipe
# in the sglang cookbook. A feature or perf task whose user took it (server_script: template) launches every
# arm with this one, rendered by scripts/harness.py into its run directory. Each parameter line below then
# holds the task's value, else the one the feature's source tests with, else the cookbook recipe's for this
# GPU, else the default written here; the cookbook block holds the rest of the recipe, its environment and
# its other flags. InferPilot sets PORT, HIP_VISIBLE_DEVICES, PYTHONPATH (the tree under test) and
# EXTRA_ARGS (the feature's flags, empty for the baseline), which come last.
# Parameters, each described on its line: MODEL_PATH, TP, CONTEXT_LENGTH, MEM_FRACTION_STATIC,
# MAX_RUNNING_REQUESTS, CHUNKED_PREFILL_SIZE, PREFIX_CACHE, SERVER_ARGS.
set -u
# >>> parameters (scripts/harness.py reads and renders these lines)
MODEL_PATH="${MODEL_PATH:-}"                     # str; the model to serve: the task's model_path
TP="${TP:-1}"                                    # int; --tp: the run's GPUs
CONTEXT_LENGTH="${CONTEXT_LENGTH-}"              # int; --context-length (vLLM: --max-model-len); empty: the model's own
MEM_FRACTION_STATIC="${MEM_FRACTION_STATIC-}"    # float; --mem-fraction-static; empty: sglang's
MAX_RUNNING_REQUESTS="${MAX_RUNNING_REQUESTS-}"  # int; --max-running-requests (vLLM: --max-num-seqs); empty: sglang's
CHUNKED_PREFILL_SIZE="${CHUNKED_PREFILL_SIZE-}"  # int; --chunked-prefill-size (vLLM: --max-num-batched-tokens); empty: sglang's
PREFIX_CACHE="${PREFIX_CACHE:-1}"                # bool; 0: --disable-radix-cache (vLLM: --no-enable-prefix-caching)
SERVER_ARGS="${SERVER_ARGS-}"                    # str; more flags, the task's or the feature's, after the cookbook's
# <<< parameters
# >>> cookbook (scripts/harness.py writes the recipe's environment and other flags here)
cookbook=()
# <<< cookbook
: "${PORT:?InferPilot sets PORT}"
if [ -z "$MODEL_PATH" ]; then
  echo "server.sh: MODEL_PATH is not set" >&2
  exit 2
fi
args=(--model-path "$MODEL_PATH" --tp "$TP" --host 127.0.0.1 --port "$PORT")
[ -n "$CONTEXT_LENGTH" ] && args+=(--context-length "$CONTEXT_LENGTH")
[ -n "$MEM_FRACTION_STATIC" ] && args+=(--mem-fraction-static "$MEM_FRACTION_STATIC")
[ -n "$MAX_RUNNING_REQUESTS" ] && args+=(--max-running-requests "$MAX_RUNNING_REQUESTS")
[ -n "$CHUNKED_PREFILL_SIZE" ] && args+=(--chunked-prefill-size "$CHUNKED_PREFILL_SIZE")
[ "$PREFIX_CACHE" = 0 ] && args+=(--disable-radix-cache)
# shellcheck disable=SC2086  # flag lists
exec sglang serve "${args[@]}" ${cookbook[@]+"${cookbook[@]}"} $SERVER_ARGS ${EXTRA_ARGS:-}
