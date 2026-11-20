#!/usr/bin/env python3
"""One InferPilot task per GPU set: a lock in /dev/shm, which every container started with --ipc=host
shares with the host (scripts/run_docker.sh starts them that way), which no other machine sees, and
which a reboot clears.

    python3 scripts/run_lock.py acquire --run-dir DIR --mode MODE --task TEXT [--gpus 0,1] [--idle-hours 12]
    python3 scripts/run_lock.py release --run-dir DIR        # or: release --run-tag TAG, release --force
    python3 scripts/run_lock.py status

Tasks run at once when they share no GPU and run in different containers (a container's framework
checkout serves one task); a task without --gpus (a scan) takes the whole machine.
acquire exits 1 when a holder is in the way and prints who. A holder is dropped without asking when its
run has finished (a report newer than the lock), when the runner that took it is gone, or when its run
has written nothing in its run directory for the holder's --idle-hours. The runner is the process named by
INFERPILOT_RUN_TAG (<pid>-<start time>-<pid namespace>, set by runtime/run.mjs for every agent); it is
checked only from the pid namespace it was recorded in. The same run directory again (a retry, a resume)
gets its hold back, in the name of the runner asking from then on, unless the runner that took it still
runs, or ran in another container and is not stale: then it is busy. A run directory this container
cannot see (the run is in another container) is never taken over for silence; a lock left by a run that
died is freed at once with `release --force`. Standard library only.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import socket
import sys
import time

LOCK_FILE = os.environ.get("INFERPILOT_LOCK_FILE", "/dev/shm/inferpilot-run.lock")
REPORTS = ("run_report.md", "validation_report.md", "report.md")  # report.md: runs before run_report.md


def _open(path: str) -> int:
    """The lock file, readable and writable by everyone. /dev/shm is sticky, and protected_regular
    refuses O_CREAT on a file another user owns even to root, so an existing file is opened without it."""
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o666)
        os.fchmod(fd, 0o666)
        return fd
    except FileExistsError:
        return os.open(path, os.O_RDWR)


def _read(fd: int) -> dict:
    os.lseek(fd, 0, os.SEEK_SET)
    raw = b""
    while True:
        chunk = os.read(fd, 65536)
        if not chunk:
            break
        raw += chunk
    try:
        data = json.loads(raw.decode() or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _write(fd: int, data: dict) -> None:
    os.ftruncate(fd, 0)
    os.lseek(fd, 0, os.SEEK_SET)
    if data:
        os.write(fd, (json.dumps(data, indent=1) + "\n").encode())


def pid_ns() -> str:
    try:
        return os.readlink("/proc/self/ns/pid")
    except OSError:
        return ""


def runner_alive(tag: str) -> bool:
    """Whether the process a run tag (<pid>-<start time in clock ticks>[-<pid namespace>]) names is still
    running; one in another pid namespace counts as running, since it cannot be looked up from here."""
    pid, start, ns = (tag.split("-") + ["", ""])[:3]
    if ns and ns != pid_ns().strip("pid:[]"):
        return True
    try:
        with open("/proc/%s/stat" % int(pid), encoding="utf-8") as f:
            stat = f.read()
    except (OSError, ValueError):
        return False
    fields = stat[stat.rindex(")") + 2:].split(" ")
    if fields[0] in ("Z", "X"):  # exited, waiting only to be reaped
        return False
    return start in ("", "0") or fields[19] == start


def last_activity(holder: dict) -> float:
    """When the holder last showed life: its start, or the newest entry of its run directory or logs/."""
    latest = float(holder.get("started", 0))
    run_dir = holder.get("run_dir", "")
    for d in (run_dir, os.path.join(run_dir, "logs")) if run_dir else ():
        try:
            with os.scandir(d) as entries:
                for e in entries:
                    try:
                        latest = max(latest, e.stat(follow_symlinks=False).st_mtime)
                    except OSError:
                        pass
        except OSError:
            pass
    return latest


def stale_reason(holder: dict, now: float) -> str:
    """'' while the holder's run is alive; otherwise why its lock can be taken over."""
    if not holder.get("run_dir"):
        return "free"
    started = float(holder.get("started", 0))
    for name in REPORTS:
        try:
            if os.stat(os.path.join(holder["run_dir"], name)).st_mtime >= started:
                return "its run finished"
        except OSError:
            pass
    tag = holder.get("run_tag", "")
    if tag and holder.get("pid_ns") and holder["pid_ns"] == pid_ns() and not runner_alive(tag):
        return "its runner is gone"
    if not os.path.isdir(holder["run_dir"]):
        # Run directories live in their container: from another one, silence proves nothing.
        return ""
    idle_hours = float(holder.get("idle_hours", 12))
    if now - last_activity(holder) > idle_hours * 3600:
        return "its run wrote nothing for %g h" % idle_hours
    return ""


