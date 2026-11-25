#!/usr/bin/env python3
"""Propose the curator's knowledge/ changes as a pull request to InferPilot's own repository.

    python3 scripts/propose_knowledge.py --run-dir RUN_DIR --body RUN_DIR/knowledge_pr.md
    python3 scripts/propose_knowledge.py --run-dir RUN_DIR --body RUN_DIR/knowledge_pr.md --dry-run
    python3 scripts/propose_knowledge.py --run-dir RUN_DIR --body RUN_DIR/knowledge_pr.md --patch RUN_DIR/knowledge.patch

Only changes under knowledge/ go in. They are applied as a patch onto the current origin/main in a
throwaway worktree, committed on inferpilot/knowledge/<run directory name> and pushed there, never to main;
the pull request targets main and a human merges it. Once it is open, those files are restored in the
checkout, so later runs read knowledge/ as merged, not as proposed; a re-run updates the same branch
and pull request. It pushes with GITHUB_TOKEN, the token every GitHub script of InferPilot uses, which needs
Contents and Pull requests: read and write on this repository (README step 1). The repository is public: added lines or a body that name a
local path, this machine or an internal site stop it, as in scripts/daily_report.py. Without the token,
or on any failure, the changes stay where they are. Standard library and git only.

Once the lines it adds name nothing local, the patch is also written to RUN_DIR/knowledge.patch, with the
files it was made against ahead of it (git apply skips them): --patch proposes it later, from any checkout,
a shallow one too (the one that saved it, holding just those edits still, is restored as above). It
outlives the task's container only where RUN_DIR does, on a host mount (an exp_root outside the container).
A patch that makes a symbolic link is refused. Runs started from the same main give their new known issues
the same next KI- number: a number main has taken since is replaced, in the entry and in every line the
patch adds, by the next free one on main, and the pull request says which ("renumbered"). New entries
main added are kept beside this run's; an existing entry that main and the patch both changed, on the same
lines or not, stops it (exit 4) for a human to reconcile.
"""
from __future__ import annotations

import argparse
import base64
import codecs
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from daily_report import default_repo, leaks, this_host  # noqa: E402

INFERPILOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MARKER = "<!-- inferpilot:knowledge -->"
AUTHOR = ["-c", "user.name=InferPilot curator", "-c", "user.email=inferpilot-curator@users.noreply.github.com"]
KNOWN = "knowledge/known_issues.md"
KI_HEADING = re.compile(r"^## (KI-(\d+))\b", re.M)
KI_REF = re.compile(r"\bKI-\d+\b")
PREIMAGE = re.compile(r"^preimage ([0-9a-f]{40,64}) ([A-Za-z0-9+/=]+)$", re.M)
SYMLINK = re.compile(r"^(?:(?:new file|new|old|deleted file) mode|index [0-9a-f]+\.\.[0-9a-f]+) 120000$", re.M)


def api(method: str, path: str, token: str, data: dict | None = None):
    req = urllib.request.Request("https://api.github.com" + path, method=method,
                                 data=json.dumps(data).encode() if data else None,
                                 headers={"Accept": "application/vnd.github+json", "User-Agent": "inferpilot-curator",
                                          "Authorization": "Bearer " + token})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def git(repo: str, *args, check: bool = True, env: dict | None = None) -> str:
    p = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True,
                       env={**os.environ, **(env or {})})
    if check and p.returncode != 0:
        raise RuntimeError("git %s: %s" % (" ".join(args[:2]), (p.stderr or p.stdout).strip()[:300]))
    return p.stdout


