---
id: pard2
title: "PARD-2 target-aligned parallel-draft speculative decoding for Qwen3 (sglang, ROCm gfx950): a standalone draft proposes K tokens in one forward, fusing the target's hidden states through target_proj"
kind: feature_skill
authors: [At1a8]
tags: [pard2, pard-2, pard, vllm#49406, vllm#55257, sglang#51465, parallel draft, parallel drafting, target-aligned draft, draft model, speculative decoding, target-dependent, target-independent, accept length]
match:
  framework: sglang
  framework_ref: 179247229d          # PR head: 3 commits on main a735253e84 (2026-11-19)
  feature: pard2_speculative_decoding
  model_arch: [qwen3]                # Qwen3-8B target with the amd/PARD2-Qwen3-8B draft
  platforms: [rocm-gfx950]
depends_on: ["vllm-project/vllm#49406", "vllm-project/vllm#55257"]   # vLLM port (MRv2 head cc933753); the anchor fix
reference_pr: "sgl-project/sglang#51465"
expects:
  accuracy: "gsm8k (all 1319 questions) within 1 point of the target alone; lossless (exact verification), so gsm8k only"
  long_context: "not claimed: the released drafts were trained at 1024 tokens and accept ~1 token per step on >= 4k contexts, in the reference implementation too"
  stability: "not claimed as a dedicated stress test"
  perf: "at least vLLM #49406's speed-up over no speculation at concurrency 1 on its 4 x 80-prompt case"
validation:
  status: validated                  # for the gates claimed below: G2 and G5 (G1 parity on an earlier revision)
  last_verified: "2026-11-19"
  framework_commit: "179247229d (sglang main a735253e84 + the PR's 3 commits)"
  model: "Qwen3-8B with amd/PARD2-Qwen3-8B, K = 15"
  gpu: "MI355X (gfx950) x1, TP1"
  measured:
    gsm8k_ci: "TD 0.9075 at accept length 6.65, TI 0.9037 at 5.34, target alone 0.9067 (5-shot, 1319, greedy; test_pard2_qwen3_8b_gsm8k_mi35x.py, thresholds 0.88 / 5.0)"
    vllm_case_c1: "TD over no speculation (114 tok/s): GSM8K 6.95x (accept 7.52), MATH-500 7.65x (8.42), HumanEval 7.48x (8.56), MT-Bench 3.90x (3.97); TI 6.00x / 6.73x / 6.57x / 3.35x; vLLM #49406 on an RTX PRO 5000: 5.38x / 5.64x / 5.99x / 2.89x at 7.72 / 8.09 / 8.72 / 4.02. Two rounds each for TD and TI, spread <= 1.9%"
    gsm8k_c8_c32: "TD 4.00x at 8 and 1.59x at 32 (accept 7.61 / 7.58; vLLM 7.70 / 7.52); TI 3.56x / 1.42x"
    reference_parity: "draft top-1 identical to the PARD-2 HF reference on the first draft step, 16 prompts, TD and TI (revision c4dcb324da, before the trims)"
  artifact: "sglang PR #51465 (At1a8/sglang branch fangyuan/pard2)"
role: advisory_prior
---

## When to use

- Adding PARD-2 (arXiv 2605.08632) to sglang on AMD GPUs, or extending this port to another Qwen3 draft
  (amd/PARD2-Qwen3-14B) or another speculative-decoding stack.
- Porting to the Llama draft (amd/PARD2-Llama-3.1-8B): the worker is family-agnostic, the draft model
  class and the startup check are Qwen3-only.
- Not for long prompts with the released drafts (see Limitations): measure on short-prompt benchmarks.

## Source design (vLLM #49406; the anchor fix is also #55257)

The draft is a standalone small causal LM (Qwen3-0.6B-sized for an 8B target) that proposes K tokens in
one forward over the block `[bonus, pard_token x K]`. The input of a row at position t is
`embed(token[t]) + pard2_scale * target_proj(concat(target hidden states at t - 1 of pard2_target_layers))`
with the feature of position -1 equal to zero, so the draft starts from token 0 paired with a zero feature
(the anchor row). The mask rows of the block reuse the last feature. Target-dependent mode (TD) loads
`target_proj` from the draft's `warp_model.bin`; target-independent mode (TI) uses the token embeddings
only. vLLM picks TD when `target_hidden_size * len(pard2_target_layers) == pard2_target_dim`. Verification
is the usual chain accept.

## Facts about the target

- sglang has no generic parallel-draft-model worker (vLLM's old proposer already ran PARD-1, which is why
  #55257 is small). DFLASH, a block drafter, provides what PARD-2 reuses: `DFlashVerifyInput`,
  `DFlashDraftInputV2`, the chain accept kernels, the target's aux hidden-state capture
  (`dflash_target_layer_ids`) and the draft's TARGET_VERIFY CUDA graphs fed through `input_embeds`.
- The target returns its captured layers in ascending depth; the post-norm final comes separately
  (`LogitsProcessor.keep_last_hidden_states`).
- The released draft's `config.json` says float32 while its weights are bf16; `--dtype auto` would run it
  in fp16.
- An HF repo id is not a directory: `warp_model.bin` has to be read from a local snapshot.
- Every new server flag needs a row in `docs/docs/advanced_features/server_arguments.mdx`
  (`test_server_args_docs_coverage`, CPU CI).

## Assumption table (vLLM → sglang on ROCm)

| vLLM assumption | holds in sglang? | consequence for the design |
|---|---|---|
| the EAGLE input path left-shifts the ids (drops the anchor row; #55257 adds an ANCHOR mode) | not used | token t stays at position t with feature P(t - 1); position 0 reads a zero feature |
| aux layers captured in ascending order; target_proj trained on config order `[-1, -8, -16, -24]` | ascending + final separate | permute target_proj's column blocks at load: aux ascending, final last |
| entry -1 is the post-norm final (fixed on MRv2 in #49406) | via `keep_last_hidden_states` | the logits processor keeps the post-norm final for PARD2 TD |
| the anchor needs an extra scheduler slot / draft-KV row | no: the draft KV shares the target's slot ids | no extra row; the feature buffer is indexed by KV slot |
| rejected rows must be excluded from the features | features are read only from committed positions' slots | nothing to do |
| one code path for every platform | this port is ROCm-only | `_handle_pard2` rejects PARD2 unless `_is_hip` |

## Design as built

1. `SpeculativeAlgorithm.PARD2` (appended enum member) with dispatch in `handle_server_args` and
   `create_worker`; `_handle_pard2` in `arg_groups/speculative_hook.py`: the `_is_hip` gate, the draft path
   (an HF id is resolved with `snapshot_download` and declared back), the chain shape (num_steps 1, topk 1,
   verify width 16 by default, so K = 15), one table of rejected flags, the checkpoint and vocabulary
   checks, and the mode (`--speculative-pard2-mode auto|target-dependent|target-independent`).
2. `Pard2WorkerV2` (spec v2, overlap and non-overlap). Prefill: target EXTEND with FULL capture, the
   projected features of the chunk into `p_buf` at its slots, one draft EXTEND over the chunk with shifted
   features. Decode: draft pass over `[bonus, pard_token x K]` (every row reads P(s - 1)), argmax of rows
   0 .. K - 1, target verify through the DFLASH chain accept (no custom mask), the verify rows' features
   into `p_buf`, then a context pass of the draft over the verify rows at their own positions, which
   rewrites the draft KV with the real tokens.
3. `p_buf` is `[num_slots, draft_hidden]` in the draft dtype, indexed by KV slot: it lives and dies with
   the target KV, so chunked prefill, the prefix cache and overlap need no special case.
4. `Pard2Qwen3ForCausalLM`: a Qwen3 draft plus `target_proj` (bias per `pard2_proj_bias`);
   `project() = pard2_scale * target_proj(features)`; `lm_head` tied to `embed_tokens` as in the HF
   reference (the checkpoint stores both).
5. Memory: the draft KV pool is sized like STANDALONE's, plus `pard2_feature_bytes_per_token` per slot.
6. The draft runs in bf16 under `--dtype auto`.
7. Shared code only gains `or is_pard2()` on existing conditions (decode and prefill CUDA-graph runners,
   spec_utils, base_spec_worker, pool configurator, aux capture); the logits processor checks `_is_hip`
   before reading the spec config.

## Invariants

1. Position 0 pairs token 0 with a zero feature (`gather_shifted_features`). Broken: acceptance ~2 on
   Qwen3-8B (vLLM before #55257: 1.99 on GSM8K).
2. The row at position t reads P(t - 1) from the slot of t - 1. Broken: acceptance drops; outputs stay
   correct, since the target verifies every token.
3. The mask rows of the draft block read P(s - 1).
4. `target_proj`'s columns follow the capture order. Broken: TD accepts no more than TI.
5. PARD2 cannot start without HIP (startup error), so no PARD2 branch runs on CUDA.
6. With PARD2 off every shared code path is unchanged (additive conditions only).

## Work packages

1. Algorithm, flag and startup validation: `speculative/spec_info.py`, `speculative/spec_registry.py`,
   `arg_groups/fields/spec.py`, `arg_groups/field_order.py`, `arg_groups/speculative_hook.py`, the
   flag's row in `server_arguments.mdx`. Test: docs coverage test; non-HIP startup error.
2. Draft model and weights: `models/qwen3_pard2.py`, `configs/model_config.py` (architecture override,
   dtype). Test: target_proj loaded, shapes, mode logged at startup.
3. Target capture and pool sizing: `model_runner_components/spec_aux_hidden_state.py`,
   `layers/logits_processor.py`, `model_executor/pool_configurator.py`,
   `runner/prefill_cuda_graph_runner.py`.
4. Row layout: `speculative/pard2_utils.py`.
5. Worker and runtime branch points: `speculative/pard2_worker_v2.py`,
   `runner/decode_cuda_graph_runner.py`, `speculative/spec_utils.py`, `speculative/base_spec_worker.py`,
   `managers/scheduler.py` (per-request validation), `layers/attention/aiter_backend.py` (chain verify
   without a custom mask). Test: acceptance on PARD-2's GSM8K set at concurrency 1.
6. CI: `test/registered/amd/accuracy/mi35x/test_pard2_qwen3_8b_gsm8k_mi35x.py`.

## Launch configuration

```bash
python3 -m sglang.launch_server --model Qwen/Qwen3-8B \
    --speculative-algorithm PARD2 --speculative-draft-model-path amd/PARD2-Qwen3-8B
# auto resolves to target-dependent; K = 15 (--speculative-num-draft-tokens 16 by default)
# target-independent: add --speculative-pard2-mode target-independent
```

Validated with `--mem-fraction-static 0.85 --max-running-requests 64`, TP1, the default aiter attention.
Startup logs `PARD2 draft worker: mode=..., target_proj loaded=...`.

## Validation recipe

- G1 (reference): first-step draft logits against the PARD-2 HF reference (`pard/pard2_infer.py` of
  AMD-AGI/PARD) on 16 prompts, TD and TI: top-1 identical.
- G2 accuracy: gsm8k, all 1319 questions (`scripts/eval_accuracy.sh`, evalscope), within 1 point of the
  target alone. PARD-2 verifies exactly, so it is a lossless change: gpqa_diamond and simple_qa are not
  needed. Measured with the PR's CI test (5-shot, greedy; it also asserts accuracy > 0.88 and accept
  length > 5.0): TD 0.9075 at 6.65, TI 0.9037 at 5.34, the target alone 0.9067.
- G5 performance, with no other GPU of the machine in use (speed numbers move otherwise), vLLM's case:
  80 prompts each of GSM8K and HumanEval (PARD-2's eval set, `datas/bmk` of AMD-AGI/PARD), MATH-500
  (its first 80 problems; PARD-2's set is the first 20) and MT-Bench (the first
  turns of its 80 questions); PARD-2's template (system prompt "You are a helpful assistant.",
  enable_thinking off), greedy, 256 new tokens, concurrency 1, 8 warm-up prompts and a cache flush before
  each dataset; output tok/s against the same server without speculation; GSM8K also at 8 and 32.
  Measured: see `validation.measured` above (one round without speculation, two for TD and TI).
- Do not gate on token-for-token greedy equality: the bf16 baseline itself differs run to run (59 of 80
  prompts identical between two runs of the target alone).

## Pitfalls

- Acceptance ~2 instead of ~7.5 → the draft input is left-shifted and loses the anchor row → keep token t
  at position t and pair position 0 with a zero feature.
- TD silently becomes TI when the draft is given as `amd/PARD2-Qwen3-8B` → `warp_model.bin` is looked up
  in a path that is not a directory → resolve the id to its snapshot before the mode is decided.
- The draft runs in fp16 → its config says float32 and auto maps that to fp16 → bf16 under auto.
- Acceptance 1.1 on 8k prompts and 1.0 on 30k-token needles → the drafts were trained at 1024 tokens; the
  reference collapses too (6.4 at 1k, 7.1 at 2k, 1.13 at 4k, 1.08 at 8k context) → not a port bug. In the
  reference, draft attention restricted to the first 4 tokens plus the last 2048 restored ~8 on 8k
  prompts (8 prompts): a possible follow-up.
- 6 to 8x slower than no speculation on random 8k/1k at concurrency 32 → the acceptance above, and ~128 ms
  per speculative step (16-row verify plus two draft passes over 8k of KV, the context pass eager) against
  ~14 ms per decode step → a port is measured on its source's scenarios, and vLLM's PR never ran 8k/1k:
  benchmark PARD-2 on vLLM's short-prompt case only, and leave 8k/1k out unless a goal names it.
- A gate "greedy outputs equal to the target's alone" fails → baseline nondeterminism → judge correctness
  by accuracy (and, if needed, by whether each divergence starts at a near-tie).
- The docs coverage test fails → the new flag needs a `server_arguments.mdx` row.
- Unit suites that import flashinfer fail to collect in a ROCm container → CUDA-only, unrelated.
- On MI355X hosts the rocm-smi card index can differ from the HIP device index → check free memory per HIP
  device (`torch.cuda.mem_get_info`) before pinning a GPU.

## Do-no-harm

- Without HIP: startup error "PARD2 speculative decoding is supported on AMD GPUs (ROCm) only."
- Other algorithms: unchanged; every shared change is `or is_pard2()`, and the logits processor reads the
  spec config only under `_is_hip`.
- Startup rejects TP > 1, PP, DP attention, attention CP, DCP, PD disaggregation, hierarchical cache,
  hisparse, unified memory, the draft window, the DFLASH KV ring, page size other than 1, mixed chunk,
  ragged verify and topk other than 1; the generic hooks reject LoRA and block verification and turn
  `--speculative-adaptive` off with a warning.
- Per request: constrained decoding, and `return_hidden_states` under overlap, are aborted.

## Limitations and open items

- Qwen3 drafts only; TP1 only.
- Long prompts (>= 4k) with the released drafts: acceptance ~1. The speed-up shrinks with concurrency:
  on GSM8K 6.95x at 1, 4.00x at 8, 1.59x at 32.
- Step-cost items found by profiling, not done: the K/V `.contiguous()` copies before the aiter
  target-verify attention, a fused QK-norm + RoPE + KV write at verify width, the eager context pass and
  glue between graphs; kernel hand-offs for the verify-width attention and the small-M GEMMs.
- TI with a target of another size (e.g. Qwen3-4B with the 8B draft) resolves under auto but is not
  measured here.

## Porting checklist

- The draft family and its config fields: `spd_type`, `pard_token`, `pard2_target_layers`,
  `pard2_target_dim`, `pard2_scale`, `pard2_proj_bias`, `tie_word_embeddings`, `torch_dtype`.
- Target hidden size x `len(pard2_target_layers)` against `pard2_target_dim`: is TD possible?
- The order in which the framework returns the target's aux layers, and where the post-norm final comes
  from.
- How draft input embeddings reach the CUDA-graph runner.
- Slot-indexed buffers against the page size.
- The upstream benchmark case to reproduce, and the platforms the change may touch.

## Scripts

None: the CI test in the PR is the accuracy gate, and the G5 client is fully described above.

## Sources

- Paper: arXiv 2605.08632. Reference implementation: AMD-AGI/PARD at 6f279bf3 (`pard/pard2_infer.py`,
  `datas/bmk`).
- vLLM #49406 (head cc933753, MRv2 speculator) and #55257 (the anchor fix on the older proposer).
- sglang PR #51465 (At1a8/sglang branch fangyuan/pard2).
