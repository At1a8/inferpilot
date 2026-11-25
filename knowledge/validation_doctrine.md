# Validation doctrine — what "correct" means for a framework feature

A feature passes when every gate below that its task runs holds **relative to baseline in the same
pattern**. Absolute pass rates mean little on a low-precision serving stack: the baseline itself has a
non-zero failure rate on long-context tests, so the question is always "does the feature change the
failure rate or the failure kind beyond the measured noise?".

Which of these gates a task needs, how its acceptance criteria are written and who confirms them:
[`acceptance.md`](acceptance.md).

## Gates

| Gate | What it shows | Typical suite |
|---|---|---|
| G0 feature off | the change is inert when disabled | baseline suites on the feature branch with the switch off |
| G1 unit / oracle | each new kernel or data structure does what it claims, and a port's own metric agrees with its source's | standalone tests vs a reference implementation, each able to fail: on the parent commit when the change alters behaviour, on a copy of the change broken in one way when it keeps behaviour (`reward_hacks.md` T1); for a port with a value to compare with, the feature's own metric (accept length for speculative decoding) against the source's, on the source's prompts and settings |
| G2 accuracy | quality stays within its budget | every set at most 1 point below the baseline, once per arm (`scripts/eval_accuracy.sh`, evalscope): gsm8k (1319) by default; gpqa_diamond (198) and simple_qa (4326, LLM judge) as well when the user's request or goal says the change may lose accuracy; a set that misses its budget is reproduced at most twice and judged on the median |
| G3 long context | the feature's own data path is exercised and correct | when the task asks (`long_context`): needle-in-a-haystack, single and multi-needle |
| G4 stability | no crash, hang or out-of-memory under sustained load; throughput and memory hold | when the task asks (`stability`): the task's performance test at high concurrency on long prompts, run back to back for more than 48 hours (`scripts/stability.sh`) |
| G5 performance | the claimed benefit exists on the task's workload; the guards the task names hold | see `perf_evaluation.md`; a held-out point, more guards and sweeps only when the task asks (`held_out`, `guards`, `sweep`) |

Notes per gate:

- **G1 for a port: the feature's own metric against the source's.** A feature may have a metric of its
  own that the hardware does not change (accept length for speculative decoding): for the same models,
  prompts, sampling and draft length, a correct port gives the source's value, near-tie flips aside.
  The test of the work package that completes the feature's path measures it on the source's own prompt
  set and settings, and passes within the tolerance the spec names, above or below. A pass holds for
  the commit it was measured on: a later package, an integrated kernel or a repair can change the path
  without its own test or G2 showing it, so every Verify round measures the metric again on the commit
  it verifies (`roles/verify_engineer.md`). Where the source reports no value, the reference
  implementation run on the same prompts gives it; with neither, the spec says so and the metric is
  reported, not judged. It belongs to G1 because nothing else shows that this unit is right: with exact
  verification the target verifies every token, so a broken draft path leaves the outputs correct, G2
  passes, and only the accept length falls (PARD-2 with its draft input shifted by one position: about
  2 tokens a step instead of about 7.5). On vLLM's case of four sets the port accepts 7.52 / 8.42 /
  8.56 / 3.97 tokens a step where vLLM reports 7.72 / 8.09 / 8.72 / 4.02, on other hardware and at
  another speed-up (`feature_skills/pard2`). G2's data sets are no place for the comparison: other
  prompts give another number (6.65 on PARD-2's gsm8k test, 5-shot), which G2 records as its engagement
  number and does not judge.
- **gsm8k first.** gsm8k (1319) runs before every other gate, and a blocking failure ends the round: an
  hour of long-context runs on a build that already fails accuracy is wasted. Other data sets run last,
  one after another, only when the goal or the matrix asks. No gate has a fixed time cap: every limit
  comes from this run's measurements of healthy runs, never from the arm that fails (an issue's or a
  failing test's baseline), and a hang is judged on progress (`roles/verify_engineer.md`). A report-only
  item that misses is reported, never investigated, and stops nothing.
- **G2 does not prove a long-context feature works.** Short prompts may never engage it (HiSparse V2
  only admits requests longer than its 5120-token reservation; GSM8K prompts are ~1k tokens). G2 is a
  regression check for the non-feature path; G3 must exercise the feature path. When G3 is not run,
  nothing has checked the outputs of that path, and the report shows G3 as `not run`.
- **G2 compares scores, not tokens.** Outputs need not match the baseline token for token: batch
  composition, kernel choice and speculative verification all flip near-ties, and chasing those flips
  costs runs without finding a defect. A token comparison is a diagnosis aid (the failure taxonomy
  below) or the subject of a determinism task, never the accuracy gate.
- **gsm8k by default; three sets when the user says the change may lose accuracy.** GSM8K alone
  may not show a loss: relaxed speculative verification (FLy) left gsm8k within ±0.4pp at every
  setting while simple_qa lost up to 3pp (`feature_skills/fly-dspark`). Whether a change may lose
  accuracy is the user's to say, in the request or `goal`, before anything runs (relaxed draft
  verification, quantization, sparse attention and KV compression are the usual cases): then gsm8k,
  gpqa_diamond and simple_qa run, and without it gsm8k alone; no role infers it. Each set runs once per arm,
  at most 1 point below the baseline, with the feature's engagement number per set (accept length). A
  set that misses is reproduced at most twice, three results in all, and judged on their median, every
  run reported: one simple_qa run is good to about ±0.5pp, and a 0.6pp loss took 5 runs against 8
  baselines to separate from the noise (two runs had called it noise).
