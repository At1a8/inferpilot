#!/usr/bin/env python3
"""The AMD CI as main sees it now, and the AMD Failure Trackers (knowledge/ci_triage.md §1).

main: the latest completed run on main of each AMD workflow that runs there by schedule (MAIN_WORKFLOWS),
and for every job failing in it: the failed steps, how many runs in a row it failed, since which run,
when it last passed, and from its log (a token is needed) the failing tests, pytest's totals and the
first errors. Runs of forks and pull requests are left out.

Trackers: the bot keeps its state in an HTML comment `<!-- ci-failure-tracker-state: {...} -->`, keyed
by `test_file::test_function`. Each cluster reports its tests, statuses, the earliest `since` date named
in a status, first/last seen dates and one example error. A tracker can be days behind main.
The bot has written several forms of that state, and two are read (knowledge/ci_triage.md §1): entries
that carry a `status` in words, and version 3, whose `active` entries carry counts instead (`failed`,
`observed`, `streak`) with the runs that failed and the fix PR the bot names. Version 3 keeps a test in
`active` for days after it stopped failing: one the tracker's latest scan did not see is reported as
`no longer failing`, as the tracker's own body lists it. A state in any other form, or one that is not
JSON, is reported as not read, never taken for entries.

  scan_ci.py --out ci.json [--md ci.md]          # main and all open trackers
  scan_ci.py --no-main --out ci.json             # the trackers only
  scan_ci.py --tracker 45847 --out ci.json
  scan_ci.py --file body.md --out ci.json         # offline: a saved issue body
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _github as gh  # noqa: E402

DEFAULT_REPO = "sgl-project/sglang"
MARKER = "ci-failure-tracker-state:"
MALFORMED = object()  # a state marker with no JSON behind it: cut short, or encoded another way
_SINCE = re.compile(r"since (\d{4}-\d\d-\d\d)")
_STATUS_HEAD = re.compile(r"^\s*([a-z][a-z \-]*?)\s*(\(|$)", re.I)
_SNAPSHOT = re.compile(r"\*\*Last auto-update:\*\*\s*(\d{4}-\d\d-\d\d)")
_PR_URL = re.compile(r"github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)")
_PR_REF = re.compile(r"([\w.-]+/[\w.-]+)#(\d+)")
_MD_LINK = re.compile(r"\[[^\]]*\]\([^)]*\)")
NO_LONGER = "no longer failing"


def read_state(body: str):
    """The state block as the bot wrote it; None when the body has none, MALFORMED when its marker is there
    and what follows is not JSON."""
    i = (body or "").find(MARKER)
    if i < 0:
        return None
    text = body[i + len(MARKER):].lstrip()
    try:
        state, _ = json.JSONDecoder().raw_decode(text)
    except json.JSONDecodeError:
        return MALFORMED
    return state


def state_form(state) -> str:
    """Which form a state is in: `status` (entries with a status in words, bare or under "entries"), `counts`
    (version 3: "active" entries with failed / observed / streak), `none` (no state), or `unknown`: a state
    this script does not read. An unknown one is said so, not read as entries: version 3's four top-level
    keys were once taken for four tests. Version 2 kept its entries under "active" as well, with day counts
    and none of version 3's: only version 3 is read as counts. The bare form is known by its keys
    (`test_file::test_function`), so that other maps of maps are not taken for it."""
    if state is None:
        return "none"
    if not isinstance(state, dict):
        return "unknown"  # MALFORMED as well
    if isinstance(state.get("active"), dict):
        return "counts" if state.get("version") == 3 else "unknown"
    if isinstance(state.get("entries"), dict):
        return "status"
    if all(isinstance(v, dict) and "::" in str(k) for k, v in state.items()):
        return "status"  # the bare form: {key: entry}; an empty state is one with no entry
    return "unknown"


def snapshot_day(body: str, entries: dict) -> str:
    """The day of the tracker's latest scan: the body's "Last auto-update", else the latest day any entry was
    seen. The bot calls a test failing only when that scan saw it fail (its `last_seen` is that day)."""
    m = _SNAPSHOT.search(body or "")
    if m:
        return m.group(1)
    return max((e.get("last_seen") or "" for e in entries.values() if isinstance(e, dict)), default="")


def _fix_prs(text: str, repo: str) -> list[str]:
    """The pull requests a `fix_pr` cell names: `#N` in the tracker's own repository, `owner/repo#N` in any
    other (the bot's analysis also names fixes in aiter and other repositories)."""
    found = {(m.group(1), int(m.group(2))) for m in _PR_URL.finditer(text)}
    rest = _MD_LINK.sub(" ", text)
    found |= {(m.group(1), int(m.group(2))) for m in _PR_REF.finditer(rest)}
    found |= {(repo, int(n)) for n in re.findall(r"(?<![\w/])#(\d+)", _PR_REF.sub(" ", rest))}
    own = repo.lower()
    return [f"#{n}" if r.lower() == own else f"{r}#{n}" for r, n in sorted(found, key=lambda x: (x[0].lower() != own, x[0].lower(), x[1]))]


def _streak_start(entry: dict, streak: int, failed: int, started: dict) -> str:
    """The day the present run of failures began: when the first run of the streak started. The streak is the
    last `streak` of the runs that failed, and run IDs grow with time, so that run is found with or without
    its date; without one no day is given, since the dated runs alone may reach into an earlier streak. Only
    where every failure the entry records is in the streak is its first failure the start: `first_seen`."""
    fail_runs = [str(r) for r in entry.get("fail_runs") or []]
    if fail_runs and all(r.isdigit() for r in fail_runs) and 0 < streak <= len(fail_runs):
        first = sorted(fail_runs, key=int)[-streak]
        if first in started:
            return started[first][:10]
    if streak and streak == failed == len(fail_runs):
        return entry.get("first_seen") or ""
    return ""


def _counts_entries(state: dict, snapshot: str, repo: str) -> dict:
    """Version 3's active entries, each with what the earlier form gave in words: a `status` from its counts,
    the day its present run of failures began (`failing_since`) and the fix PRs the bot names (`fix_prs`).
    The words are the bot's own kinds, as its tracker body shows them: `no longer failing` (the latest scan,
    `snapshot`, did not see it fail, whatever streak it kept: the run of an image that is not run again
    keeps one), `broken` (two or more runs in a row), `new` (one failing run, and never archived before),
    `flaky` (anything else). `observed` counts the runs of the test's image variants from its first
    failing run on, so failing all of them does not mean that it never passed."""
    runs = state.get("runs") if isinstance(state.get("runs"), dict) else {}
    started = {str(r): v[0] for r, v in runs.items() if isinstance(v, list) and v and isinstance(v[0], str)}
    out = {}
    for key, entry in state["active"].items():
        if not isinstance(entry, dict):
            continue
        e = dict(entry)
        failed, observed, streak = (e.get(k) if isinstance(e.get(k), int) else 0 for k in ("failed", "observed", "streak"))
        of = f"failed {failed} of {max(observed, failed)} runs"
        since = ""
        if not snapshot or e.get("last_seen") != snapshot:
            e["status"] = f"{NO_LONGER} (last seen {e.get('last_seen') or '?'}; {of})"
        elif streak >= 2:
            since = _streak_start(e, streak, failed, started)
            e["status"] = f"broken ({streak} runs in a row{', since ' + since if since else ''}; {of})"
        elif len(e.get("fail_runs") or []) <= 1 and not e.get("back_from"):
            since = e.get("first_seen") or ""
            e["status"] = f"new ({of}{', since ' + since if since else ''})"
        else:
            e["status"] = f"flaky ({of})"
        e["failing_since"] = since
        e["fix_prs"] = _fix_prs(str(e.get("fix_pr") or ""), repo)
        out[key] = e
    return out


def parse_state(body: str, repo: str = DEFAULT_REPO) -> dict:
    """The tracker's entries ({key: entry}); empty when the body has no state block, or one in a form this
    script does not read (state_form tells the two apart)."""
    state = read_state(body)
    form = state_form(state)
    if form == "counts":
        return _counts_entries(state, snapshot_day(body, state["active"]), repo)
    if form == "status":
        return state["entries"] if isinstance(state.get("entries"), dict) else state
    return {}


def status_head(status: str) -> str:
    m = _STATUS_HEAD.match(status or "")
    return m.group(1).strip().lower() if m else (status or "").strip().lower()


def aggregate(entries: dict) -> list[dict]:
    clusters: dict[str, dict] = {}
    for key, e in entries.items():
        if not isinstance(e, dict):
            continue
        name = e.get("cluster") or "(no cluster)"
        c = clusters.setdefault(name, {"cluster": name, "tests": [], "statuses": collections.Counter(),
                                       "since": "", "first_seen": "", "last_seen": "", "example_error": "",
                                       "job_urls": [], "variants": set(), "fix_prs": set()})
        c["tests"].append(key)
        st = e.get("status", "")
        c["statuses"][status_head(st)] += 1
        for d in _SINCE.findall(st):
            c["since"] = min(c["since"] or d, d)
        if e.get("first_seen"):
            c["first_seen"] = min(c["first_seen"] or e["first_seen"], e["first_seen"])
        if e.get("last_seen"):
            c["last_seen"] = max(c["last_seen"] or e["last_seen"], e["last_seen"])
        if not c["example_error"] and e.get("error"):
            c["example_error"] = e["error"][:300]
        if e.get("job_url") and len(c["job_urls"]) < 3:
            c["job_urls"].append(e["job_url"])
        # what only the form with counts carries: the image variants it failed on, the fix PRs the bot names
        c["variants"].update(v for v in e.get("variants") or [] if isinstance(v, str))
        c["fix_prs"].update(n for n in e.get("fix_prs") or [] if isinstance(n, str))
        c["n_failing"] = c.get("n_failing", 0) + (status_head(st) != NO_LONGER)
    out = []
    for c in clusters.values():
        c["n_tests"] = len(c["tests"])
        c["tests"] = sorted(c["tests"])[:10]
        c["statuses"] = dict(c["statuses"])
        c["variants"] = sorted(c["variants"])
        c["fix_prs"] = sorted(c["fix_prs"], key=lambda f: ("#" not in f[:1], f.split("#")[0].lower(), int(f.rsplit("#", 1)[1])))
        out.append(c)
    # what fails now comes first; the earlier form has nothing else, and keeps its order
    return sorted(out, key=lambda c: (-c["n_failing"], -c["n_tests"], c["cluster"]))


# AMD workflows that run on main by schedule; the miles ones are for reinforcement learning and stay out.
MAIN_WORKFLOWS = ("pr-test-amd.yml", "nightly-test-amd.yml", "nightly-amd-mi355x-disagg.yml")
MAIN_EVENTS = ("schedule", "push", "workflow_dispatch")
_AGGREGATE = re.compile(r"-finish\b|^check[- ]all[- ](dependent|jobs)\b", re.I)  # jobs that only report the others
_TS = re.compile(r"^\d{4}-\d\d-\d\dT[\d:.]+Z ")
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
# pytest's summary (FAILED/ERROR file::test) and sglang's run_suite.py ("✗ FAILED: <file> returned exit code N")
_FAILED_TEST = re.compile(r"^(?:(?:FAILED|ERROR) (\S+::\S+)|✗ FAILED: (\S+\.py))")
_CHECKOUT = re.compile(r"^/.*?/(?=(?:test|python|sgl-kernel)/)")  # an absolute path's runner checkout part
_TOTALS = re.compile(r"^=+ (.*\d+ \w+.*) in [\d.]+s")
_EXC = re.compile(r"^(?:E\s+)?(\w*(?:Error|Exception|Failure|Exit)\w*):\s*(.*)")
_GH_ERROR = re.compile(r"^##\[error\](.*)")


def parse_log(text: str) -> dict:
    """What a failed job's log says: the failing tests, pytest's totals, the first errors."""
    tests, errors, gh_errors, totals = [], [], [], ""
    for raw in text.splitlines():
        line = _ANSI.sub("", _TS.sub("", raw)).rstrip()
        m = _FAILED_TEST.match(line)
        if m:
            t = _CHECKOUT.sub("", m.group(1) or m.group(2))[:240]
            if t not in tests:
                tests.append(t)
        m = _GH_ERROR.match(line)
        if m and m.group(1).strip() and m.group(1).strip()[:200] not in gh_errors:
            gh_errors.append(m.group(1).strip()[:200])
        m = _EXC.match(line)
        if m:
            e = ("%s: %s" % (m.group(1), m.group(2).strip())).rstrip(": ")[:240]
            if e not in errors:
                errors.append(e)
        m = _TOTALS.match(line)
        if m and (not totals or re.search(r"failed|error", m.group(1))):
            totals = m.group(1)
    return {"failed_tests": tests[:50], "errors": errors[:5], "totals": totals, "gh_errors": gh_errors[:5]}


def main_runs(repo: str, workflow: str, history: int) -> list[dict]:
    """The workflow's last completed runs on main by the repository itself (not forks), newest first."""
    d = gh.get(f"/repos/{repo}/actions/workflows/{workflow}/runs",
               {"branch": "main", "status": "completed", "per_page": 30})
    runs = [r for r in (d.get("workflow_runs") or []) if r.get("event") in MAIN_EVENTS
            and (r.get("head_repository") or {}).get("full_name", repo) == repo]
    return runs[:history]


def run_jobs(repo: str, run_id: int) -> list[dict]:
    d = gh.get(f"/repos/{repo}/actions/runs/{run_id}/jobs", {"filter": "latest", "per_page": 100})
    return [j for j in (d.get("jobs") or []) if not _AGGREGATE.search(j.get("name", ""))]


def _run_ref(r: dict | None) -> dict | None:
    return {"run_at": r.get("created_at", ""), "head_sha": (r.get("head_sha") or "")[:12], "run_url": r.get("html_url", "")} if r else None


def main_status(repo: str, workflows=MAIN_WORKFLOWS, history: int = 10, logs: bool = True) -> dict:
    """The AMD CI on main now: each workflow's latest run, and for each job failing there, since when it
    fails, when it last passed and what its log says."""
    out = {"workflows": [], "failures": []}
    for wf in workflows:
        runs = main_runs(repo, wf, history)
        if not runs:
            continue
        latest, jobs = runs[0], {}
        jobs[latest["id"]] = run_jobs(repo, latest["id"])
        failed = [j for j in jobs[latest["id"]] if j.get("conclusion") == "failure"]
        out["workflows"].append({"file": wf, "name": latest.get("name", wf), "conclusion": latest.get("conclusion", ""),
                                 "failed_jobs": len(failed), **_run_ref(latest)})
        for j in failed:
            since, last_ok, streak = latest, None, 1
            for r in runs[1:]:
                if r["id"] not in jobs:
                    jobs[r["id"]] = run_jobs(repo, r["id"])
                same = [x for x in jobs[r["id"]] if x.get("name") == j["name"]]
                if not same:
                    continue
                if same[0].get("conclusion") == "success":
                    last_ok = r
                    break
                if same[0].get("conclusion") == "failure":
                    since, streak = r, streak + 1
            entry = {"workflow": latest.get("name", wf), "job": j["name"], "job_url": j.get("html_url", ""),
                     "failed_steps": [s["name"] for s in j.get("steps", []) if s.get("conclusion") == "failure"],
                     "latest": _run_ref(latest), "failing_since": _run_ref(since), "consecutive_failures": streak,
                     "last_success": _run_ref(last_ok), "history_checked": len(runs)}
            if logs:
                try:
                    entry.update(parse_log(gh.text(f"/repos/{repo}/actions/jobs/{j['id']}/logs")))
                except Exception as e:  # a log needs a token; the job and its steps are still known
                    entry["log_error"] = str(e)[:200]
            out["failures"].append(entry)
    return out


def trackers(repo: str, number: int | None) -> list[dict]:
    if number:
        return [gh.get(f"/repos/{repo}/issues/{number}")]
    return gh.search_issues(f'repo:{repo} is:issue is:open "Failure Tracker" in:title', max_pages=2)


def to_markdown(result: dict) -> str:
    lines = []
    main = result.get("main") or {}
    if main.get("workflows"):
        lines.append("## AMD CI on main\n")
        lines.append("| Workflow | Latest run | Failing jobs |")
        lines.append("|---|---|---|")
        for w in main["workflows"]:
            lines.append(f"| {w['name']} | {w['conclusion']} at {w['run_at']} ({w['head_sha']}) | {w['failed_jobs']} |")
        lines.append("")
        lines.append("| Job | Failing since | Last success | Failed tests / first error |")
        lines.append("|---|---|---|---|")
        for f in main["failures"]:
            ok = f["last_success"]["run_at"] if f["last_success"] else f"none in {f['history_checked']} runs"
            what = ", ".join(f.get("failed_tests", [])[:3]) or "; ".join(f.get("errors", [])[:1]) or ", ".join(f["failed_steps"])
            lines.append(f"| {f['job'].replace('|', '/')} | {f['failing_since']['run_at']} ({f['consecutive_failures']} runs) "
                         f"| {ok} | {what.replace('|', '/')} |")
        lines.append("")
    for t in result["trackers"]:
        now = f", {t['n_failing']} of them failing at its latest scan ({t.get('snapshot') or 'day unknown'})" if "n_failing" in t else ""
        lines.append(f"## #{t['number']} {t['title']} (updated {t['updated_at']}) — "
                     f"{t['n_entries']} tests in {len(t['clusters'])} clusters{now}\n")
        if t.get("unread"):
            lines.append(f"**Not read:** {t['unread']}\n")
            continue
        lines.append("| Tests | Statuses | Since | First / last seen | Fix PRs named | Cluster |")
        lines.append("|---|---|---|---|---|---|")
        for c in t["clusters"]:
            st = ", ".join(f"{k} {v}" for k, v in c["statuses"].items())
            fixes = ", ".join(c.get("fix_prs") or []) or "-"
            lines.append(f"| {c['n_tests']} | {st} | {c['since'] or '-'} | {c['first_seen']} / {c['last_seen']} | {fixes} | "
                         f"{c['cluster'].replace('|', '/')} |")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=DEFAULT_REPO)
    ap.add_argument("--tracker", type=int, help="one tracker issue number")
    ap.add_argument("--file", help="parse a saved issue body instead of calling GitHub")
    ap.add_argument("--workflow", action="append", help="an AMD workflow file to read on main (repeatable; "
                    "default: %s)" % ", ".join(MAIN_WORKFLOWS))
    ap.add_argument("--history", type=int, default=10, help="runs per workflow to look back through (default 10)")
    ap.add_argument("--no-main", action="store_true", help="only the Failure Trackers")
    ap.add_argument("--no-logs", action="store_true", help="do not read the failed jobs' logs")
    ap.add_argument("--out", required=True)
    ap.add_argument("--md")
    a = ap.parse_args()

    if a.file:
        issues = [{"number": 0, "title": Path(a.file).name, "updated_at": "", "state": "",
                   "body": Path(a.file).read_text()}]
    else:
        issues = trackers(a.repo, a.tracker)
    result = {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
              "repo": a.repo, "trackers": []}
    if not (a.file or a.tracker or a.no_main):
        result["main"] = main_status(a.repo, a.workflow or MAIN_WORKFLOWS, a.history, not a.no_logs)
        print("[scan_ci] main: %d workflows, %d failing jobs" % (len(result["main"]["workflows"]),
                                                                 len(result["main"]["failures"])), file=sys.stderr)
    for it in issues:
        state = read_state(it.get("body") or "")
        form = state_form(state)
        entries = parse_state(it.get("body") or "", a.repo)
        clusters = aggregate(entries)
        tracker = {"number": it.get("number"), "title": it.get("title", ""),
                   "state": it.get("state", ""), "updated_at": it.get("updated_at", ""),
                   "state_form": form, "n_entries": len(entries), "clusters": clusters}
        if form == "counts":
            # A test stays in "active" for days after it stopped failing, then moves to "archive", which keeps
            # only its key, dates and counts: what fails now is what the latest scan saw.
            tracker["snapshot"] = snapshot_day(it.get("body") or "", state["active"])
            tracker["n_failing"] = sum(1 for e in entries.values() if status_head(e.get("status", "")) != NO_LONGER)
            tracker["n_archived"] = len(state["archive"]) if isinstance(state.get("archive"), list) else 0
        if form == "unknown":
            if state is MALFORMED:
                what = "the tracker's state block is not JSON (cut short, or encoded another way)"
            else:
                keys = sorted(map(str, state))[:8] if isinstance(state, dict) else type(state).__name__
                what = (f"the tracker's state is in a form scan_ci.py does not read (version "
                        f"{state.get('version', '?') if isinstance(state, dict) else '?'}, keys {keys})")
            tracker["unread"] = f"{what}: read the issue itself"
            print(f"[scan_ci] #{it.get('number')}: NOT READ: {tracker['unread']}", file=sys.stderr)
        result["trackers"].append(tracker)
        print(f"[scan_ci] #{it.get('number')}: {len(entries)} tests, {len(clusters)} clusters ({form})", file=sys.stderr)
    Path(a.out).write_text(json.dumps(result, indent=1, ensure_ascii=False))
    if a.md:
        Path(a.md).write_text(to_markdown(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
