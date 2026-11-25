# Environment hygiene — host, container, servers, disk

Most framework work runs in a container that bind-mounts the source tree from the host. The container
usually runs as root; the host checkout belongs to a user. That split causes most environment
accidents.

## Git: as root in the container, give the files back

InferPilot runs git inside the container, as root. What it works in inside the container (InferPilot and the run
directories in `/workspace`, the image's `/sgl-workspace/sglang`) is root's; a checkout on a host mount
(a `framework_dir` in a mounted data directory such as `/data`, or in one added with `EXTRA_MOUNTS`)
belongs to a host user, and the rules below are about those.

- Any git write as root (worktree add, fetch, commit, cherry-pick, rebase, `pre-commit install`) leaves
  root-owned files in `.git` (refs, objects, `ORIG_HEAD`, hooks) and in the worktree. The next host-side
  git operation fails with `cannot lock ref ... Permission denied` — and a fast-forward can stop
  **after** updating the working tree and index but **before** moving the branch ref, leaving hundreds
  of staged changes on an old HEAD.
- So after every git write, and before handing over a push command, give them back:
  `<inferpilot_dir>/scripts/restore_owner.sh <run_dir> <worktree>/.git`, plus the `.git` of any other host
  checkout you fetched into (each path goes to the owner of its closest ancestor
  that is not root's; nothing happens when not root, in the container's `/workspace`, or for the image's
  own checkout).
- Detect leftovers: `find <repo>/.git ! -user "$(stat -c %U <repo>)" | head`.
- An update interrupted by a permission error: restore ownership, finish the ref update
  (`git update-ref refs/heads/<branch> <new> <old>`), then confirm `git status` is clean.
- git refuses a repository another user owns ("dubious ownership"); the container setup trusts the
  mounted checkouts (`safe.directory '*'`). Python runs there with `PYTHONDONTWRITEBYTECODE=1`, so
  imports leave no root-owned `__pycache__` in the checkouts.
- `pre-commit install` inside the container writes a hook that calls the container's interpreter; a
  commit from the host then fails with "`pre-commit` not found". Commit inside the container.
- Do not run git or pre-commit from two places at once on the same checkout; formatters rewrite files
  in place and the two runs interleave.
- Trial merges and lint runs go in a throwaway **worktree** (`git worktree add`), never in the checkout a
  running server imports from; give it back like any other write, remove it with `git worktree remove`,
  then `git worktree prune`.
- Agents usually have no push credentials. Prepare the commit, then hand the user the exact `git push`.

## The code under test

- The image provides the runtime — PyTorch, aiter, sgl-kernel — and the framework, installed in
  editable mode from the image's checkout (`/sgl-workspace/sglang` in `rocm/sgl-dev`): a plain
  `python -m sglang.launch_server` runs whatever is checked out there.
- That checkout has the whole history and is InferPilot's default `framework_dir`, and each task works in it
  on its own branch. The image leaves it detached at the commit it built, with the build's changes to
  the packaging files (`python/pyproject.toml` becomes the ROCm one). `scripts/run_docker.sh` (README
  step 2) discards those, checks out main and pulls when it creates a container from the daily image; a
  container from a given image keeps them, and one entered again keeps its code as it was left. Never
  `pip install -e` from it afterwards: its `pyproject.toml` is upstream's, which would pull CUDA packages
  into the ROCm environment.
- The checkout is the container's, and a task's branch and commits stay in it; the PR engineer writes
  them to `RUN_DIR/patches`, so the change can leave the container with the run directory (README step
  5). Before that, the implementer keeps `RUN_DIR/branch.bundle` in step with the branch after every
  commit and rollback. Run directories are the container's too, in `<inferpilot_dir>/exp`: where the
  container can go away mid-run (a preemptible node, a job's time limit), put `exp_root` on a host
  mount, and a retry in a new container takes the commits from the bundle instead of writing them
  again. A task starts only from a clean checkout: changes left there stop Setup and are never
  discarded.
- Run every server, test and benchmark from the tree under test, first on `PYTHONPATH` (sglang:
  `PYTHONPATH=<tree>/python`), and check it once per launch script:
  `python -c "import sglang; print(sglang.__file__)"` must print a path inside that tree. The feature
  arm runs the worktree on the task branch; the baseline arm runs with the feature off, or at the base
  commit from a worktree of the same repository (`RUN_DIR/base`), since the checkout cannot be on two
  commits at once. A scan reads the current main from one more such worktree (`RUN_DIR/main`, removed
  at the end), whatever the checkout is on.
- Tasks test the newest sglang: the task's branch starts from the current upstream main and, unless the user
  named an image (`image`), the image is the newest daily build for its ROCm and GPU
  (`scripts/latest_image.py`, which `scripts/run_docker.sh` starts a new container from; the `miles`
  images are for reinforcement learning). A container keeps its image, so Setup checks it first
  (`latest_image.py --compare`) and an older or different one stops the run, before anything exists,
  until the user answers `image_ok`. Compiled packages too old for the worktree fail at import or startup: that is
  an environment failure that names the newer image, not a framework bug to fix.

## Servers

- One launch script with a mode switch and a tag; one log per launch, made by
  `mktemp RUN_DIR/logs/<tag>_<time>_XXXXXX.log` with `<time>` from `date -u +%Y%m%d-%H%M%SZ` (container
  logs are often UTC, the host may be another zone; the Z says which): a launch in the same second as
  another, a retry's among them, still gets a log of its own, and its own `<log>.pid` and `<log>.exit`.
- A server, or any run longer than one tool call (a benchmark, a driver of several runs), starts detached
  from the agent's shell. It runs a bash script, the script's own arguments after it (`gsm8k` for
  `<accuracy_script> gsm8k`): the launch script, or one written for the run with a quoted heredoc
  (`cat > <script> <<'EOF'`, which keeps the command's own quotes) when the command is not a bash script
  or needs more than arguments (a Python suite, its environment). The wrapper writes its own PID, and the
  script's exit status when it ends:
  `setsid nohup bash -c 'log=$1; shift; echo $$ > "$log.pid"; bash "$@" > "$log" 2>&1; echo $? > "$log.exit"' _ <log> <script> [<arg>...] > /dev/null 2>&1 < /dev/null &`.
  The run's PID is the one in `<log>.pid`, once the file has it, not `$!`: in a shell with job control
  `setsid` forks, and `$!` is then a parent that is gone at once. The wrapper leads its own process
  group. The script runs in a shell of its own, so an `exit`, an `exec` or `set -e` in it ends that
  shell and the status is still written. An agent CLI may stop the background jobs of its shell when a
  tool call or the agent's call ends, and a server stopped that way leaves a log that ends in the middle
  of startup with no error. Wait for it in the same call: poll its health endpoint, its log or result
  file and whether the wrapper still runs, `grep -qs '^State:[[:space:]]*[^ZX[:space:]]' /proc/<pid>/status`,
  each poll a short tool call, until it is up or done. Not `kill -0 <pid>`: it succeeds for a wrapper
  that has exited and that nobody has reaped, which a container without an init keeps as a zombie. A
  run is done when `<log>.exit` has its status, and passed only when that is 0: a later tool shell
  cannot `wait` for it, and a run can write its results and still fail (`templates/harness/perf.sh`
  can). A wrapper that is gone with no status in `<log>.exit`, read once it is gone, was stopped with
  its run (its group killed, or every process of the container): the run did not pass. A server has no
  status until it is stopped: it is waited for until it is up, and stopping it once the checks that use
  it are done fails nothing. Before the call that started it returns, stop it from its launch record
  (Killing, below) and wait until that check says it is gone: the next call works on these GPUs and in
  this worktree.
- Poll the health endpoint before using a server; read the effective configuration from the
  `server_args=` log line.
- Know the process name you are looking for: `sglang serve ...` and `python -m sglang.launch_server`
  differ. Probe the health endpoint rather than trusting a `pgrep` miss.
- Killing: `pkill -f` patterns inside `bash -lc "..."` match the shell itself; use `'[s]glang serve'`
  style patterns. Better, record the server's identity at launch (its PID, `<log>.pid` for one started
  detached, and whether it leads its own process group) and kill from that record: the PID and its
  descendants (`setsid` workers keep VRAM otherwise), the whole group only when the launch proved the
  group is the server's. Never look the group up at kill time: a recycled PID belongs to a stranger,
  and a teardown that did this once TERMed a container's PID 1.
- Every server, test and benchmark runs with `HIP_VISIBLE_DEVICES=<gpu_ids>`, the set Setup pinned;
  without it, tensor-parallel ranks land on GPUs 0..N-1 whoever is using them.
- Keep one server per GPU set; a previous server still holding the GPUs looks like an out-of-memory bug.
  Stop it only if this run started it (its recorded PID); a GPU busy with anything else is somebody's
  work — wait, or ask Setup for other GPUs, never kill it or reset the GPU (`guardrails.md` §3).
- Before the servers of every performance measurement start (the baseline's, a profile's, each A/B
  round's), this run's earlier servers stopped by their recorded PIDs:
  `python3 <inferpilot_dir>/scripts/gpu_status.py --check all`, where the servers run. Never while a server
  of this run is up: its memory and load count like anyone's, and the pids the script names are host pids,
  which do not name this run's processes inside a container. A GPU in use anywhere on the machine (its
  VRAM% or GPU% at 5 or more, as rocm-smi shows them) moves the numbers, not only one of the run's GPUs:
  that measurement is not run, reported at once with each such GPU, its VRAM%, GPU% and pids. A number
  measured next to somebody else's job is not a measurement. GPUs are numbered as HIP numbers them, in KFD
  node order; rocm-smi's Device numbers can differ.
- Running a test that needs a GPU the server occupies: stop the server first.

## Disk and tools

- GPU memory faults leave core dumps (`gpucore.<pid>.gpu`, tens of GB each) in the working directory.
  Check after any fault and delete them once the fault is understood.
- Containers may lack host tools (e.g. `rg`); scripts that run inside the container should use `grep`.
- Generated build outputs (e.g. hipified `*.hip` files from JIT builds) show up as untracked files; never
  `git add -A`.

## Code you edit while a server runs

An editable install imports from the checkout. Edits do not affect a running server's already-imported
modules, but JIT kernels compile from the source files at first use and future launches pick everything
up. Restart servers after edits you intend to test, and record which commit each log ran.
