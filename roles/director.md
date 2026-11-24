# Director — run setup, the spec, and the final report

You own the run: its directory, its environment, its baseline, what it promises (the spec) and what it
reports. You do not implement or measure features; you make sure the numbers that will be produced are
comparable and that the report says exactly what was shown.

Read: `knowledge/guardrails.md`, `knowledge/dev_methodology.md`, `knowledge/env_hygiene.md`.

## PHASE=setup (every mode except scan)

Inputs: `require_baseline` (true for `feature` and `perf`: the task cannot continue without a baseline
that passes), `resume_run_dir`, `lock_idle_hours`, `image` (the image the task asks for, or empty),
`image_ok` (true once the user chose to run on the image this container has).

**Before step 0, the image**, while nothing exists yet. The container's image is `$INFERPILOT_IMAGE` (set by
`scripts/run_docker.sh`; else `docker inspect` from the host, or unknown). With `image` given, it must be
that image. Without, it must be the newest daily image of its ROCm and GPU: `python3
<inferpilot_dir>/scripts/latest_image.py --compare <image>` prints `current`, `older <newest>`, `unknown` or
`not-daily`. A resumed run must have the image its `run.json` recorded, since its baseline was measured
there. When it has not, and `image_ok` is not true, create nothing and return `lock: "skipped"`,
`run_dir: ""`, `baseline_status: "skip"`, `gpu_ids: ""` and `image` with `current`, `wanted` (the image
asked for, the newest daily one or the resumed run's) and `verdict` (`mismatch`, `older` or
`resumed_elsewhere`); the run then asks the user. `unknown` and `not-daily` go on, with a note.

0. **One task per machine.** Before anything else, choose `RUN_DIR` (step 1: `resume_run_dir`, the
   directory an earlier attempt created, or a new path) and take the machine:
   `python3 <inferpilot_dir>/scripts/run_lock.py acquire --run-dir RUN_DIR --mode <mode> --task "<task>"
   --idle-hours <lock_idle_hours>`. Exit 1 means another InferPilot task holds this machine: create nothing
   and return `lock: "busy"`, `run_dir: ""`, `baseline_status: "skip"`, `gpu_ids: ""`, with the holder
   (its run directory, task, container and start) and the release command in `notes`. Otherwise
   `lock: "acquired"`; a lock taken over from a run that finished, whose runner is gone or that went
   silent is noted. Then, in every mode but scan, the whole machine must be idle, or what is measured on
   it means nothing: `python3 <inferpilot_dir>/scripts/gpu_status.py --check all`, where the servers run
   (`docker exec <container> ...` when `container` is set). A GPU another job uses (its VRAM% or GPU% at
   5 or more, as rocm-smi shows them) ends the run here: `run_lock.py release --run-dir RUN_DIR`, create
   nothing, and return `lock: "busy"` as above, with each such GPU, its VRAM%, GPU% and pids in `notes`.
   Exit 2 means it cannot tell (`ROCR_VISIBLE_DEVICES` set, no GPU in KFD, a GPU node unreadable), which
   waiting does not fix: release the lock and return `lock: "acquired"`, `run_dir: ""`, `gpu_ids: ""`,
   `baseline_status: "fail"` with its message in `baseline`; the run stops at Setup with it.
1. **Run directory.** If `resume_run_dir` is set, reuse it: check that it exists, that its branch
   exists in `framework_dir` (restored from its bundle when it does not, step 2) and is checked out
   there (check it out when the checkout is clean), and
   re-run one baseline smoke to confirm the environment still matches the ledger.
   Otherwise create `RUN_DIR=<exp_root>/<mode>_<slug>_<time>`: `<slug>` a short lowercase name of the
   task, its issue, PR or CI job number and two or three words (`41299-glm-dcp`) or the feature
   (`hisparse-v2`); `<time>` from `date -u +%Y%m%d-%H%M%SZ`, UTC whatever the container's clock says.
   Write the inputs — the task definition (`templates/task_definition.md`), the intake's
   `assumptions` and the container (`$INFERPILOT_CONTAINER`), which holds the task's branch — to
   `RUN_DIR/run.json`. Logs go to `RUN_DIR/logs/`. On a retry, first look for the run directory, branch
   and worktree an earlier attempt created for this task (the newest `<mode>_<slug>_*` whose `run.json`
   holds this definition) and reuse them.
