"""What the code reads and what the docs say stay in step: every environment variable that setup, the
container script and the runner read is in the READMEs, every other script names the ones it reads in
its own header, every variable the READMEs' tables list is read by something, the command-line flags
the READMEs and AGENTS.md give exist, the branch bundle the roles write is the one a retry or a
resume takes the task's commits from, with commands that do it, and the detached launcher of
knowledge/env_hygiene.md leaves the run's PID and exit status. Offline.

    python3 -m unittest discover -s tests
"""
import ast
import os
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from test_readme import EN, ROOT, ZH, table_names

# Set by the system or the shell, not by a user of InferPilot.
STANDARD = {"HOME", "PATH", "PWD", "SHELL", "USER", "SUDO_USER", "LOGNAME", "TMPDIR", "LANG", "TERM", "TZ"}
BASH = {"SECONDS", "RANDOM", "LINENO", "BASH_SOURCE", "BASH_REMATCH", "PIPESTATUS", "FUNCNAME", "OPTARG",
        "OPTIND", "IFS", "PPID", "UID", "EUID", "HOSTNAME", "BASHPID", "REPLY"}
# Their variables are the READMEs' to document; any other script documents its own.
USER_FACING = ["inferpilot/bootstrap.py", "setup.py", "scripts/run_docker.sh", "runtime/*.mjs"]
ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
HEREDOC = re.compile(r"<<-?\s*(\\?)(['\"]?)([A-Za-z_]\w*)\2")
FUNCTION = re.compile(r"^[ \t]*(?:function[ \t]+)?([A-Za-z_][\w-]*)[ \t]*\(\)[ \t]*\{", re.M)


def shell_code(text):
    """What bash expands: comments, single-quoted and $'...' strings and quoted heredocs left out. A $(...)
    inside double quotes is code again, with quotes of its own."""
    out, i, n, stack = [], 0, len(text), []  # '"': inside double quotes; a number: inside such a $( ), its depth
    while i < n:
        c = text[i]
        dq = bool(stack) and stack[-1] == '"'
        if not dq:
            line = text[text.rfind("\n", 0, i) + 1:i]
            heredoc = HEREDOC.match(text, i) if c == "<" and text[i - 1:i] != "<" and not text.startswith("<<<", i) \
                and line.count("((") <= line.count("))") else None
            if heredoc:
                body = text.find("\n", i)
                end = re.compile(r"^\s*%s$" % heredoc.group(3), re.M).search(text, body + 1) if body >= 0 else None
                stop = end.end() if end else n
                if not (heredoc.group(1) or heredoc.group(2)):
                    out.append(text[body:stop] if body >= 0 else "")
                i = stop
                continue
            if c == "'" or text.startswith("$'", i):
                j = i + (2 if c == "$" else 1)
                while j < n and text[j] != "'":
                    j += 2 if c == "$" and text[j] == "\\" else 1
                i = j + 1
                continue
            if c == "#" and (i == 0 or text[i - 1] in " \t\n;("):
                j = text.find("\n", i)
                i = n if j < 0 else j
                continue
        if c == "\\" and i + 1 < n:
            out.append(text[i:i + 2])
            i += 2
            continue
        if c == '"':
            stack.pop() if dq else stack.append('"')
        elif dq and text.startswith("$(", i) and not text.startswith("$((", i):
            stack.append(0)
            out.append("$(")
            i += 2
            continue
        elif stack and not dq and c in "()":
            if c == "(":
                stack[-1] += 1
            elif stack[-1] == 0:
                stack.pop()
            else:
                stack[-1] -= 1
        out.append(c)
        i += 1
    return "".join(out)


def runs_at(code):
    """Where each position of a script runs: in a function's body, where the function is first called (from
    the top level, or from a function called there); elsewhere, where it stands."""
    bodies = {}
    for m in FUNCTION.finditer(code):
        depth, j = 1, m.end()
        while j < len(code) and depth:
            depth += {"{": 1, "}": -1}.get(code[j], 0)
            j += 1
        bodies[m.group(1)] = (m.start(), m.end(), j)

    def inside(pos):
        names = [f for f, (_, a, b) in bodies.items() if a <= pos < b]
        return max(names, key=lambda f: bodies[f][1]) if names else None

    called = {}

    def first_call(name, seen=()):
        if name not in called:
            if name in seen:
                return len(code)
            start, _, _ = bodies[name]
            sites = [m.start() for m in re.finditer(r"(?<![\w-])%s(?![\w-])" % re.escape(name), code) if m.start() != code.find(name, start)]
            called[name] = min([s if inside(s) is None else first_call(inside(s), seen + (name,)) for s in sites] or [len(code)])
        return called[name]

    return lambda pos: pos if inside(pos) is None else first_call(inside(pos))


