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
| G3 long context | the feature's own data path is exercised and correct | when the task asks (`long_context`): the task's own long-context test (`long_context_script`), needle-in-a-haystack, single and multi-needle |
| G4 stability | no crash, hang or out-of-memory under sustained load; throughput and memory hold | when the task asks (`stability`): the task's performance test at high concurrency on long prompts, run back to back for more than 48 hours (`scripts/stability.sh`) |
| G5 performance | the claimed benefit exists on the task's workload, and a change that claims none is no slower there; the guards the task names hold | see `perf_evaluation.md`; a held-out point, more guards and sweeps only when the task asks (`held_out`, `guards`, `sweep`) |

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
- **G2 for a fix.** The fix of an issue or of a CI failure is measured as a feature is, whatever the
  report was about, when it changes anything but tests and documentation: gsm8k (1319) on the fix and
  on the base, each once, with the run's launch script and model, the fix at most 1 point below the
  base. Every task that ends in a pull request brings its accuracy with it: the framework's PR
  template asks it of a change to outputs, and a fix on the serving path can be one without meaning
  to. Nobody judges whether the server runs the file a fix changes: that judgement can be wrong, and
  measuring is the safe side (*What a fix changes decides its gates*). A fix to tests or
  documentation alone has no G2: `not run: only tests or documentation changed`. Nor has the fix for
  a model that answers no text questions (a diffusion,
  speech or embedding model), which gsm8k cannot be put to: `not run: the model answers no text
  questions`; what its outputs are worth is the reproduction's or the failing test's to show. The
  reproduction or the failing test runs first, before gsm8k and before any server G2 needs, the one
  exception to *gsm8k first*: a fix that does not fix has no accuracy worth measuring. Where the base
  cannot serve the configuration because that is the failure being fixed (a crash, a server that does
  not start), it is never launched for G2, and the baseline is the last commit that served the
  configuration, measured the same way: the culprit's parent for a CI fix (`ci_triage.md` §3), the
  commit or release the report says worked for an issue. The gate and the pull request name that
  commit, and whoever measures again before the push measures against it. Where the run knows no such
  commit there is nothing to judge the fix's score against: it is reported, in the gate and in the
  pull request, and both say that it has no baseline. An issue that reports an accuracy loss is
  judged by its reproduction (`issue_repro`: the reported score comes back); G2 runs all the same, and
  its base arm shows the loss.
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
- **G3 is the task's own long-context test.** The user who asks for G3 says what it runs: the task's
  `long_context_script`, needle-in-a-haystack prompts, single and multi-needle, long enough for the
  feature's path (next notes). It runs unchanged on the baseline and on the change, in the same
  pattern. A task with `long_context` on that names none is asked for it, in any mode: InferPilot has
  no template for a long-context test, and no role writes one in its place or changes the task's. Its
  prompts, their lengths, the needles and the scoring are the user's, as a client's workload is.
  Where the feature does not engage on them, the report says that G3 showed the server's
  long-context path, not the feature's.
- **G4 is the task's performance test, run back to back for more than 48 hours.** One server, not
  restarted, under the run's performance client (the task's `client_script`, or InferPilot's `perf.sh` on
  the task's workload), one pass after another, at the highest concurrency of the task's workload:
  `scripts/stability.sh` runs it and writes one line per pass, with the client's result and the memory
  in use. It is one run, not repeated and not taken to other concurrencies, after everything else in
  its round. `perf.sh` keeps its concurrency full within a pass; the server is idle only while one pass
  ends and the next starts, and the lines show how much of the run was under load. The server, the
  client and the workload are the task's to bring: the user asked for G4, so a task with `stability` on
  that lacks its `server_script` or its `client_script` is asked for it, in any mode, and no template is
  taken for it. InferPilot's client is G4's load only where the task names it and gives the workload
  it runs, never on lengths and a concurrency of its own. The load that shows most is long prompts at
  high concurrency, on which the feature engages. Where it does not engage, the report says that G4
  showed the server's stability, not the feature path's.
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
- **G0 needs a switch.** It runs where the change can be turned off: a feature, and a fix or a
  performance change behind a feature's switch. Such a fix has a baseline that runs, the base commit
  with the switch off, since its failure needs the switch on. A change without a switch, which nothing
  turns off (most issue and CI fixes, a performance change that is always on), has no G0. Where it
  takes effect the base commit fails or is slower, which `issue_repro`, `failing_test` and G5 judge.
  What it must leave alone is shown another way (next note): by the existing tests of the modules it
  touches, per work package (`roles/test_engineer.md`) and on the final commit
  (`roles/pr_engineer.md`), and by the review, which finds that it takes effect only in the case it is
  for (`roles/reviewer.md`).
- **Without a switch, a change is judged against the tree without it, never as an absolute pass.**
  That tree is a work package's parent, and the branch's base for the final commit. An existing test
  of a module the change touches that fails with the change is run again without it, with the same
  command and environment, and is unchanged only when it fails there the same way: the same test, the
  same error. One that passes there is the change's, and so is one that fails there another way (an
  assertion without the change, a crash with it). One the tree without the change cannot run at all
  because of the failure being fixed (a module that did not import) has no result there: it is run on
  the last commit that could run it, the culprit's parent for a CI fix (`ci_triage.md` §3), and
  compared with that; where the run knows no such commit, it is the change's. A server smoke is
  compared only with a baseline of a case the change is meant to keep: any baseline for a change that
  keeps behaviour (a performance change), one on a configuration outside its case for a fix. On the
  case a fix is for, the baseline is the failure, and the server is meant to behave differently with
  the fix: the package's own test, which fails without the change (G1), and the mode's gate judge
  that difference. An investigator's "feature off on the branch" control is, for such a change, the
  failing gate with the change's commits reverted (`debugging_playbook.md` §1).
