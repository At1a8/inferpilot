#!/usr/bin/env python3
"""What InferPilot's weekly report counts (templates/weekly_release.md), from GitHub: the range, InferPilot's pull
requests in it (merged, closed without merge, still open at its end), main at its start and at its end with the
change between, and the sglang pull requests from InferPilot's work. Read only: GitHub through _github, which reads
GITHUB_TOKEN; the change counted with git in this checkout when its origin is the repository (fetching main when a
commit is missing), else from GitHub, whose compare leaves some files' counts at 0 (counted_by says which).

The range runs from 00:00 seven days before its end to its end, in UTC+8; its end is now unless --until gives
another. --since and --until take a date or a date and a time, in UTC+8 unless they carry an offset: a date starts
at its 00:00 for --since and takes its whole day for --until.

    python3 scripts/weekly_report.py                                        # 7 days back at 00:00 to now
    python3 scripts/weekly_report.py --since 2026-11-17 --until 2026-11-23  # the 17th to the 23rd, whole days
    python3 scripts/weekly_report.py --since "2026-11-17 00:00" --until "2026-11-24 10:00"

The sglang pull requests are those pushed from inferpilot/<slug> branches (source "branch"), and those a feature skill
changed in the range names as its reference_pr (source "feature_skill:<id>"): a skill may record a hand-written port,
so the report's writer decides which of those are InferPilot's. kind_hint guesses a pull request's kind from its
branch (a CI job's number: ci; an issue's: issue) or its title ([Perf]: perf; [Feat]: feature), empty when neither
tells. Exit 1 when GitHub cannot be read, 2 when the range is empty or a time cannot be read.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import http.client
import json
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
SKILL = re.compile(r"^knowledge/feature_skills/([^/_][^/]*)/SKILL\.md$")
REFERENCE_PR = re.compile(r'^reference_pr:\s*"?sgl-project/sglang#(\d+)"?', re.M)


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


def sort_pulls(pulls: list, start: dt.datetime, end: dt.datetime) -> dict:
    """InferPilot's pull requests merged in the range, closed in it without merge, and open at its end."""
    merged = [brief(p) for p in pulls if within(p.get("merged_at"), start, end)]
    closed = [brief(p) for p in pulls if not p.get("merged_at") and within(p.get("closed_at"), start, end)]
    still = [brief(p) for p in pulls if parse_time(p["created_at"]) < end
             and not (p.get("closed_at") and parse_time(p["closed_at"]) < end)]
    return {"merged": sorted(merged, key=lambda p: p["merged_at"]), "closed": closed, "open": still}


def main_at(repo: str, t: dt.datetime) -> str:
    commits = _github.get("/repos/%s/commits" % repo, {"sha": "main", "until": utc(t), "per_page": 1})
    return commits[0]["sha"] if commits else ""


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, timeout=120)


def git_change(repo: str, base: str, head: str) -> dict | None:
    """`git diff --shortstat` in this checkout when its origin is repo, fetching main when it lacks a commit; None
    when it cannot tell."""
    try:
        if repo.lower() not in git("remote", "get-url", "origin").stdout.lower():
            return None
        if any(git("cat-file", "-e", c + "^{commit}").returncode for c in (base, head)):
            git("fetch", "-q", "origin", "main")
        out = git("diff", "--shortstat", base, head)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode:
        return None
    n = {k: int(v) for v, k in re.findall(r"(\d+) (file|insertion|deletion)", out.stdout)}
    return {"files": n.get("file", 0), "insertions": n.get("insertion", 0), "deletions": n.get("deletion", 0)}


def change(repo: str, base: str, head: str) -> dict:
    """The change from base to head: its numbers from git (GitHub's compare leaves some files' counts at 0), from
    GitHub when git cannot tell (counted_by says which), and the paths it touches."""
    if not base or not head or base == head:
        return {"files": 0, "insertions": 0, "deletions": 0, "counted_by": "", "paths": []}
    files = _github.get("/repos/%s/compare/%s...%s" % (repo, base, head)).get("files", [])
    counted, by = git_change(repo, base, head), "git"
    if counted is None:
        counted, by = {"files": len(files), "insertions": sum(f.get("additions", 0) for f in files),
                       "deletions": sum(f.get("deletions", 0) for f in files)}, "github"
    return {**counted, "counted_by": by, "paths": [f["filename"] for f in files]}


def skill_prs(repo: str, paths: list, ref: str) -> dict:
    """sglang pull request number -> the feature skill changed in the range that names it as its reference_pr."""
    out = {}
    for path in paths:
        m = SKILL.match(path)
        if not m:
            continue
        try:
            d = _github.get("/repos/%s/contents/%s" % (repo, path), {"ref": ref})
        except urllib.error.HTTPError as e:
            if e.code == 404:
                continue
            raise
        for n in REFERENCE_PR.findall(base64.b64decode(d.get("content", "")).decode("utf-8", "replace")):
            out.setdefault(int(n), "feature_skill:" + m.group(1))
    return out


def upstream_pulls(upstream: str, start: dt.datetime, end: dt.datetime, recorded: dict) -> list:
    """The sglang pull requests from InferPilot's work opened, merged, closed or updated in the range."""
    found = {}
    query = "repo:%s is:pr head:inferpilot updated:>=%s" % (upstream, (start - dt.timedelta(days=1)).date().isoformat())
    for item in _github.search_issues(query):
        found.setdefault(item["number"], "branch")
    for n, source in recorded.items():
        found.setdefault(n, source)
    out = []
    for n, source in sorted(found.items()):
        p = _github.get("/repos/%s/pulls/%d" % (upstream, n))
        if source == "branch" and not p["head"]["ref"].startswith("inferpilot/"):
            continue
        if not any(within(p.get(k), start, end) for k in ("created_at", "merged_at", "closed_at", "updated_at")):
            continue
        out.append({**brief(p), "state": "merged" if p.get("merged_at") else p["state"], "updated_at": p["updated_at"],
                    "kind_hint": kind_hint(p["head"]["ref"], p["title"]), "source": source})
    return out


def collect(repo: str, upstream: str, start: dt.datetime, end: dt.datetime) -> dict:
    pulls = sort_pulls(_github.paginate("/repos/%s/pulls" % repo, {"state": "all"}), start, end)
    for p in pulls["merged"]:
        merger = _github.get("/repos/%s/pulls/%d" % (repo, p["number"])).get("merged_by") or {}
        p["merged_by"] = merger.get("login", "")
    contributors = {}
    for p in pulls["merged"]:
        contributors.setdefault(p["author"], {"wrote": 0, "merged": 0})["wrote"] += 1
        if p["merged_by"]:
            contributors.setdefault(p["merged_by"], {"wrote": 0, "merged": 0})["merged"] += 1
    base, head = main_at(repo, start), main_at(repo, end)
    diff = change(repo, base, head)
    return {"range": {"since": start.isoformat(), "until": end.isoformat()},
            "inferpilot": {"repo": repo, **pulls, "contributors": contributors, "main": {"since": base, "until": head},
                           "change": {k: v for k, v in diff.items() if k != "paths"}},
            "upstream": {"repo": upstream, "pulls": upstream_pulls(upstream, start, end, skill_prs(repo, diff["paths"], head))}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="What InferPilot's weekly report counts, from GitHub, as JSON. Read only.")
    ap.add_argument("--since", default="", help="the range's start: a date or a date and a time (default: 00:00 seven days before its end)")
    ap.add_argument("--until", default="", help="the range's end: a date or a date and a time (default: now)")
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
