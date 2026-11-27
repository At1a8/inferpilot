"""What the code reads and what the docs say stay in step: every environment variable that setup, the
container script and the runner read is in the READMEs, every other script names the ones it reads in
its own header, every variable the READMEs' tables list is read by something, the command-line flags
the READMEs and AGENTS.md give exist, the branch bundle the roles write is the one a retry or a
resume takes the task's commits from, with commands that do it, and the detached launcher of
knowledge/env_hygiene.md leaves the run's PID and exit status. Offline.

The roles and the doctrine also agree on G0: it runs only where a change has a switch, and each role that
reports it says what it reports without one.

    python3 -m unittest discover -s tests
"""
import ast
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
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


class TokenComparisonTest(unittest.TestCase):
    """G2 compares scores, not tokens: a token comparison is a diagnosis aid or the subject of a determinism
    task (knowledge/validation_doctrine.md). No other document asks a gate for outputs equal to another
    run's, which a bf16 baseline does not give twice."""

    ASKS = re.compile(r"output\s+parity|match(?:es)?\s+greedy\s+output|greedy\s+outputs?\s+(?:equal|identical|match\w*)", re.I)

    def test_no_gate_asks_for_output_parity(self):
        doctrine = " ".join((ROOT / "knowledge" / "validation_doctrine.md").read_text(encoding="utf-8").split())
        self.assertIn("never the accuracy gate", doctrine)
        found = ["%s: %s" % (path.relative_to(ROOT), " ".join(m.group(0).split()))
                 for pattern in ("knowledge/*.md", "roles/*.md", "templates/*.md") for path in sorted(ROOT.glob(pattern))
                 for m in self.ASKS.finditer(path.read_text(encoding="utf-8"))]
        self.assertEqual(found, [], "these ask for outputs equal to another run's; G2 compares scores")


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


class LongContextScriptTest(unittest.TestCase):
    """G3 runs the task's own long-context test, `long_context_script`, as G4 keeps up the task's own client
    (knowledge/validation_doctrine.md): the user who asks for the gate says what it runs. A task with
    `long_context` on that names none is asked for it; InferPilot has no template for one, and no role writes
    one in its place. inferpilot.js, the roles, the templates and both READMEs say the same."""

    FIELD = "long_context_script"

    @staticmethod
    def flat(path):
        return " ".join((ROOT / path).read_text(encoding="utf-8").split())

    def test_the_task_definition_takes_the_script_and_every_role_reads_it(self):
        src = (ROOT / "inferpilot.js").read_text(encoding="utf-8")
        keys = re.search(r"const TASK_KEYS = \[(.*?)\];", src, re.S).group(1)
        intake = re.search(r"const TASK_DEF = S\(\{(.*?)\n\}\);", src, re.S).group(1)
        self.assertIn("'%s'" % self.FIELD, keys, "inferpilot.js does not take the script")
        self.assertIn("\n  %s: str," % self.FIELD, intake, "the intake cannot return the script")
        self.assertIn("\n  %s: String(A.%s || '').trim()," % (self.FIELD, self.FIELD), src, "the script does not reach the roles")
        self.assertIn("args.%s" % self.FIELD, src, "inferpilot.js's whenToUse does not name the script")
        fields = (ROOT / "templates" / "task_definition.md").read_text(encoding="utf-8")
        row = [line for line in fields.splitlines() if line.startswith("| `%s` |" % self.FIELD)]
        self.assertEqual(len(row), 1, "templates/task_definition.md has no row for the script")
        self.assertEqual(row[0].count("| with `long_context` on "), 4, "a feature, an issue, a ci and a perf task each name it with the gate on")
        self.assertIn("InferPilot has no template for it", row[0])
        gates = [line for line in fields.splitlines() if line.startswith("| `long_context`, `stability` |")]
        self.assertEqual(len(gates), 1)
        self.assertRegex(gates[0], r"\| optional \| optional \| optional \| optional \| \|$",
                         "the two gates are any task's that changes code to ask for, a ci task's too")
        for name, text in (("README.md", EN), ("README.zh-CN.md", ZH)):
            self.assertIn(self.FIELD, table_names(text), "%s has no row for the script" % name)

    def test_with_long_context_on_the_task_is_asked_for_its_test_and_nothing_stands_in(self):
        src = (ROOT / "inferpilot.js").read_text(encoding="utf-8")
        self.assertIn("const g3script = String(a.long_context_script || '').trim();\n"
                      "  if (mode !== 'scan' && asked(a.long_context) === 'true' && (!g3script || isTemplate(g3script))) {\n"
                      "    ask('long_context_script',", src, "long_context is on without a test for G3 to run: a path of blanks is none")
        question = re.search(r"\n    ask\('long_context_script',(.*?)\);\n", src, re.S)
        self.assertIsNotNone(question)
        self.assertNotIn("TEMPLATE", question.group(1), "the question offers a template InferPilot does not have")
        self.assertIn("InferPilot has no template for it", question.group(1))
        self.assertFalse((ROOT / "templates" / "harness" / "long_context.sh").exists(), "a template nobody asked for")

    def test_g3_runs_the_tasks_own_test_unchanged_on_both_arms(self):
        note = [i for i in items("knowledge/validation_doctrine.md") if i.startswith("- **G3 is the task's own long-context test.**")]
        self.assertEqual(len(note), 1, "knowledge/validation_doctrine.md does not say what G3 runs")
        for said in ("The user who asks for G3 says what it runs: the task's `long_context_script`",
                     "It runs unchanged on the baseline and on the change",
                     "A task with `long_context` on that names none is asked for it, in any mode",
                     "InferPilot has no template for a long-context test, and no role writes one in its place or changes the task's",
                     "G3 showed the server's long-context path, not the feature's"):
            self.assertIn(said, note[0])
        gate = [line for line in (ROOT / "knowledge" / "validation_doctrine.md").read_text(encoding="utf-8").splitlines()
                if line.startswith("| G3 long context |")]
        self.assertEqual(len(gate), 1)
        self.assertIn("the task's own long-context test (`long_context_script`)", gate[0])
        for path, said in (("roles/verify_engineer.md", "G3 long context is the task's own test, its `long_context_script`"),
                           ("roles/verify_engineer.md", "each run in a directory of its own that you make first: `mkdir -p "
                                                        "RUN_DIR/logs/g3_<arm>_c<n>_<run>`"),
                           ("roles/verify_engineer.md", "CONCURRENCY=<n> bash <long_context_script>"),
                           ("templates/task_definition.md", "a directory InferPilot makes for each run before it starts the script"),
                           ("roles/verify_engineer.md", "at two or more concurrencies, at least 4 runs each"),
                           ("roles/verify_engineer.md", "Never write a long-context test of your own, and never change the task's"),
                           ("roles/verify_engineer.md", "A script that does not read `CONCURRENCY` runs at its own"),
                           ("roles/verify_engineer.md", "G3 then showed the server's long-context path, not the feature's"),
                           ("roles/director.md", "and `long_context_script`, for which there is no template) run as given"),
                           ("roles/director.md", "G3, the task's `long_context_script`"),
                           ("roles/director.md", "G3's row names the task's script and the concurrencies it runs at"),
                           ("roles/intake.md", "`long_context_script` (`task_definition.md` §1.1): the user's own long-context test"),
                           ("roles/intake.md", "never answer `template`: InferPilot has no long-context test of its own"),
                           ("roles/intake.md", "With `long_context` on and no script, the run asks for it"),
                           ("knowledge/acceptance.md", "on the task's own test, its `long_context_script`"),
                           ("templates/task_definition.md", "**`long_context_script`**, with `long_context` on, runs the task's long-context "
                                                            "test against `PORT` and writes its results to `OUT_DIR`"),
                           ("templates/task_definition.md", "at least 4 times at each of two or more concurrencies"),
                           ("templates/task_definition.md", "It has no template for one, and writes none"),
                           ("templates/task_definition.md", "a task with `long_context` on names its `long_context_script`"),
                           ("templates/task_definition.md", "InferPilot has none for G3"),
                           ("templates/feature_spec.md", "the task's `long_context_script` at <two or more concurrencies>, at least 4 runs "
                                                         "each")):
            self.assertIn(said, self.flat(path), "%s does not say it" % path)
        for name, text, said in (("README.md", EN, "G3 runs it unchanged on the baseline and on the change"),
                                 ("README.zh-CN.md", ZH, "G3 在 baseline 和改动上原样运行它")):
            row = [line for line in text.splitlines() if line.startswith("| `%s` |" % self.FIELD)]
            self.assertEqual(len(row), 1, name)
            self.assertIn(said, row[0], "%s: the long_context_script row" % name)


