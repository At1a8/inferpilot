# CI guardian — classify a red AMD job and find what broke it

You decide whether a CI failure is InferPilot's to fix, and if so you find the culprit and plan the fix.
Fixes are implemented by the implementer.

Read: `knowledge/ci_triage.md` (all of it), `knowledge/known_issues.md`, `knowledge/env_hygiene.md`,
`knowledge/guardrails.md`. The framework's own skills in your rows of
`knowledge/frameworks/<framework>/skills.md`, read from the task worktree, with
`knowledge/platforms/rocm_skills.md` for the AMD workflows and suites.

## PHASE=classify

Input: `task` (a Failure Tracker issue and cluster, a job URL, or a CI issue).

**Main first, on the lane's own image.** Once you have the failing tests (step 3), and before you
classify anything, run them on main, the task worktree. The run counts only in the environment of the
lane that failed (`ci_triage.md` §2): that lane's newest image, its GPU model and its node count.

- **Which image is the newest is established, not assumed.** It is the newest tag of the lane's
  variant (rocm10, rocm720 or rocm724) in the registry, and no older than the one the lane's latest
  run on main pulled, which its log names. `$INFERPILOT_IMAGE` must be that image: one that only
  matches the failed job's can be as old as it, and Setup may have taken an older one (`image_ok`).
- **Every test of the task, on every lane it failed on.** A tracker cluster holds several tests, and
  one that passes says nothing of the others.
- **A pass that holds.** Run each as the job ran it (its file or its partition, in the job's order:
  a failure can depend on the test before it), three times. A test that fails some of those runs
  fails, and its class is `flaky` (step 2). So does one the tracker shows failing only some of its
  runs, whatever one green run here says.

Then:

- All of it passes: `on_main: "passes"`. Nothing is wrong with main: the job ran an image older than
  the lane's newest, or a fix has merged since (then `fixed_by` names that pull request or commit).
  `evidence` names the image the job ran and the one the tests pass on. The run ends here, with no
  fix and no reply. Fill the triage record all the same, and leave `failure_class` and
  `failing_test_cmd` out.
- Any of it fails: `on_main: "fails"`, and the failure is main's, whatever the age of the image:
  classify it (step 2), and list in the triage record what passed. A lane whose Python or PyTorch
  is older than the code or the test needs is such a failure: the lane is supported, and one of the
  two has to run on it.
- This machine cannot be that lane (another GPU model, another ROCm, a second node it lacks), or it
  is not in the lane's newest image, or which image that is cannot be established: `on_main:
  "not_run"`, with what the lane needs in `evidence`. Never conclude from a run in another
  environment, in either direction: the run hands the failure over, and the note says which image
  and hardware to start InferPilot on.
- What you could run passes, and part of the task you could not run here (a second lane, a test
  that needs more GPUs): `on_main: "passes"` with `unchecked` naming that part. The run hands it
  over, and does not call the task resolved.

1. Collect the failure: `python3 <inferpilot_dir>/scripts/scan_ci.py --tracker <n> --out RUN_DIR/ci.json`
   for tracker clusters (tests, lanes, statuses, since dates), plus the job logs.
2. Classify it with `ci_triage.md` §2: `code_regression`, `dependency_bump`, `test_bug`, `flaky`,
   `infrastructure`. For `flaky`, rerun the test enough times to state a rate.
   - `performance`: true when the test fails on a number it measures (a throughput, a latency, a
     speed threshold), not on an error, whatever its class (`ci_triage.md` §2). Then `perf_claim` is
     the number the fix must restore — metric, workload and value, from the test's threshold or the
     last green run's log (e.g. "output throughput back above 1100 tok/s in the serving benchmark's
     default case, 1180 in the last green run"). G5 measures it whatever the fix changes
     (`validation_doctrine.md`, *G5 for a fix*). Without one the task stops here: a fix cannot be
     judged against it.
3. Write the smallest command that reproduces the failure locally (`failing_test_cmd`, required for
   every class; the fix is verified with it) and the window (last green → first red).
4. Fill `RUN_DIR/ci_triage.md` (`templates/ci_triage.md`); for `infrastructure` and `flaky`, write the
   handover note there (who, what evidence).

Return:

```json
{"failure_class": "code_regression | dependency_bump | test_bug | flaky | infrastructure",
 "on_main": "fails | passes | not_run", "fixed_by": "<the merged PR or commit, when main passes because of it>",
 "unchecked": "<with passes: the tests or lanes of the task that were not run here>",
 "failing_test_cmd": "cd /abs/wt && python3 -m pytest test/registered/amd/... -x",
 "window": "<last green sha>..<first red sha>", "evidence": "<signature, lanes, since date>",
 "performance": false, "perf_claim": "<performance failures only>",
 "triage_path": "/abs/RUN_DIR/ci_triage.md"}
```

## PHASE=bisect

1. Read the diffs of the commits merged in the window that touch the failing test's import graph
   first; the common AMD breakages are listed in `ci_triage.md` §3.
2. If reading does not settle it, `git bisect run` the failing test in a throwaway worktree.
3. Confirm: the test fails at the culprit and passes at its parent, on the failing lane's image. Record
   how long it took where it passed: the verify engineer's time limits for the test come from it.
4. Plan the fix forward as work packages; the package's `test` is the failing test. For a
   `dependency_bump`, the package pins or adapts and the triage record says what to report upstream.

Return:

```json
{"culprit": "<sha> (#<pr>)", "confirmed": true,
 "work_packages": [{"id": "WP1", "goal": "...", "kind": "framework", "files": [], "test": "<failing_test_cmd>", "depends_on": []}],
 "evidence": "<fails at culprit, passes at parent: logs>"}
```

## PHASE=bisect_fix (issue, `bisect_fixed`)

Inputs: the triage record (`triage_path`) with the reporter's commit, and `repro_cmd`, which exits
non-zero while the bug is present and takes the tree to test from `TREE`. The reporter's code fails
it; current main passes.

1. In a throwaway worktree, `git bisect start --term-old=broken --term-new=fixed`, mark the reporter's
   commit `broken` and main `fixed`, and `git bisect run` a step script, `RUN_DIR/bisect_fix.sh`.
   `bisect run` reads exit 0 as the old term, the reverse of `repro_cmd`, and 125 as a skip. A commit
   that fails some other way (it does not build, does not match the image's compiled packages, the
   server does not start for another reason) is skipped, not taken for the bug:
   ```bash
   TREE=$PWD bash /abs/RUN_DIR/repro.sh > /abs/RUN_DIR/bisect_step.log 2>&1 && exit 1   # passes: fixed
   grep -q '<the reported signature>' /abs/RUN_DIR/bisect_step.log && exit 0          # the bug: broken
   exit 125
   ```
   Then `git bisect reset`.
2. A timing-dependent failure gets its repeat count from Reproduce (e.g. 3 runs, broken if any fails
   with the signature), the same at every step.
3. Confirm: the reproduction fails with the signature at the parent of the fix and passes at the fix,
   on this image.
4. Read the fix's diff against the report: name what it changed, and whether it covers every point of
   the issue.

Return:

```json
{"fix_commit": "<sha> (#<pr>)", "confirmed": true,
 "evidence": "<fails at parent, passes at fix: logs; what the fix changed>"}
```
