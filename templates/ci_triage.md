# CI triage — <cluster or test>

<!-- mode=ci artifact (see ../knowledge/ci_triage.md). Times in one stated timezone. -->

## Failure

| Item | Value |
|---|---|
| source | Failure Tracker #<n> / nightly run / CI issue #<n> |
| tests affected | <count>, e.g. `test/registered/amd/...::Class.test` |
| lanes | rocm720 / rocm724 / rocm10 / ... |
| on main, in the lane's own environment | fails / passes (the image the job ran, the one it passes on, the fix that merged) / not run here (what the lane needs) |
| error signature | |
| first red run / last green run | <run links and times> |

## Classification

| Class | Evidence |
|---|---|
| code regression / dependency bump / test bug / flaky / infrastructure | |
| performance problem: yes / no | <the number the test measures: its threshold, the last green run's value, the failing run's> |

## Culprit (code regression or dependency bump)

| Item | Value |
|---|---|
| window | <last green commit>..<first red commit> |
| culprit | <commit, PR, merge time> |
| how found | diff reading / `git bisect run` (<steps>) |
| confirmation | fails at culprit, passes at its parent (artifacts) |

## Fix or handover

<!-- Fix PR: commits, the failing test now passing on every lane, the tests of the module the fix touches
     (G0 when the fix is behind a feature's switch), and gsm8k on the fix against the base, or against
     the culprit's parent where the base cannot serve, when the fix changes code the server runs (G2);
     its speed on the task's client against the same commit (G5), and for a performance problem the
     number the fix restores, whatever it changes.
     Handover: who, what evidence. -->

## Dates

| First red | Culprit merged | Fix PR opened | Fix merged |
|---|---|---|---|
| | | | |