- **Harness:** evalscope for every set, through `scripts/eval_accuracy.sh`, which samples every run
  afresh (never evalscope's cache), flushes the prefix cache before each set and reads the accept length
  after it; both arms use the same settings.
- **G3 and G4 run only when the task asks.** Neither is part of every task: G3's repeated long-context
  runs and G4's two days are spent only when the task sets `long_context` (G3) or `stability` (G4), two
  fields of the task definition that are off by default (`templates/task_definition.md`). The intake
  sets one when the user's request asks for that gate; no role infers it from the kind of change, and
  `goal` does not turn a gate on. A gate the task did not ask for is reported `not run: not asked`.
- **G4 is the task's performance test, run back to back for more than 48 hours.** One server, not
  restarted, under the run's performance client (the task's `client_script`, or InferPilot's `perf.sh` on
  the task's workload), one pass after another, at the highest concurrency of the task's workload:
  `scripts/stability.sh` runs it and writes one line per pass, with the client's result and the memory
  in use. It is one run, not repeated and not taken to other concurrencies, after everything else in
  its round. `perf.sh` keeps its concurrency full within a pass; the server is idle only while one pass
  ends and the next starts, and the lines show how much of the run was under load. The workload is the
  task's to bring: long prompts at high concurrency, on which the feature engages. Where it does not
  engage, the report says that G4 showed the server's stability, not the feature path's.
- **G4 passes on what its run shows.** The server neither crashes, hangs nor runs out of memory, no
  request fails, and nothing drifts: not the client's throughput, nor the memory in use on the run's
  GPUs or on the host. Each hour of the run has one value of throughput and one of memory: the median of
  the passes that ended in it, the first pass left out as warm-up. The median of the last 6 hours'
  values is no lower in throughput, and no higher in memory, than the median of the first 6 hours' by
  more than the range of those first 6 values. The spread of the whole run is no measure of a drift: the
  drift is inside it. Throughput against the baseline is G5's.
- **A failed G4 gets its baseline control in the same round, and stays failed.** The verify engineer
  runs the same test on the baseline, for its full length unless the baseline fails sooner, and the
  round asks for the time of both runs before it starts: no later call has two days
  (`roles/verify_engineer.md`). The control says whose the failure is, never that there was none: a
  server that did not hold the load is not reported as stable. The failure is the baseline's too only
  when the control shows the same failure at a comparable time: the same signature for a crash, a hang
  or a failed request, a drift at least as large for a drift, and not much later than the feature arm
  showed it. Hour 31 against the baseline's hour 27 is comparable; hour 1 against hour 47 is a change in
  rate, which is the change's. A control that ended another way first, or was interrupted, says nothing
  about this failure. The investigator makes the call: with `baseline_bug` it records a known issue, and
  G4 is reported as failed with that issue, without blocking the change; a failure that is the change's
  is investigated; where the two runs do not allow the call, the verdict is `inconclusive`, and the run
  stops for a human.
- **G4 runs once in a run, and its result names its commit.** A later Verify round does not run it
  again, on that commit or on a later one: after a review's change it would cost two more days, which
  the task did not ask for. The round reports the result with the commit it was measured on, and when
  the commit under test is another, says that this commit has not been through G4. Only a G4 whose
  failure was the change's is run again, on the fix.
- **G3/G4 must be sized so the feature engages.** For a memory-tier feature that means total context
  well above the device pool (force eviction/demotion), and concurrency high enough that several engaged
  requests decode in the same batch. Log the engagement (e.g. demotion counters) and attach it.
- **G0 for a config-construction change: compare the built config, not only a server smoke.** Diff
  `get_config(...).to_dict()` from the fix and the parent tree for every config shape the changed code
  branches on (with and without the field it touches), not only the task's checkpoint. For a YaRN config
  fix on sglang #41299 the task's checkpoint built the same config with and without the fix, while a text
  config without `rope_parameters`, built by the same code, had `{'rope_theta': 10000.0, ...}` filled in
  on the parent and `{}` with the fix, so its effective rope_theta went from a wrongly filled-in 10000 to
  the text config's own 5e6: a correction, but a behaviour change G0 should have reported, and only the
  review found it. A short greedy smoke is a weak G0 here: one of three default-rope runs with identical
  built configs gave a different 16-token text.
- **Run G3 at more than one concurrency.** Concurrency 1 often passes when concurrency 8–32 fails;
  the batch-level interactions are where bugs live.

## Failure taxonomy — classify before counting

Count each class separately; a single "correct %" hides the signal.

| Class | Looks like | Usual meaning |
|---|---|---|
| catastrophic collapse | every request in a decode batch turns into gibberish at the same step; per-token logprobs drop from ~0 to around −2 and stay there | corrupted state (KV, cache metadata, buffers) — or a pre-existing baseline bug (see `known_issues.md`) |
| mild deviation | one wrong digit in a copied key, a missed end-of-answer EOS, a near-tie token flip; the rest of the output is fine | numeric noise from low-precision paths and batch-dependent numerics |
| empty output | the first generated token is EOS | seen on baseline too; count it, do not chase it unless the rate moves |
| crash / OOM / hang | server error, `Prefill out of memory`, watchdog timeout | capacity accounting, synchronization, or a kernel fault |

How to tell them apart quickly:

- Record **per-token logprobs** (`return_logprob`) for every long-context request. Collapse is a sudden,
  persistent drop shared by the whole batch; mild deviation is one low-logprob token followed by a
  recovery.
- To locate a collapse, compare output token ids with a **reference output per prompt** (fixed seeds,
  greedy). The first divergent index, mapped onto the per-step trace, tells you which forward step went
  wrong and which requests were in that batch. This is a diagnosis, not a gate: a divergence alone
  fails nothing.
- The task metric can lie: a request that printed every key and then failed to stop (missed EOS, ran to
  `max_new_tokens`) is "correct" by key recall. Track output lengths too.

## Noise characterization

- Repeat every configuration **≥4 times** (≥6 when hunting an intermittent failure) and report the
  distribution, e.g. `27/32, 31/32, 32/32, 25/32`, not an average. G4 is the exception: one run of more
  than 48 hours.
- Use the same prompts in every configuration (fixed seed) and flush the prefix cache between runs.
- A configuration "fixes" an intermittent failure only when its failing-run rate is clearly below the
  baseline rate; one clean run after three failing runs is not evidence.
- Heavy verification (device-wide syncs after every kernel, CPU waits) changes timing and can hide a
  bug. A clean run with heavy checks on proves nothing about the run without them.

## Same-pattern baseline control (the rule that would have saved the most time)

Before instrumenting any failure, reproduce **the same scheduling pattern on baseline**:

1. Describe the pattern the failing run had: how many requests were admitted at once, whether new
   requests joined while others were decoding, how full the pool was, which concurrency.
2. Recreate it on the baseline build — limit concurrency with `--max-running-requests N`, shrink the
   pool with `--max-total-tokens`, send the same prompts in the same waves.
3. Compare the failure taxonomy over repeated runs. Only a difference beyond the noise is attributable
   to the feature; a failure present on baseline goes to `known_issues.md` and the feature is judged on
   the remainder. G4 keeps its verdict even so: a failure the baseline shares does not block the change,
   and G4 is still reported as failed (*Notes per gate*).

Also run the **original upstream code** (an untouched checkout of the framework) when the branch carries
other people's commits, so a failure can be pinned to "framework", "dependency PR" or "this change".

## Checking the checkers

A debugging check that reports a false positive costs as much as a missed bug.

- Key per-request state by request id, not by slot index: slots are reused, and a new request whose
  first decode length happens to equal the previous occupant's last length looks like "state changed".
- Validate a new check on a known-good run before trusting it on a failing one.
- Prefer checks that count into a device tensor and are read at a coarse interval over checks that sync
  the device every step.

## Suites used on the reference stack

The `hisparse-v2` skill records concrete settings and expected numbers; in short:

- **G2:** gsm8k, all 1319 questions, once, with `scripts/eval_accuracy.sh` (its default set).
- **G3:** NIAH single-needle and 8-needle prompts of ~30–37k tokens at concurrency 1, 8 and 16.
- **G4:** not yet run there under this rule (`scripts/stability.sh` on the run's performance client for
  more than 48 hours). What the skill records as G4 is the earlier rule's: short runs, at least four, of
  32 prompts × ~37k tokens with 8 needles at concurrency 32, with the same-pattern baseline control.
