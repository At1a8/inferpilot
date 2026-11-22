"""What the code reads and what the docs say stay in step: every environment variable that setup, the
container script and the runner read is in the READMEs, every other script names the ones it reads in
its own header, every variable the READMEs' tables list is read by something, and the command-line flags
the READMEs and AGENTS.md give exist. Offline.

    python3 -m unittest discover -s tests
"""
import ast
import re
import unittest

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


if __name__ == "__main__":
    unittest.main()