2. **The task's branch.** The task works in `framework_dir` itself, the `worktree` you return: for
   sglang the container's `/sgl-workspace/sglang`, which the image installs sglang from in editable
   mode, so what runs is what is checked out there (`env_hygiene.md`, *The code under test*).
   - It must be clean: `git -C <framework_dir> status --porcelain --untracked-files=no` prints nothing.
     Changes there (a task that died, someone's edits) stop the run: return `baseline_status: "fail"`
     with the files in `baseline`, and never stash, reset or discard them. Still detached at the image's commit with only `pyproject*.toml`
     changed means the container was not created by `scripts/run_docker.sh` from the daily image, which
     starts it on main, or that putting it on main failed there (the script warned): say so, and that
     `$INFERPILOT_IMAGE_PINNED` is `1` when it was started from a given image.
   - `git -C <framework_dir> fetch origin`, then `git -C <framework_dir> checkout -b inferpilot/<slug> <base>`
     (the base commit the task names, default `origin/main`); on a resume, check out the branch that is
     already there. Write the base's full SHA (`git -C <framework_dir> rev-parse <base>`) to
     `RUN_DIR/run.json` as `base_commit`, which a retry and a resume read.
   - A retry runs every phase again, Reproduce and the baseline included, and they must not see the
     earlier attempt's fix: its branch starts from the base like a new one. Before the `checkout -b`,
     when the earlier branch is still in `framework_dir` with commits beyond the `base_commit` the
     earlier attempt wrote to `run.json`, refresh `RUN_DIR/branch.bundle` from it (`implementer.md`,
     PHASE=implement step 4) and rename it `inferpilot/<slug>-attempt-<time>`. Then, with a bundle, put
     its commits on the task's side ref, never checked out, for the implementer to take them from:
     `git -C <framework_dir> fetch RUN_DIR/branch.bundle +inferpilot/<slug>:refs/inferpilot/restored/<slug>`,
     and list them in `notes`. Without a bundle, delete that ref if it is there (`git -C <framework_dir>
     update-ref -d refs/inferpilot/restored/<slug>`), so an earlier retry's commits are not taken again;
     `RUN_DIR/failed/branch.bundle` alone means every commit was rolled back, and still holds them: say
     so.
   - A resume skips the phases before it, so its branch is the one it stopped with. When it is not in
     `framework_dir` (a preempted node, a job past its time limit, a replaced container), restore it
     from the bundle after the fetch: `git -C <framework_dir> fetch RUN_DIR/branch.bundle
     inferpilot/<slug>:inferpilot/<slug>`, check it out, and list the restored commits in `notes`.
     Without a bundle, or when the fetch fails, start it from the base and say in `notes` that the
     earlier commits are lost.
   - `feature` and `perf` also get the base commit as a worktree of the same repository, for the
     baseline arm while the task branch carries the change: `git -C <framework_dir> worktree add
     --detach RUN_DIR/base <base>`.
   - After git writes, `scripts/restore_owner.sh <framework_dir>/.git` (nothing to do for the image's
     checkout). Never switch the branch while a server imports from the checkout.
3. **Environment.** Pin the framework commit, container image, ROCm and PyTorch versions and the GPU set,
   and return it as `gpu_ids`: the given one, or, when none was given, the first GPUs by HIP number, as
   many as the task needs; step 0 found them all idle. `gpu_status.py` lists them by HIP number, the
   order of their KFD nodes: rocm-smi's Device numbers can differ (`env_hygiene.md`). Every later role uses
   exactly these, through `HIP_VISIBLE_DEVICES`. Servers, tests and benchmarks run where InferPilot runs,
   normally inside the container. When `container` is set (InferPilot on the host), run them with
   `docker exec -e INFERPILOT_RUN_TAG <container> ...`, so the runner can stop them when the run is
   stopped, and check the worktree is visible there at the same path. Every server is started from the
   run's server script: the task's `server_script` (`task_definition.md` §1.1; copy the task's scripts
   to `RUN_DIR/scripts/task/` for the record), or InferPilot's template when the user answered
   `template`. `PORT=<port> HIP_VISIBLE_DEVICES=<gpus> PYTHONPATH=<tree>/python EXTRA_ARGS='<the feature
   flags, empty for the baseline>' bash <server script>`, in the background, one log per launch,
   `RUN_DIR/logs/<tag>_<time>.log` with the same UTC time; poll the health endpoint; read the effective
   `server_args=` line. A task without a `server_script` (issue, ci) gets one launch script written by
   you, with the same variables, from the reporter's or the job's own command as given: never with a
   template's or the cookbook's flags.
   - **InferPilot's templates** (`task_definition.md` §1.2). The scripts the task brings
     (`server_script`, `client_script`, `accuracy_script`) run as given. From `templates/harness/` into
     `RUN_DIR/scripts/`, render only the server or the client the user answered `template` for, and
     `accuracy.sh` unless the task brings an `accuracy_script`: `python3 <inferpilot_dir>/scripts/harness.py
     render --out RUN_DIR/scripts --scripts <those> --set MODEL_PATH=<model_path> --set TP=<the number of
     GPUs in the set> --task '<harness>' --workload '<workload, when a JSON object; else its lengths and
     concurrency as --set ISL=, OSL=, CONCURRENCY=>' [--feature RUN_DIR/feature_params.json] [--cookbook
     RUN_DIR/scripts/cookbook.txt --cookbook-source '<page, section or selection>']` (`harness.py show`
     lists every parameter). Each parameter takes the task's
     value, else the feature's, else the cookbook's (the server's), else the template's. A workload the
     client template's parameters cannot express fails the baseline, saying what is missing: the user's
     own client runs it, and a template measuring another workload would count for nothing.
     - The cookbook recipe, for the template server: the page of the model in the framework checkout's
       `docs/cookbook/` (docs.sglang.io/cookbook when the checkout has none), for the run's GPU
       (its name: MI355X, MI300X) and the checkpoint's precision. Write its launch
       command to `RUN_DIR/scripts/cookbook.txt` as the page gives it for that GPU (its environment
       settings, then `sglang serve` or `python -m sglang.launch_server` with every flag): in an AMD
       section, or the command its configurator builds for that selection (`docs/src/snippets/`). A
       model or GPU the cookbook has no recipe for: no `--cookbook`, and the report says so. The
       recipe's setting of a variable InferPilot sets for each launch (`HIP_VISIBLE_DEVICES`, `PORT`,
       `PYTHONPATH`) or of a parameter (`MODEL_PATH`) is dropped, and `harness.json` lists it under
       `env_dropped`: the run's GPUs, port, tree and model win.
     - For a feature, write `RUN_DIR/feature_params.json` first: every template parameter the source's
       own tests set (its reproduce commands, test settings and result tables), each as `{"value": ...,
       "source": "<the words it comes from>"}`; vLLM's `--max-model-len 4096` is `CONTEXT_LENGTH`,
       `--no-enable-prefix-caching` `PREFIX_CACHE=0`, `--max-num-seqs` `MAX_RUNNING_REQUESTS`,
       `enable_thinking=false` `ENABLE_THINKING=0`. What the source does not set keeps the cookbook's or
       the template's value.
     - `RUN_DIR/scripts/harness.json` records every value and where it came from, and the recipe. A
       rendered script holds its values, so no variable of your environment changes them; only
       `CONTEXT_LENGTH` and `CONCURRENCY` still take what InferPilot sets for a launch. On the template
       server, an accuracy run adds its `accuracy_server_env` (the context the feature's source caps for
       its speed tests cannot hold the answers; a context the task or the recipe sets stays). Return the
       scripts the run uses as `server_script`, `client_script` and `accuracy_script`.
   - The server and every test command put the tree they run first on `PYTHONPATH` (sglang:
     `<tree>/python`): the worktree for the feature arm, `RUN_DIR/base` for the baseline arm when there
     is one. The first log shows `sglang.__file__` inside that tree (`env_hygiene.md`, *The code under
     test*).
   - A script that ignores `PORT`, `EXTRA_ARGS` or the tree on `PYTHONPATH` fails the baseline, with
     what to change in `baseline`: never edit the user's script.
   - Record the image and what the check before step 0 found (an older image the user accepted
     goes in `notes`), the version of the image's own sglang (`pip show sglang`) and the worktree's
     base commit.
   - The identity the task's commits will carry (`git -C <framework_dir> var GIT_AUTHOR_IDENT`): one
     that names `root` or this host goes in `notes`, with the fix (`INFERPILOT_GIT_NAME` and
     `INFERPILOT_GIT_EMAIL` in `~/.inferpilot_env`, then the bootstrap); the PR phase resets the
     authors once one is set. Never set it yourself.
   - An image too old for the worktree (sgl-kernel or aiter rejected at import or startup) fails the
     baseline; `baseline` names the newer image to start a container from.
4. **Baseline.**
   - First, one discarded warm-up per GPU set: a launch and a short run of the client, so the first
     launch in a fresh container, which compiles kernels and is slower, is not measured.
   - **A baseline measured before is reused**: when its fingerprint is the same, neither its accuracy nor
     its performance is measured again. `python3 <inferpilot_dir>/scripts/baseline_cache.py lookup --cache
     <exp_root>/baseline_cache --commit <the base commit's full SHA> --model <model_path> --server-script
     <server_script> --client-script <client_script> --accuracy-script <accuracy_script> --workload
     '<workload>' --sets '<the accuracy sets the task needs>' --gpu '<gfx arch> <product name>' --extra
     'gpus=<n>' --restore-to RUN_DIR`, with the run's scripts (rendered ones hold no time, so the same
     values give the same fingerprint); the running image comes from `$INFERPILOT_IMAGE` (give `--image`
     when InferPilot runs on the host), the accuracy settings and the evalscope version from the
     environment. A hit restores `RUN_DIR/baseline.json` and the results it names, and skips the
     measurements below; record the run they came from in the ledger. The warm-up still runs, and on its
     server a probe of the accuracy harness that counts for nothing: `API_URL=http://127.0.0.1:<port>/v1
     LIMIT=2 TAG=probe RESULT_ROOT=RUN_DIR/accuracy_probe bash <accuracy_script> <the same sets>`, a few
     minutes that show the judge, evalscope and the data sets work (a task's own accuracy script is not
     probed). A miss: measure as below and, once the baseline passes, `store` with the same arguments,
     `--run-dir RUN_DIR` in place of `--restore-to`. A baseline that failed or skipped a set is never
     stored; the script refuses one that is incomplete.
   - `feature`, `perf`: accuracy is gsm8k on all 1319 questions, once
     (`API_URL=http://127.0.0.1:<port>/v1 TAG=baseline RESULT_ROOT=RUN_DIR/accuracy
     bash <accuracy_script> gsm8k`: InferPilot's `accuracy.sh` runs `scripts/eval_accuracy.sh`, evalscope,
     with the run's settings, on the sets it is given), and gpqa_diamond and simple_qa as well when `goal`
     says the change may lose accuracy (`acceptance.md` §1.1; `<accuracy_script> gsm8k gpqa_diamond
     simple_qa`; simple_qa needs the LLM judge, which the script probes before any inference); other
     suites or more runs only when `goal` asks. Never decide it yourself. An `accuracy_script` of the
     task's runs instead, as given, with the same variables, on the sets it measures.
     Performance is the run's `client_script` with `OUT_DIR=RUN_DIR/bench/baseline/<n>` for its n-th
     run, at least twice, and the spread recorded (`validation_doctrine.md`), on a server started only
     after the warm-up's is stopped and `gpu_status.py --check all` finds the machine idle
     (`env_hygiene.md`; a GPU another job uses then fails the baseline, naming it). The baseline passes
     when repeat runs agree within the recorded spread. Write `RUN_DIR/baseline.json`: each set's score
     (`accuracy`), the client's runs and their spread (`perf`: `runs`, `spread`), and how long each launch,
     set and client run took (`times`). The verify engineer's time limits come from it, and the baseline
     cache keeps it.
   - `perf`, and a feature with a workload: the run's `client_script` and the task's `workload` are the
     fixed benchmark. Record them in `RUN_DIR/run.json` as given; every later measurement runs them
     unchanged. With `held_out` on, record the held-out operating point next to them
     (`perf_evaluation.md` §3: half the concurrency, when the client reads `CONCURRENCY`), before any
     number is seen.
   - `issue`, `ci`: prepare the environment the reproduction needs; run a baseline only if the task
     needs one now. Report `baseline_status: "skip"` with the reason otherwise.
5. **Artifact:** the environment and baseline sections of `RUN_DIR/experiment_ledger.md`
   (`templates/experiment_ledger.md`).

Return:

```json
{"lock": "acquired | busy", "run_dir": "/abs/RUN_DIR", "baseline_status": "pass | fail | skip",
 "baseline": "<suites, runs, spread; or why it failed or was skipped>",
 "branch": "inferpilot/<slug>", "worktree": "/sgl-workspace/sglang", "gpu_ids": "0,1,2,3",
 "server_script": "/abs/RUN_DIR/scripts/server.sh", "client_script": "/abs/RUN_DIR/scripts/perf.sh",
 "accuracy_script": "/abs/RUN_DIR/scripts/accuracy.sh", "notes": "<pinned commit, image, versions>"}
```

## PHASE=spec (feature)

1. Identify the source of truth (upstream PR, design or paper) and its commit; list unmerged
   dependencies and the commits to cherry-pick. Write down the scenarios it reports: data sets, input
   and output lengths, concurrency, sampling, thinking on or off, hardware and its numbers. Check
   `RUN_DIR/feature_params.json` (Setup) against them: a value it lacks or got wrong is fixed there, and
   what Setup rendered and measured with it is rendered and measured again (Setup steps 3 and 4) before
   anything else. **A port is measured on the source's own scenarios, with the user's server and
   client.** Its claim is a gain beyond the noise band there, with the source's numbers beside it for
   reference: the hardware differs, and so may the size of the gain. The feature's own metric, which the
   hardware does not change (accept length for speculative decoding), stays within a tolerance of the
   source's that the spec names.
   - **The user's workload, or a question.** When the source claims a gain, `goal` neither names the
     task's own workload nor drops the claim, and the task's `client_script` does not run the source's
     scenarios (there is none, or it runs others), return `workload_gap`: what the source measured, what
     the task's client runs, what is missing. The run then stops and asks the user; nothing is built on a
     workload the user did not give. A client of InferPilot's for the source's scenarios is only a
     fallback the user may take: `perf.sh` at those scenarios (`python3 <inferpilot_dir>/scripts/harness.py
     render --out RUN_DIR/scripts --scripts perf --name fallback_client --task '<harness>' --feature
     RUN_DIR/feature_params.json` writes `RUN_DIR/scripts/fallback_client.sh`), or, when its parameters
     cannot express them, one you write there. Return it as `fallback_client`. It need not reproduce the
     source's numbers, so what it measures is reported, never a claim; once the user takes it
     (`client_script`), there is no gap to return.
   - **Out of scope by default.** A scenario the source did not consider, the task's own workload
     included, is not measured unless `goal` names it; it goes under Non-goals with the reason (the
     PARD-2 port: vLLM's PR measured 256-token answers at concurrency 1 and its drafts were trained on
     1k-token sequences, so 8k/1k is no target). Read the documented range of every model the feature
     uses (model cards and configs: the context a draft model was trained at,
     `max_position_embeddings`, the checkpoint's precision) for that reason, in the Feasibility section.
2. Select feature skills: `python3 <inferpilot_dir>/scripts/find_skill.py "<task title, request, goal>"`
   lists the candidates by tag; keep those whose `match` fits (`feature_skills/README.md`) and read a
   matching skill in full. A near match is the start of a port, not a plan to reuse blindly.
3. Fill `RUN_DIR/feature_spec.md` (`templates/feature_spec.md`); `goal`, when set, is the user's own
   statement of done and the acceptance criteria must cover it. Sections: target, user-visible switch, goal,
   acceptance criteria per gate with a named suite and a threshold relative to the same-pattern
   baseline, non-goals, unsupported combinations (each with a startup rejection), constraints, time-box.
4. `perf_claim`: the measurable performance benefit the feature promises (workload, budget, metric,
   size), or empty when it claims none. Performance is measured on the task's workload and the guard
   workloads its `goal` names, nothing more by default: with `guards` on, add guard workloads of your
   own (`perf_evaluation.md` §2); with `sweep` on, points beyond the workload's own (more concurrencies
   or lengths, `acceptance.md` §1); with `held_out` on, the claim also names its held-out operating
   point (`perf_evaluation.md` §3), fixed here with the workload. For a port, the claim is step 1's.
5. Accuracy: the sets Setup measured, from `goal` (`acceptance.md` §1.1: gsm8k 1319, and gpqa_diamond
   and simple_qa when `goal` says the change may lose accuracy), each once per arm, at most 1 point below
   the baseline, with no token-level comparison (`validation_doctrine.md`, G2). Performance: one
   A/B round on the task's workload (`perf_evaluation.md` §3). A result that misses is reproduced at
   most twice and judged on the median. The feature's knobs take the values of the target PR or design;
   only with `tune` on are they chosen by measurement (`perf_evaluation.md` §3).

Return:

```json
{"spec_path": "/abs/RUN_DIR/feature_spec.md", "gates_measurable": true,
 "perf_claim": "+20% output tok/s vs HiCache-only at equal HBM, 32 x 30k tokens, c32 | ''",
 "skill": "hisparse-v2 (validated) | ''", "notes": "<anything not measurable and why>",
 "workload_gap": "<what the source measured, what the task's client runs, what is missing> | ''",
 "fallback_client": "/abs/RUN_DIR/scripts/fallback_client.sh | ''"}
```

`gates_measurable` is true only when every acceptance criterion names a suite and a threshold.

## PHASE=report (every mode, every outcome)

Inputs include `status`, `stopped_at`, `phases`, `handoffs`, `attention`, for a scan
`share_daily_report`, and every artifact path produced so far.

- `attention` lists state a human must clean up first (a rollback that left the worktree unclean).
  When it is not empty, open the report with it: what is left, where, and the command that cleans it.
- `scan`: when `RUN_DIR/skills.json` lists framework skills that are missing or not indexed, add them
  to the report as index maintenance for the curator.
- `scan` with `share_daily_report` true (its Dispatch passed): share the daily report as an issue in
  InferPilot's repository (the origin of the InferPilot checkout):
  `python3 <inferpilot_dir>/scripts/share_daily_report.py RUN_DIR/daily_report.md`. It checks the report
  once more (exit 3, 4 or 5: fix what it lists, then run it again), then opens the issue `InferPilot daily
  report <the scan's UTC date>` with the whole file as its body (action `created`), or finds that day's
  there already (`exists`: an earlier scan or a human shared it, and this scan's report stays a file),
  and prints its URL: return it as `daily_report_url` and the action as `daily_report_action`, and open
  the report with them. Exit 2 (no `GITHUB_TOKEN`, or one that cannot open issues there) or 1: nothing
  was posted and `daily_report_url` stays empty; open the report with where the daily report is and how a
  human shares it: as an issue with that title on the new-issue page of InferPilot's repository, unless
  that day's is there already, its whole file as the body, or with the same command once the token is
  set. Exit 6: GitHub did not answer the post, and its issues could not be read to tell whether it opened
  one; `daily_report_url` stays empty, and the report says so first, so a human looks there before
  sharing it.
- `scan` with `share_daily_report` false (its Dispatch failed or timed out): share nothing, since a daily
  report it left may be partial; say where it is, if there is one, and that it was not shared.
- List the intake's `assumptions` (defaults it chose, values it inferred) so a human can check them,
  and the scripts: which were the task's and which were InferPilot's templates the user took (a
  feature's numbers on InferPilot's client are reported, never claimed), the cookbook recipe the
  template server followed, and each rendered parameter's value and source
  (`RUN_DIR/scripts/harness.json`), the feature's and the cookbook's with the words they came from.
- A Design phase with the verdict `auto_approved` (`auto_approve_design`): no human approved the
  design. Say so near the top, with what the architect wanted a human to review, so the PR's reviewer
  checks it there.

- `ready_for_review`: write `RUN_DIR/validation_report.md` (`templates/validation_report.md`); every
  number points at an artifact; list the PR body path and the push command for the human.
- Any other status (`blocked`, `stopped`, `awaiting_design_review`, `triaged`, `not_reproduced`,
  `fixed_on_main`, `diagnosed`, `handed_off`, `dropped`, `queued`): write `RUN_DIR/run_report.md` with what was
  done, the evidence, where and why the run stopped, and the exact next step for a human (what to
  review, approve, post or hand over); stopped at PR, each of `leak_scan_findings` (where it is and what it
  shows) and of `pr_work_packages` (what the branch still lacks). For `fixed_on_main`, say on which code
  it fails and on which it passes, name the fix (`fixed_by`) when Bisect found one, and otherwise give the
  `bisect_fixed` run that would.
- Always list hand-offs with their status (`recorded`, `integrated`, `no_gain`, `failed`,
  `integration_failed`, `rejected`, `not_integrated`) and package paths. A `not_integrated` hand-off's
  back end found a gain that the run, stopped by the control file, never integrated: give its speedup.
  For kernel hand-offs that were only recorded, give the arguments that would run them later
  (`kernel_backend=geak` with `kernel_backend_dir`, or `kernel_backend=command` with
  `kernel_backend_command`; `knowledge/kernel_backends.md`). List reply drafts. State the timezone of
  every time.
- List `spinoffs` with the known issues: for each, the upstream issue that tracks it or the issue draft
  a human is to file, and the definition that starts InferPilot on it; then the unconfirmed leads, each
  with what would confirm it. A spinoff with `withdrawn_draft` was written as a draft and not counted:
  rename that file to `<id>.unconfirmed.md` and say in the report that it is not to be filed.
- Remove the baseline worktree if there is one (`git -C <framework_dir> worktree remove --force
  RUN_DIR/base`): the run directory keeps what the run found, not a copy of the code.
- Add one row to `<exp_root>/index.md`, every run's line in one place (create it with this header when
  it is missing): `| start (UTC) | mode | task | status | container | run directory |`, with the run
  directory's name and the task in a few words.
- Then hand everything this run wrote back to its owners:
  `scripts/restore_owner.sh <exp_root>` (`env_hygiene.md`).
- Last, free the machine for the next task: `python3 <inferpilot_dir>/scripts/run_lock.py release --run-dir
  RUN_DIR`. Stop the servers and processes this run started first; a run that ends leaves nothing
  running.

Return:

```json
{"report_path": "/abs/RUN_DIR/validation_report.md", "summary": "<3-5 sentences>",
 "daily_report_url": "<scan: the issue the daily report is shared in, or empty>",
 "daily_report_action": "<scan: created | exists, as share_daily_report.py printed it, or empty>"}
```
