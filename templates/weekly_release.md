# InferPilot weekly release <Tuesday YYYY-MM-DD> to <Monday YYYY-MM-DD>

<!-- A week of InferPilot in the shape of an sglang release note: Highlights a reader acts on, then the full
     notes by area, Breaking Changes & Upgrade Notes, Contributors and the Full Changelog. A progress report,
     not posted to GitHub, in English (`weekly_release.md`) and in Chinese (`weekly_release.zh-CN.md`): the
     same sections, tables, inline code, numbers and links, as `tests/test_templates.py` checks. It is written
     from the repositories, not from any machine's runs: InferPilot's pull requests merged in the week, and
     the sglang pull requests from its work, pushed from `inferpilot/<slug>` branches or recorded by its pull
     requests as its ports. Every line links to its pull request or issue. The title's two dates bound the
     week, from Tuesday 00:00 to the next Monday 24:00 in UTC+8; a section with nothing in the week is left
     out. -->

# Highlights

*<m> pull requests merged from <c> contributors, <x> closed without merge; <u> sglang pull requests from InferPilot's work opened, <k> merged.*

<!-- Three to six bullets, as sglang's "What's new", each opening with a short title in bold: what InferPilot
     gained and what it brought to sglang on AMD this week, with its pull requests and, where it was measured,
     its number. Merged work first. -->

* **<a short title>:** <what changed, with the number that shows it> ([#<n>](<url>)).

## Upstream pull requests

<!-- Every sglang pull request from InferPilot's work that opened, changed or merged this week, merged first.
     Gates: each one's verdict as the pull request or its feature skill records it, "not run" for one that
     did not run; a CI fix has `failing_test` and no G5, an issue fix `issue_repro`, and G5 only when the
     issue is about performance. Result: the gain with its workload, or for a fix what passes again. Review
     is upstream's state. -->

| Pull request | Kind | Model / feature | Platform | Gates | Result | Review |
|---|---|---|---|---|---|---|
| [#<n>](<url>) <title> | feature / issue / ci / perf | | MI30x / MI35x | <each gate's verdict, e.g. G0-G2 pass, G3 not run, failing_test pass> | <the gain and its workload, or what passes again> | merged / approved / changes requested / open / closed |

# Full Notes

<!-- By area: what the week's sglang pull requests bring to AMD first, then InferPilot's own pull requests. -->

## AMD feature porting

* [Feat] [AMD] <scope>: <title> (<the gain measured, or the parity shown>): [#<n>](<url>)

## Issue fixes

<!-- The AMD user issues the week's pull requests fix, P0 first. -->

* [#<n>](<url>) <title> (<P0 / P1 / P2>): fixed by [#<n>](<url>); reply <posted / not posted>

## CI fixes

* [<job>](<job URL>): <class>, <the culprit commit or pull request>; fixed by [#<n>](<url>)

## Performance

* <model>, <workload>: <the gain, measured alone at equal budget (G5)> ([#<n>](<url>))

## Kernels

<!-- The kernels the week's pull requests support on AMD: a new kernel, or the kernel an operator, a model or a
     dtype now runs on. -->

* <operator> for <model or dtype> on <platform>: <the kernel> ([#<n>](<url>))

## Runtime & Backend

<!-- InferPilot's runner and the agents that run its roles (Claude Code, claude -p, Cursor): the run's log, its
     control and its result. -->

* <title>: [#<n>](https://github.com/At1a8/inferpilot/pull/<n>)

## Workflow & Gate

<!-- What every mode passes, from Setup to PR: the phases, the gates and how they are measured, the harness. -->

* <title>: [#<n>](https://github.com/At1a8/inferpilot/pull/<n>)

## Task mode optimization

<!-- What changed in one mode, feature, issue, ci or perf, its title naming the mode. -->

* <title>: [#<n>](https://github.com/At1a8/inferpilot/pull/<n>)

## Daily community updates

<!-- The scan, its daily report on AMD CI, user issues and upstream changes, and the report's sharing. -->

* <title>: [#<n>](https://github.com/At1a8/inferpilot/pull/<n>)

## Container & environment setup

<!-- run_docker.sh, the image and the bootstrap. -->

* <title>: [#<n>](https://github.com/At1a8/inferpilot/pull/<n>)

## Tests & GitHub CI

<!-- InferPilot's own tests and the CI that runs them on every pull request. -->

* <title>: [#<n>](https://github.com/At1a8/inferpilot/pull/<n>)

## Knowledge

<!-- What changes InferPilot's knowledge: feature skills, known issues, sglang skills indexed, ROCm notes,
     reward hacks. -->

* knowledge: <title>: [#<n>](https://github.com/At1a8/inferpilot/pull/<n>)

## Breaking Changes & Upgrade Notes

<!-- What a user must change before the next run: a token or its permissions, an argument renamed or
     removed, a bootstrap or image change. A bold sentence, then what to do. -->

* **<what changed>.** <what to do>: [#<n>](https://github.com/At1a8/inferpilot/pull/<n>)

## Numbers

| | Merged | Closed without merge | Open at the week's end |
|---|---|---|---|
| InferPilot pull requests | | | |
| sglang pull requests from InferPilot | | | |

<f> files changed on InferPilot's main, +<a> / −<d> lines.

## Contributors

<!-- The people who wrote, pushed, reviewed or merged InferPilot's work this week, as sglang names its new
     contributors. -->

* @<user>: <wrote / pushed / reviewed / merged> [#<n>](<url>)

**Full Changelog**: https://github.com/At1a8/inferpilot/compare/<main at the week's start>...<main at the week's end>
