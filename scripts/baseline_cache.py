#!/usr/bin/env python3
"""Baseline measurements kept for reuse: a run whose baseline was measured under the same fingerprint takes
those numbers, accuracy and performance alike, instead of measuring them again.

    python3 scripts/baseline_cache.py lookup --cache DIR --commit SHA --model DIR --server-script F \\
        [--client-script F] [--workload TEXT] [--sets 'gsm8k ...'] --gpu 'ARCH PRODUCT' [--image IMAGE] \\
        [--extra TEXT] [--max-age-days 7] [--restore-to RUN_DIR]
    python3 scripts/baseline_cache.py store --cache DIR <the same fingerprint> --run-dir RUN_DIR

The fingerprint is everything the numbers depend on: the framework commit (a full SHA, never a ref that
moves), the model directory (its small files by content, its weights by name and size), the contents of the
server and client scripts, the workload, the accuracy sets with the contents of scripts/eval_accuracy.sh,
the settings it reads from the environment that change a score (ENABLE_THINKING, REASONING_EFFORT,
MAX_TOKENS, TOP_P, TOP_K, DATASET_HUB, JUDGE_MODEL, JUDGE_URL, LONGBENCH_LOCAL_PATH) and the version of the
evalscope installed ($EVALSCOPE, else evalscope on PATH), the GPU (its architecture and product name), the
running image ($INFERPILOT_IMAGE unless --image is given), --extra for anything else (the GPU count), and the
rule it was measured under (every GPU of the machine idle: an entry stored under an older one is measured
again). Without a known image, or without a known evalscope version for a baseline with accuracy sets,
nothing is looked up or stored.

A stored baseline holds RUN_DIR/baseline.json (the scores, the client's runs and their spread, and how long
each launch, set and client run took), RUN_DIR/accuracy/ when it measured sets and RUN_DIR/bench/baseline/
when it has a client, at those paths. store refuses one that is incomplete: a set without a score in
accuracy/summary.txt (the harness writes none for a set it skipped or that failed), a baseline.json without
its times, a score or two client runs, or a run with LIMIT set (a smoke test). lookup prints the newest entry
no older than --max-age-days, {"hit": true, "dir", "run_dir", "stored_at", "files"}, and exits 0 (with
--restore-to, its files are copied there first), or {"hit": false} and exits 1. store copies into
<cache>/<fingerprint hash>/<time>-<random>/files/, writes meta.json there last, removes entries older than
--max-age-days, gives the cache back to the owner of its directory (scripts/restore_owner.sh), and prints
the entry; a refusal exits 1. Standard library only.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.path.join(HERE, "eval_accuracy.sh")
RESTORE_OWNER = os.path.join(HERE, "restore_owner.sh")
# What scripts/eval_accuracy.sh reads from the environment that changes a score; its defaults are in the
# harness, whose contents are in the fingerprint.
EVAL_ENV = ("ENABLE_THINKING", "REASONING_EFFORT", "MAX_TOKENS", "TOP_P", "TOP_K", "DATASET_HUB",
            "JUDGE_MODEL", "JUDGE_URL", "LONGBENCH_LOCAL_PATH")
# The rule a baseline is measured under; a new one makes every entry measured under an older one miss.
POLICY = "every GPU of the machine idle (scripts/gpu_status.py)"
SMALL = 16 << 20  # a model file up to this size is fingerprinted by its contents, a larger one by its size


def sha_of(path: str) -> str:
    if not path:
        return ""
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def model_fingerprint(path: str) -> str:
    """A checkpoint replaced at the same path is another model; hashing its weights would take minutes."""
    h = hashlib.sha256()
    for name in sorted(os.listdir(path)):
        p = os.path.join(path, name)
        if not os.path.isfile(p):
            continue
        size = os.path.getsize(p)
        h.update(f"{name}\0{size}\0".encode())
        if size <= SMALL:
            with open(p, "rb") as f:
                h.update(hashlib.sha256(f.read()).digest())
    return h.hexdigest()


def evalscope_version(exe: str) -> str:
    """The version of the evalscope installed in the venv its executable runs from; '' when unknown."""
    if not exe:
        return ""
    exe = shutil.which(exe) or exe
    if not os.path.exists(exe):
        return ""
    venv = os.path.dirname(os.path.dirname(os.path.realpath(exe)))
    found = sorted(glob.glob(os.path.join(venv, "lib", "python*", "site-packages", "evalscope-*.dist-info")))
    return os.path.basename(found[-1])[len("evalscope-"):-len(".dist-info")] if found else ""


def key_of(a, env=os.environ) -> dict:
    sets = " ".join(sorted(a.sets.split()))
    return {"commit": a.commit, "model": a.model, "model_files": model_fingerprint(a.model),
            "server_script": sha_of(a.server_script), "client_script": sha_of(a.client_script),
            "workload": a.workload, "sets": sets, "harness": sha_of(a.harness) if sets else "",
            "eval_env": {k: env.get(k, "") for k in EVAL_ENV} if sets else {},
            "evalscope": evalscope_version(a.evalscope) if sets else "",
            "gpu": a.gpu, "image": a.image, "extra": a.extra, "policy": POLICY}


def digest(key: dict) -> str:
    return hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()[:16]


def unusable(key: dict) -> str:
    """Why this fingerprint cannot vouch for a baseline; '' when it can."""
    if not key["image"]:
        return "the running image is unknown"
    if key["sets"] and not key["evalscope"]:
        return "the evalscope version is unknown"
    return ""


def parts(key: dict) -> list:
    """What a stored baseline holds, relative to its run directory."""
    return ["baseline.json"] + (["accuracy"] if key["sets"] else []) \
        + ([os.path.join("bench", "baseline")] if key["client_script"] else [])


def incomplete(key: dict, run_dir: str, env=os.environ) -> str:
    """Why the baseline in run_dir is not one to keep; '' when it is."""
    if key["sets"] and env.get("LIMIT"):
        return "LIMIT is set: a smoke test, not a baseline"
    for rel in parts(key):
        if not os.path.exists(os.path.join(run_dir, rel)):
            return f"{rel} is missing"
    try:
        with open(os.path.join(run_dir, "baseline.json"), encoding="utf-8") as f:
            summary = json.load(f)
    except ValueError:
        return "baseline.json is not JSON"
    if not isinstance(summary, dict) or not summary.get("times"):
        return "baseline.json has no times"
    scored = set()
    if key["sets"] and os.path.exists(os.path.join(run_dir, "accuracy", "summary.txt")):
        with open(os.path.join(run_dir, "accuracy", "summary.txt"), encoding="utf-8", errors="replace") as f:
            scored = {m.group(1) for m in re.finditer(r"^baseline (\S+): ", f.read(), re.M)}
    missing = [s for s in key["sets"].split() if s not in scored or s not in (summary.get("accuracy") or {})]
    if missing:
        return "no score for " + ", ".join(missing)
    if key["client_script"] and len((summary.get("perf") or {}).get("runs") or []) < 2:
        return "baseline.json has fewer than two client runs"
    return ""


def lookup(cache: str, key: dict, max_age_days: float, restore_to: str = "", now: float | None = None) -> dict:
    why = unusable(key)
    if why:
        return {"hit": False, "reason": why}
    now = time.time() if now is None else now
    root = os.path.join(cache, digest(key))
    best = None
    for stamp in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        try:
            with open(os.path.join(root, stamp, "meta.json"), encoding="utf-8") as f:
                meta = json.load(f)
        except (OSError, ValueError):
            continue
        files = os.path.join(root, stamp, "files")
        names = meta.get("files", [])
        if meta.get("key") != key or now - meta.get("stored", 0) > max_age_days * 86400 or "baseline.json" not in names:
            continue
        if not all(os.path.exists(os.path.join(files, n)) for n in names):
            continue
        if best is None or meta["stored"] > best[1]["stored"]:
            best = (files, meta)
    if not best:
        return {"hit": False}
    files, meta = best
    if restore_to:
        shutil.copytree(files, restore_to, dirs_exist_ok=True)
    return {"hit": True, "dir": files, "run_dir": meta.get("run_dir", ""), "stored_at": meta.get("stored_at", ""),
            "files": meta.get("files", []), **({"restored_to": os.path.abspath(restore_to)} if restore_to else {})}


def prune(cache: str, max_age_days: float, now: float) -> None:
    """Entries past their age, and stores that died before their meta.json, are never used again."""
    for root in glob.glob(os.path.join(cache, "*")):
        for entry in glob.glob(os.path.join(root, "*")):
            try:
                with open(os.path.join(entry, "meta.json"), encoding="utf-8") as f:
                    stored = json.load(f).get("stored", 0)
            except (OSError, ValueError):
                stored = os.path.getmtime(entry)
            if now - stored > max_age_days * 86400:
                shutil.rmtree(entry, ignore_errors=True)
        if os.path.isdir(root) and not os.listdir(root):
            os.rmdir(root)


def store(cache: str, key: dict, run_dir: str, max_age_days: float = 7, now: float | None = None,
          env=os.environ) -> dict:
    """A directory of its own per store, its meta.json written last: a lookup sees whole entries only,
    and two runs storing the same key at once never share one."""
    why = unusable(key) or incomplete(key, run_dir, env)
    if why:
        return {"stored": False, "reason": why}
    now = time.time() if now is None else now
    stamp = time.strftime("%Y%m%d-%H%M%SZ", time.gmtime(now))
    root = os.path.join(cache, digest(key))
    os.makedirs(root, exist_ok=True)
    entry = tempfile.mkdtemp(prefix=stamp + "-", dir=root)
    files = os.path.join(entry, "files")
    try:
        os.makedirs(files)
        for rel in parts(key):
            src, dst = os.path.join(run_dir, rel), os.path.join(files, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if os.path.isdir(src):
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
        meta = {"key": key, "run_dir": os.path.abspath(run_dir), "stored": now,
                "stored_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(now)), "files": parts(key)}
        with open(os.path.join(entry, "meta.json.part"), "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=1)
        os.replace(os.path.join(entry, "meta.json.part"), os.path.join(entry, "meta.json"))
    except BaseException:
        shutil.rmtree(entry, ignore_errors=True)
        raise
    prune(cache, max_age_days, now)
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        subprocess.run(["sh", RESTORE_OWNER, cache], check=False)
    return {"stored": True, "dir": files, "files": parts(key)}


def main(argv=None, env=os.environ) -> int:
    ap = argparse.ArgumentParser(description="Reuse a baseline measured under the same fingerprint.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("lookup", "store"):
        p = sub.add_parser(name)
        p.add_argument("--cache", required=True)
        p.add_argument("--commit", required=True, help="the full SHA of the base commit")
        p.add_argument("--model", required=True, help="the local model directory")
        p.add_argument("--server-script", required=True)
        p.add_argument("--client-script", default="")
        p.add_argument("--workload", default="")
        p.add_argument("--sets", default="", help="the accuracy sets measured, e.g. 'gsm8k gpqa_diamond simple_qa'")
        p.add_argument("--gpu", required=True, help="the GPU architecture and product name, e.g. 'gfx950 MI355X'")
        p.add_argument("--image", default=env.get("INFERPILOT_IMAGE", ""), help="the running image (default: $INFERPILOT_IMAGE)")
        p.add_argument("--extra", default="", help="anything else the numbers depend on, e.g. 'gpus=8'")
        p.add_argument("--max-age-days", type=float, default=7)
        p.add_argument("--harness", default=HARNESS, help=argparse.SUPPRESS)  # another accuracy harness, for tests
        p.add_argument("--evalscope", default=env.get("EVALSCOPE", "evalscope"), help=argparse.SUPPRESS)
        if name == "lookup":
            p.add_argument("--restore-to", default="", help="copy a hit's files into this run directory")
        else:
            p.add_argument("--run-dir", required=True)
    a = ap.parse_args(argv)
    if not re.fullmatch(r"[0-9a-f]{40}", a.commit):
        ap.error("--commit takes the full SHA of the base commit, not a ref such as origin/main")
    if not os.path.isdir(a.model):
        ap.error(f"--model {a.model}: not a local model directory")
    key = key_of(a, env)
    if a.cmd == "lookup":
        out = lookup(a.cache, key, a.max_age_days, a.restore_to)
    else:
        out = store(a.cache, key, a.run_dir, a.max_age_days, env=env)
    print(json.dumps(out, indent=1))
    return 0 if out.get("hit", out.get("stored")) else 1


if __name__ == "__main__":
    sys.exit(main())