class LongGatesTest(unittest.TestCase):
    """G3 and G4 run only when the task sets `long_context` or `stability`, two fields inferpilot.js takes
    and hands to every role. G4 is the task's performance client run back to back for more than 48 hours
    (scripts/stability.sh), once in a run, judged between its first hours and its last; its failure gets
    its baseline control in the same round, is the baseline's only at a comparable time, and is never
    reported as a pass (knowledge/validation_doctrine.md). The roles, the templates and both READMEs say
    the same."""

    FLAGS = ("long_context", "stability")

    @staticmethod
    def flat(path):
        return " ".join((ROOT / path).read_text(encoding="utf-8").split())

    def test_the_task_definition_takes_the_two_fields_off_by_default(self):
        src = (ROOT / "inferpilot.js").read_text(encoding="utf-8")
        keys = re.search(r"const TASK_KEYS = \[(.*?)\];", src, re.S).group(1)
        intake = re.search(r"const TASK_DEF = S\(\{(.*?)\n\}\);", src, re.S).group(1)
        fields = (ROOT / "templates" / "task_definition.md").read_text(encoding="utf-8")
        row = [line for line in fields.splitlines() if line.startswith("| `long_context`, `stability` |")]
        self.assertEqual(len(row), 1, "templates/task_definition.md has no row for the two fields")
        self.assertIn("each off by default", row[0])
        for flag in self.FLAGS:
            self.assertIn("'%s'" % flag, keys, "inferpilot.js does not take %s" % flag)
            self.assertIn("\n  %s: { enum: [true, false, 'true', 'false', ''] }," % flag, intake,
                          "the intake's %s is not true or false: another word would be read as off" % flag)
            self.assertRegex(src, r"\nconst [A-Z_]+ = on\(A\.%s\);" % flag, "%s is not off unless the task sets it" % flag)
            self.assertRegex(src, r"\n  %s: [A-Z_]+,\n" % flag, "%s does not reach the roles" % flag)
            self.assertIn("args.%s" % flag, src, "inferpilot.js's whenToUse does not name %s" % flag)
            for name, text in (("README.md", EN), ("README.zh-CN.md", ZH)):
                self.assertIn(flag, table_names(text), "%s has no row for %s" % (name, flag))

    def test_g3_and_g4_run_only_when_the_task_sets_their_field(self):
        for path in ("knowledge/validation_doctrine.md", "knowledge/acceptance.md", "knowledge/dev_methodology.md",
                     "roles/intake.md", "roles/director.md", "roles/verify_engineer.md",
                     "templates/task_definition.md", "templates/feature_spec.md"):
            text = self.flat(path)
            for flag in self.FLAGS:
                self.assertIn("`%s`" % flag, text, "%s does not name %s" % (path, flag))
            self.assertNotRegex(text, r"G3 and G4[^.|]*`goal`[^.|]* asks", "%s still makes `goal` the switch" % path)
        self.assertIn("G3 only with `long_context` on, G4 only with `stability` on", self.flat("roles/verify_engineer.md"))
        self.assertIn("`goal` does not turn a gate on", self.flat("knowledge/validation_doctrine.md"))
        self.assertIn("a `goal` the caller gave does not drop what the request asked", self.flat("roles/intake.md"))
        spec = (ROOT / "templates" / "feature_spec.md").read_text(encoding="utf-8")
        for gate, flag in zip(("G3", "G4"), self.FLAGS):
            row = [line for line in spec.splitlines() if line.startswith("| %s " % gate)]
            self.assertEqual(len(row), 1, gate)
            self.assertIn("only with `%s` on; else `not run: not asked`" % flag, row[0], gate)
        acceptance = (ROOT / "knowledge" / "acceptance.md").read_text(encoding="utf-8")
        profile = [line for line in acceptance.splitlines() if line.startswith("| Stability | ") and "G4)" in line]
        self.assertEqual(len(profile), 1)
        self.assertNotIn("required", profile[0], "no profile makes G4 required")

    def test_g4_is_one_run_of_more_than_48_hours(self):
        for path in ("knowledge/validation_doctrine.md", "knowledge/acceptance.md", "roles/verify_engineer.md",
                     "roles/director.md", "roles/intake.md", "templates/task_definition.md", "templates/feature_spec.md"):
            self.assertRegex(self.flat(path), r"more than 48 h(?:ours)?\b", "%s does not give G4 its 48 hours" % path)
        verify = self.flat("roles/verify_engineer.md")
        self.assertNotIn("G3/G4 at two or more concurrencies", verify, "G4 is one run, at one concurrency")
        self.assertIn("G4 stability is one run, not repeated and not taken to other concurrencies", verify)
        doctrine = self.flat("knowledge/validation_doctrine.md")
        self.assertNotIn("Run G3/G4 at more than one concurrency", doctrine)
        self.assertIn("G4 is the exception: one run of more than 48 hours", doctrine)

    def test_a_drift_is_judged_between_the_first_hours_and_the_last(self):
        """Not by the spread of the whole run: a steady fall from 100 to 50 lies within its own range."""
        hourly = ("Each hour of the run has one value of throughput and one of memory: the median of the passes "
                  "that ended in it, the first pass left out as warm-up.")
        window = ("The median of the last 6 hours' values is no lower in throughput, and no higher in memory, than "
                  "the median of the first 6 hours' by more than the range of those first 6 values.")
        for path in ("knowledge/validation_doctrine.md", "roles/verify_engineer.md"):
            text = self.flat(path)
            self.assertIn(hourly, text, "%s does not say what an hour's value is" % path)
            self.assertIn(window, text, "%s does not compare the first hours with the last" % path)
            self.assertNotRegex(text, r"beyond the spread of (?:its hourly|the) samples", path)

    def test_a_failed_g4_gets_its_control_in_the_round_and_stays_failed(self):
        verify = self.flat("roles/verify_engineer.md")
        self.assertIn("A failed G4 gets its control in this round", verify)
        self.assertIn("its estimate counts G4 twice, its run and the control a failure would need", verify)
        self.assertIn("A known issue never turns a failed G4 into a pass** (step 4 does not apply to it)", verify)
        self.assertIn("until `known_issues` holds it as the baseline's: from then on report it as failed, with that "
                      "issue, in `failures` with `blocking: false`", verify)
        investigator = self.flat("roles/investigator.md")
        self.assertIn("comes with its same-pattern baseline control already run", investigator)
        doctrine = self.flat("knowledge/validation_doctrine.md")
        self.assertIn("A failed G4 gets its baseline control in the same round, and stays failed", doctrine)
        self.assertIn("G4 keeps its verdict even so", doctrine)

    def test_a_g4_failure_is_the_baselines_only_at_a_comparable_time(self):
        """The same signature is not enough: hour 1 against the baseline's hour 47 is a change in rate."""
        for path in ("knowledge/validation_doctrine.md", "roles/investigator.md"):
            text = self.flat(path)
            self.assertIn("the same failure at a comparable time", text, path)
            self.assertRegex(text, r"Hour 31 against the baseline's hour 27 is(?: comparable)?; hour 1 against hour 47 is a "
                                   r"change in rate, which is the change's", path)
            self.assertRegex(text, r"A control that ended another way first, or was interrupted, (?:says|shows) nothing "
                                   r"about this failure", path)
            self.assertIn("`inconclusive`", text, path)

    def test_the_investigator_starts_no_control_it_cannot_finish(self):
        investigator = self.flat("roles/investigator.md")
        self.assertIn("never start a run that cannot finish in it", investigator)
        self.assertIn("return `inconclusive` with the long controls still to run", investigator)

    def test_g4_runs_once_in_a_run_and_its_result_names_its_commit(self):
        self.assertIn("G4 runs once in a run, and its result names its commit", self.flat("knowledge/validation_doctrine.md"))
        verify = self.flat("roles/verify_engineer.md")
        self.assertIn("**G4 runs once in a run.** Write the commit it ran on", verify)
        self.assertIn("say in `gates` that this commit has not been through G4", verify)
        self.assertIn("Only a G4 whose failure was the change's is run again, on the fix", verify)
        self.assertIn("the code handed over has not been through G4", self.flat("roles/director.md"))
        self.assertIn("G4 runs once in a run", self.flat("templates/task_definition.md"))
        self.assertIn("once in a run", EN)
        self.assertIn("一次运行里只跑一次", ZH)

    def test_g4s_load_is_the_runs_performance_client_and_the_task_brings_one(self):
        doctrine = self.flat("knowledge/validation_doctrine.md")
        self.assertIn("G4 is the task's performance test, run back to back for more than 48 hours", doctrine)
        self.assertNotIn("throughput_test.py", doctrine, "a finite batch looped lets the concurrency drain")
        for path in ("knowledge/validation_doctrine.md", "roles/verify_engineer.md", "templates/task_definition.md",
                     "templates/feature_spec.md", "README.md", "README.zh-CN.md"):
            self.assertIn("scripts/stability.sh", self.flat(path), "%s does not name G4's script" % path)
        self.assertTrue((ROOT / "scripts" / "stability.sh").is_file())
        src = (ROOT / "inferpilot.js").read_text(encoding="utf-8")
        self.assertIn("const g4 = (mode === 'issue' || mode === 'ci') && asked(a.stability) === 'true';", src,
                      "a feature and a perf task name their server and their client anyway: G4 asks an issue or a ci task")
        self.assertIn("if ((g4 || fixes) && !a.client_script) {", src, "stability is on without a client to run")
        for field in self.FLAGS:
            self.assertIn("['%s', '" % field, src, "a value of %s that is neither true nor false is not asked about" % field)

    def test_with_stability_on_the_task_names_its_server_and_its_client_and_no_template_is_offered(self):
        """The user asks for G4 and says what it keeps up for two days: the server and the client are the task's,
        asked for where it lacks one, in any mode. No template stands in on G4's account, and InferPilot's client,
        where the task names it all the same, runs the task's workload, never lengths and a concurrency of its own."""
        src = (ROOT / "inferpilot.js").read_text(encoding="utf-8")
        self.assertIn("if (g4 && !a.server_script) {", src,
                      "an issue or a ci task with stability on may start without a server for G4")
        self.assertIn("if ((g4 || fixes) && gaps.length) {", src, "the client template would keep up its own lengths and concurrency")
        block = re.search(r"\n  const g4 = .*?\n  const kb = ", src, re.S)
        self.assertIsNotNone(block, "the G4 questions are not where missingFields has them")
        self.assertEqual(block.group(0).count("ask('"), 3, "G4 asks for its server, its client and the template's workload")
        self.assertNotIn("[TEMPLATE]", block.group(0), "a G4 question offers the template")
        self.assertNotIn("Or ${TEMPLATE}", block.group(0), "a G4 question offers the template")
        self.assertNotIn("A.client_script = TEMPLATE", src, "the run takes the template for G4 without being told to")
        for path, said in (("templates/task_definition.md", "names its `server_script` and its `client_script`, whatever its mode"),
                           ("templates/task_definition.md", "No template is offered for either on G4's account"),
                           ("templates/task_definition.md", "gives the `workload` it runs, and is asked for that"),
                           ("knowledge/validation_doctrine.md", "The server, the client and the workload are the task's to bring"),
                           ("knowledge/validation_doctrine.md", "no template is taken for it"),
                           ("knowledge/validation_doctrine.md", "never on lengths and a concurrency of its own"),
                           ("roles/intake.md", "a task without a `server_script` or without a `client_script` is asked for it, in any mode"),
                           ("roles/intake.md", "An issue's or a ci task's question offers no `template`"),
                           ("README.md", "names its `server_script` and its `client_script`, in any mode, and is asked for the one it lacks: "
                                         "G4 takes no template of its own accord"),
                           ("README.zh-CN.md", "任何模式下任务都要给出自己的 `server_script` 和 `client_script`，缺哪个就问哪个：G4 不会自行改用模板")):
            self.assertIn(said, self.flat(path), "%s does not say it" % path)
        fields = (ROOT / "templates" / "task_definition.md").read_text(encoding="utf-8")
        server = [line for line in fields.splitlines() if line.startswith("| `server_script` |")]
        self.assertEqual(len(server), 1)
        self.assertIn("| recommended; required with `stability` on |", server[0], "an issue with stability on needs its server script")
        both = [line for line in fields.splitlines() if line.startswith("| `long_context`, `stability` |")]
        self.assertIn("so the task names both", both[0])


