# InferPilot

**English** | [简体中文](README.zh-CN.md)

InferPilot is the framework agent for AMD GPUs in open-source LLM inference frameworks, starting with
sglang. It turns one task — an upstream feature to bring to AMD, an AMD user's issue, a red AMD CI
job, a system-level performance gap — into a pull request a maintainer can review: **correct first**
(independent unit gates, accuracy / long-context / stress gates against a same-pattern baseline, a
controlled investigation loop), **then fast** (equal-budget A/B), within the community rules in
[`knowledge/guardrails.md`](knowledge/guardrails.md). InferPilot proposes; people review, push and merge.

Why it exists and how it maps onto the agreed design: [`docs/design.md`](docs/design.md).

## Modes

| Mode | Task | Phases | Ends with |
|---|---|---|---|
| `feature` | an upstream PR or design to bring to AMD | Setup → Spec → Recon → Design → Implement → (Kernel) → Verify → Performance → Review → PR → (Spinoff) → Report | branch + PR body + validation report |
| `issue` | an AMD user issue | Setup → Triage → Reproduce → Diagnose (skipped for an existing fix PR that passes) → Implement → (Kernel) → Verify → Performance (skipped for a fix to tests, documentation or CI files alone) → Review → PR → (Spinoff) → Reply → Report | fix PR, or a conclusion (triaged, not reproduced, fixed on main, diagnosed); always a reply draft, rewritten from how the run ended |
| `ci` | a red AMD CI job or Failure Tracker cluster | Setup → Classify → Bisect (skipped for an existing fix PR that passes) → Implement → (Kernel) → Verify → Performance (skipped as for an issue) → Review → PR → (Spinoff) → Report | fix PR; a handover for flaky/infrastructure failures, or for a lane this machine cannot be; nothing to fix when main passes on the lane's own image |
| `perf` | a system-level performance goal for a model and a fixed workload | Setup → Profile → Plan → Implement → (Kernel) → Verify → Performance → Review → PR → (Spinoff) → Report | PR with a measured win, or the change dropped; kernel-level items go to the kernel back end |
| `scan` | none — the day's state of the framework: the AMD CI on main, open AMD user issues, upstream changes AMD needs to follow (platform gates and host-sync leads on request) | Collect → Dispatch → Report | the daily report, shared as an issue in InferPilot's repository (else a file for you to share), with the ranked tasks and the definition that starts each |

Phases in parentheses run only when they apply: Kernel when there is a kernel hand-off and
`kernel_backend` is not `none`. Performance measures the fix of an issue or of a CI failure when it
changes code the server runs (G5: no slower than the base); a fix to tests, documentation or CI files
alone skips it, unless the problem is itself one of performance (an issue triaged as `performance`,
a CI failure on a number its test measures): its number must come back, whatever the fix changes. An
issue whose reporter's code fails while main passes ends as *fixed on main*; with `bisect_fixed` a
Bisect phase first finds the commit that fixed it. An issue or a CI failure that an open pull request
already fixes skips Diagnose or Bisect: the run verifies that pull request and drafts a comment for it.
It runs that pull request's code only on your word (`apply_prs`), and asks when the task does not say.
Where the pull request does not pass, the comment says so, and the run goes on to Diagnose or Bisect
and fixes the failure itself.
Spinoff runs when the run found problems that are not its own: a failure the base commit shows without
the change, or a lead a role met on the way (a bug in code it read, a broken test or image). For each
failure the base commit shows, it gives the upstream issue that tracks it, or an issue draft for you to
file. Leads are listed with how to reproduce them; with `confirm_leads` it first checks each on the
base commit and drafts those that show there.

A task starts from a **task definition** ([`templates/task_definition.md`](templates/task_definition.md)):
the fields `inferpilot.js` takes, the same names every role reads. It can be given as fields, or as a
request in plain words: then an Intake phase runs first and turns the request into a definition,
checking what it can without changing anything (the checkout, the model on this machine, the GPUs,
the issue). A definition that still lacks something its mode needs — the issue, the checkout, the
model for `feature` and `perf` and for an issue or CI fix, the fixed workload for `perf` — starts
nothing and comes back as questions, one per field, each with options or a default. A complete
definition skips the Intake.

Every implementation runs one work package at a time; an independent test engineer re-runs its unit
test, checks that the test fails without the change, and runs the feature-off check; a package that
keeps failing is rolled back, while one whose gate has no result yet is finished by one more call and
otherwise stops for a human, never rolled back. Verification failures go to an investigator that runs the controls
before any instrumentation and returns a fix, a proof that the failure is pre-existing on the
baseline, or an inconclusive verdict that stops the task. Before the PR, a reviewer reads the diff and
its evidence the way a maintainer would, starting from a catalog of known reward hacks — changes that
move a number while breaking what it meant ([`knowledge/reward_hacks.md`](knowledge/reward_hacks.md));
requested changes, and a test for every confirmed hack, become work packages that go through
Implement and Verify again. Performance numbers count only on the workload fixed at Setup and on the
serving path the task targets; when the task asks (`held_out`), a gain counts only if it also holds at
a held-out operating point fixed with the workload ([`knowledge/perf_evaluation.md`](knowledge/perf_evaluation.md) §3).

Kernel-level work — tuning a kernel InferPilot wrote for a feature, or a kernel-level performance
opportunity — is handed to a **kernel workflow back end** in the Kernel phase between Implement and
Verify ([`knowledge/kernel_backends.md`](knowledge/kernel_backends.md)):

| `kernel_backend` | What happens to a hand-off |
|---|---|
| `none` (default) | recorded in the report with its package; nothing runs |
| `geak` | an interface to GEAK: the kernel lane of the GEAK checkout given as `kernel_backend_dir` runs it, as an outside program; InferPilot ships nothing of GEAK |
| `command` | a kernel agent's CLI, given as `kernel_backend_command`, runs it |

A gain comes back as a work package: the implementer integrates it and InferPilot's own gates judge it. A
back end that fails or finds nothing never blocks the task. An integrated kernel that then fails
Verify goes back to the functional kernel and is handed to the back end once more, with the failing
case added to its tests; it is integrated again only if it passes.

## Roles

Each phase calls a role: an agent that reads `roles/<role>.md`, does its part and returns structured
JSON; `inferpilot.js` holds the schemas and branches only on their required fields. Four rules hold across
the roles:

- only the **implementer** edits framework source, and only on the task's branch;
- the roles that measure (test engineer, verify engineer, performance engineer), the intake and the
  reviewer never edit framework source; the measuring ones never reuse anyone else's numbers but run
  everything again;
- the **curator** edits only InferPilot's own `knowledge/`, and proposes the edits as a pull request to InferPilot's
  repository for you to review and merge (with a `GITHUB_TOKEN` that can write there; without one they stay
  uncommitted in the checkout);
- no role pushes or posts upstream: the PR engineer returns the exact `git push`, issue replies are
  drafts; the two posts both go to InferPilot's own repository, and only with your `GITHUB_TOKEN`: the
  curator's knowledge changes, as a pull request that you merge, and a scan's daily report, as an issue
  (when the token cannot write there, a file for you to share).