def shell_inputs(text):
    """Variables a script takes from its environment: read before it first sets them (when it runs), or never
    set, or set from themselves with a default (X=${X:-...})."""
    code = shell_code(text)
    when = runs_at(code)
    reads, sets = {}, {}
    for m in re.finditer(r"\$\{?[#!]?([A-Z][A-Z0-9_]*)|\bprintenv\s+([A-Z][A-Z0-9_]*)", code):
        name = m.group(1) or m.group(2)
        reads[name] = min(reads.get(name, len(code)), when(m.start()))
    for m in re.finditer(r"\(\((.*?)\)\)", code, re.S):  # (( X > 0 )) reads X without a $
        for name in re.findall(r"(?<![\w$])([A-Z][A-Z0-9_]*)(?!\w)", m.group(1)):
            reads[name] = min(reads.get(name, len(code)), when(m.start()))
    for m in re.finditer(r"(?<![\w$.{/-])([A-Z][A-Z0-9_]*)(?:\[[^\]]*\])?\+?=|"
                         r"\b(?:local|declare|typeset|readonly|for|mapfile|printf -v)\s+(?:-\w+\s+)*([A-Z][A-Z0-9_]*)\b|"
                         r"\bread\s+(?:-\w+\s+(?:\S+\s+)?)*([A-Z][A-Z0-9_ ]*)", code):
        for name in (m.group(3) or "").split() or [m.group(1) or m.group(2)]:
            sets[name] = min(sets.get(name, len(code)), when(m.start()))
    defaulted = set(re.findall(r"\b([A-Z][A-Z0-9_]*)=\"?\$\{\1:?[-=]", code))
    return {v for v, at in reads.items() if v not in sets or at < sets[v] or v in defaulted} - STANDARD - BASH


def python_inputs(text):
    """Keys the environment is read with: env.get/setdefault/pop(...), environ[...], getenv(...) and
    "X" in environ, under any name environ or getenv is imported or bound as. A key is a string, a module
    constant holding one, or the loop variable over a module's tuple of them (for v in IDENTITY_VARS);
    setting a variable for a child (environ["X"] = ...) is not reading it."""
    tree = ast.parse(text)
    strings, consts = {}, {}
    envs, getenvs = {"env", "environ"}, {"getenv"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "os":
            envs |= {a.asname for a in node.names if a.name == "environ" and a.asname}
            getenvs |= {a.asname for a in node.names if a.name == "getenv" and a.asname}
        elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Attribute) and node.value.attr == "environ":
            envs |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in [t for t in node.targets if isinstance(t, ast.Name)]:
                if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    strings[target.id] = node.value.value
                elif isinstance(node.value, (ast.Tuple, ast.List, ast.Set)):
                    consts[target.id] = [e.value for e in node.value.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    looped = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.For, ast.comprehension)) and isinstance(node.iter, ast.Name) \
                and isinstance(node.target, ast.Name):
            looped.setdefault(node.target.id, []).extend(consts.get(node.iter.id, []))

    def is_env(n):
        return isinstance(n, ast.Name) and n.id in envs or isinstance(n, ast.Attribute) and n.attr == "environ"

    out = set()
    for node in ast.walk(tree):
        key = None
        if isinstance(node, ast.Call) and node.args:
            f = node.func
            if isinstance(f, ast.Attribute) and f.attr in ("get", "setdefault", "pop") and is_env(f.value) \
                    or isinstance(f, ast.Attribute) and f.attr == "getenv" or isinstance(f, ast.Name) and f.id in getenvs:
                key = node.args[0]
        elif isinstance(node, ast.Subscript) and is_env(node.value) and isinstance(node.ctx, ast.Load):
            key = node.slice
        elif isinstance(node, ast.Compare) and any(isinstance(o, (ast.In, ast.NotIn)) for o in node.ops) \
                and any(is_env(c) for c in node.comparators):
            key = node.left
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            out.add(key.value)
        elif isinstance(key, ast.Name):
            out.update(looped.get(key.id, []) + ([strings[key.id]] if key.id in strings else []))
    return {n for n in out if ENV_NAME.match(n)} - STANDARD