class ComponentCauseTest(unittest.TestCase):
    """A root cause in an AMD component outside the framework (aiter, MoRI, ROCm, the ROCm build of PyTorch) is
    that component's to fix: the issue for its repository is drafted, and the framework gets a workaround only
    for a P0 issue (knowledge/issue_triage.md §5). A CI failure is fixed as any other, with the draft besides."""

    @staticmethod
    def flat(path):
        return " ".join((ROOT / path).read_text(encoding="utf-8").split())

    def test_the_run_decides_by_the_priority(self):
        src = (ROOT / "inferpilot.js").read_text(encoding="utf-8")
        for said in ("function componentCause(found, said) {",
                     "C.component_issue = R.component_issue = { component, draft_path: draft, summary: String(said || '').trim().slice(0, 1200) };",
                     "if (C.component_issue && C.priority !== 'P0') {",
                     "const noDraft = ['fix_needed', 'baseline_bug'].includes(dg.verdict) ? componentCause(dg, dg.summary) : '';",
                     "file the issue draft in ${R.component_issue.component}'s repository"):
            self.assertIn(said, src)
        self.assertIn("const COMPONENTS = ['aiter', 'mori', 'rocm', 'pytorch_rocm'];", src)
        self.assertIn("const COMPONENT_CAUSE = { component: { type: 'string', enum: ['', ...COMPONENTS] }, component_issue_path: str };", src,
                      'any word is taken for a component: "none" would stop a fix of the framework\'s own')
        self.assertIn("if (!COMPONENTS.includes(component)) return '';", src)
        # ci: the draft is recorded before the check that ends a run without a fix to make.
        self.assertLess(src.index("const noDraft = bi && bi.confirmed === true ? componentCause(bi, bi.evidence) : '';"),
                        src.index("if (!bi || bi.confirmed !== true || !(bi.work_packages || []).length) {"),
                        "a defect the lane cannot be fixed for loses its draft")
        for schema in ("DIAG", "BISECT"):
            self.assertIn("Object.assign(%s.properties, COMPONENT_CAUSE);" % schema, src, "%s takes no component" % schema)

    def test_the_rule_is_written_where_each_role_reads_it(self):
        for path, said in (
                ("knowledge/issue_triage.md", "**A root cause in an AMD component outside the framework** (aiter, MoRI, ROCm, the ROCm build of PyTorch) is that component's to fix."),
                ("knowledge/issue_triage.md", "**P0:** a workaround in the framework, HIP-only"),
                ("knowledge/issue_triage.md", "so G2 and G5 measure it"),
                ("knowledge/issue_triage.md", "**P1 and P2:** no workaround. The run ends at Diagnose (`diagnosed`)"),
                ("knowledge/issue_triage.md", "A component that changed its interface on purpose has no defect"),
                ("roles/investigator.md", "and draft the issue for its repository as `RUN_DIR/component_issue.md` (`component_issue_path`)"),
                ("roles/investigator.md", "for a P0, `work_packages` are the workaround in the framework"),
                ("roles/investigator.md", "for any other issue return none."),
                ("roles/investigator.md", "Never change the component: it comes from the image"),
                ("roles/investigator.md", "Return `component` (`aiter`, `mori`, `rocm` or `pytorch_rocm`, and nothing for any other cause"),
                ("roles/investigator.md", "Either way `summary` says what the reporter can do meanwhile"),
                ("roles/issue_triager.md", "as the diagnosis gave it (`component_issue.summary`)"),
                ("roles/ci_guardian.md", "where you can plan none, return the component and its draft all the same"),
                ("roles/issue_triager.md", "With `component_issue` the root cause is in an AMD component outside the framework"),
                ("roles/issue_triager.md", "`<component issue link>` placeholder, since the human files that issue"),
                ("roles/ci_guardian.md", "and draft the issue for its repository as `RUN_DIR/component_issue.md` (`component_issue_path`)"),
                ("roles/ci_guardian.md", "The lane is still fixed by the package; where you can plan none"),
                ("knowledge/ci_triage.md", "**A defect of an AMD component** outside the framework"),
                ("knowledge/ci_triage.md", "with a pin to the last version that passed where a bump brought it"),
                ("roles/director.md", "With `component_issue`, name the component the root cause is in"),
                ("templates/issue_triage.md", "the issue drafted for its repository (`component_issue.md`)"),
                ("templates/ci_triage.md", "| defect of an AMD component |"),
                ("README.md", "| `component_issue.md` |"),
                ("README.zh-CN.md", "| `component_issue.md` |")):
            self.assertIn(said, self.flat(path), "%s does not say it" % path)

    def test_the_components_are_the_ones_the_priority_rubric_calls_the_amd_side(self):
        # issue_triage.md §2 names the AMD-maintained stacks; the rule is about the same four, wherever it is written.
        rubric = self.flat("knowledge/issue_triage.md")
        self.assertIn("AMD-maintained stacks (aiter, MoRI, ROCm, the ROCm build of PyTorch)", rubric)
        for path in ("knowledge/issue_triage.md", "knowledge/ci_triage.md", "roles/investigator.md", "roles/ci_guardian.md"):
            self.assertIn("(aiter, MoRI, ROCm, the ROCm build of PyTorch", self.flat(path), path)


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


def items(path):
    """A document's paragraphs, list items and table rows, each on one line."""
    text = (ROOT / path).read_text(encoding="utf-8")
    return [" ".join(part.split()) for part in re.split(r"\n(?=[ \t]*(?:\d+\.|[-*]) |\|)|\n[ \t]*\n", text)]


class FeatureOffTest(unittest.TestCase):
    """G0 turns a feature off, so it runs only where the change has a switch (knowledge/validation_doctrine.md).
    Every mode that changes code still answers for it: the roles that report it say what they report for a
    change without a switch, and the documents of the modes that fix things ask for it only where there is one.
    Such a change is judged against the tree without it: a test by how it fails there, not by an absolute pass;
    a smoke only where the change is meant to keep what the server does; the review lets no regression through
    as minor; and the investigator's feature-off control becomes the change reverted."""

    @staticmethod
    def flat(path):
        return " ".join((ROOT / path).read_text(encoding="utf-8").split())

    def test_the_doctrine_gives_g0_only_to_a_change_with_a_switch(self):
        note = [i for i in items("knowledge/validation_doctrine.md") if i.startswith("- **G0 needs a switch.**")]
        self.assertEqual(len(note), 1, "knowledge/validation_doctrine.md does not say where G0 runs")
        for said in ("without a switch", "has no G0", "`roles/test_engineer.md`", "`roles/pr_engineer.md`",
                     "`roles/reviewer.md`"):
            self.assertIn(said, note[0])

    def test_every_role_that_reports_g0_says_what_it_reports_without_a_switch(self):
        for path, result in (("roles/test_engineer.md", "`feature_off_unchanged`"),
                             ("roles/pr_engineer.md", "`feature_off_final`"), ("roles/verify_engineer.md", "`gates`")):
            named = [i for i in items(path) if re.search(r"\bG0\b", i) and "feature off" in i.lower()]
            self.assertEqual(len(named), 1, "%s: the steps that run G0" % path)
            self.assertRegex(named[0], r"without (?:a switch|one)", "%s runs G0 on a change with nothing to turn off" % path)
            self.assertIn(result, named[0], "%s does not say what %s reports without a switch" % (path, result))

    def test_a_fix_is_asked_for_g0_only_behind_a_features_switch(self):
        for path in ("knowledge/ci_triage.md", "knowledge/issue_triage.md"):
            named = [i for i in items(path) if re.search(r"\bG0\b", i)]
            self.assertTrue(named, "%s does not say when a fix needs G0" % path)
            for item in named:
                self.assertIn("only when the fix is behind a feature's switch", item, path)
                self.assertIn("existing tests of the module", item, "%s: what a fix without a switch shows instead" % path)

    def test_without_a_switch_a_test_is_judged_by_how_it_fails_without_the_change(self):
        """Not by a pass on the change alone, which a test the base already fails never gives; not by pass or
        fail alone, which takes an assertion that became a crash for no change; and a test the fix made
        runnable has no result on the parent to be compared with."""
        self.assertIn("judged against the tree without it, never as an absolute pass",
                      self.flat("knowledge/validation_doctrine.md"))
        for path, same in (("knowledge/validation_doctrine.md", "fails there the same way: the same test, the same error"),
                           ("roles/test_engineer.md", "fails there the same way, the same test with the same error"),
                           ("roles/pr_engineer.md", "fails the same way on the base, the same test with the same error"),
                           ("knowledge/pr_playbook.md", "or fail the same way on the base")):
            self.assertIn(same, self.flat(path), "%s does not say which failure the change did not cause" % path)
        for path in ("knowledge/validation_doctrine.md", "roles/test_engineer.md", "roles/pr_engineer.md"):
            text = self.flat(path)
            self.assertIn("fails there another way", text, "%s takes a failure that changed for no change" % path)
            self.assertIn("cannot run at all because of the failure", text,
                          "%s: a test the fix made runnable has no result without the fix" % path)
            self.assertIn("the last commit that could run it, the culprit's parent for a CI fix", text, path)
        engineer, pr = self.flat("roles/test_engineer.md"), self.flat("roles/pr_engineer.md")
        self.assertIn("each one that fails again on the parent", engineer, "nothing asks for the parent's result")
        self.assertNotIn("no test which passes on the parent fails", engineer)
        self.assertIn("each one that fails again on the base", pr, "nothing asks for the base's result")
        self.assertIn("`feature_off_final` is then true when", pr, "the PR engineer is not told when the check passes")

    def test_without_a_switch_the_smoke_is_compared_only_where_the_change_keeps_what_the_server_does(self):
        """The baseline of the case a fix is for is the failure: a smoke that had to match it would have a
        correct fix rolled back at Implement, before the mode's gate could judge it."""
        doctrine, engineer = self.flat("knowledge/validation_doctrine.md"), self.flat("roles/test_engineer.md")
        self.assertIn("A server smoke is compared only with a baseline of a case the change is meant to keep", doctrine)
        self.assertIn("**The smoke,** for a package on the serving path, only against a baseline of a case the package "
                      "is meant to keep", engineer)
        for name, text in (("the doctrine", doctrine), ("the test engineer", engineer)):
            self.assertIn("meant to behave differently with the fix", text, name)
            self.assertRegex(text, r"the mode's gate[^.]* judge that difference", name)
        self.assertNotIn("matches its baseline", engineer)

    def test_the_review_lets_no_regression_outside_the_changes_case_through_as_minor(self):
        harm = [i for i in items("roles/reviewer.md") if "**Do-no-harm.**" in i]
        self.assertEqual(len(harm), 1)
        self.assertIn("is a `major` finding, whose work package narrows the change to its case", harm[0])
        self.assertIn("only when the change cannot avoid it and the evidence shows that case is no worse for it", harm[0])
        self.assertIn("the verdict is `blocked`", harm[0], "a regression the change cannot avoid is a human's to accept")
        self.assertNotIn("one the change cannot avoid is a `minor`", harm[0])

    def test_the_control_that_turns_the_feature_off_has_a_form_without_a_switch(self):
        for path in ("roles/investigator.md", "knowledge/debugging_playbook.md", "templates/experiment_ledger.md"):
            named = [i for i in items(path) if re.search(r"feature off on (?:the )?branch", i, re.I)]
            self.assertTrue(named, "%s has no feature-off control" % path)
            for item in named:
                self.assertIn("without a switch", item, "%s asks a change with nothing to turn off for this control" % path)
                self.assertIn("the change's commits reverted", item, path)
        again = [i for i in items("knowledge/debugging_playbook.md") if re.search(r"\bG0\b", i)]
        self.assertTrue(again)
        for item in again:
            self.assertIn("without a switch", item, "the playbook runs G0 again after a fix, switch or no switch")


