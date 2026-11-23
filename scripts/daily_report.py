#!/usr/bin/env python3
"""Check a scan's daily report before a human shares it, and find the last one they shared. Read only:
InferPilot posts nothing; the human uploads the report (e.g. as an issue in InferPilot's repository).

    python3 scripts/daily_report.py check RUN_DIR/daily_report.md   # public-safe, titled and short enough?
    python3 scripts/daily_report.py latest                          # the newest shared report: date, URL, age, body

check: exit 3 when a line names a local path, this machine or a sibling, or the company's network (the lines are listed),
exit 5 when a table row or list item names a pull request or issue by its number alone, without its title (the lines
are listed), exit 4 when the body is longer than a GitHub issue allows. latest: the newest issue in InferPilot's repository
(the origin of this checkout) that carries the report's marker, `{}` when there is none; it reads a
public repository, with GITHUB_TOKEN when set. Standard library only.
"""
from __future__ import annotations

import argparse
import datetime as dt
import functools
import json
import os
import re
import socket
import subprocess
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"
MARKER = "<!-- inferpilot:daily-report -->"
ISSUE_LIMIT = 65536  # characters GitHub accepts in an issue body
LOCAL = re.compile(r"(?<![\w.-])/(?:data\d*|mnt|home|root|workspace|sgl-workspace|apps|shared|nfs\w*|kmd\d*|scratch|raid\w*"
                   r"|work|export|models|opt|tmp)/|confluence|jira\.", re.I)
# The company's network, which tests/test_hygiene.py keeps out of this repository with the same patterns: a
# host of amd.com that is not public, an internal site, a private network address.
AMD_HOST = re.compile(r"\b[a-z0-9][a-z0-9.-]*\.amd\.com\b", re.I)
# Public, or named on purpose: the LLM gateway Claude Code runs on.
PUBLIC_AMD = {"www.amd.com", "rocm.docs.amd.com", "rocm.blogs.amd.com", "instinct.docs.amd.com", "llm-api.amd.com"}
INTERNAL_SITE = re.compile(r"\b(?:confluence|jira)\.[a-z0-9-]+\.[a-z]|\b[a-z0-9-]+\.(?:atlassian\.net|sharepoint\.com)\b"
                           # a host under .internal, in lower case; not an import (from google.protobuf.internal import x),
                           # self.unique_id.internal or grpc.StatusCode.INTERNAL
                           r"|(?<![\w.])(?<!from )(?<!import )(?-i:(?:[a-z0-9-]+\.)+internal)\b", re.I)
PRIVATE_IP = re.compile(r"(?<![\d.])(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}(?!\d|\.\d)")
# A row or item the template opens with [#<n>](<url>) <title>, written without the title: at any indentation and
# after any list marker, the link (or a bare #<n>) is followed only by the end of its cell or item, *new*, or a
# colon before a space or the end (a title may open with :rocket:).
UNTITLED = re.compile(r"^[ \t]*(?:\|\s*|(?:[-*+]|\d+[.)])\s+)(?:\[#\d+\]\([^)\s]*\)|#\d+)\s*(?:\*new\*\s*)?(?:\||:(?:\s|$)|$)")


def title(date: str) -> str:
    """The title a shared report gets, which latest reads its date from."""
    return "InferPilot daily report %s" % date


def repo_from_url(url: str) -> str:
    """owner/name of a GitHub remote URL (https or ssh), '' for anything else."""
    m = re.match(r"^(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([^/]+/[^/]+?)(?:\.git)?/?$", url.strip())
    return m.group(1) if m else ""


def default_repo() -> str:
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        url = subprocess.run(["git", "-C", here, "remote", "get-url", "origin"], capture_output=True, text=True).stdout
    except OSError:
        return ""
    return repo_from_url(url)


def this_host() -> str:
    """This machine's name without its domain: a mention of the full name holds it too."""
    return socket.gethostname().split(".")[0]


@functools.lru_cache(maxsize=None)
def machine_names(host: str):
    """This machine's name, and the stem its cluster's siblings share (node-m01-13: node-m01-12 too)."""
    stem = host
    while re.search(r"[-_.][a-z]?\d+$", stem, re.I):
        stem = re.sub(r"[-_.][a-z]?\d+$", "", stem, flags=re.I)
    if stem != host and len(stem) >= 8:
        return re.compile(r"(?<![a-z0-9-])%s(?:[-_.][a-z]?\d+)*(?![a-z0-9-])" % re.escape(stem), re.I)
    return re.compile(r"(?<![a-z0-9-])%s(?![a-z0-9-])" % re.escape(host), re.I)