- **A change to config construction: compare the built config, not only a server smoke**, in G0 when
  it has a switch and in the review when it has none. Diff `get_config(...).to_dict()` from the change
  and the parent tree for every config shape the changed code branches on (with and without the field
  it touches), not only the task's checkpoint. For a YaRN config fix on sglang #41299 the task's
  checkpoint built the same config with and without the fix, while a text config without
  `rope_parameters`, built by the same code, had `{'rope_theta': 10000.0, ...}` filled in on the parent
  and `{}` with the fix, so its effective rope_theta went from a wrongly filled-in 10000 to the text
  config's own 5e6: a correction, but a behaviour change outside the task's case, which this comparison
  would have reported and only the review's reading found. A short greedy smoke is a weak check here:
  one of three default-rope runs with identical built configs gave a different 16-token text.
- **Run G3 at more than one concurrency.** Concurrency 1 often passes when concurrency 8–32 fails;
  the batch-level interactions are where bugs live.
- **G5 for a feature.** Every feature is measured against the baseline, whether it claims a gain or
  not. Whoever reviews a new feature asks what it does to accuracy and to speed, so its pull request
  shows both on the feature's scenarios: G2 and G5. A feature that claims a gain shows it there, beyond
  the noise band (`perf_evaluation.md`). One that claims none is no slower than the baseline beyond the
  noise band, on the run's client, by the median of its rounds. So is one measured with InferPilot's
  client (its template, or Spec's fallback for the source's scenarios), on which the source's gain is
  never the claim. A cost the user states in the request or `goal` (determinism, memory bought with
  speed) is the claim instead, on the user's client and on InferPilot's alike, and the feature loses no
  more than it; no role states one for the user.
  A feature that is slower stops the task at Performance for a human, and nothing is rolled back. So
  a feature task names its client as it names its server, and one that names none is asked for it
  (`templates/task_definition.md` §1.1).
- **G5 for a fix.** The fix of an issue or of a CI failure is measured for speed as it is for
  accuracy (*G2 for a fix*), on a narrower condition: when it changes code the server runs. Whoever
  merges a fix on the serving path asks what it costs, so its pull request shows the fix next to the
  base on the task's client, with the run's launch script and model: no slower than the base beyond
  the noise band, by the median of its rounds. The same diff is read for both gates, by two lines
  (*What a fix changes decides its gates*): a fix that changes no code the server runs has no G5,
  `not run: no code the server runs changed`, though G2 may run on it. Where
  the base cannot serve the configuration because that is the failure being fixed, the fix is compared
  with the last commit that served it: the one G2 measured against, or, where G2 did not run (a model
  that answers no text questions), the one the triage record names. Only where the run knows no such
  commit are its numbers reported and not judged, and the pull request says that they have no
  baseline. So an issue or a ci task that can reach a fix names its client as it names its model, and
  one that names none is asked for it (`templates/task_definition.md` §1.1); no template is offered
  for it.
  A performance problem is measured whatever its fix changes: an issue triaged `performance`, or a
  CI failure on a number its test measures (a throughput, a latency, a speed threshold). It is fixed
  when that number comes back, so the number is the claim: the reproduction states it for an issue,
  Classify for a CI failure (`perf_claim`), and G5 measures it where it showed, on the reproduction
  or the failing test. A threshold moved in a test restores nothing, and G5 says so.
  A fix that is slower, or a number that does not come back, stops the task at Performance for a
  human, and nothing is rolled back: what a correct fix may cost is not a role's to decide.
- **What a fix changes decides its gates.** The fix's diff is read for G2 and for G5, each by a line
  of its own, drawn by file.
  G2 runs for every fix that changes a file other than tests and documentation (sglang: `test/`;
  `docs/`, `*.md`). Whether the server runs that file is nobody's to judge: a module under `python/`
  can be taken for a tool, a script can be one the launch path imports, and an accuracy loss let
  through on such a reading is the costly mistake. So a fix to a CI file, a Dockerfile, a benchmark
  or tool script or a dependency file is measured on gsm8k like a fix to a model file.
  G5 runs when the fix changes code the server runs. These are not: tests, documentation, CI files
  (`.github/`, `scripts/ci/`), Dockerfiles, benchmark and tool scripts (`benchmark/`, `tools/`,
  `examples/`), and a dependency declaration that moves no version (one that was missing, a
  package's metadata). A fix that changes only such files has no G5: `not run: no code the server
  runs changed`. A pull request that changes one of them and a line of code changes code. The
  framework's own fixes measure less than either line: of the 32 fix pull requests merged from
  2026-05-26 to 2026-11-26 that changed no code, none measured the server's accuracy or speed.
  One change to those files is a change to code all the same: a new version of a package the
  server runs (aiter, PyTorch, Triton, flashinfer), bumped or pinned. No line of the framework
  changes, and every kernel may. The fix arm is measured with that version installed and the base
  arm with the image's, for both gates, and the image's version is put back when the round ends.
  Where the version cannot be installed in the run's image, G2 and G5 are a blocking failure,
  `<package> <version> cannot be installed in this image`: the fix was not measured, and a human
  decides.
  A fix whose change is not in the run's container (a Dockerfile, a dependency declaration) is
  measured there all the same, and `gates` says next to the score that the change was not in
  effect: both arms then ran one environment, and the score shows only that.
  A fix is shown to work where its change takes effect. Its `issue_repro` or `failing_test` runs
  with the declaration installed, in the image built from the Dockerfile, or by running the tool:
  the same gate in the unchanged environment shows nothing.

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