class FixGatesByFileTest(unittest.TestCase):
    """The diff of a fix is read once, for G2 and for G5, by one line drawn by file
    (knowledge/validation_doctrine.md, What a fix changes decides its gates): both gates run for every fix that
    changes a file other than tests, documentation and CI files. Of any other file nobody judges whether the
    server runs it, since that judgement can be wrong: a fix to a Dockerfile, a tool or a dependency file is
    measured like one to a model file, and a new version of a runtime dependency with that version installed."""

    @staticmethod
    def flat(path):
        return " ".join((ROOT / path).read_text(encoding="utf-8").split())

    def test_the_doctrine_draws_one_line_for_both_gates(self):
        note = [i for i in items("knowledge/validation_doctrine.md") if i.startswith("- **What a fix changes decides its gates.**")]
        self.assertEqual(len(note), 1, "knowledge/validation_doctrine.md does not say what a fix's files decide")
        for said in ("read once, for G2 and for G5, by one line drawn by file",
                     "both gates run for every fix that changes a file other than tests, documentation and CI files "
                     "(sglang: `test/`; `docs/`, `*.md`; `.github/`, `scripts/ci/`)",
                     "That is what *code the server runs* means wherever a rule on a fix names it",
                     "Whether the server in fact runs such a file is nobody's to judge",
                     "an accuracy or a speed loss let through on such a reading is the costly mistake",
                     "a fix to a Dockerfile, a benchmark or tool script or a dependency file is measured like a fix to a model file, "
                     "on gsm8k and on the task's client",
                     "A change outside the framework's code takes effect only once it is put into effect",
                     "an environment variable it sets, such as `SGLANG_USE_AITER`",
                     "So every tree runs in the environment its own files call for: the fix with the change in effect, the base with the "
                     "image's, a parent or a mutant of a commit with what that tree declares",
                     "the unit test and its control at Implement, the feature-off checks, `issue_repro` or `failing_test`, both arms of G2 "
                     "and of G5, and the command the pull request gives",
                     "The same run in the unchanged environment shows nothing",
                     "a new version of a package the server runs (aiter, PyTorch, Triton, flashinfer), bumped or pinned",
                     # the arm's own environment first: nothing to restore, nothing left behind by a call that times out
                     "The environment is the arm's own wherever it can be",
                     "`pip install --no-deps --target RUN_DIR/env/<arm>`",
                     "a call that times out leaves nothing behind",
                     "Only a package that must replace the image's is installed in place",
                     "The image's state and the commands that restore it are written first (`RUN_DIR/env_restore.sh`, the ledger)",
                     "Every later launch, in this call or any other, checks the environment against that record before it starts",
                     "The arms run one after the other, never side by side",
                     "put back once every process of the round has stopped, not before",
                     "The report's commands carry the setup",
                     "Install what the change names and nothing else: never the framework again",
                     "the gate is a blocking failure of the class `environment`",
                     "It is not investigated, and no fix is planned for it",
                     "Only what this run's platform uses counts",
                     "`python/pyproject_other.toml`, `python/pyproject_rocm.toml`, `docker/rocm.Dockerfile`",
                     "a version moved only in the CUDA declaration changes nothing in this run",
                     "cannot be installed in this image"):
            self.assertIn(said, note[0])
        # A change that is not in effect is not measured as if it were: the note no longer lets a gate run both arms
        # in one environment and say so.
        self.assertNotIn("the change was not in effect", note[0])
        # No second, narrower line for G5: what the server is judged not to run is measured all the same.
        for gone in ("G5 runs when the fix changes code the server runs", "has no G5: `not run", "each by a line of its own"):
            self.assertNotIn(gone, note[0], "G5 still has a line of its own")
        for gate in ("G2", "G5"):
            text = [i for i in items("knowledge/validation_doctrine.md") if i.startswith("- **%s for a fix.**" % gate)][0]
            self.assertIn("anything but tests, documentation and CI files", text, "%s for a fix does not draw the line by file" % gate)
            self.assertNotIn("when it changes code the server runs", text, "%s still rests on a reading of what the server runs" % gate)
        g2 = [i for i in items("knowledge/validation_doctrine.md") if i.startswith("- **G2 for a fix.**")][0]
        for said in ("A fix to tests, documentation or CI files alone changes nothing the server runs and has no G2",
                     "Of any other file nobody judges whether the server runs it: that judgement can be wrong"):
            self.assertIn(said, g2)
        g5 = [i for i in items("knowledge/validation_doctrine.md") if i.startswith("- **G5 for a fix.**")][0]
        for said in ("and on the same condition: when it changes anything but tests, documentation and CI files",
                     "One diff decides both gates, by one line (*What a fix changes decides its gates*)",
                     "a fix to tests, documentation or CI files alone has no G5 either"):
            self.assertIn(said, g5)

    def test_the_roles_read_the_diff_by_that_line(self):
        role = self.flat("roles/verify_engineer.md")
        for said in ("when every file it names is a test, a documentation file or a CI file of the repository",
                     '`"G2": "not run: no code the server runs changed"`',
                     "That one diff decides G5 as well",
                     "The line is drawn by the file's path, and by nothing else",
                     "Never judge whether the server runs a file that is none of the three",
                     "a fix to a Dockerfile, a benchmark or tool script or a dependency file is measured like one to a model file",
                     "which the ROCm image does not install",
                     "A fix whose change takes effect outside the worktree",
                     "a package or an environment variable a Dockerfile sets",
                     "Run that gate, and every arm after it, in the environment its tree calls for",
                     "Make the environment the arm's own wherever you can",
                     "Only a package that must replace the image's is installed in place",
                     "First write the image's state and the commands that restore it (`RUN_DIR/env_restore.sh`, and the ledger)",
                     "no server is started ahead and no two arms run side by side",
                     "The reproduction that put the change into effect is stopped, and the image's state back, before the base arm of G2 starts",
                     "check the environment against the ledger's record before the round's first launch",
                     "Install what the change names and nothing else, never the framework again",
                     '`"failure_class": "environment"`',
                     "Never run the fix's arm in the image's environment and call it the fix",
                     "`false` only when every file the fix changes is a test, a documentation file or a CI file"):
            self.assertIn(said, role, "roles/verify_engineer.md does not say it")
        self.assertNotIn("G5 has the narrower line", role, "the verify engineer still judges what the server runs for G5")
        perf = self.flat("roles/perf_engineer.md")
        for said in ("**A fix is measured whatever file it changes,** tests, documentation and CI files apart",
                     "never judge whether the server runs it",
                     "is measured with each arm in the environment its tree calls for, as Verify set them",
                     "the warm-up, each round, the held-out point and the guards alike",
                     "One installed in place is checked against the ledger's record before every launch",
                     "return `not_run` saying so",
                     "never the fix's arm on the image's environment",
                     "is measured as it is, and `summary` says so"):
            self.assertIn(said, perf, "roles/perf_engineer.md does not say it")
        # Every role that runs a fix runs it where its change takes effect, not Verify alone.
        for path, said in (("roles/test_engineer.md", "is tested with every tree in the environment its own files call for"),
                           ("roles/test_engineer.md", "a mutant of the commit in the same, the parent with what the parent declares"),
                           ("roles/test_engineer.md", "Step 4 compares the same way"),
                           ("roles/test_engineer.md", "a check you leave running for the finishing call keeps its environment"),
                           ("roles/test_engineer.md", "Two trees in one environment show nothing"),
                           ("roles/pr_engineer.md", "each tree in the environment its own files call for"),
                           ("roles/pr_engineer.md", "the body gives the setup with the command"),
                           ("roles/director.md", "gives an arm whose change takes effect outside its tree"),
                           ("templates/validation_report.md", "the setup that puts it into effect before the arm's commands")):
            self.assertIn(said, self.flat(path), "%s does not say it" % path)
        # A change that cannot be put into effect was not measured, and no change to the code repairs that: the run
        # stops for a human before anything is investigated.
        src = (ROOT / "inferpilot.js").read_text(encoding="utf-8")
        stop = "const unset = blocking.filter((f) => String(f.failure_class || '').trim().toLowerCase() === 'environment');"
        self.assertIn(stop, src)
        self.assertLess(src.index(stop), src.index("enter('Investigate');"), "an environment failure goes to the investigator")
        review = self.flat("roles/reviewer.md")
        for said in ("`server_code_changed` false means the run measured neither its accuracy nor its speed",
                     "A changed file that is not a test, a documentation file or a CI file is a `blocker` finding",
                     "whether or not the server runs it"):
            self.assertIn(said, review, "roles/reviewer.md does not say it")

    def test_every_place_that_states_the_condition_states_it_by_file(self):
        for path, said in (
                ("knowledge/acceptance.md", "is measured too when it changes anything but tests, documentation and CI files, on the task's `model_path`"),
                ("knowledge/acceptance.md", "is measured too when it changes anything but tests, documentation and CI files, on the task's `client_script`"),
                ("knowledge/issue_triage.md", "A fix that changes anything but tests, documentation and CI files is measured on gsm8k as well"),
                ("knowledge/issue_triage.md", "Such a fix is measured for speed as well (G5 for a fix"),
                ("knowledge/ci_triage.md", "A fix that changes anything but tests, documentation and CI files is measured on gsm8k as well"),
                ("knowledge/ci_triage.md", "Such a fix is measured for speed as well (G5 for a fix"),
                ("knowledge/pr_playbook.md", "A fix that changes anything but tests, documentation and CI files gives the same two scores"),
                ("knowledge/pr_playbook.md", "So does a fix that changes anything but tests, documentation and CI files, the fix next to the base")):
            self.assertIn(said, self.flat(path), "%s does not say it" % path)
        acceptance = (ROOT / "knowledge" / "acceptance.md").read_text(encoding="utf-8")
        for name in ("Accuracy", "Performance"):
            row = [line for line in acceptance.splitlines() if line.startswith("| %s | required" % name)]
            self.assertEqual(len(row), 1, name)
            self.assertIn("required when the fix changes anything but tests, documentation and CI files", row[0], name)
            self.assertNotIn("required when the fix changes code the server runs", row[0], name)


class FixAccuracyTest(unittest.TestCase):
    """The fix of an issue or of a CI failure is measured on gsm8k as a feature is, when it changes code the
    server runs (knowledge/validation_doctrine.md, G2 for a fix). Such a task names its model unless it stops
    before the fix, and the roles and documents say what the fix's score is compared with."""

    @staticmethod
    def flat(path):
        return " ".join((ROOT / path).read_text(encoding="utf-8").split())

    def test_an_issue_or_a_ci_task_that_can_reach_a_fix_names_its_model(self):
        src = (ROOT / "inferpilot.js").read_text(encoding="utf-8")
        early = re.search(r"const BEFORE_A_FIX = \{\n  issue: \[(.*?)\],\n  ci: \[(.*?)\],\n\};", src)
        self.assertIsNotNone(early, "inferpilot.js does not say which phases come before a fix")
        stops = re.search(r"const STOP_PHASES = \{(.*?)\n\};", src, re.S).group(1)
        for mode, listed in zip(("issue", "ci"), early.groups()):
            phases = [p.lower() for p in re.findall(r"'(\w+)'", re.search(r"\n  %s: \[(.*?)\]," % mode, stops).group(1))]
            self.assertEqual(re.findall(r"'(\w+)'", listed), phases[:phases.index("implement")],
                             "%s: a run that stops before Implement has no fix, and one that reaches it has" % mode)
        self.assertIn("const fixes = !!BEFORE_A_FIX[mode] && !BEFORE_A_FIX[mode].includes(stop);", src)
        self.assertIn("if (fixes && !a.model_path) {\n    ask('model_path',", src)
        self.assertIn("const fix = (MODE === 'issue' || MODE === 'ci') && !!C.model_path;\n"
                      "  if ((MODE === 'feature' || MODE === 'perf' || fix) && !C.accuracy_script) {", src,
                      "a fix G2 will measure may start without the accuracy script")
        self.assertIn("if (fix && !C.server_script) {", src, "or without a server script to serve it with")

    def test_no_fix_starts_without_a_model(self):
        """A stop before the fix lets a run start without one; the route can skip that stop's phase (an existing
        fix PR has no Diagnose), and the control file can lift it."""
        src = (ROOT / "inferpilot.js").read_text(encoding="utf-8")
        self.assertIn("function askForFix(at) {\n  const lacks = ['model_path', 'client_script'].filter((k) => !C[k]);", src)
        self.assertRegex(src, r"const FIX_NEEDS = \{ model_path: [^}]*\};", "the model is not among what a fix needs")
        for at in ("Reproduce", "Diagnose", "Bisect"):
            self.assertIn("const lacking = askForFix('%s');\n" % at, src, "a fix can start after %s without a model" % at)
        self.assertRegex(src, r"if \(tri\.action === 'push_existing_pr'\) \{\n(?:\s*//.*\n)*\s*if \(stopHere\('Diagnose'\)\) \{",
                         "a stop after Diagnose does not hold for an existing fix PR, which has no Diagnose")
        for name, text in (("README.md", EN), ("README.zh-CN.md", ZH)):
            self.assertRegex(text, r"reaches its\s+fix without\s+one|走到修复却还没有模型", "%s does not list the question" % name)
        fields = (ROOT / "templates" / "task_definition.md").read_text(encoding="utf-8")
        row = [line for line in fields.splitlines() if line.startswith("| `model_path` |")]
        self.assertEqual(len(row), 1)
        self.assertEqual(row[0].count("required unless `stop_after` ends the run before a fix"), 2,
                         "templates/task_definition.md: an issue and a ci task each name a model")
        for name, text in (("README.md", EN), ("README.zh-CN.md", ZH)):
            row = [line for line in text.splitlines() if line.startswith("| `model_path` |")]
            self.assertEqual(len(row), 1, name)
            for said in ("`issue`", "`ci`", "`stop_after`", "gsm8k"):
                self.assertIn(said, row[0], "%s: the model_path row" % name)

    def test_a_fix_is_measured_when_it_changes_code_the_server_runs_and_says_what_it_is_compared_with(self):
        note = [i for i in items("knowledge/validation_doctrine.md") if i.startswith("- **G2 for a fix.**")]
        self.assertEqual(len(note), 1, "knowledge/validation_doctrine.md does not say how a fix is measured")
        for said in ("when it changes anything but tests, documentation and CI files", "the fix at most 1 point below the base", "has no G2",
                     "a model that answers no text questions", "The reproduction or the failing test runs first",
                     "it is never launched for G2", "the last commit that served the configuration",
                     "the culprit's parent for a CI fix", "The gate and the pull request name that commit", "it is reported"):
            self.assertIn(said, note[0])
        for path, said in (("roles/verify_engineer.md", "not run: no code the server runs changed"),
                           ("roles/verify_engineer.md", "not run: the model answers no text questions"),
                           ("roles/verify_engineer.md", "That gate of `extra_gates` runs first"),
                           ("roles/verify_engineer.md", "launch nothing for G2 until its gate of `extra_gates` has passed"),
                           ("roles/verify_engineer.md", "never launch it for G2"),
                           ("roles/verify_engineer.md", "Write that commit in the ledger and in `gates`"),
                           ("roles/verify_engineer.md", "it is reported, not judged"),
                           ("roles/director.md", "the verify engineer measures the base arm"),
                           ("roles/director.md", "when the task has a `model_path`, render InferPilot's server template"),
                           ("roles/director.md", "the one template a run takes without the user's answer, and say so in `notes`"),
                           ("templates/task_definition.md", "It is the one case in which a template runs without the user's answer"),
                           ("templates/task_definition.md", "it is not chosen for the user, but for the server a fix is measured on "
                                                            "(G2, G5) when the report or the job starts none"),
                           ("roles/director.md", "Return the one or the other as `server_script`"),
                           ("roles/director.md", "without one, skip them as well"),
                           ("roles/director.md", "for the G2 and the G5 of a fix whose baseline was measured on another commit"),
                           ("templates/validation_report.md", "the commit its baseline was measured on when that was not the base"),
                           ("roles/intake.md", "An issue or a ci task needs it too"),
                           ("knowledge/acceptance.md", "required when the fix changes anything but tests, documentation and CI files"),
                           ("knowledge/issue_triage.md", "A fix that changes anything but tests, documentation and CI files is measured on gsm8k as well"),
                           ("knowledge/ci_triage.md", "A fix that changes anything but tests, documentation and CI files is measured on gsm8k as well"),
                           ("knowledge/pr_playbook.md", "A fix that changes anything but tests, documentation and CI files gives the same two scores")):
            self.assertIn(said, self.flat(path), "%s does not say it" % path)
        acceptance = (ROOT / "knowledge" / "acceptance.md").read_text(encoding="utf-8")
        row = [line for line in acceptance.splitlines() if line.startswith("| Accuracy | required: gsm8k")]
        self.assertEqual(len(row), 1, "knowledge/acceptance.md §2 has no Accuracy row")
        for stale in ("when the issue is an accuracy loss", "| no regression |"):
            self.assertNotIn(stale, row[0], "the profiles still leave a fix's accuracy to the kind of issue")


