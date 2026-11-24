#!/bin/bash
# Accuracy of one running server with evalscope: InferPilot's G2 harness.
#
#   bash scripts/eval_accuracy.sh                        # GSM8K, all 1319 questions, once
#   bash scripts/eval_accuracy.sh gsm8k gpqa_diamond     # several sets, one after the other
#   API_URL=http://127.0.0.1:30010/v1 TAG=baseline RESULT_ROOT=<run dir>/accuracy bash scripts/eval_accuracy.sh
#
# Sets: gsm8k (1319, rule-graded), gpqa_diamond (198), mmlu_pro (12032), simple_qa (4326, LLM judge),
# hle (the 2158 text-only questions, LLM judge), longbench_v2 (503; LONGBENCH_LOCAL_PATH for a local copy).
#
# Every key comes from the environment (InferPilot's ~/.inferpilot_env); none is written here.
#   API_URL           the server's OpenAI endpoint (default http://127.0.0.1:30000/v1)
#   MODEL             default: the model the server serves; another name makes every request fail
#   TAG, RESULT_ROOT  each run of a set writes a directory of its own, <RESULT_ROOT>/<TAG>_<set>/<UTC time>-*/,
#                     with evalscope's report and log (defaults: eval, ./results); one line per set, naming
#                     that directory, is appended to <RESULT_ROOT>/summary.txt
#   BATCH             concurrent requests (default per set). Keep it within the server's KV capacity, or
#                     sglang retracts requests and the run slows down
#   LIMIT             at most this many questions per subset: a smoke test, not a result
#   WAIT_SERVER       seconds to wait for a server that is still starting (default 0)
#   MAX_TOKENS        default 32768, enough for the common sets; a truncated reasoning trace leaves the answer
#                     empty, so a model that reasons longer is given more. At most the server's context
#                     (max_model_len) less 2048 tokens for the question, and lowered to that
#   REASONING_EFFORT  default max; ENABLE_THINKING default 1 (chat_template_kwargs.thinking and
#                     .enable_thinking true; 0 sends both false). Thinking models score wrongly without
#                     them; ENABLE_THINKING=0 REASONING_EFFORT= for others
#   TOP_P, TOP_K      sampling overrides (default: the server's own)
#   DATASET_HUB       modelscope (default) or huggingface; HF_TOKEN for gated sets
#   EVALSCOPE         the evalscope executable (default: evalscope on PATH, which the bootstrap installs
#                     in a venv of its own)
#   Judge-graded sets: JUDGE_KEY (default LLM_GATEWAY_KEY), JUDGE_URL (default the AMD LLM gateway),
#   JUDGE_MODEL (default Claude-Opus-5; never the model under test), JUDGE_NTID (the gateway's user
#   header, which its SDK route requires). The key goes to evalscope only for a set a judge grades, and
#   where evalscope writes it (its configs, its log) it is replaced by <JUDGE_KEY> once the run ends or
#   is stopped (not by SIGKILL).
#
# Before each set the server's prefix cache is flushed; after it, /server_info's speculative accept
# length is read, so it covers this set only. Run nothing else on the server meanwhile. Every run samples
# afresh: evalscope's cache (--use-cache) is never used, so no answer of an earlier run, or of other
# settings, reaches a score.
# Exit status: 0 when every set produced a score over all its questions (with LIMIT, a report), 1 otherwise.
set -u

