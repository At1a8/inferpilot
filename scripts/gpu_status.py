#!/usr/bin/env python3
"""The GPUs as HIP numbers them, the numbers HIP_VISIBLE_DEVICES takes, and what holds memory on each.

    python3 scripts/gpu_status.py                # every GPU: HIP number, KFD node, PCI address, arch, memory held and by whom
    python3 scripts/gpu_status.py --check all    # exit 1 when a process holds memory on any GPU: the machine is not idle
    python3 scripts/gpu_status.py --check 0,2    # the same for these GPUs

HIP numbers the GPUs in the order of their KFD topology nodes (/sys/class/kfd/kfd/topology/nodes: the CPUs
first, then the GPUs, so on a two-socket host GPU 0 is node 2), which is rocm-smi's Node column. rocm-smi's
Device column numbers them by PCI address instead, which can differ (Device 0 can be HIP's GPU 1): GPUs chosen
by it can be GPUs someone else is using. The memory each process holds comes from
/sys/class/kfd/kfd/proc/<pid>/vram_<gpu id>, which lists the processes of every container on the host (by host
pid) and creates no context on any GPU; a process holding less than --min-mib (default 256) does not count.
ROCR_VISIBLE_DEVICES, which every server inherits, renumbers HIP's GPUs: while it is set the script stops
(exit 2) rather than name GPUs the servers would see under other numbers, and so it does when KFD lists no
GPU. Prints one JSON object; --check exits 1 when one of its GPUs is held, unknown or missing, or when KFD's
process accounting cannot be read.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

KFD = "/sys/class/kfd/kfd"


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def kfd_gpus(root: str = KFD) -> list:
    """The GPU nodes of the KFD topology in node order, which is HIP's: {hip, node, gpu_id, pci, arch}."""
    out = []
    nodes = os.path.join(root, "topology", "nodes")
    for name in sorted((n for n in os.listdir(nodes) if n.isdigit()) if os.path.isdir(nodes) else [], key=int):
        gpu_id = int(_read(os.path.join(nodes, name, "gpu_id")).strip() or 0)
        if not gpu_id:
            continue
        props = {}
        for line in _read(os.path.join(nodes, name, "properties")).splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                props[parts[0]] = int(parts[1])
        loc, gfx = props.get("location_id", 0), props.get("gfx_target_version", 0)
        out.append({"hip": len(out), "node": int(name), "gpu_id": gpu_id,
                    "pci": "%04x:%02x:%02x.%x" % (props.get("domain", 0), loc >> 8, (loc >> 3) & 31, loc & 7),
                    "arch": "gfx%d%d%x" % (gfx // 10000, gfx // 100 % 100, gfx % 100) if gfx else ""})
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


def status(gpus: list, held: dict, min_mib: int = 256, unknown=frozenset()) -> list:
    """One row per GPU. used_mib None: its use is unknown (its accounting is unreadable; unknown None: all
    of it is)."""
    rows = []
    for g in gpus:
        if unknown is None or g["gpu_id"] in unknown:
            rows.append({**g, "used_mib": None, "pids": {}})
            continue
        procs = {pid: mib for pid, mib in sorted(held.get(g["gpu_id"], {}).items()) if mib >= min_mib}
        rows.append({**g, "used_mib": sum(procs.values()), "pids": {str(p): m for p, m in procs.items()}})
    return rows


def main(argv=None, env=os.environ) -> int:
    ap = argparse.ArgumentParser(description="GPUs by HIP number and what holds memory on them.")
    ap.add_argument("--check", default="", help="HIP numbers that must be free, e.g. 0,2, or all")
    ap.add_argument("--min-mib", type=int, default=256)
    ap.add_argument("--kfd", default=KFD, help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    if env.get("ROCR_VISIBLE_DEVICES"):
        print(json.dumps({"error": "ROCR_VISIBLE_DEVICES=%s renumbers the GPUs every server sees; unset it where "
                          "InferPilot runs" % env["ROCR_VISIBLE_DEVICES"]}, indent=1))
        return 2
    gpus = kfd_gpus(a.kfd)
    if not gpus:
        print(json.dumps({"error": "KFD lists no GPU (%s/topology): no ROCm driver, or no /dev/kfd here" % a.kfd}, indent=1))
        return 2
    held, unknown = holders(a.kfd)
    out, rc = {"gpus": status(gpus, held, a.min_mib, unknown)}, 0
    if unknown is None:
        out["error"] = "KFD's process accounting (%s/proc) is unreadable: no GPU is known to be free" % a.kfd
    if a.check:
        rows = out["gpus"]
        want = {r["hip"] for r in rows} if a.check.strip() == "all" else {int(x) for x in a.check.split(",") if x.strip()}
        out["busy"] = [r for r in rows if r["hip"] in want and r["used_mib"]]
        out["unknown"] = [r["hip"] for r in rows if r["hip"] in want and r["used_mib"] is None]
        out["missing"] = sorted(want - {r["hip"] for r in rows})
        rc = 1 if out["busy"] or out["unknown"] or out["missing"] or unknown is None else 0
    print(json.dumps(out, indent=1))
    return rc


if __name__ == "__main__":
    sys.exit(main())
