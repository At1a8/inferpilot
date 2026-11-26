# Performance evaluation — proving the benefit without flattering it

Start only after the correctness gates pass. The question is "what does the feature buy, under which
conditions, at what cost", answered with an A/B that a reviewer can reproduce.

**Performance is measured alone.** Throughput, TTFT, TPOT, latency and every other speed number are
measured only while no other job uses any GPU of the machine: work on the other GPUs shares the CPUs, the
PCIe and xGMI links, memory bandwidth and the power budget, and moves the numbers. So a run holds the whole
machine (`scripts/run_lock.py`), and before the servers of each measurement start, this run's earlier
servers stopped, `python3 <inferpilot_dir>/scripts/gpu_status.py --check all` must find every GPU idle,
under 5% of its VRAM and of its busy time (`env_hygiene.md`). Otherwise the measurement is not run,
reported at once with who uses which GPU; a number measured next to another job is no evidence either way.
Accuracy and accept length do not depend on speed: a Verify round goes on next to another job, and the
user is warned of it (`../roles/verify_engineer.md`, PHASE=gpus).

## 1. Compare at an equal budget

A feature usually spends a resource to save another. Hold the scarce resource equal:

- **Memory features** (cache tiers, sparse attention buffers): compare at equal HBM spent on KV state.
  If the feature adds bytes per token, shrink its device pool or grow the baseline's pool by the same
  ratio. Example: HiSparse V2 on DeepSeek-V4.1 raises the per-token KV budget from 1630 B to 1970 B
  (host ratio 2), so a V2 run with a 131,072-token device pool is compared against a baseline/HiCache
  run with `131072 × 1970 / 1630 ≈ 158,412` tokens.
- State the budget in the report (`--max-total-tokens`, host ratio, memory fraction).

## 2. Choose workloads on purpose

The benchmark is the task's: its `client_script` and `workload` (`../templates/task_definition.md` §1.1),
run unchanged, against servers started from its `server_script`. InferPilot never edits or retunes it.
A user without a server script may take InferPilot's template, after the model's sglang cookbook recipe
and the settings the feature's source tests with (PARD-2: vLLM's `--max-model-len 4096` and no prefix
caching; `../templates/task_definition.md` §1.2). A user without a client may take InferPilot's,
sglang's serving benchmark on the task's workload: for a perf task, it is then the benchmark, and its
numbers count. A port is measured on its source's scenarios: when the task's client does not run them,
the run stops at Spec and asks the user, and InferPilot's client for them (`perf.sh` at the source's
lengths, concurrency and sampling) is a fallback the user may take, whose numbers never stand for the
source's gain (`../roles/director.md`, PHASE=spec); nor do those of InferPilot's client when a feature
takes it for its own workload.
A feature is measured whether it claims a gain or not (`validation_doctrine.md`, *G5 for a feature*):
with a claim, the gain shows beyond the noise band; without one, or on InferPilot's client, the feature
is no slower than the baseline beyond the noise band. So a feature task names its client, as a perf
task does.
The fix of an issue or of a CI failure is measured the same way when it changes code the server runs
(`validation_doctrine.md`, *G5 for a fix*): on the task's client, no slower than the base beyond the
noise band, or than the last commit that served the configuration where the base cannot. A
performance problem is measured whatever its fix changes, where it showed, and its number comes back.
So an issue or a ci task that can reach a fix names its client too.
The table below is for the workloads a task lists beside it, and for choosing one with the user when a
task has none. Guard workloads are the ones the task's `goal` names; InferPilot adds its own from the
table only when the task sets `guards`. When there is none, the report says that none was measured.

| Workload | Why |
|---|---|
| where the feature should help | e.g. long context, many concurrent requests, decode-heavy, capacity-bound |
| where it should be neutral | short prompts that never engage it — must not regress |
| where it costs | e.g. prefill-heavy, low concurrency — quantify the overhead |

Report input and output token totals; a prefill-dominated run (e.g. 960k input vs 16k output tokens)
hides a decode-side benefit. **Run at least one decode-heavy workload**: the HiSparse V2 admission bug
(server crash with `Prefill out of memory`) only appeared once requests generated 512 tokens; 30-token
outputs never triggered it.

## 3. Procedure

- **Warm up a fresh container first.** Its first launch compiles kernels and runs slower, which once
  spread one baseline over 9.6% at c32: one discarded launch and client run come before the first
  measured round.
- **Fix the benchmark before reading any profile.** The workload (cases, lengths, concurrency, budget)
  is set before the first trace and never changed after results are seen: no case dropped because the
  change loses there, no shape added because it wins there. A workload that must change restarts the
  evaluation and says why.
