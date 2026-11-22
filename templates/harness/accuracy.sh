#!/bin/bash
# InferPilot's accuracy template: scripts/eval_accuracy.sh (evalscope) with the run's settings written in, so
# that every arm is measured the same way. scripts/harness.py renders it into the run directory, where each
# parameter line below holds the task's value, else the one the feature's source tests with (its sampling,
# thinking on or off), else the default written here, eval_accuracy.sh's own. Every setting is exported, so
# what the run sets reaches eval_accuracy.sh for every arm. The caller sets API_URL, TAG and RESULT_ROOT as
# for eval_accuracy.sh; arguments, when given, replace SETS. INFERPILOT_DIR is the checkout whose
# eval_accuracy.sh runs; rendering writes it in.
# Parameters, each described on its line: SETS, MAX_TOKENS, ENABLE_THINKING, REASONING_EFFORT, BATCH, TOP_P,
# TOP_K.
set -u
# >>> parameters (scripts/harness.py reads and renders these lines)
SETS="${SETS:-gsm8k}"                       # strs; the sets when none are given: gsm8k, and gpqa_diamond simple_qa too when the goal says the change may lose accuracy
MAX_TOKENS="${MAX_TOKENS:-32768}"           # int; tokens per answer, lowered to what the server's context leaves beside a question
ENABLE_THINKING="${ENABLE_THINKING:-1}"     # bool; 1: thinking on, 0: off, through both chat-template keys
REASONING_EFFORT="${REASONING_EFFORT-max}"  # str; the reasoning_effort of each request; empty: none
BATCH="${BATCH-}"                           # int; concurrent requests; empty: each set's own
TOP_P="${TOP_P-}"                           # float; empty: the server's
TOP_K="${TOP_K-}"                           # int; empty: the server's
# <<< parameters
# Without thinking there is no reasoning to spend an effort on.
[ "$ENABLE_THINKING" = 1 ] || REASONING_EFFORT=
INFERPILOT_DIR="${INFERPILOT_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
export MAX_TOKENS ENABLE_THINKING REASONING_EFFORT BATCH TOP_P TOP_K
# shellcheck disable=SC2086  # a list of set names
[ $# -gt 0 ] || set -- $SETS
exec bash "$INFERPILOT_DIR/scripts/eval_accuracy.sh" "$@"