class FixPerformanceTest(unittest.TestCase):
    """The fix of an issue or of a CI failure is measured for speed as it is for accuracy, and on the same condition
    (knowledge/validation_doctrine.md, G5 for a fix): when it changes code the server runs, it is no slower than the
    base on the task's client. A performance problem is measured whatever its fix changes: its number comes back.
    So such a task names its client unless it stops before the fix, and the roles and documents say so."""

    @staticmethod
    def flat(path):
        return " ".join((ROOT / path).read_text(encoding="utf-8").split())

    def test_an_issue_or_a_ci_task_that_can_reach_a_fix_names_its_client(self):
        src = (ROOT / "inferpilot.js").read_text(encoding="utf-8")
        self.assertIn("if ((g4 || fixes) && !a.client_script) {\n", src, "a fix G5 will measure may start without a client")
        self.assertIn("if ((g4 || fixes) && gaps.length) {\n    ask('workload',", src,
                      "InferPilot's client would measure a fix on lengths or a concurrency nobody chose")
        self.assertIn("const FIX_NEEDS = { model_path: 'the model', client_script: 'the client', "
                      "workload: \"the whole workload of InferPilot's client\" };", src,
                      "a run that reaches its fix without a client is not asked for one")
        # Intake asks nothing about the template's workload of a task that stops before its fix: where that stop
        # is lifted, the fix must not be measured on what Setup rendered from a part of a workload.
        self.assertIn("  const gaps = isTemplate(A.client_script) ? templateLoadGaps(A) : [];\n  if (gaps.length) lacks.push('workload');\n",
                      src, "a fix can start on InferPilot's client with lengths or a concurrency nobody chose")
        self.assertIn("const partial = R.questions.some((q) => q.field === 'workload') && A.workload ? { workload: '' } : {};\n"
                      "    out.rerun_with = { ...partial, answers:", src,
                      "the part of a workload the task gave wins over the answer on the next run")
        self.assertIn("Where the control file lifts that stop, the run asks before any code changes for what the fix lacks: the "
                      "model, the client, and of a task that names InferPilot's client all the same, what its `workload` leaves out.",
                      self.flat("templates/task_definition.md"))
        for mode in ("issue", "ci"):
            for table in ("ROUTES", "STOP_PHASES"):
                body = re.search(r"const %s = \{(.*?)\n\};" % table, src, re.S).group(1)
                phases = re.findall(r"'(\w+)'", re.search(r"\n  %s: \[(.*?)\]," % mode, body).group(1))
                self.assertEqual(phases[phases.index("Verify"):phases.index("Review") + 1], ["Verify", "Performance", "Review"],
                                 "%s: %s has no Performance between Verify and Review" % (table, mode))
        use = re.search(r"\n  whenToUse: '(.*)',\n", src).group(1)
        self.assertIn("args.client_script (required for feature and perf, where G5 measures every change, and for an issue or a ci "
                      "task that can reach a fix", use)
        self.assertNotIn("for a feature with a workload", use, "whenToUse still gives the client's old rule")
        fields = (ROOT / "templates" / "task_definition.md").read_text(encoding="utf-8")
        row = [line for line in fields.splitlines() if line.startswith("| `client_script` |")]
        self.assertEqual(len(row), 1)
        self.assertIn("| required unless `stop_after` ends the run before a fix: G5 measures the fix on it; with `stability` on, "
                      "required either way | as for an issue |", row[0], "templates/task_definition.md: an issue's and a ci task's client")
        for name, text in (("README.md", EN), ("README.zh-CN.md", ZH)):
            row = [line for line in text.splitlines() if line.startswith("| `client_script` |")]
            self.assertEqual(len(row), 1, name)
            for said in ("`issue`", "`ci`", "`stop_after`", "G5"):
                self.assertIn(said, row[0], "%s: the client_script row" % name)
            self.assertRegex(text, r"a model or a client, when an issue or a ci run reaches its\s+fix without\s+one"
                             r"|走到修复却还没有模型或 client", "%s does not list the question" % name)

    def test_a_fix_is_measured_when_it_changes_code_the_server_runs_and_a_performance_problem_always(self):
        src = (ROOT / "inferpilot.js").read_text(encoding="utf-8")
        for said, why in (
                ("const NOT_MEASURED = 'not run: no code the server runs changed';", "the run record does not say why G5 was not run"),
                ("const unmeasured = (when) => when === 'fix' && C.server_code_changed === false;",
                 "a fix is left unmeasured on anything but the verify engineer's word about its diff"),
                ("if (BEFORE_A_FIX[MODE]) C.server_code_changed = v.server_code_changed !== false;",
                 "what the last Verify round says of the diff does not reach Performance, or a round that says nothing leaves a fix unmeasured"),
                ("const issueClaim = perfIssue ? String(rep.perf_claim || '').trim() : NO_SLOWER;\n  const runPerf = perfIssue ? true : 'fix';",
                 "an issue's fix is not measured on the right claim"),
                ("const ciClaim = perfFailure ? String(cl.perf_claim || '').trim() : NO_SLOWER;", "a ci fix is not measured on the right claim"),
                ("ciClaim, perfFailure ? true : 'fix');", "a performance failure is not measured whatever its fix changes"),
                ("const unjudged = trusted && BEFORE_A_FIX[MODE] && claim === NO_SLOWER ? String(p.no_baseline || '').trim() : '';",
                 "no baseline excuses more than a fix that claims no gain")):
            self.assertIn(said, src, why)
        for schema, field in (("VERIFY", "server_code_changed: bool,"), ("CLASSIFY", "performance: bool,"), ("CLASSIFY", "perf_claim: str,"),
                              ("PERF", "no_baseline: str,")):
            body = re.search(r"\nconst %s = S\(\{(.*?)\n\}, \[" % schema, src, re.S).group(1)
            self.assertIn(field, body, "%s does not take %s" % (schema, field))
        note = [i for i in items("knowledge/validation_doctrine.md") if i.startswith("- **G5 for a fix.**")]
        self.assertEqual(len(note), 1, "knowledge/validation_doctrine.md does not say how a fix is measured for speed")
        for said in ("when it changes anything but tests, documentation and CI files", "no slower than the base beyond the noise band",
                     "One diff decides both gates", "has no G5 either", "the last commit that served it", "where G2 did not run (a model that answers no text questions), "
                     "the one the triage record names", "reported and not judged", "names its client as it names its model",
                     "no template is offered for it", "A performance problem is measured whatever its fix changes",
                     "a CI failure on a number its test measures", "A threshold moved in a test restores nothing",
                     "stops the task at Performance for a human, and nothing is rolled back"):
            self.assertIn(said, note[0])
        for path, said in (("roles/verify_engineer.md", 'and the answer `"server_code_changed": false`'),
                           ("roles/verify_engineer.md", "That one diff decides G5 as well"),
                           ("roles/verify_engineer.md", "Read the diff in every round"),
                           ("roles/perf_engineer.md", "**A fix (issue, ci) has no baseline from Setup:** measure both arms yourself"),
                           ("roles/perf_engineer.md", "the baseline is the last commit that served it, in a throwaway worktree. That "
                                                      "is the commit G2 measured against, which the ledger names"),
                           ("roles/perf_engineer.md", "Where G2 measured none, as for a model that answers no text questions, which it "
                                                      "does not run on, the commit is still known: take it from the triage record"),
                           ("roles/perf_engineer.md", "Only where the run knows no such commit, measure the fix alone"),
                           ("roles/perf_engineer.md", "return `no_baseline` with the reason"),
                           ("roles/perf_engineer.md", "**A performance problem is measured where it showed:**"),
                           ("roles/perf_engineer.md", "a feature or a fix that is slower does not"),
                           ("roles/ci_guardian.md", "`performance`: true when the test fails on a number it measures"),
                           ("roles/ci_guardian.md", "Without one the task stops here"),
                           ("roles/issue_triager.md", "`performance` makes the reported number the claim G5 judges the fix on, whatever the "
                                                      "fix changes"),
                           ("roles/director.md", "Nor does its speed (*G5 for a fix*): the performance engineer measures both arms after "
                                                 "Verify"),
                           ("roles/director.md", "for the G2 and the G5 of a fix whose baseline was measured on another commit"),
                           ("roles/intake.md", "An issue or a ci task needs a client too, unless `stop_after` ends the run before a fix"),
                           ("roles/intake.md", "offer no `template` for it"),
                           ("roles/reviewer.md", "`server_code_changed` false means the run measured neither its accuracy nor its speed"),
                           ("templates/task_definition.md", "So does an issue or a ci task that can reach a fix"),
                           ("templates/task_definition.md", "G2 and G5 then serve the fix of a task that has a `model_path` with "
                                                            "`server.sh`"),
                           ("templates/ci_triage.md", "| performance problem: yes / no |"),
                           ("templates/ci_triage.md", "for a performance problem the number the fix restores, whatever it changes"),
                           ("templates/issue_triage.md", "its speed on the task's client against the same commit (G5)"),
                           ("templates/validation_report.md", "for the G2 and the G5 of a fix"),
                           ("templates/validation_report.md", "`not run: no code the server runs changed`, and no table"),
                           ("knowledge/acceptance.md", "**A fix** (issue, ci) is measured too when it changes anything but tests, documentation "
                                                       "and CI files, on the task's `client_script`"),
                           ("knowledge/acceptance.md", "always when the failure is a performance problem: the number its test measures comes "
                                                       "back"),
                           ("knowledge/perf_evaluation.md", "The fix of an issue or of a CI failure is measured the same way when it changes "
                                                            "code the server runs"),
                           ("knowledge/issue_triage.md", "Such a fix is measured for speed as well (G5 for a fix"),
                           ("knowledge/ci_triage.md", "Such a fix is measured for speed as well (G5 for a fix"),
                           ("knowledge/ci_triage.md", "**A failure on a number is a performance problem,** whatever its class"),
                           ("knowledge/pr_playbook.md", "So does a fix that changes anything but tests, documentation and CI files, the fix next "
                                                         "to the base"),
                           ("knowledge/dev_methodology.md", "nor is a fix that changes code the server runs slower than the base")):
            self.assertIn(said, self.flat(path), "%s does not say it" % path)
        for name, text in (("README.md", EN), ("README.zh-CN.md", ZH)):
            modes = [line for line in text.splitlines() if re.match(r"\| `(issue|ci)` \|", line) and "Setup →" in line]
            self.assertEqual(len(modes), 2, name)
            for line in modes:
                self.assertRegex(line, r"Verify → Performance[ （(]", "%s: a fix's Performance phase, and when it is skipped" % name)
            self.assertRegex(text, r"its number must come back, whatever the fix changes|掉下去的数值都必须回来", name)


