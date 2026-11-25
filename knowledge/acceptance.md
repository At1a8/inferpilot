# Acceptance — what a task must show, and to whom, before it counts as done

The gates in [`validation_doctrine.md`](validation_doctrine.md) say what "correct" means for a change,
and [`perf_evaluation.md`](perf_evaluation.md) says how a gain is proven. This document says which of
them a task needs, how its acceptance criteria are written, who confirms them and how the result is
reported. The criteria are fixed before the work starts (§5).

Status: this is the definition. The orchestration does not build or check the acceptance matrix yet;
until it does, the director and the reviewer apply this document by hand.

## 1. Dimensions

| Dimension | What it shows | Typical checks |
|---|---|---|
| Correctness | the change does what it claims, and nothing when it is off | G0 feature off, G1 unit / oracle, startup rejection of unsupported combinations; parity with a reference implementation for a new model, kernel or refactor (§1.1) |
| Accuracy | model outputs keep their quality, or lose no more than a declared budget | §1.1 |
| Performance | the claimed gain exists, and nothing else regresses | gain items and guard items, §1.2 |
| Stability | no crash, hang or leak, under pressure and over time | G4, when the task asks (`stability`): the task's performance test at high concurrency on long prompts, run back to back for more than 48 hours, with no crash, hang or failed request and with throughput and memory flat (`validation_doctrine.md`); cancelled requests, oversized inputs, out-of-memory fallback; a restart; injected faults for a feature that spans instances, a network or storage (a timeout, a peer down, a backend unavailable): requests fail cleanly and nothing hangs |
| Compatibility | other platforms, features and configurations behave as before | the CUDA path untouched by a HIP change (`pr_playbook.md`); the combinations in `composability.md`; the platform matrix the task asks for |
| Engineering | upstream can take it | lint, tests registered in CI, docs, the PR description (`pr_playbook.md`), upstream CI green, a maintainer's approval |
| Requirement | the requester's own definition of done | §1.3 |

Two rules hold across the dimensions:

- **Engagement.** Every item that measures a feature also shows that the feature engaged: its counters,
  its log lines, its accept length or hit rate. Without that, a gate may have measured the baseline
  (`reward_hacks.md` C2).
- **Sweeps** (`sweep`, off by default). Measurement covers the points of the task's workload; with
  `sweep` on, more, since where an effect can turn around with length, concurrency or batch size, one
  point is not evidence: speculative decoding is measured over concurrency, a long-context feature over
  length up to the longest supported, a kernel over the batch sizes and shapes the models use.

### 1.1 Accuracy

- **The rule:** every set at most 1 point below the baseline, one run per arm on the same server
  settings (`validation_doctrine.md`, G2). Outputs need not match the baseline token for token.
- **The sets:** gsm8k, all 1319 questions (`scripts/eval_accuracy.sh`, its default set), for every
  change. More only when the user's request or `goal` asks for them, before anything runs: a change the
  user says may lose accuracy (relaxed draft verification such as FLy, quantization, sparse attention,
  KV compression) adds gpqa_diamond (198) and simple_qa (4326, LLM-judged), since GSM8K alone can hide
  the loss (`eval_accuracy.sh gsm8k gpqa_diamond simple_qa`); other sets (a long-context set for KV
  compression), more runs or another budget by name; a determinism task, token equality (§1.4). No role
  decides it on its own: the Intake keeps what the user said in `goal`, and every role reads it there.
