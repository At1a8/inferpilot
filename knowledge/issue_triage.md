# Issue triage — from an AMD user report to a reply, a reproduction and a fix

Read this in `mode=issue` and whenever a user report is the task source. The goal is that every AMD
user report gets an answer: a reproduction conclusion, a fix PR, or a clear reason why it is not
InferPilot's to fix. On sglang in late October 2026, 20 of the 21 open P0/P1 AMD issues had no reply from
anyone but the reporter, and issues untouched for 60 days are closed automatically (see *Auto-close*).

## 1. Intake

- **In scope:** open issues whose title or labels mention AMD, ROCm, MI3xx, aiter, gfx9xx, HIP or MoRI,
  or whose body names an AMD GPU (MI3xx, MI30x / MI35x, gfx942, gfx950), plus customer requests imported
  by hand. `scripts/scan_issues.py` collects them with the linked PRs, the reply status and the projected
  auto-close date. A body match (`found_in: body`) carries up to three lines that name the GPU
  (`gpu_lines`): many only mention it, and some are AMD bugs whose title does not say so.
- **Not user issues** (list them, do not triage them): roadmaps, `[Failure Tracker]` issues (those are
  `ci_triage.md` input), internal tracking issues (AITER upgrade readiness, PR review trackers, CI plans).
- **Not AMD** although the title mentions HIP or ROCm: e.g. an NVIDIA crash whose title says that a
  workaround path is HIP-only. These are P2.

## 2. Priority