class CiAccuracyProblemTest(unittest.TestCase):
    """A CI failure on a score its test measures is an accuracy problem, as one on a speed number is a
    performance problem (knowledge/validation_doctrine.md, G2 for an accuracy problem in CI). It is fixed when
    the score comes back: G2 measures it whatever the fix changes, and a threshold moved in the test restores
    nothing."""

    @staticmethod
    def flat(path):
        return " ".join((ROOT / path).read_text(encoding="utf-8").split())

    def test_the_run_takes_the_score_from_classify_and_accepts_no_round_without_g2(self):
        src = (ROOT / "inferpilot.js").read_text(encoding="utf-8")
        body = re.search(r"\nconst CLASSIFY = S\(\{(.*?)\n\}, \[", src, re.S).group(1)
        for field in ("accuracy: bool,", "accuracy_claim: str,"):
            self.assertIn(field, body, "CLASSIFY does not take %s" % field)
        for said, why in (
                ("const accClaim = accFailure ? String(cl.accuracy_claim || '').trim() : '';", "the score to restore is not read"),
                ("if (!handOver && accFailure && !accClaim) {", "an accuracy failure goes on without the score its fix must restore"),
                ("if (!handOver && accClaim) C.accuracy_claim = accClaim;", "the claim does not reach the verify engineer"),
                ("const claimGate = String((v.gates || {}).accuracy_claim || '').trim();",
                 "the score to restore is read from G2, which also holds gsm8k against the base"),
                ("if (C.accuracy_claim && (v.all_pass === true || (failures.length && !blocking.length)) && !/^pass\\b/i.test(claimGate)) {",
                 "a round is accepted without the score's pass: both ways a round is accepted are held to it, and only \"pass\" is one"),
                ("const ownGates = [...(extraGates || []).map((g) => g.gate), ...(C.accuracy_claim ? ['accuracy_claim'] : [])];",
                 "the fix's own gates are not told from the others"),
                ("const mine = own(blocking).map((f) => f.gate);", "a gate of the fix's own that fails on the base becomes a known issue")):
            self.assertIn(said, src, why)
        # The check sits before the line that accepts a round, whichever way it is accepted; and what the task is
        # there to fix is never recorded as a known issue, which the next round would excuse.
        self.assertLess(src.index("if (C.accuracy_claim && (v.all_pass === true"), src.index("note('Verify', 'pass',"))
        self.assertLess(src.index("const mine = own(blocking).map((f) => f.gate);"), src.index("if (inv.known_issue) R.known_issues.push(inv.known_issue);"))

    def test_the_documents_say_what_is_measured(self):
        note = [i for i in items("knowledge/validation_doctrine.md") if i.startswith("- **G2 for an accuracy problem in CI.**")]
        self.assertEqual(len(note), 1, "knowledge/validation_doctrine.md does not say how a CI accuracy problem is judged")
        for said in ("A CI failure on a score its test measures", "It is fixed when the score comes back, so the score is the claim",
                     "`accuracy_claim`: the set, the model and the value", "with the failing test's own measurement, whatever the fix changes",
                     "G2 reads the score, not the assertion", "a threshold moved in a test restores nothing",
                     "No known issue excuses it: the base scores low as well",
                     "It is reported under its own name (`accuracy_claim`), apart from gsm8k against the base",
                     "cannot stand in for it",
                     "that is a human's to say"):
            self.assertIn(said, note[0])
        for path, said in (
                ("knowledge/ci_triage.md", "**A failure on a score is an accuracy problem,** whatever its class"),
                ("knowledge/ci_triage.md", "a lower threshold in the test is not a fix"),
                ("roles/ci_guardian.md", "`accuracy`: true when the test fails on a score it measures"),
                ("roles/ci_guardian.md", '"accuracy": false, "accuracy_claim": "<accuracy failures only>"'),
                ("roles/verify_engineer.md", "**An accuracy problem is measured where it showed, whatever the fix changes.**"),
                ("roles/verify_engineer.md", "read the score it measures, not whether its assertion passes"),
                ("roles/verify_engineer.md", "Report it in `gates` under its own name, apart from G2's gsm8k against the base"),
                ("roles/verify_engineer.md", '`"accuracy_claim": "pass 0.936, the claim is 0.93 (the test\'s GSM8K, 200 q)"`'),
                ("roles/verify_engineer.md", "with `pass` only for a score at the claim or above"),
                ("roles/verify_engineer.md", "Never leave it out, and never report it `not run`: the run accepts no round without its `pass`"),
                ("roles/verify_engineer.md", "Step 4 excuses nothing here"),
                ("roles/verify_engineer.md", "no entry of `known_issues` makes a score below the claim pass, or fail without blocking"),
                ("roles/reviewer.md", "read G2's score against the claim yourself"),
                ("roles/reviewer.md", "is hack C1, whatever the base scores and whatever the fix changes"),
                ("knowledge/ci_triage.md", "An accuracy problem (§2) is measured whatever its fix changes, a fix to the test alone included"),
                ("knowledge/acceptance.md", "**An accuracy problem in CI** (a test that fails on a score it measures, not on an error)"),
                ("knowledge/acceptance.md", "A failure that is itself one of accuracy or of performance is measured whatever its fix changes"),
                ("templates/ci_triage.md", "for an accuracy problem the score the fix restores"),
                ("templates/ci_triage.md", "| accuracy problem: yes / no |")):
            self.assertIn(said, self.flat(path), "%s does not say it" % path)


