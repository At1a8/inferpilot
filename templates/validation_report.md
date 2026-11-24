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
| claimed | | | | | | |
| held-out (`perf_evaluation.md` §3; only with `held_out`) | | | | | | |

<!-- Also: the guard workloads the task names (and InferPilot's own with `guards`); out-of-memory / retraction checks; the
     reward-hack checks (`knowledge/reward_hacks.md` P) with their result. -->

## Known issues encountered

<!-- Pre-existing failures, with the control that proved it, the `known_issues.md` entry, and the upstream
     issue that tracks it or its draft in `RUN_DIR/spinoff/` with the definition that starts it. Then the
     unconfirmed leads, each with what would confirm it. -->

## Limitations and open items

## Lessons

<!-- At most a few; say where each was recorded (feature skill, method docs, known_issues). -->

## Before pushing

<!-- What the human runs before pushing, in a fresh shell, relying on neither RUN_DIR/base, which Report
     removes, nor the branch the checkout is on by then. For each test and measurement the PR body cites:
     each arm's tree made again from its commit (`git -C <framework_dir> worktree add --detach <dir>
     <commit>`: the base commit and the branch's head); the server launch of each arm with that tree first
     on PYTHONPATH, and `python -c "import sglang; print(sglang.__file__)"` with the same PYTHONPATH
     printing a path inside it (`env_hygiene.md`, *The code under test*); the client and accuracy
     commands; the numbers the PR body gives, with the run's spread; removing the worktrees after. Then
     the PR body path and the push command. -->