[ $# -eq 0 ] && set -- gsm8k

API_URL=${API_URL:-http://127.0.0.1:30000/v1}
TAG=${TAG:-eval}
RESULT_ROOT=${RESULT_ROOT:-./results}
WAIT_SERVER=${WAIT_SERVER:-0}
DATASET_HUB=${DATASET_HUB:-modelscope}
MAX_TOKENS=${MAX_TOKENS:-32768}
TOP_P=${TOP_P:-}
TOP_K=${TOP_K:-}
REASONING_EFFORT=${REASONING_EFFORT-max}
ENABLE_THINKING=${ENABLE_THINKING:-1}
# Templates read thinking (DeepSeek) or enable_thinking (Qwen3, which thinks unless it is told not to):
# both go, true or false, so ENABLE_THINKING=0 turns thinking off for either.
THINK=false
[ "${ENABLE_THINKING}" = 1 ] && THINK=true
TEMPLATE_KWARGS="{\"thinking\":${THINK},\"enable_thinking\":${THINK}}"
EVALSCOPE=${EVALSCOPE:-evalscope}
SERVER_URL=${API_URL%/v1}
SERVER_URL=${SERVER_URL%/}
JUDGE_URL=${JUDGE_URL:-https://llm-api.amd.com/Unified/v1}
JUDGE_MODEL=${JUDGE_MODEL:-Claude-Opus-5}
JUDGE_KEY=${JUDGE_KEY:-${LLM_GATEWAY_KEY:-}}
JUDGE_NTID=${JUDGE_NTID:-}
[ -n "${HF_TOKEN:-}" ] && export HF_TOKEN HUGGING_FACE_HUB_TOKEN="${HF_TOKEN}"

# DS_TOTAL checks the denominator afterwards: evalscope's --ignore-errors drops failed samples from both
# the predictions and the mean, so a missing question is invisible in the score. A set with one missing gets
# no score (but with LIMIT, a smoke test). 0: not a fixed count; then a dropped sample in the log counts.
dataset_profile() {  # <set> -> DS_BATCH, DS_ARGS, DS_TOTAL, DS_JUDGE
    DS_BATCH=256
    DS_ARGS=""
    DS_TOTAL=0
    DS_JUDGE=0
    case "$1" in
        gsm8k)        DS_TOTAL=1319 ;;
        gpqa_diamond) DS_TOTAL=198
                      [ "${DATASET_HUB}" = huggingface ] && \
                          DS_ARGS='{"gpqa_diamond":{"dataset_id":"Idavidrein/gpqa","subset_list":["gpqa_diamond"]}}' ;;
        mmlu_pro)     DS_TOTAL=12032 ;;
        simple_qa)    DS_TOTAL=4326; DS_JUDGE=1
                      [ "${DATASET_HUB}" = huggingface ] && \
                          DS_ARGS='{"simple_qa":{"dataset_id":"basicv8vc/SimpleQA"}}' ;;
        # About 14% of HLE comes with images, which a text-only server cannot see.
        hle)          DS_TOTAL=2158; DS_JUDGE=1
                      DS_ARGS='{"hle":{"extra_params":{"include_multi_modal":false}}}' ;;
        longbench_v2) DS_TOTAL=503
                      if [ -n "${LONGBENCH_LOCAL_PATH:-}" ]; then
                          DS_TOTAL=$(grep -c . "${LONGBENCH_LOCAL_PATH}/default_test.jsonl" 2>/dev/null || echo 0)
                          DS_ARGS="{\"longbench_v2\":{\"local_path\":\"${LONGBENCH_LOCAL_PATH}\",\"eval_split\":\"test\"}}"
                      elif [ "${DATASET_HUB}" = huggingface ]; then
                          DS_ARGS='{"longbench_v2":{"dataset_id":"zai-org/LongBench-v2"}}'
                      fi ;;
    esac
}

if [ "${WAIT_SERVER}" -gt 0 ]; then
    deadline=$((SECONDS + WAIT_SERVER))
    until curl -s -o /dev/null --max-time 5 "${SERVER_URL}/health" || [ "${SECONDS}" -ge "${deadline}" ]; do
        sleep 15
    done
fi

# evalscope sends --model as the request's model and names its result directory after it.
MODELS=$(curl -s --max-time 20 "${API_URL%/}/models")
SERVED=$(echo "${MODELS}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"][0]["id"])' 2>/dev/null)
if [ -z "${SERVED}" ]; then
    echo "eval_accuracy: no server answers at ${API_URL} (WAIT_SERVER=<seconds> waits for one that is starting)" >&2
    exit 1
fi
MODEL=${MODEL:-${SERVED}}
if [ "${MODEL}" != "${SERVED}" ]; then
    echo "eval_accuracy: MODEL=${MODEL}, but the server serves ${SERVED}; every request would fail. Unset MODEL." >&2
    exit 1
fi

# sglang rejects a request whose question and max_tokens exceed the context, and --ignore-errors would drop
# it from the score unseen: MAX_TOKENS keeps QUESTION_ROOM tokens of the server's context for the question.
QUESTION_ROOM=2048
CONTEXT=$(echo "${MODELS}" | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"][0].get("max_model_len") or "")' 2>/dev/null)
if [ -n "${CONTEXT}" ] && [ "${MAX_TOKENS}" -gt $((CONTEXT - QUESTION_ROOM)) ]; then
    if [ $((CONTEXT - QUESTION_ROOM)) -lt 1 ]; then
        echo "eval_accuracy: the server's context (${CONTEXT} tokens) leaves no room for an answer" >&2
        exit 1
    fi
    echo "eval_accuracy: MAX_TOKENS=${MAX_TOKENS} lowered to $((CONTEXT - QUESTION_ROOM)), what the server's" \
         "${CONTEXT}-token context leaves beside a question"
    MAX_TOKENS=$((CONTEXT - QUESTION_ROOM))
fi

# The judge is probed only when a requested set needs one, and before any inference: a bad key then
# fails in seconds, not after an hour of generation. Three tries ride out a transient gateway error.
NEED_JUDGE=0
for ds in "$@"; do
    dataset_profile "${ds}"
    [ "${DS_JUDGE}" = 1 ] && NEED_JUDGE=1
done
JUDGE_CODE=none
JUDGE_HEADERS=(-H "Ocp-Apim-Subscription-Key: ${JUDGE_KEY}")
[ -n "${JUDGE_NTID}" ] && JUDGE_HEADERS+=(-H "user: ${JUDGE_NTID}")
if [ "${NEED_JUDGE}" = 1 ] && [ -n "${JUDGE_KEY}" ]; then
    for attempt in 1 2 3; do
        JUDGE_CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "${JUDGE_HEADERS[@]}" "${JUDGE_URL%/}/models")
        [ "${JUDGE_CODE}" = 200 ] && break
        [ "${attempt}" -lt 3 ] && sleep 10
    done