- **Check the path before trusting a number.** Compare the intended and the effective configuration of
  every arm — attention, MoE and GEMM backends, graph capture, torch.compile, quantization, and any
  fallback or "disabling" warning in the log. A run that silently left the intended path measures a
  different system; it is not evidence for or against the change.
- **With `held_out` on, hold out an operating point before claiming a win.** It is off by default: the
  claim is then judged on the task's workload alone, and the report says that no held-out point was
  measured. When it is on, a gain counts only if it also holds where nobody tuned: the same workload
  with another seed (or another slice of a trace), at half the concurrency (at least 1), through
  `CONCURRENCY` when the client reads it; otherwise the report says no held-out point was measured. It
  is fixed with the workload at Setup and measured like the claim; the
  change must still win beyond the noise band there, if not by as much. A gain that vanishes is fitted
  to the benchmark (`reward_hacks.md` P7), not evidence.
- **A feature runs at its source's configuration; tuning is opt-in (`tune`).** By default its knobs
  take the values the target PR or design uses (vLLM's FLy: window 2, threshold 0.3, top-k 3), and the
  task is to make the feature work there: accuracy within its budget, and a gain in throughput and in
  the feature's own metric (accept length for speculative decoding). With `tune` on, choose the
  defaults on throughput, at one point: run each candidate once against the baseline at one
  concurrency of the task's workload (the lowest, unless the task names one), with the baseline on the
  other lane when there are two, and rank by total tok/s. No accuracy set is added to rank them: the
  task's accuracy check (`validation_doctrine.md` G2) runs on the chosen candidate, then on the
  next-fastest if it fails, before the defaults are frozen. Tuning runs are not evidence for the claim,
  and the held-out point stays out of them.
- Check the measurement entries of [`reward_hacks.md`](reward_hacks.md) (P) on both arms before
  reporting any number.
- Fresh server per configuration; same container, commit family, GPU set and launch flags apart from
  the feature switch and the budget.
- Flush the prefix cache between runs; use random-token or seed-fixed distinct prompts so prefix hits do
  not differ between arms.
- One warm-up, then one A/B round: one timed run per arm, back to back on the same GPUs. When the round
  shows no gain, or a slowdown beyond the noise band where no gain is claimed, reproduce it at most
  twice and judge on the median of the rounds, reporting every round. Interleave arms when the box is
  shared.
- Metrics: elapsed time, output tok/s, total tok/s, p50 and max request latency, the concurrency the
  scheduler actually reached (`#running-req` in decode logs), and the feature's own counters (e.g.
  demotions, rows swapped in).
- Check the server log after every run for out-of-memory, retraction and watchdog messages; a run that
  crashed or retracted is not a performance number.
- Check it for serving-time compilation too (sglang: `... took <n> s to compile after serving
  started`): a kernel compiled inside a timed run stalls the engine. One such 1.45 s stall in the first
  baseline of a sweep made every later arm look ~4% faster at that point; discard or repeat the run,
  and never put all the baselines in the first round (`feature_skills/fly-dspark`).
- Two arms on one node: run them on disjoint GPU sets at the same time, swap them between the sets every
  round, and repeat ≥3 rounds. On DeepSeek-V4.1-Flash 8k/1k a single run is noisy (two runs of the same
  baseline differed by 7.9% at concurrency 32); three runs per arm bring a campaign's mean to about
  ±0.5%, yet two such campaigns a day apart measured +2.8% and +1.4% for the same setting. A gain of a
  few percent needs a repeat campaign and is quoted as a range
  (`feature_skills/fly-dspark/scripts/lockstep_ab.sh`).

## 4. Admission and capacity checks

A feature that lets more requests run concurrently must also keep the scheduler from admitting more
than it can hold:

- Test a load that drives the pool to its limit with long outputs and high concurrency.
- Make sure the admission rule accounts for everything that must stay on device: per-request scratch,
  each request's own tail and decode growth, and any request whose prefix is pinned (e.g. locked during
  chunked prefill).
- Requests that bypass the admission path (e.g. too long to reserve for) must not bypass the accounting.

## 5. Reference numbers (HiSparse V2, DeepSeek-V4.1-Flash, TP4, gfx950)

32 requests × 30,000 random input tokens, concurrency 32, `ignore_eos`:

| Output per request | HiCache only (158k device tokens) | HiSparse V2 (131k device + 262k host) |
|---|---|---|
| 512 | 37.9 s, 432 out tok/s, ≤5 running | 30.3 s, 540 out tok/s, ≤10 running (+25%) |
| 2048 | 110.9 s, 591 out tok/s, p50 69.2 s | 77.3 s, 848 out tok/s, p50 43.8 s (+43%) |

The gain grows with the decode share, as expected for a feature whose benefit is concurrent decode
capacity.