def _holders(data: dict) -> list:
    """The holders a lock file names; a file from before GPU sets held one, as the whole object."""
    if isinstance(data.get("holders"), list):
        return [h for h in data["holders"] if isinstance(h, dict) and h.get("run_dir")]
    return [data] if data.get("run_dir") else []


def clash(holder: dict, gpus: list, container: str) -> bool:
    """Whether a run on these GPUs (none: the whole machine) in this container must wait for holder."""
    theirs = holder.get("gpus") or []
    return not theirs or not gpus or bool(set(theirs) & set(gpus)) or holder.get("container", "") == container


def acquire(lock: str, run_dir: str, task: str = "", mode: str = "", idle_hours: float = 12,
            now: float | None = None, gpus: list | None = None) -> dict:
    now = time.time() if now is None else now
    run_dir = os.path.abspath(run_dir)
    gpus = sorted(set(gpus or []))
    container = os.environ.get("INFERPILOT_CONTAINER", "")
    tag = os.environ.get("INFERPILOT_RUN_TAG", "")
    fd = _open(lock)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        holders = _holders(_read(fd))
        # The same run directory again: its own runner keeps its hold (a new one when it asks for other
        # GPUs). Another runner (a retry, a resume) holds it from now on, unless the one that took it still
        # runs, or ran in another container and is not stale: nothing here can tell that one stopped.
        took = None
        mine = next((h for h in holders if h["run_dir"] == run_dir), None)
        if mine is not None:
            old = mine.get("run_tag", "")
            local = mine.get("container", "") == container and mine.get("pid_ns", "") == pid_ns()
            if old == tag and local and (not gpus or (mine.get("gpus") or []) == gpus):
                return {"status": "acquired", "holder": mine}
            if old != tag or not local:
                if old and mine.get("pid_ns") == pid_ns() and runner_alive(old):
                    return {"status": "busy", "holder": mine}
                reason = stale_reason(mine, now)
                if not reason and not local:
                    return {"status": "busy", "holder": mine}
                took = {"run_dir": run_dir, "reason": reason} if reason else None
            holders = [h for h in holders if h is not mine]
        live, gone = [], []
        for h in holders:
            reason = stale_reason(h, now)
            (gone if reason else live).append((h, reason))
        in_way = [h for h, _ in live if clash(h, gpus, container)]
        if in_way:
            return {"status": "busy", "holder": in_way[0]}
        new = {"run_dir": run_dir, "mode": mode, "task": task, "gpus": gpus, "idle_hours": idle_hours, "started": now,
               "started_at": time.strftime("%Y-%m-%d %H:%M:%S %z", time.localtime(now)),
               "host": socket.gethostname(), "container": container, "run_tag": tag, "pid_ns": pid_ns()}
        _write(fd, {"holders": [h for h, _ in live] + [new]})
        out = {"status": "acquired", "holder": new}
        if took or gone:
            out["took_over"] = took or {"run_dir": gone[0][0].get("run_dir", ""), "reason": gone[0][1]}
        return out
    finally:
        os.close(fd)  # closing the descriptor releases the flock