fi

# What a run sampled with, kept next to its results; the template keywords as sent, not as ENABLE_THINKING.
sampling_settings() {
    echo "model=${MODEL} max_tokens=${MAX_TOKENS} effort=${REASONING_EFFORT:-none}" \
         "chat_template_kwargs=${TEMPLATE_KWARGS} top_p=${TOP_P:-server} top_k=${TOP_K:-server}"
}

# evalscope keeps its arguments, the judge's among them, in the run's configs and log: the results of a
# run hold no key (knowledge/guardrails.md, secrets).
redact_key() {  # <dir>
    [ -n "${JUDGE_KEY}" ] && [ -d "$1" ] || return 0
    KEY="${JUDGE_KEY}" python3 - "$1" <<'EOF'
import os, sys
key = os.environ["KEY"].encode()
for root, _, files in os.walk(sys.argv[1]):
    for name in files:
        path = os.path.join(root, name)
        with open(path, "rb") as f:
            data = f.read()
        if key in data:
            with open(path, "wb") as f:
                f.write(data.replace(key, b"<JUDGE_KEY>"))
EOF
}
# A run stopped on its way (SIGTERM, SIGINT, a timeout) is redacted as it exits; only SIGKILL leaves the key.
REDACT_DIR=""
trap 'redact_key "${REDACT_DIR}"' EXIT

FAILED=0
SUMMARY=""

