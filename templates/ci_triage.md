# CI triage — <cluster or test>

<!-- mode=ci artifact (see ../knowledge/ci_triage.md). Times in one stated timezone. -->

## Failure

| Item | Value |
|---|---|
| source | Failure Tracker #<n> / nightly run / CI issue #<n> |
| tests affected | <count>, e.g. `test/registered/amd/...::Class.test` |
| lanes | rocm720 / rocm724 / rocm10 / ... |
| on main, in the lane's own environment | fails / passes (the image the job ran, the lane's newest and how it was found, each test's runs, the fix that merged where the runs confirmed it, with the tests and lanes it was confirmed on: the job's commit and the change's parent fail on this image, the change passes; a change that only looks like the fix as a candidate, marked not confirmed; the tests and lanes that have no confirmed fix; what of the task was not run) / not run here (what the lane needs) |
| error signature | |
| first red run / last green run | <run links and times> |
| open fix PRs | none / <#n: what it changes, and whether its diff addresses this failure; the run verifies it in place of a fix of its own> |

## Classification

| Class | Evidence |
|---|---|
| code regression / dependency bump / test bug / flaky / infrastructure | |
| accuracy problem: yes / no | <the score the test measures: its threshold when it last passed, the last green run's score, the failing run's> |
| performance problem: yes / no | <the number the test measures: its threshold, the last green run's value, the failing run's> |

## Culprit (code regression or dependency bump)

| Item | Value |
|---|---|
| window | <last green commit>..<first red commit> |
| culprit | <commit, PR, merge time> |
| how found | diff reading / `git bisect run` (<steps>) |
| confirmation | fails at culprit, passes at its parent (artifacts) |
| defect of an AMD component | none / <the component, how it shows without the framework, and the issue drafted for its repository: `component_issue.md`> |

## Fix or handover

<!-- Fix PR: commits, the failing test now passing on every lane, the tests of the module the fix touches
     (G0 when the fix is behind a feature's switch), and gsm8k on the fix against the base, or against
     the culprit's parent where the base cannot serve, when the fix changes code the server runs (G2);
     its speed on the task's client against the same commit (G5), and for a performance problem the
     number the fix restores, whatever it changes; for an accuracy problem the score the fix restores,
     by the failing test's own measurement (G2), whatever it changes.
     Handover: who, what evidence. -->

## Dates

| First red | Culprit merged | Fix PR opened | Fix merged |
|---|---|---|---|
| | | | |
