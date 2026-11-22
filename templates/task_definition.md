# Task definition — what InferPilot needs to start a task

The fields are `inferpilot.js`'s arguments, so a definition can be passed as it is, produced by the Intake
phase from a request in plain words (`roles/intake.md`), or taken from a scan's task queue; every role
reads the same names. Operator settings (`inferpilot_dir`, `exp_root`, budgets, `review_model`) are not part
of it.

## 1. Fields

| Field | Meaning | feature | issue | ci | perf | scan |
|---|---|---|---|---|---|---|
| `mode` | the kind of task | required | required | required | required | required |
| `task` | the source of truth: upstream PR or design URL, issue URL, CI job or tracker cluster, performance goal | required | required | required | required | — |
| `framework_dir` | the framework checkout each task works in, on its own branch from the current main (absolute path; for sglang the image's `/sgl-workspace/sglang` when not given, which the image installs sglang from) | required | required | required | required | the upstream scan reads it |
| `model_path` | the model the baseline and the gates run | required | recommended: the reporter's model | — | required | — |
| `workload` | the fixed benchmark, e.g. `{"isl": 8192, "osl": 1024, "conc": 64}` | when the task claims a gain | for a performance issue | — | required | — |
| `server_script` | the user's script that starts one server for `model_path` (§1.1), or `template`: InferPilot's, after the model's sglang cookbook recipe (§1.2) | required | recommended | — | required | — |
| `client_script` | the user's script that runs the fixed workload against that server (§1.1), or `template`: InferPilot's, on the workload; a feature's numbers on it are reported, never a claim (§1.2) | when a workload is given | for a performance issue | — | required | — |
| `accuracy_script` | the user's script that measures accuracy against that server (§1.1); without one, InferPilot's (§1.2) | optional | optional | — | optional | — |
| `harness` | values for InferPilot's templates, e.g. `{"CONTEXT_LENGTH": 8192}` (§1.2) | optional | optional | — | optional | — |
| `goal` | what done means, measurable when the mode measures | recommended | — | — | recommended | — |
| `framework` | default `sglang` | | | | | |
| `platform`, `gpu_ids`, `container` | target and environment; empty `gpu_ids` lets Setup pick free GPUs | | | | | |
| `apply_prs` | candidate PRs to apply and A/B | | | | optional | |
| `sources`, `since` | what to scan, from when | | | | | optional |
| `stop_after` | stop after this phase | optional | optional | optional | optional | optional |
| `bisect_fixed` | when the reporter's code fails and main passes, find the commit that fixed it | | optional | | | |
| `confirm_leads` | default `false`: leads are listed with how to reproduce them; `true`: each is checked on the base commit, and those that show there are drafted as issues | optional | optional | optional | optional | |
| `resume_from`, `run_dir` | `implement`: continue after a human approved the design | optional | | | | |
| `auto_approve_design` | default `true`: a design the architect flags goes on to Implement and the report says so; `false`: it waits for a human review | optional | | | | |
| `tune` | default `false`: the feature runs at the configuration of the target PR or design; `true`: a work package chooses its knobs by measurement | optional | | | | |
| `held_out`, `guards`, `sweep` | performance checks beyond the task's workload and goal, each off by default: a held-out operating point the gain must also hold at, guard workloads of InferPilot's choosing, points beyond the workload's own | optional | optional | | optional | |
| `kernel_backend` with `kernel_backend_dir` or `kernel_backend_command` | where kernel hand-offs go | optional | optional | optional | optional | |

`inferpilot.js` checks the required fields (and a back end's own field) for every definition; one that
lacks something returns `needs_input` and starts nothing. The intake asks for the recommended fields
when the likely path of the task needs them.

### 1.1 Server and client scripts

A task that measures accuracy or performance brings the scripts that run its baseline, so every arm is
launched and measured the way the user runs the model, not the way an agent guessed. A script the user
gives runs unchanged, whatever the templates or the feature's source say. A user who has none may take
InferPilot's template instead (§1.2) by answering `template` when the run asks; it is never chosen for
the user.

- **`server_script`** starts one server in the foreground. InferPilot runs it with `PORT`,
  `HIP_VISIBLE_DEVICES`, `PYTHONPATH` (the tree under test) and `EXTRA_ARGS` (the feature's flags; empty
  for the baseline) set. The script passes `--port $PORT` and `$EXTRA_ARGS` to the server and sets
  neither `HIP_VISIBLE_DEVICES` nor `PYTHONPATH` itself.
- **`client_script`** runs the fixed workload against `PORT` and writes its logs to `OUT_DIR`. It is the
  benchmark: InferPilot runs it unchanged and never edits it. If it reads `CONCURRENCY` (a
  space-separated list), the held-out point (`held_out`) can be measured at half the concurrency.
- **`accuracy_script`**, optional, measures accuracy against the server at `API_URL`
  (`http://127.0.0.1:$PORT/v1`; `PORT` is set too) and writes its results under `RESULT_ROOT`, with
  `TAG` naming the arm. InferPilot runs it unchanged for every arm and compares its scores set by set,
  each at most 1 point below the baseline.
- **Accuracy** needs no script of the user's: InferPilot's `scripts/eval_accuracy.sh` measures gsm8k
  through evalscope, once, and the change passes at most 1 point below the baseline. A change the user
  says may lose accuracy, said in `goal`, runs gpqa_diamond and simple_qa as well; other suites or more
  runs are named there too. It runs through InferPilot's `accuracy.sh` (§1.2), which fixes the settings
  for every arm.

```bash
# server.sh
sglang serve --trust-remote-code --model-path /models/DeepSeek-V4.1-Flash --tp 4 --ep-size 4 \
  --speculative-algorithm DSPARK --speculative-dspark-block-size 5 --port "$PORT" $EXTRA_ARGS
```

```bash
# client.sh
for c in ${CONCURRENCY:-32 64 128}; do
  python3 -m sglang.benchmark.serving --port "$PORT" --dataset-name random \
    --random-input-len 8000 --random-output-len 1000 --random-range-ratio 1 --max-concurrency "$c" \
    --num-prompts $((4 * c)) --warmup-requests $((2 * c)) --flush-cache > "$OUT_DIR/bench_c$c.log" 2>&1
done
```

### 1.2 InferPilot's templates

InferPilot's templates are in `templates/harness/`. The server and the client stand in for the user's
only where the user takes them:

- `server.sh`: one sglang server after the model's recipe in the sglang cookbook (the framework
  checkout's `docs/cookbook/`, published at docs.sglang.io/cookbook) for the run's GPU and checkpoint.
  A feature or perf task runs it when the user answers `server_script: template`.
- `perf.sh`: sglang's serving benchmark on random prompts: one run per concurrency, with 4 prompts and
  2 warm-up requests per concurrent request after a flush of the prefix cache, as the client above. A
  feature or perf task runs it on the task's workload when the user answers `client_script: template`.
  A perf task's numbers on it count as on any client: the workload is the user's, and there is no
  source to reproduce. A feature's are reported, never a claim: it need not reproduce the source's
  numbers, and the same holds when a feature takes it as Spec's fallback client for the source's
  scenarios (`../roles/director.md`, PHASE=spec). A workload its parameters cannot express needs the
  user's own client.
- `accuracy.sh`: `scripts/eval_accuracy.sh` with the run's settings for every arm. It runs unless the
  task brings an `accuracy_script`. Its defaults are `eval_accuracy.sh`'s (thinking on, 102400 tokens
  per answer); only the task or the feature's source changes them (PARD-2: thinking off).

An issue or a ci task launches its server with the reporter's or the job's own command, as given, never
with a template's or the cookbook's flags.

Setup renders the templates a run uses into `RUN_DIR/scripts/` with `scripts/harness.py`, which takes
each parameter from, in this order:

1. **the task**: `harness`, `workload` (`isl`, `osl` and `conc`), `model_path` and the run's GPUs;
2. **the feature's source**: the value its own tests use, with the words it comes from (vLLM PR #49406
   serves PARD-2 with `--max-model-len 4096`, so `CONTEXT_LENGTH=4096`);