class FeaturePerformanceTest(unittest.TestCase):
    """G5 is part of every feature (knowledge/validation_doctrine.md, G5 for a feature): whoever reviews a new
    feature asks what it does to accuracy and to speed, so it is measured against the baseline whether it claims
    a gain or not. With a claim the gain shows; without one, or on InferPilot's client, the feature is no slower
    than the baseline. So a feature names its client as a perf task does, and the roles and documents say so."""

    @staticmethod
    def flat(path):
        return " ".join((ROOT / path).read_text(encoding="utf-8").split())

    def test_every_feature_names_the_client_g5_measures_it_with(self):
        src = (ROOT / "inferpilot.js").read_text(encoding="utf-8")
        self.assertIn("if ((mode === 'perf' || mode === 'feature') && !a.client_script) {\n", src,
                      "a feature without a workload starts without a client, and G5 has nothing to measure it with")
        self.assertNotIn("(mode === 'feature' && a.workload)", src, "a feature's client still depends on its having a workload")
        self.assertIn("const gaps = isTemplate(a.client_script) ? templateLoadGaps(a) : [];", src)
        self.assertIn("if ((mode === 'feature' || (mode === 'perf' && a.workload)) && gaps.length) {\n    ask('workload',", src,
                      "InferPilot's client would measure a task on lengths or a concurrency nobody chose")
        fields = (ROOT / "templates" / "task_definition.md").read_text(encoding="utf-8")
        row = [line for line in fields.splitlines() if line.startswith("| `client_script` |")]
        self.assertEqual(len(row), 1)
        self.assertIn("| required: G5 measures every feature |", row[0], "templates/task_definition.md: a feature's client")
        workload = [line for line in fields.splitlines() if line.startswith("| `workload` |")]
        self.assertIn("required with `client_script: template`", workload[0])
        for name, text in (("README.md", EN), ("README.zh-CN.md", ZH)):
            row = [line for line in text.splitlines() if line.startswith("| `client_script` |")]
            self.assertEqual(len(row), 1, name)
            self.assertRegex(row[0], r"required for `feature` and `perf`|`feature` 和 `perf` 必填", "%s: the client_script row" % name)
            self.assertNotRegex(row[0], r"a feature with a `workload`|给了 `workload` 的 feature", name)

    def test_the_whole_of_the_load_inferpilots_client_runs_is_the_tasks(self):
        """A workload that names a part of it would leave the rest to the feature's source or to the template: the
        three the check asks of the task are the ones scripts/harness.py takes from a workload."""
        src = (ROOT / "inferpilot.js").read_text(encoding="utf-8")
        harness = (ROOT / "scripts" / "harness.py").read_text(encoding="utf-8")
        takes = dict(re.findall(r'"(\w+)": "(\w+)"', re.search(r"^WORKLOAD = \{(.*?)\}$", harness, re.M).group(1)))
        ours = re.search(r"\nconst TEMPLATE_LOAD = \{(.*?)\};\n", src)
        self.assertIsNotNone(ours, "inferpilot.js does not say what a task that takes the client template gives")
        self.assertEqual(dict(re.findall(r"(\w+): \['(\w+)'", ours.group(1))), takes,
                         "inferpilot.js asks for other dimensions than scripts/harness.py takes from a workload")
        self.assertIn("function templateLoadGaps(a) {", src)
        for path, said in (("templates/task_definition.md", "gives the whole of that workload, both lengths and the concurrency (in "
                                                            "`workload`, or in `harness`), and is asked for what it leaves out"),
                           ("roles/intake.md", "a task that takes the template gives the whole `workload` it runs: both lengths and "
                                               "the concurrency")):
            self.assertIn(said, self.flat(path), "%s does not say it" % path)

    def test_a_feature_without_a_claimed_gain_is_judged_as_no_slower_than_the_baseline(self):
        src = (ROOT / "inferpilot.js").read_text(encoding="utf-8")
        self.assertIn("const NO_SLOWER = \"no gain is claimed: no slower than the baseline beyond the noise band, on the run's client\";",
                      src)
        self.assertIn("const ours = fallback || isTemplate(A.client_script);\n"
                      "  const claimOf = (c, cost) => (ours && cost !== true ? '' : String(c || '').trim()) || NO_SLOWER;",
                      src, "a feature without a claimed gain, or on InferPilot's client, is measured and nothing is judged")
        self.assertEqual(src.count("claimOf("), 2, "the first run and the resumed one read the claim the same way")
        for call in ("claimOf(spec.perf_claim, spec.perf_cost)", "claimOf(approved.perf_claim, approved.perf_cost)"):
            self.assertIn(call, src, "a cost the user stated is lost on the way to Performance")
        self.assertEqual(src.count("perf_cost: bool,"), 2,
                         "the spec and the approved design both say whether the claim is a cost the user stated")
        note = [i for i in items("knowledge/validation_doctrine.md") if i.startswith("- **G5 for a feature.**")]
        self.assertEqual(len(note), 1, "knowledge/validation_doctrine.md does not say that every feature is measured")
        for said in ("Every feature is measured against the baseline, whether it claims a gain or not",
                     "what it does to accuracy and to speed", "G2 and G5",
                     "One that claims none is no slower than the baseline beyond the noise band",
                     "on which the source's gain is never the claim",
                     "A cost the user states in the request or `goal`", "no role states one for the user",
                     "stops the task at Performance for a human, and nothing is rolled back",
                     "a feature task names its client as it names its server"):
            self.assertIn(said, note[0])
        gates = [line for line in (ROOT / "knowledge" / "validation_doctrine.md").read_text(encoding="utf-8").splitlines()
                 if line.startswith("| G5 performance |")]
        self.assertEqual(len(gates), 1)
        self.assertIn("a change that claims none is no slower there", gates[0])
        for path, said in (("knowledge/acceptance.md", "**Without a claimed gain** a feature is measured all the same"),
                           ("knowledge/acceptance.md", "a feature that claims no gain is no slower when that median lies no further below "
                                                       "the baseline than the noise band"),
                           ("knowledge/perf_evaluation.md", "A feature is measured whether it claims a gain or not"),
                           ("knowledge/perf_evaluation.md", "or a slowdown beyond the noise band where no gain is claimed"),
                           ("knowledge/dev_methodology.md", "a feature that claims none is no slower than the baseline"),
                           ("knowledge/pr_playbook.md", "A feature that claims no gain gives the same table"),
                           ("roles/perf_engineer.md", "that the change is no slower than the baseline beyond the noise band: a feature "
                                                      "that claims no gain or is measured with InferPilot's client"),
                           ("roles/perf_engineer.md", "`no slower than the baseline` is met when the median lies no further below the "
                                                      "baseline than the noise band"),
                           ("roles/perf_engineer.md", "the claim still holds: a gain still beyond the noise band, no slowdown still none"),
                           ("roles/director.md", "**Without a claimed gain there is no gap.**"),
                           ("roles/director.md", "G5 runs either way"),
                           ("roles/director.md", "an empty claim is judged as no slower than the baseline beyond the noise band"),
                           ("roles/director.md", "returned with `perf_cost: true`; never state one yourself"),
                           ("roles/director.md", "It stays the claim on InferPilot's client: only a gain of the source's is never "
                                                 "claimed there"),
                           ("roles/director.md", "`perf`, and every feature: the run's `client_script`, with the task's `workload` "
                                                 "when it gives one, is the fixed benchmark"),
                           ("roles/director.md", "A feature's are recorded whether it claims a gain or not"),
                           ("roles/architect.md", "`perf_claim` and `perf_cost` copied from `RUN_DIR/feature_spec.md`"),
                           ("roles/architect.md", "and `perf_cost` with it: true when the claim is a cost the user stated"),
                           ("knowledge/validation_doctrine.md", "on the user's client and on InferPilot's alike"),
                           ("roles/perf_engineer.md", "A cost the user stated (`at most 5% below the baseline`) is met when the "
                                                      "median lies within that bound"),
                           ("roles/perf_engineer.md", "`RUN_DIR/run.json` in `perf`, in `feature`"),
                           ("templates/feature_spec.md", "a cost the user stated in `goal`: no more than it, on either client"),
                           ("roles/director.md", "the source's gain is never the claim on it: G5 judges there only that the feature is "
                                                 "no slower than the baseline"),
                           ("roles/intake.md", "A feature needs its client as a perf task does, whether it claims a gain or not"),
                           ("templates/task_definition.md", "G5 measures every feature against the baseline with it"),
                           ("templates/task_definition.md", "G5 judges on it only that the feature is no slower than the baseline"),
                           ("templates/feature_spec.md", "no claimed gain, or InferPilot's client: no slower than the baseline beyond the "
                                                         "noise band"),
                           ("templates/validation_report.md", "where none is claimed, no slower than the baseline")):
            self.assertIn(said, self.flat(path), "%s does not say it" % path)
        acceptance = (ROOT / "knowledge" / "acceptance.md").read_text(encoding="utf-8")
        row = [line for line in acceptance.splitlines()
               if line.startswith("| Performance | ") and "when the issue is a performance problem" in line]
        self.assertEqual(len(row), 1, "knowledge/acceptance.md §2 has no Performance row")
        self.assertIn("| Performance | required: the gain the feature claims, or, where it claims none, no slower than the baseline",
                      row[0])
        self.assertNotIn("when the feature claims a gain", row[0], "the profile still leaves a feature's G5 to its claim")
        for path in ("roles/perf_engineer.md", "roles/director.md", "templates/task_definition.md", "knowledge/perf_evaluation.md"):
            self.assertNotRegex(self.flat(path), r"reported, never a claim|reported, never claimed|true when `claim` is empty",
                                "%s still says that nothing is judged without a claimed gain" % path)


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


class TriageCasesTest(unittest.TestCase):
    """Two cases the triage of an issue left open (knowledge/issue_triage.md §4): a report of a failure that
    another open issue already has is answered and not worked on twice, and a report with several problems is
    several tasks, each with its own reproduction, fix and gates."""

    @staticmethod
    def flat(path):
        return " ".join((ROOT / path).read_text(encoding="utf-8").split())

    def test_a_duplicate_ends_at_triage_with_its_original_and_its_reply(self):
        src = (ROOT / "inferpilot.js").read_text(encoding="utf-8")
        self.assertIn("enum: ['reproduce', 'push_existing_pr', 'close_fixed', 'needs_info', 'not_amd', 'plan', 'duplicate'] },", src)
        self.assertIn("duplicate_of: str,", src)
        self.assertIn("if (['needs_info', 'not_amd', 'plan', 'duplicate'].includes(tri.action)) return finish('triaged', 'Triage');", src,
                      "a duplicate goes on to a reproduction")
        self.assertIn("const duplicateOf = tri.action === 'duplicate' ? issueRef(tri.duplicate_of) : '';", src,
                      'any word names the original: "unknown" would end the run with no issue to follow')
        guard = "if (tri.action === 'duplicate' && (!duplicateOf || !String(tri.reply_draft_path || '').trim())) {"
        self.assertIn(guard, src, "a duplicate is accepted without the issue it duplicates, or without a reply")
        self.assertLess(src.index(guard), src.index("return finish('triaged', 'Triage');"), "the check comes before the run ends as triaged")
        table = [line for line in (ROOT / "knowledge" / "issue_triage.md").read_text(encoding="utf-8").splitlines()
                 if line.startswith("| `duplicate` |")]
        self.assertEqual(len(table), 1, "knowledge/issue_triage.md §4 has no row for a duplicate")
        for path, said in (("knowledge/issue_triage.md", "**A duplicate is answered, not worked on twice.**"),
                           ("knowledge/issue_triage.md", "when the signature and the path that fails are the same, not when the titles are alike"),
                           ("knowledge/issue_triage.md", "One of them is the original: the one the maintainers already call so (a label, a comment), "
                                                         "else the one opened first"),
                           ("knowledge/issue_triage.md", "so that two reports are never each the other's"),
                           ("knowledge/issue_triage.md", "whose triage searches for its duplicates and takes what they add into its record"),
                           ("roles/issue_triager.md", "For a `duplicate`, return `duplicate_of` (`#<n>`, the original)"),
                           ("roles/issue_triager.md", "Then search the open issues for the same failure, by its error signature and the path that fails"),
                           ("roles/issue_triager.md", "the one the maintainers call so (a label, a comment), and only where they name none, the one "
                                                      "opened first"),
                           ("roles/issue_triager.md", "Where that is another report, this one is a `duplicate`"),
                           ("roles/issue_triager.md", "read them, since the state of an issue is not what it reports"),
                           ("roles/issue_triager.md", "needs_info | not_amd | plan | duplicate"),
                           ("roles/issue_triager.md", '"duplicate_of": "<with duplicate: #n, the original>"'),
                           ("templates/issue_triage.md", "/ plan / duplicate (of #<n>) |")):
            self.assertIn(said, self.flat(path), "%s does not say it" % path)

    def test_a_report_with_several_problems_is_several_tasks(self):
        for path, said in (("knowledge/issue_triage.md", "**A report with several problems is several tasks.**"),
                           ("knowledge/issue_triage.md", "what its pull request changes decides its gates one by one"),
                           ("knowledge/issue_triage.md", "each with its priority and the fix PRs that are its own"),
                           ("knowledge/issue_triage.md", "A run takes one, the one `task` names or else the most urgent, and says which in `reason`"),
                           ("knowledge/issue_triage.md", "the run's priority, category, action and existing fix PRs are that problem's"),
                           ("knowledge/issue_triage.md", "for each of the others its known state (a fix linked to it, open or merged, or nothing yet) "
                                                         "and that this run did not cover it"),
                           ("knowledge/issue_triage.md", "Each of them starts as a run of its own"),
                           ("roles/issue_triager.md", "A report with several problems is several tasks"),
                           ("roles/issue_triager.md", "`priority`, `category`, `action` and `existing_prs` are then that problem's and no other's"),
                           ("roles/issue_triager.md", "a fix PR for another problem of the report is listed in the triage record only"),
                           ("roles/issue_triager.md", "Return the others as `other_problems`"),
                           ("knowledge/issue_triage.md", "the run's result offers those runs (`next.start`)"),
                           ("knowledge/issue_triage.md", "and with the operator's settings this run was given"),
                           ("knowledge/issue_triage.md", "A run that has a model takes a problem on that model and configuration"),
                           ("knowledge/issue_triage.md", "where the task itself names such a problem, the run stops before Reproduce and asks for them"),
                           ("knowledge/issue_triage.md", "A task without a model, one that stops before its fix, takes the most urgent problem of all"),
                           ("roles/issue_triager.md", "Where the task has a `model_path`, \"the most urgent\" is said of the problems on that model and "
                                                      "configuration"),
                           ("roles/issue_triager.md", "a problem the report puts on another model is one of the others, however urgent"),
                           ("roles/issue_triager.md", "A task without a model (one that stops before its fix) takes the most urgent of all"),
                           ("roles/issue_triager.md", "in both you take that problem and return `model_mismatch`"),
                           ("roles/issue_triager.md", "`task` itself names a problem on another model or configuration than the task's"),
                           # a task that names the issue alone has a problem to take even where none is on its model
                           ("roles/issue_triager.md", "Or `task` names the issue alone and none of its problems is on the task's: take the most "
                                                      "urgent of all then"),
                           ("roles/issue_triager.md", "one without a model that pins two GPUs does not fit a problem at TP8 either"),
                           ("roles/issue_triager.md", "one that names no model and pins nothing has nothing to mismatch"),
                           ("roles/issue_triager.md", "for the problem's model where the task has one, and otherwise for each input it pins, not for a "
                                                      "model the task has no use for"),
                           ("knowledge/issue_triage.md", "It is then asked for those inputs, each of them, and not for a model"),
                           ("knowledge/issue_triage.md", "A task that names no model and pins nothing is asked nothing, and reproduces the problem as "
                                                         "reported"),
                           ("knowledge/issue_triage.md", "So it does where the task names the issue alone and no problem of the report is on the "
                                                         "run's model and configuration"),
                           ("knowledge/issue_triage.md", "what it pins all the same (its GPUs, its scripts) has to fit that problem"),
                           ("roles/issue_triager.md", "one with a `model` asks for its own model, scripts and GPUs instead of taking this run's"),
                           ("AGENTS.md", "(a scan's top tasks, the other problems of an issue's report)"),
                           ("README.md", "a scan's top tasks, the other problems of an issue's report"),
                           ("README.zh-CN.md", "scan 排在前面的任务，issue 报告里的其他问题"),
                           ("templates/issue_triage.md", "| problems in the report |")):
            self.assertIn(said, self.flat(path), "%s does not say it" % path)
        src = (ROOT / "inferpilot.js").read_text(encoding="utf-8")
        for said, why in (
                ("const issue = issueOf(def.task);", "a follow-up of a follow-up names two problems"),
                ("const task = p.task || `${issue}: ${p.problem}`;", "a follow-up takes the task as it stands"),
                ("args: { inferpilot_dir: INFERPILOT, ...pick(RAW, OPERATOR_KEYS), ...own, mode: 'issue', task, ...words } };",
                 "a follow-up loses the operator's settings, or the model the report names for it"),
                ("'gpu_ids'];", "a problem on another configuration is pinned to this run's GPUs"),
                ("const pinned = MEASURED_WITH.filter((k) => k !== 'model_path' && A[k]);\n  if (elsewhere && !C.model_path && !pinned.length) {",
                 "a task that names no model and pins nothing is stopped for a mismatch it cannot have"),
                ("  } else if (elsewhere) {\n    R.model_mismatch = elsewhere;",
                 "a problem on another model or configuration is reproduced on the task's, with a model of its own or without"),
                # a task without a model is asked for what it pins, not for a model
                (": pinned.map((k) => ({ field: k, question: `The task's ${k} (${JSON.stringify(String(A[k]).slice(0, 120))}) is another `",
                 "a task without a model is asked for a model"),
                ("  } else if (status === 'needs_input' && R.model_mismatch) {\n", "the result of a mismatch on what a task pins (its GPUs alone) "
                                                                                 "does not say what to give, or how to run again"),
                ("if (R.model_mismatch) afterMismatch(out, at, last, report);", "the rerun after a mismatch brings the old configuration back"),
                # the rerun clears what the task measured with as a field and as an earlier answer, and names the problem's configuration
                ("Object.fromEntries(MEASURED_WITH.filter((k) => A[k]).map((k) => [k, ''])),", "the rerun keeps the task's own model, scripts or GPUs"),
                ("out.rerun_with.answers = { ...Object.fromEntries(MEASURED_WITH.filter((k) => k in earlier).map((k) => [k, null])), "
                 "...out.rerun_with.answers };", "an earlier answer brings the old configuration back into the rerun"),
                ("{ request: `${R.task_def.task}. The report puts this problem on ${R.model_mismatch}: ", "the rerun keeps the request of the old configuration"),
                ("pick(R.task_def, TASK_KEYS.filter((k) => !MEASURED_WITH.includes(k) && k !== 'apply_prs')),",
                 "the rerun loses the rest of the task, or makes an inferred apply_prs the caller's"),
                ("const own = p.model ? Object.fromEntries(Object.entries(def).filter(([k]) => !MEASURED_WITH.includes(k))) : def;",
                 "a problem on another model is measured with this run's model and scripts")):
            self.assertIn(said, src, why)
        self.assertNotIn("says for each of the others that it is not yet worked on", self.flat("knowledge/issue_triage.md"),
                         "another problem may have a fix already: the reply gives its known state")