The roadmap is the AMD roadmap issue of the quarter (sglang #46462 for 2026 Q4): its frontier models
(DeepSeek-V4.1, Kimi-K3/3+, GLM-5.3, MiniMax-M3.1, Qwen3.8 Flash, Muse Spark Open), its model families
for distributed inference and speculative decoding (DeepSeek-V4/4.1, Qwen3.X, GLM-5.X, Kimi-K3.X,
MiniMax-M3.X, GPT-OSS), its features (PD disaggregation, parallelisms, speculative decoding, KV-cache
tiers, load balancing, attention and low-precision KV cache, quantization, diffusion) and its platforms
(MI30x, MI35x, MI45x, Ryzen AI Halo).

| Priority | Definition |
|---|---|
| **P0** | A roadmap model or feature crashes or loses accuracy under the **default or cookbook** configuration, directly blocking a roadmap deliverable, and no usable fix PR exists |
| **P1** | A roadmap model or feature crashes or loses accuracy under a **specific** configuration; a CI failure that blocks testing of a roadmap feature; a performance problem |
| **P2** | Not on the AMD side: main shows a similar failure and the root cause is outside AMD code; or a model or platform outside the roadmap |

Judgement calls, decided once so they are applied the same way every time:

- A roadmap deliverable that is itself an opt-in feature (e.g. DCP for GLM-5.x) counts as default when
  it fails in the feature's standard usage (e.g. `--tp-size 8 --dcp-size 8`).
- A cookbook recipe the reporter changed (e.g. multi-GPU recipe run at TP1) stays P0 until the
  original recipe is shown not to fail; if only the changed setting fails, it becomes P1.
- A default-configuration failure that already has an open fix PR is P1: the work left is to verify
  and push that PR.
- "Accuracy failure" means a measured loss (e.g. a GSM8K or needle-in-a-haystack drop). A report of a
  precision difference without a measured loss is P1 until someone measures it.
- "AMD side" means AMD-specific code paths in the framework and AMD-maintained stacks (aiter, MoRI,
  ROCm, the ROCm build of PyTorch). Generic framework code and third-party libraries (flashinfer,
  Mooncake, UCX) are not, unless the failure only occurs on AMD.
- Already fixed but still open, feature requests and developer-tool problems are listed with P2 and
  an action of *close*, *plan* or *hand over*.

## 3. State checks before any work

| Check | Why | How |
|---|---|---|
| Linked PRs and their state | a fix PR is not a fix: of 44 AMD bugs with a fix PR from someone else in 2026, 21 were merged by November; PRs also get closed unmerged. And a PR that cites the issue is not always its fix: it may name it as background, or be the change that caused it (sglang #43815 cites #43526 because it made the GEMM that issue reports on the default for gfx942) | PRs whose title or body cites `#<n>` or the issue URL; `scan_issues.py`, which marks `closes` on those that close it with GitHub's keywords (`Fixes #<n>`). Another is a fix only when its title or description says it fixes this issue |
| Replies from anyone but the reporter | an unanswered report is the first thing to fix | issue comments, excluding the reporter and bots |
| Projected auto-close date | the report disappears without a fix | see *Auto-close* |
| Partial fixes | a merged PR may fix one of two problems (e.g. the correction-bias half of a routing precision report while the router GEMM still returns bf16) | read the merged diff against every point of the report |
| Tests skipped instead of fixed | a CI skip hides the problem (e.g. a nightly accuracy test skipped for an FP8 checkpoint that still loses ~10 points) | look for `skip` commits that cite the issue |

## 4. Actions

| Action | When | Output |
|---|---|---|
| `reproduce` | no usable fix exists | reproduction on the reporter's configuration, then diagnosis and a fix PR |
| `push_existing_pr` | an open fix PR exists | verify it on AMD hardware, rebase, add the missing test, report results on the PR |
| `close_fixed` | the fix is merged and covers every point | verification on main and a closing comment draft |
| `needs_info` | the report cannot be reproduced as written | a reply asking for the exact command, image and commit |
| `not_amd` | the root cause is outside AMD code | a reply saying so, with the evidence |
| `plan` | feature request | a dispatch entry (`dispatch.md`) and a reply with the plan or the reason |
| `duplicate` | another open issue reports the same failure | a reply that names that issue, and what this report adds to it (a configuration, a log, a second model) |

Every action ends with a **reply draft** for the issue (`templates/issue_triage.md`). Posting it is a
human decision.

**A duplicate is answered, not worked on twice.** Two reports are one failure when the signature and
the path that fails are the same, not when the titles are alike: a second model, GPU or flag that fails
the same way is the same failure with more evidence. One of them is the original: the one the
maintainers already call so (a label, a comment), else the one opened first. Only the others are
duplicates, so that two reports are never each the other's. A run on a duplicate ends at Triage, with
the original as `duplicate_of` and a reply that names it; the original keeps its normal actions. The
failure is fixed once, in a run on the original, whose triage searches for its duplicates and takes
what they add into its record and its reproduction.

**A report with several problems is several tasks.** One issue can list more than one defect (a crash
and an accuracy loss, a hang and a speed problem, a batch of eleven): each has its own category, its
own reproduction and its own fix, and what its pull request changes decides its gates one by one. The
triage record lists them separately, each with its priority and the fix PRs that are its own. A run
takes one, the one `task` names or else the most urgent, and says which in `reason`: the run's
priority, category, action and existing fix PRs are that problem's. The reply covers every one: what
this run showed for the problem it took, and for each of the others its known state (a fix linked to
it, open or merged, or nothing yet) and that this run did not cover it. Each of them starts as a run of
its own, with the issue and the problem's name as its `task`: the run's result offers those runs
(`next.start`), each on this run's own definition and with the operator's settings this run was given.
A run that has a model takes a problem on that model and configuration: G2 and G5 measure on what
the run was set up with. A problem the report puts on another one is offered as a follow-up that
asks for its own model, scripts and GPUs; where the task itself names such a problem, the run stops
before Reproduce and asks for them. So it does where the task names the issue alone and no problem
of the report is on the run's model and configuration: the most urgent of all is taken then, and
the run asks for what that one is on. A task without a model, one that stops before its fix, takes
the most urgent problem of all; what it pins all the same (its GPUs, its scripts) has to fit that
problem, or the run stops before Reproduce in the same way.

## 5. Reproduction

- Use the reporter's image, commit and command first; record them in the triage record.
- Pin what you change: one change at a time, each with its log.
- Run the same-pattern baseline control from `validation_doctrine.md` before blaming a recent change;
  for long-running crashes (e.g. a segfault after about a million served tokens) define the soak test
  and its length up front.
- "Not reproduced" is a result: state the environment, the duration and the counts.
- A reproduction becomes an extra verification gate for the fix (`issue_repro`). Besides it, the
  existing tests of the modules the fix touches still pass. G0 (`validation_doctrine.md`) only when the
  fix is behind a feature's switch: most fixes have none, and on the reported configuration main fails,
  so there is nothing to turn off and no baseline to compare with.
- A fix that changes anything but tests, documentation and CI files is measured on gsm8k as well (G2
  for a fix, `validation_doctrine.md`), on the reporter's model: at most 1 point below the base, or
  below the commit or release the report says worked where the base cannot serve the configuration;
  with neither, the fix's score is reported and not judged. Record the model, and what the report says
  worked, in the triage record.
- Such a fix is measured for speed as well (G5 for a fix, `validation_doctrine.md`), on the task's
  `client_script`: no slower than the base beyond the noise band, or than the same stand-in for a base
  that cannot serve; with neither, its numbers are reported and not judged. A `performance` issue is
  measured whatever its fix changes: the number the reproduction states comes back.
- **Match the load before concluding.** A crash the reporter saw on one request can need queued load
  elsewhere: sglang #41299, reported on an MI350X VF for a single 32k prompt, on the reporter's commit
  and bare metal passed 6 sequential 32k prompts and crashed 3 of 3 times with 32 of them at concurrency
  16. Run the reporter's pattern and a queued one before writing "not reproduced".
- **When the reporter's image cannot run here,** run the reporter's commit from a worktree on the
  current image: one runtime for both trees isolates the framework change. An old commit may not import
  on a new image (sglang v0.5.18 on transformers 5.19: `ValueError: 'qwen3_asr' is already used by a
  Transformers config`); then only main is tested, and the report says so.
- **A GPU fault can leave the server up with a hung scheduler.** The gate needs a per-request timeout
  and a scan of the server log for fault lines, not only a liveness check.
- **The same gfx architecture is not the same kernel.** aiter keys its tuned MoE kernels on the gfx name
  and the CU count (the first two fields of the key in the `[fused_moe] using ...` log line): MI300X logs
  `('gfx942', 304, ...)`, while an MI308X, also gfx942, has 80 CUs, so it looks up other rows of the
  tuned table and may run another kernel. Compare the selected kernel name with the reporter's log
  before calling a reproduction faithful. Record bare metal or VF too: sglang #41299 and #38849 both
  came from VFs.
- **A root cause in an AMD component outside the framework** (aiter, MoRI, ROCm, the ROCm build of
  PyTorch) is that component's to fix. Show it there first: the component's own call fails without the
  framework, on the shapes and dtypes of the reproduction, or the failure is gone with that call
  replaced. The investigator then drafts the issue for the component's repository
  (`RUN_DIR/component_issue.md`): that reproduction, the versions, the GPU, counts and short log
  excerpts. A human files it. What the framework does meanwhile goes by the priority (§2):
  - **P0:** a workaround in the framework, HIP-only (`pr_playbook.md`) and the smallest that serves
    the reported configuration (another kernel, or the reference path, for the shapes that fail). It
    is a fix that changes code the server runs, so G2 and G5 measure it. Its comment and its pull
    request name the component's issue, and say what to remove once the component is fixed.
  - **P1 and P2:** no workaround. The run ends at Diagnose (`diagnosed`), and the reply gives the root
    cause, the component, and what the reporter can do meanwhile (a version that works, a flag that
    avoids the path).

  A component that changed its interface on purpose has no defect: following it is the framework's
  fix, as for any other issue.

## 6. Auto-close

sglang's `.github/workflows/close-inactive-issues.yml` runs daily (cron `0 0 * * *` UTC) and closes
every open issue whose last update is older than 60 days, adding the `inactive` label. Projected close
date = first daily run after `updated_at + 60 days`. Any comment resets the clock, but the purpose of
triage is a conclusion, not a bump.
