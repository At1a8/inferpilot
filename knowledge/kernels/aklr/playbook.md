---
id: aklr-playbook
title: "Landing a GEAK kernel gain in AKLR — triage, port, evidence, PR (SGLang, MI355X)"
kind: kernel_skill
authors: [Zhao An]
# ---- selector: matched against the task ----
tags: [aklr, kernel library, plugin route, kernel fusion, geak overlay, kernel hand-off, upstream kernel]
match:
  framework: sglang                 # the framework whose function the AKLR route replaces
  framework_ref: "v0.5.23"          # the SGLang release the reviewed routes declared
  kernel_library: aklr
  kernel_library_ref: 78496c03      # AKLR main the rules were checked against
  platforms: [rocm-gfx950]
depends_on: [AMD-AGI/AKLR#153]      # one 5% kernel bar; the 2% end-to-end bar for new routes
reference_pr: "AMD-AGI/AKLR#86"
# ---- what the gates must show ----
expects:
  accuracy: "GSM8K with the op off and on, accuracy simulation unset; no drop beyond the run-to-run spread"
  perf: "each kernel file at AKLR's kernel bar; a new route at AKLR's end-to-end bar, this op alone, off vs on"
# ---- validation record (fill from real runs; never estimate) ----
validation:
  status: draft                     # draft | validated | stale | failed
  last_verified: ""
  framework_commit: ""
  model: ""
  gpu: ""
  measured: {}
  artifact: ""
role: advisory_prior
---

AKLR's own documents, and which role reads which: [`skills.md`](skills.md). They come first; this
playbook adds what they do not say, from AKLR maintainers' reviews of GEAK fusion PRs (AKLR #86, #87,
#88) and from the runs behind them (DeepSeek-V4.1-Flash, InferenceX AgentX, MI355X, SGLang 0.5.23).
Numbers below that AKLR's documents also give (bars, defaults) are examples from the time this was
written: take the current ones from AKLR.

## When to use

A kernel gain — a kernel back end's result (`../../kernel_backends.md` §2), a KernelFusion overlay
fusion, a hand-written kernel — that could land in AKLR instead of in the framework or in AITER. The
architect uses §Triage to decide where a kernel-level opportunity lands; the kernel liaison where an
`improved` result goes; the implementer, verify engineer and PR engineer the rest. The task still
integrates and verifies the kernel in the framework for its own gates; the AKLR branch is prepared
alongside, and the report names both.

Close tasks it ports to: a vLLM route (`@vllm_route`, AKLR's `docs/vllm_plugin_tutorial.md`); an op in
`aklr/ops` with no route.

## Source design

A kernel gain has two homes: the framework (`../../pr_playbook.md`) or AKLR. AITER does not take
agent-generated kernels, and AKLR's `harness/rules.md` rule 2 says they land in AKLR rather than in an
upstream library, so a kernel gain inside an AITER call also goes to AKLR. In AKLR an
**op** is a contract (`contract.py`), a reference (`reference.py`, the correctness oracle), a baseline
(`baseline.py`, the current path it must beat) and an API (`api.py`), plus kernel schemas (JSON with the
kernel sources and the workloads they serve). A **plugin route** (`@sglang_route(target, versions=...)`)
replaces one whole framework function and dispatches to the op, or falls back to the framework's code
with a `FallbackReason`. Dispatch serves a kernel only on workloads where it met AKLR's kernel bar.

The reference example AKLR's maintainers point to — a clear boundary, easy to maintain, a clear
end-to-end gain — is `aklr/plugins/sglang/models/deepseek_v2` (`deepseek_v2_mxfp4_moe@1.0`), the example
of AKLR's `docs/plugin_tutorial.md`. It serves GLM-5's MXFP4 MoE layer on SGLang 0.5.22 (GLM-5 runs on
SGLang's `deepseek_v2.py`); it is an example of the shape of a good plugin, not of a DeepSeek-V4 one:

- it replaces one whole, stable SGLang function, `DeepseekV2MoE.forward_normal`, copying its signature;
- one op takes the whole MoE layer — router GEMM, expert selection, shared expert appended, grouping,
  quantization, both GEMMs, weighted sum — and leaves only the cross-GPU reduction to SGLang;
- its baseline is SGLang's whole current path for those steps, not just `fused_moe`;
- its end-to-end result compares `AKLR_DISABLE=deepseek_v2_mxfp4_moe@1.0` against enabled.

One op faster → an op in `aklr/ops`. An optimization that crosses one op's boundary → a plugin that
takes a whole function or layer, like this one.

A whole AITER call is also a route target: AKLR main routes `aiter.fused_moe.fused_moe` itself
(`aklr/plugins/sglang/aiter_calls.py`, served by `aklr/plugins/aiter/fused_moe.py`), and
`@sglang_call_route` hooks one call site. An optimization inside an AITER call starts there.

## Assumption table (source → target)

What holds in a GEAK overlay (the source) and what AKLR (the target) requires.

| GEAK overlay assumption | Holds in AKLR? | Consequence |
|---|---|---|
| a fusion may patch any function, private ones included | no | hook a stable, upper-level API (§Triage 5); private symbols and AITER-internal buffers are refused |
| several fusions ship as one overlay | no | one optimization per PR |
| a gain from reordering calls or cutting communication counts | no | compute only; such gains go to the framework |
| a kernel may keep state across calls (self-resetting barrier counters, semaphores) | no | stateless kernels (§Invariants 1); AKLR #88 |
| the overlay's gain apportioned to one fusion is that fusion's gain | no | measure each op alone, off vs on (§Validation recipe); measured: router apportioned +1.0% → +1.6–1.8% alone; sparse MLA apportioned +0.75% (kernel 1.02–1.41x) → no gain alone |
| a sub-step inside an AITER call can be its own op | no | no caller in the framework; an AKLR op at the level of the whole call or layer |
| workloads recorded once stay valid | not always | axes that follow startup state stop matching after a restart (§Pitfalls) |

## Design as built

### Triage — does it belong in AKLR?

Check each gain before porting. When one fails, the gain goes elsewhere and the report says where and
by which rule.

1. **One optimization per PR.** Fusions are not bundled by subsystem.
2. **Compute only.** The replaced call keeps its inputs and outputs; only its computation gets a faster
   kernel. A gain from reordering calls, cutting communication (all-gather, copy-in) or rewriting
   framework logic is a framework change.
3. **Framework-side changes go to the framework**: a constructor (`__init__`), a whole `forward`,
   communication fusion, DP gather, sampling or verification flow.
4. **No op for a piece inside an AITER call** (e.g. sort + quant inside `aiter.fused_moe`): make it part
   of an AKLR op at the level of the whole call (the existing `aiter.fused_moe.fused_moe` route) or of
   the layer, as in the reference example. Not a PR to AITER: AITER does not take agent-generated
   kernels (§Source design).
5. **Hook a stable, upper-level API**: e.g. `select_experts`, `aiter.fused_moe`,
   `flash_mla_with_kvcache`, a layer's `forward_*`.
6. **The end-to-end bar.** A new route, or a route that now handles more cases, must clear AKLR's
   end-to-end bar (2% when this was written); an op in `aklr/ops` with no route needs only the kernel
   bar (end to end is recommended). Decide in this order:
   - the overlay's apportioned gain only ranks candidates: it errs both ways (§Assumption table), so it
     never decides alone;
   - measure the op alone, off vs on (§Validation recipe 3), before a verdict;
   - at or above the bar: submit;
   - below the bar, or within the workload's noise of it: add pairs until the gain is clear of the
     noise; if it stays below, combine it with the other fusions of its layer into one op (as the
     reference example does), or ask AKLR's maintainers before submitting; never submit a route whose
     measured gain is below the bar without saying so in `End to end`;
   - no gain: drop.
7. **A second call site of the same kernel** (e.g. a draft model's copy of a layer) is another route of
   the same op, a route that handles more cases: it needs its own end-to-end result, and it follows the
   op's triage (stateless first).

### Layout

1. **The package path mirrors the framework source file of the replaced function**: a route on SGLang's
   `srt/layers/moe/topk.py` lives in `aklr/plugins/sglang/layers/moe_topk` (AKLR #86). AKLR's docs say
   one package per SGLang source file and that the package name is the op type, unique across AKLR; the
   flattening of a nested path (`layers/moe/topk.py` → `layers/moe_topk`) is what AKLR's reviewer asked
   for, not a written rule.
2. **Declare only the framework versions validated** (`versions=(...)`) and name the image. AKLR's CI
   image (`AKLR_CI_IMAGE` in its `ci.yaml`) and its existing routes are on SGLang 0.5.22 at the time of
   writing; a route on another version (the reviewed DeepSeek-V4.1 routes declared 0.5.23) is agreed
   with an AKLR maintainer first and says so in `Not run`.
3. **Workloads come from real runs**: serve the model with the plugin on and export with `AKLR_EXPORT`.
   Several serving configurations of one fusion (e.g. concurrency 32 and 64) share one schema.

## Invariants

1. **Stateless kernels.** Cached scratch is rewritten whole by every call before it is read (AKLR
   `aklr/ops/README.md`). Violation: a kernel that zeroes its own counters at exit; it reads stale state
   after an aborted call or under concurrent streams. Rewrite cross-block synchronization statelessly:
   each CTA recomputes the row statistics it needs, or one CTA owns a whole row, or the call zeroes its
   scratch first and the zeroing counts against the gain.
2. **The reference reproduces the kernel's rounding points and tie-breaking** where the contract fixes
   them. Enforced by AKLR's accuracy audit. AKLR #86: fp32 scoring and AITER's per-lane order in top-k;
   the same floor on a normalizing denominator.
3. **A route replaces the whole original function** and returns exactly what it returns; configurations
   it does not handle return a `FallbackReason`.
4. **No contract changed to fit the kernel, no slow fallback hidden in the kernel** (AKLR
   `aklr-bad-cases`).

## Work packages

1. **Triage** (§Design as built): destination and rule, per gain.
2. **Op**: contract, reference, baseline, API, unit tests (AKLR `docs/ops_tutorial.md`).
3. **Route** (plugins only): package path, signature copied from the framework, fallbacks (AKLR
   `docs/plugin_tutorial.md`, `aklr/plugins/README.md`).
4. **Kernel and provenance**: port the GEAK kernel (AKLR `aklr-port-generated-kernels`), stateless.
5. **Workloads**: serve with the plugin on, `AKLR_EXPORT`, generate the schema.
6. **Evidence** (§Validation recipe).
7. **PR body** from AKLR's template, with InferPilot's attribution line (`../../pr_playbook.md`
   §Attribution); AKLR's `aklr-review-kernel-pr` on the branch; a human re-runs the evidence, pushes and
   opens.

## Launch configuration

- Image: the SGLang release the route declares (e.g. `lmsysorg/sglang:v0.5.23-rocm10-mi35x`), AKLR
  installed editable from the branch.
- Plugin on: `AKLR_SGLANG_PLUGIN=1`. This op off: `AKLR_DISABLE=<contract id>`, everything else
  identical. Not `AKLR_SGLANG_PLUGIN=0`, which turns every plugin off.
- Serving runs may set `AKLR_MIN_SPEEDUP=0` to serve every exported workload; it loosens a check and is
  declared (§Validation recipe 5).
- Accuracy runs unset accuracy-simulation switches (`SGLANG_SIMULATE_ACC_*`).
- CPUs pinned: each server's container bound to its GPUs' NUMA node; off and on can run at once on one
  8-GPU node, each on its own GPUs and NUMA node.

## Validation recipe

All on the PR's head commit; InferPilot's own gates (`../../validation_doctrine.md`,
`../../perf_evaluation.md`) still apply to the serving runs.

1. `pre-commit run --all-files`, `pytest tests`, `python -m aklr export check`,
   `python -m aklr validate --base origin/main --stages audit,bench <schemas>`. Always `--base`:
   without it a new kernel is held to the retention threshold (0.99, the bar an already-merged kernel
   must keep) instead of the entry bar, and a local pass can still fail CI.
2. **Kernel bar**, per kernel file: one workload faster than the op's `baseline.py` by AKLR's margin (5%
   when this was written), against the AITER commit `AKLR_BASELINE_AITER` in AKLR's
   `.github/workflows/ci.yaml`.
3. **End-to-end bar**, for a new route or a route that now handles more cases — this op alone, off vs on
   (§Launch configuration), on the workload the PR states (e.g. InferenceX AgentX at the stated
   concurrency). Confirm the kernel is served (`AKLR_EXPORT` counts, no `no_workload` majority). Report
   the primary metric (e.g. P90 interactivity) with TPOT and throughput, the number of pairs and the
   workload's run-to-run noise (DeepSeek-V4.1-Flash AgentX c64: ~3% P90). One pair shows a direction,
   not a number. Never an overlay-apportioned estimate in place of the measured result.
4. **Accuracy**: GSM8K with the op off and on.
5. **Gate overrides**: every `AKLR_*` / `AITER_*` variable that loosened a check in any run goes into
   the PR's `Gate overrides` line.
6. **CI**: kernels and code take the `ci:gpu` label and a passing `review-tier` status (a human adds the
   label). When CI's image cannot run the declared framework version yet, `Not run` says so.

## Pitfalls

| Symptom | Cause | Fix |
|---|---|---|
| the route is hooked, but almost every call falls back with `no_workload` after a restart (~96% on AKLR #87) | an exact-match workload axis follows startup state: in `flash_mla_with_kvcache`, the KV pool page counts follow free memory at startup | check served-call counts after a fresh restart; re-export, or ask AKLR's maintainers about making the axis size-agnostic |
| GSM8K ~0.74 both with the op off and on | accuracy-simulation switches (`SGLANG_SIMULATE_ACC_*`) left set from the performance runs | unset them for accuracy runs |
| reference and kernel pick different experts on ties | the reference sorts differently from AITER's per-lane sorting network | reproduce the kernel's tie-breaking in the reference (§Invariants 2) |
| a fusion measured in the overlay shows no gain alone | its share of step time is small; the overlay's gain came from the other fusions | triage 6: combine it into a layer-level op or drop it |
| the review asks to move the package | the package was named after the model, not the replaced function's source file | §Layout 1 |
| a local `validate` passes, CI fails the kernel | `validate` ran without `--base`, so the retention threshold applied | always `--base origin/main` |

## Do-no-harm

- With `AKLR_SGLANG_PLUGIN=0`, or the op disabled with `AKLR_DISABLE`, the framework runs its own code
  unchanged.
- Workloads without a qualifying kernel, and configurations the route does not handle, fall back to the
  framework's code; the route never changes results on a fallback.

## Limitations and open items

- Triage rules come from three reviews of one model's fusions; AKLR has not written them down. Confirm
  with AKLR's maintainers before relying on them for a different kind of change.
- InferPilot's PR phase prepares framework PRs; for AKLR this playbook is advisory. The AKLR branch and body
  are prepared by the roles that read it, not by a dedicated phase.
- Validated so far only on SGLang routes; vLLM routes follow AKLR's `docs/vllm_plugin_tutorial.md`.

## Porting checklist

For a different model, framework version or AKLR revision:

- AKLR's current bars, PR template and skills (`skills.md`);
- the framework function the route replaces, its signature at the declared version, and the source file
  that names the package;
- what the baseline is: the framework's whole current path for the steps the op takes;
- the workload's run-to-run noise, measured before claiming an end-to-end gain.

## Scripts

`scripts/replay_cases.yaml`: past GEAK fusions with the destination AKLR's maintainers decided. To check
an edit of this playbook, give an agent only this directory and AKLR's `main`, ask it to triage each
case's `description`, and compare with `expected`. Every case should match before the status moves past
`draft`. Most of these cases also appear in this playbook as examples, so a pass shows the rules are
consistent, not that they generalize: add each new reviewed case to the file, and replay new cases
before they are written into the playbook.

## Sources

- AKLR #86 (router with fused shared experts): review asked to move the package to
  `aklr/plugins/sglang/layers`, drop the DP gather (communication glue) and `moe_sort_mxfp8_quant` (no
  caller outside `aiter.fused_moe`). End to end alone, one pair each, DeepSeek-V4.1-Flash AgentX on
  MI355X, SGLang 0.5.23: c32 P90 146.16 → 148.79 (+1.80%), c64 75.65 → 76.83 (+1.55%).
- AKLR #87 (attention): q prep judged framework-side (it replaces a whole private `_forward_prepare`) →
  SGLang; wo_a split-K judged stateful (semaphores cached across calls; stateless, small shapes only
  1.03–1.06x) → dropped; sparse MLA + inverse RoPE measured alone: c64 P90 75.70 / 75.65 off vs 74.32 on,
  no gain → withdrawn.
- AKLR #88 (mHC): self-resetting counters judged a breach of the stateless rule.
- AKLR #153: one 5% kernel bar; `docs/acceptance_criteria.md` with the 2% end-to-end bar.
- AKLR `docs/plugin_tutorial.md`: the `deepseek_v2_mxfp4_moe` reference example.