def changed(inferpilot: str) -> tuple[list, list]:
    """(tracked, untracked) paths under knowledge/ that differ from HEAD."""
    tracked, untracked = [], []
    entries = iter(git(inferpilot, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", "knowledge/").split("\0"))
    for e in entries:
        if len(e) <= 3:
            continue
        (untracked if e[:2] == "??" else tracked).append(e[3:])
        if e[0] in "RC":
            tracked.append(next(entries, ""))  # a rename's source: gone from the tree
    return [t for t in tracked if t], untracked


def patch(inferpilot: str, tracked: list, untracked: list) -> str:
    out = git(inferpilot, "diff", "HEAD", "--binary", "--full-index", "--", *tracked) if tracked else ""
    for f in untracked:
        out += git(inferpilot, "diff", "--no-index", "--binary", "--full-index", "--", "/dev/null", f, check=False)
    return with_preimages(inferpilot, out)


def with_preimages(inferpilot: str, diff: str) -> str:
    """The patch with the files it was made against ahead of it, where git apply skips what it reads: a
    three-way apply needs them, and a later checkout, a shallow one above all, may not have them."""
    head = []
    for sha in dict.fromkeys(re.findall(r"^index ([0-9a-f]+)\.\.", diff, re.M)):
        p = subprocess.run(["git", "-C", inferpilot, "cat-file", "blob", sha], capture_output=True)
        if set(sha) != {"0"} and p.returncode == 0:
            head.append("preimage %s %s\n" % (sha, base64.b64encode(zlib.compress(p.stdout, 9)).decode()))
    return "".join(head) + diff


def store_preimages(repo: str, diff: str) -> None:
    """Into the repository's objects, the files the patch was made against that it lacks."""
    for sha, data in PREIMAGE.findall(diff):
        if subprocess.run(["git", "-C", repo, "cat-file", "-e", sha + "^{blob}"], capture_output=True).returncode == 0:
            continue
        try:
            content = zlib.decompress(base64.b64decode(data))
        except (zlib.error, ValueError):
            content = None
        p = subprocess.run(["git", "-C", repo, "hash-object", "-w", "--stdin"], input=content or b"", capture_output=True)
        if content is None or p.returncode != 0 or p.stdout.decode().strip() != sha:
            raise RuntimeError("the patch's copy of the file it was made against (%s) is damaged" % sha[:12])


def added(diff: str) -> str:
    return "\n".join(l[1:] for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++"))


def outside_git(args: list, diff: str, files: dict | None = None) -> tuple[subprocess.CompletedProcess, dict]:
    """git apply on the patch in a directory no repository holds, with `files` in it: git's answer, and
    those of the files that are still regular files after it."""
    with tempfile.TemporaryDirectory() as d:
        for name, text in (files or {}).items():
            os.makedirs(os.path.dirname(os.path.join(d, name)), exist_ok=True)
            with open(os.path.join(d, name), "w", encoding="utf-8") as f:
                f.write(text)
        p = subprocess.run(["git", "apply", *args, "-"], cwd=d, input=diff.encode("utf-8", "surrogateescape"),
                           capture_output=True, env={**os.environ, "GIT_CEILING_DIRECTORIES": os.path.dirname(d)})
        after = {}
        for name in files or {}:
            path = os.path.join(d, name)
            if os.path.isfile(path) and not os.path.islink(path):
                with open(path, encoding="utf-8") as f:
                    after[name] = f.read()
        return p, after


def unquote(name: str) -> str:
    """A path as a git header writes it: in C quotes when it has to be."""
    if len(name) > 1 and name[0] == name[-1] == '"':
        return codecs.escape_decode(name[1:-1].encode("utf-8", "surrogateescape"))[0].decode("utf-8", "surrogateescape")
    return name


def patched(diff: str) -> list:
    """The paths a patch touches, as git reads them (a quoted name, one with a space), a rename's source too."""
    p, _ = outside_git(["--numstat", "-z"], diff)
    names = [r.split("\t", 2)[2] for r in p.stdout.decode("utf-8", "surrogateescape").split("\0")
             if r.count("\t") >= 2] if p.returncode == 0 else []
    names += [unquote(n) for n in re.findall(r"^(?:rename|copy) from (.+)$", diff, re.M)]
    return sorted({n for n in names if n})


def entries(text: str) -> dict:
    """The known issues by number, each its lines without blank ones; the text before the first under ""."""
    out, key = {"": []}, ""
    for line in text.splitlines():
        m = KI_HEADING.match(line)
        if m:
            key = m.group(1)
            out[key] = []
        if line.strip():
            out[key].append(line.rstrip())
    return {k: "\n".join(v) for k, v in out.items()}


def changed_on_both(pre: str, main: str, post: str) -> list:
    """The entries of the file the patch was made against that main and the patch each changed their own way."""
    a, b, c = entries(pre), entries(main), entries(post)
    return [k or "the text before the first entry" for k in a if a[k] != b.get(k) and a[k] != c.get(k) and b.get(k) != c.get(k)]


def applied(diff: str, pre: str) -> str | None:
    """known_issues.md as the patch leaves the file it was made against; None when it does not apply there."""
    p, after = outside_git(["--include=" + KNOWN], diff, {KNOWN: pre})
    return after.get(KNOWN) if p.returncode == 0 else None


def renumber(diff: str, ours: str, main: str) -> tuple[str, dict]:
    """New known issues of the patch (headings it adds to known_issues.md that neither `ours`, the file it was
    made against, nor its own context and removed lines have) whose number `main` has taken: each gets the
    next free number on main, in every line the patch adds."""
    had, taken = {m[0] for m in KI_HEADING.findall(ours)}, {m[0] for m in KI_HEADING.findall(main)}
    adds, in_known = [], False
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            in_known = line.endswith(" b/" + KNOWN)
        elif in_known and line and line[0] in "+- " and not line.startswith(("+++", "---")):
            m = KI_HEADING.match(line[1:])
            if m:
                (adds.append if line[0] == "+" else had.add)(m.group(1))
    new = [ki for ki in dict.fromkeys(adds) if ki not in had]
    used = taken | had | set(new)
    nxt = max([int(k[3:]) for k in used] + [0]) + 1
    mapping = {}
    for ki in new:
        if ki in taken:
            mapping[ki] = "KI-%03d" % nxt
            nxt += 1
    if not mapping:
        return diff, {}
    out = [KI_REF.sub(lambda m: mapping.get(m.group(0), m.group(0)), l) if l.startswith("+") and not l.startswith("+++") else l
           for l in diff.splitlines(keepends=True)]
    return "".join(out), mapping


def preimage(inferpilot: str, diff: str, path: str) -> str:
    """The file the patch was made against: its blob from the patch's index line (--binary writes it whole).
    Empty when this checkout lacks the blob: its HEAD may already hold main's entries under the same numbers."""
    m = re.search(r"^diff --git a/%s b/%s\n(?:(?!diff --git ).*\n)*?index ([0-9a-f]+)\.\." % (re.escape(path), re.escape(path)),
                  diff, re.M)
    return git(inferpilot, "cat-file", "-p", m.group(1), check=False) if m and set(m.group(1)) != {"0"} else ""


def new_entries(extra: list, rest: str) -> bool:
    """Lines a side added after the base's that are whole new known issues: from a heading on, and up to
    where an entry ends (`rest`, the next line with text after the conflict, is a heading or nothing)."""
    filled = [l for l in extra if l.strip()]
    return not filled or bool(KI_HEADING.match(filled[0])) and (not rest or rest.startswith("## "))


def resolve_appends(text: str) -> str | None:
    """The known issues after a three-way apply with diff3 conflicts. A conflict where one side kept the base's
    lines and added only whole new entries after them, where an entry ends (or both added at the same such
    place), keeps both, main's first where it can; None otherwise: lines added inside an entry, or the base's
    lines changed on both sides."""
    lines = text.splitlines(keepends=True)
    out, block, part = [], None, ""
    for i, line in enumerate(lines):
        if block is None:
            if line.startswith("<<<<<<< "):
                block, part = {"ours": [], "base": [], "theirs": []}, "ours"
            else:
                out.append(line)
        elif part == "ours" and line.startswith("||||||| "):
            part = "base"
        elif part == "base" and line.rstrip("\n") == "=======":
            part = "theirs"
        elif part == "theirs" and line.startswith(">>>>>>> "):
            ours, base, theirs = block["ours"], block["base"], block["theirs"]
            rest = next((l for l in lines[i + 1:] if l.strip()), "")
            if theirs[:len(base)] == base and new_entries(theirs[len(base):], rest):
                out += ours + theirs[len(base):]
            elif ours[:len(base)] == base and new_entries(ours[len(base):], rest):
                out += theirs + ours[len(base):]
            else:
                return None
            block = None
        elif part == "ours" and line.rstrip("\n") == "=======":
            return None  # no base section: not diff3, so nothing tells an addition from an edit
        else:
            block[part].append(line)
    return None if block is not None else "".join(out)


def with_renumbering(body: str, mapping: dict) -> str:
    if not mapping:
        return body
    pairs = ", ".join("%s is %s here" % (k, v) for k, v in mapping.items())
    text = KI_REF.sub(lambda m: mapping.get(m.group(0), m.group(0)), body)
    return text.rstrip() + "\n\nNumbered against main when proposed: %s; the run's own files keep %s.\n" % (
        pairs, ", ".join(mapping))


def auth_env(token: str) -> dict:
    """The token as an HTTP header in git's environment, never on a command line."""
    basic = base64.b64encode(("x-access-token:%s" % token).encode()).decode()
    return {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
            "GIT_CONFIG_VALUE_0": "AUTHORIZATION: basic " + basic}


def commit_branch(inferpilot: str, branch: str, diff: str, title: str, run: str, push_url: str,
                  token: str) -> tuple[str, dict]:
    """Apply the patch onto origin/main in a throwaway worktree, commit, push the branch; the commit, and the
    known issues renumbered against main."""
    git(inferpilot, "fetch", "-q", push_url, "+refs/heads/main:refs/remotes/origin/main", env=auth_env(token))
    store_preimages(inferpilot, diff)
    pre, main_known = preimage(inferpilot, diff, KNOWN), git(inferpilot, "show", "origin/main:" + KNOWN, check=False)
    diff, mapping = renumber(diff, pre, main_known)
    files = patched(diff)
    # Git merges changes to different lines of one entry without a word: the entries say what each side changed.
    post = applied(diff, pre) if pre and KNOWN in files else None
    both = changed_on_both(pre, main_known, post) if post is not None else []
    if both:
        raise RuntimeError("git apply: %s: %s changed on main as well; reconcile them by hand" % (KNOWN, ", ".join(both)))
    wt = tempfile.mkdtemp(prefix="inferpilot-knowledge-")
    os.rmdir(wt)
    git(inferpilot, "worktree", "add", "-q", "--detach", wt, "origin/main")
    tmp = tempfile.mkdtemp(prefix="inferpilot-knowledge-patch-")
    try:
        git(wt, "checkout", "-q", "-B", branch)
        patch_file = os.path.join(tmp, "knowledge.patch")
        with open(patch_file, "w") as f:
            f.write(diff)
        known = os.path.join(wt, KNOWN)
        p = subprocess.run(["git", "-C", wt, "-c", "merge.conflictStyle=diff3", "apply", "--3way", "--index", patch_file],
                           capture_output=True, text=True)
        # Before anything below opens a file: through a symbolic link it would write outside the worktree.
        staged = git(wt, "ls-files", "-s", "-z", "--", *files).split("\0") if files else []
        links = {e.split("\t", 1)[1] for e in staged if e.startswith("120000 ")}
        links |= {f for f in files if os.path.islink(os.path.join(wt, f))}
        if links:
            raise RuntimeError("the patch makes a symbolic link: %s" % ", ".join(sorted(links)))
        if p.returncode != 0:
            # The known issues are a list: what main appended since this checkout and what this run appends
            # are both kept; an entry both changed stops here for a human.
            unmerged = [f for f in git(wt, "diff", "--name-only", "--diff-filter=U").splitlines() if f]
            resolved = None
            if unmerged == [KNOWN]:
                with open(known, encoding="utf-8") as f:
                    resolved = resolve_appends(f.read())
            if resolved is None:
                raise RuntimeError("git apply: %s" % ("%s: lines main changed as well; reconcile them by hand" % ", ".join(unmerged)
                                                      if unmerged else (p.stderr or p.stdout).strip()[:300]))
            with open(known, "w", encoding="utf-8") as f:
                f.write(resolved)
            git(wt, "add", KNOWN)
        if KNOWN in files and os.path.isfile(known) and not os.path.islink(known):
            with open(known, encoding="utf-8") as f:
                text = f.read()
            tidy = re.sub(r"([^\n])\n(## KI-)", r"\1\n\n\2", text)
            if tidy != text:
                with open(known, "w", encoding="utf-8") as f:
                    f.write(tidy)
                git(wt, "add", KNOWN)
        # What was staged, not what the patch's headers seemed to name (a quoted path, one with a space).
        staged = [f for f in git(wt, "diff", "--cached", "--name-only", "--no-renames", "-z").split("\0") if f]
        outside = [f for f in staged if not f.startswith("knowledge/")]
        if outside:
            raise RuntimeError("the patch changes more than knowledge/: %s" % ", ".join(outside))
        git(wt, *AUTHOR, "commit", "-q", "-m", title, "-m", "From the InferPilot run %s." % run)
        sha = git(wt, "rev-parse", "HEAD").strip()
        git(wt, "push", "-q", "--force", push_url, "HEAD:refs/heads/%s" % branch, env=auth_env(token))
        return sha, mapping
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        git(inferpilot, "worktree", "remove", "--force", wt, check=False)
        git(inferpilot, "branch", "-D", branch, check=False)


def open_pr(repo: str, branch: str, title: str, body: str, token: str) -> dict:
    owner = repo.split("/")[0]
    text = body.rstrip() + "\n\n" + MARKER + "\n"
    found = api("GET", "/repos/%s/pulls?state=open&head=%s:%s" % (repo, owner, branch), token)
    if found:
        pr = api("PATCH", "/repos/%s/pulls/%d" % (repo, found[0]["number"]), token, {"body": text})
        return {"action": "updated", "url": pr.get("html_url", ""), "number": pr["number"]}
    pr = api("POST", "/repos/%s/pulls" % repo, token, {"title": title, "head": branch, "base": "main", "body": text})
    return {"action": "created", "url": pr.get("html_url", ""), "number": pr["number"]}


def restore(inferpilot: str, tracked: list, untracked: list) -> None:
    """Put knowledge/ back as HEAD has it: the changes now live on the pull request's branch."""
    if tracked:
        git(inferpilot, "checkout", "HEAD", "--", *tracked)
    for f in untracked:
        path = os.path.join(inferpilot, f)
        if os.path.isfile(path) or os.path.islink(path):
            os.remove(path)
        d = os.path.dirname(path)
        while d.startswith(os.path.join(inferpilot, "knowledge") + os.sep) and os.path.isdir(d) and not os.listdir(d):
            os.rmdir(d)
            d = os.path.dirname(d)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Propose the curator's knowledge/ changes as a pull request.")
    ap.add_argument("--run-dir", required=True, help="the run whose curator made the changes")
    ap.add_argument("--body", required=True, help="the pull request's description, e.g. RUN_DIR/knowledge_pr.md")
    ap.add_argument("--title", default="", help="default: knowledge: what <run> learned")
    ap.add_argument("--repo", default="", help="owner/name (default: the origin of this checkout)")
    ap.add_argument("--inferpilot-dir", default=INFERPILOT, help=argparse.SUPPRESS)
    ap.add_argument("--push-url", default="", help=argparse.SUPPRESS)
    ap.add_argument("--dry-run", action="store_true", help="show what would be proposed")
    ap.add_argument("--patch", default="", help="propose this patch of knowledge/ (a run's knowledge.patch), not the checkout's changes")
    a = ap.parse_args(argv)
    run = os.path.basename(os.path.normpath(a.run_dir))
    branch, title = "inferpilot/knowledge/%s" % run, a.title or "knowledge: what %s learned" % run
    saved = a.patch
    try:
        if a.patch:
            diff = open(a.patch, encoding="utf-8").read()
            # The checkout that saved it, holding just these edits still, reads main again once they are
            # proposed; any other checkout, or one with more edits, is left as it is.
            mine = changed(a.inferpilot_dir)
            tracked, untracked = mine if (mine[0] or mine[1]) and patch(a.inferpilot_dir, *mine) == diff else ([], [])
            files = patched(diff)
            outside = [f for f in files if not f.startswith("knowledge/")]
            if not files or outside:
                print("propose_knowledge: %s is not a patch of knowledge/%s" % (
                    a.patch, ": it touches " + ", ".join(outside) if outside else ""), file=sys.stderr)
                return 1
        else:
            tracked, untracked = changed(a.inferpilot_dir)
            if not tracked and not untracked:
                print(json.dumps({"action": "none", "reason": "no changes under knowledge/"}))
                return 0
            diff = patch(a.inferpilot_dir, tracked, untracked)
            files = tracked + untracked
        if SYMLINK.search(diff):
            print("propose_knowledge: %s makes a symbolic link, which nothing under knowledge/ is" % (a.patch or "the change"),
                  file=sys.stderr)
            return 1
        # Saved only when it names nothing of this machine: --patch, elsewhere, would not know this one.
        bad = leaks(added(diff), this_host())
        if not a.patch and not bad and os.path.isdir(a.run_dir):
            saved = os.path.join(a.run_dir, "knowledge.patch")
            with open(saved, "w", encoding="utf-8") as f:
                f.write(diff)
        body = open(a.body, encoding="utf-8").read()
        bad += leaks(body, this_host())
        if bad:
            print("propose_knowledge: not proposed; it names something local or internal:\n  " + "\n  ".join(bad), file=sys.stderr)
            return 3
        if a.dry_run:
            print(json.dumps({"dry_run": True, "branch": branch, "title": title, "files": files, "patch": saved}, indent=1))
            return 0
        token = os.environ.get("GITHUB_TOKEN", "")
        repo = a.repo or default_repo()
        if not token or not repo:
            where = saved if a.patch else "%s/knowledge%s" % (a.inferpilot_dir, " and in %s" % saved if saved else "")
            later = " --patch %s" % saved if saved else ""
            print("propose_knowledge: %s; the changes stay in %s. Propose them later%s with:\n  python3 %s "
                  "--run-dir %s --body %s%s" % ("GITHUB_TOKEN is not set" if not token else "no GitHub repository", where,
                                                ", from any checkout," if saved else "", sys.argv[0], a.run_dir, a.body, later),
                  file=sys.stderr)
            return 2
        sha, mapping = commit_branch(a.inferpilot_dir, branch, diff, title, run, a.push_url or "https://github.com/%s.git" % repo, token)
        pr = open_pr(repo, branch, title, with_renumbering(body, mapping), token)
        restore(a.inferpilot_dir, tracked, untracked)
        print(json.dumps({**pr, "branch": branch, "commit": sha, "files": files, **({"renumbered": mapping} if mapping else {})},
                         indent=1))
        return 0
    except urllib.error.HTTPError as e:
        print("propose_knowledge: HTTP %d from GitHub%s; the changes stay where they are" % (
            e.code, " (GITHUB_TOKEN cannot write to the repository; README step 1)" if e.code in (401, 403) else ""),
            file=sys.stderr)
        return 2 if e.code in (401, 403) else 1
    except RuntimeError as e:
        denied = any(k in str(e) for k in ("403", "401", "Authentication failed", "Permission to"))
        print("propose_knowledge: %s; the changes stay where they are%s" % (
            e, " (GITHUB_TOKEN cannot push; README step 1)" if denied else ""), file=sys.stderr)
        return 4 if "apply" in str(e) else 2 if denied else 1
    except (urllib.error.URLError, OSError, ValueError) as e:
        print("propose_knowledge: %s; the changes stay where they are" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
