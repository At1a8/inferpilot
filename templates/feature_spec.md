# Feature spec — <feature> on <framework> / <model> / <platform>

<!-- Phase 1 artifact (see ../knowledge/dev_methodology.md). Gate: every acceptance criterion below is
     measurable with a named suite and threshold. -->

## Source of truth

- Upstream design: <PR / paper / design doc link>, at commit <sha>
- Dependencies that are not merged yet: <PR links, commits to cherry-pick>
- Matching feature skill, if any: `knowledge/feature_skills/<id>/SKILL.md` (status: <validated / stale>)
- Scenarios the source reports: <data sets, input / output lengths, concurrency, sampling, thinking,
  hardware, and its numbers>; the port is measured on these with the user's server and client. Claim: a
  gain beyond the noise band there, the source's numbers for reference; the feature's own metric (e.g.
  accept length) within <tolerance> of the source's <value, with the prompt set and settings it was
  measured on; the reference implementation's on the same prompts when the source reports none>, judged
  in G1. With no value to compare with: <the metric is reported only, and G1 below has no criterion for
  it>.

## Feasibility

<!-- The documented range of each model the feature uses (model card, config: training context of a
     draft model, max_position_embeddings, checkpoint precision) and the range the source was tested
     on. A scenario outside the source's, the task's own workload included, is a non-goal unless the
     goal names it: say why here and list it under Non-goals. A task whose client does not run the
     source's scenarios stops at Spec and asks the user (`roles/director.md`, PHASE=spec). -->

## Target

| Item | Value |
|---|---|
| framework + commit | |
| model + checkpoint path | |
| platform / GPUs / parallelism | |
| container image | |
| user-visible switch | e.g. `--enable-<feature>`, config JSON keys |

## Goal

<!-- One paragraph: what the user gets (capability, capacity, latency) and on which workloads. -->

## Acceptance criteria

<!-- The gates are the core of a feature's profile; the full matrix (every dimension, its rule, runs and
     who confirms it) follows acceptance.md and ../knowledge/acceptance.md. -->

| Gate | Suite | Threshold (relative to baseline in the same pattern) |
|---|---|---|
| G0 feature off | | the baseline's tests pass; accuracy and throughput as the baseline's |
| G1 unit / oracle | <unit tests; for a port with a value to compare with, the source's prompt set and settings> | all cases pass, including self-check mode; for a port with a value to compare with, the feature's own metric within <tolerance> of the source's, above or below |
| G2 accuracy | gsm8k (1319); gpqa_diamond and simple_qa as well when `goal` says the change may lose accuracy | each at most 1 point below the baseline |
| G3 long context (only with `long_context` on; else `not run: not asked`) | | e.g. NIAH pass rate within baseline spread, feature engaged |
| G4 stability (only with `stability` on; else `not run: not asked`) | the run's `client_script` at <its highest concurrency, on long prompts>, run back to back for more than 48 hours (`scripts/stability.sh`) | no crash, hang, out-of-memory or failed request; hour by hour, the last 6 hours' throughput no lower and memory no higher than the range of the first 6 hours' values allows |
| G5 performance (every feature) | the run's `client_script` on <the source's scenarios, or the task's own workload> | a claimed gain: e.g. >= +X% output tok/s at equal HBM; no claimed gain, or InferPilot's client: no slower than the baseline beyond the noise band; a cost the user stated in `goal`: no more than it, on either client |

## Non-goals and unsupported combinations

<!-- Each unsupported combination gets a startup rejection with a clear message. -->

## Constraints

- Feature off ⇒ framework unchanged.
- <memory budget limits, latency limits, files/subsystems that must not change>

## Deliverables

- Branch / PR: <name>
- Tests: <unit tests, registered CI tests>
- Docs: <user docs to add>

## Time-box

<!-- Budget per phase; when an investigation exceeds it, return to the controls. -->
