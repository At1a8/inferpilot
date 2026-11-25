# Kernel back ends — handing kernel-level work to a kernel workflow

InferPilot does not tune kernels. Two kinds of kernel-level work leave a framework run:

1. a kernel InferPilot wrote for a feature (functional and unit-tested, e.g. HiSparse V2's swap-in kernel)
   whose speed matters — the implementer returns a `kernel_handoff`;
2. a kernel-level performance opportunity found by a profile — the architect's performance plan
   returns a hand-off with `owner: "kernel_workflow"`.

`args.kernel_backend` decides what happens to them in the **Kernel** phase, between Implement and
Verify:

| Back end | What runs | Needs |
|---|---|---|
| `none` (default) | nothing: the hand-off package is written and listed in the report | — |
| `geak` | an interface to GEAK: the kernel lane of a GEAK checkout, which the kernel liaison runs as an outside program through GEAK's own workflow CLI ("The GEAK interface" below); InferPilot ships nothing of GEAK | `kernel_backend_dir`, the GEAK checkout; `kernel_backend_command` replaces the default command when GEAK's entry points move |
| `command` | a kernel agent's CLI: the kernel liaison runs `kernel_backend_command` with `{task_dir}`, `{out_dir}`, `{gpu_ids}` and `{budget}` (`kernel_budget` optimization rounds, default 6) substituted and reads the JSON result it writes | `kernel_backend_command` |

Adding a back end: a CLI needs no code (`command`); a native one is one branch in `kernelStage()` of
`inferpilot.js` plus a row here.

## 1. The hand-off package

The implementer (functional kernel) or the architect (performance opportunity) writes it under
`RUN_DIR/handoffs/<id>/`:

- `op_spec.md`: the operator, the shapes and dtypes the framework calls it with (from a trace when one
  exists), the math contract, the tolerance, the regime (prefill or decode, graph-captured or not);
- the **reference**, frozen: the functional kernel or the framework's current implementation; it is the
  correctness oracle;
- a unit test the reference passes;
- the plug-in point (file : function) and how the kernel is built (JIT, AOT, Triton).

The kernel liaison turns it into a task directory, `RUN_DIR/kernel/<id>/task`, in this layout unless the
command documents one of its own:

- `mode: "optimize"` — the task directory holds the kernel source to edit and its test;
- `mode: "author"` — it holds the immutable oracle (`meta.json`, `unittest.py`, frozen `baseline_src/`),
  plus `op_spec` `{op_kind, shapes, dtype, math_contract, regime}` and `target_language` (triton | flydsl
  | hip | ck);
- the back end never writes into the framework: its result comes back as a patch.

### The GEAK interface

`kernel_backend=geak` runs GEAK from its checkout, as any other program; nothing of GEAK is in InferPilot,
and a run that does not choose it never touches GEAK. The default command:

```
node <kernel_backend_dir>/interface/runtime/engine/run_workflow.mjs <kernel_backend_dir>/kernel_workflow/kernel_lane.js
  --args-file {task_dir}/lane_args.json --result-file {out_dir}/result.json
```

- `lane_args.json`, written by the kernel liaison with the task directory: `kernel_path` (the task
  directory), `workflow_dir` (`<kernel_backend_dir>/kernel_workflow`), `mode`, `budget` (`kernel_budget`),
  `gpu_ids`, `task`, `exp_root` (`{out_dir}`), `apply_to_original: "false"`, and `target_language`,
  `op_spec`, `workload_spec_path` when they are set. GEAK's `kernel_workflow/README.md` is the reference.
- `result.json`, the lane's return value: `validation_status`, `final_speedup` / `final_weighted` /
  `final_geomean`, `final_patch`, `eval_dir`.
- GEAK needs Node 20+ and picks its own agent from its settings (`GEAK_AGENT_PROFILE`,
  `GEAK_AGENT_BACKEND`). When its entry points move, `kernel_backend_command` replaces the command.

## 2. Results

| Status | Meaning | What follows |
|---|---|---|
| `improved` | the back end verified correctness against the oracle and a speedup beyond its noise on the shapes in `op_spec` | an `integrate_wp` work package: the implementer applies the patch at the plug-in point in the task worktree; the test engineer re-runs the unit test and the feature-off check; Verify and Performance then judge the integrated kernel with everything else |
| `no_gain` | correct, not faster | the functional kernel stays |
| `failed` | no correct result, or the back end did not run | the functional kernel stays; the report says why |

After integration a hand-off ends `integrated`, `integration_failed` (rolled back), or `integration_held`
when its unit gate has no result: its commit stays on the branch, unverified, and the run stops for a
human, the hand-offs it had not reached `recorded`. A run the control file stops before the integration
leaves it `not_integrated`, its gain in the report and not in the code.
With `kernel_backend=none` every hand-off ends `recorded`, and the report says how to run it later.

A back end's own numbers are never InferPilot's evidence: an integrated kernel passes InferPilot's gates like
any other change (`guardrails.md`: measured data wins). A failing or slow back end never blocks the
task.

An integrated kernel that fails Verify — the investigator's control shows the failure goes away with
its commit reverted (`kernel_regression`) — goes back to the functional kernel: its commit is
reverted, the failing case is recorded on the hand-off as a counterexample, and the hand-off goes to
the back end once more with that case added to its tests. A retry that passes is integrated and
verified again; one that fails, or a kernel that fails Verify a second time, ends `rejected`, and the
task goes on with the functional kernel. Hand-offs raised while fixing a failed Verify or a review
reach the Kernel phase too.