def js_inputs(text):
    names = {a or b or c for a, b, c in re.findall(
        r"process\.env\.([A-Z][A-Z0-9_]*)|process\.env\[['\"]([A-Z][A-Z0-9_]*)|binEnv: '([A-Z][A-Z0-9_]*)'", text)}
    for group in re.findall(r"\{([^{}]*)\}\s*=\s*process\.env\b", text):  # const { A, B: b } = process.env
        names |= {part.split(":")[0].strip() for part in group.split(",")} - {""}
    return {n for n in names if ENV_NAME.match(n)} - STANDARD


def argparse_flags(path):
    """Every --flag an add_argument(...) call of the file declares, in any of its names."""
    return {a.value for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "add_argument"
            for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str) and a.value.startswith("--")}


def inputs(path):
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".sh":
        return shell_inputs(text)
    if path.suffix == ".py":
        return python_inputs(text)
    return js_inputs(text)


def code_files():
    return sorted(p for pattern in ("inferpilot/*.py", "setup.py", "scripts/*.py", "scripts/*.sh", "templates/harness/*.sh",
                                    "runtime/*.mjs")
                  for p in ROOT.glob(pattern) if not p.name.startswith("test_"))


def header(path):
    """A script's own documentation: its docstring, or the comments it opens with."""
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".py":
        return ast.get_docstring(ast.parse(text)) or ""
    mark = "//" if path.suffix == ".mjs" else "#"
    lines = text.splitlines()
    top = []
    for line in lines[1:] if lines and lines[0].startswith("#!") else lines:
        if not line.startswith(mark):
            break
        top.append(line)
    return "\n".join(top)


def mentions(name, text):
    return re.search(r"(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])" % re.escape(name), text) is not None


def flags_in(text, command):
    """The --flags given to command: from where it is named to the end of its code span or command."""
    text = text.replace("\\\n", " ")
    out = set()
    for m in re.finditer(re.escape(command), text):
        rest = re.split(r"`|\n|&&|;|\|", text[m.end():], maxsplit=1)[0]
        out |= set(re.findall(r"(?<![\w-])--[a-z][a-z0-9-]*", rest))
    return out


class EnvironmentTest(unittest.TestCase):
    def test_setup_container_and_runner_variables_are_in_both_readmes(self):
        missing = ["%s reads %s; %s does not say what it does" % (path.relative_to(ROOT), name, readme)
                   for pattern in USER_FACING for path in sorted(ROOT.glob(pattern)) for name in sorted(inputs(path))
                   for readme, text in (("README.md", EN), ("README.zh-CN.md", ZH)) if not mentions(name, text)]
        self.assertEqual([], missing)

    def test_every_other_script_names_the_variables_it_reads(self):
        facing = {p for pattern in USER_FACING for p in ROOT.glob(pattern)}
        missing = ["%s reads %s; neither its header nor the README says what it does" % (path.relative_to(ROOT), name)
                   for path in code_files() if path not in facing for name in sorted(inputs(path))
                   if not mentions(name, header(path)) and not mentions(name, EN)]
        self.assertEqual([], missing)

    def test_every_variable_the_readme_tables_list_is_read(self):
        read = set().union(*(inputs(p) for p in code_files()))
        self.assertEqual([], ["README.md lists %s, which nothing reads" % name
                              for name in sorted(table_names(EN)) if ENV_NAME.match(name) and name not in read])


