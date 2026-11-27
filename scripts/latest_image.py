#!/usr/bin/env python3
"""Print the newest daily sglang image on Docker Hub, e.g. rocm/sgl-dev:v0.5.23-rocm10-mi35x-20261103.

Tasks test the newest sglang unless the user names an image or a container: scripts/run_docker.sh
starts from this image, and the director compares the image a run uses with it.

    python3 scripts/latest_image.py                          # ROCm 10, MI35x (the default)
    python3 scripts/latest_image.py --variant rocm10-mi30x   # another GPU or ROCm
    python3 scripts/latest_image.py --compare rocm/sgl-dev:v0.5.23-rocm10-mi35x-20261105
        # one line: "current", "older <newest image>", "unknown" (Docker Hub not readable) or "not-daily"
    python3 scripts/latest_image.py --base v0.5.23-rocm10-mi35x --days 7
        # the newest date of one base tag (release, ROCm and GPU) among today and the six days before

Only the daily sglang builds count (v<version>-<variant>-<YYYYMMDD>); the miles images in the same
repository are for reinforcement learning and are skipped. The newest of a variant goes by the date,
whatever the release: an older release built on a later day wins it, and a release and its .post1
built on one day are told apart by their push time alone. A CI lane takes the newest date of one
exact base tag instead, and --base answers for that: the tags <base>-<YYYYMMDD> and no other. A
lane also looks back only so many days before it falls back to another release: --days N counts
the tags dated today or in the N-1 days before (UTC), for a base or for a variant, and an older
image is then no answer.

Docker Hub lists tags by push time. Without --days the two newest pages are read (200 tags), which
hold the newest build unless that many older tags were pushed again after it. With --days, every
page that holds a push of those days is read: a tag is not pushed before its date. A lookup that
would need more than MAX_PAGES pages fails, and never answers from a part of the list.

Prints nothing and exits 1 when Docker Hub cannot be read, has no such tag, or the lookup would be
cut short; --compare always exits 0; a blank --base or a --days below 1 is a usage error (exit 2).
Standard library only.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import urllib.parse
import urllib.request

REPO = "rocm/sgl-dev"
VARIANT = "rocm10-mi35x"
API = "https://hub.docker.com/v2/repositories/%s/tags?page_size=100&ordering=last_updated&name=%s"
MAX_PAGES = 20  # of 100 tags each: a lookup with --days that would need more is cut short, and fails


def today() -> dt.date:
    """Today in UTC: the daily builds date their tags by it."""
    return dt.datetime.now(dt.timezone.utc).date()


def window(days: int) -> tuple[str, str]:
    """For the last `days` days, today included: the oldest tag date that counts (YYYYMMDD), and the day
    before it as a push date (YYYY-MM-DD). A tag is not pushed before its date, so no tag of those days was
    pushed earlier than that day: the pages older than it hold none."""
    first = today() - dt.timedelta(days=days - 1)
    return first.strftime("%Y%m%d"), (first - dt.timedelta(days=1)).isoformat()


def daily_key(name: str, variant: str):
    """Sort key of a daily sglang tag for the variant (date, version), None for any other tag."""
    m = re.fullmatch(r"v(\d+(?:\.\d+)*)[0-9a-z.]*-%s-(\d{8})" % re.escape(variant), name)
    if not m:
        return None
    return m.group(2), tuple(int(x) for x in m.group(1).split("."))


def pick(results: list, variant: str, first: str = "") -> str:
    """The newest daily tag among Docker Hub tag records ({"name", "last_updated"}), '' if none. With
    `first` (YYYYMMDD), only tags dated that day or later count."""
    best, best_key = "", None
    for t in results:
        key = daily_key(t.get("name", ""), variant)
        if key is None or key[0] < first:
            continue
        key = key + (t.get("last_updated") or "",)
        if best_key is None or key > best_key:
            best, best_key = t["name"], key
    return best


def base_date(name: str, base: str):
    """The date of a tag of exactly this base (<base>-<YYYYMMDD>), None for any other tag."""
    m = re.fullmatch(r"%s-(\d{8})" % re.escape(base), name)
    return m.group(1) if m else None


def pick_base(results: list, base: str, first: str = "") -> str:
    """The newest date among the tags of one exact base (release, ROCm and GPU), '' if none. With `first`
    (YYYYMMDD), only tags dated that day or later count."""
    best, best_date = "", ""
    for t in results:
        date = base_date(t.get("name", ""), base)
        if date and date >= first and date > best_date:
            best, best_date = t["name"], date
    return best


def fetch(repo: str, variant: str, timeout: float, pages: int = 2, since: str = "") -> list:
    """The tag records whose name holds `variant`, newest push first: `pages` pages of 100. With `since`
    (YYYY-MM-DD), every page that holds a push of that day or later instead, however many: ValueError
    when those are more than MAX_PAGES, since the rest of the list would go unread."""
    url = API % (repo, urllib.parse.quote(variant))
    results = []
    for n in range(1, (MAX_PAGES if since else pages) + 1):
        with urllib.request.urlopen(url, timeout=timeout) as r:
            page = json.load(r)
        got = page.get("results") or []
        results += got
        url = page.get("next")
        pushed = [t["last_updated"][:10] for t in got if t.get("last_updated")]
        if not url or (since and pushed and min(pushed) < since):
            break
        if since and n == MAX_PAGES:
            raise ValueError("more than %d pages of tags pushed since %s: the lookup would be cut short" % (MAX_PAGES, since))
    return results


def parse_daily(image: str) -> tuple[str, str, str] | None:
    """(repository, variant, date) of a daily sglang image, None for any other image."""
    repo, _, tag = image.rpartition(":")
    m = re.fullmatch(r"v\d+(?:\.\d+)*[0-9a-z.]*-(.+)-(\d{8})", tag)
    return (repo or REPO, m.group(1), m.group(2)) if m and "miles" not in m.group(1) else None


def compare(image: str, results: list | None) -> str:
    """How an image stands against the newest daily one of its variant: current, older <newest>,
    unknown (no tag list) or not-daily."""
    daily = parse_daily(image)
    if not daily:
        return "not-daily"
    repo, variant, date = daily
    newest = pick(results, variant) if results is not None else ""
    if not newest:
        return "unknown"
    if newest == image.rpartition(":")[2] or daily_key(newest, variant)[0] <= date:
        return "current"
    return "older %s:%s" % (repo, newest)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Print the newest daily sglang image on Docker Hub.")
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--variant", default=VARIANT, help="ROCm and GPU part of the tag (default %(default)s)")
    ap.add_argument("--compare", metavar="IMAGE", help="say whether IMAGE is the newest daily image of its variant")
    ap.add_argument("--base", metavar="TAG", help="print the newest date of one exact base tag instead, "
                                                  "e.g. v0.5.23-rocm10-mi35x (release, ROCm and GPU)")
    ap.add_argument("--days", type=int, metavar="N", help="with --variant or --base: only tags dated today or in the "
                                                          "N-1 days before (UTC)")
    ap.add_argument("--timeout", type=float, default=20, help="seconds per request (default %(default)s)")
    a = ap.parse_args(argv)
    # A base given blank is an unresolved base, not a request for the default variant.
    if a.base is not None and not a.base.strip():
        ap.error("--base needs a tag: <release>-<rocm>-<gpu>")
    if a.days is not None and a.days < 1:
        ap.error("--days needs a number of days, 1 or more")
    if a.compare:
        daily = parse_daily(a.compare)
        try:
            results = fetch(daily[0], daily[1], a.timeout) if daily else None
        except (OSError, ValueError):
            results = None
        print(compare(a.compare, results))
        return 0
    base = (a.base or "").strip()
    first, since = window(a.days) if a.days else ("", "")
    try:
        results = fetch(a.repo, base or a.variant, a.timeout, since=since)
        tag = pick_base(results, base, first) if base else pick(results, a.variant, first)
    except (OSError, ValueError) as e:
        print("latest_image: cannot read the %s tags on Docker Hub (%s)" % (a.repo, e), file=sys.stderr)
        return 1
    if not tag:
        print("latest_image: no %s tag of the base %s%s" % (a.repo, base, " dated in the last %d days" % a.days if a.days else "") if base
              else "latest_image: no daily %s tag for %s%s" % (a.repo, a.variant, " dated in the last %d days" % a.days if a.days else ""),
              file=sys.stderr)
        return 1
    print("%s:%s" % (a.repo, tag))
    return 0


if __name__ == "__main__":
    sys.exit(main())
