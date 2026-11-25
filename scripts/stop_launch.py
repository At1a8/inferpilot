#!/usr/bin/env python3
"""Stop what detached launches started (knowledge/env_hygiene.md, Servers).

    python3 scripts/stop_launch.py <log> [<log>...] [--grace 30]
    docker exec -i <container> python3 - <log> [<log>...] < scripts/stop_launch.py    # where the launches run

A launch gives its wrapper INFERPILOT_LAUNCH, the full path of its log, and every process the run starts
inherits it, whatever becomes of its parent, its session or the wrapper. The processes of a launch are
those whose environment holds that path, and what they started, whatever its environment: no PID is read
from a file, so one that another process has taken since is never signalled, and each process found is
known from then on by its PID and start time. SIGTERM to each, SIGKILL to those still there --grace
seconds later, looking again all the while for ones started meanwhile; the logs of one command are
stopped together, so it takes little more than twice the grace however many they are. The exit status
is 0 once three looks in a row find none left (a zombie has exited; a process in the middle of an exec
shows no environment for a moment), 1 when one is still there --grace seconds after SIGKILL, named in
the output, and 2 when a log does not exist: another path than the launch's. It never signals itself,
whatever its own environment holds. Out of its reach: a process that emptied its environment and left
its parent, and one of another user whose environment this user cannot read. So 0 says the processes
it could find are gone, not that the GPUs are free: its caller checks those before the first launch and
after the stop (scripts/gpu_status.py --check, as Servers says). Run it where the launches run: in a
container their PIDs and environments are the container's. Standard library only.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time

MARK = b"INFERPILOT_LAUNCH="


def table() -> dict:
    """Every process: PID -> (state, parent, start time in clock ticks)."""
    out = {}
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open("/proc/%s/stat" % name, encoding="utf-8", errors="replace") as f:
                stat = f.read()
        except OSError:
            continue
        try:
            fields = stat[stat.rindex(")") + 2:].split(" ")
            out[int(name)] = (fields[0], int(fields[1]), fields[19])
        except (ValueError, IndexError):  # gone while it was read
            continue
    return out


def marked(procs: dict, marks: set) -> set:
    """The processes whose environment holds one of marks."""
    out = set()
    for pid in procs:
        try:
            with open("/proc/%d/environ" % pid, "rb") as f:
                env = f.read()
        except OSError:
            continue
        if MARK in env and not marks.isdisjoint(env.split(b"\0")):
            out.add(pid)
    return out


def below(roots, procs: dict) -> set:
    """roots and every process below them."""
    kids = {}
    for pid, p in procs.items():
        kids.setdefault(p[1], []).append(pid)
    out, todo = set(roots), list(roots)
    while todo:
        for kid in kids.get(todo.pop(), []):
            if kid not in out:
                out.add(kid)
                todo.append(kid)
    return out


def command(pid: int) -> str:
    try:
        with open("/proc/%d/cmdline" % pid, "rb") as f:
            return f.read().replace(b"\0", b" ").decode("utf-8", "replace").strip()
    except OSError:
        return ""


def stop(logs, grace: float = 30, poll: float = 0.2) -> tuple:
    missing = [log for log in logs if not os.path.isfile(log)]
    if missing:
        return 2, {"status": "no_log", "logs": missing}
    marks = {MARK + os.fsencode(os.path.realpath(log)) for log in logs}
    known, sent, quiet = set(), set(), 0  # known: (pid, start time)
    sig, deadline = signal.SIGTERM, time.monotonic() + grace
    while True:
        procs = table()
        here = {p for p, start in known if p in procs and procs[p][2] == start}
        found = below(marked(procs, marks) | here, procs) - {os.getpid()}
        known |= {(p, procs[p][2]) for p in found if p in procs}
        live = sorted(p for p, start in known if p in procs and procs[p][2] == start and procs[p][0] not in ("Z", "X"))
        quiet = 0 if live else quiet + 1
        if quiet == 3:
            return 0, {"status": "stopped", "pids": sorted(p for p, _ in known)}
        if live and time.monotonic() >= deadline:
            if sig == signal.SIGKILL:
                return 1, {"status": "stuck", "left": [{"pid": p, "command": command(p)} for p in live]}
            sig, deadline = signal.SIGKILL, time.monotonic() + grace
        for p in live:
            if sig == signal.SIGKILL or p not in sent:
                try:
                    os.kill(p, sig)
                except OSError:
                    pass
                sent.add(p)
        time.sleep(min(poll, max(grace / 4, 0.01)))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Stop what detached launches started.")
    ap.add_argument("log", nargs="+", help="a launch's log, the path its wrapper got as INFERPILOT_LAUNCH")
    ap.add_argument("--grace", type=float, default=30,
                    help="seconds from SIGTERM to SIGKILL, and from SIGKILL to giving up (default %(default)s)")
    a = ap.parse_args(argv)
    if not a.grace >= 0:
        ap.error("--grace takes seconds, 0 or more")
    code, out = stop(a.log, a.grace)
    print(json.dumps(out, indent=1))
    return code


if __name__ == "__main__":
    sys.exit(main())