class DetachedRunTest(unittest.TestCase):
    """A run longer than one tool call starts detached, and a later tool shell cannot wait for it: the
    documented launcher leaves the run's PID and exit status where that shell reads them, whatever the
    script does with quotes of its own, `exit`, `exec` or `set -e`, and in a shell with job control; no
    other launch shares its log and records, a wrapper that has exited is gone though nobody reaped it, and
    the stop ends every process the launch gave its mark to, and no other."""

    # (the script, its arguments, the status it ends with, what its log has)
    SCRIPTS = (
        ("EXTRA_ARGS='--foo 1' sh -c 'echo \"results written $EXTRA_ARGS\"'\nexec sh -c 'exit 3'\necho never\n", [],
         "3", "results written --foo 1"),
        ("set -e\necho results written\nfalse\necho never\n", [], "1", "results written"),
        ("echo results written\nexit 4\necho never\n", [], "4", "results written"),
        ('printf "%s|" "$#" "$@"; echo\n', ["gsm8k", "two words"], "0", "2|gsm8k|two words|"),
    )

    def setUp(self):
        self.launch = doc_command("knowledge/env_hygiene.md", 'INFERPILOT_LAUNCH="$(realpath <log>)" setsid nohup')
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

    @staticmethod
    def stop(*logs, stdin=False, env=None, grace="1"):
        """scripts/stop_launch.py on logs, with a second of grace: its exit status and what it printed."""
        script = ROOT / "scripts" / "stop_launch.py"
        cmd = [sys.executable, "-" if stdin else str(script)] + [str(log) for log in logs] + ["--grace", grace]
        with open(script, encoding="utf-8") as program:
            out = subprocess.run(cmd, stdin=program if stdin else subprocess.DEVNULL, capture_output=True, text=True,
                                 timeout=60, env=dict(os.environ, **(env or {})))
        return out.returncode, out.stdout + out.stderr

    def pids(self, *paths):
        got = [self.read(Path(p)) for p in paths]
        self.assertNotIn(None, got, "the run did not start what it was to")
        return [int(p) for p in got]

    @staticmethod
    def kill(pids):
        for p in pids:
            if alive(p):
                os.kill(p, signal.SIGKILL)

    def test_a_stop_ends_every_process_of_the_launch(self):
        # A worker that ignores SIGTERM; one whose parent has exited and that leads a session of its own,
        # which only its environment still ties to the launch; and one with no environment, below the script.
        body = ('bash -c \'trap "" TERM; exec sleep 60\' &\necho $! > "$1/stubborn"\n'
                '(setsid sleep 60 & echo $! > "$1/away")\n'
                'env -i sleep 60 &\necho $! > "$1/bare"\nsleep 60\n')
        with tempfile.TemporaryDirectory() as d:
            log = self.start(d, body, [str(Path(d) / "a run")])
            pids = self.pids("%s.pid" % log, "%s/a run/stubborn" % d, "%s/a run/away" % d, "%s/a run/bare" % d)
            try:
                self.assertNotEqual(os.getsid(pids[2]), pids[0], "the worker that left the session is still in it")
                code, out = self.stop(log)
                self.assertEqual(code, 0, out)
                self.assertEqual([p for p in pids if alive(p)], [], "a process of the launch outlived its stop")
            finally:
                self.kill(pids)
        self.assertIn("every process whose environment holds `INFERPILOT_LAUNCH` with one of these logs, whatever became of "
                      "its parent, its session or the wrapper, and what those started", self.doc)
        self.assertIn("SIGKILLs those still there after `--grace` seconds (30 unless given), and exits 0 once none is left", self.doc)

    def test_a_stop_ends_what_a_wrapper_that_exited_left_and_reads_no_pid_from_a_file(self):
        with tempfile.TemporaryDirectory() as d:
            log = self.start(d, 'sleep 60 &\necho $! > "$1/left"\n', [str(Path(d) / "a run")])
            wrapper, left = self.pids("%s.pid" % log, "%s/a run/left" % d)
            # Another process has the recorded PID now, and the log among its arguments.
            stranger = subprocess.Popen(["tail", "-f", str(log)], stdout=subprocess.DEVNULL)
            try:
                self.assertEqual(self.read(Path("%s.exit" % log)), "0")
                for _ in range(200):
                    if not alive(wrapper):
                        break
                    time.sleep(0.05)
                Path("%s.pid" % log).write_text("%d\n" % stranger.pid)
                code, out = self.stop(log, stdin=True)
                self.assertEqual(code, 0, out)
                self.assertFalse(alive(left), "what the wrapper left outlived the stop")
                self.assertTrue(alive(stranger.pid), "a stop signalled the process that has the recorded PID now")
                self.assertEqual(self.stop(Path(d) / "another.log")[0], 2, "a log that does not exist is taken for a stopped launch")
                self.assertEqual(self.stop(log, grace="-1")[0], 2, "a grace below 0 is taken")
            finally:
                stranger.kill()
                stranger.wait()
                self.kill([left])
        self.assertIn("not by a PID read later", self.doc)
        self.assertIn("2 for a log that does not exist", self.doc)
        self.assertIn("`docker exec -i <container> python3 - <log>... < <inferpilot_dir>/scripts/stop_launch.py`", self.doc)

    def test_a_stop_takes_the_launches_it_is_given_together_and_no_other(self):
        with tempfile.TemporaryDirectory() as d:
            logs, pids = [], []
            for name in ("a", "b", "c"):
                (Path(d) / name).mkdir()
                logs.append(self.start(str(Path(d) / name), 'bash -c \'trap "" TERM; exec sleep 60\'\n'))
                pids += self.pids("%s.pid" % logs[-1])
            try:
                code, out = self.stop(logs[0], logs[1])
                self.assertEqual(code, 0, out)
                self.assertEqual([alive(p) for p in pids], [False, False, True], "the stop ended another launch, or left one of its own")
            finally:
                self.stop(*logs)  # the third launch, and what its wrapper started
                self.kill(pids)
        self.assertIn("All the launches of a call go in one command, which takes little more than twice the grace", self.doc)

    def test_a_stop_started_from_a_shell_of_the_launch_ends_the_launch_not_itself(self):
        with tempfile.TemporaryDirectory() as d:
            log = self.start(d, "sleep 60\n")
            pids = self.pids("%s.pid" % log)
            try:
                code, out = self.stop(log, env={"INFERPILOT_LAUNCH": os.path.realpath(log)})
                self.assertEqual(code, 0, out)
                self.assertFalse(alive(pids[0]), "the launch outlived its stop")
            finally:
                self.kill(pids)

    def test_a_stop_that_exits_0_has_not_reached_a_process_with_no_mark_and_no_parent_and_the_gpus_are_checked(self):
        # Started with an empty environment by a shell that then exited: nothing ties it to the launch.
        body = '(env -i setsid sh -c \'echo $$ > "$1"; exec sleep 60\' _ "$1/gone" &)\nsleep 60\n'
        with tempfile.TemporaryDirectory() as d:
            log = self.start(d, body, [str(Path(d) / "a run")])
            wrapper, gone = self.pids("%s.pid" % log, "%s/a run/gone" % d)
            try:
                for _ in range(200):
                    parent = Path("/proc/%d/stat" % gone).read_text().rsplit(")", 1)[1].split()[1]
                    try:
                        if b"INFERPILOT_LAUNCH=" not in Path("/proc/%s/environ" % parent).read_bytes():
                            break
                    except OSError:
                        pass
                    time.sleep(0.05)
                code, out = self.stop(log)
                self.assertEqual(code, 0, out)
                self.assertFalse(alive(wrapper), "the launch outlived its stop")
                self.assertTrue(alive(gone), "the stop reaches such a process: what the documents say of it is out of date")
            finally:
                self.kill([wrapper, gone])
        self.assertIn("Its exit 0 says the processes it could find are gone, not that the GPUs are free", self.doc)
        self.assertIn("`python3 <inferpilot_dir>/scripts/gpu_status.py --check <gpu_ids>`, where the servers run, before the "
                      "call's first launch and again once the stop exits 0", self.doc)
        self.assertIn("A GPU idle at the first and in use at the second is held by something the call started and the stop did "
                      "not reach", self.doc)
        script = re.sub(r"\s+", " ", (ROOT / "scripts" / "stop_launch.py").read_text())
        self.assertIn("So 0 says the processes it could find are gone, not that the GPUs are free", script)


if __name__ == "__main__":
    unittest.main()