class FlagsTest(unittest.TestCase):
    def test_bootstrap_flags_the_docs_give_exist_and_every_one_is_documented(self):
        defined = argparse_flags(ROOT / "inferpilot" / "bootstrap.py")
        self.assertTrue(defined)
        for name, text in (("README.md", EN), ("README.zh-CN.md", ZH), ("AGENTS.md", (ROOT / "AGENTS.md").read_text(encoding="utf-8"))):
            for flag in sorted(flags_in(text, "inferpilot.bootstrap") - defined):
                self.fail("%s gives inferpilot.bootstrap %s, which it does not take" % (name, flag))
        for flag in sorted(defined):
            for name, text in (("README.md", EN), ("README.zh-CN.md", ZH)):
                self.assertTrue(mentions(flag, text), "%s does not mention inferpilot.bootstrap %s" % (name, flag))

    def test_runner_flags_read_listed_and_given_are_the_same(self):
        run = ROOT / "runtime" / "run.mjs"
        src = run.read_text(encoding="utf-8")
        read = {"--" + (a or b) for a, b in re.findall(r"opts(?:\.([a-z]+)\b|\['([a-z-]+)'\])", src)}
        taken = {"--" + f for f in re.findall(r"'([a-z-]+)'", re.search(r"const FLAGS = \[(.*?)\]", src).group(1))}
        usage = set(re.findall(r"--[a-z][a-z0-9-]*", re.search(r"usage: node runtime/run\.mjs(.*?)\\n'", src, re.S).group(1)))
        comment = {a or b for a, b in re.findall(r"^//\s+(--[a-z][a-z0-9-]*)|\| (--[a-z][a-z0-9-]*)", header(run), re.M)}
        self.assertEqual(read, taken, "run.mjs reads these flags; it takes those (any other stops it)")
        self.assertEqual(read, usage, "run.mjs reads these flags; its usage line lists those")
        self.assertEqual(read, comment, "run.mjs reads these flags; the comment it opens with lists those")
        for name in ("README.md", "README.zh-CN.md", "AGENTS.md"):
            given = flags_in((ROOT / name).read_text(encoding="utf-8"), "runtime/run.mjs")
            self.assertLessEqual(given, read, "%s gives runtime/run.mjs flags it does not take" % name)


BUNDLE = "RUN_DIR/branch.bundle"
SIDE = "refs/inferpilot/restored/<slug>"


def doc_command(path, start):
    """The backquoted command in a role file that begins with `start`, its line breaks undone."""
    text = re.sub(r"\s+", " ", (ROOT / path).read_text())
    m = re.search(r"`(%s[^`]*)`" % re.escape(start), text)
    return m.group(1) if m else None


