# PR playbook — from a working branch to a reviewable upstream PR

## Before opening the PR

InferPilot's implementer meets these in every work package; the reviewer and the PR engineer check them and
send what is missing back as work packages, so the branch that is pushed is the one that was verified.

- [ ] Debug instrumentation removed, or gated behind an environment switch that is off by default.
- [ ] Feature-off check (G0) passes on the final commit; a change without a switch has none, and the
      existing tests of the modules it touches pass there instead, or fail the same way on the base,
      which the description then says (`validation_doctrine.md`).
- [ ] The target repo's formatters/linters pass locally (sglang: `pre-commit run --all-files`, which
      includes isort, ruff format and clang-format; CI's `lint` job runs the same).
- [ ] New tests registered where the repo's CI discovers them (sglang: `test/registered/...` with the
      CI registration helper), not only in a private scratch directory.
- [ ] Only tests CI runs, the fewest that cover the change: one end-to-end accuracy test at the
      defaults for a change to outputs (sglang: GSM8K under `test/registered/amd/accuracy/`, as its
      `write-sglang-test` skill describes).
      InferPilot's gate oracles and harnesses stay in the run directory.
- [ ] The smallest diff: code CUDA also runs changes only by addition, behind the switch or a
      module-level platform constant (sglang: `_is_hip = is_hip()` at import); no refactor, rename,
      reformatting or moved code; no check repeated where an earlier one holds; no unused helper.
- [ ] Nothing private: `scripts/leak_scan.py` finds no local path, host name, internal site or `root`
      identity in the diff, the commits or the description (a path upstream already carries is public;
      `--allow` passes what else is, with its reason in the ledger).
- [ ] The repository's own CPU tests for the changed modules pass (sglang: `test/registered/unit/`,
      `test_server_args_docs_coverage` for a new argument).
- [ ] The command the description gives to show the change works (the launch command; for a CI fix
      the failing test, for an issue fix the reproduction) was run once on the final commit, as written
      but with the local copy of the model in place of its id.
- [ ] Docs updated for the user-visible switch, and no more than the switch and CI need.
- [ ] Commits tell the story: one per work package, follow-up fixes squashed into the commit they fix.
- [ ] Description written from `validation_report.md`, citing artifacts, with the attribution line word
      for word (§Attribution).

## Title

Follow the target repo's convention. sglang: `[Type] [Hardware] scope: title`, with Type one of Feat,
Fix, Refactor, Perf, Doc, Test, CI, Chore, an uppercase hardware tag (AMD) and a module or model as the
scope, e.g. `[Feat] [AMD] DeepSeek-v4.1: Support HiSparse V2 with HiCache-backed compressed KV`. Use
"X-backed" (supported by X) rather than "X-backend" (a noun that, in sglang, suggests a storage backend).

## Description

Write it into the repository's own PR template and keep the template as it is: its comments, its
headings in order, the checklist wording and the closing sections (sglang:
`.github/pull_request_template.md`, mirrored in `../templates/pr_body.md` with the attribution line
added; check it against upstream, which changes it now and then). Editing an existing PR's
description, keep the block a bot maintains at the end (sglang: `<!-- pr-states:start -->` …
`<!-- pr-states:end -->`).

