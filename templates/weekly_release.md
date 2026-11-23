# InferPilot weekly release <YYYY>-W<ww> — <Monday YYYY-MM-DD> to <Sunday YYYY-MM-DD>

<!-- A week of InferPilot's work in the shape of an sglang release note: Highlights a reader acts on,
     then the full notes by area, Breaking Changes & Upgrade Notes, Contributors and the Full Changelog.
     A progress report, not posted to GitHub, in English (`weekly_release.md`) and in Chinese
     (`weekly_release.zh-CN.md`): the same sections, tables, inline code, numbers and links, as
     `tests/test_templates.py` checks. Its sources: the week's runs (each container's
     `exp/index.md` and its run directories), the daily reports shared in InferPilot's repository,
     InferPilot's pull requests merged in the week, and the sglang pull requests pushed from
     `inferpilot/<slug>` branches. Every line links to its evidence: the upstream pull request or issue,
     the daily report, the InferPilot pull request, or the run by its directory's name
     (`issue_41299-glm-dcp_20261106-071502Z`). A week with nothing in a section says none. Times are
     UTC. -->

| Week (UTC) | sglang main | Images | Runs | Previous weekly |
|---|---|---|---|---|
| <Monday> 00:00 to <Sunday> 24:00 | `<sha of the first scan>` to `<sha of the last>` | `<the daily images the runs used>` | <n> runs in <c> containers | <link, or none> |

# Highlights

*<n> runs (<f> feature, <i> issue, <c> ci, <p> perf, <s> scan); <r> pull requests ready for review, <m> merged upstream; <u> user issues answered.*

<!-- Three to six bullets, as sglang's "What's new": what reached AMD users this week, each with its
     measured number and its pull requests. Merged work first, then work ready for review; a problem
     only when it blocks AMD (a red CI job on main, a P0 without a fix). -->

* <what changed for AMD users, with the number that shows it> ([#<n>](<url>), run `<run directory>`).

## Upstream pull requests

<!-- Every sglang pull request an InferPilot run prepared and a human pushed that opened, changed or
     merged this week, merged first. Gates: each one's verdict as the validation report records it,
     "not run" for one that did not run; a CI fix has `failing_test` and no G5, an issue fix
     `issue_repro`, and G5 only when the issue is about performance. Result: the gain with its
     workload, or for a fix what passes again. Review is upstream's state. -->

| Pull request | Kind | Model / feature | Platform | Gates | Result | Review |
|---|---|---|---|---|---|---|
| [#<n>](<url>) <title> | feature / issue / ci / perf | | MI30x / MI35x | <each gate's verdict, e.g. G0-G2 pass, G3 not run, failing_test pass> | <the gain and its workload, or what passes again> | merged / approved / changes requested / open / closed |

## To use

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/At1a8/inferpilot/main/scripts/run_docker.sh)
```

<!-- The InferPilot commit the week ended on and the newest daily image; whatever a user must do before
     the next run goes under Breaking Changes & Upgrade Notes. -->

| InferPilot | sglang image |
|---|---|
| `<main sha>` ([changes](https://github.com/At1a8/inferpilot/compare/<sha of the previous weekly>...<main sha>)) | `rocm/sgl-dev:<the newest daily tag>` |

# Full Notes

## Features brought to AMD

* [Feat] [AMD] <scope>: <title> (<the gain measured, or the parity shown>): [#<n>](<url>), run `<run directory>`

## User issues

<!-- The issues runs took this week, P0 first: the conclusion each run reached and what a human did
     with it. -->

* [#<n>](<url>) <title> (<P0 / P1 / P2>): <reproduced / not reproduced / fixed on main / diagnosed / fixed by [#<n>](<url>)>; reply <posted / drafted>

## CI on main

* [<job>](<job URL>): <class>, <the culprit commit or pull request>; <fixed by [#<n>](<url>) / handed over / flaky, rate measured>

## Performance

* <model>, <workload>: <the gain, measured alone at equal budget (G5)>, or dropped: <why> ([#<n>](<url>), run `<run directory>`)

## Kernel hand-offs

* <operator> of <model> on <platform>: <recorded / integrated / no gain> (<the speedup>), run `<run directory>`

## Knowledge

<!-- The curator's pull requests to InferPilot merged this week: feature skills validated, known
     issues, sglang skills indexed, ROCm notes, reward hacks. -->

* knowledge: <title>: [#<n>](https://github.com/At1a8/inferpilot/pull/<n>)

## InferPilot

<!-- InferPilot's own pull requests merged this week other than the curator's: its runner, roles,
     gates, scripts and templates. -->

* <area>: <title>: [#<n>](https://github.com/At1a8/inferpilot/pull/<n>)

## Daily reports

* <YYYY-MM-DD>: [#<n>](https://github.com/At1a8/inferpilot/issues/<n>), <failing CI jobs>, <open P0 issues>, <tasks ranked>

## Breaking Changes & Upgrade Notes

<!-- What a user must change before the next run: a token or its permissions, an argument renamed or
     removed, a bootstrap or image change. A bold sentence, then what to do. -->

* **<what changed>.** <what to do>: [#<n>](https://github.com/At1a8/inferpilot/pull/<n>)

## Not done

<!-- What waits on a human, and what stopped: a branch to push, a reply draft to post, a design to
     approve, a run blocked or stopped, with why and what a human does next. -->

* <task>: <status> at <phase>, <why>; next: <what a human does> (run `<run directory>`)

## Next week

<!-- The top tasks of the week's last daily report, ranked with §6 of dispatch.md. -->

| Rank | Task | Source | Type | Level | Priority |
|---|---|---|---|---|---|
| 1 | | | | L1 / L2 / L3 | |

## Numbers

| Mode | Runs | Ready for review | Other outcomes | Agent hours | GPU hours |
|---|---|---|---|---|---|
| feature | | | | | |
| issue | | | | | |
| ci | | | | | |
| perf | | | | | |
| scan | | | | | |

## Contributors

<!-- The people who pushed, reviewed or merged InferPilot's work this week, as sglang names its new
     contributors. -->

* @<user>: <pushed / reviewed / merged> [#<n>](<url>)

**Full Changelog**: https://github.com/At1a8/inferpilot/compare/<sha of the previous weekly>...<main sha>
