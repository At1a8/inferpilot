# InferPilot, for agents other than Claude Code

You drive InferPilot when the user asks for a task: "run InferPilot ...", "use inferpilot.js with ...", or
a task in InferPilot's terms (an upstream feature to port, an AMD issue, a red CI job, a performance goal,
a scan). Claude Code runs `inferpilot.js` in its Workflow tool (README step 4). You have no such tool: you
start InferPilot's own runner, `runtime/run.mjs`, and watch the run (README step 4, *With Cursor*).

## Where it runs

InferPilot and its runs live in the task's ROCm container, in `/workspace/inferpilot` (README steps 2-3).
Inside that container (`/.dockerenv` exists) run the commands below as they are. Anywhere else, such as
the GPU host opened over Remote-SSH, run each one through `docker exec -i <container> bash -ic '...'`.
It has to be an interactive shell: only then does the container's `~/.bashrc` load the keys file and put
`claude` on PATH. Its warnings about job control are harmless.
The container is the user's: `scripts/run_docker.sh` names a new one `inferpilot_<user>_<MMDD>_<random>`
and labels it, so `docker ps --filter label=inferpilot.user=<user>` lists them (older ones carry the
user's login as their name). Ask which one when there are several, or none.

## Start a task

1. Write the task definition, the arguments of README *Running it*, to
   `/workspace/inferpilot/exp/<name>.json` with `"inferpilot_dir": "/workspace/inferpilot"`. Ask the user
   for what the mode requires and they have not said, or pass their words as `"request"` and let the
   Intake ask.
2. Start the runner so it outlives your turn, every role on the Cursor CLI (or `--backend claude`, on
   Claude Code):

   ```
   cd /workspace/inferpilot && mkdir -p exp && nohup node runtime/run.mjs inferpilot.js --backend cursor \
     --args-file exp/<name>.json --result-file exp/<name>.result.json < /dev/null > exp/<name>.log 2>&1 &
   ```

   `--backend cursor` needs `bootstrap --cursor` and a logged-in CLI (`agent status`); `--backend claude`
   needs Claude Code and Node.js (`bootstrap --cursor --claude`, or `--runner`) and Claude Code's API access
   (README step 1).
3. Tell the user where it runs (the log and the result file) and stop; look again when they ask. Once it
   has ended, do what its `next` says (*After a run*).

## Watch it

- `exp/<name>.log` numbers each phase along the mode's route (`PHASE 2/3`, with the agent calls, tokens
  and time so far), names each agent call and every tool call in it as it starts, adds a still-working line
  after five quiet minutes, ends each call with its time and tokens, and the run with an `=== END` line. The
  run directory is the newest
  `exp/<mode>_<slug>_<time>Z`.
- In the run directory, `experiment_ledger.md` holds every measurement and decision; at the end comes
  `run_report.md` or `validation_report.md`. The result file holds the final `status` and `stopped_at`.
- `exp/<name>.status.json` is rewritten every 30 s while the runner lives: its state, phase and the agent
  calls under way, each with its last line and when it came (`last_at`). An `updated` more than two
  minutes old, or `python3 scripts/run_lock.py status` saying `"runner": "gone"`, means the runner died:
  tell the user, and start the run again only when they ask (`--kill-orphans` stops what it left).
- `exp/<name>.agents/` keeps each agent call's prompt, its whole output stream and stderr; judge progress
  from what the agents produce there and in the run directory, not from the log's silence.

## Stop it or move its stop

Only when the user asks:

- to stop after another phase, or after the one now running, write `{"stop_after": "<phase>"}` or
  `{"stop_after": "now"}` to `exp/<name>.control.json`; the runner reads it at every phase boundary and
  logs the change (`[inferpilot] control: stop_after=...`, which also says when the run is past that
  phase), or `!! control file ...` when the file holds no JSON object. `""` runs to the end again.
- to stop at once, `kill <runner pid>`, the pid on the first line of `exp/<name>.log` (SIGTERM, never
  `kill -9`): the runner stops its agents and what they started, frees the machine lock and writes
  `"status": "interrupted"` to the result file.

## After a run

The result file ends with `next`, what comes after the run, the same Claude Code gets:

- tell the user `next.say`;
- put each question of `next.ask` to the user, never answering one yourself, then start the runner again
  with the same arguments plus `next.rerun_with`, its answers filled in from the user's (they merge with the
  answers already given);
- offer each of `next.start` (a scan's top tasks, the other problems of an issue's report) as a new run
  with its arguments, and start the one the user picks;
- `next.user` is what only the user does: the push, a reply to post, a daily report the director could
  not share.

The runner's log ends with the same steps (`=== NEXT: ...`), and it writes the arguments of each run they
name next to the arguments file: `exp/<name>.again.json`, `exp/<name>.start-<rank>.json`.

## Rules

- Never edit the files of a run directory to change its task. A change of goal, workload, GPUs or gates
  comes from the user and goes in new arguments; the run's agents reject a goal changed behind them. The
  control file moves only where the run stops.
- One task per machine (`python3 scripts/run_lock.py status`), and but for a scan no other job on its
  GPUs: check them by HIP number before a run (`python3 scripts/gpu_status.py`; rocm-smi's Device numbers
  can differ).
- Never print or write keys. InferPilot never pushes and never posts upstream: the push command is the
  user's.
