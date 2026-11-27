# Validation report — <feature> on <framework> / <model> / <platform>

<!-- Phase 6 and Phase 8 artifact; the PR description is written from this file. Every number needs an
     artifact path. -->

## Summary

<!-- Three to five sentences: what was built, whether every gate passed, the headline benefit with its
     budget, and the open issues. -->

## What was built

| Commit | Work package / change |
|---|---|
| | |

## Configuration

<!-- Baseline and feature launch flags, budgets (`--max-total-tokens`, host ratio, memory fraction),
     the framework commit, model, GPUs. -->

## Correctness gates

| Gate | Suite | Baseline (same pattern) | Feature | Verdict | Artifact |
|---|---|---|---|---|---|
| G0 | | | | | |
| G1 | unit tests, each able to fail (parent commit, or a broken copy when the change keeps behaviour) | | | | |
| G2 | | | | | |
| accuracy claim (a CI failure on a score its test measures) | the failing test's own measurement, as the test stood at the base: set, questions, model, sampling | the score on the base by that measurement, as the verify engineer measured it in this run on this machine (the commit, each run's score), or `not measured`: only the test, the verify and the performance engineer produce numbers that count (`guardrails.md` §3). Classify's runs reproduce the failure and are named apart as that, each with its commit: the task worktree is at the base, and main only where the task began on that commit. Never the failed job's own score, which is another commit's on another machine: that one is named apart too, with its commit and its lane | the score restored, and the claim | | |
| G3 | | | | | |
| G4 | | | | | |

### Failure taxonomy (G3/G4)

| Configuration | Runs | Catastrophic collapse | Mild deviation | Empty output | Crash |
|---|---|---|---|---|---|
| baseline, same pattern | | | | | |
| feature | | | | | |

### Engagement evidence

<!-- Log lines / counters showing the feature path ran in G3–G5 (e.g. admissions, demotions, rows
     swapped in) and that its error counters stayed at zero. -->

## Performance (G5)

| Workload | Budget | Reference arm | Feature arm | Delta | Runs | Artifact |
|---|---|---|---|---|---|---|
| the claim's: a gain, or, where none is claimed, no slower than the baseline (a feature, a fix); for a performance problem, the number restored | | | | | | |
| held-out (`perf_evaluation.md` §3; only with `held_out`) | | | | | | |

<!-- Also: the guard workloads the task names (and InferPilot's own with `guards`); out-of-memory / retraction checks; the
     reward-hack checks (`knowledge/reward_hacks.md` P) with their result.
     A fix that changes no code the server runs: `not run: no code the server runs changed`, and no
     table. A fix whose base cannot serve the configuration: the commit it was compared with, or that
     it has no baseline. -->

## Known issues encountered

<!-- Pre-existing failures, with the control that proved it, the `known_issues.md` entry, and the upstream
     issue that tracks it or its draft in `RUN_DIR/spinoff/` with the definition that starts it. Then the
     unconfirmed leads, each with what would confirm it. -->

## Limitations and open items

## Lessons

<!-- At most a few; say where each was recorded (feature skill, method docs, known_issues). -->

## Before pushing

<!-- What the human runs before pushing, in a fresh shell, relying on none of what Report undoes or
     leaves to chance: RUN_DIR/base, which it removes, the machine, whose lock it frees, and the branch
     the checkout is on by then.
     - The machine first: `mkdir -p RUN_DIR/rerun/logs && python3 <inferpilot_dir>/scripts/run_lock.py
       acquire --run-dir RUN_DIR/rerun --mode rerun --task '<the PR>'`, which exits 1 while another task
       holds it; then `python3 <inferpilot_dir>/scripts/gpu_status.py --check all` before each arm's
       servers start, since a number measured next to another job is not a measurement.
     - For each test and measurement the PR body cites: each arm's tree made again from its commit
       (`git -C <framework_dir> worktree add --detach <dir> <commit>`: the base commit and the branch's
       head; for the G2 and the G5 of a fix, the commit its baseline was measured on when that was not
       the base, and the fix alone when it had none); every command of an arm, its server, tests,
       reproduction and benchmark client alike, with
       that tree first on PYTHONPATH, and `python -c "import sglang; print(sglang.__file__)"` under the
       same PYTHONPATH printing a path inside it (`env_hygiene.md`, *The code under test*); for an
       arm whose change takes effect outside its tree (a package's version, an environment variable a
       Dockerfile sets), the setup that puts it into effect before the arm's commands, its check, and
       the image's environment back after them; the
       measurement as the run made it (`perf_evaluation.md` §3: the discarded warm-up, a fresh server per
       arm, the same rounds in the same order), its logs under RUN_DIR/rerun/logs; the numbers the PR body
       gives, with the run's spread.
     - Then the worktrees removed, `run_lock.py release --run-dir RUN_DIR/rerun`, the PR body path and the
       push command. -->
