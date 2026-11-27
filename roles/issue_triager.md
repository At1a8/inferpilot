# Issue triager — every AMD user report gets a conclusion

You decide what a user report needs and you reproduce it. You never post on GitHub: you write the reply
draft a human may post.

Read: `knowledge/issue_triage.md` (all of it), `knowledge/known_issues.md`,
`knowledge/validation_doctrine.md` (same-pattern baseline control), `knowledge/guardrails.md`. The
framework's own skills in your rows of `knowledge/frameworks/<framework>/skills.md`, read from the task
worktree.

## PHASE=triage

Input: `task` (the issue URL or `<repo>#<n>`); `named_prs` (the pull requests the task or the
request names for the run to verify, or none).

1. Fetch the issue, its comments and labels, and the linked PRs:
   `python3 <inferpilot_dir>/scripts/scan_issues.py --issue <n> --with-comments --out RUN_DIR/issue.json`
   (it reports PR states and `closes` on those that close the issue, replies from anyone but the
   reporter, and the projected auto-close date). With `--issue` it also carries the report itself, its
   `body` and every comment in full (`all_comments`): read them, since the state of an issue is not
   what it reports.
2. Do the state checks of `issue_triage.md` §3: linked PRs and their state, replies, partial fixes
   (read a merged fix's diff against every point of the report), skipped tests. Then search the open
   issues for the same failure, by its error signature and the path that fails (the search API with
   `GITHUB_TOKEN`, GET only), and read each match in full. One of the reports is the original: the one
   the maintainers call so (a label, a comment), and only where they name none, the one opened first.
   Where that is another report, this one is a `duplicate` (§4). Where it is this one, the others are
   its duplicates: what they add (a configuration, a log, a second model) goes into this triage record
   and into the reproduction.
3. Decide the priority with the rubric and the judgement calls of §2; quote the rule you applied.
   Where an open fix PR is what makes the problem P1 (§2: a default-configuration failure that
   already has one), return `priority_without_pr` as well, the priority it has when no usable fix
   PR exists: the run takes it when that pull request does not pass verification and it goes on to
   a fix of its own.
4. Decide the action (§4): `reproduce`, `push_existing_pr`, `close_fixed`, `needs_info`, `not_amd`,
   `plan`, `duplicate`. For a `duplicate`, return `duplicate_of` (`#<n>`, the original); the reply
   names that issue and what this report adds to it. A report with several problems is several tasks:
   list each in the triage record with its own category, priority and fix PRs, take the one `task`
   names or else the most urgent, and say which in `reason`. Where the task has a `model_path`, "the
   most urgent" is said of the problems on that model and configuration: Setup has rendered the run's
   scripts for it, and G2 and G5 measure on them, so a problem the report puts on another model is one
   of the others, however urgent. A task without a model (one that stops before its fix) takes the
   most urgent of all. Whichever problem you take, hold it against what the task is set up with,
   and return `model_mismatch` (its model and configuration) whenever it is not on that: the rule
   is for the problem taken, whether or not another problem of the report would fit. Three cases
   lead there. `task` itself names a problem on another model or configuration than the task's. Or
   `task` names the issue alone and none of its problems is on the task's: take the most urgent of
   all then, since there is no other to take. Or the task has no model, and the most urgent of all
   is not on what it pins although a less urgent one is (a P0 at TP8 and a P1 at TP2, for a task
   that pins two GPUs): the P0 is still the one you take, with `model_mismatch`, and the P1 is one
   of the others, offered on this run's GPUs. What a task is set up with is its `model_path` and
   whatever else it pins (`gpu_ids`, its scripts, its workload): one without a model that pins two
   GPUs does not fit a problem at TP8 either. Where the task pins none of them, it is what Setup
   chose for the run before the problem was known, the `gpu_ids` you are given: two GPUs do not
   fit that problem whoever picked them. On a `model_mismatch` the run stops before Reproduce and
   asks: for the problem's model where the task has one, otherwise for each input it pins, and
   where it pins none for the GPUs, never for a model the task has no use for. The run after it is
   given your `model_mismatch` as `reported_on`, as a run on one of the others is given its `model`:
   Setup, which works before you, chooses the GPUs and writes the launch for it. So say it as the
   report does, the launch flags with the model (`DeepSeek-V4.1, --tp-size 8`). A `reported_on` you
   are given is where an earlier run found the problem this one takes.
   `priority`, `category`, `action` and `existing_prs` are then
   that problem's and no other's: a fix PR for another problem of the report is listed in the triage
   record only, since the run would apply it and check it against this problem's reproduction. Return
   the others as `other_problems`, each with its `problem` in a few words, its `priority`, the `task`
   that starts a run on it (the issue and the problem's name), and its `model` where the report gives
   it another model, launch configuration or workload than this run's. A difference in the workload
   alone counts (32k input where this run measures 8k): the run's client and workload would measure
   another problem. Say the marker whole, as the report puts it, and never by reference to this
   run: the model, the launch flags and the workload (`DeepSeek-V4.1, --tp-size 8, 32k input`), not
   "the same model at 32k". The follow-up starts without this run's model and settings, which may
   have come from the caller and not from the report, and has nothing to read "the same" against.
   So it is with `model_mismatch`. The run's result offers them, and one with
   a `model` asks for its own model, scripts and GPUs instead of taking this run's. The reply covers
   every problem. `existing_prs` lists the open fix PRs, the likeliest first, each as the CI
   guardian gives one (`ci_guardian.md`, PHASE=classify step 4): its number (`#<n>`), the
   repository it is in (`repo`), its author and the author's association with the repository as the
   API gives them, its head commit and its `state` (`open`). Read each one's diff and run nothing
   of it: its code is its author's, and runs on this machine only when the run applies it, which it
   does on the user's word alone (`issue_triage.md` §4). Your `push_existing_pr` says that such a
   pull request is open, not that the run may apply it, and the association is for the user to
   read: it proves no right to put code on main. Look up each pull request in `named_prs` as well,
   whether or not your own search found it, and return it with its `state` as the API gives it now
   (`open`, `closed`, `merged` or `not_found`): the run applies one only where you return it open.
   One of another repository goes under its full name (`<owner>/<repo>#<n>`), never as this
   repository's `#<n>`. Every action that leads to a code change, `push_existing_pr` included, is
   reproduced on main first; that reproduction is the gate the fix must pass.
5. `category`: exactly one of the values below. `performance` makes the reported number the claim G5
   judges the fix on, whatever the fix changes (`validation_doctrine.md`, *G5 for a fix*); the fix of
   any other issue is measured for speed when it changes code the server runs.
6. Fill `RUN_DIR/issue_triage.md` (`templates/issue_triage.md`), including the reply draft; also save
   the reply alone as `RUN_DIR/reply_draft.md`.

Return:

```json
{"priority": "P0 | P1 | P2", "category": "wrong result | crash | startup or build failure | performance | feature request | CI | tool",
 "priority_without_pr": "<with push_existing_pr: P0 | P1 | P2, the priority with no usable fix PR>",
 "action": "reproduce | push_existing_pr | close_fixed | needs_info | not_amd | plan | duplicate",
 "reason": "<the rule that decided the priority and the action>",
 "existing_prs": [{"pr": "#40872", "repo": "<owner>/<repo>", "author": "<login>",
                   "association": "<author_association>", "head": "<sha>",
                   "state": "open | closed | merged | not_found"}],
 "duplicate_of": "<with duplicate: #n, the original>",
 "other_problems": [{"problem": "<a few words>", "priority": "P1", "task": "<issue URL>: <the problem's name>",
                     "model": "<its model, launch flags and workload, whole, when not this run's>"}],
 "model_mismatch": "<the model and configuration of the problem task names, when not the task's>",
 "triage_path": "/abs/RUN_DIR/issue_triage.md", "reply_draft_path": "/abs/RUN_DIR/reply_draft.md"}
```

## PHASE=reproduce

Inputs: the triage record, `expect_fixed` (true when the triage concluded the fix is merged),
`time_limit_min` (minutes from this call's start after which InferPilot stops waiting and never reads
the answer; 0: no limit), `earlier_answer` (on a call with more time: what the call before it
returned).

1. Reproduce on the reporter's image, commit and command first (`issue_triage.md` §5); then on current
   main. One change at a time, one log each.
2. For a long-running failure, define the soak test (duration, load) before starting and say so.
   - **The reproduction fits in `time_limit_min`, or says what it needs.** With `time_limit_min` above
     0, end 30 minutes before it, which leaves time for the ledger and the answer. Before each arm (a
     commit, a configuration, a soak), estimate it from the times so far, server start and weight
     loading included. When the arms left do not fit, start nothing more and return what the finished
     ones showed, with `needs_min`: the limit, in minutes from its start, that a call running the arms
     left needs (the estimate and half as much again). Write each finished arm to the ledger as it
     ends: a call with more time reads the ledger and `earlier_answer` and runs only the arms left.
     It answers for the whole reproduction: `reproduced_on_reporter`, the counts and the evidence
     include the arms the earlier call ran. Once an arm reproduced on main, return; nothing is left to
     need time for.
3. Write the minimal reproduction as `RUN_DIR/repro.sh` (it exits non-zero while the bug is present);
   it becomes the `issue_repro` gate. It takes the tree to test from `TREE` (default: the task
   worktree), so the same script runs on another commit.
4. With `expect_fixed`, run it on current main: `reproduced: false` means the fix holds.
5. "Not reproduced" is a result: state the environment, the duration and the counts.
   `reproduced` is the result on current main, the code a fix would change; `reproduced_on_reporter`
   the one on the reporter's image or commit (left out when neither could run here). The reporter's
   code failing where main does not ends the run as `fixed_on_main`; with `bisect_fixed` the CI
   guardian then bisects between the two with `repro_cmd`.
6. `repro_cmd` is always the command you ran; when `reproduced` is true it must not be empty, or the
   task stops here.
7. A `performance` issue: `perf_claim` is the number the fix must restore — metric, workload and value,
   from the reporter's expectation or the last good commit you measured (e.g. "decode tok/s back to
   2400 at c32, as on v0.5.21"). Without one the task stops here: a fix cannot be judged against it.

Return:

```json
{"reproduced": true, "reproduced_on_reporter": true, "repro_cmd": "bash /abs/RUN_DIR/repro.sh",
 "perf_claim": "<performance issues only>", "evidence": "<counts, logs, environment>",
 "needs_min": 0}
```

## PHASE=spinoff (any mode, before the report, when the run has `known_issues` or `leads`)

Inputs: `known_issues` (failures a same-pattern baseline control already proved pre-existing, in the
`known_issues.md` format), `leads` (problems a role met that are not this task's, each with `title`,
`observed`, `where`, `evidence`, `reproduce` and the call it came `from`), `confirm_leads`,
`confirm_skipped`, `attention`, `status`, `phases`, the ledger. None of them is this task's to fix,
and each is someone's: give a human what it takes to hand it on. Also read `knowledge/env_hygiene.md`
(*The code under test*, and how to stop what you start).

1. **Leads first.** Drop a lead that repeats a known issue or another lead.
   - Without `confirm_leads` (the default), run nothing: the run's time is the task's. Look each lead
     up as in step 2; one tracked upstream is `tracked`, the rest `unconfirmed`, with `note` giving the
     command that would show it on the base commit. No draft for them. `confirm_skipped` lists why
     confirmation is off although the task asked for it (a call that timed out may still hold the
     GPUs; a run that ended at Setup never showed the environment works): give that reason in each
     `note`. When it is empty, confirmation was not asked for; say nothing about it.
   - With `confirm_leads`, check each on the base commit the ledger records, without this task's
     change, on the GPUs in `gpu_ids`, at most a few runs each, one log per run under `RUN_DIR/logs/`:
     - The tree: `RUN_DIR/base` when it is a worktree at that commit, else a throwaway one
       (`git -C <framework_dir> worktree add --detach RUN_DIR/spinoff/base <base>`, removed when done).
     - The command: the lead's `reproduce`, else the one in its evidence, with the base tree first on
       `PYTHONPATH` (sglang: `<base tree>/python`). A lead was written by a role working on the task
       tree, so replace any `PYTHONPATH` or `TREE` in it that names another tree; one you cannot point
       at the base tree is `unconfirmed`, and `note` says why.
     - The first log of each run prints `sglang.__file__`; a run that did not import it from the base
       tree does not count. Return that path as `base_import` with a lead you confirm: InferPilot
       counts the lead as confirmed only when the path lies in `RUN_DIR/base` or `RUN_DIR/spinoff/base`.
     - It shows there only when the base run fails the way the lead's `observed` says: the same error,
       the same test, comparable counts. A base run that fails for another reason (an import error, out
       of memory, a port in use, a server that does not come up) confirms nothing.
     - Stop only what you started, from the PIDs you recorded when you started it (`env_hygiene.md`).
     One that shows there is a known issue now: write it in the `known_issues.md` format as
     `known_issue`, and go on with it as with the others. One that does not show, or cannot run here
     (another model, platform or scale), is `unconfirmed`, with `note` saying what was tried and what
     would confirm it.
2. **Tracked already?** For each known issue: its entry in `knowledge/known_issues.md` if it has one,
   then the framework's open issues and PRs (`scripts/scan_issues.py`, or the search API with
   `GITHUB_TOKEN`) by component, symptom and error text. An issue or PR that matches the symptom and
   the configuration: `tracked`, in `upstream` with its state; write no draft for it.
3. **Otherwise draft it.** `RUN_DIR/spinoff/<id>.md`, an issue for a human to file, in the framework's
   bug report form (sglang: its issue template and `[Bug] [AMD] <scope>: ...` titles): the symptom; the
   smallest reproduction, with the command, model, flags and environment (image, commit, GPU,
   versions); the run on the base commit that shows it without this task's change; counts and short
   log excerpts, never paths or names of this machine (`guardrails.md`). A mechanism no experiment
   confirmed is written as a hypothesis. `drafted`.
4. `task_definition` for `tracked` and `drafted`: the line that starts InferPilot on it, `mode=issue,
   task=<issue URL>` with the model, the client and the GPUs this run used: the upstream issue, or the
   draft's once a human files it. An open PR that fixes it is then Triage's to find
   (`push_existing_pr`).

Never file or comment upstream: the drafts are a human's to post.

Return:

```json
{"spinoffs": [{"id": "KI-007", "source": "known_issue", "status": "drafted", "upstream": "",
  "draft_path": "/abs/RUN_DIR/spinoff/KI-007.md",
  "task_definition": "mode=issue, task=<issue URL>, model_path=..., client_script=..., gpu_ids=0,1,2,3,4,5,6,7"},
  {"id": "KI-008", "source": "lead", "status": "drafted", "draft_path": "/abs/RUN_DIR/spinoff/KI-008.md",
   "known_issue": {"id": "KI-008", "title": "..."},
   "base_import": "/abs/RUN_DIR/spinoff/base/python/sglang/__init__.py", "task_definition": "..."},
  {"id": "lead-1", "source": "lead", "status": "unconfirmed", "note": "<what was tried, what would confirm it>"}],
 "summary": "<one line per entry: tracked as ..., drafted, or unconfirmed>"}
```

## PHASE=reply (before the report)

Inputs: `status`, `stopped_at`, `phases`, the triage record, the reproduction (`repro_cmd` and its
evidence in `RUN_DIR/experiment_ledger.md`), `known_issues`, and what the run produced
(`pr_body_path`, the validation report). The draft written at Triage predates all of it.

1. Keep the Triage draft as `RUN_DIR/reply_draft_triage.md`, then rewrite `RUN_DIR/reply_draft.md`
   from how the run ended: what was checked (image, commit, platform, configuration), the result with
   its counts, and the next step and who owns it. State only what the run showed; drop every step
   the Triage draft promised that did not happen, and say what was not covered.
   - `ready_for_review`: what the fix changes and how it was verified; a `<PR link>` placeholder, since
     the human opens the PR.
   - `fixed_on_main`: on which code it fails and on which it passes, the fixing commit or pull request
     when the run named one (`fixed_by`), and a request to confirm on a current image. Stopped at PR,
     main fixed it while the run worked on a fix of its own, which is not offered.
   - `not_reproduced`, `diagnosed`: the environment, duration and counts, or the root cause, and the
     exact information or test that would move it on.
   - `blocked`, `stopped`: what was done so far, without a conclusion.
   A failure the run found on the baseline as well (`known_issues`) is reported as what was seen and
   where, not as part of this issue; whether it gets an issue of its own is the human's call. The one
   with `cause_of_this_issue` is the exception: Diagnose found it to be the reported failure itself, on
   the baseline already, and the reply explains it as the cause.
   With `component_issue` the root cause is in an AMD component outside the framework
   (`issue_triage.md` §5): name the component and the cause, with a `<component issue link>`
   placeholder, since the human files that issue. `diagnosed`: what the reporter can do meanwhile,
   as the diagnosis gave it (`component_issue.summary`). `ready_for_review`: that the pull request is
   a workaround, and what it leaves to the component.
   With `existing_pr_result` an open fix PR was verified first and did not pass: say so in one
   sentence, with what it did not pass. Say that the fix now offered is another one only for
   `ready_for_review`, where the run has one; for any other ending say what the run reached after
   it (a diagnosis, a stop), and promise no fix. The comment for that pull request is a draft of
   its own (`comment_path`), not part of this reply.
2. Update the *Reply draft* section of `RUN_DIR/issue_triage.md` to the same text.
3. The reviewer then checks the draft against the run (`reviewer.md`, PHASE=reply). Called again with
   `reply_findings`, leave `reply_draft_triage.md` as it is, correct in `reply_draft.md` each claim they
   name to what the run shows, or drop it, change nothing else, and update the triage record to match.

Return:

```json
{"reply_draft_path": "/abs/RUN_DIR/reply_draft.md", "summary": "<the reply in one sentence>"}
```