run_one() {  # <set>
    local ds=$1 batch work_dir run_dir log eb="" tail_cfg gen_cfg judge_args rc accept rep line complete
    local -a limit_arg=() args_arg=() judge_arg=()
    dataset_profile "${ds}"
    batch=${BATCH:-${DS_BATCH}}
    work_dir="${RESULT_ROOT}/${TAG}_${ds}"
    mkdir -p "${RESULT_ROOT}"

    if [ "${DS_JUDGE}" = 1 ] && [ "${JUDGE_CODE}" != 200 ]; then
        if [ -z "${JUDGE_KEY}" ]; then
            echo "${ds}: needs an LLM judge, and neither JUDGE_KEY nor LLM_GATEWAY_KEY is set" >&2
        else
            echo "${ds}: needs an LLM judge, and the judge did not answer (HTTP ${JUDGE_CODE}; 401: a bad key," \
                 "400: JUDGE_NTID missing)" >&2
        fi
        SUMMARY="${SUMMARY}\n  ${ds}: skipped, no judge"
        FAILED=1
        return
    fi
    # A directory of its own: the report read below is this run's, never one an earlier run left there.
    mkdir -p "${work_dir}" && run_dir=$(mktemp -d "${work_dir}/$(date -u +%Y%m%d-%H%M%SZ)-XXXX") || exit 2
    log="${run_dir}/evalscope.log"
    sampling_settings > "${run_dir}/sampling_settings.txt"
    [ -n "${LIMIT:-}" ] && limit_arg=(--limit "${LIMIT}")
    [ -n "${DS_ARGS}" ] && args_arg=(--dataset-args "${DS_ARGS}")

    # extra_body is spread into the request's top level, so reasoning_effort must be a value the server
    # accepts; top_k is an sglang extension and only reaches the server through extra_body.
    [ -n "${REASONING_EFFORT}" ] && eb="\"reasoning_effort\":\"${REASONING_EFFORT}\""
    [ -n "${eb}" ] && eb="${eb},"
    eb="${eb}\"chat_template_kwargs\":${TEMPLATE_KWARGS}"
    if [ -n "${TOP_K}" ]; then
        [ -n "${eb}" ] && eb="${eb},"
        eb="${eb}\"top_k\":${TOP_K}"
    fi
    tail_cfg="\"max_tokens\":${MAX_TOKENS}"
    [ -n "${TOP_P}" ] && tail_cfg="${tail_cfg},\"top_p\":${TOP_P}"
    gen_cfg="{${tail_cfg}}"
    [ -n "${eb}" ] && gen_cfg="{\"extra_body\":{${eb}},${tail_cfg}}"

    echo "== ${ds}: model=${MODEL} batch=${batch} questions=${DS_TOTAL:-?} judge=${DS_JUDGE}; $(sampling_settings);" \
         "results ${run_dir}; log ${log}"
    curl -s -X POST --max-time 60 "${SERVER_URL}/flush_cache" > /dev/null

    # Only a set a judge grades gets the judge. The gateway wants its key in a header as well, which
    # evalscope's CLI passes through model_args.
    if [ "${DS_JUDGE}" = 1 ]; then
        judge_args="{\"model_id\": \"${JUDGE_MODEL}\", \"api_url\": \"${JUDGE_URL}\", \"api_key\": \"${JUDGE_KEY}\", \"model_args\": {\"default_headers\": {\"Ocp-Apim-Subscription-Key\": \"${JUDGE_KEY}\", \"user\": \"${JUDGE_NTID}\"}}}"
        judge_arg=(--judge-strategy auto --judge-model-args "${judge_args}")
    fi
    REDACT_DIR="${run_dir}"
    "${EVALSCOPE}" eval \
        --model "${MODEL}" \
        --api-url "${API_URL}" \
        --api-key EMPTY_KEY \
        --datasets "${ds}" \
        --dataset-hub "${DATASET_HUB}" \
        "${args_arg[@]}" \
        --generation-config "${gen_cfg}" \
        "${judge_arg[@]}" \
        --eval-batch-size "${batch}" \
        --stream \
        --ignore-errors \
        "${limit_arg[@]}" \
        --work-dir "${run_dir}" \
        --no-timestamp \
        --repeats 1 > "${log}" 2>&1
    rc=$?
    redact_key "${run_dir}"
    REDACT_DIR=""

    accept=$(curl -s --max-time 20 "${SERVER_URL}/server_info" | python3 -c \
        "import json,sys; print([s.get('avg_spec_accept_length') for s in json.load(sys.stdin)['internal_states']])" \
        2>/dev/null)
    rep=$(find "${run_dir}" -name '*.json' -path '*/reports/*' 2>/dev/null | sort | tail -1)
    if [ -z "${rep}" ]; then
        echo "${ds}: no report (evalscope exit ${rc}); the end of ${log}:" >&2
        tail -8 "${log}" | cut -c1-200 >&2
        SUMMARY="${SUMMARY}\n  ${ds}: failed (exit ${rc})"
        FAILED=1
        return
    fi

    line=$(DS_TOTAL="${DS_TOTAL}" LOG="${log}" LIMIT="${LIMIT:-}" python3 -c "
import json, os, sys
rep = json.load(open(sys.argv[1]))
total = int(os.environ['DS_TOTAL'])
metrics = rep.get('metrics', [])
out = []
for m in metrics:
    n, score = m['num'], m['score']
    name = m.get('name') or (m.get('identity') or {}).get('name') or m.get('legacy_name')
    out.append('%s=%s (%d questions)' % (name, score, n))
    if total and n < total:
        out.append('%d missing -> %.4f over all %d' % (total - n, score * n / total, total))
dropped = sum(1 for l in open(os.environ['LOG'], errors='replace') if 'Error ignored' in l)
if dropped:
    out.append('%d samples dropped by --ignore-errors' % dropped)
print('  '.join(out))
missing = (total and (not metrics or metrics[0]['num'] < total)) or (not total and dropped)
sys.exit(3 if missing and not os.environ['LIMIT'] else 0)
" "${rep}" 2>/dev/null)
    complete=$?
    echo "${ds}: ${line}  accept_length=${accept:-?}"
    # A score over the questions that got an answer is no score: the arms may miss different ones.
    if [ "${complete}" = 3 ]; then
        echo "${ds}: no score, since not every question got an answer: a question too long for MAX_TOKENS=${MAX_TOKENS}" \
             "within the server's context, or a request that failed (${log})" >&2
        SUMMARY="${SUMMARY}\n  ${ds}: failed, not every question got an answer (${line})"
        FAILED=1
        return
    fi
    SUMMARY="${SUMMARY}\n  ${ds}: ${line}  accept_length=${accept:-?}"

    python3 -c "
import json, sys
rep = json.load(open(sys.argv[1]))
metrics = rep.get('metrics', [])
agg = {m.get('name') or (m.get('identity') or {}).get('name'): m['score'] for m in metrics}
if metrics:
    agg = {'score': metrics[0]['score'], 'num': metrics[0]['num'], **agg}
print('%s %s: accept_length=%s aggregate=%s dir=%s' % (sys.argv[2], sys.argv[3], sys.argv[4], agg, sys.argv[5]))
" "${rep}" "${TAG}" "${ds}" "${accept:-[None]}" "${TAG}_${ds}/${run_dir##*/}" >> "${RESULT_ROOT}/summary.txt"
}

for ds in "$@"; do
    run_one "${ds}"
done
echo -e "== summary (${TAG}):${SUMMARY}"
exit "${FAILED}"