def release(lock: str, run_dir: str = "", force: bool = False, run_tag: str = "") -> dict:
    """Frees the holder named by run_dir or run_tag; force alone frees every holder."""
    try:
        fd = os.open(lock, os.O_RDWR)
    except FileNotFoundError:
        return {"status": "free"}
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        holders = _holders(_read(fd))
        if not holders:
            return {"status": "free"}
        named = run_dir or run_tag
        gone = [h for h in holders if (force and not named) or (run_dir and h["run_dir"] == os.path.abspath(run_dir))
                or (run_tag and h.get("run_tag") == run_tag)]
        if not gone:
            return {"status": "held", "holder": holders[0]}
        left = [h for h in holders if h not in gone]
        _write(fd, {"holders": left} if left else {})
        return {"status": "released", "holder": gone[0]}
    finally:
        os.close(fd)


def describe(holder: dict, now: float) -> dict:
    out = {"holder": holder, "idle_hours_so_far": round((now - last_activity(holder)) / 3600, 1)}
    if holder.get("run_tag") and holder.get("pid_ns") == pid_ns():
        out["runner"] = "running" if runner_alive(holder["run_tag"]) else "gone"
    reason = stale_reason(holder, now)
    if reason:
        out["reason"] = reason
    return out


def status(lock: str, now: float | None = None) -> dict:
    """free, or held (stale when every holder is), with each holder; the first one's fields at the top too."""
    now = time.time() if now is None else now
    try:
        fd = os.open(lock, os.O_RDONLY)
    except FileNotFoundError:
        return {"status": "free"}
    try:
        fcntl.flock(fd, fcntl.LOCK_SH)
        holders = _holders(_read(fd))
    finally:
        os.close(fd)
    if not holders:
        return {"status": "free"}
    each = [describe(h, now) for h in holders]
    return {"status": "stale" if all("reason" in d for d in each) else "held", **each[0], "holders": each}


def others(lock: str, now: float | None = None) -> list:
    """The live holders of other runs than this runner's (its run tag, container and pid namespace): the
    GPUs they hold are not free, however idle they look (gpu_status.py --free)."""
    now = time.time() if now is None else now
    try:
        fd = os.open(lock, os.O_RDONLY)
    except FileNotFoundError:
        return []
    try:
        fcntl.flock(fd, fcntl.LOCK_SH)
        holders = _holders(_read(fd))
    finally:
        os.close(fd)
    me = (os.environ.get("INFERPILOT_RUN_TAG", ""), os.environ.get("INFERPILOT_CONTAINER", ""), pid_ns())
    return [h for h in holders if (h.get("run_tag", ""), h.get("container", ""), h.get("pid_ns", "")) != me
            and not stale_reason(h, now)]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="One InferPilot task per GPU set.")
    ap.add_argument("--lock", default=LOCK_FILE, help="lock file (default %(default)s; INFERPILOT_LOCK_FILE)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    acq = sub.add_parser("acquire", help="take GPUs (or the machine) for a run; exit 1 when a holder is in the way")
    acq.add_argument("--run-dir", required=True)
    acq.add_argument("--mode", default="")
    acq.add_argument("--task", default="")
    acq.add_argument("--gpus", default="", help="HIP numbers, e.g. 0,1 (default: the whole machine)")
    acq.add_argument("--idle-hours", type=float, default=12,
                     help="hours without a write in the run directory after which the lock counts as stale")
    rel = sub.add_parser("release", help="free the machine")
    rel.add_argument("--run-dir", default="")
    rel.add_argument("--run-tag", default="", help="free it when the runner with this tag holds it")
    rel.add_argument("--force", action="store_true", help="free it whoever holds it (a run that died)")
    sub.add_parser("status", help="who holds the machine")
    a = ap.parse_args(argv)
    if a.cmd == "acquire":
        out = acquire(a.lock, a.run_dir, a.task, a.mode, a.idle_hours,
                      gpus=[int(g) for g in a.gpus.split(",") if g.strip()])
    elif a.cmd == "release":
        out = release(a.lock, a.run_dir, a.force, a.run_tag)
    else:
        out = status(a.lock)
    print(json.dumps(out, indent=1))
    return 1 if a.cmd != "status" and out["status"] in ("busy", "held") else 0


if __name__ == "__main__":
    sys.exit(main())
