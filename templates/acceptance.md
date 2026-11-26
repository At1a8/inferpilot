# Acceptance matrix — <task>

<!-- Fixed before the work starts, and not changed after results are seen (../knowledge/acceptance.md
     §5). One row per item; the profile's required items come first. The Result and Evidence columns
     are filled at the end. -->

| | |
|---|---|
| Task | <mode, title, link> |
| Profile | upstream feature / issue fix / CI fix / performance / requirement |
| Requirement owner | <name; only for a requirement-driven task> |
| Baseline | <image, commit, launch line; only the switch differs> |
| Fixed on | <date and time, with the time zone>, by <who> |

## Items

| ID | Dimension | Metric | Workload / data set | Rule | Runs / statistics | Blocking | Confirmed by | Result | Evidence |
|---|---|---|---|---|---|---|---|---|---|
| COR-1 | correctness (a change with a switch) | G0 feature off | the baseline suites, switch off | the baseline's tests pass; accuracy and throughput as the baseline's | 1 | yes | InferPilot | | |
| ACC-1 | accuracy | gsm8k, 1319 questions | `scripts/eval_accuracy.sh` (evalscope), <sampling> | at most 1 point below the baseline | 1 run per arm; a miss is reproduced at most twice, judged on the median | yes | InferPilot | | |
| ACC-2 | accuracy (when `goal` says the change may lose accuracy) | gpqa_diamond, 198 questions | `scripts/eval_accuracy.sh gpqa_diamond` (evalscope), <sampling> | at most 1 point below the baseline | as ACC-1 | yes | InferPilot | | |
| ACC-3 | accuracy (when `goal` says the change may lose accuracy) | simple_qa, 4326 questions, LLM-judged | `scripts/eval_accuracy.sh simple_qa` (evalscope), <sampling> | at most 1 point below the baseline | as ACC-1 | yes | InferPilot | | |
| PRF-1 | performance (gain) | total tok/s | the task's `client_script` (<lengths, concurrency>), budget | a gain on the median of the rounds, at least +<x>% when the goal states one (also at the held-out point, with `held_out` on) | one A/B round; one with no gain is reproduced at most twice | yes | InferPilot | | |
| PRF-2 | performance (guard) | total tok/s, mean TPOT | <a guard workload the goal names, or one of InferPilot's with `guards` on; no row when there is none> | within the noise band | as PRF-1 | yes | InferPilot | | |
| REQ-1 | requirement | <the requester's metric> | <the requester's workload or data set> | <the requester's target> | <as agreed> | yes | requirement owner | | |

## Waivers

| ID | Reason | Waived by | When |
|---|---|---|---|

## Sign-off

| Role | Name | Date | Decision |
|---|---|---|---|
| InferPilot (automatic items) | | | |
| Requirement owner | | | |
