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
    `observed` are the runs a test failed, of the runs that had it; `streak` is its failures in a row
    up to its latest run, 0 once it passed again; `fail_runs` are the runs that failed, `variants` the
    images it failed on (rocm10, rocm720, rocm724); `fix_pr` is a pull request the bot's analysis names
    as the fix, which may be wrong. The script says the counts in words of its own: `failing every
    run` (it never passed while tracked), `failing now` (so many in a row, since the day that run of
    failures began) and `not failing now`. A test that stopped failing moves to `archive`, which
    keeps its key, dates and counts only.

  What a status said in words, the counts say in numbers. `failing every run` on one variant is the
  earlier `never-passed` on a lane. A streak that begins on one day, after runs that passed, is the
  earlier `new regression (since DATE)`. `failed` well below `observed` and no streak is the earlier
  `flaky`. A state in any other form is reported as not read (`unread` in the script's output): read
  the issue itself then, and never take an empty result for a tracker without failures.
- **Nightly** failures and **human-filed** CI issues (`[CI] [AMD] ...`).
- A tracker only covers a few days (one snapshot covered 10-22 to 10-25). Record first-seen and fixed
  dates yourself; the tracker will not keep them.
- Cluster titles are generated per run and can split one root cause: the Wan DiT `ModuleNotFoundError:
  cuda` import failure appeared as three clusters (22, 14 and 8 tests) in one snapshot. Merge clusters
  by error signature before counting.

## 2. Classify before fixing

| Class | Signals | Action |
|---|---|---|
| code regression | `status` says `new regression (since DATE)`; many tests share one cluster; one lane or all lanes start failing on the same day | find the culprit commit, fix forward |
| dependency bump | started with a library or image bump (e.g. Mooncake v0.3.14 broke a PD test on ROCm images) | pin or adapt, report upstream to the library |
| test bug | `never-passed` on a lane, order dependence (`world group is not initialized` only when another test ran first), an API newer than the lane's Python (`enterContext` is 3.11+, the rocm720 lane runs 3.10) | fix the test, not the framework |
| flaky | passes on rerun; `flaky` status; failure rate below the noise you measured | rerun N times, report the rate, propose quarantine only with numbers |
| infrastructure | `Self-hosted runner lost communication`, interface errors (`UCX ERROR failed to get interface index ... errno 19`), image pull or disk failures | hand over to CI maintainers with the evidence |

**A failure on a number is a performance problem,** whatever its class: the test measures a
throughput, a latency or a speed threshold and fails because the number fell, not on an error (a
serving benchmark below its threshold on one lane). Record the number the fix must restore: the
metric, the workload and the value, from the test's threshold or the last green run's log. The fix is
judged on that number (§4), so a threshold moved in the test fixes nothing by itself
(`reward_hacks.md` C1).

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

Reference times from late October 2026 (culprit merge → fix PR opened): 17 h for the stale import,
25 h for the 32-bit mask, 18 h for the CuTe DSL import. Three of the four fixes came from one person;
the long tail (a torch-compile decode CUDA-graph capture failure on the rocm720 lane, failing every run
from 10-02 to at least 10-25) had no owner.

## 4. Gates for a CI fix

- The failing test passes on the fix, on every lane it failed on (extra gate `failing_test`).
- The existing tests of the module the fix touches still pass, judged against the tree without the
  fix; one the regression kept from running at all (the module did not import) is judged against the
  culprit's parent. G0 (`validation_doctrine.md`) only when the fix is behind a feature's switch: most
  CI fixes have none, and nothing to turn off.
- A fix that changes code the server runs is measured on gsm8k as well (G2 for a fix,
  `validation_doctrine.md`): at most 1 point below the base, or below the culprit's parent where the
  regression keeps the base from serving. A fix to tests, documentation or CI files alone (a
  `test_bug`) is not, nor one for a model that answers no text questions (the Wan DiT failures of §1).
- Such a fix is measured for speed as well (G5 for a fix, `validation_doctrine.md`), on the task's
  `client_script`: no slower than the base beyond the noise band, or than the culprit's parent where
  the regression keeps the base from serving. A fix to tests, documentation or CI files alone is not,
  unless the failure is a performance problem (§2): G5 then measures the number the fix must restore,
  whatever the fix changes.
- No test is skipped or loosened to turn the job green unless the PR says why and links an issue.

## 5. Output

`templates/ci_triage.md`: class, window, culprit and confirmation, fix PR or handover note, and the
dates (first red, culprit merged, fix PR opened) so the time-to-fix metric can be computed.
