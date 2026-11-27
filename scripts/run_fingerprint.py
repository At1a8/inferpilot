#!/usr/bin/env python3
"""What a run measures with that lies outside its run directory, written down when the run is set up and held
against when the run is resumed from Verify.

    python3 scripts/run_fingerprint.py record --run-dir RUN_DIR --gpus 0,1,2,3 [--model DIR] \\
        [--accuracy-script F] [--replace]
    python3 scripts/run_fingerprint.py check --run-dir RUN_DIR --gpus 4,5,6,7 [--model DIR] [--accuracy-script F]

The run directory holds the scripts a run measures with, and the task definition names the model by its path.
Some of what it measures with lies outside the directory and can change under the same name: the model's files
(a checkpoint replaced at its path, a link that points elsewhere now), the GPUs the run is pinned to (how many,
and what each is) and scripts/eval_accuracy.sh, which InferPilot's accuracy script runs from this checkout. A run
resumed from Verify holds what it measures against what the run measured before it: on another model, on other
GPUs or by another harness, the two say nothing of the change.

record writes RUN_DIR/fingerprint.json:
  model_files  the model directory as scripts/baseline_cache.py fingerprints it (its small files by content, its
               weights by name and size); empty without --model
  gpus         each GPU of --gpus (HIP numbers, as HIP_VISIBLE_DEVICES takes them) by what it is: its PCI device
               id (two products can have one architecture and as much memory), its architecture and its
               memory, in no order of their numbers: another set of the same GPUs is the same
  harness      the contents of scripts/eval_accuracy.sh when --accuracy-script runs it (its text names it, as
               InferPilot's rendered accuracy.sh does); empty for a script that does not, and without one
A record that says the same stays as it is. One that says otherwise, or cannot be read, is replaced only with
--replace (exit 1 without it): a run that is resumed keeps the record it was set up with, and only a run that
measures everything again writes a new one. check computes the same and compares it with the record: exit 0
when nothing differs; exit 1, naming what differs, when something does, or when RUN_DIR has no record that can
be read (nothing shows what the run measured with). Both exit 2 when they cannot tell: ROCR_VISIBLE_DEVICES is
set (it renumbers the GPUs every server sees), --gpus names a GPU KFD does not list or one of which a part
cannot be read, --model is no directory, or a file cannot be read or written. A record --replace that ends so
removes the record it was to replace, which is no longer what the run measures with: a run without a record
is not resumed, where one with an earlier attempt's would be held against the wrong one. Prints one JSON
object. Standard library only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import baseline_cache  # noqa: E402
import gpu_status  # noqa: E402

RECORD = "fingerprint.json"
PARTS = ("model_files", "gpus", "harness")


class CannotTell(Exception):
    pass


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def device_id(kfd: str, node: int) -> str:
    """The PCI device id of a KFD node, as lspci writes it (0x74a1); '' when its properties do not have it."""
    for line in _read(os.path.join(kfd, "topology", "nodes", str(node), "properties")).splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "device_id" and parts[1].isdigit():
            return "0x%04x" % int(parts[1])
    return ""


def gpus_of(spec: str, kfd: str = gpu_status.KFD, drm: str = gpu_status.DRM, env=os.environ) -> list:
    """What each GPU of spec is, {device, arch, vram_bytes}, sorted: which numbers they have is no part of it."""
    if "ROCR_VISIBLE_DEVICES" in env:
        raise CannotTell("ROCR_VISIBLE_DEVICES is set (%r): it renumbers the GPUs every server sees; unset it where "
                         "InferPilot runs" % env["ROCR_VISIBLE_DEVICES"])
    tokens = [t.strip() for t in spec.split(",")]
    if not all(t.isdigit() for t in tokens):
        raise CannotTell("--gpus takes comma-separated HIP numbers (as HIP_VISIBLE_DEVICES does), not %r" % spec)
    want = [int(t) for t in tokens]
    if len(set(want)) != len(want):
        raise CannotTell("--gpus names a GPU twice: %r" % spec)
    try:
        listed = {g["hip"]: g for g in gpu_status.kfd_gpus(kfd)}
    except gpu_status.Unreadable as e:
        raise CannotTell(str(e))
    out = []
    for n in want:
        g = listed.get(n)
        if g is None:
            raise CannotTell("GPU %d is not among the GPUs KFD lists (%s)" % (n, ", ".join(map(str, sorted(listed))) or "none"))
        minor = g.get("render_minor")
        total = _read(os.path.join(drm, "renderD%s" % minor, "device", "mem_info_vram_total")).strip() if minor is not None else ""
        device = device_id(kfd, g["node"])
        if not device or not g["arch"] or not total.isdigit():
            raise CannotTell("GPU %d: its device id, its architecture or its memory cannot be read" % n)
        out.append({"device": device, "arch": g["arch"], "vram_bytes": int(total)})
    return sorted(out, key=lambda g: (g["device"], g["arch"], g["vram_bytes"]))


def fingerprint(a, env=os.environ) -> dict:
    model = ""
    if a.model:
        if not os.path.isdir(a.model):
            raise CannotTell("--model %s: not a local model directory" % a.model)
        try:
            model = baseline_cache.model_fingerprint(a.model)
        except OSError as e:
            raise CannotTell("--model %s: %s" % (a.model, e.strerror or e))
    harness = ""
    if a.accuracy_script:
        try:
            with open(a.accuracy_script, encoding="utf-8", errors="replace") as f:
                runs_it = os.path.basename(a.harness) in f.read()
            harness = baseline_cache.sha_of(a.harness) if runs_it else ""
        except OSError as e:
            raise CannotTell("%s: %s" % (e.filename or a.accuracy_script, e.strerror or e))
    return {"model_files": model, "gpus": gpus_of(a.gpus, a.kfd, a.drm, env), "harness": harness}


def saved(run_dir: str):
    """The record of run_dir; None when it has none that can be read."""
    try:
        with open(os.path.join(run_dir, RECORD), encoding="utf-8") as f:
            rec = json.load(f)
    except (OSError, ValueError):
        return None
    return rec if isinstance(rec, dict) else None


def differing(was: dict, now: dict) -> list:
    return [k for k in PARTS if was.get(k) != now.get(k)]


def record(run_dir: str, now: dict, replace: bool = False) -> dict:
    path = os.path.join(run_dir, RECORD)
    was = saved(run_dir)
    if os.path.lexists(path) and not replace:
        diff = list(PARTS) if was is None else differing(was, now)
        if not diff:
            return {"recorded": True, "unchanged": True, "path": path, **now}
        return {"recorded": False, "differs": diff, "path": path,
                "reason": "the run directory has a record that %s: a run that is resumed keeps it, and only a run that "
                          "measures everything again replaces it (--replace)" % ("cannot be read" if was is None else "says otherwise")}
    with open(path + ".part", "w", encoding="utf-8") as f:
        json.dump(now, f, indent=1)
        f.write("\n")
    os.replace(path + ".part", path)
    return {"recorded": True, "path": path, **now}


def check(run_dir: str, now: dict) -> dict:
    was = saved(run_dir)
    if was is None:
        return {"same": False, "reason": "%s has no %s that can be read: nothing shows what the run measured with" % (run_dir, RECORD)}
    diff = differing(was, now)
    if not diff:
        return {"same": True}
    return {"same": False, "differs": diff, "was": {k: was.get(k) for k in diff}, "now": {k: now[k] for k in diff}}


def main(argv=None, env=os.environ) -> int:
    ap = argparse.ArgumentParser(description="What a run measures with outside its run directory: recorded at Setup, "
                                             "checked when the run is resumed from Verify.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("record", "check"):
        p = sub.add_parser(name)
        p.add_argument("--run-dir", required=True)
        p.add_argument("--gpus", required=True, help="the GPUs the run is pinned to: comma-separated HIP numbers, e.g. 0,1,2,3")
        p.add_argument("--model", default="", help="the local model directory, when the task has one")
        p.add_argument("--accuracy-script", default="", help="the run's accuracy script, rendered or the task's own")
        p.add_argument("--harness", default=baseline_cache.HARNESS, help=argparse.SUPPRESS)  # another accuracy harness, for tests
        p.add_argument("--kfd", default=gpu_status.KFD, help=argparse.SUPPRESS)
        p.add_argument("--drm", default=gpu_status.DRM, help=argparse.SUPPRESS)
        if name == "record":
            p.add_argument("--replace", action="store_true", help="replace a record that says otherwise: the run measures everything again")
    a = ap.parse_args(argv)

    def stop(error: str) -> int:
        out = {"error": error}
        stale = os.path.join(a.run_dir, RECORD)
        # The record a retry was to replace is the attempt's before it, not what this one measures with
        if a.cmd == "record" and a.replace and os.path.lexists(stale):
            try:
                os.remove(stale)
                out["removed"] = stale
            except OSError:
                pass
        print(json.dumps(out, indent=1))
        return 2

    try:
        if not os.path.isdir(a.run_dir):
            raise CannotTell("--run-dir %s: not a directory" % a.run_dir)
        now = fingerprint(a, env)
    except CannotTell as e:
        return stop(str(e))
    try:
        out = record(a.run_dir, now, a.replace) if a.cmd == "record" else check(a.run_dir, now)
    except OSError as e:  # the record cannot be written
        return stop("%s: %s" % (e.filename or a.run_dir, e.strerror or e))
    print(json.dumps(out, indent=1))
    return 0 if out.get("recorded", out.get("same")) else 1


if __name__ == "__main__":
    sys.exit(main())
