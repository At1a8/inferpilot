# Task queue — scan of <sources> at <time, timezone>

<!-- mode=scan artifact (see ../knowledge/dispatch.md). One row per candidate; the ranking follows
     dispatch.md §6: within a rank, the smaller effort first; a task of 6 GPU hours or more is long.
     GPU hours are the wall hours a task holds its GPUs, not hours times GPUs. -->

## Sources scanned

| Source | Scope | Candidates | Artifact |
|---|---|---|---|
| upstream | <repo>, <commit range> | | `upstream.json` |
| issues | open AMD-related issues | | `issues.json` |
| ci | AMD CI on main (runs and logs), Failure Trackers | | `ci.json` |
| hardware | platform-gate audit of <arch> | | `gates.json` |

## Ranked tasks

| Rank | Task | Source | Type | Level | Priority | Depends on (AMD counterpart) | Effort | Evidence |
|---|---|---|---|---|---|---|---|---|
| 1 | | | | L1 / L2 / L3 | | | <agent hours> agent h / <GPU hours> GPU h, long when 6 or more | |

## Starting the top tasks

<!-- One task definition per top entry (task_definition.md), passed to inferpilot.js as it is. Fields the
     scan cannot establish (a model not on this machine, a performance task's workload, the client an
     issue's or a ci task's fix is measured on) stay out; the run asks for them. -->

```json
{"mode": "issue", "task": "<issue URL>", "framework_dir": "<checkout>", "model_path": "<the reporter's model>"}
```

## Ecosystem matrix changes

| Feature | Model | Platform | Was | Now | Evidence |
|---|---|---|---|---|---|
| | | | | | |

## Not taken

<!-- Candidates dropped, with the reason (duplicate, not AMD, already fixed, out of scope). -->
