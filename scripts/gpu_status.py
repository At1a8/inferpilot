#!/usr/bin/env python3
"""The GPUs as HIP numbers them, the numbers HIP_VISIBLE_DEVICES takes, and what holds memory on each.

    python3 scripts/gpu_status.py                # every GPU: HIP number, model, arch, PCI bus, memory held and by whom
    python3 scripts/gpu_status.py --free 2       # the first 2 GPUs nothing holds memory on and no other
                                                 # InferPilot task holds (scripts/run_lock.py): {"free": "0,2", ...}
    python3 scripts/gpu_status.py --check 0,2    # exit 1 when a process holds memory on one of them
    python3 scripts/gpu_status.py --check all    # the same for every GPU: before a performance measurement

rocm-smi numbers cards in an order of its own, which on some hosts differs from HIP's (HIP device 1 can be
rocm-smi's card 3): GPUs chosen by rocm-smi's numbers can be GPUs someone else is using. Here each HIP device
is matched to its KFD node by PCI bus (torch's device properties), and the memory each process holds on it
comes from /sys/class/kfd/kfd/proc/<pid>/vram_<gpu id>, which lists the processes of every container on the
host (by host pid) and creates no context on any GPU. A process holding less than --min-mib (default 256)
does not count. A GPU that matches no KFD node is never free, and without torch, whose device properties
give the PCI bus, or with a torch that finds no GPU where KFD lists some, the script stops (exit 2) rather
than guess HIP's order. A GPU another InferPilot task holds in the run lock is not free either, though it
may hold no memory yet (a run in its Spec or Design phase), and none is while one holds the whole machine;
"locked" lists those tasks. Prints one JSON object; --free exits 1 when fewer GPUs are free, --check when
one is held or unknown, or when KFD's process accounting cannot be read.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_lock  # noqa: E402

KFD = "/sys/class/kfd/kfd"


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def kfd_nodes(root: str = KFD) -> list:
    """The GPU nodes of the KFD topology, in node order: {node, gpu_id, bus, domain}."""
    out = []
    nodes = os.path.join(root, "topology", "nodes")
    for name in sorted(os.listdir(nodes) if os.path.isdir(nodes) else [], key=lambda n: int(n) if n.isdigit() else -1):
        gpu_id = int(_read(os.path.join(nodes, name, "gpu_id")).strip() or 0)
        if not gpu_id:
            continue
        props = dict(line.split()[:2] for line in _read(os.path.join(nodes, name, "properties")).splitlines()
                     if len(line.split()) >= 2)
        out.append({"node": int(name), "gpu_id": gpu_id, "bus": int(props.get("location_id", 0)) >> 8,
                    "domain": int(props.get("domain", 0))})
    return out


def holders(root: str = KFD):
    """gpu_id -> {host pid: MiB it holds there}, and the gpu ids whose accounting could not be read: None
    when none of it can (no readable proc directory, or a process that cannot be listed, which could be on
    any GPU). A process that exits during the scan is skipped."""
    out, unknown = {}, set()
    proc = os.path.join(root, "proc")
    try:
        pids = os.listdir(proc)
    except OSError:
        return out, None
    for pid in pids:
        try:
            files = os.listdir(os.path.join(proc, pid))
        except FileNotFoundError:
            continue
        except OSError:
            return out, None
        for f in files:
            if not f.startswith("vram_"):
                continue
            try:
                with open(os.path.join(proc, pid, f), encoding="utf-8") as fh:
                    v = fh.read().strip()
            except FileNotFoundError:
                continue
            except OSError:
                unknown.add(int(f[5:]))
                continue
            if v.isdigit() and int(v) > 0:
                out.setdefault(int(f[5:]), {})[int(pid)] = int(v) // 2 ** 20
    return out, unknown


def hip_devices():
    """The devices as HIP numbers them, all of them (no *_VISIBLE_DEVICES), or None without torch."""
    for k in ("HIP_VISIBLE_DEVICES", "ROCR_VISIBLE_DEVICES", "CUDA_VISIBLE_DEVICES"):
        os.environ.pop(k, None)
    try:
        import torch
    except ImportError:
        return None
    out = []
    for i in range(torch.cuda.device_count()):
        p = torch.cuda.get_device_properties(i)
        out.append({"index": i, "name": p.name, "arch": str(getattr(p, "gcnArchName", "")).split(":")[0],
                    "bus": int(getattr(p, "pci_bus_id", -1)), "domain": int(getattr(p, "pci_domain_id", 0)),
                    "total_mib": int(p.total_memory) // 2 ** 20})
    return out


def status(nodes: list, devices: list, held: dict, min_mib: int = 256, unknown=frozenset()) -> list:
    """One row per HIP device. used_mib None: nothing is known of its use (no KFD node matches it, or its
    accounting is unreadable; unknown None: all of it is)."""
    by_bus = {(n["domain"], n["bus"]): n for n in nodes}
    rows = []
    for d in devices:
        n = by_bus.get((d.get("domain", 0), d["bus"]))
        if not n or unknown is None or n["gpu_id"] in unknown:
            rows.append({**d, "kfd_node": n["node"] if n else None, "used_mib": None, "pids": {}})
            continue
        procs = {pid: mib for pid, mib in sorted(held.get(n["gpu_id"], {}).items()) if mib >= min_mib}
        rows.append({**d, "kfd_node": n["node"], "used_mib": sum(procs.values()),
                     "pids": {str(p): m for p, m in procs.items()}})
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="GPUs by HIP number and what holds memory on them.")
    ap.add_argument("--free", type=int, default=0, help="name this many GPUs nothing holds memory on")
    ap.add_argument("--check", default="", help="HIP numbers that must be free, e.g. 0,2, or all")
    ap.add_argument("--min-mib", type=int, default=256)
    ap.add_argument("--kfd", default=KFD, help=argparse.SUPPRESS)
    ap.add_argument("--devices", default="", help=argparse.SUPPRESS)  # the HIP devices as JSON, for tests
    ap.add_argument("--lock", default=run_lock.LOCK_FILE, help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    nodes, (held, unknown) = kfd_nodes(a.kfd), holders(a.kfd)  # read before torch makes this process a holder
    if a.devices:
        with open(a.devices, encoding="utf-8") as f:
            devices = json.load(f)
    else:
        devices = hip_devices()
    if devices is None:
        print(json.dumps({"error": "torch is needed to number the GPUs as HIP does; KFD's order can differ",
                          "kfd_nodes": nodes}, indent=1))
        return 2
    if not devices and nodes:
        print(json.dumps({"error": "torch finds no GPU where KFD lists %d: a torch without ROCm, or HIP failed to start"
                          % len(nodes), "kfd_nodes": nodes}, indent=1))
        return 2
    rows = status(nodes, devices, held, a.min_mib, unknown)
    out, rc = {"gpus": rows}, 0
    if unknown is None:
        out["error"] = "KFD's process accounting (%s/proc) is unreadable: no GPU is known to be free" % a.kfd
    taken = run_lock.others(a.lock)
    if taken:
        out["locked"] = [{"run_dir": h["run_dir"], "task": h.get("task", ""), "container": h.get("container", ""),
                          "gpus": h.get("gpus") or "all"} for h in taken]
    if a.free:
        locked = {g for h in taken for g in h.get("gpus") or []}
        whole = any(not h.get("gpus") for h in taken)
        free = [] if whole else [str(r["index"]) for r in rows if r["used_mib"] == 0 and r["index"] not in locked][:a.free]
        out["free"] = ",".join(free)
        rc = 0 if len(free) == a.free else 1
    if a.check:
        want = ({r["index"] for r in rows} if a.check.strip() == "all"
                else {int(x) for x in a.check.split(",") if x.strip()})
        out["busy"] = [r for r in rows if r["index"] in want and r["used_mib"]]
        out["unknown"] = [r["index"] for r in rows if r["index"] in want and r["used_mib"] is None]
        out["missing"] = sorted(want - {r["index"] for r in rows})
        rc = 1 if out["busy"] or out["unknown"] or out["missing"] or unknown is None else rc
    print(json.dumps(out, indent=1))
    return rc


if __name__ == "__main__":
    sys.exit(main())
