# CI triage — from a red AMD job to a fix PR or a handover

Read this in `mode=ci`. A broken AMD CI lane blocks every AMD PR, so a code regression is the most
urgent task InferPilot gets. Target from the design: a fix PR within 12 hours for a code regression, and a
fix PR or a handover within a week for anything else.

## 1. Sources

- **The AMD CI on main**, read from its own runs: the scheduled `PR Test (AMD)`, `Nightly Test (AMD)` and
  `Nightly Test (AMD MI355X Disagg)` workflows on main, not pull requests or forks. For each job failing
  in the latest run: its failed steps, since which run it fails, when it last passed and, from its log
  (a token is needed), the failed tests and the first errors (`scripts/scan_ci.py`). This is what main
  shows now; the trackers below can be days behind it. Jobs that only collect the others' results
  (`*-finish`, `check-all-jobs`) are left out.
- **AMD Failure Tracker** issues on sglang, titled `[Failure Tracker] PR Test (AMD)` (label
  `ci-failure-tracker`). A bot rewrites the body; the machine-readable state sits in an HTML comment
  `<!-- ci-failure-tracker-state: {...} -->`. Entries are keyed by `test_file::test_function` with
  `cluster` (an error-signature title), `error`, `job_url`, `job_id`, `detail_url`, `first_seen`,
  `last_seen`. `scripts/scan_ci.py` parses it and aggregates clusters. The bot has written the state in
  two forms, and the script reads both:
  - each entry with a `status` in words (`new regression (since DATE)`, `never-passed`, `flaky`,
    `infrastructure`): the trackers up to October 2026;
  - version 3, the tracker open since 2026-10-22: `active` entries carry counts instead. `failed` and
    `observed` are the runs a test failed, of the runs of its image variants from its first failing
    run on; `streak` is its failures in a row up to the latest run of those variants; `fail_runs` are
    the runs that failed, `variants` the images it failed on (rocm10, rocm720, rocm724); `fix_pr` is a
    pull request the bot's analysis names as the fix, in sglang or in another repository, which may
    be wrong. An entry stays in `active` for days after the test stopped failing, then moves to
    `archive`, which keeps its key, dates and counts only. The script gives each entry the bot's own
    kind, as the tracker's body lists them: `no longer failing` (the latest scan did not see it fail,
    whatever streak it kept: the run of an image that is not run again keeps one), `broken` (two or
    more runs in a row, since the day that run of failures began), `new` (one failing run) and
    `flaky` (the rest). On 2026-11-26 the tracker held 156 active entries, and 14 of them were
    failing.

  What a status said in words, the counts say in part. `broken` since one day, after runs that
  passed, is the earlier `new regression (since DATE)`, and `flaky` the earlier `flaky`. Nothing in
  the counts is the earlier `never-passed`: `observed` begins at the first failure the tracker saw,
  so a test that failed every run counted may have passed before, and a regression that has not
  passed since looks the same. Tell the two apart by the lane's runs before `first_seen` (the job's
  history) before a failure is classified as a `test_bug`. A state in any other form, or one that is
  not JSON, is reported as not read (`unread` in the script's output): read the issue itself then,
  and never take an empty result for a tracker without failures.
- **Nightly** failures and **human-filed** CI issues (`[CI] [AMD] ...`).
- A tracker only covers a few days (one snapshot covered 10-22 to 10-25). Record first-seen and fixed
  dates yourself; the tracker will not keep them.
- Cluster titles are generated per run and can split one root cause: the Wan DiT `ModuleNotFoundError:
  cuda` import failure appeared as three clusters (22, 14 and 8 tests) in one snapshot. Merge clusters
  by error signature before counting.

## 2. Classify before fixing

**First: does main still fail, on the lane's own image?** A red job says what one image saw of one
commit. Run the failing tests on main (the commit `origin/main` names now, which the task's worktree
is only when the task began there) in the environment of the lane that failed: that lane's newest
image, its GPU model and its node count. The newest image is established, not assumed: the newest tag
of the lane's variant in the registry, no older than the one the lane's latest run on main pulled.
The answer is for every test of the task on every lane it failed on, and a pass has to hold: each
test run as the job ran it, in the job's order, three times. One that fails some of those runs is
`flaky`, not passing. So is one that passes all three here while the lane's own runs of that commit
on that image show it failing in some and passing in others: three passes do not undo an
intermittence the lane has shown. Intermittent is said of one commit, one lane and one image: the
tracker's counts span commits and images, and prove nothing of the kind. A failure that was the
CI's own (a runner, an image pull, a disk) is `infrastructure` and is handed over, whatever the
tests do here.

- All of it passes: there is nothing to fix in the framework, and the run ends with no fix and no
  reply. The job ran an image older than the lane's newest, or a fix has merged since. Most of two
  tracker snapshots were this: 160 of 214 entries in June 2026 came from an image whose aiter lacked
  `shuffle_scale`, and 168 of 208 in September from one with Triton 3.4.0 where aiter needed 3.6.0.
- Any of it fails: it is a failure of main, and is classified below. An image that is newer than
  main expects (a library bump that breaks a test) is a `dependency bump`. A lane whose Python or
  PyTorch is older than the code needs is one too, or a `test bug`: the lane is supported, so the
  test or the code has to run on it (`enterContext` on the rocm720 lane's Python 3.10: 40 tracker
  entries in November 2026).
- This machine cannot be that lane (another GPU model, another ROCm, a second node), or is not in
  its newest image: no conclusion is drawn, in either direction, and the failure is handed over with
  what the lane needs. So is the part of a task that could not be run here, where the rest passes;
  where the rest fails, the run fixes what fails and names what it did not check. A failure that
  shows on main and not on the task's own tree came in after the task's base: the task is started
  again on main.

A task that only asks whether main still fails sets `stop_after=Classify`: it needs no model and no
client, which a task that can reach a fix is asked for before it starts.

| Class | Signals | Action |
|---|---|---|
| code regression | `status` says `new regression (since DATE)`; many tests share one cluster; one lane or all lanes start failing on the same day | find the culprit commit, fix forward |
| dependency bump | started with a library or image bump (e.g. Mooncake v0.3.14 broke a PD test on ROCm images) | pin or adapt, report upstream to the library |
| test bug | `never-passed` on a lane, order dependence (`world group is not initialized` only when another test ran first), an API newer than the lane's Python (`enterContext` is 3.11+, the rocm720 lane runs 3.10) | fix the test, not the framework |
| flaky | fails some runs and passes others on one commit, lane and image; the tracker's `flaky` is a hint, since its counts span commits and images | rerun N times, report the rate, propose quarantine only with numbers |
| infrastructure | `Self-hosted runner lost communication`, interface errors (`UCX ERROR failed to get interface index ... errno 19`), image pull or disk failures | hand over to CI maintainers with the evidence |

**A failure an open pull request already fixes is verified, not fixed again.** Before a culprit is
searched for, look for the fix someone has opened: the one the tracker or the CI issue names, the pull
requests linked to it, the open ones that touch the failing test. Read its diff, since a pull request
that cites a failure does not always fix it. The run applies it to main and verifies it as it would a
fix of its own: the failing test is its gate, on every lane it failed on, and its accuracy and speed
are measured by what it changes (§4). It ends in a verification comment for that pull request, drafted
for a human to post, with what was run, on which image and hardware, at which commit of the pull
request, and the results (`pr_playbook.md` §Attribution). No second pull request is opened for a
failure that one fixes. A fix that has merged needs none of this: main has it.

One that does not pass is answered, then replaced, as for an issue (`issue_triage.md` §4, *An
existing fix that does not pass is answered, then replaced*). It is verified as it is, and nothing
is repaired on top of it: where it no longer applies to main, its own tests fail, the failing test
still fails, or it loses accuracy or speed against the base, the run drafts a comment for that pull
request with what it measured (`RUN_DIR/pr_comment.md`, for a human to post), takes the pull request
off the task branch, and goes on to Bisect and a fix of its own, as for a failure nobody had opened a
pull request for. That fix's pull request names the earlier one, and says in one sentence what it
did not pass. Only what is shown to be the pull request's counts against it: where that is not
shown, the run stops for a human, as it does for an issue.

Verifying a pull request runs its author's code and tests on this machine, next to the keys the agents
hold. The run applies one only on the user's word, whoever wrote it: it stops after Classify and asks
which one to verify, or none. The task can say so from the start (`apply_prs`: the pull requests the
run may apply, or `none`), and then nothing is asked. Nothing else stands for that word. What the
classification says of the author is no proof that the author can put code on main: an association
with the repository (a member of its organisation, a collaborator) can come without write access, a
model read it, and the run has no way of its own to look the permission up. What the Intake read
into a request in plain words is a reading too, and is asked about: only a field or an answer the
caller gave counts. A task a scan offers names no pull request to apply: it is taken by the fields
of a task definition, and an answer or a request a model wrote next to them goes no further. The
word has to decide: it names a pull request, or it says `none`. Any other answer, or both at once,
is asked about again, since taken for `none` it would send the run to a fix of its own.

What the user names is looked up like any other: the classification is given it (`named_prs`), and
the run applies only a pull request found open, with the commit its branch is at. A word that names
its repository (a URL, `<owner>/<repo>#<n>`) keeps it: it is a word for a pull request the
classification places in that repository, and never for the framework's pull request of the same
number. A pull request is fetched from the checkout's `origin` by its number, and Setup says which
repository that is: one returned under such a number and placed in another repository is not
applied, and one returned without a repository is origin's. Only where Setup did not say is a word
that names its repository held neither for nor against a pull request that came back without one:
the run asks, and the number is the answer. One that is closed, merged or not found is not applied,
the triage record says why, and the run goes on to a fix of its own. One pull request is verified
in a run: several applied together would show nothing of each, and the others are named in the
triage record.

**A failure on a number is a performance problem,** whatever its class: the test measures a
throughput, a latency or a speed threshold and fails because the number fell, not on an error (a
serving benchmark below its threshold on one lane). Record the number the fix must restore: the
metric, the workload and the value, from the test's threshold or the last green run's log. The fix is
judged on that number (§4), so a threshold moved in the test fixes nothing by itself
(`reward_hacks.md` C1).

**A failure on a score is an accuracy problem,** whatever its class: the test measures an accuracy
(a GSM8K or MMMU score against a threshold) and fails because the score fell, not on an error
(`test_gsm8k` at 0.87 where 0.93 is asked). Record the score the fix must restore: the set, the model
and the value, from the last green run's log or from the threshold the test had when it last passed.
The fix is judged on that score: G2 measures it with the test's own measurement whatever the fix
changes (`validation_doctrine.md`, *G2 for an accuracy problem in CI*), and a lower threshold in the
test is not a fix.

## 3. Code regressions: find the culprit

1. **Window:** from the last green run to the first red one (or the tracker's `since` date). Pull the
   commits merged in that window that touch the failing test's import graph.
2. **Read before bisecting.** Most AMD breakages are visible in the diff:
   - a module that imports a private helper which another change just moved or removed
     (`smallm_fp8_blockscale_gfx950` imported `_check` from a module that no longer had it);
   - CUDA warp assumptions in shared device code (`__shfl_xor_sync(0xffffffffu, ...)`, a 32-bit lane mask
     in a kernel that ROCm compiles for wave64);
   - an unconditional import of a CUDA-only package in a shared model file (a CuTe DSL kernel imported by
     a diffusion model at module load, which raises `ModuleNotFoundError: cuda` on ROCm).
3. **Bisect** with the failing test when the diff does not show it (`git bisect run` in a worktree;
   one GPU job per step).
4. **Confirm** the culprit: the test fails at the culprit and passes at its parent.
5. **Fix forward** with the smallest change (guard the import, use the shared helper, make the mask
   wave-size aware). Revert only when the fix is not obvious and the lane blocks merges.
6. **A defect of an AMD component** outside the framework (aiter, MoRI, ROCm, the ROCm build of
   PyTorch) is that component's to fix, whichever commit or bump exposed it: draft the issue for its
   repository (`RUN_DIR/component_issue.md`), with the component's own call that fails without the
   framework. A human files it. The lane is fixed as any other: forward in the framework where a
   framework commit exposed the defect, with a pin to the last version that passed where a bump
   brought it.

Reference times from late October 2026 (culprit merge → fix PR opened): 17 h for the stale import,
25 h for the 32-bit mask, 18 h for the CuTe DSL import. Three of the four fixes came from one person;
the long tail (a torch-compile decode CUDA-graph capture failure on the rocm720 lane, failing every run
from 10-02 to at least 10-25) had no owner.

## 4. Gates for a CI fix

- The failing test passes on the fix, on every lane it failed on (extra gate `failing_test`). A lane
  this machine cannot be stays `unchecked`: the gate is judged on the lanes that were run, and the
  pull request says where the fix was not verified.
- The existing tests of the module the fix touches still pass, judged against the tree without the
  fix; one the regression kept from running at all (the module did not import) is judged against the
  culprit's parent. G0 (`validation_doctrine.md`) only when the fix is behind a feature's switch: most
  CI fixes have none, and nothing to turn off.
- A fix that changes anything but tests, documentation and CI files is measured on gsm8k as well (G2
  for a fix, `validation_doctrine.md`): at most 1 point below the base, or below the culprit's parent
  where the regression keeps the base from serving. A fix to tests, documentation or CI files alone
  (a `test_bug`) is not. One for a model that answers no text questions (the Wan DiT failures of
  §1) is measured by the task's own output comparison in gsm8k's place (`output_script`; *G2 for a
  model that answers no text questions*).
- Such a fix is measured for speed as well (G5 for a fix, `validation_doctrine.md`), on the task's
  `client_script`: no slower than the base beyond the noise band, or than the culprit's parent where
  the regression keeps the base from serving. A fix to tests, documentation or CI files alone is not,
  unless the failure is a performance problem (§2): G5 then measures the number the fix must restore,
  whatever the fix changes.
- An accuracy problem (§2) is measured whatever its fix changes, a fix to the test alone included:
  G2 measures the score the fix must restore, with the failing test's own measurement, and a
  threshold moved in the test restores nothing. gsm8k against the base runs besides, by the fix's
  files.
- No test is skipped or loosened to turn the job green unless the PR says why and links an issue.

## 5. Output

`templates/ci_triage.md`: class, window, culprit and confirmation, fix PR or handover note, and the
dates (first red, culprit merged, fix PR opened) so the time-to-fix metric can be computed.