| Role | Phases | What it does |
|---|---|---|
| `intake` | Intake | turns a request in plain words, or a definition with gaps, into the task definition: decides the mode, fills fields with read-only checks, records defaults as assumptions, asks instead of guessing (a missing source of truth, model or workload, anything that would cost something to obtain), rejects what InferPilot does not do |
| `director` | Setup, Spec, Report | creates the run directory and the task's branch, pins commit, image and GPUs and runs the baseline; writes the feature spec with measurable acceptance criteria; writes the final report for every outcome, with the next step for a human |
| `scout` | Collect | gathers candidate tasks with the scripts below |
| `dispatcher` | Dispatch | sets each candidate's gap type, level, CUDA dependencies with their AMD starting points, effort and priority, and ranks them into a queue |
| `analyst` | Recon | maps every subsystem a feature needs to a file, function and hook in the target framework, and tables which assumptions of the source design hold on AMD; reads only |
| `architect` | Design, Plan | turns the recon or the profile into work packages that can each be tested alone, with invariants, memory budget, synchronization, startup rejections and a test plan; flags designs a human must review; assigns each performance opportunity to InferPilot, the kernel back end or Hyperloom |
| `issue_triager` | Triage, Reproduce, Spinoff, Reply | sets the priority and the action from the issue, its comments and linked PRs; drafts the reply; writes the reproduction that becomes the issue's gate; finds the upstream issue for each problem a run found that is not its own, or drafts one, and lists the leads (checked on the base commit with `confirm_leads`); at the end rewrites the reply from what the run showed |
| `ci_guardian` | Classify, Bisect | classifies a red job (code regression, dependency bump, test bug, flaky, infrastructure) and hands the last two over; says whether it is a performance problem, and then the number the fix must restore; finds and confirms the culprit commit and plans the fix; for an issue fixed on main, with `bisect_fixed`, the commit that fixed it |
| `implementer` | Implement | one work package at a time, smallest diff, neutral with the feature off, HIP-only or behind a switch, committed lint-clean with its tests registered; functional kernels with unit tests (tuning is a hand-off); applies existing PRs and verified kernel gains; rolls back a failed package |
| `test_engineer` | Implement | re-runs each package's unit test on its commit, checks that the test can fail (on the parent commit when the change alters behaviour, on a broken copy when it keeps it) and that behaviour with the feature off is unchanged (for a package without a switch, that the existing tests of the modules it touched pass, or fail as they do on its parent) |
| `verify_engineer` | Verify, PR | runs the correctness gates (G0, G2, composability, the mode's gate, and G3 and G4 when the task asks) on fresh servers against a same-pattern baseline, classifying and counting every failure; before the PR of a fix, runs its gate again on the latest main without the fix, and a failure main no longer shows gets no PR |
| `investigator` | Investigate, Diagnose | runs the controls first, then one-mechanism-at-a-time experiments with a hypothesis ledger; returns fix work packages, a known issue the baseline shares, an inconclusive verdict, or (for an issue) a root cause outside the AMD side; probes only behind switches |
| `perf_engineer` | Profile, Performance | checks the run took the intended path, turns a steady-state trace into fixed tables (kernels, overlap, fuse patterns, layers), metrics and opportunities with upper bounds; evaluates changes at equal budget on the fixed workload, against noise and the guard workloads the task names (a held-out point and more guards when the task asks), checking the known measurement hacks; measures a fix against its base, and the number a performance problem must get back |
| `reviewer` | Review, Reply | reads the diff and its evidence as a maintainer would, starting from the catalog of known reward hacks: shapes, dtypes, parallel layouts, graphs, fallbacks, do-no-harm, the framework's conventions, a fair benchmark; requested changes, and a test for every confirmed hack, come back as work packages; checks an issue's reply draft against the run, claim by claim, before the report; can run on another model (`review_model`) |
| `kernel_liaison` | Kernel | turns a kernel hand-off into the back end's task, runs `command` back ends, judges the result on the back end's own verification and returns a gain as a work package |
| `pr_engineer` | PR | changes no code: checks hygiene, lint and CI registration (gaps go back as work packages), arranges one commit per work package without changing the tree, trial-merges main to report conflicts, checks feature off on the final commit (for a change without a switch, the existing tests of the modules it touches), writes the PR body and the push command |
| `curator` | Report | writes what the run proved into `knowledge/` (known issues, feature skills, CUDA → AMD rows, new reward hacks, method lessons), every number traceable to the run, and proposes it as a pull request to InferPilot's repository |

## Setup

InferPilot runs inside a ROCm container, as root: Claude Code, InferPilot, git and the
framework's servers and tests all run there, next to the GPUs and the framework's Python environment.
Each task gets its own container, and everything it works on lives in that container: InferPilot's
checkout and its run directories in `/workspace`, and the image's own sglang in `/sgl-workspace/sglang`,
which the task changes on its own branch. Containers do not share them, so they stay out of each
other's way, and they last as long as their container. Claude Code is installed in each container too,
its first-start questions answered ahead. From your home on the host a
container takes one file, your keys in `~/.inferpilot_env`, mounted read-only; the rest of your home stays
out, since the agents run as root with permissions auto-approved and a home holds credentials they do
not need.

Every step can be run again: it checks first, keeps what is already there and adds only what is
missing, and run_docker.sh, run again, takes you back into the container. In the commands,
`check || action` runs the action only when the check fails: on a new machine every action runs, and
a second run skips them. Nothing is deleted, overwritten or re-cloned, apart from the image build's
packaging changes in `/sgl-workspace/sglang` of a new container from the daily image (step 2).

### What the host needs

- Linux with AMD Instinct GPUs (MI300 or MI350 series) and the ROCm driver loaded (`/dev/kfd` exists).
- Docker you can run (your user in the `docker` group, or sudo), git, curl and python3.
- Network access to claude.ai, github.com and Docker Hub, and about 45 GB of disk per sglang image.
- An AMD LLM gateway key (`LLM_GATEWAY_KEY`), an Anthropic API key or another gateway; without any, a
  Claude subscription.
- A GitHub token (`GITHUB_TOKEN`), strongly recommended: scans and issue runs read GitHub's API, and
  where it may write to InferPilot's own repository, the curator's pull requests and the daily reports go
  there with it.
- Model weights in a directory the container mounts (`/data`, `/mnt`, `/kmd2`, ...; any other through
  `EXTRA_MOUNTS`): InferPilot never downloads weights.

### 1. Keep your keys (host)

```bash
[ -e ~/.inferpilot_env ] || (umask 077 && echo 'export LLM_GATEWAY_KEY=<your AMD LLM gateway key>' > ~/.inferpilot_env)
(umask 077 && grep -qs GITHUB_TOKEN ~/.inferpilot_env || echo 'export GITHUB_TOKEN=<your GitHub token>' >> ~/.inferpilot_env)
```

The host needs no Claude Code: step 3 installs it in the container. Put your own values in place of
`<your AMD LLM gateway key>` and `<your GitHub token>` before you run these lines: each writes its key
only when `~/.inferpilot_env` does not have it yet, so a key is changed later by editing the file.
`~/.inferpilot_env` keeps the keys on the host, readable only by you; run_docker.sh mounts it read-only into
every container it starts, so create it before the container (one started without it can get a copy in
its own `/root/.inferpilot_env`).

- **LLM key (required).** With the AMD LLM gateway key, step 3 points Claude Code at the gateway
  (`https://llm-api.amd.com/Unified`, the key in its `Ocp-Apim-Subscription-Key` header), which serves
  the Claude models under their usual names. Outside AMD, write `export ANTHROPIC_API_KEY=sk-ant-...`
  instead, or another gateway (`ANTHROPIC_BASE_URL` with `ANTHROPIC_AUTH_TOKEN` or
  `ANTHROPIC_CUSTOM_HEADERS`). With a Claude subscription, run `claude setup-token` wherever you have
  Claude Code and a browser and write `export CLAUDE_CODE_OAUTH_TOKEN=<token>`; or leave the key out and
  log in at the first start, in every new container (step 4).
- **GitHub token (strongly recommended).** InferPilot uses this one token, `GITHUB_TOKEN`, for all it does
  on GitHub. Scans and issue runs read issues, pull requests and CI trackers through GitHub's API: without
  a token GitHub allows 60 requests an hour and 10 searches a minute per IP address, shared with everyone
  behind the same address, so a scan crawls and can stop partway; with one, 5000 requests an hour. It also
  writes to InferPilot's own repository, and nowhere else: the curator's knowledge changes, as a pull
  request from a `inferpilot/knowledge/<run>` branch to main, which you review and merge, and a scan's
  daily report, as an issue. Every agent in the container can read what `~/.inferpilot_env` holds, so give
  the token no more than that: on github.com, Settings → Developer settings → Personal access tokens →
  Fine-grained tokens, *Only select repositories* → `At1a8/inferpilot` and *Repository permissions* →
  *Contents*, *Pull requests* and *Issues*: *Read and write* (every fine-grained token reads all public
  repositories as well, sglang's among them), and keep your everyday token, the one you push with, out of
  it. InferPilot pushes only `inferpilot/` branches; protect main on GitHub (require a pull request) so the
  token cannot change it either. A token that cannot write there, a read-only one, still serves the
  scans: the knowledge changes then stay in the container's checkout, and the report gives the command
  that proposes them, and the daily report stays a file for you to share.
- **Git identity (recommended).** The container carries no home, so no git config of yours: commits
  InferPilot makes there would read `root@<this host>`. Add `export INFERPILOT_GIT_NAME="<your name>"` and
  `export INFERPILOT_GIT_EMAIL=<your email>`; step 3 gives them to root.

### 2. Start a container (host)

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/At1a8/inferpilot/main/scripts/run_docker.sh)    # a new container; NAME=<name> to come back to one
```

This fetches [`scripts/run_docker.sh`](scripts/run_docker.sh) and runs it, so the host needs no copy of
InferPilot; in a checkout, `scripts/run_docker.sh` does the same. It is the usual ROCm `run_docker.sh`: a
privileged container with the GPUs, `/home` and the data directories that exist (`/data`, `/mnt`, ...)
mounted at the same paths (`EXTRA_MOUNTS` adds more), and a root shell in its `/workspace`. InferPilot's
agents run as root with permissions auto-approved, so they can read every home on the machine, credentials
(git, ssh) included: `MOUNT_HOME=0` leaves `/home` out, for a task that must not see what is there (a
clean-room re-implementation). Your keys also come in as root's `~/.inferpilot_env`, mounted read-only.
Unlike many such scripts it does not mount the host's Docker socket (`DOCKER_SOCKET=1` does): it would put
every container on the host within the agents' reach.

What it does depends on whether `NAME` names an existing container and whether `IMAGE` is given:

| | no `IMAGE` | `IMAGE` given |
|---|---|---|
| no `NAME`, or no container `NAME` | a new container from the newest daily image; in its `/sgl-workspace/sglang`: `git checkout -- .`, `git checkout main`, `git pull` | a new container from `IMAGE`; its code as the image has it |
| container `NAME` exists | you enter the old container of that name, its code as it was left, with a warning that the name is taken, giving its name and image | the same; when its image is not `IMAGE`, the warning says that it is another image version too |

- **Image.** Tasks test the newest sglang, so the launcher detects the host GPU and starts from the newest
  matching daily ROCm 10 `rocm/sgl-dev` build: `rocm10-mi30x` for gfx942 (MI300/MI308/MI325) and
  `rocm10-mi35x` for gfx950 (MI350/MI355); a GPU with no ROCm 10 image (on Docker Hub, or here when
  Docker Hub cannot be reached) takes ROCm 7.2.4, then 7.2.0. If detection is unavailable it warns and
  falls back to MI35x ([`scripts/latest_image.py`](scripts/latest_image.py), e.g.
  `v0.5.23-rocm10-mi35x-20261103`; the `miles` images on the same page are for reinforcement learning
  and are skipped). A new day's image is pulled at its first use, about 43 GB. `IMAGE_VARIANT` overrides detection and fallback, for example
  `IMAGE_VARIANT=rocm724-mi30x`; for a fixed image, use `IMAGE=rocm/sgl-dev:<tag>`, whose
  `/sgl-workspace/sglang` is then left exactly as the image has it. A container keeps the
  image it started from: run_docker.sh says so when you come back to one whose image is no longer the
  newest daily one, and a task there stops at Setup, before it creates anything, to ask whether to run
  on it anyway (`image_ok`), unless the task names that image (`image`). A scan goes on and names its
  image in the report.
- **What is tested.** The image provides PyTorch, aiter, sgl-kernel and its own sglang checkout,
  `/sgl-workspace/sglang`, with the whole history, which it installs sglang from in editable mode: what
  runs is what is checked out there, so there is no sglang to clone or name. A new container from the
  daily image starts with it on the current main: run_docker.sh discards the image build's changes there,
  checks out main and pulls. Never `pip install -e` from it afterwards: its `pyproject.toml` is then
  upstream's, which would pull CUDA packages. A container from a given `IMAGE`, or one you come back to,
  keeps its code as it is. Each task works in it on its own branch from the newest main, which Setup
  fetches first. A baseline at the base commit and a scan of the current main read worktrees of the same
  repository under the run directory (about 0.2 GB each). The run records the image, the image's sglang
  version and the task's base commit.
- **One container per task.** Without `NAME`, every run starts a new container, named
  `inferpilot_<you>_<MMDD>_<4 random letters or digits>` (the date in UTC+8; a name no container has) and
  labelled `inferpilot.user=<you>`; run_docker.sh prints its name and image, the command that brings you back
  (`NAME=<name> scripts/run_docker.sh`) and your other InferPilot containers. With `NAME` of an existing
  container it takes you back into it, started again if it was stopped, with `/workspace` as you left it:
  it never removes a container or changes its code. The shell is a `docker exec` into the container, so
  leaving it leaves the container running (`docker stop <name>` stops it). `docker rm -f <name>` removes
  one you are done with, and its `/workspace` with it; each one keeps its image (about 43 GB) on the
  host until then. However many
  containers there are, one InferPilot task runs on a machine at a time ([Running it](#running-it)). A
  run_docker.sh of your own works too: mount your keys as root's `~/.inferpilot_env`
  (`-v $HOME/.inferpilot_env:/root/.inferpilot_env:ro`), or name the file with `INFERPILOT_KEYS`; in a container that
  mounts `/home`, the bootstrap also finds it in your home there (step 3).
- **Without a terminal.** Under a job scheduler (`srun`), in CI or from an agent's shell there is no
  terminal for a shell: run_docker.sh then leaves the container running in the background and prints
  the `docker exec` that enters it (`DETACH=0` or `1` overrides the guess). Where the Docker daemon
  refuses `--privileged`, `PRIVILEGED=0` leaves it out; the GPUs still come through `/dev/kfd` and
  `/dev/dri`, but the container cannot write `/proc/sys`. Where it refuses a mount of all of `/home`,
  `MOUNT_HOME=0` leaves it out, and `EXTRA_MOUNTS=$HOME` brings back only yours. run_docker.sh says
  which of these a refusal calls for.

`claude` is not there yet (`claude: command not found`): step 3 installs it.

### 3. Set up InferPilot in the container (once per container)

This puts InferPilot in the container's `/workspace` and makes the container ready to run it, as the second
table below lists. As written it sets up both Claude Code and Cursor, so that a task can run on either
(step 4); leave out an option to install less:

```bash
[ -d /workspace/inferpilot/.git ] || git clone https://github.com/At1a8/inferpilot /workspace/inferpilot
cd /workspace/inferpilot && python3 -B -m inferpilot.bootstrap --cursor --claude
source ~/.bashrc
claude --version    # installed in this container
agent --version     # the Cursor CLI, here too
```

| Bootstrap | It installs | Tasks run with |
|---|---|---|
| `python3 -B -m inferpilot.bootstrap --cursor --claude` | Claude Code, Node.js and the Cursor CLI (`agent`) | Claude Code or Cursor |
| `python3 -B -m inferpilot.bootstrap --cursor` | Node.js and the Cursor CLI | Cursor |
| `python3 -B -m inferpilot.bootstrap` (as `--claude`) | Claude Code | Claude Code |

`--runner` adds Node.js to Claude Code, to run every role on `claude -p` without a Claude Code session
(step 4); `--cursor` brings it anyway. The Cursor CLI logs in once per container, since the Cursor app's
login does not reach it:

```bash
NO_OPEN_BROWSER=1 agent login   # open the URL it prints in a browser elsewhere, within about 20 minutes (q shows a QR code)
agent status                    # Logged in as ...
```

or it uses `CURSOR_API_KEY` (Cursor Dashboard, API Keys) from your keys file on the host, which containers
started after it get.

| Step | In a new container |
|---|---|
| keys | root's `~/.inferpilot_env`, which run_docker.sh mounts from your home, or the file `INFERPILOT_KEYS` names. In a container that mounts `/home`, the one in your home there: `INFERPILOT_HOST_HOME`, else, with a warning, the home of the user the container is named after (`<you>`, `<you>-0116`). Without a keys file the rest is still set up, and the next steps say where a key goes |
| `claude` | (with `--claude`, or with neither option) installed in the container with Claude Code's native installer (`~/.local/bin/claude`, put on `PATH`): the newest version, a large download (minutes without output on a slow link are the download). It stays at that version for the container's life (`DISABLE_AUTOUPDATER=1`); a new container gets the newest |
| Claude Code config | (with `--claude`, or with neither option) root's `~/.claude.json` answers the first start's questions ahead: the onboarding, trust in `/workspace/inferpilot`, bypass permissions mode and, for an `ANTHROPIC_API_KEY`, using it. The rest of the file is kept; like everything of root's, it goes with the container |
| git | trusts checkouts on host mounts, which belong to you, not root (`safe.directory '*'`) |
| git identity | root commits as `INFERPILOT_GIT_NAME <INFERPILOT_GIT_EMAIL>` from the keys file (or the environment), so no commit reads `root@<this host>`; with neither it says so and sets nothing |
| shell | root's `~/.bashrc` gets `PYTHONDONTWRITEBYTECODE=1` (no root-owned `__pycache__` in checkouts on host mounts) and `DISABLE_AUTOUPDATER=1`, and loads the keys file, also one put in place later; with `LLM_GATEWAY_KEY` and no `ANTHROPIC_BASE_URL`, it points Claude Code at the AMD LLM gateway |
| framework | the image's `/sgl-workspace/sglang`, left as it is: run_docker.sh has put it on the current main in a new container from the daily image (step 2), and a container from a given `IMAGE`, or one you came back to, keeps its code. Uncommitted changes there are reported, since a task's Setup stops on them until you deal with them (in a container from a given `IMAGE`, the image build's packaging changes); `--framework-dir` names a host checkout instead (used as it is, or cloned with its whole history when missing) |
| evalscope | for `scripts/eval_accuracy.sh`, every accuracy set: installed in a venv of its own, `/opt/evalscope` (about a minute and 1.2 GB), and linked as `/usr/local/bin/evalscope`. Not in the image's Python, where it would downgrade `datasets` and `fsspec` below what sglang ships with; an `evalscope` already on `PATH` is kept |
| node, cursor | node with `--runner` or `--cursor`, for `runtime/run.mjs`: Node.js, the newest LTS unless `INFERPILOT_NODE_VERSION` names one, checked against nodejs.org's checksums, unpacked into `/opt/node/<version>` and linked into `/usr/local/bin`. cursor only with `--cursor`, for `--backend cursor`: the Cursor CLI (`agent`) from its installer. A Node.js 20+ or an `agent` already there is kept. Logging the CLI in is yours, once per container: `NO_OPEN_BROWSER=1 agent login`, then the printed URL in a browser, or `CURSOR_API_KEY` in your keys file on the host |
| checks | GPUs, sglang, evalscope, API access, `GITHUB_TOKEN`, counting what the keys file sets, and with `--cursor` the Cursor CLI (detect only) |

The clone runs only when `/workspace/inferpilot` is not there yet; the bootstrap never writes keys.

### 4. Give it a task (inside the container): with Claude Code or with Cursor

Claude Code runs `inferpilot.js` in its Workflow tool. Cursor has no such tool: there InferPilot's own
runner, `runtime/run.mjs`, runs `inferpilot.js` and every role on the Cursor CLI. The task is the same,
the arguments of [Running it](#running-it):

| With | Set up in step 3 | Start | Give the task |
|---|---|---|---|
| Claude Code | `--claude` (or no option) | `IS_SANDBOX=1 claude --dangerously-skip-permissions` | one sentence in its input box |
| the Cursor CLI's chat | `--cursor`, `agent login` | `cd /workspace/inferpilot && agent`, which starts the runner | one sentence in its input box |
| the Cursor app | `--cursor`, `agent login` | its agent starts the runner | one sentence in the app's chat |
| the runner, from a shell | `--cursor`, `agent login` | `node runtime/run.mjs inferpilot.js --backend cursor ...` | the arguments in a JSON file |

Claude Code and the Cursor agents (the CLI's chat, the app) drive the run for you: they put its questions
to you and start what comes next, as the result's `next` says ([Running it](#running-it)); from a shell,
the runner's log lists the same steps for you to take.

#### With Claude Code

```bash
cd /workspace/inferpilot && IS_SANDBOX=1 claude --dangerously-skip-permissions
```

`IS_SANDBOX=1` lets Claude Code auto-approve permissions as root, which InferPilot's agents need. Step 3
answered the first start's questions in Claude Code's config, so it opens on its input box. Should one
still come up (step 3 reported `claude-config` as `skipped`), three of the defaults would stop you:

| Question | Answer |
|---|---|
| Do you want to use this API key? (asked only for an `ANTHROPIC_API_KEY`; the gateway key is not) | **Yes**; the default, No, asks you to log in instead |
| Is this a project you trust? | **Yes, I trust this folder**; the default exits |
| Bypass Permissions mode | **Yes, I accept**; the default exits |

With a key, the first start asks for no login. If it asks you to select a login method, or shows
`Paste code here if prompted >`, Claude Code has no key in this shell: enter nothing, press Ctrl-C three
times in a row to leave, and read the end of step 3's output, which names what is missing. With a Claude
subscription and no token (step 1), that login is the way in, again in every new container: pick
*Claude account with subscription*, open the URL it prints in any browser, log in, and paste the code
the page shows at that prompt.

Once it runs, the bottom line shows `bypass permissions on` for the whole session: that is the mode
`--dangerously-skip-permissions` turned on, not a question. Leave it as it is (Shift+Tab would switch to
a mode that waits for your approval before commands, and InferPilot's agents would stall) and type the task
in the input box above it:

```
use /workspace/inferpilot/inferpilot.js with mode=scan, inferpilot_dir=/workspace/inferpilot
```

The scan changes no code. It writes the day's report on the framework since the last report (else the
last 7 days): the AMD CI on main (every failing job, since when it fails and what its log says), the
open AMD user issues under P0, P1 and P2, and the upstream changes AMD needs to follow. It ranks the
tasks in them into `exp/scan_<time>/task_queue.md`, each with the task definition that starts it (within a
rank the smaller effort first; a task that holds its GPUs 6 hours or more is marked long, to start apart
from the queue, on a machine and with time of its own), and writes the report, without anything about this machine, to
`exp/scan_<time>/daily_report.md`, which it
shares as an issue in InferPilot's repository with `GITHUB_TOKEN` (step 1; when the token cannot write
there, the file is yours to share). One report a day is enough: when one is less than 24 hours old, the scan asks before it starts again
(answer `rescan`), and `upstream_repos` adds repositories to follow, such as vLLM. To take one task,
stopping after the reproduction to confirm the environment, name its fields:

```
use /workspace/inferpilot/inferpilot.js with mode=issue, task=https://github.com/sgl-project/sglang/issues/41299,
inferpilot_dir=/workspace/inferpilot, model_path=<weights>, gpu_ids=0, stop_after=Reproduce
```

or say it in plain words and let the Intake phase write the definition:

```
use /workspace/inferpilot/inferpilot.js with inferpilot_dir=/workspace/inferpilot, request="DeepSeek-V4.1 decode
is slow on MI355X, make it faster"
```

When something is missing (here: which fixed workload every measurement uses), the run starts
nothing and returns `status: needs_input` with one question per field. Answer them in the chat;
Claude Code runs `inferpilot.js` again with the same arguments plus `answers={"workload": ...}`.
[Running it](#running-it) lists all arguments.

**Without a terminal to type in** (an agent's shell, a job scheduler), keep the interactive session and
run it in tmux. `claude -p "use inferpilot.js ..."` is no way around it: the Workflow tool runs
`inferpilot.js` in the background, print mode answers within seconds and then ends the process, and the
run dies with it, leaving its machine lock behind. [`scripts/run_in_tmux.sh`](scripts/run_in_tmux.sh)
starts Claude Code in a tmux session, waits for its prompt and pastes the task from a file; it stops at a
first-start question (`claude-config` did not answer it) or a login prompt, and says which:

```bash
command -v tmux >/dev/null || apt-get install -y tmux
echo 'use /workspace/inferpilot/inferpilot.js with mode=scan, inferpilot_dir=/workspace/inferpilot' > /workspace/task.txt
/workspace/inferpilot/scripts/run_in_tmux.sh /workspace/task.txt
tmux capture-pane -p -t inferpilot | tail -40
```

**Claude roles on the runner.** Without a Claude Code session, the runner runs every role on `claude -p`
instead (the Node.js it needs comes with `--cursor`, or with `--runner`), started as on Cursor below:

```bash
cd /workspace/inferpilot && mkdir -p exp
echo '{"mode": "scan", "inferpilot_dir": "/workspace/inferpilot"}' > exp/scan.json
nohup node runtime/run.mjs inferpilot.js --backend claude --args-file exp/scan.json --result-file exp/scan.result.json < /dev/null > exp/scan.log 2>&1 &
```

#### With Cursor

**From the container's shell.** With the Cursor CLI logged in (step 3), write the task's arguments to a
file and start the runner in the background; it runs every role on `agent -p`:

```bash
cd /workspace/inferpilot && mkdir -p exp
echo '{"mode": "scan", "inferpilot_dir": "/workspace/inferpilot"}' > exp/scan.json
nohup node runtime/run.mjs inferpilot.js --backend cursor --args-file exp/scan.json --result-file exp/scan.result.json < /dev/null > exp/scan.log 2>&1 &
tail -f exp/scan.log    # Ctrl-C leaves the log; the run goes on
```

**Watching a run.** `exp/scan.status.json` is rewritten every 30 s while the runner lives (state, phase,
the agent calls under way with the last line of each and when it came, `last_at`); an `updated` more than
two minutes old means the runner is gone. `exp/scan.agents/` keeps each agent call's prompt, whole output
stream and stderr (`--agent-log-dir` puts them elsewhere), and `--show-text` adds what the agents write to
the log.

**Moving a run's stop.** The runner reads `exp/scan.control.json` (the arguments file's name with
`.control.json`; `--control-file` names another) at every phase boundary: `{"stop_after": "<phase>"}`
stops after that phase of the mode (`Collect` for this scan, `Verify` for a feature task),
`{"stop_after": "now"}` after the phase now running, `{"stop_after": ""}` runs to the end. The log says
when the stop moved, and when the run is already past that phase or the file holds no JSON object; a
file already there when the run starts counts only once it changes. A run that ends with questions
after its stop moved hands the stop on: the arguments for running it again (`next.rerun_with`) carry
`stop_after` as the file last set it, so the task is not run again only to stop where it first said.
Only the runner reads a control file, on either backend; a run in Claude Code's Workflow tool keeps
the `stop_after` it started with.

**Stopping a run.** `kill <runner pid>` (SIGTERM; the log's first line gives the pid), or Ctrl-C on a
runner started in the foreground, stops its agents and everything they started, servers started with
`setsid` or `nohup` included (each inherits `INFERPILOT_RUN_TAG`, which names the runner), frees the
machine lock and writes `"status": "interrupted"` to `--result-file`. A run that died otherwise
(`kill -9`, the OOM killer) leaves its processes behind: the next run lists them, those in the task's
container too, and `--kill-orphans` stops them first. A restarted container takes its processes with
it but leaves the machine lock, whose runner a new container cannot judge:
`python3 scripts/run_lock.py release --force` frees it.

The file holds, as JSON, the arguments the sentences above give Claude Code. The issue that stops after
the reproduction is `{"mode": "issue", "task": "https://github.com/sgl-project/sglang/issues/41299",
"inferpilot_dir": "/workspace/inferpilot", "model_path": "<weights>", "gpu_ids": "0", "stop_after": "Reproduce"}`;
a request in plain words is `{"inferpilot_dir": "/workspace/inferpilot", "request": "..."}`. `exp/scan.log`
numbers each phase along the mode's route, with what the run took so far
(`=== PHASE 2/3 [##-]: Dispatch ===  so far: 1 agent call, 410k tokens, 4 min`), names each agent call and
every tool call in it as it starts (a command, a file read or written), adds a still-working line after five
quiet minutes, ends each call with its time and tokens (as the CLI counts them, cache included) and the run
with one summary line; `exp/scan.result.json` holds the run's end: its `status`, and `next`, what comes
after it. The log ends with the same steps (`=== NEXT: ...`): the questions only you answer, each run that
can start next with its arguments written next to yours (`exp/scan.start-1.json` for the scan's first
task, `exp/<name>.again.json` for the same task again, its answers to fill in) and the command that starts
it, and what only you do.

**From the Cursor app.** Its agent does not run `inferpilot.js` either: it writes the task to a file,
starts the runner in the background and reads the run as it goes, as [`AGENTS.md`](AGENTS.md) at the top of
the checkout tells it. Open a checkout of InferPilot as the app's workspace, so that Cursor loads that file
as a rule: on the GPU machine over Remote-SSH (any clone, such as the one you ran run_docker.sh from), where
the agent runs each command in your container, or attached to the container at `/workspace/inferpilot`.
Then give the task in one sentence, as to Claude Code; the agent asks for what the mode needs and you have
not said, and tells you where the run goes. It uses the Cursor CLI; say so for Claude roles on `claude -p`
instead (Claude Code and Node.js: bootstrap `--cursor --claude`, or `--runner`). The Cursor CLI's own chat (`agent`, in the container) reads the same file.

With Cursor roles, code and prompts go to Cursor's service instead of the AMD LLM gateway, and count against
the account's usage. From outside the container, every command goes through
`docker exec -i <container> bash -ic '...'`: only an interactive shell reads `~/.bashrc`, which loads the
keys file and puts `claude` on PATH. A plain `bash -c` gets neither, so Claude roles fail and a scan runs
without `GITHUB_TOKEN`.

### 5. After a run (in the container)

Every run that got past Setup has its directory in `/workspace/inferpilot/exp` (ignored by git), named
`<mode>_<slug>_<time>`: the mode, a short name of the task and its start in UTC, e.g.
`issue_41299-glm-dcp_20261106-071502Z` or `scan_20261106-064327Z`. `exp/index.md` lists every run of
the container with its status. A run directory holds:

| File | What it holds |
|---|---|
| `run.json` | the task definition, the intake's assumptions and the container, which holds the task's branch |
| `validation_report.md`, `run_report.md` | the report: the first when a PR is ready, the second for any other outcome (the result names it as `report_path`) |
| `experiment_ledger.md` | the environment, the baseline and every measurement |
| `logs/` | one log per server or benchmark launch, `<tag>_<time>_XXXXXX.log` from `mktemp`, with its `.pid` and `.exit` |
| `pr_body.md`, `patches/` | the PR description and the branch's commits as patches |
| `pr_comment.md` | for an existing fix PR that did not pass verification: the comment that says what the run measured, for you to post on that PR |
| `reply_draft.md`, `reply_check.md` | an issue's reply draft, for you to post, and the reviewer's check of it against the run |
| `component_issue.md` | an issue drafted for the AMD component a root cause is in (aiter, MoRI, ROCm, the ROCm build of PyTorch), for you to file in its repository |
| `knowledge_pr.md`, `knowledge.patch` | the description of the curator's pull request to InferPilot's repository, and its change, which `scripts/propose_knowledge.py --patch` proposes later from any checkout |
| `daily_report.md`, `task_queue.md` | a scan's daily report, shared as an issue or for you to share (below); the ranked tasks with local detail, each with the definition that starts it, next to what each source found (`<source>.json`) |

A scan's `daily_report.md` is written to be shared, and `scripts/daily_report.py check` has made sure
it names nothing about this machine. With a `GITHUB_TOKEN` that can write there (step 1) the director
shares it as an issue in InferPilot's repository titled `InferPilot daily report <date>`, unless that day's
is there already, and the result names it. Without one, and when that day's is not there yet, open a new
issue there on GitHub, give it that title and paste the whole file, or run
`python3 scripts/share_daily_report.py exp/scan_<time>/daily_report.md` once the token is set. A scan
whose Dispatch failed shares nothing: the report it left may be partial. Its first line, a hidden marker, lets the next scan, in any container, find it
there and mark what changed since.

Before you push, read every changed line and re-run the PR's tests and measurements yourself, as the
*Before pushing* section of `validation_report.md` gives them: it takes the machine and checks that
every GPU is idle, makes each arm's tree again from its commit, checks which code each command imports
and measures as the run did, since by then the run has freed the machine and removed its baseline
worktree. A new PR's description has, under its checklist, the line
`Developed with the [InferPilot](https://github.com/At1a8/inferpilot) AI agent; reviewed and measured by AMD engineers.`,
which says you did; a verification comment for an existing PR has none. Where your result differs from
the description's by more than run-to-run noise, put yours in it. Don't push a PR you have not re-run,
and keep that line word for word: a search of GitHub finds every InferPilot PR by it.

InferPilot never pushes. The task's branch, `inferpilot/<slug>`, is in `/sgl-workspace/sglang`, and the report
gives the exact `git -C /sgl-workspace/sglang push ...`: run it here with credentials that can push.
Run directories go with the container; to keep one, or to push from the host, where your git
credentials are, copy it out on the host (`<name>` is the container's name, `<run>` the run
directory's):

```bash
docker cp <name>:/workspace/inferpilot/exp/<run> ~/
```

The copy is yours, not root's. On the host, in your sglang checkout, `git checkout -b inferpilot/<slug> <base>`
with the base commit the report names, then `git am ~/<run>/patches/*.patch`, and push from there.

### Re-running and options

Each step of `inferpilot.bootstrap` reports `kept`, `installed`, `updated`, `added`, `created`, `skipped` or
`failed`; a summary lists them, the next steps list only what is still missing, and the exit code is 1
when a step failed. In a new container it installs Claude Code and evalscope, a minute or two each, and
writes Claude Code's config and the git and shell settings. `--claude-only` runs only the keys and Claude Code steps,
`--check` only the checks. `--claude` and `--cursor` choose what to install: with neither, Claude Code; with
`--cursor` alone, Node.js and the Cursor CLI; with both, all three. `--runner` adds Node.js to Claude Code.
Inside a container, `cd /workspace && pip install -v git+https://github.com/At1a8/inferpilot` runs
step 3 too, cloning InferPilot to `/workspace/inferpilot` (pip shows the output only with `-v`; a cached wheel skips it).

| Variable | Used by | Default |
|---|---|---|
| `IMAGE` | `scripts/run_docker.sh` | the newest daily `rocm/sgl-dev` image of `IMAGE_VARIANT` (the newest local one when Docker Hub cannot be reached) |
| `IMAGE_VARIANT`, `NAME`, `EXTRA_MOUNTS`, `MOUNT_HOME` | `scripts/run_docker.sh` | detected from gfx942/gfx950 (fallback MI35x), ROCm 10, else 7.2.4, else 7.2.0; a new container's `inferpilot_<you>_<MMDD>_<random>`; more directories to mount, space-separated; `0` leaves `/home` out |
| `DOCKER_SOCKET` | `scripts/run_docker.sh` | unset; `1` also mounts the host's Docker socket and `docker` |
| `DETACH`, `PRIVILEGED` | `scripts/run_docker.sh` | unset: in the background when there is no terminal, else an interactive shell; `PRIVILEGED=0` leaves out `--privileged` |
| `INFERPILOT_LOCK_FILE` | `scripts/run_lock.py` | `/dev/shm/inferpilot-run.lock`, the machine's one-task lock |
| `INFERPILOT_HOME` | bootstrap | `./inferpilot`, or the checkout you run from |
| `INFERPILOT_KEYS` | bootstrap | root's `~/.inferpilot_env`, where run_docker.sh mounts yours: the keys file the container's shells load |
| `INFERPILOT_HOST_HOME` | `scripts/run_docker.sh` (with `/home` mounted), bootstrap | in a container that mounts `/home`, your home there, where the bootstrap looks for the keys file (without it, the home of the user the container is named after) |
| `INFERPILOT_CONTAINER` | `scripts/run_docker.sh` sets it; bootstrap, `scripts/run_lock.py` | the container's name (without it, Docker's answer when the socket is mounted): the bootstrap looks for the keys file in the home of the user it names, and the run lock records it as the holder |
| `INFERPILOT_REPO_URL`, `INFERPILOT_REF` | bootstrap | `https://github.com/At1a8/inferpilot.git`, its default branch |
| `CLAUDE_VERSION`, `CLAUDE_BIN_DIR` | bootstrap | `latest`, `~/.local/bin` |
| `CLAUDE_CONFIG_DIR` | Claude Code, bootstrap | unset (`~`): Claude Code's own, where its config lives; the bootstrap writes the first-start answers there |
| `EVALSCOPE_VERSION`, `EVALSCOPE_VENV` | bootstrap | `latest`, `/opt/evalscope` |
| `INFERPILOT_CLAUDE`, `INFERPILOT_CURSOR`, `INFERPILOT_RUNNER`, `INFERPILOT_NODE_VERSION` | bootstrap | unset; `1` as `--claude`, `--cursor` and `--runner` (`0` is off; with neither of the first two, Claude Code); the newest Node.js LTS (`22` for the newest 22.x, or an exact `v22.11.0`) |
| `INFERPILOT_CLAUDE_BIN`, `INFERPILOT_CURSOR_BIN` | `runtime/run.mjs` | `claude`, `agent` on `PATH`: the CLIs that `--backend claude` and `--backend cursor` run |
| `INFERPILOT_GIT_NAME`, `INFERPILOT_GIT_EMAIL` | `~/.inferpilot_env`, bootstrap | unset; the identity root commits with in the container |
| `GITHUB_TOKEN` | `~/.inferpilot_env`, bootstrap, the GitHub scripts | unset; the one GitHub token, strongly recommended (step 1): it reads GitHub's API, and with Contents, Pull requests and Issues read and write on InferPilot's own repository the curator opens its pull request with it and the director shares a scan's daily report |
| `LLM_GATEWAY_KEY` | `~/.inferpilot_env`, the container's shell settings | unset; set, Claude Code runs on the AMD LLM gateway unless `ANTHROPIC_BASE_URL` names another |
| `INFERPILOT_FRAMEWORK_DIR`, `INFERPILOT_FRAMEWORK_REPO` | bootstrap: `--framework-dir`, `--framework-repo` | the image's `/sgl-workspace/sglang`; sglang |
| `INFERPILOT_SKIP_BOOTSTRAP` | bootstrap | unset; set it to install the package with no side effects (CI, images) |

## Running it

InferPilot is a workflow: `inferpilot.js` holds the control flow, `roles/` the agent instructions,
`knowledge/` the method, `templates/` the artifacts. In Claude Code, name `inferpilot.js` and its arguments
in one sentence, as in [step 4](#4-give-it-a-task-inside-the-container-with-claude-code-or-with-cursor): its Workflow tool runs it.
Anywhere else InferPilot's own runner, `runtime/run.mjs` (Node 20+), runs it with each role on a
command-line agent: `--backend claude` runs `claude -p`, and `--backend cursor` runs the Cursor CLI
(`agent -p`) on the account it was logged in with (`agent login`) or on `CURSOR_API_KEY`. In a container,
`python3 -B -m inferpilot.bootstrap --cursor` installs Node.js and the Cursor CLI, and
`NO_OPEN_BROWSER=1 agent login` prints the URL to log in from a browser elsewhere. The dry run uses the
same runner.

```bash
node runtime/run.mjs inferpilot.js --backend cursor \
  --args '{"mode":"scan","inferpilot_dir":"'"$PWD"'","framework_dir":"/path/to/sglang"}'
```

`--model`, `--args-file`, `--result-file`, `--concurrency`, `--agent-timeout-ms`, `--schema-retries`,
`--kill-orphans`, `--control-file`, `--agent-log-dir` and `--show-text` are described at the top of
`runtime/run.mjs`.

| Argument | Meaning |
|---|---|
| `inferpilot_dir` | absolute path of this checkout (required) |
| `request` | the task in plain words, any language; the Intake phase turns it into the fields below (`roles/intake.md`), and fields given with it win |
| `answers` | replies to a `needs_input` result, `{"<field>": <value>}`, passed with the same arguments as before |
| `mode` | `feature` \| `issue` \| `ci` \| `perf` \| `scan` (required; inferred from `request` when not given) |
| `task` | the upstream PR/design, issue URL, CI job/cluster or performance goal (required except `scan`) |
| `goal` | what done means, measurable when the mode measures; the spec and the performance plan address it |
| `framework_dir` | the framework checkout each task works in, on its own branch from the current main (for sglang the image's `/sgl-workspace/sglang` by default, which the image installs sglang from) |
| `model_path` | the model the baseline and the gates run (required for `feature` and `perf`; for `issue` and `ci` too, unless `stop_after` ends the run before a fix: a fix that changes code the server runs is measured on gsm8k against the base, G2) |
| `framework`, `platform`, `gpu_ids` | target and environment (`sglang`; empty `gpu_ids` lets Setup pick idle GPUs, which every later role then uses through `HIP_VISIBLE_DEVICES`) |
| `workload` | the benchmark every measurement uses, e.g. `{"isl":8192,"osl":1024,"conc":64}`; required for `perf`, fixed at Setup |
| `server_script` | your script that starts the server for `model_path`; InferPilot sets `PORT`, `HIP_VISIBLE_DEVICES`, `PYTHONPATH` and `EXTRA_ARGS` (the feature's flags, empty for the baseline), and the script passes `--port $PORT` and `$EXTRA_ARGS` to the server; required for `feature` and `perf` ([`templates/task_definition.md`](templates/task_definition.md) §1.1). Without one, you may answer `template`: InferPilot's `templates/harness/server.sh`, after the model's recipe in the sglang cookbook |
| `client_script` | your script that runs the workload against `PORT` and writes its logs to `OUT_DIR`: the benchmark, run unchanged; required for `feature` and `perf`: every feature is measured against the baseline (G5), the gain it claims or, where it claims none, that it is no slower; for `issue` and `ci` too, unless `stop_after` ends the run before a fix: a fix that changes code the server runs is measured on it against the base, and is no slower (G5). Without one, a feature or a perf task may answer `template`: InferPilot's `templates/harness/perf.sh` (sglang's serving benchmark) on the `workload`, which a task that takes it gives in full; on it a feature is judged only as no slower than the baseline, never on its source's gain |
| `accuracy_script` | your script that measures accuracy against `API_URL` and writes its results under `RESULT_ROOT` (for each set its score, the number of questions and how many of them got an answer), run unchanged for every arm; optional: without one, InferPilot's `templates/harness/accuracy.sh` (`scripts/eval_accuracy.sh`) |
| `output_script` | `issue` and `ci`, for a model that answers no text questions (a diffusion, speech or embedding model), which gsm8k cannot be put to: your test of its outputs, which stands in for gsm8k as G2 when a fix changes code the server runs. It puts the same inputs with the same seeds to the server at `PORT` and writes each output to `OUT_DIR`; with `BASE_DIR` set (its run on the base), it compares its outputs with those and exits non-zero when they differ by more than it tolerates. Run unchanged, once on the base and once on the fix. InferPilot has no template for it: Setup says what the model is, and a task that can reach a fix without one is asked for it then ([`templates/task_definition.md`](templates/task_definition.md) §1.1) |
| `text_model` | `issue` and `ci`: your word on what the model is, where Setup's is wrong. `true`: it answers text questions, so G2 runs gsm8k and no `output_script` is asked for. `false`: it answers none, so G2 is the output comparison. Left out, Setup says. Either way the verify engineer, who launches the model, has the last word |
| `harness` | values for InferPilot's templates, e.g. `{"CONTEXT_LENGTH": 8192, "ENABLE_THINKING": 0}` (`python3 scripts/harness.py show` lists them): each parameter takes the task's value, else the one the tests of the feature's source use (vLLM's `--max-model-len 4096` for PARD-2), else for the server the model's sglang cookbook recipe, else the template's default ([`templates/task_definition.md`](templates/task_definition.md) §1.2) |
| `container` | only when InferPilot runs on the host: the framework's container, reached with `docker exec` |
| `exp_root` | where run directories go (default `<inferpilot_dir>/exp`, which git ignores). On a host mount they outlive the container, and a retry in a new container takes the task's commits from `RUN_DIR/branch.bundle` instead of writing them again: use one where the container can go away mid-run (a preemptible node, a job's time limit) |
| `kernel_backend` | `none` (default) \| `geak` \| `command`: where kernel hand-offs go |
| `kernel_backend_dir` | `geak` back end: the GEAK checkout whose kernel lane runs as an outside program |
| `kernel_backend_command` | `command` back end: shell command with `{task_dir}`, `{out_dir}`, `{gpu_ids}`, `{budget}`; for `geak` it replaces the default command |
| `kernel_budget` | optimization rounds for the kernel back end (default 6), its `{budget}` |
| `apply_prs` | `perf`: candidate PRs to apply and A/B. `issue`, `ci`: the open fix PR the run may verify (`#<n>`), or `none`: a pull request runs its author's code on this machine, so the run applies one only when it is named here, whoever wrote it; without it the run asks |
| `sources`, `since` | `scan`: any of `ci`, `issues`, `upstream` (the default) and `hardware`, `perf`; the start of the window (default: the previous report) |
| `image`, `image_ok` | the image a task must run on (default: the newest daily one of the container's ROCm and GPU; another one stops Setup to ask); `true` runs on the container's image anyway |
| `rescan`, `upstream_repos` | `scan`: `true` scans again although a report from the last 24 hours exists; more repositories to follow upstream, as `[{"name": "vllm", "url": "https://github.com/vllm-project/vllm"}]` |
| `bisect_fixed` | `issue`: `true` bisects between the reporter's commit and main, with the reproduction as the test, when the first fails and the second passes, and names the commit that fixed it |
| `reported_on` | `issue`: the model and launch configuration the report puts the task's problem on (`DeepSeek-V4.1, --tp-size 8`). A run that finds its problem on another configuration than the one it was set up for stops before Reproduce, and its result gives this to the run after it (`next.rerun_with`; `next.start` for the report's other problems): with no `gpu_ids` given, Setup takes as many GPUs as that configuration needs, and writes the launch for it |
| `confirm_leads` | default `false`: leads, the problems roles met that are not the task's, are listed in the report with how to reproduce them, and Spinoff runs nothing on the GPUs; `true` checks each on the base commit before the report and drafts an issue for those that show there |
| `stop_after` | stop after the named phase of the mode (e.g. `Design`, `Reproduce`); a phase the mode does not run comes back as a question |
| `resume_from`, `run_dir` | `feature`: `implement` continues a run that stopped at Design once a human approved `design.md` |
| `auto_approve_design` | `feature`, default `true`: a design the architect flags, or L3 work, goes on to Implement, and the report says no human approved the design. `false` makes such a design wait at Design for a human (`awaiting_design_review`). `stop_after` still stops |
| `held_out`, `guards`, `sweep` | performance checks beyond the task's own workload and goal, each off by default: `held_out` measures the gain again at a held-out operating point (half the concurrency) and requires it to hold there; `guards` adds guard workloads of InferPilot's choosing to the ones the goal names; `sweep` measures points beyond the workload's own (more concurrencies or lengths) |
| `tune` | `feature`, default `false`: the feature runs at the configuration of the target PR or design (for FLy, vLLM's window 2, threshold 0.3, top-k 3) and has to work there; `true` adds a work package that chooses its knobs by measurement |
| `long_context`, `stability` | correctness gates beyond the default ones, each off by default: `long_context` runs G3, the task's own long-context test (`long_context_script`: needle-in-a-haystack runs on long prompts); `stability` runs G4, the `client_script` run back to back for more than 48 hours against one server (`scripts/stability.sh`; the task names its `server_script` and its `client_script`, in any mode, and is asked for the one it lacks: G4 takes no template of its own accord), once in a run: a later change to the code is not run through it again, and the report says which commit was. A Verify round with it asks for a limit of about 6 days and gets it, up to 7 days, with no `agent_timeout_ms` to set for it; the limit is used in full only when G4 fails and the baseline runs the same test as its control |
| `long_context_script` | your script that runs the long-context test against `PORT` and writes each request's result to `OUT_DIR`; G3 runs it unchanged on the baseline and on the change, at two or more concurrencies (`CONCURRENCY`); required with `long_context` on, and asked for when the task names none: InferPilot has no template for it ([`templates/task_definition.md`](templates/task_definition.md) §1.1) |
| `review_model` | the reviewer's model, passed with its agent call (InferPilot's runner gives it to the CLI as `--model`; in Claude Code it depends on the Workflow tool) |
| `max_fix_rounds`, `max_investigate_rounds`, `max_review_rounds`, `max_work_packages` | budgets |
| `agent_retries`, `agent_timeout_ms` | an agent call is retried after an error (the retry checks what the failed attempt left), never after a timeout: the timed-out agent may still be running, so it goes on the report's attention list, and its work package gets no further round and no rollback. Default timeout 3 h, 6 h for the verify and performance engineers, the investigator and an issue's reproduction; a Verify round or a reproduction that needs longer says how long and gets it, once and at most twice its limit (with `stability` on, a Verify round gets up to 7 days, which G4 and its control need), unless `agent_timeout_ms` is given: a timeout the task gives stays the limit |

Everything but `inferpilot_dir`, `request`, `answers`, `exp_root`, `review_model` and the budgets is the task
definition; the result of every run that started records the definition it used (`task_def`), where
each field came from (`provenance`) and the intake's `assumptions`.

Human checkpoints: questions from the Intake (`needs_input`), and from Spec when a port's client does not
run its source's scenarios; the question for a model or a client, when an issue or a ci run reaches its
fix without one, or with InferPilot's client and only a part of its `workload`; the question after Setup
for the script that compares outputs (`output_script`), when the model of such a run answers no text
questions; any `stop_after`; with
`auto_approve_design`
set to `false`, L3 tasks and designs the architect flags stop at Design (`awaiting_design_review`; by
default they go on, and the report says no human approved the design); a review the reviewer marks `blocked`
stops before the PR; the PR engineer returns the exact `git push` instead of pushing; issue replies
are drafts in the run directory. A run that has its run directory ends with a report, whatever its
status; one that ends at the Intake (questions, a rejection) or before Setup created its directory
(`busy`) returns only its result.

Every result ends with `next`, what comes after the run, the same for Claude Code, a Cursor agent and the
runner's log: `say`, a sentence for you; `ask`, questions only you answer; `rerun_with`, the arguments to
set before running it again with the rest unchanged (answers merge with those already given); `start`, new
runs it offers, with their arguments: a scan's top tasks, the other problems of an issue's report;
`user`, what only you do: the push, a reply to post, a daily report the director could not share.

**One task per machine.** A run takes the machine before it creates anything — a lock in `/dev/shm`,
which every container started with `--ipc=host` shares with the host and no other machine sees — and
frees it at the end of its report. While another task holds it, a new run ends at once as `busy` and
names the holder. Every mode but scan also needs the machine's GPUs free of other jobs
(`scripts/gpu_status.py --check all`): another job on any of them ends the run at Setup as `busy` too,
and makes a later performance measurement not run. A lock whose run finished, whose runner (`runtime/run.mjs`, seen from the same
container) is gone, or which wrote nothing in its run directory for twice its longest agent call, one given
the most time it can get (24 h by default, 14 days with `stability` on; with `agent_timeout_ms` given, twice that timeout), is taken over by the next run. A run in another container is
never taken over for silence, since its run directory is not visible from here; once that run is
gone, the second command frees the machine, as it frees one a run that died left behind:

```bash
python3 scripts/run_lock.py status            # who holds this machine
python3 scripts/run_lock.py release --force   # free it after that run died
```

The framework's own skills (sglang's `.agents/skills/`) are referenced in place, never copied: each
role reads the ones [`knowledge/frameworks/sglang/skills.md`](knowledge/frameworks/sglang/skills.md)
lists for it from the task worktree, so they match the code it changes, with ROCm differences in
[`knowledge/platforms/rocm_skills.md`](knowledge/platforms/rocm_skills.md).

## Scripts

Standard-library Python; the GitHub ones read `GITHUB_TOKEN` (step 1; without a token
GitHub allows 60 requests an hour and 10 searches a minute per IP address).

| Script | Source of tasks |
|---|---|
| `scripts/scan_issues.py` | open AMD issues with linked PRs (and which of them close the issue), replies and the projected 60-day auto-close date |
| `scripts/scan_ci.py` | the AMD CI on main from its own runs and logs (each failing job: since when, last passed, failed tests, first errors), and the Failure Trackers' clusters |
| `scripts/eval_accuracy.sh` | accuracy of a running server through evalscope (the bootstrap installs it), the G2 gate (each set at most 1 point below the baseline): gsm8k on all 1319 questions by default; gpqa_diamond and simple_qa (LLM judge) as well when the task says the change may lose accuracy; other sets by name; every run sampled afresh, never from evalscope's cache; a set where a question got no answer gets no score; keys only from the environment |
| `scripts/stability.sh` | G4's load, with `stability` on: the run's `client_script` run back to back against one server until `HOURS` have passed (48 for G4; the pass in progress finishes), with one line per pass in `OUT_DIR/stability.jsonl`: the client's exit status and result, and the memory in use on every GPU and on the host. The first pass that fails ends it |
| `scripts/harness.py` | InferPilot's templates (`templates/harness/`): `show` lists the parameters of the server, performance client and accuracy templates with their defaults; `render` writes the ones a run uses (the server or client a user without a script of their own took, the accuracy script unless the task brings one, Spec's fallback client) with the task's values, then those the feature's source tests with, then for the server the model's sglang cookbook recipe (`--cookbook`), then the defaults, and `harness.json`, where each value came from |
| `scripts/scan_upstream.py` | merged upstream commits: new CUDA gates, CUDA-first features, new models |
| `scripts/audit_platform_gates.py` | platform gates to re-check on a new architecture (capability checks true on gfx1250, wave32 assumptions) |
| `scripts/audit_host_syncs.py` | host-device sync and copy candidates on the hot path (leads for a profile) |
| `scripts/baseline_cache.py` | baseline measurements kept for reuse in `<exp_root>/baseline_cache`, keyed by the framework commit, the model's files, the contents of the server, client and accuracy scripts, the workload, the accuracy sets with the contents of `scripts/eval_accuracy.sh`, the settings it reads and the evalscope version, the GPU, the running image and the rule it was measured under (every GPU idle); Setup takes a hit from the last 7 days instead of measuring accuracy and performance again, stores only a baseline that passed, and on a hit still probes the accuracy harness with a few questions |
| `scripts/gpu_status.py` | the GPUs by HIP number (the numbers `HIP_VISIBLE_DEVICES` takes: the order of their KFD nodes, which rocm-smi's Device numbers can differ from), each with its VRAM% and GPU% as rocm-smi shows them and the processes of any container holding memory on it; `--check all` fails while one is in use (either at 5% or more), and every mode but scan runs it when it starts and before each performance measurement |
| `scripts/leak_scan.py` | what a branch would publish about this machine (local paths upstream does not already carry, host names, internal sites, `root` commits) in the lines each of its commits adds, its commit messages and PR description; the PR engineer runs it before handing a branch over |

`scripts/restore_owner.sh` (POSIX sh) gives what root wrote in the container on a host mount back to
its owner (in `/workspace` there is nothing to give back); every agent runs it after a git write ([`knowledge/env_hygiene.md`](knowledge/env_hygiene.md)).
`scripts/check_framework_skills.py --framework-dir <dir>` checks the skills index against a framework
checkout: skills it names that are gone, and new ones it does not name yet (the scout runs it on every
scan). `scripts/find_skill.py "<query>"` lists the feature skills whose tags, id or feature name occur
in a request or task title, best match first (the intake and the director run it). `scripts/latest_image.py [--variant rocm10-mi30x]` prints the newest daily sglang image
(`run_docker.sh` starts from it; the director compares a run's image with it). `scripts/run_lock.py`
keeps one InferPilot task per machine (`acquire`, `release`, `status`). `scripts/daily_report.py check`
tells whether a daily report can be made public (no secret, local path, hostname or internal site), names every
pull request and issue by its title as well as its number, and fits in an issue; `latest` finds the
newest report shared in InferPilot's repository, which the next scan, in any container, continues from.
Both only read. `scripts/share_daily_report.py` shares a report that passes the check as an issue in
InferPilot's repository with `GITHUB_TOKEN`, titled with the report's date, unless that day's
report is there already (the director runs it at the end of a scan whose Dispatch passed).
`scripts/propose_knowledge.py` turns the curator's changes under `knowledge/` into a pull request
from `inferpilot/knowledge/<run>` to main, built on the current main in a throwaway worktree, and then
restores `knowledge/` in the checkout, so later runs read only merged knowledge.
`scripts/weekly_report.py` collects what the weekly report (`templates/weekly_release.md`) counts:
InferPilot's pull requests merged, closed or still open in a range, by default from 7 days back at 00:00
to now in UTC+8, or between `--since` and `--until`, main at its start and end with the change between,
and the sglang pull requests from InferPilot's work. It only reads.

## Tests

Before you push, run `tests/run_all.sh`: offline, without a GPU, in about ten seconds (Python 3.10+ and
Node.js 20+; shellcheck when installed). CI runs the same on GitHub's runners
([`.github/workflows/tests.yml`](.github/workflows/tests.yml)), one check per part on a pull request's page:
`changes` (the task groups the pull request reaches, with each file's reason in its summary), basic's five
steps, one per task group (skipped when the change does not reach it), the whole of `tests/run_all.sh` on
Python 3.10 and Node.js 20, and `tests-finish`, green when every job that ran passed. A push to main, every
night and on demand run every group.

Basic runs for every change: the syntax of every script, shellcheck, the runner, the two backends side by
side, and the checks below that keep the parts in step. A task group (`feature`, `issue`, `ci`, `perf`,
`scan`, `kernel`) adds the task's dry-run scenarios and the unit tests of its scripts, and runs when a change
reaches the task: code of `inferpilot.js` the task runs, a role it calls, or a file such a role reads
([`tests/groups.json`](tests/groups.json)). A change to what every task shares (`runtime/`, the dry run and
its fixtures, CI, code the main flow of `inferpilot.js` runs) runs every group.

```bash
tests/run_all.sh                                  # basic and every group; it says which step failed, and how
tests/run_all.sh --changed                        # basic and the groups your changes since origin/main reach, and why
tests/run_all.sh --group perf,kernel              # basic and these groups
node tests/dry_run.mjs --only scan,roles          # only the dry-run scenarios named scan: and roles:
```

| Kept in step | Checked by |
|---|---|
| the two backends: the same prompts, phases, retries, timeouts, models and result on `claude` and `cursor` | `tests/backend_parity.mjs` |
| the two READMEs: section by section the same headings, commands, inline code, numbers, links and tables | `tests/test_readme.py` |
| workflow and roles: every phase entered is in `meta.phases`; each mode's route is what it walks and what the READMEs list; every field an answer must have is named in its role's PHASE section | `tests/dry_run.mjs` |
| code and docs: every variable that setup, `run_docker.sh` and the runner read is in the READMEs, and other scripts name theirs; every flag the READMEs and `AGENTS.md` give exists | `tests/test_docs.py` |
| the public repository: no token or key, internal host, private address, real home or storage directory, or the name of this machine or its siblings | `tests/test_hygiene.py` |
| the split: every dry-run scenario and unit test is in basic or a group, and CI has a check per group | `tests/test_groups.py` |

To make merging wait for the tests, add a branch protection rule for `main` in the repository's Settings →
Branches (or a ruleset in Settings → Rules), turn on *Require status checks to pass before merging* and add
`tests-finish`, which is listed once it has run on a pull request.

## Layout

```
inferpilot/
├── inferpilot.js            # orchestration: five modes, gates, budgets, rollback, kernel back ends
├── runtime/                 # InferPilot's runner for inferpilot.js outside Claude Code: engine, CLI backends
├── AGENTS.md                # how an agent other than Claude Code (the Cursor app or CLI) starts and watches a run
├── roles/                   # intake, director, scout, dispatcher, analyst, architect, implementer,
│                            # test_engineer, verify_engineer, investigator, perf_engineer, issue_triager,
│                            # ci_guardian, kernel_liaison, reviewer, pr_engineer, curator
├── knowledge/
│   ├── dev_methodology.md   # phases, artifacts, gates (the contract)
│   ├── validation_doctrine.md, debugging_playbook.md, perf_evaluation.md, env_hygiene.md, pr_playbook.md
│   ├── acceptance.md        # what each kind of task must show, and who confirms it
│   ├── guardrails.md        # safety constraints and community conduct
│   ├── reward_hacks.md      # changes that move a number while breaking what it meant: tell, killed by
│   ├── dispatch.md, issue_triage.md, ci_triage.md, system_perf.md, composability.md, kernel_backends.md
│   ├── frameworks/sglang/feature_dev_map.md, platforms/rocm.md, known_issues.md
│   ├── frameworks/sglang/skills.md, platforms/rocm_skills.md   # sglang's own skills: index + ROCm notes
│   ├── kernels/aklr/skills.md, playbook.md   # AKLR's own docs (index); when a kernel gain lands in AKLR
│   └── feature_skills/      # validated plans per feature (hisparse-v2 first)
├── templates/               # task_definition, feature_spec, design, experiment_ledger, validation_report,
│                            # pr_body, issue_triage, ci_triage, perf_analysis, task_queue, daily_report,
│                            # weekly_release, acceptance
├── scripts/                 # task collection, audits, run_docker.sh, latest_image.py, run_lock.py,
│                            # restore_owner.sh, run_in_tmux.sh
├── inferpilot/bootstrap.py  # setup step 3, in the container; safe to re-run
├── setup.py, pyproject.toml # packaging; setup.py runs the bootstrap when pip builds the wheel
├── tests/                   # run_all.sh, groups.json; dry run of inferpilot.js, the runner's self-test, the two
│                            # backends side by side, unit tests of the scripts, the bootstrap and the docs
├── .github/                 # CI (workflows/tests.yml runs tests/run_all.sh) and the pull request template
├── README.md, README.zh-CN.md
└── docs/design.md
```

## Relationship to kernel workflows and Hyperloom

- A kernel's speed is the kernel workflow's job, on whichever back end is selected: InferPilot writes a
  functional kernel with a unit test, hands over the op spec with the reference as the oracle, and
  integrates a verified gain.
- Flag and backend searches for a workload belong to Hyperloom; its upstream candidates are InferPilot
  inputs, and features InferPilot lands become its options.

## Origin

The method, validation doctrine, known-issue register, templates and the HiSparse V2 feature skill come
from GEAK's `framework_workflow` (branch `fangyuan/framework_agent_init`, commit `fc857c9`), which was at
its knowledge-first phase. InferPilot adds the orchestration that was planned there, the roles, the issue /
CI / performance / scan entry points from the design, and the task-collection scripts. It runs without
GEAK: Claude Code's Workflow tool or InferPilot's own runner (`runtime/`) executes it.
