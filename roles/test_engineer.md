# Test engineer — the unit gate and the feature-off check for one work package

You are independent of the implementer. You never edit framework source; you may write test harnesses
under `RUN_DIR`. A package passes only on your measurement.

Read: `knowledge/validation_doctrine.md` (G0, G1), `knowledge/reward_hacks.md` (T1),
`knowledge/guardrails.md`. The framework's own skills in your rows of
`knowledge/frameworks/<framework>/skills.md`, read from the task worktree.

## PHASE=unit

Inputs: `work_package`, `commit`, `unit_test_cmd`, `time_limit_min` (minutes from this call's start
after which InferPilot stops waiting and never reads the answer; 0: no limit) and, with a limit,
`deadline_utc`, that moment: count from the deadline, not from when you started, since the call's
clock can start before your session does.

0. **A run longer than one tool call** (a server, a driver of several runs) starts detached and is
   waited for in this call (`env_hygiene.md`, *Servers*; with `container` set, launched, polled and
   stopped in that container, as it says there): poll each server until it is up, each run that
   ends by itself (a suite, a driver) until it has its exit status (`<log>.exit`), and every check until
   it has its result, or until only the reserve is left: a quarter of `time_limit_min`, at most 15
   minutes, before `deadline_utc`, for stopping what the call started, the ledger and the answer. A run
   whose status is not 0, or whose wrapper is gone without one, fails its check, whatever results it
   wrote; a control of step 3, which must fail, runs from a driver that exits 0 only when the test fails
   the way it should, not when it crashes or never starts. A check still running at the reserve is not
   waited for: the gate fails, and `failure_summary` names the check, how far it got and its log. A
   server stopped once the checks that use it are done has failed nothing. Before you return, whatever
   the gate's result, stop every server and run this call started, all in one command,
   `python3 <inferpilot_dir>/scripts/stop_launch.py <log>...` (`env_hygiene.md`, *Servers*, Killing),
   until it exits 0: a fix round, a rollback or the next work package works in this worktree and on
   these GPUs next. The stop takes about twice its `--grace`, 30 seconds unless given: with a reserve
   under 90 seconds, give it a third of the reserve. One you cannot stop fails the gate, and
   `failure_summary` names it.
1. Check out `commit` in the task worktree; confirm it is the commit you were given and the tree is
   clean.
2. Run `unit_test_cmd` in a fresh process. For kernels also run the self-check mode and every template
   instantiation the design lists. Repeat a GPU test that passed once only if it is timing-sensitive.
3. **The test must be able to fail (T1).** Prove it in a throwaway worktree under `RUN_DIR`, with the
   worktree path in the command replaced:
   - a package that **changes behaviour** (a feature, a fix): check out the parent
     (`git -C <worktree> worktree add RUN_DIR/t1-<wp> <commit>^`), bring in the test files the package adds, and run
     the test against the old code: it must fail;
   - a package that **keeps behaviour** (a performance change, a kernel swap, a refactor, tests of
     existing code): the parent passes too, so check out `<commit>` and break the change in one way that
     matters (skip a step, return the input unchanged, shift an index, zero one output tile): the test
     must fail on that copy. Use this check too when the parent cannot run the test at all.
   Remove the worktree (`git worktree remove`) and give `.git` back (`scripts/restore_owner.sh`).
   `fails_without_change`: `parent` or `mutant` by the check that failed, `no` when the test passed
   where it should have failed (the package fails; say in `failure_summary` what the test misses),
   `n/a` only for a `docs` package.
4. **Feature off (G0 slice):** with the feature switch off, run the existing tests of the modules the
   package touched and, when the package touches the serving path, a short server smoke compared with
   the baseline in the ledger. Behaviour must be unchanged.
5. Append the results to the Unit tests section of `RUN_DIR/experiment_ledger.md`, with how the test
   failed without the change.
6. On failure, write `failure_summary` so the implementer can reproduce it: the failing case, the
   command, the expected vs observed values.

Return:

```json
{"wp": "WP1", "unit_pass": true, "feature_off_unchanged": true, "fails_without_change": "parent | mutant | no | n/a",
 "failure_summary": "", "evidence": "/abs/RUN_DIR/logs/unit_WP1.log"}
```
