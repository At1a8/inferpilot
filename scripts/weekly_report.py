#!/usr/bin/env python3
"""What InferPilot's weekly report counts (templates/weekly_release.md), from GitHub: the range, InferPilot's pull
requests in it (merged, closed without merge, still open at its end), main at its start and at its end with the
change between, and the sglang pull requests from InferPilot's work. It reads GitHub through _github, which reads
GITHUB_TOKEN, and, when this checkout's origin is the repository, fetches origin's main to read git: main at a
time along its first-parent line (where commits landed, not when they were written), and the change with
`git diff --numstat` under LC_ALL=C. Without git, both come from GitHub, whose compare stops at 300 files and leaves
some files' counts at 0: found_by, counted_by and complete say so.

The range runs from 00:00 seven days before its end to its end, in UTC+8; its end is now unless --until gives
another. --since and --until take a date or a date and a time, in UTC+8 unless they carry an offset: a date starts
at its 00:00 for --since and takes its whole day for --until.

    python3 scripts/weekly_report.py                                        # 7 days back at 00:00 to now
    python3 scripts/weekly_report.py --since 2026-11-17 --until 2026-11-23  # the 17th to the 23rd, whole days
    python3 scripts/weekly_report.py --since "2026-11-17 00:00" --until "2026-11-24 10:00"

A pull request's state is its state at the range's end, from its merge and close times and, when it was reopened,
its events. The sglang pull requests are those pushed from inferpilot/<slug> branches (source "branch") and those a
feature skill of the range's end names as its reference_pr (source "feature_skill:<id>"; a skill may record a
hand-written port, so the report's writer decides which are InferPilot's): "pulls" holds those opened, merged,
closed or given commits in the range, "open_at_end" every one still open at its end. kind_hint guesses a pull
request's kind from its branch (a CI job's number: ci; an issue's: issue) or its title ([Perf]: perf; [Feat]:
feature), empty when neither tells. Exit 1 when GitHub cannot be read, 2 when the range is empty or a time cannot be
read.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import http.client
import json
import os
import re
import subprocess
import sys
import urllib.error
from pathlib import Path

import _github

ROOT = Path(__file__).resolve().parent.parent
UTC8 = dt.timezone(dt.timedelta(hours=8), "UTC+8")
REPO = "At1a8/inferpilot"
UPSTREAM = "sgl-project/sglang"
SKILLS = "knowledge/feature_skills"
SKILL = re.compile(r"^knowledge/feature_skills/([^/_][^/]*)/SKILL\.md$")
REFERENCE_PR = re.compile(r'^reference_pr:\s*"?sgl-project/sglang#(\d+)"?', re.M)
GITHUB_FILES = 300


def parse_time(text: str) -> dt.datetime:
    """A date (its 00:00) or a date and a time; UTC+8 unless it carries an offset or Z."""
    s = text.strip().replace("Z", "+00:00")
    t = dt.datetime.fromisoformat(s)
    return t if t.tzinfo else t.replace(tzinfo=UTC8)


def window(now: dt.datetime | None = None, since: str = "", until: str = "") -> tuple:
    """(start, end) in UTC+8: end is until (a date's whole day), else now; start is since, else 00:00 seven days
    before end's date."""
    if until:
        end = parse_time(until) + (dt.timedelta(days=1) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", until.strip()) else dt.timedelta())
    else:
        end = now or dt.datetime.now(UTC8)
    end = end.astimezone(UTC8)
    start = parse_time(since).astimezone(UTC8) if since else \
        dt.datetime.combine(end.date() - dt.timedelta(days=7), dt.time(), UTC8)
    if start >= end:
        raise ValueError("the range is empty: %s is not before %s" % (start.isoformat(), end.isoformat()))
    return start, end


def utc(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def before(stamp: str | None, t: dt.datetime) -> bool:
    return bool(stamp) and parse_time(stamp) < t


def within(stamp: str | None, start: dt.datetime, end: dt.datetime) -> bool:
    return bool(stamp) and start <= parse_time(stamp) < end


def kind_hint(head: str, title: str) -> str:
    if re.match(r"inferpilot/\d{9,}-", head):
        return "ci"
    if re.match(r"inferpilot/\d+-", head):
        return "issue"
    if "[Perf]" in title:
        return "perf"
    if "[Feat]" in title:
        return "feature"
    return ""


def brief(p: dict) -> dict:
    return {"number": p["number"], "title": p["title"], "author": p["user"]["login"], "url": p["html_url"],
            "head": p["head"]["ref"], "created_at": p["created_at"], "merged_at": p.get("merged_at"),
            "closed_at": p.get("closed_at")}


def at_end(p: dict, end: dt.datetime, events) -> tuple:
    """(state, closed_at) of a pull request at end: merged, closed or open. Its fields tell, unless it was open at
    some point after end (open now, or closed or merged after end): then its last close or reopen event before end
    does. events(number) lists its issue events."""
    if before(p.get("merged_at"), end):
        return "merged", p["merged_at"]
    if p.get("state") == "closed" and before(p.get("closed_at"), end):
        return "closed", p["closed_at"]
    last = None
    for e in events(p["number"]):
        if e.get("event") in ("closed", "reopened") and before(e.get("created_at"), end):
            if last is None or parse_time(e["created_at"]) >= parse_time(last["created_at"]):
                last = e
    if last and last["event"] == "closed":
        return "closed", last["created_at"]
    return "open", None


def sort_pulls(pulls: list, start: dt.datetime, end: dt.datetime, events) -> dict:
    """InferPilot's pull requests merged in the range, closed in it without merge, and open at its end."""
    out = {"merged": [], "closed": [], "open": []}
    for p in pulls:
        if not before(p["created_at"], end):
            continue
        state, closed = at_end(p, end, events)
        if state == "merged" and within(closed, start, end):
            out["merged"].append(brief(p))
        elif state == "closed" and within(closed, start, end):
            out["closed"].append(brief(p))
        elif state == "open":
            out["open"].append(brief(p))
    out["merged"].sort(key=lambda p: p["merged_at"])
    return out


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, timeout=120,
                          env={**os.environ, "LC_ALL": "C", "LANGUAGE": ""})


def git_ready(repo: str) -> bool:
    """Whether this checkout's origin is repo and its main could be fetched."""
    try:
        if repo.lower() not in git("remote", "get-url", "origin").stdout.lower():
            return False
        return git("fetch", "-q", "origin", "main").returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def main_at(repo: str, t: dt.datetime, use_git: bool) -> tuple:
    """(sha, found_by): main as it stood at t, along its first-parent line with git, else GitHub's newest commit
    on main dated before t."""
    if use_git:
        out = git("rev-list", "-1", "--first-parent", "--before=" + t.strftime("%Y-%m-%d %H:%M:%S %z"), "origin/main")
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip(), "git"
    commits = _github.get("/repos/%s/commits" % repo, {"sha": "main", "until": utc(t), "per_page": 1})
    return (commits[0]["sha"] if commits else ""), "github"


def numstat(text: str) -> list:
    """(added, deleted, path) from `git diff --numstat -z`; a renamed file by its new path, a binary one with 0 lines."""
    fields, out, i = text.split("\0"), [], 0
    while i < len(fields) and fields[i]:
        added, deleted, path = fields[i].split("\t", 2)
        if path:
            i += 1
        else:
            path, i = fields[i + 2], i + 3
        out.append((int(added) if added.isdigit() else 0, int(deleted) if deleted.isdigit() else 0, path))
    return out


def change(repo: str, base: str, head: str, use_git: bool) -> dict:
    """The change from base to head: its numbers and every path it touches, from git, else from GitHub."""
    if not base or not head or base == head:
        return {"files": 0, "insertions": 0, "deletions": 0, "counted_by": "", "complete": True, "paths": []}
    if use_git:
        out = git("diff", "--numstat", "-z", base, head)
        if out.returncode == 0:
            rows = numstat(out.stdout)
            return {"files": len(rows), "insertions": sum(r[0] for r in rows), "deletions": sum(r[1] for r in rows),
                    "counted_by": "git", "complete": True, "paths": [r[2] for r in rows]}
    files = _github.get("/repos/%s/compare/%s...%s" % (repo, base, head)).get("files", [])
    return {"files": len(files), "insertions": sum(f.get("additions", 0) for f in files),
            "deletions": sum(f.get("deletions", 0) for f in files), "counted_by": "github",
            "complete": len(files) < GITHUB_FILES, "paths": [f["filename"] for f in files]}


def recorded_ports(repo: str, head: str, use_git: bool) -> dict:
    """sglang pull request number -> the feature skill of head that names it as its reference_pr."""
    skills = {}
    if use_git:
        listed = git("ls-tree", "-r", "--name-only", head, SKILLS)
        if listed.returncode == 0:
            for path in listed.stdout.splitlines():
                if SKILL.match(path):
                    shown = git("show", "%s:%s" % (head, path))
                    if shown.returncode == 0:
                        skills[path] = shown.stdout
    if not skills:
        for d in _github.get("/repos/%s/contents/%s" % (repo, SKILLS), {"ref": head}):
            path = "%s/%s/SKILL.md" % (SKILLS, d["name"])
            if d.get("type") != "dir" or not SKILL.match(path):
                continue
            try:
                f = _github.get("/repos/%s/contents/%s" % (repo, path), {"ref": head})
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    continue
                raise
            skills[path] = base64.b64decode(f.get("content", "")).decode("utf-8", "replace")
    out = {}
    for path, text in sorted(skills.items()):
        for n in REFERENCE_PR.findall(text):
            out.setdefault(int(n), "feature_skill:" + SKILL.match(path).group(1))
    return out


def events_of(repo: str):
    cache = {}

    def events(number: int) -> list:
        if number not in cache:
            cache[number] = _github.paginate("/repos/%s/issues/%d/events" % (repo, number))
        return cache[number]
    return events


def active(repo: str, p: dict, closed: str | None, start: dt.datetime, end: dt.datetime) -> bool:
    """Whether a pull request was opened, merged or closed (closed: its merge or close time at the range's end) or
    given a commit in the range."""
    if within(p["created_at"], start, end) or within(closed, start, end):
        return True
    commits = _github.paginate("/repos/%s/pulls/%d/commits" % (repo, p["number"]), max_pages=3)
    return any(within((c.get("commit") or {}).get("committer", {}).get("date"), start, end) for c in commits)


def upstream_pulls(upstream: str, start: dt.datetime, end: dt.datetime, recorded: dict) -> dict:
    """The sglang pull requests from InferPilot's work: those active in the range, and those open at its end."""
    found = {}
    for item in _github.search_issues("repo:%s is:pr head:inferpilot" % upstream):
        found.setdefault(item["number"], "branch")
    for n, source in recorded.items():
        found.setdefault(n, source)
    events = events_of(upstream)
    out = {"pulls": [], "open_at_end": []}
    for n, source in sorted(found.items()):
        p = _github.get("/repos/%s/pulls/%d" % (upstream, n))
        if source == "branch" and not p["head"]["ref"].startswith("inferpilot/"):
            continue
        if not before(p["created_at"], end):
            continue
        state, closed = at_end(p, end, events)
        row = {**brief(p), "state": state, "kind_hint": kind_hint(p["head"]["ref"], p["title"]), "source": source}
        if state == "open":
            out["open_at_end"].append(row)
        if active(upstream, p, closed, start, end):
            out["pulls"].append(row)
    return out


def collect(repo: str, upstream: str, start: dt.datetime, end: dt.datetime) -> dict:
    use_git = git_ready(repo)
    pulls = sort_pulls(_github.paginate("/repos/%s/pulls" % repo, {"state": "all"}), start, end, events_of(repo))
    for p in pulls["merged"]:
        merger = _github.get("/repos/%s/pulls/%d" % (repo, p["number"])).get("merged_by") or {}
        p["merged_by"] = merger.get("login", "")
    contributors = {}
    for p in pulls["merged"]:
        contributors.setdefault(p["author"], {"wrote": 0, "merged": 0})["wrote"] += 1
        if p["merged_by"]:
            contributors.setdefault(p["merged_by"], {"wrote": 0, "merged": 0})["merged"] += 1
    (base, found_base), (head, found_head) = main_at(repo, start, use_git), main_at(repo, end, use_git)
    diff = change(repo, base, head, use_git)
    return {"range": {"since": start.isoformat(), "until": end.isoformat()},
            "inferpilot": {"repo": repo, **pulls, "contributors": contributors,
                           "main": {"since": base, "until": head, "found_by": found_base if found_base == found_head else "git and github"},
                           "change": {k: v for k, v in diff.items() if k != "paths"}},
            "upstream": {"repo": upstream, **upstream_pulls(upstream, start, end, recorded_ports(repo, head, use_git))}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="What InferPilot's weekly report counts, from GitHub, as JSON. Read only.")
    ap.add_argument("--since", default="", help="the range's start: a date or a date and a time (default: 00:00 seven days before its end)")
    ap.add_argument("--until", default="", help="the range's end: a date (its whole day) or a date and a time (default: now)")
    ap.add_argument("--repo", default=REPO, help="InferPilot's repository (default: %(default)s)")
    ap.add_argument("--upstream", default=UPSTREAM, help="the framework's repository (default: %(default)s)")
    a = ap.parse_args(argv)
    try:
        start, end = window(since=a.since, until=a.until)
    except ValueError as e:
        print("weekly_report: %s" % e, file=sys.stderr)
        return 2
    try:
        print(json.dumps(collect(a.repo, a.upstream, start, end), indent=1, ensure_ascii=False))
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as e:
        print("weekly_report: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