class BranchBundleTest(unittest.TestCase):
    """What an attempt committed outlives its container: every role that moves the branch refreshes the
    bundle; a retry starts from the base and takes its commits from a side ref, a resume gets its branch
    back. The documented commands do that on a real repository."""

    def test_every_role_that_moves_the_branch_names_the_bundle(self):
        implementer = (ROOT / "roles/implementer.md").read_text()
        for phase in ("## PHASE=implement", "## PHASE=rollback"):
            body = implementer.split(phase, 1)[1].split("\n## ", 1)[0]
            self.assertIn(BUNDLE, body, "implementer %s does not refresh the bundle" % phase)
        for path in ("roles/director.md", "roles/pr_engineer.md", "knowledge/env_hygiene.md"):
            self.assertIn(BUNDLE, (ROOT / path).read_text(), "%s does not name %s" % (path, BUNDLE))
        self.assertIn(SIDE, implementer, "the implementer does not take a retry's commits from %s" % SIDE)
        director = (ROOT / "roles/director.md").read_text()
        self.assertIn("`base_commit`", director, "Setup does not write the base_commit a retry reads")
        self.assertIn("update-ref -d " + SIDE, re.sub(r"\s+", " ", director),
                      "Setup does not drop a side ref no bundle backs, so a retry could take rolled-back commits")

    def test_the_documented_commands_keep_a_retry_on_the_base_and_give_a_resume_its_branch(self):
        create = doc_command("roles/implementer.md", "git -C <worktree> bundle create")
        retry = doc_command("roles/director.md", "git -C <framework_dir> fetch %s +" % BUNDLE)
        resume = doc_command("roles/director.md", "git -C <framework_dir> fetch %s inferpilot/" % BUNDLE)
        for name, cmd in (("bundle create", create), ("retry's side-ref fetch", retry), ("resume's restore", resume)):
            self.assertIsNotNone(cmd, "the roles give no %s command" % name)

        def run(cmd, **subs):
            for k, v in subs.items():
                cmd = cmd.replace(k, v)
            return subprocess.run(cmd.split(), capture_output=True, text=True)

        def git(*args, cwd):
            out = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd,
                                 check=True, capture_output=True, text=True)
            return out.stdout.strip()

        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            origin, old, run_dir = d / "origin", d / "old", d / "run"
            run_dir.mkdir()
            git("init", "-q", "-b", "main", str(origin), cwd=d)
            git("commit", "-q", "--allow-empty", "-m", "base", cwd=origin)
            base = git("rev-parse", "HEAD", cwd=origin)
            git("clone", "-q", str(origin), str(old), cwd=d)
            git("checkout", "-q", "-b", "inferpilot/t", cwd=old)
            # WP1 applies a PR: the author's commit, then the package's own
            for name, subject in (("pr", "the PR author's change"), ("WP1", "WP1: goal of WP1"),
                                  ("WP2", "WP2: goal of WP2")):
                (old / name).write_text(name)
                git("add", name, cwd=old)
                git("commit", "-q", "-m", subject, cwd=old)
            wp1 = git("rev-parse", "HEAD~1", cwd=old)
            subs = {"RUN_DIR": str(run_dir), "<worktree>": str(old), "<base>": base, "<slug>": "t"}
            side = SIDE.replace("<slug>", "t")

            def fresh(name):  # a new container: the image's checkout, fetched, nothing of the task's
                git("clone", "-q", str(origin), str(d / name), cwd=d)
                return {**subs, "<framework_dir>": str(d / name)}

            made = run(create, **subs)
            self.assertEqual(made.returncode, 0, made.stderr)

            # resume: the branch it stopped with comes back as it was
            r = fresh("resume")
            got = run(resume, **r)
            self.assertEqual(got.returncode, 0, got.stderr)
            self.assertEqual(git("rev-parse", "inferpilot/t", cwd=d / "resume"), git("rev-parse", "HEAD", cwd=old))

            # retry: the branch starts from the base; the commits wait on the side ref for their packages
            r = fresh("retry")
            git("checkout", "-q", "-b", "inferpilot/t", base, cwd=d / "retry")
            got = run(retry, **r)
            self.assertEqual(got.returncode, 0, got.stderr)
            self.assertEqual(git("rev-parse", "HEAD", cwd=d / "retry"), base, "a retry's earlier phases see the fix")
            found = git("log", "--reverse", "--format=%H %s", "%s..%s" % (base, side), cwd=d / "retry").splitlines()
            upto = [line.split(" ", 1)[1] for line in found].index("WP1: goal of WP1")
            for line in found[:upto + 1]:  # WP1 is the first package: everything after the base up to it
                git("cherry-pick", line.split()[0], cwd=d / "retry")
            self.assertEqual(git("rev-parse", "HEAD^{tree}", cwd=d / "retry"), git("rev-parse", wp1 + "^{tree}", cwd=old))

            # the second commit conflicts: --abort keeps the first, the recorded HEAD takes it back out
            git("checkout", "-q", "-b", "other", base, cwd=origin)
            (origin / "WP1").write_text("someone else's WP1")
            git("add", "WP1", cwd=origin)
            git("commit", "-q", "-m", "another WP1", cwd=origin)
            git("checkout", "-q", "main", cwd=origin)
            r = fresh("conflict")
            git("checkout", "-q", "-b", "inferpilot/t", "origin/other", cwd=d / "conflict")
            self.assertEqual(run(retry, **r).returncode, 0)
            recorded = git("rev-parse", "HEAD", cwd=d / "conflict")
            picks = git("log", "--reverse", "--format=%H", "%s..%s" % (base, side), cwd=d / "conflict").split()
            git("cherry-pick", picks[0], cwd=d / "conflict")
            bad = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "cherry-pick", picks[1]],
                                 cwd=d / "conflict", capture_output=True, text=True)
            self.assertNotEqual(bad.returncode, 0, "the test meant a conflict")
            git("cherry-pick", "--abort", cwd=d / "conflict")
            self.assertNotEqual(git("rev-parse", "HEAD", cwd=d / "conflict"), recorded)
            git("reset", "-q", "--hard", recorded, cwd=d / "conflict")
            self.assertEqual(git("rev-parse", "HEAD", cwd=d / "conflict"), recorded)

            # a kernel rolled back with revert: the message the implementer looks for is git's own
            (d / "conflict" / "kernel").write_text("k")
            git("add", "kernel", cwd=d / "conflict")
            git("commit", "-q", "-m", "WP3: a kernel", cwd=d / "conflict")
            k = git("rev-parse", "HEAD", cwd=d / "conflict")
            git("revert", "--no-edit", k, cwd=d / "conflict")
            self.assertIn("This reverts commit %s" % k, git("log", "-1", "--format=%B", cwd=d / "conflict"))
            self.assertIn("This reverts commit", re.sub(r"\s+", " ", (ROOT / "roles/implementer.md").read_text()))

            # main moved on and merged the PR: its commit comes up empty and is skipped, WP1 still applies
            (origin / "pr").write_text("pr")
            git("add", "pr", cwd=origin)
            git("commit", "-q", "-m", "the PR, merged", cwd=origin)
            r = fresh("moved")
            git("checkout", "-q", "-b", "inferpilot/t", cwd=d / "moved")
            self.assertEqual(run(retry, **r).returncode, 0)
            picks = git("log", "--reverse", "--format=%H", "%s..%s" % (base, side), cwd=d / "moved").split()
            empty = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "cherry-pick", picks[0]],
                                   cwd=d / "moved", capture_output=True, text=True)
            self.assertNotEqual(empty.returncode, 0, "a change the base already has was picked again")
            git("cherry-pick", "--skip", cwd=d / "moved")
            git("cherry-pick", picks[1], cwd=d / "moved")
            self.assertEqual(sorted(git("ls-files", cwd=d / "moved").split()), ["WP1", "pr"])

            # rollback of WP2, bundle refreshed: a retry finds only WP1
            git("reset", "-q", "--hard", wp1, cwd=old)
            self.assertEqual(run(create, **subs).returncode, 0)
            r = fresh("after_rollback")
            self.assertEqual(run(retry, **r).returncode, 0)
            self.assertEqual(git("log", "--format=%s", "%s..%s" % (base, side), cwd=d / "after_rollback"),
                             "WP1: goal of WP1\nthe PR author's change")

            # rollback of ALL: nothing beyond the base, which implementer.md says git refuses to bundle
            git("reset", "-q", "--hard", base, cwd=old)
            self.assertNotEqual(run(create, **subs).returncode, 0, "an empty bundle was written")


