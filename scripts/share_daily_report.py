#!/usr/bin/env python3
"""Share a scan's daily report as an issue in InferPilot's own repository.

    python3 scripts/share_daily_report.py RUN_DIR/daily_report.md
    python3 scripts/share_daily_report.py RUN_DIR/daily_report.md --dry-run

The report is read once, and what was read is what scripts/daily_report.py checks (its exit codes 3, 4 and 5
come back as they are) and what is posted. It must carry the report's marker and its date in the heading: the
issue is titled "InferPilot daily report <date>" with the whole file as its body, which is how the next scan,
in any container, finds it (daily_report.py latest). When the newest shared report is already that day's,
nothing is posted and its URL is printed with the action "exists". It reads and posts with
INFERPILOT_REPO_TOKEN, the fine-grained token for this repository alone with which the curator opens its pull
request; opening issues takes Issues: read and write as well. Without the token, or with one that cannot open
issues (exit 2), or on any other failure (exit 1), nothing is posted and the report stays a file for a human
to share. A post GitHub did not answer (a timeout, a dropped connection, a 5xx) may have opened the issue all
the same: the script looks for that day's report again and prints it as created when it is there; when it
cannot tell (exit 6), a human looks in the repository's issues before sharing the report by hand. Standard
library only.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import daily_report  # noqa: E402

HEADING = re.compile(r"^# InferPilot daily report (\d{4}-\d{2}-\d{2})\b", re.M)


def post(repo: str, title: str, body: str, token: str) -> dict:
    req = urllib.request.Request("https://api.github.com/repos/%s/issues" % repo, method="POST",
                                 data=json.dumps({"title": title, "body": body}).encode(),
                                 headers={"Accept": "application/vnd.github+json", "User-Agent": "inferpilot",
                                          "Authorization": "Bearer " + token})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def reconcile(repo: str, date: str, title: str, token: str, failure: str, later: str) -> int:
    """After a post GitHub did not answer: 0, with the issue printed, when that day's report is there now; 1 when
    it is not; 6 when the repository's issues cannot be read to tell."""
    try:
        shared = daily_report.latest(repo, token)
    except (urllib.error.URLError, OSError, ValueError) as e:
        print("share_daily_report: %s, and the issues of %s cannot be read to tell whether it was opened (%s); look there "
              "for \"%s\" before sharing it by hand" % (failure, repo, e, title), file=sys.stderr)
        return 6
    if shared.get("date") == date:
        print(json.dumps({"action": "created", "url": shared.get("url", ""), "number": shared.get("number"), "title": title}))
        return 0
    print("share_daily_report: %s; %s" % (failure, later), file=sys.stderr)
    return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Share a scan's daily report as an issue in InferPilot's repository.")
    ap.add_argument("file", help="the scan's daily report, RUN_DIR/daily_report.md")
    ap.add_argument("--repo", default="", help="owner/name (default: the origin of this checkout)")
    ap.add_argument("--dry-run", action="store_true", help="check it and show the issue it would open")
    a = ap.parse_args(argv)
    later = "share it by hand (README step 5), or later with: python3 %s %s" % (sys.argv[0], a.file)
    try:
        body = open(a.file, encoding="utf-8").read()
    except OSError as e:
        print("share_daily_report: %s" % e, file=sys.stderr)
        return 1
    rc = daily_report.verdict(daily_report.check(body, daily_report.this_host()))
    if rc:
        print("share_daily_report: not shared; fix what daily_report.py check lists, then run it again", file=sys.stderr)
        return rc
    heading = HEADING.search(body)
    if not heading or daily_report.MARKER not in body:
        print("share_daily_report: not shared; %s lacks the report's %s" % (
            a.file, "marker" if heading else "heading, # InferPilot daily report <YYYY-MM-DD>"), file=sys.stderr)
        return 1
    date = heading.group(1)
    title = daily_report.title(date)
    repo = a.repo or daily_report.default_repo()
    if not repo:
        print("share_daily_report: no GitHub repository (set --repo owner/name); %s" % later, file=sys.stderr)
        return 2
    token = os.environ.get("INFERPILOT_REPO_TOKEN", "")
    try:
        shared = daily_report.latest(repo, token)
    except (urllib.error.URLError, OSError, ValueError) as e:
        print("share_daily_report: cannot read the reports shared in %s (%s); %s" % (repo, e, later), file=sys.stderr)
        return 1
    if shared.get("date") == date:
        print(json.dumps({"action": "exists", "url": shared.get("url", ""), "number": shared.get("number"), "title": title}))
        return 0
    if a.dry_run:
        print(json.dumps({"dry_run": True, "repo": repo, "title": title, "chars": len(body)}, indent=1))
        return 0
    if not token:
        print("share_daily_report: INFERPILOT_REPO_TOKEN is not set; %s" % later, file=sys.stderr)
        return 2
    try:
        issue = post(repo, title, body, token)
        print(json.dumps({"action": "created", "url": issue.get("html_url", ""), "number": issue.get("number"), "title": title}))
        return 0
    except urllib.error.HTTPError as e:
        if e.code < 500:
            denied = e.code in (401, 403)
            print("share_daily_report: HTTP %d from GitHub%s; %s" % (
                e.code, " (INFERPILOT_REPO_TOKEN cannot open issues in the repository; README step 1)" if denied else "", later),
                file=sys.stderr)
            return 2 if denied else 1
        failure = "HTTP %d from GitHub" % e.code
    except (urllib.error.URLError, OSError, ValueError) as e:
        failure = str(e)
    return reconcile(repo, date, title, token, failure, later)


if __name__ == "__main__":
    sys.exit(main())