3. **the cookbook**, for the server: the recipe's flags (`--tp`, `--mem-fraction-static`, ...); its
   environment and its other flags (attention backends, parsers) are run as the recipe writes them;
4. **the template's default**.

`python3 scripts/harness.py show` lists every parameter with its default and meaning, and
`RUN_DIR/scripts/harness.json` the value each one took and where it came from, which the report lists.
On the template server, an accuracy run keeps the model's own context when the feature's cannot hold the
answers (`accuracy_server_env`); on any server, `eval_accuracy.sh` lowers `MAX_TOKENS` to what the
server's context leaves beside a question.

## 2. Telling the modes apart

| The request is about | Mode |
|---|---|
| bringing an upstream PR, design or paper to AMD | `feature` |
| a user's report: an issue URL, a crash, wrong output or an error on AMD | `issue` |
| a red CI job, a Failure Tracker cluster, a test failing on AMD lanes | `ci` |
| making a model faster on a workload: throughput, latency, TTFT/TPOT, idle GPU time | `perf` |
| what to work on next | `scan` |

## 3. Examples

```json
{"mode": "feature", "task": "https://github.com/sgl-project/sglang/pull/<n>",
 "goal": "the feature passes G0–G5 on DeepSeek-V4.1 at TP4",
 "model_path": "/mnt/models/DeepSeek-V4.1-Flash", "gpu_ids": "0,1,2,3",
 "server_script": "/workspace/scripts/server.sh"}
```

```json
{"mode": "issue", "task": "https://github.com/sgl-project/sglang/issues/<n>",
 "model_path": "/mnt/models/<the reporter's model>", "stop_after": "Reproduce"}
```

```json
{"mode": "ci", "task": "Failure Tracker #<n>, cluster <signature>"}
```

```json
{"mode": "perf", "task": "DeepSeek-V4.1 decode throughput on MI355X",
 "goal": "+5% output tok/s at equal memory, guards unchanged",
 "model_path": "/mnt/models/DeepSeek-V4.1-Flash", "workload": {"isl": 8192, "osl": 1024, "conc": 64},
 "server_script": "/workspace/scripts/server.sh", "client_script": "/workspace/scripts/client.sh"}
```

```json
{"mode": "scan", "sources": ["upstream", "issues", "ci"]}
```

## 4. When something is missing

The run starts nothing and returns:

```json
{"status": "needs_input", "stopped_at": "Intake",
 "task_def": {"mode": "perf", "task": "DeepSeek-V4.1 decode throughput on MI355X"},
 "questions": [{"field": "workload", "question": "Which fixed workload should every measurement use?",
                "why": "performance numbers count only on a workload fixed before any profile is read",
                "options": [], "default": "{\"isl\": 8192, \"osl\": 1024, \"conc\": 64}"}],
 "next": "answer the questions, then run inferpilot.js again with the same arguments plus answers={...}"}
```

Answer, then run again with the same arguments plus `answers` (`{"workload": {"isl": 8192, ...}}`), or
with the fields set directly. Fields set in the call win over answers, answers over what the intake
inferred. `rejected` carries a `reason` and what to do instead. A run that started records the
definition it used (`task_def`) and the intake's `assumptions` in its result and report.