def alive(pid):
    """Whether pid runs: a zombie nobody has reaped yet does not."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        return Path("/proc/%d/stat" % pid).read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return True


class DetachedRunTest(unittest.TestCase):
    """A run longer than one tool call starts detached, and a later tool shell cannot wait for it: the
    documented launcher leaves the run's PID and exit status where that shell reads them, whatever the
    script does with quotes of its own, `exit`, `exec` or `set -e`, and in a shell with job control; no
    other launch shares its log and records, and a wrapper that has exited is gone though nobody reaped it."""

    # (the script, its arguments, the status it ends with, what its log has)
    SCRIPTS = (
        ("EXTRA_ARGS='--foo 1' sh -c 'echo \"results written $EXTRA_ARGS\"'\nexec sh -c 'exit 3'\necho never\n", [],
         "3", "results written --foo 1"),
        ("set -e\necho results written\nfalse\necho never\n", [], "1", "results written"),
        ("echo results written\nexit 4\necho never\n", [], "4", "results written"),
        ('printf "%s|" "$#" "$@"; echo\n', ["gsm8k", "two words"], "0", "2|gsm8k|two words|"),
    )

    def setUp(self):
        self.launch = doc_command("knowledge/env_hygiene.md", "setsid nohup")
        self.assertIsNotNone(self.launch, "env_hygiene.md gives no detached launcher")
        if not shutil.which("setsid"):
            self.skipTest("no setsid here")
        self.doc = re.sub(r"\s+", " ", (ROOT / "knowledge/env_hygiene.md").read_text())

    def start(self, d, body, args=(), job_control=False):
        """Launch body with the documented launcher, from a directory whose name has a space; its log."""
        (Path(d) / "a run").mkdir()
        script, log = Path(d) / "a run" / "run.sh", Path(d) / "a run" / "run.log"
        script.write_text(body)
        cmd = (self.launch.replace("<log>", shlex.quote(str(log))).replace("<script>", shlex.quote(str(script)))
               .replace("[<arg>...]", " ".join(shlex.quote(a) for a in args)))
        subprocess.run(["bash", "-c", ("set -m; " if job_control else "") + cmd], check=True, timeout=30)
        return log

    @staticmethod
    def read(path):
        """What path holds once it holds something; None when it is still empty after 10 s."""
        for _ in range(200):
            if path.exists() and path.read_text().strip():
                return path.read_text().strip()
            time.sleep(0.05)
        return None

    def test_a_run_that_writes_its_results_and_fails_is_seen_to_fail(self):
        for body, args, code, output in self.SCRIPTS:
            with self.subTest(script=body), tempfile.TemporaryDirectory() as d:
                log = self.start(d, body, args)
                self.assertEqual(self.read(Path("%s.exit" % log)), code)
                self.assertIn(output, log.read_text())
                self.assertNotIn("never", log.read_text())
        self.assertIn("A run is done when `<log>.exit` has its status, and passed only when that is 0", self.doc)
        self.assertIn("The script runs in a shell of its own, so an `exit`, an `exec` or `set -e` in it ends that "
                      "shell and the status is still written", self.doc)

    def test_the_pid_it_records_is_the_run_s_in_a_shell_with_job_control(self):
        with tempfile.TemporaryDirectory() as d:
            log = self.start(d, "sleep 2\n", job_control=True)
            pid = self.read(Path("%s.pid" % log))
            self.assertIsNotNone(pid, "the wrapper wrote no PID")
            pid = int(pid)
            self.assertTrue(alive(pid), "the recorded PID is gone while its run goes on")
            self.assertEqual((os.getsid(pid), os.getpgid(pid)), (pid, pid), "the recorded PID leads no session and group of its own")
            self.assertEqual(self.read(Path("%s.exit" % log)), "0")
        self.assertIn("The run's PID is the one in `<log>.pid`, once the file has it, not `$!`", self.doc)

    def test_a_run_stopped_with_its_wrapper_leaves_no_status(self):
        with tempfile.TemporaryDirectory() as d:
            log = self.start(d, "sleep 30\n")
            pid = int(self.read(Path("%s.pid" % log)))
            os.killpg(pid, signal.SIGTERM)
            for _ in range(200):
                if not alive(pid):
                    break
                time.sleep(0.05)
            self.assertFalse(alive(pid), "the wrapper outlived its group")
            self.assertFalse(Path("%s.exit" % log).exists(), "a wrapper stopped with its run wrote a status")
        self.assertIn("A wrapper that is gone with no status in `<log>.exit`, read once it is gone, was stopped with its run", self.doc)

    def test_two_launches_in_one_second_get_records_of_their_own(self):
        make = doc_command("knowledge/env_hygiene.md", "mktemp RUN_DIR/logs/")
        self.assertIsNotNone(make, "env_hygiene.md gives no command that makes a launch's log")
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "logs").mkdir()
            cmd = make.replace("RUN_DIR", shlex.quote(d)).replace("<tag>", "arm").replace("<time>", "20261125-010203Z")
            logs = [subprocess.run(["bash", "-c", cmd], check=True, capture_output=True, text=True, timeout=30).stdout.strip()
                    for _ in range(2)]
            self.assertNotEqual(logs[0], logs[1])
            self.assertTrue(all(Path(p).is_file() and p.endswith(".log") and "/logs/arm_20261125-010203Z_" in p for p in logs), logs)
        self.assertIn("a launch in the same second as another, a retry's among them, still gets a log of its own", self.doc)

    def test_a_wrapper_that_has_exited_is_gone_though_nobody_reaped_it(self):
        check = doc_command("knowledge/env_hygiene.md", "grep -qs '^State:")
        self.assertIsNotNone(check, "env_hygiene.md gives no check that a wrapper still runs")

        def runs(pid):
            return subprocess.run(["bash", "-c", check.replace("<pid>", str(pid))], timeout=30).returncode == 0

        live, done = subprocess.Popen(["sleep", "30"]), subprocess.Popen(["true"])
        try:
            for _ in range(200):
                if Path("/proc/%d/stat" % done.pid).read_text().rsplit(")", 1)[1].split()[0] == "Z":
                    break
                time.sleep(0.05)
            self.assertTrue(runs(live.pid), "a process that runs is not seen to")
            self.assertFalse(runs(done.pid), "a zombie is seen to run")
            done.wait()
            self.assertFalse(runs(done.pid), "a process that is gone is seen to run")
        finally:
            live.kill()
            live.wait()
            done.wait()
        self.assertIn("Not `kill -0 <pid>`: it succeeds for a wrapper that has exited and that nobody has reaped", self.doc)


if __name__ == "__main__":
    unittest.main()