def machine(line: str, host: str) -> bool:
    """Whether a line names this machine (inside a word too) or a sibling in its cluster."""
    return bool(host) and (host.lower() in line.lower() or bool(machine_names(host).search(line)))


def network(line: str) -> bool:
    """Whether a line names the company's network: an internal site or host, a private address."""
    return bool(INTERNAL_SITE.search(line) or PRIVATE_IP.search(line)
                or any(m.group(0).lower() not in PUBLIC_AMD for m in AMD_HOST.finditer(line)))


def leaks(body: str, host: str = "") -> list:
    """Lines that would make something private public: local paths, this machine or a sibling, the company's
    network."""
    out = []
    for n, line in enumerate(body.splitlines(), 1):
        if LOCAL.search(line) or network(line) or machine(line, host):
            out.append("%d: %s" % (n, line.strip()[:160]))
    return out


def untitled(body: str) -> list:
    """Lines that name a pull request or issue by its number alone where the template gives its title too."""
    return ["%d: %s" % (n, line.strip()[:160]) for n, line in enumerate(body.splitlines(), 1) if UNTITLED.match(line)]


def check(body: str, host: str = "") -> dict:
    return {"leaks": leaks(body, host), "untitled": untitled(body), "chars": len(body), "limit": ISSUE_LIMIT,
            "marker": MARKER in body}


def age_hours(stamp: str, now: dt.datetime | None = None) -> float:
    """Hours since a GitHub timestamp (2026-11-09T01:11:40Z)."""
    then = dt.datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    return round(((now or dt.datetime.now(dt.timezone.utc)) - then).total_seconds() / 3600, 1)


def get(path: str):
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    req = urllib.request.Request(API + path, headers={"Accept": "application/vnd.github+json", "User-Agent": "inferpilot",
                                                      **({"Authorization": "Bearer " + token} if token else {})})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def latest(repo: str) -> dict:
    items = [i for i in get("/repos/%s/issues?state=all&per_page=50&sort=created&direction=desc" % repo)
             if "pull_request" not in i and MARKER in (i.get("body") or "")]
    if not items:
        return {}
    i = items[0]
    m = re.search(r"(\d{4}-\d{2}-\d{2})", i.get("title", ""))
    stamp = i.get("created_at") or ""
    return {"date": m.group(1) if m else "", "number": i["number"], "url": i.get("html_url", ""),
            "state": i.get("state", ""), "created_at": stamp, "age_hours": age_hours(stamp) if stamp else None,
            "body": i.get("body", "")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Check a daily report, or find the last one shared. Read only.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="is the report public-safe, every pull request and issue titled, short enough for an issue?")
    c.add_argument("file")
    lt = sub.add_parser("latest", help="the newest shared report in InferPilot's repository")
    lt.add_argument("--repo", default="", help="owner/name (default: the origin of this checkout)")
    a = ap.parse_args(argv)
    if a.cmd == "check":
        r = check(open(a.file, encoding="utf-8").read(), this_host())
        print(json.dumps(r, indent=1))
        if r["leaks"]:
            print("daily_report: not public-safe; it names something local or internal:\n  " + "\n  ".join(r["leaks"]),
                  file=sys.stderr)
            return 3
        if r["untitled"]:
            print("daily_report: these name a pull request or issue by its number alone; write each as [#<n>](<url>) <title>:\n  "
                  + "\n  ".join(r["untitled"]), file=sys.stderr)
            return 5
        if r["chars"] > ISSUE_LIMIT:
            print("daily_report: %d characters, more than an issue's %d; shorten it" % (r["chars"], ISSUE_LIMIT), file=sys.stderr)
            return 4
        return 0
    repo = a.repo or default_repo()
    if not repo:
        print("daily_report: no GitHub repository (set --repo owner/name)", file=sys.stderr)
        return 1
    try:
        print(json.dumps(latest(repo), indent=1))
        return 0
    except (urllib.error.URLError, OSError, ValueError) as e:
        print("daily_report: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
