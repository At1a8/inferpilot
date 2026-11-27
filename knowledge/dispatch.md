# Dispatch — what kind of gap is this, what does it need, how big is it

Read this in `mode=scan` (Dispatch phase) and at the start of every task. Dispatch turns a candidate
(an upstream PR, an issue, a CI failure, a new-hardware audit hit, a performance opportunity) into a
typed, estimated, ranked task.

## 1. Gap types

| Type | Meaning | Typical owner of the hard part |
|---|---|---|
| framework-only | the feature needs framework changes; AMD kernels and libraries exist | InferPilot |
| needs-kernel | an operator has no adequate AMD implementation | the kernel workflow on the selected back end (`kernel_backends.md`); InferPilot writes the functional kernel or the reference, the hand-off package and the integration |
| needs-comm-library | depends on a communication or transfer library without an AMD equivalent | library owners; InferPilot integrates |
| new-model | a model architecture is missing or broken on AMD | InferPilot |
| bug | a user-visible failure (`issue_triage.md`) | InferPilot |
| ci-failure | a red AMD CI lane (`ci_triage.md`) | InferPilot |
| performance | a system-level inefficiency (`system_perf.md`) | InferPilot; kernel-level parts are kernel hand-offs (`kernel_backends.md`) |

Reference lag on sglang (NV merge → AMD merge, median, as of October 2026): new models ~8 days;
framework-only features ~84 days, of which ~67 before AMD opened a PR; features needing a new kernel
~136 days; features needing a communication library ~304 days. The type predicts the lag far better
than the size of the diff.

## 2. Difficulty

| Level | The design already exists… | Example |
|---|---|---|
| L1 | in this framework, for a similar AMD model or on the NV path | context parallelism for Qwen3.5 → Qwen3.6; DeepSeek-V4.1 CP on NV → on AMD |
| L2 | on another platform or framework | ATOM's two-batch overlap → sglang |
| L3 | only in a paper or a design document | independent development |

L1 tasks are routine for InferPilot; L2 tasks need an expert's review of the design; L3 tasks start from a
design a human should approve (a run waits for that only with `auto_approve_design: false`; otherwise its
report says no human did).

## 3. CUDA dependencies and AMD starting points

Starting points only; check the current framework code before relying on a row.

| CUDA-side dependency | AMD starting point |
|---|---|
| FlashInfer attention / sampling | aiter attention backends, Triton attention backend |
| FlashMLA | aiter MLA kernels (`hip_flash_mla`, `aiter_mla_gluon` in sglang) |
| DeepGEMM (FP8 block-scale, grouped GEMM) | aiter / CK block-scale GEMM, hipBLASLt, FlyDSL |
| CUTLASS, CuTe DSL | CK, FlyDSL, Triton |
| TensorRT-LLM kernels | aiter |
| DeepEP | MoRI-EP |
| Mooncake / NIXL transfer | MoRI-IO, Mooncake with HIP support |
| NCCL, custom all-reduce | RCCL, `AiterCustomAllreduce`, quick all-reduce |
| hardware features (TMA, NVLink-specific paths, NVFP4) | no direct equivalent: redesign (e.g. NVFP4 → MXFP4 conversion) |

## 4. Common enablement moves

- Replace a CUDA-only call on HIP with an equivalent or a no-op, behind the platform check.
- Route the operator to an aiter, Triton or TileLang backend.
- Fix a platform gate that switches a ROCm fast path off. Example from Hyperloom's static recon: vLLM's
  `cutlass_fp8_supported` is always false on ROCm, so FP8 linear layers fall back to per-tensor scales
  and never reach aiter's FP8 GEMM.
- Make device code wave-size aware (wave64 on Instinct, wave32 on RDNA).
- Keep the change HIP-only or behind an environment switch so the CUDA path is untouched.

## 5. New hardware audit

When a new architecture arrives (e.g. gfx1250 / MI45x), audit the platform gates
(`scripts/audit_platform_gates.py`):

- device-capability checks written for NV generations: gfx1250 reports a major version of 12, so
  `major >= 12` checks meant for NV Blackwell can select a CUDA-only path on ROCm (sglang's
  `layers/attention/dsa_backend.py` carries a comment about exactly this);
- gates that name one AMD architecture (`gfx950`, `is_gfx95_supported`) where the new one also
  qualifies, or must not;
- wave-size assumptions.

## 6. Ranking

1. CI code regressions (they block every AMD PR).
2. P0 issues.
3. Roadmap items, ordered by the customer workloads that need them.
4. P1 issues and performance opportunities.
5. Everything else, including P2 and documentation.

Within a rank, what a named customer workload or a roadmap item needs comes first, then the smaller
effort, by GPU hours and then agent hours, then the larger measured or estimated gain: the day's machines
reach more conclusions, and a long task does not hold back the short ones behind it. Effort orders tasks
within a rank, never across ranks.
GPU hours are the wall hours a task holds its GPUs (a machine for a TP8 run), not hours times GPUs.

A task of 6 GPU hours or more is `long`. Its run is likely to outlast a default time limit or a shared
machine's slot (an intermittent failure that needs soaks, a multi-GPU or multi-node configuration): start
it apart from the queue, on a machine of its own and with time of its own, not in queue order. That is
also why a long task at the end of its rank is not left waiting behind the short ones every day.

Each ranked task records: type, level, dependencies with their AMD counterparts, the subsystems it
touches, an effort estimate (agent hours and GPU hours), whether it is `long`, and the evidence it came
from.
