#!/usr/bin/env python3
"""The GPUs as HIP numbers them, the numbers HIP_VISIBLE_DEVICES takes, how much of each is in use, and by whom.

    python3 scripts/gpu_status.py                # every GPU: HIP number, KFD node, PCI address, arch, VRAM%, GPU%, pids
    python3 scripts/gpu_status.py --check all    # exit 1 when a GPU is in use: the machine is not idle
    python3 scripts/gpu_status.py --check 0,2    # the same for these GPUs (comma-separated HIP numbers)

HIP numbers the GPUs in the order of their KFD topology nodes (/sys/class/kfd/kfd/topology/nodes: the CPUs
first, then the GPUs, so on a two-socket host GPU 0 is node 2), which is rocm-smi's Node column. rocm-smi's
Device column numbers them by PCI address instead, which can differ (Device 0 can be HIP's GPU 1): GPUs chosen
by it can be GPUs someone else is using.

A GPU is in use while its VRAM% (memory allocated) or its GPU% (busy time) reaches --max-vram-pct or
--max-busy-pct, 5 by default: the two columns rocm-smi shows, read where it reads them, from the GPU's DRM
device (mem_info_vram_used, mem_info_vram_total, gpu_busy_percent; GPU% the highest of --samples reads). An
idle GPU's own driver allocations stay far below that. Who holds memory on it comes from
/sys/class/kfd/kfd/proc/<pid>/vram_<gpu id>, which lists the processes of every container on the host by host
pid (so it cannot tell this run's own processes inside a container: check before this run's servers start);
it names the holders and decides nothing. A GPU whose use cannot be read is in use too. ROCR_VISIBLE_DEVICES,
which every server inherits, renumbers HIP's GPUs: while it is set the script stops (exit 2) rather than name
GPUs the servers would see under other numbers, and so it does when KFD lists no GPU, when a GPU node's id
cannot be read (the numbers after it would shift), and on a --check it cannot parse. Prints one JSON object;
--check exits 1 when one of its GPUs is in use, unknown or missing.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

KFD = "/sys/class/kfd/kfd"
DRM = "/sys/class/drm"


class Unreadable(Exception):
    pass


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def kfd_gpus(root: str = KFD) -> list:
    """The GPU nodes of the KFD topology in node order, which is HIP's: {hip, node, gpu_id, render_minor, pci,
    arch}. A node whose gpu_id cannot be read stops it: dropped, it would shift every HIP number after it."""
    out = []
    nodes = os.path.join(root, "topology", "nodes")
    for name in sorted((n for n in os.listdir(nodes) if n.isdigit()) if os.path.isdir(nodes) else [], key=int):
        raw = _read(os.path.join(nodes, name, "gpu_id")).strip()
        if not raw.isdigit():
            raise Unreadable("KFD node %s: its gpu_id cannot be read" % name)
        if raw == "0":
            continue
        props = {}
        for line in _read(os.path.join(nodes, name, "properties")).splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                props[parts[0]] = int(parts[1])
        loc, gfx = props.get("location_id", 0), props.get("gfx_target_version", 0)
        out.append({"hip": len(out), "node": int(name), "gpu_id": int(raw), "render_minor": props.get("drm_render_minor"),
                    "pci": "%04x:%02x:%02x.%x" % (props.get("domain", 0), loc >> 8, (loc >> 3) & 31, loc & 7),
                    "arch": "gfx%d%d%x" % (gfx // 10000, gfx // 100 % 100, gfx % 100) if gfx else ""})
    return out


def use(gpus: list, drm: str = DRM, samples: int = 3) -> list:
    """(VRAM%, GPU%) of each GPU as rocm-smi shows them, GPU% the highest of its samples (every GPU read in each
    round); None for one that cannot be read."""
    devs = [os.path.join(drm, "renderD%s" % g["render_minor"], "device") if g.get("render_minor") is not None else ""
            for g in gpus]
    vram = []
    for dev in devs:
        used = _read(os.path.join(dev, "mem_info_vram_used")).strip() if dev else ""
        total = _read(os.path.join(dev, "mem_info_vram_total")).strip() if dev else ""
        vram.append(round(100 * int(used) / int(total), 1) if used.isdigit() and total.isdigit() and int(total) else None)
    busy = [0] * len(devs)
    for i in range(max(1, samples)):
        if i:
            time.sleep(0.2)
        for k, dev in enumerate(devs):
            b = _read(os.path.join(dev, "gpu_busy_percent")).strip() if dev else ""
            busy[k] = max(busy[k], int(b)) if b.isdigit() and busy[k] is not None else None
    return list(zip(vram, busy))


def holders(root: str = KFD) -> dict:
    """gpu_id -> {host pid: MiB it holds there, rounded up}; empty where KFD's accounting cannot be read."""
    out = {}
    proc = os.path.join(root, "proc")
    for pid in os.listdir(proc) if os.path.isdir(proc) else []:
        try:
            files = os.listdir(os.path.join(proc, pid))
        except OSError:
            continue
        for f in files:
            v = _read(os.path.join(proc, pid, f)).strip() if f.startswith("vram_") else ""
            if v.isdigit() and int(v) > 0:
                out.setdefault(int(f[5:]), {})[int(pid)] = -(-int(v) // 2 ** 20)
    return out


def main(argv=None, env=os.environ) -> int:
    ap = argparse.ArgumentParser(description="GPUs by HIP number, how much of each is in use, and by whom.")
    ap.add_argument("--check", default="", help="comma-separated HIP numbers that must be idle, e.g. 0,2, or all")
    ap.add_argument("--max-vram-pct", type=float, default=5)
    ap.add_argument("--max-busy-pct", type=float, default=5)
    ap.add_argument("--samples", type=int, default=3, help="reads of GPU%%, 0.2 s apart; the highest counts")
    ap.add_argument("--kfd", default=KFD, help=argparse.SUPPRESS)
    ap.add_argument("--drm", default=DRM, help=argparse.SUPPRESS)
    a = ap.parse_args(argv)

    def stop(error: str) -> int:
        print(json.dumps({"error": error}, indent=1))
        return 2

    if env.get("ROCR_VISIBLE_DEVICES"):
        return stop("ROCR_VISIBLE_DEVICES=%s renumbers the GPUs every server sees; unset it where InferPilot runs"
                    % env["ROCR_VISIBLE_DEVICES"])
    check = a.check.replace(" ", "")
    if check and check != "all" and not all(x.isdigit() for x in check.split(",")):
        return stop("--check takes comma-separated HIP numbers (as HIP_VISIBLE_DEVICES does) or all, not %r" % a.check)
    try:
        gpus = kfd_gpus(a.kfd)
    except Unreadable as e:
        return stop(str(e))
    if not gpus:
        return stop("KFD lists no GPU (%s/topology): no ROCm driver, or no /dev/kfd here" % a.kfd)
    held = holders(a.kfd)
    rows = []
    for g, (vram, busy) in zip(gpus, use(gpus, a.drm, a.samples)):
        free = vram is not None and busy is not None and vram < a.max_vram_pct and busy < a.max_busy_pct
        rows.append({**{k: v for k, v in g.items() if k != "render_minor"}, "vram_pct": vram, "busy_pct": busy,
                     "in_use": None if vram is None or busy is None else not free,
                     "pids": {str(p): m for p, m in sorted(held.get(g["gpu_id"], {}).items())}})
    out, rc = {"gpus": rows}, 0
    if check:
        want = {r["hip"] for r in rows} if check == "all" else {int(x) for x in check.split(",")}
        out["busy"] = [r for r in rows if r["hip"] in want and r["in_use"]]
        out["unknown"] = [r["hip"] for r in rows if r["hip"] in want and r["in_use"] is None]
        out["missing"] = sorted(want - {r["hip"] for r in rows})
        rc = 1 if out["busy"] or out["unknown"] or out["missing"] else 0
    print(json.dumps(out, indent=1))
    return rc


if __name__ == "__main__":
    sys.exit(main())