Write it the way a person tells reviewers about their change, not like a report: short sentences, first
person, everyday words, contractions where they read naturally. No bold label on every bullet, no
footnotes, no definitions of your own shorthand. Read two or three merged PRs of the same area first
(sglang #42297 and #44588 for speculative decoding).

- **Motivation:** at most 100 words, why the PR exists: the problem and what the change brings. Few or
  no numbers; the results sections carry them. `Depends on #NNNN` for unmerged dependencies.
- **Modifications:** one sentence per changed file, saying what changed in it; every file of the diff
  appears once. For an AMD change, open with **This is HIP-only and doesn't touch the CUDA path.** and
  say how the diff makes sure of it (an `is_hip()` gate, a ROCm-only file, a startup rejection on other
  platforms, new arguments that stay `None` on CUDA). Check it in the diff first: every changed line that
  CUDA also runs must sit behind such a gate. A limitation is a sentence here, not a new section.
- **Accuracy Tests:** GSM8K on all 1319 questions with the feature at its default settings, next to the
  baseline measured with the same harness; name the model, hardware, TP, harness and sampling. A loss the
  validation found elsewhere gets one sentence; other suites only when the change targets them (NIAH for
  a long-context feature). A fix that changes anything but tests and documentation gives the same two
  scores, the fix next to the base (`validation_doctrine.md`, *G2 for a fix*); where the base cannot
  serve the configuration, next to the last commit that served it, named, or alone, saying that it
  has no baseline. A fix to tests or documentation alone says so, and gives no table; one for a model
  that answers no text questions says that instead.
- **Speed Tests and Profiling:** the baseline against the feature in one scenario (input and output
  lengths, concurrency, hardware, TP), the same harness and runs for both arms, absolute numbers and the
  change. Show every point of the scenario that was measured, not only the best one. Commands go in a
  collapsed `<details>` block. A feature that claims no gain gives the same table: a reviewer reads in
  it that the feature is no slower (`validation_doctrine.md`, *G5 for a feature*). So does a fix that
  changes code the server runs, the fix next to the base (*G5 for a fix*); where the base cannot serve
  the configuration, next to the last commit that served it, named, or alone, saying that it has no
  baseline. The fix of a performance problem shows the number that fell and the number it restores.
  A fix to tests, documentation or CI files alone says that it changes no code the server runs, and
  gives no table.
- **Checklist:** tick only what holds for this PR.

Every number comes from `validation_report.md` or the experiment ledger.

## Attribution

Every upstream PR InferPilot prepares says so in one line of its description, word for word:

```text
Developed with the [InferPilot](https://github.com/At1a8/inferpilot) AI agent; reviewed and measured by AMD engineers.
```

- One wording, so `InferPilot in:body` on GitHub finds every such PR and only those; never reword it.
- A paragraph of its own: in sglang's template after the Checklist's last item, before `## Review and
  Merge Process` (`../templates/pr_body.md` has it there); in another template at the end of the
  description, above any line the template says is removed (vLLM: `BEFORE SUBMITTING`) and above a
  block a bot appends.
- InferPilot writes the line; the human who pushes makes it true. Before pushing, they read every
  changed line and re-run the PR's tests and measurements themselves, as the *Before pushing* section
  of `validation_report.md` gives them: on the machine's lock with every GPU idle, each arm's tree made
  again from its commit, every command of an arm run from it with its import checked, and measured as
  the run did; Report has freed the machine and removed the baseline worktree by then. A result that
  differs from the description's by more than run-to-run noise replaces it there. A PR nobody has
  re-run is not pushed.
- The verification comment for an existing PR (the PR engineer's step 5) rewrites no description and
  carries no attribution line; it is re-run before it is posted all the same.
- A repository that asks for more disclosure of AI assistance gets it as well (vLLM: the "AI
  assistance was used" box of its PR template, its `/pr-checklist` skill, and a trailer such as
  `Co-authored-by:` on each commit).

## Building on an unmerged PR

- Cherry-pick the dependency's commits as the base of your branch and say `Depends on #NNNN` in the
  description; reviewers then read only your commits.
- If you must change the dependency's content on your branch (e.g. revert its docs so your PR carries no
  docs change), make it a separate commit whose message says to **drop it together with the
  dependency's commits when rebasing after the dependency merges** — otherwise your PR's final diff will
  revert the dependency in main.
- After the dependency merges: rebase onto main, drop its commits and any such revert commits, re-run
  the gates.

## Keeping up with main

- Before the PR exists, InferPilot only trial-merges main to report conflicts; it does not update a
  verified branch. Updating it is the owner's step, and the gates run again on the merged tree.
- Prefer merging main into the PR branch (no force push, review history kept); maintainers squash at
  merge time.
- Do a trial merge in a throwaway worktree first; list conflicts; compile-check and smoke-test the merged
  tree (start the server from the worktree via `PYTHONPATH`) before touching the branch.
- sglang appends every new built-in `ServerArgs` field at the end of `POSITIONAL_FIELD_ORDER`
  (`srt/arg_groups/field_order.py`, checked by `test_builtin_fields_have_explicit_positional_slots`), so
  each upstream PR that adds a server argument conflicts there with an open PR that adds one. Keep
  upstream's new lines and re-append yours after them; expect it again at every update.
- After a merge, run the unit directories of every subsystem the PR touches one file per process (one
  aborting test must not hide the rest) and compare each failure with the same file on an untouched
  worktree of main. Tests that build objects by hand (a `SimpleNamespace` of server args, `__new__`
  plus attribute setup) break when the code reads a new field: add the field to the stub, as upstream
  does (`feature_skills/fly-dspark`).
- Resolve a modify/delete conflict by following upstream's intent: if main deleted a file in a cleanup,
  do not resurrect it to keep a test you added there — move or drop the test and say so in the merge
  commit message.

## CI on sglang (as observed)

- `lint` and title checks run on every push; most test jobs need a maintainer to add the `run-ci` label,
  so gate jobs (`pr-gate`, `*-finish`) show as failed on a fresh fork PR — expected.
- A docs change on a fork PR produces a **neutral** "Mintlify Preview (fork)" check (preview build waits
  for a maintainer's approval). Neutral does not block merging.
- AMD CI runs separately and also needs to be requested.