- **Harness:** evalscope for every set, through `scripts/eval_accuracy.sh`, every run sampled afresh
  (never evalscope's cache). It runs through InferPilot's `accuracy.sh`, with the same settings for
  every arm: the task's, else those the feature's source tests with (thinking off for PARD-2), else
  `eval_accuracy.sh`'s own (`../templates/task_definition.md` §1.2). A task that brings an
  `accuracy_script` is measured with it instead, as given, on the sets it measures. A set where a
  question got no answer (a request the server rejected, or one that failed) has no score: the arms
  may miss different questions.
- **Capability suites**, for what the change touches: long context (needle-in-a-haystack, G3, when the
  task asks with `long_context`), reasoning (GPQA, AIME), tool calls, multimodal input.
- **Parity with a reference**, for a new model, a new kernel or a refactor: logprobs or the KL
  divergence against the reference implementation (Hugging Face, or the model's official one) on a fixed
  prompt set, and the official benchmark scores matched. sglang's own skills do this
  (`kl-consistency-test`, `processor-model-parity`; `frameworks/sglang/skills.md`).
- **Sampling**, for a change to a sampler or to speculative verification that claims to keep the target
  distribution: greedy agreement does not show it. Test the output distribution at a temperature above
  0 against the target's (§4).

### 1.2 Performance

- **Metrics:** total and output throughput; TTFT and TPOT, mean and P99; throughput within an SLA
  (requests per second that meet their TTFT and TPOT targets); memory and capacity (KV tokens, the
  concurrency the scheduler reached); startup time when the change touches loading or compilation.
- **Gain items:** on the task's own workload (its `client_script`, run unchanged) at an equal budget, the change wins beyond the noise
  band, and, with `held_out` on, still wins at the held-out operating point (`perf_evaluation.md` §1, §3).
- **Guard items**, the ones the task's `goal` names, and more of InferPilot's own only with `guards` on:
  workloads where the change should be neutral, or where it costs. The change stays
  inside the noise band (a decode-side change at 100k in / 1k out, where prefill sets the total), or its
  cost is measured and stated.
- **The resource held equal** is named in every item: HBM for a memory feature (`perf_evaluation.md`
  §1), the GPU count for distributed serving (8 GPUs serving together against 4 for prefill and 4 for
  decode), the memory fraction, or the cost.
- **The load model:** a fixed concurrency measures capacity. A claim about latency or an SLA is measured
  with a request arrival rate swept up to saturation, reporting the throughput of the requests that meet
  the SLA.

### 1.3 Requirement

A task driven by a requirement (from a customer or another team) carries the requester's definition of
done as acceptance items: the model and weights, the precision, the deployment (TP / EP / PD, GPUs),
the workload (an input / output length distribution or a trace, the concurrency), the SLA targets, the
accuracy on the requester's data set, and the delivery date. The **requirement owner**, the person who
represents the requester, confirms these items before the work starts and signs off at the end (§6).

### 1.4 Feature classes

Some kinds of change need items that the dimensions alone do not suggest. A feature takes the extra
items of every class it belongs to.

| Class | Examples | Extra items |
|---|---|---|
| KV movement and memory tiers | HiCache, KV offload, prefix cache, lossless KV compression | KV read back through the reload path byte-identical with what was written (or with a recompute); hit counters per tier; a reuse workload whose working set exceeds HBM; equal HBM; a full pool evicting under load; an unavailable storage backend falling back to recompute |
| Distributed and multi-instance serving | PD disaggregation, multi-node TP / PP / EP, DP attention, routers, elastic scaling | accuracy (G2) within 1 point of serving on one instance, also with different TP sizes on the two sides; equal GPU count; an arrival-rate sweep with the SLA throughput; the transfer's share of TTFT; scaling efficiency over node counts; injected faults (a timeout, a peer down, a cancellation mid-transfer); the topology and transfer-backend matrix |
| Model support | a new architecture, a multimodal model | parity with the reference implementation; the official benchmark scores; the tokenizer, chat template, tool-call and reasoning parsers; every parallelism and checkpoint precision the model card lists |
| Sampling and speculative decoding | samplers, draft verification, draft models | the accept length reported, and for a port whose spec gives it a tolerance, within it of the source's on the source's prompts (G1, `validation_doctrine.md`); a distribution test at a temperature above 0 when the goal claims the target distribution is kept exactly (§4); a concurrency sweep, since the gain can turn into a loss at high concurrency; the greedy and the sampled paths both |
| Determinism | deterministic inference, batch-invariant kernels | bitwise-identical outputs over repeated runs, batch compositions, concurrency and, where claimed, TP sizes; the cost in throughput |
| API and protocol | OpenAI-compatible API, structured output, function calling, streaming, logprobs, multi-LoRA | contract tests of the API; structured outputs valid against their schema; streaming equal to non-streaming; the official client SDK working; the overhead of constrained decoding; adapters switched under load |
| Scheduling | priority, preemption, chunked-prefill policy, overlap scheduling | P99 under mixed and bursty traffic; fairness between long and short requests; no request starving; accuracy (G2) within 1 point with preemption or retraction forced |
| Online updates and operations | weight updates for RL, graceful shutdown, metrics | the weights after an update equal to a fresh load of the same weights, tensor by tensor; no request lost while updating or draining; metrics correct and cheap |
| Platform bring-up | a new GPU architecture | the pass rate over the model × feature matrix; performance against the previous generation; the known issues listed |
| Kernels | attention, MoE, GEMM, communication | the shapes the models use, small batches, unaligned sizes and padding included; tolerances per data type; an op-level gain shown end to end, or why it cannot show (`reward_hacks.md` P11) |
| Long context | context parallelism, sparse attention, long-context KV layouts | a length sweep up to the longest supported; multi-task long-context suites (RULER-style), not one needle test alone; memory over length |

## 2. Default profiles

Which dimensions each kind of task must show. "When" names the condition under which an item becomes
required; the task may add items, never drop a required one. On top of its profile, a change takes the
extra items of its feature classes (§1.4).

| Dimension | Upstream feature | Issue fix | CI fix | Performance | Requirement |
|---|---|---|---|---|---|
| Correctness | required | required; the reproduction becomes a regression test | required; the failing test passes on every lane it failed on | required | required |
| Accuracy | required: gsm8k, and gpqa_diamond and simple_qa when the user says the change may lose accuracy; each at most 1 point below the baseline (§1.1) | when the issue is an accuracy loss | no regression | required, as for a feature (§1.1) | on the requester's data set |
| Performance | when the feature claims a gain | when the issue is a performance problem: the reported number comes back | — | required: the gain; a guard and a held-out point when the task asks (`guards`, `held_out`) | the requester's SLA |
| Stability | when the task asks (`stability`, G4) | when the task asks (`stability`, G4); a reported crash or hang is judged by its reproduction | a flaky test passes repeatedly (§4) | when the task asks (`stability`, G4) | when the requester asks (`stability`, G4) |
| Compatibility | required | required | required | required | the requester's platforms |
| Engineering | required | required | required | required | when the change goes upstream |
| Requirement | — | when a requester reported it: the reporter confirms the fix | — | when a requester asked for it | required; the requirement owner signs off |

A scan changes no code; its daily report has its own checks (`scripts/daily_report.py check`).

**Strictness.** Every item is either blocking or report-only, decided when the matrix is fixed. A P0
hotfix may ship on the minimal set: the reproduction passes, G0 holds, accuracy and the guard workloads
are not worse. The rest of its profile becomes follow-up items, listed in the report.

## 3. An acceptance item

Each item is one row of the acceptance matrix (`../templates/acceptance.md`):

| Field | Content |
|---|---|
| ID | `COR-`, `ACC-`, `PRF-`, `STB-`, `CMP-`, `ENG-`, `REQ-` plus a number |
| Dimension and metric | e.g. accuracy, simple_qa score |
| Workload or data set | lengths, concurrency and budget, or the data set with its harness, sampling and seed |
| Baseline | same image, commit family and launch line, only the switch differs (`validation_doctrine.md`, same-pattern baseline) |
| Rule | an absolute threshold, a change relative to the baseline, a non-inferiority margin, or a gain beyond the noise band |
| Runs and statistics | runs per arm, how lanes are paired, how the noise band is set (§4) |
| Blocking | yes or report-only |
| Confirmed by | InferPilot (automatic), a human reviewer, or the requirement owner |
| Result and evidence | filled at the end: the measured value, the verdict, the path of the logs and tables |

Three items for FLy on DSPARK (`feature_skills/fly-dspark`), whose task asked for simple_qa too, written in
this form after the fact:

| ID | Item | Rule | Measured |
|---|---|---|---|
| ACC-2 | simple_qa, all 4326 questions, LLM-judged, at the defaults | at most 1 point below the mean of at least 3 baseline runs; blocking | −0.8 points (5 runs against 14 baseline runs): pass |
| PRF-1 | 8k in / 1k out at concurrency 32 / 64 / 128, two lanes swapped every round, 3 rounds | total throughput at least +1.5%, beyond the noise band; blocking | +2.3%: pass |
| PRF-2 | 100k in / 1k out, guard | total throughput and mean TPOT within ±1%; blocking | inside the band in round 1; the swapped round is still missing |

## 4. Statistics

- **Noise band:** set from repeated baseline runs (their range, or the standard error of the mean) and
  stated next to every result.
- **Runs:** each accuracy set once per arm; performance one A/B round, one timed run per arm after a
  warm-up (`perf_evaluation.md` §3). A result that misses its expectation (accuracy below its budget,
  no performance gain) is reproduced at most twice, so three results at most, and the verdict is their
  median. Failure rates at least 4 per configuration (`validation_doctrine.md`); G4 one run of more
  than 48 hours; a flaky test passes in every one of at least 10 runs.
- **Distribution tests** (a goal that claims a sampler or a verification change keeps the target
  distribution exactly): per tested prompt, compare the sampled tokens with the target distribution
  using a stated test, sample size and significance level, e.g. a chi-square test on at least 10,000
  samples at p = 0.01.
- **Determinism:** identical outputs over at least 3 runs and at least 3 batch compositions; one
  differing token fails the item.
- **Two lanes at once** (two servers on one node): swap the arms between the lanes every round, and
  report the result both pooled and paired by round.
- **Verdicts:** a gain counts when the median of the rounds shows it (above zero, or at least the size
  the goal claims), and with `held_out` on when it also holds at the held-out point; a guard holds when
  the change stays inside the noise band. Every run is reported, with its range, never only the
  best (`reward_hacks.md` P9).

## 5. Fixed before the work

The acceptance matrix is written and confirmed before implementation: in the Spec phase for a feature,
at Setup together with the workload for a performance task, and with the requirement owner's
confirmation for a requirement. Once results are seen it is not changed. A dropped item, a loosened
rule or a blocking item made report-only is `reward_hacks.md` C1, and a changed workload is P1. An item
that has to change restarts the evaluation, and the report says why.

## 6. Who confirms

- InferPilot judges the automatic items and records their evidence. It never declares acceptance on a
  human's behalf.
- An upstream PR is accepted by the maintainers' review and the upstream CI.
- A requirement is accepted by the requirement owner, who signs the acceptance report; InferPilot
  supplies the evidence.
- A failed blocking item can be waived only by the person who confirms it, with the reason written in
  the report.

## 7. Reporting

The final report carries the acceptance matrix with a result for every item: pass, fail, waived (by
whom, and why) or not run (and why), each linked to its evidence. A requirement-driven task also gets
an acceptance report of its own, written for the requirement owner: the requester's items first, then
everything else.
