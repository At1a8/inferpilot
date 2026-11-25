#!/usr/bin/env python3
"""Stop what a detached launch started (knowledge/env_hygiene.md, Servers), from its launch record.

    python3 scripts/stop_launch.py <log> [--grace 30]

The wrapper's PID is the one in <log>.pid. While the wrapper still runs (its command names <log>), it,
every process below it and every process of its session are listed before the first signal, since one
whose parent exits first is reparented away from the wrapper, and the wrapper can exit while its workers
still shut down; each is known from then on by its PID and start time, and what they start meanwhile is
added as it appears. SIGTERM to each, SIGKILL to those still there --grace seconds later, and the exit
status is 0 once every one has exited (a zombie has), 1 when one is still there --grace seconds after
SIGKILL: stuck, named in the output. A wrapper that had already exited leaves nothing that can be tied
to the record: the processes still in its session are named and left alone, with status 1, and the PID
taken by another process means nothing of the launch is left in its session (0). A process that left
both the session and the tree before the stop (a daemon) is out of its reach. Status 2: no record.
Standard library only.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time


def table() -> dict:
    """Every process: PID -> (state, parent, session, start time in clock ticks)."""
    out = {}
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open("/proc/%s/stat" % name, encoding="utf-8", errors="replace") as f:
                stat = f.read()
        except OSError:
            continue
        fields = stat[stat.rindex(")") + 2:].split(" ")
        out[int(name)] = (fields[0], int(fields[1]), int(fields[3]), fields[19])
    return out


def command(pid: int) -> list:
    try:
        with open("/proc/%d/cmdline" % pid, "rb") as f:
            return [a.decode("utf-8", "replace") for a in f.read().split(b"\0") if a]
    except OSError:
        return []


def named(procs: dict, pids) -> list:
    return [{"pid": p, "command": " ".join(command(p))} for p in sorted(pids) if p in procs]


def spread(roots, procs: dict, session: int | None) -> set:
    """roots and every process below them; with a session, its processes too and every one below them."""
    kids = {}
    for pid, p in procs.items():
        kids.setdefault(p[1], []).append(pid)
    out = {p for p in roots if p in procs}
    if session is not None:
        out |= {pid for pid, p in procs.items() if p[2] == session}
    todo = list(out)
    while todo:
        for kid in kids.get(todo.pop(), []):
            if kid not in out:
                out.add(kid)
                todo.append(kid)
    return out


def stop(log: str, grace: float = 30, poll: float = 0.2) -> tuple:
    try:
        with open(log + ".pid", encoding="utf-8") as f:
            root = int(f.read().strip())
    except (OSError, ValueError):
        return 2, {"status": "no_record", "log": log}
    procs = table()
    running = root in procs and procs[root][0] not in ("Z", "X")
    if not running or not any(os.path.basename(a) == os.path.basename(log) for a in command(root)):
        if running:  # its PID is another process's: it was free, so the session had no process left
            return 0, {"status": "exited", "left": []}
        left = [p for p, v in procs.items() if v[2] == root and v[0] not in ("Z", "X")]
        return (1 if left else 0), {"status": "exited", "left": named(procs, left)}
    known = {(p, procs[p][3]) for p in spread([root], procs, root)}
    sent, sig, deadline = set(), signal.SIGTERM, time.monotonic() + grace
    while True:
        procs = table()
        here = [p for p, start in known if p in procs and procs[p][3] == start]
        # The session is still the launch's while one of its processes, a zombie too, keeps its ID taken.
        held = any(procs[p][2] == root for p in here)
        known |= {(p, procs[p][3]) for p in spread(here, procs, root if held else None)}
        live = {p for p, start in known if p in procs and procs[p][3] == start and procs[p][0] not in ("Z", "X")}
        if not live:
            return 0, {"status": "stopped", "pids": sorted(p for p, _ in known)}
        if time.monotonic() >= deadline:
            if sig == signal.SIGKILL:
                return 1, {"status": "stuck", "left": named(procs, live)}
            sig, deadline = signal.SIGKILL, time.monotonic() + grace
        for p in live:
            if sig == signal.SIGKILL or p not in sent:
                try:
                    os.kill(p, sig)
                except OSError:
                    pass
                sent.add(p)
        time.sleep(poll)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Stop what a detached launch started, from its launch record.")
    ap.add_argument("log", help="the launch's log; the wrapper's PID is in <log>.pid")
    ap.add_argument("--grace", type=float, default=30,
                    help="seconds from SIGTERM to SIGKILL, and from SIGKILL to giving up (default %(default)s)")
    a = ap.parse_args(argv)
    code, out = stop(a.log, a.grace)
    print(json.dumps(out, indent=1))
    return code


if __name__ == "__main__":
    sys.exit(main())
